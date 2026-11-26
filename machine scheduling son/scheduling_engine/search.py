"""Improvement methods for identical parallel machines: local search, Tabu Search, Simulated Annealing.

REPRESENTATION
    A solution is one ordered list of jobs per machine (list of lists of job indices).
    Start/end times come from the common decoder (decoder.decode_parallel):
    start = max(previous end on the machine, release time). Every returned solution is
    re-decoded, checked and evaluated by the common checker/evaluator in cli.py.

NEIGHBORHOODS (positions are 0-based; machine k is shown as M(k+1))
    swap(a,i,b,k)    exchange the job at position i of machine a with the job at position k
                     of machine b. Within a machine (a == b) or between machines (a != b).
    insert(a,i,b,k)  remove the job at position i of machine a and insert it so that it ends
                     up at position k of machine b (k counted AFTER the removal, 0..len(b')
                     where b' is machine b without the removed job). Cross-machine inserts
                     change the job's machine; inserting into an empty machine is allowed.
    Excluded: identity moves (same machine and k == i) and within-machine inserts with
    |k - i| == 1, which produce exactly the same sequence as an adjacent swap.
    Enumeration order (full neighborhood): all swaps by (position of 1st job, position of
    2nd job) in machine-major order, then all inserts by (source position, target machine,
    target position). Full neighborhood size: N(N-1)/2 swaps plus the inserts above.

Randomness: every method uses one random.Random(seed). Local search is deterministic.
"""
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .decoder import decode_parallel
from .dispatch import dispatch_parallel
from .errors import InputError, UnsupportedInputError
from .models import Instance
from .objectives import DUE_METRICS, METRICS, evaluate

SEARCH_METHODS = ("LS", "TS", "SA")
Solution = List[List[int]]
Placement = Tuple[int, int, int]          # (job index, machine index, position)

HISTORY_FIELDS = ["iteration", "elapsed_s", "candidate_objective", "current_objective",
                  "best_objective", "move", "accepted", "temperature",
                  "candidate_tabu", "aspiration_used", "forced_tabu_move"]


@dataclass(frozen=True)
class Move:
    kind: str      # "swap" or "insert"
    a: int         # machine of the first/moved job
    i: int         # position of the first/moved job
    b: int         # swap: machine of the second job; insert: target machine
    k: int         # swap: position of the second job; insert: final position in target machine


@dataclass
class SearchResult:
    method: str
    best_lists: Dict[str, List[str]]
    initial_objective: int
    best_objective: int
    final_current_objective: int
    iterations: int
    evaluations: int
    termination_reason: str
    elapsed_s: float
    parameters: dict
    history: List[dict] = field(default_factory=list)
    extras: dict = field(default_factory=dict)
    applied_moves: list = field(default_factory=list)     # Tabu Search: audit trail of applied moves


# ------------------------------------------------------------------ evaluation context
class Context:
    """Fast objective evaluation on index-based data (same formulas as objectives.evaluate)."""

    def __init__(self, inst: Instance, objective: str):
        if inst.environment != "parallel":
            raise InputError("Improvement methods are implemented for the 'parallel' environment "
                             "(use --environment parallel; --machines 1 is allowed).")
        if objective not in METRICS:
            raise InputError("Unknown objective '%s'. Options: %s" % (objective, ", ".join(METRICS)))
        if objective in DUE_METRICS and not inst.has_due_dates:
            raise UnsupportedInputError(
                "The '%s' objective requires due_date for every job; some jobs have no due_date."
                % objective)
        self.ids = [j.job_id for j in inst.jobs]
        self.index = {jid: n for n, jid in enumerate(self.ids)}
        self.m = inst.machines
        self.p = [j.processing_time for j in inst.jobs]
        self.r = [j.release_time for j in inst.jobs]
        self.w = [j.weight for j in inst.jobs]
        self.d = [j.due_date if j.due_date is not None else 0 for j in inst.jobs]
        self.which = METRICS.index(objective)
        self.evaluations = 0

    def objective(self, sol: Solution) -> int:
        self.evaluations += 1
        p, r, w, d = self.p, self.r, self.w, self.d
        mk = tc = wc = tt = wt = 0
        lmax = None
        for seq in sol:
            t = 0
            for j in seq:
                rj = r[j]
                t = (t if t >= rj else rj) + p[j]
                tc += t
                wc += w[j] * t
                late = t - d[j]
                if lmax is None or late > lmax:
                    lmax = late
                if late > 0:
                    tt += late
                    wt += w[j] * late
            if t > mk:
                mk = t
        return (mk, tc, wc, lmax, tt, wt)[self.which]

    def to_solution(self, lists: Dict[str, List[str]]) -> Solution:
        return [[self.index[j] for j in lists.get("M%d" % (k + 1), [])] for k in range(self.m)]

    def to_lists(self, sol: Solution) -> Dict[str, List[str]]:
        return {"M%d" % (k + 1): [self.ids[j] for j in seq] for k, seq in enumerate(sol)}


