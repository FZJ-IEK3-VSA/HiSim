# `hisim.economics` — the lifecycle cost engine

This package is the parallel successor of HiSim's per-component capex/opex cost calculation.
It computes **lifecycle costs over a configurable horizon** (default 20 years, annuity method
per VDI 2067-1 / DIN EN 15459-1) from three kinds of inputs: *facts* declared by components
(what am I, how big), *energy flows* measured by the meters (what crossed the system boundary),
and *versioned data files* (prices, lifetimes, subsidy schemes). Everything else — discounting,
replacements, subsidies, actor splits, uncertainty bands — happens in one central engine.

The full specification lives in **`cost_spec.md`** at the repo root; every provisional decision
and every legacy bug found during implementation is tracked in **`cost_module_issues.md`**.
Section references like "§3.9" below point into the spec.

---

## 1. Overview

### 1.1 What it computes

For one simulated building variant the engine produces, per **perspective**:

| Output | Meaning |
|---|---|
| Net present cost (NPV) | All cash flows over the horizon, discounted to year 0 |
| Equivalent annual cost | NPV × annuity factor — the headline KPI |
| NPV by category / component / payer | Pivots of the same timeline |
| Per-component breakdowns | For stacked-bar frontends; sum exactly to the totals |
| Nominal annual cost series | The liquidity view ("euros out of pocket per year") |
| Monthly cost year 1 | Liquidity / affordability figure |
| Lifecycle CO2 | Embodied + operational, undiscounted, parallel to money |
| Subsidy decision audit trail | Which schemes applied, which caps bound, what was rejected and why |

Every monetary figure is a **(minimum, average, maximum) triplet** (`UncertainValue`, §3.9):
the engine evaluates three coherent worlds (LOW = everything comes in cheap, HIGH = everything
expensive) in one pass, so every published number carries a defensible band. The bounds are
*envelopes*, not statistical quantiles.

### 1.2 The pipeline

```
Components ──get_cost_facts()──▶ ComponentCostFacts   (asset class, size, overrides — NO prices)
Meters ──get_energy_flow_facts()─▶ BillingDeterminants (kWh bought/sold, peaks, ToU bands)
                                        │
   cost_database/*.json ───────────────▶│◀─────────── subsidy_catalog/*.json
   EconomicParameters ─────────────────▶│◀─────────── ExistingAssetRegister (brownfield)
                                        ▼
                              EconomicEvaluator (pure function)
                                        │
                      one canonical CashFlowTimeline per perspective
                     (year × category × subject × payer, all amounts banded)
                                        │
        ┌───────────────┬───────────────┼──────────────────┬────────────────┐
        ▼               ▼               ▼                  ▼                ▼
  LifecycleCostResult  exports    provenance ledger   cost audit     parity report
  (typed, in memory)   (JSON/CSV) (explain any value) (one table)    (vs legacy path)
```

Principles (§3.1): components declare, the engine computes; energy is billed only at carrier
boundaries (no double counting by construction); every perspective/KPI is a filter or pivot of
one canonical timeline; the evaluator is a pure function; no unsourced numbers.

### 1.3 Perspectives (§4, §7.1)

A perspective is a named combination of five dimensions: installation context
(GREENFIELD / BROWNFIELD / STATUS_QUO / OPERATING_ONLY), actor scope (SYSTEM /
OWNER_OCCUPIER / LANDLORD / TENANT), subsidy mode (NONE / FULL / ONLY / EXCLUDE),
financing (cash or an annuity loan), and accounting (FINANCIAL / MACROECONOMIC).
The default bundle (`cost_database/perspectives_default.json`) evaluates nine of them;
greenfield rows are skipped automatically when an existing-asset register is present and
vice versa.

### 1.4 Strictly parallel to the legacy path (§10)

The legacy `get_cost_capex`/`get_cost_opex` path is **untouched** and remains the source of all
published KPI names until the Phase-7 cutover. This engine is opt-in
(`PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS`) and writes only new files:

`lifecycle_costs.json`, `component_costs.json/.csv`, `cash_flow_timeline.csv`,
`lifecycle_kpis.json`, `cost_audit.csv`, `cost_parity_report.csv`, `economic_inputs.json`,
`cost_provenance.json`.

The parity report compares the legacy CSVs (read-only) against the new path per component and
is the evidence base for the eventual cutover decision.

