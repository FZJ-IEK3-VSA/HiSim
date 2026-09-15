"""Enqueue declarative energy systems (``*.energy_system.yaml``) as harness jobs.

Scans ``energy_systems/*.energy_system.yaml``, keeps those whose filename contains a
substring (default: ``building_sizer``), and submits one ``hisim`` job per system, each
paired with a shared ``*.simulation.yaml`` (or ``*.simulation.json``) that defines the
time range and postprocessing. An energy-system file carries its own components, so
*which* postprocessing runs (charts, PDF report, KPIs, …) is chosen by the
``--sim-params`` file — pick a "plots" one for human-useful artefacts.

The ``*.grouped.energy_system.yaml`` files are skipped: a grouped file is the same twin
with its optional structure expressed as groups and variants, so submitting both would
run one household twice. The flat twin is the runnable one.

Usage (from the repo root, server already running)::

    python scripts/hpc_harness/submit_energy_systems.py \\
        --server-url-file /project/run/server.url

    # a different subset / simulation profile:
    python scripts/hpc_harness/submit_energy_systems.py \\
        --server-url-file /project/run/server.url \\
        --name-filter household --sim-params 2021_minutely.simulation.yaml

Workers for these jobs must serve the ``hisim`` runner (add an ``autoscale`` profile
for it, or start a worker with ``--runner hisim``).
"""

import argparse
import datetime
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ on sys.path

from hpc_harness.client import HarnessClient  # noqa: E402  # pylint: disable=wrong-import-position

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENERGY_SYSTEM_DIR = REPO_ROOT / "energy_systems"
DEFAULT_NAME_FILTER = "building_sizer"
#: The suffix a runnable twin carries, and the one a grouped variant of it carries.
ENERGY_SYSTEM_SUFFIX = ".energy_system.yaml"
GROUPED_SUFFIX = ".grouped.energy_system.yaml"
# A full-year, minutely profile that renders plots — charts + the usual result files.
DEFAULT_SIM_PARAMS = "2021_minutely.simulation.yaml"


def find_energy_systems(energy_system_dir: Path, name_filter: str) -> List[Path]:
    """All runnable ``*.energy_system.yaml`` files whose name contains ``name_filter``.

    Case-insensitive on the filter; ``*.grouped.energy_system.yaml`` files are excluded,
    because they describe the same household as the flat twin beside them.
    """
    needle = name_filter.lower()
    return sorted(
        p
        for p in energy_system_dir.glob(f"*{ENERGY_SYSTEM_SUFFIX}")
        if needle in p.name.lower() and not p.name.endswith(GROUPED_SUFFIX)
    )


def resolve_sim_params(sim_params: str, energy_system_dir: Path) -> Path:
    """Resolve ``--sim-params`` as an absolute path, a repo path, or a name under the directory."""
    candidates = [Path(sim_params), energy_system_dir / sim_params, REPO_ROOT / sim_params]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Simulation-parameters file not found: {sim_params} "
        f"(looked in {energy_system_dir} and {REPO_ROOT})"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Build one ``hisim`` job per matching ``*.energy_system.yaml`` and POST the batch."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url-file", dest="server_url_file",
                        help="Path to the published server.url file.")
    parser.add_argument("--server-url", dest="server_url", help="Direct server URL override.")
    parser.add_argument("--token", help="Bearer token (default: HARNESS_TOKEN env var).")
    parser.add_argument("--energy-system-dir", dest="energy_system_dir",
                        default=str(DEFAULT_ENERGY_SYSTEM_DIR),
                        help=f"Directory of energy-system files (default: {DEFAULT_ENERGY_SYSTEM_DIR}).")
    parser.add_argument("--name-filter", default=DEFAULT_NAME_FILTER,
                        help=f"Only systems whose filename contains this (default: {DEFAULT_NAME_FILTER!r}).")
    parser.add_argument("--sim-params", default=DEFAULT_SIM_PARAMS,
                        help=f"Simulation-parameters file paired with every energy system "
                             f"(default: {DEFAULT_SIM_PARAMS}).")
    parser.add_argument("--batch", help="Batch name; default: energy-systems-<name-filter>-<date>.")
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="Only list what would be submitted.")
    args = parser.parse_args(argv)

    energy_system_dir = Path(args.energy_system_dir)
    energy_systems = find_energy_systems(energy_system_dir, args.name_filter)
    if not energy_systems:
        print(f"No *{ENERGY_SYSTEM_SUFFIX} matching {args.name_filter!r} in {energy_system_dir}",
              file=sys.stderr)
        return 2
    try:
        sim_params = resolve_sim_params(args.sim_params, energy_system_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    batch = args.batch or f"energy-systems-{args.name_filter}-{datetime.date.today().isoformat()}"
    sim_params_str = str(sim_params)
    jobs = []
    for energy_system in energy_systems:
        resolved = energy_system.resolve()
        jobs.append({
            "payload": {"energy_system": str(resolved), "sim_params": sim_params_str},
            "label": energy_system.name[: -len(ENERGY_SYSTEM_SUFFIX)],
            "dedup_key": f"{resolved}|{sim_params}",
            "priority": args.priority,
        })

    print(f"Batch {batch!r}: {len(jobs)} energy system(s) matching {args.name_filter!r}, "
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
