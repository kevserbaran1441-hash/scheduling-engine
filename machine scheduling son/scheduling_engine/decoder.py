"""Ortak decoder: verilen sıralamadan başlama/bitiş zamanlarını hesaplar.

  decode_single    - tek makine, SABİT sıra   (start = max(önceki bitiş, release))
  decode_parallel  - makine başına sıralı liste, aynı formül her makinede ayrı
  decode_flowshop  - tüm makinelerde ortak permütasyon

Bunlar kural seçmez; verilen sıraya aynen uyar (gerekirse boşluk bırakır).
Dinamik dispatching için dispatch.py'ye bakın.
"""
from typing import Dict, List, Sequence

from .errors import InputError
from .models import Instance, ScheduleRow


def _check_sequence(inst: Instance, seq: Sequence[str], what: str) -> None:
    seen = set()
    for jid in seq:
        if jid not in inst.job_map:
            raise InputError("%s: unknown job '%s'" % (what, jid))
        if jid in seen:
            raise InputError("%s: '%s' appears more than once" % (what, jid))
        seen.add(jid)
    missing = [j.job_id for j in inst.jobs if j.job_id not in seen]
    if missing:
        raise InputError("%s: missing job(s): %s" % (what, ", ".join(missing)))


def decode_parallel(inst: Instance, machine_lists: Dict[str, List[str]]) -> List[ScheduleRow]:
    ids = inst.machine_ids()
    for m in machine_lists:
        if m not in ids:
            raise InputError("Invalid machine '%s' (valid: %s)" % (m, ", ".join(ids)))
    _check_sequence(inst, [j for m in ids for j in machine_lists.get(m, [])],
                    "Machine lists")
    rows = []
    for m in ids:
        t = 0
        for jid in machine_lists.get(m, []):
            job = inst.job_map[jid]
            start = max(t, job.release_time)
            end = start + job.processing_time
            rows.append(ScheduleRow(jid, m, start, end))
            t = end
    return rows


def decode_single(inst: Instance, sequence: Sequence[str]) -> List[ScheduleRow]:
    if inst.machines != 1:
        raise InputError("decode_single only applies to single-machine instances.")
    return decode_parallel(inst, {"M1": list(sequence)})


def decode_flowshop(inst: Instance, permutation: Sequence[str]) -> List[ScheduleRow]:
    """M1'de: max(önceki işin M1 bitişi, release). Sonraki makinelerde:
    max(aynı makinede önceki iş bitişi, işin önceki makinedeki bitişi)."""
    _check_sequence(inst, permutation, "Permutation")
    ids = inst.machine_ids()
    prev_end = {jid: inst.job_map[jid].release_time for jid in permutation}
    rows = []
    for m in ids:
        t = 0
        for jid in permutation:
            start = max(t, prev_end[jid])
            end = start + inst.p(jid, m)
            rows.append(ScheduleRow(jid, m, start, end))
            t = end
            prev_end[jid] = end
    return rows