### 1.5 How to run it

```python
# In a system setup / test:
my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)

# Additionally write the human-readable reports (implies the computation):
# cost_summary.md, lifecycle_report.html (plausibility panel + charts along the
# calculation chain) and matplotlib PNGs:
my_simulation_parameters.post_processing_options.append(PostProcessingOptions.LIFECYCLE_COST_REPORT)

# Optionally attach parameters (otherwise defaults with the simulation's country apply):
from hisim.economics import EconomicParameters
my_simulation_parameters.set_economic_parameters(EconomicParameters(interest_rate=0.03))
```

```bash
# Re-price stored results without re-simulating (§4.6):
python -m hisim.economics evaluate <results_dir> [--scenarios scenarios.json]

# Trace any result value back to its sources (§3.10):
python -m hisim.economics explain <results_dir> --value "brownfield_net/equivalent_annual_cost_in_euro"

# Data-file CI checks (§9.6):
python -m hisim.economics validate

# Human-readable report for stored results; --compare adds the variant comparison
# (delta waterfall by subject, discounted payback curve, warm-rent change):
python -m hisim.economics report <results_dir> [--compare <reference_results_dir>]
```

The report layer follows the money along the calculation chain — every spec feature has at
least one visualization plus a result table: **0** automated plausibility panel (thresholds:
`cost_database/plausibility_checks.json` — range checks WARN, structural invariants FAIL),
**1** input audit + the §3.10 sources-used table, **2** year-0 investment waterfalls +
investment table + sunk-cost note, **3** annual cash-flow timeline, cumulative discounted
cost with its min/max uncertainty band, loan amortization chart when financed (§4.4), and
the NPV-by-category table, **4** year-1 energy bill with implied effective prices (the
fastest unit-mix-up detector) + decomposition table, **4b** lifecycle CO2 (§3.8: embodied
vs. operational bars, cumulative curve, table), **5** subsidy composition bars + decision
cards + awards table (flat-shim note when no catalog ships for the country), **6**
perspective whiskers + result table (NPV/EAC/monthly/LCOH/sunk cost), **6b** actor split
(payer whiskers + payer-by-cost-group table, zero-sum checked), **7** per-component stacks +
subject table, **8** variant comparison (delta waterfall + delta table + payback band),
**9** scenario tornado + all-scenarios table + robustness summary, **10** the lifecycle KPI
table (§7.3). The HTML is fully self-contained (inline SVG, light/dark aware, native
tooltips); the markdown summary is deliberately git-diffable so price-data PRs show up as
clean textual deltas on golden scenarios (§9.5).

To feed the engine what the simulation cannot know — the existing system, the applicant, the
tenancy, envelope measures, scenario sets — attach a
`hisim.economics.bridge.EconomicContext` via
`simulation_parameters.set_economic_context(...)`. With a register present, the full
brownfield/owner/landlord/tenant/macroeconomic bundle activates automatically. The complete
worked example is **`system_setups/economic_example/`** (run it with
`python system_setups/economic_example/economic_example_heatpump.py`).

Tests: `pytest tests/test_economics_*.py` (engine math against hand-computed examples,
property tests, subsidy/tariff/scenario behavior, data-file CI, and one real end-to-end
simulation in shadow mode).

---

## 2. Code modules — which file does what

