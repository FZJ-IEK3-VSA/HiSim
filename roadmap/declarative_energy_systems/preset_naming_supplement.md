# Preset naming — inventory survey and convention audit

**Date:** 2026-08-25 · **Status:** survey for P1 Q-P1.9 (`roadmap/declarative_energy_systems/p1_sizing_kernel_requirements.md` §11)
**Scope:** every config class under `hisim/components/` (incl. `hisim/components/building/`).

**Method and counts.** Enumerated all 65 modules in `hisim/components/` plus `hisim/components/building/{config,building,information,window}.py`; grepped `^class .*Config`, then every `get_default_*` / `get_scaled_*` / `get_config_*` / `config_*` / `control_*` / `read_config` classmethod and read the signature and the constructed-field block of each with `sed`. Cross-checked actual usage with `grep -o '\.get_[a-z_]*('` over `system_setups/*.py` (25 setups) and `tests/*.py` to fix the canonical preset per class (rule 3). Found **88 config classes**, **103 factory classmethods** in **79 distinct spellings** (plus 9 `read_config` static readers), of which **12** are `get_scaled_*`, **19** are a bare `get_default_config`, and **17** config classes have **no factory at all**. 10 classes are lookup-shaped (an identifier keys a JSON/CSV/enum table) rather than variant-shaped.

## Verdict on the convention

The five rules fit the bulk of the inventory: for the heat generators, the meters, the mobility chain and the storages they produce names that read as substance (`condensing_gas`, `oil`, `pellets`, `air_water`, `diesel`, `electric`, `gas`, `oil`), and rule 3 is decidable everywhere because every class is reachable from at most a handful of setups. Two rules collide in practice. **Rule 2 versus rule 5:** a class whose only variation is a rating (`HeatPumpHplibConfig`, `BatteryConfig`, `SimpleHotWaterStorageConfig`, `SolarThermalSystemConfig`) has exactly one defensible *variant* and therefore wants `standard` under rule 5, but also wants a catalogue-device preset under rule 2 — and the moment `standard_8kw` exists, rule 5 says `standard` must be retired, which leaves no legal name at all. **Rule 1 versus the boolean-flag factories:** ~10 classes have factories that differ only by `with_domestic_hot_water_preparation`, `parallel_space_heating_and_dhw_option` or `secondary_mode`; these are operating options of one variant, and minting `gas_with_dhw` / `gas_space_heating_only` doubles the wire vocabulary for a field a file can set in one line. Rule 4 causes no trouble. Rule 3 has one real casualty: `PVSystemConfig` has *three* factories, one of them on the component class (`PVSystem.get_default_config`), with a different module than the config-class factories — the canonical preset is a decision, not a reading.

Proposed amendment, concretely worded: **(A1)** *A rating-suffixed preset does not count as a second preset for rule 5; a class may carry `standard` plus `standard_<rating>` and nothing else.* **(A2)** *A boolean feature flag is never part of a preset name. Presets vary by fuel, source medium, technology and control law only; a flag is set in the file's `config:` block.* **(A3)** *Where the variation is a rating and the class also implies a technology, name the variant after the technology rather than `standard` (`air_water`, `flat_plate`), so rule 5's retirement clause never fires.* With A1–A3 the rules cover 86 of 88 classes without an ad-hoc decision.

