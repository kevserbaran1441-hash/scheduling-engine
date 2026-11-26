"""Tests for local search, Tabu Search and Simulated Annealing (identical parallel machines)."""
import itertools
import json
import math
import os
import random
import tempfile
import unittest

from scheduling_engine.checker import check_schedule
from scheduling_engine.cli import main
from scheduling_engine.decoder import decode_parallel
from scheduling_engine.errors import InputError, UnsupportedInputError
from scheduling_engine.io_csv import load_instance
from scheduling_engine.models import Instance, Job
from scheduling_engine.objectives import METRICS, evaluate
from scheduling_engine.search import (Context, Move, apply_move, calibrate_initial_temperature,
                                      initial_solution, local_search, neighborhood, placements,
                                      random_move, sa_accept, simulated_annealing, tabu_expiry,
                                      tabu_search)
from helpers import parallel

DEMO = [("J1", 7, 0, 20, 2), ("J2", 3, 0, 8, 5), ("J3", 9, 0, 30, 1), ("J4", 5, 0, 15, 3),
        ("J5", 12, 0, 25, 4), ("J6", 4, 0, 12, 2), ("J7", 8, 0, 28, 1), ("J8", 6, 0, 18, 3),
        ("J9", 10, 0, 35, 2), ("J10", 2, 0, 6, 5)]


def random_instance(seed, n=9, m=3, releases=True):
    rng = random.Random(seed)
    jobs = [(("J%d" % i), rng.randint(1, 12), rng.randint(0, 15) if releases else 0,
             rng.randint(5, 40), rng.randint(1, 5)) for i in range(1, n + 1)]
    return parallel(jobs, m)


def flat(sol):
    return sorted(j for seq in sol for j in seq)


def strip_time(history):
    return [{k: v for k, v in row.items() if k != "elapsed_s"} for row in history]


class TestNeighborhood(unittest.TestCase):
    def test_move_semantics_hand_checked(self):
        sol = [[0, 1, 2], [3]]
        self.assertEqual(apply_move(sol, Move("insert", 0, 0, 0, 2)), [[1, 2, 0], [3]])
        self.assertEqual(apply_move(sol, Move("insert", 0, 2, 1, 0)), [[0, 1], [2, 3]])
        self.assertEqual(apply_move(sol, Move("insert", 0, 0, 1, 1)), [[1, 2], [3, 0]])
        self.assertEqual(apply_move(sol, Move("swap", 0, 1, 1, 0)), [[0, 3, 2], [1]])
        self.assertEqual(apply_move(sol, Move("swap", 0, 0, 0, 2)), [[2, 1, 0], [3]])
        self.assertEqual(sol, [[0, 1, 2], [3]])                     # input never mutated

    def test_full_neighborhood_size_and_no_identity_or_duplicates(self):
        sol = [[0, 1], [2]]
        moves = list(neighborhood(sol, 2))
        self.assertEqual(len([m for m in moves if m.kind == "swap"]), 3)
        self.assertEqual(len([m for m in moves if m.kind == "insert"]), 7)
        results = [tuple(map(tuple, apply_move(sol, m))) for m in moves]
        self.assertNotIn(tuple(map(tuple, sol)), results)           # no identity move
        self.assertEqual(len(results), len(set(results)))           # no duplicates here

    def test_moves_preserve_jobs_and_change_the_solution(self):
        rng = random.Random(5)
        for _ in range(20):
            jobs = list(range(7))
            rng.shuffle(jobs)
            cuts = sorted(rng.sample(range(8), 2))
            sol = [jobs[:cuts[0]], jobs[cuts[0]:cuts[1]], jobs[cuts[1]:]]     # empty machines allowed
            for mv in itertools.chain(neighborhood(sol, 3),
                                      (random_move(sol, 3, rng) for _ in range(30))):
                new = apply_move(sol, mv)
                self.assertEqual(flat(new), flat(sol))
                self.assertNotEqual(new, sol)

    def test_cross_machine_moves_change_assignments(self):
        sol = [[0, 1], [2]]
        moved = set()
        for mv in neighborhood(sol, 2):
            new = apply_move(sol, mv)
            for j in range(3):
                old_m = next(k for k, s in enumerate(sol) if j in s)
                new_m = next(k for k, s in enumerate(new) if j in s)
                if old_m != new_m:
                    moved.add((mv.kind, j))
        self.assertTrue(any(kind == "swap" for kind, _ in moved))
        self.assertTrue(any(kind == "insert" for kind, _ in moved))

    def test_single_machine_has_only_within_machine_moves(self):
        sol = [[0, 1, 2, 3]]
        for mv in neighborhood(sol, 1):
            self.assertEqual((mv.a, mv.b), (0, 0))

    def test_one_job_one_machine_has_no_moves(self):
        self.assertEqual(list(neighborhood([[0]], 1)), [])
        self.assertIsNone(random_move([[0]], 1, random.Random(0)))


