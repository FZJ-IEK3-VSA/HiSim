# ETHOS.HiSim Golden-Reference System — Implementation Spec

Status: **draft for review** · Owner: n.pflugradt

## 1. Goal

A regression gate that runs a curated set of system setups, captures their KPIs
as committed golden references, and fails CI when a code change moves any KPI
beyond tolerance — so refactors can be proven output-preserving. Parallelized
across setups in GitHub Actions.

## 2. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Setup selection | **Manually curated** in `golden_config.json` | You decide exactly which setups the gate protects; validator only vets them |
| Comparison unit | **KPIs only** (`all_kpis.json`) | Robust to plot/PDF/log non-determinism; small; diffable |
| Golden storage | **Committed in repo** (`golden_references/`, repo root) | Versioned with code, PR-diffable, no expiry, no artifact glue |
| Setup profiles | **Offline** (`USE_PREDEFINED_PROFILE`) | No UTSP dependency in CI; deterministic |
| Parallelism | **Data-driven GH Actions matrix**, one job per setup | Wall-time ≈ slowest setup, not sum |
| Blessing | **`workflow_dispatch` fan-out → single PR** | Reference env == check env; auditable |

## 3. Facts established by spikes (grounding)

- Golden file = `<result_dir>/all_kpis.json`, written only when **both**
  `COMPUTE_KPIS` **and** `WRITE_KPIS_TO_JSON` are set
  (`postprocessing_main.py:325`). 72 KPI leaves for `basic_household`,
  structured `{building: {category: {kpi: {value,…}}}}`, ~27 KB.
- `basic_household` runs **fully offline** and is **byte-identical run-to-run**
  (53/53 CSVs + `all_kpis.json`). Same-machine determinism is perfect.
- **Not all setups are golden-able**: KPI computation raises
  `NotImplementedError` for components lacking `get_component_kpi_entries`
  (hit on the electrolyzer setup); `simple_system_setup_*` use unseeded RNG
  (`random.Random()`); the building_sizer households need live UTSP.
- Existing CI (`.github/workflows/tests.yml`) uses container
  `ghcr.io/fzj-iek3-vsa/hisim-test:py311` + `uv pip install --system -e .`,
  matrix by marker, `fail-fast: false`.

## 4. Current code state to reconcile

Three inconsistent things exist and must be unified:

- **`runner.py` + `golden_update.py`** — config-driven runner that hashes
  *every artifact* (keep the runner machinery, drop hashing-as-gate).
- **`golden_check.py`** — a `[FILL]` KPI-dict stub, `run_setup` unwired.
- **`golden_references/README.md`** — documents a semantic per-artifact checker
  plus `compare.py` / `golden_check.sh` that **do not exist**.

## 5. Target architecture & file layout

```
golden_references/          # committed goldens (repo root — easy to find)
  <setup_id>__<param_id>.json
  manifest.json             # env metadata (informational, not a compare key)
  README.md                 # REWRITE to match reality
scripts/
  golden_config.json        # REWRITE: offline KPI-complete setups + correct options
  runner.py                 # KEEP config/param/run machinery; REWIRE run_one → read all_kpis.json
  golden_kpis.py            # NEW: flatten all_kpis.json → {dotted.key: value}; compare with tolerance
  golden_update.py          # REWRITE: write per-pair committed KPI JSON + manifest
  golden_check.py           # REWRITE: config-driven, tolerance compare, --setup filter, report, exit codes
  golden_matrix.py          # NEW: emit GH Actions matrix JSON (per-horizon) from golden_config.json
  golden_validate.py        # NEW: vet the manually-listed setups (offline/KPI/deterministic)
  ci_all_green.sh           # NEW: gh-api helper — did quality+tests+golden-check all pass for a SHA?
.github/workflows/
  golden-check.yml          # NEW: Tier 1 — week pairs, every PR + push
  golden-year.yml           # NEW: Tier 2 — full-year pairs, PR→main only, gated on all CI green
  golden-update.yml         # NEW: workflow_dispatch bless → PR (both horizons)
tests/
  test_runner.py            # UPDATE
  test_golden_update.py     # UPDATE
  test_golden_check.py      # UPDATE
  test_golden_kpis.py       # NEW
```

