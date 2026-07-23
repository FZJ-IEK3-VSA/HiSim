# Specification: Lifecycle Cost Calculation for HiSim (v2)

Status: DRAFT — proposal for review
Author: generated 2026-07-05, based on a review of the current CAPEX/OPEX implementation; extended with
multi-perspective costing (greenfield/brownfield, actor split, EU subsidy engine)
Revision 2026-07-05: added per-component cost breakdowns for frontend visualization (§3.7, §7.4) and
restructured the migration plan around a strictly parallel implementation with a single final cutover (§10)
Revision 2026-07-07: every cost input now carries a minimum/average/maximum uncertainty band that is
propagated slot-wise through all calculations, results and exports (§3.9); all cost data files gain
mandatory structured source metadata, and a provenance ledger traces every result value back to the
datapoints and sources that produced it, on demand (§3.10)
Revision 2026-07-07 (c): scenario sets can now overlay individual cost-database datapoints (§4.6);
CO2 price paths and escalation defaults are country-keyed data, tariff contracts declare a
jurisdiction, and investment escalation is settable per asset class (learning curves)
Revision 2026-07-07 (d): subsidy context extended by heritage-protection status and
residential/commercial usage split (with eligible-cost proration); new user-facing eligibility
questionnaire derived from the scheme conditions, with tri-state eligibility for unanswered
questions (§5.7)
Replaces: per-component `get_cost_capex()` / `get_cost_opex()` + CSV-based postprocessing aggregation
(after a fully parallel transition period, §10)

Contents:
1. Review of the current implementation
2. Goals and non-goals
3. Core engine (facts → cash flows → results)
4. Cost perspectives (greenfield, brownfield, operating, liquidity, socio-economic)
5. Subsidy engine (EU scheme modeling)
6. Actor model (owner-occupier, landlord, tenant)
7. Standard perspective bundle, outputs and KPIs
8. Dynamic electricity prices and capacity tariffs
9. Maintainability: keeping per-component verification easy and safe
10. Migration plan
11. Open questions

---

## 1. Review of the current implementation

### 1.1 How it works today

1. **Per-component methods.** Every `Component` subclass (~46 files) implements two methods
   (`hisim/component.py:425-438`):
   - `get_cost_capex(config, simulation_parameters) -> CapexCostDataClass` (static): investment cost,
     device CO2 footprint, lifetime, maintenance cost, subsidy percentage — either taken from the
     component config (all-or-nothing: all five fields set) or looked up from the hard-coded dict
     `capex_techno_economic_parameters[country][year][ComponentType]` in
     `hisim/components/configuration.py` and scaled by system size (per kW / kWh / liter / m² / unitless).
   - `get_cost_opex(all_outputs, postprocessing_results) -> OpexCostDataClass`: sums the component's own
     electricity/fuel output column, multiplies by a single-year price and emission factor from
     `opex_techno_economic_parameters[country][year]`, and adds a maintenance cost that is *derived from
     the capex data* via `calc_maintenance_cost()`.

2. **Proration to the simulated period.** `CapexComputationHelperFunctions.compute_capex_costs_and_emissions`
   (`postprocessing/cost_and_emission_computation/capex_computation.py`) converts investment into a
   "per simulated period" figure by straight-line depreciation:
   `investment / lifetime * (simulated_seconds / seconds_per_year)`.

3. **Postprocessing aggregation.** `opex_and_capex_cost_calculation.py` loops over all components, builds
   two CSV tables (`investment_cost_co2_footprint.csv`, `operational_costs_co2_footprint.csv`) with
   hard-coded subtotal rows ("Total", "Total_without_heatpump", "Total_only_heatpump"), special-casing
   heat pump classes and meter classes by `isinstance` checks.

4. **KPI computation re-reads the CSVs.** `kpi_computation/kpi_preparation.py` parses those CSV files back
   in by string-matching row and column names, and computes e.g.
   `Total costs for simulated period = maintenance + rest-investment(prorated) + energy costs`.

### 1.2 Problems

**Methodological gaps**

- **No time value of money.** There is no interest/discount rate anywhere in the codebase — no NPV, no
  annuity, no discounted payback.
- **No projection horizon.** Everything is expressed "per simulated period" (usually one year). There is
  no concept of an observation period (e.g. 20 years) over which investments, replacements and operating
  costs are projected.
- **No replacement costs.** A battery with a 10-year lifetime inside a 20-year horizon needs one
  replacement; straight-line proration silently assumes fractional replacement at original prices.
- **No residual value** at the end of the horizon, no disposal/demolition costs.
- **No price escalation.** Energy prices are a snapshot of one year.
- **No uncertainty.** Every price and cost rate is a point estimate; results pretend four-digit
  precision on inputs that are honestly ±30 %. There is no way to express, propagate or report
  cost-data uncertainty, so studies cannot publish defensible ranges.
- **One implicit perspective.** The current model computes exactly one number set — effectively a
  greenfield system-total view. Brownfield (partial replacement of an existing system), operating-only,
  owner-liquidity, and landlord/tenant views cannot be expressed.
- **Simplistic subsidies.** A single flat percentage of investment. Real EU programs have caps,
  eligibility conditions, stacking bonuses, lump sums, tax-credit schedules, reduced VAT, and
  subsidized loans.
- **Investment = device price only.** No separation of device cost, installation cost, planning cost,
  removal/disposal of replaced equipment.

**Architectural problems**

- **Massive duplication.** ~46 components each contain nearly identical `get_cost_opex` boilerplate
  (find own output column → sum → multiply by price), bugs included (matching outputs by
  `component_name` strings is flagged with `# Todo` in several files).
- **Double-counting handled ad hoc.** Component-level energy costs overlap with meter-level costs; the
  aggregation excludes meters from subtotals via `isinstance` checks, and KPI code later picks the meter
  values again.
- **Config mutation as a side effect.** `get_cost_capex` calls
  `overwrite_config_values_with_new_capex_values(config, ...)`, mutating the component config during
  postprocessing.
- **CSV round-trip as an API.** KPIs are computed by re-parsing CSV files with string-matched row/column
  names.
- **Hard-coded domain special cases.** "without_heatpump" / "only_heatpump" groupings are baked into the
  generic aggregation code via `isinstance` against four specific classes.
- **Cost data hard-coded in Python.** Prices, lifetimes and emission factors live in nested dicts in
  `configuration.py`; adding a country or year means editing source code.
- **Sources are prose, not data.** The price references live in `configuration.py` docstrings; they
  are neither machine-checkable (nothing fails when a value has no source) nor connected to results —
  given a published KPI value, there is no way to tell which prices, lifetimes and factors produced
  it, which is untenable for scientific use.
- **Costs and CO2 entangled** in the same dataclasses despite different logic.
- **Precision loss.** Intermediate values are rounded to 2 decimals before aggregation.

### 1.3 What is worth keeping

- The split between *device-dependent facts* (size, type) and *centrally maintained price data*.
- The config-override mechanism (a scenario can pin exact investment costs) — RenoVisor and
  building_sizer both use it.
- The KPI entry structure (`KpiEntry`, tags, webtool JSON) as the delivery channel.
- Emission factor tables per country/year as a data model — they just don't belong in `.py` files.

---

## 2. Goals and non-goals

### Goals

1. **Lifecycle cost analysis over a configurable horizon** (default 20 years) following the annuity
   method of **VDI 2067-1** / **DIN EN 15459-1**, and compatible with the EU cost-optimal methodology
   (**Commission Delegated Regulation (EU) 244/2012**), which distinguishes a *financial* perspective
   (with taxes and subsidies) from a *macroeconomic* perspective (without transfers, with GHG damage
   cost) — both must be producible.
2. **Multiple named cost perspectives** computed from one canonical cash-flow model:
   greenfield installation, brownfield partial replacement, operating-only, monthly owner liquidity,
   landlord view, tenant view, each with and without subsidies (§4, §6, §7).
3. **Replacements, residual values, disposal, and per-carrier price escalation** correctly modeled,
   including CO2-price trajectories (national ETS / EU ETS2).
4. **A data-driven subsidy engine** capable of representing the currently active EU residential
   energy-retrofit schemes (percentage grants with caps and stacking bonuses, lump sums, per-unit
   grants, multi-year tax credits, reduced VAT, subsidized loans, operational per-kWh support) with
   eligibility conditions and cumulation rules (§5) — including a user-facing eligibility
   questionnaire derived from the same condition data, so frontends can ask users exactly the
   friendly questions needed to determine eligibility (§5.7).
5. **An actor model** that allocates every cash flow to a payer (owner-occupier / landlord / tenant)
   via country-specific, data-driven allocation rulesets (§6).
6. **A basic financing model** (annuity loan, optionally subsidized) — required for the monthly owner
   cost and for loan-type subsidies. *(Moved here from the non-goals of the first draft.)*
7. **One central calculation engine**; components only *declare facts*, they do not compute costs.
8. **Cost and subsidy data as versioned data files**, not Python literals.
9. **Typed, in-memory result objects**; CSV/JSON are export formats, never an internal API.
10. **Differential analysis support**: comparing two variants (RenoVisor base vs. measures) is a
    first-class operation, including differential discounted payback and warm-rent neutrality.
11. Full **backward compatibility of published KPIs** during a transition phase.
12. **Per-component cost breakdowns in every perspective and every variant**, as typed results and
    exports, so the frontend can visualize what each component contributes to the total (stacked
    bars per variant, component-level diffs between variants) — §3.7, §7.4.
13. **Strictly parallel implementation.** The new engine is built, activated and validated *alongside*
    the existing cost calculation without modifying it; the legacy path remains the sole source of all
    published numbers until a single, explicit cutover at the very end (§10). Until then, every change
    to existing files is purely additive.
14. **Cost uncertainty as a first-class citizen.** Every cost input is a (minimum, average, maximum)
    triplet; the engine propagates all three values through every cash flow, perspective, KPI and
    export, so every published cost figure carries a defensible band (§3.9).
15. **Transparency and traceability for science.** Every datapoint in the cost data files carries
    mandatory, structured source metadata, and the engine can answer on demand which datapoints —
    and therefore which sources — went into any given result value (§3.10).

### Non-goals (v1 of the new engine)

- Income-tax modeling beyond what subsidy tax credits require (no depreciation-for-tax, no
  landlord income tax on rent).
- Intra-year discounting (all cash flows end-of-year; monthly figures are annual figures / 12).
- Re-dispatch or re-simulation of projection years under future price profiles: dynamic tariffs are
  fully modeled in the simulated year and projected via the decomposition of §8.5, not by simulating
  20 years of spot markets.
- Rent-market feedback (whether the market bears a modernization levy is out of scope; the levy is
  computed per the legal cap).
- Portfolio optimization across subsidy schemes over multiple years (the cumulation solver optimizes
  one investment event).
- Built-in Monte-Carlo UI (but the engine is a pure function, so parameter sweeps are cheap; §4.5
  provides the scenario hook). Deterministic min/average/max bands per §3.9 *are* in scope; sampled
  distributions are not.

---

## 3. Core engine

### 3.1 Overview

```
┌─────────────┐  declares   ┌───────────────────┐
│  Component   │───────────▶│ ComponentCostFacts │  (what am I, how big, overrides)
└─────────────┘             └────────┬──────────┘
                                     │
┌─────────────┐  measures   ┌────────▼──────────┐
│  Meters      │───────────▶│  EnergyFlowFacts   │  (kWh bought/sold per carrier, simulated year)
└─────────────┘             └────────┬──────────┘
                                     │
   cost_database/*.json   ──────────▶│◀───────── subsidy_catalog/*.json
   EconomicParameters     ──────────▶│◀───────── ApplicantProfile / BuildingContext
   PerspectiveDefinitions ──────────▶│◀───────── ExistingAssetRegister (brownfield)
                            ┌────────▼──────────┐
                            │ EconomicEvaluator  │  (postprocessing, pure function)
                            └────────┬──────────┘
                                     │
              ┌──────────────────────┼────────────────────────┐
              ▼                      ▼                        ▼
      CashFlowTimeline        EvaluationMatrix          KpiEntries / CSV / webtool JSON
      (year × category ×      {perspective →
       payer × component)      LifecycleCostResult}
```

Principles:

1. **Components declare, the engine computes.** A component provides a `ComponentCostFacts` object
   (asset class, size, unit, optional overrides). It never touches prices, discounting, or output
   dataframes.
2. **Energy costs are computed only at carrier boundaries.** The meters (electricity, gas, fuel,
   heating) already integrate what crosses the system boundary. Only these flows are priced. Component
   consumption figures are kept for *attribution* but never summed into the bill — this removes the
   double-counting problem structurally.
3. **One canonical timeline, many views.** The engine builds a single set of dated, categorized,
   payer-tagged cash flows per variant; every perspective (§4), actor view (§6) and KPI is a filter,
   allocation or discounting of that same timeline. No perspective recomputes physics or prices.
4. **The evaluator is a pure function** `(facts, flows, cost_db, subsidy_catalog, econ_params,
   perspective) -> results`. No config mutation, no file I/O inside the calculation, unit-testable
   against hand-computed VDI 2067 / EN 15459 examples.
5. **Every monetary figure is a triplet.** Cost inputs are (minimum, average, maximum) values (§3.9);
   the engine evaluates every timeline in three coherent slots, so an uncertainty band exists on
   every result by construction — never bolted on as a separate error analysis.