| Module | Contents |
|---|---|
| `uncertainty.py` | `UncertainValue` (min/avg/max triplet) and the slot arithmetic (§3.9). Revenue bands are mirrored via `as_revenue()` so slot ordering always holds. |
| `carriers.py` | `EnergyCarrier` enum — the pricing vocabulary at the system boundary (distinct from `LoadTypes`). |
| `parameters.py` | `EconomicParameters`: horizon, interest, escalation rates, CO2 scenario, country, data paths (§3.2). |
| `facts.py` | What components/meters declare: `ComponentCostFacts`, `EnergyFlowFacts`, `BillingDeterminants`, `ExistingAsset(Register)`, `CostRelevance` (§3.3, §3.4, §9.2). Deliberately a leaf module so `hisim/component.py` can import it. |
| `database.py` | Loads/validates `hisim/cost_database/` (device entries, energy prices, CO2 paths, escalation defaults, source registry) and applies scenario **data overlays** (§3.5, §4.6). |
| `provenance.py` | The provenance ledger: interned `ParameterProvenance` records, `ProvenanceReport` rendering (§3.10). |
| `timeline.py` | `CashFlowEntry`, `CashFlowTimeline`, `CostCategory`, `Actor` — the canonical timeline and its NPV/pivot helpers (§3.6). |
| `financing.py` | `FinancingPlan` and annuity / interest-only loan schedules (§4.4). |
| `perspectives.py` | `Perspective` and its five dimensions; loads the default bundle (§4, §7.1). |
| `subsidies.py` | The data-driven subsidy engine: condition language (tri-state), benefit/payout kinds, eligible-cost caps and proration, cumulation solver, questionnaire (`required_questions`) (§5). |
| `tariffs.py` | `TariffContract` (FLAT / TIME_OF_USE / DYNAMIC supply, capacity charges, feed-in, §14a discounts), the pure `apply_tariff` billing function, the §8.5 decomposition, tariff counterfactual, spot-series loader and the synthetic test profile (§8). |
| `actors.py` | Allocation rulesets: trivial owner-occupier, `DE_2024` (BetrKV split, CO2KostAufG tier table, modernization levy), zero-sum invariant (§6). |
| `evaluator.py` | **The core.** `EconomicEvaluator` builds the timeline (investment, replacements, residual value, maintenance, energy projection, subsidies, financing, CO2 damage) and evaluates perspectives. `EvaluationInputs` is the full input record. |
| `results.py` | `LifecycleCostResult`, `ComponentCostBreakdown`, `LifecycleCo2Result`, `EvaluationMatrix`, `VariantComparison` + `compare()` (slot-wise deltas, payback bands, warm-rent neutrality) (§3.7). |
| `scenarios.py` | Scenario sets (FACTORIAL / ONE_AT_A_TIME), parameter axes and data overlays, the evaluation cube, tornado / break-even / robustness helpers, cube exports (§4.6). |
| `serialization.py` | `economic_inputs.json` round-trip — post-hoc re-pricing without re-simulation (§4.6). |
| `adapter.py` | Compatibility adapter for components that have not yet adopted `get_cost_facts()`: maps known classes/configs to facts and meter specs. Shrinks as adoption grows; never calls legacy cost methods (§10.0 rule 4). |
| `audit.py` | `cost_audit.csv` (one row per component: origin, sources, bands, subsidies) and the legacy parity report (§9.5, §9.7). |
| `exports.py` | All JSON/CSV exports and the namespaced lifecycle KPIs with `value_min`/`value_max` bands (§7.2–§7.4). |
| `bridge.py` | The postprocessing entry point behind `COMPUTE_LIFECYCLE_COSTS`: collects facts/flows from a finished run, picks a covered price basis year, evaluates the default bundle, writes everything. Guarded so it can never break a run. |
| `validation.py` | Data-file CI: source completeness, coverage matrix, question coverage, staleness (§9.6). |
| `reporting.py` | Human-readable reports (option `LIFECYCLE_COST_REPORT`): plausibility panel, `cost_summary.md`, self-contained `lifecycle_report.html` with inline-SVG charts, variant-comparison section. |
| `report_plots.py` | Matplotlib PNG companions (annual cash flows, investment build-up, perspective whiskers, component stacks, payback curve) — same display groups and colors as the HTML. |
| `__main__.py` | The `evaluate` / `explain` / `report` / `validate` CLI. |

Related but outside this package: `hisim/components/tariff_provider.py` (the in-simulation
price signal driven by the same `TariffContract`, §8.3), and the additive hooks on
`hisim/component.py` (`get_cost_facts`, `get_energy_flow_facts`, `cost_relevance`).

---

## 3. Data files — where the numbers live and how to edit them

All numbers live in JSON data files, never in Python. Two directories:

```
hisim/cost_database/          prices, lifetimes, emission factors, tariffs, allocation rules
hisim/subsidy_catalog/        subsidy schemes and the user questionnaire
```

Each directory has its own `sources.json` registry. **Every data entry must reference at least
one source id** — an unsourced datapoint fails loading (§3.10). After any edit, run
`python -m hisim.economics validate` (the same checks run in `tests/test_economics_data_and_integration.py`).

### 3.1 Uncertainty bands — the universal value syntax