## 6. Component-by-component work

### 6.1 `golden_config.json` — the manually curated setup list
- **This file is the single, hand-authored source of truth for which setups are
  in the golden gate.** You add/remove entries in the `setups` array yourself;
  nothing auto-discovers or auto-populates it. Everything downstream (runner,
  update, check, CI matrix) is driven off this list, so curating the gate never
  requires touching Python or the workflows.
- Each setup entry: `{ "id": "<stable_id>", "path": "system_setups/<file>.py" }`.
  Pick whichever setups you want to protect; the only hard requirements are that
  each is **offline-runnable** and **KPI-complete** (see §9 for the validator
  that checks this for you).
- Every parameter set: `post_processing_options: ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]`
  — nothing else (no plots/CSV/PDF).
- Keep per-parameter-set `nondeterministic: false`; add optional `weight`/`slow`
  hint for shard balancing.
- **Two parameter sets per setup** (decision §14.5): `one_week_60s`
  (`one_week_only`) and `full_year_60s` (`full_year`), both 2021 @ 60s. The
  full-year run is deliberately included — many bugs only surface over long
  horizons. Current list = 11 building_sizer setups × 2 = **22 pairs**.

### 6.2 `runner.py`
- **Keep**: `load_config`, dataclasses, `build_simulation_parameters`,
  `resolve_setup_path`, `run_all`, `_git_commit`, manifest write/load.
- **Change `run_one`**: after `hisim_main.main(...)`, read
  `<result_dir>/all_kpis.json`; if absent → error (misconfigured options);
  flatten via `golden_kpis.flatten`; return KPIs on `RunResult`.
- **Replace `ArtifactEntry` inventory** with a `kpis: dict[str, float]` field on
  `RunResult` (drop `classify_artifact` / `inventory_directory` /
  `compute_sha256` from the gate path).
- Add a `filter_setups(config, setup_id)` helper for the `--setup` CI slice.

### 6.3 `golden_kpis.py` (new, pure/tested)
- `flatten(all_kpis_dict) -> dict[str, float]` — walk
  `{building:{category:{kpi:{"value":…}}}}` to dotted keys; treat `{"value": x}`
  as leaf; skip/stringify non-numeric leaves.
- `compare(name, got, ref, rel_tol, abs_tol) -> list[str]` — reuse the clean
  logic already in `golden_check.py` (missing / changed / new), `math.isclose`.
- **Compare all leaves** in `all_kpis.json` (already curated, meaningful KPIs;
  spike proved every leaf stable) — no hand-picking.

### 6.4 `golden_update.py`
- Run all (or `--setup`/`--param`-filtered) pairs; write each pair's KPIs to
  `golden_references/<setup>__<param>.json` (sorted keys, indented).
- Write `manifest.json` (commit, python, platform, config hash, timestamp) as
  informational sidecar.
- Print a summary; this is the deliberate "bless" button, **invoked only by the
  `golden-update.yml` CI job** (§14.4) so the reference environment always matches
  the check environment. Runnable locally for inspection/debugging, but locally
  produced goldens are not the canonical committed ones.

### 6.5 `golden_check.py`
- Load config + committed goldens; **hard-fail** if a golden file is missing for
  a configured pair.
- `--setup <id>` / `--param <id>` filters (CI slice = one pair); default = all.
- Re-run each pair → flatten KPIs → `compare` with tolerance → collect deviations.
- Write `results/golden-ref-check/report.txt` + `report.json`; exit `0`/`1`.
- A pair whose run raises (e.g. KPI `NotImplementedError`) = failure with traceback.
- Honor `nondeterministic: true` → compared but advisory (mismatch warns, doesn't fail).

### 6.6 `golden_matrix.py` (new)
- Print `{"include": [{"setup": …, "param": …}, …]}` (one entry per pair) from
  `golden_config.json` for `fromJSON` in CI.
- `--horizon week|year` filters to one tier's parameter sets (by the factory:
  `one_week_only` / `full_year`); default = all pairs.
