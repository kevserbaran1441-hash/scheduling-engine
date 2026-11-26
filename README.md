# Scheduling Engine

A modular command-line scheduling engine for **single-machine**, **identical parallel-machine** and **permutation flow-shop** environments. It reads job data from CSV files, builds schedules with dispatching rules (or evaluates a supplied sequence), improves parallel-machine schedules with **local search, Tabu Search and Simulated Annealing**, checks feasibility, computes six objectives and exports Gantt charts.

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
| Local search (`LS`), Tabu Search (`TS`), Simulated Annealing (`SA`) for parallel machines, with search history, convergence plot and before/after Gantt | implemented |
| Instance generator and batch experiment runner | **planned** |
| OR-Tools CP-SAT extension | planned / optional |

`--seed` is recorded in `run_config.json`. Dispatching rules and local search are deterministic; the seed controls the random tie-breaking and sampling of Tabu Search and all random choices of Simulated Annealing.

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

# Improvement methods (parallel machines): start from the best of SPT/LPT/EDD, then improve
python -m scheduling_engine --environment parallel --input data/demo.csv --machines 3 --method LS TS SA --objective makespan --seed 1 --output results/search
python -m scheduling_engine --environment parallel --input data/demo.csv --machines 3 --method TS --objective total_tardiness --time-limit 5 --max-iterations 100000 --no-improve-limit 0 --seed 2

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
| `--init-rule` | LS/TS/SA starting schedule: `SPT`, `LPT`, `EDD`, `WSPT`, `ERD` or `BEST` | `BEST` |
| `--time-limit SEC` | LS/TS/SA wall-clock limit in seconds | none |
| `--max-iterations N` | LS/TS/SA iteration limit | TS: 300; LS, SA: none |
| `--no-improve-limit N` | TS: stop after N iterations without a new best (`0` disables) | 100 |
| `--tabu-tenure N` | TS tabu tenure | 7 |
| `--neighborhood-sample K` | TS: evaluate K random moves per iteration instead of the full neighborhood | full |
| `--sa-alpha` | SA cooling factor, 0 < alpha < 1 | 0.95 |
| `--sa-moves-per-temp N` | SA moves per temperature level | max(20, 3n) |
| `--sa-initial-temp T` | SA initial temperature | calibrated |
| `--sa-min-temp T` | SA minimum temperature | initial / 1000 |

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

## Improvement methods (`--method LS TS SA`)

Available in the `parallel` environment (also with `--machines 1`). They support every objective; the experiments use `makespan` and `total_tardiness`. A search starts from a dispatching-rule schedule, keeps the initial solution, and returns the best feasible solution seen (including the initial one). Every result is re-decoded, checked by the feasibility checker and re-evaluated by the common evaluator; the run stops with an error if the search objective and the evaluator ever disagree.

**Starting solution.** `--init-rule BEST` (default) computes SPT, LPT and EDD (EDD only if all due dates exist) and takes the best one for the selected objective; ties go to the earlier rule in the order SPT, LPT, EDD. The same starting schedule is used by LS, TS and SA, which makes comparisons fair.

**Representation.** One ordered job list per machine. Start times come from the common decoder (`start = max(previous end, release)`).

**Neighborhoods** (positions are 0-based):

| Move | Definition |
|---|---|
| `swap(a,i,b,k)` | exchange the job at position `i` of machine `a` with the job at position `k` of machine `b`; within a machine (`a = b`) or between machines |
| `insert(a,i,b,k)` | remove the job at position `i` of machine `a` and insert it so that it ends at position `k` of machine `b` (`k` counted after the removal, `0..len(b')`); inserting into an empty machine is allowed |

Excluded: identity moves (`a = b` and `k = i`) and within-machine inserts with `|k - i| = 1`, which give the same sequence as an adjacent swap. Cross-machine moves change job assignments. Enumeration order: all swaps (by machine-major position of the first, then second job), then all inserts (by source position, target machine, target position). With N jobs the full neighborhood has N(N-1)/2 swaps plus the inserts; for n = 100, m = 8 it has about 15,000 moves.

**Local search (`LS`).** First-improvement descent over the full neighborhood in the order above; the scan restarts from the first move after every accepted move. A move is accepted only if it strictly improves the objective. It stops with `local_optimum` when a complete scan finds no improving move (a true local optimum of the full neighborhood), or `iteration_limit` / `time_limit`.

**Tabu Search (`TS`).**
- Tabu attribute: `(job, machine, position)`. When a move is applied at iteration *t*, the old `(machine, position)` of every relocated job becomes tabu until iteration *t + tenure*: placing that job back there is forbidden in iterations *t+1 ... t+tenure* and allowed again at *t+tenure+1*. A candidate is tabu if any job it relocates would be placed on a tabu `(machine, position)`, so the immediate reversal of a move is always tabu.
- Each iteration evaluates the full neighborhood (or `--neighborhood-sample K` random moves) and applies the best admissible candidate, even if it is worse than the current solution. Admissible means not tabu, or tabu but better than the best solution found so far (aspiration).
- Ties between equally good candidates are broken uniformly at random using the seed. If no candidate is admissible, the tabu candidate whose restriction expires soonest is applied (`forced_tabu_move`). If there is no candidate at all the search stops with `no_moves`.
- Current and best solutions are separate. Stops: `iteration_limit`, `no_improvement`, `time_limit`, `no_moves`.
- A tabu search can walk across plateaus of equally good solutions; the current objective then stays equal to the best one.

