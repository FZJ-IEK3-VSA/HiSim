# HiSim RenoVisor Translator — Energy-System Specification (v2 draft)

The v1 translator (`spec.md`) takes a RenoVisor JSON request, selects one of the
`household_*_building_sizer.py` Python setups and generates a `ModularHouseholdConfig`,
then runs that setup in process. This document specifies its successor, which targets
HiSim's declarative **energy-system files** (`*.energy_system.yaml`) instead.

It is the realisation of decision **Q6** in
`roadmap/declarative_energy_systems/config_requirements_spec.md` and task **P5** in
`roadmap/declarative_energy_systems/plan.md`:

> `ModularHouseholdConfig` was a workaround for the missing energy-system files and is
> replaced by using them directly. Its knobs map onto three things the format already has:
> field overrides → sparse `config:`; topology choice (`heating_system`) → one checked-in
> base file per heating system; presence toggles → component `groups` with `enabled`.
> RenoVisor's `mapping.py` shrinks to "pick base file, apply overrides, toggle components".

The proposed input contract is `openapi.yaml` (RenoVisor API, `0.3.0-draft`). Its
`HomeInventoryInput` is the building as the user entered it; a `PackageDefinition` carries
the renovation measures. Both are **pre-energy-system-yaml**: their `energy_system_config`
is a flat component-keyed block of scalar knobs, which does not map onto the YAML format's
model of a wired topology of named components with presets, groups and AUTO-sizing. This
spec defines the mapping from the openapi contract onto energy-system files, and notes where
the openapi must move to match the format.

```
HomeInventoryInput + PackageDefinition
  └─ schema.py        validate the openapi contract
  └─ compose.py       request ──▶ EnergySystemFile model  (was: mapping.py ──▶ ModularHouseholdConfig)
        1. pick a base *.energy_system.yaml per heating system
        2. apply sparse config overrides (building code, floor area, PV, weather, LPG, …)
        3. toggle groups (pv, battery_and_ems, solar_thermal, ev)
        4. dump via EnergySystemEmitter
  └─ runner.py        run_energy_system(energy_system.yaml, simulation.yaml)   (was: hisim_main.main)
  └─ uploader.py      REST lifecycle (unchanged)
```

`ModularHouseholdConfig`, `ArcheTypeConfig`, `EnergySystemConfig` and the setup-selection
table are deleted from the translator. `tabula_ie.py`, the country→weather and the
residents→LPG-household mappings carry over unchanged.

---

## 1. Goal and scope

Produce, from a RenoVisor `HomeInventoryInput` and an optional `PackageDefinition`, a
runnable pair of files — one `*.energy_system.yaml` and one `*.simulation.yaml` — that
`hisim.energy_system.executor.run_energy_system` can execute, then run it and submit the
result files via REST, exactly as v1 does.

**In scope:** request validation against the openapi contract; composition of the
energy-system file (base-file selection, config overrides, group toggles, sizing);
measure application; the mapping report; the runner switch; the unchanged REST lifecycle.

**Out of scope (as in v1):** deriving RenoVisor's `Kpis`/`Costs` from the HiSim outputs —
the translator uploads raw result files; interpreting them is the receiving server's job.
`condition_assessment`, `selected_grant_schemes`, `Schedule` and `Financing` are
package/cost-layer concerns with no simulation meaning; they are accepted, flagged in the
mapping report and otherwise ignored (§9).

## 2. Relationship to v1 and to the energy-system format