- Stdlib-only (no `hisim`/`runner` import) so it can run in the lightweight
  `discover` job before dependencies are installed.
- *(Future)* runtime-balanced sharding is **not** implemented in v1 — the per-pair
  matrix is the shipped design; add a `--shard` mode only if per-pair spin-up
  overhead becomes a problem.

## 7. Determinism & tolerance policy

- Same-machine is byte-exact (proven); drift only appears cross-platform (last-ULP).
- **Compare with `rel_tol = 1e-9`, `abs_tol = 0.0`** (confirmed, §14.2) — absorbs
  sub-ULP platform noise, catches real changes. (Replaces the unjustified `1e-6`
  placeholder.)
- **Eliminate cross-platform drift at the source**: goldens are **only** generated
  **in the CI container** via `golden-update.yml` (CI-only blessing, §14.4), so
  reference env == check env. `golden_update.py` refuses to be the source of a
  committed golden from a local machine — the README documents the
  `workflow_dispatch` path as the sole blessing mechanism.

## 8. Parallelization & trigger tiers

**Unit: per-pair.** Each leg is one `(setup, param)` pair; `fail-fast: false`;
matrix emitted from `golden_config.json`. Per-pair keeps a setup's heavy year run
off the critical path of its cheap week run, and the matrix emitter filters by
horizon so each tier only fans out its own pairs (`golden_matrix.py --horizon
week|year`). `golden_check.py` / `golden_update.py` take `--setup` + `--param`.

**Two trigger tiers** (decision §14.3):

- **Tier 1 — week pairs (`golden-check.yml`)**: runs on **every PR + push to main**,
  fast feedback (11 one-week pairs in parallel). Same shape as the tests matrix:
  `discover → golden (matrix) → summary (required check)`.
- **Tier 2 — full-year pairs (`golden-year.yml`)**: runs **only on PRs to main, and
  only after all other CI has already gone green** — no point burning ~11×
  tens-of-minutes of compute if mypy/pylint/prospector/pytest/basic-execution or
  the week golden failed.

**Gating mechanism for Tier 2 (event-driven, no idle runners):** trigger
`golden-year.yml` on `workflow_run` completion of the prerequisite workflows
(`quality`, `tests`, `golden-check`). It fires once per prerequisite completion;
each firing first checks — via `gh api` for the triggering `head_sha` — that **all**
prerequisites concluded `success` for a **pull_request targeting main**. Only the
last-completing one sees everything green and proceeds to the year matrix; earlier
firings bail cheaply. This wastes no runner idling in a poll loop, and the result
is surfaced back onto the PR as a commit status via the Checks API.

> Nuances of `workflow_run`: the workflow must be on `main` to take effect; it runs
> in the base-repo context (safe for forks); PR association is resolved from
> `head_sha`. Alternative if you'd rather avoid the cross-check glue: convert
> `quality.yml`/`tests.yml` to reusable workflows (`workflow_call`) and orchestrate
> everything (quality → tests → golden-week → golden-year) with native `needs` in
> one workflow — cleaner gating, but a larger refactor of existing CI.

**Supporting evidence these run offline:** `quality.yml`'s `basic-execution` job
already runs `household_heatpump_building_sizer.py` in the CI container **without
UTSP secrets**, so at least that building_sizer setup runs offline in CI today
(the validator, §9, confirms the rest).

**Fallback (future, not in v1)**: if per-pair spin-up overhead becomes a concern,
shard into N runtime-balanced buckets via `weight`/`slow` hints. Not implemented
yet — the per-pair matrix is the shipped design.

### Sketch — Tier 1 `golden-check.yml` (week, every PR)