# ------------------------------------------------------------------ moves
def apply_move(sol: Solution, mv: Move) -> Solution:
    """Returns a NEW solution; the input and unchanged machine lists are never mutated."""
    new = list(sol)
    if mv.kind == "swap":
        if mv.a == mv.b:
            seq = list(sol[mv.a])
            seq[mv.i], seq[mv.k] = seq[mv.k], seq[mv.i]
            new[mv.a] = seq
        else:
            sa, sb = list(sol[mv.a]), list(sol[mv.b])
            sa[mv.i], sb[mv.k] = sb[mv.k], sa[mv.i]
            new[mv.a], new[mv.b] = sa, sb
    else:
        if mv.a == mv.b:
            seq = list(sol[mv.a])
            job = seq.pop(mv.i)
            seq.insert(mv.k, job)
            new[mv.a] = seq
        else:
            sa, sb = list(sol[mv.a]), list(sol[mv.b])
            job = sa.pop(mv.i)
            sb.insert(mv.k, job)
            new[mv.a], new[mv.b] = sa, sb
    return new


def neighborhood(sol: Solution, m: int) -> Iterator[Move]:
    """Full neighborhood in the documented deterministic order."""
    pos = [(a, i) for a in range(m) for i in range(len(sol[a]))]
    for x in range(len(pos)):
        for y in range(x + 1, len(pos)):
            (a, i), (b, k) = pos[x], pos[y]
            yield Move("swap", a, i, b, k)
    for (a, i) in pos:
        for b in range(m):
            size = len(sol[b]) - (1 if b == a else 0)
            for k in range(size + 1):
                if b == a and abs(k - i) <= 1:
                    continue          # identity (k == i) or same as an adjacent swap
                yield Move("insert", a, i, b, k)


def random_move(sol: Solution, m: int, rng: random.Random) -> Optional[Move]:
    """One random valid, non-identity move: swap or insert with probability 1/2 each
    (parameters drawn uniformly). Returns None if no valid move was found (e.g. 1 job, 1 machine)."""
    pos = [(a, i) for a in range(m) for i in range(len(sol[a]))]
    for _ in range(50):
        if len(pos) >= 2 and rng.random() < 0.5:
            (a, i), (b, k) = rng.sample(pos, 2)
            return Move("swap", a, i, b, k)
        a, i = rng.choice(pos)
        b = rng.randrange(m)
        size = len(sol[b]) - (1 if b == a else 0)
        k = rng.randint(0, size)
        if b == a and k == i:
            continue
        return Move("insert", a, i, b, k)
    return None


def placements(sol: Solution, mv: Move) -> Tuple[List[Placement], List[Placement]]:
    """(old, new) placements (job, machine, position) of the jobs the move relocates."""
    if mv.kind == "swap":
        x, y = sol[mv.a][mv.i], sol[mv.b][mv.k]
        return [(x, mv.a, mv.i), (y, mv.b, mv.k)], [(x, mv.b, mv.k), (y, mv.a, mv.i)]
    j = sol[mv.a][mv.i]
    return [(j, mv.a, mv.i)], [(j, mv.b, mv.k)]


