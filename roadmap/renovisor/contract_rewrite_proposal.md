# Proposal: the rewritten `openapi.yaml`, seen from the simulation

**Status:** proposal for the two contract teams, 2026-09-15
**Basis:** `scripts/hisim_spec.md` (v1: the user's answers are the input, HiSim derives the physics),
the UI's later additions as reflected in v0.3, the measure catalogue, and the decision register in
`challenges.md` §9 and §12. Supersedes the in-place edits on the contract branch `hisim-alignment`;
its `REQUESTS.md` and its generated enum lists carry over.
**Scope:** the documents HiSim reads and writes. Auth, the async model, ids, `/materials`,
`packageset`, `fast-estimate` and `report.pdf` are the frontend's and the backend's and are not
touched here.

---

## 1. Four principles

**P1 — One document, passed through.** The `HomeInventoryInput` the page posts is byte-for-byte
the `home_inventory.json` the container reads, and the `PackageDefinition` is the `package.json`.
The C# service stores, hashes and forwards; it transforms nothing. Then the content hash covers
exactly what HiSim simulated, and there is no second schema to drift. Fields HiSim does not
simulate are fine inside the document: the translation report says `non_simulation` for them.

**P2 — The user's answers are the input; HiSim derives.** Categorical descriptors are the primary
fields (`walls.construction`, `walls.insulation`, `windows.type`), each with an *expert override*
beside it (`u_value_in_watt_per_m2_per_kelvin`, `area_in_m2`) that wins when present. No field asks
the homeowner for a HiSim internal (`travel_route_set`, `tabula_building_type`,
`building_heat_capacity_class` go; what the user can answer replaces them and HiSim maps).

**P3 — HiSim spelling** (C3): field names with units spelled out, enum values UPPER_SNAKE generated
from `hisim.renovisor.vocabulary` and HiSim's component enums, materials by `asp_id`, money
`_in_euro`, bands `{low, best_estimate, high}`.

**P4 — Every field has a declared consumer**, `x-consumer: simulation | translation | economics |
schedule | none`, and nothing is removed without the UI team confirming it left the form.

## 2. `HomeInventoryInput`

Structure follows v1; the field list is v1 ∪ the v0.3 additions. `[v1]` = back from v1, `[0.3]` =
kept from v0.3, `[new]` = required by the decisions, `[expert]` = behind the UI's expert disclosure.

```
location            country_code · region · eircode_or_postcode                       [0.3]
building
  dwelling_type     DETACHED_SFH SEMI_DETACHED_SFH TERRACED_SFH BUNGALOW APARTMENT OTHER   [v1]  replaces tabula_building_type; HiSim derives the TABULA type (MFH has no Irish archetype and disappears as an input)
  construction_year · conditioned_floor_area_in_m2 · storeys[v1] · number_of_apartments[0.3, expert]
  retrofit_status   UNRENOVATED USUAL_REFURB ADVANCED_REFURB                           [0.3]  the coarse chip; element rows win when filled (v1 rule)
  set_heating_temperature_in_celsius · set_cooling_temperature_in_celsius             [0.3]
  home_office_days_per_week                                                           [v1]  x-consumer translation (occupancy match)
  smart_thermostats                                                                   [v1]  x-consumer none until a control model exists
  max_thermal_building_demand_in_watt                                                 [0.3, expert]
envelope            one object per thermal element, same shape:
  roof     form PITCHED FLAT ROOM_IN_ROOF[v1] · orientation_in_degree[new, Q16] · construction · insulation · area_in_m2[expert] · u_value…[expert]
  walls    construction (e.g. CAVITY SOLID TIMBER_FRAME)[v1/Q3] · cavity_width_in_mm[new, N7] · insulation · area_in_m2 · u_value…
  floor    construction (SLAB_ON_GROUND SUSPENDED_TIMBER OVER_UNHEATED_BASEMENT OVER_HEATED_BASEMENT)[v1/Q3] · insulation · area_in_m2 · u_value…
  windows  type · glazing_panes[0.3-branch] · area_in_m2 · u_value…
  doors    type · area_in_m2 · u_value…
                    the value lists of construction / insulation / type are DEFINED WITH THE CATALOGUE OWNERS (Q31);
                    v1's Irish ids are the proposal brought to that meeting. HiSim derives the base-state U-value
                    from (construction, insulation, construction_year, country) via TABULA + the same layer composer
                    the measures use; a stated u_value wins.
occupancy
  residents_count · residents_type[] ADULT KID · residents_employment_status[] FULL_TIME HALF_TIME UNEMPLOYED EDUCATION RETIRED   [0.3]
  commuting_distance_in_km                                                            [new]  replaces travel_route_set; HiSim maps to the LPG route set
heating
  system            the 11 HeatGenerator members                                       [0.3+catalogue]
  secondary         OPEN_FIREPLACE WOOD_STOVE ELECTRIC_HEATER                          [v1]  x-consumer none until modelled
  irish_cooking_range                                                                 [v1]  x-consumer none
  heat_distribution_system  RADIATOR FLOORHEATING LOW_TEMPERATURE_RADIATOR             [0.3]
  dhw_supply        FROM_SPACE_HEATING_GENERATOR HEAT_PUMP DIRECT_ELECTRIC             [new, Q14]  replaces with_dhw_preparation
  installation_year[0.3] · seasonal_efficiency_in_percent[v1, expert] · flow_temperature_in_celsius[v1, expert] · power_in_watt[0.3, expert]
water_storage       hot_water_storage {volume_in_liter, installation_year} · domestic_hot_water_storage {…}   [0.3, expert]
photovoltaics       (block absent = none)  peak_power_in_watt[v1 kWp] · orientation SOUTH SOUTH_EAST SOUTH_WEST EAST_WEST[v1] · tilt_in_degree[expert] · installation_year · remaining_performance_in_percent[expert, none]
battery_storage     (absent = none)  capacity_in_kwh · installation_year · smart_charging[v1, none] · state_of_health_in_percent[expert, none]
solar_thermal_system (absent = none) supplies DHW_ONLY SPACE_HEATING_ONLY DHW_AND_SPACE_HEATING[new, = catalogue] · collector_area_in_m2[expert] · orientation · tilt_in_degree[expert] · installation_year
ventilation_system  (absent = natural) system MECHANICAL_EXTRACT DEMAND_CONTROLLED_EXTRACT MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY · installation_year   [F6]
air_tightness       air_change_rate_n50_per_hour[v1 measure param, expert]           [new]  x-consumer none until the infiltration field exists (Q15)
air_conditioning_system (absent = none) power_in_watt · installation_year              [F6]
vehicles[]          one list: vehicle_id · drivetrain ELECTRIC GASOLINE DIESEL · km_per_year[v1, A5] · consumption_in_kwh_per_km | consumption_in_liter_per_100km[v1 unit] · battery_capacity_in_kwh · construction_year · charging_location
                    replaces the two drivetrain lists; the drivetrain decides which consumption field applies
condition_assessment  as v0.3 (18 fields)                                              x-consumer schedule
selected_grant_schemes[]  GrantScheme                                                 x-consumer economics
```

Removed from the *input* (not from the product): `tabula_building_type`,
`building_heat_capacity_class`, `travel_route_set`, `with_dhw_preparation`, the two boolean
solar-thermal flags, the flat `envelope_details`. Each is derived by HiSim from what remains and
appears in the translation report as `defaulted` with the rule.

## 3. `PackageDefinition`

```
measures[]   {measure_id: MeasureId (33, closed), options: {option_id: value}}      Q1; stage-0 entries are a 422 (Q4); duplicates a 422
grants       selected_schemes[] GrantScheme · answers {question_id: value}          the answers are shaped by subsidy_catalog/questions_IE.json, the same
                                                                                     mechanism the German catalogue uses; x-consumer economics
schedule     preferred_start yyyy-MM · phases[] {starts_in yyyy-MM, measure_ids[]}  every measure in exactly one phase; x-consumer schedule
financing    loan_term_years · interest_rate_in_percent[new] · own_contribution_in_euro   x-consumer economics
```

Semantics written into the schema descriptions: stage k of a package is the inventory with the
measures of phases 1..k applied (Q29); a stage's start year escalates the economics and dates the
installations, the weather stays the country's dataset (Q30).

## 4. Results

- **One result per stage.** The calculation resource gains a stage index:
  `GET /homeinventories/{hi_id}/packages/{pkg_id}/stages/{k}/detailed-simulation`, k = 0 the
  as-is building. The C# side composes the roadmap from the stage results; HiSim produces the
  stage inputs (`stages` command) and one result per stage. `Package` stays the container of the
  full package; `StageResult` is new.
- **Every KPI and cost value is `{value, provenance, source[, period]}`** with provenance
  `SIMULATED | MOCKED | PARTIAL` (Q21); `Range` is `{low, best_estimate, high}` from one slot (A9).
- **KPI fields** (Q22, A14): `energy_demand_in_kilowatt_hour_per_year` (delivered, summed over
  carriers), `emissions_in_kg_co2_per_year` (operational), `self_sufficiency_in_percent` (number),
  `embodied_co2_in_kg` (new), the ten mocked fields unchanged in name.
- **Cost fields**: `investment_costs_in_euro`, `energy_costs_in_euro_per_year`,
  `maintenance_costs_in_euro_per_year`, `grant_in_euro`, `net_present_value_in_euro`,
  `monthly_net_cost_20y_in_euro`, `monthly_net_cost_10y_in_euro`, `payback_period_in_years`;
  `property_value_increase_in_percent` removed (A13). `missing[] {field, reason}` for what a
  calculation could not supply.
- **Provenance of the calculation**: `calculation_version {image_digest}` (Q26, A15),
  `weather_basis {location, station, dataset, year}` (A4), `period {start, end, fraction_of_year}`.
- **Failures**: `CalculationStatus.errors[] {reason: ReasonCode, group, path, detail}` with
  `ReasonCode` generated from `hisim.renovisor.reasons` (A16, R13.2.2); `reason: string` goes.

## 5. `Material` (for the materials team; `/materials` itself is theirs)

`id` = `asp_id`, `name`, `summary`, `thermal_conductivity_in_watt_per_meter_per_kelvin`,
`co2_footprint_in_kg_per_m3`, `co2_storage_in_kg_per_m2`, `lifespan_in_years {low, high}`,
`investment_cost_in_euro_per_m3 {IE, ES, NL}`, **`total_cost_in_euro_per_m2 {IE, ES, NL}`** (Q9),
`end_of_life[]`, `image_url`; window and door rows with `u_value_in_watt_per_m2_per_kelvin` and
`glazing_panes` (Q13).

## 6. What HiSim then does with it (so the teams see the consumer)

| Block | HiSim consumer |
|---|---|
| `building`, `envelope`, `occupancy`, `heating`, storages, PV, battery, solar thermal | the parametriser: TABULA code, weather, LPG household, config overrides on the recorded base files (steps 3–5) |
| `envelope.*.construction/insulation` | base-state U-value derivation: TABULA baseline + insulation layer through the composer (new step) |
| `measures` | the registry (step 3) |
| `schedule` | the `stages` expansion (Q29, new step) |
| `financing`, `grants`, `selected_grant_schemes` | `EconomicParameters` and the subsidy engine (new wiring; Irish catalogue is data work) |
| `condition_assessment`, `secondary`, `smart_thermostats`, `air_tightness`, `remaining_performance`, `state_of_health`, `smart_charging` | reported `non_simulation` / `ignored` until a model exists |

## 7. Open before the PR is written

1. Q31: the envelope value lists, with the catalogue owners.
2. Whether the frontend wants the vehicles as one list with a drivetrain or keeps two lists
   (P1 says the page's shape is HiSim's shape, so this is theirs to choose; one list is simpler).
3. `grants.answers`: which eligibility questions Ireland needs (`questions_IE.json`), from the
   subsidy data work.
4. The stage index in the URL versus a stage list inside one result; the backend's caching model
   decides.
