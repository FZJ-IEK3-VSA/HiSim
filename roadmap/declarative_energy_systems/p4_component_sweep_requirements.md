# P4 — Component sweep — requirements

**Status:** draft · **Date:** 2026-08-27 (D-20 answered and implemented in P2)
**Author(s):** Noah Pflugradt (owner; `[given]`) · assistant (`[proposed]`, survey)
**Reviewers:** HiSim core team
**Parent:** `roadmap/declarative_energy_systems/epic.md` (E1–E8 apply by reference) · **Plan:** `roadmap/declarative_energy_systems/plan.md` §P4 · **Depends on:** P1 accepted; P2 for fixtures; P3 recorded files where they exist (Q-P3.1 answered 2026-08-28: P3 records now, so a batch that lands after the first recording round has its fixtures)
**Companions (the evidence):** `p4_class_survey.md` (every config class: factories, call sites, presets, laws, facts, behaviour, deletions, hazards, and the full five-part text of decisions D-1…D-32) · `preset_naming_supplement.md` (naming rules 1–5 + A1–A3, per-class proposals, conflicts 1–10) · `sizing_fact_inventory.md` (the math per sized field) · `p3_setup_inventory.md` §3 (class usage per setup)

**Tags:** refactoring, migration, behavior-change, compatibility
**Keywords:** presets, AUTO, laws, SIZING_CONTRIBUTIONS, factories, wire format, golden parity, D13, C11, physics change, batches

**What a reviewer must decide here:** (1) the per-class conversion table (§8 R3) as the wire-format registry that EQ1 freezes at P5 — every preset, constructor and fact name; (2) which conversions are **physics changes** and are therefore separate commits with result diffs (§8 R5; the survey found 6, not the 2 the plan knew: C11, Q-P1.8, HDS threshold D-11, PV share recording D-12, solar-thermal area D-7, CHP-controller flip D-4); (3) the gates that touch `hisim/config/` and so break locality E6 by design — three new facts, constructor-argument decoding (§8 R2); (4) the batch order, now by how many recorded setups a class unblocks rather than by family (§8 R7); (5) the 32 owner decisions in §11, most of them delete-vs-convert calls on dead code.

---

## 1. Abstract

After P1–P3 the kernel, the file format, the recorder and nine converted classes exist; the remaining ~70 component config classes still ship 100+ legacy factory classmethods in 79 spellings, setup-side arithmetic, hand-copied cross-component values and dead construction-time singleton keys. P4 converts them class by class — presets and constructors replace factories, laws replace setup-side math, `SIZING_CONTRIBUTIONS` declares the facts — in mechanical, golden-neutral batches, with every result-changing decision isolated into its own commit. This document is the per-class specification of *what* each class becomes, the registry of names that become public wire format, and the list of decisions that must be taken before each batch. It deliberately does not say how a batch is implemented: the converted pilots are the pattern.

## 3. Executive Summary

Surveyed: **88 config classes** in 65 modules; **9 converted** (4 of them still carrying live legacy factories with 44/32/25/21 call sites); **~55 to convert**, **~16 to delete** (dead, duplicate or unbuildable), **~8 exempt** (data tables, non-component configs). Every conversion is byte-identical for the golden fleet except six named physics changes. Three new `SizingContext` facts and one executor fix are prerequisites that touch shared code. The batch order is driven by P3's recorded files: PV (17 instantiations), battery (13), DHW storage (13), buffer storage (12) and the hplib heat pump turn most recorded files from literal blocks into presets. Cost of inaction: the file format stays a demo for 9 classes, RenoVisor's base files stay 300-line literal dumps, and the names in them freeze at P5 without ever having been reviewed.

## 4. Context and Current Situation

