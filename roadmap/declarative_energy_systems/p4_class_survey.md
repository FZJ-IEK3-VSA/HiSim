# P4 per-class survey — companion to `p4_component_sweep_requirements.md`

Factual per-class survey of every config class under `hisim/components/`, taken 2026-08-27 on branch
`energy_system_files` by three sequential subagent surveys (group A: heat generators and their
controllers; group B: heat distribution, storages, electricity, EMS and meters; group C: occupancy,
weather, building, mobility, H₂/CHP chain, examples, legacy `configuration.py`). Read-only; nothing here
is a requirement — the requirements document cites rows from here by class name and turns the
"decisions the owner must take" lists into its §11 questions.

---

## P4 batch group A — heat generators and heat‑generator controllers

Read: `epic.md`, `plan.md` §P4, `preset_naming_supplement.md`, `sizing_fact_inventory.md` §2/§3/§6, `p3_setup_inventory.md` §2e/§3, and the pilots (`generic_boiler.py`, `heat_distribution_system.py`, `weather.py`, `hisim/config/{presets,sizing,laws,context,contributions}.py`). Branch `energy_system_files`, nothing modified.

### Scope resolution first

Modules in the assignment that **do not exist** on this branch (already moved to `obsolete/`, commits `d2586e79`, `c0e39eb9`): `controller_l1_building_heating.py`, `controller_l1_generic_runtime.py`, `generic_heat_pump_modular.py`, `controller_l1_heat_source.py`, `controller_l2_generic_heat_simple.py`, `generic_heat_water_storage.py`. `generic_CHP.py` is `generic_chp.py`; `controller_l1_heat_pump.py` is `controller_l1_heatpump.py`. Two of the plan's "3 zombies" are therefore already gone; the third (`controller_l1_heatpump`) is live and imports cleanly.

Added to the group by inspection of `ls hisim/components`: `night_setback_controller.py` (the supplement files it under heat‑generator controllers), `advanced_fuel_cell_controller.py` (component of `ExtendedControllerConfig`), and the four heat‑generator‑shaped legacy dataclasses in `configuration.py`.

**Zero group‑A classes are converted.** `grep -l '@preset\|sized_field' hisim/components/` returns only `building/config.py`, `controller_l2_energy_management_system.py`, `electricity_meter.py`, `generic_boiler.py`, `heat_distribution_system.py`, `loadprofilegenerator_utsp_connector.py`, `weather.py`.

**A fact the group needs does not exist yet.** `SizingContext` (`hisim/config/context.py:47-57`) has 11 fields; `set_heating_threshold_outside_temperature_in_celsius` is **not** among them, and `HeatDistributionControllerConfig.SIZING_CONTRIBUTIONS` (`heat_distribution_system.py:1564`) contributes only `water_mass_flow_rate_in_kg_per_second` and `heat_distribution_system_type`. Every generator controller in this group copies that threshold from the HDS controller today. Adding the fact term + context field + contribution is a **prerequisite commit for B2**, and it touches `hisim/config/` — i.e. it is the one place where E6 (locality) does not hold for this batch.

---

## A1 — Heat generators

### `HeatPumpHplibConfig` (`hisim/components/advanced_heat_pump_hplib.py:58`) → `HeatPumpHplib` (`:155`)

1. Configures the hplib air/water heat pump.
2. **status: to convert — but see the delete question (D‑1 below).**
3. **Factories.**
   - `get_default_generic_advanced_hp_lib(set_thermal_output_power_in_watt=Quantity(8000,Watt), heating_reference_temperature_in_celsius=Quantity(-7.0,Celsius), component_id=None)` — `:91`. setups **0**, tests **1** (`tests/test_advanced_heat_pump_hplib.py:218`), hisim **0**.
   - `get_scaled_advanced_hp_lib(heating_load_of_building_in_watt: Quantity[float,Watt], heating_reference_temperature_in_celsius=Quantity(-7.0), name="AdvancedHeatPumpHPLib", component_id=None)` — `:123`. setups **0**, tests **1** (`tests/test_sizing_energy_systems.py:152`), hisim **0**.
   - **No setup instantiates this class.** It survives only because `simple_water_storage.py:809`, `controller_l2_energy_management_system.py:974`, `electricity_meter.py:384` and `postprocessing/.../opex_and_capex_cost_calculation.py:17` name `HeatPumpHplib` in default connections / KPI dispatch.
4. **Presets.** Supplement: `air_water`, `air_water_8kw`. **Deviation flagged:** `air_water_8kw` has no basis as a catalogue rating — `model="Generic"` is hplib's generic curve fit and 8000 W is a *default argument*, not a device (`:91`, docstring cites the hplib source line, not a datasheet). Conflict 3's own resolution ("rating suffix only if the number is a real catalogue rating") says drop it. Proposed: **`air_water` only**. The one test that calls the unscaled factory can pass `set_thermal_output_power_in_watt=8000` as a `config:` override.
5. **Sized fields.**
   - `set_thermal_output_power_in_watt` ← `heating_load_in_watt`, math **copy**: `set_thermal_output_power_in_watt: Quantity[float, Watt] = heating_load_of_building_in_watt` (`advanced_heat_pump_hplib.py:134`). Lives in the factory. Not a constant law.
   - `heating_reference_temperature_in_celsius` ← `heating_reference_temperature_in_celsius`, math **copy**. Today a factory default `-7.0` (`:126`); setups that use the sibling class pass a literal `-7.0` (`household_heatpump_building_sizer.py:123`). Making it `AUTO` is neutral only while the Building's value is `-7.0`; see behaviour.
   - `flow_temperature_in_celsius` — literal `Quantity(52, Celsius)` (`:107`). §6 marks it "copy of HDS max flow temp, confidence high" — **future**, must stay a plain default in B1 or it is a physics change with no fact to read.
6. **Facts provided.** `maximal_thermal_power_in_watt` = `set_thermal_output_power_in_watt.value` (inventory §3 lists heat pump power under this fact name; consumed by the buffer storage in B4). `minimal_thermal_power_in_watt` is not modelled on this class.
7. **Behaviour: neutral.** Preset `air_water` with both fields `AUTO` reproduces `get_scaled_advanced_hp_lib` exactly (copy law, same constant `-7.0`). The `get_default_*`/`get_scaled_*` pair does **not** disagree on any constant — the two bodies (`:104-121` vs `:136-153`) are field‑for‑field identical apart from the power. Dropping `air_water_8kw` changes nothing numerically because no setup uses it.
8. **Deletions.** Both factories. No `SingletonSimRepository` construction‑time keys.
9. **Flags.** `cycling_mode: bool` (default `True` in both factories) — an operating option, A2 → `config:` override, not a preset name.
10. **Hazards.**
   - **`Quantity`-typed sizable fields.** Every power/temperature field is `Quantity[float, Watt]` / `Quantity[float, Celsius]`. `sized_field` (`hisim/config/sizing.py:169`) offers `value_type` for enum coercion only; `Sizable[Quantity[float, Watt]]` has no codec, and the law would have to return a `Quantity` while the fact vocabulary is `float`. This class is the **only** one in group A with this problem — the sibling `MoreAdvancedHeatPumpHPLibConfig` uses plain floats. Either the kernel grows `Quantity` support or this class is lowered to floats first (a separate commit, golden‑neutral because no setup uses it).
   - `component_id` is defaulted inside both factories (`ComponentID(name="AdvancedHeatPumpHPLib")`) — the preset takes the name as its first argument instead.
   - Not buildable from a bare `cls()` — every field is mandatory; both factories build fine (verified with `local_python_env/bin/python`).

---

### `MoreAdvancedHeatPumpHPLibConfig` (`more_advanced_heat_pump_hplib.py:84`) → `MoreAdvancedHeatPumpHPLib` (`:204`)

1. The heat pump every heat‑pump setup actually uses (4 instantiations, p3 §3b).
2. **status: to convert** — first class of B1 by consumer count.
3. **Factories.**
   - `get_default_generic_advanced_hp_lib(component_id=None, name="MoreAdvancedHeatPumpHPLib", set_thermal_output_power_in_watt=8000, heating_reference_temperature_in_celsius=-7.0, massflow_nominal_secondary_side_in_kg_per_s=0.333)` — `:121`. setups **0**, tests **3 calls + 1 reference** (`test_more_advanced_heat_pump_hplib.py:201,252`; `test_heating_meter.py:174`; `test_config_enum_serialization.py:79` passes the bound method itself), hisim **0**.
   - `get_scaled_advanced_hp_lib(heating_load_of_building_in_watt, name=…, component_id=None, heating_reference_temperature_in_celsius=-7.0, massflow_nominal_secondary_side_in_kg_per_s=0.333)` — `:163`. setups **4** — `automatic_default_connections.py:129`, `household_heatpump_building_sizer.py:328`, `household_heatpump_car_building_sizer.py:329`, `household_heatpump_solar_thermal_building_sizer.py:318`; tests **2** (`test_controller_l2_energy_management_system.py:177`, `test_time_resolution.py:427`); hisim **0**.
4. **Presets.** Supplement: `air_water`, `air_water_8kw`. **Deviation:** same as above — drop `air_water_8kw` (8000 is a default argument, `model="Generic"`; the three tests that call the unscaled factory become `config:` overrides). Proposed: **`air_water`**. Same wire name as the class above is legal (presets are class‑scoped).
5. **Sized fields.**
   - `set_thermal_output_power_in_watt` ← `heating_load_in_watt`, **copy**: `set_thermal_output_power_in_watt: float = heating_load_of_building_in_watt` (`:175`). In the factory. Not constant.
   - `heating_reference_temperature_in_celsius` ← `heating_reference_temperature_in_celsius`, **copy**; today a factory default `-7.0` that the four setups re‑pass as the literal `heating_reference_temperature_in_celsius = -7.0` set at e.g. `household_heatpump_building_sizer.py:123` and also pushed onto the Building at `:194`. Setup‑side.
   - **Do not size** `massflow_nominal_secondary_side_in_kg_per_s` (supplement lists it as an AUTO field). Today it is a fixed `0.333` in both factories (`:127`, `:170`) and is never derived. §6's proposal (`P/(c_p·ΔT)`, 2 providers) needs the HDS ΔT fact, which does not exist. **Deviation from the supplement: it stays a plain default in B1.** Making it `AUTO` now is a silent physics change to all four heat‑pump golden setups.
   - `flow_temperature_in_celsius = 52`, `minimum_thermal_output_power_in_watt = 1800` — §6 future candidates, plain defaults.
6. **Facts provided.** `maximal_thermal_power_in_watt` (= `set_thermal_output_power_in_watt`) for the buffer storage (B4) and for its own SH controller.
7. **Behaviour: neutral.** The `get_default_*`/`get_scaled_*` bodies are identical except for the power field (compare `:137-161` with `:180-203`) — **no constant disagreement**. `air_water` + `AUTO` reproduces `get_scaled_advanced_hp_lib` byte‑for‑byte for the four setups.
   Caveat for B4: the buffer storage in those setups is sized from `my_building_information.max_thermal_building_demand_in_watt` (`household_heatpump_building_sizer.py:365`), not from the heat pump. Because the HP law is a pure copy, re‑pointing the storage at `<hp>.maximal_thermal_power_in_watt` is **neutral for heat pumps** — the C11 gate only bites the boiler setups.
8. **Deletions.** Both factories.
9. **Flags** (all A2 → `config:`): `with_domestic_hot_water_preparation` (default `False`, but **all four setups set it to `True` post‑construction** — `automatic_default_connections.py:133`, `household_heatpump_building_sizer.py:332`, `household_heatpump_car_building_sizer.py:333`, `household_heatpump_solar_thermal_building_sizer.py:322`), `cycling_mode` (`True`), `passive_cooling_with_brine` (`False`).
10. **Hazards.**
   - **`with_domestic_hot_water_preparation` changes the component's I/O surface**, not just a number: `__init__` branches on it, and so do `i_simulate` and the KPI code. Setting it post‑construction (as all four setups do) works only because it is read in `__init__` after the config is handed over. In a file it becomes a `config:` line — fine — but the recorder must diff against a fresh preset, and the auto‑wiring then depends on a config value.
   - `position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup` — enum‑typed field (P2 R3.7): if it ever becomes sizable it needs `value_type=`.
   - `fluid_primary_side: str = "air"` and `model: str = "Generic"` are free‑text; they are what `air_water` actually means. Consider promoting to enums before they become wire format.
   - `specific_heat_capacity_of_primary_fluid=0` in both factories — a zero that only survives because the air path never divides by it.

---

### `GenericHeatPumpConfig` (`generic_heat_pump.py:45`) → `GenericHeatPump` (`:158`)

1. Device‑table heat pump used by the three "basic household" demo setups.
2. **status: to convert (constructor‑shaped).**
3. `get_default_generic_heat_pump_config(component_id=None)` — `:60`. setups **3** (`basic_household.py:113`, `basic_household_with_weather_data_request.py:146`, `default_connections.py:112`), tests **0**, hisim **0**.
4. **Presets.** Supplement: `standard` + constructor `for_device(manufacturer, heat_pump_name)`. **Agreed, no deviation.** The lookup is real: `generic_heat_pump.py:355` raises `ValueError(f"Heat pump '{manufacturer}' / '{name}' not registered in the database")`, so `for_device` can validate at build time.
5. **Sized fields: none.** §6 suggests `manufacturer`/`heat_pump_name` from `heating_load` by catalogue lookup — future, low confidence, and it is a *multi‑field* law (see the air‑conditioner problem below).
6. **Facts provided:** none today. Its thermal power lives in the device table, not in the config, so it cannot contribute `maximal_thermal_power_in_watt` without reading the table at build time.
7. **Behaviour: neutral** — `preset_standard` returns exactly the `Vitocal 300-A AWO-AC 301.B07` / `Viessmann Werke GmbH & Co KG` / `min_operation_time=3600` / `min_idle_time=900` tuple.
8. **Deletions.** The one factory. Return type is `Any` (`:62`) — the preset gets a real return type.
9. **Flags:** none.
10. **Hazards.** `min_operation_time=60*60` and `min_idle_time=15*60` are written as products; the preset must keep them as `3600.0`/`900.0` and the regenerated `.scenario.json` fixtures will show the int→float drift the plan warns about.

---

### `ElectricHeatingConfig` (`generic_electric_heating.py:51`) → `ElectricHeating` (`:101`)

