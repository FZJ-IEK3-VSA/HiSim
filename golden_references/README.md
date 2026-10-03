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
set enables both `COMPUTE_KPIS` and `WRITE_KPIS_TO_JSON`). Every KPI in it is
resolved by its address (`KpiFinder`, `hisim/postprocessing/kpi_computation/kpi_address.py`)
and stored here, in `<setup_id>__<param_id>.json` (keys sorted, two-space indent), as one
**leaf** keyed by its dotted address (`KpiAddress.dotted`):

```json
{
  "BUI1.Building.Conditioned floor area (Building)": {
    "value": 121.2,
    "unit": "m2",
    "building": "BUI1",
    "tag": "Building",
    "name": "Conditioned floor area",
    "source": {"import": null, "instance": null, "member": "Building", "assembly": null, "name": "Building"}
  },
  "BUI1.General.Self-sufficiency rate of electricity": {
    "value": 41.2, "unit": "%", "building": "BUI1", "tag": "General",
    "name": "Self-sufficiency rate of electricity", "source": null
  }
}
```

- `value` is the entry's value (a number, stored as a float; a string; or `null`), `unit` the
  entry's unit verbatim.
- `source` holds the five identity fields of the entry's `KpiSource`; its `display_name` and
  `label` are presentation and stay out. It is `null` for a derived KPI.
- **Identity rule.** The key is the dotted address the leaf's own fields produce, and every
  reader checks it: a leaf whose key disagrees with its fields, that lacks or adds a field, or
  a golden in the old flat form (`{"BUI1.Building.Conditioned floor area": 121.2}`, a bare value
  per key) is refused by `golden_check.py` (before anything runs) and by `golden_update.py`, with
  the instruction to re-bless through the `golden-update` workflow with `force_rewrite`, which
  writes the fresh leaves without reading the old file.

Values are compared numerically with a tight tolerance (`rel_tol = 1e-9`, `abs_tol = 0`);
non-numeric values exactly. **Unit rule:** the unit is compared exactly, and a changed unit is a
failure of its own kind — listed as `[unit]` in `report.txt`, under `unit_changes` in
`report.json`, and counted separately in the summary — whatever the value did. The address
fields under one key are compared exactly too. **Only KPIs are compared** — never plots,
PDFs, logs, or raw CSVs (those are non-deterministic and/or platform-dependent).

`manifest.json` is an informational sidecar (git commit, Python, platform, config
hash, list of golden files). The checker does **not** read it.

## Configuration

[`../scripts/golden_config.json`](../scripts/golden_config.json) is the single,
hand-maintained source of truth for **which** setups and parameter sets are in the
gate. Edit it to add or remove a setup; everything downstream (runner, check,
update, CI matrix) follows. Every parameter set must enable the cost stages plus the
KPIs — `["COMPUTE_OPEX", "COMPUTE_CAPEX", "COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]`, so
the building-level cost KPIs are pinned — and every setup must be **offline-runnable**
and **KPI-complete** — validate with:

```bash
python scripts/golden_validate.py            # vet the setups listed in the config
python scripts/golden_validate.py --scan-all # probe every system_setups/*.py
```

## Checking (the gate)

```bash
python scripts/golden_check.py                              # all pairs
python scripts/golden_check.py --setup <id> --param <id>    # one pair
python scripts/golden_check.py --pairs <id>:<id> ... --jobs 4 # a CI shard, four pairs at once
python scripts/golden_check.py --mode yaml ...               # the recorded YAML twins
python scripts/golden_check.py --mode both ...               # both, side by side, one report each
```

Re-runs the pairs, compares KPIs to the goldens here, writes
`results/golden-ref-check/report.{txt,json}` (`golden-ref-check-yaml/` in YAML mode), and exits non-zero on any deviation,
unit change, missing or unusable golden, or run failure. Missing and unusable goldens fail
**before** running, so they never waste compute.

