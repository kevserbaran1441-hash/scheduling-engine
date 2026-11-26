"""Komut satırı arayüzü.

Seçenek önceliği:  komut satırı  >  --config JSON dosyası  >  varsayılanlar.
Seçenekleri alan yer: build_parser() (argparse) -> main() içinde birleştirme -> run_method().

Örnek:
  python -m scheduling_engine --environment parallel --input data/demo.csv --machines 3 \
      --method SPT LPT --objective makespan --output results/demo
"""
import argparse
import json
import os
import platform
import sys
import time
from typing import Dict, List, Optional

from . import __version__
from .checker import assert_feasible
from .decoder import decode_flowshop, decode_parallel, decode_single
from .dispatch import (RULES_BY_ENV, dispatch_parallel, dispatch_single,
                       flowshop_permutation)
from .errors import EngineError, InputError, UnsupportedInputError
from .search import (SEARCH_METHODS, initial_solution, local_search, simulated_annealing,
                     tabu_search, write_history_csv)
from .io_csv import load_instance, read_schedule_csv, write_schedule_csv
from .models import ENVIRONMENTS, Instance
from .objectives import DUE_METRICS, METRICS, evaluate

DEFAULTS = {
    "environment": None, "input": None, "operations": None, "machines": 1,
    "method": None, "objective": "makespan", "sequence": None, "machine_lists": None,
    "output": None, "time_unit": "units", "seed": 0, "show": False,
    # improvement methods (LS / TS / SA)
    "init_rule": "BEST", "time_limit": None, "max_iterations": None, "no_improve_limit": None,
    "tabu_tenure": None, "neighborhood_sample": None,
    "sa_alpha": 0.95, "sa_moves_per_temp": None, "sa_initial_temp": None, "sa_min_temp": None,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scheduling_engine", argument_default=argparse.SUPPRESS,
        description="Scheduling engine: CSV input -> schedule, metrics, Gantt chart.")
    p.add_argument("--config", help="JSON configuration file (command-line options override it)")
    p.add_argument("--environment", choices=ENVIRONMENTS, help="single | parallel | flowshop")
    p.add_argument("--input", help="jobs CSV (job_id,processing_time,release_time,due_date,weight)")
    p.add_argument("--operations", help="flowshop: operations.csv (job_id,machine_id,processing_time)")
    p.add_argument("--machines", type=int, help="number of machines (default 1)")
    p.add_argument("--method", nargs="+",
                   help="rule(s) (SPT LPT EDD WSPT ERD) or FIXED (evaluate a fixed sequence)")
    p.add_argument("--objective", choices=METRICS, help="selected objective (default makespan)")
    p.add_argument("--sequence", help="sequence for FIXED: J3,J1,J2 (single and flowshop)")
    p.add_argument("--machine-lists", dest="machine_lists",
                   help="for FIXED with parallel: 'M1:J1,J3;M2:J2'")
    p.add_argument("--output", help="output folder (default results/<instance>)")
    p.add_argument("--time-unit", dest="time_unit", help="time unit name, e.g. minutes")
    p.add_argument("--seed", type=int, help="random seed (recorded in run_config.json)")
    p.add_argument("--show", action="store_true", help="also open the Gantt chart window")
    g = p.add_argument_group("improvement methods (--method LS TS SA, parallel environment)")
    g.add_argument("--init-rule", dest="init_rule",
                   help="starting schedule: SPT LPT EDD WSPT ERD or BEST (default BEST = best of SPT/LPT/EDD)")
    g.add_argument("--time-limit", dest="time_limit", type=float, help="search time limit in seconds")
    g.add_argument("--max-iterations", dest="max_iterations", type=int,
                   help="iteration limit (TS default 300; LS and SA: none)")
    g.add_argument("--no-improve-limit", dest="no_improve_limit", type=int,
                   help="TS: stop after this many iterations without a new best (default 100; 0 = off)")
    g.add_argument("--tabu-tenure", dest="tabu_tenure", type=int, help="TS tabu tenure (default 7)")
    g.add_argument("--neighborhood-sample", dest="neighborhood_sample", type=int,
                   help="TS: evaluate this many random moves per iteration instead of the full neighborhood")
    g.add_argument("--sa-alpha", dest="sa_alpha", type=float, help="SA cooling factor, 0<alpha<1 (default 0.95)")
    g.add_argument("--sa-moves-per-temp", dest="sa_moves_per_temp", type=int,
                   help="SA moves per temperature level (default max(20, 3n))")
    g.add_argument("--sa-initial-temp", dest="sa_initial_temp", type=float,
                   help="SA initial temperature (default: calibrated from sampled moves)")
    g.add_argument("--sa-min-temp", dest="sa_min_temp", type=float,
                   help="SA minimum temperature (default: initial temperature / 1000)")
    return p