1. Direct electric resistance heating.
2. **status: to convert.**
3. `get_default_electric_heating_config(component_id=None, with_domestic_hot_water_preparation=False, maximum_electric_power_w=40000)` — `:77`. setups **1** (`household_electric_heating_building_sizer.py:294`), tests **2** (`tests/test_generic_electric_heating.py`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed** (rule 5 clean; no technology name distinguishes anything, so A3 does not apply).
5. **Sized fields.**
   - `maximum_electric_power_w` ← `heating_load_in_watt`, **copy**. The math is **setup‑side today**: `maximum_electric_power_w=my_building_information.max_thermal_building_demand_in_watt` (`household_electric_heating_building_sizer.py:296`). The factory's own default is the unrelated literal `40000`. Not a constant law.
6. **Facts provided.** `maximal_thermal_power_in_watt` — note the *thermal* power equals the electric power only because `efficiency=1.0` (`:88`). If the fact is declared as thermal, the contribution should be `maximum_electric_power_w * efficiency`, which is a (currently invisible) modelling choice. No consumer today: this setup has no buffer storage.
7. **Behaviour: neutral** for the one setup (copy of the same number). **Watch:** the preset's *unsized* fallback disappears — the `40000` default is not a catalogue rating and has no call site relying on it; per conflict 3 it is dropped, not turned into `standard_40kw`.
8. **Deletions.** The one factory. Return type `"ElectricHeatingConfig"` is fine.
9. **Flags:** `with_domestic_hot_water_preparation` (default `False`; the setup passes `True` at `:295`) → A2 `config:` override.
10. **Hazards.** Same I/O‑shape‑from‑flag problem as the heat pump: `generic_electric_heating.py:181,275` add DHW inputs/outputs only when the flag is set, and `:829,866,947,974,979` branch on it at run time.

---

### `DistrictHeatingConfig` (`generic_district_heating.py:60`) → `DistrictHeating` (`:119`)

1. District‑heating transfer station.
2. **status: to convert.**
3. `get_default_district_heating_config(component_id=None, with_domestic_hot_water_preparation=False, connected_load_in_w=20000)` — `:84`, returns `Any`. setups **1** (`household_district_heating_building_sizer.py:358`), tests **1** (`tests/test_generic_district_heating.py`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields.** `connected_load_in_w` ← `heating_load_in_watt`, **copy**, **setup‑side today**: `connected_load_in_w=my_building_information.max_thermal_building_demand_in_watt` (`household_district_heating_building_sizer.py:360`). Factory default `20000` is unrelated and unused. Not constant.
6. **Facts provided.** `maximal_thermal_power_in_watt` = `connected_load_in_w`. No consumer today (this setup has no buffer storage either).
7. **Behaviour: neutral.**
8. **Deletions.** The one factory; give the preset a real return type instead of `Any`.
9. **Flags:** `with_domestic_hot_water_preparation` (`False`; setup passes `True` at `:359`).
10. **Hazards.** Same flag‑shapes‑I/O pattern (`:208,285,949,995,1118,1156,1161`).

---

### `AirConditionerConfig` (`air_conditioner.py:91`) → `AirConditioner` (`:246`)

1. Database‑driven split air conditioner (Seville demo).
2. **status: to convert (constructor‑shaped) — with one genuine mechanism gap.**
3. **Factories.**
   - `get_air_conditioner_config_from_database(manufacturer, model_name, scale_factor=1.0, component_id=None, name="AirConditioner")` — `:115`. setups **0**, tests **0**, hisim **2** (its own two callers at `:174` and `:238`).
   - `get_default_air_conditioner_config(component_id=None)` — `:168`, pins `Samsung / "AC120HBHFKH/SA - AC120HCAFKH/SA"`, `scale_factor=1.0`. **0 call sites anywhere.**
   - `get_scaled_air_conditioner_config(heating_load, heating_reference_temperature, component_id=None)` — `:181`. setups **1** (`air_conditioned_house.py:193`), tests **0**, hisim **0**.
4. **Presets.** Supplement: `standard` + `for_device(manufacturer, model_name, *, scale_factor=1.0)`. **Deviation flagged:** the supplement treats the two `get_default_*`/`get_scaled_*` methods as a sized/unsized twin of one variant. They are not — the *unscaled* one pins a specific Samsung unit, the *scaled* one **searches the database and picks a different unit**. Since the pinned one has zero call sites, propose **`standard` = the auto‑selecting form** (canonical, matches rule 3: the only setup uses it) and **delete** `get_default_air_conditioner_config`; a file that wants the Samsung unit calls `for_device("Samsung", "AC120HBHFKH/SA - AC120HCAFKH/SA")`.
5. **Sized fields — the gap.** The supplement lists `scale_factor` (from `heating_load`). That is only a third of what the factory derives. `get_scaled_air_conditioner_config` computes, from `heating_load` + `heating_reference_temperature`:
   `scaling_factor = relevant_capacity_for_scaling * 0.6 / heating_load` (`air_conditioner.py:236`, comment: "0.6 was determined heuristically")
   and, in the same pass, `manufacturer`, `model_name`, and the six reference curves `t_out_cooling_ref`, `t_out_heating_ref`, `eer_ref`, `cop_ref`, `cooling_capacity_ref`, `heating_capacity_ref` plus `investment_costs_in_euro`, `maintenance_costs_in_euro_per_year`, `lifetime_in_years`, `co2_emissions_kg_co2_eq`. **`sized_field` is one law per field**; there is no mechanism for "one law, twelve fields". Owner decision D‑3 below. Reads: `heating_load_in_watt`, `heating_reference_temperature_in_celsius`. Math: **fn** (nearest‑temperature index, then `idxmin` over `abs(capacity − heating_load)`). Factory‑side. Not constant.
6. **Facts provided:** none.
7. **Behaviour: neutral** only if the twelve‑field selection is preserved as one build‑time step. Any per‑field decomposition is a physics change to `air_conditioned_house.py`.
8. **Deletions.** `get_default_air_conditioner_config` (0 call sites). **`SingletonSimRepository` construction‑time writes:** `AirConditioner.__init__` writes `COEFFICIENT_OF_PERFORMANCE_HEATING` and `ENERGY_EFFICIENY_RATIO_COOLING` (`air_conditioner.py:468-474`, inside `__init__`, before `i_prepare_simulation` at `:502`); the only reader is `controller_mpc.py:503-504`. Not a *sizing* key, so it is out of the "delete the dead construction‑time keys" item, but it is an order‑dependent construction‑time singleton coupling that the executor will have to preserve.
9. **Flags:** none boolean.
10. **Hazards.** `List[float]` fields (`:97-102`) are mandatory, so no mutable‑default bug — but a preset that hard‑codes them would embed a database row in source. `get_air_conditioner_config_from_database` does file I/O (`utils.load_smart_appliance`) at build time; the constructor must keep that. `component_id` defaulted inside the factory.

---

### `SimpleAirConditionerConfig` (`simple_air_conditioner.py:56`) → `SimpleAirConditioner` (`:89`)

1. Carnot‑model cooling‑only unit.
2. **status: to convert.**
3. `get_default_simple_air_conditioner_config(component_id=None)` — `:70`. setups **1** (`simple_air_conditioner_household_building_sizer.py:116`), tests **6** (`tests/test_simple_air_conditioner.py`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none.** `nominal_cooling_power_w = 2000.0` is §6's "copy of a NEW max‑cooling‑load fact, confidence high" — the fact does not exist; plain default in B1.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory. Note it restates all three field defaults that the dataclass already declares (`:61-63` vs `:78-80`) — pure duplication.
9. **Flags:** none.
10. **Hazards.** None. This is the cheapest conversion in the group and a good first commit of B1.

---

### `IdealizedHeaterConfig` (`idealized_electric_heater.py:30`) → `IdealizedElectricHeater` (`:57`)

1. Reference/test heater that holds the building at setpoint.
2. **status: to convert.**
3. `get_default_config(component_id=None)` — `:43`. setups **0**, tests **1** (`tests/test_electricity_meter.py:87`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none in B1.** §6 proposes both setpoints as copies of the Building's `set_heating_temperature_in_celsius` / `set_cooling_temperature_in_celsius` (both facts **already exist** in `SizingContext`, `context.py:56-57`). But the current values are `19.5`/`23.5` while the Building's defaults are different — making them `AUTO` is a **physics change**. Keep as plain defaults; flag as the cheapest available demonstration of a two‑fact copy law if the owner wants one.
6. **Facts provided:** none.
7. **Behaviour: neutral** as proposed.
8. **Deletions.** The one factory.
9. **Flags:** none.
10. **Hazards.** No `get_default_connections` at all (0 in the module) — the executor must wire it explicitly.

---

### `SimpleHeatSourceConfig` (`simple_heat_source.py:65`) → `SimpleHeatSource` (`:278`)

1. Brine/ground source feeding a heat pump's primary side.
2. **status: to convert.**
3. **Factories** (all `(component_id=None)`), setups **0** throughout:
   - `get_default_config_const_power` — `:100`. tests **4** (`tests/test_simple_heat_source.py`).
   - `get_default_config_const_temperature` — `:124`. tests **1**.
   - `get_default_config_near_surface_brine_temperature` — `:150`. tests **1**, hisim **1** (its own alias at `:199`).
   - `get_default_config_var_brinetemperature` — `:182`, deprecated alias emitting `DeprecationWarning`. tests **1** (the test that asserts the warning).
4. **Presets.** Supplement: `constant_thermal_power`, `constant_temperature`, `near_surface_brine`. **Agreed** — they mirror `SimpleHeatSourceType` members (`:35-46`) and rule 1 reads as substance. Canonical: `constant_thermal_power` (4 of 6 test uses).
5. **Sized fields: none in B1.** `power_th_in_watt = 5000.0` is §6's `P_th·(1−1/COP)` from a heat‑pump evaporator fact that does not exist. Plain default.
6. **Facts provided:** none.
7. **Behaviour: neutral.** The three factories differ **only** in `heat_source_type`, `power_th_in_watt` and `temperature_output_in_celsius`; the other nine fields are byte‑identical across all three (`:106-119`, `:130-146`, `:157-172`) — **no constant disagreement**.
8. **Deletions.** All four, including the deprecated alias (drop it with the sweep; its only user is the test asserting it warns).
9. **Flags:** `use_external_massflow_as_signal_input_for_nominal_massflow` (default `False` in all three).
10. **Hazards.** The module installs a **custom `from_dict`** shim for the renamed fields `const_source`→`heat_source_type`, `temperature_out_in_celsius`→`temperature_output_in_celsius` (`:200-215`). `sized_field` merges its own `dataclasses_json` encoder/decoder into field metadata (`sizing.py:207`) — if either renamed field ever becomes sizable, the two codecs collide. `heat_source_type` and `fluid_type` are enum‑typed (P2 R3.7 `value_type=`).

---

### `SolarThermalSystemConfig` (`solar_thermal_system.py:42`) → `SolarThermalSystem` (`:183`)

1. Flat‑plate collector for DHW.
2. **status: to convert.**
3. **Factories** (identical 10‑parameter signatures):
   - `get_default_solar_thermal_system(component_id=None, coordinates=Coordinates(50.78, 6.08), azimuth=180.0, tilt=30.0, area_m2=1.5, eta_0=0.78, a_1_w_m2_k=3.2, a_2_w_m2_k=0.015, old_solar_pump=False, source_weight=1)` — `:101`. setups **3** (`household_gas_solar_thermal.py:190`, `household_gas_solar_thermal_building_sizer.py:354`, `household_heatpump_solar_thermal_building_sizer.py:373`), tests **1**, hisim **0**.
   - `get_default_solar_thermal_system_manually_calculated_capex(...)` — `:141`. **0 call sites anywhere.**
4. **Presets.** Supplement: `flat_plate` (A3). **Agreed.** The capex twin is not a variant (conflict 3) → delete.
5. **Sized fields.** `area_m2` ← `number_of_apartments`, math **×k**: `area_m2 = 4 * number_of_apartments` — **setup‑side today**, `household_gas_solar_thermal_building_sizer.py:355` and `household_heatpump_solar_thermal_building_sizer.py:374` (both commented "4 m2 per apartment"). Not a constant law.
6. **Facts provided:** none.
7. **Behaviour: PHYSICS CHANGE.** `household_gas_solar_thermal.py:191` passes **`area_m2=4`** — the bare literal, *not* multiplied — even though that setup computes `number_of_apartments = arche_type_config_.number_of_dwellings_per_building` at `:98` and uses it four lines later for the DHW storage (`:210`). Before: `area_m2 = 4`. After (law): `area_m2 = 4 × number_of_apartments`. Identical only when the archetype says 1 dwelling; different for every MFH archetype. Affected setup: **`household_gas_solar_thermal.py`** (not in the 8‑setup golden fleet, so the golden suite will not catch it). The other two setups are neutral.
   Also flag: the capex twin computes `investment_costs_in_euro = area_m2 * 797` and `device_co2_footprint_in_kg = _compute_device_co2_footprint(area_m2)` (`:169-171`) while the live factory leaves them `None` for post‑processing to look up. Deleting the twin is a no‑op today but discards the only in‑repo record of the €797/m² and the emission‑factor breakdown at `:82-99` — those are `note=` material (Q‑P1.7).
8. **Deletions.** `get_default_solar_thermal_system_manually_calculated_capex`.
9. **Flags:** `old_solar_pump` (default `False`).
10. **Hazards.**
   - **Mutable default argument.** `coordinates: Coordinates = Coordinates(latitude_in_degrees=50.78, longitude_in_degrees=6.08)` (`:105` and `:145`) — `Coordinates` is a plain `@dataclass`, **not frozen** (`hisim/component.py:590-598`). Every config built from the factory shares one instance; mutating one config's coordinates mutates all of them. Fix in the preset (build a fresh `Coordinates` per call).
   - `coordinates` duplicates the Weather's location (§6: "copy / geocode lookup, confidence high") — the class silently disagrees with the weather file today.
   - `source_weight: int` is an EMS priority, not a physical parameter; it is the kind of field a group flag will want to override.

---

### `CHPConfig` (`generic_chp.py:33`) → `SimpleCHP` (`:124`)

1. Gas CHP / hydrogen fuel cell, one class, fuel selected by field.
2. **status: to convert.**
3. Both are `@staticmethod`, not `@classmethod`:
   - `get_default_config_chp(thermal_power: float, component_id=None)` — `:49`. setups **0**, tests **4** (`tests/test_generic_chp.py:203,216,225,232`), hisim **0**.
   - `get_default_config_fuelcell(thermal_power: float, component_id=None)` — `:80`. setups **0**, tests **3** (`:40,244,254`), hisim **0**.
4. **Presets.** Supplement: `gas`, `hydrogen` (rule 1: name the fuel, not the device). **Agreed.** Canonical `gas` (4 test uses vs 3).
5. **Sized fields.** All three power fields are functions of the **mandatory** `thermal_power` argument, which nothing derives:
   - `gas`: `p_el = (0.33 / 0.5) * thermal_power` (`:73`), `p_th = thermal_power`, `p_fuel = (1 / 0.5) * thermal_power` (`:75`).
   - `hydrogen`: `p_el = (0.48 / 0.43) * thermal_power` (`:105`), `p_fuel = (1 / 0.43) * thermal_power` (`:107`).
   Per §6, `p_el`/`p_fuel` become `Self("p_th") * k` per‑preset laws (the boiler's `Self(...)` pattern, `generic_boiler.py:228`) — reads **no external fact**, so the plan's "CHP/fuel cell (constants only)" phrasing for B1 is right for `p_el`/`p_fuel`. `p_th` itself stays a mandatory field (§6's `heating_load × 0.3` base‑load share is a **physics invention** with no precedent in any call site — do not adopt it in B1).
6. **Facts provided.** Could contribute `maximal_thermal_power_in_watt` = `p_th`; no consumer today.
7. **Behaviour: neutral** if `p_th` stays caller‑supplied and `p_el`/`p_fuel` become per‑preset `Self("p_th") * k` laws with the exact ratios above. **Physics change** the moment `p_th` gets a law.
8. **Deletions.** Both factories; also convert `@staticmethod` → `@classmethod` (the `@preset` decorator wraps classmethods, `presets.py`).
9. **Flags:** none. `source_weight: int = 1` is the EMS priority.
10. **Hazards.** `fuel_type: lt.LoadTypes` is enum‑typed (R3.7 `value_type=`) and is exactly what the two presets differ in. Name collision with `advanced_fuel_cell.CHPConfig` is harmless (class‑scoped presets) but confusing in `describe` output — both classes are called `CHPConfig`.

---

### `CHPConfig` (`advanced_fuel_cell.py:38`) → `CHP` (`:165`)

1. The detailed modulating CHP/fuel cell used by the EMS demo.
2. **status: to convert.**
3. `get_default_config(component_id=None)` — `:65`. setups **2** (`dynamic_components.py:74,75`), tests **1** (`tests/test_advanced_fuel_cell.py:45`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Deviation flagged:** the default sets `gas_type="Hydrogen"` (`:76`), so under rule 1 (name the fuel) the honest name is **`hydrogen`**, matching what `generic_chp.CHPConfig` gets. `standard` here means "a hydrogen fuel cell", which rule 1 says a preset name must not hide. Low stakes but it is a wire name; owner call D‑5.
5. **Sized fields: none in B1.** §6's `p_th_max`/`p_el_max` from heating load are low confidence; `p_th_min`, `p_el_min`, `mass_flow_max` from own maxima are medium. All plain defaults.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory. **`CHPConfigAdvanced` (`:94`) is a delete/exempt candidate**: not a dataclass, not a `ConfigBase`, reads `mock_up_efficiencies.xlsx` in `__init__` keyed by `system_name="BlueGen BG15"`, has **0 call sites**, and constructs successfully today (verified). Supplement says out of scope for presets — agreed: **delete**, or convert to `for_device(system_name)` if the Excel table is still wanted.
9. **Flags:** `is_modulating` (default `True`).
10. **Hazards.**
   - `gas_type: str = "Hydrogen"` and `operating_mode: str = "both"` are **free‑text strings** that steer behaviour. They should be enums before they become wire format (same objection the supplement raises for `RsocBatteryControllerConfig.operation_mode`).
   - The two `dynamic_components.py` uses set `component_id` **post‑construction** (`:76,77`: `my_advanced_fuel_cell_config_1.component_id = ComponentID("CHP1")`). This is the exact case the name‑as‑first‑argument preset fixes: `CHPConfig.preset_hydrogen("CHP1")`. Good conversion showcase.

---

## A2 — Heat‑generator controllers

### `HeatPumpHplibControllerL1Config` (`advanced_heat_pump_hplib.py:924`) → `HeatPumpHplibController` (`:958`)

1. L1 controller of the advanced hplib heat pump.
2. **status: delete candidate (see D‑1); otherwise to convert.**
3. `get_default_generic_heat_pump_controller_config(heat_distribution_system_type: Any, mode: int = 2, component_id=None, name="HeatPumpController")` — `:939`. **setups 0, tests 0, hisim 0 — zero call sites anywhere in the repository.** Neither the config nor the component class appears in any setup or test.
4. **Presets.** Supplement: `standard`. Agreed *if* it survives.
5. **Sized fields.** `heat_distribution_system_type` ← `heat_distribution_system_type` (fact exists, `context.py:52`), **copy**. `set_heating_threshold_outside_temperature_in_celsius = 16.0` ← the HDS controller's threshold, **copy** — **the fact does not exist yet** (see the scope note). `set_cooling_threshold_outside_temperature_in_celsius = 20.0` is §6's mirror step table, future.
6. **Facts provided:** none.
7. **Behaviour: neutral** (no call sites to change).
8. **Deletions.** The whole class/component if D‑1 says so.
9. **Flags:** none. `mode: int = 2` is a magic number (1 = heating only, 2 = heating+cooling+off, per the sibling class's setup comment) — supplement says keep as a field; **agreed**, but it should become an enum.
10. **Hazards.** `heat_distribution_system_type: Any` — untyped, and it is the field that would carry an enum fact (R3.7 needs a real type before `value_type=` can be given).

---

### `MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig` (`more_advanced_heat_pump_hplib.py:1970`)

1. SH controller of the heat pump every heat‑pump setup uses.
2. **status: to convert.**
3. `get_default_space_heating_controller_config(heat_distribution_system_type: Any, name=…, component_id=None, upper_temperature_offset_for_state_conditions_in_celsius=5.0, lower_temperature_offset_for_state_conditions_in_celsius=5.0, set_heating_threshold_outside_temperature_in_celsius=16.0, set_cooling_threshold_outside_temperature_in_celsius=20.0)` — `:1987`. setups **4** (`automatic_default_connections.py:110`, `household_heatpump_building_sizer.py:306`, `household_heatpump_car_building_sizer.py:307`, `household_heatpump_solar_thermal_building_sizer.py:298`), tests **3** (`test_controller_l2_energy_management_system.py`, `test_heating_meter.py`, `test_time_resolution.py`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields.**
   - `heat_distribution_system_type` ← `heat_distribution_system_type`, **copy**. Setup‑side today: `heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type` (`household_heatpump_building_sizer.py:307`). Fact exists.
   - `set_heating_threshold_outside_temperature_in_celsius` ← HDS controller threshold, **copy**. Setup‑side: `=my_hds_controller_information.set_heating_threshold_temperature_in_celsius` (`:308`). **Fact must be created first.**
   - `set_cooling_threshold_outside_temperature_in_celsius = 20.0` — §6 mirror step table, future; plain default.
6. **Facts provided:** none.
7. **Behaviour: neutral** once the threshold fact exists (the setups already feed exactly that value).
8. **Deletions.** The one factory.
9. **Flags:** none boolean.
10. **Hazards.**
   - `mode` is **hard‑coded to `1` inside the factory** (`:2004`) even though the factory takes no `mode` parameter, and then three setups assign `my_heatpump_controller_sh_config.mode = hp_controller_mode` post‑construction where `hp_controller_mode = 1` (`household_heatpump_building_sizer.py:126,310`). The assignment is a **no‑op** today. Converting to `preset_standard` + `config: {mode: 1}` is neutral, but the redundancy must be noticed rather than "preserved" as if it meant something.
   - `heat_distribution_system_type: Any` again untyped.

---

### `MoreAdvancedHeatPumpHPLibControllerDHWConfig` (`more_advanced_heat_pump_hplib.py:2479`)

1. DHW controller of the same heat pump.
2. **status: to convert.**
3. `get_default_dhw_controller_config(name="HeatPumpControllerDHW", component_id=None)` — `:2498`. setups **4** (same four files, `:120/:318/:319/:309`), tests **3**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none in B1.** §6 proposes `p_th_max_dhw_in_watt = min(HP_max, k·apts)` (medium) and `t_min/t_max_dhw_storage` from a DHW‑storage setpoint fact that does not exist. Plain defaults (`40.0`, `60.0`, `5000.0`).
6. **Facts provided:** none.
7. **Behaviour: neutral** — the preset is a literal copy of the four hard‑coded values.
8. **Deletions.** The one factory.
9. **Flags:** `thermalpower_dhw_is_constant` (default `False`; the comment at `:2511` explains `False` = modulating, `True` = constant). A2 → `config:`.
10. **Hazards.** `p_th_max_dhw_in_watt=5000.0` is documented as "only if true" — i.e. dead unless the flag is set. A `describe` reader will see a live field that is inert in the default preset; worth a docstring, not a law.

---

### `GenericHeatPumpControllerConfig` (`generic_heat_pump.py:78`)

1. Controller of the demo `GenericHeatPump`.
2. **status: to convert.**
3. `get_default_generic_heat_pump_controller_config(component_id=None)` — `:93`, returns `Any`. setups **0 by this name** — the three basic‑household setups construct the controller but via the same‑named method on the *other* class; grep for `.get_default_generic_heat_pump_controller_config(` returns **0** in `system_setups/`, **0** in `tests/`, **0** in `hisim/`. (The name collides with `HeatPumpHplibControllerL1Config`'s factory, which is also unused — the collision is why the naive grep count was ambiguous.)
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none in B1.** §6: `temperature_air_heating_in_celsius` (18.0) and `temperature_air_cooling_in_celsius` (26.0) are copies of the Building's set temperatures — **both facts already exist** (`context.py:56-57`) but the current values differ from the Building's, so `AUTO` would be a physics change. Plain defaults.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory; give it a real return type.
9. **Flags:** none. `mode: int = 1` magic number.
10. **Hazards.** Two identically named factories on two unrelated classes in two modules — the sweep's `describe` output and any name‑based discovery (`json_executor.py:_get_default_config` matches on method name) will disambiguate only by class. Worth resolving in the same PR.

---

### `ElectricHeatingControllerConfig` (`generic_electric_heating.py:731`)

1. Controller of the direct electric heater.
2. **status: to convert.**
3. **Factories.**
   - `get_default_electric_heating_controller_config(component_id=None, with_domestic_hot_water_preparation=False, set_heating_threshold_outside_temperature_in_celsius=16.0, parallel_space_heating_and_dhw_option=False)` — `:747`. setups **0**, tests **1**, hisim **0**.
   - `get_electric_heating_config_based_on_building_efficiency(specific_heating_load_of_building_in_watt_per_m2: float, component_id=None, with_domestic_hot_water_preparation=False, set_heating_threshold_outside_temperature_in_celsius=16.0, parallel_space_heating_and_dhw_option=False)` — `:767`. setups **1** (`household_electric_heating_building_sizer.py:282`), tests **0**, hisim **0**.
4. **Presets.** Supplement: `standard`, second factory is the sizable form of the first (conflict 3). **Agreed.**
5. **Sized fields.**
   - `specific_heating_load_of_building_in_watt_per_m2` ← `heating_load_in_watt`, `conditioned_floor_area_in_m2`, math **fn: ratio**. **Setup‑side today**: `specific_heating_load_of_building_in_watt_per_m2 = my_building_information.max_thermal_building_demand_in_watt / my_building_information.scaled_conditioned_floor_area_in_m2` (`household_electric_heating_building_sizer.py:284-285`). The identical law already exists as `HeatDistributionControllerConfig.SPECIFIC_LOAD_LAW` (`heat_distribution_system.py:882-887`) — reuse it, do not re‑derive.
   - `set_heating_threshold_outside_temperature_in_celsius` ← the sibling field above, math **fn: step table** — `HeatDistributionControllerConfig.set_heating_threshold_temperature_based_on_building_efficiency` (`heat_distribution_system.py:956-968`): `18.0` for `50 < x ≤ 80`, `20.0` for `x > 80`, else the passed‑in `16.0`. Called cross‑module at `generic_electric_heating.py:779`. Mirror of `HEATING_THRESHOLD_LAW` (`heat_distribution_system.py:890-896`) — a `fields=("specific_heating_load_of_building_in_watt_per_m2",)` law. Not constant.
6. **Facts provided:** none.
7. **Behaviour: neutral** — the sized preset reproduces `get_electric_heating_config_based_on_building_efficiency` exactly. **No constant disagreement** between the twins: both set `hysteresis_water_temperature_offset=15` and the same defaults; the sized one only overwrites the threshold via the step table.
8. **Deletions.** Both factories. Also removes the **cross‑module import of `HeatDistributionControllerConfig` into `generic_electric_heating.py`** — a nice E6 (locality) win, since the law moves to the shared `Size`/context vocabulary.
9. **Flags** (both A2 → `config:`): `with_domestic_hot_water_preparation` (`False`; setup passes `True`), `parallel_space_heating_and_dhw_option` (`False`; setup passes `True`).
10. **Hazards.** `specific_heating_load_of_building_in_watt_per_m2: Optional[float]` — the unsized factory leaves it `None` (`:764`) while the sized one fills it. As a `Sizable[Optional[float]]` it matches the HDS controller's existing declaration (`heat_distribution_system.py:915`), so the pattern is proven.

---

### `DistrictHeatingControllerConfig` (`generic_district_heating.py:859`)

1. Controller of the district‑heating station.
2. **status: to convert.**
3. `get_default_district_heating_controller_config(component_id=None, with_domestic_hot_water_preparation=False, set_heating_threshold_outside_temperature_in_celsius=16.0, parallel_space_heating_and_dhw_option=False)` — `:874`, returns `Any`. setups **1** (`household_district_heating_building_sizer.py:345`), tests **1**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields.** `set_heating_threshold_outside_temperature_in_celsius` ← HDS controller threshold, **copy**. **Setup‑side today**: `=my_hds_controller_information.set_heating_threshold_temperature_in_celsius` (`household_district_heating_building_sizer.py:347`). **Requires the new fact.** Note the asymmetry with the electric‑heating controller, which recomputes the same number from the specific load instead of copying it from the HDS controller — after the sweep both should read the same fact.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory; real return type instead of `Any`.
9. **Flags:** `with_domestic_hot_water_preparation` (`False`; setup `True`), `parallel_space_heating_and_dhw_option` (`False`; setup `True`).
10. **Hazards.** `hysteresis_water_temperature_offset_in_celsius=15` here vs `hysteresis_water_temperature_offset=15` on the electric‑heating controller — same quantity, two field names. E8 freezes both once released; rename now if ever.

---

### `AirConditionerControllerConfig` (`air_conditioner.py:730`)

1. On/off controller of the database air conditioner.
2. **status: to convert.**
3. `get_default_air_conditioner_controller_config(component_id=None)` — `:747`, returns `Any`. setups **1** (`air_conditioned_house.py:214`), tests **1**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none in B1.** §6: `heating_set_temperature_deg_c` (20.0) and `cooling_set_temperature_deg_c` (24.0) copy the Building's set temperatures — facts exist, values differ → physics change; plain defaults.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory; real return type. `component_id` defaults to `ComponentID(name="AirConditionerControllerConfig")` (`:754`) — the **config class name**, not the component's. A latent naming bug the preset removes.
9. **Flags:** none.
10. **Hazards.** `minimum_runtime_s=30*60`, `minimum_idle_time_s=15*60` — int‑product literals; expect float drift in regenerated fixtures.

---

### `SimpleAirConditionerControllerConfig` (`simple_air_conditioner.py:513`)

1. Deadband controller of the Carnot AC.
2. **status: to convert.**
3. `get_default_simple_air_conditioner_controller_config(component_id=None)` — `:526`. setups **1** (`simple_air_conditioner_household_building_sizer.py:126`), tests **12** (`tests/test_simple_air_conditioner.py`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none.** §6: `setpoint_temperature_c` (24.0) copies the Building's `set_cooling_temperature_in_celsius` — fact exists, value differs → physics change; plain default.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory (again a verbatim restatement of the dataclass defaults, `:517-518` vs `:534-535`).
9. **Flags:** none.
10. **Hazards.** None. Pair it with `SimpleAirConditionerConfig` in one commit.

---

### `SolarThermalSystemControllerConfig` (`solar_thermal_system.py:763`)

1. ΔT controller of the solar‑thermal loop.
2. **status: to convert.**
3. `get_solar_thermal_system_controller_config(component_id=None, name="SolarThermalSystemController", set_temperature_difference_for_on=10)` — `:775`. setups **3** (`household_gas_solar_thermal.py:200`, `household_gas_solar_thermal_building_sizer.py:365`, `household_heatpump_solar_thermal_building_sizer.py:384`), tests **0**, hisim **0**.
4. **Presets.** Supplement: `standard`, noting the factory name lacks `default`. **Agreed.**
5. **Sized fields: none.** One real field.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory.
9. **Flags:** none.
10. **Hazards.** `set_temperature_difference_for_on=10` is an `int` default on a `float` field — float drift in fixtures.

---

### `L1CHPControllerConfig` (`controller_l1_chp.py:35`) → `L1CHPController` (`:216`)

1. Hysteresis controller for the gas CHP / fuel cell.
2. **status: to convert.**
3. Four `@staticmethod` factories, all `(component_id=None)`:
   - `get_default_config_chp` — `:65`. setups 0, tests 0, hisim 0.
   - `get_default_config_fuel_cell` — `:89`. **0 everywhere.**
   - `get_default_config_chp_with_buffer` — `:113`. **0 everywhere.**
   - `get_default_config_fuel_cell_with_buffer` — `:138`. setups 0, tests **1** (`tests/test_generic_chp.py:44`), hisim 0.
4. **Presets.** Supplement's table lists four names; conflict 2 then resolves to `gas` + `hydrogen` with the buffer as a field override. **Deviation flagged — the resolution does not survive contact with the code.** The four factories do not factor into (fuel) × (buffer):

   | field | `chp` | `chp_with_buffer` | `fuel_cell` | `fuel_cell_with_buffer` |
   |---|---|---|---|---|
   | `use` | GAS | GAS | GREEN_HYDROGEN | GREEN_HYDROGEN |
   | `h2_soc_threshold` | 0 | 0 | 8.0 | 8.0 |
   | `t_min_heating_in_celsius` | 20.0 | 35.0 | 20.0 | **31.0** |
   | `t_max_heating_in_celsius` | 20.5 | 40.0 | 20.5 | 40.0 |
   | `t_min_dhw_in_celsius` | **42** | **50** | **50** | **42** |
   | `day_of_heating_season_begin` | 270 | 269 | 270 | 269 |

   `t_min_dhw_in_celsius` flips **42 → 50** on the gas axis and **50 → 42** on the hydrogen axis, and `t_min_heating` is 35 for the gas buffer but 31 for the hydrogen buffer. Neither is explained by any comment; both look like copy‑paste drift (`:99` vs `:124` vs `:151`). Under the accepted resolution, `gas` reproduces `get_default_config_chp` exactly and `gas_with_buffer` needs **four** `config:` overrides — which is the noise A2 was meant to avoid. Owner call D‑4.
   Proposed if the inconsistency is a bug: presets **`gas`, `hydrogen`**, buffer as four documented overrides, and the 42/50 flip fixed in a separate physics commit.
5. **Sized fields: none in B1.** §6's temperature copies need HDS‑flow and DHW‑storage facts that do not exist; `day_of_heating_season_*` needs weather facts (parking lot).
6. **Facts provided:** none.
7. **Behaviour: neutral** for the one live call site (`fuel_cell_with_buffer`) only if its exact tuple is reproducible — under `hydrogen` + overrides it is.
8. **Deletions.** All four; convert `@staticmethod` → `@classmethod`.
9. **Flags:** none boolean; the buffer axis is *not* a flag on this config, which is precisely the problem.
10. **Hazards.** `use: LoadTypes` enum (R3.7). `L1CHPController.__init__` at `:248` does `if not config.__class__.__name__ == L1CHPControllerConfig.__name__:` — a **string comparison of class names** as a type guard; it will accept any class with the same name and will break if a resolved copy is a different class object. Worth fixing while touching the module.

---

### `L1HeatPumpConfig` (`controller_l1_heatpump.py:42`) → `L1HeatPumpController` (`:173`)

1. Generic vessel controller (building / buffer / DHW).
2. **status: delete candidate.** **Zero call sites anywhere** — `grep -rn "controller_l1_heatpump\|L1HeatPumpConfig\|L1HeatPumpController"` over `system_setups/`, `tests/` and `hisim/` returns exactly one hit, a *comment* in `controller_l2_energy_management_system.py:164`. It is one of the plan's "3 zombies" (D13), but unlike the other two it still imports cleanly and was not moved to `obsolete/`.
3. Three `@staticmethod` factories, all `(name="Controller", component_id=None)`: `get_default_config_heat_source_controller` `:69`, `…_buffer` `:90`, `…_dhw` `:112`. **0 / 0 / 0** call sites.
4. **Presets** if it survives. Supplement: `space_heating`, `buffer`, `dhw`. **Agreed** — clean rule‑1 fit (the variant is which vessel is regulated), canonical `space_heating`.
5. **Sized fields: none in B1.** §6's `t_min/t_max_heating` from an HDS flow‑temperature band and `day_of_heating_season_*` from the weather are both parking‑lot facts.
6. **Facts provided:** none.
7. **Behaviour: neutral** (nothing calls it). The three factories differ only in `t_min/t_max_heating`, `cooling_considered` and `day_of_heating_season_begin` (270 vs 269); everything else is identical — no constant disagreement.
8. **Deletions.** Whole module if D‑2 says delete.
9. **Flags:** `cooling_considered` (`True`/`True`/`False`) — note this one **is** part of what distinguishes `dhw`, so under A2 it should be an override on `dhw`, not part of the name. The name `dhw` is justified by the temperature band, not the flag.
10. **Hazards.** None beyond being dead.

---

### `NightSetbackConfig` (`night_setback_controller.py:45`) → `NightSetbackController` (`:73`)

1. Night temperature setback.
2. **status: to convert.**
3. `get_default_config(component_id=None)` — `:59`, `@staticmethod`. setups **0**, tests **1** (`tests/test_night_setback_controller.py:18`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none.**
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory; `@staticmethod` → `@classmethod`.
9. **Flags:** none.
10. **Hazards.** None.

---

## A3 — Already converted (one line each)

| Class (module) | State |
|---|---|
| `GenericBoilerConfig` (`generic_boiler.py:98`) | **done (P1)** — 7 presets `preset_condensing_gas` … `preset_hydrogen` (`:177-246`), `MAXIMAL_POWER_LAW` (`:136`), per‑preset `Self("maximal_thermal_power_in_watt") * (1/12)` for pellets/wood chips (`:228,240`), `SIZING_CONTRIBUTIONS` at `:1637`. No legacy factories left on the config class. |
| `GenericBoilerControllerConfig` (`generic_boiler.py:1009`) | **done (P1), partially** — `preset_modulating` `:1038`, `preset_on_off` `:1050`, sized `minimal/maximal_thermal_power_in_watt` copying the boiler's band (`:1023-1025`); **four legacy factories still live** (`get_default_modulating_…` `:1067`, `…on_off…` `:1095`, `get_default_pellet_controller_config` `:1122`, `get_default_wood_chip_controller_config` `:1149`) — B2 finishes the job by deleting them and turning pellet/wood‑chip into `on_off` + runtime overrides, as the `preset_on_off` docstring already announces. |
| `HeatDistributionConfig` (`heat_distribution_system.py:~90`) | **done (P2)** — `preset_standard` `:121`; three sized fields `:98-104`, including the enum‑typed `heating_system` with `value_type=HeatDistributionSystemType` (the R3.7 reference implementation). |
| `HeatDistributionControllerConfig` (`heat_distribution_system.py:~840`) | **done (P2), partially** — `preset_standard` `:919`, six sized fields `:902-915`, `SPECIFIC_LOAD_LAW` `:882`, `HEATING_THRESHOLD_LAW` `:890`, `SIZING_CONTRIBUTIONS` `:1564`; two legacy factories remain (`get_default_heat_distribution_controller_config` `:930`, `get_config_based_on_building_efficiency` `:971`) for B3, and `heating_system` is the Q‑P1.8 case (plain default until the construction‑year law lands). |

---

## A4 — Exempt / delete rows (`configuration.py` legacy block, heat‑generator half)

| Class | Line | Call sites | Verdict |
|---|---|---|---|
| `GasHeaterConfig` | `configuration.py:681` | **0 anywhere** | **delete** (supplement conflict 10; superseded by `GenericBoilerConfig`). Plain class of class attributes, not a `ConfigBase`, no factory. |
| `GasControllerConfig` | `:694` | `advanced_fuel_cell_controller.py:51,57,240,246,247`; `tests/test_advanced_fuel_cell_controller.py:23,63,64,83,223` | **cannot delete standalone** — blocked on the fate of `advanced_fuel_cell_controller`. |
| `CHPControllerConfig` | `:655` | `advanced_fuel_cell_controller.py:38-56,114,129,160,198,204,205`; `tests/test_advanced_fuel_cell_controller.py:22,26,61,62,75,91,189` | **same blocker.** |
| `ExtendedControllerConfig` | `:809` | `get_default_config` `:825`, **0 call sites**; the class is read as **class attributes** by `advanced_fuel_cell_controller.py:103,123,481,499,500,513,533,544,551,585,586` | **delete** — see D13 below; it is a `@dataclass` with no field defaults, so `ExtendedControllerConfig.chp` raises `AttributeError` (verified). |
| `PhysicsConfig`, `EmissionFactorsAndCostsForFuelsConfig`, `EmissionFactorsAndCostsForDevicesConfig` | `:849`, `:252`, `:582` | live | **exempt** (data tables, not component configs) — confirmed, unchanged from the supplement. |
| `CHPConfigAdvanced` | `advanced_fuel_cell.py:94` | **0** | **delete or `for_device`** — not a dataclass, not a `ConfigBase`, reads `mock_up_efficiencies.xlsx` in `__init__`. Constructs successfully today. |

---

## Dependency order inside group A

**Gate 0 (before any conversion, one commit, touches `hisim/config/`).** Add `set_heating_threshold_outside_temperature_in_celsius` to `SizingContext` (`context.py`) and `Size` (`context.py:104+`), and add it to `HeatDistributionControllerConfig.SIZING_CONTRIBUTIONS` (`heat_distribution_system.py:1564`). Six group‑A controllers copy it and cannot be converted without it. This is the one E6 exception in the batch.

Then:

1. **Leaves, no facts consumed or provided** — any order, good first commits:
   `SimpleAirConditionerConfig` + `SimpleAirConditionerControllerConfig`; `NightSetbackConfig`; `IdealizedHeaterConfig`; `SolarThermalSystemControllerConfig`; `GenericHeatPumpConfig` (+ `for_device`) and `GenericHeatPumpControllerConfig`; `SimpleHeatSourceConfig`; `advanced_fuel_cell.CHPConfig`; `generic_chp.CHPConfig` (self‑referential `Self("p_th")` laws only).
2. **Generators reading Building facts** (both facts already exist):
   `MoreAdvancedHeatPumpHPLibConfig` → `heating_load_in_watt`, `heating_reference_temperature_in_celsius`.
   `ElectricHeatingConfig`, `DistrictHeatingConfig` → `heating_load_in_watt`.
   `SolarThermalSystemConfig` → `number_of_apartments` (physics change, own commit).
   `AirConditionerConfig` → `heating_load_in_watt` + `heating_reference_temperature_in_celsius` (blocked on D‑3).
   `HeatPumpHplibConfig` → blocked on the `Quantity` codec (D‑1).
3. **Controllers reading the HDS controller's facts** — after Gate 0 *and* after B3 has actually converted the HDS controller's remaining factories, since these read `heat_distribution_system_type` and the new threshold fact:
   `MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig`, `DistrictHeatingControllerConfig`, `HeatPumpHplibControllerL1Config`.
   `ElectricHeatingControllerConfig` is the odd one out: it reads only Building facts (`heating_load_in_watt`, `conditioned_floor_area_in_m2`) and reuses the HDS controller's two *static* law helpers — so it can go in step 2, and doing so **deletes a cross‑module import** (`generic_electric_heating.py:779` → `HeatDistributionControllerConfig`).
4. **Controllers reading their own generator's fact** — after their generator:
   `MoreAdvancedHeatPumpHPLibControllerDHWConfig` (only if §6's `min(HP_max, k·apts)` is adopted; otherwise step 1).
5. **Consumers outside group A** — B4's `SimpleHotWaterStorageConfig` reads `maximal_thermal_power_in_watt` from whichever generator; group A must have declared `SIZING_CONTRIBUTIONS` first. For heat pumps this is neutral; for boilers it is the C11 gate.

The chain is acyclic and at most 3 deep (Building → HDS controller → generator controller), matching the inventory.

---

## D13 list for these modules

**Import check** (`local_python_env/bin/python -c "import hisim.components.<m>"`) — all 17 modules in scope import cleanly: `advanced_heat_pump_hplib`, `more_advanced_heat_pump_hplib`, `generic_heat_pump`, `generic_electric_heating`, `generic_district_heating`, `air_conditioner`, `simple_air_conditioner`, `idealized_electric_heater`, `simple_heat_source`, `solar_thermal_system`, `advanced_fuel_cell`, `advanced_fuel_cell_controller`, `generic_chp`, `controller_l1_chp`, `controller_l1_heatpump`, `night_setback_controller`, `configuration`. **No import failures.**

**Build‑from‑own‑defaults check** — all 38 factories in the group build successfully, including `CHPConfigAdvanced()` (the Excel file is present). **One failure:**

- **`configuration.ExtendedControllerConfig` — defective by design.** `ExtendedControllerConfig.get_default_config()` builds fine, but `advanced_fuel_cell_controller.ExtendedControllerSimulation` reads the *class* attributes (`ExtendedControllerConfig.chp_power_states_possible` `:103`, `.maximum_autarky` `:123`, `.chp` `:499`, `.gas_heater` `:481,533`, `.electrolyzer` `:551`, `.chp_mode` `:481,500,513`). Since `ExtendedControllerConfig` is a `@dataclass` whose fields carry **no defaults**, every one of those raises at run time:
  ```
  AttributeError: type object 'ExtendedControllerConfig' has no attribute 'chp'
  ```
  (verified). The same applies to `chp.CHPConfig.is_modulating` / `.p_el_min` / `.p_el_max` at `:102,104,105` — `advanced_fuel_cell.CHPConfig` is also a defaults‑free dataclass. `tests/test_advanced_fuel_cell_controller.py` passes only because it exercises `_sensor_index` and `__init__`, never the control methods. **`ExtendedControllerSimulation.control_*` is unreachable dead code that would crash on first call.** Add to the D13 list; it is not in the plan's enumeration.

- **`controller_l1_heatpump` (`L1HeatPumpConfig` / `L1HeatPumpController`) — dead, not defective.** Zero call sites in `system_setups/`, `tests/` or `hisim/`. Named as a D13 zombie in `plan.md:72`, but on this branch it imports and builds fine; the other two named zombies are already in `obsolete/`.

- **`HeatPumpHplibControllerL1Config` / `HeatPumpHplibController` — dead.** Zero call sites for the config, the factory and the component class.

- **`advanced_heat_pump_hplib.HeatPumpHplibConfig` — near‑dead.** Zero setup uses; 2 test uses; but the *component* class is named by `simple_water_storage.py:809`, `controller_l2_energy_management_system.py:974`, `electricity_meter.py:384` and the capex post‑processing, so it cannot simply be dropped.

- **Not a D13 case but a real defect: shared mutable default.** `SolarThermalSystemConfig.get_default_solar_thermal_system` and its capex twin both take `coordinates: Coordinates = Coordinates(50.78, 6.08)` (`:105`, `:145`); `Coordinates` is an unfrozen `@dataclass` (`hisim/component.py:590`), so all configs built from the factory share one instance.

---

## Decisions the owner must take

**D‑1 — `advanced_heat_pump_hplib`: convert or retire?**
(a) Convert: needs a `Quantity`‑aware `sized_field`/law path (the only class in the group with `Quantity`‑typed sizable fields), for a class no setup instantiates. (b) Lower it to plain floats first (golden‑neutral — 2 test call sites) and then convert like its sibling. (c) Retire it as a duplicate of `more_advanced_heat_pump_hplib` and rewire the four references in `simple_water_storage`, EMS, `electricity_meter` and the capex post‑processing.
*Consequence:* (a) grows the kernel for a dead class; (b) is two cheap commits; (c) removes ~1370 lines and a near‑duplicate module but touches four other components, violating the "mechanical PR" shape of P4.

**D‑2 — `controller_l1_heatpump`: delete or convert?**
(a) Move to `obsolete/` with the other two zombies. (b) Convert with presets `space_heating`/`buffer`/`dhw`.
*Consequence:* (a) removes 438 lines and three preset names from the wire vocabulary before they are ever released; (b) mints three names nothing uses, which E8 then freezes at P5.

**D‑3 — `AirConditionerConfig`: how does a twelve‑field database selection become a "law"?**
(a) A build‑time **constructor** `for_building_load(heating_load, heating_reference_temperature)` that runs the existing search and returns a complete config, with no `AUTO` fields at all. (b) Extend the kernel with a multi‑field law (one callable filling several fields), which nothing else in the repo needs today. (c) Freeze the Samsung device as `standard` and drop the search entirely.
*Consequence:* (a) keeps the kernel scalar and is a two‑line change, but the selection is invisible to the sizing report and to `sizing_sources`; (b) is a kernel feature with exactly one consumer; (c) is a physics change to `air_conditioned_house.py`.

**D‑4 — `L1CHPControllerConfig`: is the 42/50 DHW flip a bug?**
The four factories cross fuel × buffer inconsistently: `t_min_dhw_in_celsius` is 42/50/50/42 and `t_min_heating_in_celsius` is 35 for the gas buffer but 31 for the hydrogen buffer. (a) Declare it a copy‑paste bug, normalise, and ship `gas` + `hydrogen` with a clean buffer override set. (b) Preserve it byte‑for‑byte, which means either four presets (against conflict 2's resolution) or a `gas`/`hydrogen` pair whose buffer overrides differ per fuel — i.e. the "buffer" override is not one thing.
*Consequence:* (a) is a physics change to `tests/test_generic_chp.py:44` only (no setup uses this class) and gives a clean wire vocabulary; (b) freezes an unexplained asymmetry into the format.

**D‑5 — `advanced_fuel_cell.CHPConfig`: `standard` or `hydrogen`?**
Its single default sets `gas_type="Hydrogen"`. (a) `standard` per the supplement. (b) `hydrogen` per rule 1 ("the name names the variant"), matching `generic_chp.CHPConfig`.
*Consequence:* (a) is one fewer deviation from the accepted supplement; (b) means a reader of an energy‑system file can tell the two `CHPConfig` classes' presets apart at a glance, and leaves room for a `gas` variant that the class's `gas_type` field plainly anticipates.

**D‑6 — Drop the two `*_8kw` presets?**
The supplement proposes `air_water_8kw` for both hplib classes. Neither 8000 W value is a catalogue device — both are default arguments on a `model="Generic"` hplib curve fit — which conflict 3's own rule says should be dropped, not suffixed. (a) Drop both; the four affected test call sites pass `set_thermal_output_power_in_watt` as an override. (b) Keep them as the supplement wrote.
*Consequence:* (a) removes two wire names that would otherwise imply a device that does not exist; (b) keeps the supplement's table literal and saves editing four tests.

**D‑7 — Solar‑thermal area law: accept the physics change?**
A `4 m²/apartment` law makes `household_gas_solar_thermal.py:191` (`area_m2=4`, unmultiplied) agree with its two sizer twins. (a) Adopt the law and record the result diff — that setup is outside the 8‑setup golden fleet, so nothing will catch it automatically. (b) Keep `area_m2` a plain field and leave the three setups passing it by hand.
*Consequence:* (a) fixes what looks like a missing `* number_of_apartments` but changes results for every MFH archetype in that setup; (b) leaves setup‑side arithmetic that P4 exists to remove.

**D‑8 — `advanced_fuel_cell_controller` and the `configuration.py` CHP/gas controller pair.**
`GasControllerConfig` and `CHPControllerConfig` are proposed for deletion (conflict 10) but are live imports of `advanced_fuel_cell_controller`, whose control methods are provably broken (D13 above). (a) Obsolete `advanced_fuel_cell_controller` together with `ExtendedControllerConfig`, `GasControllerConfig`, `CHPControllerConfig` and `tests/test_advanced_fuel_cell_controller.py` in one commit. (b) Fix the class‑attribute reads (give the dataclasses defaults) and keep the component.
*Consequence:* (a) unblocks the whole `configuration.py` deletion in one move and removes code that cannot run; (b) resurrects a component nothing uses and freezes `ExtendedControllerConfig` as wire format.


---

## P4 batch group B — heat distribution, storages, electricity, EMS and meters

Read: `epic.md`, `plan.md` §P4, `preset_naming_supplement.md`, `sizing_fact_inventory.md` §2/§3/§6, `p3_setup_inventory.md` §3/§4, `p4_class_survey.md` (group A), and the converted pilots (`generic_boiler.py`, `heat_distribution_system.py`, `electricity_meter.py`, `controller_l2_energy_management_system.py`) plus `hisim/config/{presets,sizing,laws,context,contributions,channels}.py`. Branch `energy_system_files`, nothing modified. Sub‑sections are lettered `B‑a…B‑f` so they do not collide with the plan's batch names B1–B8.

### Scope resolution first

Modules named in the assignment that **do not exist on this branch**: `generic_battery.py`, `generic_hot_water_storage_modular.py`, `generic_heat_water_storage.py`, `generic_ev_charger.py`, `controller_l1_generic_runtime.py`. The whole `obsolete/` tree was moved out to a separate repository in commit `28bfa1dd` (#590); `generic_battery` and `generic_ev_charger` got there via `11dc2dc1` (#574). **All six of the plan's "6 defective" D13 members (`generic_battery` ×2, `generic_ev_charger` ×4) are therefore already gone from this repository** — the D13 gate for group B is smaller than `plan.md:72` assumes.

`generic_windturbine.py` is the spelling (not `generic_wind_turbine.py`). No generic `controller_l1_*` module is left in scope: `controller_l1_example_controller` and `controller_l1_generic_ev_charge` are group C, `controller_l1_heatpump` is group A, the rest are H₂/CHP (group C).

Added by inspection of `ls hisim/components`: `dual_circuit_system.py` (`SetTemperatureConfig`, filed under Heat distribution in the supplement, not covered by group A) and the storage/electricity half of the `configuration.py` legacy block (`WarmWaterStorageConfig`, `HydrogenStorageConfig`, `PVConfig`, `LoadConfig`, `ElectricityDemandConfig`, `HouseholdWarmWaterDemandConfig`) — group A took only the heat‑generator half.

**17 config classes in the group's own modules, 3 of them converted** (`HeatDistributionConfig`, `ElectricityMeterConfig`, `EMSConfig` fully; `HeatDistributionControllerConfig` partially), plus 6 legacy `configuration.py` classes and `SetTemperatureConfig`.

**Two facts the group needs do not exist yet.** `SizingContext` (`hisim/config/context.py:47-57`) has 11 fields; neither `roof_area_in_m2` (read by the PV law) nor `pv_peak_power_in_watt` (read by the battery law, named in inventory §3) is among them, and `BuildingConfig.SIZING_CONTRIBUTIONS` (`hisim/components/building/information.py:779-790`) contributes six facts, none of them the roof area. Adding both facts + their `Size` terms + the Building contribution is a **prerequisite commit for B5** and it touches `hisim/config/` — the group‑B equivalent of group A's Gate 0, i.e. the second place where E6 (locality) does not hold. A third and fourth fact (`heating_value_of_fuel_in_kwh_per_liter`, `fuel_density_in_kg_per_m3`) are needed only if D‑15 is decided in favour of the copy law.

**No group‑B module contains a construction‑time `SingletonSimRepository` *write*.** Every `set_entry` in the group is in `i_prepare_simulation` (`generic_pv_system.py:797`) or `i_simulate` (`generic_price_signal.py:230,234`) — the runtime half the plan parks. The group's only construction‑time singleton coupling is the two dead *reads* of `WATERMASSFLOWRATEOFHEATGENERATOR` (see B‑b).

---

## B‑a — Heat distribution (remaining)

### `HeatDistributionControllerConfig` (`hisim/components/heat_distribution_system.py:842`) → `HeatDistributionController` (`:1003`)

1. The controller that turns the building's efficiency into a flow‑temperature curve and a heating threshold; provider of `water_mass_flow_rate_in_kg_per_second` and `heat_distribution_system_type`.
2. **status: converted (P2), partially — the two legacy factories are B3's whole job, and finishing it is a physics change (D‑11).**
3. **Factories.**
   - `get_default_heat_distribution_controller_config(heating_load_of_building_in_watt, set_heating_temperature_for_building_in_celsius, set_cooling_temperature_for_building_in_celsius, set_heating_threshold_outside_temperature_in_celsius=16.0, heating_reference_temperature_in_celsius=-7.0, heating_system=FLOORHEATING, component_id=None)` — `:930`. setups **3** (`household_gas_solar_thermal.py:126`, `basic_household_only_heating.py:82`, `automatic_default_connections.py:94`), tests **8** (`test_heat_distribution_system.py:128`, `test_gas_meter.py:112`, `test_controller_l2_energy_management_system.py:139`, `test_heating_meter.py:113`, `test_sizing_engine.py:166`, `test_fuel_meter.py:117`, `test_config_enum_serialization.py:93`, `test_time_resolution.py:388`), hisim **0**. Sets `specific_heating_load_of_building_in_watt_per_m2=None` and leaves the threshold at the literal `16.0`.
   - `get_config_based_on_building_efficiency(… , specific_heating_load_of_building_in_watt_per_m2, …)` — `:971`. setups **10** — every `_building_sizer` except the electric‑heating one: `household_gas:290`, `household_pellets:289`, `household_oil:315`, `household_hydrogen_boiler:276`, `household_wood_chips:298`, `household_heatpump_car:287`, `household_district_heating:324`, `household_heatpump_solar_thermal:277`, `household_gas_solar_thermal_building_sizer:277`, `household_heatpump:286`. tests **0**, hisim **0**.
   - `set_heating_threshold_temperature_based_on_building_efficiency` (`:956`, static): 1 external caller, `generic_electric_heating.py:778` (group A's cross‑module import).
   - `preset_standard` (`:919`): **0 call sites anywhere.** The preset exists and is unused; all 21 live call sites still go through the two factories.
4. **Presets.** Supplement: `standard`. **Agreed** — already minted. No deviation.
5. **Sized fields** — all six already declared (`:902-915`), all factory‑side today:
   - `set_heating_threshold_outside_temperature_in_celsius` ← own `specific_heating_load_…`, math **fn** step table, `HEATING_THRESHOLD_LAW` `:890` over `heating_threshold_for` `:868`; the legacy body is `set_heating_threshold_temperature_based_on_building_efficiency` `:956-968` (`18.0` above 50 W/m², `20.0` above 80 W/m²). Not a constant law.
   - `specific_heating_load_of_building_in_watt_per_m2` ← `heating_load_in_watt`, `conditioned_floor_area_in_m2`, math **fn** ratio, `SPECIFIC_LOAD_LAW` `:882`. In the 10 sizer setups this ratio is still computed **setup‑side**: `specific_heating_load_of_building_in_watt_per_m2=my_building_information.max_thermal_building_demand_in_watt / my_building_information.scaled_conditioned_floor_area_in_m2` (`household_gas_building_sizer.py:296-297`).
   - `heating_load_of_building_in_watt` ← `heating_load_in_watt`, **copy** rounded 2 (`:914`; legacy `round(heating_load_of_building_in_watt, 2)` `:993`).
   - `heating_reference_temperature_in_celsius`, `set_heating_temperature_for_building_in_celsius`, `set_cooling_temperature_for_building_in_celsius` ← the same‑named Building facts, **copy**.
   - `heating_system` — a plain default `FLOORHEATING` (`:900`), **not** `AUTO`: the Q‑P1.8 construction‑year law is scheduled but its facts do not exist. It stays a `config:` override; the 10 sizer setups pass `heating_system=my_hds_system`, chosen at `household_gas_building_sizer.py:134,136` between `FLOORHEATING` and `RADIATOR`.
6. **Facts provided.** `water_mass_flow_rate_in_kg_per_second` and `heat_distribution_system_type`, via `_hds_controller_sizing_facts` (`:1547-1560`) and `SIZING_CONTRIBUTIONS` (`:1564`). Group A additionally needs `set_heating_threshold_outside_temperature_in_celsius` added here (its Gate 0).
7. **Behaviour: NOT neutral for the `get_default_*` call sites — physics change.** For the 10 setups on `get_config_based_on_building_efficiency` the conversion is byte‑identical (same step table, same ratio, same rounding). For the **3 setups + 8 tests** on `get_default_heat_distribution_controller_config` the threshold is the hard literal `16.0` today, while `preset_standard` computes it. All three setups build the plain `BuildingConfig.preset_standard("Building")` (`basic_household_only_heating.py:64`, `household_gas_solar_thermal.py:104`, `automatic_default_connections.py:66`), for which (verified with `local_python_env/bin/python`):

   `heating_load = 7780.75 W`, `floor = 121.2 m²` → `specific = 64.198 W/m²` → **threshold 18.0 °C, not 16.0 °C**.

   None of the three is in the golden fleet (`p3_setup_inventory.md` §4a: the 8 gates are all `_building_sizer.py`), so the +2 °C shift in `summer_heating_condition` would pass CI silently. → **D‑11.**
8. **Deletions.** Both factories (`:930`, `:971`). `set_heating_threshold_temperature_based_on_building_efficiency` (`:956`) must survive as a private helper until group A converts `ElectricHeatingControllerConfig`, which imports it. No SimRepository keys.
9. **Flags:** none boolean on this class.
10. **Hazards.**
    - `heating_system: HeatDistributionSystemType` is enum‑typed; if Q‑P1.8 ever makes it `AUTO` it needs `value_type=HeatDistributionSystemType` (P2 R3.7) exactly as `HeatDistributionConfig.heating_system` already does (`:98-100` — the reference implementation).
    - `basic_household_only_heating.py:88` mutates the config **after** construction: `my_heat_distribution_controller_config.heating_system = …RADIATOR`. In a file that is a `config:` line, but the recorder must diff against a fresh preset.
    - `HeatDistributionControllerInformation` (13 instantiations, `p3 §3b`) is constructed both by the setups and inside `_hds_controller_sizing_facts` — the derivation runs twice per run today; harmless but worth a note in the sweep.
    - Imports cleanly; all four builders build from their own defaults.

### `SetTemperatureConfig` (`hisim/components/dual_circuit_system.py:18`)

1. The set‑temperature tuple `DiverterValve.determine_operating_mode` takes.
2. **status: exempt** (supplement conflict 9 — not a `ConfigBase`, not a component config).
3. No factory. Constructed at **run time**, per timestep, inside three group‑A generators: `generic_boiler.py:1495`, `generic_electric_heating.py:981`, `generic_district_heating.py:1163`.
4. **Presets: none.** Supplement proposes `standard`; **deviation flagged** — it is neither a component config nor a build‑time object, so it can have neither preset nor constructor. Conflict 9's own resolution already says "exempt"; the supplement's Heat‑distribution table contradicts its own conflict list. Recommend the exemption.
5. Sized fields: none (§6 lists all four as future copies of the HDS flow band, which does not exist).
6. Facts provided: none. 7. Neutral. 8. Nothing to delete. 9. No flags.
10. **Hazard:** it is a bare `@dataclass` with four mandatory fields, so the contract test must skip it explicitly rather than fail it as "a config class with no builder".

---

## B‑b — Storages

### `SimpleHotWaterStorageConfig` (`hisim/components/simple_water_storage.py:73`) → `SimpleHotWaterStorage` (`:542`)

1. The space‑heating buffer vessel; the C11 class.
2. **status: to convert — blocked on C11 (D‑9) and on where the l/kW factor lives (D‑10).**
3. **Factories.**
   - `get_default_simplehotwaterstorage_config(component_id=None)` — `:100`, pins 500 l. setups **0**, tests **1** (`test_config_enum_serialization.py:76`, which passes the bound method itself), hisim **0**.
   - `get_scaled_hot_water_storage(max_thermal_power_in_watt_of_heating_system, name="SimpleHotWaterStorage", component_id=None, sizing_option=SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM)` — `:127`. setups **12**, tests **6**, hisim **0** (two doc references only: `hisim/cost_database/sources.json:383`, `configuration.py:306`).
     Setups: `household_gas_building_sizer.py:348`, `household_oil_building_sizer.py:371`, `household_hydrogen_boiler_building_sizer.py:334`, `household_pellets_building_sizer.py:345`, `household_wood_chips_building_sizer.py:359`, `household_gas_solar_thermal.py:164`, `household_gas_solar_thermal_building_sizer.py:324`, `basic_household_only_heating.py:130`, `household_heatpump_building_sizer.py:364`, `household_heatpump_car_building_sizer.py:365`, `household_heatpump_solar_thermal_building_sizer.py:343`, `automatic_default_connections.py:161`. Tests: `test_sizing_energy_systems.py:157`, `test_gas_meter.py:159`, `test_controller_l2_energy_management_system.py:213`, `test_heating_meter.py:143`, `test_fuel_meter.py:168`, `test_time_resolution.py:463`.
4. **Presets.** Supplement: `buffer` (conflict 4: "one preset `buffer`; `sizing_option` stays a field"). **Deviation flagged, twice.** (a) `sizing_option` is **not a field** — `dataclasses.fields(SimpleHotWaterStorageConfig)` is `component_id, volume_heating_water_storage_in_liter, heat_transfer_coefficient_in_watt_per_m2_per_kelvin, heat_exchanger_is_present, position_hot_water_storage_in_system` + 5 capex (verified). It is a factory *argument* only, so today the resolved config does not record which law produced the volume — a provenance hole P2's realized record cannot close by itself. Conflict 4's resolution therefore requires **adding a field**, which it does not say. (b) `plan.md:88` (B4) says "hot water storage (**per‑generator volume presets**)", i.e. the opposite of conflict 4. → **D‑10.**
5. **Sized fields.**
   - `volume_heating_water_storage_in_liter` ← today the *argument* `max_thermal_power_in_watt_of_heating_system`; math **×k** with `k ∈ {50, 20, 40, 50, 20}` l/kW selected by `sizing_option`:
     `volume_heating_water_storage_in_liter = max_thermal_power_in_watt_of_heating_system / 1e3 * 50` (heat pump, `:148`); `* 20` (general, `:154`); `* 40` (pellets, `:158`); `* 50` (wood chips, `:162`); `* 20` (gas heater, `:166`); then `round(…, 2)` at `:174`. Factory‑side. Not a constant law.
     **The fact it must read is `maximal_thermal_power_in_watt` of the generator** (inventory §3) — but **all 12 setups pass the building load instead**: `max_thermal_power_in_watt_of_heating_system=my_building_information.max_thermal_building_demand_in_watt` (identical line in all 12, e.g. `household_gas_building_sizer.py:349`). That mismatch *is* C11.
   - Nothing else is sized. `heat_transfer_coefficient_in_watt_per_m2_per_kelvin=2.0` and `heat_exchanger_is_present=True` are identical in both factories (`:112-115` vs `:172-175`) — no constant disagreement.
6. **Facts provided:** none. (§6 lists no downstream consumer of the storage volume.)
7. **Behaviour — the C11 gate, quantified.** Reading `<generator>.maximal_thermal_power_in_watt` instead of the building load multiplies the volume by `max(load, 2500·apts)·1.1 / load` (the boiler law, `generic_boiler.py:119-135`). Verified against each setup's own default archetype (`ModularHouseholdConfig.get_default_config_for_household_*` → `BuildingInformation`):

   | setup | sizing_option | k [l/kW] | volume today | volume from generator | Δ | golden‑gated? |
   |---|---|---|---|---|---|---|
   | `household_gas_building_sizer` | GAS_HEATER | 20 | 155.62 l | 171.18 l | **+10.0 %** | yes |
   | `household_oil_building_sizer` | GENERAL | 20 | 155.62 l | 171.18 l | **+10.0 %** | yes |
   | `household_hydrogen_boiler_building_sizer` | GAS_HEATER | 20 | 155.62 l | 171.18 l | **+10.0 %** | yes |
   | `household_pellets_building_sizer` | PELLET | 40 | 311.23 l | 342.35 l | **+10.0 %** | yes |
   | `household_wood_chips_building_sizer` | WOOD_CHIP | 50 | 389.04 l | 427.94 l | **+10.0 %** | yes |
   | `household_gas_solar_thermal_building_sizer` | GAS_HEATER | 20 | 155.62 l | 171.18 l | **+10.0 %** | no |
   | `household_gas_solar_thermal` | GAS_HEATER | 20 | 155.62 l | 171.18 l | **+10.0 %** | no |
   | `basic_household_only_heating` | GAS_HEATER | 20 | 155.62 l | **240.00 l** | **+54.2 %** | no (smoke only) |
   | `household_heatpump_building_sizer` | HEAT_PUMP | 50 | — | unchanged | **0** | yes |
   | `household_heatpump_car_…`, `household_heatpump_solar_thermal_…`, `automatic_default_connections` | HEAT_PUMP | 50 | — | unchanged | **0** | no |

   All defaults resolve to `DE.N.SFH.05.Gen.ReEx.001.002`, 1 apartment, `load = 7780.75 W`, `boiler = 8558.83 W`. The heat‑pump setups are exactly neutral because `MoreAdvancedHeatPumpHPLibConfig.get_scaled_advanced_hp_lib` is a pure copy of the load (group A, `more_advanced_heat_pump_hplib.py:175`) — the C11 gate bites the **boiler setups only**, exactly as group A predicted. `basic_household_only_heating` is the outlier because its boiler is the *pinned* `preset_condensing_gas_12kw` (`:112`, 12 000 W) while its storage is sized from a 7.78 kW load.

   **The +10 % is not the ceiling.** The ratio is `max(load, 2500·apts)·1.1/load`, so it exceeds 1.1 whenever DHW dominates. For a 2016‑vintage MFH (`DE.N.MFH.12.Gen.ReEx.001.003`, 17 apartments, load 27 220 W, `2500·17 = 42 500 W`) the ratio is **1.717 → +72 %**. RenoVisor and the building sizer reach such archetypes; the golden fleet does not.

   So: 5 of the 8 golden setups shift by exactly +10 %, 1 golden setup (heat pump) is untouched, 2 golden setups (district heating, electric heating) have no buffer storage at all, and the 3 largest relative shifts are all outside any numeric gate.
8. **Deletions.** Both factories. **Dead SimRepository key — verified.** `SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR` (`hisim/sim_repository_singleton.py:141`) has **two readers and zero writers** in the whole repository:
   ```
   simple_water_storage.py:599   if SingletonSimRepository().entry_exists(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR):
   simple_water_storage.py:601       SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR)
   simple_water_storage.py:1326  if SingletonSimRepository().entry_exists(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR):
   simple_water_storage.py:1328      SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR)
   ```
   `grep -rn 'WATERMASSFLOWRATEOFHEATGENERATOR' --include=*.py .` returns no `set_entry`; the only historical writer, `obsolete/generic_heat_pump_for_house_with_hds.py:240`, left with the `obsolete/` tree in `28bfa1dd`.
   **Correction to `sizing_fact_inventory.md`** (§ preamble and §3): the readers are **not** "both storages". Class boundaries are `SimpleHotWaterStorage` at `:542`, `SimpleHotWaterStorageController` at `:1300`, `SimpleDHWStorage` at `:1432` — so the readers are `SimpleHotWaterStorage.__init__` and **`SimpleHotWaterStorageController.__init__`**; `SimpleDHWStorage` never touches the key.
   The dead branch is also **unrunnable**: if a writer ever existed, `SimpleHotWaterStorage.i_simulate` would raise `UnboundLocalError`, because `water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second` is assigned only in the `else` branch (`:934`) but read unconditionally at `:971` and `:1091`. Deleting the key is therefore behaviour‑neutral *and* removes a latent crash.
9. **Flags.** `heat_exchanger_is_present: bool` — `True` in both factories, comment "until now stratified mode is causing problems" (`:114`). A2 → `config:` override, **but** it changes physics, not wiring: `build()` `:1212-1224` picks `(1, 0)` vs `calculate_mixing_factor_for_water_temperature_outputs()`.
10. **Hazards.**
    - **The config value shapes the I/O surface.** `SimpleHotWaterStorage.__init__:633` adds four inputs (`WaterTemperatureFromHeatGenerator`, `WaterMassFlowRateFromHeatGenerator`, and the two `…Secondary…`) **only if** `position_hot_water_storage_in_system == PARALLEL_TO_HEAT_SOURCE`; `:917` and `:1097` branch on it again at run time. Same class of hazard as group A's `with_domestic_hot_water_preparation`.
    - **Two same‑named enums with different members.** `simple_water_storage.PositionHotWaterStorageInSystemSetup` (`:58`) = `PARALLEL_TO_HEAT_SOURCE | SERIES_TO_HEAT_SOURCE`; `heat_distribution_system.PositionHotWaterStorageInSystemSetup` (`:61`) = `PARALLEL | SERIES | NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR | NO_STORAGE_MASS_FLOW_FIX`. Both are config fields (`simple_water_storage.py:85`, `heat_distribution_system.py:106`), so one energy‑system file would spell the same topology fact `PARALLEL` on the HDS and `PARALLEL_TO_HEAT_SOURCE` on the storage. Under E8 both spellings freeze at P5. → **D‑17.**
    - `HeatDistribution.__init__:293-294` makes its **default‑connection set depend on a config value** (`if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL: add_default_connections(get_default_connections_from_simple_hot_water_storage())`). This is the group's only `get_default_connections`‑depends‑on‑config case.
    - No mutable defaults (all 10 fields mandatory). `component_id` is defaulted inside both factories (`ComponentID(name="SimpleHotWaterStorage")`, `:106`/`:145`) — the preset takes the name as its first argument.
    - Imports cleanly; both factories build.

### `SimpleHotWaterStorageControllerConfig` (`hisim/components/simple_water_storage.py:192`) → `SimpleHotWaterStorageController` (`:1300`)

1. Controller of the buffer vessel.
2. **status: delete** (see D‑16).
3. `get_default_simplehotwaterstoragecontroller_config()` — `:203`, returns `Any`, takes no name. setups **0**, tests **0**, hisim **0**. **The component class has zero call sites outside its own module** as well.
4. **Presets.** Supplement: `standard`. **Deviation flagged:** minting a preset for a class nothing instantiates freezes a wire name at P5 for dead code (same argument group A made for `controller_l1_heatpump`, D‑2). Recommend deletion instead.
5. Sized fields: none (the config has exactly one field, `component_id`).
6. Facts provided: none. 7. Neutral either way. 
8. **Deletions.** The factory, the config and the component — and with the component, **one of the two readers of `WATERMASSFLOWRATEOFHEATGENERATOR`** (`:1326-1331`).
9. Flags: none.
10. **Hazards.** Return type `Any`; the factory hard‑codes `ComponentID(name="SimpleHotWaterStorageController")` with no `name` argument, so two instances would collide.

### `SimpleDHWStorageConfig` (`hisim/components/simple_water_storage.py:215`) → `SimpleDHWStorage` (`:1432`)

1. The domestic‑hot‑water vessel; 13 instantiations, the third‑most‑used class in the fleet.
2. **status: to convert — the cleanest conversion in the group.**
3. **Factories.**
   - `get_default_simpledhwstorage_config(component_id=None)` — `:238`, pins 250 l. **0 call sites anywhere.**
   - `get_scaled_dhw_storage(number_of_apartments=1, default_volume_in_liter=250.0, name="DHWStorage", component_id=None)` — `:261`. setups **13** (`household_gas:337`, `household_pellets:334`, `household_hydrogen_boiler:323`, `household_heatpump_car:354`, `household_heatpump_solar_thermal:394`, `household_heatpump:353`, `household_electric_heating:303`, `household_district_heating:368`, `household_oil:360`, `household_gas_solar_thermal:209`, `household_wood_chips:348`, `household_gas_solar_thermal_building_sizer:375`, `automatic_default_connections:152`), tests **6** (`test_sizing_energy_systems.py:168`, `test_gas_meter.py:169`, `test_controller_l2_energy_management_system.py:202`, `test_heating_meter.py:184`, `test_fuel_meter.py:178`, `test_time_resolution.py:452`), hisim **0**.
4. **Presets.** Supplement: `standard`, and "`default_volume_in_liter=250.0` becomes the law constant, not a name". **Agreed, no deviation.**
5. **Sized fields.** `volume_heating_water_storage_in_liter` ← `number_of_apartments`, math **×k**: `volume = default_volume_in_liter * max(number_of_apartments, 1)` (`:274`), i.e. `250 · max(apts, 1)`. Factory‑side, argument supplied setup‑side. Not a constant law. The clamp `max(…, 1)` needs either a `max` in the law or a guarantee that the fact is ≥ 1 — the algebra has `_ClampedLaw` (`hisim/config/laws.py:289`) but `at_least`/`at_most` are in the plan's parking lot; a two‑line function law with `reads=(Size.NUMBER_OF_APARTMENTS,)` is the P4‑safe form.
   `heat_transfer_coefficient_in_watt_per_m2_per_kelvin=0.36` is identical in both factories — a plain default.
6. **Facts provided:** none today. §6 wants a *DHW storage set temperature* fact for the DHW controllers; that value is not on this config.
7. **Behaviour: neutral** in 12 of 13 setups. **One latent divergence:** `household_gas_solar_thermal.py` passes the *archetype's* `number_of_dwellings_per_building` (`:98`, `:210`) while its Building is the un‑overridden `BuildingConfig.preset_standard("Building")` (`:104`) — nothing pushes the apartment count into the building. A law reading `Size.NUMBER_OF_APARTMENTS` would read the Building's value instead. Today both are 1, so the change is numerically neutral; it stops being neutral the moment that setup gets an MFH archetype.
8. **Deletions.** Both factories.
9. **Flags:** none.
10. **Hazards.** No mutable defaults (all 8 fields mandatory). `component_id` defaulted inside both factories as `ComponentID(name="DHWStorage")`.
    **A real two‑provider case exists here, but it is a wiring case, not a sizing case:** in `household_gas_solar_thermal_building_sizer.py:389-411` the DHW storage is fed by the solar thermal system as *primary* and the gas boiler as *secondary* generator. Its volume is still sized from apartments, so it is **not** the many‑cardinality consumer EQ2 waits for.

---

## B‑c — Electricity generation and storage

### `PVSystemConfig` (`hisim/components/generic_pv_system.py:98`) → `PVSystem` (`:267`)

1. The pvlib rooftop array; 17 instantiations, the most‑used unconverted class in the fleet (`p3 §3b`).
2. **status: to convert — and it carries a recording defect that must be decided first (D‑12).**
3. **Factories.**
   - `get_default_pv_system(name="PVSystem", power_in_watt=10e3, source_weight=0, share_of_maximum_pv_potential=1.0, location="Aachen", component_id=None, module_name="Trina Solar TSM-435NE09RC.05", module_database=CEC_MODULE_DATABASE, inverter_name="Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]", inverter_database=CEC_INVERTER_DATABASE)` — `:136`. setups **15**, tests **3** + 1 method reference (`test_config_enum_serialization.py:70`), hisim **1** (its own caller at `:202`). Four of the 15 call it with **no arguments at all** and thus depend on the 10 kW default: `basic_household.py:86`, `basic_household_with_weather_data_request.py:119`, `default_connections.py:77`, `dynamic_components.py:101`.
   - `get_scaled_pv_system(rooftop_area_in_m2, name="PVSystem", share_of_maximum_pv_potential=1.0, …)` — `:180`. setups **12**, tests **7** (`test_controller_l2_energy_management_system.py:125`, `test_heating_meter.py:92`, `test_gas_meter.py:91`, `test_fuel_meter.py:96`, `test_electricity_meter.py:60`, `test_time_resolution.py:376`, `test_sizing_energy_systems.py:147`), hisim **0**.
   - `PVSystem.get_default_config(power_in_watt=10e3, source_weight=1, share_of_maximum_pv_potential=1.0, component_id=None)` — `:441`, a **static method on the component class** returning `Any`, pinning `Hanwha HSL60P6-PA-4-250T [2013]` + Sandia databases against the config class's Trina/CEC. **0 call sites anywhere.**
4. **Presets.** Supplement: `rooftop`, `rooftop_10kw`; conflict 5 resolves "delete the component‑class factory; canonical preset `rooftop` from `get_scaled_pv_system`". **Agreed on `rooftop` and on the deletion.** **Deviation flagged on `rooftop_10kw`, in the opposite direction from group A's D‑6:** 10 kW is a round default, not a catalogue rating, so conflict 3's rule says drop it — but unlike the hplib `_8kw` case, **four setups actually depend on that default** (list above), and dropping it turns four zero‑argument call sites into explicit `power_in_watt: 10000` overrides. Recommend **keeping** `rooftop_10kw` and recording the exception. → part of **D‑13**.
5. **Sized fields.**
   - `power_in_watt` ← `roof_area_in_m2` (**fact does not exist yet**) plus three own fields, math **fn**:
     ```
     effective_rooftop_area_in_m2 = rooftop_area_in_m2 * 0.6            # generic_pv_system.py:255-257
     total_pv_power_in_watt = (effective_rooftop_area_in_m2 / module_area_in_m2 * module_power_in_watt) * share_of_maximum_pv_potential
     return round(total_pv_power_in_watt, 2)                            # :259-264
     ```
     with `(module_area_in_m2, module_power_in_watt)` a two‑entry table over `(module_name, module_database)`: `(1.65, 250.0)` for Hanwha/Sandia, `(1.98, 435.16)` for Trina/CEC, `ValueError` otherwise (`:230-252`). Factory‑side. Not a constant law. Expressible as `law(fn(ctx, own), reads=(Size.ROOF_AREA_IN_M2,), fields=("share_of_maximum_pv_potential", "module_name", "module_database"))` — the existing `fields=` protocol (`hisim/config/sizing.py:189-191`) covers it; no kernel extension beyond the new fact.
   - `share_of_maximum_pv_potential` stays a **plain field** (an author/consumer choice, default 1.0 — `hisim/building_sizer_utils/interface_configs/system_config.py:181`). §6's "1 − ST_area/roof" is the roof‑contention many‑reader, deferred.
   - `location: str` is a second hand‑typed copy of the weather location (§6, confidence high); all 12 sizer setups pass `location=weather_location`. Group C owns the Weather side; leave a plain field in B5.
6. **Facts provided.** `pv_peak_power_in_watt` = resolved `power_in_watt` (inventory §3) — **the fact does not exist in `SizingContext` yet**, and `PVSystemConfig` has no `SIZING_CONTRIBUTIONS`. Its only consumer is the battery.
7. **Behaviour: neutral for the golden fleet, NOT neutral in general — a recording defect.** `get_scaled_pv_system` applies the share inside `size_pv_system` and then calls `get_default_pv_system` **without forwarding it** (`:202-211`), so the field records `1.0` regardless. Verified:
   ```
   get_scaled_pv_system(rooftop_area_in_m2=100, share_of_maximum_pv_potential=0.5)  -> power 6593.33, share recorded 1.0
   get_scaled_pv_system(rooftop_area_in_m2=100, share_of_maximum_pv_potential=1.0)  -> power 13186.67, share recorded 1.0
   get_default_pv_system(power_in_watt=10000, share_of_maximum_pv_potential=0.5)    -> power 5000.0, share recorded 0.5
   ```
   The two factories therefore disagree on what the field *means*. Any law that reads `Self("share_of_maximum_pv_potential")` reproduces the `get_default_*` semantics, i.e. it **changes** every `get_scaled_*` result whose share ≠ 1. The golden fleet is safe because `share_of_maximum_pv_potential` defaults to `1.0`; RenoVisor and building‑sizer payloads that set it are not. Also: a realized record written today is **not re‑executable** for share ≠ 1 (EAC2/UC5), because replaying `share=1.0` with the recorded power double‑counts nothing but loses the provenance. → **D‑12.**
8. **Deletions.** `PVSystem.get_default_config` (`:441`, 0 call sites, diverging module/inverter/database — conflict 5). Both config‑class factories become the two presets. No SimRepository construction‑time keys (`:797` is inside `i_prepare_simulation`, `:649`).
9. **Flags** (all A2 → `config:`): `integrate_inverter` (`True`), `load_module_data` (`False`, and `get_scaled_pv_system` overrides it *after* delegating, `:210`), `predictive` (`False`), `predictive_control` (`False`).
10. **Hazards.**
    - **Post‑construction mutation by every sizer setup:** `my_photovoltaic_system_config.azimuth = azimuth` / `.tilt = tilt` (`household_gas_building_sizer.py:279-280` and the same two lines in the other 11). Both become `config:` overrides.
    - `module_database`/`inverter_database` are enum‑typed (`PVLibModuleAndInverterEnum`); if either ever becomes sizable it needs `value_type=` (P2 R3.7).
    - The module table (`:230-252`) raises `ValueError` for any module the two hard‑coded pairs do not cover, so a `config:` override of `module_name` alone breaks the law at build time — the error message must survive into the file‑format catalogue.
    - 22 mandatory fields, no defaults, no mutable defaults. Imports cleanly; all three factories build.
    - `source_weight` differs between the two factories (`0` in `get_default_pv_system`, `1` in the dead `PVSystem.get_default_config`) — an EMS ranking value, not a size.

### `BatteryConfig` (`hisim/components/advanced_battery_bslib.py:42`) → `Battery` (`:136`)

1. The bslib AC‑coupled home battery; 13 instantiations.
2. **status: to convert** — after PV, because it reads PV's fact.
3. **Factories.**
   - `get_default_config(component_id=None, name="Battery")` — `:76`, pins **10 kWh** / 5000 W. setups **2** (`dynamic_components.py:51,58`), tests **0** (both battery tests construct `BatteryConfig(...)` directly, `test_advanced_battery_bslib.py:36,121`), hisim **0**.
   - `get_scaled_battery(total_pv_power_in_watt_peak, component_id=None, name="Battery")` — `:103`. setups **11** (`household_gas:406`, `household_pellets:408`, `household_heatpump_solar_thermal:455`, `household_heatpump_car:473`, `household_gas_solar_thermal_building_sizer:443`, `household_heatpump:414`, `household_electric_heating:333`, `household_oil:434`, `household_hydrogen_boiler:392`, `household_district_heating:430`, `household_wood_chips:422`), tests **3** (`test_sizing_energy_systems.py:163`, `test_controller_l2_energy_management_system.py:260`, `test_time_resolution.py:508`), hisim **0**.
4. **Presets.** Supplement: `standard`, `standard_5kwh` (A1/A3, conflict 6). **Deviation flagged:** the nominal factory pins **10 kWh**, not 5 — `custom_battery_capacity_generic_in_kilowatt_hour = 10` (`:80-82`) and `custom_pv_inverter_power_generic_in_watt = round(10 * 0.5 * 1e3, 2)` (`:89`). `standard_5kwh` names a device that does not exist in the code. Options are `standard_10kwh` or, per conflict 3, dropping the rating preset altogether — but `dynamic_components.py` overrides both numbers anyway (`:53-54`, `:60-61`), so nothing depends on the 10 kWh default either. → **D‑14.** Recommend **`standard` only**.
5. **Sized fields.**
   - `custom_battery_capacity_generic_in_kilowatt_hour` ← `pv_peak_power_in_watt` (**fact does not exist yet**), math **×k**: `custom_battery_capacity_generic_in_kilowatt_hour = total_pv_power_in_watt_peak * 1e-3` (`:111-113`), then `round(…, 2)` (`:118`). 1 kWh per kWp. Factory‑side; the argument is supplied setup‑side as `total_pv_power_in_watt_peak=my_photovoltaic_system_config.power_in_watt` (`household_gas_building_sizer.py:406-407`, identical in all 11).
   - `custom_pv_inverter_power_generic_in_watt` ← the same fact, math **×k**: `round(custom_battery_capacity_generic_in_kilowatt_hour * c_rate * 1e3, 2)` with `c_rate = 0.5` (`:114`, `:119`).
   **Golden‑parity trap:** line `:119` multiplies the **unrounded** local, while line `:118` stores the rounded capacity. A law written as `Self("custom_battery_capacity_generic_in_kilowatt_hour") * 500` would use the rounded value and drift. Measured on the default archetype (roof 168.9 m² → PV 22 272.28 W): today `capacity = 22.27 kWh`, `inverter = 11 136.14 W`; a `Self(...)`‑based law gives `11 135.00 W` — a 1.14 W difference, far outside the golden `rel_tol = 1e-9`. The inverter law must therefore be `Size.PV_PEAK_POWER_IN_WATT * 0.5` rounded to 2, **not** `Self(capacity) * 500`.
6. **Facts provided:** none today. §6 wants battery capacity/C‑rate/efficiency facts for `MpcControllerConfig`; no live consumer.
7. **Behaviour: neutral** for all 11 scaled call sites provided the inverter law reads the fact rather than the sibling field (above). `charge_in_kwh=0`, `discharge_in_kwh=0`, `system_id="SG1"`, `source_weight=1`, `lifetime_in_cycles=5e3` are identical in both factories — no constant disagreement.
8. **Deletions.** Both factories. No `get_default_connections` at all in the module (the EMS wires the battery from its side, `controller_l2_energy_management_system.py:599`).
9. **Flags:** none boolean.
10. **Hazards.**
    - `dynamic_components.py` is the group's only two‑instance case and it **reassigns `component_id` after construction** (`:52`, `:59`: `my_advanced_battery_config_1.component_id = ComponentID("Battery1")`). The preset takes the name as its first argument, so this becomes `preset: standard` under two named components — but the recorder must recognise the mutation as a rename, not an override.
    - 13 mandatory fields, no mutable defaults. Imports cleanly; both factories build.

### `WindturbineConfig` (`hisim/components/generic_windturbine.py:44`) → `Windturbine` (`:212`)

1. windpowerlib turbine.
2. **status: to convert (constructor‑shaped) — or delete (D‑16).**
3. `get_default_windturbine_config(component_id=None)` — `:94`. setups **0**, tests **1** (`test_generic_windturbine.py:49`), hisim **0**.
4. **Presets.** Supplement: `standard` + constructor `for_turbine_type(turbine_type, hub_height)`. **Agreed in shape, one deviation:** the config carries `nominal_power` and `rotor_diameter` alongside `turbine_type`, and the factory pins `rotor_diameter=126` while `turbine_type="V126/3300"` already encodes it. `for_turbine_type` should derive `rotor_diameter`/`nominal_power` from the windpowerlib catalogue rather than take them, otherwise the constructor can produce a self‑inconsistent turbine.
5. **Sized fields: none.** §6's candidates (`hub_height`, `rotor_diameter`, `nominal_power` from `turbine_type`; `measuring_height_*`/`hellman_exp` from the weather data source) are all catalogue lookups without facts. Plain defaults in B5.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The one factory.
9. **Flags:** `density_correction` (`False`), `predictive` (`False`), `predictive_control` (`False`) → A2 `config:`.
10. **Hazards.** `power_coefficient_curve: None` and `power_curve: None` are typed `None` — literally the `NoneType` annotation, not `Optional[...]`; dataclasses_json will round‑trip them but the JSON‑Schema export (P2) has no type to emit. 26 mandatory fields, four of them (`device_co2_footprint_in_kg`, `investment_costs_in_euro`, `maintenance_costs_in_euro_per_year`, `lifetime_in_years`) typed `float` and pinned to `0` rather than `None` — this is the "trailing capex fields need `None` defaults" item from `random_findings` and it *changes* postprocessing (0 suppresses the database lookup). Imports cleanly; the factory builds.

---

## B‑d — EMS, meters and generic controllers

### `GasMeterConfig` (`hisim/components/gas_meter.py:33`) → `GasMeter` (`:80`)

1. Aggregating gas meter for gas and hydrogen boilers.
2. **status: to convert.**
3. `get_gas_meter_default_config(component_id=None, gas_loadtype=lt.LoadTypes.GAS)` — `:59`. setups **4** (`household_gas_building_sizer.py:386`, `household_hydrogen_boiler_building_sizer.py:372` with `gas_loadtype=lt.LoadTypes.GREEN_HYDROGEN`, `household_gas_solar_thermal.py:227`, `household_gas_solar_thermal_building_sizer.py:423`), tests **1** (`test_gas_meter.py:186`), hisim **0**.
4. **Presets.** Supplement: `gas`, "a second carrier would give `biogas`". **Deviation flagged:** the second carrier already exists in a setup — `GREEN_HYDROGEN` — so the preset set is `gas`, `hydrogen`, not `gas` alone. Rule 1 (name the substance) gives `hydrogen`, matching `GenericBoilerConfig.preset_hydrogen`.
5. **Sized fields: none in B5 as proposed.** §6 wants `gas_loadtype` copied from the connected generator's energy carrier (many‑cardinality, consistency aggregator). That fact does not exist and the aggregator is not implemented (`Many` raises, `hisim/config/laws.py:225-228`). Keep a plain field selected by the preset. → part of **D‑15**.
6. **Facts provided:** none.
7. **Behaviour: neutral** (the two presets reproduce the two call shapes exactly).
8. **Deletions.** The one factory.
9. **Flags:** none boolean.
10. **Hazards.**
    - `total_energy_from_grid_in_kwh: float` is a **runtime accumulator living in the config** (`:46`, set to `0.0` by the factory `:70`, read back in `get_cost_opex` at `:344` where it is always the stale `0.0` while the real value is recomputed at `:322-326`). It is dead state that would nonetheless become wire format. Recommend deleting the field in the same commit.
    - `gas_loadtype` is enum‑typed (`lt.LoadTypes`); a sizable version needs `value_type=lt.LoadTypes`.
    - **`GasMeter` is a `DynamicComponent` with no `CHANNELS` declaration** (`grep -rln CHANNELS hisim/components/` returns only `controller_l2_energy_management_system.py` and `electricity_meter.py`). Until it declares channels, P2's consumer‑side `inputs:` cannot describe its feeds — a prerequisite commit for B5, in its own module (E6 holds).

### `FuelMeterConfig` (`hisim/components/fuel_meter.py:35`) → `FuelMeter` (`:67`)

1. Aggregating meter for oil, pellets, wood chips and district heat.
2. **status: to convert.**
3. `get_fuel_meter_default_config(component_id=None, fuel_loadtype=lt.LoadTypes.OIL, heating_value_of_fuel_in_kwh_per_liter=9.82, fuel_density_in_kg_per_m3=0.83*1e3)` — `:48`. setups **4** (`household_oil_building_sizer.py:400`, `household_wood_chips_building_sizer.py:388`, `household_district_heating_building_sizer.py:397`, `household_pellets_building_sizer.py:374`), tests **5** (`test_fuel_meter.py:195`; `tests/components/test_fuel_meter.py:47,58,70,124`), hisim **0**.
4. **Presets.** Supplement: `oil`, `pellets`, `wood_chips`. **Deviation flagged: `district_heating` is missing** — `household_district_heating_building_sizer.py:397-399` passes `fuel_loadtype=lt.LoadTypes.DISTRICTHEATING`, and `FuelMeter.__init__:98-108` accepts exactly those four carriers. Four presets, not three.
5. **Sized fields: none in B5 as proposed.** But this is the group's most concrete future law: three setups copy the two physical constants **from the boiler component instance**:
   ```
   household_oil_building_sizer.py:402-403
       heating_value_of_fuel_in_kwh_per_liter=my_oil_heater.heating_value_of_fuel_in_kwh_per_liter,
       fuel_density_in_kg_per_m3=my_oil_heater.fuel_density_in_kg_per_m3,
   ```
   and the boiler derives them in `__init__`, not in its config: `heating_value_of_fuel_in_kwh_per_liter = heating_value_of_fuel_in_joule_per_m3 / (3.6 * 1e9)` (`generic_boiler.py:533`) where the numerator is the **higher** heating value for `BoilerType.CONDENSING` and the **lower** one for `CONVENTIONAL` (`:520-528`), and `fuel_density_in_kg_per_m3 = PhysicsConfig.get_properties_for_energy_carrier(...).density_in_kg_per_m3` (`:534-536`). So the meter **cannot** derive them from its own `fuel_loadtype` alone — it needs the boiler's `boiler_type` too, i.e. two genuine new facts contributed by `GenericBoilerConfig`. → **D‑15.**
6. **Facts provided:** none.
7. **Behaviour: neutral if the presets ship the values the setups pass.** Verified from `PhysicsConfig`: OIL LHV `9.8217` kWh/l, ρ `830`; PELLETS `3.25`, ρ `650`; WOOD_CHIPS `4.3333`, ρ `250`; DISTRICTHEATING **raises** `"Energy carrier LoadTypes.DISTRICTHEATING not implemented in PhysicsConfig yet"`. Note the factory's default `9.82` is a rounded oil LHV, so a preset must ship the setup's exact value, not the factory default.
   **Latent defect to decide, not to freeze:** the district‑heating setup passes no heating value, so the config records the **oil** numbers `9.82 / 830` for district heat. They are harmless in `get_cost_opex` (the DISTRICTHEATING branch, `:329-334`, works in kWh and never touches `fuel_consumption_in_liter`), but a `district_heating` preset must ship `None` for both, not the oil numbers.
8. **Deletions.** The one factory.
9. **Flags:** none boolean.
10. **Hazards.** `fuel_loadtype` enum‑typed (needs `value_type=lt.LoadTypes` if sized). `FuelMeter` is a `DynamicComponent` with **no `CHANNELS`** — same prerequisite as the gas meter. Four mandatory fields, no mutable defaults; imports and builds.

### `HeatingMeterConfig` (`hisim/components/heating_meter.py:36`) → `HeatingMeter` (`:63`)

1. Aggregating heat meter.
2. **status: to convert — the cheapest conversion in the group.**
3. `get_heating_meter_default_config(component_id=None)` — `:50`. setups **0**, tests **1** (`test_heating_meter.py:201`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none** (the config has exactly one field, `component_id`). §6 confirms.
6. **Facts provided:** none. 7. **Neutral.** 8. The one factory. 9. No flags.
10. **Hazards.** `DynamicComponent` with **no `CHANNELS`** — third instance of the prerequisite. Otherwise none.

### `MpcControllerConfig` (`hisim/components/controller_mpc.py:35`) → `MpcController` (`:~200`)

1. Model‑predictive controller for the air‑conditioned building.
2. **status: delete or defer (D‑16).**
3. `get_default_config(component_id=None)` — `:100`. **0 call sites anywhere** — `tests/test_controller_mpc.py:29` constructs `MpcControllerConfig(...)` directly. The component class has 0 setup uses; the `mpc` pytest marker guards it.
4. **Presets.** Supplement: `standard`. **Deviation flagged:** minting a wire name for a class with no live user, on a config with 49 fields most of which are not configuration (below).
5. **Sized fields: none today.** §6 lists 15 candidates, all against facts that do not exist (Building 5R1C coefficients, battery capacity/C‑rate, device performance curves) — and all of them are **already read at run time** from the `SingletonSimRepository` in `i_prepare_simulation` (`:428-458`) and `build` (`:464-504`), which silently overwrite the config's own `0.0` literals. Sizing them would be a *replacement* of the runtime mechanism, i.e. the plan's parking‑lot item, not a P4 conversion.
6. **Facts provided:** none. 7. Neutral.
8. **Deletions.** The factory if converted; otherwise the module.
9. **Flags:** `predictive` (`True` in the factory) — and it is not an operating option: `i_prepare_simulation` and `build` branch the whole singleton‑read block on it.
10. **Hazards.** **31 of the 49 fields are runtime result buffers**, not configuration — `optimal_cost_in_eur`, `revenues_in_eur`, `pv2load_in_watt`, `battery_control_state`, `batt_soc_normalized_timestep`, all `*_forecast_24h_1min_*` … each defaulted to `[]` by the factory. Serialising them into an energy‑system file would freeze empty lists as wire format and write full‑year arrays into a realized record. Any conversion must first move them off the config. No mutable *dataclass* defaults (all 49 fields mandatory), so no shared‑list bug — but only by accident.

### `PIDControllerConfig` (`hisim/components/controller_pid.py:34`) → `PIDController` (`:~250`)

1. PI controller with a 5R1C feed‑forward identification.
2. **status: to convert (trivial) or delete with the MPC (D‑16).**
3. `get_default_config(component_id=None)` — `:44`. setups **0**, tests **3** (`test_controller_pid.py:126,143,155`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed** (the config has exactly one field).
5. **Sized fields: none;** §6 confirms "config has no tunable fields". Its 5R1C coefficients come from the singleton at `:136-141`, the runtime half.
6. **Facts provided:** none. 7. Neutral. 8. The one factory. 9. No flags.
10. **Hazards.** `BuildingThermalModel5R1C` reads six singleton keys that only the Building writes, so the component is order‑dependent at construction time — but the reads are inside a method, not `__init__`.

### `SumBuilderConfig` (`hisim/components/sumbuilder.py:18`) → `SumBuilderForTwoInputs` / `CalculateOperation` (`:~150`, `:46`)

1. Load‑type/unit plumbing for the two demo setups.
2. **status: to convert.**
3. `get_sumbuilder_default_config()` — `:41`, **takes no arguments** and hard‑codes `ComponentID(name="Sum")`. setups **2** (`simple_system_setup_one.py:60`, `simple_system_setup_two.py:79`), tests **2** (`test_sumbuilder.py:24,96`), hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none;** §6 confirms.
6. **Facts provided:** none. 7. Neutral.
8. **Deletions.** The one factory.
9. **Flags:** none.
10. **Hazards.** (a) The factory takes no `name`, so two sum builders in one system collide — the preset's mandatory first argument fixes this. (b) `get_main_classname()` returns `SumBuilderForTwoInputs.get_full_classname()` (`:34`) although the same config also configures `CalculateOperation` (`:46`) — a config class serving two components with one declared main class. The executor resolves a class from the file, so this is survivable, but `describe` will lie. (c) `loadtype`/`unit` are enum‑typed (`lt.LoadTypes`, `lt.Units`).

### `TransformerConfig` (`hisim/components/transformer_rectifier.py:19`) → `Transformer` (`:50`)

1. Single‑efficiency transformer/rectifier.
2. **status: to convert.**
3. `get_default_transformer_config()` — `:43`, no arguments, hard‑codes `ComponentID(name="Generic Transformer and rectifier Unit")`. setups **0**, tests **1** (`test_transformer_rectifier.py:29`), hisim **0**. (`simple_system_setup_two.py:67` uses the unrelated `example_transformer.ExampleTransformerConfig` — group C.)
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none;** §6 confirms.
6. Facts provided: none. 7. Neutral. 8. The one factory. 9. No flags.
10. **Hazards.** The hard‑coded name contains spaces (`"Generic Transformer and rectifier Unit"`) — it would become a component name in a YAML file; the preset takes the name as an argument instead. Otherwise none.

### `RandomNumbersConfig` (`hisim/components/random_numbers.py:20`) → `RandomNumbers` (`:44`)

1. Test stimulus generator used by the two demo setups.
2. **status: to convert or exempt (D‑16).**
3. `get_default_config()` — `:33`, no arguments, hard‑codes `ComponentID(name="RandomNumbers")` and `timesteps=100`. **0 call sites anywhere** — all four setup uses construct `RandomNumbersConfig(...)` directly (`simple_system_setup_one.py:36,48`, `simple_system_setup_two.py:43,55`), as do the tests.
4. **Presets.** Supplement: `standard`. **Deviation flagged:** the factory has no user and the config has no defensible default (see the hazard).
5. **Sized fields: none;** §6 lists it under "pure technology/model constants".
6. Facts provided: none. 7. Neutral. 8. The one factory (0 call sites).
9. **Flags:** none.
10. **Hazards.** `timesteps: int` is a **simulation parameter in a component config** — `get_default_config` pins `100` while the four setups pass `my_simulation_parameters.timesteps`. A `standard` preset would have to ship a number that is wrong for every real run. Either the field is dropped and read from `SimulationParameters`, or the class is constructor‑only (`for_range(minimum, maximum)`), or it is exempt as a test helper. Two instances per setup, both renamed post‑construction via the `component_id` argument.

---

## B‑e — Already converted (one line each)

| Class (module) | State |
|---|---|
| `HeatDistributionConfig` (`heat_distribution_system.py:89`) | **done (P2), complete** — `preset_standard` `:121`, three sized fields `:98-104` including the enum‑typed `heating_system` with `value_type=HeatDistributionSystemType` (P2 R3.7 reference implementation). `get_default_heat_distribution_config` is gone; **22 call sites already use `preset_standard(...).resolve(SizingContext(...))`** (`household_gas_building_sizer.py:362-368` is the canonical shape). Nothing left for B3 on this class. |
| `ElectricityMeterConfig` (`electricity_meter.py:30`) | **done (P2), partially** — `preset_standard` `:69`, `CHANNELS` `:128-143`, no sized fields (correctly: §6 "pure accounting"). **`get_electricity_meter_default_config` `:51` is still live at 16 setups and 9 test call sites**, while `preset_standard` has exactly **1** (`tests/test_energy_system_wiring.py`). Deleting the legacy factory and moving 25 call sites is a mechanical, byte‑identical B5 commit (both builders produce the same six values). |
| `EMSConfig` (`controller_l2_energy_management_system.py:47`) | **done (P1), complete** — `preset_optimize_own_consumption` `:79`, four `DynamicConnectionChannel`s `:195-231`, **no legacy factory left** (`get_default_config_ems` no longer exists), 12 setups + 3 tests already on the preset. Nothing sized (inventory §2). The `strategy: str` field still duplicates the preset name (`strategy="optimize_own_consumption"`, `:84`) and is commented "more or less obsolete" — a free deletion in B5. §6's `limit_to_shave` many‑reader has no fact and no provider. |
| `HeatDistributionControllerConfig` (`heat_distribution_system.py:842`) | **done (P2), partially** — see B‑a; two legacy factories at `:930` and `:971`, 21 call sites, and the conversion is **not** result‑neutral for 3 of them. |

---

## B‑f — Exempt / delete rows (`configuration.py` legacy block, storage/electricity half; `dual_circuit_system`)

| Class | Line | Call sites | Verdict |
|---|---|---|---|
| `WarmWaterStorageConfig` | `configuration.py:625` | `get_default_config` `:636`, **0 call sites**; the class is named only inside commented‑out code (`generic_heat_pump.py:490,491,498`) | **delete** — supplement conflict 10; superseded by `SimpleHotWaterStorageConfig`. It *is* a `ConfigBase` and builds fine, so it would otherwise be converted by mistake. §6's `tank_diameter`/`tank_height` law would then have no consumer. |
| `PVConfig` | `configuration.py:801` | **0 anywhere** | **delete** — a plain class holding `peak_power = 20_000`; duplicate of `PVSystemConfig`. |
| `HydrogenStorageConfig` | `configuration.py:753` | **0 anywhere** (one comment in `tests/test_generic_electrolyzer_and_h2_storage.py:35`) | **delete** — duplicate of `generic_hydrogen_storage.GenericHydrogenStorageConfig`. Overlaps group C's H₂ scope; the supplement files it under Storages, so it is listed here. |
| `LoadConfig` | `configuration.py:713` | **0 anywhere** | **delete.** |
| `ElectricityDemandConfig` | `configuration.py:730` | **0 anywhere** | **delete.** |
| `HouseholdWarmWaterDemandConfig` | `configuration.py:737` | **live** — read as *class attributes* by `loadprofilegenerator_utsp_connector.py:346,355,356,2002,2004,2005` and `simple_water_storage.py:1831` | **exempt, do not delete.** **Deviation from supplement conflict 10**, which lists it among the nine deletion candidates. It is a physical‑constant table exactly like `PhysicsConfig` (freshwater 10 °C, DHW 45 °C, Grädigkeit 5/6 K) and has live readers in two modules including one of mine. Reclassify as exempt. |
| `SetTemperatureConfig` | `dual_circuit_system.py:18` | live, constructed per timestep by `generic_boiler.py:1495`, `generic_electric_heating.py:981`, `generic_district_heating.py:1163` | **exempt** — see B‑a. |

---

## Dependency order inside group B, and to group A

**Gate B‑0 (before any electricity conversion; one commit; touches `hisim/config/`).** Add `roof_area_in_m2` and `pv_peak_power_in_watt` to `SizingContext` (`context.py:47-57`) and `Size` (`context.py:104+`); add `roof_area_in_m2` to `_building_sizing_facts` / `BuildingConfig.SIZING_CONTRIBUTIONS` (`building/information.py:761-790`, the value is `BuildingInformation.roof_area_in_m2`, already computed). This is group B's E6 exception, the counterpart to group A's Gate 0 (`set_heating_threshold_outside_temperature_in_celsius`). Both gates can be one commit.

**Gate B‑0b (before B5's meters).** Declare `CHANNELS` on `GasMeter`, `FuelMeter` and `HeatingMeter` (their own modules, so E6 holds), mirroring `electricity_meter.py:128-143`. Without it a v3 file cannot express their feeds.

**Gate B‑0c.** Delete `WATERMASSFLOWRATEOFHEATGENERATOR` (its own commit, per the plan's "never bundled with a conversion"). Behaviour‑neutral, verified.

Then:

1. **Leaves, no facts consumed or provided — any order, good first commits:**
   `HeatingMeterConfig`; `SumBuilderConfig`; `TransformerConfig`; `PIDControllerConfig`; `SimpleHotWaterStorageControllerConfig` (or delete); `RandomNumbersConfig` (or exempt); `WindturbineConfig` (+ `for_turbine_type`); `PriceSignalConfig`; `MpcControllerConfig` (or delete).
2. **`SimpleDHWStorageConfig`** → reads `number_of_apartments` only; the fact already exists. Independent of every other class in the group and of group A. Best second commit — 19 call sites moved for one law.
3. **`HeatDistributionControllerConfig`** (B3, finish) → reads only Building facts; must land **before** group A's step 3, which needs its threshold fact. Carries the 16 → 18 °C physics change (D‑11), so it is two commits: the mechanical one for the 10 `get_config_based_on_building_efficiency` setups (neutral) and the physics one for the 3 `get_default_*` setups + 8 tests.
4. **`GasMeterConfig`, `FuelMeterConfig`** → neutral as plain‑field presets; if D‑15 goes the copy‑law way they move **after** group A has given `GenericBoilerConfig` the two fuel facts and the carrier fact.
5. **`PVSystemConfig`** → after Gate B‑0 (`roof_area_in_m2`).
6. **`BatteryConfig`** → after PV, because it reads `pv_peak_power_in_watt`, which PV must contribute first. The chain Building → PV → Battery is the group's only 3‑level chain and matches inventory §3.
7. **`SimpleHotWaterStorageConfig`** → **last**, and only after D‑9. It reads `maximal_thermal_power_in_watt` from whichever generator, so every generator in group A must already declare `SIZING_CONTRIBUTIONS`: `GenericBoilerConfig` does (`generic_boiler.py:1637`), the heat pumps, electric heating and district heating do not yet. Cross‑group edge: **group A steps 1–2 → group B step 7.**
   Note the ambiguity risk under E2: in `household_gas_solar_thermal*` the storage sits in a system with a boiler *and* a solar thermal system. Today only the boiler will declare `maximal_thermal_power_in_watt`, so the bare fact binds. The moment group A's D‑7 gives `SolarThermalSystemConfig` a power fact, those two setups need an explicit `sizing_sources` line.
8. **`ElectricityMeterConfig`** legacy‑factory removal (25 call sites) — independent of everything, can go anywhere.

**Many‑cardinality (EQ2): no class in group B is the first real consumer today.** All 11 `get_scaled_battery` call sites pass exactly one PV config's `power_in_watt`; `dynamic_components.py` has two batteries and one PV but sizes neither from it (`:51-64` uses `get_default_config` plus literal overrides); `PriceSignalConfig` has zero setup uses; the hybrid‑generator pair that does exist (`household_gas_solar_thermal_building_sizer.py:389-411`, solar thermal + gas boiler into one **DHW** storage) is a wiring pair, not a sizing pair, because that storage is sized from apartments. `Many(...)` may stay a raising hook (`hisim/config/laws.py:225-228`) through B5.

---

## D13 list for these modules

**Import check** (`local_python_env/bin/python -c "import hisim.components.<m>"`) — all 18 modules in scope import cleanly: `heat_distribution_system`, `simple_water_storage`, `generic_pv_system`, `advanced_battery_bslib`, `generic_windturbine`, `generic_price_signal`, `electricity_meter`, `gas_meter`, `fuel_meter`, `heating_meter`, `controller_l2_energy_management_system`, `controller_mpc`, `controller_pid`, `sumbuilder`, `transformer_rectifier`, `random_numbers`, `dual_circuit_system`, `configuration`. **No import failures.**

**Build‑from‑own‑defaults check** — all **28** presets/factories in the group build successfully (script in the scratchpad; includes both HDS legacy factories with dummy arguments, all five storage factories, all three PV factories including the dead component‑class one, both battery factories, and `configuration.WarmWaterStorageConfig.get_default_config`). **No build failures.** Group B contributes **nothing** to the D13 list of "cannot be built from own defaults".

What it *does* contribute, as dead-or-defective code the gate should sweep up:

- **`SimpleHotWaterStorageController` + `SimpleHotWaterStorageControllerConfig` — dead.** 0 call sites outside `simple_water_storage.py`. Holds one of the two readers of the dead singleton key.
- **`PVSystem.get_default_config` (`generic_pv_system.py:441`) — dead and divergent.** 0 call sites; pins `Hanwha HSL60P6-PA-4-250T [2013]` + Sandia databases against the config class's Trina + CEC. Conflict 5 already resolves: delete.
- **`MpcControllerConfig.get_default_config` (`controller_mpc.py:100`) and `RandomNumbersConfig.get_default_config` (`random_numbers.py:33`) — 0 call sites each.**
- **`MpcController`, `PIDController`, `Windturbine`, `PriceSignal` — 0 setup uses**, tests only.
- **`configuration.WarmWaterStorageConfig`, `PVConfig`, `HydrogenStorageConfig`, `LoadConfig`, `ElectricityDemandConfig` — 0 call sites**, all five deletable in one commit.
- **Not a D13 case but a latent defect:** `SimpleHotWaterStorage.i_simulate` would raise `UnboundLocalError` on `water_mass_flow_rate_from_secondary_heat_generator_in_kg_per_second` (assigned only at `:934`, read at `:971` and `:1091`) if `WATERMASSFLOWRATEOFHEATGENERATOR` ever had a writer again.
- **Not a D13 case but a recording defect:** `PVSystemConfig.get_scaled_pv_system` records `share_of_maximum_pv_potential = 1.0` regardless of the share it applied (`:198`, `:202-211`; verified). See D‑12.
- **No mutable defaults anywhere in group B** — every unconverted config has all fields mandatory (verified over all 21 classes). Group A's `Coordinates` hazard has no analogue here.

---

## Decisions the owner must take

**D‑9 — C11: does the buffer storage read the generator's power or the building's load?**
All 12 setups pass `my_building_information.max_thermal_building_demand_in_watt` into a parameter named `max_thermal_power_in_watt_of_heating_system` (`simple_water_storage.py:127`). Options: (a) **Bless the status quo** — declare the storage law a reader of `heating_load_in_watt`, byte‑identical, and rename the parameter. (b) **Fix the physics** — read `<generator>.maximal_thermal_power_in_watt`, own commit with result diffs: five golden setups (`gas`, `oil`, `hydrogen_boiler`, `pellets`, `wood_chips`) shift by exactly **+10.0 %** storage volume, the heat‑pump golden setup is unchanged, `basic_household_only_heating` shifts **+54.2 %** (155.62 → 240.00 l) and is not numerically gated, and MFH archetypes where `2500·apts > load` shift by up to **+72 %**. (c) **Split**: fix it for the boilers, keep the load for heat pumps — which is what the numbers already do, so it collapses into (b).
*Consequence:* (a) freezes a parameter whose name has been lying since it was written and makes the wire format say "generator power" while meaning "building load"; (b) is the only option under which the `sizing_sources` mapping means what it says, at the cost of re‑blessing five golden references and accepting a silent 54 % change in one ungated setup.

**D‑10 — Where does the l/kW factor live?**
`sizing_option` is **not** a config field (verified); it is a factory argument, so today nothing records which of the five laws produced a volume. Options: (a) **Add `sizing_option: HotWaterStorageSizingEnum` as a field** and give the class one preset `buffer` (supplement conflict 4) — needs `value_type=HotWaterStorageSizingEnum` on the volume law's sibling field and adds a fifth enum to the wire vocabulary. (b) **Five presets** `buffer_for_heat_pump`, `buffer_for_gas_heater`, … (`plan.md:88` "per‑generator volume presets") — which conflict 4 explicitly rejects because it puts the provider's identity into the consumer's preset name. (c) **One preset `buffer` plus a plain numeric field `litres_per_kilowatt`** (20/40/50), which records the actual number and drops the enum entirely.
*Consequence:* (a) is closest to today's code and to the accepted supplement, but the plan says the opposite; (b) reads well in a file and dies the moment a hybrid system needs two generators; (c) is the only one where the realized record shows the number a run used, and it turns five enum members into three integers.

**D‑11 — The HDS‑controller conversion is not result‑neutral. Take the change or preserve 16.0?**
`get_default_heat_distribution_controller_config` hard‑codes `set_heating_threshold_outside_temperature_in_celsius = 16.0`; `preset_standard` computes it. For the three setups and eight tests on that factory the building is always the default `preset_standard` building → 64.198 W/m² → **18.0 °C**. Options: (a) **Convert and record the diff** — `basic_household_only_heating`, `household_gas_solar_thermal` and `automatic_default_connections` change results, and none of them is behind a numeric gate, so nothing will catch a regression later either. (b) **Ship a second preset** (`fixed_threshold_16c`) that pins 16.0, so the three setups stay byte‑identical — one more wire name that E8 freezes at P5, for a value nobody chose deliberately.
*Consequence:* (a) makes all 13 heat‑distribution setups agree on one law, at the cost of an unwitnessed change in three of them (consider adding one of them to the golden fleet in the same commit); (b) preserves the diff and preserves the inconsistency.

**D‑12 — `share_of_maximum_pv_potential`: fix the recording or preserve it?**
`get_scaled_pv_system(share=0.5)` halves the power and records `share = 1.0`; `get_default_pv_system(share=0.5)` halves the power and records `0.5`. Options: (a) **Fix** — the preset's law reads `Self("share_of_maximum_pv_potential")` and the field records the real share. Golden‑neutral (the fleet's share is 1.0, `system_config.py:181`) but changes results for every RenoVisor / building‑sizer payload with `share ≠ 1` that came through the *scaled* path. (b) **Preserve** — the law ignores the field and takes the share as a builder argument, i.e. the field stays a lie. (c) **Delete the field** and make the share a pure builder argument, recording only the resulting `power_in_watt`.
*Consequence:* (a) is the only option under which a realized record re‑executes (EAC2/UC5) for a scaled PV with a share; (b) freezes a field that means two different things depending on which factory built it; (c) loses the provenance the epic exists to provide but is honest.

**D‑13 — Keep `rooftop_10kw`?**
Conflict 3's rule ("rating suffix only for a real catalogue rating") says drop it — 10 kW is a round default. But four setups call `get_default_pv_system()` with no arguments and depend on it (`basic_household.py:86`, `basic_household_with_weather_data_request.py:119`, `default_connections.py:77`, `dynamic_components.py:101`), unlike group A's `air_water_8kw`, which had none. Options: (a) keep `rooftop_10kw` as a documented exception to conflict 3; (b) drop it and give the four setups a `power_in_watt: 10000` override.
*Consequence:* (a) creates one precedent for "a default nobody chose becomes a preset"; (b) is four one‑line edits and keeps the rule clean, but the four demo setups then carry a magic number in the file rather than in the class.

**D‑14 — `BatteryConfig`: which rating preset, if any?**
The supplement proposes `standard` + `standard_5kwh`; the nominal factory pins **10 kWh** and a 5 kW inverter (`advanced_battery_bslib.py:80-89`), so `standard_5kwh` names nothing. Its only two call sites (`dynamic_components.py:51,58`) override both numbers immediately. Options: (a) `standard` only; (b) `standard` + `standard_10kwh`; (c) `standard_10kwh` only, with the sizable form named `standard`.
*Consequence:* (a) removes a name nothing needs and is consistent with group A's D‑6 recommendation; (b) preserves a default that is already overridden everywhere it is used.

**D‑15 — Do the meters copy their carrier and fuel constants from the generator?**
`FuelMeterConfig.heating_value_of_fuel_in_kwh_per_liter` and `fuel_density_in_kg_per_m3` are copied setup‑side from the **boiler component instance** (`household_oil_building_sizer.py:402-403` and the pellet/wood‑chip twins), and the boiler derives them in `__init__` from `energy_carrier` **and** `boiler_type` (`generic_boiler.py:520-536`). `GasMeterConfig.gas_loadtype` / `FuelMeterConfig.fuel_loadtype` are the same story for the carrier. Options: (a) **Plain fields set by the preset** (`oil`/`pellets`/`wood_chips`/`district_heating`, `gas`/`hydrogen`) — neutral, four setup‑side copies survive as literals in the file, and a file can silently pair a gas boiler with an oil meter. (b) **Copy laws** — needs three new facts (`energy_carrier`, `heating_value_of_fuel_in_kwh_per_liter`, `fuel_density_in_kg_per_m3`) contributed by `GenericBoilerConfig` (which must move the `__init__` derivation to build time), plus a `value_type=lt.LoadTypes` codec, plus — the moment two generators exist — the consistency aggregator §6 asks for, which `Many` does not implement.
*Consequence:* (a) ships B5 now and leaves the "gas boiler + oil meter" foot‑gun; (b) is the group's most valuable law but pulls a group‑A class and an unimplemented aggregator into B5's scope.

**D‑16 — The dead classes: delete or mint wire names for them?**
`SimpleHotWaterStorageController(+Config)` (0 call sites at all), `MpcController` (0 setups; 31 of its 49 config fields are runtime buffers), `PIDController` (0 setups), `Windturbine` (0 setups), `PriceSignal` (0 setups), `RandomNumbersConfig.get_default_config` (0), `PVSystem.get_default_config` (0), and the five dead `configuration.py` classes. Options: (a) **Delete** the storage controller, the `configuration.py` five and the three zero‑call‑site factories now, and defer MPC/PID/wind/price to the runtime‑SimRepository redesign in the parking lot; (b) **convert everything**, minting ~9 preset names for code no setup runs; (c) delete all of them including the four components.
*Consequence:* (a) removes the storage controller's copy of the dead singleton read for free and keeps the wire vocabulary honest, while leaving four components in limbo; (b) freezes nine names at P5 and forces `MpcControllerConfig`'s 31 result buffers into the file format; (c) removes the only MPC/PID prototypes in the repo.

**D‑17 — Unify the two `PositionHotWaterStorageInSystemSetup` enums?**
`simple_water_storage.py:58` (`PARALLEL_TO_HEAT_SOURCE`, `SERIES_TO_HEAT_SOURCE`) and `heat_distribution_system.py:61` (`PARALLEL`, `SERIES`, `NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR`, `NO_STORAGE_MASS_FLOW_FIX`) are different types with the same name, both are config fields, and both decide the same topology question — which is really a property of the *system*, not of either component (§6 lists it under topology queries). Options: (a) **Unify now**, before P5 freezes both spellings, into one enum in a shared module with the four‑member vocabulary; (b) leave both and let a file spell the same fact two ways; (c) derive both from a topology query in the executor and drop the fields (the §6 "presence/count" family, which the epic says stays out of the sizing engine).
*Consequence:* (a) is a small, golden‑neutral rename commit now and a breaking change after P5; (b) guarantees the two fields can contradict each other in a hand‑written file, with `HeatDistribution.__init__:293` and `SimpleHotWaterStorage.__init__:633` then wiring inconsistent I/O surfaces; (c) is the right long‑term answer and is out of P4's scope.


---

## P4 batch group C — occupancy, weather, building, mobility, H₂ chain, examples

Read: `epic.md`, `plan.md` §P4, `preset_naming_supplement.md` (rules 1–5, A1–A3, conflicts 1–10), `sizing_fact_inventory.md` §1/§1a/§3/§6, `p3_setup_inventory.md` §2c/§2e/§3, `p4_class_survey.md` groups A and B, and the converted pilots `weather.py`, `building/{config,information}.py`, `loadprofilegenerator_utsp_connector.py` plus `hisim/config/{presets,sizing,laws,context,contributions}.py` and `hisim/energy_system/{configure,bindings,codec,wiring}.py`. Branch `energy_system_files`, nothing modified. Sub-sections are lettered `C‑a…C‑f` so they do not collide with the plan's batch names B1–B8.

### Scope resolution first

Modules named in the assignment that **do not exist on this branch**: `loadprofilegenerator_connector.py` (deleted in `b4e4e40e`, "Utsp connector upgrade" #289 — the non‑UTSP `Occupancy` class is gone; `UtspLpgConnector` is the only occupancy component), `generic_ev_charger.py` (moved out with `obsolete/` in `11dc2dc1`/`28bfa1dd`), `simple_hot_water_demand.py` / `generic_hot_water_demand.py` (never existed — DHW demand lives inside `UtspLpgConnector` and `simple_water_storage.SimpleDHWStorage`, group B), and any `building_*`/`occupancy_*` helper config module (`hisim/components/building/` is `{config,building,information,window}.py` only). `idealized_electric_heater.py` is group A's; there is no other `idealized_*`. `controller_l1_rsoc_battery_system.py` is spelled `controller_l2_rsoc_battery_system.py`.

Group C is therefore the 26 modules left after A and B: `weather.py`, `weather_data_import.py`, `building/`, `loadprofilegenerator_utsp_connector.py`, `generic_smart_device.py`, `generic_car.py`, `advanced_ev_battery_bslib.py`, `controller_l1_generic_ev_charge.py`, `csvloader.py`, the ten H₂/fuel‑cell/RSOC modules, the five example modules, and the H₂ remainder of the `configuration.py` legacy block (`AdvElectrolyzerConfig`) — group A took the heat‑generator half, group B the storage/electricity half.

**Three of group C's classes are the repo's only converted classes besides the pilots** (`p3` §3a): `WeatherConfig`, `BuildingConfig`, `UtspLpgConnectorConfig`. All three still carry a live legacy factory except the Building.

**Group C is the root of the whole fact graph.** `BuildingConfig.SIZING_CONTRIBUTIONS` (`building/information.py:761-790`) is the *only* primary‑fact contribution in the repository; `SizingContext` (`context.py:47-57`) has 11 fields of which the Building fills six. `WeatherConfig` declares **no** contributions at all, so every climate fact of inventory §1a is §6 future.

---

## C‑a — Occupancy, weather, building

### `WeatherConfig` (`hisim/components/weather.py:384`) → `Weather` (`:517`)

1. The climate provider; 20 instantiations, the most‑used component in the fleet (`p3` §3a).
2. **status: converted (P2, PR‑3) — partially. One legacy factory still carries the whole fleet.**
3. **Factories.**
   - `get_default(location_entry: Union[LocationEnum, str], name="Weather", component_id=None, weather_direct_filepath=None, weather_direct_data_source=None)` — `:403`. setups **19**, tests **25**, hisim **0**. Of the 19: **7** pass `weather_direct_filepath` (`household_{gas,oil,pellets,wood_chips,electric_heating,district_heating,heatpump}_building_sizer.py:256/280/254/253/248/290/252` — i.e. **7 of the 8 golden setups**), **7** pass a `LocationEnum` literal (`basic_household.py:82`, `household_gas_solar_thermal.py:119`, `default_connections.py:69`, `air_conditioned_house.py:149` (SEVILLE), `basic_household_only_heating.py:77`, `dynamic_components.py:98`, `automatic_default_connections.py:79`), **5** pass a **string** from the archetype (`simple_air_conditioner_household_building_sizer.py:108`, `household_hydrogen_boiler_building_sizer.py:246`, `household_heatpump_solar_thermal_building_sizer.py:247`, `household_gas_solar_thermal_building_sizer.py:247`, `household_heatpump_car_building_sizer.py:257`; the value is `ArcheTypeConfig.weather_location: str = "AACHEN"`, `hisim/building_sizer_utils/interface_configs/archetype_config.py:160`).
   - `@preset preset_standard(name)` — `:466-468`. setups **0**, tests **0**, hisim **0** (only `energy_systems/gas_boiler_household.energy_system.yaml:15`).
   - `@constructor for_location(name, location: LocationEnum, data_source: Optional[WeatherDataSourceEnum]=None)` — `:478-480`. **0 call sites anywhere.**
4. **Presets.** Supplement: `standard` + constructor `for_location(location, *, direct_filepath=None, direct_data_source=None)`. **Shipped as `standard` + `for_location(name, location, data_source=None)` — the two direct‑file parameters the supplement specified were dropped.** **Deviation flagged, and it is load‑bearing:** without them `get_default` cannot be deleted, because 7 golden setups and `tests/test_weather.py:91,108` have no other way to point at a file (D‑18). Second deviation: `for_location` takes `LocationEnum` only, so the 5 string call sites and every YAML spelling of the argument fail (D‑19).
5. **Sized fields: none, correctly.** §6 lists `source_path` ← `location` + `data_source` "fn (path template), confidence high" — that is exactly what `for_location:508-511` already does at build time, so it needs no law. No `AUTO` field is appropriate: the Weather is a provider, like the Building.
6. **Facts provided: none today.** All four inventory §1a facts (`heating_reference_temperature_in_celsius` as a *derived* fact, `cooling_design_temperature_in_celsius`, `pv_annual_yield_in_kwh_per_kwp`, `heating_season_begin/end_day`) are **§6 future**; none is a `SizingContext` field and `WeatherConfig` has no `SIZING_CONTRIBUTIONS`. Note the TRY region §1a wants to retire is already inside the catalogue entry: `LocationEnum.BREMERHAVEN.value[1..3]` spells `weather_region_01` (`:62+`, 45 members), so a `heating_reference_temperature_in_celsius` law would key off the enum member, not a new knob.
7. **Behaviour: neutral** for the 7 enum call sites (`for_location` reproduces `get_default`'s five fields byte for byte, `:454-462` vs `:508-513`). **Not yet expressible** for the 5 string sites and the 7 direct‑file sites.
8. **Deletions.** `get_default` `:403-463` once D‑18 and D‑19 land — 44 call sites. **`SingletonDictKeyEnum.LOCATION` is a construction‑time key written here** (`:569`, in `__init__`, unconditional) with **live readers**: `postprocessing/postprocessing_main.py:921,922,1003,1004` (report region) and `tests/test_singleton_sim_repository.py:154`. It is *not* dead — deleting it means postprocessing reads the Weather component's own config instead (D‑32). The nine `WEATHER*YEARLYFORECAST` writes (`:881-919`) are the runtime half, parking lot.
9. **Flags.** `predictive_control: bool` (`:395`, `False` in both builders, read at `:724`) — A2 → `config:` override. Not a preset axis.
10. **Hazards.**
    - **`data_source: WeatherDataSourceEnum` is an enum‑typed field** (P2 R3.7) — fine as a plain field, but if it ever becomes sizable it needs `value_type=`.
    - **`location: str` is a free‑text field that is really the enum's display name** (`"Aachen"`, `"01_Bremerhaven"`), and it is what goes into the singleton and the report. Two different vocabularies for one thing (`LocationEnum.AACHEN` vs `"Aachen"`).
    - `source_path` is a *stem* — `get_default:447-450` strips `.dat`/`.csv` before storing. A `config:` override written by hand will get this wrong; the P2 `${var}` path resolver treats it as a path field (`configure.py:250-260`).
    - `get_default` is the repo's only bare `get_default` spelling (supplement's legacy inventory); it is also the one that silently accepts an unknown string and then raises only if no filepath was given (`:437-441`).

---

### `BuildingConfig` (`hisim/components/building/config.py:27`) → `Building` (`building/building.py:63`)

1. The root fact provider; 19 instantiations.
2. **status: converted (P1/P2) — complete. The only class in the repository with no legacy factory left.**
3. **Factories.** `@preset preset_standard(name)` `:86-88`; `@constructor for_tabula_code(name, building_code, number_of_apartments=None, absolute_conditioned_floor_area_in_m2=None, total_base_area_in_m2=None, building_heat_capacity_class="medium", heating_reference_temperature_in_celsius=-7.0)` `:96-98`. Combined call sites: setups **18**, tests **24**, hisim **0**. `get_default_german_single_family_home` **no longer exists** — it survives only as the docstring at `:31` (conflict 8 executed).
4. **Presets.** Supplement + conflict 8: exactly one preset `standard` plus `for_tabula_code`. **Agreed, shipped, no deviation.** `german_multi_family_home` (plan B6) **does not exist anywhere in the repository** and conflict 8's resolution forbids minting it — see the B6 section below.
5. **Sized fields: none, deliberately** — the docstring at `:33-38` states the rule ("the building is the *source* of the facts … and therefore has no sizable field of its own"). **Deviation from the supplement**, which lists eleven `AUTO` fields (`absolute_conditioned_floor_area_in_m2`, `number_of_apartments`, `max_thermal_building_demand_in_watt`, the five U‑value/area pairs). The shipped design is right and §6 agrees ("already internal to `BuildingInformation`", "high (done)"): those fields are `Optional[...] = None` and the *component* derives them from the TABULA code, which is a lookup, not a law. The one genuine future law is `heating_reference_temperature_in_celsius` ← the Weather (§1a, D‑21).
6. **Facts provided: six**, all in one `FactContribution` (`building/information.py:761-790`): `heating_load_in_watt` (= `BuildingInformation.max_thermal_building_demand_in_watt`), `number_of_apartments`, `conditioned_floor_area_in_m2` (= `scaled_conditioned_floor_area_in_m2`), `heating_reference_temperature_in_celsius`, `set_heating_temperature_in_celsius`, `set_cooling_temperature_in_celsius`. **Not provided, and needed by other batches:** `roof_area_in_m2` (group B gate B‑0, the value already exists as `BuildingInformation.roof_area_in_m2`), `number_of_residents` (a `SizingContext` field with **no writer and no reader**, `context.py:53`), and the §6 5R1C coefficients / max cooling load / roof tilt+azimuth.
7. **Behaviour: neutral** — already landed and golden‑green.
8. **Deletions: none left on the class.** **Six construction‑time `SingletonSimRepository` keys are written by `Building.build()`** (called from `__init__`, `building.py:170`; writes at `:909-931`): `THERMALTRANSMISSIONCOEFFICIENTGLAZING`, `THERMALTRANSMISSIONSURFACEINDOORAIR`, `THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM`, `THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS`, `THERMALTRANSMISSIONCOEFFICIENTVENTILLATION`, `THERMALCAPACITYENVELOPE`. **Readers verified: `controller_mpc.py:465-480` and `controller_pid.py:136-141` — both group‑B components with zero setup uses (group B's D‑16).** If D‑16 retires MPC and PID, these six become dead construction‑time keys and go with them (D‑32). The `HEATFLUX*FORECAST` trio (`:844-853`) and `HEATINGBYRESIDENTSYEARLYFORECAST` (read `:821`, written by the occupancy at `loadprofilegenerator_utsp_connector.py:1072`) are the runtime half — parking lot.
9. **Flags.** `predictive: bool`, `enable_opening_windows: bool` (`:70,73`) — A2 `config:` overrides; the sizers set `enable_opening_windows = True` post‑construction.
10. **Hazards.**
    - **Post‑construction mutation is the largest in the repo: 20 fields, not 15.** `household_heatpump_building_sizer.py:194-218` mutates `heating_reference_temperature_in_celsius`, `max_thermal_building_demand_in_watt`, `set_heating/cooling_temperature_in_celsius`, `building_code`, `total_base_area_in_m2`, `absolute_conditioned_floor_area_in_m2`, `number_of_apartments`, `enable_opening_windows` (9) plus the ten envelope U‑value/area fields (`:207-216`) plus a conditional `building_heat_capacity_class` (`:218`) = **20 unconditional + 1 conditional**, in all 11 sizers. **Deviation from `p3` §2e, which says 15.** Only 6 of the 20 are `for_tabula_code` parameters; the other 14 must become `config:` overrides, and 10 of those are `None`‑by‑default opt‑ins whose recorder diff will be all‑`None` for the golden fleet (D‑22).
    - `for_tabula_code` validates nothing: an unknown `building_code` fails later, inside `BuildingInformation`, not at build time — unlike the H₂ chain's `read_config`, and unlike `generic_heat_pump.for_device` (group A's D‑1 note).
    - `building_code: str` and `building_heat_capacity_class: str` are free‑text fields over closed vocabularies (TABULA codes; `"very light"/"light"/"medium"/"heavy"/"very heavy"`) — the capacity class is a five‑member enum written as a string and is a wire‑format freeze candidate at P5.
    - Five capex fields default to `None` in `for_tabula_code` — this is the `random_findings` "trailing capex fields need `None` defaults" pattern, already done here and the model for the rest of the sweep.

---

### `UtspLpgConnectorConfig` (`hisim/components/loadprofilegenerator_utsp_connector.py:67`) → `UtspLpgConnector` (`:200`)

1. The occupancy; 19 instantiations, and the only source of resident, DHW and car data.
2. **status: converted (P2, PR‑3) — partially; the legacy factory still carries the whole fleet.**
3. **Factories.**
   - `get_default_utsp_connector_config(component_id=None)` — `:94`. setups **19** (every household setup: `basic_household.py:76`, `basic_household_only_heating.py:70`, `basic_household_with_weather_data_request.py:102`, `default_connections.py:62`, `automatic_default_connections.py:73`, `dynamic_components.py:93`, `air_conditioned_house.py:140`, `household_gas_solar_thermal.py:112`, and all 11 sizers), tests **13**, hisim **0**.
   - `@preset preset_standard(name)` `:122-124` and `@constructor for_household(name, household, energy_intensity=EnergySaving, travel_route_set=None, transportation_device_set=None, charging_station_set=None)` `:135-137`: setups **0**, tests **2**, hisim **0**.
4. **Presets.** Supplement (Q‑P1.6): `standard` + `for_household(...)`. **Shipped as specified. No naming deviation.** One substantive gap: neither builder can select `USE_UTSP` / `USE_LOCAL_LPG` — both hard‑code `data_acquisition_mode=USE_PREDEFINED_PROFILE` (`:104`, `:170`), while **11 of the 19 setups override it to `USE_LOCAL_LPG` immediately** (`household_heatpump_building_sizer.py:227` and ten twins). Under A2 that is a legitimate `config:` override, but see hazard 1 — the flag is not cosmetic.
5. **Sized fields: none today, correctly.** §6 proposes `household` ← `number_of_apartments` ("one household reference per apartment", card. *1 → many instances*, medium) and `energy_intensity` ← building code (low). Both are outside the scalar algebra: the first is §6's own "count‑driven instance fan‑out … for the R5 template layer", the second a lookup. **Neither should get a law in B6.**
6. **Facts provided: none today.** It is the *provider* §6 wants most after the Building: `number_of_residents` (a `SizingContext` field with no writer — `context.py:53`; the component's own `self.number_of_residents` is a per‑timestep **list**, `:388`, not a scalar, so the fact needs a reduction the class must choose), DHW peak/daily demand, annual electricity demand, and **number of cars**. All §6 future.
7. **Behaviour: neutral** for `preset_standard` vs `get_default_utsp_connector_config` — the two bodies (`:100-116` vs `:167-188`) produce identical values for the default household.
8. **Deletions.** `get_default_utsp_connector_config` `:94-118`, 32 call sites. `HEATINGBYRESIDENTSYEARLYFORECAST` (`:1072`) is a runtime key read by `building/building.py:821` — parking lot, not a P4 deletion.
9. **Flags.** `profile_with_washing_machine_and_dishwasher` (`True` in both builders), `predictive_control`, `predictive` (`:76-77`) → A2 `config:`.
10. **Hazards.**
    - **`for_household` ignored its own identifier space in its own default mode — fixed in P2 on 2026-08-27** (review of #592; D-20 answered (a)). The paragraph below describes the state the survey found.
    - **~~`for_household` ignores its own identifier space in its own default mode.~~** In `USE_PREDEFINED_PROFILE` the profile actually read is `name_of_predefined_loadprofile` (`:601`), and both builders pin it to `"CHR01 Couple both at Work"` (`:105`, `:171`) regardless of the `household` argument. `for_household(name, household=Households.CHR03_Family_1_child_both_at_work)` therefore **silently simulates CHR01** — the exact "silent repair" E1/R7 forbids, on the constructor whose whole purpose is the 66‑member household catalogue (D‑20).
    - **The component mutates its own config at run time.** `:809`, `:822`, `:1004`, `:1007` reassign `self.utsp_config.data_acquisition_mode` on failure (UTSP → LOCAL_LPG → PREDEFINED). A realized record written after the run therefore does not describe the run that produced it, breaking UC5/EAC2 for any UTSP setup.
    - **`household: Union[JsonReference, List[JsonReference]]`** (`:72`) — a scalar‑or‑list union field. The 11 sizers build the list in a loop (`household_heatpump_building_sizer.py:180-188`) and assign it post‑construction. The codec's `_decode_scalar`/`_decode_enum` (`codec.py:287-380`) have no union‑of‑`JsonReference` path; the `from_dict` route works because `dataclasses_json` handles it, which means the two decode paths disagree.
    - **`JsonReference` spelling (C‑P2.6)** is verified *for this class* at config level (`p2_implementation_spec.md:498`, `tests/test_energy_system_utsp.py:84,111`) — writer → YAML → codec → `from_dict` round‑trips `Name`/`Guid`/`StrVal` for all four references. The defect survives in the 22 v1 `system_setups/*.scenario.json` and `json_executor.py:171-177`, which pascalize four keys by hand. I re‑verified the same round trip for group C's *other* `JsonReference` field, `ChargingStationConfig.charging_station_set`: `to_dict` emits `{'Name': 'Charging At Home with 11 kW', 'Guid': {'StrVal': '78dae308-…'}}` and `from_dict` restores it — **no second defect**.
    - `result_dir_path`, `cache_dir_path`, `predefined_loadprofile_filepaths` are path fields (P2 `${var}`); `cache_dir_path` is mutated post‑construction in all 11 sizers (`:229`) and is machine‑specific — it must not enter a checked‑in record.
    - `energy_intensity: EnergyIntensityType` and `data_acquisition_mode: LpgDataAcquisitionMode` are enum‑typed (R3.7).

---

### `WeatherDataImport` (`hisim/components/weather_data_import.py:36`)

1. A DWD network fetcher that writes a CSV into the inputs directory; **not a `Component`, not a `ConfigBase`, no config class at all** — every parameter is a plain `__init__` argument.
2. **status: exempt** (§6 "data reader, no component config" — confirmed).
3. **Factories: none.** Instantiated directly in **2 setups** (`simple_weather_data_import.py:65`, `basic_household_with_weather_data_request.py:61`); its `.csv_path` and `.weather_data_source` are then fed into `WeatherConfig(source_path=…, data_source=…)` at `basic_household_with_weather_data_request.py:108-114` (`p3` §2g). tests **1 file, fully commented out** (`tests/test_weather_data_import.py:7-24`) plus an AST‑only test (`tests/test_system_setups_simple_weather_data_import.py`) that never imports it.
4. **Presets: none. Exempt.** Agreed with the supplement (it is not in the supplement's tables at all).
5–7. No sized fields, no facts, no behaviour change.
8. **Deletions: none proposed** — but see D13: **`import hisim.components.weather_data_import` fails** (`ModuleNotFoundError: No module named 'wetterdienst'`); the dependency is **commented out** in `requirements.txt:28` ("TODO: migrate weather_data_import.py to new API before upgrading"). It is the only import failure in group C.
9. **Flags:** none.
10. **Hazards.** A pre‑simulation network fetch that a v3 file cannot express at all: the energy‑system executor builds only `component_class(my_simulation_parameters=…, config=…)` (`hisim/energy_system/wiring.py:143-145`). `basic_household_with_weather_data_request.py` is therefore **not recordable** as a v3 file without a `${var}` path pointing at an already‑fetched CSV.

---

### `SmartDeviceConfig` (`hisim/components/generic_smart_device.py:42`) → `SmartDevice` (`:131`)

1. A shiftable LPG appliance (washing machine / dishwasher) driven by an LPG flexibility report.
2. **status: delete — dead *and* defective (new D13 member).**
3. `get_default_config(component_id=None)` — `:56`. setups **0**, tests **0**, hisim **0** outside its own module.
4. **Presets.** Supplement: `standard`, with the note "`identifier="Identifier"` placeholder — the preset ships a meaningless value". **Deviation: propose no preset.** The class cannot be constructed at all (below), so minting a name that E8 freezes at P5 for unreachable code is the worst of both options.
5. **Sized fields: none.** §6 lists `smart_devices_included` ← occupancy's `profile_with_washing_machine_and_dishwasher` (copy, low confidence) — a fact with no provider and no consumer.
6. **Facts provided:** none.
7. **Behaviour: n/a.**
8. **Deletions.** The module. No singleton keys.
9. **Flags.** `smart_devices_included: bool` gates the component's participation together with `my_simulation_parameters.surplus_control` (`:180`).
10. **Hazards / D13.** **`SmartDevice.__init__` raises `KeyError: 'utsp_reports'` unconditionally** — `build()` reads `path.join(utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json")` (`:291`) and `HISIMPATH` has no such key (it has `utsp_results`, `utsp_example_results`, `utsp_example_reports`, `report`). Verified with `local_python_env/bin/python`. This is a **new D13 member not in `plan.md:72`'s enumeration** and the only *component* in group C that cannot be constructed at all. Even if the key were fixed, `identifier="Identifier"` matches no device (`:314`), so the preset would build an appliance with an empty profile.

---

## C‑b — Mobility

### `CarConfig` (`hisim/components/generic_car.py:147`) → `Car` (`:230`), with `GenericCarInformation` (`:42`)

1. A constant‑consumption vehicle whose driving profile comes from the LPG.
2. **status: to convert — but the *component* is not buildable by the executor (D‑23).**
3. **Factories.**
   - `get_default_diesel_config(name="Car", component_id=None)` — `:174`, returns `Any`. setups **0**, tests **0**, hisim **0**.
   - `get_default_ev_config(component_id=None)` — `:196`, returns `Any`. setups **1** (`household_heatpump_car_building_sizer.py:396`), tests **0**, hisim **0**.
   - `GenericCarInformation(my_occupancy_instance)` — `:42`; setups **1** (`household_heatpump_car_building_sizer.py:395`).
4. **Presets.** Supplement: `diesel`, `electric`. **Agreed, no deviation** — a clean rule‑1 fit, the two factories differ in `fuel`, `consumption_per_km`, and the three cost/CO₂ numbers, and `electric` beats `ev` (rule 1). Note `diesel` has **zero** call sites, so it is minted for symmetry only.
5. **Sized fields: none, and none should be added in B7.** §6 lists two candidates, both out of scope: `consumption_per_km` ← fuel + a *new* vehicle class (lookup, low confidence), and "number of car instances / `source_weight`" ← number of cars from occupancy — which §6 itself classifies as "count‑driven instance fan‑out … a question for the R5 template layer", not sizing arithmetic.
   **The car setup's data‑dependent loop** (`p3` §2c) is `household_heatpump_car_building_sizer.py:395-431`: `GenericCarInformation(my_occupancy_instance=my_occupancy)` at `:395`, `for idx, car_information_dict in enumerate(my_car_information.data_dict_for_car_component.values())` at `:402` → one `Car` per entry with `ComponentID(car_information_dict["car_name"] + f"_{idx}")` at `:403`, then `for car in my_cars:` at `:417` → one `CarBattery` (`:418-420`) and one `L1Controller` (`:427-431`) each, plus four more `zip(...)` loops at `:446`, `:482`, `:556`, `:568`.
   **Answer to the assignment's question: no, none of the three configs is derivable from occupancy *facts*; they must stay constructor/loop‑built.** `GenericCarInformation.build` reads `my_occupancy_instance.car_data_dict`, which `UtspLpgConnector.build()` fills **only** in `USE_UTSP`/`USE_LOCAL_LPG` mode (`:901-903`, `:958-960`); in `USE_PREDEFINED_PROFILE` it stays empty and `generic_car.py:63-72` raises `ValueError("The car data from occupancy contains only empty dictionaries…")`. So the *count* of cars is a live LPG result, not a build‑time fact, and the per‑car *payload* (`car_location`, `driven_meters`, `time_resolution`) is a time series. Neither can enter `SizingContext`.
6. **Facts provided:** none. §6 wants "car battery capacity and charger power — provider `CarConfig`" for `CarBatteryConfig`, but `CarConfig` has **no capacity field**; the fact would have to be invented first.
7. **Behaviour: neutral** — `preset_electric` reproduces `get_default_ev_config`'s nine values exactly. The setup is smoke‑gated only (`tests/test_system_setups_households_for_building_sizer.py:277-279`, `@pytest.mark.system_setups`) and is **not in the 8‑setup golden fleet** — any change here is numerically unwitnessed.
8. **Deletions.** Both factories; give the presets a real return type instead of `Any` (`:177`, `:199`).
9. **Flags:** none. `fuel: lt.LoadTypes` is the preset axis, not a flag.
10. **Hazards.**
    - **`Car.__init__` takes a third mandatory argument**, `data_dict_with_car_information: Dict` (`:242`). The executor calls `component_class(my_simulation_parameters=…, config=…)` and nothing else (`wiring.py:143-145`), so **`Car` is the only component in group C that a v3 file cannot construct** (verified by signature sweep over all 26 modules; `CSVLoader`'s two extras are optional). Converting `CarConfig` to presets does not make the car expressible.
    - **Config aliasing in the loop.** `my_car_config` is built **once** at `:396` and mutated **inside** the loop (`:403`), then handed to every `Car`; all cars share one `CarConfig` instance whose `component_id` ends as the last car's. Component *names* differ because `get_component_name()` (`hisim/component.py:269`) is evaluated during `super().__init__`, but every `car.config` afterwards reports the last car's identity — which is exactly what a recorder would write. Latent today only because CHR01 + `Bus_and_one_30_km_h_Car` yields one car.
    - **`source_weight = 1` for every car** (`:143`, `:203`) and copied onto the battery and controller at `:419`, `:429`; two cars would collide in the EMS dispatch order.
    - **`fuel` shapes the I/O surface**: `Car.__init__:257-278` adds `ElectricityOutput`+`CarLocation` for electricity and `FuelConsumption` for diesel. A `config:` override of `fuel` changes the component's ports, same pattern as group A's `with_domestic_hot_water_preparation`.
    - `L1Controller.get_default_connections_from_generic_car` (`controller_l1_generic_ev_charge.py:217`) binds by `Car.get_classname()` — with N>1 cars a *bare* input item is ambiguous under E2 and every wire must be explicit.

### `CarBatteryConfig` (`hisim/components/advanced_ev_battery_bslib.py:41`) → `CarBattery` (`:83`)

1. The EV traction battery, bslib‑backed.
2. **status: to convert.**
3. `get_default_config(component_id=None, name="CarBattery")` — `:65`. setups **1** (`household_heatpump_car_building_sizer.py:418`), tests **7**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed** (rule 5 clean; no technology name distinguishes anything, so A3 does not apply — unlike group B's `BatteryConfig`, this class has exactly one factory and no rating twin, so A1 never fires either).
5. **Sized fields: none today; none proposable.** §6's "car battery capacity and charger power ← `CarConfig` / `ChargingStationConfig`" has no provider field on either class (see above). `e_bat_custom=30` kWh and `p_inv_custom=1e4` W (`:75-76`) stay plain defaults.
6. **Facts provided:** none. (Group B's stationary `BatteryConfig` is the one §6 wants as an MPC provider; this class is not it.)
7. **Behaviour: neutral.**
8. **Deletions.** The one factory. No singleton keys.
9. **Flags:** none.
10. **Hazards.** `system_id: str = "SG1"` (`:74`) is a bslib database key written as free text — the same "identifier inside a string field" pattern as `WindturbineConfig.turbine_type` (group B) and `GenericHeatPumpConfig.heat_pump_name` (group A), but here the supplement proposes **no** constructor. `source_weight` and `component_id` are both assigned post‑construction (`:419-420`). `total_charged_energy_in_kilowatthour` / `total_discharged_energy_in_kilowatthour` (`:55,57`) are **run‑time accumulators sitting in the config** — they will be written into every realized record as `0` and are the small‑scale version of group B's `MpcControllerConfig` "31 of 49 fields are runtime buffers".

### `ChargingStationConfig` (`hisim/components/controller_l1_generic_ev_charge.py:39`) → `L1Controller` (`:107`)

1. Home charger and the car‑battery charge controller.
2. **status: to convert (constructor‑shaped).**
3. `get_default_config(charging_station_set: JsonReference = ChargingStationSets.Charging_At_Home_with_03_7_kW, component_id=None)` — `:68`, a **`@staticmethod`**, not a classmethod. setups **1** (`household_heatpump_car_building_sizer.py:427`, passing `Charging_At_Home_with_11_kW`), tests **0**, hisim **0**.
4. **Presets.** Supplement + conflict 7: `standard` + `for_charging_station_set(charging_station_set: JsonReference)`. **Agreed in shape, one deviation flagged:** the factory's own default is **3.7 kW** while the only caller passes **11 kW**, so `preset_standard` would ship a rating nobody uses and the single real call site would immediately override it (D‑24). Note `ChargingStationSets` has 10 members, so a constructor is right regardless.
5. **Sized fields: `lower_threshold_charging_power_in_watt`.** Math, quoted from `controller_l1_generic_ev_charge.py:78-82`:
   ```
   charging_power_in_kilowatt = float((charging_station_set.Name or "").split("with ")[1].split(" kW")[0])
   lower_threshold_charging_power_in_watt = charging_power_in_kilowatt * 1e3 * 0.1
   ```
   Facts read: **none** — the input is the class's own sibling field, so this is a `Self("charging_station_set")` **constant law with a ×0.1 scale**, entirely factory‑side today, exactly conflict 7's "the rating *is* the identifier". §6 grades it "×k, card. 1, confidence low". It is **not** a `SizingContext` reader and needs no new fact.
6. **Facts provided:** none. §6 would like `charging_station_set` ← car battery capacity (lookup, medium) — no provider.
7. **Behaviour: neutral** if `for_charging_station_set` keeps the same parse; **not neutral** if `standard` pins 3.7 kW and the setup is switched to it (D‑24).
8. **Deletions.** The one factory.
9. **Flags:** none. `battery_set_soc` is a comfort number, flipped 0.4/1.0 post‑construction by the `car_surplus_charging` fork (`household_heatpump_car_building_sizer.py:518-521`) — §6 calls it "user comfort choice", so it stays a plain field a file sets.
10. **Hazards.**
    - **String‑parsing an identifier.** `.split("with ")[1].split(" kW")[0]` raises `IndexError` for any set whose `Name` does not contain `"with … kW"`; nothing validates the 10 members against that shape. A `for_charging_station_set` constructor is the place to make the failure precise.
    - `charging_station_set: JsonReference` round‑trips correctly through `to_dict`/`from_dict` with `Name`/`Guid`/`StrVal` (verified) — no second C‑P2.6 defect here.
    - `@staticmethod` (not `@classmethod`) — the `@preset`/`@constructor` decorators require the `(cls, name, …)` shape (`presets.py:_check_signature`), so the conversion is also a signature change.
    - `electricity_meter.get_default_connections_from_electric_car` (`electricity_meter.py:509-529`) hard‑codes `source_weight=999`, which is the *uncontrolled* branch only; the setup's EMS‑controlled branch uses weight 5 and a `connect_dynamic_input` (`household_heatpump_car_building_sizer.py:483-521`). A bare `inputs` item would silently pick the uncontrolled wiring.

---

## C‑c — H₂, electrolyzer, fuel cell and RSOC chain

The chain splits cleanly into **three self‑defaulting classes**, **two duplicate pairs**, and **six classes whose only builder reads a JSON file that is not in the repository**.

### `ElectrolyzerConfig` (`hisim/components/generic_electrolyzer_h2.py:32`) → `Electrolyzer` (`:118`)

1. The manufacturer‑table electrolyzer used by `electrolyzer_with_renewables.py`.
2. **status: to convert (constructor‑shaped).**
3. `get_default_alkaline_electrolyzer_config(component_id=None)` `:52` (returns `Any`) — setups **0**, tests **1**, hisim **0**. `read_config(electrolyzer_name)` `:74` (static). `config_electrolyzer(electrolyzer_name, component_id=None)` `:93` — setups **1** (`electrolyzer_with_renewables.py:119`, `electrolyzer_name = "HTecME450"` at `:73`), tests **0**.
4. **Presets.** Supplement: `alkaline` + constructor. **Agreed on the constructor, deviation on the name:** `get_default_alkaline_electrolyzer_config` is a *hand‑typed* default (100 kW nominal, 100 kg/h, `faraday_eff=1.0`, `:59-67`) that matches **no** entry of `electrolyzer_manufacturer_config.json` — whose nine members are `KumaTecPEM40100`, `McPhyMcLyzer20030`, `McPhy - McLyzer 3200`, `HTecME450`, `NEL - MC250`, `NEL - MC500`, `KYROS50`, `SunfireHYLINKSOEC`, `FuelCellEnergySOEC` (three of them PEM/SOEC, not alkaline). Naming it `alkaline` implies a technology the numbers do not come from. Propose **`standard`** + `for_device(electrolyzer_name)`.
5. **Sized fields: none.** Every field comes from the table.
6. **Facts provided:** none. §6 wants "electrolyzer max H₂ production rate" for the H₂ storage — the field exists (`nom_h2_flow_rate`, `:41`) but no consumer declares a law.
7. **Behaviour: neutral** — `for_device` is `config_electrolyzer` renamed.
8. **Deletions.** Merge `read_config` into the constructor.
9. **Flags:** none.
10. **Hazards.** `electrolyzer_type: str` (`:36`) is free text over `{"PEM","Alkaline","SOEC"}` — an enum before P5. **This class's `read_config` is the *only* one of the nine that fails loudly** (`:79-82`, `KeyError: "The electrolyzer … could not be found …"`, verified) — see D‑26.

### `ElectrolyzerControllerConfig` (`hisim/components/controller_l1_electrolyzer_h2.py:31`) → `ElectrolyzerController` (`:103`)

1. Its controller, same table.
2. **status: to convert (constructor‑shaped).**
3. `get_default_electrolyzer_controller_config(component_id=None)` `:48` — setups **0**, tests **1**. `read_config(electrolyzer_name)` `:67`. `control_electrolyzer(electrolyzer_name, component_id=None)` `:79` — setups **1** (`electrolyzer_with_renewables.py:113`).
4. **Presets.** Supplement: `alkaline` + constructor. **Same deviation — propose `standard` + `for_device(electrolyzer_name)`** (the `control_*` spelling is one of only two in the repo and must go).
5. **Sized fields: none today.** The natural law — copy `nom_load`/`min_load`/`max_load` from the electrolyzer, exactly the generator→controller pattern group A relies on — needs three new facts and is **not** worth minting in B7 for a chain with one setup.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** `read_config` folds into the constructor.
9. **Flags:** none.
10. **Hazards.** **Silent zero‑fill.** `read_config:71-73` returns `{}` for an unknown name and `control_electrolyzer:88-96` then builds a config of **all zeros** with a `log.information` line, whereas `generic_electrolyzer_h2.read_config` raises for the same input. Two readers of one file, two error contracts (verified: `ElectrolyzerControllerConfig.read_config("Alkaline Electrolyzer")` returns `{}`; `ElectrolyzerConfig.read_config("Alkaline Electrolyzer")` raises). D‑26.

### `PTXControllerConfig` (`hisim/components/controller_l2_ptx_energy_management_system.py:28`) → `PTXController` (`:80`)

1. The power‑to‑X EMS over the same electrolyzer table.
2. **status: to convert (constructor‑shaped).**
3. `read_config(electrolyzer_name)` `:44`; `control_electrolyzer(electrolyzer_name, operation_mode, component_id=None)` `:52`. setups **0**, tests **4**, hisim **0**. **No default factory at all.**
4. **Presets.** Supplement: `standard` + `for_device(electrolyzer_name, operation_mode)`. **Deviation: propose constructor‑only, no preset** — conflict 9's precedent (`CSVLoaderConfig`) applies exactly: with no default factory, a `standard` preset would have to invent four load numbers. The supplement's own conflict 9 already asks the contract test to permit "constructor, no preset".
5. **Sized fields: none.**
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** `read_config` folds in.
9. **Flags:** none.
10. **Hazards.** `operation_mode: str` (`:41`) is free text over a closed set the code branches on — `"NominalLoad"`, `"MinimumLoad"`, `"StandbyLoad"`, `"StandbyandOffLoad"` (`:178-205`), with no `else` (an unknown string silently does nothing). Must be an enum before it is wire format (D‑27). Same silent `{}` zero‑fill as above (`:48-50`).

### `FuelCellConfig` (`generic_fuel_cell.py:38`), `FuelCellControllerConfig` (`controller_l1_fuel_cell.py:28`), `XTPControllerConfig` (`controller_l2_xtp_fuel_cell_ems.py:31`)

1. The PEM fuel cell, its L1 controller, and the X‑to‑power EMS.
2. **status: to convert (constructor‑shaped) — but their constructors cannot run (D‑25).**
3. `FuelCellConfig.get_default_pem_fuel_cell_config` `:64` (setups 0, tests 1), `read_config` `:86`, `config_fuel_cell` `:94` (setups 0, tests 0). `FuelCellControllerConfig.get_default_fuel_cell_controller_config` `:47` (setups 0, tests 1), `read_config` `:66`, `control_fuel_cell` `:75`. `XTPControllerConfig.read_config` `:47`, `control_fuel_cell` `:55` (setups 0, tests 2) — **no default factory**.
4. **Presets.** Supplement: `pem` + `for_device` for the first two, `standard` + `for_device` for XTP. **Agreed for the first two** (`pem` is a real technology name and both hand‑typed defaults set `type="PEM"`, `:71`). **Deviation for XTP: constructor‑only, no preset** (same reasoning as PTX).
5. **Sized fields: none.**
6. **Facts provided:** none. §6's "fuel cell H₂ consumption rate → H₂ storage" has no consumer law.
7. **Behaviour: neutral for the two hand‑typed defaults; the table path is unreachable.**
8. **Deletions.** Three `read_config` readers fold into `for_device`.
9. **Flags:** none. `XTPControllerConfig.operation_mode: str` — see D‑27 (`"StandbyLoad"`, `"StandbyandOffLoad"`, `controller_l2_xtp_fuel_cell_ems.py:163,178`).
10. **Hazards / D13.** **`hisim/inputs/fuel_cell_manufacturer_config.json` does not exist in this repository.** All six calls fail with `FileNotFoundError: … /hisim/inputs/fuel_cell_manufacturer_config.json` (verified). `hisim/inputs/` ships only `electrolyzer_manufacturer_config.json`, `electrolyzer_polarization_curve_data.json`, `polarization_curve_data_fc.json`, `rSOC_efficiency_curve_data.json`. `type: str` on `FuelCellConfig` is free text over the same PEM/SOFC space.

### `RsocConfig` (`generic_rsoc.py:31`), `RsocControllerConfig` (`controller_l1_rsoc.py:31`), `RsocBatteryControllerConfig` (`controller_l2_rsoc_battery_system.py:29`)

1. The reversible solid‑oxide cell, its L1 controller and its L2 battery‑coupled controller.
2. **status: to convert (constructor‑only) — no class has a buildable factory today (D‑25).**
3. `RsocConfig.read_config` `:56`, `from_rsoc_name(rsoc_name, component_id=None)` `:65` — setups 0, tests 4. `RsocControllerConfig.read_config(rsoc_name, path=None)` `:56`, `config_rsoc(rsoc_name, component_id=None, config_json=None)` `:77` — setups 0, tests 7. `RsocBatteryControllerConfig.read_config(rsoc_name, config_path=None)` `:54`, `config_rsoc(rsoc_name, operation_mode, component_id=None, config_data=None)` `:78` — setups 0, tests 10. **None of the three has a default factory.**
4. **Presets.** Supplement: `standard` + `for_device` for all three. **Deviation: constructor‑only, no preset, for all three** — conflict 9 again; there is no default to name. `RsocConfig.from_rsoc_name` already satisfies `ConfigBuilder.CONSTRUCTOR_PREFIXES = ("for_", "from_")` (`presets.py:109`) and can be decorated **in place** without a rename, which is cheaper than the supplement's `for_device` and reads better; flag it as a deliberate deviation.
5. **Sized fields: none.**
6. **Facts provided:** none.
7. **Behaviour: neutral** — the tests already inject `config_json`/`config_data`/`path`, so nothing depends on the missing file.
8. **Deletions.** Three `read_config` readers.
9. **Flags:** none. `RsocBatteryControllerConfig.operation_mode: str` — D‑27 (`"NominalLoad"`, `"MinimumLoad"`, … `:227-232`).
10. **Hazards / D13.** **`hisim/inputs/rSOC_manufacturer_config.json` does not exist.** All six disk‑reading calls raise `FileNotFoundError` (verified); the three classes are therefore **only** constructible from an in‑memory dict the tests fabricate. Additionally `RsocBatteryControllerConfig` is the repo's only config with **`dataclasses_json` field aliases** (`:43-49`): the seven power fields serialize as `nom_load_soec_in_kW` etc. while their Python names are `…_in_kw`. `from_dict` accepts both spellings (verified), but `to_dict` emits only the mixedCase one while the P2 codec (`codec.py:57`) and the record writer (`record.py:136`) both key off `dataclasses.fields(...)` — one field, two wire spellings, and the two serialization paths disagree.

### `GenericElectrolyzerConfig` (`generic_electrolyzer.py:31`) → `GenericElectrolyzer` (`:88`)

1. The *other*, simpler electrolyzer, sized from a rated power.
2. **status: to convert.**
3. `get_default_config(p_el: float, component_id=None)` — `:47`, a `@staticmethod` with a **mandatory positional argument**. setups **0**, tests **1**, hisim **0**.
4. **Presets.** Supplement: `standard`, AUTO fields `min_power`, `max_power`. **Deviation on both counts.** There is no `p_el` field — it is a factory argument — so `max_power` cannot be `AUTO`; it must become the plain rated field, and the other three fields become `Self("max_power")` laws. Propose **`standard`** with `max_power` a plain default and three sized siblings.
5. **Sized fields (proposed, all `Self("max_power")`, no `SizingContext` fact read).** Math quoted from `generic_electrolyzer.py:70-73`:
   ```
   min_power=p_el * 0.5,
   max_power=p_el,
   min_hydrogen_production_rate=p_el * (1 / 4) * 8.989 / 3.6e4,
   max_hydrogen_production_rate=p_el * (50 / 24) * 8.989 / 3.6e4,
   ```
   All four factory‑side today; all four are **constant‑scale laws** over one sibling field, well inside the algebra (C5). Note the supplement omits the two production rates entirely.
6. **Facts provided:** none today; `max_hydrogen_production_rate` is §6's candidate for the H₂ storage.
7. **Behaviour: neutral** — one test call site, no setups.
8. **Deletions.** The factory (and the `@staticmethod`→`@classmethod` change the decorator needs).
9. **Flags:** none.
10. **Hazards / D13.** **This is a genuine "cannot be built from its own defaults" case**: `GenericElectrolyzerConfig.get_default_config()` raises `TypeError: missing 1 required positional argument: 'p_el'` (verified). It is the only such factory in group C and belongs on the D13 list. It is also a near‑duplicate of `ElectrolyzerWithStorageConfig` below.

### `L1ElectrolyzerControllerConfig` (`controller_l1_electrolyzer.py:22`) → `L1GenericElectrolyzerController` (`:98`)

1. Its controller.
2. **status: to convert.**
3. `get_default_config(component_id=None)` — `:38`, a `@staticmethod`. setups **0**, tests **2**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed** (rule 5 clean).
5. **Sized fields: none.** `p_min_electrolyzer=1200` (`:50`) duplicates `GenericElectrolyzerConfig.min_power` for the default `p_el=2400`; a copy law would need the electrolyzer to declare a fact. Leave plain.
6. **Facts provided:** none. 7. **Behaviour: neutral.** 8. **Deletions:** the factory.
9. **Flags:** none.
10. **Hazards.** `h2_soc_threshold=96` (`:51`) is a percentage in a field with no unit suffix, against the repo's unit‑explicit convention — rename before it is wire format. `@staticmethod` again.

### `ElectrolyzerWithStorageConfig` and `ElectrolyzerWithHydrogenStorageConfig` (`generic_electrolyzer_and_h2_storage.py:31`, `:73`) → `AdvancedElectrolyzer` (`:254`), `HydrogenStorage` (`:622`)

1. A second, older electrolyzer + H₂ storage pair in one module.
2. **status: delete (duplicates) — or convert to `standard` each if the owner keeps them (D‑29).**
3. Both have `get_default_config(component_id=None)` — `:50` and `:92`, **the same method name twice in one module**, both returning `Any`. setups **0** each, tests **3** each, hisim **0**.
4. **Presets.** Supplement: `standard` for both. **Agreed if they survive**; the duplicate‑factory‑name note in the supplement is harmless because presets are class‑scoped.
5. **Sized fields: none.** 6. **Facts provided:** none. 7. **Behaviour: neutral.** 8. **Deletions:** the two factories, or the whole module.
9. **Flags:** none.
10. **Hazards.** `ElectrolyzerWithStorageConfig` (waste energy, min/max power, min/max power percent, min/max H₂ rate per hour, output pressure) and `ElectrolyzerWithHydrogenStorageConfig` (min/max capacity, starting fill, charge/discharge rates, charge/discharge energy, loss factor) are field‑for‑field the same *models* as `GenericElectrolyzerConfig` and `GenericHydrogenStorageConfig`, in different units (`Nl/h` vs `kg/s`, `kWh/kg` vs `Wh/kg`) — four classes for two devices, and a v3 file would offer both with no way to tell which is meant.

### `GenericHydrogenStorageConfig` (`generic_hydrogen_storage.py:29`) → `GenericHydrogenStorage` (`:104`)

1. The H₂ storage the `generic_chp`/`generic_electrolyzer` pair connects to.
2. **status: to convert.**
3. `get_default_config(capacity=200, max_charging_rate=2/3600, max_discharging_rate=2/3600, source_weight=1, component_id=None)` — `:51`, a `@staticmethod`. setups **0**, tests **6**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: none today.** The supplement lists an AUTO field **`max_capacity`** — **that field does not exist**; the field is `max_capacity_in_kg` (`:37`). **Deviation flagged (a name error in the supplement).** §6 has no law for it either ("electrolyzer max H₂ production rate → H₂ storage capacity" is a *new fact* with no provider), so it stays a plain default: the supplement's own note ("all four params defaulted in the factory (`capacity=200`) — numbers stay in the law, not the name") argues for a plain field, not `AUTO`.
6. **Facts provided:** none.
7. **Behaviour: neutral.**
8. **Deletions.** The factory. Note `2/3600` must be preserved as the float `0.000555…`, an int→float literal drift the plan already warns about.
9. **Flags:** none.
10. **Hazards.** `min_capacity`, `max_charging_rate`, `max_discharging_rate`, `energy_for_charge`, `energy_for_discharge`, `loss_factor_per_day` carry **no unit suffix** (kg, kg/s, Wh/kg, %/day — documented only in comments `:32-48`), against the repo convention and about to become wire format. Its two default‑connection methods bind by class name (`:184` from `generic_chp`, `:203` from `generic_electrolyzer`), so a system with both electrolyzers is ambiguous.

### `CSVLoaderConfig` (`csvloader.py:31`) → `CSVLoader` (`:65`)

1. A load profile read from a CSV; used by the electrolyzer setup.
2. **status: to convert (constructor‑only, no preset).**
3. **No factory at all.** Constructed literally at `electrolyzer_with_renewables.py:86-97` (10 keyword arguments); `CSVLoader.from_config_file(config, my_simulation_parameters, my_display_config, inputs_dir=None)` `:258` is an I/O helper, not a config factory. setups **1** (config) + 1 (`CSVLoader(...)` at `:100`), tests **4**, hisim **0**.
4. **Presets.** Supplement conflict 9: **no preset**, constructor `for_csv_file(csv_filename, column, loadtype, unit, *, sep=";", decimal=".", multiplier=1.0)`. **Agreed on "no preset"; deviation on the signature** — the class has **ten** mandatory fields, and the supplement's signature omits two of them: `column_name: str` and `output_description: str` (`:53,57`). Both must be parameters or get defaults.
5. **Sized fields: none.** §6 proposes `multiplier` ← floor area or apartments ("scale a reference profile", confidence low) — speculative, no call site scales anything (`electrolyzer_with_renewables.py:96` passes a literal). Leave plain.
6. **Facts provided:** none. 7. **Behaviour: neutral.**
8. **Deletions:** none (nothing to delete); the constructor is added.
9. **Flags:** none.
10. **Hazards.** `csv_filename` is resolved against `HISIMPATH["inputs"]` (`:252`, `from_config_file:288`), so it is a *relative* path field — the P2 `${var}` resolver treats any field ending in a path noun as absolute (`configure.py:250-268`); `csv_filename` does not end in one, so it escapes the resolver by luck. `loadtype`/`unit` are enum‑typed (R3.7). `CSVLoader.__init__` takes two optional extras (`inputs_dir`, `dataframe`) the executor never passes — harmless, since both default.

---

## C‑d — Examples and templates

### `ExampleComponentConfig` (`example_component.py:28`) → `ExampleComponent` (`:63`)

1. The template every new component author copies (`CLAUDE.md` names it).
2. **status: to convert — and it is the natural place to demonstrate a law (D‑31).**
3. `get_default_example_component(component_id=None)` — `:45`. setups **0**, tests **11**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5. **Sized fields: one, hiding in plain sight.** `capacity=45 * 121.2` (`example_component.py:58`, repeated as a fallback at `:174`) — **121.2 m² is the reference building's `absolute_conditioned_floor_area_in_m2`** (`building/config.py:92`), so the literal is `45 J/K/m² × conditioned_floor_area_in_m2`. Fact read: `conditioned_floor_area_in_m2`, which **exists** (`context.py:50`, contributed by the Building). Math: **×k scale**, `Size.CONDITIONED_FLOOR_AREA_IN_M2 * 45`. Factory‑side today. Making it a law is golden‑neutral (0 setups) and turns the teaching example into a real one.
6. **Facts provided:** none. 7. **Behaviour: neutral** for the 11 tests, all of which build the default 121.2 m² building.
8. **Deletions:** the factory, and the duplicated `45 * 121.2` fallback at `:173-176`.
9. **Flags:** none.
10. **Hazards.** `electricity`, `capacity`, `initial_temperature` are `Optional[float]` with an `if None` fallback in `build` (`:173-176`) — the "two defaults, one in the config and one in the component" pattern `sized_field(default=…)` exists to remove. `loadtype=lt.LoadTypes.HEATING` with `unit=lt.Units.WATT` on a class whose only stored quantity is a heat capacity is a mismatch a reader will copy.

### `ComponentNameConfig` (`example_template.py:35`) → `ComponentName` (`:62`)

1. The literal copy‑me template.
2. **status: to convert — highest documentation leverage in the whole sweep (B8).**
3. `get_default_template_component(component_id=None)` — `:48`. setups **0**, tests **5**, hisim **0**.
4. **Presets.** Supplement: `standard`, with the note "the preset name is copied by every new component author — worth getting right". **Agreed.**
5. **Sized fields: none today.** The template has only `loadtype` and `unit`. **Recommendation (D‑31): the template should grow one `sized_field` and one `SIZING_CONTRIBUTIONS` entry**, because EAC4 ("a new component appears in an energy system with no change outside its module") is only credible if the template shows the pattern.
6–9. Nothing provided, neutral, factory deleted, no flags.
10. **Hazards.** None beyond enum‑typed `loadtype`/`unit`.

### `ExampleTransformerConfig` (`example_transformer.py:27`) → `ExampleTransformer` (`:63`)

1. Example only. 2. **status: to convert.**
3. `get_default_transformer(component_id=None)` — `:40`. setups **1** (`simple_system_setup_two.py:67`), tests **1**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.**
5–9. No sized fields, no facts, neutral, factory deleted, no flags.
10. **Hazards.** `LoadTypes.ANY` / `Units.ANY` — a config that types nothing; harmless for an example.

### `SimpleStorageConfig` (`example_storage.py:65`) → `SimpleStorage` (`:94`)

1. Example only. 2. **status: to convert.**
3. `get_default_thermal_storage(component_id=None)` — `:79`. setups **0**, tests **2**, hisim **0**.
4. **Presets.** Supplement: `thermal` ("only factory names a medium; `thermal` keeps rule 1"). **Agreed** — and it is the only example class where rule 1 has anything to bite on.
5–9. No sized fields (`capacity=50` kWh is a round example number), no facts, neutral, factory deleted, no flags.
10. **Hazards.** `capacity: float` with no unit suffix while the sibling `unit` field says `KWH`.

### `SimpleControllerConfig` (`controller_l1_example_controller.py:34`) → `SimpleController` (`:61`)

1. Example only; the config has **exactly one field**, `component_id`.
2. **status: to convert.**
3. `get_default_config(component_id=None)` — `:50`. setups **0**, tests **12**, hisim **0**.
4. **Presets.** Supplement: `standard`. **Agreed.** This is the minimal legal case — a preset that sets nothing but the name — and worth keeping as the contract test's degenerate fixture.
5–10. Nothing else. Its docstring documents a `name` attribute the class does not have (`:38`).

---

## C‑e — Already converted (one line each)

| Class (module) | State |
|---|---|
| `WeatherConfig` (`weather.py:384`) | **done (P2), partially** — `preset_standard` `:466`, `for_location` `:478`, no sized fields (correct: it is a provider). **`get_default` `:403` is still live at 19 setups + 25 tests while the two new builders have 0 call sites outside `energy_systems/gas_boiler_household.energy_system.yaml`.** Deleting it is blocked twice over: `for_location` dropped the supplement's `direct_filepath`/`direct_data_source` parameters that 7 of the 8 golden setups need (D‑18), and it takes `LocationEnum` only while 5 setups pass a string and the executor passes constructor arguments undecoded (D‑19). Contributes **no** facts; all four §1a climate facts are §6 future. |
| `BuildingConfig` (`building/config.py:27`) | **done (P1/P2), complete** — `preset_standard` `:86`, `for_tabula_code` `:96`, **no legacy factory left** (`get_default_german_single_family_home` deleted, conflict 8 executed), 18 setups + 24 tests already on the new builders. **The repo's only primary‑fact provider: six facts at `building/information.py:761-790`.** Deliberately has no sized field (`:33-38`) — a deviation from the supplement's eleven `AUTO` fields that §6 endorses. Open items: the 20‑field post‑construction mutation in all 11 sizers (D‑22), `heating_reference_temperature_in_celsius` as a weather‑derived fact (§1a, D‑21), and `roof_area_in_m2` for group B's gate B‑0. |
| `UtspLpgConnectorConfig` (`loadprofilegenerator_utsp_connector.py:67`) | **done (P2), partially** — `preset_standard` `:122`, `for_household` `:135`, no sized fields (correct). **`get_default_utsp_connector_config` `:94` is still live at 19 setups + 13 tests**; the new builders have 2 test call sites. Mechanically deletable (the bodies agree value for value) **except** that 11 sizers immediately override `data_acquisition_mode`, `household` and `cache_dir_path` (`household_heatpump_building_sizer.py:227-229`), and `for_household` silently ignores its own `household` in its own default mode (D‑20). Provides no facts; `number_of_residents` is a `SizingContext` field with neither writer nor reader. |

---

## C‑f — Exempt / delete rows

| Class | Line | Call sites | Verdict |
|---|---|---|---|
| `WeatherDataImport` | `weather_data_import.py:36` | 2 setups; tests commented out | **exempt** (data reader, no config class — §6 confirmed). **Its import fails** (`wetterdienst` commented out in `requirements.txt:28`) — the only import failure in group C. |
| `SmartDeviceConfig` / `SmartDevice` | `generic_smart_device.py:42`, `:131` | **0 anywhere** | **delete** — dead *and* defective: `__init__` raises `KeyError: 'utsp_reports'` unconditionally (`:291`; the key is absent from `HISIMPATH`). New D13 member. |
| `AdvElectrolyzerConfig` | `configuration.py:774` | **0 anywhere** | **delete** — supplement conflict 10. A plain class of class attributes duplicating `ElectrolyzerWithStorageConfig`, with a typo: `max_power = 2_4000` (`:779`, i.e. 24 000 W) against the live class's 2 400 W, and `min_power = 1_400` against 1 200. |
| `ElectrolyzerWithStorageConfig`, `ElectrolyzerWithHydrogenStorageConfig` | `generic_electrolyzer_and_h2_storage.py:31`, `:73` | 0 setups, 3 tests each | **delete or convert — D‑29.** Duplicates of `GenericElectrolyzerConfig` / `GenericHydrogenStorageConfig` in different units. |
| `RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig` | `generic_rsoc.py:31`, `controller_l1_rsoc.py:31`, `controller_l2_rsoc_battery_system.py:29` | 0 setups; 4 / 7 / 10 tests | **blocked on D‑25** — every builder reads `rSOC_manufacturer_config.json`, which is not in the repository. |
| `FuelCellConfig`, `FuelCellControllerConfig`, `XTPControllerConfig` | `generic_fuel_cell.py:38`, `controller_l1_fuel_cell.py:28`, `controller_l2_xtp_fuel_cell_ems.py:31` | 0 setups; 1 / 1 / 2 tests | **the hand‑typed PEM defaults are convertible; the three `read_config`/`for_device` paths are blocked on D‑25** (`fuel_cell_manufacturer_config.json` absent). |

---

## Dependency order inside group C, and to groups A and B

**Group C has no gate of its own** — unlike group A (`set_heating_threshold_outside_temperature_in_celsius`) and group B (`roof_area_in_m2`, `pv_peak_power_in_watt`), nothing in group C needs a new `SizingContext` field, because group C is the *provider* side. But **groups A and B both depend on group C's Building**, which is already done: `BuildingConfig.SIZING_CONTRIBUTIONS` (`building/information.py:779`) supplies `heating_load_in_watt`, `conditioned_floor_area_in_m2`, `number_of_apartments`, `heating_reference_temperature_in_celsius` and the two setpoints that every group‑A generator and group‑B storage law reads. **Group C's only *outgoing* obligation is group B's gate B‑0: adding `roof_area_in_m2` to the Building's contribution touches `building/information.py`, i.e. my module, not group B's.** It should ship as part of B‑0's single commit, as group B proposes.

Then, inside group C:

1. **Leaves, no facts consumed or provided — any order, good first commits:**
   `SimpleControllerConfig`; `ExampleTransformerConfig`; `SimpleStorageConfig`; `ComponentNameConfig`; `L1ElectrolyzerControllerConfig`; `GenericHydrogenStorageConfig`; `CarBatteryConfig`; `CSVLoaderConfig` (constructor‑only, the conflict‑9 precedent — do it **first**, because it is what proves the contract test accepts "constructor, no preset" before six H₂ classes need that state).
2. **`ExampleComponentConfig`** — reads `conditioned_floor_area_in_m2`, which already exists. The smallest end‑to‑end demonstration of a law in the repository; golden‑neutral (0 setups, 11 tests on the default building). Best second commit, and the model for `ComponentNameConfig`'s template law (D‑31).
3. **`ChargingStationConfig`** → `Self("charging_station_set")` scale law; self‑contained, no context fact.
4. **`GenericElectrolyzerConfig`** → three `Self("max_power")` laws; self‑contained.
5. **The occupancy and weather legacy‑factory removals** (`get_default_utsp_connector_config`, 32 call sites; `WeatherConfig.get_default`, 44 call sites) — **the two largest mechanical commits in the whole sweep**, both blocked on owner decisions (D‑18/D‑19 for the weather, D‑20 for the occupancy) rather than on any other class. They are independent of everything else and of groups A and B.
6. **`CarConfig`, and the car chain generally** — after step 1's `CarBatteryConfig` and step 3's `ChargingStationConfig`, and only if D‑23 keeps the chain. Note the ordering constraint is *not* a fact chain: it is that `Car` is not executor‑buildable, so converting its config buys nothing until that is resolved.
7. **The H₂/fuel‑cell/RSOC chain** — last, and split by D‑25: `generic_electrolyzer_h2` + `controller_l1_electrolyzer_h2` + `controller_l2_ptx_energy_management_system` are convertible today (their JSON exists and `electrolyzer_with_renewables.py` runs); the six fuel‑cell/RSOC classes are not.

**Cross‑group edges, complete:** group C → group A/B via the Building's six facts (already in place) and via `roof_area_in_m2` (B‑0, my file). Group B → group C via D‑16: retiring `MpcController` and `PIDController` makes the Building's six construction‑time 5R1C singleton keys (`building/building.py:909-931`) dead, which is a *deletion* in my module gated on a *decision* in group B's (D‑32). No group‑C class consumes a group‑A or group‑B fact. The chain stays acyclic and, counting the Weather as a future root, at most 4 deep (weather → building → generator → controller), matching inventory §1a.

**Many‑cardinality (EQ2): no class in group C is the first real consumer.** The one shape that looks like it — one occupancy producing N cars — is §6's "count‑driven instance fan‑out", which §6 explicitly rules out of the sizing engine and hands to the R5 template layer. `Many(...)` may stay a raising hook through B7 and B8.

---

## D13 list for these modules

**Import check** (`local_python_env/bin/python -c "import hisim.components.<m>"`) over all 26 modules in scope — **one failure**:

- **`weather_data_import`** — `ModuleNotFoundError: No module named 'wetterdienst'`. The dependency is deliberately commented out (`requirements.txt:28`, "TODO: migrate weather_data_import.py to new API before upgrading"). Not a P4 case (no config class, exempt), but it means two setups (`simple_weather_data_import.py`, `basic_household_with_weather_data_request.py`) cannot even be imported in this environment.

The other 25 import cleanly: `weather`, `loadprofilegenerator_utsp_connector`, `building.{config,building,information}`, `generic_smart_device`, `generic_car`, `advanced_ev_battery_bslib`, `controller_l1_generic_ev_charge`, `controller_l1_electrolyzer`, `controller_l1_electrolyzer_h2`, `controller_l1_fuel_cell`, `controller_l1_rsoc`, `controller_l2_ptx_energy_management_system`, `controller_l2_rsoc_battery_system`, `controller_l2_xtp_fuel_cell_ems`, `csvloader`, `generic_electrolyzer`, `generic_electrolyzer_and_h2_storage`, `generic_electrolyzer_h2`, `generic_fuel_cell`, `generic_hydrogen_storage`, `generic_rsoc`, `example_component`, `example_storage`, `example_template`, `example_transformer`, `controller_l1_example_controller`.

**Build‑from‑own‑defaults check** — 46 factories/presets/constructors exercised (script in the scratchpad). **14 failures, in four kinds:**

1. **Missing input data — 10 failures across 6 classes.** `fuel_cell_manufacturer_config.json` and `rSOC_manufacturer_config.json` **are not in this repository** (`hisim/inputs/` ships only `electrolyzer_manufacturer_config.json`, `electrolyzer_polarization_curve_data.json`, `polarization_curve_data_fc.json`, `rSOC_efficiency_curve_data.json`). Affected: `FuelCellConfig.read_config` / `.config_fuel_cell`, `FuelCellControllerConfig.read_config` / `.control_fuel_cell`, `XTPControllerConfig.read_config`, `RsocConfig.read_config` / `.from_rsoc_name`, `RsocControllerConfig.read_config` / `.config_rsoc`, `RsocBatteryControllerConfig.read_config` — all `FileNotFoundError`. **For `RsocConfig`, `RsocControllerConfig` and `RsocBatteryControllerConfig` this is total: those three classes have no other builder.** This is the concrete content of the plan's "5 legitimately data‑dependent" — except the data is absent, so they are *un*buildable rather than merely data‑dependent. D‑25.
2. **A factory with a mandatory argument — 1.** `GenericElectrolyzerConfig.get_default_config()` → `TypeError: missing 1 required positional argument: 'p_el'`. A real "cannot be built from its own defaults" case, not in `plan.md:72`'s enumeration.
3. **A config with no factory — 1.** `CSVLoaderConfig()` → `TypeError: missing 10 required positional arguments`. Expected (conflict 9); it needs `for_csv_file`, not a preset.
4. **Two‑argument constructors called with one — 2.** `RsocBatteryControllerConfig.config_rsoc` and `XTPControllerConfig.control_fuel_cell` both require `operation_mode`; not defects, just signature facts.

**Component‑construction check** — 18 components built with their default config. **One failure:** `generic_smart_device.SmartDevice` → `KeyError: 'utsp_reports'` (`:291`), unconditional. **Add to D13.**

**Executor‑buildability check** — signature sweep over every `Component` subclass in scope for constructor parameters beyond `(my_simulation_parameters, config, my_display_config)`. **One failure:** `generic_car.Car` requires `data_dict_with_car_information: Dict` (`:242`), which the executor never passes (`wiring.py:143-145`). `CSVLoader`'s two extras (`inputs_dir`, `dataframe`) are optional and harmless.

**Legitimately data‑dependent, therefore constructor‑ not preset‑shaped** (the assignment's question): `WeatherConfig` (45 `LocationEnum` stations + arbitrary files), `BuildingConfig` (hundreds of TABULA codes), `UtspLpgConnectorConfig` (66 LPG households), `CSVLoaderConfig` (any file), `ChargingStationConfig` (10 LPG charging sets), `ElectrolyzerConfig` + `ElectrolyzerControllerConfig` + `PTXControllerConfig` (9 JSON devices), `FuelCellConfig` + `FuelCellControllerConfig` + `XTPControllerConfig` (JSON absent), `RsocConfig` + `RsocControllerConfig` + `RsocBatteryControllerConfig` (JSON absent). **Genuinely data‑dependent on a *simulation result* rather than a catalogue, and therefore not expressible as a constructor either:** `CarConfig`/`Car` (needs a live LPG car profile) and `SmartDeviceConfig`/`SmartDevice` (needs an LPG flexibility report).

**Other dead or defective code the gate should sweep up:**
- `SmartDeviceConfig` + `SmartDevice` — 0 call sites, defective (above).
- `CarConfig.get_default_diesel_config` — 0 call sites anywhere.
- `AdvElectrolyzerConfig` (`configuration.py:774`) — 0 call sites, with a `2_4000` typo.
- `ElectrolyzerWithStorageConfig`, `ElectrolyzerWithHydrogenStorageConfig`, `GenericElectrolyzerConfig`, `L1ElectrolyzerControllerConfig`, `GenericHydrogenStorageConfig` — 0 setup uses, tests only.
- All ten H₂/fuel‑cell/RSOC modules together account for **one** setup (`electrolyzer_with_renewables.py`, using 2 of the 14 classes).
- **No mutable dataclass field defaults anywhere in group C** — verified over all 26 config classes; group A's `Coordinates` hazard has no analogue here (every `[]`/`{}` is inside a method body).
- **Not a D13 case but a P2 defect:** **constructor arguments are passed to the builder undecoded.** `EntryConfigurer._call_builder` (`configure.py:188-204`) forwards `dict(entry.constructor.arguments)` straight from YAML; `BindingResolver.check_constructor_arguments` (`bindings.py:283-310`) checks names and presence only. Verified: `WeatherConfig.for_location("W", location="AACHEN")` raises `AttributeError: 'str' object has no attribute 'value'`. Of the repo's three constructors, only `BuildingConfig.for_tabula_code` (all‑scalar parameters) is callable from a v3 file; `for_location` (`LocationEnum`) and `for_household` (`JsonReference`) are not. `tests/test_energy_system_utsp.py:84` exercises `for_household` in **Python**, not through the file, so nothing catches this. D‑19.

---

## Is there anything left in plan B6?

**Yes, and it is the biggest mechanical commit pair in the sweep — but its third item is void.** B6 reads "Occupancy, weather (`for_location` pattern), building presets (`german_multi_family_home`)". After P2's conversions:

- **Occupancy — content remains.** `preset_standard` and `for_household` exist, but `get_default_utsp_connector_config` (`:94`) is still the fleet's builder at **19 setups + 13 tests**. B6 owes: delete it, move 32 call sites, decide how the 11 sizers' `USE_LOCAL_LPG` + list‑of‑households + `cache_dir_path` overrides are spelled (A2 `config:`), and fix or document the inert `household` argument (D‑20).
- **Weather — content remains, and it is blocked.** `preset_standard` and `for_location` exist with **zero** call sites; `get_default` (`:403`) carries **19 setups + 25 tests**. B6 owes: `for_location` must gain the two direct‑file parameters the supplement specified and dropped (7 of the 8 golden setups depend on them) or a second `for_data_file` constructor (D‑18); it must accept the string spelling 5 setups use, or the executor must decode constructor arguments (D‑19); then 44 call sites move. The "`for_location` pattern" the plan names is *implemented but unusable*, which is not what the checkbox implies.
- **Building presets, `german_multi_family_home` — no content; the item is void as written.** `BuildingConfig` is fully converted with **no legacy factory left**, and supplement conflict 8 (accepted) resolves that "Building is a TABULA‑code lookup and must expose exactly one preset named `standard`" — which forbids minting `german_multi_family_home`. No such preset, archetype or fixture exists anywhere in the repository (grep over `hisim/`, `system_setups/`, `tests/`). An MFH is spelled `for_tabula_code("DE.N.MFH.…", number_of_apartments=N)`, which is what the sizers already do. **Recommendation: strike the third clause of B6 and record it as executed by conflict 8.**
- **Two items B6 does not name but owns**, because they live in these modules: adding `roof_area_in_m2` to `BuildingConfig.SIZING_CONTRIBUTIONS` (group B's gate B‑0 — the code change is in `building/information.py`), and the §1a decision on `heating_reference_temperature_in_celsius` becoming a Weather‑derived fact (D‑21), which is the *only* thing that would give the Weather a `SIZING_CONTRIBUTIONS` and turn "climate facts" from a parking‑lot item into B6 work.

So B6 = two large factory‑removal commits (44 + 32 call sites), gated on four owner decisions, plus one fact‑contribution line shared with B5 — and one clause to delete.

---

## Decisions the owner must take

**D‑18 — `WeatherConfig.for_location` dropped the direct‑file path the supplement specified. How does it come back?**
Seven setups — `household_{gas,oil,pellets,wood_chips,electric_heating,district_heating,heatpump}_building_sizer.py`, i.e. **7 of the 8 golden setups** — and `tests/test_weather.py:91,108` pass `weather_direct_filepath` + `weather_direct_data_source` to `get_default`; `for_location` (`weather.py:478-513`) accepts neither, so the legacy factory cannot be deleted. (a) **Add the two keyword parameters to `for_location`**, as the supplement's signature had them, making one constructor cover both the catalogue and an arbitrary file. (b) **Mint a second constructor `for_data_file(path, data_source)`** and keep `for_location` purely catalogue‑shaped. (c) **Keep `get_default` alive** as a third builder alongside the preset and the constructor.
*Consequence:* (a) is one small edit and matches the accepted supplement, but gives one constructor two mutually exclusive modes and a `location` argument that means a display string in one of them; (b) is two clean identifier spaces and one more wire name that E8 freezes at P5; (c) leaves the fleet's real builder outside the preset system indefinitely and makes B6 undeliverable.

**D‑19 — Constructor arguments reach the builder undecoded, so two of the three shipped constructors cannot be called from a file at all.**
`configure.py:188-204` forwards YAML arguments verbatim; `bindings.py:283-310` checks names and presence only. Verified: `for_location("W", location="AACHEN")` → `AttributeError: 'str' object has no attribute 'value'`. `for_household` has the same problem with `JsonReference`; only `for_tabula_code` (all‑scalar) works. Five setups also pass `weather_location` as a **string** from `ArcheTypeConfig.weather_location: str` (`archetype_config.py:160`). (a) **Decode constructor arguments by annotation in the executor**, reusing `ConfigCodec._decode_enum` (`codec.py:287-327`) and adding a `JsonReference` path — a kernel change, so an E6 exception like group A's Gate 0 and group B's Gate B‑0. (b) **Widen the constructor signatures** to `Union[LocationEnum, str]` / `Union[JsonReference, str]` and resolve inside each builder, as `get_default:420-424` already does. (c) **Leave it**: constructors stay Python‑only and files use `preset:` plus a full `config:` block for any non‑default station or household.
*Consequence:* (a) fixes it once for every constructor the sweep will mint (twelve in the supplement's table) and is where the error message belongs; (b) is per‑class and re‑introduces the `getattr(Enum, string)` idiom the conversion removed, in twelve places; (c) means the constructor half of the format is decorative — `describe` advertises builders no file can call, and UC1/UC2 can only ever use the one preset per lookup class.

**D‑20 — `for_household` silently simulates the wrong household.** `[answered 2026-08-27, review of #592]` **(a)**, implemented in P2: the constructor takes `data_acquisition_mode`, derives `name_of_predefined_loadprofile` from the household through `predefined_profile_of`, and refuses an unshipped household or a list of them, naming the shipped households and the two computing modes. The original entry follows.
In `USE_PREDEFINED_PROFILE` — the mode both builders hard‑code (`:104`, `:170`) — the profile actually read is `name_of_predefined_loadprofile` (`:601`), which both builders pin to `"CHR01 Couple both at Work"` regardless of the `household` argument. `for_household(name, household=Households.CHR03_…)` therefore runs CHR01, with no warning. (a) **Derive `name_of_predefined_loadprofile` from `household`** and raise when no shipped profile matches, making the mode honest. (b) **Make `for_household` select `USE_LOCAL_LPG`** and let the predefined profile be `preset_standard`'s business only. (c) **Restrict `for_household` to lists / non‑default households and document the coupling.**
*Consequence:* (a) is the only option under which the constructor's 66‑member identifier space means anything, and it will turn a silent wrong answer into a hard error for households with no shipped profile; (b) makes the constructor need a local LPG installation, which CI does not have; (c) freezes an argument that does nothing into the wire format, which is precisely the "silent repair" E1 forbids.

**D‑21 — Does `BuildingConfig.heating_reference_temperature_in_celsius` become a Weather‑derived fact in B6?**
Inventory §1a: it is a hand‑typed `-7.0` (`config.py:104`) that nothing checks against the chosen weather, and making the Weather a provider turns the Building into a consumer as well (chain depth 4, still acyclic). The setups re‑pass `-7.0` twice — onto the Building (`household_heatpump_building_sizer.py:194`) and onto the heat pump (group A). (a) **Do it in B6:** add `heating_reference_temperature_in_celsius` to `WeatherConfig.SIZING_CONTRIBUTIONS` from a per‑station DIN 12831 table (the `LocationEnum` entries already carry the TRY region in their directory name, `weather.py:62+`), make the Building's field `AUTO`. Physics change with result diffs for every non‑Aachen setup, own commit. (b) **Defer to the parking lot** ("climate facts … trigger: first law that reads them") and leave the constant. (c) **Do the fact plumbing but keep the Building's field a plain default**, so the fact exists for group A's heat pumps without changing the Building's heating load.
*Consequence:* (a) removes the `weather_try_region` / `heating_reference_temperature` knobs from the archetype (a P5 simplification the inventory already anticipates) but changes the norm heating load — and therefore *every* generator size — in the four non‑Aachen setups; (b) means B6 ships without the one thing that would make the Weather a provider at all, and the parking‑lot trigger never fires because no law can read a fact that does not exist; (c) is the cheap middle and creates the first two‑provider ambiguity in the repo (a district with two stations needs a `sizing_sources` line, R4.3).

**D‑22 — The Building's 20‑field post‑construction mutation: sparse `config:` overrides, or more constructor parameters?**
`household_heatpump_building_sizer.py:194-218` mutates **20 fields unconditionally plus one conditionally** on a `preset_standard` config, in all 11 sizers (`p3` §2e says 15 — the real count is 20). Only 6 are `for_tabula_code` parameters; 10 of the rest are `None`‑by‑default envelope opt‑ins. (a) **All 14 non‑parameters become `config:` overrides**, with the recorder diffing against a fresh preset — the recorded file carries ten `null`s for the golden fleet unless the recorder omits equal‑to‑default fields. (b) **Widen `for_tabula_code`** to take the ten envelope pairs, giving a 17‑parameter constructor. (c) **Add a second constructor `for_measured_envelope(...)`** for the RenoVisor/building‑sizer path and leave `for_tabula_code` as it is.
*Consequence:* (a) keeps the constructor readable and puts the overrides where a reader expects them, but makes an MFH archetype file ~20 lines of `config:` per building; (b) produces a constructor nobody can read and duplicates the field list; (c) is one more wire name and matches how the two consumers actually differ (P5), at the cost of deciding it before P5 tells us what they need.

**D‑23 — The car chain cannot be expressed in an energy‑system file. Convert it, or take it out of P4?**
`Car.__init__` requires a third argument, `data_dict_with_car_information` (`generic_car.py:242`), which the executor never passes (`wiring.py:143-145`); the data comes from a live LPG/UTSP run via `GenericCarInformation` (`:42-72`, raising for the predefined profile), and the number of cars is a property of that result. `household_heatpump_car_building_sizer.py` is smoke‑gated only, not in the golden fleet. (a) **Convert the three configs to presets anyway** (`electric`/`diesel`, `standard`, `standard`) and accept that `Car` stays Python‑only until the R5 template layer and a `${var}` data path exist. (b) **Move the profile into `CarConfig`** as a path field (`driving_profile_path`) so `Car` becomes a two‑argument component and the setup's loop becomes N file entries. (c) **Defer the whole chain to P5/R5** and convert nothing; fix only the shared‑config aliasing bug (`:396` reused for every car).
*Consequence:* (a) mints four wire names for a component no file can instantiate, which E8 freezes at P5; (b) is the only route to a recordable car setup and turns a data dependency into a declared input, at the cost of a real refactor inside `generic_car.py` and a decision about who writes the per‑car CSV; (c) leaves `p3`'s one loop‑generating setup permanently unrecordable and postpones the aliasing bug, which is latent only because CHR01 yields exactly one car.

**D‑24 — `ChargingStationConfig`: whose rating is `standard`?**
The factory defaults to `Charging_At_Home_with_03_7_kW` (`:69`); the only caller passes `Charging_At_Home_with_11_kW` (`household_heatpump_car_building_sizer.py:399,428`). (a) **`standard` = 3.7 kW** (the factory default) + `for_charging_station_set`; the one setup overrides via the constructor. (b) **`standard` = 11 kW** (what is actually used) + the constructor; the factory default is discarded as an unused number, the same call the group‑A survey made for `air_water_8kw` (D‑6). (c) **No preset, constructor only** (conflict 9), since the rating *is* the identifier (conflict 7).
*Consequence:* (a) ships a wire name whose only meaning is "a value nobody chose"; (b) makes the bare preset match the fleet, at the price of an unrecorded change to the factory default that no test covers; (c) is the most honest and forces one line into every file that has a car — which is one line, since there is one such setup.

**D‑25 — Two manufacturer JSON files are missing; six classes have no buildable builder.**
`hisim/inputs/fuel_cell_manufacturer_config.json` and `rSOC_manufacturer_config.json` do not exist in the repository. `FuelCellConfig`, `FuelCellControllerConfig`, `XTPControllerConfig`, `RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig` therefore fail on every disk path; the last three have **no other builder at all** and their tests only pass because they inject a fabricated dict. (a) **Ship the two JSON files** (they clearly existed once — the readers, the tests' key names and `rSOC_efficiency_curve_data.json` all assume them) and convert all six normally. (b) **Move the RSOC trio and the fuel‑cell table path to `obsolete/`**, keeping only `FuelCellConfig.preset_pem` and `FuelCellControllerConfig.preset_pem`, whose hand‑typed defaults do work. (c) **Convert as constructors anyway** and let them fail at build time with the existing `FileNotFoundError`.
*Consequence:* (a) is the only option that makes the classes usable, but the data has to come from somewhere and nobody in this repository can produce it; (b) removes ~1 400 lines and 21 tests of code that has never been runnable here, and is the same call the plan's D13 gate exists to make; (c) puts six classes into the wire vocabulary and the `describe` output whose every constructor throws — the worst outcome for a format whose selling point is that errors are precise.

**D‑26 — Five of the nine `read_config` readers silently return `{}` and build a config of zeros.**
`controller_l1_electrolyzer_h2:71-73`, `controller_l2_ptx_energy_management_system:48-50`, `generic_fuel_cell:90`, `controller_l1_fuel_cell:70`, `controller_l2_xtp_fuel_cell_ems:52`, `generic_rsoc:61`, `controller_l1_rsoc:74`, `controller_l2_rsoc_battery_system:74` all use `data.get(section, {}).get(name, {})` and then `.get(key, 0.0)` per field; **only `generic_electrolyzer_h2:79-82` raises** — and the two readers of the *same* file disagree (verified: `ElectrolyzerControllerConfig.read_config("Alkaline Electrolyzer")` returns `{}` and logs; `ElectrolyzerConfig.read_config("Alkaline Electrolyzer")` raises `KeyError`). (a) **Normalise on raising**, listing the available device names — which is what a named constructor should do, and what P2's error catalogue does everywhere else. (b) **Keep the zero‑fill** and let a mistyped device name produce a silently idle electrolyzer.
*Consequence:* (a) is a behaviour change to eight builders, all of which are currently unreachable or single‑use, and it is the E1/R7 answer; (b) means a v3 file can name a device that does not exist and get a plausible‑looking run of zeros — the exact failure mode `for_device` is being introduced to prevent.

**D‑27 — `operation_mode: str` in three classes is free text over a closed set.**
`PTXControllerConfig:41` (`"NominalLoad"`, `"MinimumLoad"`, `"StandbyLoad"`, `"StandbyandOffLoad"`, branched at `:178-205` with no `else`), `XTPControllerConfig:44` (`"StandbyLoad"`, `"StandbyandOffLoad"`, `:163,178`), `RsocBatteryControllerConfig:51` (`:227-232`). The supplement already flags the third ("should be an enum before it becomes wire format"). (a) **One shared enum** in `loadtypes.py` before conversion — a small golden‑neutral commit, and `value_type=` then works if the field ever becomes sizable. (b) **Three per‑module enums**, since the three vocabularies differ. (c) **Leave them as strings.**
*Consequence:* (a) is the cheapest and gives the codec a members list for its error message, but merges three sets that are not the same; (b) is correct and mints three enums for code with one live setup; (c) freezes at P5 three fields where a typo silently disables the controller.

**D‑28 — `GenericElectrolyzerConfig` has no default at all: laws or a constructor?**
`get_default_config(p_el)` (`:47`) requires a rated power and derives four fields from it (`:70-73`), including two the supplement does not mention. (a) **Preset `standard` with a plain `max_power` default and three `Self("max_power")` laws** — a clean, self‑contained demonstration of the sibling‑field law with no new fact. (b) **Constructor `for_rated_power(p_el)`**, keeping the four values opaque. (c) **Delete the class** as a duplicate of `ElectrolyzerWithStorageConfig` (D‑29).
*Consequence:* (a) puts the `8.989 / 3.6e4` conversion into the audit trail where a reviewer can see it, and requires choosing a default rating nobody has chosen before; (b) records the four numbers but not where they came from; (c) makes the question moot but needs D‑29 first.

**D‑29 — Four classes, two devices: which electrolyzer and which H₂ storage survive?**
`GenericElectrolyzerConfig` (`generic_electrolyzer.py:31`, W and kg/s) vs `ElectrolyzerWithStorageConfig` (`generic_electrolyzer_and_h2_storage.py:31`, W and Nl/h); `GenericHydrogenStorageConfig` (`generic_hydrogen_storage.py:29`, kg and Wh/kg) vs `ElectrolyzerWithHydrogenStorageConfig` (`:73`, kg and kWh/kg). Neither pair shares a setup; each has 1–6 tests. (a) **Keep the `generic_*` pair, obsolete `generic_electrolyzer_and_h2_storage.py`** — its two classes have the same factory name, its `AdvancedElectrolyzer`/`HydrogenStorage` components duplicate the other two, and `configuration.AdvElectrolyzerConfig` (0 call sites, with a `2_4000` typo at `:779`) is its dead third copy. (b) **Keep both** and convert all four, giving a file two indistinguishable ways to spell an electrolyzer. (c) **Keep the `_and_h2_storage` pair** (it models waste energy and part‑load percentages the `generic_*` pair does not) and retire the others.
*Consequence:* (a) removes ~700 lines and 6 tests and leaves the pair whose units match the rest of HiSim; (b) doubles the H₂ wire vocabulary for a chain with one setup and guarantees the two get out of sync; (c) keeps the richer model but forces `generic_hydrogen_storage`'s two default‑connection methods (`:184`, `:203`) to be rewritten.

**D‑30 — `generic_smart_device`: delete, or fix and convert?**
`SmartDevice.__init__` raises `KeyError: 'utsp_reports'` on every construction (`:291`; the key is absent from `HISIMPATH`), 0 setups, 0 tests, and the supplement's proposed `standard` preset would ship `identifier="Identifier"`, which matches no device (`:314`). It is a **new D13 member** the plan does not list. (a) **Move to `obsolete/`** with `SmartDeviceConfig` in one commit, before anyone mints a preset for it. (b) **Fix the path key** (presumably `utsp_example_reports`), give the preset a real identifier, and convert. (c) **Leave it and skip it in the sweep.**
*Consequence:* (a) removes a component that has been unreachable long enough for its input path to disappear, and drops §6's `smart_devices_included` copy‑law candidate with it; (b) resurrects a demand‑response feature nothing uses and freezes an LPG appliance name as wire format; (c) leaves a class the sweep's contract test will trip over.

**D‑31 — What does the template teach?**
`example_template.py` is the file every new component author copies (`CLAUDE.md`), and EAC4 claims a new component reaches an energy system "with no change outside its module" — but `ComponentNameConfig` (`:35`) has two fields, one factory and no law, so the template demonstrates none of the mechanism. Meanwhile `ExampleComponentConfig` already contains a hidden law: `capacity=45 * 121.2` (`example_component.py:58`) is `45 J/K/m² × conditioned_floor_area_in_m2`, a fact that exists (`context.py:50`), on a class with 0 setups and 11 tests all using the 121.2 m² building. (a) **Give the template one `sized_field` and one `SIZING_CONTRIBUTIONS` entry**, and turn `example_component`'s `capacity` into the real law — both golden‑neutral, and B8 becomes the batch that documents the pattern. (b) **Convert both to bare `standard` presets** and leave the literal, documenting the pattern in prose only. (c) **Template gets the law, `example_component` keeps the literal** (it is a numerics fixture for 11 tests).
*Consequence:* (a) makes EAC4 demonstrable and removes the repo's last unexplained `45 * 121.2`, at the cost of the 11 tests now depending on the sizing kernel; (b) means every component written after P4 is written from a template that predates P4; (c) keeps the fixture inert and leaves one magic literal, duplicated at `:174`.

**D‑32 — The two construction‑time singleton key groups group C owns.**
(i) `SingletonDictKeyEnum.LOCATION` is written by `Weather.__init__` (`weather.py:569`) and **read by live code**: `postprocessing/postprocessing_main.py:921,922,1003,1004` (the report region) plus `tests/test_singleton_sim_repository.py:154`. (ii) The Building writes six 5R1C keys from `build()` (`building/building.py:909-931`), whose **only** readers are `controller_mpc.py:465-480` and `controller_pid.py:136-141` — the two zero‑setup components group B's D‑16 proposes retiring. (a) **Delete both groups in P4**: postprocessing reads the Weather component's `config.location` directly, and the six 5R1C keys go with MPC/PID under D‑16 — one commit each, per the plan's "never bundled with a conversion". (b) **Delete only the six**, if D‑16 retires MPC and PID, and leave `LOCATION` to the runtime‑SimRepository redesign. (c) **Leave both** to that redesign.
*Consequence:* (a) finishes the plan's "dead SimRepository sizing keys deleted" for group C and removes the last construction‑time global out of the Building and the Weather; (b) is safe and correct but leaves one config value travelling through a global into the report; (c) means the Weather and the Building keep writing globals after the fact engine has made them unnecessary, and the parking‑lot item stays larger than it needs to be.