def describe_move(sol: Solution, mv: Move, ids: List[str]) -> str:
    if mv.kind == "swap":
        return "swap %s(M%d[%d]) <-> %s(M%d[%d])" % (
            ids[sol[mv.a][mv.i]], mv.a + 1, mv.i, ids[sol[mv.b][mv.k]], mv.b + 1, mv.k)
    return "insert %s M%d[%d] -> M%d[%d]" % (ids[sol[mv.a][mv.i]], mv.a + 1, mv.i, mv.b + 1, mv.k)


# ------------------------------------------------------------------ starting solution
def initial_solution(inst: Instance, objective: str, rule: str = "BEST"):
    """Documented starting schedule. rule = SPT/LPT/EDD/WSPT/ERD uses that dispatching rule;
    BEST picks, for the selected objective, the best of SPT, LPT, EDD (EDD only when every job
    has a due date). Ties are broken deterministically by the order SPT, LPT, EDD.
    Returns (rule_used, machine_lists, objective_value)."""
    rule = rule.upper()
    if rule == "BEST":
        candidates = ["SPT", "LPT"] + (["EDD"] if inst.has_due_dates else [])
    else:
        candidates = [rule]
    best = None
    for r in candidates:
        lists = dispatch_parallel(inst, r).machine_lists
        value = evaluate(inst, decode_parallel(inst, lists))[objective]
        if value is None:
            raise UnsupportedInputError("Objective '%s' is not available for this instance." % objective)
        if best is None or value < best[2]:
            best = (r, lists, value)
    return best


# ------------------------------------------------------------------ common helpers
def _check_limits(max_iterations, time_limit, seed):
    if max_iterations is not None and (not isinstance(max_iterations, int) or max_iterations < 1):
        raise InputError("max_iterations must be a positive integer.")
    if time_limit is not None and time_limit < 0:
        raise InputError("time_limit must be >= 0 seconds.")
    if not isinstance(seed, int):
        raise InputError("seed must be an integer.")


def _row(it, t0, cand, cur, best, move="", accepted="", temp="", tabu="", asp="", forced=""):
    return {"iteration": it, "elapsed_s": round(time.perf_counter() - t0, 6),
            "candidate_objective": cand, "current_objective": cur, "best_objective": best,
            "move": move, "accepted": accepted, "temperature": temp,
            "candidate_tabu": tabu, "aspiration_used": asp, "forced_tabu_move": forced}


def write_history_csv(path: str, history: List[dict]) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        for row in history:
            w.writerow(row)


# ------------------------------------------------------------------ local search
def local_search(inst: Instance, init_lists, objective: str, max_iterations: Optional[int] = None,
                 time_limit: Optional[float] = None, seed: int = 0) -> SearchResult:
    """First-improvement descent over the FULL neighborhood in the documented order.
    After every accepted move the enumeration restarts from the first move. One iteration =
    one neighborhood scan (ends with an accepted move or an exhausted scan). Stops with
    'local_optimum' when a complete scan finds no strictly improving move (a true local
    optimum for the full neighborhood), or 'iteration_limit' / 'time_limit'."""
    _check_limits(max_iterations, time_limit, seed)
    ctx = Context(inst, objective)
    t0 = time.perf_counter()
    cur = ctx.to_solution(init_lists)
    cur_f = init_f = ctx.objective(cur)
    history = [_row(0, t0, cur_f, cur_f, cur_f, "initial", True)]
    out_of_time = lambda: time_limit is not None and time.perf_counter() - t0 >= time_limit
    it, reason = 0, None
    while reason is None:
        if max_iterations is not None and it >= max_iterations:
            reason = "iteration_limit"
            break
        if out_of_time():
            reason = "time_limit"
            break
        it += 1
        improved = False
        for n_seen, mv in enumerate(neighborhood(cur, ctx.m)):
            if n_seen % 64 == 63 and out_of_time():
                reason = "time_limit"
                break
            cand = apply_move(cur, mv)
            f = ctx.objective(cand)
            if f < cur_f:
                label = describe_move(cur, mv, ctx.ids)
                cur, cur_f, improved = cand, f, True
                history.append(_row(it, t0, f, cur_f, cur_f, label, True))
                break
        if reason is None and not improved:
            reason = "local_optimum"
    return SearchResult("LS", ctx.to_lists(cur), init_f, cur_f, cur_f, it, ctx.evaluations, reason,
                        time.perf_counter() - t0,
                        {"max_iterations": max_iterations, "time_limit": time_limit,
                         "neighborhood": "full", "policy": "first-improvement, restart after accepted move"},
                        history)