6. **No unsourced numbers.** Every datapoint entering a calculation is either simulation output, an
   enumerated engine default, or carries structured source metadata; the provenance ledger (§3.10)
   connects every result value back to these origins on demand.

### 3.2 Economic parameters

New dataclass, JSON-serializable, attached to `SimulationParameters` (settable from
`*.simulation.json` and the RenoVisor request):

```python
@dataclass
class EconomicParameters(JSONWizard):
    """Parameters of the lifecycle cost evaluation (annuity method, VDI 2067 / DIN EN 15459)."""

    observation_period_in_years: int = 20
    # Nominal calculation interest rate (discount rate).
    interest_rate: float = 0.03
    # General price change for maintenance/operation-related costs.
    general_price_escalation_rate: float = 0.02
    # Per-carrier nominal energy price escalation rates. Unset carriers fall back to the country's
    # `escalation_defaults_<COUNTRY>.json` (§3.5), then to `general_price_escalation_rate`.
    energy_price_escalation_rates: dict[EnergyCarrier, float] = field(default_factory=dict)
    # Escalation applied to feed-in remuneration (EEG-style tariffs are nominally fixed → 0.0).
    feed_in_escalation_rate: float = 0.0
    # Investment price change rate for replacements.
    investment_price_escalation_rate: float = 0.02
    # Per-asset-class overrides for diverging technology trajectories (PV/battery learning curves
    # falling while labor-heavy installation rises). Unset classes fall back to the country defaults
    # file (§3.5), then to `investment_price_escalation_rate`.
    investment_price_escalation_rates: dict[ComponentType, float] = field(default_factory=dict)
    # Named CO2-price trajectory (see §3.5); "none" disables explicit carbon pricing.
    co2_price_scenario: str = "central"
    # CO2 damage cost for the macroeconomic perspective (UBA recommendation ~250 EUR/t, configurable).
    co2_damage_cost_in_euro_per_ton: float = 250.0
    # Price basis year for database lookups; defaults to simulation year.
    price_basis_year: Optional[int] = None
    country: str = "DE"
    apply_subsidies: bool = True            # default for perspectives that don't override it
    cost_database_path: Optional[str] = None
    subsidy_catalog_path: Optional[str] = None
```

All rates are nominal; results are in nominal euros discounted to year 0. (Real-term calculation is
possible by supplying real rates consistently; the engine does not care, but the docs must state the
convention.)

`EnergyCarrier` is a new enum (ELECTRICITY, ELECTRICITY_FEED_IN, NATURAL_GAS, HEATING_OIL, PELLETS,
WOOD_CHIPS, DISTRICT_HEATING, HYDROGEN, DIESEL) replacing the ad-hoc use of `LoadTypes` for pricing.

### 3.3 What components provide: `ComponentCostFacts`

Replaces `get_cost_capex` / `get_cost_opex` on `Component`:

```python
@dataclass
class ComponentCostFacts:
    """Facts a component declares about itself for cost/emission evaluation. No prices."""

    asset_class: ComponentType            # key into the cost database
    size: float                           # capacity in `size_unit`
    size_unit: Units                      # KILOWATT / KWH / LITER / SQUARE_METER / ANY
    kpi_tag: Optional[KpiTagEnumClass]
    count: int = 1
    # Per-field overrides (no more all-or-nothing). Monetary overrides are UncertainValue triplets
    # (§3.9); a plain number is accepted and means exact (min = avg = max):
    investment_cost_override_in_euro: Optional[UncertainValue] = None
    installation_cost_override_in_euro: Optional[UncertainValue] = None
    lifetime_override_in_years: Optional[float] = None
    maintenance_rate_override: Optional[UncertainValue] = None
    fixed_operation_cost_override_in_euro_per_year: Optional[UncertainValue] = None
    embodied_co2_override_in_kg: Optional[float] = None
    # Provenance of the overrides (§3.10): who set them, based on what — e.g. "installer quote
    # 2026-03-14", "RenoVisor request field investmentCosts". Mandatory whenever any override is
    # set (enforced in strict mode, §9.3); recorded in the provenance ledger.
    override_source: Optional[str] = None
    # Technical attributes consumed by subsidy eligibility conditions (§5.4), e.g.
    # {"scop": 4.1, "refrigerant": "R290", "heat_source": "air"}:
    technical_attributes: dict[str, Any] = field(default_factory=dict)

class Component:
    def get_cost_facts(self) -> Optional[ComponentCostFacts]:
        """Return cost-relevant facts, or None if the component has no cost representation."""
        return None
```

