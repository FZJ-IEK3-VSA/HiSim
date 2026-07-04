"""Head rank (rank 0): owns the task database and dispatches work to all nodes.

The head runs a single event loop that, each tick:
  1. answers MPI work requests and applies finished-task reports,
  2. optionally runs its own local simulation pool (``head_runs_jobs``),
  3. reclaims stale leases,
  4. checks for completion.

It is the only process that touches the SQLite database.
"""

import time
from typing import TYPE_CHECKING

from hisim.hpc_harness import db
from hisim.hpc_harness.config import HarnessConfig
from hisim.hpc_harness.pool import LocalPool, compute_max_slots
from hisim.hpc_harness.protocol import GRANT, NO_WORK_AVAILABLE, REPORT, REQUEST, SHUTDOWN, TAG

if TYPE_CHECKING:
    # mpi4py is imported lazily inside ``run_head`` to avoid a hard import
    # dependency at module load; it is only needed here for type checking.
    from mpi4py import MPI


# Reaper period constants
REAPER_PERIOD_MULTIPLIER = 10
MIN_REAPER_PERIOD_SECONDS = 30.0


def _log(message: str) -> None:
    print(f"[head] {message}", flush=True)


def run_head(comm: "MPI.Comm", cfg: HarnessConfig) -> None:
    """Run the dispatcher loop on rank 0 until every task is finished.

    Args:
        comm: MPI communicator for exchanging request/grant/report/shutdown
            messages with worker ranks.
        cfg: Harness configuration providing the database path, simulation
            parameters, result root, lease timeout, and optional local-pool
            settings.

    Raises:
        ValueError: If ``cfg.lease_timeout_s`` is not set.
    """
    from mpi4py import MPI  # pylint: disable=import-outside-toplevel,import-error

    size = comm.Get_size()
    n_workers = size - 1
    host = MPI.Get_processor_name()
    db_path, sim_params, result_root = cfg.required_paths()
    lease_timeout_s = cfg.lease_timeout_s
    if lease_timeout_s is None:
        raise ValueError("lease_timeout_s must be set before running the dispatcher.")

    conn = db.connect(db_path)
    db.set_meta(conn, "sim_params", sim_params)
    db.set_meta(conn, "result_root", result_root)
    db.set_meta(conn, "run_started_at", str(time.time()))
    recovered = db.startup_recovery(conn, cfg.max_attempts)
    initial = db.counts(conn)
    _log(f"start on {host}: {size} ranks, {initial.get('total', 0)} tasks "
         f"({initial.get('pending', 0)} pending, {recovered} leases recovered from a previous run)")

    pool = None
    if cfg.head_runs_jobs:
        slots = compute_max_slots(cfg.per_sim_mem_gb, cfg.min_headroom_gb, cfg.max_slots)
        pool = LocalPool(
            host=f"rank0@{host}", sim_params=sim_params, result_root=result_root,
            per_sim_mem_gb=cfg.per_sim_mem_gb, min_headroom_gb=cfg.min_headroom_gb,
            timeout_s=cfg.timeout_s, max_slots=slots,
        )
        _log(f"head also runs jobs (up to {slots} concurrent on this node)")

    shutdown_ranks: set = set()
    status = MPI.Status()
    last_reaper = 0.0
    reaper_period = max(cfg.sample_interval_s * REAPER_PERIOD_MULTIPLIER, MIN_REAPER_PERIOD_SECONDS)

    try:
        while True:
            # 1. Service all pending MPI messages.
            while comm.iprobe(source=MPI.ANY_SOURCE, tag=TAG, status=status):
                src = status.Get_source()
                msg = comm.recv(source=src, tag=TAG)
                if msg["type"] == REQUEST:
                    tasks = db.lease_tasks(conn, msg["num_free_slots"], leased_by=f"rank{src}@{msg['host']}")
                    if tasks:
                        conn.commit()
                        comm.send({"type": GRANT, "tasks": tasks}, dest=src, tag=TAG)
                    elif db.is_drained(conn):
                        comm.send({"type": SHUTDOWN}, dest=src, tag=TAG)
                        shutdown_ranks.add(src)
                    else:
                        comm.send({"type": NO_WORK_AVAILABLE}, dest=src, tag=TAG)
                elif msg["type"] == REPORT:
                    for report in msg["reports"]:
                        db.record_report(conn, report, cfg.max_attempts)
                    conn.commit()

            # 2. Run the head's own local pool.
            if pool is not None:
                reports = pool.tick()
                if reports:
                    for report in reports:
                        db.record_report(conn, report, cfg.max_attempts)
                    conn.commit()
                free = pool.free_slots()
                if free > 0:
                    tasks = db.lease_tasks(conn, free, leased_by=f"rank0-local@{host}")
                    if tasks:
                        conn.commit()
                        pool.add_tasks(tasks)

            # 3. Reclaim stale leases (lost reports / dead workers).
            now = time.time()
            if now - last_reaper > reaper_period:
                reclaimed = db.reset_stale_leases(conn, lease_timeout_s, cfg.max_attempts)
                if reclaimed:
                    conn.commit()
                    _log(f"reclaimed {reclaimed} stale lease(s)")
                last_reaper = now

            # 4. Done when the queue is drained, the local pool is idle, and every
            #    worker has been told to shut down.
            drained = db.is_drained(conn)
            pool_idle = pool is None or pool.is_idle()
            if drained and pool_idle and len(shutdown_ranks) >= n_workers:
                break

            time.sleep(cfg.sample_interval_s)

        final = db.counts(conn)
        _log(f"finished: {final.get('done', 0)} done, {final.get('dead', 0)} dead, "
             f"{final.get('failed', 0)} failed of {final.get('total', 0)} total")
    finally:
        conn.commit()
        conn.close()