**Simulated Annealing (`SA`).**
- One random valid non-identity move per iteration (swap or insert with probability 1/2, parameters drawn uniformly).
- `delta = f(candidate) - f(current)`; accept if `delta <= 0`, otherwise accept if a uniform draw `u` satisfies `u < exp(-delta / T)`.
- Geometric cooling `T <- alpha * T` after every `moves_per_temp` iterations. Initial temperature: given by `--sa-initial-temp`, or calibrated from 100 random moves of the starting solution as `T0 = mean uphill delta / ln 2` (an average uphill move is accepted with probability 0.5; `T0 = 1` if no uphill move is sampled). Minimum temperature: given, or `T0 / 1000`.
- Current and best-so-far solutions are separate; the best one is returned. Stops: `min_temperature`, `iteration_limit`, `time_limit`, `no_moves`.

**Pseudocode**

```
LOCAL-SEARCH(start):                       TABU-SEARCH(start, tenure):
  cur <- start                               cur <- best <- start; tabu <- {}
  repeat                                     for it = 1, 2, ...  until a stop rule fires
    found <- false                             drop expired entries from tabu
    for move in neighborhood(cur):             choice <- none; forced <- none
      if f(apply(cur, move)) < f(cur):         for move in neighborhood(cur):          # or a sample
        cur <- apply(cur, move)                  cand <- apply(cur, move)
        found <- true; break  (restart scan)     is_tabu <- any relocated job lands on a tabu (machine, position)
  until not found                                if not is_tabu or f(cand) < f(best):  # aspiration
  return cur                                       keep the best admissible (random tie-break)
                                                 else: keep the tabu move that expires soonest
                                               move <- choice, or forced if choice is none
                                               for each relocated job: tabu[old (machine, position)] <- it + tenure
                                               cur <- apply(cur, move)
                                               if f(cur) < f(best): best <- cur
                                             return best

SIMULATED-ANNEALING(start, T0, alpha, L, Tmin):
  cur <- best <- start; T <- T0
  while T >= Tmin and no stop rule fires:
    repeat L times:
      cand <- apply(cur, random move); delta <- f(cand) - f(cur)
      if delta <= 0 or uniform(0,1) < exp(-delta / T): cur <- cand
      if f(cur) < f(best): best <- cur
    T <- alpha * T
  return best
```

**What the search writes** (in addition to the standard files): `search_history.csv` (iteration, elapsed time, candidate/current/best objective, move, accepted, SA temperature, TS tabu/aspiration/forced flags), `convergence.png` (best-so-far and current objective versus elapsed time and iteration), `gantt_before_after.png` (starting schedule next to the final schedule) and a `search` block in `metrics.json`: start rule, initial and best objective, absolute change, improvement percentage `100 (f0 - fbest) / f0` (reported as not applicable when `f0 <= 0`), termination reason, iterations, evaluations, construction and search runtime, and all parameters. Improvement on every instance is not guaranteed: the run keeps the unchanged initial solution when no better one is found.

## Output files

Each run writes to its output folder:

| File | Content |
|---|---|
| `schedule.csv` | `job_id,machine_id,start_time,end_time` |
| `metrics.json` | instance, method, objective and value, feasibility, all metrics, algorithm and end-to-end runtime, seed |
| `run_config.json` | effective parameters, input paths, engine/Python versions, resulting sequence or machine lists |
| `gantt.png` | one lane per machine, idle gaps hatched; drawn from the exported `schedule.csv` |
| `dispatch_trace.csv` | single/parallel rules: time, available jobs, selected job, machine, priority value |
| `search_history.csv`, `convergence.png`, `gantt_before_after.png` | LS/TS/SA only (see above) |

## Project structure

| File | Responsibility |
|---|---|
| `scheduling_engine/models.py` | `Job`, `Instance`, `ScheduleRow` data structures and structural validation |
| `scheduling_engine/io_csv.py` | CSV reading with line-level errors; `schedule.csv` writing/reading |
| `scheduling_engine/dispatch.py` | priority rules, single/parallel dispatchers, flow-shop permutation rules |
| `scheduling_engine/search.py` | LS, TS and SA: moves and neighborhoods, tabu list, annealing, starting solution, history |
| `scheduling_engine/plots.py` | convergence plot of the searches |
| `scheduling_engine/decoder.py` | start/end times from a given sequence or machine lists |
| `scheduling_engine/checker.py` | shared feasibility checker |
| `scheduling_engine/objectives.py` | shared objective evaluator |
| `scheduling_engine/gantt.py` | Gantt charts (single and side-by-side comparison) |
| `scheduling_engine/cli.py` | option parsing, config merging, run pipeline, file export |
| `tests/` | hand-calculated tests (decoder, rules, checker, input validation, CLI outputs, neighborhoods, tabu rules, SA acceptance, reproducibility) |
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
| `Method 'TS' is only available in the parallel environment` | LS/TS/SA work with `--environment parallel` (use `--machines 1` for one machine). |
| `SA cooling factor alpha must satisfy 0 < alpha < 1` (or similar) | An improvement parameter is out of range; the message names it. |

## Extending the engine

- **New dispatching rule:** add a priority function in `_priority_fn` (`dispatch.py`), add the name to the relevant rule tuples, and add a hand-calculated test.
- **New objective:** add it to `METRICS` and `evaluate()` in `objectives.py`.
- **New improvement method:** write a function like `local_search` in `search.py` that takes the instance, the starting machine lists and the objective and returns a `SearchResult`; register its name in `SEARCH_METHODS` and `run_search()` (`cli.py`). Input parsing, the feasibility checker, the evaluator and the plots need no change.
- **New neighborhood move:** add a case to `apply_move()`, `neighborhood()`, `placements()` and `describe_move()`.
- **New environment or construction method:** add a decoder, extend `run_method()` in `cli.py`, and route the result through `check_schedule()` and `evaluate()`.

## AI use

Parts of this project were developed with AI assistance (Claude). All code was reviewed and tested by the authors.