| | v1 (`spec.md`) | v2 (this document) |
|---|---|---|
| Input contract | `scripts/hisim_spec.md` `homeInputs` + `measures` (camelCase) | `openapi.yaml` `HomeInventoryInput` + `PackageDefinition` (snake_case) |
| Translation output | a `*_building_sizer.py` setup name + `ModularHouseholdConfig` JSON | a composed `*.energy_system.yaml` + a `*.simulation.yaml` |
| Topology | hidden in the chosen Python setup | one checked-in base file per heating system, overridden |
| Building | `ArcheTypeConfig` (building_code, floor area, PV, weather, LPG, …) | `building` component: `constructor: for_tabula_code` + `config:` overrides |
| Heating system | `EnergySystemConfig.heating_system` enum + setup file | base-file selection |
| PV / battery / solar thermal / EV | `share_of_maximum_pv_potential`, `use_battery_and_ems` flags | `groups.<name>.enabled` + `config:` overrides |
| Sizing | auto-sized inside the setup, opaque to the caller | `AUTO` from facts, or a concrete `config:` override |
| Per-element U-values | not represented (TABULA variant bump only) | `building.config` overrides (first-class) |
| Runner | `hisim_main.main(path_to_module, my_module_config)` | `run_energy_system(energy_system_path, simulation_parameters_path)` |
| Post-processing | `SimulationParameters` built in code | the `*.simulation.yaml` file |

The energy-system format is specified by `hisim/energy_system/` and the mockups in
`roadmap/declarative_energy_systems/`. Its in-memory model (`hisim/energy_system/model.py`)
is `EnergySystemFile` → `ComponentEntry` (class, preset/constructor, config, inputs,
sizing_sources) and `Group` (enabled, components). `EnergySystemEmitter.dump(model)` writes
the canonical YAML; `parse_energy_system(path)` reads it back. Three classes are
parameterised by an open identifier space and take a `constructor` rather than a `preset`:
`Building.for_tabula_code`, `Weather.for_location`, `UtspLpgConnector.for_household`.

## 3. The input contract

The translator consumes the openapi `HomeInventoryInput` (the building as it stands today)
and, for the `measures` variant, a `PackageDefinition`. The `job` dispatch envelope from v1
(`jobId`, `submission`, `simulationOverrides`) is retained unchanged — it is not part of the
openapi contract; whoever dispatches jobs adds it.

### 3.1 `HomeInventoryInput` → energy-system fields

The openapi block and its mapping. Field names are the openapi's (snake_case).

**`location`** — `country_code` (ISO-3166-1 alpha-2), `region`, `eircode_or_postcode`.
`country_code` selects the weather location and the country prefix of the TABULA
`building_code`. `region`/`eircode_or_postcode` are recorded but unused in v2 (one weather
station per country, as in v1).

**`building_config.general`** — `tabula_building_type` (`SFH`|`TH`|`MFH`|`AB`),
`construction_year`, `conditioned_floor_area_in_m2`, `number_of_apartments`,
`set_heating_temperature_in_celsius`, `set_cooling_temperature_in_celsius`,
`retrofit_status` (`unrenovated`|`usual_refurb`|`advanced_refurb`),
`max_thermal_building_demand_in_watt`, `building_heat_capacity_class`
(`very_light`..`very_heavy`). These map onto the `building` component (§6.2, §6.5).

**`building_config.envelope_details`** — per-element `*_u_value_in_watt_per_m2_per_kelvin`
and `*_area_in_m2` for floor, facade, roof, window, door. These become `building.config`
overrides — a fidelity gain over v1, which could only bump the TABULA refurbishment variant.

**`occupancy_config`** — `residents_count`, `residents_type` (`adult`|`kid`),
`residents_employment_status` (`full_time`|`half_time`|`unemployed`|`education`|`retired`),
`travel_route_set`. The LPG catalogue is keyed by household name, not by a count, so the
translator keeps a residents→household selection table (§6.7). `residents_type`,
`employment_status` and `travel_route_set` are richer than v1's `occupants` count; the
table is extended and unmapped dimensions are flagged (§9).

**`energy_system_config`** — the flat block the openapi defines (`heating_system`,
`water_storage`, `photovoltaics`, `battery_storage`, `solar_thermal_system`, `vehicles`).
**This block does not survive into the output.** It is the source of three kinds of decision
the YAML format makes explicitly: base-file selection (§6.1), config overrides (§6.2) and
group toggles (§6.3). §7 is the field-by-field table.

**`condition_assessment`** — per-element/per-system condition (`good`|`medium`|`bad`).
Not simulated; flagged ignored (§1, §9).

**`selected_grant_schemes`** — grant calculations; not simulated; flagged ignored.

### 3.2 `PackageDefinition` → measures

