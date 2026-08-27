# P4 — Component sweep — requirements

**Status:** draft · **Date:** 2026-08-27 (D-20 answered and implemented in P2)
**Author(s):** Noah Pflugradt (owner; `[given]`) · assistant (`[proposed]`, survey)
**Reviewers:** HiSim core team
**Parent:** `roadmap/declarative_energy_systems/epic.md` (E1–E8 apply by reference) · **Plan:** `roadmap/declarative_energy_systems/plan.md` §P4 · **Depends on:** P1 accepted; P2 for fixtures; P3 recorded files where they exist (Q-P3.1)
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
**Non-Goals** — many-cardinality laws (EQ2: no first consumer found in any group) · climate facts beyond D-21 · the runtime half of `SingletonSimRepository` (forecasts, 5R1C coefficients) · template/repeat layer (cars: D-23) · renaming legacy aggregator port names (P3 Q-P3.2) · retiring `system_setups/` or the v1 JSONs.

## 6. Use Cases

| | Case | Shows |
|---|---|---|
| UC-P4.1 | `energy_systems/household_heatpump.energy_system.yaml` (recorded base, P3) before/after batch B2 | PV + battery blocks (≈60 lines) collapse to `preset: rooftop` / `preset: standard` with ≤3 overrides (`azimuth`, `tilt`, `share_of_maximum_pv_potential`); numbers identical |
| UC-P4.2 | `hisim energy-system describe hisim.components.generic_pv_system.PVSystem` after B2 | presets `rooftop`, `rooftop_10kw`; sizable `power_in_watt` reading `roof_area_in_m2` with law text; facts provided `pv_peak_power_in_watt` |
| UC-P4.3 | C11 commit | `golden_check.py` fails on 5 sizers with +10.0 % buffer volume; references re-blessed in the same PR with the table from the survey; `basic_household_only_heating` +54 % documented |
| UC-P4.4 | `energy_system_mockup.yaml` / `_mfh.yaml` (P2 UC2/UC3) | the pinned unknown-preset error set shrinks to empty when B2–B5 land; both run end to end (P2 AC-P2.1 handover) |
| UC-P4.5 | New component written from `example_template.py` after B8 | one `sized_field`, one `SIZING_CONTRIBUTIONS`, one preset — appears in a file with no change outside its module (EAC4) |

## 8. Requirements

### R1 — Conversion contract per class `[given; epic E6, P1]`
For every class in §R3 marked *convert*: legacy factories deleted; presets `preset_<name>` / constructors `for_…` per the table; every field the table marks `AUTO` carries a `sized_field` with the quoted law; `SIZING_CONTRIBUTIONS` declares the listed facts; every call site (setups, tests, `hisim/`) moved; fixtures regenerated; the class's recorded files (P3) re-recorded in the same PR. Nothing outside the module changes except call sites and the gates in R2.

### R2 — Gates that touch shared code `[proposed; survey A Gate 0, B Gate B-0, C D-19]`
Own commits, before the batch that needs them, each golden-neutral:
- R2.1 Facts added to `SizingContext`/`Size`: `set_heating_threshold_outside_temperature_in_celsius` (contributed by `HeatDistributionControllerConfig`), `roof_area_in_m2` (Building; value exists as `BuildingInformation.roof_area_in_m2`), `pv_peak_power_in_watt` (PV). Plus D-15/D-21 facts if decided.
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
| `HeatPumpHplibConfig` | ? | `air_water` | as above, but `Quantity`-typed fields | same | N | D-1 |
| `GenericHeatPumpConfig` | conv | `standard`, `for_device(manufacturer, heat_pump_name)` | — | — | N | |
| `ElectricHeatingConfig` | conv | `standard` | `maximum_electric_power_w` ← heating_load (copy; setup-side today) | `maximal_thermal_power_in_watt` | N | |
| `DistrictHeatingConfig` | conv | `standard` | `connected_load_in_w` ← heating_load (copy; setup-side) | same | N | |
| `AirConditionerConfig` | ? | `standard` (= auto-selecting), `for_device(manufacturer, model_name)` | 12-field database selection ← heating_load, heating_reference_temperature (fn) — not one-law-per-field | — | N | D-3 |
| `SimpleAirConditionerConfig` | conv | `standard` | — | — | N | |
| `IdealizedHeaterConfig` | conv | `standard` | — (setpoint copies would be P) | — | N | |
| `SimpleHeatSourceConfig` | conv | `constant_thermal_power`, `constant_temperature`, `near_surface_brine` | — | — | N | |
| `SolarThermalSystemConfig` | conv | `flat_plate` | `area_m2` ← number_of_apartments (×4; setup-side) | — | **P** (one setup passes 4 unmultiplied) | D-7 |
| `generic_chp.CHPConfig` | conv | `gas`, `hydrogen` | `p_el`, `p_fuel` ← `Self("p_th")` × per-preset ratio; `p_th` stays a field | (`maximal_thermal_power_in_watt`) | N | |
| `advanced_fuel_cell.CHPConfig` | conv | `hydrogen` (or `standard`) | — | — | N | D-5 |
| `CHPConfigAdvanced` | del | | | | | |
| `GenericBoilerConfig` | done | 7 presets | done | done | | |

