# Cost module implementation — open issues and decisions to clarify

Running log of ambiguities found while implementing `cost_spec.md`. Items marked **DECIDED (provisional)**
were resolved with a documented default to keep the implementation moving; they should be reviewed.

## Spec open questions (§11) — provisional defaults taken in code

- **Q1 (nominal vs real rates)**: implemented nominal as documented default (interest 3 %, general escalation 2 %).
- **Q2 (default escalation rates / CO2 paths)**: `escalation_defaults_DE.json` ships with a small,
  clearly-labeled EXPERT_ESTIMATE table (electricity 2 %, gas 3 %, oil 3 %, others 2 %); the
  per-asset-class investment escalation table ships **empty** per the spec proposal. CO2 price paths
  for DE encode nEHS 2024–2026 fixed prices and an ETS2 corridor estimate from 2027 — values are
  EXPERT_ESTIMATE and need team review against sources [33]–[35].
- **Q4 (replacement timing)**: end-of-year convention everywhere, as proposed.
- **Q5 (gross vs net storage)**: device entries migrated from `configuration.py` carry
  `vat_rate: 0.19` (DE) / `0.20` (AT) and are stored **as-is** from the legacy dicts. The legacy
  numbers' VAT status is *undocumented* — parity requires using them unchanged, so the migrated
  entries are flagged `"price_basis": "AS_LEGACY"` and the gross-up for FINANCIAL accounting is a
  no-op for them. Needs a data-review PR to reclassify each entry as net and set true VAT handling.
- **Q6 (anyway-cost threshold)**: default 2 years, credit on by default, gross figures always reported.
- **Q10 (EEG feed-in)**: fixed nominal for 20 years from install (escalation 0.0 default).
- **Q14 (strictness rollout)**: `strict_cost_completeness` defaults to warning; tests/CI use strict=True.
- **Q15 (capacity-field convention)**: dataclass field metadata `field(metadata={"capacity": True})`.
- **Q16 (spot series)**: no licensed EPEX data shipped; a synthetic reference profile generator is
  provided for tests plus a documented CSV loader for user-supplied series.
- **Q17 (spread escalation)**: defaults to the carrier escalation rate.
- **Q18 (default counterfactual)**: tariff counterfactual is always computed when a DYNAMIC contract is
  active; behavioral counterfactual is a second run (not automated).
- **Q19 (§14a depth)**: grid-fee discount as tariff data only; no dimming simulation requirement.
- **Q21 (cube explosion)**: warn > 1 000 scenarios, error > 100 000.
- **Q25 (bands beyond money)**: service lives and emission factors stay exact in v1.
- **Q27 (source granularity)**: per-entry `source_ids` mandatory, per-field `field_sources` optional.
- **Q28 (registry scope)**: one `sources.json` per directory (cost_database and subsidy_catalog).
- **Q30 (subsidy overlays)**: data overlays on subsidy catalog entries are accepted by the schema and
  re-run scheme selection per scenario.

## New issues found during implementation

1. **Legacy capex dict has meter "maintenance rates" > 1** (`ELECTRICITY_METER: 2.4`,
   `GAS_METER: 1.8` in DE/2024, i.e. 240 %/180 % of investment per year — these encode absolute
   yearly fees, not rates). Migrated 1:1 into `devices_DE.json` for parity, but they violate the
   sanity range a maintenance *rate* should have. The new schema has
   `fixed_operation_cost_in_euro_per_year` for exactly this; a data PR should move
   240 €/a resp. 360 €/a there and zero the rate — that would be a deliberate, visible KPI delta.
   The AT entries (0.2 / 0.15) look like the same confusion with different magnitudes (20 €/month
   comment vs 0.2 rate).
2. **Legacy `ENERGY_MANAGEMENT_SYSTEM` investment comment says "EUR/kW" but the key is absolute EUR**
   (`investment_costs_in_euro: 3500`). Migrated as absolute (per_unit = null); needs review.
3. **`opex_techno_economic_parameters` mixes carriers and units** (oil in €/l, pellets in €/t,
   diesel absurdly `128.90` €/l for DE/2018 — an obvious typo for 1.2890). Migrated 1:1 (parity),
   but the DE/2018 diesel price should be corrected in a data PR.
