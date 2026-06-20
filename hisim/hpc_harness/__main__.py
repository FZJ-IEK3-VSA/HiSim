"""Command-line interface for the HiSim MPI HPC harness.

Commands
--------
  import   Scan a directory of scenario files into the task database.
  run      Launch the MPI master/worker run (invoke under ``srun``/``mpirun``).
  status   Print a summary of task states.
  reset    Requeue leased and/or failed tasks.

Examples
--------
  python -m hisim.hpc_harness import --db run.db --scenario-dir ./scenarios
  srun python -m hisim.hpc_harness run --config harness.json
  python -m hisim.hpc_harness status --db run.db
"""

import argparse
import sys
import traceback
from typing import Callable, cast

from hisim.hpc_harness import db
from hisim.hpc_harness.config import HarnessConfig


def _build_config(args: argparse.Namespace) -> HarnessConfig:
    """Merge config file and CLI overrides into a finalized HarnessConfig."""
    cfg = HarnessConfig.from_file(args.config) if args.config else HarnessConfig()
    cfg.apply_overrides(
        db=args.db,
        sim_params=args.sim_params,
        result_root=args.result_root,
        per_sim_mem_gb=args.per_sim_mem_gb,
        min_headroom_gb=args.min_headroom_gb,
        max_slots=args.max_slots,
        timeout_s=args.timeout_s,
        max_attempts=args.max_attempts,
        lease_timeout_s=args.lease_timeout_s,
        sample_interval_s=args.sample_interval_s,
        backoff_s=args.backoff_s,
        head_runs_jobs=args.head_runs_jobs,
    )
    return cfg.finalize()


def cmd_import(args: argparse.Namespace) -> int:
    """Import scenario files from a directory into the database (idempotent)."""
    conn = db.connect(args.db)
    result = db.import_scenarios(conn, args.scenario_dir, args.glob)
    conn.close()
    print(f"Found {result['found']} file(s) matching '{args.glob}', "
          f"inserted {result['inserted']} new task(s).")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print task counts by status."""
    conn = db.connect(args.db)
    counts = db.counts(conn)
    conn.close()
    total = counts.pop("total", 0)
    print(f"Database: {args.db}")
    for state in (db.PENDING, db.LEASED, db.DONE, db.FAILED, db.DEAD):
        print(f"  {state:>8}: {counts.get(state, 0)}")
    print(f"  {'total':>8}: {total}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Requeue leased and/or failed tasks."""
    if not (args.leased or args.failed):
        print("Nothing to do: pass --leased and/or --failed.", file=sys.stderr)
        return 2
    conn = db.connect(args.db)
    affected = db.reset(conn, leased=args.leased, failed=args.failed)
    conn.close()
    print(f"Requeued {affected} task(s).")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Launch the MPI run. Rank 0 dispatches; other ranks are node-agents."""
    cfg = _build_config(args)

    try:
        from mpi4py import MPI
    except ModuleNotFoundError:
        print("mpi4py is required for 'run'. Install it (built against your cluster MPI), "
              "e.g. 'pip install -e .[hpc]'.", file=sys.stderr)
        return 1

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    try:
        if rank == 0:
            from hisim.hpc_harness.dispatcher import run_head
            run_head(comm, cfg)
        else:
            from hisim.hpc_harness.node_agent import run_agent
            run_agent(comm, cfg)
    except Exception:
        # A crashing rank must tear down the whole job; restart resumes from the DB.
        traceback.print_exc()
        comm.Abort(1)
    return 0


def _add_run_overrides(parser: argparse.ArgumentParser) -> None:
    """Add the optional run settings that override the config file."""
    parser.add_argument("--config", help="Path to a harness JSON config file.")
    parser.add_argument("--db", help="Path to the SQLite task database.")
    parser.add_argument("--sim-params", dest="sim_params",
                        help="Path to the *.simulation.json shared by all tasks.")
    parser.add_argument("--result-root", dest="result_root",
                        help="Root directory for per-task result folders.")
    parser.add_argument("--per-sim-mem-gb", dest="per_sim_mem_gb", type=float,
                        help="Estimated peak memory per simulation (GB).")
    parser.add_argument("--min-headroom-gb", dest="min_headroom_gb", type=float,
                        help="Minimum free memory to keep available (GB).")
    parser.add_argument("--max-slots", dest="max_slots", type=int,
                        help="Hard cap on concurrent simulations per node.")
    parser.add_argument("--timeout-s", dest="timeout_s", type=float,
                        help="Per-simulation wall-clock timeout (seconds).")
    parser.add_argument("--max-attempts", dest="max_attempts", type=int,
                        help="Maximum attempts per task before marking it dead.")
    parser.add_argument("--lease-timeout-s", dest="lease_timeout_s", type=float,
                        help="Reclaim a lease after this long with no report (seconds).")
    parser.add_argument("--sample-interval-s", dest="sample_interval_s", type=float,
                        help="Loop tick interval (seconds).")
    parser.add_argument("--backoff-s", dest="backoff_s", type=float,
                        help="Wait after a NONE reply before re-requesting (seconds).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--head-runs-jobs", dest="head_runs_jobs", action="store_true", default=None,
                       help="Rank 0 also runs simulations (default).")
    group.add_argument("--no-head-jobs", dest="head_runs_jobs", action="store_false", default=None,
                       help="Rank 0 is a pure dispatcher.")


def main(argv: "list[str] | None" = None) -> int:
    """Parse arguments and dispatch to the requested sub-command."""
    parser = argparse.ArgumentParser(prog="python -m hisim.hpc_harness", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Import scenario files into the database.")
    p_import.add_argument("--db", required=True, help="Path to the SQLite task database.")
    p_import.add_argument("--scenario-dir", required=True, help="Directory of scenario files.")
    p_import.add_argument("--glob", default="*.json", help="Glob pattern (default: *.json).")
    p_import.set_defaults(func=cmd_import)

    p_run = sub.add_parser("run", help="Launch the MPI master/worker run.")
    _add_run_overrides(p_run)
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="Show task counts by status.")
    p_status.add_argument("--db", required=True, help="Path to the SQLite task database.")
    p_status.set_defaults(func=cmd_status)

    p_reset = sub.add_parser("reset", help="Requeue leased and/or failed tasks.")
    p_reset.add_argument("--db", required=True, help="Path to the SQLite task database.")
    p_reset.add_argument("--leased", action="store_true", help="Requeue stuck leased tasks.")
    p_reset.add_argument("--failed", action="store_true", help="Revive failed/dead tasks (reset attempts).")
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    command = cast(Callable[[argparse.Namespace], int], args.func)
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