## Heat generators

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `GenericBoilerConfig` (`generic_boiler.py`) | `condensing_gas`, `condensing_gas_12kw`, `oil`, `oil_12kw`, `pellets`, `wood_chips`, `hydrogen` | `get_scaled_condensing_gas_boiler_config`, `get_default_condensing_gas_boiler_config`, `get_scaled_conventional_oil_boiler_config`, `get_default_conventional_oil_boiler_config`, `get_scaled_conventional_pellet_boiler_config`, `get_scaled_conventional_wood_chip_boiler_config`, `get_scaled_condensing_hydrogen_boiler_config` | `maximal_thermal_power_in_watt`, `minimal_thermal_power_in_watt` | Already converted on `config_presets`; `conventional`/`condensing` qualifier exists for gas/oil/H2 only — dropped as noise (§conflict 1) |
| `DistrictHeatingConfig` (`generic_district_heating.py`) | `standard` | `get_default_district_heating_config` | `connected_load_in_w` | `with_domestic_hot_water_preparation` flag → A2 |
| `ElectricHeatingConfig` (`generic_electric_heating.py`) | `standard` | `get_default_electric_heating_config` | `maximum_electric_power_w` | flag `with_domestic_hot_water_preparation` → A2 |
| `HeatPumpHplibConfig` (`advanced_heat_pump_hplib.py`) | `air_water`, `air_water_8kw` | `get_scaled_advanced_hp_lib`, `get_default_generic_advanced_hp_lib` | `set_thermal_output_power_in_watt` | `model="Generic"`, `group_id=1` = air/water; A3. Future `brine_water`, `water_water` (hplib group_id 2/3) |
| `MoreAdvancedHeatPumpHPLibConfig` (`more_advanced_heat_pump_hplib.py`) | `air_water`, `air_water_8kw` | `get_scaled_advanced_hp_lib`, `get_default_generic_advanced_hp_lib` | `set_thermal_output_power_in_watt`, `massflow_nominal_secondary_side_in_kg_per_s` | Same names as the class above in a different module — acceptable, presets are class-scoped |
| `GenericHeatPumpConfig` (`generic_heat_pump.py`) | `standard` + constructor | `get_default_generic_heat_pump_config` | — | `heat_pump_name="Vitocal 300-A AWO-AC 301.B07"` + `manufacturer` = device lookup → constructor (§Constructor classes) |
| `IdealizedHeaterConfig` (`idealized_electric_heater.py`) | `standard` | `get_default_config` | — | Test/reference component; rule 5 clean |
| `SimpleHeatSourceConfig` (`simple_heat_source.py`) | `constant_thermal_power`, `constant_temperature`, `near_surface_brine` | `get_default_config_const_power`, `get_default_config_const_temperature`, `get_default_config_near_surface_brine_temperature` | `power_th_in_watt` (in `constant_thermal_power`) | Names mirror `SimpleHeatSourceType` members. `get_default_config_var_brinetemperature` is a deprecated alias → no preset |
| `SolarThermalSystemConfig` (`solar_thermal_system.py`) | `flat_plate` | `get_default_solar_thermal_system`, `get_default_solar_thermal_system_manually_calculated_capex` | `area_m2` | Two factories differ only in whether capex fields are `None` or hard-coded — not a variant (§conflict 3) |
| `AirConditionerConfig` (`air_conditioner.py`) | `standard` + constructor | `get_scaled_air_conditioner_config`, `get_default_air_conditioner_config` | `scale_factor` (from `heating_load`) | `get_air_conditioner_config_from_database(manufacturer, model_name)` = lookup → constructor |
| `SimpleAirConditionerConfig` (`simple_air_conditioner.py`) | `standard` | `get_default_simple_air_conditioner_config` | — | rule 5 clean |
| `GasHeaterConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy plain dataclass, superseded by `GenericBoilerConfig` — candidate for deletion instead of a preset |

## Heat generator controllers

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `GenericBoilerControllerConfig` (`generic_boiler.py`) | `modulating`, `on_off`, `pellets`, `wood_chips` | `get_default_modulating_generic_boiler_controller_config`, `get_default_on_off_generic_boiler_controller_config`, `get_default_pellet_controller_config`, `get_default_wood_chip_controller_config` | `maximal_thermal_power_in_watt`, `minimal_thermal_power_in_watt` | Mixed axes: two control laws + two fuels (§conflict 2). Canonical `modulating` (6 setup uses) |
| `DistrictHeatingControllerConfig` (`generic_district_heating.py`) | `standard` | `get_default_district_heating_controller_config` | — | flags `with_domestic_hot_water_preparation`, `parallel_space_heating_and_dhw_option` → A2 |
| `ElectricHeatingControllerConfig` (`generic_electric_heating.py`) | `standard` | `get_default_electric_heating_controller_config`, `get_electric_heating_config_based_on_building_efficiency` | `specific_heating_load_of_building_in_watt_per_m2`-derived fields | Second factory is the *sizable* form of the first, not a variant (§conflict 3) |
| `HeatPumpHplibControllerL1Config` (`advanced_heat_pump_hplib.py`) | `standard` | `get_default_generic_heat_pump_controller_config` | `heat_distribution_system_type` | `mode: int = 2` is a magic number, not a variant — keep as field |
| `MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig` (`more_advanced_heat_pump_hplib.py`) | `standard` | `get_default_space_heating_controller_config` | `heat_distribution_system_type` | rule 5 clean |
| `MoreAdvancedHeatPumpHPLibControllerDHWConfig` (`more_advanced_heat_pump_hplib.py`) | `standard` | `get_default_dhw_controller_config` | — | rule 5 clean |
| `GenericHeatPumpControllerConfig` (`generic_heat_pump.py`) | `standard` | `get_default_generic_heat_pump_controller_config` | — | rule 5 clean |
| `L1HeatPumpConfig` (`controller_l1_heatpump.py`) | `space_heating`, `buffer`, `dhw` | `get_default_config_heat_source_controller`, `get_default_config_heat_source_controller_buffer`, `get_default_config_heat_source_controller_dhw` | — | Variant = which vessel the controller regulates; good fit for rule 1 |
| `SolarThermalSystemControllerConfig` (`solar_thermal_system.py`) | `standard` | `get_solar_thermal_system_controller_config` | — | Factory name lacks `default` — spelling outlier |
| `AirConditionerControllerConfig` (`air_conditioner.py`) | `standard` | `get_default_air_conditioner_controller_config` | — | returns `Any` — untyped factory |
| `SimpleAirConditionerControllerConfig` (`simple_air_conditioner.py`) | `standard` | `get_default_simple_air_conditioner_controller_config` | — | rule 5 clean |
| `NightSetbackConfig` (`night_setback_controller.py`) | `standard` | `get_default_config` | — | rule 5 clean |
| `GasControllerConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |
| `CHPControllerConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |

## Heat distribution

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `HeatDistributionConfig` (`heat_distribution_system.py`) | `standard` | `get_default_heat_distribution_config` | `water_mass_flow_rate_in_kg_per_second`, `absolute_conditioned_floor_area_in_m2`, `heating_system` | Already `standard` on `config_presets`; rule 5 clean |
| `HeatDistributionControllerConfig` (`heat_distribution_system.py`) | `standard` | `get_default_heat_distribution_controller_config`, `get_config_based_on_building_efficiency` | `heating_load_of_building_in_watt`, `set_heating_temperature_for_building_in_celsius`, `set_cooling_temperature_for_building_in_celsius`, `heating_system` | Two factories = unsized/sized pair (§conflict 3); `heating_system` law is the Q-P1.8 case |
| `SetTemperatureConfig` (`dual_circuit_system.py`) | `standard` | *(none)* | — | Plain dataclass, not a `ConfigBase`, not a component config — held by `DiverterValve`. Needs one preset or exemption |

## Storages

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `SimpleHotWaterStorageConfig` (`simple_water_storage.py`) | `buffer` | `get_scaled_hot_water_storage`, `get_default_simplehotwaterstorage_config` | `volume_heating_water_storage_in_liter` (from `max_thermal_power_in_watt_of_heating_system`) | `sizing_option: HotWaterStorageSizingEnum` has 5 members — sizing *laws*, not variants; keep as field, not 5 presets (§conflict 4) |
| `SimpleHotWaterStorageControllerConfig` (`simple_water_storage.py`) | `standard` | `get_default_simplehotwaterstoragecontroller_config` | — | Factory name unreadable (`simplehotwaterstoragecontroller`) |
| `SimpleDHWStorageConfig` (`simple_water_storage.py`) | `standard` | `get_scaled_dhw_storage`, `get_default_simpledhwstorage_config` | `volume_heating_water_storage_in_liter` (from `number_of_apartments`) | rule 5 clean; `default_volume_in_liter=250.0` becomes the law constant, not a name |
| `WarmWaterStorageConfig` (`configuration.py`) | `standard` | `get_default_config` | — | Legacy; overlaps `SimpleHotWaterStorageConfig` — deletion candidate |
| `GenericHydrogenStorageConfig` (`generic_hydrogen_storage.py`) | `standard` | `get_default_config` | `max_capacity` | All four params defaulted in the factory (`capacity=200`) — numbers stay in the law, not the name |
| `HydrogenStorageConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy duplicate of the above — deletion candidate |