4. **VAT status of legacy prices unknown** (see Q5 above): all migrated energy prices and device
   costs are treated as household-final prices; `tax_and_levy_share` is only filled where a source
   exists, otherwise 0 with an EXPERT_ESTIMATE source — macroeconomic results for migrated data are
   therefore approximate until the data review.
5. **`EconomicParameters` on `SimulationParameters`**: `SimulationParameters` has a custom
   `__init__` and its JSONWizard field list does not include e.g. `country`. To stay strictly
   additive (identical `*.simulation.json` round-trips), `economic_parameters` is a plain attribute
   (default None) set via keyword arg or `set_economic_parameters()`, **not** a dataclass field. A
   follow-up decision is needed on how `*.simulation.json` should carry it (spec Q3 proposes
   serializing it there — that changes the JSON schema of simulation files).
6. **New namespaced lifecycle KPIs are written to `lifecycle_kpis.json`**, not merged into
   `all_kpis.json`, so the legacy KPI JSON stays byte-identical during the parallel phase. The spec
   (§7.3) is ambiguous on whether new names should already appear in the existing KPI collection;
   merging them in is a one-line change at cutover.
7. **`KpiEntry` gains optional `value_min`/`value_max` fields (spec §7.3)**. dataclass-wizard emits
   `null` for unset optional fields, so `all_kpis.json` gains two null keys per entry. This is the
   one visible (backward-compatible) change to a legacy artifact; if the JSON golden parity check
   diffs byte-wise it must be re-baselined once.
8. **Meter identification during the parallel phase**: the compatibility adapter maps the known
   meter classes (`ElectricityMeter`, `GasMeter`, `FuelMeter`, `HeatingMeter`) to carriers by class
   name to avoid importing component modules (import cycles). District/EMS-as-meter setups are not
   yet covered by the adapter and fall back to a postprocessing warning listing unbilled carriers.
9. **Peak billing intervals**: `BillingDeterminants` peaks are computed from the meter's power
   series in postprocessing; if `seconds_per_timestep` does not divide the billing interval the
   pre-check fails per spec §8.4. For setups with 3600 s timesteps and 15-min intervals this means
   capacity tariffs simply cannot be billed — acceptable? (Spec says fail; implemented as fail.)
10. **CO2KostAufG tier table and modernization levy parameters** are shipped as data
    (`allocation_DE_2024.json`) with values per §559/§559e BGB and CO2KostAufG as of 2024 — the spec
    itself requires a legal review pass before release (§10 Phase 5).
11. **BEG EM catalog values** (`subsidy_catalog/DE.json`): base 30 %, speed bonus 20 %, income bonus
    30 % (income ≤ 40 000 €), efficiency bonus 5 %, combined cap 70 %, eligible-cost cap
    30 000 € first unit / 15 000 € units 2–6 / 8 000 € further; §35c EStG 20 % over 3 years
    (7/7/6 %) mutually exclusive with BEG. Encoded from the Richtlinie as of 2024 — needs legal
    verification, as the spec demands.
12. **Building envelope measures (Q7) — IMPLEMENTED** (2026-07-07, user-approved design):
    eight new `ComponentType`s (WALL_INSULATION, ROOF_INSULATION, TOP_CEILING_INSULATION,
    FLOOR_INSULATION, WINDOWS, EXTERIOR_DOOR, AIR_SEALING, VENTILATION_SYSTEM) with AI-estimate
    entries for DE/IE 2026/2035, BEG EM envelope schemes (15 % + 5 % iSFP, U-value conditions)
    plus the `building.has_isfp` question, and three engine mechanics (see README §3.2b).
    Decisions taken:
    - **(a)** `energy_related_cost_share` implemented as the coupled-cost (Ohnehin-Kosten)
      mechanism but **shipped as 1.0 everywhere** — user-acknowledged as "a bit of a hack, but
      good enough to start"; the credit logic is tested via scenario overlays and activates the
      moment reviewed shares land in the data.
    - **(b)** Granularity: the eight fine classes are authoritative; RenoVisor's three envelope
      MVP types (roof/wall/floor + windows) are temporary and need to be mapped onto the finer
      classes when the translator adopts the cost engine (extends issue #13).
    - **(c)** Envelope anyway-cost threshold ~5 a, implemented as the per-entry
      `anyway_threshold_years_override` (devices keep the 2 a default).
    Additional evaluator change worth knowing: the replaced-asset check now runs *before* the
    same-class "kept" match, so like-for-like measures (new windows replacing old windows) are
    charged as investments when the register declares `replaced_by_asset_classes` — previously
    they would have been silently treated as kept.
    Still open: BEG's 60 kEUR-with-iSFP eligible-cost cap cannot be expressed (caps are not
    conditional on context fields yet; shipped cap is the 30 kEUR base), and envelope embodied
    CO2 values are rough per-m2 AI estimates.
