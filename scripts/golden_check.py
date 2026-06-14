#!/usr/bin/env python3
"""Golden-output regression gate for HiSim.

Runs each registered system setup, extracts KPIs, compares to stored references in
scripts/golden_refs/. Exits non-zero on any mismatch or missing reference.
Read-only: never writes references (that is golden_update.py's job).
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

REF_DIR = Path(__file__).parent / "golden_refs"
REL_TOL = 1e-6          # [FILL: justify per step 3]
ABS_TOL = 0.0

# ---------------------------------------------------------------------------
# [FILL] Replace this with a real HiSim run. Given a setup name, run the
# simulation and return a flat dict of {kpi_name: float}. Keep it to stable,
# meaningful KPIs (step 2), not raw timeseries.
def run_setup(name: str) -> dict[str, float]:
    if name == "_dummy":                      # self-test so this script runs before HiSim wiring
        return {"answer": 42.0, "ratio": 1.0 / 3.0}
    raise NotImplementedError(f"wire up HiSim run for setup {name!r}")
# ---------------------------------------------------------------------------

# [FILL] the real setups once run_setup is wired (step 1):
SETUPS = ["_dummy"]   # e.g. ["basic_household", "household_with_heatpump", ...]


def compare(name: str, got: dict[str, float], ref: dict[str, float]) -> list[str]:
    errs: list[str] = []
    for k, ref_v in ref.items():
        if k not in got:
            errs.append(f"{name}: missing KPI '{k}' in current run")
            continue
        if not math.isclose(got[k], ref_v, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            errs.append(f"{name}: KPI '{k}' changed: ref={ref_v!r} got={got[k]!r}")
    for k in got.keys() - ref.keys():
        errs.append(f"{name}: new KPI '{k}' not in reference (regenerate if intended)")
    return errs


def main() -> int:
    all_errs: list[str] = []
    for name in SETUPS:
        ref_path = REF_DIR / f"{name}.json"
        if not ref_path.exists():
            all_errs.append(f"{name}: no reference at {ref_path} (run golden_update.py)")
            continue
        ref = json.loads(ref_path.read_text())
        try:
            got = run_setup(name)
        except Exception as e:                 # a crashing setup is a failure, not an error to swallow
            all_errs.append(f"{name}: run failed: {e!r}")
            continue
        all_errs.extend(compare(name, got, ref))

    if all_errs:
        print("GOLDEN CHECK FAILED:")
        for e in all_errs:
            print("  -", e)
        return 1
    print(f"GOLDEN CHECK OK ({len(SETUPS)} setup(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())