# ------------------------------------------------------------------ tabu search
def tabu_expiry(tabu: Dict[Placement, int], pls: List[Placement], it: int) -> int:
    """Largest expiry iteration among the restrictions still active at iteration `it`
    that the placements hit; 0 when none. A restriction stored with value e is active for
    iterations <= e."""
    return max((tabu.get(pl, 0) for pl in pls if tabu.get(pl, 0) >= it), default=0)


def tabu_search(inst: Instance, init_lists, objective: str, tenure: int = 7,
                max_iterations: Optional[int] = 300, no_improve_limit: Optional[int] = 100,
                time_limit: Optional[float] = None, seed: int = 0,
                sample_size: Optional[int] = None) -> SearchResult:
    """Tabu Search with an assignment-position tabu list.

    Tabu attribute: (job, machine, position). When a move is applied at iteration t, every
    relocated job's OLD (machine, position) becomes tabu with expiry t + tenure: placing that
    job back there is forbidden in iterations t+1 .. t+tenure and allowed again at t+tenure+1.
    A candidate move is tabu if ANY job it relocates is placed on a tabu (machine, position),
    so the immediate reversal of the previous move is always tabu.
    Each iteration evaluates the full neighborhood (sample_size=None) or sample_size random
    moves, and applies the best ADMISSIBLE candidate even if it is worse than the current
    solution. Admissible = not tabu, or tabu but better than the global best (aspiration).
    Ties are broken uniformly at random (seeded). If no candidate is admissible, the tabu
    candidate whose restriction expires soonest is applied (ties: lower objective, then
    first in enumeration order) and flagged forced_tabu_move. No candidate at all -> 'no_moves'.
    Current and best solutions are kept separately; the best (including the initial) is returned.
    Stops: iteration_limit, no_improvement (iterations without a new global best), time_limit."""
    _check_limits(max_iterations, time_limit, seed)
    if not isinstance(tenure, int) or tenure < 1:
        raise InputError("tabu tenure must be a positive integer.")
    if no_improve_limit is not None and no_improve_limit < 1:
        raise InputError("no_improve_limit must be a positive integer.")
    if sample_size is not None and sample_size < 1:
        raise InputError("neighborhood sample size must be a positive integer.")
    ctx = Context(inst, objective)
    rng = random.Random(seed)
    t0 = time.perf_counter()
    cur = ctx.to_solution(init_lists)
    cur_f = best_f = init_f = ctx.objective(cur)
    best = cur
    history = [_row(0, t0, cur_f, cur_f, best_f, "initial", True)]
    out_of_time = lambda: time_limit is not None and time.perf_counter() - t0 >= time_limit
    tabu: Dict[Placement, int] = {}
    applied, forced_count, aspiration_count = [], 0, 0
    it, since_best, reason = 0, 0, None
    while reason is None:
        if max_iterations is not None and it >= max_iterations:
            reason = "iteration_limit"
            break
        if no_improve_limit is not None and since_best >= no_improve_limit:
            reason = "no_improvement"
            break
        if out_of_time():
            reason = "time_limit"
            break
        it += 1
        tabu = {pl: e for pl, e in tabu.items() if e >= it}          # drop expired restrictions
        if sample_size is None:
            moves = neighborhood(cur, ctx.m)
        else:
            moves = (random_move(cur, ctx.m, rng) for _ in range(sample_size))
        choice, ties = None, 0                    # best admissible candidate (reservoir tie-break)
        forced = None                             # fallback: tabu candidate expiring soonest
        n_seen = 0
        for mv in moves:
            if mv is None:
                continue
            n_seen += 1
            if n_seen % 64 == 0 and out_of_time():
                reason = "time_limit"
                break
            old_pl, new_pl = placements(cur, mv)
            f = ctx.objective(apply_move(cur, mv))
            expiry = tabu_expiry(tabu, new_pl, it)
            is_tabu = expiry > 0
            aspirated = is_tabu and f < best_f
            if not is_tabu or aspirated:
                if choice is None or f < choice[1]:
                    choice, ties = (mv, f, old_pl, new_pl, is_tabu, aspirated), 1
                elif f == choice[1]:
                    ties += 1
                    if rng.randrange(ties) == 0:
                        choice = (mv, f, old_pl, new_pl, is_tabu, aspirated)
            elif forced is None or (expiry, f) < (forced[0], forced[2]):
                forced = (expiry, mv, f, old_pl, new_pl)
        if reason is not None:
            it -= 1
            break
        if choice is None and forced is None:
            reason = "no_moves"
            it -= 1
            break
        if choice is not None:
            mv, f, old_pl, new_pl, was_tabu, aspirated = choice
            is_forced = False
        else:
            _, mv, f, old_pl, new_pl = forced
            was_tabu, aspirated, is_forced = True, False, True
            forced_count += 1
        aspiration_count += 1 if aspirated else 0
        label = describe_move(cur, mv, ctx.ids)
        cur = apply_move(cur, mv)
        cur_f = f
        for pl in old_pl:
            tabu[pl] = it + tenure                                    # active for it+1 .. it+tenure
        if cur_f < best_f:
            best, best_f, since_best = cur, cur_f, 0
        else:
            since_best += 1
        applied.append({"iteration": it, "move": mv, "old": old_pl, "new": new_pl,
                        "was_tabu": was_tabu, "aspirated": aspirated, "forced": is_forced})
        history.append(_row(it, t0, f, cur_f, best_f, label, True, "", was_tabu, aspirated, is_forced))
    return SearchResult("TS", ctx.to_lists(best), init_f, best_f, cur_f, it, ctx.evaluations, reason,
                        time.perf_counter() - t0,
                        {"tenure": tenure, "max_iterations": max_iterations,
                         "no_improve_limit": no_improve_limit, "time_limit": time_limit,
                         "neighborhood": "full" if sample_size is None else "sample of %d random moves" % sample_size,
                         "tabu_attribute": "(job, machine, position) just left",
                         "tie_breaking": "uniform random (seeded)", "seed": seed},
                        history, {"forced_tabu_moves": forced_count,
                                  "aspiration_moves": aspiration_count}, applied)


