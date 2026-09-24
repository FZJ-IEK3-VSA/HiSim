# Step 6 — the result payload: KPIs and costs with provenance

**Status:** implementation specification, 2026-09-15
**Builds on:** step 5 (`calculate.py` runs the parametrised file and writes the records and the
report). This step adds `result.json` beside them.
**Decisions:** `challenges.md` §9 — Q21 (per-field provenance objects), Q22 (delivered energy and
operational CO₂ per year; embodied CO₂ separate), Q23 (full `Costs` block: envelope from the
materials table, devices from `devices_IE.json` labelled `PARTIAL`, the rest from the economics
engine), Q24 (Irish subsidy catalogue — **data task, not this step**; see §5), Q26 (image digest),
A4 (weather basis in the result), A12/R8 (mocked KPIs labelled, absent when neither computed nor
mocked), A14 (boundary and period stated), C3 (HiSim spelling in field names).
**Out of scope:** `subsidy_catalog/IE.json` (step 6b, data with sources); payback and NPV deltas,
which need the base run as well (a `compare` entry point, later); the contract PR (step 7).

Ground rules are those of step 3 §0. Unit tests `@pytest.mark.base`; the extended end-to-end test
stays `@pytest.mark.system_setups`.

---

## 1. Post-processing options `calculate` guarantees

The caller's `simulation.yaml` decides period and resolution (A18), but the payload needs three
outputs. `CalculationRunner` adds `COMPUTE_KPIS`, `WRITE_KPIS_TO_JSON` and
`COMPUTE_LIFECYCLE_COSTS` to the caller's options when absent and records in `calculation.json`
which options it added (`"options_added": [...]`). It also sets the simulation parameters'
`country` attribute from `location.country_code` so the economics engine prices against the
`_IE` data files (`hisim/economics/bridge.py` reads `simulation_parameters.country`, default `DE`).

## 2. `provenance.py` — the value wrapper (Q21)

```python
@dataclass(frozen=True)
class ProvenancedValue:
    value: Union[float, int, str, None, Dict[str, Any]]
    provenance: Provenance           # SIMULATED | MOCKED | PARTIAL (vocabulary.py)
    source: str                      # the HiSim KPI name, the engine field, the schema example, or the reason
    period: Optional[Period] = None  # {"start": ISO, "end": ISO, "fraction_of_year": float} for period-bound values
    def to_json(self) -> Dict[str, Any]
```

