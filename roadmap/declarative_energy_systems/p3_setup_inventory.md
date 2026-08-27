# P3 setup inventory — companion to `p3_recording_requirements.md`

Factual survey of `system_setups/` taken 2026-08-27 on branch `energy_system_files` (read-only;
produced by a subagent, cited by the requirements document). Numbers here back the counts in the
main document; nothing here is a requirement.

---

Repo `/home/noah/hisim/HiSim`, branch `energy_system_files`. Read-only survey; nothing was modified.

**Scope**: 24 setup modules (`system_setups/*.py` minus `__init__.py`), 7 849 lines total. Every one defines exactly one top-level `def setup_function(...)`; **no module has a second `setup_function` variant, no module has an `if __name__ == "__main__"` block, and no module defines a class.** (`electrolyzer_with_renewables.py:156` has the invocation in a trailing comment only.)

---

## 1. Setups

Legend for the last column: `pre` = `connect_only_predefined_connections`, `aci` = `add_component_input_and_connect`, `acis` = `add_component_inputs_and_connect`, `aco` = `add_component_output`, `cdi` = `connect_dynamic_input`. No setup anywhere calls `connect_with_default_connections_dynamically` or `connect_similar_inputs`.

| File | Purpose | `add_component` sites (runtime) | `connect_automatically=True` | `connect_input` | dynamic / aggregator | MHC? |
|---|---|---|---|---|---|---|
| `simple_system_setup_one.py` | Two random-number series summed by a `SumBuilder`. | 3 | 0 | 2 | — | — |
| `simple_system_setup_two.py` | Random numbers + `ExampleTransformer` + sum. | 4 | 0 | 3 | — | — |
| `simple_weather_data_import.py` | Pre-fetches DWD weather into `inputs/`; **adds no components**, returns a non-`Component`. | 0 | 0 | 0 | — | — |
| `default_connections.py` | SFH: occupancy, weather, PV, building, `GenericHeatPump` + controller, electricity meter. | 7 | 0 | 2 | 4 pre, 3 aci | — |
| `basic_household.py` | Same household, all wiring done before the adds. | 7 | 0 | 2 | 4 pre, 3 aci | — |
| `basic_household_with_weather_data_request.py` | `basic_household` + live DWD weather fetch at setup time. | 7 | 0 | 2 | 4 pre, 3 aci | — |
| `basic_household_only_heating.py` | Gas boiler + buffer storage + HDS, heating only. | 8 | 5 | 0 | 1 pre | — |
| `automatic_default_connections.py` | Wholly auto-wired hplib heat-pump household (SH + DHW). | 12 | 10 | 1 | — | — |
| `air_conditioned_house.py` | Seville SFH cooled by an `AirConditioner`; PV; literal `BuildingConfig`. | 6 | 3 | 1 | 2 pre | — |
| `dynamic_components.py` | EMS demo: 2 batteries + 2 fuel cells behind an `L2GenericEnergyManagementSystem`. | 8 | 0 | 0 | 1 pre, 2 acis, 4 aci, **4 aco**, 4 cdi | — |
| `electrolyzer_with_renewables.py` | CSV wind profile → rectifier → controller → electrolyzer. | 4 | 0 | 4 | — | — |
| `household_gas_solar_thermal.py` | Gas boiler + solar-thermal DHW, no PV/battery/EMS. | 13 | 10 | 5 | 1 pre, 1 aci | ✅ |
| `simple_air_conditioner_household_building_sizer.py` | Building + weather + `SimpleAirConditioner` + controller only. | 4 | 3 | 1 | 1 pre | ✅ |
| `household_heatpump_building_sizer.py` | hplib HP household + PV + battery + EMS + meter. | 15 sites → **14 or 12** | 11 | 1 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_gas_building_sizer.py` | Gas boiler variant of the above (+ gas meter). | 15 → 14 / 12 | 11 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_oil_building_sizer.py` | Oil boiler + fuel meter. | 15 → 14 / 12 | 11 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_pellets_building_sizer.py` | Pellet boiler + fuel meter. | 15 → 14 / 12 | 11 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_wood_chips_building_sizer.py` | Wood-chip boiler + fuel meter. | 15 → 14 / 12 | 11 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_hydrogen_boiler_building_sizer.py` | Hydrogen boiler + gas meter. | 15 → 14 / 12 | 11 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_district_heating_building_sizer.py` | District-heating station + controller + fuel meter. | 14 → 13 / 11 | 10 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_electric_heating_building_sizer.py` | Direct electric heating, no HDS/buffer. | 11 → 10 / 8 | 7 | 0 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_gas_solar_thermal_building_sizer.py` | Gas boiler + solar thermal + PV/battery/EMS. | 17 → 16 / 14 | 12 | 5 | 1 aco, 1 cdi, 1 aci | ✅ |
| `household_heatpump_solar_thermal_building_sizer.py` | hplib HP + solar thermal; HP controllers added *last*, after the result-path block. | 17 → 16 / 14 | 10 | 6 | 2 pre, 1 aco, 1 cdi, 1 aci | ✅ |
| `household_heatpump_car_building_sizer.py` | hplib HP + PV/battery/EMS + **N electric cars from the occupancy data**. | 21 sites, 11 fixed + 3·N + (3 or 1) | 11 | 1 | 3 pre, 2 aco, 1 cdi, 3 aci | ✅ |

