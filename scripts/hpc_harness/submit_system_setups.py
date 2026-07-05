"""Enqueue every Python-based system setup as a harness job (systematic test).

Scans ``system_setups/*.py`` and submits one ``hisim_setup`` job per setup, all with
the same forced simulation duration (default: **one week**, 60 s timesteps) so the
whole matrix is comparable and finishes quickly. HiSim writes ``finished.flag`` at the
end of a successful run, which is exactly the harness's default success marker — a
setup that exits 0 without completing is still counted as failed.

Usage (from the repo root, server already running)::

    python scripts/hpc_harness/submit_system_setups.py \\
        --server-url-file /project/run/server.url

    # later, the same set for a full year in a fresh batch:
    python scripts/hpc_harness/submit_system_setups.py \\
        --server-url-file /project/run/server.url --duration full_year

Workers for these jobs must serve the ``hisim_setup`` runner::

    python -m hpc_harness worker --config worker.json --runner hisim_setup
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

# Not system setups (no runnable single-household setup_function of their own).
ALWAYS_SKIP = {"__init__.py"}

# Postprocessing that produces charts and other human-useful artefacts (enabled by
# --full-postprocessing). Prerequisites are included (GENERATE_PDF_REPORT for the report
# writers, MAKE_NETWORK_CHARTS for WRITE_NETWORK_CHARTS_TO_REPORT). The niche webtool /
# scenario-evaluation / housing-database exports are intentionally left out.
FULL_POSTPROCESSING = [
    "PLOT_LINE", "PLOT_CARPET", "PLOT_SINGLE_DAYS", "PLOT_MONTHLY_BAR_CHARTS", "PLOT_SANKEY",
    "MAKE_NETWORK_CHARTS",
    "EXPORT_TO_CSV", "EXPORT_MONTHLY_RESULTS",
    "COMPUTE_OPEX", "COMPUTE_CAPEX", "COMPUTE_KPIS",
    "GENERATE_PDF_REPORT", "WRITE_COMPONENTS_TO_REPORT", "WRITE_ALL_OUTPUTS_TO_REPORT",
    "WRITE_NETWORK_CHARTS_TO_REPORT", "INCLUDE_CONFIGS_IN_PDF_REPORT", "INCLUDE_IMAGES_IN_PDF_REPORT",
]


def find_setups(setup_dir: Path, exclude: List[str]) -> List[Path]:
    """All Python system-setup files, minus exclusions."""
    excluded = ALWAYS_SKIP | {name if name.endswith(".py") else f"{name}.py" for name in exclude}
    return sorted(p for p in setup_dir.glob("*.py") if p.name not in excluded)


def main(argv: Optional[List[str]] = None) -> int:
    """Build one job per system setup and POST the batch to the server."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url-file", dest="server_url_file",
                        help="Path to the published server.url file.")
    parser.add_argument("--server-url", dest="server_url", help="Direct server URL override.")
    parser.add_argument("--token", help="Bearer token (default: HARNESS_TOKEN env var).")
    parser.add_argument("--setup-dir", default=str(DEFAULT_SETUP_DIR),
                        help=f"Directory of system setups (default: {DEFAULT_SETUP_DIR}).")
    parser.add_argument("--duration", default="one_week",
                        choices=["one_day", "one_week", "three_months", "full_year"],
                        help="Forced simulation duration for every setup (default: one_week).")
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--seconds-per-timestep", dest="seconds_per_timestep",
                        type=int, default=60)
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Setup filenames to skip (with or without .py).")
    parser.add_argument("--batch", help="Batch name; default: system-setups-<duration>-<date>.")
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--full-postprocessing", action="store_true",
                        help="Enable all chart/report/KPI postprocessing (plots, PDF report, "
                             "CSV, monthly results, OPEX/CAPEX, KPIs). Slower but produces "
                             "human-useful artefacts, not just finished.flag.")
    parser.add_argument("--post-processing-options", nargs="*", dest="post_processing_options",
                        metavar="OPTION",
                        help="Explicit PostProcessingOptions member names to enable (overrides "
                             "--full-postprocessing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list what would be submitted.")
    args = parser.parse_args(argv)

    if args.post_processing_options is not None:
        post_processing = list(args.post_processing_options)
    elif args.full_postprocessing:
        post_processing = list(FULL_POSTPROCESSING)
    else:
        post_processing = []

    setups = find_setups(Path(args.setup_dir), args.exclude)
    if not setups:
        print(f"No system setups found in {args.setup_dir}", file=sys.stderr)
        return 2

    batch = args.batch or f"system-setups-{args.duration}-{datetime.date.today().isoformat()}"
    pp_tag = f"|pp={','.join(post_processing)}" if post_processing else ""
    jobs = []
    for setup in setups:
        payload = {
            "setup_module": str(setup),
            "duration": args.duration,
            "year": args.year,
            "seconds_per_timestep": args.seconds_per_timestep,
        }
        if post_processing:
            payload["post_processing_options"] = post_processing
        jobs.append(
            {
                "payload": payload,
                "label": setup.stem,
                "dedup_key": f"{setup}|{args.duration}|{args.year}|{args.seconds_per_timestep}{pp_tag}",
                "priority": args.priority,
            }
        )

    pp_note = f", postprocessing={len(post_processing)} option(s)" if post_processing else ""
    print(f"Batch {batch!r}: {len(jobs)} system setup(s), duration={args.duration}, "
          f"year={args.year}, {args.seconds_per_timestep}s timesteps{pp_note}")
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
        result = client.submit_jobs("hisim_setup", jobs, batch)
    finally:
        client.close()
    print(f"Inserted {result['inserted']} job(s), skipped {result['skipped']} duplicate(s).")
    print("Reminder: workers must run with --runner hisim_setup to pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