A package's `measures` array is applied to the inventory before composition when the variant
is `measures` (§8). The openapi `Measure` shape (`type`, `material_id`, `subtype`,
`thickness_mm`, `capacity_kw`, `alternatives`) differs from v1's (`type`, `params`); the
measure application is reworked against it (§8).

### 3.3 `simulationOverrides` (job envelope)

`year`, `secondsPerTimestep`, `postProcessingOptions` move from v1's in-code
`SimulationParameters` construction into the `*.simulation.yaml` file
(`start_date`/`end_date`/`seconds_per_timestep`/`post_processing_options`), which the
translator writes beside the energy-system file (§10).

## 4. The output

Two files, written into the run's result directory:

1. **`renovisor.energy_system.yaml`** — the composed energy system, schema version 3. It is
   a base file (§5) with overrides applied and groups toggled. It carries no `metadata`
   block (that is generator-only; a realized record is produced by the executor).
2. **`renovisor.simulation.yaml`** — the period, resolution and post-processing options,
   built from `simulationOverrides` and the v2 defaults (§10).

The translator composes the `EnergySystemFile` model in code and renders it with
`EnergySystemEmitter.dump(model)`, or loads a base file with `parse_energy_system`,
mutates the model and re-dumps. The recommended approach is **load base → mutate → dump**:
the wiring (`inputs`, `sizing_sources`) lives in the checked-in base files rather than being
reconstructed in Python, so the translator never hand-lists 15 components and 30 connections
(R10). The model types are frozen (`model.py`), so mutation means replacing an entry or a
group, not editing in place.

## 5. Base files per heating system

One checked-in `*.energy_system.yaml` per heating system, under `energy_systems/`, mirroring
the v1 `household_*_building_sizer.py` set. Only `gas_boiler_household.energy_system.yaml`
exists today; authoring the rest is the prerequisite for this spec.

| `heating_system.system` (openapi) | base file | v1 setup it replaces |
|---|---|---|
| `GasHeating` | `gas_boiler_household.energy_system.yaml` (exists) | `household_gas_building_sizer.py` |
| `GasSolarThermal` | `gas_solar_thermal_household.energy_system.yaml` | `household_gas_solar_thermal_building_sizer.py` |
| `OilHeating` | `oil_boiler_household.energy_system.yaml` | `household_oil_building_sizer.py` |
| `HeatPump` | `heatpump_household.energy_system.yaml` | `household_heatpump_building_sizer.py` |
| `HeatPumpSolarThermal` | `heatpump_solar_thermal_household.energy_system.yaml` | `household_heatpump_solar_thermal_building_sizer.py` |
| `ElectricHeating` | `electric_heating_household.energy_system.yaml` | `household_electric_heating_building_sizer.py` |
| `DistrictHeating` | `district_heating_household.energy_system.yaml` | `household_district_heating_building_sizer.py` |
| `PelletHeating` | `pellets_boiler_household.energy_system.yaml` | `household_pellets_building_sizer.py` |
| `WoodChipHeating` | `wood_chips_boiler_household.energy_system.yaml` | `household_wood_chips_building_sizer.py` |
| `HydrogenHeating` | `hydrogen_boiler_household.energy_system.yaml` | `household_hydrogen_boiler_building_sizer.py` |

Each base file is the gas-boiler file's shape: `weather` and `occupancy` with constructors,
a `building` with `constructor: for_tabula_code`, the heating chain (source, controller,
heat distribution, DHW), a `meter`, and `groups` for `pv`, `battery_and_ems`, `solar_thermal`
and `ev` — enabled or disabled as appropriate to that heating system. The boiler preset
encodes the fuel: `GenericBoiler.preset_condensing_gas` / `preset_oil` / `preset_pellets` /
`preset_wood_chips` / `preset_hydrogen`; heat pumps use
`MoreAdvancedHeatPumpHPLib.preset_air_water`; district heating and electric heating use
their own components. The `describe`/`facts` introspection
(`hisim config describe <class>`, `presets_of`/`constructors_of`) is the source of truth for
preset and constructor names.

Solar thermal combined with a primary other than gas/heat pump has no base file (as in v1);
the solar-thermal group is dropped and flagged (§9).

