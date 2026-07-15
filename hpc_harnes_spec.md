# HPC Harness Spec — REST Job-Distribution Redesign

Status: **Draft v2** (revised 2026-07-04 after design review)
Supersedes: the MPI master/worker harness in `hisim/hpc_harness/` (`dispatcher.py`,
`node_agent.py`, `protocol.py`, `hisim_hpc.sbatch`).
Owner: n.pflugradt

**Revision 2 changes (summary):** attempt-fenced leases and reports (no duplicate
execution / result corruption); attempt-scoped staging dirs with atomic rename on
success; server-restart reconciliation instead of blind requeue; idempotent lease and
report protocol; fork-server for safe warm-child creation; core DB on server-local disk
(WAL) with periodic snapshots to the shared FS; batched DB writes behind a single writer
thread; auto-raise-only memory budget with live propagation; cgroup-aware admission
gates (observation gate retained for non-cgroup clusters); bounded-retry filesystem
preflight; rewritten autoscaler control law with `squeue`-based tracking; failure-storm
circuit breaker; batch-scoped dedup; open read access + token-gated mutations; logging-DB
archive at end of run; idle-worker release at the tail of a run.

---

## 1. Purpose & motivation

Run large batches (10 000+) of simulations across many HPC compute nodes with a
design that is **resilient, observable, and reusable across simulation programs** at
the institute.

We are moving **away from MPI** because:

- MPI is not fault-tolerant — one dead rank aborts the whole job; resilience only
  came from Slurm requeue + DB resume.
- A single MPI world couples the lifetime of all nodes; we cannot grow/shrink the
  worker fleet on demand.
- The transport (pickled dicts over `comm.send`) is HiSim-specific and awkward to
  observe or drive from outside the job.

We are moving **to a REST job-distribution model**:

- **One long-lived queue server** owns a job queue + results in SQLite and serves an
  HTTP dashboard.
- **Many workers**, each started on demand via Slurm, pull work from the server over
  HTTP and run it locally.
- Workers can appear and disappear freely; the server tracks them and **requeues work
  from workers that go missing** (default 15 min) — with fencing so a worker that
  *comes back* can never corrupt or duplicate work that was reassigned (§5.1).

### Design goals (from the brief)

| # | Goal |
|---|------|
| G1 | Server-side job queue + worker fleet over REST (replace MPI). |
| G2 | Workers started on demand via Slurm; each worker owns a whole node. |
| G3 | Each worker runs sims in threads or sub-processes locally. |
| G4 | **Minimize Python/env loading overhead.** |
| G5 | Transferable to other simulation programs at the institute (as general as possible). |
| G6 | SQLite persistence of jobs + results. |
| G7 | Auto-requeue failed jobs, and jobs from workers missing > 15 min. |
| G8 | HTTP dashboard: queue, next 50 / last 50 jobs, ETA, and more. |
| G9 | Workers log peak memory, CPU, and other metrics; dashboard visualizes them. |
| G10 | Submit scripts to push jobs to the server. |

### Decisions locked in (see `AskUserQuestion` outcomes)

- **Worker model (G3/G4):** *pre-forked warm interpreter pool via a fork-server*. A
  dedicated single-threaded **spawner** child performs the heavy imports **once per
  node**, then forks all warm children (initial pool, recycling replacements, crash
  replacements) on request. The threaded worker parent never forks (§4.3).
- **Job generality (G5):** *pluggable runner + generic payload*. A job is an opaque
  JSON payload tagged with a named **runner**. HiSim ships as one runner; other programs
  register their own. The server, DB, and dashboard are entirely program-agnostic.
- **Server stack (G8):** *Python + FastAPI/Uvicorn*, reusing `db.py` and `run_one.py`
  directly. Dashboard is server-rendered HTML with inlined (self-contained) JS/CSS.
- **Result-dir isolation (rev 2):** each attempt runs in its own **staging directory**
  and is atomically renamed to the canonical job directory only on verified success
  (§4.8). A stale writer can never touch a finished result.
- **Server deployment (rev 2):** the server runs on a **login node / VM** with
  persistent local disk. The core DB lives on that local disk (WAL) with periodic
  snapshots to the shared FS for disaster recovery (§6.5). A shared-FS fallback profile
  exists for clusters where this is not allowed.
- **Memory budget (rev 2):** the server **auto-raises** the per-job budget when observed
  peaks exceed it (OOM protection); **lowering is manual only** (§4.6).
- **Dashboard auth (rev 2):** GET endpoints and the dashboard are **open** on the
  cluster-internal interface; all mutating endpoints require the bearer token (§11).
- **Admission on shared nodes (rev 2):** the node-wide observation gate is **retained as
  basic insurance** because at least one target cluster does not enforce cgroups; where
  cgroup limits are detected, the worker gates on its own cgroup instead (§4.2).

---

## 2. Non-goals

- Not a general-purpose workflow engine (no DAGs / inter-job dependencies). Jobs are
  **independent**. Dependencies, if ever needed, are a future extension.
- Not multi-tenant with per-user auth/RBAC. A single shared bearer token guards the
  mutating API (the fleet runs inside the trusted cluster network); reads are open (§11).
- Not responsible for **generating** scenarios. As today, an external script produces
  the payloads; the harness only distributes and runs them.
- Not a replacement for Slurm scheduling — Slurm still allocates nodes; the harness
  layers a fine-grained job queue on top of the coarse node allocation.

---

## 3. Design principles

1. **The core database is the single source of truth.** Only the server process writes it,
   and a server restart resumes cleanly from it. Telemetry lives in a separate, disposable
   logging DB that can be deleted anytime without affecting the run (§6).
2. **Only small JSON crosses the network.** Result files are written by the worker
   **directly to the shared filesystem**; the server never proxies bulk data.
3. **Workers are cattle — and leases are fenced.** Any worker can die at any time; its
   in-flight jobs are reclaimed and re-leased. A worker that *resurrects* after its leases
   were reclaimed is detected and told to kill the stale work; its late reports are
   rejected. No two attempts of a job can ever interleave writes in the same directory
   (§4.8, §5.1). Adding workers only speeds things up.
4. **Both memory and cores are constraints.** Each worker is the sole decision-maker for
   its node's resources within its allocation: admission control is a local check that
   admits a job only when there is enough *both* free memory **and** free cores. The
   concurrent-job cap is `min(memory-based slots, core-based slots)`.
5. **Pay heavy imports once.** The spawner keeps the interpreter + simulation package
   loaded; warm children inherit it via copy-on-write and keep it between jobs (G4).
6. **Program-agnostic core.** Everything except the `runners/` package is independent of
   HiSim.
7. **Every protocol message is safe to retry.** Leases are replayable via a client
   `lease_id`; reports are deduplicated on `(job_id, attempt)`; heartbeats are
   informational. A lost HTTP response never strands or duplicates work (§7).

---

## 4. Architecture

```
                         persistent shared filesystem
                    ┌───────────────────────────────────────────┐
                    │  results/<job>/…       (written by workers)│
                    │  results/.staging/…    (attempt dirs, §4.8)│
                    │  tasks.snapshot.db     (periodic DR copy)  │
                    │  server.url            (published host:port)│
                    └───────────────────────────────────────────┘
                          ▲ snapshots               ▲ writes results
                          │                         │
   submit CLI            ┌┴───────────────┐         │
   ───HTTP──▶  POST /jobs │  QUEUE SERVER  │ tasks.db (local WAL)
   dashboard  ◀──HTTP──── │  (FastAPI)     │ logs.db  (local, disposable)
   (browser)             │  - REST API    │         │
                         │  - dashboard   │         │
                         │  - reaper      │         │
                         │  - writer thr. │         │
                         └───┬────────┬───┘         │
              lease / report / heartbeat (HTTP/JSON)│
                 ┌──────────┼────────┼─────────────┐│
                 ▼          ▼        ▼              ▼│
          ┌───────────┐ ┌───────────┐        ┌───────────┐
          │ WORKER  A │ │ WORKER  B │  ...   │ WORKER  N │   ← one per node (Slurm)
          │ (1 node)  │ │ (1 node)  │        │ (1 node)  │
          │ spawner ──┼─┼─▶ warm    │        │           │   ← fork-server (§4.3)
          │ ┌─┐┌─┐┌─┐ │ │ ┌─┐┌─┐┌─┐ │        │ ┌─┐┌─┐┌─┐ │   ← warm children
          │ └─┘└─┘└─┘ │ │ └─┘└─┘└─┘ │        │ └─┘└─┘└─┘ │
          └───────────┘ └───────────┘        └───────────┘
```

### 4.1 Queue server

A single long-lived FastAPI/Uvicorn process on a **login node / VM** with persistent
local disk (the primary deployment; see the fallback below). Responsibilities:

- Serve the REST API (§7) and the dashboard (§10).
- Own and mutate the two SQLite DBs (§6) — the durable **core** DB and the disposable
  **logging** DB — as the **only** writer.
- Run background tasks: the **reaper** (stale leases + missing workers + orphan
  reconciliation), the **ETA/throughput aggregator**, the **memory-budget validator /
  auto-raiser** (§4.6), the **failure-storm circuit breaker** (§8.1), the **DB
  snapshotter** (§6.5), the optional **autoscaler** (§13.1), and optional metric
  downsampling.
