"""Veri yapıları: Job, Instance, ScheduleRow.

Zaman birimi tamsayıdır; birimin adı (dakika, saat...) yalnızca çıktılarda
etiket olarak kullanılır (--time-unit).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .errors import InputError

ENVIRONMENTS = ("single", "parallel", "flowshop")


def machine_ids(m: int) -> List[str]:
    """M1 ... Mm. Flow shop'ta bu sıra aynı zamanda rotadır."""
    return ["M%d" % (k + 1) for k in range(m)]


@dataclass(frozen=True)
class Job:
    job_id: str                                # her zaman string
    processing_time: Optional[int] = None      # flow shop'ta None (süreler Instance.operations'ta)
    release_time: int = 0
    due_date: Optional[int] = None             # None = bilinmiyor (sessizce 0 yapılmaz)
    weight: int = 1


@dataclass(frozen=True)
class ScheduleRow:
    """Yarı açık aralık [start_time, end_time)."""
    job_id: str
    machine_id: str
    start_time: int
    end_time: int


@dataclass
class Instance:
    name: str
    environment: str
    machines: int
    jobs: List[Job]
    # Yalnızca flow shop: (job_id, machine_id) -> işlem süresi
    operations: Dict[Tuple[str, str], int] = field(default_factory=dict)
    job_map: Dict[str, Job] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self):
        if self.environment not in ENVIRONMENTS:
            raise InputError("Unknown environment '%s'. Options: %s"
                             % (self.environment, ", ".join(ENVIRONMENTS)))
        if not isinstance(self.machines, int) or self.machines < 1:
            raise InputError("Machine count must be a positive integer.")
        if self.environment == "single" and self.machines != 1:
            raise InputError("In the 'single' environment the machine count must be 1 "
                             "(use 'parallel' or 'flowshop' for several machines).")
        if not self.jobs:
            raise InputError("Empty instance: at least one job is required.")
        ids = [j.job_id for j in self.jobs]
        if any((not isinstance(i, str)) or i == "" for i in ids):
            raise InputError("job_id must be a non-empty string.")
        if len(ids) != len(set(ids)):
            raise InputError("Duplicate job_id found.")
        for j in self.jobs:
            if j.release_time < 0:
                raise InputError("Job %s: release_time must be >= 0." % j.job_id)
            if j.due_date is not None and j.due_date < 0:
                raise InputError("Job %s: due_date must be >= 0." % j.job_id)
            if j.weight <= 0:
                raise InputError("Job %s: weight must be > 0." % j.job_id)
            if self.environment != "flowshop" and (j.processing_time is None or j.processing_time <= 0):
                raise InputError("Job %s: processing_time must be a positive integer." % j.job_id)
        if self.environment == "flowshop":
            for j in self.jobs:
                for m in machine_ids(self.machines):
                    p = self.operations.get((j.job_id, m))
                    if p is None or p <= 0:
                        raise InputError("Flow shop: no positive processing time for (%s, %s)."
                                         % (j.job_id, m))
        self.job_map = {j.job_id: j for j in self.jobs}

    def machine_ids(self) -> List[str]:
        return machine_ids(self.machines)

    def p(self, job_id: str, machine_id: str) -> int:
        if self.environment == "flowshop":
            return self.operations[(job_id, machine_id)]
        return self.job_map[job_id].processing_time

    def total_processing(self, job_id: str) -> int:
        """Bir işin tüm makinelerdeki toplam süresi (flow shop SPT/LPT için)."""
        return sum(self.p(job_id, m) for m in self.machine_ids())

    @property
    def has_due_dates(self) -> bool:
        return all(j.due_date is not None for j in self.jobs)