## Electricity generation and storage

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `PVSystemConfig` (`generic_pv_system.py`) | `rooftop`, `rooftop_10kw` | `get_scaled_pv_system`, `get_default_pv_system`, `PVSystem.get_default_config` | `power_in_watt` (from `rooftop_area_in_m2`) | Three factories, one on the *component* class with a different `module_name`; canonical is a decision (§conflict 5). `share_of_maximum_pv_potential` is the Q-P1.4 sibling trap |
| `PVConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy duplicate of `PVSystemConfig` — deletion candidate |
| `WindturbineConfig` (`generic_windturbine.py`) | `standard` + constructor | `get_default_windturbine_config` | — | `turbine_type="V126/3300"` is a windpowerlib library key → lookup (§Constructor classes); the rating is inside the identifier, so rule 2's suffix cannot apply |
| `BatteryConfig` (`advanced_battery_bslib.py`) | `standard`, `standard_5kwh` | `get_scaled_battery`, `get_default_config` | `custom_battery_capacity_generic_in_kilowatt_hour` (from `total_pv_power_in_watt_peak`), `custom_pv_inverter_power_generic_in_watt` | Pure rating variation → A1/A3 with no technology name available (§conflict 6) |
| `TransformerConfig` (`transformer_rectifier.py`) | `standard` | `get_default_transformer_config` | — | rule 5 clean |

## EMS, meters and generic controllers

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `EMSConfig` (`controller_l2_energy_management_system.py`) | `optimize_own_consumption` | `get_default_config_ems` | — | Already converted; `strategy` field is commented "more or less obsolete" — the preset name and the field duplicate each other |
| `ElectricityMeterConfig` (`electricity_meter.py`) | `standard` | `get_electricity_meter_default_config` | — | rule 5 clean; spelling `get_<x>_default_config` |
| `GasMeterConfig` (`gas_meter.py`) | `gas` | `get_gas_meter_default_config` | — | Parameterised by `gas_loadtype: lt.LoadTypes = GAS`; a second carrier would give `biogas` |
| `FuelMeterConfig` (`fuel_meter.py`) | `oil`, `pellets`, `wood_chips` | `get_fuel_meter_default_config` | — | One factory, three real carriers via `fuel_loadtype` + heating value; presets replace the parameter (rule 4 gives `oil` singular, `pellets` plural) |
| `HeatingMeterConfig` (`heating_meter.py`) | `standard` | `get_heating_meter_default_config` | — | rule 5 clean |
| `SumBuilderConfig` (`sumbuilder.py`) | `standard` | `get_sumbuilder_default_config` | — | rule 5 clean |
| `PriceSignalConfig` (`generic_price_signal.py`) | `standard` | `get_default_price_signal_config` | — | rule 5 clean; test-only in practice |
| `MpcControllerConfig` (`controller_mpc.py`) | `standard` | `get_default_config` | — | `mpc` test marker only |
| `PIDControllerConfig` (`controller_pid.py`) | `standard` | `get_default_config` | — | rule 5 clean |
| `SimpleControllerConfig` (`controller_l1_example_controller.py`) | `standard` | `get_default_config` | — | Example component |
| `SmartDeviceConfig` (`generic_smart_device.py`) | `standard` | `get_default_config` | — | `identifier="Identifier"` placeholder — the preset ships a meaningless value |
| `ExtendedControllerConfig` (`configuration.py`) | `standard` | `get_default_config` | — | Belongs to `advanced_fuel_cell_controller.ExtendedController` |
| `LoadConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |
| `ElectricityDemandConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |
| `HouseholdWarmWaterDemandConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |

## Mobility

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `CarConfig` (`generic_car.py`) | `diesel`, `electric` | `get_default_diesel_config`, `get_default_ev_config` | — | Clean rule-1 fit; `electric` preferred over `ev` (rule 1 wants substance, not an acronym) |
| `CarBatteryConfig` (`advanced_ev_battery_bslib.py`) | `standard` | `get_default_config` | — | rule 5 clean |
| `ChargingStationConfig` (`controller_l1_generic_ev_charge.py`) | `standard` + constructor | `get_default_config` | `lower_threshold_charging_power_in_watt` (derived from the set name) | `charging_station_set: JsonReference` (LPG `ChargingStationSets`) is an identifier space *and* carries the rating inside the identifier (§conflict 7) |

## Occupancy, weather, building

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `UtspLpgConnectorConfig` (`loadprofilegenerator_utsp_connector.py`) | `standard` + constructor | `get_default_utsp_connector_config` | — | Q-P1.6 decision; `household`, `travel_route_set`, `transportation_device_set`, `charging_station_set` are all LPG `JsonReference` identifier spaces |
| `WeatherConfig` (`weather.py`) | `standard` + constructor | `get_default` | — | Q-P1.6 decision; `LocationEnum` has dozens of members. `get_default` is the only bare `get_default` spelling in the repo |
| `BuildingConfig` (`building/config.py`) | `standard` + constructor | `get_default_german_single_family_home` | `absolute_conditioned_floor_area_in_m2`, `number_of_apartments`, `max_thermal_building_demand_in_watt` and the 5 `*_u_value_*`/`*_area_in_m2` pairs | The `config_presets` branch ships `german_single_family_home`, which contradicts the Q-P1.6 refinement (§conflict 8) |
| `CSVLoaderConfig` (`csvloader.py`) | `standard` + constructor | *(none)* | — | No factory; every field (`csv_filename`, `column`, `loadtype`, `unit`) is caller data — a `for_csv_file(...)` constructor, not a preset (§conflict 9) |

