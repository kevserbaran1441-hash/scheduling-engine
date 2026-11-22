# Scheduling Engine

A modular command-line scheduling engine for **single-machine**, **identical parallel-machine** and **permutation flow-shop** environments. It reads job data from CSV files, builds schedules with dispatching rules (or evaluates a supplied sequence), checks feasibility, computes six objectives and exports a Gantt chart.

## Status

| Feature | Status |
|---|---|
| CSV input with line-level validation | implemented |
| Single machine: SPT, WSPT, EDD, ERD, LPT (dynamic dispatching with release dates) | implemented |
| Parallel machines: SPT, LPT, EDD (+ WSPT, ERD), event-based dispatcher | implemented |
| Permutation flow shop: SPT, LPT (total processing time), EDD | implemented |
| Fixed-sequence evaluation (`--method FIXED`) | implemented |
| Shared feasibility checker and shared objective evaluator (6 objectives) | implemented |
| Exports: `schedule.csv`, `metrics.json`, `run_config.json`, `gantt.png` | implemented |
| Config file (command line > config file > defaults) | implemented |
| Local search, Tabu Search, Simulated Annealing (parallel machines) | **planned** |
| `--time-limit` option | **planned** (with the search methods) |
| OR-Tools CP-SAT extension | planned / optional |

`--seed` is accepted and recorded in `run_config.json`; no current method is randomized.

## Requirements and installation

Developed and tested with Python 3.12 and 3.14. The only runtime dependency is matplotlib.

```
git clone <repository-url>
cd <repository-folder>
pip install -r requirements.txt
```

## Quick start

Run all commands from the **project root** (the folder that contains `scheduling_engine/`, `data/` and `tests/`). Relative paths are resolved against the current working directory.

```
python -m scheduling_engine --environment parallel --input data/demo.csv --machines 3 --method SPT LPT EDD --objective total_tardiness --output results/demo
```

Other examples:

```
# Release dates -> visible idle intervals
python -m scheduling_engine --environment parallel --input data/demo_release.csv --machines 3 --method SPT LPT --output results/release

# Permutation flow shop (two input files)
python -m scheduling_engine --environment flowshop --input data/flow_jobs.csv --operations data/flow_ops.csv --machines 2 --method SPT EDD --output results/flow

# Evaluate a supplied sequence / assignment
python -m scheduling_engine --environment single --input data/demo.csv --method FIXED --sequence J10,J2,J6,J4,J8,J1,J7,J3,J9,J5
python -m scheduling_engine --environment parallel --input data/demo.csv --machines 2 --method FIXED --machine-lists "M1:J10,J4,J5;M2:J2,J8,J7,J9,J6,J1,J3"

# All options from a configuration file
python -m scheduling_engine --config configs/demo_parallel.json
```

## Command-line options

| Option | Meaning | Default |
|---|---|---|
| `--config FILE` | JSON configuration file | none |
| `--environment` | `single`, `parallel` or `flowshop` | required |
| `--input FILE` | jobs CSV | required |
| `--operations FILE` | flow shop only: operations CSV | none |
| `--machines N` | number of machines (positive integer; must be 1 for `single`) | 1 |
| `--method ...` | one or more rules, or `FIXED` | required |
| `--objective` | `makespan`, `total_completion`, `weighted_completion`, `max_lateness`, `total_tardiness`, `weighted_tardiness` | `makespan` |
| `--sequence J1,J2,...` | order for `FIXED` (single, flow shop) | none |
| `--machine-lists "M1:J1,J3;M2:J2"` | assignment for `FIXED` (parallel) | none |
| `--output DIR` | output folder | `results/<instance>` |
| `--time-unit NAME` | unit label on the Gantt time axis | `units` |
| `--seed N` | recorded in `run_config.json` | 0 |
| `--show` | also open the chart window | off |

**Precedence:** command line > configuration file > defaults. Configuration keys are the option names with underscores (`time_unit`, `machine_lists`); unknown keys are rejected. With several methods, each one gets its own sub-folder and a side-by-side `comparison.png` is written.

The selected objective is shown in the chart title and `metrics.json`; all available metrics are always reported.

## Input format

**`jobs.csv`** (UTF-8, delimiter `,` or `;`, header required)

