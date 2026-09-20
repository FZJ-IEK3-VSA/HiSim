# Step 10 — the staged economics evaluator, its result document and the translator integration

**Status:** implementation specification, 2026-09-19
**Implements:** `/home/contract-proposals/economics-hisim-spec.md` ("the E-spec" below), read together
with `economics-backend-spec.md` (what the backend transports), `calculation-request.md` §9 and
`todos.md` H7/H14. The E-spec is the requirements document; this file settles the engine-side
choices it left open and says what exists to build on. Where the two disagree, this file names it
and wins for the implementation; each such point is also recorded in `todos.md`.
**Builds on:** `renovisor-backend` at `c52a5619` (PR #778): `hisim/renovisor/run.py` attaches
`EconomicParameters(country)` and writes `economic_inputs.json`, `lifecycle_costs.json`,
`cost_provenance.json`; `hisim/renovisor/costs.py` already re-evaluates stored inputs over a
second horizon (`ShortHorizonEvaluation`); `hisim/economics/` has `EvaluationInputs`,
`serialization.read_inputs`, `EconomicEvaluator.evaluate`, `results.compare`,
`ExistingAssetRegister`/`ExistingAsset`, `SubjectCostFacts`, `FinancingPlan`, the perspective
bundle `hisim/cost_database/perspectives_default.json`, and the worked-example library under
`tools/worked_examples/` with its attestation.

Ground rules as in step 3 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, interpreter and
`PYTHONPATH`, no commits, class-scope constants, plain complete docstrings, 120 columns, flake8 +
mypy clean on `hisim/economics`, `hisim/renovisor` and the tests; prospector clean on every file
you touch — run `prospector --profile .prospector.yml <file>`; the three CI mypy runs). New tests
`@pytest.mark.base`; the end-to-end one `system_setups`. **Invent no numbers**: every constant
carries a source comment and the words TO BE REVIEWED; the worked examples' expected values are
computed by the harness and left `unreviewed` for a person to attest, never typed as if reviewed.

---

## 1. Engine-side decisions the E-spec left open (settled here, mirrored in `todos.md`)

| E-spec says | Engine has | Decision |
|---|---|---|
| default perspective `brownfield_owner_subsidized_cash` | ids `greenfield_gross`, `greenfield_net`, `brownfield_gross`, `brownfield_net`, `operating`, `owner_monthly`, `landlord`, `tenant`, `macroeconomic` | **`brownfield_net` is the RenoVisor default** (existing assets in the register, subsidies applied, cash financing unless a loan is asked for). The E-spec's id is recorded as an alias in the spec text, not added to the bundle. `reference` uses the same perspective. |
| subsidies come from the catalogue; `undetermined` never `zero` | `*_net` perspectives apply a **flat subsidy shim** when no country catalogue is configured (finding F4: 4 626 € on the Irish example) | With no catalogue for the country, the staged evaluation runs `subsidy_mode: NONE` and every `subsidies[]` entry is `undetermined` with the note "no subsidy catalogue for <CC>"; the shim is never published as a grant. A catalogue directory may be passed (`--subsidy-catalog`), and then `awarded`/`ineligible` follow the solver. |
| "one implementation of the money" | `result.json` (step 6) carries a single-state costs block | **`result.json` keeps the KPI half only** once `economics_result.json` exists: its `costs` block is removed, `missing` keeps the cost fields with the reason "money is in economics_result.json (E-spec §3)". `kpis.py`/`provenance.py` stay; `costs.py` is retired except for what §4 reuses. `calculation.json` lists `economics_result.json` when the backend later calls the staged command; a single `run` does **not** produce it (the plan spans jobs). |
| stage `job_id` in the document | the engine sees directories, not jobs | `stages[].job_id` is taken from the `--stage` argument's optional fourth field (`<dir>:<from_year>:<label>[:<job_id>]`) and is `null` when absent; the backend passes it. |
| `installation_year` on request fields (E-spec §7) | not in the vendored schema yet (F7) | Read when present, `construction_year` otherwise, `defaulted` line in the mapping report; never blocks. |
| `measures[i].cost` block for envelope pricing (E-spec §7) | not in the schema yet (F7/F10) | Read when present → `SubjectCostFacts` for the envelope measure; absent → the subject is added with `investment: UncertainValue.exact(0)` **and** flagged `unpriced: true` in `by_subject[]`, so the document says what it does not know instead of hiding the measure. |

## 2. `hisim/economics/staged.py` — the evaluator (E-spec §1)