Differences from today: default is **None = not part of the cost model** (controllers, weather,
occupancy simply don't override the hook); overrides are per-field; no dataframe access — the typical
implementation shrinks from ~40 lines to ~6. Energy-consumption *attribution* for KPI display stays in
`get_component_kpi_entries()` and is no longer entangled with billing.

### 3.4 What meters provide: `EnergyFlowFacts`

The meter components (`ElectricityMeter`, `GasMeter`, `FuelMeter`, `HeatingMeter`, EMS as district
meter) implement:

```python
@dataclass
class EnergyFlowFacts:
    carrier: EnergyCarrier
    energy_bought_in_kwh: float          # simulated-period total, integrated by the meter
    energy_sold_in_kwh: float = 0.0
    # Optional: cost already computed against a dynamic tariff during simulation; if set, used as the
    # year-1 cost instead of energy * static price.
    simulated_cost_in_euro: Optional[float] = None
    simulated_revenue_in_euro: Optional[float] = None
```

If a simulation has no meter for a carrier that some component consumes, postprocessing emits a
warning listing the unbilled outputs — the previously implicit double-counting rules become explicit
and checkable. For time-of-use, dynamic, and capacity tariffs, `EnergyFlowFacts` is superseded by the
richer `BillingDeterminants` (§8.4).

### 3.5 Cost database

Move `capex_techno_economic_parameters` and `opex_techno_economic_parameters` out of
`configuration.py` into data files under `hisim/cost_database/`:

```
hisim/cost_database/
    devices_DE.json         # per ComponentType, per valid-from year
    devices_AT.json
    energy_prices_DE.json   # per EnergyCarrier, per year: working price, standing charge, taxes/levies
    co2_price_paths.json    # named trajectories, keyed by country (see below)
    escalation_defaults_DE.json  # default per-carrier and per-asset-class escalation rates (§3.2)
    sources.json            # structured source registry (§3.10) — replaces the reference list
                            # currently living in configuration.py docstrings
```

Prices are maintained at **country level** deliberately: regional variation within a country (labor
costs, installer density) is expressed through the uncertainty band (§3.9) or per-request overrides,
while regionally differing *subsidies* are handled by the catalog's `jurisdiction` field (§5.2).

Every data file entry **must** reference at least one entry of the source registry (`source_ids`);
an entry without a resolvable source fails schema validation (§9.6). Registry entry schema:

```jsonc
{
  "id": "src_hp_market_survey_2024",
  "citation": "Fraunhofer ISE: Wärmepumpen-Marktanalyse 2024, Tab. 12",   // mandatory
  "url": "https://...",                       // mandatory where one exists, else null + notes
  "publication_year": 2024,                   // mandatory
  "retrieved": "2026-05-12",                  // mandatory — feeds the staleness checks (§9.6)
  "kind": "MARKET_SURVEY",                    // MARKET_SURVEY | STANDARD | STATUTE | MANUFACTURER |
                                              // LITERATURE | PROJECT_DATA | EXPERT_ESTIMATE
  "notes": "min/max span the regional installer quote spread"
}
```

`EXPERT_ESTIMATE` is an admissible kind — an honestly labeled guess is scientifically fine, a number
without provenance is not. Entries reference sources per entry (`source_ids`, mandatory) and may
refine per field (`field_sources`) when one field comes from a different source than the rest.

Device entry schema (one entry per `(component_type, valid_from_year)`):

```jsonc
{
  "component_type": "HEAT_PUMP",
  "valid_from_year": 2024,
  // Every monetary value is an uncertainty triplet (§3.9); a bare number means min = avg = max.
  "specific_investment": {"value": {"min": 1250, "avg": 1600, "max": 2100}, "per_unit": "kW"},
  // Optional economies of scale: cost = c0 * size^exponent; linear if omitted.
  "scaling_exponent": null,
  "fixed_installation_cost_in_euro": 0,
  "planning_cost_in_euro": 0,              // energy consultant, permits — subsidy-eligible in DE
  "removal_cost_in_euro": 0,               // disposal of THIS device type when replaced
  "maintenance_rate_per_year": {"min": 0.01, "avg": 0.015, "max": 0.025},
  "fixed_operation_cost_in_euro_per_year": 0,  // e.g. chimney sweep, metering service, insurance delta
  "service_life_in_years": 18,
  "embodied_co2": {"value": 165.84, "per_unit": "kW"},
  "vat_rate": 0.19,                        // prices stored NET; VAT applied per country/scheme (§5)
  "source_ids": ["src_hp_market_survey_2024"],          // mandatory, resolved against sources.json
  "field_sources": {                                     // optional per-field refinement (§3.10)
    "maintenance_rate_per_year": ["src_vdi2067_blatt1"],
    "service_life_in_years": ["src_vdi2067_blatt1"]
  }
}
```

Energy price entries become **two-part tariffs with an explicit CO2 component**:

```jsonc
{
  "carrier": "NATURAL_GAS",
  "year": 2024,
  // incl. taxes/levies, excl. explicit CO2 price; triplet spans the supplier/contract spread:
  "working_price_in_euro_per_kwh": {"min": 0.09, "avg": 0.11, "max": 0.14},
  "standing_charge_in_euro_per_year": {"min": 120, "avg": 160, "max": 220},  // Grundpreis
  "grid_exit_fee_in_euro": 250,               // one-off disconnection fee when the carrier is dropped
  "emission_factor_in_kg_per_kwh": 0.20,
  "co2_price_exposure": 1.0,                  // share of emissions subject to the CO2 price path
  "source_ids": ["src_bdew_gas_2024", "src_uba_emission_factors_2024"]   // mandatory (§3.10)
}
```

The CO2 price is **not** buried in escalation rates: `co2_price_paths.json` holds named year-by-year
trajectories **keyed by country** — carbon pricing differs per member state before EU ETS2 takes
over (e.g. `DE`: `low`/`central`/`high` covering nEHS 2024-2026 and ETS2 from 2027; `AT`: the NEHG
path into the same ETS2 segments). Shared EU-wide segments are defined once and referenced by the
national paths rather than duplicated. The engine computes
`fuel_cost_t = E * (working_price * (1+e)^t + emission_factor * co2_price(country, t))`.
This makes carbon-price sensitivity a first-class research knob and feeds the tenant/landlord CO2 cost
split (§6.3).

Lookup rules: `get_device_entry(component_type, year)` picks the entry with the greatest
`valid_from_year <= year` (today's code falls back to an arbitrary first year with only a log warning).
`EconomicParameters.cost_database_path` lets research projects swap in their own price sets.

### 3.6 Cash flow construction

For each component with cost facts, over horizon `T` (all monetary parameters enter as uncertainty
triplets; every step below is evaluated in the three slots of §3.9):

1. **Initial investment** at year 0 (whether it is actually charged depends on the installation
   context, §4.1): `I_gross = device_cost(size) + fixed_installation_cost + planning_cost`, plus
   `removal_cost` of any replaced existing asset. Subsidies enter as separate cash flows (§5.6), never
   by silently shrinking `I_gross`.
2. **Replacements** at years `n·L` while `n·L < T` (L = service life; for brownfield assets the first
   replacement is at `L − current_age`): replacement cost `I_gross · (1 + r_inv)^t`, discounted.
3. **Residual value** at year `T`: straight-line share of the last-installed unit's escalated purchase
   price, entered as negative cost (VDI 2067).
4. **Maintenance & fixed operation**: `(maintenance_rate · I_gross + fixed_operation_cost) · (1+r_gen)^t`.
5. **Energy costs** per carrier: year-1 cost from `EnergyFlowFacts` (or the meter's simulated dynamic-
   tariff cost), split into working price (escalated), standing charge (escalated with the general
   rate), and CO2-price component (from the trajectory). Feed-in revenue as negative cost with its own
   escalation. If the simulated period is shorter than one year, flows are annualized by linear
   extrapolation, explicitly and with a warning.
6. **Financing** (only if the perspective's financing plan says so, §4.4): loan disbursement, interest
   and principal flows replace the year-0 outflow.

Every entry in the timeline is:

```python
@dataclass
class CashFlowEntry:
    year: int
    amount_in_euro: UncertainValue       # nominal, LOW/AVERAGE/HIGH slots (§3.9);
                                         # sign: cost positive, revenue/subsidy negative
    category: CostCategory               # INVESTMENT | PLANNING | REMOVAL | REPLACEMENT |
                                         # RESIDUAL_VALUE | MAINTENANCE | FIXED_OPERATION |
                                         # ENERGY_WORKING | ENERGY_STANDING | ENERGY_CO2_PRICE |
                                         # FEED_IN_REVENUE | SUBSIDY | LOAN_INTEREST | LOAN_PRINCIPAL |
                                         # CO2_DAMAGE (macroeconomic only)
    subject: str                         # component name or carrier
    payer: Actor                         # assigned by the allocation ruleset, §6; SYSTEM before allocation
    subsidy_scheme_id: Optional[str]     # provenance for SUBSIDY entries
    provenance_ids: tuple[int, ...]      # interned ParameterProvenance records (§3.10) of every
                                         # datapoint that entered this amount
```

### 3.7 Result objects

```python
@dataclass
class LifecycleCostResult:
    perspective: PerspectiveId
    parameters: EconomicParameters
    # Every monetary field is an UncertainValue triplet (§3.9); .average is the headline figure.
    total_npv_in_euro: UncertainValue              # net present cost over the horizon
    equivalent_annual_cost_in_euro: UncertainValue # NPV * annuity factor — the headline KPI
    npv_by_category: dict[CostCategory, UncertainValue]
    npv_by_component: dict[str, UncertainValue]
    npv_by_payer: dict[Actor, UncertainValue]
    # Full per-subject breakdown for frontend visualization (§7.4); keys are timeline subjects
    # (component names and carriers), values sum to the perspective totals by construction.
    component_breakdowns: dict[str, ComponentCostBreakdown]
    annual_cost_series_nominal_in_euro: list[UncertainValue]  # liquidity view, year 1..T
    monthly_cost_year1_in_euro: Optional[UncertainValue]      # for liquidity perspectives (§4.4)
    levelized_cost_of_heat_in_euro_per_kwh: Optional[UncertainValue]
    timeline: CashFlowTimeline
    lifecycle_co2_result: LifecycleCo2Result             # parallel, undiscounted (§3.8)
```

Because every timeline entry carries a `subject` (§3.6), the per-component breakdown is a pure pivot
of the canonical timeline — no separate computation, no chance of diverging from the headline totals:

```python
@dataclass
class ComponentCostBreakdown:
    subject: str                                  # component name, or carrier for energy subjects
    subject_kind: SubjectKind                     # COMPONENT | CARRIER
    asset_class: Optional[ComponentType]          # None for carriers
    kpi_tag: Optional[KpiTagEnumClass]
    npv_by_category: dict[CostCategory, UncertainValue]  # discounted, signed (subsidies/residuals neg.)
    total_npv_in_euro: UncertainValue
    equivalent_annual_cost_in_euro: UncertainValue
    # Undiscounted display figures for "what does X cost to buy" views:
    investment_gross_in_euro: UncertainValue      # device + installation + planning, year 0
    subsidies_in_euro: UncertainValue             # total support received for this subject
    annual_cost_series_nominal_in_euro: list[UncertainValue]
    lifecycle_co2_in_kg: float                    # embodied + attributed operational (§3.8)
```

Variant comparison (the RenoVisor case):

```python
def compare(reference: LifecycleCostResult, variant: LifecycleCostResult) -> VariantComparison:
    """Differential NPV, differential annuity, discounted payback, warm-rent-neutrality (§6.4)."""
```

`VariantComparison` also carries `npv_delta_by_subject: dict[str, UncertainValue]`, with subjects
aligned across the two variants by `(asset_class, subject)` and explicit zero entries for subjects
present in only one variant — this feeds the "which component drives the difference" visualization
(§7.4) without name-matching heuristics in the frontend.

All deltas are computed **slot-wise** (§3.9): reference and variant are compared within the same
LOW/AVERAGE/HIGH world, so shared cost uncertainty (the same heat-pump price band appearing in both
variants) cancels instead of inflating the delta band.

The **discounted payback time** is the first year where cumulative discounted savings exceed the
differential investment (`None` if never within the horizon). It is computed per slot, yielding a
payback band (best case / expected / worst case, each independently `None`-able).

### 3.8 CO2 accounting

Separate, parallel `LifecycleCo2Result`: embodied emissions at install and each replacement (no
discounting), plus annual operational emissions per carrier with an optional per-carrier emission-factor
trend (grid electricity decarbonizes; a constant 20-year factor overstates heat pump emissions). The
CO2 *damage cost* (macroeconomic perspective) and the CO2 *price* (a real cash flow) are distinct and
must never be added together.

### 3.9 Cost uncertainty (minimum / average / maximum)

No cost input in this domain is actually known to the euro: device prices vary by installer and
region, maintenance rates are rules of thumb, energy prices vary by supplier and contract. The engine
therefore treats **every monetary input as a (minimum, average, maximum) triplet** and propagates all
three values through every calculation — uncertainty is carried from input to KPI, never estimated
after the fact.

```python
@dataclass(frozen=True)
class UncertainValue:
    """A monetary figure with an uncertainty band. Invariant: minimum <= average <= maximum."""

    average: float
    minimum: float
    maximum: float

    @staticmethod
    def exact(value: float) -> "UncertainValue":
        """Degenerate band for values that are actually certain (statutory amounts, contracts)."""
        return UncertainValue(value, value, value)
```

In every JSON schema (cost database, tariff contracts, subsidy catalog, config overrides) a **bare
number means exact** (`min = avg = max`) and `{"min": .., "avg": .., "max": ..}` declares a band;
loaders validate `min <= avg <= max` and finiteness on load. Legally fixed values (lump-sum grants,
statutory levy caps, tax rates, contracted EEG feed-in tariffs) thus stay naturally exact without a
second schema.

**What carries a band:** every monetary parameter — specific and fixed investment, installation,
planning and removal costs, maintenance rates, fixed operation costs, energy working prices and
standing charges, tariff components (markup, grid fee, taxes/levies, capacity price), feed-in
remuneration, grid exit fees, and the per-field config overrides of §3.3.

**What stays exact (v1):** physical quantities from the simulation (kWh, peaks — measured, not
estimated); the economic *rates* of §3.2 (interest, escalations — their uncertainty is explored via
scenario axes, §4.6); CO2 price paths (uncertainty already expressed as named low/central/high
trajectories); service lives and emission factors (§11 Q25).

**Propagation: three coherent worlds, not per-entry interval arithmetic.** The engine evaluates every
timeline in three parallel *slots*:

- **LOW** — the optimistic world: every cost-type parameter at its minimum, every revenue-type
  parameter (feed-in remuneration, operational support) at its *maximum*.
- **AVERAGE** — all parameters at their average value; the headline figure everywhere.
- **HIGH** — the pessimistic world: cost-type parameters at maximum, revenue-type at minimum.

Whether a parameter is cost- or revenue-type is fixed by its role (its cost category), never guessed
per entry. Within a slot everything is internally consistent: the subsidy is the scheme rate times
*that slot's* eligible cost, checked against caps at that slot's basis; maintenance is the rate band
times the slot's investment; replacements and residual values escalate the slot's purchase price.
Because every parameter's effect on total cost is monotone (caps and subsidy shares dampen but never
reverse a price increase), the slot totals are true optimistic/pessimistic envelopes of the total —
while nonlinear rules (subsidy caps, levy caps, payback crossings) still see a consistent world. A
cap may bind in HIGH but not in LOW; that is intended and is reported per slot.

Mechanically, `CashFlowEntry.amount_in_euro` carries the triplet; discounting and aggregation are
linear and act slot-wise, so all three valuations come out of a single pass at negligible extra cost.
Every monetary field of every result object (§3.7), KPI (§7.3) and export (§7.2, §7.4) is an
`UncertainValue`, and the reconciliation invariants (§6.5, §7.4) hold per slot.

**Semantics — envelope, not confidence interval.** The bounds mean "if everything comes in at the
favorable / unfavorable end": full correlation toward cheap respectively expensive. They are *not*
statistical quantiles — treating the inputs as independent random variables would produce narrower
bands. The envelope is the honest deterministic statement the input data supports; distribution-based
sampling remains a non-goal (§2) and can later be layered onto the scenario hook (§4.6) without
engine changes.

**Differences and decisions.**

- Variant deltas, payback and warm-rent neutrality are computed slot-wise (§3.7, §6.5): both variants
  are evaluated in the same world, so shared uncertainty cancels correctly. Caveat: slot-wise deltas
  are coherent scenarios, not outer envelopes of the difference — an extremal delta could occur in a
  mixed world (gas price at max *while* the heat pump comes in cheap). Cross-parameter questions of
  that kind are exactly what the scenario axes and break-even search (§4.6) are for.
- Anything the engine *chooses* — the subsidy cumulation combination (§5.4), the default tariff
  counterfactual (§8.5) — is decided once on the AVERAGE slot and then valued in all three slots, so
  LOW/AVERAGE/HIGH always describe the same physical and contractual plan. Where a different choice
  would win in another slot, the audit trail says so.

### 3.10 Provenance: from any result value back to its sources

Scientific use demands that every published number be traceable: a reviewer asking "where does this
equivalent annual cost come from" must get an answer down to the individual datapoints and their
citations, without reverse-engineering the engine. Two mechanisms provide this — mandatory source
metadata on the data side (the `sources.json` registry and `source_ids` fields of §3.5, mirrored in
the subsidy catalog and tariff contracts), and a provenance ledger on the engine side.

**The provenance ledger.** During parameter resolution (the same pass as the pre-run resolution
check, §9.3 — the lookups happen anyway), every resolved input is recorded once as an interned,
immutable record:

```python
@dataclass(frozen=True)
class ParameterProvenance:
    parameter: str                  # dotted path, e.g. "devices_DE.HEAT_PUMP@2024.specific_investment"
    value: UncertainValue | float | str
    origin: ParameterOrigin         # DATABASE_ENTRY | CONFIG_OVERRIDE | REQUEST | SCENARIO_OVERLAY |
                                    # ENGINE_DEFAULT | SIMULATION_OUTPUT
    data_file: Optional[str]        # file, entry key and valid_from_year for DATABASE_ENTRY origins
    source_ids: tuple[str, ...]     # resolved against the source registry; may be empty only for
                                    # SIMULATION_OUTPUT and ENGINE_DEFAULT origins
    detail: Optional[str]           # override_source text, request field name, scenario id, ...
```

`ORIGIN` covers everything that can feed a number: cost database and subsidy catalog entries,
per-field config overrides (with their mandatory `override_source`, §3.3), RenoVisor request fields,
scenario overlays (§4.6), engine defaults (all of which are enumerated in one documented table — a
default is a source too), and simulation outputs (kWh, peaks — their provenance is the simulation
itself). Every `CashFlowEntry` carries the ids of the records that entered its amount (§3.6);
`EconomicParameters` fields get ledger records of their own (default / `*.simulation.json` /
request / overlay).

**Lineage of any result value is then a set union, not a new mechanism.** Every result figure —
total NPV, a category NPV, a per-component breakdown cell, a KPI — is by construction a
filter/pivot/discounting of timeline entries (§3.1 principle 3), so the datapoints behind it are
exactly the union of the contributing entries' provenance records plus the discounting parameters.
The engine exposes this on demand:

```python
result.explain("npv_by_category[MAINTENANCE]") -> ProvenanceReport
result.explain("equivalent_annual_cost_in_euro") -> ProvenanceReport
```

```bash
python -m hisim.economics explain <results_dir> --value "brownfield_net/equivalent_annual_cost"
```

A `ProvenanceReport` is a tree: the value at the root, the contributing timeline entries (year,
category, subject, amount) below it, each entry's parameters below that, and at the leaves the
resolved sources with full citation, url and retrieval date. It renders as human-readable text and
as JSON. Addressing uses the result field paths (perspective / field / key), the same names as the
exports — no separate query language.

**Storage and reproducibility.** The ledger is serialized as `cost_provenance.json` next to
`economic_inputs.json` (§4.6), and every row of every export carries the ids of its timeline entries
— so any number in any CSV produced years ago can still be explained offline from the archived
result directory, with no re-run. KPI payloads for the webtool do *not* embed provenance (size);
they are addressable by (perspective, field), which `explain` resolves against the stored ledger.

**Guarantees, enforced not hoped for** (§9.6): every non-`SIMULATION_OUTPUT`,
non-`ENGINE_DEFAULT` record has at least one resolvable source id — a datapoint without a source
cannot enter a calculation without failing CI or strict-mode runs; unknown source ids and orphaned
registry entries fail schema validation; `retrieved` dates feed staleness warnings for all data
files, not just subsidy catalogs. Cost: the ledger is built once per variant in the structure pass;
scenario overlays add only records for the fields they override — negligible against the evaluation
itself.

---

## 4. Cost perspectives

A **perspective** is a named configuration of five orthogonal dimensions. The engine evaluates a set of
perspectives against the same simulation results and returns an `EvaluationMatrix`.

```python
@dataclass
class Perspective:
    id: str
    installation_context: InstallationContext    # §4.1
    actor_scope: ActorScope                      # SYSTEM | OWNER_OCCUPIER | LANDLORD | TENANT (§6)
    subsidy_mode: SubsidyMode                    # NONE | FULL | ONLY(scheme_ids) | EXCLUDE(scheme_ids)
    financing: Optional[FinancingPlan]           # §4.4; None = cash purchase
    accounting: Accounting                       # FINANCIAL | MACROECONOMIC (§4.5)
```

### 4.1 Installation contexts

**GREENFIELD** — everything is bought new at year 0. All components with cost facts contribute full
investment (device + installation + planning). This is the right frame for new construction and for
"what does this system cost in absolute terms" research comparisons.

**BROWNFIELD(register)** — the building has an existing system; only the *measures* cost money. Needs
an explicit register of what is already there:

```python
@dataclass
class ExistingAsset:
    asset_class: ComponentType
    size: float
    size_unit: Units
    installation_year: int                  # → age, remaining life, replacement schedule
    replacement_cost_override_in_euro: Optional[UncertainValue] = None   # scalar accepted = exact (§3.9)
    is_functional: bool = True              # feeds subsidy conditions (e.g. "functioning oil boiler")
```

Brownfield rules:
- Components matched to an existing asset that is *kept* cost no investment; they contribute
  maintenance, energy, and a replacement at `service_life − current_age` (like-for-like, at
  then-escalated prices).
- Components that *replace* an existing asset contribute full investment plus the old asset's
  `removal_cost`. The written-off residual book value of the replaced asset is reported separately
  (`sunk_cost_written_off_in_euro`) but excluded from decision KPIs — sunk costs must not distort the
  comparison, yet researchers want to see them.
- **Anyway-cost (Sowieso-Kosten) credit**: if the replaced asset had ≤ `anyway_threshold_years`
  (default 2) of remaining life, the differential comparison credits the avoided like-for-like
  replacement against the measure. This is the "anyway renovation" baseline of the EU cost-optimal
  methodology and the fair frame for RenoVisor's base-vs-measures question: replacing a dead boiler
  with a heat pump should be charged the *extra* cost over a new boiler, not the full heat pump price.
  The credit is reported as its own category so both gross and anyway-adjusted figures are available.

**STATUS_QUO(register)** — no measures; the existing system is kept and replaced like-for-like at end
of life. This is the natural *reference variant* for brownfield comparisons: "do nothing" still costs
money, and pretending otherwise flatters doing nothing. RenoVisor's `base` variant maps here (or to
BROWNFIELD with an empty measure list, which is equivalent).

The register comes from the scenario/RenoVisor request; for TABULA-typology buildings, defaults
(existing boiler type, age = building renovation state) can be derived by the RenoVisor mapping layer.

### 4.2 Operating-cost view

`installation_context = OPERATING_ONLY`: investment and financing categories are excluded; the view
contains energy (working + standing + CO2), maintenance, fixed operation, and a **replacement reserve**
— the sinking-fund rate that pre-funds all replacements within the horizon
(`annuity of discounted replacement costs`). This answers "what does running this system cost per
year/month, honestly including wear" — the figure a homeowner or WEG should put aside, and the German
Instandhaltungsrücklage logic.

### 4.3 Economic vs. liquidity view

Both are always derivable from the same timeline; `LifecycleCostResult` carries both:
- **Economic**: discounted NPV and equivalent annual cost — for comparing alternatives.
- **Liquidity**: nominal euros out of pocket per year (`annual_cost_series_nominal_in_euro`), divided
  by 12 for the monthly figure — for "can I afford this". Financing changes the liquidity profile
  dramatically and the NPV only via the spread between loan rate and discount rate.

### 4.4 Financing plan (needed for "monthly cost for the owners")

```python
@dataclass
class FinancingPlan:
    financed_share: float = 1.0               # of net investment after upfront subsidies
    nominal_interest_rate: float = 0.04
    term_in_years: int = 20
    type: LoanType = LoanType.ANNUITY          # ANNUITY | INTEREST_ONLY_WITH_BULLET
    # A subsidized-loan scheme (§5.3 SoftLoan) can override rate/term and add a repayment grant.
    subsidized_by_scheme_id: Optional[str] = None
```

Generates LOAN_INTEREST / LOAN_PRINCIPAL flows replacing the year-0 outflow. Replacements within the
horizon are financed per `refinance_replacements: bool` (default: paid cash / from the reserve).

### 4.5 Financial vs. macroeconomic accounting (EU 244/2012)

- **FINANCIAL**: household prices (gross of VAT and energy taxes), subsidies included per
  `subsidy_mode`. The default for owner/landlord/tenant perspectives.
- **MACROECONOMIC**: transfers removed — no subsidies, prices net of VAT and energy taxes/levies —
  and a CO2_DAMAGE flow added (`co2_damage_cost_in_euro_per_ton`). This is the standard
  socio-economic research view and mandatory for EPBD cost-optimal studies.

This requires the energy-price database to record the tax/levy share per carrier (already implied by
the two-part schema in §3.5; add `tax_and_levy_share`).

Price and rate scenarios (low/central/high escalations, interest rates, CO2 paths) are *not* a
perspective dimension; they are separate `EconomicParameters` sets, evaluated by the scenario
analysis layer (§4.6).

### 4.6 Scenario analysis

Research questions like "does the heat pump still win at 5 % interest and low gas prices?" need the
same simulation evaluated under many economic assumptions. The design splits scenario dimensions by
what they touch:

- **Economic-only dimensions** — interest rate, escalation rates, CO2-price path, subsidy mode,
  observation period, financing terms, damage cost. These do **not** change the simulated load
  profiles, so a scenario is just another call of the pure evaluator on the same facts and billing
  determinants: milliseconds each, full factorial sweeps are cheap.
- **Physics-affecting dimensions** — component sizing, retrofit measures, weather year, and any
  tariff whose price signal a controller consumed during the run. These require a new simulation and
  are *variants*, handled by the existing infrastructure (system setups, hpc_harness, and the
  `scenario_evaluation` cross-run aggregation module — which this layer feeds, not replaces).

**Cost-data uncertainty (§3.9) is a third, orthogonal mechanism**: scenario axes vary *economic
assumptions* (rates, paths) across scenarios, while the min/avg/max slots vary *cost data* within
every single evaluation. Every cell of the cube therefore already carries an uncertainty band. The
robustness summary reports extremes across scenarios × slots (worst worst-case, best best-case), and
the dominance flag is strongest in its slot-aware form: variant A beats B in every scenario *even
comparing A's HIGH slot against B's LOW slot*. Break-even search runs on the AVERAGE slot by default
and reports the LOW/HIGH crossings as a bracket around the break-even point.

The boundary is enforced, not documented-only: the run records which inputs the simulation consumed
(notably the active `TariffContract` id, §8.3). An economic scenario that overrides a consumed field
is rejected by default — rebilling a load profile that was optimized against a different tariff has
tariff-counterfactual semantics (§8.5), and the user must opt in to exactly that interpretation
(`allow_counterfactual_billing: true`) rather than get it silently.

**Scenario set definition** (data file or RenoVisor request block):

```jsonc
{
  "base": "central",                       // the EconomicParameters everything else deviates from
  "mode": "FACTORIAL",                     // FACTORIAL | ONE_AT_A_TIME
  "axes": [
    {"name": "interest", "field": "interest_rate",
     "levels": {"low": 0.01, "central": 0.03, "high": 0.05}},
    {"name": "electricity_price", "field": "energy_price_escalation_rates.ELECTRICITY",
     "levels": {"low": 0.00, "central": 0.02, "high": 0.05}},
    {"name": "co2", "field": "co2_price_scenario", "levels": {"low": "low", "high": "high"}},
    // Axes may also target individual cost-database datapoints (data overlays, see below):
    {"name": "hp_price", "field": "devices_DE.HEAT_PUMP.specific_investment",
     "levels": {"central": null,                                  // null = as shipped
                "cheap": {"min": 900, "avg": 1100, "max": 1400}}}
  ],
  // Alternatively/additionally: explicit named overlays for hand-picked storylines; overrides may
  // mix EconomicParameters fields and cost-database datapoints:
  "named_scenarios": [
    {"id": "stagnation", "overrides": {"interest_rate": 0.05,
                                        "energy_price_escalation_rates": {"ELECTRICITY": 0.0}}}
  ]
}
```

`FACTORIAL` expands the cartesian product (scenario ids like `interest=high|electricity_price=low`);
`ONE_AT_A_TIME` varies each axis from the base individually — the standard input for tornado
diagrams and much easier to interpret. Axes address `EconomicParameters` fields by dotted path,
validated against the dataclass schema at load time (unknown field = hard error, in the spirit of §9).

**Data overlays: scenario-specific cost data.** Axes and named-scenario overrides may equally
address individual **cost-database datapoints**, using a dotted path rooted at the data file stem
(`devices_DE.HEAT_PUMP.specific_investment`,
`energy_prices_DE.NATURAL_GAS.working_price_in_euro_per_kwh`; an `@year` suffix pins a specific
`valid_from_year` entry where a type has several). Overlay values are validated against the database
entry schema at load time (unknown entry or field = hard error) and may be uncertainty triplets
(§3.9). This answers "does the retrofit still win if heat pumps get 30 % cheaper?" without forking
database files: the shipped database stays the single maintained source, the overlay is a diffable
few-line change, and every overlaid value enters the provenance ledger as `SCENARIO_OVERLAY` with
its scenario id (§3.10) — an explained result names exactly which numbers were counterfactual.
Overlays touching `service_life_in_years` move replacement years and therefore invalidate the
structure-once optimization below: such scenarios rebuild their timeline structure (correct, merely
slower; the loader warns when an axis forces per-scenario rebuilds).

Two fields are **not sweepable** and rejected as axis/override targets with an explanatory error:
`cost_database_path` / `subsidy_catalog_path` (a whole-dataset swap is a run-level choice — sweep
individual datapoints via overlays instead), and `country` (a country change invalidates the
simulated physics context — building stock, weather, codes — and is a *variant*, not an economic
scenario).

**Evaluation cube.** The result is `results[variant][perspective][scenario]`, each cell a full
`LifecycleCostResult`. Implementation guidance: the event *structure* of the timeline (what is bought
when, replacement years, billing determinants) does not depend on rates — build it once per
variant/perspective, apply each scenario's escalation and discounting as a second pass. Sweeps of
10³–10⁴ scenarios must stay interactive.

**Post-hoc re-pricing without re-simulation.** All evaluator inputs — cost facts, billing
determinants, existing-asset register — are serialized into the result directory
(`economic_inputs.json`, accompanied by the provenance ledger `cost_provenance.json`, §3.10). A
standalone entry point

```bash
python -m hisim.economics evaluate <results_dir> --scenarios scenarios.json
```

re-runs the full scenario cube on stored results: new interest-rate assumptions, updated subsidy
catalogs, or a reviewer's "what if" never require re-running the building simulation. (This also
means archived study results stay re-evaluable years later.)

