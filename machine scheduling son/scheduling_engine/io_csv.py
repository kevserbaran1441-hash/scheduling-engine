"""CSV girdi/çıktı.

Girdiyi okuyan ve ayrıştıran TEK yer burasıdır:
    read_jobs()        -> jobs.csv
    read_operations()  -> operations.csv (yalnızca flow shop)
    load_instance()    -> Instance
Hatalar "dosya, satır, alan, neden" biçiminde bildirilir.
Kodlama UTF-8 (BOM olabilir). Ayraç ',' veya ';' (başlık satırından anlaşılır).
Göreli yollar çalışma dizinine göre çözülür.
"""
import csv
import os
from typing import Dict, List, Optional, Tuple

from .errors import InputError
from .models import Instance, Job, ScheduleRow, machine_ids


def _read_table(path: str, required: List[str]) -> List[Tuple[int, Dict[str, str]]]:
    if not os.path.isfile(path):
        raise InputError("%s: file not found (relative paths are resolved against the working directory: %s)"
                         % (path, os.getcwd()))
    rows = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            first = f.readline()
            f.seek(0)
            delim = ";" if first.count(";") > first.count(",") else ","
            reader = csv.reader(f, delimiter=delim)
            header = next(reader, None)
            if header is None:
                raise InputError("%s: file is empty." % path)
            header = [h.strip().lower() for h in header]
            missing = [c for c in required if c not in header]
            if missing:
                raise InputError("%s: missing column(s): %s (header: %s)"
                                 % (path, ", ".join(missing), ",".join(header)))
            for rec in reader:
                if not any(c.strip() for c in rec):
                    continue
                rows.append((reader.line_num,
                             {h: (rec[i].strip() if i < len(rec) else "")
                              for i, h in enumerate(header)}))
    except UnicodeDecodeError:
        raise InputError("%s: file could not be read as UTF-8." % path)
    if not rows:
        raise InputError("%s: no data rows (empty instance)." % path)
    return rows


def _int(path: str, line: int, field_name: str, text: str, minimum: int) -> int:
    try:
        value = int(text)
    except ValueError:
        raise InputError("%s line %d, field '%s': must be an integer (read: %r)"
                         % (path, line, field_name, text))
    if value < minimum:
        raise InputError("%s line %d, field '%s': must be at least %d (read: %d)"
                         % (path, line, field_name, minimum, value))
    return value


def read_jobs(path: str, environment: str) -> List[Job]:
    flow = environment == "flowshop"
    required = ["job_id"] if flow else ["job_id", "processing_time"]
    jobs, seen = [], {}
    for line, rec in _read_table(path, required):
        jid = rec["job_id"]
        if not jid:
            raise InputError("%s line %d, field 'job_id': must not be empty" % (path, line))
        if jid in seen:
            raise InputError("%s line %d, field 'job_id': '%s' is duplicated (first seen on line %d)"
                             % (path, line, jid, seen[jid]))
        seen[jid] = line
        p = None if flow else _int(path, line, "processing_time", rec["processing_time"], 1)
        r = _int(path, line, "release_time", rec.get("release_time") or "0", 0)
        d_text = rec.get("due_date") or ""
        d = _int(path, line, "due_date", d_text, 0) if d_text != "" else None
        w = _int(path, line, "weight", rec.get("weight") or "1", 1)
        jobs.append(Job(jid, p, r, d, w))
    return jobs


def read_operations(path: str, jobs: List[Job], mids: List[str]) -> Dict[Tuple[str, str], int]:
    known = {j.job_id for j in jobs}
    ops, first_line = {}, {}
    for line, rec in _read_table(path, ["job_id", "machine_id", "processing_time"]):
        jid, mid = rec["job_id"], rec["machine_id"]
        if jid not in known:
            raise InputError("%s line %d, field 'job_id': '%s' not found in the jobs file" % (path, line, jid))
        if mid not in mids:
            raise InputError("%s line %d, field 'machine_id': '%s' is invalid (expected: %s..%s)"
                             % (path, line, mid, mids[0], mids[-1]))
        p = _int(path, line, "processing_time", rec["processing_time"], 1)
        if (jid, mid) in ops:
            raise InputError("%s line %d: pair (%s, %s) is duplicated (first seen on line %d)"
                             % (path, line, jid, mid, first_line[(jid, mid)]))
        ops[(jid, mid)] = p
        first_line[(jid, mid)] = line
    missing = [(j.job_id, m) for j in jobs for m in mids if (j.job_id, m) not in ops]
    if missing:
        shown = ", ".join("(%s, %s)" % pair for pair in missing[:5])
        raise InputError("%s: %d missing job-machine pair(s), e.g. %s" % (path, len(missing), shown))
    return ops


def load_instance(environment: str, jobs_path: str, machines: int,
                  operations_path: Optional[str] = None,
                  name: Optional[str] = None) -> Instance:
    if not isinstance(machines, int) or machines < 1:
        raise InputError("Machine count must be a positive integer (given: %r)." % (machines,))
    if environment == "flowshop" and not operations_path:
        raise InputError("The flowshop environment requires --operations (operations.csv).")
    if environment != "flowshop" and operations_path:
        raise InputError("--operations can only be used with the flowshop environment.")
    jobs = read_jobs(jobs_path, environment)
    ops = {}
    if environment == "flowshop":
        ops = read_operations(operations_path, jobs, machine_ids(machines))
    inst_name = name or os.path.splitext(os.path.basename(jobs_path))[0]
    return Instance(inst_name, environment, machines, jobs, ops)


# ---------------------------------------------------------------- schedule.csv
SCHEDULE_HEADER = ["job_id", "machine_id", "start_time", "end_time"]


def write_schedule_csv(path: str, rows: List[ScheduleRow]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SCHEDULE_HEADER)
        for r in rows:
            w.writerow([r.job_id, r.machine_id, r.start_time, r.end_time])


def read_schedule_csv(path: str) -> List[ScheduleRow]:
    rows = []
    for line, rec in _read_table(path, SCHEDULE_HEADER):
        rows.append(ScheduleRow(rec["job_id"], rec["machine_id"],
                                _int(path, line, "start_time", rec["start_time"], 0),
                                _int(path, line, "end_time", rec["end_time"], 0)))
    return rows