```python
@dataclass(frozen=True)
class Stage:
    inputs: EvaluationInputs
    from_year: int
    label: str
    measures: Tuple[str, ...] = ()
    job_id: Optional[str] = None

@dataclass
class StagedResult:
    reference: LifecycleCostResult          # stages[0] held over the horizon
    plan: LifecycleCostResult               # the staged plan
    comparison: VariantComparison           # results.compare(reference, plan)
    stages: Tuple[Stage, ...]
    per_stage: Tuple[LifecycleCostResult, ...]   # each stage evaluated alone over the full horizon (§2 step 1)

class StagedEvaluator:
    def evaluate(self, stages: Sequence[Stage], parameters: EconomicParameters,
                 perspective: Perspective, catalog: Optional[SubsidyCatalog]) -> StagedResult
```

Semantics exactly E-spec §1.2, implemented as a **timeline splice** so that "a plan of one stage
equals `EconomicEvaluator` on that stage, entry for entry" holds by construction:

1. Evaluate every stage alone over the full horizon with today's `EconomicEvaluator` (one
   `LifecycleCostResult` each, `per_stage`).
2. Build the plan's `CashFlowTimeline` from the per-stage timelines: for year `y`, take every
   **operating** entry (the E-spec's list: energy, maintenance, fixed operation, feed-in, CO₂
   price, levy, replacement reserve) from the stage active in `y`.
3. **Investment-class** entries (`INVESTMENT`, `PLANNING`, `REMOVAL`, `SUBSIDY`, `LOAN_*`,
   `ANYWAY_COST_CREDIT`) of stage `k` are taken from stage `k`'s own year-0 entries for the
   subjects new in `k` (present in `S_k`, absent from `S_{k-1}` under the same asset class, or
   larger: the increment), moved to `from_year_k` and escalated with the investment escalation
   rate to that year. Subjects carried over are not charged again.
4. **Aging across stages**: before evaluating stage `k > 0`, its `EvaluationInputs.existing_assets`
   is the inventory register merged with one `ExistingAsset` per subject charged in an earlier
   stage `j` (`installation_year = simulation_year + from_year_j`, `asset_class` from the subject's
   cost facts, `replaced_by_asset_classes` from later stages). This is what makes replacements,
   residual value and removal fall in the right years through the engine's brownfield machinery;
   the evaluator adds no second mechanism. Consequence: stages are evaluated **in order** and
   stage `k`'s inputs depend on `j < k`; say so in the docstring.
5. Subsidies decided per stage at `from_year_k` against the one catalogue (or NONE, §1).
6. Financing: one `FinancingPlan` per stage when `parameters` ask for a loan, starting in
   `from_year_k`.
7. Discounting and annuity as today; `reference = per_stage[0]`; `comparison =
   results.compare(reference, plan, "reference", "plan")`.

Refuse with a named `StagedEvaluationError` (exit 2 at the CLI): `stages[0].from_year != 0`,
non-increasing `from_year`, `from_year > horizon`, parameters differing between stages'
stored inputs (`simulation_year`, `simulated_period_fraction`, country), unknown perspective id,
a country without price data. An unresolved cost subject (D7) is an engine error (exit 3),
unchanged.

**Invariants, each a test (E-spec §1.3):** one stage equals `EconomicEvaluator` entry for entry;
two stages at `0` and `T+1` equal the baseline alone; moving a stage from `a` to `b > a` changes no
flow before `a`; payer NPVs sum to system NPV per slot; every `by_group`/`by_subject` sum equals
its total per slot to the cent.

## 3. `hisim/economics/staged_document.py` — `economics_result.json` (E-spec §3)

Build the document of E-spec §3 verbatim from `StagedResult`: `schema_version: 1`, `engine
{hisim_commit, economics_version}`, `parameters`, `currency`, `groups` (the eight names in the
E-spec's order, as an Enum `CostGroup` with a fixed `CostCategory → CostGroup` mapping in one
table), `stages[]`, `reference`, `plan` (the `Evaluation` shape: `totals`, `by_group`,
`by_category`, `by_payer`, `by_subject[]` with `measure_id`, `stage`, `unpriced`, `annual[]` with
`events[]`, `cumulative[]`, `energy_year1[]`, `co2`, `subsidies[]`, `financing`), `comparison`,
`provenance`. Bands are `{min, best, max}` from `UncertainValue`; costs positive, credits negative,
years relative with `calendar_year` given per row. Write `economics_result.schema.json` beside the
code (JSON Schema 2020-12) and validate every written document against it. `hisim_commit` comes
from `hisim.renovisor.report.HiSimCommit` **extended** to read a baked `hisim/COMMIT` file or the
`HISIM_COMMIT` environment variable before falling back to git (todo H6); document that the
Dockerfile writes the file.

Tests: schema validity; every stack sums to its total per slot on the written file; `groups`
order fixed; ids only (no label strings from HiSim); `measure_id` stamped from the mapping
report's `subjects` map (§4).

## 4. Translator integration (`hisim/renovisor/`, E-spec §4)