class TestFastObjectiveMatchesCommonEvaluator(unittest.TestCase):
    def test_all_six_objectives_on_random_solutions(self):
        for seed in range(6):
            inst = random_instance(seed, n=8, m=3, releases=seed % 2 == 0)
            rng = random.Random(seed)
            for name in METRICS:
                ctx = Context(inst, name)
                for _ in range(15):
                    order = list(range(8))
                    rng.shuffle(order)
                    sol = [order[:3], order[3:5], order[5:]]
                    lists = ctx.to_lists(sol)
                    expected = evaluate(inst, decode_parallel(inst, lists))[name]
                    self.assertEqual(ctx.objective(sol), expected, name)


class TestLocalSearch(unittest.TestCase):
    def test_hand_calculated_descent(self):
        inst = parallel([("A", 5, 0, None, 1), ("B", 5, 0, None, 1), ("C", 4, 0, None, 1),
                         ("D", 4, 0, None, 1)], 2)
        start = {"M1": ["A", "B"], "M2": ["C", "D"]}                 # makespan 10
        res = local_search(inst, start, "makespan")
        # first improving move in enumeration order: swap A(M1[0]) <-> C(M2[0]) -> makespan 9
        self.assertEqual(res.history[1]["move"], "swap A(M1[0]) <-> C(M2[0])")
        self.assertEqual(res.best_lists, {"M1": ["C", "B"], "M2": ["A", "D"]})
        self.assertEqual((res.initial_objective, res.best_objective), (10, 9))
        self.assertEqual(res.termination_reason, "local_optimum")
        self.assertEqual(res.iterations, 2)             # 1 accepted move + 1 exhausted scan

    def test_more_machines_than_jobs_uses_empty_machines(self):
        inst = parallel([("A", 5, 0, None, 1), ("B", 3, 0, None, 1)], 3)
        res = local_search(inst, {"M1": ["A", "B"], "M2": [], "M3": []}, "makespan")
        self.assertEqual(res.best_objective, 5)

    def test_one_job_is_immediately_locally_optimal(self):
        inst = parallel([("A", 5, 0, None, 1)], 1)
        res = local_search(inst, {"M1": ["A"]}, "makespan")
        self.assertEqual((res.best_objective, res.termination_reason), (5, "local_optimum"))

    def test_iteration_limit(self):
        inst = random_instance(1)
        _, lists, _ = initial_solution(inst, "makespan", "LPT")
        res = local_search(inst, lists, "total_tardiness", max_iterations=1)
        self.assertIn(res.termination_reason, ("iteration_limit", "local_optimum"))
        self.assertLessEqual(res.iterations, 1)


