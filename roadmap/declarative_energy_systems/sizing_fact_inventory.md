# Sizing-fact inventory — what is actually sized, from what, with which math

**Status:** survey, 2026-08-25. Read from `main` (factories and setups), the `json_v2`
spike (design-B laws for the heat pump chain) and `config_presets` (boiler/HDS/EMS
pilots). Purpose, per the request from the review: list every sizing fact of every
component and the arithmetic it really needs, so the sizing engine is built to this
list and not to a hypothetical one. Companion to `roadmap/declarative_energy_systems/config_requirements_spec.md`.

Out of scope here: run-time data exchange through `SingletonSimRepository` (weather
forecasts, thermal transmission coefficients, battery limits, COPs) — those are
simulation-time values, not construction-time sizing. One sizing-looking key there,
`WATERMASSFLOWRATEOFHEATGENERATOR`, has readers (both storages) but **no writer
anywhere** — dead, an instance of the silent-fallback rot the engine replaces.

---

## 1. Primary facts — where sizing information enters

| Fact | Source | Computed how |
|---|---|---|
| `heating_load_in_watt` | `BuildingInformation.max_thermal_building_demand_in_watt` | TABULA/EPISCOPE lookup + norm heating load calc; or overridden by the building sizer's `norm_heating_load_in_kilowatt` |
| `number_of_apartments` | `BuildingInformation.number_of_apartments` (config `number_of_apartments`, else TABULA) | lookup |
| `conditioned_floor_area_in_m2` | `BuildingInformation.scaled_conditioned_floor_area_in_m2` | TABULA × scaling |
| `roof_area_in_m2` | `BuildingInformation.roof_area_in_m2` | TABULA geometry |
| `heating_reference_temperature_in_celsius` | `BuildingConfig` field | hand-typed constant today; **should be derived from the weather** (see §1a) |
| `set_heating_temperature_in_celsius`, `set_cooling_temperature_in_celsius` | `BuildingConfig` fields | constants |
| `number_of_residents` | in the spike's registry; **no law reads it today** | — |
| location / coordinates | `WeatherConfig` (spike pattern B9 `for_location`) | — |

Everything below derives from these seven numbers plus a handful of secondary facts
(§3). There is exactly **one provider class per primary fact** (the Building; the
Weather for location).

### 1a. Climate facts (added 2026-08-25) — the weather as a provider

Climate already drives sizing, but only indirectly and through a hand-typed number: the
Building's norm heating load is computed from `heating_reference_temperature_in_celsius`
(the DIN design outside temperature), which today is a constant on `BuildingConfig` that
nothing checks against the chosen weather. Closing that gap makes the Weather a provider
and the Building a consumer as well as a provider (R4.6); the chain becomes
weather → building → generator → controller (4 levels, still acyclic). No format change.