## 6. Composition rules

### 6.1 Base-file selection

Driven by `energy_system_config.heating_system.system` and the presence of
`energy_system_config.solar_thermal_system`:

| `heating_system.system` | `solar_thermal_system` present | base file |
|---|---|---|
| `GasHeating` | no | `gas_boiler_household` |
| `GasHeating` | yes | `gas_solar_thermal_household` |
| `HeatPump` | no | `heatpump_household` |
| `HeatPump` | yes | `heatpump_solar_thermal_household` |
| `OilHeating` / `ElectricHeating` / `DistrictHeating` / `PelletHeating` / `WoodChipHeating` / `HydrogenHeating` | any | the matching boiler/electric/district base file; solar thermal dropped + flagged |

This replaces v1's `_SETUP_BY_PRIMARY` / `_SOLAR_THERMAL_SETUPS`. `WoodChipHeating` and
`PelletHeating` both map to the pellet/wood-chip boiler presets; the mapping report notes
the approximation (v1 mapped `wood`/`solid_fuel` to the pellet setup).

### 6.2 Config overrides

Sparse `config:` overrides applied to named components of the base file, after the preset or
constructor. The override mechanism is the format's own (`configure.py` `_apply_overrides`):
each key is decoded into the field's type, and `AUTO` re-opens a field a preset had pinned.

| Component | Override source | Field |
|---|---|---|
| `building` | `building_config.general` | `absolute_conditioned_floor_area_in_m2` ← `conditioned_floor_area_in_m2`; `number_of_apartments`; `set_heating_temperature_in_celsius`; `set_cooling_temperature_in_celsius`; `max_thermal_building_demand_in_watt`; `building_heat_capacity_class` |
| `building` | `building_config.envelope_details` | `floor_u_value_in_watt_per_m2_per_kelvin`, `floor_area_in_m2`, `facade_u_value_in_watt_per_m2_per_kelvin`, `facade_area_in_m2`, `roof_u_value_in_watt_per_m2_per_kelvin`, `roof_area_in_m2`, `window_u_value_in_watt_per_m2_per_kelvin`, `window_area_in_m2`, `door_u_value_in_watt_per_m2_per_kelvin`, `door_area_in_m2` (all `None` in the base file → derived from the TABULA code; an override replaces the derived value) |
| `building` | TABULA construction | `constructor: for_tabula_code` with `building_code` from §6.5, plus `building_heat_capacity_class` and `heating_reference_temperature_in_celsius` when the request supplies them |
| `weather` | `location.country_code` | `constructor: for_location` with `location` (a `LocationEnum` member) and `data_source` |
| `occupancy` | `occupancy_config` | `constructor: for_household` with `household` (LPG name) and `energy_intensity` |
| `hds` / heat-distribution | `energy_system_config.heating_system.heat_distribution_system` | the distribution type (`Conventional Radiator` / `Floorheating` / `Low Temperature Radiator`) — a config override on the heat-distribution component, or a base-file choice |
| heat source | `energy_system_config.heating_system` | `heatpump_flow_temperature_in_celsius`, `power_in_watt`, `with_dhw_preparation` as config overrides on the heat-source component; `power_in_watt` may be a concrete override or left `AUTO` (sized from `building.heating_load_in_watt`) |
| storage | `energy_system_config.water_storage` | `volume_in_liters` on the `hot_water_storage` / `domestic_hot_water_storage` components |
| PV | `energy_system_config.photovoltaics` | `power_in_watt` (concrete or `AUTO` from `building.conditioned_floor_area_in_m2`), `azimuth_in_degree`, `tilt_in_degree` on the PV component(s) |
| battery | `energy_system_config.battery_storage` | `capacity_in_kwh` on the battery component |
| solar thermal | `energy_system_config.solar_thermal_system` | `collector_area_in_m2` (concrete or `AUTO`), `azimuth_in_degree`, `tilt_in_degree`, `used_for_space_heating`, `used_for_dhw` |

`installation_year`, `remaining_performance_in_percent` (PV), `state_of_health_in_percent`
(battery) have no energy-system home yet; they are flagged approximated/ignored (§9) until
the components gain fields for them.