## H2, CHP and fuel-cell chain

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `CHPConfig` (`generic_chp.py`) | `gas`, `hydrogen` | `get_default_config_chp`, `get_default_config_fuelcell` | `p_el`, `p_th`, `p_fuel` (from `thermal_power`) | Factory name says `fuelcell` but the class field that differs is `fuel_type=GREEN_HYDROGEN` — rule 1 says name the fuel |
| `CHPConfig` (`advanced_fuel_cell.py`) | `standard` | `get_default_config` | — | Name collision with the class above across modules; class-scoped presets make this harmless |
| `CHPConfigAdvanced` (`advanced_fuel_cell.py`) | *none* | *(none)* | — | Not a dataclass and not a `ConfigBase`: reads `mock_up_efficiencies.xlsx` in `__init__` keyed by `system_name="BlueGen BG15"`. Out of scope for presets; a lookup class if converted |
| `L1CHPControllerConfig` (`controller_l1_chp.py`) | `gas`, `gas_with_buffer`, `hydrogen`, `hydrogen_with_buffer` | `get_default_config_chp`, `get_default_config_chp_with_buffer`, `get_default_config_fuel_cell`, `get_default_config_fuel_cell_with_buffer` | — | Two crossed axes (fuel × buffer present) → 4 names for 2+2 (§conflict 2) |
| `GenericElectrolyzerConfig` (`generic_electrolyzer.py`) | `standard` | `get_default_config` | `min_power`, `max_power` (from `p_el`) | rule 5 clean |
| `L1ElectrolyzerControllerConfig` (`controller_l1_electrolyzer.py`) | `standard` | `get_default_config` | — | rule 5 clean |
| `ElectrolyzerConfig` (`generic_electrolyzer_h2.py`) | `alkaline` + constructor | `get_default_alkaline_electrolyzer_config`, `config_electrolyzer` | — | `config_electrolyzer(electrolyzer_name)` reads `electrolyzer_manufacturer_config.json` → constructor |
| `ElectrolyzerControllerConfig` (`controller_l1_electrolyzer_h2.py`) | `alkaline` + constructor | `get_default_electrolyzer_controller_config`, `control_electrolyzer` | — | Same JSON lookup; `control_*` spelling |
| `FuelCellConfig` (`generic_fuel_cell.py`) | `pem` + constructor | `get_default_pem_fuel_cell_config`, `config_fuel_cell` | — | `fuel_cell_manufacturer_config.json` lookup |
| `FuelCellControllerConfig` (`controller_l1_fuel_cell.py`) | `pem` + constructor | `get_default_fuel_cell_controller_config`, `control_fuel_cell` | — | Same JSON lookup |
| `RsocConfig` (`generic_rsoc.py`) | `standard` + constructor | `from_rsoc_name` | — | `rSOC_manufacturer_config.json` lookup; no default factory at all |
| `RsocControllerConfig` (`controller_l1_rsoc.py`) | `standard` + constructor | `config_rsoc` | — | Same lookup; 9 test uses |
| `RsocBatteryControllerConfig` (`controller_l2_rsoc_battery_system.py`) | `standard` + constructor | `config_rsoc` | — | Lookup plus a free-text `operation_mode: str` — should be an enum before it becomes wire format |
| `PTXControllerConfig` (`controller_l2_ptx_energy_management_system.py`) | `standard` + constructor | `control_electrolyzer` | — | Lookup; no default factory |
| `XTPControllerConfig` (`controller_l2_xtp_fuel_cell_ems.py`) | `standard` + constructor | `control_fuel_cell` | — | Lookup; no default factory |
| `ElectrolyzerWithStorageConfig` (`generic_electrolyzer_and_h2_storage.py`) | `standard` | `get_default_config` | — | rule 5 clean |
| `ElectrolyzerWithHydrogenStorageConfig` (`generic_electrolyzer_and_h2_storage.py`) | `standard` | `get_default_config` | — | Two classes with the identical factory name in one module |
| `AdvElectrolyzerConfig` (`configuration.py`) | `standard` | *(none)* | — | No factory; legacy, deletion candidate |