```yaml
name: golden-check
on:
  pull_request: { branches: [main] }
  push: { branches: [main] }
jobs:
  discover:
    runs-on: ubuntu-latest
    outputs: { matrix: ${{ steps.gen.outputs.matrix }} }
    steps:
      - uses: actions/checkout@v6
      - id: gen
        run: echo "matrix=$(python scripts/golden_matrix.py --horizon week)" >> "$GITHUB_OUTPUT"
  golden:
    needs: discover
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/fzj-iek3-vsa/hisim-test:py311
      credentials: { username: ${{ github.actor }}, password: ${{ secrets.GITHUB_TOKEN }} }
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.discover.outputs.matrix) }}   # {"include":[{setup,param},...]}
    steps:
      - uses: actions/checkout@v6
      - run: git config --global --add safe.directory "$GITHUB_WORKSPACE"
      - run: uv pip install --system -e .
      - run: python scripts/golden_check.py --setup "${{ matrix.setup }}" --param "${{ matrix.param }}"
      - if: failure()
        uses: actions/upload-artifact@v6
        with: { name: golden-report-${{ matrix.setup }}-${{ matrix.param }}, path: results/golden-ref-check/report.* }
  summary:
    needs: golden
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: '[ "${{ needs.golden.result }}" = "success" ] || exit 1'
```

### Sketch — Tier 2 `golden-year.yml` (full year, gated on all CI green)

```yaml
name: golden-year
on:
  workflow_run:
    workflows: ["quality", "tests", "golden-check"]
    types: [completed]
jobs:
  gate:
    # Proceed only for a PR→main whose every prerequisite workflow is green on this SHA.
    if: >
      github.event.workflow_run.event == 'pull_request' &&
      github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-latest
    outputs: { go: ${{ steps.check.outputs.go }}, matrix: ${{ steps.gen.outputs.matrix }} }
    steps:
      - uses: actions/checkout@v6
      - id: check
        env: { GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}, SHA: ${{ github.event.workflow_run.head_sha }} }
        run: |
          # all of quality, tests, golden-check must have a successful run for $SHA
          # (gh api run list --commit "$SHA"); set go=true only if so.
          echo "go=$(scripts/ci_all_green.sh "$SHA")" >> "$GITHUB_OUTPUT"
      - id: gen
        if: steps.check.outputs.go == 'true'
        run: echo "matrix=$(python scripts/golden_matrix.py --horizon year)" >> "$GITHUB_OUTPUT"
  golden-year:
    needs: gate
    if: needs.gate.outputs.go == 'true'
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/fzj-iek3-vsa/hisim-test:py311
      credentials: { username: ${{ github.actor }}, password: ${{ secrets.GITHUB_TOKEN }} }
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.gate.outputs.matrix) }}
    steps:
      - uses: actions/checkout@v6
        with: { ref: ${{ github.event.workflow_run.head_sha }} }
      - run: git config --global --add safe.directory "$GITHUB_WORKSPACE"
      - run: uv pip install --system -e .
      - run: python scripts/golden_check.py --setup "${{ matrix.setup }}" --param "${{ matrix.param }}"
      # + report upload + a commit-status step to surface the result on the PR
```

## 9. Setup curation — you choose, a validator checks

**You manually pick the setups** and list them in `golden_config.json` (§6.1).
There is no auto-discovery; the gate contains exactly the setups you decided to
protect. So far only `basic_household` is *proven* offline + KPI-complete +
deterministic — the rest of your chosen list needs to be checked, and that's
what the validator is for (not to pick setups, only to vet the ones you picked).

`scripts/golden_validate.py` (new, opt-in) — for each setup **currently listed in
`golden_config.json`**:

1. Runs it (short horizon) with `[COMPUTE_KPIS, WRITE_KPIS_TO_JSON]`, **UTSP unset**.
2. Records: runs offline? / KPI computation succeeds? / `all_kpis.json` produced?
   / byte-identical on a second run?
3. Prints a PASS/FAIL table so you can see which of *your* setups are ready,
   which need a config tweak (e.g. switch the LPG connector to
   `USE_PREDEFINED_PROFILE`), and which can't be used (genuine non-determinism or
   missing KPI implementations).