class TestTabuSearch(unittest.TestCase):
    def test_restriction_expires_at_exact_iteration(self):
        tabu = {(5, 0, 2): 8}              # move applied at iteration 5 with tenure 3
        for it in (6, 7, 8):
            self.assertGreater(tabu_expiry(tabu, [(5, 0, 2)], it), 0)
        self.assertEqual(tabu_expiry(tabu, [(5, 0, 2)], 9), 0)
        self.assertEqual(tabu_expiry(tabu, [(5, 1, 0)], 7), 0)

    def test_immediate_reversal_is_tabu(self):
        sol = [[0, 1, 2], [3, 4]]
        cases = [(Move("insert", 0, 0, 1, 1), Move("insert", 1, 1, 0, 0)),
                 (Move("insert", 0, 0, 0, 2), Move("insert", 0, 2, 0, 0)),
                 (Move("swap", 0, 1, 1, 0), Move("swap", 1, 0, 0, 1)),
                 (Move("swap", 0, 0, 0, 2), Move("swap", 0, 2, 0, 0))]
        for it, (mv, rev) in enumerate(cases, start=1):
            old, _ = placements(sol, mv)
            after = apply_move(sol, mv)
            self.assertEqual(apply_move(after, rev), sol)                    # it IS the reversal
            tabu = {pl: it + 4 for pl in old}                                 # tenure 4
            _, rev_new = placements(after, rev)
            self.assertGreater(tabu_expiry(tabu, rev_new, it + 1), 0)

    def test_audit_no_tabu_move_without_aspiration_or_force(self):
        for seed, sample in ((1, None), (2, None), (3, 12)):
            inst = random_instance(seed)
            _, lists, _ = initial_solution(inst, "total_tardiness")
            tenure = 5
            res = tabu_search(inst, lists, "total_tardiness", tenure=tenure, max_iterations=60,
                              no_improve_limit=None, seed=seed, sample_size=sample)
            applied = res.applied_moves
            self.assertGreater(len(applied), 10)
            for pos, cur in enumerate(applied):
                for earlier in applied[:pos]:
                    s, t = earlier["iteration"], cur["iteration"]
                    hit = set(cur["new"]) & set(earlier["old"])
                    if hit and s < t <= s + tenure:
                        self.assertTrue(cur["aspirated"] or cur["forced"],
                                        "tabu placement used at iteration %d" % t)

    def test_forced_move_when_every_candidate_is_tabu(self):
        inst = parallel([("A", 3, 0, None, 1), ("B", 4, 0, None, 1)], 1)    # only one swap exists
        res = tabu_search(inst, {"M1": ["A", "B"]}, "makespan", tenure=3, max_iterations=3,
                          no_improve_limit=None, seed=0)
        self.assertEqual([m["forced"] for m in res.applied_moves], [False, True, True])
        self.assertEqual(res.extras["forced_tabu_moves"], 2)

    def test_no_moves_terminates(self):
        inst = parallel([("A", 3, 0, None, 1)], 1)
        res = tabu_search(inst, {"M1": ["A"]}, "makespan")
        self.assertEqual((res.termination_reason, res.iterations, res.best_objective), ("no_moves", 0, 3))

    def test_current_and_best_are_separate_states(self):
        # A(3), B(4) on one machine, objective total completion: A,B -> 3+7 = 10 (optimal),
        # B,A -> 4+7 = 11. The only move worsens the current solution; best must stay at 10.
        inst = parallel([("A", 3, 0, None, 1), ("B", 4, 0, None, 1)], 1)
        res = tabu_search(inst, {"M1": ["A", "B"]}, "total_completion", tenure=3, max_iterations=1,
                          no_improve_limit=None, seed=0)
        self.assertEqual(res.history[1]["current_objective"], 11)
        self.assertEqual(res.history[1]["best_objective"], 10)
        self.assertEqual((res.best_objective, res.final_current_objective), (10, 11))
        self.assertEqual(res.best_lists, {"M1": ["A", "B"]})        # best is the initial solution

    def test_best_never_gets_worse_on_a_larger_instance(self):
        inst = random_instance(4)
        _, lists, _ = initial_solution(inst, "total_tardiness")
        res = tabu_search(inst, lists, "total_tardiness", tenure=7, max_iterations=80,
                          no_improve_limit=None, seed=1)
        best_col = [r["best_objective"] for r in res.history]
        self.assertEqual(best_col, sorted(best_col, reverse=True))


