#!/usr/bin/env python3
"""Regenerate golden references. DELIBERATE, HUMAN-ONLY — never run by the agent.
Review the resulting diff in git before committing."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

try:
    from golden_check import REF_DIR, SETUPS, run_setup   # type: ignore[import-not-found]  # run as a script from scripts/
except ModuleNotFoundError:  # imported as scripts.golden_update (e.g. by the test suite)
    from scripts.golden_check import REF_DIR, SETUPS, run_setup


def main(
    run_setup_fn: Callable[[str], dict[str, float]] = run_setup,
    ref_dir: Path = REF_DIR,
    setups: list[str] = SETUPS,
) -> int:
    """Regenerate one ``<name>.json`` reference per setup under ``ref_dir``.

    The three collaborators default to the module-level values imported from
    ``golden_check`` so that running this script unchanged reproduces the previous
    behaviour exactly. Tests inject a fake ``run_setup_fn``, a temporary
    ``ref_dir`` and a small ``setups`` list to exercise the orchestration — the
    filename pattern, JSON formatting and return code — without running real
    HiSim simulations or writing to the committed ``scripts/golden_refs/``.
    """
    ref_dir.mkdir(parents=True, exist_ok=True)
    for name in setups:
        setup_results = run_setup_fn(name)
        (ref_dir / f"{name}.json").write_text(json.dumps(setup_results, indent=2, sort_keys=True))
        print(f"wrote {name}.json: {setup_results}")
    print("Done. Review `git diff scripts/golden_refs/` before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