## Other and examples

| Config class (module) | Proposed presets (canonical first) | Replaces factory | AUTO fields | Rule flags / notes |
|---|---|---|---|---|
| `ExampleComponentConfig` (`example_component.py`) | `standard` | `get_default_example_component` | — | Example/test only (8 test uses, 0 setups) |
| `ComponentNameConfig` (`example_template.py`) | `standard` | `get_default_template_component` | — | Template file; the preset name is copied by every new component author — worth getting right |
| `ExampleTransformerConfig` (`example_transformer.py`) | `standard` | `get_default_transformer` | — | Example only |
| `SimpleStorageConfig` (`example_storage.py`) | `thermal` | `get_default_thermal_storage` | — | Only factory names a medium; `thermal` keeps rule 1 |
| `RandomNumbersConfig` (`random_numbers.py`) | `standard` | `get_default_config` | — | Test helper |
| `PhysicsConfig` (`configuration.py`) | *none* | `get_properties_for_energy_carrier` | — | Physical-constant table keyed by `LoadTypes`, not a component config — exempt from presets |
| `EmissionFactorsAndCostsForFuelsConfig` (`configuration.py`) | *none* | `get_values_for_year` | — | Year-keyed data table, not a component config — exempt |
| `EmissionFactorsAndCostsForDevicesConfig` (`configuration.py`) | *none* | `get_values_for_year` | — | Year-keyed data table — exempt |

## Constructor classes

Lookup-shaped classes: an identifier keys an external table (enum, JSON, CSV, library). Each gets **one** preset `standard` (or the one technology name it defaults to) plus a named constructor. Names avoid `get_default*`/`get_*config*` so no default-discovery heuristic picks them up.

| Config class | Constructor | Identifier space |
|---|---|---|
| `WeatherConfig` | `for_location(location: LocationEnum \| str, *, direct_filepath=None, direct_data_source=None)` | `LocationEnum` (dozens) + arbitrary file |
| `BuildingConfig` | `for_tabula_code(building_code: str, *, heat_capacity_class="medium")` | TABULA codes (hundreds) |
| `UtspLpgConnectorConfig` | `for_household(household: JsonReference \| list[JsonReference], *, travel_route_set=None, transportation_device_set=None, charging_station_set=None)` | LPG `Households`, `TravelRouteSets`, `TransportationDeviceSets`, `ChargingStationSets` |
| `ChargingStationConfig` | `for_charging_station_set(charging_station_set: JsonReference)` | LPG `ChargingStationSets` |
| `CSVLoaderConfig` | `for_csv_file(csv_filename: str, column: int, loadtype: lt.LoadTypes, unit: lt.Units, *, sep=";", decimal=".", multiplier=1.0)` | any file on disk |
| `WindturbineConfig` | `for_turbine_type(turbine_type: str, hub_height: float)` | windpowerlib turbine library |
| `AirConditionerConfig` | `for_device(manufacturer: str, model_name: str, *, scale_factor=1.0)` | `smart_devices` database |
| `GenericHeatPumpConfig` | `for_device(manufacturer: str, heat_pump_name: str)` | heat-pump device table |
| `ElectrolyzerConfig`, `ElectrolyzerControllerConfig` | `for_device(electrolyzer_name: str, *, operation_mode=None)` | `electrolyzer_manufacturer_config.json` |
| `FuelCellConfig`, `FuelCellControllerConfig`, `XTPControllerConfig` | `for_device(fuel_cell_name: str, *, operation_mode=None)` | `fuel_cell_manufacturer_config.json` |
| `RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig` | `for_device(rsoc_name: str, *, operation_mode=None)` | `rSOC_manufacturer_config.json` |
| `PTXControllerConfig` | `for_device(electrolyzer_name: str, operation_mode: str)` | `electrolyzer_manufacturer_config.json` |