# ------------------------------------------------------------------ simulated annealing
def sa_accept(delta: float, temp: float, u: float) -> bool:
    """Metropolis rule for minimization: accept if delta <= 0, else with probability
    exp(-delta/temp) using the uniform draw u in [0, 1)."""
    if delta <= 0:
        return True
    return u < math.exp(-delta / temp)


def calibrate_initial_temperature(ctx: Context, sol: Solution, f0: int, rng: random.Random,
                                  samples: int = 100, accept_prob: float = 0.5):
    """T0 such that an average uphill move is accepted with probability accept_prob:
    T0 = mean_uphill_delta / ln(1/accept_prob), from `samples` random moves of the start
    solution. Falls back to 1.0 when no sampled move is uphill."""
    ups = []
    for _ in range(samples):
        mv = random_move(sol, ctx.m, rng)
        if mv is None:
            break
        delta = ctx.objective(apply_move(sol, mv)) - f0
        if delta > 0:
            ups.append(delta)
    if not ups:
        return 1.0, {"rule": "no uphill moves sampled; T0 = 1.0", "samples": samples}
    mean_up = sum(ups) / len(ups)
    return mean_up / math.log(1.0 / accept_prob), {
        "rule": "T0 = mean uphill delta / ln(1/%.2f) over %d sampled moves" % (accept_prob, samples),
        "mean_uphill_delta": mean_up, "uphill_samples": len(ups)}