Every leaf of `kpis` and `costs` in `result.json` is a `ProvenancedValue`. A `Range`
(`{low, best_estimate, high}` — HiSim's slot names, C3/A9) is the `value` of one
`ProvenancedValue`, never three.

## 3. `kpis.py` — the KPI block

Read `results/all_kpis.json` (written by `WRITE_KPIS_TO_JSON`; a dict keyed by building/district
holding `KpiEntry` dicts with `name`, `unit`, `value`). One class `KpiSources` maps each contract
field to the HiSim KPI name it reads, so the mapping is a table a test can check against the
names `kpi_preparation.py` produces (grep them at test time; a renamed KPI fails the build):

| Contract field (HiSim spelling) | Source | Provenance |
|---|---|---|
| `energy_demand_in_kilowatt_hour_per_year` | delivered energy summed over carriers: electricity purchased from the grid plus every fuel the file's meters report (`GasMeter`/`FuelMeter`/`HeatingMeter` consumption KPIs; the exact names to be read off `kpi_preparation.py` and listed in `KpiSources`) — the boundary is "energy bought", Q22 | `SIMULATED` when the period is a full year; `PARTIAL` when shorter, with the value **scaled** to a year by `1 / fraction_of_year` and the period stated |
| `emissions_in_kg_co2_per_year` | `"Total CO2 emissions for simulated period"` | same rule |
| `self_sufficiency_in_percent` | `"Self-sufficiency rate of electricity"` (a number, A10) | `SIMULATED` (a rate does not scale) |
| `embodied_co2_in_kg` | Σ over insulation layers of `co2_footprint_in_kg_per_m3 × thickness × element area` from the materials table — **extend `materials_import.py`** to import the dump's `(kg CO₂-eq./unit) column X` as `co2_footprint_in_kg_per_m3` when `column Y` reads `kg CO₂-eq./m³` (regenerate the committed table); element areas from the realized record's `Building` config (`facade_area_in_m2` etc.; they are TABULA values resolved by the run) | `SIMULATED` for insulation layers; the field is **absent** when the package has no envelope measure (R8) |
| `energy_label` | none | `MOCKED`, `value: null`, source "no BER/DEAP procedure; A12" — no letter is invented |
| `disruption_days_by_level` | none | `MOCKED`; `value` = the contract schema's own `examples[0]` (`{none: 6, minor: 12, moderate: 5, major: 0}`), source "openapi.yaml Kpis.disruption_days_by_level examples[0]; A12" |
| `indoor_air_quality`, `thermal_insulation_effect`, `summer_heat_protection`, `comfort.heating`, `comfort.cooling` | none | `MOCKED`; value = the schema's `examples[0]` for that field, read from `ContractFiles.openapi()` at build time so the constants are the frontend's own and never typed in HiSim |

`fraction_of_year` comes from the simulation parameters (`(end - start) / 365.25 d`). A KPI name
that `all_kpis.json` lacks (e.g. no gas meter in a heat-pump file) contributes zero to a sum and is
listed in the `ProvenancedValue.source` as "absent: <name>"; a *required* single-source KPI that
is missing makes the field absent and is listed under `result.json["missing"]` with the reason.

## 4. `costs.py` — the `Costs` block (Q23)

Read `results/lifecycle_costs.json` and `results/lifecycle_kpis.json` (written by
`COMPUTE_LIFECYCLE_COSTS`, `hisim/economics/exports.py`). The engine evaluates perspectives; use
the owner-occupier system perspective the default bundle selects for a greenfield run (name it in
`CostSources.PERSPECTIVE_ID` after reading `hisim/economics/perspectives.py`; if the default
bundle has no single obvious choice, take the first and say so in the report).

| Contract field | Source | Provenance |
|---|---|---|
| `investment_costs_in_euro` | engine capex over the horizon's year-0 investments from `npv_by_category` / `component_breakdowns` (devices: heat pump, PV, battery, storages, boiler) **plus** the envelope material cost: Σ layers `investment_cost_in_euro_per_m3[country].{low, high} × thickness × area`, `best_estimate` = midpoint | `PARTIAL`: devices are AI estimates (`price_basis`/`source_ids: src_ai_estimates`), envelope is material-only until the total-cost column exists (Q9); the source string says both |
| `energy_costs_in_euro_per_year` | engine year-1 energy cost by carrier (`annual_energy_quantities_by_carrier` × prices, or the `ENERGY` category's first-year nominal cash flow) | `PARTIAL` (Irish prices are AI estimates) |
| `maintenance_costs_in_euro_per_year` | engine `MAINTENANCE` category, year 1 | `PARTIAL` |
| `net_present_value_in_euro` | `total_npv_in_euro` (the horizon is the engine default, 20 a; state it in `source`) | `PARTIAL` |
| `monthly_net_cost_20y_in_euro` | `equivalent_annual_cost_in_euro / 12` | `PARTIAL` |
| `monthly_net_cost_10y_in_euro` | absent unless the engine's horizon can be set per evaluation without a second full post-processing run; if it can, evaluate twice and fill it; else list under `missing` | — |
| `grant_in_euro` | the subsidy solver's result **when** `subsidy_catalog/IE.json` exists (step 6b); until then absent, `missing` reason "no Irish subsidy catalogue (Q24, step 6b)" | — |
| `payback_period_in_years` | absent; `missing` reason "needs the base calculation (compare step)" | — |
| `property_value_increase_in_percent` | absent; `missing` reason "no model (A13)" | — |

> **Note (2026-09-24, hisim-cyc.6).** Since step 10 the cost fields live in `economics_result.json`;
> `monthly_net_cost_20y_in_euro` is now `plan.totals.monthly_equivalent_cost_in_euro` there, the
> equivalent annual cost over twelve computed by the engine, rather than a division by the reader.

Envelope measures as engine subjects: the bridge accepts `extra_cost_facts` (`SubjectCostFacts`)
for non-component subjects. **Do not** feed the envelope through the engine's own `devices_IE.json`
envelope entries (Q9 forbids HiSim price estimates for envelope work); compute the envelope
material cost in `costs.py` from the materials table and add it to the engine's device capex in
the payload, with the split visible in `result.json["costs"]["investment_breakdown"]`
(`{"devices": Range, "envelope_material": Range}`). If the engine's investment total is easier to
obtain by adding envelope subjects, it is acceptable to pass a `SubjectCostFacts` whose price is
the materials-table number — the constraint is *where the number comes from*, not which code adds
it up; say which route you took.

## 5. Irish subsidy catalogue — **not this step**

`hisim/subsidy_catalog/IE.json` is data entry with sources (SEAI scheme amounts, eligibility,
validity dates) and is step 6b, done by a person or with sources at hand. This step only makes
sure the payload picks the solver's result up when the file appears: a unit test with a tiny
synthetic `IE.json` in a temporary catalogue directory proves the wiring.

## 6. `result.py` — assembling `result.json`

```json
{
  "translator_version": "...", "calculation_version": {"image_digest": "... or null"},
  "contract": {"commit": "...", "sha256": {...}},
  "base_file": "household_heatpump_building_sizer.grouped.energy_system.yaml",
  "weather_basis": {"location": "IE", "dataset": "NSRDB_15MIN", "year": 2019},
  "period": {"start": "...", "end": "...", "fraction_of_year": 0.0027},
  "kpis": { "<field>": {"value": ..., "provenance": "...", "source": "...", "period": {...}} , ... },
  "costs": { "<field>": {"value": {"low":..,"best_estimate":..,"high":..}, "provenance": "...", "source": "..."} , ...,
             "investment_breakdown": {...} },
  "missing": [ {"field": "costs.grant_in_euro", "reason": "..."} , ... ]
}
```

`weather_basis` is read from the parametrised file's `Weather` constructor and the resolved
`WeatherConfig` in the realized record (dataset and the file's year). `ResultBuilder.build(...)` is
called by `CalculationRunner` after the run; a failure inside it is a crash (`SIMULATION_FAILED`
stays for the run; add `RESULT_DERIVATION_FAILED` to `ReasonCode`, group `CRASH`) — the run's
records and report stay on disk either way. `calculation.json["output_files"]` lists
`result.json`.

## 7. The map's Trace tab

Add a fourth pane, "Result", showing the `result.json` of the committed example **if** a run
artefact is available at generation time; it is not (the map is generated offline), so render the
*schema* of the payload instead: the field list with, per field, source and expected provenance
from `KpiSources`/`CostSources`, so the frontend team sees what will arrive and which numbers are
real. Deterministic, no simulation.

## 8. Tests

- `test_renovisor_provenance.py`: JSON shape, `Range` as one value.
- `test_renovisor_kpis.py`: `KpiSources` names exist in `kpi_preparation.py` (grep the source
  text); scaling by `fraction_of_year`; mocked values equal the schema examples; absent single
  source → `missing`.
- `test_renovisor_costs.py`: envelope material cost arithmetic on one layer with the committed
  materials table; `Range` mapping from an `UncertainValue`-shaped dict; the synthetic `IE.json`
  wiring of §5; every `missing` reason present when the inputs lack the data.
- `test_renovisor_end_to_end.py` (extend): the one-day run now produces `result.json`;
  `energy_demand` and `emissions` are `PARTIAL` with `fraction_of_year` ≈ 1/365; `self_sufficiency`
  is `SIMULATED`; `embodied_co2_in_kg` present (the example package has two insulation layers);
  `investment_costs_in_euro` present with the breakdown; `grant_in_euro`, `payback_period_in_years`
  and `property_value_increase_in_percent` in `missing`. Report the wall time.

## 9. Done means

All `tests/test_renovisor_*.py` green; flake8 and mypy clean (`hisim/renovisor` and `hisim/`);
the materials table regenerated with the CO₂-per-m³ column; the map regenerated; the final report
lists: the perspective id chosen and why, the exact KPI names read, how `country` reached the
engine, which route added the envelope material cost, whether the 10-year horizon was feasible,
and every field that ended up in `missing` for the example run.
