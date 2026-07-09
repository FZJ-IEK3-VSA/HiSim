# How to verify changes to HiSim
Run all three; all must pass before a change is considered done.

1. Lint:   `ruff check hisim/`                          — clean for files you touched.
2. Tests:  `pytest -m "base or buildingtest"`           — must pass; add tests for new public functions.
3. Golden: `python scripts/golden_check.py`             — simulation outputs must match within tolerance.

Do NOT regenerate golden references. If a change is expected to alter outputs, say so
explicitly and stop — a human regenerates references via scripts/golden_update.py.