"14 or 12" = the `if share_of_maximum_pv_potential != 0 and energy_system_config_.use_battery_and_ems:` fork (e.g. `household_heatpump_building_sizer.py:399`); the true branch adds meter + battery + EMS, the false branch adds only an auto-connected meter.

12 setups build via `read_in_configs` / `ModularHouseholdConfig` (`hisim/building_sizer_utils/interface_configs/modular_household_config.py`); `household_gas_solar_thermal.py` and `simple_air_conditioner_household_building_sizer.py` read only the archetype half.

---

## 2. Python-only behaviour, per setup

### 2a. `SimulationParameters` mutation — 23 of 24 setups

Every setup except `simple_weather_data_import.py` opens with `if my_simulation_parameters is None: my_simulation_parameters = SimulationParameters.<factory>(...)` and then `my_sim.set_simulation_parameters(...)`. Factories used: `full_year` (`household_heatpump_building_sizer.py:91`, `simple_air_conditioner_household_building_sizer.py:68`, `simple_system_setup_one.py:29`, `simple_system_setup_two.py:37`), `full_year_with_only_plots` (`basic_household.py:65`, `basic_household_only_heating.py:57`, `basic_household_with_weather_data_request.py:89`, `air_conditioned_house.py:87`, `household_gas_solar_thermal.py:81`), `full_year_all_options` (`default_connections.py:52`, `automatic_default_connections.py:51`, `dynamic_components.py:43`, `electrolyzer_with_renewables.py:77`).

**Post-processing option mutation — 14 setups.** `simple_system_setup_one.py:30`; `air_conditioned_house.py:88` (`enable_plots_only()`) and `:89` (`extend` with 4 options); `simple_air_conditioner_household_building_sizer.py:71,74,77`; the 11 building-sizer files each append 5–6 options, e.g. `household_heatpump_building_sizer.py:97-106` (`PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION`, `COMPUTE_OPEX`, `COMPUTE_CAPEX`, `COMPUTE_KPIS`, `WRITE_KPIS_TO_JSON`, `WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER`); `household_oil_building_sizer.py:125` uses `extend` instead.

**Other parameter fields — 11 setups.** `logging_level = 3` (`household_heatpump_building_sizer.py:111` and siblings). `cache_dir_path` set only when a hard-coded cluster path exists — `household_heatpump_building_sizer.py:94-96`: `cache_dir_path_simuparams = "/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/"` guarded by `os.path.exists`. `my_simulation_parameters.country = "DE"` at `household_gas_building_sizer.py:117` (that file only). `my_simulation_parameters.year` is temporarily reassigned and restored around occupancy construction: `household_heatpump_building_sizer.py:231-241`.

**Result path / singletons — 12 setups.** All 11 sizers plus `simple_air_conditioner_household_building_sizer.py` end with a `re.findall(r"\-?\d+", ...)` on the config filename, a `SortingOptionEnum` choice, a `SingletonSimRepository().set_entry(RESULT_SCENARIO_NAME, ...)` write and a `ResultPathProviderSingleton().set_important_result_path_information(...)` call — e.g. `household_heatpump_building_sizer.py:464-501`, `simple_air_conditioner_household_building_sizer.py:159-189`.

### 2b. External reads at setup time

- `read_in_configs(my_sim.my_module_config)` — 13 setups: `household_heatpump_building_sizer.py:76`, `household_gas_building_sizer.py:77`, `household_oil_building_sizer.py:103`, `household_pellets_building_sizer.py:77`, `household_wood_chips_building_sizer.py:77`, `household_hydrogen_boiler_building_sizer.py:77`, `household_district_heating_building_sizer.py:109`, `household_electric_heating_building_sizer.py:76`, `household_gas_solar_thermal_building_sizer.py:75`, `household_heatpump_solar_thermal_building_sizer.py:74`, `household_heatpump_car_building_sizer.py:85`, `household_gas_solar_thermal.py:71`, `simple_air_conditioner_household_building_sizer.py:55`. Nearly every subsequent config value is read off `arche_type_config_` / `energy_system_config_` (~20 fields in `household_heatpump_building_sizer.py:120-218`).
- `household_gas_building_sizer.py:81` additionally writes back: `my_sim.my_module_config = my_config.to_dict()`.
- CSV read: `air_conditioned_house.py:99` `pd.read_csv(utils.HISIMPATH["housing_reference_temperatures"])`, then `.set_index("Location").loc["ES", ...]` at `:100`.
- Network fetch: `basic_household_with_weather_data_request.py:61-70` constructs `WeatherDataImport` (DWD 10-min request), and the resulting `weather_data_import.csv_path` / `.weather_data_source` feed `WeatherConfig` at `:111-112`. `simple_weather_data_import.py:65-74` is the same fetch with **no components at all** and a non-`Component` return value.
- Filesystem probes: `os.path.exists("/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/")` (`household_heatpump_building_sizer.py:95`) and `os.path.exists("/benchtop/2024-k-rieck-hisim/lpg-utsp-cache")` (`:167`), in all 11 sizers.
- Filesystem *mutation*: `air_conditioned_house.py:71-74` deletes every file in `../hisim/inputs/cache` before building anything.
- No setup reads an environment variable directly (no `os.environ` / `os.getenv` anywhere in `system_setups/`).

