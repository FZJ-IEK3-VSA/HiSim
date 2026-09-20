# Step 12 — answering the review of PR #779

**Status:** implementation specification, 2026-09-20. **Source:** the aggregated multi-reviewer
report on PR #779 (`/hisimreviews/779.md`, 50 deduplicated findings against head `3cbac31d`),
evaluated finding by finding against the code on 2026-09-20; the owner took the four decisions of
§1 in an interview the same day. Everything lands as new commits on PR #779 (branch
`renovisor-staged-economics`).

Ground rules as in step 10 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, no commits, class-scope
constants, plain complete docstrings, 120 columns; flake8/mypy/pylint-critical/prospector clean on
every file touched; the three CI mypy runs). New tests `@pytest.mark.base` unless they run a
simulation. Keep test runs targeted. **Invent no numbers.**

---

## 1. Owner decisions (2026-09-20)

| Question | Decision |
|---|---|
| How is the existing heat generator sized in the register? | From the **realized heating load**: the building's design heat load the run sizes a new generator from (`BuildingInformation.max_thermal_building_demand_in_watt` computed from the translated `Building` config and the design temperature the weather carries), in kW, reported `approximated` in the mapping report. No request field. |
| A stage directory has `economic_inputs.json` but no `mapping_report.json`? | **Refuse**: `problems.json` naming the directory, exit 2. |
| A measure `cost` block with min > max or a negative price; an envelope placement absent from the asset-class table? | **Refuse**: the band is a request problem (exit 2, `problems.json`); the unknown placement is a translator build failure (exit 3), like any unmapped item. |
| Where do the fixes land? | All on PR #779. |

## 2. Engine arithmetic (`hisim/economics/staged.py`, `staged_document.py`)

### 2.1 A stage's own purchases age from the stage's start, not from year 0 (found in the evaluation, not by a reviewer)

Today stage `k`'s own evaluation buys its new subjects in its year 0, so its `REPLACEMENT` entries
sit at multiples of the service life and its `RESIDUAL_VALUE` entry at the horizon is for an asset
as old as the whole horizon; the splice keeps both at their own year. In the plan the purchase is
at `from_year_k`. Consequences: a battery bought in year 5 is replaced five years too early; a heat
pump bought in year 3 of a 20-year plan gets **no** residual value where `3/20` of its (escalated)
investment is due; and worse, when a later stage exists, the residual of an earlier stage's
purchase is lost altogether, because the stage active at the horizon treats it as a kept asset
("predates year 0, earns nothing", `calculators/investment.py` §3.6 rule 3).

Rule to implement, for every subject stage `k` **charges** (`_charged_subjects` share > 0):

1. `REPLACEMENT` entries of that subject from stage `k`'s own timeline are shifted by
   `from_year_k` and escalated by the same factor as the investment (`escalation_factor(subject,
   from_year_k)`), dropped when they land past the horizon, and — like today's operating rule —
   kept only for plan years in which stage `k` is active (later stages already schedule the
   subject's replacements from their register entry, whose installation year is
   `simulation_year + from_year_k`; do not double-book).
2. `RESIDUAL_VALUE` is **computed by the splice**, not taken from any stage: for each charged
   subject, `last_install` = the plan year of the last `INVESTMENT` or `REPLACEMENT` entry of that
   subject on the spliced plan timeline; `remaining = last_install + life − T`; if positive,
   `residual = (escalated price at last_install) × remaining / life`, booked at `T` as revenue,
   subject and provenance as the investment entry. `life` is the service life of the subject's
   asset class from the cost database (`database.get_device_entry(...).service_life_in_years`,
   the same value `costing.service_life_years` carries); the escalated price is the plan's own
   investment/replacement amount at `last_install` (already escalated). Drop every stage's own
   `RESIDUAL_VALUE` entry for charged subjects; keep the engine's for uncharged ones (they are
   zero by the kept-asset rule anyway, so the simplest correct implementation is: the splice never
   takes a `RESIDUAL_VALUE` entry from a stage and always writes its own).
3. The replacement **reserve** flows of the operating perspective (`replacement_flows_for_reserve`
   → `calculators/reserve.py`) follow the shifted replacement years. If that needs the reserve to
   be rebuilt from the spliced timeline rather than spliced from the stages' reserve entries, do
   that; say so.
4. Extract the shared pieces (`replacement years from install year and life`, `residual from last
   install, life and horizon`) from `calculators/investment.py` into helpers both the engine and
   the splice call, so the two rules cannot drift. Behaviour of the plain evaluator must not
   change (its tests and the worked examples are the guard).