**Derived analyses** (all trivially built on the cube because the evaluator is pure and fast):

- **Tornado data** (`ONE_AT_A_TIME`): per KPI and axis, the swing vs. the base scenario — exported as
  a table; plotting hook in the report.
- **Break-even search**: `find_break_even(axis, kpi, variant_a, variant_b)` — bisection on one axis
  for the value where two variants' KPI difference crosses zero ("above which gas-price escalation
  does the heat pump win?", "up to which interest rate is the retrofit NPV-positive?"). Reported with
  the bracket, or "no crossing in range".
- **Robustness summary** per variant pair: min / max / spread of the differential KPI across all
  scenarios, and a dominance flag (variant A beats B in *every* scenario — the strongest statement a
  study can make).

**Exports.** `scenario_cube.csv` in long format — one row per (variant, perspective, scenario, KPI)
with the scenario axes as separate columns — deliberately shaped so the existing
`scenario_evaluation` aggregation and plotting can consume it alongside cross-run results, plus
`scenario_cube.json` with the full typed cube for the webtool.

---

## 5. Subsidy engine

### 5.1 Requirements derived from currently active EU schemes

A survey of the scheme *mechanisms* the model must express (examples, parameters as of ~2024/25, all
values live in data files and must be verifiable/updatable, not in code):

| Mechanism | Examples |
|---|---|
| % of eligible cost with cap on eligible cost | DE BEG EM: 30 % base, eligible cost ≤ 30 k€ (1st dwelling unit, self-occupied), + further units tiered |
| Stacking bonuses with a combined-rate cap | DE BEG: +20 % speed bonus (replacing functioning old fossil system, degressive over time), +30 % income bonus (taxable household income ≤ 40 k€, self-occupied), +5 % efficiency bonus (natural refrigerant / ground source); total ≤ 70 % |
| Income-class lump sums per measure | FR MaPrimeRénov': fixed € amounts per measure by household income band and region |
| Per-unit grants | Insulation subsidies in €/m²; some PV programs in €/kWp |
| Multi-year tax credits | IT Ecobonus/Bonus Casa: 50–65 % of cost as income-tax deduction spread over 10 annual installments — payout *timing* materially changes NPV |
| Reduced VAT | Zero/reduced VAT on heat pumps or renovation works (several member states); modeled as rate change on the eligible cost basis |
| Subsidized loans ± repayment grant | DE KfW programs; modeled as a FinancingPlan override (§4.4) plus optional grant |
| Operational per-kWh support | Feed-in remuneration (EEG, fixed nominal for 20 y from install), heat-generation premiums |
| Regional stacking | National + Land/regional + municipal programs with cumulation caps (incl. EU state-aid ceilings) |
| One-off lump sums | AT "Raus aus Öl und Gas" style fixed grants with regional top-ups |

### 5.2 Scheme definition (data-driven)

Schemes live in `hisim/subsidy_catalog/<COUNTRY>.json`, one file per country, versioned by validity
dates; a `catalog_snapshot_date` documents when the file was last verified. `legal_basis` and `url`
are **mandatory** fields — a scheme is a `STATUTE`-kind source in the sense of §3.10, and subsidy
cash flows enter the provenance ledger via their `subsidy_scheme_id`, so an explained result value
cites the funding directive it rests on.

