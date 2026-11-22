"""Ortak feasibility checker. Tüm yöntemler (kural, arama, solver) aynı kontrolden geçer.

check_schedule() ihlal mesajlarının listesini döndürür (boş liste = feasible).
"""
from typing import Dict, List, Tuple

from .errors import InfeasibleScheduleError
from .models import Instance, ScheduleRow


def check_schedule(inst: Instance, rows: List[ScheduleRow]) -> List[str]:
    errs = []
    mids = inst.machine_ids()
    valid, per_pair = [], {}
    for r in rows:
        if r.machine_id not in mids:
            errs.append("Invalid machine '%s' (job %s)" % (r.machine_id, r.job_id))
            continue
        if r.job_id not in inst.job_map:
            errs.append("Unknown job '%s'" % r.job_id)
            continue
        if r.start_time < 0:
            errs.append("(%s,%s): negative start time" % (r.job_id, r.machine_id))
        expected = inst.p(r.job_id, r.machine_id)
        if r.end_time - r.start_time != expected:
            errs.append("(%s,%s): duration %d, expected %d"
                        % (r.job_id, r.machine_id, r.end_time - r.start_time, expected))
        valid.append(r)
        per_pair.setdefault((r.job_id, r.machine_id), []).append(r)

    # --- makine başına çakışma
    by_machine: Dict[str, List[ScheduleRow]] = {m: [] for m in mids}
    for r in valid:
        by_machine[r.machine_id].append(r)
    for m, rs in by_machine.items():
        rs = sorted(rs, key=lambda r: (r.start_time, r.end_time))
        for a, b in zip(rs, rs[1:]):
            if b.start_time < a.end_time:
                errs.append("Overlap on %s: %s [%d,%d) and %s [%d,%d)"
                            % (m, a.job_id, a.start_time, a.end_time,
                               b.job_id, b.start_time, b.end_time))

    if inst.environment != "flowshop":
        for jid, job in inst.job_map.items():
            rs = [r for r in valid if r.job_id == jid]
            if not rs:
                errs.append("Job %s is missing from the schedule" % jid)
            elif len(rs) > 1:
                errs.append("Job %s appears %d times in the schedule" % (jid, len(rs)))
            for r in rs:
                if r.start_time < job.release_time:
                    errs.append("Job %s release violation: start %d < r=%d"
                                % (jid, r.start_time, job.release_time))
        return errs

    # --- flow shop
    complete = True
    for jid in inst.job_map:
        for m in mids:
            n = len(per_pair.get((jid, m), []))
            if n == 0:
                errs.append("Missing operation (%s,%s)" % (jid, m)); complete = False
            elif n > 1:
                errs.append("Duplicate operation (%s,%s)" % (jid, m)); complete = False
    if not complete:
        return errs
    op = {k: v[0] for k, v in per_pair.items()}
    for jid, job in inst.job_map.items():
        if op[(jid, mids[0])].start_time < job.release_time:
            errs.append("Job %s release violation on M1" % jid)
        for a, b in zip(mids, mids[1:]):
            if op[(jid, b)].start_time < op[(jid, a)].end_time:
                errs.append("Job %s: %s starts before %s finishes (route violation)" % (jid, b, a))
    orders = {m: [r.job_id for r in sorted(by_machine[m], key=lambda r: r.start_time)]
              for m in mids}
    for m in mids[1:]:
        if orders[m] != orders[mids[0]]:
            errs.append("Common job order violated: %s order %s, %s order %s"
                        % (m, orders[m], mids[0], orders[mids[0]]))
    return errs


def assert_feasible(inst: Instance, rows: List[ScheduleRow]) -> None:
    errs = check_schedule(inst, rows)
    if errs:
        raise InfeasibleScheduleError("Schedule is not feasible: " + "; ".join(errs))