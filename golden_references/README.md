# Golden KPI References

This directory holds the **committed golden KPI references** for HiSim — one JSON
file per `(system setup, parameter set)` pair. They are the known-good baseline the
golden-reference regression gate compares against, so an unintended change to
simulation output (e.g. after a numerical refactor) fails CI with the exact KPIs
that moved.

The scripts that drive this live in [`../scripts/`](../scripts); the full design is
in [`../golden_ref_spec.md`](../golden_ref_spec.md).

## What is compared

Each run writes `all_kpis.json` (HiSim's KPI collection, produced when a parameter
set enables both `COMPUTE_KPIS` and `WRITE_KPIS_TO_JSON`). It is flattened to a
`{dotted.key: value}` map and stored here as `<setup_id>__<param_id>.json`.
Comparison is numeric with a tight tolerance (`rel_tol = 1e-9`, `abs_tol = 0`);
non-numeric KPIs are compared exactly. **Only KPIs are compared** — never plots,
PDFs, logs, or raw CSVs (those are non-deterministic and/or platform-dependent).

`manifest.json` is an informational sidecar (git commit, Python, platform, config
hash, list of golden files). The checker does **not** read it.

## Configuration

[`../scripts/golden_config.json`](../scripts/golden_config.json) is the single,
hand-maintained source of truth for **which** setups and parameter sets are in the
gate. Edit it to add or remove a setup; everything downstream (runner, check,
update, CI matrix) follows. Every parameter set must enable
`["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]`, and every setup must be **offline-runnable**
and **KPI-complete** — validate with:

```bash
python scripts/golden_validate.py            # vet the setups listed in the config
python scripts/golden_validate.py --scan-all # probe every system_setups/*.py
```

## Checking (the gate)

```bash
python scripts/golden_check.py                              # all pairs
python scripts/golden_check.py --setup <id> --param <id>    # one pair (CI slice)
```

Re-runs the pairs, compares KPIs to the goldens here, writes
`results/golden-ref-check/report.{txt,json}`, and exits non-zero on any deviation,
missing golden, or run failure. Missing goldens fail **before** running, so they
never waste compute.

In CI this runs as two tiers (see `.github/workflows/`):

- **`golden-check.yml`** — one-week pairs, on every PR and push to `main`.
- **`golden-year.yml`** — full-year pairs, on PRs to `main` **only after** `quality`,
  `tests`, and `golden-check` have all gone green for the commit (no wasted
  full-year compute when a cheaper check already failed).

## Blessing (updating the goldens)

Do **not** hand-edit these files. Regenerate them via the CI workflow so the
reference environment matches the check environment:

> Actions → **golden-update** → *Run workflow* → give a reason.

It regenerates every pair (week and year) in the CI container and opens a PR with
the updated `golden_references/`. Review the per-KPI diff, then merge — that merge
is the bless. (`scripts/golden_update.py` can be run locally for inspection, but
locally produced goldens are not the canonical committed ones.)
