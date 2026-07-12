#!/usr/bin/env python3
"""Regenerate ``system_setups/*.scenario.json`` files from their ``.py`` setups.

Each setup is (re)built with the canonical converter
(:mod:`hisim.hisim_convert_to_json`) in its **own subprocess** so that module
imports and HiSim singletons cannot bleed between setups. Results are written
back next to the setup as ``<setup>.scenario.json``.

By default only setups that already have a committed ``.scenario.json`` sibling
are regenerated (pass ``--all-py`` to cover every ``.py`` instead).

Parallelism
-----------
``--jobs/-j N`` runs up to ``N`` setups at once. Local-LPG occupancy setups use
a shared ``pylpg/C<index>`` working directory that defaults to ``C1`` and would
collide under concurrency, so every worker slot is handed a distinct index via
the ``HISIM_LOCAL_LPG_CALC_INDEX`` environment variable (honoured by
``UtspLpgConnector``). With ``--jobs 1`` (the default) behaviour is unchanged.

The converter also emits a per-setup ``<setup>.simulation.json``; the repo only
tracks grouped ``2021_*.simulation.json`` files, so these strays are removed
afterwards unless ``--keep-simulation-json`` is given.

Examples
--------
    python scripts/regenerate_scenario_jsons.py                 # all, sequential
    python scripts/regenerate_scenario_jsons.py -j 4            # 4 in parallel
    python scripts/regenerate_scenario_jsons.py --only household_gas_building_sizer
    python scripts/regenerate_scenario_jsons.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_SETUPS_DIR = REPO_ROOT / "system_setups"
CONVERTER = REPO_ROOT / "hisim" / "hisim_convert_to_json.py"


@dataclass
class SetupResult:
    """Outcome of regenerating one setup."""

    stem: str
    ok: bool
    returncode: int
    log_path: Path
    message: str = ""


def _normalize_stems(names: list[str]) -> set[str]:
    """Return setup stems from a list of names given with or without ``.py``."""
    return {name[:-3] if name.endswith(".py") else name for name in names}


def discover_setups(only: Optional[list[str]], all_py: bool, exclude: Optional[list[str]] = None) -> list[Path]:
    """Return the setup ``.py`` paths to regenerate.

    Default: every ``*.py`` that has a sibling ``*.scenario.json``.
    ``all_py``: every ``*.py`` except ``__init__.py``.
    ``only``: restrict to the given setup stems (with or without ``.py``).
    ``exclude``: drop these setup stems (e.g. ones needing uninstalled optional
    deps); applied after ``only``.
    """
    if all_py:
        candidates = sorted(p for p in SYSTEM_SETUPS_DIR.glob("*.py") if p.name != "__init__.py")
    else:
        candidates = sorted(
            p
            for p in SYSTEM_SETUPS_DIR.glob("*.py")
            if p.name != "__init__.py" and p.with_suffix(".scenario.json").exists()
        )

    if only:
        wanted = _normalize_stems(only)
        candidates = [p for p in candidates if p.stem in wanted]
        missing = wanted - {p.stem for p in candidates}
        if missing:
            raise SystemExit(f"--only names not found as setups: {sorted(missing)}")

    if exclude:
        excluded = _normalize_stems(exclude)
        candidates = [p for p in candidates if p.stem not in excluded]

    return candidates


def regenerate_one(
    setup_path: Path,
    python: str,
    index_pool: "queue.Queue[int]",
    log_dir: Path,
    keep_simulation_json: bool,
) -> SetupResult:
    """Regenerate a single setup's scenario JSON in a subprocess.

    Borrows a unique local-LPG calc index from ``index_pool`` for the duration
    of the run so concurrent workers never share a ``pylpg/C<index>`` dir.
    """
    stem = setup_path.stem
    log_path = log_dir / f"{stem}.log"
    calc_index = index_pool.get()
    try:
        env = dict(os.environ)
        env["HISIM_LOCAL_LPG_CALC_INDEX"] = str(calc_index)
        rel = setup_path.relative_to(REPO_ROOT).as_posix()
        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.run(
                [python, str(CONVERTER), rel],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                check=False,
            )
    finally:
        index_pool.put(calc_index)

    scenario_path = setup_path.with_suffix(".scenario.json")
    if not keep_simulation_json:
        stray = setup_path.with_suffix(".simulation.json")
        try:
            stray.unlink()
        except FileNotFoundError:
            pass

    if proc.returncode != 0:
        return SetupResult(stem, False, proc.returncode, log_path, "converter exited non-zero")
    if not scenario_path.exists():
        return SetupResult(stem, False, proc.returncode, log_path, "no scenario.json produced")
    return SetupResult(stem, True, proc.returncode, log_path)


def main(argv: Optional[list[str]] = None) -> int:
    """Parse arguments, regenerate the selected setups, print a summary."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-j", "--jobs", type=int, default=1, help="Number of setups to regenerate in parallel (default 1).")
    parser.add_argument("--only", nargs="+", metavar="SETUP", help="Regenerate only these setup stems.")
    parser.add_argument("--exclude", nargs="+", metavar="SETUP", help="Skip these setup stems (e.g. ones needing uninstalled optional deps).")
    parser.add_argument("--all-py", action="store_true", help="Regenerate every *.py, not just those with an existing .scenario.json.")
    parser.add_argument("--python", default=sys.executable, help="Interpreter to run the converter with (default: this interpreter).")
    parser.add_argument("--log-dir", type=Path, default=REPO_ROOT / "results" / "regenerate_scenario_jsons", help="Where to write per-setup converter logs.")
    parser.add_argument("--keep-simulation-json", action="store_true", help="Keep the per-setup <setup>.simulation.json the converter emits.")
    parser.add_argument("--dry-run", action="store_true", help="List the setups that would be regenerated and exit.")
    args = parser.parse_args(argv)

    if args.jobs < 1:
        parser.error("--jobs must be >= 1")

    setups = discover_setups(args.only, args.all_py, args.exclude)
    if not setups:
        print("No setups matched.")
        return 1

    print(f"Setups to regenerate ({len(setups)}):")
    for p in setups:
        print(f"  - {p.stem}")
    if args.dry_run:
        return 0

    args.log_dir.mkdir(parents=True, exist_ok=True)
    jobs = min(args.jobs, len(setups))
    print(f"\nUsing interpreter: {args.python}")
    print(f"Parallel jobs: {jobs}   Logs: {args.log_dir}\n")

    # Pool of distinct local-LPG calc indices, one per concurrent worker slot.
    index_pool: "queue.Queue[int]" = queue.Queue()
    for i in range(1, jobs + 1):
        index_pool.put(i)

    results: list[SetupResult] = []
    print_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(regenerate_one, p, args.python, index_pool, args.log_dir, args.keep_simulation_json): p
            for p in setups
        }
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            done += 1
            status = "OK  " if res.ok else "FAIL"
            with print_lock:
                extra = "" if res.ok else f"  ({res.message}; see {res.log_path})"
                print(f"[{done}/{len(setups)}] {status} {res.stem}{extra}")

    failed = [r for r in results if not r.ok]
    print("\n==================================================")
    print(f"DONE: {len(results) - len(failed)} OK, {len(failed)} FAILED")
    for r in failed:
        print(f"  FAILED {r.stem} (rc={r.returncode}): {r.message} -> {r.log_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
