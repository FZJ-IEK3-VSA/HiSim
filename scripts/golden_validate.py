#!/usr/bin/env python3
"""Vet the setups listed in ``golden_config.json`` for golden-reference use.

For each setup it runs a short simulation (``one_day_only`` by default) **twice**
with ``COMPUTE_KPIS`` + ``WRITE_KPIS_TO_JSON`` and **UTSP unset**, then reports
whether the setup:

  * runs offline (no UTSP credentials / network),
  * produces ``all_kpis.json`` (i.e. KPI computation is implemented for all of
    its components), and
  * is deterministic (identical flattened KPIs across the two runs).

This does **not** pick setups — the authoritative list is the one you maintain in
``golden_config.json`` (``--scan-all`` additionally probes every
``system_setups/*.py`` to *suggest* candidates). Prints a PASS/FAIL table and
exits non-zero if any checked setup fails, so it doubles as a pre-bless gate.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Make the repo root importable whether invoked as ``python scripts/golden_validate.py``
# or imported as ``scripts.golden_validate`` (so ``hisim`` and siblings both resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from runner import ParameterSetConfig, SetupConfig, load_config, run_one  # type: ignore[import-not-found]
except ModuleNotFoundError:  # ... or imported as scripts.golden_validate
    from scripts.runner import ParameterSetConfig, SetupConfig, load_config, run_one

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"


def _probe_options() -> list[str]:
    return ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]


def validate_setup(
    setup: SetupConfig,
    repo_root: Path,
    year: int,
    seconds_per_timestep: int,
    factory: str,
    scratch: Path,
) -> tuple[bool, str]:
    """Run ``setup`` twice and return ``(ok, reason)``."""
    param = ParameterSetConfig(
        id="validate",
        factory=factory,
        year=year,
        seconds_per_timestep=seconds_per_timestep,
        post_processing_options=_probe_options(),
    )
    dir_a = scratch / setup.id / "run_a"
    dir_b = scratch / setup.id / "run_b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)

    first = run_one(setup, param, str(dir_a), repo_root)
    if first.error is not None:
        # A UTSP requirement or a missing KPI implementation surfaces here.
        short = first.error.strip().splitlines()[-1]
        return False, f"run failed (offline?/KPIs?): {short}"
    if not first.kpis:
        return False, "no KPIs produced (all_kpis.json empty/missing)"

    second = run_one(setup, param, str(dir_b), repo_root)
    if second.error is not None:
        return False, f"second run failed: {second.error.strip().splitlines()[-1]}"

    if first.kpis != second.kpis:
        diffs = sorted(k for k in set(first.kpis) | set(second.kpis) if first.kpis.get(k) != second.kpis.get(k))
        return False, f"non-deterministic: {len(diffs)} KPI(s) differ, e.g. {diffs[:3]}"

    return True, f"offline, KPI-complete, deterministic ({len(first.kpis)} KPIs)"


def _scan_all_setups(repo_root: Path) -> list[SetupConfig]:
    setups = []
    for path in sorted((repo_root / "system_setups").glob("*.py")):
        if path.name.startswith("_"):
            continue
        setups.append(SetupConfig(id=path.stem, path=f"system_setups/{path.name}"))
    return setups


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Vet setups for golden-reference use.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--factory", default="one_day_only", help="Short horizon factory for probing.")
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--setup", dest="setup_id", default=None, help="Only validate this setup id.")
    parser.add_argument("--scan-all", action="store_true", help="Probe every system_setups/*.py.")
    args = parser.parse_args(argv)

    # Make the offline requirement explicit: hide any ambient UTSP credentials.
    os.environ.pop("UTSP_URL", None)
    os.environ.pop("UTSP_API_KEY", None)

    if args.scan_all:
        setups = _scan_all_setups(args.repo_root)
    else:
        setups = load_config(args.config).setups
    if args.setup_id is not None:
        setups = [s for s in setups if s.id == args.setup_id]
        if not setups:
            print(f"No setup with id {args.setup_id!r}.")
            return 2

    all_ok = True
    with tempfile.TemporaryDirectory(prefix="golden-validate-") as tmp:
        scratch = Path(tmp)
        print(f"Validating {len(setups)} setup(s) with factory={args.factory} @ {args.seconds}s (UTSP unset)\n")
        for setup in setups:
            ok, reason = validate_setup(
                setup, args.repo_root, args.year, args.seconds, args.factory, scratch
            )
            all_ok = all_ok and ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {setup.id}: {reason}")

    print(f"\n{'ALL PASS' if all_ok else 'SOME FAILED'} ({len(setups)} setup(s))")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