Every monetary field accepts either form:

```jsonc
"working_price_in_euro_per_kwh": 0.30                                  // exact (min = avg = max)
"working_price_in_euro_per_kwh": {"min": 0.28, "avg": 0.32, "max": 0.38}  // band
```

Use exact values only for genuinely certain numbers (statutory amounts, contracted tariffs).
Loaders enforce `min <= avg <= max`.

### 3.2 `devices_<COUNTRY>.json` — device costs

One entry per `(component_type, valid_from_year)`. A lookup for year Y picks the entry with the
**greatest `valid_from_year <= Y`** (a 2030 simulation uses the 2026 entries until 2035 entries
exist). To change a price *from a given year on*, add a new entry with that `valid_from_year`
rather than editing an old one — old basis years keep reproducing their published results.

```jsonc
{
  "component_type": "HEAT_PUMP",             // ComponentType enum name
  "valid_from_year": 2026,
  "specific_investment": {"value": {"min": 1100, "avg": 1500, "max": 2100}, "per_unit": "kW"},
  "scaling_exponent": null,                  // economies of scale: cost = c0 * size^exp; linear if null
  "fixed_installation_cost_in_euro": 0,
  "planning_cost_in_euro": 0,                // energy consultant, permits — subsidy-eligible
  "removal_cost_in_euro": 0,                 // disposal of THIS device type when it gets replaced
  "maintenance_rate_per_year": {"min": 0.01, "avg": 0.015, "max": 0.025},   // share of gross investment
  "fixed_operation_cost_in_euro_per_year": 0,  // chimney sweep, metering fee — NOT a rate
  "service_life_in_years": 18,
  "embodied_co2": {"value": 165.0, "per_unit": "kW"},
  "vat_rate": 0.19,
  "price_basis": "AS_LEGACY",                // see assumptions below
  "legacy_flat_subsidy_share": 0.30,         // Phase-1 shim; ignored when a subsidy catalog is active
  "energy_related_cost_share": 1.0,          // coupled-cost share for envelope measures, see §3.2b
  "anyway_threshold_years_override": null,   // per-class anyway threshold; envelope ships 5.0
  "source_ids": ["src_ai_estimates"],        // mandatory
  "field_sources": {"service_life_in_years": ["src_vdi2067"]},   // optional per-field refinement
  "notes": "..."
}
```

#### 3.2b Building envelope measures (spec Q7)

Wall/roof/top-ceiling/floor insulation, windows, exterior doors, air sealing and ventilation
systems are ordinary cost subjects with their own `ComponentType`s, sized in **m² of the
respective envelope element** (`EXTERIOR_DOOR` and `VENTILATION_SYSTEM` per unit). They are
*not* simulation components — the building physics only sees changed U-values — so their
`SubjectCostFacts` are injected into `EvaluationInputs` by whoever defines the variant
(RenoVisor mapping or a system setup), with areas taken from the building's TABULA data and
the achieved `u_value` in `technical_attributes` (the BEG subsidy conditions test it).

Three envelope-specific mechanics, all data-driven:

- **`anyway_threshold_years_override`** (shipped 5.0 for envelope, devices keep the 2 a
  default): if the replaced element had at most this much life left, the anyway-cost credit
  applies.
- **Like-for-like replacements are recognized**: register the old element in the
  `ExistingAssetRegister` with `replaced_by_asset_classes` naming the measure's class (also
  when both are e.g. `WINDOWS`) — the measure is then charged as an investment and the dead
  element's avoided like-for-like replacement is credited. Without the `replaced_by`
  declaration, a same-class register entry still means "kept".
- **`energy_related_cost_share`** (coupled costs / Ohnehin-Kosten): when < 1 and the element
  was due anyway, the credit becomes `(1 − share) × gross` — the scaffolding/render share that
  would have been spent regardless — *replacing* the like-for-like credit so the two never
  double count. **Currently shipped as 1.0 everywhere** (deliberate interim decision: the
  mechanism exists and is tested, the data does not use it yet); it is scenario-overlayable
  for sensitivity studies. Acknowledged as a pragmatic simplification of the coupled-cost
  problem.

