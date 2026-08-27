# RenoVisor v0.3 contract ↔ HiSim — counted field inventory

**Companion to** `roadmap/renovisor/requirements.md` · **Date:** 2026-08-27
**Sources surveyed:** `roadmap/renovisor/openapi.yaml` (v0.3.0-draft), `hisim/components/`,
`hisim/economics/`, `hisim/energy_system/`, `hisim/postprocessing/kpi_computation/`,
`energy_systems/gas_boiler_household.energy_system.yaml`,
`roadmap/declarative_energy_systems/plan.md`.

This file holds the raw survey. The requirements document quotes only the headline numbers.

## 0. Status vocabulary

| Status | Meaning |
|---|---|
| `used` | A HiSim configuration field or energy-system file element consumes the value directly. |
| `approximated` | Consumed, but the contract's value space is larger than HiSim's; the mapping is lossy and many-to-one. |
| `blocked` | A HiSim field exists that would consume it, but the owning class has no preset/constructor yet, so no energy-system file can carry it (P4 sweep). |
| `structural` | Not a configuration value at all: it decides which components exist and how they are wired. |
| `no consumer` | Nothing in HiSim consumes it and nothing is planned to. |
| `non-simulation` | Legitimate input, but for the cost / scheduling / grant layer, not for the physics. |

## 1. `HomeInventoryInput` — 79 leaf fields

| # | Field | Status | HiSim counterpart / note |
|---|---|---|---|
| 1 | `location.country_code` | used | `weather.LocationEnum`; `IE = ("Dublin", "NSRDB_15min", …, "165308_53.37_-6.26_2019.csv")`. Also the TABULA country prefix. |
| 2 | `location.region` | no consumer | One weather station per country code. 44 `LocationEnum` entries, 1 of them Irish. |
| 3 | `location.eircode_or_postcode` | no consumer | Not used for weather, TABULA or tariffs. |
| 4 | `building_config.general.tabula_building_type` | used | TABULA code segment. IE rows: 48 `SFH`, 51 `TH`, 19 `AB`, 6 hybrid; **0 `MFH`** (124 rows total). |
| 5 | `…general.building_heat_capacity_class` | used | `BuildingConfig.building_heat_capacity_class` |
| 6 | `…general.construction_year` | used | TABULA age band (`Year1_Building`–`Year2_Building`) |
| 7 | `…general.conditioned_floor_area_m2` | used | `BuildingConfig.absolute_conditioned_floor_area_in_m2` |
| 8 | `…general.number_of_apartments` | used | `BuildingConfig.number_of_apartments` |
| 9 | `…general.set_heating_temperature_in_celsius` | used | same name |
| 10 | `…general.set_cooling_temperature_in_celsius` | used | same name |
| 11 | `…general.retrofit_status` | used | TABULA variant `.001` / `.002` / `.003` |
| 12 | `…general.max_thermal_building_demand_in_watt` | used | same name; precedence against the sizing kernel undefined (requirement A11) |
| 13–22 | `…envelope_details.*` (10 fields) | used | `BuildingConfig.{floor,facade,roof,window,door}_{u_value_in_watt_per_m2_per_kelvin,area_in_m2}` — **identical names** |
| 23 | `occupancy_config.residents_count` | approximated | one of 66 `utspclient.helpers.lpgdata.Households` entries |
| 24 | `occupancy_config.residents_type[]` | approximated | same; `{adult, kid}` does not address the catalogue |
| 25 | `occupancy_config.residents_employment_status[]` | approximated | same; 5 statuses × n residents ≫ 66 catalogue entries |
| 26 | `occupancy_config.travel_route_set` | used | `TravelRouteSets`, exactly the 6 values the contract lists |
| 27 | `energy_system_config.heating_system.system` | used | `hisim.loadtypes.HeatingSystems` — the contract's 10 values *are* the enum's 10 values |
| 28 | `…heating_system.heatpump_flow_temperature_in_celsius` | blocked | `more_advanced_heat_pump_hplib` unconverted; the real knob sits on the HDS controller |
| 29 | `…heating_system.power_in_watt` | used | `GenericBoilerConfig` power; precedence against `AUTO` undefined (A11) |
| 30 | `…heating_system.installation_year` | non-simulation | no ageing model; cost/replacement input |
| 31 | `…heating_system.with_dhw_preparation` | blocked | needs `simple_water_storage.SimpleDHWStorage` (unconverted) |
| 32 | `…heating_system.heat_distribution_system` | used | `lt.HeatDistributionSystemType`: `"Floorheating"`, `"Conventional Radiator"`, `"Low Temperature Radiator"` — exact string match |
| 33 | `…water_storage.hot_water_storage.volume_in_liters` | blocked | `SimpleHotWaterStorage` unconverted |
| 34 | `…hot_water_storage.installation_year` | non-simulation | |
| 35 | `…domestic_hot_water_storage.volume_in_liters` | blocked | `SimpleDHWStorage` unconverted |
| 36 | `…domestic_hot_water_storage.installation_year` | non-simulation | |
| 37 | `…photovoltaics.power_in_watt` | blocked | `PVSystemConfig.power_in_watt` (module unconverted) |
| 38 | `…photovoltaics.azimuth_in_degree` | blocked | `PVSystemConfig.azimuth` |
| 39 | `…photovoltaics.tilt_in_degree` | blocked | `PVSystemConfig.tilt` |
| 40 | `…photovoltaics.installation_year` | non-simulation | |
| 41 | `…photovoltaics.remaining_performance_in_percent` | no consumer | no degradation model; `share_of_maximum_pv_potential` is a *sizing* share, not a derate |
| 42 | `…battery_storage.capacity_in_kwh` | blocked | `BatteryConfig.custom_battery_capacity_generic_in_kilowatt_hour` |
| 43 | `…battery_storage.power_in_watt` | no consumer | only `custom_pv_inverter_power_generic_in_watt` (the inverter) exists |
| 44 | `…battery_storage.installation_year` | non-simulation | |
| 45 | `…battery_storage.state_of_health_in_percent` | no consumer | no capacity-fade model |
| 46 | `…solar_thermal_system.collector_area_in_m2` | blocked | `SolarThermalSystemConfig.area_m2` — **name mismatch** |
| 47 | `…solar_thermal_system.azimuth_in_degree` | blocked | `.azimuth` |
| 48 | `…solar_thermal_system.tilt_in_degree` | blocked | `.tilt` |
| 49 | `…solar_thermal_system.used_for_space_heating` | structural | decides which storage the collector feeds |
| 50 | `…solar_thermal_system.used_for_dhw` | structural | same |
| 51 | `…solar_thermal_system.installation_year` | non-simulation | |
| 52 | `…electric_vehicles[].model` | no consumer | |
| 53 | `…electric_vehicles[].consumption_in_kwh_per_km` | blocked | `CarConfig.consumption_per_km` |
| 54 | `…electric_vehicles[].battery_capacity_in_kwh` | blocked | `advanced_ev_battery_bslib` |
| 55 | `…electric_vehicles[].construction_year` | non-simulation | |
| 56 | `…electric_vehicles[].charging_location` | no consumer | the setups charge at home only |
| 57 | `…fossil_vehicles[].fuel_source` | blocked | `CarConfig.fuel` (`lt.LoadTypes`) |
| 58 | `…fossil_vehicles[].model` | no consumer | |
| 59 | `…fossil_vehicles[].consumption_in_liter_per_km` | blocked | `CarConfig.consumption_per_km` |
| 60 | `…fossil_vehicles[].construction_year` | non-simulation | |
| 61–69 | `condition_assessment.building_elements.*` (9) | non-simulation | scheduling / replacement layer |
| 70–78 | `condition_assessment.energy_systems.*` (9) | non-simulation | scheduling / replacement layer |
| 79 | `selected_grant_schemes[]` | non-simulation | `hisim/subsidy_catalog/` holds `AT.json`, `DE.json` — **no `IE.json`** |