**Current behaviour** (counts from `p4_class_survey.md` unless noted).
- Converted and complete: `BuildingConfig`, `HeatDistributionConfig`, `EMSConfig`, `GenericBoilerConfig`. Converted but with a live legacy factory carrying the fleet: `WeatherConfig.get_default` (19 setups + 25 tests; the shipped `for_location` has **0** call sites and cannot serve 7 of the 8 golden setups, which pass a direct weather file), `UtspLpgConnectorConfig.get_default_utsp_connector_config` (19 + 13), `ElectricityMeterConfig.get_electricity_meter_default_config` (16 + 9), `HeatDistributionControllerConfig` (two factories, 21 sites), `GenericBoilerControllerConfig` (four factories).
- Unconverted classes in the recorded setups (P3 inventory §3b): 32 classes; by instantiation count PV 17, battery 13, DHW storage 13, buffer storage 12, hplib heat pump + 2 controllers 4 each, fuel/gas meters 4 each.
- **Facts.** `SizingContext` has 11 fields, six written by the Building — the repository's only provider. Facts the surveys need and that do not exist: `set_heating_threshold_outside_temperature_in_celsius` (six generator controllers copy it from the HDS controller), `roof_area_in_m2` (PV law), `pv_peak_power_in_watt` (battery law). `number_of_residents` exists with neither writer nor reader. The Weather provides nothing; climate facts (inventory §1a) remain future.
- **Setup-side math left** (P3 inventory §2e): 5 expressions plus the pervasive `Information`-object threading; the surveys locate each in a class law.
- **Dead code confirmed.** Of the plan's D13 list, the 6 defective (`generic_battery`, `generic_ev_charger`) and 2 of 3 zombies already left with `obsolete/` (#590); `controller_l1_heatpump` is live but has 0 call sites. New D13 members: `SmartDevice` (raises `KeyError` on construction), `ExtendedControllerSimulation` (reads class attributes of a defaults-free dataclass — unrunnable), `GenericElectrolyzerConfig.get_default_config` (mandatory argument), six H₂/RSOC classes whose only builders read two JSON files **absent from the repository**. Zero-call-site classes: `SimpleHotWaterStorageController`, `HeatPumpHplibControllerL1`, `L1HeatPumpController`, 9 legacy `configuration.py` classes, plus MPC/PID/wind/price signal/`Car` diesel with 0 setup uses.
- **Dead singleton keys.** `WATERMASSFLOWRATEOFHEATGENERATOR`: 2 readers, 0 writers, and the reading branch would raise `UnboundLocalError` if ever taken. Six Building 5R1C keys: readers only in MPC/PID. `LOCATION`: written by the Weather, read by postprocessing (live).
- **Recording defects found by the survey**, i.e. cases where a realized record would not re-execute today: `PVSystemConfig.get_scaled_pv_system` records `share_of_maximum_pv_potential = 1.0` whatever share it applied; `UtspLpgConnector` rewrites its own `data_acquisition_mode` at run time; ~~`UtspLpgConnectorConfig.for_household` ignores its `household` argument in its default mode~~ (**fixed in P2 on 2026-08-27**, review of #592: the constructor takes the mode and derives the profile from the household, refusing what the predefined mode cannot serve — D-20 answered (a)); constructor arguments reach builders undecoded, so `for_location` and `for_household` are uncallable from a file (only `for_tabula_code` works).
- **Convention conflicts** the supplement did not foresee: rating-suffixed presets named after default arguments, not devices (`air_water_8kw` ×2, `standard_5kwh` names a 10 kWh factory); `sizing_option` of the buffer storage is a factory *argument*, not a field, so conflict 4's resolution needs a new field; the CHP controller's four factories do not factor into fuel × buffer (a 42/50 °C flip between axes).

**Stakeholders.** Producers: each class's module (E6). Consumers: energy-system files and their authors (P2), recorded files (P3, re-recorded per batch), `describe`/`facts` CLI and the JSON Schema, the wire-format pin test (EAC6), RenoVisor/building sizer/HPC (P5). Existing: golden suites (8 sizers), the 24 setups, 4 000+ tests.

**Required behaviour:** R1–R8. **Kind of change:** refactoring by default with **named behaviour changes** (R5). **Assumption A1:** each batch is reviewed against the recorded file diff (Q-P3.1) plus golden parity.

## 5. Goals and Non-Goals

**Goals** — G1 every component config has presets/constructors and no legacy factory · G2 every sized value is computed by a class-side law from declared facts; no arithmetic in setups · G3 every name that becomes wire format is listed in this document before it is minted · G4 every result-changing conversion is a separate, diffed commit · G5 dead classes are removed before anyone mints a name for them.
**Non-Goals** — many-cardinality laws (EQ2: no first consumer found in any group) · climate facts beyond D-21 · the runtime half of `SingletonSimRepository` (forecasts, 5R1C coefficients) · template/repeat layer (the car setup's N-car loop stays Python even after D-23) · renaming legacy aggregator port names (P3 Q-P3.2) · retiring `system_setups/` or the v1 JSONs.

## 6. Use Cases

| | Case | Shows |
|---|---|---|
| UC-P4.1 | `energy_systems/household_heatpump.energy_system.yaml` (recorded base, P3) before/after batch B2 | PV + battery blocks (≈60 lines) collapse to `preset: rooftop` / `preset: standard` with ≤3 overrides (`azimuth`, `tilt`, `share_of_maximum_pv_potential`); numbers identical |
| UC-P4.2 | `hisim energy-system describe hisim.components.generic_pv_system.PVSystem` after B2 | preset `rooftop` (D-13 dropped `rooftop_10kw`); sizable `power_in_watt` reading `roof_area_in_m2` with law text; facts provided `pv_peak_power_in_watt` |
| UC-P4.3 | C11 commit | `golden_check.py` fails on 5 sizers with +10.0 % buffer volume; references re-blessed in the same PR with the table from the survey; `basic_household_only_heating` +54 % documented |
| UC-P4.4 | `energy_system_mockup.yaml` / `_mfh.yaml` (P2 UC2/UC3) | the pinned unknown-preset error set shrinks to empty when B2–B5 land; both run end to end (P2 AC-P2.1 handover) |
| UC-P4.5 | New component written from `example_template.py` after B8 | one `sized_field`, one `SIZING_CONTRIBUTIONS`, one preset — appears in a file with no change outside its module (EAC4) |

## 8. Requirements

### R1 — Conversion contract per class `[given; epic E6, P1]`
For every class in §R3 marked *convert*: legacy factories deleted; presets `preset_<name>` / constructors `for_…` per the table; every field the table marks `AUTO` carries a `sized_field` with the quoted law; `SIZING_CONTRIBUTIONS` declares the listed facts; every call site (setups, tests, `hisim/`) moved; fixtures regenerated; the class's recorded files (P3) re-recorded in the same PR. Nothing outside the module changes except call sites and the gates in R2.

### R2 — Gates that touch shared code `[proposed; survey A Gate 0, B Gate B-0, C D-19]`
Own commits, before the batch that needs them, each golden-neutral:
- R2.1 Facts added to `SizingContext`/`Size`: `set_heating_threshold_outside_temperature_in_celsius` (contributed by `HeatDistributionControllerConfig`), `roof_area_in_m2` (Building; value exists as `BuildingInformation.roof_area_in_m2`), `pv_peak_power_in_watt` (PV). Plus D-15's three carrier/fuel facts (contributed by `GenericBoilerConfig`, decided (b)) and D-21's `heating_reference_temperature_in_celsius` (contributed by `WeatherConfig`, decided (c)).
- R2.2 `CHANNELS` declared on `GasMeter`, `FuelMeter`, `HeatingMeter` (own modules).
- R2.3 Executor decodes constructor arguments by annotation (enum, `JsonReference`) — D-19; without it the constructor form of P2 is decorative.
- R2.4 Deletions of dead singleton keys: `WATERMASSFLOWRATEOFHEATGENERATOR` (now); the six 5R1C keys and `LOCATION` per D-32.
- R2.5 D13 sweep: the delete rows of §R3 removed (or moved to the obsolete repository) before their batch.

### R3 — Per-class table (the wire-format registry) `[proposed; p4_class_survey.md]`
Legend: **conv** convert · **del** delete · **done** converted (remaining work in the last column) · **ex** exempt. *Behaviour* **N** neutral, **P** physics change (R5), **?** depends on a decision. Facts use the `SizingContext` names. Full entries with `file:line` evidence: the survey, by class name.

**Heat generators (survey A)**

| Class | Act | Presets / constructors | `AUTO` fields ← facts (law) | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `MoreAdvancedHeatPumpHPLibConfig` | conv | `air_water` | `set_thermal_output_power_in_watt` ← heating_load (copy); `heating_reference_temperature_in_celsius` ← same (copy). **Not** massflow (supplement deviation). | `maximal_thermal_power_in_watt` | N | D-6 |
| `HeatPumpHplibConfig` | del | | `advanced_heat_pump_hplib.py` → `obsolete/` with its controller and tests (D-1); its `Quantity`-typed fields are why | | | D-1 |
| `GenericHeatPumpConfig` | conv | `standard`, `for_device(manufacturer, heat_pump_name)` | — | — | N | |
| `ElectricHeatingConfig` | conv | `standard` | `maximum_electric_power_w` ← heating_load (copy; setup-side today) | `maximal_thermal_power_in_watt` | N | |
| `DistrictHeatingConfig` | conv | `standard` | `connected_load_in_w` ← heating_load (copy; setup-side) | same | N | |
| `AirConditionerConfig` | conv | `standard`, `for_device(manufacturer, model_name)`, `for_building_load(heating_load, heating_reference_temperature)` | — (D-3: the 12-field database search runs in the constructor; no `AUTO`) | — | N | D-3 |
| `SimpleAirConditionerConfig` | conv | `standard` | — | — | N | |
| `IdealizedHeaterConfig` | conv | `standard` | — (setpoint copies would be P) | — | N | |
| `SimpleHeatSourceConfig` | conv | `constant_thermal_power`, `constant_temperature`, `near_surface_brine` | — | — | N | |
| `SolarThermalSystemConfig` | conv | `flat_plate` | `area_m2` ← number_of_apartments (×4) **done 2026-09-11**; the preset is still to come | — | **P** — executed, golden-neutral | D-7 |
| `generic_chp.CHPConfig` | conv | `gas`, `hydrogen` | `p_el`, `p_fuel` ← `Self("p_th")` × per-preset ratio; `p_th` stays a field | (`maximal_thermal_power_in_watt`) | N | |
| `advanced_fuel_cell.CHPConfig` | conv | `hydrogen` | — | — | N | D-5 |
| `CHPConfigAdvanced` | del | | | | | |
| `GenericBoilerConfig` | done | 7 presets | done | done | | |

**Heat-generator controllers (survey A)** — all need R2.1's threshold fact except where noted.

| Class | Act | Presets | `AUTO` fields ← facts | Beh. | Dec. |
|---|---|---|---|---|---|
| `…HPLibControllerSpaceHeatingConfig` | conv | `standard` | `heat_distribution_system_type` (copy), `set_heating_threshold_outside_temperature_in_celsius` (copy) | N | |
| `…HPLibControllerDHWConfig` | conv | `standard` | — | N | |
| `HeatPumpHplibControllerL1Config` | del | | moves with `advanced_heat_pump_hplib.py` (D-1) | | D-1 |
| `GenericHeatPumpControllerConfig` | conv | `standard` | — | N | |
| `ElectricHeatingControllerConfig` | conv | `standard` | `specific_heating_load…` ← heating_load, floor area (ratio — reuse `SPECIFIC_LOAD_LAW`); threshold ← `Self(specific)` step table (reuse `HEATING_THRESHOLD_LAW`); removes a cross-module import | N | |
| `DistrictHeatingControllerConfig` | conv | `standard` | threshold (copy) | N | |
| `AirConditionerControllerConfig`, `SimpleAirConditionerControllerConfig`, `SolarThermalSystemControllerConfig`, `NightSetbackConfig` | conv | `standard` | — | N | |
| `L1CHPControllerConfig` | conv | `gas`, `hydrogen`, `gas_with_buffer`, `hydrogen_with_buffer` | — | N — D-4 reversed 2026-09-11, the 42/50 and 35/31 values are preserved, so no threshold moves | D-4 |
| `L1HeatPumpConfig` (`controller_l1_heatpump`) | ? | (`space_heating`, `buffer`, `dhw`) | — | N | D-2 |
| `GenericBoilerControllerConfig` | done | `modulating`, `on_off` | delete 4 legacy factories; pellet/wood-chip → `on_off` + overrides | N | |
| `GasHeaterConfig`, `GasControllerConfig`, `CHPControllerConfig`, `ExtendedControllerConfig` (+ `advanced_fuel_cell_controller`) | del | | | | D-8 |

**Heat distribution, storages (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `HeatDistributionControllerConfig` | done | `standard` | delete 2 factories (21 sites); `heating_system` plain default until Q-P1.8 | + threshold fact (R2.1) | **P** for 3 setups + 8 tests (16 → 18 °C) | D-11 |
| `HeatDistributionConfig` | done | | nothing left | | | |
| `SimpleHotWaterStorageConfig` | conv | `buffer` (+ `sizing_option: HotWaterStorageSizingEnum` field, D-10) | `volume_heating_water_storage_in_liter` ← **generator** `maximal_thermal_power_in_watt` × k (20/40/50 l/kW) — today the building load | — | **P** (C11: +10 % on 5 golden sizers, +54 % one ungated, up to +72 % MFH) | D-9, D-10, D-17 |
| `SimpleHotWaterStorageControllerConfig` | del | | | | | D-16 |
| `SimpleDHWStorageConfig` | conv | `standard` | volume ← number_of_apartments (250 l × max(apts,1)) | — | N | |
| `SetTemperatureConfig`, `WarmWaterStorageConfig`, `HydrogenStorageConfig` (`configuration.py`), `LoadConfig`, `ElectricityDemandConfig`, `PVConfig` | del/ex | | | | | |
| `HouseholdWarmWaterDemandConfig` | **ex** (live constant table; supplement wrongly lists it for deletion) | | | | | |

**Electricity, EMS, meters (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `PVSystemConfig` | conv | `rooftop` | `power_in_watt` ← roof_area_in_m2 × 0.6 / module area × module power × `Self(share)` (fn, `fields=`) | `pv_peak_power_in_watt` | N for fleet; **P** for share ≠ 1 | D-12, D-13 |
| `BatteryConfig` | conv | `standard` | capacity ← pv_peak_power × 1e-3; inverter ← pv_peak_power × 0.5 (**not** `Self(capacity)` — rounding trap) | — | N | D-14 |
| `WindturbineConfig` | del | | `generic_windturbine.py` → `obsolete/` with its test (D-16); the KPI vocabulary keys on `ComponentType.WINDTURBINE`, so no result moves | | | D-16 |
| `PriceSignalConfig` | del | | `generic_price_signal.py` → `obsolete/` with its test (D-16); superseded by `tariff_provider.py` | | | D-16 |
| `ElectricityMeterConfig` | done | `standard` | delete legacy factory (25 sites) | | N | |
| `GasMeterConfig` | conv | `gas`, `hydrogen` | `gas_loadtype` ← generator carrier (copy, D-15 (b)) | — | N | D-15 |
| `FuelMeterConfig` | conv | `oil`, `pellets`, `wood_chips`, `district_heating` | `fuel_loadtype`, `heating_value_of_fuel_in_kwh_per_liter`, `fuel_density_in_kg_per_m3` ← generator (copy, D-15 (b); `None` for district heating) | — | N | D-15 |
| `HeatingMeterConfig` | conv | `standard` | — | — | N | |
| `EMSConfig` | done | | delete obsolete `strategy` field; align the class-side default feed for the occupancy to weight 999 (its own channel rejects weight 1, so a bare `- occupancy` under the EMS fails EF-29 today — P2 R2.5); legacy-path behaviour check first | | N/? | |
| `MpcControllerConfig`, `PIDControllerConfig` | del | | `controller_mpc.py` and `controller_pid.py` → `obsolete/` with their tests (D-16); 27 of 49 MPC fields are lists — 13 runtime result buffers and 12 forecast inputs, none of which belongs in a file — and the two were the only readers of the Building's 5R1C keys (D-32) | | | D-16, D-32 |
| `SumBuilderConfig`, `TransformerConfig` | conv | `standard` | — | | N | |
| `RandomNumbersConfig` | conv | `standard` | the class stays live (test helper); D-16 deleted only its dead `get_default_config`, so the preset is minted in its batch | | N | D-16 |

**Occupancy, weather, building (survey C)**

| Class | Act | Presets / constructors | Remaining work | Beh. | Dec. |
|---|---|---|---|---|---|
| `WeatherConfig` | done | `standard`, `for_location(…)`, `for_data_file(path, data_source)` (D-18) | delete `get_default` (44 sites) after the two gates: the direct-file constructor (D-18) and argument decoding (D-19) | N | D-18, D-19 |
| `BuildingConfig` | done | `standard`, `for_tabula_code` | + `roof_area_in_m2` fact; the 14 non-parameter post-construction mutations → sparse `config:` overrides (D-22); `heating_reference_temperature` stays a plain default (D-21 (c)) | N / P | D-21, D-22 |
| `UtspLpgConnectorConfig` | done | `standard`, `for_household` | delete legacy factory (32 sites); the 11 sizers' `USE_LOCAL_LPG` + household-list + `cache_dir_path` overrides become `config:`/constructor arguments (`for_household` now takes `data_acquisition_mode`, P2 2026-08-27) | N | ~~D-20~~ |
| `WeatherDataImport` | ex | | not a config; import fails (`wetterdienst`) | | |
| `SmartDeviceConfig` | del | | defective (`KeyError` on construction); module and config → `obsolete/` (D-30) | | D-30 |

**Mobility, H₂ chain, examples (survey C)**

| Class | Act | Presets / constructors | Notes | Beh. | Dec. |
|---|---|---|---|---|---|
| `CarConfig` | done | `for_household` | `Car` built from `(parameters, config)`; the driving profile is handed over through the per-simulation `SimRepository` and the config carries its identity (`household_name`, `car_name`) | N | ~~D-23~~ |
| `CarBatteryConfig` | conv | `standard` | runtime accumulators in the config | N | |
| `ChargingStationConfig` | conv | `for_charging_station_set` (no preset, D-24) | `lower_threshold…` ← `Self(charging_station_set)` × 0.1 | N | D-24 |
| `ElectrolyzerConfig`, `ElectrolyzerControllerConfig` | conv | `standard`, `for_device(electrolyzer_name)` | `read_config` folded in; raise on unknown name | N | D-26 |
| `PTXControllerConfig`, `XTPControllerConfig` | conv | constructor only | `operation_mode` → enum | N | D-27 |
| `FuelCellConfig`, `FuelCellControllerConfig` | conv | `pem` | manufacturer JSON absent, so the table path (`for_device`) goes to `obsolete/` (D-25) | N | D-25 |
| `RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig` | del | | JSON absent, no other builder → `obsolete/` with their tests (D-25) | | D-25 |
| `GenericElectrolyzerConfig` | del | | `generic_electrolyzer.py` → `obsolete/` (D-29 (c)); D-28 moot, so no `Self("max_power")` laws | | D-28, D-29 |
| `L1ElectrolyzerControllerConfig`, `GenericHydrogenStorageConfig` | del | | `controller_l1_electrolyzer.py` and `generic_hydrogen_storage.py` → `obsolete/` with their tests; `controller_l1_chp` drops its optional H₂-storage default connection (D-29) | | D-29 |
| `ElectrolyzerWithStorageConfig`, `ElectrolyzerWithHydrogenStorageConfig` | conv | `standard` | the survivors (D-29 (c)): waste energy and part-load; preset carries the 2.4 kW factory values, nothing derived (D-28) | N | D-28, D-29 |
| `AdvElectrolyzerConfig` (`configuration.py`) | del | | dead third copy → `obsolete/` (D-29) | | D-29 |
| `CSVLoaderConfig` | conv | `for_csv_file(…)` (10 params), no preset | conflict-9 precedent; do first | N | |
| `ExampleComponentConfig` | conv | `standard` | `capacity` ← conditioned_floor_area × 45 (the hidden law) | N | D-31 |
| `ComponentNameConfig` (template) | conv | `standard` | + one `sized_field`; no contribution — a documented comment block instead, since only `SizingContext` fields may be contributed (D-31 deviation) | N | D-31 |
| `ExampleTransformerConfig`, `SimpleStorageConfig` (`thermal`), `SimpleControllerConfig` | conv | `standard` / `thermal` | | N | |

### R4 — Naming `[decided 2026-08-26; Q-P1.9 + A1–A3]`
Names in R3 follow rules 1–5 with A1–A3. Deviations from the supplement are flagged in the survey and decided in §11 (D-5, D-6, D-13, D-14, D-24). A rating suffix is minted only for a real catalogue device; boolean flags are `config:` overrides; `standard` only where one defensible preset exists. Names are free until P5 (EQ1).

### R5 — Behaviour changes are named, separate and diffed `[given; epic E7; plan §P4]`
Each of the following is its own commit with before/after result tables, never bundled with a conversion, and only after its decision: **C11** buffer volume from generator power (D-9); **Q-P1.8** `heating_system` law from construction year/renovation level (needs new Building facts; decided in P1); **D-11** HDS threshold 16 → 18 °C for 3 setups; **D-12** PV share recording; **D-7** solar-thermal area × apartments; **D-4** CHP-controller 42/50 °C normalisation; **D-21** heating reference temperature from the Weather (if taken). Everything else in R3 is byte-identical for every existing setup, and the batch PR proves it (AC-P4.2).

### R6 — Dead code removed before conversion `[proposed; plan D13]`
Classes marked *del* in R3, the zero-call-site factories, and the dead singleton keys are removed in the gate commit(s) of R2.4/R2.5 before their batch; no preset is ever minted for a class that is later deleted.

### R7 — Batch order `[proposed; supersedes plan B1–B8 order]`
Ordered by recorded-setup impact and fact dependency (Building → generator → controller → storage):
- **B1 Legacy-factory removals on converted classes** — Weather (after D-18/D-19), UTSP (after D-20), ElectricityMeter, HDS controller (neutral half; D-11 separately), boiler controller. ~130 call sites, no new names.
- **B2 Electricity** — gates R2.1 (`roof_area`, `pv_peak_power`), R2.2; PV, battery, gas/fuel/heating meters; wind/price per D-16.
- **B3 Storages** — DHW storage; buffer storage after D-9/D-10 (C11 commit separate).
- **B4 Heat generators** — hplib (D-1/D-6), electric, district, generic HP, air conditioners (D-3), heat source, solar thermal (D-7 separate), CHP ×2 (D-5).
- **B5 Generator controllers** — gate R2.1 threshold fact; all controllers of B4; CHP controller (D-4 separate); D-2 delete.
- **B6 Providers** — Building `roof_area` contribution (with B2's gate), D-22 overrides, D-21 (separate, physics); *`german_multi_family_home` clause struck — void by conflict 8*.
- **B7 Mobility and H₂** — after D-23/D-25/D-29; CSVLoader constructor first.
- **B8 Examples, template, `configuration.py` deletion, `describe` review of every converted class**.

### R8 — Per-batch review artefacts `[proposed]`
Each batch PR contains: the R3 rows it implements (unchanged or with the amendment noted), the recorded-file diffs (P3), `golden_check.py` output, the contract-test run, the `describe` output for each converted class, and the list of deleted factories with their former call-site counts.

## 9. Constraints, Invariants and Assumptions

- C-P4.1 `[given]` Golden parity (E7) for every non-R5 commit; R5 commits re-bless with the diff table in the PR.
- C-P4.2 `[proposed; survey]` A field that shapes a component's I/O surface (`with_domestic_hot_water_preparation`, `position_hot_water_storage_in_system`, `fuel`) stays a plain field; setting it in `config:` is legal, but the wiring then depends on a config value — the executor must build before wiring (it does).
- C-P4.3 `[proposed; survey B]` `Self(field)` laws must not read a rounded sibling where the legacy code used the unrounded value (battery inverter); laws read facts.
- C-P4.4 `[proposed; survey C]` `Car` and `SmartDevice` depend on a simulation result, not a catalogue, so no constructor can carry their *payload* (D-23, D-30). D-23 (answered 2026-08-31) shows what a constructor can still do for such a class: carry the payload's **identity** and leave the payload itself to a repository hand-off between the producing component and the consuming one.
- C-P4.5 `[proposed]` Enum-typed sizable fields carry `value_type=` (P2 R3.7); free-text fields over closed sets (`gas_type`, `operating_mode`, `operation_mode`, `building_heat_capacity_class`, `electrolyzer_type`) become enums before P5 freezes them (D-27 for three of them).
- C-P4.6 `[proposed]` Many-cardinality is not needed by any class in R3 (EQ2 confirmed by all three surveys); `Many` stays a raising hook.
- A1 `[proposed]` Batches are reviewed against recorded-file diffs (P3 Q-P3.1 (a), decided 2026-08-28). A batch that lands before its classes have been recorded uses the Python setups' regenerated v1 fixtures instead, and re-records as soon as the file exists.
- A2 `[proposed]` The obsolete repository (#590) is the destination for deletions that may still be wanted.

## 10. Acceptance Criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC-P4.1 | After the last batch, `grep -rn "def get_default\|def get_scaled\|def config_\|def control_\|def read_config" hisim/components` is empty; `presets_of`/`constructors_of` is non-empty for every non-exempt config class; the contract test passes with the exempt list explicit. | R1, R3, R6 |
| AC-P4.2 | Every non-R5 commit: `golden_check.py` green on unchanged references; recorded files differ only in `preset:`/`config:` shape, never in a number. | R1, R5, C-P4.1 |
| AC-P4.3 | Every R5 commit: PR body carries the before/after table per affected setup; references re-blessed in the same PR; the ungated setups' changes are listed. | R5 |
| AC-P4.4 | The wire-format pin test lists exactly the presets, constructors and facts of R3 (as amended); a name not in R3 fails the test. | R3, R4 |
| AC-P4.5 | Gates: the three facts resolve from a Building/HDS-controller/PV in `resolve_all`; `for_location(location: "AACHEN")` and `for_household(...)` are callable from a YAML file; `WATERMASSFLOWRATEOFHEATGENERATOR` no longer exists. | R2 |
| AC-P4.6 | Every *del* class is gone before the batch that would have converted it; no preset name exists for a deleted class in any commit. | R6 |
| AC-P4.7 | P2 UC2/UC3 mockups run end to end with an empty pinned-error set after B2–B5. | R3, P2 AC-P2.1 |
| AC-P4.8 | `describe` output for every converted class reviewed and checked in as a fixture. | R8 |
| AC-P4.9 | A component written from the template appears in an energy system with no change outside its module. | R3 (template), EAC4 |

## 11. Open Questions and Decisions

**Answered.** Q-P1.8 (HDS `heating_system` law) is decided in P1 and executed here. The decisions below are
taken — D-1 and the nine after it executed, the rest waiting for the batch or gate that carries them; every
row is struck from the table, which keeps the question and the option chosen next to each other.

- **D-1** `[answered 2026-09-02, #604]` **(c) retire as a duplicate** — the survey recommended (b), lower it to
  floats and convert. `advanced_heat_pump_hplib` moved to `obsolete/components/advanced_heat_pump_hplib.py` with
  `HeatPumpHplibConfig` and `HeatPumpHplibControllerL1Config`: zero setup instantiations, a sibling
  (`MoreAdvancedHeatPumpHPLibConfig`) that does the same job in plain floats, and the only `Quantity`-typed sizable
  fields in the repository, which the sizing kernel cannot express. The commit opened the `obsolete/` staging area the
  later moves use, and its rationale is the first row of `obsolete/README.md`.
- **D-2** `[answered 2026-09-10]` **(a)** — `controller_l1_heatpump` moves to `obsolete/components/`. No test
  file, no call site anywhere, no energy-manager default connection.
- **D-8** `[answered 2026-09-10]` **(a)** — `advanced_fuel_cell_controller` and its test move to `obsolete/`,
  and the three legacy configurations it was the only user of (`ExtendedControllerConfig`,
  `GasControllerConfig`, `CHPControllerConfig`) move with it, out of the shared
  `hisim/components/configuration.py` into `obsolete/components/configuration_fuel_cell_controller.py`.
  `configuration.py` keeps everything else.
- **D-16** `[answered 2026-09-10]` **beyond the options offered: everything to `obsolete/`, nothing deleted; a class
  sharing a file is moved out into its own file rather than removed with it.** The four components the survey's
  option (a) would have deferred — `controller_mpc`, `controller_pid`, `generic_windturbine`,
  `generic_price_signal` — move now with their four tests, so the group leaves nothing in limbo.
  `SimpleHotWaterStorageController` and its config move out of `simple_water_storage.py` into
  `obsolete/components/simple_hot_water_storage_controller.py`; `WarmWaterStorageConfig`, `LoadConfig`,
  `ElectricityDemandConfig` and `PVConfig` move out of `configuration.py` into
  `obsolete/components/configuration_legacy.py`. `HouseholdWarmWaterDemandConfig` is **live** — the UTSP connector
  and `simple_water_storage.py` read its class constants — and stays; `HydrogenStorageConfig` and
  `AdvElectrolyzerConfig` belong to D-25/D-29 and stay. The two dead factories
  (`RandomNumbersConfig.get_default_config`, `PVSystem.get_default_config`) are **deleted**, not moved: a factory is
  code, not a class, and both have zero callers.
- **D-9 (C11)** `[answered 2026-09-10]` **(b) fix the physics.** The buffer-storage law reads the generator's
  `maximal_thermal_power_in_watt`, not the building's heating load, so the parameter finally means what its name
  says and `sizing_sources` maps to the provider it names. Own commit with result diffs: the five golden sizers
  (`gas`, `oil`, `hydrogen_boiler`, `pellets`, `wood_chips`) are re-blessed at **+10 % storage volume**, the
  heat-pump golden setup is unchanged, and `basic_household_only_heating` (**+54 %**, 155.62 → 240.00 l) is added
  to the week gate **in the same commit** — an ungated 54 % change is exactly the one that must not land unwitnessed.
- **D-11** `[answered 2026-09-10]` **(a) convert, record the diff.** The heat-distribution controller's threshold
  becomes the computed one, 16 → 18 °C for the three setups still on the legacy factory
  (`basic_household_only_heating`, `household_gas_solar_thermal`, `automatic_default_connections`); no
  `fixed_threshold_16c` preset is minted for a value nobody chose. `basic_household_only_heating` is blessed **in
  the same commit**, so all 13 heat-distribution setups agree on one law and one of the three is gated.
- **D-12** `[answered 2026-09-10]` **(a) fix.** The PV preset's law reads `Self("share_of_maximum_pv_potential")`
  and the field records the share actually applied, so a realized record re-executes (EAC2/UC5). Golden-neutral —
  the fleet's share is 1.0 — but every RenoVisor and building-sizer payload with a share below one, which came
  through the *scaled* path and was recorded as 1.0, now changes to what it always meant. The executing commit and
  the RenoVisor documentation say so explicitly; a silent correction of somebody else's stored payload is worse
  than the bug.
- **D-7** `[answered 2026-09-10]` **(a) adopt the law, record the diff.** Solar-thermal collector area becomes
  `4 m² × number_of_apartments`, which is what `household_gas_solar_thermal.py` looks like it meant when it passed
  `area_m2=4` unmultiplied and what its two sizer twins already do. That setup's week golden is re-blessed in the
  same commit; every MFH archetype in it changes.

  **Executed 2026-09-11.** `SolarThermalSystemConfig.area_m2` is a sizable field with the law
  `Size.NUMBER_OF_APARTMENTS * COLLECTOR_AREA_IN_M2_PER_APARTMENT`, the constant named once at module level, and
  both factories default the parameter to `AUTO` — except the manually-calculated-capex one, which turns the area
  into euros and kilograms at construction time and so keeps a concrete default; see the derived-field note below.
  The three setups lose their hand arithmetic and resolve against a `SizingContext` carrying the dwelling count
  they already scale the domestic hot water storage by: `household_gas_solar_thermal` (which passed `4`),
  `household_gas_solar_thermal_building_sizer` and `household_heatpump_solar_thermal_building_sizer` (which passed
  `4 * number_of_apartments`). **The golden did not move.** The week check for `household_gas_solar_thermal` runs
  the setup under its class defaults, where the archetype holds one dwelling and the TABULA default building is a
  single-family house, so the law computes the 4 m² the setup used to hardcode: no re-blessing was needed, and
  the expectation that "every MFH archetype in it changes" does not apply — `scripts/golden_config.json` sweeps
  simulation horizons, not archetypes. What the law does move is the setup's own configuration axis: its
  `two_dwellings` probe column now records an 8 m² collector, so `SolarThermalSystem` joins `DHWStorage` as an
  override in `household_gas_solar_thermal.grouping.yaml` and the probe prose says so. The three flat twins and
  the three grouped twins are re-recorded; every one of them changes only by `area_m2` moving down the config
  block, because a defaulted field has to follow the non-default ones.

  **The derived-field obstacle, reported and not worked around.**
  `get_default_solar_thermal_system_manually_calculated_capex` computes `investment_costs_in_euro = area × 797`
  and `device_co2_footprint_in_kg` from the area. The first is `Self("area_m2") * 797` and the algebra expresses
  it cleanly; the second is affine — `area × K + 108.28` — and `SizingLaw` has `__mul__` but no `__add__`, so
  no `Self(...)` expression can say it. Spelling it would mean a function law plus turning two `Optional[float]`
  fields into optional sizable fields, a wire-format change to the config class for a factory with zero callers
  in the repository. The factory therefore keeps `area_m2: float = 1.5` and its docstring says why. If the class
  gains its `flat_plate` preset later, that is the moment to decide the two capex fields as well.
- **D-4** `[answered 2026-09-10]` **(a) it is a bug.** The CHP controller's 42/50/50/42 `t_min_dhw_in_celsius`
  cross and the 35-versus-31 `t_min_heating_in_celsius` split between the gas and hydrogen buffers are a
  copy-paste asymmetry nobody chose, so they are normalised rather than frozen into the wire format: `gas` and
  `hydrogen` presets with **one shared buffer override**, not one override per fuel. No setup builds this class;
  `tests/test_generic_chp.py` changes and its comment says which numbers moved and why.

  **Reversed 2026-09-11 (b) preserve**, on the first review of the D-4 PR (#683), which questioned normalising
  four thresholds on inference alone: nothing in the tree says the cross is a mistake, and a guessed value is
  worse than an unexplained one. The four `L1CHPControllerConfig` factories keep their 2023 numbers — chp DHW
  42/60; fuel cell DHW 50/60; chp-with-buffer heating 35/40 and DHW 50/60; fuel-cell-with-buffer heating 31/40
  and DHW 42/60 — and the asymmetry is recorded as unexplained and kept rather than guessed. What stays from
  the PR is everything that was not the physics: a pure, parameterised buffer helper; module-private constants
  with the heating-season begin derived from one base value; `fuel_cell_with_buffer` keeping the default name
  `FuelCellController` instead of `CHPController`; a `__post_init__` check that each minimum is below its
  maximum; and literal pins in the tests. The conversion consequence is **four presets** — `gas`, `hydrogen`,
  `gas_with_buffer`, `hydrogen_with_buffer` — not two plus one shared override, because with the values kept a
  "buffer" override is not one thing.
- **D-21** `[answered 2026-09-10]` **(c) plumb the fact, keep the Building's field a plain default.**
  `heating_reference_temperature_in_celsius` becomes a `WeatherConfig` contribution from a per-station DIN 12831
  table (the `LocationEnum` entries already carry the TRY region in their directory names), so the fact exists for
  group A's heat pumps. The Building's field stays a plain `-7.0` default rather than `AUTO`, so the norm heating
  load — and therefore every generator size — does not move: **no result change**. This is the repository's first
  fact with two possible providers, so a district drawing on two weather stations needs a `sizing_sources` line
  (R4.3), and that is the cost the option is accepted with.
- **D-3** `[answered 2026-09-10]` **(a) a constructor, not a law.** `AirConditionerConfig.for_building_load(heating_load,
  heating_reference_temperature)` runs the existing twelve-field database search at build time and returns a complete
  config; no field is `AUTO`, so the kernel stays scalar and no multi-field law is added for a single consumer. Accepted
  cost: the device selection is invisible to the sizing report and to `sizing_sources`, which show the twelve selected
  values with no provider behind them.
- **D-5** `[answered 2026-09-10]` **(b) `hydrogen`.** The fuel-cell `advanced_fuel_cell.CHPConfig` preset is named after
  the variant it ships (its single default sets `gas_type="Hydrogen"`), matching `generic_chp.CHPConfig`'s `gas`/
  `hydrogen` pair. Recorded as a deviation from the supplement's `standard` rule: a reader can tell the two `CHPConfig`
  classes' presets apart at a glance, and the `gas` variant the class's own field anticipates keeps its name free.
- **D-6** `[answered 2026-09-10]` **(a) drop both.** Neither `air_water_8kw` names a catalogue device — 8000 W is a
  default argument on a `model="Generic"` hplib curve fit — so both hplib classes ship `air_water` alone and the four
  affected test call sites pass `set_thermal_output_power_in_watt` as an explicit argument. Two wire names that would
  have implied a device that does not exist are never minted.
- **D-10** `[answered 2026-09-10]` **(a) a recorded `sizing_option` field** — the survey recommended (c), a plain
  numeric `litres_per_kilowatt`. `HotWaterStorageSizingEnum` becomes a field of `SimpleHotWaterStorageConfig`, so its
  five litres-per-kilowatt members enter the wire vocabulary, and the class ships one preset, `buffer`. The volume law
  reads that sibling enum field (`value_type=HotWaterStorageSizingEnum`), which makes it the first law in the
  repository to read a sibling **enum** rather than a number — the realized record then names the law that produced the
  volume instead of only the litres it chose. Hybrid systems with two generators stay out of scope.
- **D-13** `[answered 2026-09-10]` **(b) drop `rooftop_10kw`.** 10 kW is a round default, not a catalogue rating, so
  conflict 3's rule holds and `PVSystemConfig` ships `rooftop` alone. The setups that call the PV default factory with
  no arguments get a `power_in_watt: 10000` override and their recorded twins are re-recorded; today there are
  **three** — `basic_household.py:91`, `default_connections.py:82`, `dynamic_components.py:101` — not the survey's
  four, because `basic_household_with_weather_data_request.py` left with the `wetterdienst` files in #596. The three
  demo setups then carry the magic number in the file rather than in the class.
- **D-14** `[answered 2026-09-10]` **(a) `standard` only.** `BatteryConfig` carries one preset; `standard_5kwh` named a
  10 kWh factory and both of its call sites overrode capacity and inverter power immediately. Capacity and inverter
  power come from the law (`pv_peak_power_in_watt`) or from the caller, never from a rating in a name.
- **D-15** `[answered 2026-09-11]` **(b) copy laws** — the survey recommended (a), plain preset fields revisited when
  `Many` has a consumer. The gas and fuel meters copy the carrier, `heating_value_of_fuel_in_kwh_per_liter` and
  `fuel_density_in_kg_per_m3` from the generator rather than repeating them, which closes the "gas boiler + oil meter"
  foot-gun. The prerequisites are accepted with the option: three new facts contributed by `GenericBoilerConfig`, whose
  `__init__` derivation moves to build time; a `value_type=lt.LoadTypes` codec; and the consistency aggregator for
  `Many`, unimplemented in `hisim/config/laws.py` — and since the meters convert after the boiler, a group-A class
  enters the meter group's scope. The survey's two side facts stand: the presets ship the setups' exact constants (not
  the rounded 9.82), and district heating ships `None` for both.
- **D-17** `[answered 2026-09-11]` **(b) leave both enums** — the survey recommended (a), unify now. The two
  `PositionHotWaterStorageInSystemSetup` types (`simple_water_storage.py`, `heat_distribution_system.py`) stay
  distinct and both spellings freeze at P5. The owner accepts the consequence the survey named: a hand-written file can
  state the same topology twice, and the two statements can contradict each other.
- **D-24** `[answered 2026-09-11]` **(c) constructor only.** `ChargingStationConfig` gets no `standard` preset — the
  rating *is* the identifier (conflict 7), and neither 3.7 kW (the factory default nobody chose) nor 11 kW (what the
  one setup uses) deserves the bare name. `for_charging_station_set` is the only builder, which costs exactly one line
  in the one file that has a car.
- **D-27** `[answered 2026-09-11]` **(b) three per-module enums.** `operation_mode` becomes an enum in each module that
  has one — the PTX controller, the XTP controller and the RSOC battery controller — rather than one shared enum in
  `loadtypes.py`, because the three vocabularies are not the same set. Each codec error then lists that module's own
  members, and C-P4.5's "enums before P5" holds for three more fields.
- **D-28** `[answered 2026-09-11]` **moot by D-29.** The class the question was about, `GenericElectrolyzerConfig`,
  retires. The survivor `ElectrolyzerWithStorageConfig` converts as preset `standard` carrying the 2.4 kW factory
  values (minimum 1.2 kW, 400 W waste energy, 300–5000 Nl/h, 30 bar) — no constructor, nothing derived — so neither the
  three `Self("max_power")` laws nor `for_rated_power` is ever minted.
- **D-31** `[answered 2026-09-11]` **(a) both.** `example_template.py` gains one `sized_field` and one
  `SIZING_CONTRIBUTIONS` entry, so the file every new component author copies demonstrates the mechanism it is supposed
  to teach (EAC4, AC-P4.9); and `example_component`'s `capacity = 45 * 121.2` becomes the real law,
  `45 J/K/m² × conditioned_floor_area_in_m2`, on the fact that already exists. The repository's last unexplained
  literal goes, at the accepted cost that the class's 11 tests now depend on the sizing kernel.

  **Deviation on execution, 2026-09-11 (#688).** The kernel rule surfaced while writing the template's
  contribution: `FactContribution.__post_init__` (`hisim/config/contributions.py`) refuses any fact name that is
  not a `SizingContext` field, because the vocabulary of facts is one Size term per context field. A template
  therefore cannot contribute a template-only fact without adding one to the shared vocabulary — which is the
  opposite of what a template should teach. The owner chose **no `SIZING_CONTRIBUTIONS` entry on the template**:
  in its place a documented comment block giving the exact shape a contribution takes and pointing at the real
  providers (weather, occupancy, building), so the author who needs one copies a working example rather than a
  fact invented for the template. The sized field, the `AUTO` factory and `example_component`'s real law are
  implemented as decided.
- **D-18** `[answered 2026-09-11]` **(b) a second constructor** — the survey recommended (a), two keyword parameters on
  `for_location`. `WeatherConfig` gets `for_data_file(path, data_source)` for the direct-file path seven of the eight
  golden setups take, and `for_location` stays purely catalogue-shaped: two clean identifier spaces instead of one
  builder with two mutually exclusive modes and a `location` argument that means a display string in one of them. The
  cost is one more wire name frozen at P5.
- **D-19** `[answered 2026-09-11]` **(a) the executor decodes constructor arguments by annotation.** Concretely:
  `hisim/energy_system/codec.py` gets a `decode_argument(annotation, value, …)` shared by `config:` blocks and
  constructor calls, with a list case for `List[JsonReference]`; `configure.py`'s `_call_builder` decodes each argument
  against the parameter's real annotation before calling; a bad value is EF-1A at the argument's own key path
  (`components.X.constructor.for_location.location`) with the members list; and the `@constructor` decorator in
  `hisim/config/presets.py` refuses at import time a parameter whose type the codec cannot decode (allowed: scalars,
  enums, dataclasses with `from_dict`, and `Optional`/`Union`/`List` over those). It is a prerequisite commit before
  the batches (R2.3), golden-neutral because no recorded twin uses `constructor:`, and tested by calling all four
  existing constructors from a YAML file. Verified today: `constructor: {for_location: {location: AACHEN}}` fails with
  `'str' object has no attribute 'value'`, while the same mistake inside a `config:` block gets the EF-1A members list.
- **D-22** `[answered 2026-09-11]` **(a) sparse `config:` overrides.** The Building's 14 non-parameter
  post-construction mutations become overrides rather than ten more constructor parameters, and the recorder diffs
  against a fresh preset and omits every field equal to the default — so the golden fleet's twins do not carry the ten
  `null` envelope opt-ins the survey warned the option would otherwise produce.
- **D-26** `[answered 2026-09-11]` **(a) raise, listing the devices.** All nine hydrogen-chain `read_config` readers
  raise on an unknown device name and name the available ones, so the eight that zero-fill today stop building a
  plausible-looking config of zeros. A file that names a device which does not exist fails at build time, as
  everything else in P2's error catalogue does.
- **D-32** `[answered 2026-09-11]` **(a) both key groups go, one commit each.** Postprocessing reads the Weather
  component's `config.location` directly instead of `SingletonDictKeyEnum.LOCATION`; the six Building 5R1C keys go once
  this branch's D-16 move has put `controller_mpc` and `controller_pid` in `obsolete/`, after which they have no reader
  at all. The last construction-time globals leave the Weather and the Building.
- **D-25** `[answered 2026-09-11]` **(b) obsolete the RSOC trio and the fuel-cell table path.** `RsocConfig`,
  `RsocControllerConfig` and `RsocBatteryControllerConfig` move to `obsolete/` with their tests, together with the fuel
  cell's manufacturer-table builders — verified 2026-09-11 that `hisim/inputs/` still holds only
  `electrolyzer_manufacturer_config.json`, so neither missing JSON has reappeared. `FuelCellConfig` and
  `FuelCellControllerConfig` keep preset `pem`, whose hand-typed defaults do build. Consistent with D-16's rule: moved,
  not deleted.
- **D-29** `[answered 2026-09-11]` **(c) keep the `_and_h2_storage` pair** — the survey recommended (a), the other way
  round. `ElectrolyzerWithStorageConfig` and `ElectrolyzerWithHydrogenStorageConfig`
  (`generic_electrolyzer_and_h2_storage.py`) survive as the richer model, the one that has waste energy and part-load
  percentages, and `generic_electrolyzer.py` and `generic_hydrogen_storage.py` move to `obsolete/` with their tests.
  Follow-up decided the same day: `controller_l1_electrolyzer` (`L1GenericElectrolyzerController`, written for the
  retiring electrolyzer) and its test move to `obsolete/` too; `controller_l1_chp`'s optional default connection from
  `generic_hydrogen_storage` is dropped, not re-pointed; and `AdvElectrolyzerConfig` in `configuration.py`, the dead
  third copy, goes to `obsolete/` as well. The live setup `electrolyzer_with_renewables` uses the third electrolyzer,
  `generic_electrolyzer_h2` (the manufacturer-table one), and is untouched.
- **D-30** `[answered 2026-09-11]` **(a) move to `obsolete/`.** `generic_smart_device` and `SmartDeviceConfig` go in
  one commit, before any preset is minted for a component whose `__init__` raises `KeyError: 'utsp_reports'` on every
  construction; §6's `smart_devices_included` copy-law candidate goes with it. Under D-16's rule, moved, not deleted.

**Follow-up defects found on the way, 2026-09-11.** Three defects in already-merged code surfaced while
executing these decisions and belong to none of them. They are recorded in full beside F-1 in
`roadmap/p4_random_findings.md`, which is where this sweep's incidental findings live; the numbers continue
that document's own sequence.

- **F-3** — `controller_l1_chp.calculate_state`'s summer branch deactivates on `t_max_heating_in_celsius`
  (`:495`) where `t_max_dhw_in_celsius` is evidently meant, so in summer the CHP switches off once the DHW
  store passes the heating band's top — 40 °C with a buffer, 20.5 °C without — instead of the 60 °C its own
  config asks for. Found by the #683 review, outside that PR's diff. Own PR with a test showing the wrong
  switch-off; a physics change (R5) behind no recorded result, since no setup builds the class.
- **F-4** — `hisim/cli.py` never calls `load_dotenv()`; only `hisim/hisim_main.py` does, so the installed
  `hisim` console script ignores the repository's `.env` and the `UTSP_URL`/`UTSP_API_KEY` in it. One line in
  `cli.main`.
- **F-5** — `hisim/components/example_template.py` implements no `i_prepare_simulation`, and `Component`'s
  raises `NotImplementedError`, so a component copied from the template fails inside a Simulator before its
  first timestep. `example_component.py` has the same gap. Fixed alongside the D-31 follow-up, which edits
  both files anyway.

The 32 questions below are owner decisions surfaced by the survey, and **all 32 are now answered**. **Each five-part entry (question, context with `file:line` evidence, options with consequences, recommendation) is in `p4_class_survey.md` under the same ID** — kept there because 32 full entries would triple this document; the table gives the question, the option taken and what it blocks. Answers are recorded here as dated decisions and mirrored into R3.

| ID | Question | Recommendation | Blocks |
|---|---|---|---|
| **Delete vs convert** | | | |
| D-1 | ~~`advanced_heat_pump_hplib` (Quantity-typed, 0 setups): convert, lower to floats, or retire as duplicate?~~ | `[answered 2026-09-02, #604]` **(c)** retired as a duplicate into `obsolete/components/` — survey recommended (b); the module opened the `obsolete/` staging area | R3 hplib rows |
| D-2 | ~~`controller_l1_heatpump` (0 call sites): delete or convert?~~ | `[answered 2026-09-10]` **(a)** moved to `obsolete/components/`, not deleted | R3, R6 |
| D-8 | ~~`advanced_fuel_cell_controller` + 3 legacy `configuration.py` configs (unrunnable): obsolete together?~~ | `[answered 2026-09-10]` **(a)** one commit; the three configs move out into their own file under `obsolete/components/` rather than being deleted | R6 |
| D-16 | ~~Storage controller, MPC, PID, wind, price signal, dead factories, 5 `configuration.py` classes~~ | `[answered 2026-09-10]` **beyond the three options: everything to `obsolete/`, nothing deleted** — MPC/PID/wind/price move now rather than being deferred, and a class sharing a file is moved out into its own file. Only the two dead factories are deleted; only `HouseholdWarmWaterDemandConfig` (live) and the D-25/D-29 pair stay in `configuration.py` | R3, R6 |
| D-23 | ~~Car chain not expressible in a file~~ | `[answered 2026-08-31]` **(d), none of the three offered:** convert now by *routing around* `SizingContext` rather than through it. The occupancy publishes its per-car profiles into the per-simulation `SimRepository` in `i_prepare_simulation`; `Car` reads its own there in the same phase and loses its third constructor argument; `CarConfig` gains `household_name`, `car_name` and a `for_household` constructor. The survey's objection stands unamended — a time series can never be a fact — but a config can name one. The N-car loop stays Python. The shared-config aliasing bug is fixed on the way, since each car now builds its own config. | R3 mobility |
| D-25 | ~~Two manufacturer JSONs absent; six H₂/RSOC classes unbuildable~~ | `[answered 2026-09-11]` **(b)** the RSOC trio and the fuel-cell table path move to `obsolete/` with their tests (re-verified 2026-09-11); `FuelCellConfig`/`FuelCellControllerConfig` keep `pem` | R3 H₂, R6 |
| D-29 | ~~Two electrolyzer + two H₂-storage classes for two devices~~ | `[answered 2026-09-11]` **(c)** keep the `_and_h2_storage` pair — survey recommended (a); `generic_electrolyzer`, `generic_hydrogen_storage`, `controller_l1_electrolyzer` and `AdvElectrolyzerConfig` move to `obsolete/`, and `controller_l1_chp`'s H₂-storage default connection is dropped | R3 H₂ |
| D-30 | ~~`generic_smart_device` defective: delete or fix?~~ | `[answered 2026-09-11]` **(a)** `generic_smart_device` and `SmartDeviceConfig` move to `obsolete/` in one commit, with §6's `smart_devices_included` candidate | R6 |
| **Physics changes (R5)** | | | |
| D-9 | ~~**C11**: buffer volume from generator power (+10 % on 5 golden sizers, +54 % one ungated, ≤ +72 % MFH) or bless the load?~~ | `[answered 2026-09-10]` **(b) fix the physics** — the law reads the generator's maximal thermal power; own commit, five golden sizers re-blessed at +10 %, `basic_household_only_heating` (+54 %) added to the week gate in the same commit | R3 buffer, R5 |
| D-11 | ~~HDS controller: 16 → 18 °C for 3 ungated setups, or a `fixed_threshold_16c` preset?~~ | `[answered 2026-09-10]` **(a) convert, record the diff** — no `fixed_threshold_16c`; `basic_household_only_heating` blessed in the same commit | R3, R5 |
| D-12 | ~~PV `share_of_maximum_pv_potential` recorded as 1.0 by the scaled factory: fix, preserve, or delete the field?~~ | `[answered 2026-09-10]` **(a) fix** — the law reads `Self(...)` and the field records the real share; golden-neutral, but RenoVisor payloads with a share below one change to what they meant, stated in the commit and the RenoVisor docs | R3 PV, R5 |
| D-7 | ~~Solar-thermal `area_m2 = 4 × apartments` law (one setup passes 4 unmultiplied)~~ | `[answered 2026-09-10]` **(a) adopt, record the diff** — executed 2026-09-11; the golden did not move, the setup's `two_dwellings` probe did | R3, R5 |
| D-4 | ~~CHP controller 42/50 °C flip across axes: bug or preserve?~~ | `[answered 2026-09-10; reversed 2026-09-11 after the #683 review]` **(b) preserve** — the four factories keep their 2023 values and the asymmetry is recorded as unexplained rather than guessed, so the class converts as **four** presets (`gas`, `hydrogen`, `gas_with_buffer`, `hydrogen_with_buffer`), not two plus a shared buffer override; the buffer helper, the module-private constants, the `FuelCellController` name and the `__post_init__` min<max check stay from the PR. No result change | R3, R5 |
| D-21 | ~~`heating_reference_temperature` from the Weather in B6 (physics), defer, or plumb the fact only?~~ | `[answered 2026-09-10]` **(c) plumb the fact** from a per-station DIN 12831 table, Building's field stays a plain -7.0 default — no result change; the first two-provider fact, so two-station districts need a `sizing_sources` line | R2.1, R5 |
| **Naming / shape** | | | |
| D-3 | ~~Air conditioner's 12-field database selection: constructor, multi-field law, or freeze the device?~~ | `[answered 2026-09-10]` **(a)** constructor `for_building_load(...)` runs the database search at build time; no `AUTO` field, and the selection is invisible to `sizing_sources` | R3 |
| D-5 | ~~`advanced_fuel_cell.CHPConfig`: `standard` or `hydrogen`?~~ | `[answered 2026-09-10]` **(b)** `hydrogen`, a recorded deviation from the supplement's `standard` rule | R3, R4 |
| D-6 | ~~Drop the two `air_water_8kw` presets (default arguments, not devices)?~~ | `[answered 2026-09-10]` **(a)** drop both; the four tests pass `set_thermal_output_power_in_watt` explicitly | R3, R4 |
| D-10 | ~~Buffer l/kW factor: add `sizing_option` field, five presets, or a numeric `litres_per_kilowatt` field?~~ | `[answered 2026-09-10]` **(a)** a recorded `sizing_option` enum field (five members into the wire vocabulary) and one preset `buffer`; the law reads the sibling enum, a first — survey recommended (c) | R3 |
| D-13 | ~~Keep `rooftop_10kw` (four setups depend on the default)?~~ | `[answered 2026-09-10]` **(b)** drop; the three setups that still call the factory bare get a `power_in_watt: 10000` override and are re-recorded | R3, R4 |
| D-14 | ~~`BatteryConfig` rating preset (`standard_5kwh` names a 10 kWh factory)~~ | `[answered 2026-09-10]` **(a)** `standard` only; capacity and inverter power come from the law or the caller | R3, R4 |
| D-15 | ~~Meters copy carrier and fuel constants from the generator (3 new facts + aggregator) or plain preset fields?~~ | `[answered 2026-09-11]` **(b)** copy laws — survey recommended (a); accepts three `GenericBoilerConfig` facts, a `LoadTypes` codec and the `Many` consistency aggregator, so a group-A class enters the meter group | R3 meters |
| D-17 | ~~Unify the two `PositionHotWaterStorageInSystemSetup` enums before P5?~~ | `[answered 2026-09-11]` **(b)** leave both — survey recommended (a); both spellings freeze at P5 and a file may state the topology twice | R3, C-P4.5 |
| D-24 | ~~`ChargingStationConfig`: `standard` = 3.7 kW, 11 kW, or constructor only?~~ | `[answered 2026-09-11]` **(c)** no `standard`; `for_charging_station_set` only | R3 |
| D-27 | ~~`operation_mode: str` ×3 → one shared enum, three enums, or strings?~~ | `[answered 2026-09-11]` **(b)** three per-module enums (PTX controller, XTP controller, RSOC battery controller) | C-P4.5 |
| D-28 | ~~`GenericElectrolyzerConfig`: `standard` + `Self` laws, constructor, or delete?~~ | `[answered 2026-09-11]` **moot by D-29** — the survivor `ElectrolyzerWithStorageConfig` ships preset `standard` with the 2.4 kW factory values; no constructor, nothing derived | R3 |
| D-31 | ~~Template: add a `sized_field` + contribution; turn `example_component`'s `45 × 121.2` into the real law?~~ | `[answered 2026-09-11]` **(a)** both; the 11 example tests then depend on the sizing kernel. **Deviation on execution (#688):** `FactContribution.__post_init__` admits only `SizingContext` fields, so the template gets no `SIZING_CONTRIBUTIONS` entry — a documented comment block with the contribution's exact shape points at the real providers instead; the sized field, the `AUTO` factory and the example's real law ship as decided | R3, AC-P4.9 |
| **Providers and gates** | | | |
| D-18 | ~~`for_location` lacks the direct-file parameters 7 golden setups need~~ | `[answered 2026-09-11]` **(b)** a second constructor `for_data_file(path, data_source)`; `for_location` stays catalogue-shaped — survey recommended (a); one more wire name frozen at P5 | B1 Weather, R3 |
| D-19 | ~~Constructor arguments undecoded — executor fix, widen signatures, or leave constructors Python-only?~~ | `[answered 2026-09-11]` **(a)** `codec.decode_argument` shared by `config:` and `constructor:`, decoded in `_call_builder`, EF-1A at the argument's key path, and `@constructor` refuses undecodable parameter types at import | R2.3, B1 |
| D-20 | ~~`for_household` ignores its argument in the predefined-profile mode~~ | `[answered 2026-08-27, review of #592]` **(a)** implemented in P2: `data_acquisition_mode` parameter, profile derived from the household, refusal listing the shipped households and the computing modes | B1 UTSP |
| D-22 | ~~Building's 20-field post-construction mutation: `config:` overrides, wider constructor, or `for_measured_envelope`?~~ | `[answered 2026-09-11]` **(a)** the 14 non-parameters become sparse `config:` overrides; the recorder diffs against a fresh preset, so no twin carries ten nulls | R3 Building, P3 recorder |
| D-26 | ~~Five `read_config` readers zero-fill on unknown device: raise everywhere?~~ | `[answered 2026-09-11]` **(a)** all nine readers raise, listing the available device names | R3 H₂ |
| D-32 | ~~Delete the `LOCATION` key and the six 5R1C keys?~~ | `[answered 2026-09-11]` **(a)** both, one commit each: postprocessing reads the Weather's `config.location`; the six 5R1C keys follow D-16's MPC/PID move, after which they have no reader | R2.4 |

## 12. Glossary

See the epic. P4-specific: **batch** — one mechanical PR converting a family of classes under R1; **gate** — an own commit that must precede a batch (R2, R6); **physics change** — a conversion that changes any existing setup's numbers (R5); **legacy factory** — a `get_default_*`/`get_scaled_*`/`config_*`/`control_*`/`read_config` classmethod replaced by a preset or constructor; **wire-format registry** — R3, the list of names EQ1 freezes at P5.