def simulated_annealing(inst: Instance, init_lists, objective: str, alpha: float = 0.95,
                        moves_per_temp: Optional[int] = None, min_temp: Optional[float] = None,
                        initial_temp: Optional[float] = None, max_iterations: Optional[int] = None,
                        time_limit: Optional[float] = None, seed: int = 0) -> SearchResult:
    """Simulated Annealing, one random move per iteration (see random_move).
    delta = f(candidate) - f(current); accepted by sa_accept with a seeded uniform draw.
    Geometric cooling T <- alpha * T after every `moves_per_temp` iterations (default
    max(20, 3n)). Initial temperature: given, or calibrated (calibrate_initial_temperature).
    Minimum temperature: given, or T0/1000. Stops: min_temperature, iteration_limit, time_limit,
    no_moves. The best-so-far solution (including the initial) is returned; the current state
    is kept separately."""
    _check_limits(max_iterations, time_limit, seed)
    if not (0 < alpha < 1):
        raise InputError("SA cooling factor alpha must satisfy 0 < alpha < 1.")
    if moves_per_temp is not None and moves_per_temp < 1:
        raise InputError("SA moves per temperature level must be a positive integer.")
    if initial_temp is not None and initial_temp <= 0:
        raise InputError("SA initial temperature must be > 0.")
    if min_temp is not None and min_temp <= 0:
        raise InputError("SA minimum temperature must be > 0.")
    ctx = Context(inst, objective)
    rng = random.Random(seed)
    t0 = time.perf_counter()
    cur = ctx.to_solution(init_lists)
    cur_f = best_f = init_f = ctx.objective(cur)
    best = cur
    if initial_temp is None:
        temp, calib = calibrate_initial_temperature(ctx, cur, cur_f, rng)
    else:
        temp, calib = float(initial_temp), {"rule": "given"}
    t_start = temp
    t_min = float(min_temp) if min_temp is not None else t_start / 1000.0
    if t_min >= t_start:
        raise InputError("SA minimum temperature (%.6g) must be below the initial temperature (%.6g)."
                         % (t_min, t_start))
    level_len = moves_per_temp if moves_per_temp is not None else max(20, 3 * len(ctx.ids))
    history = [_row(0, t0, cur_f, cur_f, best_f, "initial", True, round(temp, 6))]
    it, accepted_count, reason = 0, 0, None
    while reason is None and temp >= t_min:
        for _ in range(level_len):
            if max_iterations is not None and it >= max_iterations:
                reason = "iteration_limit"
                break
            if time_limit is not None and time.perf_counter() - t0 >= time_limit:
                reason = "time_limit"
                break
            mv = random_move(cur, ctx.m, rng)
            if mv is None:
                reason = "no_moves"
                break
            it += 1
            cand = apply_move(cur, mv)
            f = ctx.objective(cand)
            label = describe_move(cur, mv, ctx.ids)
            accept = sa_accept(f - cur_f, temp, rng.random())
            if accept:
                cur, cur_f = cand, f
                accepted_count += 1
                if cur_f < best_f:
                    best, best_f = cur, cur_f
            history.append(_row(it, t0, f, cur_f, best_f, label, accept, round(temp, 6)))
        temp *= alpha
    if reason is None:
        reason = "min_temperature"
    return SearchResult("SA", ctx.to_lists(best), init_f, best_f, cur_f, it, ctx.evaluations, reason,
                        time.perf_counter() - t0,
                        {"alpha": alpha, "moves_per_temp": level_len, "initial_temp": t_start,
                         "min_temp": t_min, "max_iterations": max_iterations,
                         "time_limit": time_limit, "seed": seed,
                         "initial_temp_calibration": calib,
                         "neighbor": "random swap/insert (p=0.5 each)"},
                        history, {"accepted_moves": accepted_count, "final_temperature": temp})
