# HPC Harness (REST job distribution)

Runs large batches (10 000+) of simulations across many HPC compute nodes:
one long-lived **queue server** (FastAPI + SQLite) hands out jobs over HTTP to a
fleet of Slurm-started **workers**, each running a warm pool of forked child
interpreters. Full design: [`hpc_harnes_spec.md`](../../hpc_harnes_spec.md) at the
repository root (Draft v2). Replaces the retired MPI harness that lived in
`hisim/hpc_harness/`.

Key properties (spec references):

- **Fenced leases** — every lease carries an attempt number; stale reports from
  resurrected workers are rejected, duplicate replays are absorbed (§5.1).
- **Attempt staging dirs** — each attempt runs in `results/.staging/…attempt-N/` and is
  atomically renamed to the canonical dir only on verified success (§4.8).
- **Fork-server** — heavy imports happen once per node in a single-threaded spawner;
  all warm children are forked there, never from the threaded parent (§4.3).
- **Program-agnostic** — HiSim is just one `Runner`; other programs plug in via the
  `hpc_harness.runners` entry-point group or `register_runner()` (§4.4).
- **Two DBs** — durable core DB (server-local WAL + shared-FS snapshots) and a
  disposable logging DB you can purge anytime (§6).
- **Autoscaler, circuit breaker, memory auto-raise, dashboard** (§13.1, §8.1, §4.6, §10).

## Quick start

```bash
# 1. On the login node: start the server (publishes server.url to the shared FS)
cd scripts
python -m hpc_harness server --config server.json

# 2. Submit jobs
python -m hpc_harness submit --server-url-file /project/run/server.url \
    --runner hisim --batch run1 \
    --scenario-dir /project/run/scenarios --glob '*.scenario.json' \
    --sim-params /project/run/2021.simulation.json

# 3. Start workers (or enable the autoscaler in server.json)
sbatch hpc_harness/slurm/worker.sbatch            # one exclusive node each
./hpc_harness/slurm/submit-workers.sh 20          # twenty of them

# 4. Watch: open http://<server>:8080/  (or `python -m hpc_harness status ...`)
```

## Systematic test: all Python system setups

`submit_system_setups.py` enqueues every `system_setups/*.py` as a job under the
`hisim_setup` runner, with a forced simulation duration (default **one week**, 60 s
timesteps) so the whole matrix is comparable:

```bash
python scripts/hpc_harness/submit_system_setups.py --server-url-file /project/run/server.url
python scripts/hpc_harness/submit_system_setups.py ... --duration full_year   # new batch later
```

Workers for these jobs must serve that runner: `python -m hpc_harness worker ...
--runner hisim_setup` (the server only leases jobs matching a worker's runner).
Success is HiSim's own end-of-run marker `finished.flag`. Because HiSim uses
process-global singletons, consider a low `max_jobs_per_child` (1–5) for this runner
so sequential setups can't leak state into each other.

Config templates: `server.example.json`, `worker.example.json`. Auth: set
`HARNESS_TOKEN` in the environment of the server, workers, and submit CLI — GET
endpoints and the dashboard are open on the cluster network; every mutation needs the
token (§11). Console capture from the dashboard requires the token too; without a
token configured the buttons work as-is.

## Layout

```
hpc_harness/
  config.py     ServerConfig / WorkerConfig (JSON + CLI overrides)
  db.py         core DB: tasks/attempts/workers/slurm_submissions, fenced lease/report
  logdb.py      disposable logging DB: metrics, shipped logs, console snapshots
  client.py     HTTP client with retry/backoff + lease replay
  run_one.py    run exactly one HiSim simulation (moved from hisim/hpc_harness)
  runners/      Runner protocol + registry; hisim + generic subprocess runners
  worker/       spawner (fork-server), warm_pool, child loop, gates, log shipping
  server/       FastAPI app, service (queue logic + reconciliation), writer thread,
                memcheck, circuit breaker, ETA, autoscaler, dashboard
  slurm/        server.sbatch (fallback), worker sbatch files, submit-workers.sh
```

Note vs the spec's module map (§14): the reaper, reconciliation, and snapshot logic
live as methods on `server/service.py` (`reap()`, `heartbeat()`, `snapshot()`) rather
than separate modules — same behaviour, fewer moving parts.

## Development

Windows note: everything under `worker/` (except `metrics`/`logbuffer` helpers) needs
POSIX `fork` — develop/test the worker under WSL or Linux CI. Server, DB, and client
are OS-independent; their tests run anywhere:

```bash
pytest tests/test_hpc_harness_db.py tests/test_hpc_harness_server.py \
       tests/test_hpc_harness_units.py
pytest -m harness       # integration tests (Linux only)
```

Install extras: `pip install -e .[hpc]` (fastapi, uvicorn, httpx, psutil).