def _load_config(path: str) -> dict:
    if not os.path.isfile(path):
        raise InputError("%s: configuration file not found." % path)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise InputError("%s: not valid JSON (%s)" % (path, e))
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise InputError("%s: unknown key(s): %s. Valid: %s"
                         % (path, ", ".join(sorted(unknown)), ", ".join(DEFAULTS)))
    return cfg


def _parse_sequence(text: str) -> List[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def _parse_machine_lists(text: str) -> Dict[str, List[str]]:
    out = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        if ":" not in part:
            raise InputError("--machine-lists must look like 'M1:J1,J3;M2:J2' (read: %r)" % part)
        m, seq = part.split(":", 1)
        out[m.strip()] = _parse_sequence(seq)
    return out


def _validate_settings(s: dict) -> None:
    if not s["environment"]:
        raise InputError("--environment is required (single | parallel | flowshop).")
    if not s["input"]:
        raise InputError("--input (jobs CSV file) is required.")
    if not s["method"]:
        raise InputError("--method is required (e.g. SPT, LPT, EDD or FIXED).")
    if isinstance(s["method"], str):
        s["method"] = [s["method"]]
    s["method"] = [m.upper() for m in s["method"]]
    if s["objective"] not in METRICS:
        raise InputError("Unknown objective '%s'. Options: %s" % (s["objective"], ", ".join(METRICS)))
    allowed = RULES_BY_ENV[s["environment"]]
    for m in s["method"]:
        if m in SEARCH_METHODS:
            if s["environment"] != "parallel":
                raise InputError("Method '%s' is only available in the parallel environment." % m)
        elif m != "FIXED" and m not in allowed:
            raise InputError("Method '%s' is not supported in the %s environment. Options: %s, FIXED%s"
                             % (m, s["environment"], ", ".join(allowed),
                                ", " + ", ".join(SEARCH_METHODS) if s["environment"] == "parallel" else ""))
    s["init_rule"] = str(s["init_rule"]).upper()
    if s["init_rule"] not in ("BEST",) + RULES_BY_ENV["parallel"]:
        raise InputError("Unknown --init-rule '%s'. Options: BEST, %s"
                         % (s["init_rule"], ", ".join(RULES_BY_ENV["parallel"])))
    if "FIXED" in s["method"]:
        if len(s["method"]) > 1:
            raise InputError("FIXED cannot be combined with other methods in the same run.")
        if s["environment"] == "parallel":
            if not s["machine_lists"]:
                raise InputError("FIXED with parallel requires --machine-lists.")
            if s["sequence"]:
                raise InputError("In the parallel environment use --machine-lists instead of --sequence.")
        else:
            if not s["sequence"]:
                raise InputError("FIXED requires --sequence (e.g. J3,J1,J2).")
            if s["machine_lists"]:
                raise InputError("--machine-lists is only for the parallel environment.")


def run_search(inst: Instance, method: str, start_lists, s: dict):
    """Runs LS / TS / SA from the given starting schedule with the settings in s."""
    obj, common = s["objective"], {"time_limit": s["time_limit"], "seed": s["seed"]}
    if method == "LS":
        return local_search(inst, start_lists, obj, max_iterations=s["max_iterations"], **common)
    if method == "TS":
        nil = 100 if s["no_improve_limit"] is None else (s["no_improve_limit"] or None)
        return tabu_search(inst, start_lists, obj,
                           tenure=7 if s["tabu_tenure"] is None else s["tabu_tenure"],
                           max_iterations=300 if s["max_iterations"] is None else s["max_iterations"],
                           no_improve_limit=nil, sample_size=s["neighborhood_sample"], **common)
    return simulated_annealing(inst, start_lists, obj, alpha=s["sa_alpha"],
                               moves_per_temp=s["sa_moves_per_temp"], min_temp=s["sa_min_temp"],
                               initial_temp=s["sa_initial_temp"],
                               max_iterations=s["max_iterations"], **common)


def run_method(inst: Instance, method: str, s: dict):
    """Builds the schedule. Returns (rows, trace, structure, algorithm_time, extra);
    extra is None for rules/FIXED and a dict with the search details for LS/TS/SA."""
    t0 = time.perf_counter()
    trace, structure, extra = [], None, None
    if method == "FIXED":
        if inst.environment == "single":
            structure = _parse_sequence(s["sequence"])
            rows = decode_single(inst, structure)
        elif inst.environment == "parallel":
            structure = _parse_machine_lists(s["machine_lists"])
            rows = decode_parallel(inst, structure)
        else:
            structure = _parse_sequence(s["sequence"])
            rows = decode_flowshop(inst, structure)
    elif method in SEARCH_METHODS:
        rule, start_lists, _ = initial_solution(inst, s["objective"], s["init_rule"])
        construction = time.perf_counter() - t0
        res = run_search(inst, method, start_lists, s)
        structure = res.best_lists
        rows = decode_parallel(inst, structure)
        extra = {"result": res, "init_rule": rule, "initial_lists": start_lists,
                 "initial_rows": decode_parallel(inst, start_lists), "construction_s": construction}
    elif inst.environment == "flowshop":
        structure = flowshop_permutation(inst, method)
        rows = decode_flowshop(inst, structure)
    else:
        fn = dispatch_single if inst.environment == "single" else dispatch_parallel
        res = fn(inst, method)
        structure, trace = res.machine_lists, res.trace
        rows = decode_parallel(inst, structure)
    return rows, trace, structure, time.perf_counter() - t0, extra


def _fmt(v) -> str:
    return "unavailable (no due_date)" if v is None else str(v)


def _write_trace(path: str, trace) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "available_jobs", "selected_job", "machine_id", "priority_value"])
        for t in trace:
            w.writerow([t.time, " ".join(t.available), t.job_id, t.machine_id, t.priority])