13. **RenoVisor integration**: engine-side APIs (perspectives, existing assets, question list,
    economic parameters) are implemented and additive optional request fields are documented, but
    wiring them through `hisim/renovisor/mapping.py` was deferred to keep the translator stable —
    the translator has its own spec/test suite and should adopt the cost engine in its own PR.
    (See §10 Phase 3: "RenoVisor request schema gains ... additive, optional fields".)
14. **Attribution view (Q7b)** is implemented per-component as each consumer's kWh share of the
    carrier total, sourced from `get_component_kpi_entries()` consumption KPIs where available.
    Components without consumption KPIs simply don't appear in the attribution view.
15. **Simulated-period fraction**: simulations shorter than a year are annualized by linear
    extrapolation with a warning (spec §3.6 rule 5). Simulations *longer* than a year are not
    supported by the lifecycle engine (first simulated year is used, warning emitted) — spec is
    silent on multi-year simulations.
16. **Dynamic-tariff in-simulation cost integration** (§8.4) is implemented in postprocessing from
    the meter power series and the contract's spot series (native resolution). The optional
    `simulated_cost_in_euro` hand-off from a meter that already integrated cost during simulation
    is honored when present, but no meter currently computes it.
17. **`TariffProvider` component** publishes price signals per timestep; the price *forecast*
    publication to `SingletonSimRepository` mirrors what `generic_price_signal.py` does today
    (24 h horizon). Whether MPC should consume the new provider already in the parallel phase is a
    control-side decision (spec keeps EMS work out of scope).
18. **Heating meter / district heating carrier mapping**: `HeatingMeter` maps to DISTRICT_HEATING.
    Setups that use `HeatingMeter` for contracting-style heat delivery may need a different carrier;
    flagged in the adapter with a warning.
19. **Hydrogen price key**: legacy opex dict prices "green hydrogen gas" per kWh; the EnergyCarrier
    enum has HYDROGEN. Migrated as HYDROGEN with the legacy value; the name difference is recorded
    in the entry notes.