### 2c. Loops generating N components — 1 setup

`household_heatpump_car_building_sizer.py`: cars come from the occupancy object, so their number is data-dependent.
- `:395` `my_car_information = generic_car.GenericCarInformation(my_occupancy_instance=my_occupancy)`
- `:402` `for idx, car_information_dict in enumerate(my_car_information.data_dict_for_car_component.values())` → one `Car` each, name `ComponentID(car_information_dict["car_name"] + f"_{idx}")` at `:403`
- `:417` `for car in my_cars:` → one `CarBattery` + one `L1Controller` each, names `f"CarBattery_{car_number}"` / `f"L1EVChargeControl_{car_number}"` (`:420`, `:431`)
- `:446`, `:482`, `:556`, `:568` — four more `zip(...)` loops that wire, EMS-feed, and register the triples.

Also loop-shaped but not component-generating: `for household_string in arche_type_config_.lpg_households` in all 11 sizers (`household_heatpump_building_sizer.py:180`), building a `List[JsonReference]` for one occupancy config; and `for file in dir_cache.iterdir()` (`air_conditioned_house.py:73`).

### 2d. Conditionals that change the component graph or wiring

- **PV/battery/EMS fork** (11 sizers): `household_heatpump_building_sizer.py:399` — three components exist or not, and the meter is wired differently in each branch (`:454-460`).
- **Heat-pump primary-source branch** — reads a value off the *constructed* component: `household_heatpump_building_sizer.py:339` `if my_heatpump.parameters["Group"].iloc[0] == 1.0 or ... == 4.0:` → `connect_input(TemperatureInputPrimary, weather.DailyAverageOutsideTemperatures)`, `else: raise KeyError`. Same at `automatic_default_connections.py:140`, `household_heatpump_car_building_sizer.py:340`, `household_heatpump_solar_thermal_building_sizer.py:329`.
- **Car surplus charging** (`household_heatpump_car_building_sizer.py:483`): `if car_surplus_charging:` selects between an EMS-controlled feed (tags `CAR_BATTERY, ELECTRICITY_CONSUMPTION_EMS_CONTROLLED`, weight 5, plus a dispatch output and a `connect_dynamic_input`) and a plain uncontrolled feed (weight 999). It also flips `battery_set_soc` between 0.4 and 1.0 at `:518-521`.
- **HDS emitter type**: `if energy_system_config_.heat_distribution_system == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING: ... elif ... RADIATOR ... else: raise ValueError` (`household_heatpump_building_sizer.py:129-134`), in all 11 sizers.
- **PV sizing fork**: `if pv_power_in_watt is None: get_scaled_pv_system(...) else: get_default_pv_system(...)` (`household_heatpump_building_sizer.py:262-273`).
- **Occupancy year fork**: `if my_simulation_parameters.year > 2025:` (`household_heatpump_building_sizer.py:231`).
- **Guard raises**, 3–5 per sizer, e.g. `household_heatpump_building_sizer.py:122` (`heating_system != HEAT_PUMP`), `:186` (empty household list), `:188` (`TypeError` on wrong list type).

### 2e. Arithmetic and derived sizing done in the setup

Most sizing has already migrated into the sizing kernel (`SizingContext` / `.resolve(...)`); the residue is small.

- `air_conditioned_house.py:159-160,177`: `pv_co2_footprint = pv_system_power_in_watt * 1e-3 * 130.7`, `pv_cost = ... * 535.81`, `maintenance = 0.01 * pv_cost`.
- `household_heatpump_building_sizer.py:292-293`: `specific_heating_load_of_building_in_watt_per_m2 = max_thermal_building_demand_in_watt / scaled_conditioned_floor_area_in_m2` (10 sizers; `household_wood_chips_building_sizer.py:304` and `household_district_heating_building_sizer.py:330` spell it slightly differently).
- Unit conversions off the archetype config: `pv_rooftop_capacity_in_kilowatt * 1000` and `norm_heating_load_in_kilowatt * 1000` (`household_heatpump_building_sizer.py:149,160`).
- `household_gas_solar_thermal_building_sizer.py:355`: solar-thermal `area_m2 = 4 * number_of_apartments`.
- **Config values threaded from one component's *information* object into another's factory** — the dominant pattern, present in 15 setups. `BuildingInformation` supplies `max_thermal_building_demand_in_watt`, `scaled_conditioned_floor_area_in_m2`, `roof_area_in_m2`, `number_of_apartments`, `set_heating/cooling_temperature_for_building_in_celsius`; `HeatDistributionControllerInformation` supplies `water_mass_flow_rate_in_kg_per_second`, `heat_distribution_system_type`, `set_heating_threshold_temperature_in_celsius`. Example: `basic_household_only_heating.py:82-105`, `household_heatpump_building_sizer.py:286-294, 328-331, 353-355, 364-367, 376-384`. In the YAML format these are `sizing_sources`, so they *are* expressible — but only for classes whose fields carry sizing laws.
- **Config values threaded between two configs directly**: `basic_household_only_heating.py:119-120` and `household_gas_solar_thermal.py:152-153` pass `my_gas_heater_config.minimal/maximal_thermal_power_in_watt` into the boiler *controller* factory.
- **Post-construction config mutation** — ubiquitous, ~20 lines per sizer: `household_heatpump_building_sizer.py:194-218` mutates 15 fields of a `preset_standard` `BuildingConfig`; `:275-276` sets `azimuth`/`tilt`; `:310` sets `mode`; `:332` sets `with_domestic_hot_water_preparation`; `dynamic_components.py:52-63` mutates six battery-config fields including `component_id` and `source_weight`. This maps onto the YAML `preset` + sparse `config` override, provided the recorder diffs against a fresh preset build.

