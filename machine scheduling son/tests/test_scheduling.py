"""El hesabıyla doğrulanmış zamanlama, ölçüt ve dispatching testleri."""
import unittest

from scheduling_engine.decoder import decode_flowshop, decode_parallel, decode_single
from scheduling_engine.dispatch import (dispatch_parallel, dispatch_single,
                                        flowshop_permutation)
from scheduling_engine.errors import InputError, UnsupportedInputError
from scheduling_engine.objectives import evaluate
from tests.helpers import flow, pairs, parallel, single


class TestSingleDecodeAndMetrics(unittest.TestCase):
    def setUp(self):
        # A(p3,d4,w1) B(p2,d10,w2) C(p4,d6,w1), sıra A,B,C
        self.inst = single([("A", 3, 0, 4, 1), ("B", 2, 0, 10, 2), ("C", 4, 0, 6, 1)])
        self.rows = decode_single(self.inst, ["A", "B", "C"])

    def test_times(self):
        self.assertEqual(pairs(self.rows), [("A", "M1", 0, 3), ("B", "M1", 3, 5), ("C", "M1", 5, 9)])

    def test_hand_calculated_metrics(self):
        # C = 3,5,9 ; L = -1,-5,3 ; T = 0,0,3
        m = evaluate(self.inst, self.rows)
        self.assertEqual(m["makespan"], 9)
        self.assertEqual(m["total_completion"], 17)
        self.assertEqual(m["weighted_completion"], 3 + 10 + 9)
        self.assertEqual(m["max_lateness"], 3)
        self.assertEqual(m["total_tardiness"], 3)
        self.assertEqual(m["weighted_tardiness"], 3)

    def test_negative_max_lateness_and_zero_tardiness(self):
        inst = single([("A", 2, 0, 10, 1), ("B", 3, 0, 20, 1)])
        m = evaluate(inst, decode_single(inst, ["A", "B"]))
        self.assertEqual(m["max_lateness"], -8)       # C=2,5 ; L=-8,-15 -> max = -8
        self.assertEqual(m["total_tardiness"], 0)

    def test_missing_job_in_sequence_rejected(self):
        with self.assertRaises(InputError):
            decode_single(self.inst, ["A", "B"])
        with self.assertRaises(InputError):
            decode_single(self.inst, ["A", "B", "B"])

    def test_one_job(self):
        inst = single([("A", 5, 0, 3, 1)])
        rows = decode_single(inst, ["A"])
        self.assertEqual(pairs(rows), [("A", "M1", 0, 5)])
        self.assertEqual(evaluate(inst, rows)["total_tardiness"], 2)

    def test_missing_due_dates_give_unavailable_metrics(self):
        inst = single([("A", 2, 0, None, 1), ("B", 3, 0, 5, 1)])
        m = evaluate(inst, decode_single(inst, ["A", "B"]))
        self.assertIsNone(m["total_tardiness"])
        self.assertEqual(m["makespan"], 5)


class TestReleaseAndDispatching(unittest.TestCase):
    def setUp(self):
        # A: p=2, r=5 ; B: p=3, r=0
        self.inst = single([("A", 2, 5, None, 1), ("B", 3, 0, None, 1)])

    def test_fixed_sequence_idle_interval(self):
        rows = decode_single(self.inst, ["B", "A"])
        self.assertEqual(pairs(rows), [("B", "M1", 0, 3), ("A", "M1", 5, 7)])   # [3,5) boş

    def test_dynamic_dispatch_differs_from_global_sort(self):
        # SPT: A daha kısa ama t=0'da yalnızca B hazır -> önce B, sonra A (boşluk 3-5)
        dyn = decode_single(self.inst, dispatch_single(self.inst, "SPT").machine_lists["M1"])
        self.assertEqual(pairs(dyn), [("B", "M1", 0, 3), ("A", "M1", 5, 7)])
        # Önce global sırala (A,B) sonra decode et: makespan 10
        glob = decode_single(self.inst, ["A", "B"])
        self.assertEqual(max(r.end_time for r in glob), 10)
        self.assertEqual(max(r.end_time for r in dyn), 7)

    def test_clock_jumps_to_next_release(self):
        inst = single([("A", 2, 4, None, 1)])
        self.assertEqual(pairs(decode_single(inst, ["A"])), [("A", "M1", 4, 6)])
        res = dispatch_single(inst, "SPT")
        self.assertEqual(res.trace[0].time, 4)


class TestRulesAndTies(unittest.TestCase):
    def test_all_single_rules_hand_checked(self):
        inst = single([("A", 4, 0, 9, 1), ("B", 2, 0, 3, 4), ("C", 6, 0, 5, 3)])
        seq = lambda r: dispatch_single(inst, r).machine_lists["M1"]
        self.assertEqual(seq("SPT"), ["B", "A", "C"])
        self.assertEqual(seq("LPT"), ["C", "A", "B"])
        self.assertEqual(seq("EDD"), ["B", "C", "A"])
        self.assertEqual(seq("WSPT"), ["B", "C", "A"])     # p/w: B=.5, C=2, A=4
        self.assertEqual(seq("ERD"), ["A", "B", "C"])      # hepsi r=0 -> job_id sırası

    def test_equal_priority_tie_break_by_string_id(self):
        inst = single([("J2", 3, 0, 5, 1), ("J10", 3, 0, 5, 1)])
        # string sıralama: "J10" < "J2"
        self.assertEqual(dispatch_single(inst, "SPT").machine_lists["M1"], ["J10", "J2"])
        self.assertEqual(dispatch_single(inst, "EDD").machine_lists["M1"], ["J10", "J2"])

    def test_edd_requires_due_dates(self):
        inst = single([("A", 3, 0, None, 1), ("B", 2, 0, 4, 1)])
        with self.assertRaises(UnsupportedInputError):
            dispatch_single(inst, "EDD")
        with self.assertRaises(UnsupportedInputError):
            dispatch_parallel(parallel([("A", 3, 0, None, 1)], 2), "EDD")
        dispatch_single(inst, "SPT")      # diğer yöntemler çalışır

    def test_unknown_rule(self):
        with self.assertRaises(InputError):
            dispatch_single(single([("A", 1, 0, 1, 1)]), "XYZ")