20a. **Latent legacy bug — battery capex size**: `advanced_battery_bslib.get_cost_capex` computes
    `size_of_energy_system = config.custom_battery_capacity_generic_in_kilowatt_hour * 1e-3`,
    i.e. it prices a 10 kWh battery as 0.01 kWh. The adapter declares the physically correct
    kWh size, so the parity report will show a deliberate, explained discrepancy for batteries
    (spec §9.7: legacy bugs are documented, not silently reproduced). Decision needed at
    cutover whether the legacy KPI keeps the bug until Phase 7. (Referenced in code as issue #21.)

21. **Energy-price field names vs native units**: the spec's `working_price_in_euro_per_kwh` /
    `emission_factor_in_kg_per_kwh` field names are kept, but for HEATING_OIL/DIESEL (liter) and
    PELLETS/WOOD_CHIPS (ton) the migrated entries carry `quantity_unit` and the "per kWh" reads
    "per quantity_unit". A data PR converting everything to true €/kWh (with documented heating
    values) would be cleaner but changes legacy-parity numbers.

22. **`scenario_cube.csv` and `scenario_evaluation`**: the export is written in the long format
    the spec prescribes so the existing `scenario_evaluation` aggregation can consume it, but no
    ingestion code was added on the `scenario_evaluation` side (that module aggregates across
    runs and should adopt the cube in its own PR — spec §4.6 "which this layer feeds, not
    replaces").

24. **Emission-factor units in migrated pellet/wood-chip price entries**: the legacy dicts price
    pellets/wood chips per ton but state their emission factors per kWh; the migration kept both
    as-is, so for these two carriers the engine multiplies tons by a per-kWh factor —
    understating CO2 by a factor of a few thousand. The new AI-estimate entries (2026/2035)
    carry per-ton factors (pellets ~175 kg/t, wood chips ~80 kg/t); the migrated pre-2026
    entries should be corrected in the same data-review PR as issue #21.

25. **AI-estimate data for DE and IE, 2026 and 2035** (added 2026-07-07 for testing): all device
    classes and carriers have entries under source id `src_ai_estimates` ("AI Estimates",
    kind EXPERT_ESTIMATE) with real min/avg/max bands. Deliberate modeling differences vs the
    migrated legacy entries, all of which exercise engine features the legacy data cannot:
    - fossil carriers carry `co2_price_exposure = 1.0` with working prices **excluding** the
      explicit CO2 price (legacy entries embed it, exposure 0) — so 2026+ basis years price
      carbon via `co2_price_paths.json`, including the new IE carbon-tax/ETS2 path;
    - electricity/gas/district/hydrogen entries have non-zero **standing charges** and gas a
      grid exit fee;
    - meter entries put the metering fee into `fixed_operation_cost_in_euro_per_year` instead
      of the bogus legacy >1 "maintenance rates" (fixes issue #1 for the new years);
    - `tax_and_levy_share` is filled with rough estimates so the macroeconomic view does
      something meaningful.
    All values are placeholders to be replaced by reviewed sources; the bands are honest
    guesses of market spreads, not survey data. Ireland has **no subsidy catalog yet** (SEAI
    grants would be the natural content) — the legacy flat shim fields carry rough SEAI-like
    shares (HP 25 %, solar 30 %, PV 20 %) so `*_net` perspectives differ from `*_gross`.

26. **Report layer (LIFECYCLE_COST_REPORT, added 2026-07-07)**: `reporting.py` +
    `report_plots.py` write `cost_summary.md`, `lifecycle_report.html` and five PNGs; the flag
    implies COMPUTE_LIFECYCLE_COSTS. Plausibility thresholds live in
    `cost_database/plausibility_checks.json` (deliberately generous — they catch unit mix-ups,
    not modelling disagreements; tighten as experience accumulates). Two notes:
    - **`UncertainValue.__sub__` semantics changed** while building the comparison view:
      slot-wise deltas can legitimately invert ordering when the subtrahend's band is wider
      (dropping a very uncertain gas bill), so the delta band is now the *envelope* of the
      three slot deltas — "minimum" reads "best-case delta", not "LOW-world delta". The
      warm-rent-neutrality flags keep their meaning ("neutral even in the worst case").
    - The HTML uses native SVG `<title>` tooltips instead of a JS hover layer — a deliberate
      trade for a dependency-free file that renders from any archived result directory.

27. **Config-mutation contamination found in a real run and fixed by ordering** (2026-07-07,
    full-year `household_heatpump_building_sizer` end-to-end): with the lifecycle engine
    running *after* COMPUTE_CAPEX, the legacy `get_cost_capex` had already written its computed
    values into the component configs (`overwrite_config_values_with_new_capex_values`), so
    adopted components' `get_cost_facts()` picked them up as "overrides" — the new engine
    inherited the legacy battery unit bug and the parity report showed vacuous zero deltas.
    Exactly the §10.0-rule-4 hazard the spec warns about. Fix: the engine now runs **before**
    the legacy opex/capex blocks (pristine configs), and the parity report is written in a
    second postprocessing step after them (`write_parity_from_stored_inputs`, reading the
    pre-captured `economic_inputs.json`). The parity report immediately became meaningful:
    Battery legacy 12.16 EUR vs new 12,159.42 EUR — the documented #20a discrepancy, now
    visible in every shadow run. Note for RenoVisor/building_sizer flows: config cost fields
    set *before* the simulation remain legitimate overrides; only post-hoc mutation is
    excluded by this ordering.

28. **EconomicContext + the economic clock** (2026-07-07): system setups can now attach an
    `EconomicContext` (existing assets, subsidy context, envelope measures, technical
    attributes per subject, tenancy data, scenario set) via
    `SimulationParameters.set_economic_context()`; the bridge merges it and the full
    perspective bundle/subsidy engine/scenario cube activate. Worked example:
    `system_setups/economic_example/`. **Semantic change**: subsidy scheme validity, the
    CO2-price-path anchor and existing-asset ages now follow the **price basis year** (the
    economic "today") instead of the possibly historical weather year — a 2021 weather year
    with 2026 economics previously found no valid 2024+ schemes. Reports gained: uncertainty
    band on the cumulative-NPV chart, actor-split section (6b), scenario tornado (9).
    Open question: should `WEATHER_YEAR != PRICE_BASIS_YEAR` emit an advisory note in the
    report header (currently only visible in the parameters line)?

29. **Report coverage pass + Ireland example** (2026-07-07): after a spec-coverage review,
    the report gained CO2 section 4b (§3.8 was previously unvisualized), the sources-used
    table (§3.10), category/subject/perspective/payer/investment/delta result tables, the
    loan amortization chart (§4.4), the subsidy composition bars + awards table (with a
    flat-shim note for countries without a catalog), the robustness summary (§4.6) and the
    KPI table section (§7.3); the `report` CLI accepts `--scenarios`. Second worked example:
    `economic_example_ireland_gas_to_heatpump.py` (two simulations, gas-kept reference vs.
    heat-pump variant, IE economics — result: −26.5 kEUR NPV, payback 5/6/7 a).
    Remaining known non-covered spec outputs (deliberate): the §8.5 dynamic-tariff
    decomposition (volume/flexibility/capacity) has no report section because no shipped
    example runs a dynamic contract yet (the synthetic contract exists; a meter-side
    cost-integration example is control-side work, issues #16/#17), and the parity report
    stays a CSV-only artifact (it is transition tooling, not a result).

30. **Actor-scoped timeline charts fixed** (2026-07-07, user-reported): `LifecycleCostResult.
    timeline` holds the FULL allocated timeline (all payers — needed for the zero-sum
    invariant and payer pivots), but the report's timeline charts (annual flows, cumulative
    NPV, loan chart, year-1 bill, PNG) plotted it unfiltered, so the tenant view showed the
    landlord's investment and the cumulative-NPV endpoint contradicted its own label. Fix:
    `scope_payer` on the result + `scoped_timeline()`; all presentation-of-"this
    perspective's flows" consumers filter by it (the actor-split payer table deliberately
    keeps the full timeline). The tables/KPIs were always correct — only the charts lied.
    Regression test: tenant chart must not contain investment flows.

31. **Per-component chart redesigned as diverging stacks** (2026-07-07, user-reported): the
    §7.4 chart previously stacked the *absolute values* of all display groups, so credits
    (residual value, subsidies, feed-in, anyway credit) were drawn as if they were costs —
    the bar showed gross activity Σ|group| and the net-NPV whisker therefore always ended
    "inside" it. Redesign (HTML SVG + PNG): costs stack right of a zero line, credits left,
    whisker + dot mark the net NPV band on the same signed axis (`net = costs - credits` is
    now visible geometry). Immediately made the envelope economics readable: wall insulation
    nets -6.5 kEUR [-26.9 | +14.5] in the German example because residual value + anyway
    credit outweigh the investment in the optimistic world. Regression test pins that
    residual-value segments render on the credit side with negative tooltips.

32. **Year-2 "residual value" drop in the German example explained + detail table added**
    (2026-07-07, user-reported): the spike is 58 kEUR of ANYWAY_COST_CREDIT — the example's
    old wall/windows/top-ceiling (1988/1993/1988) have exactly 2 years of life left at the
    2026 price basis, so §4.1 books the avoided like-for-like renovation at year 2. Correct
    behavior, but presented misleadingly: the display group was labeled just "Residual
    value" (now "Residual value & anyway credit"), and there was no way to attribute a
    timeline spike without opening the CSV. Section 3 now carries a **cash-flow detail
    table** (year x subject x category with bands, discounted values and year subtotals) per
    perspective. Two modelling notes this surfaced: (a) with the interim
    `energy_related_cost_share = 1.0`, a like-for-like envelope credit equals nearly the
    full measure cost — the "vs doing nothing" frame is only as honest as that share (user
    decision (a), issue #12); (b) the anyway credit is currently booked in *gross* views
    too, while spec §4.1 ties it to the *differential comparison* — worth a review whether
    `*_gross` perspectives should exclude it (the credit is separately reported either way).

33. **Wood chips / pellets per-ton prices**: energy prices for pellets/wood chips are stored per
    ton in the legacy dicts; converted to €/kWh at migration using the heating values from
    `PhysicsConfig` (pellets 4.9 kWh/kg — LHV 11.7 GJ/m3 at 650 kg/m3; wood chips 17.3 GJ/m3 at
    250 kg/m3). The conversion factors are recorded in the entry notes and the provenance ledger.
