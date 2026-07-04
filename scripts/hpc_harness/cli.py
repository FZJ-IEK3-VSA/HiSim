"""Command-line interface for the HPC harness (spec §13, §14).

Commands
--------
  server   Run the queue server (FastAPI/uvicorn) — login node/VM or service allocation.
  worker   Run one worker (inside a Slurm allocation on a compute node).
  submit   Scan scenario files and enqueue them as jobs on the server.
  status   Print the server's /status summary.
  reset    Requeue stuck/failed jobs via the server.

Examples
--------
  python -m hpc_harness server --config server.json
  python -m hpc_harness worker --config worker.json
  python -m hpc_harness submit --server-url-file /project/run/server.url \\
      --runner hisim --batch run1 --scenario-dir ./scenarios --sim-params sim.json
"""

import argparse
import datetime
import glob as globmod
import json
import logging
import os
import socket
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, List, Optional, cast

from hpc_harness.config import ServerConfig, WorkerConfig


def _publish_url(cfg: ServerConfig) -> None:
    """Write ip:port atomically to the server.url file (spec §4.5)."""
    if not cfg.url_publish_path:
        return
    host = cfg.bind_host
    if host in ("0.0.0.0", "::"):
        try:  # the IP a compute node can reach, not just a login-node alias
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("10.255.255.255", 1))
            host = probe.getsockname()[0]
            probe.close()
        except OSError:
            host = socket.gethostbyname(socket.gethostname())
    target = Path(cfg.url_publish_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".server.url.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"http://{host}:{cfg.bind_port}\n")
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o640)  # group-readable only (spec §11)
    except OSError:
        pass
    print(f"Published server address http://{host}:{cfg.bind_port} to {target}")