class TestParallelDispatch(unittest.TestCase):
    JOBS = [("J1", 5, 0, 1, 1), ("J2", 3, 0, 1, 1), ("J3", 4, 0, 1, 1), ("J4", 2, 0, 1, 1)]

    def test_spt_hand_trace(self):
        inst = parallel(self.JOBS, 2)
        res = dispatch_parallel(inst, "SPT")
        self.assertEqual(res.machine_lists, {"M1": ["J4", "J3"], "M2": ["J2", "J1"]})
        self.assertEqual([(t.time, t.job_id, t.machine_id) for t in res.trace],
                         [(0, "J4", "M1"), (0, "J2", "M2"), (2, "J3", "M1"), (3, "J1", "M2")])
        m = evaluate(inst, decode_parallel(inst, res.machine_lists))
        self.assertEqual((m["makespan"], m["total_completion"]), (8, 2 + 3 + 6 + 8))

    def test_lpt_hand_trace(self):
        inst = parallel(self.JOBS, 2)
        res = dispatch_parallel(inst, "LPT")
        self.assertEqual(res.machine_lists, {"M1": ["J1", "J4"], "M2": ["J3", "J2"]})
        m = evaluate(inst, decode_parallel(inst, res.machine_lists))
        self.assertEqual((m["makespan"], m["total_completion"]), (7, 5 + 4 + 7 + 7))

    def test_more_machines_than_jobs(self):
        inst = parallel([("A", 3, 0, 1, 1), ("B", 2, 0, 1, 1)], 3)
        res = dispatch_parallel(inst, "SPT")
        self.assertEqual(res.machine_lists, {"M1": ["B"], "M2": ["A"], "M3": []})

    def test_release_induced_idle_on_parallel(self):
        # M1,M2; A(p4,r0) B(p4,r0) C(p2,r6): C başlar t=6; M1 [4,6) boş
        inst = parallel([("A", 4, 0, 1, 1), ("B", 4, 0, 1, 1), ("C", 2, 6, 1, 1)], 2)
        rows = decode_parallel(inst, dispatch_parallel(inst, "SPT").machine_lists)
        self.assertEqual(pairs(rows), [("A", "M1", 0, 4), ("C", "M1", 6, 8), ("B", "M2", 0, 4)])

    def test_m1_consistency_with_single_dispatcher(self):
        jobs = [("A", 4, 3, 9, 2), ("B", 2, 0, 3, 4), ("C", 6, 1, 5, 3), ("D", 2, 8, 7, 1),
                ("E", 5, 20, 30, 2)]
        for rule in ("SPT", "LPT", "EDD", "WSPT", "ERD"):
            s = dispatch_single(single(jobs), rule).machine_lists
            p = dispatch_parallel(parallel(jobs, 1), rule).machine_lists
            self.assertEqual(s, p, rule)


class TestFlowShop(unittest.TestCase):
    def setUp(self):
        ops = {("J1", "M1"): 3, ("J1", "M2"): 2, ("J2", "M1"): 2, ("J2", "M2"): 4,
               ("J3", "M1"): 4, ("J3", "M2"): 1}
        self.inst = flow([("J1", 0, 6, 1), ("J2", 4, 9, 1), ("J3", 0, 11, 1)], ops, 2)

    def test_hand_traced_decode_with_release_wait(self):
        rows = decode_flowshop(self.inst, ["J1", "J2", "J3"])
        self.assertEqual(sorted(pairs(rows)), sorted([
            ("J1", "M1", 0, 3), ("J2", "M1", 4, 6), ("J3", "M1", 6, 10),      # M1'de [3,4) boş
            ("J1", "M2", 3, 5), ("J2", "M2", 6, 10), ("J3", "M2", 10, 11)]))
        m = evaluate(self.inst, rows)
        self.assertEqual(m["makespan"], 11)                 # son makine bitişleri 5,10,11
        self.assertEqual(m["total_completion"], 26)
        self.assertEqual(m["max_lateness"], 1)              # L = -1, 1, 0
        self.assertEqual(m["total_tardiness"], 1)

    def test_permutation_rules(self):
        # toplam süre: J1=5, J2=6, J3=5
        self.assertEqual(flowshop_permutation(self.inst, "SPT"), ["J1", "J3", "J2"])
        self.assertEqual(flowshop_permutation(self.inst, "LPT"), ["J2", "J1", "J3"])
        self.assertEqual(flowshop_permutation(self.inst, "EDD"), ["J1", "J2", "J3"])

    def test_one_machine_flowshop_equals_single_decoder(self):
        data = [("A", 3, 2, 9, 1), ("B", 2, 0, 5, 1), ("C", 4, 10, 20, 1)]
        s = single(data)
        f = flow([(i, r, d, w) for i, p, r, d, w in data],
                 {(i, "M1"): p for i, p, r, d, w in data}, 1)
        seq = ["B", "A", "C"]
        self.assertEqual(pairs(decode_single(s, seq)), pairs(decode_flowshop(f, seq)))


if __name__ == "__main__":
    unittest.main()