1. `translate` builds an `EconomicContext` and `run` attaches it with
   `set_economic_context` beside the existing `set_economic_parameters`:
   - `existing_assets`: an `ExistingAssetRegister` from the **house inventory**: the current
     generator (`heating.type_of_system` → `ComponentType` through one table in `constants.py`,
     `installation_year` from the request when present else `construction_year`, `energy_carrier`,
     `is_functional: True`), existing PV, battery, solar thermal, and the five envelope elements
     (`installation_year` = `construction_year`), with `replaced_by_asset_classes` filled from
     the package (`heating_system` replaces the generator, `window_replacement` the windows, …) and
     `anyway_share` from a `constants.AnywayShareByPlacement` table (TO BE REVIEWED, sourced to
     `cost_module_issues.md` #12; first-time insulation well below 1);
   - `extra_cost_facts`: one `SubjectCostFacts` per envelope measure, sized in m² of its element
     from the realized `Building` config (fall back to the request's `area_in_m2`; if neither,
     the subject is `unpriced`), priced from `measures[i].cost` when present (§1);
   - `technical_attributes_by_subject`: achieved U-values per element; SCOP where the generator
     is a heat pump and the value is known;
   - `subsidy_context` from the request's `applicant` block when present, else the engine's
     default (undetermined);
   - `annual_heat_demand_in_kwh` from the run's KPIs (after the run: attach what is known before
     the run, and let the bridge fill the KPI-derived value as it does today); `living_area_in_m2`
     from the request when present, else the conditioned floor area.
2. The mapping report gains `subjects: {<component key or envelope subject>: <measure_id|null>}`
   so the document can stamp `measure_id` on `by_subject[]`.
3. `result.json` loses its `costs` block (§1) — adjust `result.py`, `costs.py`, `kpis.py`'s
   `results` section of the capability document (the cost rows now say "in economics_result.json"),
   `map.py`'s result pane, and the tests.

## 5. CLI (E-spec §6)

`python -m hisim.economics staged --stage <dir>:<from_year>:<label>[:<job_id>] … [--parameters
params.json] [--perspective brownfield_net] [--subsidy-catalog <dir>] --out economics_result.json`
added to `hisim/economics/__main__.py` beside `evaluate|explain|report|validate`. Each `<dir>` is a
completed job's output directory or any directory holding `economic_inputs.json` (and, optionally,
`mapping_report.json` for the `subjects` map). Exit 0 with the document; exit 2 with a
`problems.json` on `StagedEvaluationError`; exit 3 on an engine error with one line on stderr.
Well under a second for three stages.

## 6. Worked examples (E-spec §2)

Three examples in the existing library shape (`tools/worked_examples/`, YAML emitted by
`emit_yaml`, attested by `attestation.py`): (a) one stage, heat pump in year 0, which must equal
the existing single-variant example; (b) two stages, envelope in year 0 and heat pump in year 3
(boiler kept and aging three years, then removed); (c) three stages with a loan per stage and a
subsidy whose age condition is met only from stage 2 (needs a synthetic catalogue in the test
directory; do not invent Irish schemes). Compute their expected values with the harness and leave
them `unreviewed`; the reviewer attests. Their tests run and compare to the cent but are marked so
that an unreviewed example is a finding, not a failure, exactly as `enforcement.yaml` prescribes.

## 7. End-to-end test (`tests/renovisor/test_staged_economics.py`, `system_setups`)

Run the vendored mockup baseline and package (`one_day_15min`) through `run`, then `staged` on the
two output directories (`0:baseline`, `0:package`): exit 0, the document validates, `comparison`
is present, `plan.by_subject` carries the heat pump with `measure_id: heating_system` and
`stage: 1`, the envelope subject is `unpriced: true` (no `cost` block in the mockup), `subsidies[]`
are all `undetermined` (no Irish catalogue), and `reference.totals.npv_in_euro.best > 0`.

## 8. `todos.md` bookkeeping (in `/home/contract-proposals/`, the shared file)

When you finish, append to H7 a dated "HiSim agent" note listing: what landed (files, CLI,
document schema), the decisions of §1 (perspective alias, NONE subsidy mode, `result.json` split,
`job_id` argument, unpriced subjects), the perspective-id and `cost`-block points as requests to
the frontend agent (F7/F10), and the H6 fix if you did it. Tick H6 if the baked commit landed.
Do not edit other agents' items.

## 9. Done means

All `tests/renovisor` and the new `tests/economics/test_staged*.py` green; the three CI mypy
runs, pylint critical-only and prospector clean on every file touched; the docs build has no new
warning (`docs/modules/economics.rst` if it exists gains the module; check); the mockup pair runs
through `run` and `staged` and the document validates; the final report lists every decision
taken beyond §1, every TO BE REVIEWED constant with its source, the worked examples' computed
values, and what could not be done.
