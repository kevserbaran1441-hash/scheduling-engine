"""Kural tabanlı çizelge kurma (construction).

Eşit önceliklerde ikincil anahtar job_id'dir: Python string sıralaması
(karakter kodu sırası; "J10" < "J2"). Bu belgelenmiş, deterministik bir kuraldır.

  dispatch_single      - DİNAMİK dispatching: saat t'de r<=t olan işler arasından seç;
                         hiç iş yoksa saati bir sonraki release'e ilerlet.
  dispatch_parallel    - olay tabanlı: boş makinelere (M1,M2,... sırasıyla) uygun işlerden
                         kurala göre atama; sonra sıradaki release/bitiş olayına ilerle.
  flowshop_permutation - SABİT öncelik listesi (toplam işlem süresi / due date);
                         release tarihleri sıralamada kullanılmaz, decoder'da etkili olur.

Not: "önce sırala, sonra decode et" ile dinamik dispatching, release tarihleri farklıyken
aynı sonucu VERMEZ (bkz. tests/test_dispatch.py).
"""
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List

from .errors import InputError, UnsupportedInputError
from .models import Instance, Job

SINGLE_RULES = ("SPT", "WSPT", "EDD", "ERD", "LPT")
PARALLEL_RULES = ("SPT", "LPT", "EDD", "WSPT", "ERD")   # zorunlu olan: SPT, LPT, EDD
FLOWSHOP_RULES = ("SPT", "LPT", "EDD")
RULES_BY_ENV = {"single": SINGLE_RULES, "parallel": PARALLEL_RULES, "flowshop": FLOWSHOP_RULES}


@dataclass
class TraceRow:
    time: int
    available: List[str]
    job_id: str
    machine_id: str
    priority: str


@dataclass
class DispatchResult:
    machine_lists: Dict[str, List[str]]
    trace: List[TraceRow] = field(default_factory=list)


def _priority_fn(rule: str) -> Callable[[Job], object]:
    return {
        "SPT": lambda j: j.processing_time,
        "LPT": lambda j: -j.processing_time,
        "EDD": lambda j: j.due_date,
        "WSPT": lambda j: Fraction(j.processing_time, j.weight),   # p/w, kayan nokta hatası yok
        "ERD": lambda j: j.release_time,
    }[rule]


def _prepare(inst: Instance, rule: str, allowed) -> Callable[[Job], tuple]:
    if rule not in allowed:
        raise InputError("Unknown or unsupported rule '%s'. Options: %s"
                         % (rule, ", ".join(allowed)))
    if rule == "EDD" and not inst.has_due_dates:
        raise UnsupportedInputError(
            "The EDD rule requires due_date for every job; some jobs have no due_date.")
    prio = _priority_fn(rule)
    return lambda j: (prio(j), j.job_id)


def dispatch_single(inst: Instance, rule: str) -> DispatchResult:
    if inst.machines != 1:
        raise InputError("dispatch_single only applies to single-machine instances.")
    key = _prepare(inst, rule, SINGLE_RULES)
    prio = _priority_fn(rule)
    remaining = list(inst.jobs)
    seq, trace, t = [], [], 0
    while remaining:
        eligible = [j for j in remaining if j.release_time <= t]
        if not eligible:
            t = min(j.release_time for j in remaining)
            continue
        job = min(eligible, key=key)
        trace.append(TraceRow(t, sorted(j.job_id for j in eligible), job.job_id, "M1",
                              str(prio(job))))
        seq.append(job.job_id)
        remaining.remove(job)
        t += job.processing_time
    return DispatchResult({"M1": seq}, trace)


def dispatch_parallel(inst: Instance, rule: str) -> DispatchResult:
    key = _prepare(inst, rule, PARALLEL_RULES)
    prio = _priority_fn(rule)
    mids = inst.machine_ids()
    free = {m: 0 for m in mids}
    lists = {m: [] for m in mids}
    remaining = list(inst.jobs)
    trace, t = [], 0
    while remaining:
        for m in mids:                                  # artan makine sırası
            if free[m] > t:
                continue
            eligible = [j for j in remaining if j.release_time <= t]
            if not eligible:
                break
            job = min(eligible, key=key)
            trace.append(TraceRow(t, sorted(j.job_id for j in eligible), job.job_id, m,
                                  str(prio(job))))
            lists[m].append(job.job_id)
            free[m] = t + job.processing_time
            remaining.remove(job)                       # hemen havuzdan çıkar
        if not remaining:
            break
        events = [f for f in free.values() if f > t]
        events += [j.release_time for j in remaining if j.release_time > t]
        t = min(events)                                 # sonraki release/bitiş olayı
    return DispatchResult(lists, trace)


def flowshop_permutation(inst: Instance, rule: str) -> List[str]:
    _prepare(inst, rule, FLOWSHOP_RULES)
    if rule == "EDD":
        key = lambda j: (j.due_date, j.job_id)
    elif rule == "SPT":
        key = lambda j: (inst.total_processing(j.job_id), j.job_id)
    else:  # LPT
        key = lambda j: (-inst.total_processing(j.job_id), j.job_id)
    return [j.job_id for j in sorted(inst.jobs, key=key)]