With 35–50 a service lives inside a 20 a horizon, envelope economics are dominated by the
**residual value** (half the wall insulation cost is credited back at year 20 of a 40 a life)
— that is the intended VDI 2067 treatment, not a bug. The DE catalog contains the BEG EM
envelope schemes (15 % base with per-class U-value conditions + 5 % iSFP bonus, capped at
20 %, mutually exclusive with §35c) and the `building.has_isfp` questionnaire entry.

`per_unit` is one of `"kW"`, `"kWh"`, `"liter"`, `"m2"` or `null` (absolute price per device);
it must match the `size_unit` the component declares — the pre-run resolution check enforces
this. Adding a **new country** = creating `devices_XX.json` + `energy_prices_XX.json`
(+ optionally `escalation_defaults_XX.json` and CO2 paths); no code changes.

### 3.3 `energy_prices_<COUNTRY>.json` — energy prices

One entry per `(carrier, year)`, same greatest-year-≤ lookup. Two-part tariff with an explicit
CO2 component:

```jsonc
{
  "carrier": "NATURAL_GAS",
  "year": 2026,
  "working_price_in_euro_per_kwh": {"min": 0.08, "avg": 0.10, "max": 0.13},
  "standing_charge_in_euro_per_year": {"min": 140, "avg": 180, "max": 240},   // Grundpreis
  "grid_exit_fee_in_euro": 250,              // one-off when the carrier is dropped
  "emission_factor_in_kg_per_kwh": 0.20,     // per quantity_unit (see below)
  "co2_price_exposure": 1.0,                 // share of emissions priced via co2_price_paths.json
  "tax_and_levy_share": 0.25,                // stripped in the MACROECONOMIC view
  "quantity_unit": "kWh",                    // "kWh" | "liter" (oil, diesel) | "ton" (pellets, wood chips)
  "source_ids": ["src_ai_estimates"]
}
```

**The CO2 double-counting rule:** if `co2_price_exposure > 0`, the working price must EXCLUDE
the explicit CO2 price (the engine adds `emissions × co2_price(country, year)` from the paths
file). If the working price already contains carbon costs (as the migrated pre-2026 entries
do), exposure must be 0. Never both.