## Rule conflicts found

1. **Combustion-technology qualifiers are asymmetric** (`GenericBoilerConfig`). Rule 1. `condensing_gas` / `oil` / `pellets` mix a technology qualifier into two names and omit it from three, because the legacy spellings did (`get_scaled_condensing_gas_boiler_config` vs `get_scaled_conventional_pellet_boiler_config`; `conventional` carries no information — nothing is non-conventional). *Resolution:* keep the qualifier only where it distinguishes two presets of the same fuel; drop `conventional` everywhere. `condensing_gas` stays because a `low_temperature_gas` preset is plausible.
2. **Crossed axes produce a combinatorial name set** (`L1CHPControllerConfig` 2×2; `GenericBoilerControllerConfig` control law × fuel). Rule 1/2. Every added axis doubles the wire vocabulary and the `describe` listing. *Resolution:* one axis per preset set — the *primary* axis (fuel for generators, control law for controllers); the secondary axis becomes a field override, exactly as amendment A2 does for boolean flags. For `L1CHPControllerConfig` that means `gas` and `hydrogen`, with the buffer expressed by the field the `_with_buffer` factories actually set.
3. **Unsized/sized factory pairs are not variants** (`GenericBoilerConfig` `get_default_*`/`get_scaled_*` ×2, `HeatDistributionControllerConfig`, `ElectricHeatingControllerConfig`, `SimpleHotWaterStorageConfig`, `SimpleDHWStorageConfig`, `SolarThermalSystemConfig`, both hplib heat pumps, `BatteryConfig`, `PVSystemConfig`). Rule 2. 12 `get_scaled_*` and their `get_default_*` twins describe one variant in two states, which is precisely what `AUTO` replaces. *Resolution:* the `get_scaled_*` factory becomes the bare preset; the `get_default_*` twin becomes a rating-suffixed preset only if its number is a real catalogue rating (`condensing_gas_12kw`, `air_water_8kw`) and is dropped otherwise (`SolarThermalSystemConfig`'s capex twin, `ElectricHeatingControllerConfig`).
4. **Sizing options are laws, not variants** (`SimpleHotWaterStorageConfig.sizing_option`, 5 `HotWaterStorageSizingEnum` members). Rule 1. Minting `buffer_for_heat_pump`, `buffer_for_gas_heater`, … would put the *provider's* identity into the consumer's preset name, which the P1 binding rule (`sources`) already handles. *Resolution:* one preset `buffer`; `sizing_option` stays a field.
5. **A factory on the component class** (`PVSystem.get_default_config`, a third factory for `PVSystemConfig` with `module_name="Hanwha HSL60P6-PA-4-250T [2013]"` against the config class's `"Trina Solar TSM-435NE09RC.05"`). Rule 3. The repo's setups use `get_scaled_pv_system` (12) and `get_default_pv_system` (15), never this one, so rule 3 is decidable — but the module value diverges silently. *Resolution:* delete the component-class factory during the sweep; canonical preset `rooftop` from `get_scaled_pv_system`.
6. **Rule 2 and rule 5 are mutually exclusive for rating-only classes** (`BatteryConfig`, `SimpleHotWaterStorageConfig`, both hplib heat pumps, `SolarThermalSystemConfig`). Rules 2+5. One variant plus one catalogue device means rule 5 demands `standard` and then forbids it. *Resolution:* amendments A1 and A3 — a rating suffix never triggers rule 5's retirement clause, and where a technology name exists (`air_water`, `flat_plate`) use it instead of `standard`.
7. **A rating inside an identifier** (`ChargingStationConfig`: `ChargingStationSets.Charging_At_Home_with_11_kW`, from which `lower_threshold_charging_power_in_watt` is parsed). Rule 2. There is no sizable template because the rating *is* the identifier. *Resolution:* treat it as a lookup class (`for_charging_station_set`), one preset `standard`; rule 2's suffix does not apply to lookup classes.
8. **A shipped preset name violates the Q-P1.6 refinement** (`BuildingConfig.presets.german_single_family_home` on `config_presets`). Rule 5 + Q-P1.6. Building is a TABULA-code lookup and must expose exactly one preset named `standard`. *Resolution:* rename to `standard` before the sweep, while nothing outside the branch depends on it; `german_single_family_home` becomes the docstring of the default `building_code`.
9. **Classes whose every field is caller data** (`CSVLoaderConfig`, `SetTemperatureConfig`). Rule 5. A `standard` preset for a CSV loader would ship an arbitrary filename and column index; nothing about it is a default. *Resolution:* `CSVLoaderConfig` is constructor-only (no preset), and the sweep's contract test must permit "constructor, no preset" as a legal state; `SetTemperatureConfig` is not a component config and is exempt.
10. **17 classes have no factory to convert.** Rule 3 (no canonical reading). 12 of them are the legacy plain dataclasses in `configuration.py` plus `SetTemperatureConfig`, `CSVLoaderConfig`, `RsocConfig`, `PTXControllerConfig`, `XTPControllerConfig`. *Resolution:* split the sweep — the `configuration.py` legacy block (`GasHeaterConfig`, `GasControllerConfig`, `CHPControllerConfig`, `LoadConfig`, `ElectricityDemandConfig`, `HouseholdWarmWaterDemandConfig`, `HydrogenStorageConfig`, `AdvElectrolyzerConfig`, `PVConfig`) is proposed for **deletion** rather than conversion, since each duplicates a live component config; the three data tables (`PhysicsConfig`, both `EmissionFactorsAndCosts*Config`) are declared exempt; the four lookup classes get constructors.

## Legacy spelling inventory

103 factory classmethods (excluding `get_default_connections_*`, `get_cost_capex`, `get_component_kpi_entries`) in **79 distinct spellings**:

| Pattern | Count | Examples |
|---|---|---|
| `get_default_<x>_config` | 34 | `get_default_condensing_gas_boiler_config`, `get_default_heat_distribution_config`, `get_default_simplehotwaterstorage_config` |
| `get_default_config` (bare) | 19 | `advanced_battery_bslib`, `generic_pv_system`, `configuration` (×2), `generic_electrolyzer_and_h2_storage` (×2) |
| `get_default_config_<variant>` | 14 | `get_default_config_chp`, `get_default_config_fuelcell`, `get_default_config_const_power`, `get_default_config_heat_source_controller_dhw` |
| `get_scaled_<x>` | 12 | `get_scaled_battery`, `get_scaled_pv_system`, `get_scaled_advanced_hp_lib`, `get_scaled_conventional_pellet_boiler_config` |
| `get_default_<x>` (no `_config`) | 10 | `get_default_pv_system`, `get_default_thermal_storage`, `get_default_transformer`, `get_default_german_single_family_home` |
| `get_<x>_default_config` | 5 | `get_electricity_meter_default_config`, `get_gas_meter_default_config`, `get_sumbuilder_default_config` |
| `config_<x>` | 4 | `config_rsoc` (×2), `config_electrolyzer`, `config_fuel_cell` |
| `control_<x>` | 2 | `control_electrolyzer`, `control_fuel_cell` |
| `get_config_<x>` | 1 | `get_config_based_on_building_efficiency` |
| `get_default` (bare) | 1 | `WeatherConfig.get_default` |
| `from_<x>` | 1 | `RsocConfig.from_rsoc_name` |
| unclassifiable | 3 | `get_solar_thermal_system_controller_config`, `get_electric_heating_config_based_on_building_efficiency`, `get_air_conditioner_config_from_database` |
| `read_config` (static JSON readers, not factories) | 9 | `controller_l1_rsoc`, `generic_rsoc`, `generic_fuel_cell`, `generic_electrolyzer_h2`, … |