```jsonc
{
  "id": "DE_BEG_EM_HP_BASE_2024",
  "jurisdiction": {"country": "DE", "region": null},        // region = NUTS code or null
  "valid_from": "2024-01-01", "valid_to": null,
  "legal_basis": "BEG EM Richtlinie 2023-12-21",
  "url": "https://...",
  "applies_to": {"asset_classes": ["HEAT_PUMP"], "measure_kinds": ["INSTALL", "REPLACE"]},
  "eligibility": { "all": [
      {"field": "applicant.actor", "op": "in", "value": ["OWNER_OCCUPIER", "LANDLORD", "WEG"]},
      {"field": "building.construction_year", "op": "<=", "value": 2019},
      // Residential program: building must be predominantly residential (mixed use prorated below)
      {"field": "building.residential_share", "op": ">=", "value": 0.5},
      // Heritage relaxation: the technical threshold drops for protected buildings
      {"any": [
          {"field": "measure.technical_attributes.scop", "op": ">=", "value": 3.0},
          {"all": [{"field": "building.heritage_status", "op": "!=", "value": "NONE"},
                    {"field": "measure.technical_attributes.scop", "op": ">=", "value": 2.7}]}
      ]}
  ]},
  "benefit": {"kind": "SHARE_OF_ELIGIBLE_COST", "rate": 0.30},
  "eligible_cost": {
      "categories": ["INVESTMENT", "PLANNING", "REMOVAL"],
      "cap_per_dwelling_unit_in_euro": [30000, 15000, 15000, 8000],  // 1st, 2nd, 3rd, further units
      "basis": "GROSS",                                              // gross or net of VAT
      "proration": "RESIDENTIAL_SHARE"   // mixed-use buildings: only the residential share of the
                                         // cost basis is eligible (NONE if the scheme funds all use)
  },
  "cumulation": {"group": "DE_BEG_EM_RATES", "combined_rate_cap": 0.70,
                  "excludes": ["DE_TAX_35C"]},                        // §35c EStG excludes BEG
  "payout": {"kind": "UPFRONT_GRANT"}                                 // year 0
}
```

Benefit kinds (tagged union):

```
SHARE_OF_ELIGIBLE_COST(rate)             BONUS_SHARE(rate)          LUMP_SUM(amount)
PER_UNIT(amount, unit)                   TAX_CREDIT(rate, years)    REDUCED_VAT(vat_rate)
SOFT_LOAN(interest_rate, term, repayment_grant_rate)                OPERATIONAL(rate_per_kwh, carrier,
                                                                                 duration_years)
```

Payout kinds map benefits to timeline entries: `UPFRONT_GRANT` (year 0, negative SUBSIDY),
`TAX_CREDIT_SCHEDULE` (negative flows years 1..N), `LOAN_TERMS` (modifies FinancingPlan),
`OPERATIONAL` (annual negative flows for its duration), `VAT_REDUCTION` (reduces the gross-up applied
to eligible cost categories).

### 5.3 Eligibility conditions

A small, data-only predicate language (`all` / `any` / `not` over `{field, op, value}` leaves;
ops: `== != < <= > >= in contains exists`). Fields resolve against a typed `SubsidyContext`:

```python
@dataclass
class ApplicantProfile:
    actor: Actor                         # OWNER_OCCUPIER | LANDLORD | WEG | TENANT
    taxable_household_income_in_euro: Optional[float]
    household_size: Optional[int]
    main_residence: bool = True
    region: Optional[str] = None         # NUTS-3 or municipality key for regional schemes

class HeritageStatus(Enum):
    NONE = "none"
    LISTED_MONUMENT = "listed_monument"          # Einzeldenkmal
    ENSEMBLE_PROTECTED = "ensemble_protected"    # Ensembleschutz
    PRESERVATION_WORTHY = "preservation_worthy"  # besonders erhaltenswerte Bausubstanz (BEG term)

@dataclass
class SubsidyBuildingContext:
    construction_year: int
    dwelling_units: int
    heated_floor_area_in_m2: float
    # Usage split — many schemes fund only (predominantly) residential buildings, run separate
    # residential/non-residential programs, and prorate eligible costs in mixed-use buildings
    # (§5.2 `proration`). `residential_share` is derived, never asked separately:
    residential_floor_area_in_m2: float
    commercial_floor_area_in_m2: float = 0.0
    # Heritage protection — schemes relax technical thresholds for protected buildings (BEG:
    # "Denkmal oder sonstige besonders erhaltenswerte Bausubstanz") or open dedicated variants:
    heritage_status: HeritageStatus = HeritageStatus.NONE
    energy_performance_class: Optional[str] = None  # for "worst-performing building" conditions (EPBD)
    existing_heating: Optional[ExistingAsset] = None  # carrier, age, is_functional → speed bonus

# measure.* resolves to the ComponentCostFacts of the subsidized measure, incl. technical_attributes.
```