| Fact | Provider | Computed how | Consumers |
|---|---|---|---|
| `heating_reference_temperature_in_celsius` | `Weather` (a `WeatherInformation`, build time) | lookup per station (DIN 12831 norm outside temperature, cf. the building sizer's `weather_try_region`), or from the weather year (coldest 2-day mean) | `BuildingConfig` (→ heating load), hplib heat pumps (COP design point), HDS controller |
| `cooling_design_temperature_in_celsius` (NEW) | `Weather` | lookup / warmest-period statistic | Building max cooling load (§6), air conditioners |
| `pv_annual_yield_in_kwh_per_kwp` (NEW) | `Weather` (+ tilt/azimuth) | from the irradiance year | battery sizing ("1 kWh per kWp" is really sizing to yield), solar thermal area |
| `heating_season_begin/end_day` (NEW) | `Weather` + HDS heating threshold | first/last day below threshold in the weather year | L1 controllers' `day_of_heating_season_*` (hard-coded today, §6) |

Consequence for §5: `heating_reference_temperature_in_celsius` stops being a primary
fact and becomes derived; the Building's preset carries `AUTO` for it. Two weather
stations in one file (a district) make it ambiguous for the building → one
`sizing_sources` line (R4.3). The RenoVisor/building-sizer knobs `weather_try_region`
and `heating_reference_temperature` disappear from the archetype.

## 2. Sized fields per component — inputs and math

Legend for *math*: **copy** = the field *is* the fact; **×k** = fact times a constant;
**fn** = a Python function (step table, `max`, ratio, database lookup); **1** /
**many** = how many providers the reader consumes.

### Heat generators

| Component / field | Reads | Math | Card. | Where today |
|---|---|---|---|---|
| `GenericBoiler.maximal_thermal_power_in_watt` | heating_load, number_of_apartments | **fn**: `max(load, 2500·apts)`, ×1.1 if both > 0 | 1 | pilot law (`config_presets`) |
| `GenericBoiler.minimal_thermal_power_in_watt` | *own* maximal power | 0 (gas/oil/H₂) or **×1/12** (pellets, wood chips) | — | pilot, per-preset law |
| `MoreAdvancedHeatPumpHPLib.set_thermal_output_power_in_watt` | heating_load | **copy** | 1 | spike law |
| `MoreAdvancedHeatPumpHPLib.heating_reference_temperature_in_celsius` | heating_reference_temperature | **copy** | 1 | spike law |
| `HeatPumpHplib` (advanced) — same two fields | same | **copy** | 1 | factory |
| `AirConditioner.scale_factor` (+ device choice) | heating_load, heating_reference_temperature | **fn**: database lookup, nearest capacity at nearest temperature, ×0.6/load | 1 | factory |
| `ElectricHeating.maximum_electric_power_w` | heating_load | **copy** | 1 | **setup-side** (building-sizer setups pass the load) |
| `DistrictHeating.connected_load_in_w` | heating_load | **copy** | 1 | **setup-side** |
| CHP / fuel cell `thermal_power` | — | constant argument | — | setups pass literals; not sized |
| Solar thermal, wind turbine, electrolyzer, H₂ storage | — | not sized | — | — |

### Heat generator controllers

| Component / field | Reads | Math | Card. | Where today |
|---|---|---|---|---|
| `GenericBoilerController.maximal/minimal_thermal_power_in_watt` | boiler power band (§3) | **copy** | 1 | pilot (controller still legacy; setup passes boiler config values) |
| `…ControllerSpaceHeating.heat_distribution_system_type` | HDS type (§3) | **copy** | 1 | spike law |
| `…ControllerSpaceHeating.set_heating_threshold_outside_temperature_in_celsius` | HDS threshold (§3) | **copy** | 1 | spike law |
| `…ControllerSpaceHeating.heating_reference_temperature_in_celsius`, `heating_load_of_building_in_watt` | primary | **copy** | 1 | spike law |
| Electric heating / district heating / boiler controllers `set_heating_threshold_outside_temperature_in_celsius` | HDS threshold | **copy** (today a literal 16.0 default) | 1 | factory default |
| `L1HeatPumpController` (heat-source controller) | — | not sized | — | — |

### Heat distribution

| Component / field | Reads | Math | Card. | Where today |
|---|---|---|---|---|
| `HeatDistributionController.heating_load_of_building_in_watt` | heating_load | **copy** (rounded 2) | 1 | spike law |
| `HeatDistributionController.specific_heating_load_of_building_in_watt_per_m2` | heating_load, conditioned_floor_area | **fn**: ratio | 1+1 | spike law (setups computed it inline before) |
| `HeatDistributionController.set_heating_threshold_outside_temperature_in_celsius` | the ratio above | **fn**: step table 16 / 18 / 20 °C at 50 / 80 W/m² | — | spike law |
| `HeatDistributionController.heating_system` (type) | — | author choice (constant law) | — | spike |
| `HeatDistributionController.heating_reference_temperature_in_celsius`, set heating/cooling temperatures | primary | **copy** | 1 | spike law / setups |
| `HeatDistribution.heating_system` | HDS type (§3, from its controller) | **copy** | 1 | spike law |
| `HeatDistribution.water_mass_flow_rate_in_kg_per_second` | mass flow (§3, from its controller) | **copy** (rounded 2) | 1 | spike law |
| `HeatDistribution.absolute_conditioned_floor_area_in_m2` | conditioned_floor_area | **copy** | 1 | spike law |

### Storages

| Component / field | Reads | Math | Card. | Where today |
|---|---|---|---|---|
| `SimpleHotWaterStorage.volume_heating_water_storage_in_liter` | generator max power (§3) | **×k**, k ∈ {50 (heat pump), 20 (general/gas), 40 (pellet), 50 (wood chip)} l/kW — k chosen by generator type | 1 | factory with `sizing_option` enum → spike: per-preset laws |
| `SimpleDHWStorage.volume_heating_water_storage_in_liter` | number_of_apartments | **×k**: 250 l × max(apts, 1) | 1 | spike law |

### Electricity

| Component / field | Reads | Math | Card. | Where today |
|---|---|---|---|---|
| `PVSystem.power_in_watt` | roof_area (+ share, module data) | **fn**: `roof × 0.6 × share / module_area × module_power` | 1 | spike law (`scaled_power_law(share)` per preset) |
| `Battery.custom_battery_capacity_generic_in_kilowatt_hour` | PV peak power (§3) | **×k**: 1 kWh per kWp | 1 | spike law |
| `Battery.custom_pv_inverter_power_generic_in_watt` | PV peak power | **×k**: 0.5 W per Wp (C-rate 0.5) | 1 | spike law |
| `L2GenericEnergyManagementSystem` | — | **nothing sized** | — | pilot converts presets only |
| `ElectricityMeter` | — | not sized | — | — |
| Cars, chargers, smart devices, occupancy | — | not sized | — | — |

### Multi-provider readers today

**None.** Every reader above consumes exactly one provider. The only place several
providers appear in current setups is *several PV systems into one battery* — and no
setup does that; the `total_pv_power_in_watt_peak` argument name notwithstanding, every
call passes one PV config's `power_in_watt`. The MFH mockup is the first case that
would need a **many**-reader (EMS/meter over several generators), and even there the
EMS sizes nothing today.

## 3. Secondary facts — facts that components provide to other components

| Fact | Provider | Consumers | Depth |
|---|---|---|---|
| `maximal_thermal_power_in_watt`, `minimal_thermal_power_in_watt` | boiler, heat pump (as `set_thermal_output_power`), electric heating, district heating | its controller; the buffer storage | building → generator → controller/storage (2 hops) |
| `heat_distribution_system_type` | HDS controller (author choice) | HDS; heat pump SH controller | 1 hop |
| `set_heating_threshold_outside_temperature_in_celsius` | HDS controller (fn of specific load) | heat pump SH controller; other generator controllers | building → HDS ctrl → HP ctrl (2 hops) |
| `water_mass_flow_rate_in_kg_per_second` | HDS controller (fn of heating_load, HDS type ΔT) | HDS; (storages, via the dead SimRepo key) | 2 hops |
| `pv_peak_power_in_watt` | PV system (resolved value) | battery | building → PV → battery (2 hops) |

Deepest chain in any setup: **3 levels** (building → HDS controller → heat pump SH
controller), all scalar, all acyclic. Every secondary fact is the *resolved value of a
sized field* of the provider (or an author choice) — no provider computes a fact that
is not also one of its own fields.

## 4. The math, classified

| Kind | Occurrences | Needed operator |
|---|---|---|
| copy | ~20 | fact term |
| × constant | 6 (storage volumes, battery, pellet minimum) | scale |
| rounding | 3 | round |
| ratio of two facts | 1 (specific load) | *fn* (or a `/` operator) |
| max of two + conditional factor | 1 (boiler) | *fn* |
| step table | 1 (heating threshold) | *fn* |
| database / catalogue lookup | 2 (air conditioner, PV module) | *fn* |
| clamp | 0 today (`at_least`/`at_most` exist in `laws.py`, unused) | — |
| sum / max over many providers | **0** | — |

## 5. Conclusions for the engine

1. **The scalar law algebra already exceeds what is used.** Fact term, `×k`, `rounded`
   and an opaque function law with declared `reads=` cover 100 % of today's sizing.
   `at_least`/`at_most` have no user; keep them only because they are trivial, or drop.
2. **No many-reader exists today.** Cardinality *many* (R4.4) is required by the
   requirements (MFH, several PVs) but has zero present consumers. Recommendation:
   specify it (term + tuple + `sum`/`max`/`count` on the class side, list source in the
   file), **implement it when the first real consumer is converted**, not before. The
   flat-namespace rule (R4.3) does not depend on it.
3. **Graph depth is 3, all scalar, all acyclic.** The fixed-point sweep is fine but a
   plain topological order would do; keep the cycle check (cheap, precise), drop
   anything designed for deep or dynamic graphs.
4. **Setup-side sizing must move into classes**: electric heating and district heating
   take the heating load as a literal argument from the setup; the specific load was
   computed inline in setups (the spike moved it into the HDS controller). After the
   sweep, setups and files contain no sizing arithmetic (R4.5).
5. **Provider set per fact is small and static**: `maximal_thermal_power_in_watt` is the
   only fact with several *classes* providing it (boiler, heat pump, electric, district
   heating) — the interchangeable-provider case R4.3 anticipates. Every other fact has
   one provider class.
6. **The dead `WATERMASSFLOWRATEOFHEATGENERATOR` key** should be deleted with the
   storage conversion; the storages' mass-flow fallback is exactly what the hard-error
   rule forbids.
7. Facts nobody reads today (`number_of_residents`) stay out of the registry until a
   law reads them.

---

## 6. First guess: what *should* probably be auto-sized in the future

**Status:** brainstorm, 2026-08-25, produced by a systematic pass over every module in
`hisim/components/` (an Opus subagent read each `ConfigBase` dataclass and judged each
field). Explicitly a first idea of possible future requirements — expected to be partly
wrong; nothing here is decided. Its value is the two closing lists (new facts, many-readers),
which show where the engine design of §5 would be stretched.

Three families of parameter recur across almost every module and account for most candidates. (1) **Controller mirrors**: nearly every `*ControllerConfig` in the H₂/CHP/fuel-cell branch (`ElectrolyzerControllerConfig`, `FuelCellControllerConfig`, `RsocControllerConfig`, `PTXControllerConfig`, `XTPControllerConfig`, `RsocBatteryControllerConfig`, `L1ElectrolyzerControllerConfig`) restates its device's `nom/min/max` power band as hand-copied literals — pure **copy** laws against a secondary fact the device already owns, exactly like the boiler controller pilot. (2) **Temperature setpoints**: `temperature_air_heating_in_celsius`, `heating_set_temperature_deg_c`, `setpoint_temperature_c`, `set_temperature_space_heating`, `min/max_comfort_temp_in_celsius`, `t_min/t_max_heating_in_celsius` all restate the Building's set temperatures or the HDS flow temperature. (3) **Flow-temperature / ΔT derivatives**: `flow_temperature_in_celsius` (both hplib heat pumps, literal `52`), `temperature_delta_in_celsius`, `massflow_nominal_secondary_side_in_kg_per_s`, `WarmWaterStorageConfig.temperature_difference` — all functions of the HDS type, which the HDS controller already computes as `max_flow_temperature_in_celsius`. The biggest new-fact demands are the HDS **flow/return temperature band**, the Building's **5R1C coefficients** (`controller_mpc` hard-codes six of them), a **maximum cooling load**, **roof tilt/azimuth**, and **DHW/electricity demand from the occupancy component**. Roughly a third of the modules — the examples, the meters, the transformers, the pure-model controllers — are genuinely constant-only.

### Heat generators

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `HeatPumpHplibConfig` | `flow_temperature_in_celsius` | NEW: HDS max flow temp (`HeatDistributionControllerInformation.max_flow_temperature_in_celsius`) | copy (today literal 52) | 1 | high |
| `HeatPumpHplibConfig` | `group_id` | HDS type + source medium (air/brine) | lookup | 1 | low |
| `MoreAdvancedHeatPumpHPLibConfig` | `flow_temperature_in_celsius` | NEW: HDS max flow temp | copy (today literal 52) | 1 | high |
| `MoreAdvancedHeatPumpHPLibConfig` | `minimum_thermal_output_power_in_watt` | own `set_thermal_output_power_in_watt` | ×k (modulation range) | 1 | medium |
| `MoreAdvancedHeatPumpHPLibConfig` | `massflow_nominal_secondary_side_in_kg_per_s` | own thermal power + HDS ΔT | fn `P/(c_p·ΔT)` | 2 | high |
| `MoreAdvancedHeatPumpHPLibConfig` | `electrical_input_power_brine_pump_in_watt` | own thermal power | ×k | 1 | low |
| `GenericHeatPumpConfig` | `manufacturer`, `heat_pump_name` | heating_load | catalogue lookup (as `AirConditioner`) | 1 | medium |
| `GenericBoilerConfig` | `temperature_delta_in_celsius` | NEW: HDS flow/return band | fn `max_flow − max_return` | 1 | medium |
| `CHPConfig` (generic_chp) | `p_th` | heating_load | ×k (base-load share ≈0.3) | 1 | medium |
| `CHPConfig` (generic_chp) | `p_el`, `p_fuel` | own `p_th` | ×k (power ratio); `(p_el+p_th)/η` | 1 | high |
| `CHPConfig` (advanced_fuel_cell) | `p_th_max`, `p_el_max` | heating_load; NEW annual electricity demand | ×k | 1 | low |
| `CHPConfig` (advanced_fuel_cell) | `p_th_min`, `p_el_min`, `mass_flow_max` | own maxima + `delta_temperature` | ×k; `P/(c_p·ΔT)` | 1 | medium |
| `SimpleHeatSourceConfig` | `power_th_in_watt` | NEW: heat pump source-side (evaporator) power | fn `P_th·(1−1/COP)` | 1 | medium |
| `SimpleHeatSourceConfig` | `massflow_nominal_in_kg_per_s` | NEW: heat pump primary massflow | copy | 1 | medium |
| `SolarThermalSystemConfig` | `area_m2` | roof_area_in_m2 (shared with PV); NEW DHW demand | fn `roof×share`, or `1.5 m²/person` | 1 (many w/ PV) | high |
| `SolarThermalSystemConfig` | `coordinates` | weather location | copy / geocode lookup | 1 | high |
| `SolarThermalSystemConfig` | `tilt`, `azimuth` | NEW: roof tilt + azimuth (Building/TABULA) | copy | 1 | medium |
| `SimpleAirConditionerConfig` | `nominal_cooling_power_w` | NEW: max cooling load in watt (Building) | copy (today literal 2000) | 1 | high |
| `IdealizedHeaterConfig` | `set_heating_temperature_for_building_in_celsius`, `set_cooling_…` | building set temperatures | copy | 1 | high |
| `GenericElectrolyzerConfig` | `max_power`, `min_power` | PV peak power | ×k (surplus share); min = max×k | 1 (many PVs) | medium |
| `GenericElectrolyzerConfig` | `max/min_hydrogen_production_rate` | own power band | fn `P/spec. energy` | 1 | high |
| `ElectrolyzerConfig` (h2) | `nom_load`, `max_load`, `nom_h2_flow_rate` | PV peak power; own `nom_load` | ×k; fn | 1 | medium |
| `ElectrolyzerWithStorageConfig` | `min_power`, `max_power`, `max_hydrogen_production_rate_hour` | PV peak power | ×k; fn | 1 | medium |
| `FuelCellConfig` / `RsocConfig` | `nom_output` / `nom_load_soec`, `nom_power_sofc` | PV peak power; NEW annual electricity demand | ×k | many | low |

### Heat generator controllers

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `ElectricHeatingControllerConfig` | `specific_heating_load_of_building_in_watt_per_m2` | heating_load, conditioned_floor_area | ratio (as HDS ctrl) | 2 | high |
| `HeatPumpHplibControllerL1Config` | `set_cooling_threshold_outside_temperature_in_celsius` | set_cooling_temperature, specific heating load | fn (step table, mirror of heating side) | 2 | medium |
| `MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig` | `upper/lower_temperature_offset_for_state_conditions_in_celsius` | HDS type; buffer storage volume | lookup | 1 | low |
| `MoreAdvancedHeatPumpHPLibControllerDHWConfig` | `p_th_max_dhw_in_watt` | number_of_apartments; own HP max power | fn `min(HP_max, k·apts)` | 2 | medium |
| `MoreAdvancedHeatPumpHPLibControllerDHWConfig` | `t_min/t_max_dhw_storage_in_celsius` | NEW: DHW storage set temperature | copy | 1 | medium |
| `L1HeatPumpConfig` | `t_min_heating_in_celsius`, `t_max_heating_in_celsius` | NEW: HDS flow temperature band | copy ± hysteresis | 1 | medium |
| `L1HeatPumpConfig` | `day_of_heating_season_begin`, `…_end` | heating threshold temp + weather series | fn over weather year | 2 | low |
| `L1HeatPumpConfig` | `cooling_considered` | building `set_cooling_temperature_in_celsius` | fn (bool: is cooling set?) | 1 | low |
| `L1CHPControllerConfig` | `t_min/t_max_heating_in_celsius`, `t_min/t_max_dhw_in_celsius` | HDS flow temp; NEW DHW storage temps | copy | 2 | medium |
| `L1CHPControllerConfig` | `electricity_threshold`, `day_of_heating_season_*` | CHP `p_el`; weather | ×k; fn | 1 | low |
| `L1ElectrolyzerControllerConfig` | `p_min_electrolyzer` | electrolyzer `min_power` | copy | 1 | high |
| `ElectrolyzerControllerConfig` | `nom_load`, `min_load`, `max_load`, `standby_load` | electrolyzer config band | copy | 1 | high |
| `FuelCellControllerConfig` | `nom_output`, `min_output`, `max_output`, `standby_load` | fuel cell config band | copy | 1 | high |
| `RsocControllerConfig` | all 6 SOEC/SOFC load fields | `RsocConfig` band | copy | 1 | high |
| `PTXControllerConfig` | `nom_load`, `min_load`, `max_load`, `standby_load` | electrolyzer band | copy | 1 | high |
| `XTPControllerConfig` | `nom_output`, `min_output`, `max_output`, `standby_load` | fuel cell band | copy | 1 | high |
| `RsocBatteryControllerConfig` | all 7 `*_in_kw` fields | `RsocConfig` band | copy | 1 | high |
| `GenericHeatPumpControllerConfig` | `temperature_air_heating_in_celsius`, `temperature_air_cooling_in_celsius` | building set heating/cooling temperatures | copy | 1 | high |
| `AirConditionerControllerConfig` | `heating_set_temperature_deg_c`, `cooling_set_temperature_deg_c` | building set heating/cooling temperatures | copy | 1 | high |
| `SimpleAirConditionerControllerConfig` | `setpoint_temperature_c` | building `set_cooling_temperature_in_celsius` | copy (today literal 24) | 1 | high |
| `ExtendedControllerConfig` (configuration.py) | `chp`, `gas_heater`, `electrolyzer`, `chp_power_states_possible` | NEW: system topology (which generators exist) | presence / count | many | low |

### Heat distribution

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `HeatDistributionConfig` | `position_hot_water_storage_in_system` | NEW: topology (buffer storage present, parallel/serial) | presence | many | medium |
| `HeatDistributionControllerConfig` | `heating_system` (HDS type) | NEW: building construction year + renovation level (from the TABULA code) | lookup/step table — decided 2026-08-25 (P1 Q-P1.8) to be sized this way in P4 B3 | 1 | decided |
| `SetTemperatureConfig` (dual_circuit_system) | `set_temperature_space_heating` | NEW: HDS max flow temperature | copy | 1 | high |
| `SetTemperatureConfig` | `set_temperature_dhw` | NEW: DHW storage set temperature | copy | 1 | medium |
| `SetTemperatureConfig` | `outside_temperature_threshold` | HDS heating threshold (existing secondary fact) | copy | 1 | high |

### Storages

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `SimpleHotWaterStorageConfig` | `volume_heating_water_storage_in_liter` (refinement) | maximal thermal power of **all** generators (hybrid boiler + HP) | ×k on `max`/`sum` over providers | many | medium |
| `SimpleDHWStorageConfig` | `volume_heating_water_storage_in_liter` (refinement) | NEW: DHW demand from occupancy, instead of apartments | fn (daily litres × factor) | 1 | medium |
| `WarmWaterStorageConfig` (configuration.py) | `tank_diameter`, `tank_height` | generator maximal thermal power (via a volume law) | fn `V→(h,d)` at fixed aspect ratio | 1 | medium |
| `WarmWaterStorageConfig` | `temperature_difference`, `tank_start_temperature` | NEW: HDS flow/return band | copy / difference | 1 | medium |
| `GenericHydrogenStorageConfig` | `max_capacity_in_kg` | NEW: electrolyzer max H₂ production rate | ×k (days of autonomy) | 1 | medium |
| `GenericHydrogenStorageConfig` | `max_charging_rate`, `max_discharging_rate` | electrolyzer H₂ rate; fuel cell H₂ rate | copy | 2 | high |
| `ElectrolyzerWithHydrogenStorageConfig` | `max_capacity`, `max_charging_rate_hour` | electrolyzer H₂ rate | ×k; copy | 1 | medium |

### Electricity generation & storage

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `PVSystemConfig` | `location` | weather location | copy (today a second hand-typed string) | 1 | high |
| `PVSystemConfig` | `azimuth`, `tilt` | NEW: roof azimuth + tilt (Building/TABULA geometry) | copy | 1 | medium |
| `PVSystemConfig` | `module_name`, `inverter_name` | resolved `power_in_watt` | catalogue lookup (nearest size) | 1 | low |
| `PVSystemConfig` | `share_of_maximum_pv_potential` | NEW: roof area already claimed by solar thermal | fn `1 − ST_area/roof` | many | low |
| `BatteryConfig` | `charge_in_kwh`, `discharge_in_kwh` (initial/limits) | own capacity | ×k | 1 | low |
| `CarBatteryConfig` | `e_bat_custom` | NEW: car battery capacity (from `CarConfig` / LPG transportation device set) | copy | 1 | medium |
| `CarBatteryConfig` | `p_inv_custom` | own `e_bat_custom` | ×k (C-rate); or copy charger power | 1 | high |
| `WindturbineConfig` | `hub_height`, `rotor_diameter`, `nominal_power` | `turbine_type` | windpowerlib catalogue lookup | 1 | medium |
| `WindturbineConfig` | `measuring_height_*`, `hellman_exp`, `obstacle_height` | NEW: weather data source / terrain class | lookup per `WeatherDataSourceEnum` | 1 | low |
| `PriceSignalConfig` | `installed_capacity` | PV peak power of **all** PV systems | sum over providers | many | medium |
| `PriceSignalConfig` | `country` | weather location | lookup | 1 | medium |

### EMS / meters / controllers

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `EMSConfig` | `limit_to_shave` | NEW: grid connection limit; sum of generator powers | fn over many providers | many | low |
| `EMSConfig` | `building_indoor_temperature_offset_value` | building set heating/cooling temperature span | ×k / fn | 1 | low |
| `EMSConfig` | `domestic_hot_water_storage_temperature_offset_value`, `space_heating_…` | storage set temperatures / HDS ΔT | ×k | 2 | low |
| `MpcControllerConfig` | `h_tr_w/ms/em/is_in_watt_per_kelvin`, `h_ve_adj_…`, `c_m_in_joule_per_kelvin` | NEW: Building 5R1C coefficients (`BuildingInformation.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin`, `heat_transfer_coeff_*`) | copy (6 fields, today literals) | 1 | high |
| `MpcControllerConfig` | `min_comfort_temp_in_celsius`, `max_comfort_temp_in_celsius`, `initial_temperature_in_celsius` | building set temperatures / `initial_internal_temperature_in_celsius` | copy | 1 | high |
| `MpcControllerConfig` | `maximum/minimum_storage_capacity_in_watt_hour`, `maximum_charging/discharging_power_in_watt` | NEW: battery capacity + C-rate | copy / ×k | 1 | high |
| `MpcControllerConfig` | `battery_efficiency`, `inverter_efficiency` | battery / inverter config | copy | 1 | medium |
| `MpcControllerConfig` | `cop_coef`, `eer_coef` | NEW: AC/heat pump device performance curve | lookup from device | 1 | low |
| `GasMeterConfig` | `gas_loadtype` | NEW: energy carrier of the connected generator(s) | copy (or check-consistency over many) | many | medium |
| `FuelMeterConfig` | `fuel_loadtype` | NEW: energy carrier of the connected generator(s) | copy | many | medium |
| `FuelMeterConfig` | `heating_value_of_fuel_in_kwh_per_liter`, `fuel_density_in_kg_per_m3` | own `fuel_loadtype` | table lookup per carrier | 1 | high |
| `ChargingStationConfig` | `charging_station_set` | NEW: car battery capacity / charger power | lookup (nearest set) | 1 | medium |
| `ChargingStationConfig` | `lower_threshold_charging_power_in_watt` | charging station nominal power | ×k | 1 | low |

### Mobility

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `CarConfig` | number of car instances / `source_weight` | NEW: number of cars from occupancy (LPG `transportation_device_set`) | count → instance fan-out | many | medium |
| `CarConfig` | `consumption_per_km` | `fuel` + NEW vehicle class | lookup | 1 | low |
| `SmartDeviceConfig` | `smart_devices_included` | occupancy `profile_with_washing_machine_and_dishwasher` | copy | 1 | low |

### Occupancy / weather / building

| Component | Field | Would derive from | Math guess | Card. | Confidence |
|---|---|---|---|---|---|
| `UtspLpgConnectorConfig` | `household` (single vs `List[JsonReference]`) | number_of_apartments | fn: one household reference per apartment | 1 → many instances | medium |
| `UtspLpgConnectorConfig` | `energy_intensity` | NEW: building code / renovation state | lookup | 1 | low |
| `WeatherConfig` | `source_path` | own `location` + `data_source` | fn (path template) | 1 | high |
| `BuildingConfig` | `heating_reference_temperature_in_celsius` | weather location | lookup (DIN norm outside temp per station) | 1 | high |
| `BuildingConfig` | `number_of_apartments`, `absolute_conditioned_floor_area_in_m2` | `building_code` (TABULA) — already internal to `BuildingInformation` | lookup × scaling | 1 | high (done) |
| `BuildingConfig` | all `*_u_value_*` / `*_area_in_m2` overrides | `building_code` (TABULA element table) | lookup | 1 | high (done) |
| `CSVLoaderConfig` | `multiplier` | conditioned_floor_area or number_of_apartments | ×k (scale a reference profile) | 1 | low |

### Components with no sizing candidates (pure technology/model constants)

- `electricity_meter.py` — pure accounting, no physical size
- `heating_meter.py` — config holds only `component_id`
- `sumbuilder.py` — loadtype/unit plumbing only
- `transformer_rectifier.py` — single efficiency constant
- `controller_pid.py` — config has no tunable fields
- `night_setback_controller.py` — author-chosen comfort schedule
- `example_component.py`, `example_template.py`, `example_transformer.py`, `example_storage.py`, `controller_l1_example_controller.py` — teaching examples
- `random_numbers.py` — test stimulus generator
- `controller_l1_generic_ev_charge.py` (`battery_set_soc`) — user comfort choice
- `advanced_fuel_cell_controller.py` — no `ConfigBase` at all
- `weather_data_import.py` — data reader, no component config
- `building/building.py`, `building/information.py`, `building/window.py` — fact *providers*, no own config
- `air_conditioner.py` (ref curves), `advanced_battery_bslib.py` — already sized today (§2); `generic_district_heating.py`, `generic_electric_heating.py` — sized today only setup-side (§2, §5.4), no further candidates beyond moving that into the class
- All `device_co2_footprint_in_kg` / `investment_costs_in_euro` / `lifetime_in_years` / `maintenance_costs_*` / `subsidy_*` fields — economic metadata, better keyed off the resolved size in postprocessing than sized here

### New facts this would require

- **HDS flow/return temperature band** (`max_flow_temperature_in_celsius`, `min_flow_temperature_in_celsius`, `max/min_return_temperature_in_celsius`) — provider `HeatDistributionControllerInformation` (already computes them; not yet a fact). Largest single unlock: both hplib `flow_temperature_in_celsius`, boiler ΔT, HP massflow, `L1HeatPumpConfig`/`L1CHPControllerConfig` hysteresis bands, `SetTemperatureConfig`, `WarmWaterStorageConfig.temperature_difference`.
- **Building 5R1C coefficients** (`thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin`, `heat_transfer_coeff_*`, ventilation coefficient) — provider `BuildingInformation`; consumer `MpcControllerConfig` (six literals today).
- **Maximum cooling load in watt** — provider `BuildingInformation`; consumer `SimpleAirConditionerConfig.nominal_cooling_power_w`.
- **Roof tilt and azimuth (per roof plane)** — provider `BuildingInformation`/TABULA geometry; consumers `PVSystemConfig`, `SolarThermalSystemConfig`.
- **DHW demand (peak and daily)** and **annual electricity demand** — provider `UtspLpgConnector`; consumers DHW storage volume, `p_th_max_dhw_in_watt`, solar thermal area, CHP/fuel cell sizing.
- **Number of residents / number of cars** — provider `UtspLpgConnector` (residents fact exists in the registry but has no reader); consumers `CarConfig` fan-out, DHW laws.
- **DHW storage set temperature** — provider `SimpleDHWStorage`; consumers DHW controllers, `SetTemperatureConfig`.
- **Battery usable capacity, C-rate, efficiencies** — provider `advanced_battery_bslib.BatteryConfig`; consumer `MpcControllerConfig`.
- **Car battery capacity and charger power** — providers `CarConfig` / `ChargingStationConfig`; consumer `CarBatteryConfig`.
- **Heat pump source-side power and primary massflow** — provider the hplib heat pumps; consumer `SimpleHeatSourceConfig`.
- **Electrolyzer max H₂ production rate** and **fuel cell H₂ consumption rate** — providers electrolyzer/fuel cell; consumers H₂ storage capacity and rates.
- **Energy carrier of each heat generator** (`lt.LoadTypes`) — providers boiler/district heating/electric heating; consumers `GasMeterConfig.gas_loadtype`, `FuelMeterConfig.fuel_loadtype`.
- **Grid connection limit** — no provider today (site fact); consumer `EMSConfig.limit_to_shave`.
- **System topology / presence flags** (does a buffer storage exist, which generators exist) — provider the setup graph itself; consumers `HeatDistributionConfig.position_hot_water_storage_in_system`, `ExtendedControllerConfig`.
- **Climate facts** (norm outside temperature, cooling design temperature, PV yield per kWp, heating-season bounds) — provider `Weather`; see §1a. Makes `BuildingConfig.heating_reference_temperature_in_celsius` a derived fact.

### Many-readers this would create

- `SimpleHotWaterStorageConfig.volume_heating_water_storage_in_liter` — `max`/`sum` of `maximal_thermal_power_in_watt` over **all** heat generators (hybrid HP + boiler is the first real case).
- `PriceSignalConfig.installed_capacity` and `BatteryConfig.custom_battery_capacity_generic_in_kilowatt_hour` — `sum` of `pv_peak_power_in_watt` over all PV systems (the MFH case the inventory already flags).
- `GasMeterConfig.gas_loadtype` / `FuelMeterConfig.fuel_loadtype` — read the energy carrier of every connected generator; needs a *consistency* aggregator (all-equal) rather than sum/max.
- `EMSConfig.limit_to_shave` — over all generators and controllable loads.
- Roof area contention: `PVSystemConfig.share_of_maximum_pv_potential` vs `SolarThermalSystemConfig.area_m2` — two readers of one finite fact, requiring subtraction rather than independent scaling.
- `ExtendedControllerConfig` presence flags and `HeatDistributionConfig.position_hot_water_storage_in_system` — `count`/`exists` over the component set (topology queries, not scalar arithmetic).
- `UtspLpgConnectorConfig.household` and `CarConfig` instance count — one reader producing **many instances** from a count fact, which is a different mechanism from a many-provider aggregate and may need its own engine feature.

### What §6 means for the engine (my reading, 2026-08-25)

- The bulk of future candidates are **copies of a secondary fact** (controller mirrors,
  setpoints, flow temperatures) — the same one-provider copy law that dominates today.
  §5's "scalar algebra suffices" holds for ~80 % of this list too.
- Genuine **many-readers** appear in ~6 places, all still hypothetical; the first two
  likely real ones are the buffer storage over a hybrid generator pair and the battery /
  price signal over several PVs. Still: implement `many` with the first of them.
- Two patterns are **not** sizing arithmetic and should stay out of the engine: topology
  queries (`exists`/`count` over the component set) and count-driven instance fan-out
  (one occupancy per apartment, one car per household). The first is a question for the
  executor's graph, the second for the R5 template layer.
- The most valuable single new fact is the **HDS flow/return temperature band** — one
  provider that already computes it, ~8 consumers with hard-coded literals today.