class TestSimulatedAnnealing(unittest.TestCase):
    def test_acceptance_rule(self):
        self.assertTrue(sa_accept(0, 1.0, 0.999))
        self.assertTrue(sa_accept(-5, 1.0, 0.999))
        self.assertTrue(sa_accept(1, 1.0, 0.36))                  # exp(-1) = 0.3679
        self.assertFalse(sa_accept(1, 1.0, 0.37))
        self.assertTrue(sa_accept(1, 1e9, 0.999))
        self.assertFalse(sa_accept(50, 0.5, 0.01))                # exp(-100) is tiny

    def test_calibration_and_given_temperature(self):
        inst = random_instance(2)
        _, lists, _ = initial_solution(inst, "makespan")
        res = simulated_annealing(inst, lists, "makespan", max_iterations=50, seed=1)
        self.assertGreater(res.parameters["initial_temp"], 0)
        self.assertAlmostEqual(res.parameters["min_temp"], res.parameters["initial_temp"] / 1000)
        res = simulated_annealing(inst, lists, "makespan", initial_temp=10.0, min_temp=0.5,
                                  max_iterations=50, seed=1)
        self.assertEqual((res.parameters["initial_temp"], res.parameters["min_temp"]), (10.0, 0.5))

    def test_cooling_schedule(self):
        inst = parallel(DEMO, 3)
        _, lists, _ = initial_solution(inst, "makespan")
        res = simulated_annealing(inst, lists, "makespan", alpha=0.5, moves_per_temp=4,
                                  initial_temp=8.0, min_temp=1.0, seed=1)
        temps = [r["temperature"] for r in res.history[1:]]
        self.assertEqual(temps, [8.0] * 4 + [4.0] * 4 + [2.0] * 4 + [1.0] * 4)
        self.assertEqual(res.termination_reason, "min_temperature")

    def test_current_and_best_are_separate_states(self):
        inst = parallel(DEMO, 3)
        _, lists, _ = initial_solution(inst, "makespan")
        res = simulated_annealing(inst, lists, "makespan", initial_temp=50.0, max_iterations=300, seed=3)
        self.assertTrue(any(r["current_objective"] > r["best_objective"] for r in res.history))
        self.assertTrue(all(r["best_objective"] <= res.initial_objective for r in res.history))

    def test_no_moves_terminates(self):
        inst = parallel([("A", 3, 0, None, 1)], 1)
        res = simulated_annealing(inst, {"M1": ["A"]}, "makespan", seed=0)
        self.assertEqual((res.termination_reason, res.iterations, res.best_objective), ("no_moves", 0, 3))