### 2f. `SimRepository` writes

Only `SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.RESULT_SCENARIO_NAME, ...)` — 12 setups (`household_heatpump_building_sizer.py:484`, `simple_air_conditioner_household_building_sizer.py:176`, and the other 10 sizers). No setup writes any other singleton key or touches the per-simulator `SimRepository`.

### 2g. Configs coming from external files

- `csvloader.CSVLoaderConfig(csv_filename="wind_generated_power_1_min.csv", ...)` — `electrolyzer_with_renewables.py:86-97`.
- `weather.WeatherConfig(source_path=weather_data_import.csv_path, ...)` — `basic_household_with_weather_data_request.py:108-114`, path produced by the network fetch above.
- `weather.WeatherConfig.get_default(..., weather_direct_filepath=arche_type_config_.weather_filepath, weather_direct_data_source=weather_datasource)` — `household_heatpump_building_sizer.py:252-256`, with the data-source enum looked up from a string at `:142-143`.
- `Car` configs derive from `GenericCarInformation`, which reads the LPG/UTSP occupancy result (`household_heatpump_car_building_sizer.py:395`).

---

## 3. Component classes used

**41 distinct component classes** are registered across the 24 setups (38 found by static analysis of `add_component` targets, plus `Car`, `CarBattery` and `L1Controller`, which are only added inside loops).

### 3a. Have `@preset` / `@constructor` → recorder can emit `preset:` / `constructor:` (9 classes)

Every `@preset`/`@constructor` in the whole repo (19 decorated methods, 11 config classes) lives in these files:

| Component class | Config class | Presets | Constructors |
|---|---|---|---|
| `building.Building` | `BuildingConfig` (`hisim/components/building/config.py:86,96`) | `standard` | `for_tabula_code` |
| `weather.Weather` | `WeatherConfig` (`hisim/components/weather.py:466,478`) | `standard` | `for_location` |
| `loadprofilegenerator_utsp_connector.UtspLpgConnector` | `UtspLpgConnectorConfig` (`:122,135`) | `standard` | `for_household` |
| `electricity_meter.ElectricityMeter` | `ElectricityMeterConfig` (`:69`) | `standard` | — |
| `controller_l2_energy_management_system.L2GenericEnergyManagementSystem` | `EMSConfig` (`:79`) | `optimize_own_consumption` | — |
| `heat_distribution_system.HeatDistribution` | `HeatDistributionConfig` (`:119`) | `standard` | — |
| `heat_distribution_system.HeatDistributionController` | `HeatDistributionControllerConfig` (`:917`) | `standard` | — |
| `generic_boiler.GenericBoiler` | `GenericBoilerConfig` (`:175-246`) | `condensing_gas`, `condensing_gas_12kw`, `oil`, `oil_12kw`, `pellets`, `wood_chips`, `hydrogen` | — |
| `generic_boiler.GenericBoilerController` | `GenericBoilerControllerConfig` (`:1036,1048`) | `modulating`, `on_off` | — |