In CI this runs as two tiers (see `.github/workflows/`):

- **`golden-check.yml`** — one-week pairs, on every PR and push to `main`, each pair's Python
  setup and its recorded YAML twin side by side (`golden_check.py --mode both`, one report per
  mode), in four shards (`scripts/golden_matrix.py --shards 4 --with-yaml`) that run four
  simulations at once each. A diverging YAML twin fails it, and so also holds back `golden-year`. The shards are
  balanced by each pair's measured duration, `"seconds"` in `scripts/golden_config.json`;
  `report.json` records a fresh `duration_s` per pair when they need refreshing.
- **`golden-year.yml`** — full-year pairs, for PRs to `main` **only after** `quality`,
  `tests`, and `golden-check` have all gone green for the commit (no wasted
  full-year compute when a cheaper check already failed). It is triggered by those
  workflows completing (`workflow_run`), not by the pull request itself, so that
  waiting for them costs no runner; its result reaches the pull request as a
  `golden-year` commit status rather than as an entry in the checks list. Four shards of
  two pairs each, since a full-year pair needs about 6 GiB.

## Blessing (updating the goldens)

Do **not** hand-edit these files. Regenerate them via the CI workflow so the
reference environment matches the check environment:

> Actions → **golden-update** → *Run workflow* → give a reason.

It regenerates every pair (week and year) in the CI container and opens a PR with
the updated `golden_references/`. Review the per-KPI diff, then merge — that merge
is the bless. (`scripts/golden_update.py` can be run locally for inspection, but
locally produced goldens are not the canonical committed ones. A local run is
sticky like the CI one — every leaf the gate would still accept (value within
tolerance, unit and address fields equal) stays exactly as committed, a changed unit
always reaches the diff, and nothing is dropped — so add `--force-rewrite` to see the
fresh leaves verbatim.)

## Seeing how the references moved

The git history of this directory is a record of which KPI moved in which pull
request. To draw it:

```bash
python scripts/golden_history.py                    # every pair, PNG + HTML
python scripts/golden_history.py --pairs household_oil_building_sizer --since 2026-09-01
```

Output lands in `results/golden_history/` (gitignored): one figure per pair in both
formats, an `index.html` linking them, a shared `plotly.min.js` the pages reference
relatively, and one `moves.csv` — `commit, date, pr, pair, kpi, previous, value,
relative_change` for every value that changed beyond the gate's tolerance, which is
the greppable answer to "what moved when". Nothing is committed and no CI job runs it.

A figure is one panel per KPI. The x axis is the commits that changed *this* pair's
file, labelled with the date and the PR number; the y axis is the KPI's change
against its first recorded value, in percent. A KPI whose first value is exactly `0`
has no percentage, so its panel shows the absolute change and is marked `[abs]`.
Non-numeric values (`null`, strings) are skipped and leave a gap. Panels are sorted
with the largest total movement first — the three largest carry their rank — and a
KPI that never moved is drawn in grey, so the eye lands on the movers.

The walker is the one reader of *historical* goldens, so it reads both forms the files have
had. A leaf-form series is identified by its fields — `building`, `tag`, `name` and the
source's `import`, `instance`, `member` — so a later change of the serialized source name
(the key's suffix) continues one series with no table entry. A flat-form series is identified
by its dotted key; it continues into a leaf series when the flat key equals the leaf's dotted
key, or its bare `building.tag.name` (the form the old scheme used while a name was unique). A
flat key that could continue two leaf series is refused by name, as is a file mixing the forms.

Flat-form renames are stitched back into one series through
[`../scripts/golden_kpi_renames.py`](../scripts/golden_kpi_renames.py). To add one,
find the commit that renamed the key (diff the key sets of two neighbouring golden
commits), add the old and new spelling under the pair's stem with that commit and its
PR, and run `pytest tests/test_golden_history.py` — it checks every old name against
the real history and every new name against the files as they stand.