No Python `eval`; the expression grammar is validated on catalog load with clear errors ("scheme
DE_BEG_EM_HP_INCOME_2024 references unknown field applicant.incom").

### 5.4 Cumulation solver

Input: the set of schemes whose eligibility passed, per measure. Rules:
- Schemes sharing a `cumulation.group` stack additively, capped by `combined_rate_cap`.
- `excludes` lists define mutual exclusivity across groups.
- An optional country-level `overall_cap` (EU state-aid ceiling) bounds total support per measure.

The solver enumerates admissible combinations (the sets are small — typically < 10 schemes per
measure) and picks the NPV-maximizing one *for the applicant*. The decision is made on the AVERAGE
uncertainty slot (§3.9); the chosen combination is then valued in all three slots, with caps checked
per slot against that slot's eligible cost — so a cap can bind in HIGH but not in LOW, and the three
results still describe one and the same funding application. Output is a `SubsidyDecision` that is
fully reported: which schemes applied, each amount (as min/avg/max), which caps were binding in which
slot, which schemes were rejected and why (failed condition, excluded by, or undetermined for lack
of an answer — §5.7), and whether a different combination would have been optimal in the LOW or
HIGH slot. This audit trail is a research
deliverable in itself and non-negotiable for trust in the results.

### 5.5 With / without grants

Because subsidies are separate timeline entries with provenance, "without grants" is a filter, not a
recomputation: `subsidy_mode = NONE` drops SUBSIDY/TAX_CREDIT/OPERATIONAL-support flows and resets
VAT/loan modifications. `ONLY([...])` / `EXCLUDE([...])` enable research questions like "what does the
income bonus alone contribute to heat pump adoption economics".

### 5.6 Sign convention and interaction with the levy (§6.4)

Subsidies never reduce `I_gross` in place — German landlord law (and clean accounting) requires the
gross investment and the subsidy to be visible separately, because the modernization levy must be
computed on cost *net of subsidies* while maintenance rates reference gross investment.

### 5.7 Eligibility questionnaire: asking users the right questions

Most eligibility conditions reference facts only the user can know — income, ownership, heritage
status, how the building is used. Because conditions are data over typed context fields (§5.3), the
set of fields any scheme can ever reference is statically enumerable. That turns "fill in this form"
into "answer exactly the questions that matter for *your* case":

**Question catalog.** Every user-answerable context field has a question entry, localized, in
`subsidy_catalog/questions_<COUNTRY>.json`:

```jsonc
{
  "field": "building.heritage_status",
  "answer_kind": "CHOICE",            // BOOLEAN | CHOICE | NUMBER(unit) | YEAR | INCOME_BAND
  "options": ["NONE", "LISTED_MONUMENT", "ENSEMBLE_PROTECTED", "PRESERVATION_WORTHY"],
  "question": {
    "de": "Steht das Gebäude unter Denkmalschutz oder gilt es als besonders erhaltenswerte Bausubstanz?",
    "en": "Is the building a listed monument or otherwise classified as worth preserving?"
  },
  "option_labels": {"de": {"NONE": "Nein", "LISTED_MONUMENT": "Ja, Einzeldenkmal", "...": "..."},
                     "en": {"...": "..."}},
  "help": {
    "de": "Bei Denkmalschutz gelten in vielen Förderprogrammen erleichterte technische Anforderungen.",
    "en": "Many subsidy schemes relax their technical requirements for heritage-protected buildings."
  }
}
```

The usage split is asked as two friendly numbers (residential and commercial floor area — or "is the
building purely residential?" as a BOOLEAN shortcut that skips the NUMBER questions);
`residential_share` is derived, never asked. Income is asked as a band (`INCOME_BAND`, aligning with
the privacy proposal of Q9), not as an amount.

**The minimal question set is computed, not curated.**

```python
def required_questions(catalog, schemes, planned_measures, known_answers) -> list[Question]
```

collects every context field referenced by the eligibility conditions, caps and proration rules of
the *candidate* schemes (pre-filtered by jurisdiction and by the asset classes of the planned
measures), drops fields that are already answered, known from the simulation/building model
(construction year, floor areas where the request supplies them), or derivable, and orders the rest
by pruning power (answers that can disqualify or qualify the most support first). Each question
carries an automatically derived "asked because" list of the schemes that reference it — so the
frontend can show *why* it asks, and the list can never go stale relative to the catalog.

**Unanswered questions are first-class (tri-state eligibility).** Unknown fields make a scheme
`UNDETERMINED(missing_fields=[...])` rather than silently ineligible. The cumulation solver applies
only `ELIGIBLE` schemes by default, but the `SubsidyDecision` additionally reports the optimistic
upper bound over the undetermined ones — "answering these 2 questions could unlock up to 9 100 €" —
which is both the honest default and the friendly nudge for progressive disclosure: the frontend can
start with zero questions and let the user decide whether the potential support is worth answering
more.

**Provenance and CI.** User answers enter the provenance ledger as `REQUEST`-origin records carrying
the question field (§3.10) — an explained subsidy flow shows which answer enabled it. The data-file
CI (§9.6) checks question coverage: every field referenced by any shipped scheme condition has a
catalog entry in every supported language, and orphaned question entries are flagged. RenoVisor
exposes the computed question list as an additive endpoint, so the frontend renders the dialogue
without duplicating any scheme knowledge.

---

## 6. Actor model (owner-occupier / landlord / tenant)

### 6.1 Actors and allocation

```python
class Actor(Enum):
    SYSTEM = "system"            # before allocation / total view
    OWNER_OCCUPIER = "owner_occupier"
    LANDLORD = "landlord"
    TENANT = "tenant"
```

After the timeline is built, an **allocation ruleset** stamps a payer on every entry (splitting entries
where a law splits them). Rulesets are country-specific modules with data-file parameters — legal
percentages and caps change, the *structure* rarely does:

```python
class AllocationRuleset(Protocol):
    def allocate(self, timeline: CashFlowTimeline, ctx: AllocationContext) -> CashFlowTimeline: ...
```

For the owner-occupied case the allocation is trivial (everything → OWNER_OCCUPIER). The rented case
ships first for Germany (`DE_2024` ruleset), as the structurally most complex EU regime:

### 6.2 German ruleset `DE_2024` (parameters configurable, defaults to be legally verified)

| Cost category | Payer | Mechanism |
|---|---|---|
| Investment, replacement, planning, removal | LANDLORD | may be partially passed on via modernization levy (§6.4) |
| Structural maintenance | LANDLORD | not apportionable |
| Heating system servicing (Wartung), chimney sweep, metering/billing service | TENANT | apportionable operating costs (BetrKV) |
| Energy: working price + standing charge | TENANT | Heizkostenverordnung pass-through |
| Energy: CO2-price component | SPLIT | CO2KostAufG tier table (§6.3) |
| Feed-in revenue | LANDLORD | (tenant-electricity models out of scope v1) |
| Subsidies | LANDLORD | reduce the levy basis |

### 6.3 CO2 cost split (CO2KostAufG)

The tenant/landlord split of the carbon-price component is a step function of the building's specific
emissions (kg CO2 / m² / a), in 10 tiers from "tenant pays 100 %" (efficient building) to
"landlord pays 95 %" (worst tier). The tier table is a data file. HiSim *simulates* the building's
emission intensity, so the split is computed from simulation output rather than assumed — a genuine
advantage over spreadsheet studies, and it responds correctly to retrofit variants (a renovation can
shift the building several tiers and thus shift who pays the carbon price).

### 6.4 Modernization levy (Modernisierungsumlage)

Parameterized model (defaults per §559/§559e BGB as of 2024 — exact rates/caps to be verified in
review):

```python
@dataclass
class ModernizationLevyParameters:
    levy_rate_per_year: float                 # e.g. 0.08 (general) / 0.10 (heating, §559e variant)
    cap_in_euro_per_m2_per_month: float       # e.g. 3.00, or 2.00 below a rent threshold
    cap_low_rent_threshold_in_euro_per_m2: float
    maintenance_deduction_share: float        # avoided-maintenance share deducted from the basis
    duration_in_years: Optional[int]          # None = permanent rent increase
```

Basis = allocatable modernization cost − subsidies − avoided-maintenance share (which links directly
to the anyway-cost logic of §4.1). Output flows: TENANT gets a positive rent-increase flow, LANDLORD
the mirroring negative (revenue).

### 6.5 Actor-level results and KPIs

From `npv_by_payer` and the nominal series per payer (all figures as min/avg/max bands, §3.9):
- **Tenant**: Δ warm rent in €/month (rent levy + apportioned operating/energy costs vs. reference
  variant), and the **warm-rent-neutrality flag** (Δ ≤ 0) — the standard German policy KPI for whether
  a retrofit burdens tenants. The flag is evaluated per slot; neutrality in the HIGH slot ("neutral
  even if everything comes in expensive") is the robust policy statement.
- **Landlord**: net position (investment − subsidies − levy revenue − unrecoverable maintenance),
  discounted payback of the landlord share (a payback band, per slot).
- **Owner-occupier**: monthly cost year 1 (liquidity, §4.3/4.4) and equivalent annual cost.

Landlord–tenant is a zero-sum reallocation of the SYSTEM view by construction; the engine asserts
`sum(payer NPVs) == system NPV` as an invariant test, per uncertainty slot.

---

## 7. Standard perspective bundle, outputs and KPIs

### 7.1 Default bundle

Shipped as data (`perspectives_default.json`), evaluated when
`PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS` is set:

| id | context | actor | subsidies | financing | accounting |
|---|---|---|---|---|---|
| `greenfield_gross` | GREENFIELD | SYSTEM | NONE | cash | FINANCIAL |
| `greenfield_net` | GREENFIELD | SYSTEM | FULL | cash | FINANCIAL |
| `brownfield_gross` | BROWNFIELD | SYSTEM | NONE | cash | FINANCIAL |
| `brownfield_net` | BROWNFIELD | SYSTEM | FULL | cash | FINANCIAL |
| `operating` | OPERATING_ONLY | SYSTEM | FULL | — | FINANCIAL |
| `owner_monthly` | BROWNFIELD | OWNER_OCCUPIER | FULL | loan (default plan) | FINANCIAL |
| `landlord` | BROWNFIELD | LANDLORD | FULL | cash | FINANCIAL |
| `tenant` | BROWNFIELD | TENANT | FULL | — | FINANCIAL |
| `macroeconomic` | BROWNFIELD | SYSTEM | (excluded by def.) | cash | MACROECONOMIC |

Scenarios choose a subset; RenoVisor requests can define additional perspectives. Greenfield rows are
skipped automatically when no `ExistingAssetRegister` is provided and vice versa.

### 7.2 Exports

All monetary figures are exported as min/avg/max (triplet objects in JSON, `*_min`/`*_avg`/`*_max`
column groups in CSV, §3.9):

- `lifecycle_costs.json` — full typed `EvaluationMatrix` incl. `SubsidyDecision` audit trails, for the
  webtool and the RenoVisor uploader.
- `component_costs.json` / `component_costs.csv` — the per-component breakdown for frontend
  visualization (§7.4).
- `cash_flow_timeline.csv` — long format: year, category, subject, payer, perspective, nominal,
  discounted (each as min/avg/max).
- `cost_provenance.json` — the provenance ledger (§3.10); rows of the CSV exports carry their
  timeline-entry ids, so every exported number stays explainable offline from the archived result
  directory.
- The legacy CSVs (`investment_cost_co2_footprint.csv`, `operational_costs_co2_footprint.csv`) keep
  being written unchanged during the parallel phase, purely as exports; nothing reads them back.

### 7.3 KPIs

Existing KPI names keep being emitted (computed from the new engine, verified by golden tests). New
KPIs are namespaced per perspective, e.g.:

- `Equivalent annual cost [EUR/a] (brownfield_net)`
- `Net present cost over 20 years [EUR] (greenfield_gross)`
- `Monthly cost year 1 [EUR/month] (owner_monthly)`
- `Warm rent change [EUR/month] (tenant)` + `Warm-rent neutral (tenant)` (bool)
- `Discounted payback vs reference [a] (brownfield_net)`
- `Total subsidies received [EUR]` + one KPI per applied scheme id
- `Levelized cost of heat [EUR/kWh] (operating)`

Every monetary KPI carries its uncertainty band: `KpiEntry` gains optional `value_min` / `value_max`
fields (an additive change — the webtool JSON stays backward compatible), with `value` itself being
the AVERAGE slot. Legacy KPI names remain plain scalars during the parallel phase and switch to
carrying bands at cutover (§10, Phase 7).

### 7.4 Per-component costs for frontend visualization

Requirement: the frontend must be able to show, for **every variant and every perspective**, what each
component contributes to the total — e.g. a stacked bar per variant (investment / replacements /
maintenance / subsidies per component), and a component-level diff between a base variant and a
measure variant ("the heat pump adds X, the dropped gas contract saves Y").

Design rules:

1. **One source, no recomputation.** The breakdown is the `subject` pivot of the canonical timeline
   (§3.6), materialized as `component_breakdowns` on every `LifecycleCostResult` (§3.7). Since every
   perspective already has its own timeline, per-component figures exist automatically *in all
   perspectives* — greenfield vs. brownfield per-component investment, per-payer allocations, with and
   without subsidies — at zero additional engine complexity.
2. **Subjects cover components *and* carriers.** Energy is billed at carrier boundaries (§3.1), so the
   electricity bill appears as a carrier subject, not smeared over consuming components. The invariant
   `sum(subject NPVs) == perspective total NPV` holds by construction — per uncertainty slot (§3.9) —
   and is asserted in tests; stacked charts always reconcile with the headline KPI, and per-subject
   min/avg/max bands render as error bars/whiskers on the stacked bars.
3. **Attribution is display-only and flagged.** An optional secondary view splits carrier costs over
   consuming components using the attribution shares from `get_component_kpi_entries()` (e.g. "the
   heat pump's share of the electricity bill"). These figures are marked `is_attribution: true` in the
   export and are never summed together with the billed carrier subjects — the double-counting
   protection of §3.1 extends into the visualization layer.
4. **Cross-variant alignment happens server-side.** The RenoVisor comparison layer aligns subjects
   across variants by `(asset_class, subject)` and emits explicit zero entries for one-sided subjects
   (§3.7 `npv_delta_by_subject`), so the frontend renders diffs without name-matching heuristics.

Exports (per run, next to `lifecycle_costs.json`):

- `component_costs.json` — `{perspective: {subject: ComponentCostBreakdown}}`, typed, consumed by the
  webtool and the RenoVisor uploader (which merges the variants' files into the cross-variant
  comparison payload).
- `component_costs.csv` — long format for ad-hoc analysis and plotting: one row per
  (perspective, subject, subject_kind, asset_class, category) with NPV, equivalent annual cost and
  year-1 nominal cost (each as min/avg/max columns), and lifecycle CO2 columns.

---

## 8. Dynamic electricity prices and capacity tariffs

### 8.1 The consistency problem

Electricity prices currently live in three disconnected places: the postprocessing dicts in
`configuration.py` (billing), the `PriceSignal` component in `generic_price_signal.py` (a dummy
providing fixed/ToU per-timestep prices and 24 h forecasts to the `SingletonSimRepository` for the MPC
controller), and nothing at all for the main rule-based EMS, which routes surplus without ever seeing
a price. If an EMS optimizes against one price signal and the cost evaluation bills against another,
the resulting "savings from smart control" are an artifact. The design rule therefore is:

> **One `TariffContract` object per carrier is the single source of truth. The in-simulation price
> provider and the postprocessing billing engine both read it; neither carries its own price data.**

This also defines the split of responsibilities: *control strategy* (how an EMS reacts to prices,
how a battery shaves peaks) is component/controller design and out of scope for this spec; this spec
guarantees that (a) the signals controllers need exist during simulation, and (b) whatever load
profile results is billed correctly and consistently.

### 8.2 Tariff contract model

Tariff contracts are data files (`hisim/cost_database/tariffs/*.json`), referenced by id from the
scenario / RenoVisor request:

```jsonc
{
  "id": "DE_DYNAMIC_SPOT_2024",
  "carrier": "ELECTRICITY",
  "jurisdiction": {"country": "DE", "region": null},   // explicit, like subsidy schemes (§5.2) —
                                                       // not an id naming convention
  "valid_from_year": 2024,
  "supply": {
    // FLAT | TIME_OF_USE | DYNAMIC
    "kind": "DYNAMIC",
    "spot_series": "epex_da_de_2024",        // reference to a price series in the database
    "formula": {"spot_factor": 1.0, "markup_in_euro_per_kwh": 0.017},
    // Non-energy components kept separate — needed for the macroeconomic view (strip taxes),
    // §14a discounts (grid fee only), and time-variable grid fees (module 3):
    "grid_fee_in_euro_per_kwh": 0.094,
    "taxes_and_levies_in_euro_per_kwh": 0.051,
    "vat_rate": 0.19
  },
  "standing_charge_in_euro_per_year": 140,
  "capacity_charge": {
    // NONE | ANNUAL_PEAK | MONTHLY_PEAK | PEAK_WINDOW (peaks counted only in high-load windows)
    "kind": "MONTHLY_PEAK",
    "price_in_euro_per_kw": 8.0,
    "billing_interval_in_minutes": 15        // peaks measured as 15-min mean power, not instantaneous
  },
  "feed_in": {
    // FIXED_TARIFF (EEG, nominally constant for its duration) | SPOT_REFERENCED (direct marketing)
    "kind": "FIXED_TARIFF", "rate_in_euro_per_kwh": 0.081, "duration_in_years": 20
  },
  "controllability_discount": {
    // §14a EnWG-style: reduced grid fee in exchange for dimmable devices; module 3 ToU grid fee
    // is expressed as a TIME_OF_USE structure on grid_fee instead.
    "kind": "NONE"
  },
  "source_ids": ["src_tariff_sheet_supplier_x_2024"]   // mandatory (§3.10); spot price series
                                                       // referenced by `spot_series` carry their own
}
```

`TIME_OF_USE` supply uses band definitions (weekday/hour masks); `DYNAMIC` references a spot price
series (e.g. EPEX day-ahead for the simulated year) shipped as versioned CSV in the cost database.
For fixed-price contracts, the tariff contract *replaces* the flat working price of §3.5's energy
price entries; the §3.5 entries remain as the default contract (`DE_DEFAULT_<year>`), so scenarios
without explicit tariffs behave as before.

### 8.3 In-simulation: the `TariffProvider` component

`generic_price_signal.py` evolves into a `TariffProvider` driven by a `TariffContract` (one instance
per carrier contract). Per timestep it outputs:

- `PricePurchase` / `PriceInjection` [EUR/kWh] — the *total* marginal price (all components summed),
  what a cost-optimizing controller should react to;
- `BillingPeriodPeakSoFar` [kW] and `CapacityChargeMarginal` [EUR/kW] — the state a peak-shaving
  strategy needs: staying under the period's existing peak is free, setting a new one costs
  `price_per_kw`. A rule-based EMS can implement meaningful peak shaving from these two signals alone,
  without internal tariff bookkeeping; an MPC gets the same information via the existing forecast
  mechanism (price forecast horizon published to `SingletonSimRepository`, as today).

Controllers consume these via normal input wiring or the sim repository; the EMS gaining a
price-reactive operating mode is a separate (worthwhile) work item outside this spec.

### 8.4 Billing determinants: what the meter must measure

Plain annual kWh is no longer a sufficient billing basis. `EnergyFlowFacts` (§3.4) is extended to:

```python
@dataclass
class BillingDeterminants:
    carrier: EnergyCarrier
    energy_bought_in_kwh: float
    energy_sold_in_kwh: float
    energy_bought_per_band_in_kwh: dict[str, float]        # ToU tariffs
    cost_integrated_in_euro: Optional[float]               # ∫ load·price dt for DYNAMIC supply,
    revenue_integrated_in_euro: Optional[float]            #   integrated at native resolution
    peak_per_billing_period_in_kw: list[float]             # mean power over the billing interval
    annual_peak_in_kw: float
```

Rules:
- **Peaks are billing-interval means** (typically 15 min), computed by resampling the meter's power
  series — never instantaneous timestep values. Validation: `seconds_per_timestep` must divide the
  billing interval, otherwise the run fails at pre-check (§9.3).
- For `DYNAMIC` supply the meter integrates cost at native resolution during postprocessing
  (load × price series), because annual kWh × average price is exactly the error dynamic tariffs
  exist to exploit.
- The billing engine is one pure function, `apply_tariff(determinants, contract) -> year-1 costs by
  category`, with a new `CostCategory.ENERGY_CAPACITY_CHARGE`. It is property-tested (e.g. a flat
  contract must reproduce `kWh × price` exactly; capacity charge is monotone in every peak).
- Uncertain tariff components (§3.9 — markup, grid fee, taxes/levies, capacity price) do **not**
  require re-integrating the load × price series per slot: the spot-indexed part is integrated once;
  additive per-kWh components shift each slot's bill by `E × Δcomponent`, the capacity charge by
  `Σ peaks × Δprice`. The spot series itself is simulation input and stays exact (§3.9).

### 8.5 Projecting a dynamic-tariff year over the horizon

Re-simulating 20 projection years under evolving spot profiles is out of scope (and spurious
precision); naively escalating the year-1 bill is wrong in the other direction, because the *value of
flexibility* has its own dynamics. Proposal — decompose the year-1 result, then escalate each part
with its own rate:

```
bill_year1 = E_bought × p̄            (volume effect: energy at the year's average price)
           − flexibility_value        (what load shifting saved vs. paying the average price)
           + standing + capacity charges
```

- The volume effect escalates with the carrier's escalation rate (§3.2) plus the CO2-price path.
- `flexibility_value` (= `E_bought × p̄ − cost_integrated`) escalates with its own
  `spread_escalation_rate` — spot price *spreads* are expected to grow with renewable shares, and this
  is precisely the research knob for "does an EMS pay off over 20 years". Default: equal to the
  carrier escalation rate (i.e. flexibility value keeps pace with the price level).
- Capacity charges escalate with a grid-fee escalation rate; feed-in per its contract kind
  (EEG: nominally fixed for its duration, then market value).

Two distinct counterfactuals answer "what does the dynamic tariff / the EMS earn", and both are
supported without new machinery:
1. **Tariff counterfactual** (free): bill the *same* simulated load profile under a flat contract —
   isolates the tariff choice given unchanged behavior. This is just `apply_tariff` with a different
   contract and becomes a standard output whenever a `DYNAMIC` contract is active.
2. **Behavioral counterfactual** (a second simulation): run the variant with a flat tariff and a
   price-blind EMS — isolates tariff + control together. This is an ordinary `VariantComparison`
   (§3.7), the same mechanism as RenoVisor base-vs-measures.

### 8.6 Interactions with the rest of the spec

- **Actor model (§6):** all supply-side categories (working, standing, capacity, CO2 component) follow
  the energy allocation rules; a capacity charge on a rented building's common heat pump is allocated
  like heating energy. Tenant-electricity/Mieterstrom remains out of scope (§11 Q13).
- **Macroeconomic view (§4.5):** strips `taxes_and_levies` and VAT from the tariff components — which
  is why the contract stores them separately.
- **§14a-style controllability discounts** reduce only the grid-fee component and presuppose that the
  simulation actually models the dimming obligation; v1 supports the discount as data, while dimming
  events in the simulation are a separate control-side work item (§11 Q19).
- **Audit (§9.5):** the cost audit lists the active tariff contract id per carrier, and for dynamic
  contracts the spot series id and the decomposition (volume effect, flexibility value, capacity
  charges) — so reviewers see at a glance what the EMS was rewarded for.

---

## 9. Maintainability: keeping per-component verification easy and safe

The current design's virtue is locality: opening `advanced_heat_pump_hplib.py` shows the cost
calculation next to the config it reads, so a reviewer can check in one place that the component "has
the proper calculations and uses the correct configuration". The new design must preserve that virtue —
but it is worth being precise about what locality actually buys today. Verifying a component currently
means reading ~40 lines of pricing math, dataframe indexing and manual unit conversion, per component,
46 times; the copies have drifted (several still carry the same `# Todo: check component name`
output-matching bug), and nothing *checks* them — verification is possible, not enforced.

The resolution: **what is component-specific stays in the component file; what is generic moves out and
becomes centrally tested.** The reviewable surface per component shrinks from ~40 lines of math to a
~6-line declaration, and a set of automatic checks makes wrong or missing declarations fail loudly
instead of relying on review discipline.

### 9.1 The declaration lives in the component file

`get_cost_facts()` is implemented in the component's own module, next to the lifecycle methods and the
config it reads:

```python
# hisim/components/advanced_heat_pump_hplib.py
def get_cost_facts(self) -> ComponentCostFacts:
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=self.config.set_thermal_output_power_in_watt.to(Units.KILOWATT).value,
        size_unit=Units.KILOWATT,
        kpi_tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        technical_attributes={"refrigerant": self.config.refrigerant},
    )
```

Everything a reviewer must verify — right asset class, right config field, right unit — fits on one
screen, in the same file as the config definition. What is no longer there is exactly the part that was
identical (and identically buggy) across all components: price lookup, escalation, discounting,
dataframe access. Sizes are converted via the typed `Quantity` helpers from `units.py` instead of
ad-hoc factors (the current heat pump code does `* 1e-3` by hand — the classic W/kW slip).

### 9.2 No silent omissions

The naive default (`return None` = no costs) has a failure mode locality never had: a *forgotten*
implementation silently drops a component from every cost result. Countermeasures:

- **Mandatory class-level declaration.** Every `Component` subclass sets
  `cost_relevance: ClassVar[CostRelevance]` with values `PRICED` (must return facts),
  `FREE_OF_COST` (controllers, weather, idealized devices — must return `None`), or `METER`
  (must provide `EnergyFlowFacts`). The base class default is `UNDECLARED`.
- **Completeness check at simulation start, not end.** During component registration, any `UNDECLARED`
  component, or a `PRICED` component whose facts don't build, aborts with a message naming the class
  and file. Strictness is configurable (`strict_cost_completeness`): hard error in CI and tests,
  downgradeable to a warning for legacy system setups during migration.

Forgetting the cost model on a new component thus fails the very first test run — today, forgetting
`get_cost_opex` merely produces a silent `NotImplementedError` swallowed by the None-filtering in
`opex_calculation()`.

### 9.3 Fail fast, before the simulation runs

- `ComponentCostFacts.__post_init__` validates locally: size finite and > 0, unit in the supported
  set, override bands valid (`0 <= min <= avg <= max`, §3.9), technical attributes JSON-serializable,
  and `override_source` present whenever any override field is set (strict mode; warning during
  migration — §3.10).
- **Pre-run resolution check**: before the timestep loop starts, the evaluator dry-resolves every
  declared fact against the cost database and subsidy catalog — device entry exists for
  country/year, the entry's `per_unit` matches the declared `size_unit`, every
  `technical_attributes` field referenced by an applicable subsidy condition is present. A missing
  database entry fails in seconds instead of after an hour of simulation. The same pass populates
  the provenance ledger (§3.10) — resolution and provenance recording are one mechanism, so the
  ledger cannot drift from what the evaluator actually used.

### 9.4 Auto-discovered contract tests

One parametrized pytest sweeps *all* `Component` subclasses with their `get_default_*` configs —
new components are covered the moment the class exists, nobody has to remember to add a cost test:

```
tests/test_cost_facts_contract.py, for every Component subclass:
  - cost_relevance is declared
  - PRICED  → facts build from the default config, pass validation, and resolve against every
              shipped cost database (DE, AT, …)
  - PRICED  → facts respond to the config: rebuild with the capacity field scaled ×2 and assert
              facts.size scales accordingly (catches hardcoded sizes and wrong config fields —
              the "uses the correct configuration" property, now machine-checked)
  - METER   → the declared EnergyCarrier has a price entry in every energy price file
```

The scaling assertion needs to know which config field is the capacity; proposal: mark it with
dataclass field metadata (`field(metadata={"capacity": True})`) — a one-line convention that is also
useful to building_sizer. The engine math itself is tested once, against hand-computed VDI 2067 /
EN 15459 examples plus property tests (all rates zero ⇒ NPV equals the plain sum; payer NPVs sum to
the system NPV; removing a subsidy never decreases cost; degenerate bands — min = avg = max on every
input — make all three slots identical; widening any input band never narrows a result band; every
total satisfies LOW ≤ AVERAGE ≤ HIGH, §3.9).

### 9.5 The cost audit report: review one table instead of 46 files

Every run emits `cost_audit.csv` (and a section in the PDF report): one row per component with asset
class, size and unit, **the origin and sources of every parameter** (ledger origin plus resolved
`source_ids`, §3.10 — config override with its `override_source` / database entry with its
`valid_from_year` / engine default), unit price and its min/avg/max band, lifetime, resulting gross
investment (as a band), and the applied subsidy schemes with amounts per slot, including which caps
bound in which slot (§3.9). The audit is the eager, tabular summary of the same ledger the `explain`
API queries on demand. This changes the verification workflow qualitatively:

- A researcher checking a study's cost inputs scans one table instead of reading 46 files.
- A config-wiring mistake (wrong field → 5000 kW heat pump) is visible as an implausible number in
  every run's audit, not buried in code.
- PRs that change cost data produce a reviewable diff of the audit file on the golden scenario suite,
  so price updates show up as explicit KPI/audit deltas rather than silent drift.

### 9.6 Data-file CI

Since prices and schemes become data, the data gets the CI treatment code used to get implicitly:

- JSON-schema validation of cost databases, subsidy catalogs and scenario sets (including
  data-overlay paths, §4.6), on load and in CI.
- **Source completeness check (§3.10)**: every data entry (device, energy price, tariff, scheme,
  CO2 path, escalation default, spot series) has at least one `source_ids` reference; every referenced id resolves
  against the source registry; every registry entry has the mandatory citation / publication_year /
  retrieved / kind fields; orphaned registry entries are flagged. An unsourced datapoint fails CI.
- **Coverage matrix check**: every `asset_class` declared by any component × every supported country
  has a device entry; every carrier used by a meter has a price entry — a new `ComponentType` without
  cost data fails CI, the counterpart of today's "new component must implement get_cost_capex".
- **Question coverage check (§5.7)**: every context field referenced by any shipped scheme's
  conditions, caps or proration rules has a question-catalog entry in every supported language;
  orphaned question entries (referencing no scheme) are flagged.
- Staleness warning when a subsidy catalog's `catalog_snapshot_date` — or any source registry
  entry's `retrieved` date backing a currently-valid data entry — is older than 12 months.
- Golden KPI tests on the scenario suite pin end-to-end numbers.

### 9.7 Per-component parity during the parallel phase

Components adopt `get_cost_facts()` one (or a few) per PR, *next to* the untouched legacy methods
(§10.0). A parity harness compares the legacy path's results against the new facts→engine path with
degenerate parameters (the Phase 1 golden setting) and asserts equality per component. Parity is
always checked against the AVERAGE slot (§3.9); as long as the migrated database entries are
degenerate (min = avg = max), all three slots coincide anyway. It runs in two
places:

- **In CI**, as part of the auto-discovered contract test (§9.4), so a PR adopting a component fails
  if its facts don't reproduce the legacy numbers.
- **In shadow mode on every run** where both paths are active: postprocessing writes
  `cost_parity_report.csv` (one row per component: legacy value, new value, delta) — so parity is
  demonstrated across the whole zoo of real scenarios and RenoVisor requests, not just the test suite.
  This report is the primary evidence for the cutover decision (§10, Phase 7).

Any discrepancy is either a migration mistake or a latent bug in the old implementation — both worth
surfacing explicitly (documented in the PR rather than silently "fixed"). The legacy methods remain
the source of all published numbers until cutover; the parity harness is the only consumer that reads
both.

---

## 10. Migration plan

The phases build on each other; each is releasable on its own.

### 10.0 Guiding constraint: strictly parallel implementation, one cutover at the end

The new cost module is implemented **fully parallel to the existing calculation, which is not touched
at all**; only after everything works (exit criteria below) is the old implementation removed, in one
explicit final phase. Concretely, the following rules bind every phase except Phase 7:

1. **New code lives in new modules** (`hisim/economics/`, `hisim/cost_database/`,
   `hisim/subsidy_catalog/`). Existing cost code — the `get_cost_capex`/`get_cost_opex` methods,
   `configuration.py` dicts, `capex_computation.py`, `opex_and_capex_cost_calculation.py`, the KPI
   CSV re-parse — is neither edited nor refactored.
2. **Changes to existing files are purely additive**: a new `PostProcessingOptions` flag
   (`COMPUTE_LIFECYCLE_COSTS`), new optional methods on `Component` with a no-op default
   (`get_cost_facts()` returning `None`), the `cost_relevance` class attribute (§9.2, metadata only,
   warning-mode during the parallel phase). No existing line of calculation logic changes.
3. **Activation is opt-in and side-effect-free.** With the flag off, behavior is bit-identical to
   today. With the flag on, the new engine runs *in addition* and writes only new files
   (`lifecycle_costs.json`, `component_costs.*`, `cash_flow_timeline.csv`, `cost_audit.csv`,
   `cost_parity_report.csv`) and new namespaced KPIs — every legacy file, KPI name and KPI value stays
   identical either way, pinned by golden tests in CI. The flag is on by default in CI (shadow mode,
   to accumulate parity evidence) and opt-in for users during the parallel phase.
4. **The new engine never calls the legacy methods** — `get_cost_capex` mutates the component config
   as a side effect, so invoking it a second time from the new path would corrupt the very
   calculation it must leave untouched. Facts come from `get_cost_facts()` where already adopted, and
   otherwise from a compatibility adapter *inside the new package* that maps known component classes
   and their configs to `ComponentCostFacts` directly. The parity harness (§9.7) compares against the
   legacy path's already-computed results read-only.
5. **The legacy path remains the sole source of published numbers** (existing KPI names, legacy CSVs,
   webtool fields consumed by RenoVisor) until Phase 7.
6. **Only Phase 7 touches or deletes legacy code**, and only after the exit criteria are met.

**Exit criteria for cutover** (checked at the start of Phase 7): all priced components have adopted
`get_cost_facts()` with green per-component parity (§9.7); `cost_parity_report.csv` shows zero
unexplained deltas across the golden scenario suite and an agreed set of real RenoVisor runs;
data-file CI (schema + coverage matrix) green; sign-off on the audited discrepancy list (legacy bugs
found by parity are documented, and it is decided per case whether the new engine reproduces or fixes
them — fixes become explicit, reviewed KPI deltas).

### 10.1 Phases

**Phase 1 — parallel core engine + data, no component changes.**
`EconomicParameters`, the `UncertainValue` triplet type and slot-wise evaluation (§3.9 — built into
the core from day one; retrofitting bands later would touch every type and export), `CostDatabase`
(data files generated 1:1 from the existing dicts in `configuration.py`, sources preserved — the
dicts themselves stay untouched; all entries start as degenerate bands, min = avg = max, so every
slot reproduces the legacy numbers), `EconomicEvaluator` with the canonical timeline, GREENFIELD
context, SYSTEM actor, flat-percentage subsidy shim (reproducing today's behavior), and the
compatibility adapter of §10.0 rule 4. The provenance ledger, `explain` API and `sources.json`
registry (§3.10) also ship in this phase: the citation lists currently in `configuration.py`
docstrings ([20], [22], …) are migrated 1:1 into registry entries during the data migration, so the
mandatory-source CI check is green from the first commit. Real min/max ranges are then filled in per
data source as data-only PRs, whose effect is visible in the audit diff (§9.5). **Golden test:** with
`observation_period = simulated period`, all rates 0, the engine reproduces today's
"per simulated period" numbers exactly — verified via the shadow-mode parity report, not by modifying
the old path. Unit tests against hand-calculated VDI 2067 / EN 15459 examples. This phase also ships
the maintenance infrastructure of §9: `cost_relevance` declarations (additive, warning-mode), the
pre-run resolution check, the auto-discovered contract test, the cost audit report, and the data-file
CI (schema + coverage matrix) — so the safety net exists *before* any component adopts the new API.

**Phase 2 — parallel KPI set + per-component breakdowns.**
The new lifecycle KPIs (§7.3) and the per-component visualization exports (§7.4) are emitted under
their new, namespaced names/files when the flag is on. Legacy KPI computation (CSV re-parse included)
keeps running unchanged and keeps owning the existing KPI names; the switch of those names to the new
engine is deferred to Phase 7. The frontend can start building the per-component visualizations
against `component_costs.json` in this phase, without waiting for the cutover.

**Phase 3 — perspectives.**
`ExistingAssetRegister`, BROWNFIELD/STATUS_QUO contexts with anyway-cost credit, OPERATING_ONLY,
liquidity view + `FinancingPlan`, MACROECONOMIC accounting, two-part tariffs + CO2 price paths in the
energy database, `VariantComparison` (incl. `npv_delta_by_subject`). RenoVisor request schema gains
`existingAssets`, `economicParameters`, `perspectives` (additive, optional fields).

**Phase 3b — tariff engine (§8).**
`TariffContract` schema + default flat contracts generated from the §3.5 price entries (behavioral
no-op, pinned by golden tests); `BillingDeterminants` on the electricity meter (band split, 15-min
peak series, native-resolution cost integration — implemented as an additional output path, the
meter's existing outputs unchanged); `apply_tariff` billing engine with property tests; a new
`TariffProvider` component (`generic_price_signal.py` stays untouched and is only deprecated/removed
in Phase 7); year-1 decomposition (volume / flexibility value / capacity) feeding the projection.
Price-reactive EMS operating modes are tracked as separate control-side work, not part of this
migration.

**Phase 3c — scenario analysis (§4.6).**
Scenario-set schema with dotted-path validation (`EconomicParameters` fields *and* cost-database
data overlays, incl. the non-sweepable-field guards), FACTORIAL / ONE_AT_A_TIME expansion, the
two-pass evaluator optimization (structure once, rates per scenario; per-scenario rebuild when an
overlay touches service lives), `economic_inputs.json`
serialization + the `python -m hisim.economics evaluate` re-pricing CLI, tornado / break-even /
robustness helpers, and the `scenario_cube.csv` export wired into `scenario_evaluation`.

**Phase 4 — subsidy engine (§5).**
Scheme schema, condition language, cumulation solver with tri-state eligibility, payout profiles;
`subsidy_catalog/DE.json` encoding BEG EM (incl. bonuses, heritage relaxations, residential-share
proration), §35c tax credit exclusion, KfW loan variant; the question catalog and
`required_questions` API with the RenoVisor questionnaire endpoint (§5.7); catalog + question
coverage validation CI.
Then AT, FR, IT catalogs as research needs dictate — the *engine* must not need changes for new
countries; if it does, the schema failed.

**Phase 5 — actor model.**
`AllocationRuleset` protocol, `DE_2024` ruleset (BetrKV apportionment, HeizKV pass-through,
CO2KostAufG tier table, modernization levy), actor KPIs, zero-sum invariant tests. Legal parameter
review before release.

**Phase 6 — additive component adoption.**
Components gain `get_cost_facts()` (+ `get_energy_flow_facts()` / `BillingDeterminants` on meters)
one (or a few) per PR, **next to the untouched legacy methods**, gated by the per-component parity
harness (§9.7). The compatibility adapter shrinks as adoption grows; when the last component is
adopted, the adapter is empty and every priced component is covered by shadow-mode parity.

**Phase 7 — cutover & removal (the only phase that touches legacy code).**
Entered only when the §10.0 exit criteria are met. In order: (1) existing KPI names switch to being
computed from `LifecycleCostResult`, values pinned by golden tests (modulo the signed-off legacy-bug
fixes); (2) the legacy CSVs remain as exports but are generated from the new engine; (3) delete
`get_cost_capex`/`get_cost_opex` and their implementations, `CapexComputationHelperFunctions` (incl.
the config-mutating overwrite), the hard-coded parameter dicts in `configuration.py`, the
`isinstance`-based heat pump special-casing, the KPI CSV re-parse path, and `generic_price_signal.py`;
(4) flip `strict_cost_completeness` to hard error everywhere; (5) update `example_component.py` and
the "Adding a new component" section of CLAUDE.md/docs so the template shows `cost_relevance` +
`get_cost_facts()` from day one. Until step (3) lands, the whole parallel period is trivially
revertable by turning the flag off.

---

## 11. Open questions (decisions needed before the respective phase)

**Phase 1**
1. Nominal vs. real rates as the documented default. Proposal: nominal (interest 3 %, general
   escalation 2 %, carrier escalations from a default table).
2. Default per-carrier escalation rates and CO2 price paths — which sources, per country? (The
   2025-2044 projection sources [33]-[35] already cited in `configuration.py` suggest existing team
   opinions for DE.) Same question for the per-asset-class investment escalation defaults
   (learning curves, §3.2) in `escalation_defaults_<COUNTRY>.json`; proposal: ship v1 with the
   per-class table empty (uniform rate applies) until reviewed sources exist.
3. `EconomicParameters` on `SimulationParameters` (serialized in `*.simulation.json`) — proposal: yes,
   for reproducibility.
4. Replacement timing convention: end-of-year everywhere (proposal), vs. EN 15459 mid-year variants.

**Phase 1 (uncertainty, §3.9)**
23. Sources and curation of the min/max bands: who defines the ranges per component type and energy
    carrier, and are they maintained as explicit values (proposal — seeded from the spread of the
    already-cited sources) or as symmetric ±x % rules? Entries without a reviewed band stay
    degenerate (min = avg = max) rather than getting an invented one.
24. Is the envelope semantics (full correlation toward cheap/expensive, §3.9) sufficient for the
    intended studies, or do some need statistical quantiles? Quantiles would require distributions
    plus sampling via the scenario layer — currently a non-goal.
25. Extending bands beyond money: service lives and emission factors carry at least as much
    uncertainty as prices. Proposal: keep them exact in v1 (a lifetime band changes the replacement
    *structure* of the timeline, not just amounts — a qualitatively bigger change) and revisit after
    Phase 3.
26. KPI surface for bands: are optional `value_min`/`value_max` fields on `KpiEntry` (§7.3) enough
    for the webtool and RenoVisor frontend, or do bounds need to be separate KPI entries for the
    existing plotting pipeline?

**Phase 1 (provenance, §3.10)**
27. Source granularity: per-entry `source_ids` mandatory with optional per-field `field_sources`
    (proposal), or per-field sources mandatory everywhere? The latter is more precise but multiplies
    curation effort across ~50 device types × countries × years.
28. Registry scope: one `sources.json` per database file vs. one global registry shared by cost
    database, subsidy catalogs and tariffs (proposal: one per directory — cost_database and
    subsidy_catalog — since they are curated by different processes and change at different rates).
29. Does the RenoVisor/webtool response need embedded provenance for end users, or is the stored
    ledger + `explain` CLI sufficient (proposal: sufficient — provenance is a researcher/reviewer
    tool; the frontend gets addressable values, not citation trees)?

**Phase 3**
5. Gross vs. net price storage in the cost database. Proposal: store net + `vat_rate` per entry;
   household perspectives gross up, macroeconomic uses net directly, VAT-reduction subsidies override
   the rate. Requires touching every price entry once during data migration.
6. Anyway-cost threshold default (proposal: 2 years remaining life) and whether the credit is on by
   default in RenoVisor comparisons (proposal: yes, with gross figures always reported alongside).
7. Building-envelope measures (insulation, windows) as `ComponentCostFacts` of the `Building`
   component with `size_unit = SQUARE_METER` and 40-50 a service life — needs cost-database entries
   and interacts with the levy's maintenance deduction.
7b. Per-component visualization (§7.4): how are the display-only attribution shares for carrier costs
    defined — per component (each consumer's kWh share of the carrier total, proposal) or aggregated
    per `KpiTag`? And does the frontend need the attribution view in v1 at all, or do billed
    carrier subjects suffice for the first visualizations?

**Phase 4**
8. Catalog curation process: who verifies scheme files and how often? Schemes change quarterly.
   Proposal: `catalog_snapshot_date` per file, a CI check that warns when > 12 months old, and scheme
   files carry `legal_basis` + `url` for auditability. Scope v1 to DE + AT, add FR/IT on demand.
9. Income data in RenoVisor requests (needed for income-dependent schemes): privacy handling — accept
   an income *band* enum rather than an amount?
10. EEG feed-in: fixed nominal remuneration for 20 years from install (proposal) vs. escalated price.
31. Question catalog curation (§5.7): who writes and reviews the localized question texts (the
    wording is user-facing *and* legally sensitive — "Denkmalschutz" vs. "erhaltenswerte
    Bausubstanz" are different legal categories a layperson may conflate), and which languages ship
    in v1 (proposal: de + en)?
32. Questionnaire default posture (§5.7): start with zero questions and progressively disclose via
    the optimistic upper bound (proposal), or ask the full computed question set upfront in the
    RenoVisor flow? Affects the frontend UX contract, not the engine.
33. Heritage scope: is `heritage_status` + condition-level relaxations enough (proposal), or do
    v1 studies need dedicated heritage *programs* (e.g. §7i EStG monument depreciation) as their own
    benefit kind? The tax-credit payout profile already exists, so this is likely catalog data, not
    engine work.

**Phase 5**
11. Which allocation rulesets in v1 beyond `DE_2024`? (AT has no modernization-levy analogue → much
    simpler ruleset; a generic `EU_SIMPLE` ruleset — landlord pays capex/maintenance, tenant pays
    energy — as fallback for other countries?)
12. Modernization levy parameters (§559 8 % vs. §559e 10 % heating variant, caps, duration) — needs a
    legal review pass; the spec deliberately keeps them as data.
13. Tenant-electricity models (Mieterstrom) for PV in rented buildings: v1 assigns feed-in revenue to
    the landlord; is that acceptable for the intended studies?

**Phase 3b (tariffs, §8)**
16. Spot price data: which series ship in the cost database (EPEX day-ahead DE/AT per simulation
    year)? Check redistribution licensing; fallback: a documented loader for user-supplied CSVs plus
    one synthetic reference profile for tests.
17. Default `spread_escalation_rate` for the flexibility value — equal to carrier escalation
    (proposal), or an explicit scenario table from an energy-system study?
18. Which counterfactual is reported by default when a dynamic contract is active: the free tariff
    counterfactual (§8.5, proposal: always), the behavioral counterfactual (opt-in second run), or
    both?
19. §14a EnWG depth: v1 = grid-fee discount as tariff data only (proposal), or require the simulation
    to model dimming events before the discount may be applied? The latter is more honest but blocks
    on control-side work.

**Phase 3c (scenario analysis, §4.6)**
20. Default shipped scenario set: which axes and levels constitute the standard
    low/central/high bundle (interest, per-carrier escalations, CO2 path), and from which sources?
    This is the public face of every study — worth agreeing on early.
21. Cube explosion control: FACTORIAL over many axes × perspectives × variants grows fast. Cap with a
    warning at N scenarios (proposal: warn > 1 000, error > 100 000), or leave unbounded?
22. Should the RenoVisor response include the full cube or only the robustness summary + tornado
    data (proposal: summary by default, full cube behind a request flag — payload size)?
30. Data-overlay scope (§4.6): overlaying device and energy-price datapoints is clearly needed;
    should overlays also be allowed on subsidy catalog entries (e.g. "BEG base rate drops to
    25 %")? Policy-change scenarios are a real research question, but the cumulation solver makes
    the interaction harder to reason about. Proposal: yes, same mechanism, with the overlay
    re-running scheme selection per scenario.

**Cross-cutting (maintainability, §9)**
14. Enforcement strictness rollout: when does `strict_cost_completeness` flip from warning to hard
    error — per system setup, or globally at cutover (Phase 7)? Proposal: error in CI from Phase 1,
    error everywhere at cutover.
15. Capacity-field convention for the contract test's scaling check: dataclass field metadata
    (`field(metadata={"capacity": True})`, proposal) vs. a `get_capacity_field_name()` classmethod on
    `ConfigBase`.
