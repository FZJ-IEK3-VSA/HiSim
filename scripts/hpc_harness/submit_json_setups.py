"""Enqueue JSON-based system setups (``*.scenario.json``) as harness jobs.

Scans ``system_setups/*.scenario.json``, keeps those whose filename contains a
substring (default: ``building_sizer``), and submits one ``hisim`` job per scenario,
each paired with a shared ``*.simulation.json`` that defines the time range and
postprocessing. The JSON setups carry their own components, so *which* postprocessing
runs (charts, PDF report, KPIs, …) is chosen by the ``--sim-params`` file — pick a
"plots" one for human-useful artefacts.

Usage (from the repo root, server already running)::

    python scripts/hpc_harness/submit_json_setups.py \\
        --server-url-file /project/run/server.url

    # a different subset / simulation profile:
    python scripts/hpc_harness/submit_json_setups.py \\
        --server-url-file /project/run/server.url \\
        --name-filter household --sim-params 2021_minutely_plots.simulation.json

Workers for these jobs must serve the ``hisim`` runner (add an ``autoscale`` profile
for it, or start a worker with ``--runner hisim``).
"""

import argparse
import datetime
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ on sys.path

from hpc_harness.client import HarnessClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETUP_DIR = REPO_ROOT / "system_setups"
DEFAULT_NAME_FILTER = "building_sizer"
# A 15-minute-resolution profile that renders plots — charts + the usual result files.
DEFAULT_SIM_PARAMS = "2021_15minutely_plots.simulation.json"


def find_json_setups(setup_dir: Path, name_filter: str) -> List[Path]:
    """All ``*.scenario.json`` files whose name contains ``name_filter`` (case-insensitive)."""
    needle = name_filter.lower()
    return sorted(
        p for p in setup_dir.glob("*.scenario.json") if needle in p.name.lower()
    )


def resolve_sim_params(sim_params: str, setup_dir: Path) -> Path:
    """Resolve ``--sim-params`` as an absolute path, a repo path, or a name under ``setup_dir``."""
    candidates = [Path(sim_params), setup_dir / sim_params, REPO_ROOT / sim_params]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Simulation-parameters file not found: {sim_params} "
        f"(looked in {setup_dir} and {REPO_ROOT})"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Build one ``hisim`` job per matching ``*.scenario.json`` and POST the batch."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url-file", dest="server_url_file",
                        help="Path to the published server.url file.")
    parser.add_argument("--server-url", dest="server_url", help="Direct server URL override.")
    parser.add_argument("--token", help="Bearer token (default: HARNESS_TOKEN env var).")
    parser.add_argument("--setup-dir", default=str(DEFAULT_SETUP_DIR),
                        help=f"Directory of system setups (default: {DEFAULT_SETUP_DIR}).")
    parser.add_argument("--name-filter", default=DEFAULT_NAME_FILTER,
                        help=f"Only scenarios whose filename contains this (default: {DEFAULT_NAME_FILTER!r}).")
    parser.add_argument("--sim-params", default=DEFAULT_SIM_PARAMS,
                        help=f"Simulation-parameters JSON paired with every scenario "
                             f"(default: {DEFAULT_SIM_PARAMS}).")
    parser.add_argument("--batch", help="Batch name; default: json-<name-filter>-<date>.")
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list what would be submitted.")
    args = parser.parse_args(argv)

    setup_dir = Path(args.setup_dir)
    scenarios = find_json_setups(setup_dir, args.name_filter)
    if not scenarios:
        print(f"No *.scenario.json matching {args.name_filter!r} in {setup_dir}", file=sys.stderr)
        return 2
    try:
        sim_params = resolve_sim_params(args.sim_params, setup_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    batch = args.batch or f"json-{args.name_filter}-{datetime.date.today().isoformat()}"
    jobs = []
    for scenario in scenarios:
        jobs.append(
            {
                "payload": {"scenario": str(scenario.resolve()), "sim_params": str(sim_params)},
                "label": scenario.name[: -len(".scenario.json")],
                "dedup_key": f"{scenario.resolve()}|{sim_params}",
                "priority": args.priority,
            }
        )

    print(f"Batch {batch!r}: {len(jobs)} JSON scenario(s) matching {args.name_filter!r}, "
          f"sim_params={sim_params.name}")
    for job in jobs:
        print(f"  - {job['label']}")
    if args.dry_run:
        print("(dry run — nothing submitted)")
        return 0

    import os  # local: only needed for the env-var fallback

    client = HarnessClient(
        server_url=args.server_url,
        url_file=args.server_url_file,
        token=args.token or os.environ.get("HARNESS_TOKEN"),
        max_tries=3,
    )
    try:
        result = client.submit_jobs("hisim", jobs, batch)
    finally:
        client.close()
    print(f"Inserted {result['inserted']} job(s), skipped {result['skipped']} duplicate(s).")
    print("Reminder: workers must serve the 'hisim' runner to pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