| Column | Meaning | Rule |
|---|---|---|
| `job_id` | job identifier (string) | required, unique |
| `processing_time` | integer p | required for `single` and `parallel` (not used in flow shop), > 0 |
| `release_time` | integer r | optional, default 0, >= 0 |
| `due_date` | integer d (soft target) | optional, >= 0; **no default** |
| `weight` | integer w | optional, default 1, > 0 |

If any job has no `due_date`, due-date metrics (`max_lateness`, `total_tardiness`, `weighted_tardiness`) are reported as unavailable, and `EDD` or a due-date objective stops with an explicit error. Nothing is silently replaced by 0.

**`operations.csv`** (flow shop): `job_id,machine_id,processing_time`. Exactly one row per job on every machine `M1..Mm`; the machine order is the route.

## Scheduling semantics

- **Tie-breaking:** equal priorities are broken by ascending `job_id` using Python string ordering (so `"J10"` comes before `"J2"`).
- **Single machine:** dynamic dispatching. At time t the best job with `r <= t` is chosen; if none is available the clock jumps to the next release time. This is *not* the same as sorting once and decoding.
- **Parallel machines:** event-based. At each event idle machines are filled in ascending machine order from the eligible jobs; then time advances to the next release or completion.
- **Flow shop:** one common job sequence on all machines, built from a fixed priority list (total processing time for SPT/LPT, due date for EDD). Release dates govern entry to M1; waiting between stages is allowed (unlimited buffers).
- **Objectives (all minimized):** makespan, total completion time, weighted completion time, maximum lateness, total tardiness, weighted tardiness. In a flow shop the completion time of a job is its finish time on the last machine.

## Output files

Each run writes to its output folder:

| File | Content |
|---|---|
| `schedule.csv` | `job_id,machine_id,start_time,end_time` |
| `metrics.json` | instance, method, objective and value, feasibility, all metrics, algorithm and end-to-end runtime, seed |
| `run_config.json` | effective parameters, input paths, engine/Python versions, resulting sequence or machine lists |
| `gantt.png` | one lane per machine, idle gaps hatched; drawn from the exported `schedule.csv` |
| `dispatch_trace.csv` | single/parallel rules: time, available jobs, selected job, machine, priority value |

## Project structure

| File | Responsibility |
|---|---|
| `scheduling_engine/models.py` | `Job`, `Instance`, `ScheduleRow` data structures and structural validation |
| `scheduling_engine/io_csv.py` | CSV reading with line-level errors; `schedule.csv` writing/reading |
| `scheduling_engine/dispatch.py` | priority rules, single/parallel dispatchers, flow-shop permutation rules |
| `scheduling_engine/decoder.py` | start/end times from a given sequence or machine lists |
| `scheduling_engine/checker.py` | shared feasibility checker |
| `scheduling_engine/objectives.py` | shared objective evaluator |
| `scheduling_engine/gantt.py` | Gantt charts (single and side-by-side comparison) |
| `scheduling_engine/cli.py` | option parsing, config merging, run pipeline, file export |
| `tests/` | hand-calculated tests (decoder, rules, checker, input validation, CLI outputs) |
| `data/`, `configs/` | demo instances and an example configuration |

## Tests

```
python -m unittest discover -s tests
```

## Troubleshooting

| Message / symptom | Cause and fix |
|---|---|
| `No module named scheduling_engine.__main__` | Run from the project root; check that `scheduling_engine/__init__.py` and `__main__.py` exist (double underscores). |
| `file not found (relative paths are resolved against the working directory ...)` | Wrong path or wrong folder; the message prints the current working directory. |
| `missing column(s): ...` | The CSV header lacks a required column. |
| `... requires due_date for every job` | `EDD` or a due-date objective was requested but some jobs have no due date. |
| `ModuleNotFoundError: matplotlib` | Run `pip install -r requirements.txt`. |

## Extending the engine

- **New dispatching rule:** add a priority function in `_priority_fn` (`dispatch.py`), add the name to the relevant rule tuples, and add a hand-calculated test.
- **New objective:** add it to `METRICS` and `evaluate()` in `objectives.py`.
- **New environment or method:** add a decoder, extend `run_method()` in `cli.py`, and route the result through `check_schedule()` and `evaluate()`.

## AI use

Parts of this project were developed with AI assistance (Claude). All code was reviewed and tested by the authors.