- Publish its reachable **IP address** and port to `${RUN_DIR}/server.url` on startup so
  workers and the submit CLI can find it without hard-coding anything (§4.5).
- Hold the authoritative per-job memory budget and hand it to workers at registration
  and via heartbeat directives when it changes (§4.6).

**Threading model.** FastAPI handlers are async, but `sqlite3` is blocking. All core-DB
**mutations** are serialized through a **single dedicated writer thread** consuming a
queue of write operations; handlers enqueue and await a future. Writes are **grouped**:
the writer drains whatever is queued and commits once per batch, so N concurrent
lease/report requests cost one fsync, not N. Reads run on a small read-connection pool
(WAL allows concurrent readers). **Heartbeat liveness is kept in memory** (a
`worker_id → last_seen` map) and flushed to the `workers` table in one batched write per
`heartbeat_flush_s` (default 30 s) — a heartbeat request performs **no** synchronous DB
write. On startup the in-memory map is seeded from the table.

**Where it runs.**
- **Primary:** a login node / VM where policy allows a persistent service, with the core
  DB on **server-local persistent disk** (WAL). Periodic snapshots go to the shared FS
  (§6.5) so a dead login node loses at most `db_snapshot_interval_s` of bookkeeping (and
  even that is recoverable by requeueing — results on the shared FS are never lost).
- **Fallback profile** (clusters that forbid login-node services): a small dedicated
  Slurm "service" allocation (`slurm/server.sbatch`). Node-local disk is scratch there,
  so in this profile the core DB lives on the **shared FS** with `journal_mode=DELETE` —
  viable only because of the batched writer above; the config profile documents the
  reduced write-rate expectations.

### 4.2 Worker

Different HPC systems hand out compute differently, so the worker supports **two modes**
(set by `mode` in `WorkerConfig`). Both share the same fork-server/warm-child machinery,
registration, lease/report/heartbeat protocol, and drain-and-quit behaviour; they differ
only in **how many jobs they run concurrently and how admission is gated**.

- **`whole_node`** — the worker is allocated the entire node (`--exclusive`) and owns all
  cores and memory. It runs a warm pool of many children and is responsible for not
  overloading the node. Admission is **self-accounting**: it knows every job it started.