Tests (`tests/economics/test_staged.py`), each with hand-computed expectations from the synthetic
plan's declared inputs, not from the engine: a pump with life `L` bought at `f` in a horizon `T`
with `f + L > T` has residual `I·(1+e)^f·(f+L−T)/L` at `T` and no replacement; a battery with
`L < T − f` is replaced at `f + L` at `I·(1+e)^(f+L)`; a one-stage plan at `f = 0` equals the
plain evaluation entry for entry (invariant 1 still holds); two stages both buying: each subject's
residual is present and equals the hand figure. Also the reviewers' request: one absolute
hand-derived number for a spliced investment (`I·(1+e)^f` discounted at `(1+r)^−f`) pinned to
the cent.

### 2.2 Loan debt service attributed to the wrong loan (finding, valid)

`StagedDocument._borrowing_year` picks the stage **active** in the payment year, so with two
borrowing stages the first loan's later repayments move onto the second loan in the financing
block (plan totals unaffected). Fix: attribute each `LOAN_INTEREST`/`LOAN_PRINCIPAL` entry to the
loan it belongs to. The splice knows this (the entry comes from stage `k`'s timeline); carry the
borrowing stage through — the cleanest way is for the splice to stamp it (a `stage` index on the
plan's entries, if `CashFlowEntry` can carry one without changing the engine; else a side map
`(subject, category, year) → stage` on `StagedResult`). Test: envelope loan at 0 and pump loan at
3, 12-year horizon, 10-year terms → the first loan's schedule runs years 1..10, the second's
4..12.

### 2.3 Discounted cumulative band negated without swapping ends (finding, valid)

`_comparison` writes `min = −low, max = −high`, which inverts when the reference band is wider.
Use `_negated_band` semantics (min = −high, max = −low, best = −best). Test with a wide reference
and a narrow plan band. Also add `min <= best <= max` as a check in `StagedDocument.write` over
every band (cheap, and it would have caught this).

### 2.4 Size-zero predecessor never charged (finding, valid, low)

`if facts.size > before.size > 0.0` never charges a subject that grows from 0. Charge 1.0 when
`before.size <= 0` (a declared-but-absent device is absent). Also guard `_staged_inputs`: a
charged subject with `size <= 0` gets no register entry (it is not installed), rather than an
`ExistingAsset` `ValueError`. Tests for both.

## 3. Translator data (`hisim/renovisor/economics.py`, `request.py`, `hisim/economics/__main__.py`)

### 3.1 Existing PV: watts stored as kilowatts (finding, valid, 1000×)

`DeviceAssets` reads `power_in_watt` and labels it `Units.KILOWATT`. Convert: the `Device` row
gains a `to_size` factor (`1/1000` for the array, `1.0` otherwise) applied in `_device_assets`,
or the size key names the unit and the table converts explicitly. Test: `power_in_watt: 4000` →
register size 4.0 kW.

### 3.2 Existing generator sized from the realized heating load (owner decision)

Replace `_generator_size()`'s nominal 1.0 with the design heat load in kW: instantiate the
building information the `Building` component itself uses (`BuildingInformation` from
`hisim/components/building/`) from the translated `Building` config plus the heating reference
temperature the translated `Weather` carries, read `max_thermal_building_demand_in_watt`, divide
by 1000; append a `defaults`/`approximated` line
`house.heating.nominal_power_in_kw` with the value and the note "sized from the building's design
heat load, as the run sizes a new generator; the request states no boiler rating". Correct the
docstring that claims size never matters (it prices the like-for-like replacement, the sunk cost
and the anyway credit). If the building information cannot be built from the config alone (it
needs the TABULA row and the design temperature — both are in the translated config), say exactly
what is missing rather than keeping 1.0. Test: on the mockup baseline the register's generator
size is the building's heat load in kW (compare to the value the KPI `heating_load_in_watt` of a
run reports) and the mapping report carries the line.

### 3.3 Invalid price band refused (owner decision)

`measures[i].cost` with `min_in_euro_per_m2 > max_in_euro_per_m2` or either negative (or
non-finite) is a request problem: add a semantic check to `hisim/renovisor/request.py` (code e.g.
`measure.cost.band_invalid`, one problem per measure, all reported at once) so the run exits 2
with `problems.json` before anything is translated. `_measure_price` then only sees valid bands.
Tests in `tests/renovisor/test_request.py`.

### 3.4 Unknown placement refused (owner decision)

`EnvelopeAssets.of_placement` raises `TranslatorError` (the exit-3 build failure) for a placement
absent from `BY_PLACEMENT`, naming the placement and the table, like `GeneratorAssets.of`. The
capability probe set already exercises every catalogue placement, so a table gap fails the build,
not a request. Test.