Two more preset-bearing config classes exist that no setup instantiates as a component (`GenericBoilerConfig`'s `oil_12kw` is unused by setups).

By instantiation count these 9 classes cover a large share of the fleet: `Weather` 20, `Building` 19, `UtspLpgConnector` 19, `ElectricityMeter` 16, `HeatDistribution` 13, `HeatDistributionController` 13, `L2GenericEnergyManagementSystem` 12, `GenericBoiler` 8, `GenericBoilerController` 8 — 128 of the ~250 component instantiations.

### 3b. No preset/constructor → recorder must emit a literal `config:` block (32 classes)

`generic_pv_system.PVSystem` (17 uses), `advanced_battery_bslib.Battery` (13), `simple_water_storage.SimpleDHWStorage` (13), `simple_water_storage.SimpleHotWaterStorage` (12), `more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib` (4), `…ControllerSpaceHeating` (4), `…ControllerDHW` (4), `fuel_meter.FuelMeter` (4), `gas_meter.GasMeter` (4), `random_numbers.RandomNumbers` (4), `generic_heat_pump.GenericHeatPump` (3), `generic_heat_pump.GenericHeatPumpController` (3), `solar_thermal_system.SolarThermalSystem` (3), `solar_thermal_system.SolarThermalSystemController` (3), `advanced_fuel_cell.CHP` (2), `sumbuilder.SumBuilderForTwoInputs` (2), and one use each of `air_conditioner.AirConditioner`, `air_conditioner.AirConditionerController`, `simple_air_conditioner.SimpleAirConditioner`, `simple_air_conditioner.SimpleAirConditionerController`, `generic_district_heating.DistrictHeating`, `generic_district_heating.DistrictHeatingController`, `generic_electric_heating.ElectricHeating`, `generic_electric_heating.ElectricHeatingController`, `csvloader.CSVLoader`, `transformer_rectifier.Transformer`, `controller_l1_electrolyzer_h2.ElectrolyzerController`, `generic_electrolyzer_h2.Electrolyzer`, `example_transformer.ExampleTransformer`, `generic_car.Car`, `advanced_ev_battery_bslib.CarBattery`, `controller_l1_generic_ev_charge.L1Controller`.

Non-component helper classes constructed inside setups (never registered, so no entry, but their *outputs* feed other entries' configs): `building.BuildingInformation` (12), `heat_distribution_system.HeatDistributionControllerInformation` (13), `generic_car.GenericCarInformation` (1), `weather_data_import.WeatherDataImport` (2).

### 3c. D13 overlap — zero

`roadmap/declarative_energy_systems/plan.md:72` names the 3 zombies (`controller_l1_building_heating`, `controller_l1_heatpump`, `controller_l1_generic_runtime`) and the 6 defective (`generic_battery` ×2, `generic_ev_charger` ×4). `grep` for all five module names over `system_setups/*.py` returns nothing. **None of the named D13 classes appear in any setup.** The "5 legitimately data-dependent" members of the 14 are not enumerated anywhere in `roadmap/declarative_energy_systems/`, so that part cannot be checked. Note the car setup uses `controller_l1_generic_ev_charge.L1Controller` and `advanced_ev_battery_bslib.CarBattery` — different modules from the defective `generic_ev_charger` / `generic_battery`.

---

## 4. Golden / regression suites

### 4a. The real numeric gate: `golden_references/` (KPI comparison)

- **Config**: `scripts/golden_config.json` — 8 setups × 2 parameter sets = 16 pairs. Setups: `household_district_heating`, `household_electric_heating`, `household_gas`, `household_heatpump`, `household_hydrogen_boiler`, `household_oil`, `household_pellets`, `household_wood_chips` (all `_building_sizer.py`). Parameter sets: `one_week_60s` (`one_week_only`, 2021, 60 s) and `full_year_60s` (`full_year`, 2021, 60 s), both with `["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]`.
- **What is compared**: `all_kpis.json`, flattened to `{dotted.key: value}`, numeric with `rel_tol = 1e-9, abs_tol = 0`; non-numeric exactly. Explicitly **never** plots, PDFs, logs or raw CSVs (`golden_references/README.md`). `manifest.json` is informational and not read by the checker.
- **Drivers**: `scripts/golden_check.py` (gate), `scripts/golden_update.py` (bless), `scripts/golden_validate.py` (offline-runnable / KPI-complete probe), `scripts/golden_matrix.py`, `scripts/runner.py`, `scripts/golden_kpis.py`.
- **CI**: `.github/workflows/golden-check.yml` (week pairs, every PR/push to main), `golden-year.yml` (full-year, only after quality + tests + golden-check are green), `golden-update.yml` (bless via PR).
- **Note for the recorder**: the 4 non-golden sizers — `household_gas_solar_thermal`, `household_heatpump_solar_thermal`, `household_heatpump_car`, `simple_air_conditioner` — are **not** covered by any numeric gate.

### 4b. JSON/Python parity gate

`.github/workflows/golden-json-check.yml` — **blocking**. Runs the same 8 setups expressed as `.scenario.json` with the same parameters and compares their KPIs against the same `golden_references/`. This is the existing precedent for exactly the equivalence P3 must establish for YAML.

### 4c. `tests/test_json_configs.py` — round-trip structure check (`@pytest.mark.jsonconfig`)

Parametrised over **every** `system_setups/*.py`. Builds the simulator from Python, writes JSON via `write_json_for_initialized_simulator`, re-initializes from JSON, and asserts `len(simulator.wrapped_components)` and `len(simulator.all_outputs)` match. `one_week_only(2021, 3600)`, `log_connections = True`. Skips only on a missing optional dependency (`wetterdienst`). **No values compared, only counts.**

### 4d. `@pytest.mark.system_setups` end-to-end smoke tests

All of these assert artefact existence only — `finished.flag`, a non-empty results dir, sometimes a specific file. **None compares numbers.**

| Test file | Setups | Params | Asserts |
|---|---|---|---|
| `test_system_setups_households_for_building_sizer.py` | all 12 sizer setups (incl. car and both solar-thermal) | `one_day_only(2024, 900)` + CAPEX/OPEX/KPI/JSON options | `finished.flag`, `all_kpis.json` parses, has finite numeric values, has a `Total electricity consumption` KPI |
| `test_system_setups_basic_household.py` | `basic_household` | `one_day_only(2021, 60)` | dir + `finished.flag` |
| `test_system_setups_basic_household_with_all_resultfiles.py` | `basic_household` | `one_day_only(2021, 900)` + 18 post-processing options | each option's expected file exists |
| `test_system_setups_basic_household_network_chart.py` | `basic_household` | + `MAKE_NETWORK_CHARTS` | three chart PNG prefixes exist |
| `test_system_setups_default_connections.py` | `default_connections` | + CSV export | `all_results.csv` exists |
| `test_system_setups_automatic_default_connections.py` | `automatic_default_connections` | `one_day_only(2021, 60)` | dir + flag |
| `test_system_setups_basic_household_only_heating.py` | `basic_household_only_heating` | " | dir + flag |
| `test_system_setups_basic_household_with_weather_data_request.py` | that setup | " (skips without `wetterdienst`) | dir + flag |
| `test_system_setups_electrolyzer_with_renewables.py` | `electrolyzer_with_renewables` | " | dir + flag |
| `test_system_setups_simple.py` | `simple_system_setup_one/two` | " | dir, flag, a figure file |
| `test_system_setups_simple_weather_data_import.py` | that setup | " | fetch succeeded |
| `test_system_setups_dynamic_components.py` | `dynamic_components` | `one_day_only(2021, 60)` | dir, flag — marked `@pytest.mark.extendedbase`, **not** `system_setups` |

`tests/test_simple_air_conditioner_household_building_sizer.py` is different and useful as a model: it is `@pytest.mark.base`, calls `setup_function` in-process and asserts on the *graph* — `len(my_sim.wrapped_components) == 4`, the four expected classes present, `thermal_power_input.src_object_name/.src_field_name` equal to the AC's name/field, no unconnected mandatory inputs (`:159-199`).

### 4e. Other snapshot/golden material (component-level, not setup-level)

`tests/goldens/` holds two files, `building_information.json` and `building_one_day.json`. `tests/test_building_one_day_snapshot.py:472` compares floats with `PLATFORM_ULP_TOLERANCE = 16` ULPs of the larger operand, integers exactly (raised to 16 in commit `badb7446` for CI-fleet SIMD variance). `tests/building_golden_support.py` is its support module. Both are Building-only, driven from an inline system, not from a `system_setups/` file.

`pr9_results/` is untracked local output (4 RenoVisor-style scenario runs with `lifecycle_kpis.json`, `cost_audit.csv`, HTML reports). It is not a regression suite and is not referenced by any test or workflow.

`tests/energy_systems/uc1.realized.energy_system.yaml` is the single committed realized-record fixture for the declarative path.

---

## 5. Existing recording / introspection hooks

**Verdict: the Simulator retains enough to reconstruct a file after `setup_function` ran, and a working precedent already exists in the v1 JSON path.**

### 5a. What the Simulator holds (`hisim/simulator.py`)

- `self.wrapped_components: List[ComponentWrapper]` (`:73`) — file order preserved.
- `self.all_outputs: List[cp.ComponentOutput]` (`:74`).
- `self.config_dictionary[component.component_name] = component.config` (`:136`).
- `self.module_directory`, `self.module_filename`, `self.my_module_config`, `self.setup_function` (`:76-79`).
- `ComponentWrapper` (`hisim/component_wrapper.py:11`) carries `my_component`, `is_cachable`, and crucially **`connect_automatically: bool`** (`:32`) — the per-component flag the YAML needs to decide between listing `inputs: - <src>` and listing explicit wires.
- Auto-connection is resolved lazily in `prepare_calculation` (`:145-160`) via `connect_everything_automatically` (`:587-659`), which walks `default_connections` / `dynamic_default_connections` and funnels everything through `connect_input`. So a recorder must call `prepare_calculation()` + `connect_all_components()` before reading, exactly as `write_json_for_initialized_simulator` does (`hisim/hisim_convert_to_json.py:209-210`).

### 5b. What each Component holds (`hisim/component.py`)

- `self.config: ConfigBase` (`:239`) — the fully resolved configuration object; `config.get_main_classname()` gives the YAML `class:` string, `config.get_config_classname()` the config class.
- `self.inputs: List[ComponentInput]` (`:212`), each with `src_object_name` / `src_field_name` set by `connect_input` (`:365-366`) **regardless of logging**. This is the authoritative, always-available wire list.
- `self.outputs: List[ComponentOutput]` (`:213`).
- `self.default_connections: Dict[str, List[ComponentConnection]]` (`:221`) — keyed by source *class* name; `ComponentConnection` (`:35`) has `target_input_name`, `source_class_name`, `source_output_name`, `source_instance_name`.
- `self.log_connections: List[Any]` (`:246`), appended by `connect_input` at `:345-349` — but **only when `my_simulation_parameters.log_connections` is True** (`self.enable_logging`, `:247`). The v1 JSON writer depends on this, which is why `test_json_configs.py` sets `log_connections = True` and why `Simulator.__init__` takes `force_log_connections` (`:49, :64-69`).
- Side effect worth knowing: with logging on, `connect_input` also appends every wire to `component_connections.json` in the result directory (`:370-394`).

### 5c. What DynamicComponent holds (`hisim/dynamic_component.py`)

- `self.my_component_inputs: List[DynamicConnectionInput]` (`:131`) — `source_component_class`, `source_component_field_name`, `source_load_type`, `source_unit`, `source_tags`, `source_weight` (`:36-45`). This is precisely the YAML aggregator-feed payload (`from`, `tags`, `weight`).
- `self.my_component_outputs: List[DynamicConnectionOutput]` (`:132`) — `source_output_field_name`, `source_tags`, `source_weight`, `source_load_type`, `source_unit`, `source_component_class` (`:49-59`). This is the YAML `dispatch:` block.
- `self.dynamic_default_connections: Dict[str, List[DynamicComponentConnection]]` (`:133`).
- `add_component_output` (`:135`) records everything a setup-created dynamic output needs.
- **Port-naming divergence**: the legacy path names aggregator inputs `Input_<source>_<field>_<n>` (parsed back out with a regex at `hisim/json_generator.py:184`), while the declarative path names them from `connection.aggregator_input_name` (`hisim/dynamic_component.py:253-262`) and dispatch outputs from `connection.dispatch_output_name` (`:299`). A recorded YAML re-run will therefore produce **different result-column names** for aggregator ports even when the values are identical — relevant to how P3 states its equivalence claim.

### 5d. The existing v1 recorder (the precedent to copy)

`hisim/hisim_convert_to_json.py` + `hisim/json_generator.py` already do file→simulator→file for the JSON format:

- `main()` (`hisim_convert_to_json.py:48`) imports the module, builds a `Simulator`, runs `setup_function`, writes `.simulation.json`, then **runs `setup_function` a second time** on a fresh Simulator with `log_connections = True` (`:124-135`), calls `prepare_calculation()` + `connect_all_components()` (`:141-142`), and writes `.scenario.json`.
- `write_standalone_scenario_json` (`json_generator.py:486`) walks `my_sim.wrapped_components`, dumps each `config.to_dict()`, harvests dynamic inputs/outputs, collects wires from `component.log_connections`, dedupes (`get_unique_connections`, `:280`), then `remove_automatic_connections` (`:442`) decides per component whether every default connection was actually made — setting `connect_automatically` and deleting the wires the auto-connector would recreate. That is exactly the "should this component say `inputs: - weather` or list explicit wires?" decision P3 needs.
- Known special cases already handled there and applicable to YAML: UTSP result path re-symbolized to `<<utils.HISIMPATH['utsp_results']>>` (`:97`); Weather `source_path` rewritten relative to the input directory (`:100-103`); `Car.household_name` recovered from the component (`:109`); EMS constructor-built dynamic outputs excluded so they are not duplicated on reload (`count_outputs_created_by_constructor`, `:67-87` — measured from a pristine instance, not hard-coded).
- `scripts/regenerate_scenario_jsons.py` runs the converter per setup in its own subprocess (singleton/import isolation) with a per-worker `HISIM_LOCAL_LPG_CALC_INDEX` — the same isolation the YAML recorder will need.

### 5e. The reverse direction the recorder should reuse (`hisim/energy_system/`)

- `EnergySystemExecutor.build()` (`executor.py:264`) runs expand → validate → bind → configure → wire → register, producing `BuiltEnergySystem` (`:181-207`: `model`, `expansion`, `bindings`, `configured`, `wired`, `simulator`, `warnings`, `source_*`, `path_resolver`, `rerun`). Note `CONNECT_AUTOMATICALLY = False` (`:226`) — the declarative path deliberately never uses the simulator's auto-connector.
- `record.realize(built)` (`record.py:363`) → `EnergySystemFile`, with `ConfigBlockWriter` (`:56`, enum-by-name, `${var}` path re-symbolization via `PathResolver`, `component_id` omitted because the entry key carries it) and `SizingSourceWriter` (`:154`). `verify_rerun` (`:390`) proves a record reproduces itself.
- `EnergySystemEmitter.to_document` / `.dump` (`emitter.py:149,129`) render the model to canonical YAML; `comments.write_record` (`comments.py:456`) writes the annotated `realized.energy_system.yaml` (`RECORD_FILENAME`, `:286`); `executor.write_records` (`:408`) is the caller.
- `model.ComponentEntry` (`model.py:347`) is the target datatype: `name`, `class_path`, `preset`, `constructor`, `config`, `inputs`, `sizing_sources`, canonical key order at `ENTRY_KEYS` (`:365`).
- **Preset recovery is already possible**: `@preset` stamps `preset_provenance` on every instance it builds (`hisim/config/presets.py:96`, read via `preset_provenance(config)` at `:409`), and the docstring says it is carried through `resolve_config`. Nothing outside `hisim/config/` currently reads it — the recorder would be its first consumer. `presets_of` / `constructors_of` (`:363,381`) let the recorder rebuild a pristine preset instance and diff it to produce the sparse `config:` override.

**The gap**: `record.py` takes a `BuiltEnergySystem`, i.e. it already has an `EnergySystemFile` model. The recorder must synthesize a `ComponentEntry` per `ComponentWrapper` directly from the live objects, then hand the assembled `EnergySystemFile` to `EnergySystemEmitter.dump`. What is *not* recoverable from a Simulator at all: group membership (`groups:` / `enabled:`), the distinction between a fact that flowed through a `SizingContext` and one hand-copied (so `sizing_sources:` cannot be inferred, only re-derived), and the original preset name for any of the 32 classes in §3b.

---

## 6. JSON scenario files

**23** `system_setups/*.scenario.json` (v1 `json_executor` format).

- Every one of the 23 has a `.py` twin of the same stem; there are no orphan JSONs.
- Exactly one `.py` setup has **no** JSON twin: `simple_weather_data_import.py` (it registers no components, so there is nothing to serialize).
- **No `.scenario.json` is the only form of a system** — all 23 are generated artefacts of their `.py` sibling, produced by `scripts/regenerate_scenario_jsons.py` via `hisim/hisim_convert_to_json.py`, and kept honest by `tests/test_json_configs.py` (component/output counts) plus the blocking `golden-json-check.yml` KPI parity gate for the 8 golden setups.
- The paired `<setup>.simulation.json` files the converter emits are deliberately *not* committed; only the 7 shared `2021_*.simulation.json` parameter files are (`scripts/regenerate_scenario_jsons.py` docstring).

---

## Things the recorder cannot express, grouped by kind

| Kind | Count | Where |
|---|---|---|
| **Simulation-parameter mutation** (belongs in the `.simulation.yaml`, not the energy-system file) — post-processing option lists | 14 setups | `air_conditioned_house.py:88-94`, `simple_system_setup_one.py:30`, `simple_air_conditioner_…:71-79`, 11 sizers |
| " — `logging_level`, `cache_dir_path`, `year` save/restore | 11 setups | `household_heatpump_building_sizer.py:94-96, 111, 231-241` |
| " — `country = "DE"` | 1 setup | `household_gas_building_sizer.py:117` |
| **Result-path / singleton side effects** (`SingletonSimRepository.set_entry`, `ResultPathProviderSingleton`, hash parsing from the config filename) | 12 setups | `household_heatpump_building_sizer.py:464-501`, `simple_air_conditioner_…:159-189` |
| **Setup-time external input** — `read_in_configs` + ~20 archetype/energy-system field reads driving every config | 13 setups | `household_heatpump_building_sizer.py:76, 120-218` |
| " — CSV read | 1 | `air_conditioned_house.py:99-102` |
| " — live network weather fetch feeding a config | 2 | `basic_household_with_weather_data_request.py:61-70,111-112`; `simple_weather_data_import.py:65-74` |
| " — hard-coded cluster-path probes | 11 | `household_heatpump_building_sizer.py:95,167` |
| **Filesystem mutation before building** (cache wipe) | 1 | `air_conditioned_house.py:71-74` |
| **Loops generating N components from runtime data** (cars from occupancy) | 1 setup, 6 loops, 3 classes | `household_heatpump_car_building_sizer.py:395,402,417,446,482,556,568` |
| **Loops building a value** (LPG household list) | 11 | `household_heatpump_building_sizer.py:175-188` |
| **Conditional component sets** — PV/battery/EMS fork | 11 | `household_heatpump_building_sizer.py:399-460` (partly expressible as a `groups:` flag, but the flag's *value* comes from the external config) |
| **Conditional wiring on a constructed component's data** (`my_heatpump.parameters["Group"]`) | 4 | `household_heatpump_building_sizer.py:339-348`, `automatic_default_connections.py:140-149`, `household_heatpump_car_…:340`, `household_heatpump_solar_thermal_…:329` |
| **Conditional wiring + config on a flag** (car surplus charging) | 1 | `household_heatpump_car_building_sizer.py:483-521` |
| **Config-selection conditionals** (HDS emitter type, PV scaled-vs-fixed, occupancy year) | 11 each | `household_heatpump_building_sizer.py:129-134, 262-273, 231` |
| **Validation `raise` guards** | 11 setups × 3–5 | `household_heatpump_building_sizer.py:122,134,186,188,346` |
| **Setup-side arithmetic still outside the sizing kernel** | 5 distinct expressions | `air_conditioned_house.py:159,160,177`; `household_heatpump_building_sizer.py:149,160,292`; `household_gas_solar_thermal_building_sizer.py:355` |
| **Classes with no `@preset`/`@constructor`** → literal `config:` dump, no sparse override, no `preset:` line | 32 of 41 classes | §3b |
| **Setups that register nothing** | 1 | `simple_weather_data_import.py` (a recorder would emit an empty file, or nothing) |

Expressible without loss, for the record: `connect_only_predefined_connections` and `connect_automatically=True` map to `inputs: - <source>`; `connect_input` maps to `{input:, from: src.Output}`; `add_component_input_and_connect` / `add_component_inputs_and_connect` map to `{from, tags, weight}`; `add_component_output` + `connect_dynamic_input` map to the feed's `dispatch:` block; `Information`-object threading maps to `sizing_sources` for the classes that carry sizing laws.