**Totals:** used 24 · approximated 3 · blocked 15 · structural 2 · no consumer 8 ·
non-simulation 27 = **79**.

Of the 44 fields that describe physics (used + approximated + blocked + structural), **17**
cannot reach a HiSim field today: 15 are blocked on the P4 component sweep and 2 are wiring
decisions with no base file yet.

## 2. Annual mileage: a field the contract dropped

The v1 contract carried `vehicles[].kmPerYear`
(`hisim/renovisor/examples/example_1_gas_to_heatpump_pv_insulation.json`). v0.3 carries per-km
consumption but no mileage. HiSim's driving distance comes from the LPG travel-route set and the
occupancy profile, so mileage is *implied* by field 26 and cannot be stated independently.
Consequence: a car's annual energy is a function of `travel_route_set`, which the contract
documents as an occupancy field, not a vehicle field.

## 3. `Kpis` — 13 leaf fields

| Field | Available today | Source / gap |
|---|---|---|
| `energy_demand_kwh` | yes, ambiguous | `kpi_preparation.py` produces several demands; the contract does not say which boundary (delivered / final / primary) or which carriers |
| `energy_label` | **no** | needs an IE BER/DEAP rating procedure; HiSim computes no rating |
| `self_sufficiency_in_percent` | yes | "Self-sufficiency rate of electricity", "Total energy self-suffiency rate"; contract types it `string` — a defect |
| `emissions_kg_co2` | yes, ambiguous | "Total CO2 emissions for simulated period"; period not stated in the contract |
| `disruption_days_by_level.{none,minor,moderate,major}` (4) | **no** | construction logistics; no model, no data |
| `indoor_air_quality` | **no** | no model |
| `thermal_insulation_effect` | **no** | no model (a 1–5 score) |
| `summer_heat_protection` | **no** | derivable from overheating hours; not implemented |
| `comfort.heating` | **no** | derivable from set-point deviation hours; not implemented |
| `comfort.cooling` | **no** | same |