def cmd_server(args: argparse.Namespace) -> int:
    """Run the queue server until interrupted."""
    import uvicorn  # pylint: disable=import-outside-toplevel

    from hpc_harness.server.app import create_app  # pylint: disable=import-outside-toplevel
    from hpc_harness.server.service import HarnessService  # pylint: disable=import-outside-toplevel

    cfg = ServerConfig.from_file(args.config) if args.config else ServerConfig()
    cfg.apply_overrides(
        db_path=args.db, result_root=args.result_root, bind_port=args.port, token=args.token
    ).finalize()

    service = HarnessService(cfg)
    service.startup(assume_fleet_dead=args.assume_fleet_dead)
    if cfg.autoscale.enabled:
        from hpc_harness.server.autoscaler import Autoscaler  # pylint: disable=import-outside-toplevel

        service.autoscaler = Autoscaler(service, cfg.autoscale)
    service.start_background()
    _publish_url(cfg)
    try:
        uvicorn.run(create_app(service), host=cfg.bind_host, port=cfg.bind_port, log_level="info")
    finally:
        service.shutdown()
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Run one worker inside its Slurm allocation."""
    from hpc_harness.worker.worker import Worker  # pylint: disable=import-outside-toplevel

    cfg = WorkerConfig.from_file(args.config) if args.config else WorkerConfig()
    cfg.apply_overrides(
        server_url_file=args.server_url_file,
        server_url=args.server_url,
        runner=args.runner,
        result_root=args.result_root,
        mode=args.mode,
    ).finalize()
    return Worker(cfg).run()


def _make_client(args: argparse.Namespace):  # noqa: ANN202
    from hpc_harness.client import HarnessClient  # pylint: disable=import-outside-toplevel

    return HarnessClient(
        server_url=args.server_url,
        url_file=args.server_url_file,
        token=args.token or os.environ.get("HARNESS_TOKEN"),
        max_tries=3,
    )


def _report_cli_exception(args: argparse.Namespace, source: str, exc: BaseException) -> None:
    """Best-effort: report a CLI command's exception to the server's error store (§4.7)."""
    try:
        client = _make_client(args)
        try:
            client.report_errors(None, [{
                "source": source,
                "error_type": type(exc).__name__,
                "message": str(exc)[:2000],
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[:16000],
                "location": f"cli:{source}",
                "host": socket.gethostname(),
            }])
        finally:
            client.close()
    except Exception:  # pylint: disable=broad-except
        pass


def cmd_submit(args: argparse.Namespace) -> int:
    """Build job payloads from scenario files and POST them to the server."""
    paths = sorted(
        str(Path(p).resolve()) for p in globmod.glob(os.path.join(args.scenario_dir, args.glob))
    )
    if not paths:
        print(f"No files matching {args.glob!r} in {args.scenario_dir}", file=sys.stderr)
        return 2
    sim_params = str(Path(args.sim_params).resolve()) if args.sim_params else None
    jobs = []
    for path in paths:
        payload = {"scenario": path}
        if sim_params:
            payload["sim_params"] = sim_params
        jobs.append(
            {
                "payload": payload,
                "label": Path(path).stem.replace(".scenario", ""),
                "dedup_key": path,
                "priority": args.priority,
            }
        )
    batch = args.batch or (
        Path(args.scenario_dir).resolve().name + "-" + datetime.date.today().isoformat()
    )
    client = _make_client(args)
    try:
        result = client.submit_jobs(args.runner, jobs, batch)
    finally:
        client.close()
    print(
        f"Batch {batch!r}: inserted {result['inserted']} job(s), "
        f"skipped {result['skipped']} duplicate(s)."
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print the server status summary."""
    client = _make_client(args)
    try:
        print(json.dumps(client.status(), indent=2))
    finally:
        client.close()
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Requeue leased and/or failed jobs via the admin API."""
    if not (args.leased or args.failed):
        print("Nothing to do: pass --leased and/or --failed.", file=sys.stderr)
        return 2
    client = _make_client(args)
    try:
        result = client.admin_reset(leased=args.leased, failed=args.failed)
    finally:
        client.close()
    print(f"Requeued {result['requeued']} job(s).")
    return 0


def _add_client_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server-url-file", dest="server_url_file",
                        help="Path to the published server.url file.")
    parser.add_argument("--server-url", dest="server_url", help="Direct server URL override.")
    parser.add_argument("--token", help="Bearer token (default: HARNESS_TOKEN env var).")


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and dispatch to the requested sub-command."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="python -m hpc_harness", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_server = sub.add_parser("server", help="Run the queue server.")
    p_server.add_argument("--config", help="Path to a server JSON config file.")
    p_server.add_argument("--db", help="Core DB path override.")
    p_server.add_argument("--result-root", dest="result_root", help="Result root override.")
    p_server.add_argument("--port", type=int, help="Bind port override.")
    p_server.add_argument("--token", help="Bearer token override.")
    p_server.add_argument("--assume-fleet-dead", action="store_true",
                          help="Cold start: requeue all leased jobs (only when no worker is alive).")
    p_server.set_defaults(func=cmd_server)

    p_worker = sub.add_parser("worker", help="Run one worker (on a compute node).")
    p_worker.add_argument("--config", help="Path to a worker JSON config file.")
    p_worker.add_argument("--server-url-file", dest="server_url_file")
    p_worker.add_argument("--server-url", dest="server_url")
    p_worker.add_argument("--runner")
    p_worker.add_argument("--result-root", dest="result_root")
    p_worker.add_argument("--mode", choices=["whole_node", "single_core"])
    p_worker.set_defaults(func=cmd_worker)

    p_submit = sub.add_parser("submit", help="Enqueue scenario files as jobs.")
    _add_client_args(p_submit)
    p_submit.add_argument("--runner", default="hisim")
    p_submit.add_argument("--batch", help="Batch name (dedup scope); default: dir name + date.")
    p_submit.add_argument("--scenario-dir", required=True)
    p_submit.add_argument("--glob", default="*.json")
    p_submit.add_argument("--sim-params", dest="sim_params",
                          help="*.simulation.json shared by all jobs (hisim runner).")
    p_submit.add_argument("--priority", type=int, default=0)
    p_submit.set_defaults(func=cmd_submit)

    p_status = sub.add_parser("status", help="Show the server status summary.")
    _add_client_args(p_status)
    p_status.set_defaults(func=cmd_status)

    p_reset = sub.add_parser("reset", help="Requeue leased and/or failed jobs.")
    _add_client_args(p_reset)
    p_reset.add_argument("--leased", action="store_true")
    p_reset.add_argument("--failed", action="store_true")
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    command = cast(Callable[[argparse.Namespace], int], args.func)
    try:
        return command(args)
    except Exception as exc:  # pylint: disable=broad-except
        # Client commands report their failure to the server's persistent error store
        # before propagating (server/worker record their own errors elsewhere).
        if args.command in ("submit", "status", "reset"):
            _report_cli_exception(args, args.command, exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