class TestCommonSearchProperties(unittest.TestCase):
    def runners(self, inst, lists, obj, seed):
        return {"LS": local_search(inst, lists, obj, seed=seed),
                "TS": tabu_search(inst, lists, obj, max_iterations=40, seed=seed),
                "SA": simulated_annealing(inst, lists, obj, max_iterations=600, seed=seed)}

    def test_best_no_worse_than_initial_feasible_and_consistent(self):
        for seed in (1, 2, 3):
            inst = random_instance(seed, releases=seed != 2)
            for obj in ("makespan", "total_tardiness"):
                rule, lists, f0 = initial_solution(inst, obj)
                for name, res in self.runners(inst, lists, obj, seed).items():
                    rows = decode_parallel(inst, res.best_lists)
                    self.assertEqual(check_schedule(inst, rows), [], name)
                    self.assertEqual(evaluate(inst, rows)[obj], res.best_objective, name)
                    self.assertLessEqual(res.best_objective, res.initial_objective, name)
                    self.assertEqual(res.initial_objective, f0)

    def test_fixed_seed_reproduces(self):
        inst = random_instance(7)
        _, lists, _ = initial_solution(inst, "total_tardiness")
        makers = {
            "TS-full": lambda: tabu_search(inst, lists, "total_tardiness", max_iterations=30, seed=9),
            "TS-sample": lambda: tabu_search(inst, lists, "total_tardiness", max_iterations=30,
                                             seed=9, sample_size=15),
            "SA": lambda: simulated_annealing(inst, lists, "total_tardiness", max_iterations=400, seed=9),
        }
        for name, make in makers.items():
            a, b = make(), make()
            self.assertEqual(a.best_lists, b.best_lists, name)
            self.assertEqual(strip_time(a.history), strip_time(b.history), name)

    def test_different_seeds_explore_differently(self):
        inst = random_instance(7)
        _, lists, _ = initial_solution(inst, "total_tardiness")
        a = simulated_annealing(inst, lists, "total_tardiness", max_iterations=200, seed=1)
        b = simulated_annealing(inst, lists, "total_tardiness", max_iterations=200, seed=2)
        self.assertNotEqual([r["move"] for r in a.history], [r["move"] for r in b.history])

    def test_time_limit_zero_returns_initial(self):
        inst = random_instance(3)
        _, lists, f0 = initial_solution(inst, "makespan")
        for res in (local_search(inst, lists, "makespan", time_limit=0),
                    tabu_search(inst, lists, "makespan", time_limit=0),
                    simulated_annealing(inst, lists, "makespan", time_limit=0)):
            self.assertEqual((res.termination_reason, res.iterations, res.best_objective),
                             ("time_limit", 0, f0))

    def test_invalid_parameters_rejected(self):
        inst = parallel(DEMO, 3)
        _, lists, _ = initial_solution(inst, "makespan")
        bad = [lambda: simulated_annealing(inst, lists, "makespan", alpha=1.0),
               lambda: simulated_annealing(inst, lists, "makespan", alpha=0),
               lambda: simulated_annealing(inst, lists, "makespan", initial_temp=0),
               lambda: simulated_annealing(inst, lists, "makespan", min_temp=0),
               lambda: simulated_annealing(inst, lists, "makespan", initial_temp=1, min_temp=2),
               lambda: simulated_annealing(inst, lists, "makespan", moves_per_temp=0),
               lambda: tabu_search(inst, lists, "makespan", tenure=0),
               lambda: tabu_search(inst, lists, "makespan", sample_size=0),
               lambda: tabu_search(inst, lists, "makespan", max_iterations=0),
               lambda: local_search(inst, lists, "makespan", time_limit=-1),
               lambda: local_search(inst, lists, "makespan", seed="x")]
        for make in bad:
            with self.assertRaises(InputError):
                make()

    def test_environment_and_due_date_guards(self):
        single = Instance("s", "single", 1, [Job("A", 3), Job("B", 2)])
        with self.assertRaises(InputError):
            Context(single, "makespan")
        no_due = parallel([("A", 3, 0, None, 1), ("B", 2, 0, None, 1)], 2)
        with self.assertRaises(UnsupportedInputError):
            Context(no_due, "total_tardiness")
        Context(no_due, "makespan")                         # fine without due dates

    def test_initial_solution_rules(self):
        inst = parallel(DEMO, 3)
        self.assertEqual(initial_solution(inst, "makespan", "BEST")[0], "LPT")   # 23 vs SPT 27 / EDD 27
        self.assertEqual(initial_solution(inst, "total_tardiness", "BEST")[0], "EDD")
        self.assertEqual(initial_solution(inst, "makespan", "SPT")[0], "SPT")
        one = parallel([("A", 3, 0, 5, 1)], 2)
        self.assertEqual(initial_solution(one, "makespan")[0], "SPT")             # ties -> SPT first
        no_due = parallel([("A", 3, 0, None, 1), ("B", 2, 0, None, 1)], 2)
        self.assertIn(initial_solution(no_due, "makespan")[0], ("SPT", "LPT"))    # EDD skipped
        with self.assertRaises(UnsupportedInputError):
            initial_solution(no_due, "makespan", "EDD")


class TestSearchCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.jobs = os.path.join(self.dir, "jobs.csv")
        with open(self.jobs, "w", encoding="utf-8") as f:
            f.write("job_id,processing_time,release_time,due_date,weight\n")
            for jid, p, r, d, w in DEMO:
                f.write("%s,%d,%d,%d,%d\n" % (jid, p, r, d, w))

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, name, *extra):
        out = os.path.join(self.dir, name)
        code = main(["--environment", "parallel", "--input", self.jobs, "--machines", "3",
                     "--output", out] + list(extra))
        return code, out

    def read(self, out, name):
        with open(os.path.join(out, name), encoding="utf-8") as f:
            return f.read()

    def test_outputs_and_metrics_for_each_method(self):
        for method, extra in (("LS", []), ("TS", ["--max-iterations", "30"]),
                              ("SA", ["--max-iterations", "400"])):
            code, out = self.run_cli(method, "--method", method, "--objective", "makespan",
                                     "--seed", "4", *extra)
            self.assertEqual(code, 0, method)
            for name in ("schedule.csv", "metrics.json", "run_config.json", "gantt.png",
                         "search_history.csv", "convergence.png", "gantt_before_after.png"):
                self.assertTrue(os.path.getsize(os.path.join(out, name)) > 0, (method, name))
            m = json.loads(self.read(out, "metrics.json"))
            s = m["search"]
            self.assertTrue(m["feasible"])
            self.assertEqual(s["start_rule"], "LPT")
            self.assertEqual(s["initial_objective"], 23)
            self.assertLessEqual(s["best_objective"], 23)
            self.assertEqual(m["objective_value"], s["best_objective"])
            self.assertEqual(s["absolute_change"], 23 - s["best_objective"])
            self.assertAlmostEqual(s["improvement_percent"], 100.0 * (23 - s["best_objective"]) / 23, 3)
            self.assertIn("construction_runtime_s", m)
            self.assertGreaterEqual(m["end_to_end_runtime_s"], m["algorithm_runtime_s"])
            cfg = json.loads(self.read(out, "run_config.json"))
            self.assertEqual(cfg["seed"], 4)
            self.assertIn("search_parameters", cfg)

    def test_zero_baseline_reports_absolute_change_only(self):
        code, out = self.run_cli("zero", "--method", "TS", "--objective", "total_tardiness",
                                 "--max-iterations", "10")
        self.assertEqual(code, 0)
        s = json.loads(self.read(out, "metrics.json"))["search"]
        self.assertEqual((s["initial_objective"], s["best_objective"], s["absolute_change"]), (0, 0, 0))
        self.assertIsNone(s["improvement_percent"])
        self.assertIn("not applicable", s["improvement_note"])

    def test_same_seed_same_schedule_and_comparison_run(self):
        args = ["--method", "SA", "--objective", "makespan", "--max-iterations", "300", "--seed", "11"]
        _, a = self.run_cli("a", *args)
        _, b = self.run_cli("b", *args)
        self.assertEqual(self.read(a, "schedule.csv"), self.read(b, "schedule.csv"))
        code, out = self.run_cli("cmp", "--method", "LPT", "TS", "SA", "--objective", "makespan",
                                 "--max-iterations", "50")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(out, "comparison.png")))

    def test_invalid_search_options_exit_with_error(self):
        self.assertEqual(self.run_cli("x1", "--method", "SA", "--sa-alpha", "1.5")[0], 2)
        self.assertEqual(self.run_cli("x2", "--method", "TS", "--tabu-tenure", "0")[0], 2)
        self.assertEqual(self.run_cli("x3", "--method", "TS", "--init-rule", "XYZ")[0], 2)
        flow_out = os.path.join(self.dir, "x4")
        self.assertEqual(main(["--environment", "single", "--input", self.jobs, "--method", "TS",
                               "--output", flow_out]), 2)
        no_due = os.path.join(self.dir, "nodue.csv")
        with open(no_due, "w", encoding="utf-8") as f:
            f.write("job_id,processing_time\nA,3\nB,4\n")
        self.assertEqual(main(["--environment", "parallel", "--input", no_due, "--machines", "2",
                               "--method", "TS", "--objective", "total_tardiness",
                               "--output", os.path.join(self.dir, "x5")]), 2)
        self.assertEqual(main(["--environment", "parallel", "--input", no_due, "--machines", "2",
                               "--method", "TS", "--objective", "makespan", "--max-iterations", "5",
                               "--output", os.path.join(self.dir, "x6")]), 0)


if __name__ == "__main__":
    unittest.main()