**Heat-generator controllers (survey A)** — all need R2.1's threshold fact except where noted.

| Class | Act | Presets | `AUTO` fields ← facts | Beh. | Dec. |
|---|---|---|---|---|---|
| `…HPLibControllerSpaceHeatingConfig` | conv | `standard` | `heat_distribution_system_type` (copy), `set_heating_threshold_outside_temperature_in_celsius` (copy) | N | |
| `…HPLibControllerDHWConfig` | conv | `standard` | — | N | |
| `HeatPumpHplibControllerL1Config` | ? | `standard` | as SH controller | N | D-1 |
| `GenericHeatPumpControllerConfig` | conv | `standard` | — | N | |
| `ElectricHeatingControllerConfig` | conv | `standard` | `specific_heating_load…` ← heating_load, floor area (ratio — reuse `SPECIFIC_LOAD_LAW`); threshold ← `Self(specific)` step table (reuse `HEATING_THRESHOLD_LAW`); removes a cross-module import | N | |
| `DistrictHeatingControllerConfig` | conv | `standard` | threshold (copy) | N | |
| `AirConditionerControllerConfig`, `SimpleAirConditionerControllerConfig`, `SolarThermalSystemControllerConfig`, `NightSetbackConfig` | conv | `standard` | — | N | |
| `L1CHPControllerConfig` | conv | `gas`, `hydrogen` (+ buffer overrides) | — | **P** if the 42/50 flip is normalised | D-4 |
| `L1HeatPumpConfig` (`controller_l1_heatpump`) | ? | (`space_heating`, `buffer`, `dhw`) | — | N | D-2 |
| `GenericBoilerControllerConfig` | done | `modulating`, `on_off` | delete 4 legacy factories; pellet/wood-chip → `on_off` + overrides | N | |
| `GasHeaterConfig`, `GasControllerConfig`, `CHPControllerConfig`, `ExtendedControllerConfig` (+ `advanced_fuel_cell_controller`) | del | | | | D-8 |