### 3.5 Missing mapping report refused (owner decision)

`StagedCli.read_mapping`: a stage directory that carries `economic_inputs.json` but no
`mapping_report.json` (in the directory or its parent, as today) is a plan problem: `problems.json`
entry naming the directory, exit 2. Tests in `tests/economics/test_staged_cli.py`.

### 3.6 The `missing` reasons for the monthly figures (finding, valid)

`EconomicsDocument.WHERE`: `monthly_net_cost_20y_in_euro` → `plan.totals.equivalent_annual_cost_in_euro`
with the reason saying "divided by twelve"; `monthly_net_cost_10y_in_euro` → no key, reason "the
staged document is evaluated over one horizon; a ten-year figure needs a plan evaluated with
`horizon_years: 10`". Adjust `reason_for`, the capability results section and the tests.

## 4. Minor, all in one commit

- Docstrings: `staged_document._evaluation` (reference rows carry `stage: null`, the annual
  series 0); `report.HiSimCommit` (quote the real Dockerfile line `RUN printf '%s' "$HISIM_COMMIT"
  > hisim/COMMIT`); `staged._escalation_rates` (absent subjects escalate at the general investment
  rate, not zero); `capabilities.ResultsSection` (no `CostSources` any more: `CostSchema` /
  `EconomicsDocument`); `docs/modules/renovisor.rst` (every cost field **the document answers**
  carries a key; the property-value field carries a reason).
- Duplication: the ten placement strings live once (an `Enum` in `hisim/renovisor/vocabulary.py`
  or a `Placements` class in `constants.py`) and `apply.py`, `AnywayShareByPlacement` and
  `EnvelopeAssets` reference it; `DeviceAssets` takes component and field names from
  `translate.Targets` if the import is acyclic (translate imports economics today — if it is not,
  move `Targets` to a module both can import, or leave a pinning test that the two tables agree);
  `_installation_year` / `_year_of` become one reader with a `record` flag; the four inline
  positive-number checks call one helper; `_sum_bands` → `UncertainValue.sum`;
  `StagedResult.stage_of_year` indexes the tuple `_active_by_year` builds (compute it once in
  `evaluate`, store it on the result); mapping-report field names come from constants on
  `MappingReport` that `StagedCli` imports.
- Tests: `test_report.py` git-hash tautology → assert a 7–12 hex-digit short hash when a checkout
  exists and the file/env sources deterministically with monkeypatching; drop the `$schema`
  string check; the `ECONOMICS_VERSION` mirror becomes a fixed literal; `test_costs.py` asserts
  that the pointed-to document key exists in the schema instead of a substring; a pinning test
  that `operational_co2_by_year_in_kg` is identical across every perspective of one
  `lifecycle_costs.json` (verified in code: emissions accumulate from the energy flows before any
  payer scoping — write the test on the synthetic plan's engine output).
- `subsidy_catalog_id`: the catalogue's country plus snapshot date (`IE@2026-09-19`), not the
  bare country.
- SCOP: not available from the request or the twins today; add a one-line note in
  `_technical_attributes` and an F-item in the todos note (§6) rather than inventing a value.

## 5. Not changed, and why (record in the final report, not in code)

Operating flows are **not** re-based by `from_year`: year `y` of the plan is year `y` of the active
state, so energy is priced at calendar-year prices; re-basing would price year 8 at year-1 prices.
The `costs` block removal and the deleted `costs.py` names are the decided step-10 split. Public
classes stay public (project style). Worked examples stay unreviewed snapshots by design; §2.1's
hand-computed tests are the absolute anchor the reviewers asked for. `"unknown"` for a missing
commit stays (the schema types the field as a string).

## 6. Bookkeeping and done

Append under H7 in `/home/contract-proposals/todos.md` (re-read immediately before, one edit,
other agents' items untouched) a dated note: the review's valid findings and their fixes in one
list (PV unit, generator size, residual timing, loan attribution, band, mapping report, band and
placement refusals), the `subsidy_catalog_id` format, and the F-item for SCOP. Update
`hisim/renovisor/how_to_use.md` for the new refusals if it lists exit codes.

Done means: `tests/renovisor`, `tests/economics`, `tests/test_worked_examples.py`,
`tests/test_economics_subsidies.py` green including the `system_setups` end-to-end staged test;
the three CI mypy runs, pylint critical-only and prospector clean on every touched file; docs
build without new warnings; the final report lists every finding of §2–§4 with what was done,
every hand-computed expectation with its arithmetic, and anything left undone.
