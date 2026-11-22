from scheduling_engine.models import Instance, Job


def single(jobs, name="t"):
    """jobs: (id, p, r, d, w) listesi -> tek makineli Instance"""
    return Instance(name, "single", 1, [Job(i, p, r, d, w) for i, p, r, d, w in jobs])


def parallel(jobs, m, name="t"):
    return Instance(name, "parallel", m, [Job(i, p, r, d, w) for i, p, r, d, w in jobs])


def flow(jobs, ops, m, name="t"):
    """jobs: (id, r, d, w); ops: {(id, 'M1'): p}"""
    return Instance(name, "flowshop", m, [Job(i, None, r, d, w) for i, r, d, w in jobs], ops)


def pairs(rows):
    return [(r.job_id, r.machine_id, r.start_time, r.end_time) for r in rows]