### 6.3 Group toggles

Presence is a group `enabled` flag, not a scalar:

| openapi presence | group | enabled when |
|---|---|---|
| `energy_system_config.photovoltaics` | `pv` | the block is present and `power_in_watt` ≠ 0 (or always, with `power_in_watt: 0`/`AUTO`) |
| `energy_system_config.battery_storage` | `battery_and_ems` | `capacity_in_kwh` > 0 |
| `energy_system_config.solar_thermal_system` | `solar_thermal` | the block is present |
| `energy_system_config.vehicles.electric_vehicles` | `ev` | the list is non-empty |

Toggling a group off removes its components and every `inputs`/`sizing_sources` entry pointing
at them (the executor's group-expansion rule, R14). A request with no PV leaves the `pv`
group disabled; a request with PV leaves it enabled and overrides `azimuth`/`tilt`/`power`.

### 6.4 Sizing: AUTO vs concrete

The format sizes cross-component facts automatically: a heat source's
`maximal_thermal_power_in_watt` is `AUTO` from `building.heating_load_in_watt` when exactly
one provider exists, and a `sizing_sources` block resolves ambiguity when several do (two PV
strings, a heat pump + backup heater). The translator writes a concrete `config:` override
only when the request gives an explicit size (`power_in_watt`, `capacity_in_kwh`,
`collector_area_in_m2`); otherwise it leaves the base file's `AUTO`/preset sizing alone. This
replaces v1's `share_of_maximum_pv_potential` and the auto-sized battery.

### 6.5 TABULA building-code construction

Unchanged from v1 (`tabula_ie.select_building_code`), now emitted as a constructor argument:

- `tabula_building_type` (`SFH`/`TH`/`MFH`/`AB`) ← openapi `building_config.general.tabula_building_type` (v1 mapped this from `dwellingType`; the openapi gives the TABULA type directly).
- age band ← `construction_year` against the TABULA CSV.
- refurbishment variant `.001`/`.002`/`.003` ← `retrofit_status` (`unrenovated`/`usual_refurb`/`advanced_refurb`), raised by envelope measures (§8).
- country prefix ← `location.country_code`.
- The unusable-TABULA-row workaround (rows without door/window geometry) is gone: the
  `Building` component guards every zero envelope area (hisim-4g9.1), so every band is
  selectable and the exact band is used.

The result is `constructor: {for_tabula_code: {building_code: "IE.N.SFH.04.Gen.ReEx.001.001", …}}`
on the `building` component, replacing v1's `ArcheTypeConfig.building_code`.

### 6.6 Weather location

`location.country_code` → a `WeatherConfig.for_location` constructor call. v1 maps `IE`→
`Dublin` (NSRDB) and `DE`→`Aachen`; the energy-system `for_location` takes a `LocationEnum`
member and a `data_source`. The country→`LocationEnum`/`data_source` table is the v1
`_resolve_weather_location` mapping, retargeted to the constructor arguments. The base file
ships with `preset: standard` (Aachen); the translator replaces it with the constructor when
the country is not the base file's default.

### 6.7 Occupancy (LPG household)

`occupancy_config.residents_count` → one LPG household name via a selection table (v1's
`_LPG_HOUSEHOLD_BY_OCCUPANTS`), emitted as `constructor: {for_household: {household: <name>,
energy_intensity: EnergySaving}}`. The openapi's `residents_type`, `employment_status` and
`travel_route_set` are richer than a count; the table is extended where the LPG catalogue has
a matching profile, and unmapped dimensions are flagged (§9). `travel_route_set` maps to the
constructor's `travel_route_set` argument when the LPG profile carries one.

## 7. Field mapping table (openapi → energy-system YAML)

| openapi field | YAML target | mechanism | v1 equivalent |
|---|---|---|---|
| `location.country_code` | `weather.constructor.for_location` | country→LocationEnum table | `_resolve_weather_location` |
| `building_config.general.tabula_building_type` | `building.constructor.for_tabula_code` (type segment) | `tabula_ie` | `dwellingType`→type |
| `construction_year` | TABULA age band | `tabula_ie` | `constructionYear` |
| `retrofit_status` | TABULA variant `.001/.002/.003` | `tabula_ie` | `envelopeState` |
| `conditioned_floor_area_in_m2` | `building.config.absolute_conditioned_floor_area_in_m2` | override | `floorAreaM2` |
| `number_of_apartments` | `building.config.number_of_apartments` | override | (fixed 1) |
| `set_heating_temperature_in_celsius` / `set_cooling_temperature_in_celsius` | `building.config.set_heating_temperature_in_celsius` / `set_cooling_temperature_in_celsius` | override | `targetTempC` (was unmapped) |
| `max_thermal_building_demand_in_watt` | `building.config.max_thermal_building_demand_in_watt` | override | (none) |
| `building_heat_capacity_class` | `building.constructor.for_tabula_code.building_heat_capacity_class` | constructor arg | (none) |
| `envelope_details.*_u_value_in_watt_per_m2_per_kelvin` / `*_area_in_m2` | `building.config.*` | override | (variant bump only) |
| `occupancy_config` | `occupancy.constructor.for_household` | residents→LPG table | `occupants` |
| `heating_system.system` | base-file selection | table (§6.1) | `_select_setup` |
| `heating_system.heat_distribution_system` | heat-distribution `config` / base file | override | `heating.emitter` |
| `heating_system.heatpump_flow_temperature_in_celsius` / `power_in_watt` / `with_dhw_preparation` | heat-source `config` | override | (none / auto-sized) |
| `water_storage.hot_water_storage.volume_in_liters` / `domestic_hot_water_storage.volume_in_liters` | storage `config.volume_in_liters` | override | (none) |
| `photovoltaics.power_in_watt` / `azimuth_in_degree` / `tilt_in_degree` | `groups.pv` enabled + PV `config` | group + override | `pv.kWp` / `pv.orientation` / roof tilt |
| `battery_storage.capacity_in_kwh` | `groups.battery_and_ems` enabled + battery `config` | group + override | `battery.kWh` (auto-sized) |
| `solar_thermal_system.*` | `groups.solar_thermal` (or solar-thermal base file) | group + override | `solarThermal.mode` |
| `vehicles.electric_vehicles` | `groups.ev` enabled + car/charging-station `config` | group | (none — v1 dropped vehicles) |
| `vehicles.fossil_vehicles` | not simulated (no fossil-vehicle component in the base files) | flagged ignored | (none) |
| `condition_assessment` | not simulated | flagged ignored | (none) |
| `selected_grant_schemes` | not simulated | flagged ignored | (none) |

## 8. Measures application (`--variant measures`)

A package's `measures` are applied to a copy of the inventory before composition, then the
composed file reflects the modified inventory (v1 applies measures to `homeInputs`; v2
applies them to the `HomeInventoryInput`/`PackageDefinition` and composes from the result).
The openapi `Measure` carries `type` plus `material_id`, `subtype`, `thickness_mm`,
`capacity_kw`, `alternatives`.

| `Measure.type` | Effect on the composed energy-system file |
|---|---|
| `heat_pump` | switches the base file to `heatpump_household` (or `heatpump_solar_thermal`); `capacity_kw` → heat-source `config.maximal_thermal_power_in_watt` (concrete) or `AUTO`; flagged approximated until the component honours a requested size. |
| `pv` | enables `groups.pv`; sets PV `config.power_in_watt` (new total, not additional). |
| `battery` | enables `groups.battery_and_ems`; sets battery `config.capacity_in_kwh`. |
| `solar_thermal` | enables `groups.solar_thermal` (or switches to a solar-thermal base file); `collector_area_in_m2` if given, else `AUTO`. |
| `roof_insulation` / `wall_insulation` / `floor_insulation` | sets the matching `building.config.*_u_value_in_watt_per_m2_per_kelvin` override (computed from `material_id` + `thickness_mm`) — a real per-element U-value, replacing v1's variant bump. When the U-value cannot be derived, the measure falls back to raising the TABULA variant and is flagged approximated. |
| `windows` | sets `building.config.window_u_value_in_watt_per_m2_per_kelvin` from the glazing (`subtype`/`alternatives`). |
| `doors` | sets `building.config.door_u_value_in_watt_per_m2_per_kelvin`. |
| `air_sealing` / `ventilation` | no energy-system home yet (the `Building` component has no infiltration/MVHR field); flagged ignored, as in v1. |

Envelope measures no longer collapse into a single variant bump when a per-element U-value
can be computed; the TABULA variant is raised only as the fallback. The mapping report (§9)
records every measure's effect.

## 9. The mapping report

`renovisor_mapping_report.json` is written to the result directory and always uploaded,
as in v1. Its shape adapts to the new output:

```json
{
  "jobId": "abc-123",
  "variant": "measures",
  "translatorVersion": "2.0.0",
  "selectedBaseFile": "heatpump_household.energy_system.yaml",
  "energySystemFile": "renovisor.energy_system.yaml",
  "groups": { "pv": "enabled", "battery_and_ems": "enabled", "solar_thermal": "disabled", "ev": "disabled" },
  "fields": [
    { "path": "energy_system_config.heating_system.system", "status": "used", "note": "base file heatpump_household.energy_system.yaml" },
    { "path": "building_config.envelope_details.facade_u_value_in_watt_per_m2_per_kelvin", "status": "used", "note": "building.config override" },
    { "path": "building_config.general.construction_year", "status": "used", "note": "TABULA code IE.N.SFH.04.Gen.ReEx.001.002" },
    { "path": "energy_system_config.photovoltaics.power_in_watt", "status": "used", "note": "groups.pv enabled, config.power_in_watt=8000" },
    { "path": "occupancy_config.residents_employment_status", "status": "ignored", "note": "not yet supported by the translator (v2)" },
    { "path": "condition_assessment", "status": "ignored", "note": "not simulated; package/cost-layer concern" }
  ]
}
```

`status` ∈ `used` | `approximated` | `defaulted` | `ignored`, as in v1. Every leaf of the
request appears exactly once. The report replaces v1's `selectedSetup`/`moduleConfig` with
`selectedBaseFile`/`energySystemFile`/`groups`.

## 10. The runner

`runner.py` switches from the building-sizer setup to the energy-system executor:

```python
from hisim.energy_system.executor import run_energy_system

actual = run_energy_system(
    energy_system_path,           # the composed renovisor.energy_system.yaml
    simulation_parameters_path,   # the composed renovisor.simulation.yaml
    result_directory=str(result_directory),
)
```

`build_simulation_parameters` is replaced by writing a `renovisor.simulation.yaml` with
`start_date`/`end_date` (from `simulationOverrides.year`, defaulting to a full year),
`seconds_per_timestep` (default 900) and `post_processing_options` (the v2 defaults plus
overrides), then letting `SimulationParametersReader.read` parse it. The defaults remain
`COMPUTE_KPIS`, `COMPUTE_OPEX`, `COMPUTE_CAPEX`, `WRITE_KPIS_TO_JSON`,
`WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER` plus `EXPORT_TO_CSV`. The realized record
(`realized.energy_system.yaml`), the audit and the wire log are written by the executor
before the first time step, alongside the result files the uploader collects.

The `--variant base|measures` CLI flag is retained. `--result-dir`, `--no-upload`,
`--keep-files` and the exit codes (0/2/3/4) are unchanged.

## 11. The REST lifecycle (unchanged)

`uploader.py` and the `__main__.py` pipeline (started/succeeded/failed events, multipart
upload, retries, the mapping report always uploaded) are unchanged. Only the fields the
report carries change (§9). The `started` event's `selectedSetup` becomes `selectedBaseFile`.

## 12. CLI

```
python -m hisim.renovisor run <request.json> --variant {base|measures} [options]
```

Unchanged surface; the request is the openapi `HomeInventoryInput` wrapped in the `job`
envelope. `--no-upload` runs the composition and the simulation without uploading.

## 13. Package layout

```
hisim/renovisor/
  __init__.py        TRANSLATOR_VERSION → 2.0.0
  __main__.py        CLI (unchanged surface); report fields adapted
  energy_system_spec.md   this document
  schema.py          validate the openapi HomeInventoryInput + PackageDefinition
  compose.py         request ──▶ EnergySystemFile model (was mapping.py)
  bases.py           base-file selection + load/mutate/dump (NEW)
  measures.py        apply measures to the inventory copy (reworked to the openapi Measure)
  tabula_ie.py       IE TABULA code construction (unchanged)
  runner.py          write the two YAML files; run_energy_system
  uploader.py        REST upload (unchanged)
  examples/          openapi-shaped example requests
spec.md              the v1 spec (retained for reference until v2 lands)
```

`mapping.py` is renamed `compose.py`; its `TranslationResult` becomes
`{energy_system_file: str, simulation_file: str, selected_base_file: str, groups: dict,
report: MappingReport}`. `bases.py` is new: the heating-system→base-file table and the
load/mutate/dump of the base file. The `ArcheTypeConfig`/`EnergySystemConfig`/
`ModularHouseholdConfig` imports are removed.

## 14. What is deleted / what carries over

**Deleted:** `_SETUP_BY_PRIMARY`/`_SOLAR_THERMAL_SETUPS`; `ArcheTypeConfig`,
`EnergySystemConfig`, `ModularHouseholdConfig` construction; `write_module_config`;
`_AZIMUTH_BY_ORIENTATION` and `_REFURB_VARIANT_BY_ENVELOPE_STATE` as the *only* envelope
representation (per-element U-values now override directly); the
`share_of_maximum_pv_potential`/`use_battery_and_ems` flags.

**Carries over:** `tabula_ie.select_building_code`; the country→weather-location mapping
(retargeted to `for_location`); the residents→LPG-household table (retargeted to
`for_household`); the mapping-report concept and its statuses; the REST lifecycle; the `job`
dispatch envelope.

## 15. Test plan

- `test_renovisor_compose.py` (was `test_renovisor_mapping.py`): for each example request,
  assert the selected base file, the `building` constructor's `building_code`, the `config`
  overrides, the group flags, and the mapping-report entries. Pure — no simulation — like
  v1's mapping tests.
- `test_renovisor_measures.py`: reworked to the openapi `Measure` shape; envelope measures
  set U-value overrides; `heat_pump` switches the base file.
- `test_renovisor_runner.py`: writes the two YAML files and runs
  `run_energy_system` with `--no-upload` against the gas-boiler base file (the cheapest
  smoke test), asserting the realized record reproduces.
- `test_renovisor_end_to_end.py` (`system_setups` marker): full pipeline with `--no-upload`.
- A canonicalisation test that the composed `renovisor.energy_system.yaml` for the gas-boiler
  example round-trips through `parse_energy_system` → `EnergySystemEmitter.dump` unchanged.

## 16. Open decisions

1. **Base-file authoring scope.** Only `gas_boiler_household.energy_system.yaml` exists.
   Authoring the other ~9 base files is the prerequisite and the largest piece of work.
   Start with gas (verify it covers the openapi fields) and heat pump (the most complex),
   then the rest?
2. **Build the model in code vs. load-base-and-mutate.** This spec recommends
   load-base-and-mutate so the wiring lives in the checked-in base files. Building the model
   in code is more unit-testable but duplicates the wiring. Confirm the preference.
3. **Contract break.** Adopt the openapi `HomeInventoryInput` now (breaking the v1
   `homeInputs` contract and all examples/tests), or keep `homeInputs` as the translator
   input and change only the *output* to YAML? The openapi is a `0.3.0-draft`; the v1
   examples/tests are what is green today.
4. **`condition_assessment` / `grants` / `financing` / `schedule`.** Confirm these stay
   outside the translator (RenoVisor package/cost layer) and are only flagged in the report.
5. **Fossil vehicles.** v1 models all vehicles for cost/CO₂; the energy-system base files
   have no fossil-vehicle component. Confirm fossil vehicles are flagged ignored in v2 until
   a component exists, or scope a non-electric-vehicle cost model separately.
6. **`installation_year` / `remaining_performance_in_percent` / `state_of_health_in_percent`.**
   These openapi fields have no energy-system home yet. Confirm they are flagged
   approximated/ignored until the components gain fields for age/condition.