**3 of 13** have a HiSim source today; 1 of those 3 has a type defect in the contract.

## 4. `Costs` — 9 `Range` fields (27 numbers)

| Field | Engine exists | Gap |
|---|---|---|
| `investment_costs_euro` | yes | `hisim/economics` |
| `energy_costs_euro` | yes | tariffs: `hisim/cost_database/tariffs/` holds only `DE_DYNAMIC_SYNTHETIC_2024.json` — **no IE tariff** |
| `maintenance_costs_euro` | yes | |
| `grant_euro` | yes | subsidy solver exists; **no `IE.json` catalogue** |
| `monthly_net_cost_20y_euro` | spec'd | loan math specified in `roadmap/cost-spec-v2.md`, not verified against a case |
| `monthly_net_cost_10y_euro` | spec'd | same |
| `payback_period_in_years` | derivable | not a named KPI today |
| `net_present_value_in_euro` | yes | |
| `property_value_increase_in_percent` | **no** | no model and no data source |

**7 of 9** have an engine; 2 of those are blocked on missing Irish data; 1 has no model at all.

### 4.1 `Range` vs the cost engine's band

`hisim/economics/uncertainty.py:Slot` is `LOW` / `BEST_ESTIMATE` / `HIGH`, and its docstring is
explicit that a slot is *a property of the whole evaluation, not of a single value*: within one
slot every parameter is set consistently, cost-type parameters at their minimum and revenue-type
parameters at their maximum. The contract's `Range {low, normal, high}` names the middle slot
differently and says nothing about coherence, which invites a reader to mix slots across fields.

## 5. Heating systems: 5 of 10 buildable today

`hisim/components/generic_boiler.py` carries presets `condensing_gas`, `condensing_gas_12kw`,
`oil`, `oil_12kw`, `pellets`, `wood_chips`, `hydrogen`. That covers 5 of the contract's 10
`heating_system.system` values.

| Contract value | Base file buildable today | Blocking module |
|---|---|---|
| `GasHeating` | yes | — |
| `OilHeating` | yes | — |
| `PelletHeating` | yes | — |
| `WoodChipHeating` | yes | — |
| `HydrogenHeating` | yes | — |
| `HeatPump` | no | `more_advanced_heat_pump_hplib` (3 classes) |
| `ElectricHeating` | no | `generic_electric_heating` |
| `DistrictHeating` | no | `generic_district_heating` |
| `GasSolarThermal` | no | `solar_thermal_system` (2 classes) |
| `HeatPumpSolarThermal` | no | both of the above |

## 6. Component classes: 8 convertible, ≈18 still needed

`energy_systems/gas_boiler_household.energy_system.yaml` uses 8 classes across 6 modules —
`Weather`, `UtspLpgConnector`, `Building`, `HeatDistributionController`, `HeatDistribution`,
`GenericBoiler`, `GenericBoilerController`, `ElectricityMeter`. A repository-wide search for
`presets` in `hisim/components/` returns exactly two files (`building/config.py`,
`generic_boiler.py`); the other six classes carry constructors or presets from the P2 pilot
conversion.

The union of components across the ten `*_building_sizer.py` setups adds ≈13 modules /
≈18 classes: `generic_pv_system`, `advanced_battery_bslib`,
`controller_l2_energy_management_system`, `simple_water_storage` (2),
`more_advanced_heat_pump_hplib` (3), `generic_district_heating`, `generic_electric_heating`,
`solar_thermal_system` (2), `generic_car`, `advanced_ev_battery_bslib`,
`controller_l1_generic_ev_charge`, `gas_meter`, `fuel_meter`.

Even the five buildable heating systems can only be built **without** PV, battery, EMS, buffer
storage or DHW storage: the reference file runs with `position_hot_water_storage_in_system:
NO_STORAGE_MASS_FLOW_FIX` and no DHW branch at all.

## 7. Endpoint surface

`roadmap/renovisor/openapi.yaml`: 15 paths, 17 operations, 20 schemas. Of the 17 operations, 9
need a HiSim result to answer: `packageset`, `fast-estimate`, `detailed-simulation`,
`report.pdf`, `POST /homeinventories/{hi_id}/packages`, and the four `from-*` creation routes,
each of which must produce an inventory that is actually simulable.

## 8. What the v1 translator does today, for comparison

`hisim/renovisor/` is 7 modules / ~1,700 lines with 763 lines of tests across 6 test files. It
maps a v1 camelCase request to `ModularHouseholdConfig` + one of 10 `*_building_sizer.py` setups,
runs it in-process and POSTs result *files* to a URL. `hisim/renovisor/spec.md` states that
deriving RenoVisor's `CalcResult` from HiSim output is **out of scope for v1** — the receiving
server interprets the files. The v0.3 contract moves that interpretation into the backend.