`quantity_unit` documents the native billing quantity; for `"liter"`/`"ton"` carriers the
`*_per_kwh` field names read "per liter"/"per ton" (a naming compromise, issue #21). The
`ELECTRICITY_FEED_IN` carrier holds the feed-in remuneration as its working price.

### 3.4 `co2_price_paths.json` — carbon price trajectories

Named `low`/`central`/`high` paths per country, as year→€/t points with **step interpolation**
(the last defined point ≤ the year applies). Shared EU segments live under `eu_shared` and are
referenced via `include_eu_shared` instead of duplicated. Selected via
`EconomicParameters.co2_price_scenario` (`"none"` disables carbon pricing).

### 3.5 `escalation_defaults_<COUNTRY>.json` — price-change defaults

Default nominal escalation rates per carrier and (optionally) per asset class (learning
curves). Fallback chain (§3.2): explicit `EconomicParameters` value → this file → the general
rate. The per-asset-class table ships empty on purpose (spec Q2) until reviewed sources exist.

### 3.6 `sources.json` — the source registry

```jsonc
{"id": "src_hp_survey_2026", "citation": "…", "url": "https://…", "publication_year": 2026,
 "retrieved": "2026-07-07", "kind": "MARKET_SURVEY", "notes": "…"}
```

`kind` ∈ MARKET_SURVEY | STANDARD | STATUTE | MANUFACTURER | LITERATURE | PROJECT_DATA |
EXPERT_ESTIMATE. An honestly labeled guess is fine; a number without provenance is not.
`retrieved` feeds the 12-month staleness warning; entries referenced by no datapoint are
flagged as orphans. When you add a data entry, either reuse an existing source id or add a
registry entry first.

### 3.7 `perspectives_default.json`, `allocation_DE_2024.json`

The default perspective bundle (§7.1) and the German rented-building allocation parameters:
modernization-levy rate/caps (§559 BGB), the avoided-maintenance deduction, the apportionable
maintenance share, and the CO2KostAufG tenant/landlord tier table. All legally sensitive —
flagged for legal review (issues #10).

### 3.8 `tariffs/*.json` and `spot_series/*.csv`

Tariff contracts (§8.2), referenced by id (file name = id). Supply kinds FLAT, TIME_OF_USE
(band definitions with weekday/hour masks) and DYNAMIC (references a spot series). One example
ships: `DE_DYNAMIC_SYNTHETIC_2024` on the deterministic `synthetic_reference_hourly.csv`
profile — real EPEX series are not shipped for licensing reasons (spec Q16); drop a
user-supplied hourly CSV (one €/kWh price per line) into `spot_series/` instead. Non-energy
components (markup, grid fee, taxes/levies) are stored separately so the macroeconomic view
can strip them. Without an explicit contract, a default flat contract is generated from the
§3.3 price entries — behaviorally identical to the plain price lookup.

### 3.9 `subsidy_catalog/<COUNTRY>.json` + `questions_<COUNTRY>.json`

Schemes (§5.2) with mandatory `legal_basis` and `url`, an eligibility condition tree
(`all`/`any`/`not` over `{field, op, value}` leaves — no Python eval), a benefit
(SHARE_OF_ELIGIBLE_COST, BONUS_SHARE, LUMP_SUM, PER_UNIT, TAX_CREDIT, REDUCED_VAT, SOFT_LOAN,
OPERATIONAL), eligible-cost caps per dwelling unit, residential-share proration, and cumulation
rules (group + combined rate cap + excludes). The shipped `DE.json` encodes BEG EM (base 30 % +
speed/income/efficiency bonuses, 70 % cap), §35c EStG (mutually exclusive with BEG) and the
KfW supplementary loan; `AT.json` a lump-sum boiler-replacement grant. **Ireland has no catalog
yet** — the device entries carry rough SEAI-like flat shares in `legacy_flat_subsidy_share`
instead (issue #25).

Every context field a scheme's conditions reference must have a localized (de + en) entry in
`questions_<COUNTRY>.json`, or validation fails — that's what keeps the user questionnaire
(§5.7) complete by construction. The catalog itself is only used when
`EconomicParameters.subsidy_catalog_path` is set; otherwise the flat shim applies.

### 3.10 Scenario overlays — tweaking values without editing files

For "what if" sweeps you usually should **not** edit the database at all. Scenario sets (§4.6)
can overlay individual datapoints by dotted path:

```jsonc
{"axes": [{"name": "hp_price", "field": "devices_DE.HEAT_PUMP.specific_investment",
           "levels": {"central": null, "cheap": {"min": 900, "avg": 1100, "max": 1400}}}]}
```

Overlaid values enter the provenance ledger as `SCENARIO_OVERLAY`, so an explained result names
exactly which numbers were counterfactual. `country` and the dataset paths are deliberately not
sweepable.

---

## 4. Assumptions

### 4.1 Methodology (fixed in the engine, §2–§4)

- **Nominal rates, nominal euros, discounted to year 0.** Defaults: 3 % interest, 2 % general
  escalation. Real-term calculation works by supplying real rates consistently.
- **End-of-year convention** for all flows; replacements at multiples of the service life;
  intra-year discounting is out of scope (monthly figures = annual / 12).
- **Residual value** = straight-line share of the last-installed unit's *escalated* purchase
  price, credited at the horizon (VDI 2067).
- **Kept brownfield assets** cost no investment; their first replacement comes at
  `service_life − current_age`. Replaced assets add the old device's removal cost; their
  written-off book value is reported as `sunk_cost_written_off_in_euro` but kept out of
  decision KPIs; if ≤ 2 years of life remained (`anyway_threshold_years`), the avoided
  like-for-like replacement is credited (Sowieso-Kosten, §4.1).
- **Energy projection** (§8.5): year-1 bills are decomposed into volume effect (escalated with
  the carrier rate), flexibility value (spread escalation rate, default = carrier rate),
  standing charges (general rate), capacity charges (grid-fee rate) and the CO2-price component
  (from the trajectory). EEG-style feed-in stays nominally fixed for its contract duration.
  Simulations shorter than a year are annualized linearly with a warning.
- **Uncertainty = envelope, not confidence interval** (§3.9): LOW/HIGH are fully correlated
  cheap/expensive worlds. Physical quantities, economic *rates*, service lives and emission
  factors stay exact in v1 (spec Q25); their uncertainty belongs to scenario axes.
- **Choices are made on the AVERAGE slot** (subsidy combination, counterfactual selection) and
  then valued in all three slots, so the three results describe one plan.
- **Macroeconomic view** (§4.5): subsidies dropped, tax/levy shares stripped, CO2 damage cost
  (default 250 €/t, UBA) applied to operational emissions. It is a socio-economic research
  view, not a household bill.
- **Zero-sum actor split**: landlord + tenant (+ owner) NPVs sum exactly to the system NPV;
  asserted per slot in tests.

### 4.2 Data assumptions — three vintages of data

**Migrated legacy entries (DE 2018–2024/2050, AT 2025/2040).** Copied 1:1 from the old
`configuration.py` dicts as degenerate bands (min = avg = max) so the parity harness can prove
the engine reproduces today's numbers. Known warts are preserved on purpose and tracked in
`cost_module_issues.md`: VAT status undocumented (`price_basis: "AS_LEGACY"` = "use as-is",
issue Q5/#4), CO2 price embedded in the fuel prices (exposure 0), meter "maintenance rates"
that are really absolute fees (#1), per-kWh emission factors on per-ton fuels (#24), a diesel
price typo (#3). Fix these in explicit data-review PRs, not silently.

**AI-estimate entries (DE + IE, 2026 and 2035; source id `src_ai_estimates`).** Placeholder
values *for testing only*, created 2026-07-07 (issue #25). They are honest guesses with real
min/avg/max bands and deliberately use the engine's full modeling: CO2-exclusive fuel prices
with `co2_price_exposure = 1.0`, standing charges and grid exit fees, metering fees as fixed
operation costs, filled tax/levy shares, an Irish carbon-tax/ETS2 price path, and learning
curves toward 2035 (PV/battery falling, labor-heavy trades rising; Irish heat-pump installs
~25 % above German). Replace them with reviewed sources before any scientific use — the
`explain` CLI will tell you exactly which results rest on them.

**Statute-derived data** (subsidy catalogs, allocation rules, nEHS/ETS2 path segments). Encoded
from the legal texts as of 2024 with `STATUTE` sources, but **not yet legally reviewed** — the
spec requires a legal review pass before release (issues #10, #11). The ETS2 corridor from 2027
onward is an expert estimate, not statute.

### 4.3 Engine defaults you might want to change

All on `EconomicParameters` (settable per run, per RenoVisor request, or per scenario axis):
`observation_period_in_years` (20), `interest_rate` (0.03), `general_price_escalation_rate`
(0.02), `investment_price_escalation_rate` (0.02), `co2_price_scenario` ("central"),
`co2_damage_cost_in_euro_per_ton` (250), `anyway_threshold_years` (2),
`price_basis_year` (defaults to the simulation year; the postprocessing bridge falls back to
the earliest covered year with a warning when the database has no data for the simulated year),
`apply_subsidies` (True), `allow_counterfactual_billing` (False — rebilling a load profile
under a tariff it was not simulated with must be opted into, §4.6).

---

## 5. Adding things — quick recipes

- **New component adopts the engine** (Phase 6 pattern, §9.1): set
  `cost_relevance = CostRelevance.PRICED` on the class, implement `get_cost_facts()` (~6 lines:
  asset class, size from the config, unit, KPI tag, optional per-field overrides with an
  `override_source`), and mark the capacity config field with
  `field(metadata={"capacity": True})` so the contract test can verify scaling. Leave the
  legacy methods untouched until cutover. Meters additionally implement
  `get_energy_flow_facts()`.
- **New asset class**: add the `ComponentType`, then device entries for *every* shipped country
  — the coverage-matrix check fails otherwise.
- **New country**: `devices_XX.json` + `energy_prices_XX.json` (+ escalation defaults, CO2
  paths, and — if needed — subsidy catalog with question file). The engine needs no code change;
  if it did, the schema failed (§10.1 Phase 4).
- **New envelope measure in a variant**: add a `SubjectCostFacts` entry (class, m², `u_value`
  in `technical_attributes`) and register the replaced element with
  `replaced_by_asset_classes` — see §3.2b.
- **New subsidy scheme**: append to the country catalog; make sure every context field your
  conditions reference has a question entry, and set `cumulation` correctly. `validate` catches
  the rest.
- **Price update**: new entry with a new `valid_from_year`/`year`; never rewrite history. The
  effect shows up as a reviewable diff of `cost_audit.csv` on the golden scenarios (§9.5).