def _json_dump(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _search_info(inst, extra, metrics, obj):
    """Search summary for metrics.json (also cross-checks the search against the common evaluator)."""
    res = extra["result"]
    assert_feasible(inst, extra["initial_rows"])
    initial_metrics = evaluate(inst, extra["initial_rows"])
    if metrics[obj] != res.best_objective or initial_metrics[obj] != res.initial_objective:
        raise EngineError("Internal error: search objective disagrees with the common evaluator.")
    f0, fb = res.initial_objective, res.best_objective
    info = {"start_rule": extra["init_rule"], "initial_objective": f0, "best_objective": fb,
            "absolute_change": f0 - fb,
            "improvement_percent": round(100.0 * (f0 - fb) / f0, 4) if f0 > 0 else None,
            "termination_reason": res.termination_reason, "iterations": res.iterations,
            "evaluations": res.evaluations, "parameters": res.parameters, "extras": res.extras,
            "initial_lists": extra["initial_lists"], "final_current_objective": res.final_current_objective}
    if f0 <= 0:
        info["improvement_note"] = "f0 <= 0: absolute change reported, percentage not applicable"
    return info, initial_metrics


def _execute(inst, method, s, out_dir, load_time, config_path):
    from . import gantt, plots
    t_start = time.perf_counter()
    rows, trace, structure, algo_time, extra = run_method(inst, method, s)
    assert_feasible(inst, rows)                       # shared feasibility checker
    metrics = evaluate(inst, rows)                    # shared objective evaluator
    obj, value = s["objective"], metrics[s["objective"]]
    search_info, initial_metrics = (_search_info(inst, extra, metrics, obj) if extra else (None, None))
    label = "%s (start: %s)" % (method, extra["init_rule"]) if extra else method

    os.makedirs(out_dir, exist_ok=True)
    schedule_path = os.path.join(out_dir, "schedule.csv")
    write_schedule_csv(schedule_path, rows)
    if trace:
        _write_trace(os.path.join(out_dir, "dispatch_trace.csv"), trace)
    config = {
        "engine_version": __version__, "instance": inst.name,
        "environment": inst.environment, "machines": inst.machines, "n_jobs": len(inst.jobs),
        "method": method, "objective": obj, "seed": s["seed"], "time_unit": s["time_unit"],
        "input": s["input"], "operations": s["operations"], "config_file": config_path,
        "effective_parameters": {k: s[k] for k in DEFAULTS},
        "result_structure": structure,
        "python": platform.python_version(), "platform": platform.platform(),
    }
    if extra:
        config["search_parameters"] = extra["result"].parameters
        config["initial_structure"] = extra["initial_lists"]
    _json_dump(os.path.join(out_dir, "run_config.json"), config)
    title = gantt.make_title(inst, label, metrics, obj)
    # the Gantt chart is drawn from the exported schedule.csv
    gantt.save_gantt(os.path.join(out_dir, "gantt.png"), inst,
                     read_schedule_csv(schedule_path), title, s["time_unit"], s["show"])
    if extra:
        res = extra["result"]
        write_history_csv(os.path.join(out_dir, "search_history.csv"), res.history)
        plots.save_convergence(os.path.join(out_dir, "convergence.png"), res.history,
                               "%s | %s | %s" % (inst.name, label, obj), obj, s["show"])
        gantt.save_comparison(
            os.path.join(out_dir, "gantt_before_after.png"), inst,
            [(gantt.make_title(inst, "Initial (%s)" % extra["init_rule"], initial_metrics, obj),
              extra["initial_rows"]), (title, read_schedule_csv(schedule_path))],
            s["time_unit"], s["show"])
    result = {
        "instance": inst.name, "environment": inst.environment, "method": method,
        "objective": obj, "objective_value": value, "feasible": True, "violations": [],
        "metrics": metrics, "seed": s["seed"],
        "algorithm_runtime_s": round(algo_time, 6),
        "end_to_end_runtime_s": round(load_time + time.perf_counter() - t_start, 6),
    }
    if extra:
        result["construction_runtime_s"] = round(extra["construction_s"], 6)
        result["search_runtime_s"] = round(extra["result"].elapsed_s, 6)
        result["search"] = search_info
    _json_dump(os.path.join(out_dir, "metrics.json"), result)
    print("\n[%s] structure: %s" % (label, structure))
    for k in METRICS:
        print("   %-20s: %s" % (k, _fmt(metrics[k])))
    if extra:
        si = search_info
        print("   search: %s -> %s (%s), %s after %d iterations, %d evaluations"
              % (si["initial_objective"], si["best_objective"], obj, si["termination_reason"],
                 si["iterations"], si["evaluations"]))
    print("   output folder       : %s" % out_dir)
    return title, read_schedule_csv(schedule_path)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        cli = vars(build_parser().parse_args(argv))
        config_path = cli.pop("config", None)
        settings = dict(DEFAULTS)
        if config_path:
            settings.update(_load_config(config_path))
        settings.update(cli)
        _validate_settings(settings)

        import matplotlib
        if not settings["show"]:
            matplotlib.use("Agg")
        t0 = time.perf_counter()
        inst = load_instance(settings["environment"], settings["input"], settings["machines"],
                             settings["operations"])
        load_time = time.perf_counter() - t0
        if settings["objective"] in DUE_METRICS and not inst.has_due_dates:
            raise UnsupportedInputError(
                "The '%s' objective requires due_date for every job; some jobs have no due_date."
                % settings["objective"])

        print("%s: %d jobs, %d machines, environment=%s" % (inst.name, len(inst.jobs), inst.machines,
                                                  inst.environment))
        methods = settings["method"]
        base = settings["output"] or os.path.join(
            "results", inst.name if len(methods) == 1 else inst.name + "_compare")
        entries = []
        for m in methods:
            out_dir = base if len(methods) == 1 else os.path.join(base, m.lower())
            entries.append(_execute(inst, m, settings, out_dir, load_time, config_path))
        if len(methods) > 1:
            from . import gantt
            os.makedirs(base, exist_ok=True)
            gantt.save_comparison(os.path.join(base, "comparison.png"), inst, entries,
                                  settings["time_unit"], settings["show"])
            print("\nComparison chart: %s" % os.path.join(base, "comparison.png"))
        return 0
    except EngineError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