Workflow: edit the list → run the validator → fix or drop the ones it flags →
bless. Adding a setup later is the same loop: append to the list, validate, bless.

> Optional convenience: the validator can also be pointed at *all*
> `system_setups/*.py` (`--scan-all`) to suggest candidates, but the authoritative
> list is always the one you hand-maintain in `golden_config.json`.

## 10. Blessing workflow (`golden-update.yml`)

- `workflow_dispatch` (manual). Fans out over **all pairs, both horizons** (full
  matrix, no `--horizon` filter — a bless must regenerate week *and* year); each
  leg runs `golden_update.py --setup <id> --param <id>`, uploads its golden JSON as
  an artifact.
- Final `collect` job downloads all, writes them into `golden_references/`, and
  opens a PR ("bless golden: <reason>"). Reviewer sees exact KPI deltas; merge =
  bless. Parallel regen, single reviewable PR.
- The full-year legs make blessing slow, but it is manual/occasional — acceptable.

## 11. Edge cases / failure handling

- Missing golden for a configured pair → hard fail (forces bless).
- `all_kpis.json` absent after run → config error (options wrong) → fail loudly.
- Setup run raises → failure with traceback in report.
- New / disappeared KPI → reported as new/missing (regenerate if intended).
- `nondeterministic` pairs → advisory only.

## 12. Testing strategy for the scripts

- Keep the pure-unit style (`pytest.mark.base`, no HiSim run): config schema,
  param build, flatten, compare, manifest round-trip, matrix emission (per-pair),
  `--setup`/`--param` filters, update/check `main()` with injected fake `run_fn`.
- One opt-in integration test (marked, not in `base`) that runs `basic_household`
  end-to-end and asserts `all_kpis.json` is produced — guards the option combo.

## 13. Sequencing

1. **Phase 0** — you list your chosen setups in `golden_config.json`; write
   `golden_validate.py` and run it to confirm each listed setup is offline +
   KPI-complete + deterministic; fix/drop any it flags.
2. **Phase 1** — `golden_kpis.py` + rewire `runner.run_one`; update
   `test_runner.py`, add `test_golden_kpis.py`.
3. **Phase 2** — rewrite `golden_update.py` / `golden_check.py` +
   `--setup`/`--param` + reports; update their tests.
4. **Phase 3** — `golden_matrix.py` (+ `--horizon`) + `ci_all_green.sh` +
   `golden-check.yml` (Tier 1, week) + `golden-year.yml` (Tier 2, gated) +
   `golden-update.yml`.
5. **Phase 4** — bless initial goldens (via `golden-update.yml`), commit; rewrite
   README; mark `summary` job required.

## 14. Decisions (resolved) & remaining questions

1. **Setup list** — ✅ RESOLVED: the 11 `building_sizer` setups, now in
   `golden_config.json`. Still need `golden_validate.py` to confirm they run
   offline (several reference UTSP and may need `USE_PREDEFINED_PROFILE`).
2. **Tolerance** — ✅ RESOLVED: `rel_tol = 1e-9`, `abs_tol = 0.0`.
3. **Parallel unit & triggers** — ✅ RESOLVED: per-pair (22 jobs);
   `golden_check`/`golden_update`/`golden_matrix` take `--setup` + `--param`.
   **Two tiers** (§8): week pairs on every PR + push; full-year pairs on **PRs to
   main only, gated so they run only after quality + tests + golden-check are all
   green** (event-driven via `workflow_run` + a `head_sha` all-green check) — no
   wasted compute when cheaper checks fail.
4. **Blessing** — ✅ RESOLVED: CI-only via `workflow_dispatch`; no local golden commits.
5. **Param sets** — ✅ RESOLVED: two per setup — `one_week_60s` (`one_week_only`)
   and `full_year_60s` (`full_year`), both 2021 @ 60s; full year included to catch
   long-run-only bugs.

Remaining to confirm: timestep resolution (60s assumed for both horizons).
