"""Ortak amaç fonksiyonu hesaplayıcı (altı ölçüt). Hepsi minimize edilir.

Tamamlanma zamanı iş seviyesindedir; flow shop'ta işin SON makinedeki bitişidir.
Due date eksikse due-date ölçütleri None (kullanılamıyor) döner; 0 ile doldurulmaz.
"""
from typing import Dict, List, Optional

from .models import Instance, ScheduleRow

METRICS = ("makespan", "total_completion", "weighted_completion",
           "max_lateness", "total_tardiness", "weighted_tardiness")
DUE_METRICS = ("max_lateness", "total_tardiness", "weighted_tardiness")


def completion_times(inst: Instance, rows: List[ScheduleRow]) -> Dict[str, int]:
    if inst.environment == "flowshop":
        last = inst.machine_ids()[-1]
        return {r.job_id: r.end_time for r in rows if r.machine_id == last}
    return {r.job_id: r.end_time for r in rows}


def evaluate(inst: Instance, rows: List[ScheduleRow]) -> Dict[str, Optional[int]]:
    C = completion_times(inst, rows)
    res: Dict[str, Optional[int]] = {
        "makespan": max(C.values()),
        "total_completion": sum(C.values()),
        "weighted_completion": sum(j.weight * C[j.job_id] for j in inst.jobs),
    }
    if inst.has_due_dates:
        L = {j.job_id: C[j.job_id] - j.due_date for j in inst.jobs}
        res["max_lateness"] = max(L.values())
        res["total_tardiness"] = sum(max(0, v) for v in L.values())
        res["weighted_tardiness"] = sum(j.weight * max(0, L[j.job_id]) for j in inst.jobs)
    else:
        for k in DUE_METRICS:
            res[k] = None
    return res