- **`single_core`** — the HPC allocates a single core (many such workers may land on the
  same shared node, alongside other users' jobs). The worker runs **one job at a time**
  (a warm pool of size 1) and keeps itself fed as long as conditions allow. Admission is
  **allocation-aware where possible, defensive otherwise** — see the gate description
  below.

**Common lifecycle:**

1. **Startup:** read the server's address file (§4.5), register (`POST /workers/register`)
   and receive a `worker_id` plus the **server-authoritative memory budget**
   (`per_job_mem_gb`, §4.6). Fork the **spawner** (§4.3) *before starting any threads*;
   the spawner performs the heavy imports once. In `whole_node` mode, compute the slot
   count as `min` of the memory- and core-gated caps (extends `compute_max_slots` from
   `pool.py`, which today sizes on memory only); in `single_core` mode the slot count is 1.
2. **Build the warm pool:** ask the spawner to fork up to `max_slots` **warm children**
   (exactly 1 in `single_core` mode); each inherits the warm imports and blocks waiting
   for a job on its pipe.
3. **Main loop:**
   - **Filesystem preflight:** before leasing, verify the shared result directory is
     reachable, mounted, and writable (§4.2.1) — with a bounded retry window so a
     transient NFS hiccup does not kill the fleet.
   - **Lease:** when the preflight passes, it has an idle child, *and* the admission gate
     for its mode is satisfied, `POST /lease` for up to `free_slots` jobs, carrying a
     fresh client-generated `lease_id` so a lost response can be replayed (§7). Each
     leased job arrives with its **`attempt` number — the fence token** used in every
     subsequent message about that execution (§5.1).
   - **Dispatch:** hand each leased job (payload + runner + staging dir) to an idle child.
   - **Sample:** periodically sample per-child peak RSS + CPU time and node-level metrics
     (§9).
   - **Report:** as children finish, `POST /report` with the result + metrics (batched),
     each report carrying `{job_id, attempt}`. Handle per-report `accepted:false`
     (stale lease — discard the staging dir, nothing else to do).
   - **Heartbeat:** every `heartbeat_interval_s` (default 30 s) `POST /heartbeat` with
     liveness, a rolling node-metrics sample, and the list of `running:[{job_id,attempt}]`
     — this is the **reconciliation channel**: the response may carry directives
     (`kill`, `drain`, `release`, `reregister`, `set`, `capture_console` — §4.7, §5.1).
   - **Log shipping:** batch `WARNING`+ log records (and every job-failure traceback) and
     `POST /logs` (§4.7); on a `capture_console` directive, upload a console snapshot.
4. **Drain and quit (queue empty):** when the server signals `drain` (no pending jobs and
   nothing outstanding — §7), the worker stops leasing, lets its in-flight children
   finish, sends a final report, deregisters, and **exits**. Additionally, when only
   stragglers remain (`pending == 0` but other workers still hold leases), the server
   sends `release` to workers with **zero in-flight jobs** (config
   `release_idle_workers`, default true) so idle exclusive nodes are returned to Slurm
   instead of waiting hours for the tail. If a straggler later fails and requeues, the
   autoscaler (or a manual `submit-workers.sh`) restores capacity — with the autoscaler
   off, expect a Slurm queue round-trip in that (rare) case.
5. **Forced shutdown:** on Slurm time-limit / signal it does the same teardown best-effort;
   anything left unreported is reclaimed by the server reaper.

**Admission control — `whole_node` (self-accounting gate).** At most as many jobs run
concurrently as fit within *both* memory and cores. A child is dispatched a job only when
`available_mem >= per_job_mem + min_headroom` **and** enough cores are free
(`(running_jobs + 1) * cores_per_job <= usable_cores`, where
`usable_cores = node_cores - reserved_cores`). This is why a 128-core / 256 GB node runs
only ~20–30 HiSim instances, not 128: `min(⌊256/per_job_mem⌋, 128)` ≈ 25 at ~10 GB/job —
memory, not cores, is the binding limit here. `cores_per_job` defaults to 1 (HiSim pins
BLAS/OpenMP to a single thread — see `pool.py._launch`); multi-threaded runners set it
higher so the fleet never oversubscribes cores.

**Admission control — `single_core` (allocation-aware with an observation fallback).**
The gate has three sources of truth, selected by `node_gate` (default `"auto"`):

- **`cgroup`** — at startup the worker probes for an enforced cgroup (v2 `memory.max` /
  `cpu.max`, or v1 equivalents) around its Slurm step. If found, it gates on **its own
  allocation**: start the job only when `cgroup_mem_limit − cgroup_mem_current >=
  per_job_mem` (with a small buffer). Node-wide numbers are ignored — on a properly
  packed shared node, high global CPU is the *normal* healthy state (neighbours using
  cores Slurm gave them), and gating on it would starve the worker on its own idle core.
- **`observed`** — **the insurance mode for clusters that do not enforce cgroups** (at
  least one of our target clusters does not). The worker cannot trust that neighbours
  stay inside their allocations, so it gates on observed *node-wide* state: start only
  when `node_cpu_load_percent < max_node_cpu_percent` (default 95 %) **and**
  `psutil.available >= per_job_mem + node_safety_buffer_gb`. This guards against rogue
  jobs / Slurm zombies / over-consuming neighbours. It re-checks on a short interval.
- **`auto`** (default) — use `cgroup` when an enforced limit is detected, else `observed`.

To keep gate-starvation visible instead of silent: if the gate has blocked continuously
for `gate_warn_s` (default 600 s) the worker ships a `WARNING` (visible in the dashboard
log explorer); if `gate_max_wait_s` (default 3600 s, `null` = wait forever) elapses, the
worker deregisters with reason `gate_starved` and exits, releasing the allocation —
a fresh Slurm-scheduled worker on a less contended node is the better use of the budget.

In `single_core` mode the Slurm job **must request the job's memory explicitly**
(`--mem-per-cpu` sized to `per_job_mem_gb` + interpreter overhead — see
`slurm/worker.sbatch`, §13); on cgroup clusters the allocation is otherwise a few GB and
the kernel will OOM-kill the job no matter what any gate decides.

#### 4.2.1 Shared-filesystem preflight

A dropped or unmounted shared filesystem is a common HPC failure: the mount silently
disappears (autofs timeout, stale NFS handle, node reboot) and every job the worker runs
then fails to write, producing a flood of retries that could exhaust `max_retries` on
otherwise-good jobs. To prevent this, **before leasing** the worker verifies
`result_root` is present, is an actual mountpoint (not the empty local stand-in left when
a mount is gone), and is writable — a cheap `stat` + a probe write/delete of a
`.harness_probe.<worker_id>` file (worker-unique name, so a fleet probing the same
`result_root` never races itself).

**Transient blips must not decimate the fleet.** A 30-second NFS failover would otherwise
make every worker quit simultaneously, and re-acquiring hundreds of exclusive nodes
through the Slurm queue can take hours. So the preflight distinguishes:

- **Definitive failures** (`ENOTCONN`, `ESTALE`, mountpoint gone, read-only mount): retry
  `preflight_retries` times (default 3) across `preflight_window_s` (default 60 s), then
  give up.
- **Transient failures** (timeouts, `EIO`, slow responses): same bounded retry window —
  the worker pauses leasing during it but keeps already-running children going.

Only when the whole window fails does the worker:

1. ship a `CRITICAL` `file_access` log record (§4.7) naming the path and errno,
2. report itself dead to the server with that reason (final heartbeat / deregister), and
3. **exit.**

The server's reaper then requeues that worker's in-flight leases onto healthy workers, and
the failure is visible on the dashboard so the operator can fix the mount. Beyond the
retry window the worker does *not* keep waiting — a fresh Slurm-scheduled worker on a
healthy node is the correct recovery, and quitting frees the node.

**Child recycling.** To bound memory growth / state leakage across jobs in a long-lived
interpreter, a child is **recycled** (killed; a warm replacement is forked *by the
spawner*, §4.3) after `max_jobs_per_child` jobs (default e.g. 50) or if its RSS exceeds a
ceiling. This preserves warm-start benefits while capping leaks.

### 4.3 Fork-server & warm children

`fork()` from a multi-threaded process is unsafe (locks copied in a held state → deadlock),
and the worker parent *is* threaded (heartbeat, sampling, log shipping). Therefore the
worker uses a **fork-server**:

1. Immediately after startup — **before any thread or HTTP connection exists** — the
   parent forks the **spawner**, a single-threaded helper connected by a pipe.
2. The spawner runs `runner.warmup()` **once** (the heavy imports, e.g. `hisim_main`) and
   then calls `gc.freeze()` so CPython refcount/GC traffic doesn't progressively dirty
   the shared pages (plain copy-on-write degrades quickly without this).
3. Every warm child — the initial pool, recycling replacements, crash replacements, and
   pool growth after a budget change (§4.6) — is forked **by the spawner** on request.
   The parent never forks again. Children may run a cheap `runner.on_fork()` hook
   (reseed RNGs, reopen file handles); heavy work stays in `warmup()`.

This resolves the v1 ambiguity: imports happen **once per node, in the spawner** —
`warmup()` does *not* run per child.

**OOM containment.** Children set `oom_score_adj` high and the parent + spawner set it
low, so when the kernel OOM killer fires it prefers a job child over the worker
infrastructure. (This is a strong bias, not a guarantee — the reaper still covers the
case where the whole worker dies.)

Warm-child loop, isolated from the worker and from other children:

```
# forked by the spawner; imports already loaded
runner.on_fork()                # cheap per-child re-init (RNG, handles)
while True:
    job = recv(pipe)            # {job_id, attempt, runner, payload, staging_dir}
    result = run_job(job)       # try/except → captures traceback, exit status
    send(pipe, result)          # {status, error?, ...}  (metrics sampled by parent)
```

A crash or OOM in a child never takes down the worker: the parent notices the dead child,
reports the job as failed (for requeue), and asks the spawner for a replacement.

### 4.4 Runner abstraction (generality — G5)

A **Runner** is the pluggable seam that makes the harness program-agnostic:

```python
class Runner(Protocol):
    name: str
    def warmup(self) -> None: ...                 # once per NODE, in the spawner (§4.3)
    def on_fork(self) -> None: ...                # cheap, once per warm child
    def run(self, payload: dict, result_dir: str) -> None: ...   # one job; raise on failure
```

- Runners are registered in a registry (dict keyed by name; optionally discoverable via
  setuptools entry points so external programs can plug in without editing the harness).
- **`HiSimRunner`** wraps the existing `run_one.run_single(scenario, sim_params,
  result_dir)` — no behavioural change to how a HiSim sim executes.
- **`SubprocessRunner`** (built-in, fully generic): `run()` builds an argv from the
  payload and `subprocess.run()`s it, writing into `result_dir`. This gives the
  "arbitrary command per job" capability for non-Python programs (it forgoes the
  warm-start benefit, which those programs can't use anyway).
- A new program at the institute integrates by shipping one Runner class (~20 lines);
  the server, DB, dashboard, submit tools, and Slurm scripts are untouched.

The worker is told which runner to use for the run (config / job field); a worker
serves one runner per process (so `warmup()` is meaningful).

### 4.5 Server discovery (address file)

On startup the server writes its **reachable IP address and port** to a file on the shared
filesystem (`url_publish_path`, default `${RUN_DIR}/server.url`) — the *IP*, not just a
hostname, so that any worker on any node can connect without name-resolution assumptions.
Every worker (and the submit CLI) reads this file to learn where the server is; there is
nothing to hard-code. The server rewrites the file on each restart (its host/port may
change if it is rescheduled onto a different node), and workers that lose the connection
re-read the file and reconnect with backoff. The file is written atomically (write-temp +
rename) and group-readable only.

### 4.6 Memory budget: auto-raise + continuous validation

The **peak-memory budget per job is configured on the server** (`per_job_mem_gb` in
`ServerConfig`) and handed to workers at registration — it is the single authoritative
number used for `whole_node` slot sizing and the memory gates. The server then
**continuously validates that budget against reality**: every reported job carries its
measured `peak_mem_mb`, and the server tracks the running maximum and p99 of actual peak
memory per runner.

**Auto-raise (OOM protection — automatic).** Once at least `mem_min_samples` (default 20)
reports exist, if `observed_p99 + mem_autoraise_margin_gb (default 1 GB)` exceeds the
current effective budget, the server **raises the effective budget** to that value,
logs the step, shows it on the dashboard, and pushes the new value to all workers via a
heartbeat `set` directive. The budget is only ever raised automatically — never lowered.

**Lowering (manual only).** When `observed peak < budget − mem_validation_warn_gb`
(default 1 GB), the server shows a **warning banner** ("budget too high → wasted slots")
with the numbers; the operator may apply a lower value via
`POST /admin/config {per_job_mem_gb}` (§7), which propagates the same way.

**Live application on workers.** On receiving a `set` directive the worker updates its
admission math immediately. In `whole_node` mode the effective slot cap is recomputed:

- **Cap decreased:** excess warm children are retired as they become idle (never killed
  mid-job).
- **Cap increased:** the spawner forks additional warm children up to the new cap.

This closes the loop end-to-end: detection *and* actuation, no fleet restart required.

### 4.7 Logging & on-demand console

Debugging a fleet of remote workers requires two things: **detailed error logs that reach
the server automatically**, and **the exact console of a specific worker on demand**.
Because workers are behind the pull model (compute nodes accept no inbound connections),
both flow over the existing heartbeat/directive channel — the operator never connects to a
node directly.

**Detailed error logging (always on).** Every worker configures Python `logging` to two
sinks:

1. A **local rotating file** on the shared filesystem (`log_root/worker-<worker_id>.log`),
   capturing full `DEBUG`/`INFO` detail. Because it is on shared storage, the server (and
   the operator) can read it directly without any transfer — and it survives the run as
   the deep post-mortem archive.
2. A **buffered network handler** that batches structured records and ships them to the
   server (`POST /logs`, or piggybacked on heartbeat). To bound volume, only `WARNING`+
   records ship by default (configurable via `log_ship_level`), **but every job failure
   always ships its full traceback plus the tail of that job's console**, tagged with the
   `worker_id` and `job_id`. The server stores these in a `logs` table (§6.6), so a failed
   job is traceable end-to-end from the dashboard without hunting across nodes.

Per-job console output continues to be written by each child to
`<staging_dir>/harness_run.log` (§4.8) on the shared FS; the failure tail captured into
the report `error` field is retained in the core DB, so the *decision-relevant* error
survives even a logging-DB wipe.

**On-demand console (pull, via directive).** From the Workers panel (§10) the operator can
request a live console for any worker:

- Clicking **"console"** sets a `capture_console` directive for that `worker_id` (stored on
  the server, delivered on the worker's next heartbeat response).
- On seeing the directive the worker uploads a **console snapshot** —
  the tail of its own rolling stdout/stderr ring buffer *plus* the tail of every currently
  running child's `harness_run.log` — to `POST /workers/{id}/console`. The server stores
  the latest snapshot and the dashboard renders it (`GET /workers/{id}/console`).
- A **"follow"** toggle keeps it live: while follow is active the server re-issues the
  directive each heartbeat and the worker (a) shortens its heartbeat interval to
  `console_follow_interval_s` (default 2 s) and (b) uploads only the *incremental* output
  since the last offset. Turning follow off clears the directive and restores the normal
  heartbeat cadence.

Worst-case latency for a one-shot snapshot is one heartbeat interval (≤ 30 s); follow mode
brings it to a couple of seconds. Where the server shares the filesystem with the workers
it may instead read `harness_run.log` and `worker-<id>.log` directly for completed/idle
workers, using the upload path only for live (still-buffered) output.

### 4.8 Attempt staging, rename-on-success & the success condition

**Every attempt runs in its own staging directory; only verified success is promoted to
the canonical job directory.** This is the disk-level half of the fencing story (§5.1):
two attempts of the same job can never interleave writes.

- The **canonical** result dir is `result_root/<id:06d>_<label-or-runner>/` (server-derived,
  §12). Downstream consumers see exactly this layout — same as v1.
- Each attempt executes in `result_root/.staging/<id:06d>_<label>.attempt-<n>/`, where
  `n` is the fenced attempt number from the lease. Attempt dirs are unique by
  construction, so there is **no wipe-before-run** anymore — a stale writer from a
  revoked lease keeps scribbling harmlessly into *its own* staging dir.
- On child exit the worker checks the success condition **in the staging dir**:
  the job is a success only if the child exited 0 **and** the configured `success_file`
  (a filename or glob) is present. Exit code 0 alone is *not* sufficient — a simulation
  can exit cleanly yet fail to produce its results (disk full, silent write failure,
  crash after the final flush).
- **On success:** the worker atomically renames the staging dir to the canonical path
  (same filesystem, single `rename`). If a canonical dir already exists (possible only
  after a lost-report replay or a stale duplicate), the current lease holder removes it
  and renames again — its attempt is the fenced, authoritative one. It then sends the
  report; if the server rejects it as stale (`accepted:false`), the worker's lease was
  revoked mid-run — the reconciliation `kill` normally prevents ever reaching this point,
  and in the residual race the swapped-in directory is itself a *complete, verified*
  result of the same deterministic job, so no corruption is possible — only a redundant
  identical result.
- **On failure:** the staging dir is left in place for post-mortem (its log tail also
  ships to the server). Older staging dirs of the same job are deleted when the next
  attempt starts; a janitor removes leftover `.staging/` content at drain. `dead` jobs
  keep their last attempt dir.

`success_file` is configured **server-side** (`ServerConfig.success_file`, the
authoritative default) and may be overridden per job in the submit payload; it is handed
to the worker with each leased job. For HiSim it is the completion marker written at the
end of a run (e.g. the results JSON / finished-report file); other runners set their own.
A job may declare it produces no file (`success_file: null`), in which case exit code 0 is
the success condition — but the default and recommended contract is an explicit marker.

**Scale note:** at ≥ ~50 k jobs a flat `results/` strains shared-FS metadata; optional
`result_shards` (subdir per `id // 1000`) is available. `<id:06d>` padding widens
automatically past 1 M.

---

## 5. Job & worker lifecycle (state machines)

### 5.1 Lease fencing (the core correctness mechanism)

Every lease carries an **attempt number** that acts as a fence token:

- `POST /lease` increments `tasks.attempts` and returns each job with its `attempt`.
- Every report must echo `{job_id, attempt}`. The server **accepts** a report only if the
  task is `leased`, `leased_by` matches the reporting worker, **and** `attempt` matches
  `tasks.attempts`. Anything else is answered `accepted:false, reason:"stale"` and
  ignored — a late report from a reclaimed lease can never clobber the current state.
- Duplicate replays of an *accepted* report are absorbed: `attempts` rows are
  `UNIQUE(task_id, attempt_no)`; a re-send of an already-recorded report returns
  `accepted:true` without re-applying (idempotent).
- **Heartbeat reconciliation:** each heartbeat carries `running:[{job_id,attempt}]`.
  - A running entry the server has *not* got leased to that worker (reclaimed, cancelled,
    or reassigned) → the response carries `kill:[{job_id,attempt}]`; the worker kills that
    child, deletes its staging dir, and asks the spawner for a fresh warm child.
  - A job the server *has* leased to the worker but absent from `running` (beyond a
    just-leased grace of one heartbeat) accumulates a strike; after `orphan_strikes`
    (default 2) consecutive misses the server requeues it early — much faster than
    waiting out `lease_timeout_s`.
  - A heartbeat from a `worker_id` the server considers dead/unknown → response directive
    `reregister`; the worker re-registers (new `worker_id`), and its first reconciliation
    yields `kill` for everything it still runs (those leases were reclaimed while it was
    missing). **A resurrected worker therefore cleans itself up within one heartbeat.**
- Disk writes are fenced separately by attempt-scoped staging dirs (§4.8).

### 5.2 Job status

```
 pending ──lease(attempt=n)──▶ leased ──report(done, fenced)──▶ done
    ▲                             │
    │                             ├─report(fail, retries_left)──▶ pending   (retry)
    │                             ├─report(fail, no retries)────▶ dead
    │                             ├─orphaned (heartbeat recon)──▶ pending / dead
    │                             ├─lease timeout (no report)───▶ pending / dead
    │                             ├─worker missing >15 min ─────▶ pending / dead
    │                             └─admin cancel ───────────────▶ cancelled
    └───────────── reset (admin) ──────────────────────────────┘
```

Statuses: `pending`, `leased`, `done`, `failed` (transient, on the way to retry),
`dead` (retries exhausted), `cancelled` (admin). **`done` requires the configured success
file (§4.8), not just exit code 0.** A job is attempted at most `max_retries + 1` times
(default `max_retries = 3` → up to 4 runs) before it is marked `dead`. Cancelling a
`leased` job marks it `cancelled` immediately and delivers a `kill` directive on the
holder's next heartbeat.

Builds on the semantics in `db.record_report` / `db.reset_stale_leases` / `db.reset`,
**extended with the fence checks above** (`record_report` gains the
worker/attempt-match precondition and `(task_id, attempt_no)` idempotency; it is *not*
reused unchanged). Blind `startup_recovery` is **replaced** — see "Server restart" in §8.

### 5.3 Worker status

```
 (register) ─▶ alive ──heartbeat──▶ alive
                 │
                 └─no heartbeat > worker_timeout_s (900 s) ─▶ missing
                        │
                        ├─ reaper requeues its leased jobs, marks worker dead
                        └─ if it reconnects later: told to reregister; stale
                           children killed via heartbeat reconciliation (§5.1)
```

---

## 6. Persistence (SQLite — G6)

State is split across **two separate SQLite files** so telemetry can be cleared without
touching the run:

- **Core DB** (`db_path`, e.g. `tasks.db`) — the **durable source of truth**: config/meta,
  jobs, attempts, workers, and autoscaler bookkeeping. Lives on the **server's local
  persistent disk** (login-node deployment, §4.1) in WAL mode, with periodic snapshots to
  the shared FS (§6.5). Tables: `meta`, `tasks`, `attempts`, `workers`,
  `slurm_submissions`.
- **Logging DB** (`logs_db_path`, e.g. `logs.db`) — **disposable telemetry**: node metric
  time series, shipped log records, and console snapshots. It can be **deleted at any time
  to reclaim space** (§6.8) with no effect on scheduling, recovery, ETA, or requeue.
  At end of run it is **archived to the shared FS** (§6.9) so post-mortem tracing works.
  Tables: `worker_metrics`, `logs`, `console_snapshots`.

The two files use separate connections (no cross-file `ATTACH`, so the logging file can
disappear underneath the server without corrupting a transaction). All writes to the logging
DB are **best-effort**: a failure (file missing / locked / mid-delete) is swallowed and the
job/request proceeds. Anything the server *decides* on — throughput, ETA, the memory-budget
auto-raise — is derived from the **core** `tasks`/`attempts` rows, never from the logging
DB, so a wipe never changes behaviour, only what history the dashboard can show.

Reuses the transport-agnostic shape of `db.py` (`lease_tasks`, `record_report`,
`reset_stale_leases`, `reset`, `counts`, `is_drained`) with the rev-2 extensions:
fencing preconditions and idempotency in `record_report`, `lease_id` replay in
`lease_tasks`, reconciliation-based startup instead of `startup_recovery`; generalizes
`tasks` and adds `workers` + `slurm_submissions` in core, and the telemetry tables in the
logging DB.

### 6.1 `tasks` (core DB — generalized from scenario-specific to payload-based)

| column | notes |
|--------|-------|
| `id` | PK |
| `runner` | runner name (e.g. `hisim`) |
| `payload` | JSON blob passed to `Runner.run` (e.g. `{scenario, sim_params}`) |
| `batch_id` | submit batch (nullable); scopes dedup and groups jobs on the dashboard |
| `dedup_key` | nullable — idempotent submit (e.g. the scenario path); `UNIQUE(batch_id, dedup_key)`, so re-running the same scenarios in a **new batch** is not blocked by an old run |
| `label` | optional human label, used in result-dir naming and dashboard |
| `priority` | integer, higher leased first (default 0) — enables lease ordering |
| `lease_id` | client-generated id of the lease call that leased this row (for idempotent lease replay, §7) |
| `status`, `attempts`, `leased_by`, `leased_at`, `started_at`, `finished_at`, `duration_s`, `peak_mem_mb`, `exit_code`, `result_dir`, `error`, `updated_at` | as today |

`leased_by` holds a **`worker_id`**. `lease_tasks` gains `ORDER BY priority DESC, id`,
takes a `worker_id` + `lease_id`, and returns the incremented `attempts` value as the
fence token (§5.1).

### 6.2 `attempts` (core DB)

One row per attempt with `task_id`, `attempt_no`, `host`, `worker_id`, timings,
`peak_mem_mb`, `cpu_time_s` (new), `exit_code`, `status`, `error`, `staging_dir`.
**`UNIQUE(task_id, attempt_no)`** — the idempotency anchor for report replays (§5.1).
Feeds the "last 50 jobs" and the per-job metric distributions.

### 6.3 `workers` (core DB — new)

| column | notes |
|--------|-------|
| `worker_id` | PK (server-assigned uuid) |
| `host` | node hostname |
| `mode` | `whole_node` or `single_core` |
| `slots` | configured max concurrent jobs (1 for `single_core`) |
| `cores`, `total_mem_gb` | node capacity (for the dashboard) |
| `runner` | runner this worker serves |
| `registered_at`, `last_heartbeat`, `status` | liveness (`alive`/`missing`/`dead`); `last_heartbeat` is flushed from the in-memory liveness map in batches (§4.1), not per request |
| `last_error` | reason a worker died (e.g. `file_access`, `gate_starved`), for the dashboard |
| `jobs_done`, `jobs_failed` | running totals |
| `slurm_job_id` | for cross-referencing with `squeue` |

### 6.4 `worker_metrics` (logging DB — time series for the dashboard, G9)

Append-only samples keyed by `worker_id` + `ts`: `cpu_percent`, `mem_used_gb`,
`mem_available_gb`, `load1`, `running_jobs`, `free_slots`, plus any extra metrics a
runner chooses to emit. Downsampled/rolled up by a background task to bound size. Lives in
the disposable logging DB (§6.8).

### 6.5 Durability: pragmas & snapshots

- **Core DB** (login-node deployment, local persistent disk): `journal_mode=WAL`,
  `synchronous=NORMAL`. WAL gives concurrent dashboard reads next to the single writer
  thread; `NORMAL` risks only the last moments of bookkeeping on a power loss, which the
  requeue machinery absorbs (a job whose `done` transition is lost simply re-runs). All
  mutations go through the single writer thread with grouped commits (§4.1).
- **Snapshots:** every `db_snapshot_interval_s` (default 300 s) — and at drain — the
  server writes a consistent copy (`VACUUM INTO` / SQLite backup API) to
  `db_snapshot_path` on the **shared FS**. If the login node dies, the server restarts
  anywhere from the snapshot (`--from-snapshot`); at most one interval of bookkeeping
  re-runs. Result files were on the shared FS all along.
- **Fallback profile** (server inside a Slurm allocation, §4.1): core DB on the shared FS
  with `journal_mode=DELETE`, `synchronous=FULL` (WAL's shared-memory file is unreliable
  over NFS/Lustre/GPFS); no snapshots needed. Viable only with the batched writer.
- **Logging DB** is disposable, so it favours throughput over durability:
  `synchronous=OFF` (or `NORMAL`). It sits on **local disk** of the server (never the
  shared FS) so telemetry writes never contend with the run's shared-FS I/O.

### 6.6 `logs` (logging DB — shipped error/event records, §4.7)

Append-only structured records shipped by workers: `id`, `ts`, `worker_id`, `job_id`
(nullable), `level`, `logger`, `message`, `traceback` (nullable), `host`. Indexed on
`worker_id`, `job_id`, and `level` so the dashboard can filter to a failing job or worker.
Retention is bounded (rolling window / max rows); the full-detail `DEBUG` stream stays only
in the per-worker shared-FS log file. Lives in the disposable logging DB (§6.8) — the
per-worker shared-FS log files are the deeper archive and are *not* deleted with it.

The **latest console snapshot** per worker is kept in a small `console_snapshots` table
(`worker_id` PK, `ts`, `text`, `next_offset`) — overwritten on each upload rather than
appended, so it never grows. Also in the logging DB.

### 6.7 `slurm_submissions` (core DB — autoscaler bookkeeping, §13.1)

One row per worker job the autoscaler submits: `slurm_job_id` (PK), `mode`, `submitted_at`,
`registered_worker_id` (nullable, filled when the worker checks in), `state`
(`submitted`/`queued`/`running`/`registered`/`ended`/`cancelled`) — kept current by
**polling `squeue` on the recorded job ids** (§13.1), so a worker stuck in the Slurm queue
for hours still counts correctly toward the fleet and is never blindly resubmitted.

### 6.8 Clearing the logging DB

The logging DB is designed to be thrown away whenever it grows too large. Two safe paths:

- **While the server is stopped:** just `rm` the file (and its `-wal`/`-journal` sidecars,
  if any). On next start the server recreates an empty logging DB from schema.
- **While the server is running:** call `POST /admin/logs/purge` (§7). The server closes its
  logging connection, deletes+recreates the file, and reopens — reclaiming the space
  immediately and cleanly. This is the recommended live path.

A raw `rm` of the file *while the server is running* is also tolerated but is the blunt
option: because the server still holds the open handle, the inode's space is not actually
freed until the server reopens it. To make this work anyway, the server periodically
(`logs_reopen_check_s`, default 60 s) stats the logging path; if the file is missing or its
inode changed, it closes and reopens a fresh DB. So a live `rm` frees space within one check
interval. Either way, **nothing in the core DB or the run is affected** — at most a minute
of telemetry history is lost. The server never assumes a row it wrote to the logging DB is
still there.

### 6.9 End-of-run archive

When the queue drains (and on clean server shutdown), the server copies the logging DB to
`logs_archive_path` on the **shared FS** (default alongside the snapshot). This is what
makes the §10 claim honest: pointing the dashboard at the archived core snapshot + logs
archive gives full post-mortem tracing (shipped logs, last console snapshots, metric
history) after the fleet — and even the server's local disk — are gone.

---

## 7. REST API (v1)

Base path `/api/v1`. JSON in/out. Auth: `Authorization: Bearer <token>` on **mutating**
routes; `GET` routes and the dashboard are open on the cluster-internal interface (§11).
Versioned so workers and server can be upgraded independently within a major version.

| Method & path | Caller | Body → Response | Purpose |
|---|---|---|---|
| `POST /jobs` | submit CLI | `{runner, batch?, jobs:[{payload,label?,dedup_key?,priority?}]}` → `{inserted, skipped, ids[]}` | Enqueue a batch (idempotent on `(batch, dedup_key)` — a new batch re-runs old scenarios freely). |
| `POST /workers/register` | worker | `{host,mode,slots,cores,total_mem_gb,runner,slurm_job_id}` → `{worker_id, per_job_mem_gb, config?}` | Announce a worker; get its id, the authoritative memory budget (§4.6), and optional config overrides. |
| `POST /lease` | worker | `{worker_id, num_slots, lease_id}` → `{jobs:[{id,attempt,runner,payload,staging_dir,result_dir,success_file}], drain?}` | Atomically lease ≤ N pending jobs. **Replayable:** re-POSTing the same `lease_id` returns the same job set instead of leasing new ones (a lost response strands nothing). Each job carries its `attempt` fence token (§5.1). `drain:true` → finish and quit (§4.2). |
| `POST /report` | worker | `{worker_id, reports:[{id, attempt, status, exit_code, duration_s, peak_mem_mb, cpu_time_s, result_dir, error, started_at, finished_at}]}` → `{results:[{id, accepted, reason?}]}` | Apply finished-job results (batched). Fenced: stale worker/attempt → `accepted:false`; duplicate of an accepted report → `accepted:true` (idempotent). Feeds budget auto-raise (§4.6). |
| `POST /heartbeat` | worker | `{worker_id, metrics:{cpu_percent,mem_used_gb,…}, running:[{job_id,attempt}]}` → `{directives}` | Liveness + node metrics + **reconciliation** (§5.1). Directives: `kill:[{job_id,attempt}]`, `drain`, `release`, `reregister`, `set:{per_job_mem_gb,…}`, `capture_console:{follow?}`. |
| `POST /workers/{id}/deregister` | worker | `{reason}` → `{ok}` | Clean exit or fatal error (`file_access`, `gate_starved`, …); server marks the worker dead and reclaims its leases. |
| `POST /logs` | worker | `{worker_id, records:[{ts,level,logger,job_id?,message,traceback?}]}` → `{ok}` | Ship batched error/event log records (§4.7). |
| `POST /workers/{id}/console` | worker | `{ts, text, next_offset}` → `{ok}` | Upload a console snapshot (or incremental chunk) in response to a `capture_console` directive. |
| `GET /status` | anyone | → `{counts:{pending,leased,done,failed,dead,cancelled,total}, workers_alive, throughput_per_min, eta_seconds, per_job_mem_gb, mem_warning?, circuit_breaker?}` | Machine-readable summary. |
| `GET /jobs?state=&batch=&limit=&offset=` | dashboard | → job rows | Next-N / last-N tables. |
| `GET /workers` | dashboard | → worker rows + latest metrics | Worker table. |
| `GET /workers/{id}/console` | dashboard | → latest console snapshot | Show a worker's console. |
| `GET /logs?worker=&job=&level=&limit=` | dashboard | → log records | Error/event explorer, filterable. |
| `GET /metrics/timeseries?worker=&since=` | dashboard | → downsampled series | Charts. |
| `POST /admin/workers/{id}/console` | admin/dashboard | `{follow?:bool}` → `{ok}` | Request a one-shot console snapshot, or start/stop `follow` mode (§4.7). |
| `POST /admin/config` | admin | `{per_job_mem_gb?, …}` → `{ok, applied}` | Apply a config change live (e.g. lower the memory budget, §4.6); propagated via heartbeat `set` directives. |
| `POST /admin/logs/purge` | admin | → `{ok, freed_bytes}` | Safely delete + recreate the logging DB to reclaim space (§6.8); core DB untouched. |
| `POST /admin/reset` | admin CLI | `{leased?:bool, failed?:bool}` → `{requeued}` | Requeue stuck/failed jobs. |
| `POST /admin/jobs/{id}/cancel` | admin | → `{ok}` | Cancel a job. If leased, the holder gets a `kill` directive on its next heartbeat (§5.1). |
| `POST /admin/pause` / `POST /admin/resume` | admin | → `{ok}` | Stop/resume leasing (drain the fleet without killing it). `resume` also clears a circuit-breaker pause (§8.1). |
| `GET /healthz` | ops | → `200` | Liveness probe. |

Client behaviour: workers **retry with exponential backoff** on connection errors and
5xx (re-reading `server.url` in case the server moved, §4.5). Retries are safe by
construction: lease replays via `lease_id`, reports via `(id, attempt)` dedup, heartbeats
are stateless. An empty `jobs` lease response with `drain:false` triggers a backoff before
re-leasing; `drain:true` means the queue is done and the worker finishes in-flight jobs
and **exits** (§4.2).

---

## 8. Failure handling & requeue (G7)

| Failure | Detection | Handling |
|---|---|---|
| Job crash / non-zero exit / OOM | child dies or returns non-zero | worker reports `failed`; server retries until `max_retries` exhausted, then `dead`. Repeated OOMs additionally drive the budget auto-raise (§4.6), so later retries run under a corrected budget. |
| **Job exits 0 but success file missing** | worker checks `success_file` in the staging dir (§4.8) | reported `failed` ("success file missing"); retried like any other failure. |
| Job hang | per-job `timeout_s` in the worker | worker kills the child, reports `failed` (timeout), spawner forks a replacement. |
| **Shared FS unreachable / unmounted** | worker preflight, after its bounded retry window (§4.2.1) | worker ships a `CRITICAL` `file_access` record, deregisters, and quits; reaper reclaims its leases. Transient blips inside the window pause leasing only. |
| Lost lease response (network) | worker retries `POST /lease` | same `lease_id` → same jobs returned; nothing stranded (§7). |
| Lost report (network) | worker retries `POST /report` | `(id, attempt)` dedup absorbs the replay (§7). Backstop: `lease_timeout_s` reaper reclaim. |
| Orphaned lease (worker alive, job gone) | heartbeat `running` list misses a leased job for `orphan_strikes` heartbeats (§5.1) | reaper requeues early — no 4 h wait. |
| **Worker missing > 15 min** | in-memory `last_seen` age > `worker_timeout_s` (default **900 s**) | reaper marks worker `dead` and **requeues all its `leased` jobs** (→ `pending`, or `dead` if retries exhausted). If the worker later resurrects: `reregister` directive + `kill` reconciliation (§5.1); its late reports are rejected as stale; its disk writes were confined to its own staging dirs (§4.8). |
| Queue drained (normal end) | `drain` on lease/heartbeat response | worker finishes in-flight jobs, final report, deregisters, exits and releases its node. Logging DB archived (§6.9). |
| Run tail (pending 0, stragglers leased) | server-side | idle workers get `release` and exit early (config, §4.2); the straggler's holder keeps its node. |
| Worker clean shutdown (Slurm time-limit) | signal / deregister | stop leasing, finish or release in-flight jobs, final report. |
| **Server restart** | startup | **No blind requeue.** The server seeds worker liveness from the DB with a fresh grace window and lets heartbeats reconcile: live workers re-confirm their leases within one heartbeat; leases nobody re-confirms fall to the ordinary missing-worker/orphan paths. A `--assume-fleet-dead` flag restores the old cold-start behaviour (requeue everything) for restarts *between* runs. |
| Server unreachable | worker HTTP errors | worker backs off and retries; keeps running in-flight children meanwhile (safe: if it stays gone past `worker_timeout_s`, fencing + reconciliation handle its return). |

The reaper runs on a fixed period (default 30–60 s) and performs stale-lease reclamation,
missing-worker sweeps, and orphan-strike accounting. All thresholds are configurable.

### 8.1 Failure-storm circuit breaker

A systematic failure (bad config push, broken input data, expired license) can otherwise
burn through `10 000 × (max_retries+1)` attempts and mark the whole batch `dead` before a
human looks at the dashboard. The server therefore watches a rolling window of completed
attempts (default: last `100`, minimum `20` samples) and **auto-pauses leasing** when

- the window failure rate ≥ `cb_failure_rate` (default 0.5), **or**
- ≥ `cb_consecutive` (default 25) attempts failed in a row.

A paused run shows a prominent dashboard banner with the trigger and the most common
recent error; workers idle (empty leases, `drain:false`). `POST /admin/resume` clears the
pause. Enabled by default (`circuit_breaker.enabled`); attempts already in flight finish
normally and no state is lost.

---

## 9. Metrics (G9)

**Per job** (stored in `attempts` + rolled onto `tasks`, **core DB**): peak RSS (MB), wall
duration, CPU time (user+sys), exit code, status, host/worker, attempt no. Peak RSS is
sampled by the **parent** worker reading each child's RSS (incl. grandchildren) — more
reliable than child self-reporting and it catches native allocations. These live in the
core DB because they drive decisions (memory-budget auto-raise) and the job audit trail.

**Per node / worker** (time series in `worker_metrics`, **logging DB**, sent on heartbeat):
CPU utilization %, memory used/available, 1-min load average, running jobs, free slots,
cumulative jobs done/failed, and — in `single_core` cgroup mode — own-cgroup usage vs
limit. Sampling interval ~5–15 s locally; shipped every heartbeat. Purely for
visualization — disposable with the logging DB (§6.8), archived at end of run (§6.9).

**Aggregate** (computed by the server **from the core DB** `tasks`/`attempts`, so it is
unaffected by a logging-DB wipe): throughput (rolling completions/min), ETA
(`remaining / throughput`), success/fail rates, per-worker throughput, memory
distribution across completed jobs, and the **real peak-memory-vs-budget** comparison
that drives the auto-raise and the too-high warning (§4.6).

Extensibility: a Runner may attach extra key/values to its per-job report and the worker
may emit extra node metrics; both flow into the JSON columns and can be charted without
schema changes.

---

## 10. Dashboard (G8)

Server-rendered HTML at `/`, self-contained (inlined CSS/JS, a small charting routine —
no external CDNs), auto-refreshing by polling the `GET` endpoints. Theme-aware,
responsive tables that scroll horizontally on narrow screens. **Openly readable** on the
cluster-internal interface — no token needed to view (§11).

Sections:

0. **Warning banners:** memory budget auto-raised / budget looks too high (§4.6), and the
   circuit-breaker pause with its trigger and top recent error (§8.1).
1. **Summary tiles:** total / pending / running / done / failed / dead / cancelled; active
   workers (by mode); throughput (jobs/min); **estimated time to completion**; wall-clock
   elapsed; current effective `per_job_mem_gb`.
2. **Queue progress:** stacked progress bar + a "done over time" cumulative line and a
   throughput-over-time chart.
3. **Next 50 jobs:** pending, in lease order (priority desc, id).
4. **Last 50 jobs:** most recently finished, with status, duration, peak mem, host,
   attempt, error snippet on failure.
5. **Workers table:** host, mode, slots active/total, last-heartbeat age, CPU %, mem
   used/avail, gate state (incl. "gated since …" for starved `single_core` workers),
   jobs done/failed, Slurm job id, status (alive/missing/dead). Each row has a
   **"console"** action (one-shot or follow) and a **"logs"** link filtered to that worker.
6. **Console viewer:** a panel that shows the on-demand console snapshot for the selected
   worker (§4.7), with a follow toggle that live-tails while active.
7. **Log / error explorer:** the shipped `logs` records (§6.6), filterable by worker, job,
   and level, newest first, with tracebacks expandable — the primary place to trace a
   failure. Failed rows in the "Last 50 jobs" table deep-link here pre-filtered to the job.
8. **Node metric charts:** per-worker CPU % and memory time series; peak-memory
   histogram across completed jobs; optional heatmap of node utilization.

All dashboard data comes from the read-only `GET` endpoints, so the dashboard can also be
pointed at the **archived** core-DB snapshot + logs archive (§6.9) post-run — shipped
logs, console snapshots, and metric history all survive the fleet and the server's local
disk.

---

## 11. Security / auth

- Single shared **bearer token** (`HARNESS_TOKEN`) required on all **mutating** `/api/v1`
  routes (submit, register, lease, report, heartbeat, logs, console upload, `/admin/*`).
  Set via env / config; distributed to workers through the Slurm environment.
- **`GET` routes and the dashboard are open** (no token). Rationale: the server binds to
  the cluster-internal interface, and read access only exposes progress, metrics, log
  excerpts, and job payloads to cluster users. Anyone who can read the shared project
  directory can see most of this anyway. (Consequence to be aware of: shipped tracebacks
  and payloads are visible to any cluster user who can reach the port.)
- Server binds to the cluster-internal interface; not exposed to the public internet. No
  TLS inside the trusted network; the token therefore also should not be a reused secret.
- `server.url` published on shared FS is readable only by the project group
  (filesystem permissions).

---

## 12. Configuration

Two dataclasses (mirroring today's `HarnessConfig` conventions: JSON file + CLI
overrides + `finalize()`), reusing the `_normalize_path` / deprecation-alias patterns.

### `ServerConfig`

```json
{
  "db_path": "/var/lib/harness/run123/tasks.db",
  "db_snapshot_path": "/project/run123/tasks.snapshot.db",
  "db_snapshot_interval_s": 300,
  "journal_mode": "WAL",
  "logs_db_path": "/var/lib/harness/run123/logs.db",
  "logs_archive_path": "/project/run123/logs.archive.db",
  "logs_reopen_check_s": 60,
  "bind_host": "0.0.0.0",
  "bind_port": 8080,
  "result_root": "/project/run123/results",
  "url_publish_path": "/project/run123/server.url",
  "token": "…",

  "max_retries": 3,
  "success_file": "finished.flag",
  "lease_timeout_s": 14400,
  "worker_timeout_s": 900,
  "orphan_strikes": 2,
  "reaper_period_s": 45,
  "heartbeat_flush_s": 30,
  "release_idle_workers": true,

  "per_job_mem_gb": 10.0,
  "mem_autoraise": true,
  "mem_autoraise_margin_gb": 1.0,
  "mem_min_samples": 20,
  "mem_validation_warn_gb": 1.0,

  "circuit_breaker": {
    "enabled": true,
    "window": 100,
    "min_samples": 20,
    "failure_rate": 0.5,
    "consecutive": 25
  },

  "autoscale": {
    "enabled": false,
    "worker_mode": "single_core",
    "period_s": 60,
    "standby_floor": 10,
    "max_workers": 2000,
    "worker_script": "/project/run123/slurm/worker.sbatch",
    "capacity_probe": null,
    "partition": null,
    "squeue_poll_s": 60,
    "registration_grace_s": 900
  }
}
```

- `db_path` is the durable **core** DB on the server's **local persistent disk** (WAL);
  `db_snapshot_path` is its periodic shared-FS disaster-recovery copy (§6.5). For the
  shared-FS fallback profile set `journal_mode: "DELETE"` and point `db_path` at project
  space. `logs_db_path` is the disposable **logging** DB (§6), local disk;
  `logs_archive_path` is where it is copied at end of run (§6.9).
- `orphan_strikes`: consecutive heartbeats a leased job may be absent from its worker's
  `running` list before early requeue (§5.1). `heartbeat_flush_s`: batch interval for
  persisting in-memory liveness (§4.1).
- `max_retries` (default **3**) is the number of *re-attempts* after the first run — a job
  runs at most `max_retries + 1 = 4` times before `dead`.
- `success_file` is the authoritative success marker (§4.8); may be overridden per job;
  `null` falls back to exit-code-only success.
- `per_job_mem_gb` is the **starting** authoritative budget; `mem_autoraise*` control the
  automatic raise (§4.6). Lowering is manual via `POST /admin/config`.
- `autoscale` (§13.1): `capacity_probe` is an optional override command whose stdout is
  the **idle-core integer**; when `null` the built-in probe parses the *idle* field of
  `sinfo -h -o %C` (format `allocated/idle/other/total`, summed across lines), restricted
  to `partition` when set. `squeue_poll_s` is how often submission states are refreshed
  from `squeue`; `registration_grace_s` is only the fallback when `squeue` is unavailable.

### `WorkerConfig`

```json
{
  "server_url_file": "/project/run123/server.url",
  "runner": "hisim",
  "result_root": "/project/run123/results",
  "log_root": "/project/run123/logs",
  "mode": "whole_node",

  "min_headroom_gb": 12.0,
  "cores_per_job": 1,
  "reserved_cores": 0,
  "max_slots": null,
  "max_jobs_per_child": 50,

  "node_gate": "auto",
  "max_node_cpu_percent": 95.0,
  "node_safety_buffer_gb": 16.0,
  "gate_warn_s": 600,
  "gate_max_wait_s": 3600,

  "preflight_retries": 3,
  "preflight_window_s": 60,

  "timeout_s": 7200,
  "heartbeat_interval_s": 30,
  "console_follow_interval_s": 2,
  "sample_interval_s": 10,
  "log_ship_level": "WARNING",
  "lease_batch": null,
  "backoff_s": 5.0
}
```

- `mode` is `"whole_node"` or `"single_core"` (§4.2).
- `per_job_mem_gb` is **not** set here — the worker receives it from the server at
  registration and via `set` directives (§4.6), keeping every worker aligned to one
  authoritative budget.
- `min_headroom_gb`, `cores_per_job`, `reserved_cores`, `max_slots` apply to
  `whole_node` self-accounting. `node_gate` selects the `single_core` gate source
  (`auto`/`cgroup`/`observed`/`off`, §4.2); `max_node_cpu_percent` and
  `node_safety_buffer_gb` are the observed-gate thresholds; `gate_warn_s` /
  `gate_max_wait_s` bound silent starvation. In `single_core` mode `max_slots` is forced
  to 1.

`result_root` is shared (so job payloads can omit it); the canonical per-job `result_dir`
and the per-attempt `staging_dir` are derived server-side (§4.8) and handed out with each
lease.

---

## 13. Slurm integration (G2, G10)

- **`slurm/server.sbatch`** — only for the fallback profile (§4.1): a small,
  long-time-limit allocation running `python -m hisim.hpc_harness server --config
  server.json`. Primary deployment is a plain service on a login node / VM. Either way it
  publishes `server.url` on start.
- **`slurm/worker.sbatch`** — `whole_node` variant: `--exclusive`, `--nodes=1`,
  `--requeue`. `single_core` variant: `--ntasks=1 --cpus-per-task=1` **and an explicit
  memory request** (`--mem-per-cpu` ≈ `per_job_mem_gb` + ~2 GB interpreter overhead) — on
  cgroup-enforcing clusters the job is killed at its allocation regardless of any gate,
  so the allocation must fit the job (§4.2). Runs `python -m hisim.hpc_harness worker
  --config worker.json`. Independent of every other worker — no shared MPI world.
- **`slurm/submit-workers.sh`** — convenience wrapper to scale the fleet: submit a Slurm
  **job array** (or a loop of `sbatch`) of M worker jobs; each grabs its allocation and
  joins the queue. Scale up mid-run by submitting more; scale down by cancelling — the
  server requeues anything in flight (fenced, §5.1).
- **Submit CLI** — `python -m hisim.hpc_harness submit --runner hisim --batch run123
  --scenario-dir … --glob '*.scenario.json' --sim-params …` builds payloads and
  `POST /jobs` to the server (run from a login node). Idempotent via
  `dedup_key = scenario path` **within the batch**; a new `--batch` name re-runs the same
  scenarios without colliding with a previous run (default batch name: the scenario-dir
  basename + date).

### 13.1 Autoscaler (single-core workers)

The server has an optional **autoscale mode** (`ServerConfig.autoscale`, off by default)
that keeps the `single_core` worker fleet sized to the work and the cluster, submitting
Slurm jobs itself so the operator does not have to babysit `submit-workers.sh`. It targets
`single_core` mode specifically, where scaling is naturally per-core.

**Fleet accounting.** `current` = registered, non-dead `single_core` workers **plus**
tracked Slurm submissions not yet registered. Submission states come from **polling
`squeue` on the recorded job ids** every `squeue_poll_s` (§6.7) — a worker waiting in the
Slurm queue for hours still counts, so the loop never blindly resubmits on a wall-clock
timeout. (`registration_grace_s` applies only as a fallback when `squeue` itself is
unavailable.) `slurm_queued` = tracked submissions in Slurm `PENDING` state.

**Control loop** (every `autoscale.period_s`, default 60 s). The law is **incremental** —
it sizes the *step*, not the absolute fleet, so freed cores keep getting picked up for as
long as backlog remains:

```
work         = pending + leased jobs
capacity_gap = max(0, work - current)          # jobs the current fleet can't cover
to_submit    = min(available_cores, capacity_gap)

# standby: when the cluster is (momentarily) full, keep a small queue of
# workers waiting inside Slurm so they start the instant cores free up
if available_cores < standby_floor:
    to_submit = max(to_submit, min(standby_floor - slurm_queued, capacity_gap))

to_submit = clamp(to_submit, 0, max_workers - current)
```

- `available_cores` comes from the capacity probe: by default the *idle* field of
  `sinfo -h -o %C` (`allocated/idle/other/total`), summed over lines and restricted to
  `partition` when configured — idle cores in partitions this account cannot use would
  otherwise inflate the count. `capacity_probe` overrides it with any command that prints
  the idle-core integer (portability to other schedulers/policies).
- Worked example: 10 000 jobs, 1 000 idle cores → submit 1 000. Later 200 more cores free
  up: `capacity_gap = 9 000 − ~1 000`, `to_submit = min(200, gap) = 200` — the freed cores
  are used (the v1 law, `desired = min(available, work)`, would have submitted 0 here and
  frozen the fleet at its first burst).
- **Scale-down:** when `work` shrinks below the fleet, the loop first cancels surplus
  **still-`PENDING`** Slurm submissions (`scancel`, always safe); running workers are left
  to drain-and-quit / `release` naturally (§4.2). `max_workers` is a hard safety cap.

When `work == 0` it submits nothing; existing workers drain-and-quit and the fleet
naturally shrinks to zero at the end of the run.

**Deployment note:** autoscale requires the server to run somewhere with `sbatch`/`sinfo`/
`squeue` available and submit rights — the login-node deployment (§4.1) satisfies this by
construction. On clusters where the server must live inside a compute allocation without
submit rights, run the autoscaler as a tiny separate helper on a submit node (same
`slurm_submissions` table), or fall back to manual `submit-workers.sh`.

Whole-node autoscaling is the same incremental loop with node counts instead of cores;
only single-core is specified here as requested, but the module is written
mode-generically.

---

## 14. Module layout & reuse map

```
hisim/hpc_harness/
  db.py              REUSE + extend  CORE DB (generalize tasks; fencing + idempotency in
                                     record_report/lease_tasks; add workers,
                                     slurm_submissions; batch/dedup scoping)
  logdb.py           NEW  LOGGING DB (worker_metrics, logs, console_snapshots; best-effort
                                      writes, missing-file recreate, purge/reopen — §6.8;
                                      end-of-run archive — §6.9)
  config.py          REUSE pattern   (ServerConfig, WorkerConfig)
  client.py          NEW  HTTP client (worker + submit tools; retry/backoff, lease_id
                                      replay, report dedup awareness)
  cli.py / __main__  REWRITE         subcommands: server | worker | submit | status | reset
  runners/
    base.py          NEW  Runner protocol (warmup/on_fork/run) + registry
    hisim_runner.py  NEW  wraps run_one.run_single  (thin)
    subprocess_runner.py NEW generic argv runner
  run_one.py         REUSE           HiSimRunner core (run_single unchanged)
  worker/
    worker.py        NEW  register/lease/dispatch/report/heartbeat loop; directive
                          handling (kill/drain/release/reregister/set); preflight
    spawner.py       NEW  fork-server: warmup once, gc.freeze, fork children on request (§4.3)
    warm_pool.py     REWORK of pool.py  (warm children via spawner; admission gates for
                                          both modes incl. cgroup probe; peak-RSS sampling,
                                          timeout, kill-tree, oom_score_adj, recycling)
    child.py         NEW  warm child loop (on_fork, run jobs from pipe)
    metrics.py       NEW  node + per-job + cgroup sampling
    logbuffer.py     NEW  rolling console ring buffer + buffered network log handler (§4.7)
  server/
    app.py           NEW  FastAPI routes (§7)
    service.py       NEW  queue logic over db.py (fence checks, lease replay)
    writer.py        NEW  single writer thread, grouped commits, in-memory liveness (§4.1)
    reaper.py        NEW  stale-lease + missing-worker + orphan-strike sweeps
    reconcile.py     NEW  heartbeat running-list reconciliation, kill-directive issuance (§5.1)
    eta.py           NEW  throughput / ETA
    memcheck.py      NEW  peak-vs-budget validation; auto-raise + set-directive push (§4.6)
    circuit.py       NEW  failure-storm breaker (§8.1)
    snapshot.py      NEW  periodic core-DB snapshot to shared FS (§6.5)
    logs.py          NEW  log ingest + console-snapshot store & directive issuance (§4.7)
    autoscaler.py    NEW  incremental control loop + sinfo/squeue integration (§13.1)
    dashboard/       NEW  server-rendered HTML + inlined assets
  slurm/
    server.sbatch    NEW  (fallback profile only)
    worker.sbatch    NEW  (whole_node + single_core variants; replaces hisim_hpc.sbatch)
    submit-workers.sh NEW

RETIRE (MPI): dispatcher.py, node_agent.py, protocol.py, hisim_hpc.sbatch
```

`setup.py` extra changes: replace `"hpc": ["mpi4py"]` with
`"hpc": ["fastapi", "uvicorn", "httpx", "psutil"]` (`psutil` already used by `pool.py`).

---

## 15. Testing plan

> **Platform note:** development happens on Windows, but everything from the spawner down
> is `fork()`/Linux-only. Phases 1–2 (DB, server, API) are OS-independent and run
> anywhere; the worker/warm-pool and integration tests (phase 3+) require Linux — run
> them under WSL locally and in Linux CI. Mark them `@pytest.mark.skipif(os.name != "posix")`.

- **Unit (fast, no cluster):**
  - `db.py` generalization: lease ordering by priority, dedup on `(batch_id, dedup_key)`
    incl. same key in a new batch inserting fresh, report/retry/dead transitions.
  - **Fencing:** report with wrong attempt → rejected, state unchanged; report from wrong
    worker → rejected; duplicate replay of an accepted report → `accepted:true`, no second
    `attempts` row (`UNIQUE(task_id, attempt_no)`); lease replay with the same `lease_id`
    → identical job set, `attempts` not double-incremented.
  - **Reconciliation:** heartbeat `running` containing a reclaimed job → `kill` directive;
    leased job absent from `running` for `orphan_strikes` heartbeats → requeued; heartbeat
    from dead `worker_id` → `reregister`.
  - `eta.py`, reaper thresholds (freeze time; assert requeue at 15 min).
  - `memcheck.py`: auto-raise fires at p99 + margin above budget (only after
    `mem_min_samples`), never lowers; too-high warning fires; `set` directive queued for
    all workers; `POST /admin/config` lowers.
  - retry cap: a job fails exactly `max_retries + 1` times then goes `dead` (default 4).
  - success-file check: exit 0 + marker present → renamed to canonical + `done`;
    exit 0 + marker missing → `failed`/retry; rename-collision path (existing canonical
    replaced by fenced holder) (§4.8).
  - **circuit breaker** (§8.1): trips on rate and on consecutive counts, respects
    `min_samples`, `resume` clears it.
  - **autoscaler law** (§13.1): table-driven cases — freed cores after an initial burst
    are picked up (`to_submit = min(available, gap)`), standby floor keeps ≤ floor jobs
    `PENDING` without resubmission loops, `squeue`-tracked pending workers count toward
    `current` indefinitely, scale-down cancels only `PENDING` submissions, `max_workers`
    cap; with fake `sbatch`/`sinfo`/`squeue`. Include a probe-parsing test for the
    `A/I/O/T` `sinfo -h -o %C` format (multi-line).
  - two-DB split (§6): deleting/purging the logging DB mid-run leaves scheduling and
    recovery intact; logging-DB write failures are swallowed; end-of-run archive lands at
    `logs_archive_path`.
  - snapshot (§6.5): `--from-snapshot` restart resumes; at most one interval of
    bookkeeping re-runs (jobs re-lease, results not lost).
- **Server API:** FastAPI `TestClient` over a temp-file DB — the full
  lease→report→heartbeat→reconcile cycle without a network, plus the logs/console
  round-trip, the `drain` and `release` directives, and the fenced stale-report path.
- **Worker warm pool (Linux):** a `sleep`/`echo` runner to test spawner fork, dispatch,
  admission gates (`whole_node` self-accounting; `single_core` in `observed` mode with
  faked psutil, and `cgroup` mode with a faked cgroup tree), timeout kill, child
  recycling via the spawner, crash→replacement, `kill` directive handling (child killed,
  staging dir removed), and staging→rename on success. Include the preflight: missing
  path → bounded retries → deregister `file_access` and exit; transient failure inside
  the window → leasing paused, no exit.
- **Integration (single Linux host):** real server + 1–2 workers on localhost with a
  trivial runner; assert all jobs reach `done` with canonical dirs on disk, no stray
  staging dirs, metrics recorded, dashboard endpoints return.
- **Fault injection:** SIGKILL a worker mid-job → reaper requeues after the timeout and
  the retry succeeds in a *new* staging dir; SIGSTOP a worker > `worker_timeout_s` then
  SIGCONT → on resume it gets `reregister` + `kill`, its late report is rejected, the job
  is completed exactly once by the other worker; restart the server mid-run → live
  workers' leases survive via heartbeat reconciliation (no duplicate execution), and with
  `--assume-fleet-dead` the old requeue-all behaviour holds.

Keep the existing pytest markers; add a `harness` marker for the integration tests.

---

## 16. Phased delivery

1. **DB generalization + fencing + runner registry** (+ tests). The fence/idempotency
   semantics come first — they shape the lease/report schema everything else builds on.
   No behaviour change to HiSim.
2. **Server** (FastAPI, REST, writer thread, reaper, reconciliation, ETA, circuit breaker,
   snapshots) — driven by `TestClient` and a trivial runner.
3. **Worker** (spawner/fork-server, warm pool, admission gates for both modes, preflight,
   staging/rename, metrics, recycling, log shipping + console upload) — integration on a
   Linux host with the trivial runner.
4. **HiSimRunner** wiring `run_one` end-to-end; small real batch on one node.
5. **Dashboard** (incl. console viewer, log/error explorer, banners).
6. **Slurm scripts + submit CLI + autoscaler** (§13.1); scale test on the real cluster.
7. Retire the MPI modules.

---

## 17. Open questions / future

- **Autoscaler:** whole-node autoscaling and proactively cancelling *running* (not just
  Slurm-`PENDING`) surplus workers are the natural follow-ups.
- **Priorities / fairness** across multiple submitted batches — schema supports
  `priority` and `batch_id`; policy TBD.
- **Richer output validation** — §4.8 requires a single success file for `done`; a
  future extension could let a runner check multiple files, sizes, or content hashes
  before the rename.
- **Multiple runners per server** — the DB is already runner-tagged; confirm whether one
  server should host mixed-runner queues or one runner per server instance.
- **DB growth** at very large scale — `worker_metrics` downsampling policy; possibly move
  long-term metrics to a separate rollup DB.
- **Non-cgroup cluster hardening** — the observed gate (§4.2) is deliberately coarse
  insurance; if it proves too coarse in practice, per-process attribution (tracking which
  PIDs belong to which Slurm job via `scontrol listpids`) could sharpen it.
