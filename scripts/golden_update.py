#!/usr/bin/env python3
"""Regenerate golden references. DELIBERATE, HUMAN-ONLY — never run by the agent.
Review the resulting diff in git before committing."""
from __future__ import annotations
import json
import sys
from golden_check import SETUPS, run_setup, REF_DIR   # reuse the same run logic

def main() -> int:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    for name in SETUPS:
        setup_results = run_setup(name)
        (REF_DIR / f"{name}.json").write_text(json.dumps(setup_results, indent=2, sort_keys=True))
        print(f"wrote {name}.json: {setup_results}")
    print("Done. Review `git diff scripts/golden_refs/` before committing.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
    