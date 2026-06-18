"""Worker rank (rank > 0): a node-agent that pulls tasks and runs them locally.

The agent keeps its local simulation pool full by requesting work from the head,
reports finished tasks back, and exits cleanly when the head signals SHUTDOWN and
the local pool has drained. It never touches the database.
"""

import time

from hisim.hpc_harness.config import HarnessConfig
from hisim.hpc_harness.pool import LocalPool, compute_max_slots
from hisim.hpc_harness.protocol import GRANT, NONE, REPORT, REQUEST, SHUTDOWN, TAG


def run_agent(comm: "object", cfg: HarnessConfig) -> None:
    """Run the node-agent loop on a worker rank until the head signals shutdown."""
    from mpi4py import MPI

    rank = comm.Get_rank()
    host = MPI.Get_processor_name()

    slots = compute_max_slots(cfg.per_sim_mem_gb, cfg.min_headroom_gb, cfg.max_slots)
    pool = LocalPool(
        host=f"rank{rank}@{host}", sim_params=cfg.sim_params, result_root=cfg.result_root,
        per_sim_mem_gb=cfg.per_sim_mem_gb, min_headroom_gb=cfg.min_headroom_gb,
        timeout_s=cfg.timeout_s, max_slots=slots,
    )
    print(f"[rank{rank}] start on {host}: up to {slots} concurrent simulations", flush=True)

    awaiting_grant = False
    shutting_down = False
    next_request_at = 0.0

    try:
        while True:
            # Advance the pool and report anything that finished.
            reports = pool.tick()
            if reports:
                comm.send({"type": REPORT, "reports": reports}, dest=0, tag=TAG)

            # Handle any replies from the head.
            while comm.iprobe(source=0, tag=TAG):
                msg = comm.recv(source=0, tag=TAG)
                if msg["type"] == GRANT:
                    pool.add_tasks(msg["tasks"])
                    awaiting_grant = False
                elif msg["type"] == NONE:
                    awaiting_grant = False
                    next_request_at = time.time() + cfg.backoff_s
                elif msg["type"] == SHUTDOWN:
                    awaiting_grant = False
                    shutting_down = True

            # Ask for more work when we have spare capacity.
            now = time.time()
            if not shutting_down and not awaiting_grant and now >= next_request_at:
                free = pool.free_slots()
                if free > 0:
                    comm.send({"type": REQUEST, "host": host, "rank": rank, "n_free": free},
                              dest=0, tag=TAG)
                    awaiting_grant = True

            if shutting_down and pool.is_idle():
                break

            time.sleep(cfg.sample_interval_s)

        print(f"[rank{rank}] shutdown on {host}", flush=True)
    except BaseException:
        pool.kill_all()
        raise
