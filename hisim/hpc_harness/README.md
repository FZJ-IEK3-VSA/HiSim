# HiSim MPI HPC Harness

A resilient master/worker framework for running large batches (10,000+) of HiSim
simulations across many compute nodes via MPI.

## Topology

- **One MPI rank per node.** Each rank is a *node-agent* that runs a memory-gated
  pool of HiSim simulations as isolated subprocesses.
- **Rank 0 is the dispatcher** and the only process that touches the SQLite task
  database. By default it also runs its own local pool (`head_runs_jobs`).
- Agents *pull* work: they request tasks, run them, and report results. Only small
  status records cross MPI — result files go straight to the shared filesystem.

## Why this design

- **Memory, not cores, is the constraint** (~10 GB per sim). Each node-agent is the
  sole decision-maker for its node's memory, so admission control is a simple local
  check (`available >= per_sim_mem_gb + min_headroom_gb`) with no cross-rank race.
- **Subprocess isolation.** Each task runs in a fresh process via
  `hisim.hpc_harness.run_one`, so a crash, hang, or OOM never kills the agent. It is
  caught, recorded, and retried.
- **SQLite is the durable source of truth.** MPI itself is not fault-tolerant (a dead
  rank aborts the job). Resilience to node loss comes from the database plus Slurm
  requeue: on restart, leased-but-unreported tasks are returned to the queue and
  completed work is skipped.

## Workflow

```bash
# 1. Generate scenario JSONs into a directory with your own external script, then:
python -m hisim.hpc_harness import --db tasks.db --scenario-dir ./scenarios --glob '*.scenario.json'

# 2. Launch under Slurm (see hisim_hpc.sbatch). One rank per node:
srun python -m hisim.hpc_harness run --config harness.json

# 3. Monitor / manage:
python -m hisim.hpc_harness status --db tasks.db
python -m hisim.hpc_harness reset  --db tasks.db --leased   # requeue stuck leases
python -m hisim.hpc_harness reset  --db tasks.db --failed   # revive failed/dead tasks
```

Configuration comes from a JSON file (`--config`, see `harness_config.example.json`)
with optional CLI overrides. All tasks in a run share one `*.simulation.json`.

## Failure handling

| Failure                          | Handling                                                              |
|----------------------------------|-----------------------------------------------------------------------|
| Sim crash / non-zero exit / OOM  | Caught by the agent; retried until `max_attempts`, then marked `dead`. |
| Sim hang                         | Per-task `timeout_s` kills the process tree; retried.                 |
| Lost report                      | `lease_timeout_s` reaper reclaims the lease.                          |
| Node / agent / head death        | Job aborts (vanilla MPI). Slurm `--requeue` + DB resume finishes it.  |

## Output

Each task writes to `result_root/<id>_<scenario_stem>/` (wiped before each attempt for
a clean retry). Measured peak memory and runtime per subprocess are stored back in the
database (`tasks` and `attempts` tables).