**Heat distribution, storages (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `HeatDistributionControllerConfig` | done | `standard` | delete 2 factories (21 sites); `heating_system` plain default until Q-P1.8 | + threshold fact (R2.1) | **P** for 3 setups + 8 tests (16 → 18 °C) | D-11 |
| `HeatDistributionConfig` | done | | nothing left | | | |
| `SimpleHotWaterStorageConfig` | conv | `buffer` (+ D-10 field) | `volume_heating_water_storage_in_liter` ← **generator** `maximal_thermal_power_in_watt` × k (20/40/50 l/kW) — today the building load | — | **P** (C11: +10 % on 5 golden sizers, +54 % one ungated, up to +72 % MFH) | D-9, D-10, D-17 |
| `SimpleHotWaterStorageControllerConfig` | del | | | | | D-16 |
| `SimpleDHWStorageConfig` | conv | `standard` | volume ← number_of_apartments (250 l × max(apts,1)) | — | N | |
| `SetTemperatureConfig`, `WarmWaterStorageConfig`, `HydrogenStorageConfig` (`configuration.py`), `LoadConfig`, `ElectricityDemandConfig`, `PVConfig` | del/ex | | | | | |
| `HouseholdWarmWaterDemandConfig` | **ex** (live constant table; supplement wrongly lists it for deletion) | | | | | |

**Electricity, EMS, meters (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `PVSystemConfig` | conv | `rooftop`, `rooftop_10kw`? | `power_in_watt` ← roof_area_in_m2 × 0.6 / module area × module power × `Self(share)` (fn, `fields=`) | `pv_peak_power_in_watt` | N for fleet; **P** for share ≠ 1 | D-12, D-13 |
| `BatteryConfig` | conv | `standard` | capacity ← pv_peak_power × 1e-3; inverter ← pv_peak_power × 0.5 (**not** `Self(capacity)` — rounding trap) | — | N | D-14 |
| `WindturbineConfig` | ? | `standard`, `for_turbine_type` | — | — | N | D-16 |
| `PriceSignalConfig` | ? | `standard` | — | — | N | D-16 |
| `ElectricityMeterConfig` | done | `standard` | delete legacy factory (25 sites) | | N | |
| `GasMeterConfig` | conv | `gas`, `hydrogen` | — (carrier copy needs D-15) | — | N | D-15 |
| `FuelMeterConfig` | conv | `oil`, `pellets`, `wood_chips`, `district_heating` | — (fuel constants copy needs D-15) | — | N | D-15 |
| `HeatingMeterConfig` | conv | `standard` | — | — | N | |
| `EMSConfig` | done | | delete obsolete `strategy` field; align the class-side default feed for the occupancy to weight 999 (its own channel rejects weight 1, so a bare `- occupancy` under the EMS fails EF-29 today — P2 R2.5); legacy-path behaviour check first | | N/? | |
| `MpcControllerConfig`, `PIDControllerConfig` | ? | | 31 of 49 MPC fields are runtime buffers | | | D-16 |
| `SumBuilderConfig`, `TransformerConfig` | conv | `standard` | — | | N | |
| `RandomNumbersConfig` | ? | | `timesteps` is a simulation parameter | | | D-16 |

**Occupancy, weather, building (survey C)**

| Class | Act | Presets / constructors | Remaining work | Beh. | Dec. |
|---|---|---|---|---|---|
| `WeatherConfig` | done | `standard`, `for_location(…)` | delete `get_default` (44 sites) — blocked: direct-file parameters, string locations, undecoded arguments | N | D-18, D-19 |
| `BuildingConfig` | done | `standard`, `for_tabula_code` | + `roof_area_in_m2` fact; 20-field post-construction mutation → overrides or constructor; `heating_reference_temperature` from Weather? | N / P | D-21, D-22 |
| `UtspLpgConnectorConfig` | done | `standard`, `for_household` | delete legacy factory (32 sites); the 11 sizers' `USE_LOCAL_LPG` + household-list + `cache_dir_path` overrides become `config:`/constructor arguments (`for_household` now takes `data_acquisition_mode`, P2 2026-08-27) | N | ~~D-20~~ |
| `WeatherDataImport` | ex | | not a config; import fails (`wetterdienst`) | | |
| `SmartDeviceConfig` | del | | defective (`KeyError` on construction) | | D-30 |

**Mobility, H₂ chain, examples (survey C)**

| Class | Act | Presets / constructors | Notes | Beh. | Dec. |
|---|---|---|---|---|---|
| `CarConfig` | ? | `electric`, `diesel` | `Car` needs a third constructor argument the executor cannot pass | N | D-23 |
| `CarBatteryConfig` | conv | `standard` | runtime accumulators in the config | N | |
| `ChargingStationConfig` | conv | `for_charging_station_set` (+ `standard`?) | `lower_threshold…` ← `Self(charging_station_set)` × 0.1 | N/? | D-24 |
| `ElectrolyzerConfig`, `ElectrolyzerControllerConfig` | conv | `standard`, `for_device(electrolyzer_name)` | `read_config` folded in; raise on unknown name | N | D-26 |
| `PTXControllerConfig`, `XTPControllerConfig` | conv | constructor only | `operation_mode` → enum | N | D-27 |
| `FuelCellConfig`, `FuelCellControllerConfig` | ? | `pem` (+ `for_device`) | manufacturer JSON absent | N | D-25 |
| `RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig` | ? | constructor only | JSON absent; no other builder | | D-25 |
| `GenericElectrolyzerConfig` | ? | `standard` | 3 × `Self("max_power")` laws | N | D-28, D-29 |
| `L1ElectrolyzerControllerConfig`, `GenericHydrogenStorageConfig` | conv | `standard` | unit suffixes missing | N | |
| `ElectrolyzerWithStorageConfig`, `ElectrolyzerWithHydrogenStorageConfig`, `AdvElectrolyzerConfig` | ? | | duplicates | | D-29 |
| `CSVLoaderConfig` | conv | `for_csv_file(…)` (10 params), no preset | conflict-9 precedent; do first | N | |
| `ExampleComponentConfig` | conv | `standard` | `capacity` ← conditioned_floor_area × 45 (the hidden law) | N | D-31 |
| `ComponentNameConfig` (template) | conv | `standard` | + one `sized_field`, one contribution | N | D-31 |
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
- C-P4.4 `[proposed; survey C]` `Car`, `SmartDevice` depend on a simulation result, not a catalogue; no constructor can express them (D-23, D-30).
- C-P4.5 `[proposed]` Enum-typed sizable fields carry `value_type=` (P2 R3.7); free-text fields over closed sets (`gas_type`, `operating_mode`, `operation_mode`, `building_heat_capacity_class`, `electrolyzer_type`) become enums before P5 freezes them (D-27 for three of them).
- C-P4.6 `[proposed]` Many-cardinality is not needed by any class in R3 (EQ2 confirmed by all three surveys); `Many` stays a raising hook.
- A1 `[proposed]` Batches are reviewed against recorded-file diffs (P3 Q-P3.1 (a)). If P3 lands later, the Python setups' regenerated v1 fixtures serve instead.
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

**Answered** — none yet. Q-P1.8 (HDS `heating_system` law) is decided in P1 and executed here.

The 32 questions below are owner decisions surfaced by the survey. **Each five-part entry (question, context with `file:line` evidence, options with consequences, recommendation) is in `p4_class_survey.md` under the same ID** — kept there because 32 full entries would triple this document; the table gives the question, the author's recommendation and what it blocks. Answers are recorded here as dated decisions and mirrored into R3.

| ID | Question | Recommendation | Blocks |
|---|---|---|---|
| **Delete vs convert** | | | |
| D-1 | `advanced_heat_pump_hplib` (Quantity-typed, 0 setups): convert, lower to floats, or retire as duplicate? | (b) lower to floats, then convert like its sibling | R3 hplib rows |
| D-2 | `controller_l1_heatpump` (0 call sites): delete or convert? | (a) delete | R3, R6 |
| D-8 | `advanced_fuel_cell_controller` + 3 legacy `configuration.py` configs (unrunnable): obsolete together? | (a) yes, one commit | R6 |
| D-16 | Storage controller, MPC, PID, wind, price signal, dead factories, 5 `configuration.py` classes | (a) delete storage controller, `configuration.py` five and dead factories now; defer MPC/PID/wind/price to the runtime-SimRepository redesign | R3, R6 |
| D-23 | Car chain not expressible in a file: convert configs anyway, move the profile into `CarConfig`, or defer? | (c) defer to P5/R5; fix the shared-config aliasing bug only | R3 mobility |
| D-25 | Two manufacturer JSONs absent; six H₂/RSOC classes unbuildable | (b) obsolete the RSOC trio and the fuel-cell table path; keep `pem` presets | R3 H₂, R6 |
| D-29 | Two electrolyzer + two H₂-storage classes for two devices | (a) keep `generic_*`, obsolete `generic_electrolyzer_and_h2_storage` + `AdvElectrolyzerConfig` | R3 H₂ |
| D-30 | `generic_smart_device` defective: delete or fix? | (a) delete | R6 |
| **Physics changes (R5)** | | | |
| D-9 | **C11**: buffer volume from generator power (+10 % on 5 golden sizers, +54 % one ungated, ≤ +72 % MFH) or bless the load? | (b) fix — the only option under which `sizing_sources` means what it says | R3 buffer, R5 |
| D-11 | HDS controller: 16 → 18 °C for 3 ungated setups, or a `fixed_threshold_16c` preset? | (a) convert, record the diff, add one of the three to the golden fleet | R3, R5 |
| D-12 | PV `share_of_maximum_pv_potential` recorded as 1.0 by the scaled factory: fix, preserve, or delete the field? | (a) fix — records must re-execute | R3 PV, R5 |
| D-7 | Solar-thermal `area_m2 = 4 × apartments` law (one setup passes 4 unmultiplied) | (a) adopt, diff | R3, R5 |
| D-4 | CHP controller 42/50 °C flip across axes: bug or preserve? | (a) bug — normalise, `gas`/`hydrogen` + clean buffer overrides | R3, R5 |
| D-21 | `heating_reference_temperature` from the Weather in B6 (physics), defer, or plumb the fact only? | (c) plumb the fact, keep the Building's field a default | R2.1, R5 |
| **Naming / shape** | | | |
| D-3 | Air conditioner's 12-field database selection: constructor, multi-field law, or freeze the device? | (a) constructor `for_building_load(...)`, no `AUTO` | R3 |
| D-5 | `advanced_fuel_cell.CHPConfig`: `standard` or `hydrogen`? | (b) `hydrogen` | R3, R4 |
| D-6 | Drop the two `air_water_8kw` presets (default arguments, not devices)? | (a) drop | R3, R4 |
| D-10 | Buffer l/kW factor: add `sizing_option` field, five presets, or a numeric `litres_per_kilowatt` field? | (c) numeric field — the record shows the number used | R3 |
| D-13 | Keep `rooftop_10kw` (four setups depend on the default)? | (b) drop; four one-line overrides | R3, R4 |
| D-14 | `BatteryConfig` rating preset (`standard_5kwh` names a 10 kWh factory) | (a) `standard` only | R3, R4 |
| D-15 | Meters copy carrier and fuel constants from the generator (3 new facts + aggregator) or plain preset fields? | (a) plain fields in B2; revisit when `Many` has a consumer | R3 meters |
| D-17 | Unify the two `PositionHotWaterStorageInSystemSetup` enums before P5? | (a) unify now | R3, C-P4.5 |
| D-24 | `ChargingStationConfig`: `standard` = 3.7 kW, 11 kW, or constructor only? | (c) constructor only | R3 |
| D-27 | `operation_mode: str` ×3 → one shared enum, three enums, or strings? | (b) three per-module enums | C-P4.5 |
| D-28 | `GenericElectrolyzerConfig`: `standard` + `Self` laws, constructor, or delete? | (a) if D-29 keeps it | R3 |
| D-31 | Template: add a `sized_field` + contribution; turn `example_component`'s `45 × 121.2` into the real law? | (a) both | R3, AC-P4.9 |
| **Providers and gates** | | | |
| D-18 | `for_location` lacks the direct-file parameters 7 golden setups need | (a) add the two keyword parameters | B1 Weather, R3 |
| D-19 | Constructor arguments undecoded — executor fix, widen signatures, or leave constructors Python-only? | (a) executor decodes by annotation (R2.3) | R2.3, B1 |
| D-20 | ~~`for_household` ignores its argument in the predefined-profile mode~~ | `[answered 2026-08-27, review of #592]` **(a)** implemented in P2: `data_acquisition_mode` parameter, profile derived from the household, refusal listing the shipped households and the computing modes | B1 UTSP |
| D-22 | Building's 20-field post-construction mutation: `config:` overrides, wider constructor, or `for_measured_envelope`? | (a) overrides, recorder omits equal-to-default | R3 Building, P3 recorder |
| D-26 | Five `read_config` readers zero-fill on unknown device: raise everywhere? | (a) raise, listing devices | R3 H₂ |
| D-32 | Delete the `LOCATION` key and the six 5R1C keys? | (a) both, own commits (six with D-16) | R2.4 |

## 12. Glossary

See the epic. P4-specific: **batch** — one mechanical PR converting a family of classes under R1; **gate** — an own commit that must precede a batch (R2, R6); **physics change** — a conversion that changes any existing setup's numbers (R5); **legacy factory** — a `get_default_*`/`get_scaled_*`/`config_*`/`control_*`/`read_config` classmethod replaced by a preset or constructor; **wire-format registry** — R3, the list of names EQ1 freezes at P5.
