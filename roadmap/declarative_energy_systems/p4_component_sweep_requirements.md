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

After P1–P3 the kernel, the file format, the recorder and nine converted classes exist; the remaining ~70 component config classes still ship close to 100 legacy factory classmethods in 71 spellings, setup-side arithmetic, hand-copied cross-component values and dead construction-time singleton keys. P4 converts them class by class — presets and constructors replace factories, laws replace setup-side math, `SIZING_CONTRIBUTIONS` declares the facts — in mechanical, golden-neutral batches, with every result-changing decision isolated into its own commit. This document is the per-class specification of *what* each class becomes, the registry of names that become public wire format, and the list of decisions that must be taken before each batch. It deliberately does not say how a batch is implemented: the converted pilots are the pattern.

## 3. Executive Summary

Surveyed: **88 config classes** in 65 modules; **9 converted** (none still carrying a legacy factory: the meter, the UTSP connector, the heat-distribution controller and `GenericBoilerControllerConfig` lost theirs in B1 on 2026-09-12, `WeatherConfig` on 2026-09-13); **~55 to convert**, **~16 to delete** (dead, duplicate or unbuildable), **~8 exempt** (data tables, non-component configs). Every conversion is byte-identical for the golden fleet except six named physics changes. Three new `SizingContext` facts and one executor fix are prerequisites that touch shared code. The batch order is driven by P3's recorded files: PV (17 instantiations), battery (13), DHW storage (13), buffer storage (12) and the hplib heat pump turn most recorded files from literal blocks into presets. Cost of inaction: the file format stays a demo for 9 classes, RenoVisor's base files stay 300-line literal dumps, and the names in them freeze at P5 without ever having been reviewed.

## 4. Context and Current Situation

**Current behaviour** (counts from `p4_class_survey.md` unless noted).
- Converted and complete: `BuildingConfig`, `HeatDistributionConfig`, `EMSConfig`, `GenericBoilerConfig`. No converted class carries a live legacy factory any more: `WeatherConfig.get_default` was **deleted 2026-09-13** (B1), its 48 call sites on `preset_standard`, `for_location` and the new `for_data_file` (D-18); the legacy factories of `ElectricityMeterConfig`, `GenericBoilerControllerConfig`, `HeatDistributionControllerConfig` and `UtspLpgConnectorConfig` were **deleted 2026-09-12** in B1 (#728–#731). The §8 R3 rows for those five classes record what replaced each and how many call sites moved.
- Unconverted classes in the recorded setups (P3 inventory §3b): 32 classes at the survey; by instantiation count PV 17, battery 13 and the fuel/gas meters 4 each (**all converted 2026-09-13**, B2), DHW storage 13 and buffer storage 12 (**both converted 2026-09-14**, B3), hplib heat pump + 2 controllers 4 each.
- **Facts.** `SizingContext` has 11 fields, six written by the Building — the repository's only provider. Facts the surveys need and that do not exist: `set_heating_threshold_outside_temperature_in_celsius` (six generator controllers copy it from the HDS controller), `roof_area_in_m2` (PV law), `pv_peak_power_in_watt` (battery law). `number_of_residents` exists with neither writer nor reader. The Weather provides nothing; climate facts (inventory §1a) remain future.
- **Setup-side math left** (P3 inventory §2e): 5 expressions plus the pervasive `Information`-object threading; the surveys locate each in a class law.
- **Dead code confirmed.** Of the plan's D13 list, the 6 defective (`generic_battery`, `generic_ev_charger`) and 2 of 3 zombies already left with `obsolete/` (#590); `controller_l1_heatpump` is live but has 0 call sites. New D13 members: `SmartDevice` (raises `KeyError` on construction), `ExtendedControllerSimulation` (reads class attributes of a defaults-free dataclass — unrunnable), `GenericElectrolyzerConfig.get_default_config` (mandatory argument), six H₂/RSOC classes whose only builders read two JSON files **absent from the repository**. Zero-call-site classes: `SimpleHotWaterStorageController`, `HeatPumpHplibControllerL1`, `L1HeatPumpController`, 9 legacy `configuration.py` classes, plus MPC/PID/wind/price signal/`Car` diesel with 0 setup uses.
- **Dead singleton keys.** `WATERMASSFLOWRATEOFHEATGENERATOR`: 2 readers, 0 writers, and the reading branch would raise `UnboundLocalError` if ever taken. Six Building 5R1C keys: readers only in MPC/PID. `LOCATION`: written by the Weather, read by postprocessing (live). **Executed 2026-09-12** (step 1 of retiring `SingletonSimRepository`): `WATERMASSFLOWRATEOFHEATGENERATOR` and its one surviving read are gone, and with them every other member of `SingletonDictKeyEnum` that nothing live read — `NUMBEROFAPARTMENTS`, `MAXTHERMALBUILDINGDEMAND`, `SETHEATINGTEMPERATUREFORWATERSTORAGE`, `SETCOOLINGTEMPERATUREFORWATERSTORAGE`, `PREDICTIVE`, `PREDICTIONHORIZON`, `PVINCLUDED`, `PVPEAKPOWER`, `BATTERYINCLUDED`, `MPCBATTERYCAPACITY` (no reference outside the enum file at all); `SMARTDEVICESINCLUDED`, `MAXIMUMBATTERYCAPACITY`, `MINIMUMBATTERYCAPACITY`, `MAXIMALCHARGINGPOWER`, `MAXIMALDISCHARGINGPOWER`, `BATTERYEFFICIENCY`, `INVERTEREFFICIENCY` (read only from `obsolete/`); and the publications `WEATHERPRESSUREYEARLYFORECAST`, `WEATHERALTITUDEYEARLYFORECAST` (weather), `COEFFICIENT_OF_PERFORMANCE_HEATING`, `ENERGY_EFFICIENY_RATIO_COOLING` (air conditioner), `PRICEPURCHASEFORECAST24H`, `PRICEINJECTIONFORECAST24H` (tariff provider), plus `HEATFLUX{THERMALMASS,SURFACE,INDOORAIR}NODEFORECAST` (building), `PVFORECASTYEARLY` (PV) and `HEATINGBYRESIDENTSYEARLYFORECAST` (UTSP connector) with the `predictive` branch they served. The six 5R1C keys and `LOCATION` had already gone. The enum keeps the eight weather series the PV system reads plus `RESULT_SCENARIO_NAME` and `DESCRIPTION`, which two later PRs move to the per-simulation repository and to parameters.
- **Recording defects found by the survey**, i.e. cases where a realized record would not re-execute today: `PVSystemConfig.get_scaled_pv_system` records `share_of_maximum_pv_potential = 1.0` whatever share it applied; `UtspLpgConnector` rewrites its own `data_acquisition_mode` at run time; ~~`UtspLpgConnectorConfig.for_household` ignores its `household` argument in its default mode~~ (**fixed in P2 on 2026-08-27**, review of #592: the constructor takes the mode and derives the profile from the household, refusing what the predefined mode cannot serve — D-20 answered (a)); constructor arguments reach builders undecoded, so `for_location` and `for_household` are uncallable from a file (only `for_tabula_code` works).
- **Convention conflicts** the supplement did not foresee: rating-suffixed presets named after default arguments, not devices (`air_water_8kw` ×2, `standard_5kwh` names a 10 kWh factory); `sizing_option` of the buffer storage is a factory *argument*, not a field, so conflict 4's resolution needs a new field; the CHP controller's four factories do not factor into fuel × buffer (a 42/50 °C flip between axes).

**Stakeholders.** Producers: each class's module (E6). Consumers: energy-system files and their authors (P2), recorded files (P3, re-recorded per batch), `describe`/`facts` CLI and the JSON Schema, the wire-format pin test (EAC6), RenoVisor/building sizer/HPC (P5). Existing: golden suites (8 sizers), the 24 setups, 4 000+ tests.

**Required behaviour:** R1–R8. **Kind of change:** refactoring by default with **named behaviour changes** (R5). **Assumption A1:** each batch is reviewed against the recorded file diff (Q-P3.1) plus golden parity.

## 5. Goals and Non-Goals

**Goals** — G1 every component config has presets/constructors and no legacy factory · G2 every sized value is computed by a class-side law from declared facts; no arithmetic in setups · G3 every name that becomes wire format is listed in this document before it is minted · G4 every result-changing conversion is a separate, diffed commit · G5 dead classes are removed before anyone mints a name for them.
**Non-Goals** — many-cardinality laws (EQ2: no first consumer found in any group) · climate facts beyond D-21 · the runtime half of `SingletonSimRepository` (forecasts, 5R1C coefficients) · template/repeat layer (the car setup's N-car loop stays Python even after D-23) · renaming legacy aggregator port names (P3 Q-P3.2) · retiring `system_setups/` (the v1 JSONs *were* retired mid-sweep, on 2026-09-12, by the owner's decision on Q-P2.5).

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

### R1.1 — What a converted class looks like `[decided 2026-09-14, owner; review of generic_pv_system/config.py]`

The first sixteen conversions followed the legacy modules' shape, and the result reads badly:
`generic_pv_system/config.py` spends 342 lines on one dataclass and ten lines of arithmetic, 60 %
of it prose. The rules below are the shape every conversion from B4 on uses, and the sixteen
converted classes are brought to it in one sweep (R1.2). None of them changes a value, a preset
name or a twin: they are how the same class is written down.

1. **Fields carry defaults; a preset states only what distinguishes it.** A field that has one
   value in every preset and every setup is a default on the field, not an argument every preset
   repeats. `GenericBoilerConfig` is the precedent: `eff_th_min: float = 0.60`, and
   `preset_condensing_gas` passes three arguments. The PV's `time`, `integrate_inverter`,
   `load_module_data`, `source_weight`, `predictive_control`, `prediction_horizon`, the five capex
   fields and the module and inverter names all have exactly one value anyone uses; with defaults,
   `preset_rooftop` is `cls(component_id=ComponentID(name=name))`. The recorder is indifferent —
   it diffs a live configuration against the *built* preset, not against the class.
   *Consequence of the dataclass rule "a defaulted field may not precede an undefaulted one":*
   `component_id` and the fields that genuinely vary come first, everything defaulted after,
   sized fields among them (they default to `AUTO`). `kw_only=True` would lift the rule and allow a
   shared capex mixin; **rejected for now** (owner, 2026-09-14): the last interpreter migration
   failed and the dataclass machinery is not to be varied until that is understood, even though
   `kw_only` itself is available on the 3.11 floor. The five capex fields therefore stay declared
   per class, with `None` defaults.

2. **`MAIN_CLASS` on `ConfigBase`.** Every config class carries an eight-line
   `get_main_classname` with a lazy import of its component "to avoid the import cycle" — 56
   copies under `hisim/components/`. `ConfigBase` gains `MAIN_CLASS: ClassVar[str]`, the dotted
   path of the component class, and implements `get_main_classname` once (import by path, lazily,
   at call time). A converted class declares one line:
   `MAIN_CLASS = "hisim.components.generic_pv_system.pv_system.PVSystem"`.

3. **Tables are data, not branches.** A lookup over a fixed set — the PV's two modules, the
   buffer's five l/kW figures, a carrier's fuel constants — is a class-scoped `Mapping`
   (`ClassVar`), keyed by the thing looked up, valued by a small frozen dataclass where a row
   has more than one number. The refusal for an unknown key lists the keys the table has, built
   from the table. Named constants likewise: `USABLE_ROOF_FRACTION: ClassVar[float] = 0.6`, with
   its source on the declaration, not `limiting_factor_for_rooftop = 0.6` inside a method. Reasons:
   a row is added without finding the right `elif`; `describe` and the RenoVisor translation
   layer can read what the class accepts; the repository rule "class-scoped constants, no
   module-level state" is met by construction. `SimpleHotWaterStorageConfig.LITRES_PER_KILOWATT_BY_SIZING_OPTION`
   is the shape.

4. **Contributions and laws live in the class body.** The `SIZING_CONTRIBUTIONS = ()` placeholder
   followed by a post-class assignment exists only because the compute function was a
   module-level `def` naming the class. A `staticmethod`, or a lambda over the `config` argument,
   needs no class name and is declared where the field it reads is. A function law stays a
   module-level function when it is long enough to want a docstring; a one-line law is an
   expression term on the field.

5. **Docstrings say what is, in the repository's plain form** — what the thing is, one example
   where it matters, a short why, `Args`/`Returns`/`Raises` where they add information. Not in a
   docstring or comment: what the deleted factory did, which decision or PR introduced the field,
   cross-references into `roadmap/` (they rot; the roadmap references the code, not the other way),
   and the import-layering rationale of a package — that goes once into the package `__init__`.
   A comment on a field says what the field means; its history is in `git log`.

6. **Module header.** The `__authors__ … __status__` block goes (45 modules; git carries
   authorship and `LICENSE` the licence). The `# clean` marker — a leftover of the first mypy
   migration that the code-overview generator still counted — is removed from the whole code
   base in its own PR, generator flag included (owner, 2026-09-14); `obsolete/` keeps its 20
   copies, being frozen text. A `pylint: disable` carries a one-line reason or none.

Applied to the PV configuration, the same behaviour is about 150 lines instead of 342.

### R1.2 — The sixteen converted classes are brought to R1.1 in one sweep `[proposed]`

One PR, after the B2/B3 stack has merged, covering the classes `PRESET_NAMES` lists on that day
(`BatteryConfig`, `BuildingConfig`, `ElectricityMeterConfig`, `EMSConfig`, `FuelMeterConfig`,
`GasMeterConfig`, `GenericBoilerConfig`, `GenericBoilerControllerConfig`, `HeatDistributionConfig`,
`HeatDistributionControllerConfig`, `HeatingMeterConfig`, `PVSystemConfig`,
`SimpleDHWStorageConfig`, `SimpleHotWaterStorageConfig`, `UtspLpgConnectorConfig`, `WeatherConfig`)
plus `ConfigBase.MAIN_CLASS`. Acceptance: every twin byte-identical (`record_all_setups.py --check`),
every golden pair unchanged in both modes, `describe` output identical except that fields now show
their defaults, `hisim energy-system schema` unchanged, and the line count of each touched
configuration module reported before and after.

Executed 2026-09-15: the fourteen touched modules lose 249 lines (13 589 → 13 340; the PV
configuration alone 340 → 264), `ConfigBase` gains 52 for `MAIN_CLASS` and the one
`get_main_classname`; twins, goldens (both modes, `one_week_60s`) and the schema unchanged.

### R2 — Gates that touch shared code `[proposed; survey A Gate 0, B Gate B-0, C D-19]`
Own commits, before the batch that needs them, each golden-neutral:
- R2.1 Facts added to `SizingContext`/`Size` — **executed 2026-09-11**, D-21 excepted: `set_heating_threshold_outside_temperature_in_celsius` (contributed by `HeatDistributionControllerConfig`), `roof_area_in_m2` (Building; value exists as `BuildingInformation.roof_area_in_m2`), `pv_peak_power_in_watt` (PV, `PVSystemConfig`'s first contribution), plus D-15's three carrier/fuel facts `energy_carrier`, `heating_value_of_fuel_in_kwh_per_liter` and `fuel_density_in_kg_per_m3` (contributed by `GenericBoilerConfig`, decided (b); its `__init__` derivation moved to `GenericBoilerConfig.fuel_constants`, `None` for district heating). Six facts, no reader yet: every one of them draws the "provides X, which no component reads" warning until the batch that consumes it lands, and no recorded twin moved. D-21's `heating_reference_temperature_in_celsius` moved from `BuildingConfig` to `WeatherConfig` on **2026-09-17** under the revised answer (d): no table and no second provider, the weather states the number and the building joins the two generator-side readers. The fact itself was already in the vocabulary, so R2.1 gained nothing by it. The `value_type=lt.LoadTypes` codec and the `Many` aggregator D-15 also names stay with the meter batch that needs them.
- R2.2 `CHANNELS` declared on `GasMeter`, `FuelMeter`, `HeatingMeter` (own modules) — **executed 2026-09-06** (#634): the gas meter takes `GAS_PRODUCTION` and `GAS_CONSUMPTION_UNCONTROLLED` (any carrier, Wh), the fuel meter `HEAT_CONSUMPTION` (any carrier, Wh), the heating meter `HEAT_DELIVERED` and `HEAT_CONSUMPTION` (heating, W); all dispatch-forbidden, mirroring `electricity_meter.py`. `tests/test_meter_channels.py` covers the matcher and the carrier refusals, and the four recorded sizer twins that carry a gas or fuel meter already feed it through tagged `inputs:`. Not done with it, and still open for the meter half of B2 under D-15 (b): a name-based decoder for `Sizable[LoadTypes]` (`_sizable_decoder` coerces by *value*, and `LoadTypes.GAS` is `"Gas"` while the twins write `GAS`), and the `Many` consistency aggregator (`laws.py:Many` is a declaration hook whose evaluation raises `NotImplementedError`). Neither blocks a single-generator setup.
- R2.3 Executor decodes constructor arguments by annotation (enum, `JsonReference`) — D-19; without it the constructor form of P2 is decorative.
- R2.4 Deletions of dead singleton keys: `WATERMASSFLOWRATEOFHEATGENERATOR` (now); the six 5R1C keys and `LOCATION` per D-32. — **executed 2026-09-12**, and wider than the row: every `SingletonDictKeyEnum` member without a live reader went in the same pass (the list is under "Dead singleton keys" above), leaving only the eight weather series the PV system reads, `RESULT_SCENARIO_NAME` and `DESCRIPTION`. The `predictive` config flags of the Building, PV and UTSP connector went with the publications they guarded; `predictive_control` stays, since live `i_simulate` branches still read it. The remaining three keys followed within the day, and on 2026-09-12 the module itself was deleted: `SingletonMeta` now lives in `hisim/result_path_provider.py` beside its last user, and no `SingletonSimRepository` exists any more.
- R2.5 D13 sweep: the delete rows of §R3 removed (or moved to the obsolete repository) before their batch.

### R3 — Per-class table (the wire-format registry) `[proposed; p4_class_survey.md]`
Legend: **conv** convert · **del** retired — moved to `obsolete/` under D-16's rule, never deleted · **done** converted (remaining work in the last column) · **ex** exempt. *Behaviour* **N** neutral, **P** physics change (R5), **?** depends on a decision. Facts use the `SizingContext` names. Full entries with `file:line` evidence: the survey, by class name.

**Heat generators (survey A)**

| Class | Act | Presets / constructors | `AUTO` fields ← facts (law) | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `MoreAdvancedHeatPumpHPLibConfig` | done | `air_water` | **converted 2026-09-15** (B4, the first class written in R1.1 shape from the start): `set_thermal_output_power_in_watt` and `heating_reference_temperature_in_celsius` are `sized_field`s with the copy laws `Size.HEATING_LOAD_IN_WATT` and `Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS`, both read from the Building's contribution; the massflow stays a plain field at 0.333 (supplement deviation). Every other field is a default, so `preset_air_water` passes `component_id` alone and the twin's twenty-two literal lines become one preset line. Both factories **deleted**: `get_scaled_advanced_hp_lib` (4 setups, 3 test files), `get_default_generic_advanced_hp_lib` (3 test files, one of them passing the bound method). `air_water_8kw` was never minted (D-6): the three test call sites that relied on the 8000 W default state the machine's power and rated temperature themselves. The class now contributes `maximal_thermal_power_in_watt` — the second member of the heating-generator family in `InterchangeableProviders.ALLOWED` — so the seven `SizingContext(maximal_thermal_power_in_watt=…set_thermal_output_power_in_watt)` reads take `concrete()` and the buffer vessel's volume line disappears from the four heat-pump twins: the vessel re-sizes from the machine instead of repeating 389.04 litres. 7 twins collapse (4 realized re-recorded, 3 grouped patched by hand), 22 golden pairs green in both modes | `maximal_thermal_power_in_watt` | N | D-6 |
| `HeatPumpHplibConfig` | del | | `advanced_heat_pump_hplib.py` → `obsolete/` with its controller and tests (D-1); its `Quantity`-typed fields are why | | | D-1 |
| `GenericHeatPumpConfig` | done | `vitocal_300_a`, `for_device(manufacturer, heat_pump_name)` | **converted 2026-09-15** (B4): no sizable field at all — the machine's power is not in the configuration, the component reads the A2/35 rating off the catalogue row in `build()`. The factory pinned a real catalogue device, Viessmann's Vitocal 300-A AWO-AC 301.B07, so the device names the preset and not `standard` (R4 as amended); its four values are field defaults and `preset_vitocal_300_a` passes `component_id` alone. `for_device` reaches any other row with the same timing defaults and validates nothing: an unknown pair raises in the component's `build()`, which the docstring says. Factory **deleted**: `get_default_generic_heat_pump_config` (2 setups: `basic_household`, `default_connections`; the two direct constructions in `tests/test_generic_heat_pump.py` pass their own values and stay). 2 twins collapse to `preset: vitocal_300_a`. The naming check grew one listed exception — a device designation carries digits no rating suffix can spell — rather than a looser rule. `GenericHeatPumpControllerConfig` is B5 | — | N | |
| `ElectricHeatingConfig` | done | `resistive` | **converted 2026-09-15** (B4): `maximum_electric_power_w` is a `sized_field` with the copy law `Size.HEATING_LOAD_IN_WATT`, read from the Building's contribution; every other field is a default, so `preset_resistive` passes `component_id` alone and the twin's eight literal lines become one preset line plus the DHW flag. The preset is named for the technology and not `standard` (R4 as amended). Factory **deleted**: `get_default_electric_heating_config` (1 setup, 2 test call sites); no rating-suffixed preset was minted for its 40 000 W default argument (D-6's rule), the two tests state that power themselves. The contributed power is the *electric* maximum unconverted: `efficiency` is declared but no code path reads it, and the component sets its thermal output equal to its electric input, so the thermal cap is that one number. Third member of the heating-generator family in `InterchangeableProviders.ALLOWED`; nothing in the one setup reads it yet | `maximal_thermal_power_in_watt` | N | |
| `DistrictHeatingConfig` | done | `standard` | **converted 2026-09-15** (B4): `connected_load_in_w` is a `sized_field` with the copy law `Size.HEATING_LOAD_IN_WATT`; `standard` stands because a connection to a network has no technology and no catalogue to name (R4, as for the meters). Factory **deleted**: `get_default_district_heating_config` (1 setup, 1 test). Besides the power the class contributes D-15's three fuel facts off class constants — `energy_carrier = DISTRICTHEATING`, both constants `None` — so the fuel meter in `household_district_heating_building_sizer` copies the carrier and its twin loses `fuel_loadtype: DISTRICTHEATING # pinned: no provider…`. The two `null` constants stay written: a law cannot resolve a field to `None` (the engine refuses a null-valued provider), so the setup assigns them before resolving and the recorder writes them as plain overrides. Fourth member of the power family and second of the fuel family in `InterchangeableProviders.ALLOWED` | `maximal_thermal_power_in_watt`, `energy_carrier`, `heating_value_of_fuel_in_kwh_per_liter`, `fuel_density_in_kg_per_m3` | N | D-15 |
| `AirConditionerConfig` | done | `samsung_ac120`, `for_device(manufacturer, model_name, scale_factor=1.0)`, `for_building_load(heating_load_in_watt, heating_reference_temperature_in_celsius)` | **converted 2026-09-15** (B4): no sizable field, D-3 as answered — `for_building_load` runs the twelve-field smart-devices search at build time and returns a complete configuration, so a recorded twin states the twelve selected values with no provider behind them and `air_conditioned_house` records byte-identically to before. `for_device` reaches any other catalogue row and refuses an unknown pair with the pairs the database holds. The preset is named after the device the deleted default pinned, Samsung's AC120HBHFKH/SA - AC120HCAFKH/SA (R4 as amended), and delegates to `for_device`, reading the database like any other build — `describe`, the recorder and the schema exporter all build it without trouble. Three factories **deleted**: `get_default_air_conditioner_config` (0 call sites), `get_air_conditioner_config_from_database` (its own two callers) and `get_scaled_air_conditioner_config` (1 setup, `air_conditioned_house`). The module-level maintenance share, the 12-year lifetime with its three sources, the 165.84 kg CO2 figure and the 0.6 sizing heuristic became `ClassVar`s; every field stays plain and mandatory, since a constructor always sets all of them. `AirConditionerControllerConfig` is B5 | — | N | D-3 |
| `SimpleAirConditionerConfig` | done | `standard` | **converted 2026-09-15** (B4): `standard` stands (R4) — a Carnot-factor cooling model with a 2 kW rating names no device, no standard and no building. The three numbers were already field defaults, so `preset_standard` passes `component_id` alone and both twins collapse to one preset line. Factory **deleted**: `get_default_simple_air_conditioner_config` (1 setup, 6 call sites in `tests/test_simple_air_conditioner.py`). `SimpleAirConditionerControllerConfig` is B5 | — | N | |
| `IdealizedHeaterConfig` | done | `standard` | **converted 2026-09-15** (B4): two setpoints and nothing else, so `standard` stands (R4 — a heater with neither a size nor a fuel has nothing to describe). Both are plain fields at 19.5/23.5 °C: copying the building's own 20/25 would be a physics change, so no law was written. Factory **deleted**: `get_default_config` (2 test call sites — `test_electricity_meter`, `test_economics_bridge`; the three building tests that construct the class with explicit setpoints stay). No twin uses the class; it joins the schema's closed enum so a file can build one | — | N | |
| `SimpleHeatSourceConfig` | done | `constant_thermal_power`, `constant_temperature`, `near_surface_brine` | **converted 2026-09-15** (B4): three factories differing only in the kind of source and the one number that kind needs; everything they repeated — propylene glycol at 20 %, 0.5 kg/s nominal, the external massflow signal off, 100 kg / 2000 € / 25 a / 10 €/a — became field defaults with the Buderus URL, the `emission_factors_and_costs_devices.csv` reference and the `Todo: check value` kept as field comments. Each preset passes `component_id`, `heat_source_type` and the one number; `near_surface_brine` passes no number, the component deriving the brine temperature from the daily average outside temperature. Four factories **deleted**: the three `get_default_config_*` and the deprecated alias `get_default_config_var_brinetemperature`, which no caller in this repository used — its test went with it, and the brine test now checks that the preset pins neither number. 8 call sites in `tests/test_simple_heat_source.py`, 1 in `tests/test_config_enum_serialization.py` (the factory was a callable in a table; it is a lambda over the preset now). The `from_dict` legacy field names of issue #1603 are untouched. No twin uses the class | — | N | |
| `SolarThermalSystemConfig` | done | `flat_plate` | `area_m2` ← number_of_apartments (×4) **done 2026-09-11**; the preset **converted 2026-09-15** (B4): every remaining field became a default — the Solar Keymark curve with the estif.org sheet and the "fitted to the typical flat plate curve" note as field comments, the Aachen coordinates, the south-facing 30° plane, the new pump, the unit source weight and the five `None` capex fields — so `preset_flat_plate` passes `component_id` alone. The module-level `COLLECTOR_AREA_IN_M2_PER_APARTMENT` is now a `ClassVar` on the config declared above the field, read by the law and by its `note=`; `coordinates` needs a `default_factory`, `Coordinates` being an unfrozen dataclass and so unhashable, which incidentally retires the shared-mutable-default defect the survey found. Factory **deleted**: `get_default_solar_thermal_system` (3 setups, 7 call sites in `tests/test_solar_thermal_system.py` — the five that named an area assign it on the built preset). 6 twins lose their `config:` block entirely and read `preset: flat_plate`: `area_m2` goes with the rest, the Building providing `number_of_apartments`, so the collector re-sizes per dwelling from the file instead of repeating 4.0 m². 3 golden pairs green in both modes. `SolarThermalSystemControllerConfig` is B5 | — | **P** — executed 2026-09-11, golden-neutral; the preset itself N | D-7 |
| `generic_chp.CHPConfig` | done | `gas`, `hydrogen` | `p_el`, `p_fuel` ← `Self("p_th")` × per-preset ratio **converted 2026-09-15** (B4): the four laws are `ClassVar`s over four named efficiencies (gas 0.5 overall / 0.33 electrical, fuel cell 0.43 / 0.48, the figures the two factory docstrings stated); the fields carry the gas pair as their `rule=` and `preset_hydrogen` assigns the hydrogen pair as field values, the per-preset law spelling of `GenericBoilerConfig.preset_pellets`. IEEE 754 multiplication commutes exactly, so `p_th * (0.48 / 0.43)` is the bit pattern the factory's `(0.48 / 0.43) * thermal_power` gave — pinned with `==` rather than `approx` at 0, 500 and 1000 W. `p_th` stays a plain field and keeps a **default of 500.0 W**: a preset takes only a name, no fact and no catalogue row gives a CHP's thermal power, and 500 W is what both integration tests run the machine at; the field comment says the number is nominal. `fuel_type` is what each preset states, `source_weight` a default of 1. Two factories **deleted**: `get_default_config_chp` and `get_default_config_fuelcell` (10 call sites in `tests/test_generic_chp.py`, no setup and no twin). `L1CHPControllerConfig` is B5 | — **not taken**: the parenthesised `maximal_thermal_power_in_watt` belongs to the family whose sanctioning comment in `InterchangeableProviders.ALLOWED` says exactly one of its members is in a scenario, and a CHP stands beside a boiler rather than instead of one | N | |
| `advanced_fuel_cell.CHPConfig` | done | `hydrogen` | **converted 2026-09-15** (B4): no sizable field — every value of the deleted factory became a field default (the 3 kW electric / 4 kW thermal modulating hydrogen machine, its 0.2/0.4 and 0.5/0.55 efficiency pairs, 60 and 15 timesteps of minimum operation and idle time, an 80 °C circuit at ΔT 10 K and 0.011 kg/s), the factory's literals kept int for int so nothing the machine reports moves, and `preset_hydrogen` passes `component_id` alone. `__post_init__` and the five capex fields stay. Module-level `SPECIFIC_HEAT_CAPACITY_WATER` is now a `ClassVar` on `CHP`, its only reader. Factory **deleted**: `get_default_config` (2 instances in `system_setups/dynamic_components.py`, 2 call sites in `tests/test_advanced_fuel_cell.py` — the capex builder keeps its `dataclasses.replace`, which touches two plain fields). Both CHP blocks of the `dynamic_components` twin collapse from 21 lines to `preset: hydrogen`; no `.grouped.` sibling exists. `advanced_fuel_cell_controller` retired under D-8 | — | N | D-5 |
| `CHPConfigAdvanced` | del | | **moved** 2026-09-15 into `obsolete/components/chp_config_advanced.py` (D-16): zero call sites, and not a configuration in this repository's sense — a plain class rather than a `ConfigBase` dataclass, so it can carry no preset and reach no energy-system file. It read one row of `hisim/inputs/chp_system/mock_up_efficiencies.xlsx` ("BlueGen BG15", five other machines commented out beside it) at construction, which is why its whole body sat inside `__init__`. The spreadsheet stays: nothing reads it now, but it is the only record of those six machines. `advanced_fuel_cell.py` loses the `os` and `hisim.utils` imports the class alone needed | | | D-16 |
| `GenericBoilerConfig` | done | 7 presets | done | power band; + carrier and fuel constants **landed 2026-09-11** (R2.1, D-15) | | |

**Heat-generator controllers (survey A)** — all need R2.1's threshold fact except where noted; the fact itself exists since 2026-09-11, contributed by `HeatDistributionControllerConfig`, so each conversion only has to read it.

| Class | Act | Presets | `AUTO` fields ← facts | Beh. | Dec. |
|---|---|---|---|---|---|
| `…HPLibControllerSpaceHeatingConfig` | done | `standard` | **converted 2026-09-15** (B5): `heat_distribution_system_type` is a `sized_field` with the copy law `Size.HEAT_DISTRIBUTION_SYSTEM_TYPE` and `value_type=HeatDistributionSystemType` — the field was typed `Any` and now has its real type, the wire form is unchanged because the twins already spelt the member name — and `set_heating_threshold_outside_temperature_in_celsius` carries the copy law `Size.SET_HEATING_THRESHOLD_OUTSIDE_TEMPERATURE_IN_CELSIUS` with `optional=True`: `summer_heating_condition` reads `None` as "always heating", so the `Optional` stays and the field is `Sizable[Optional[float]]` as the fuel meter's two fuel constants are. Both are exactly what the four setups copied by hand from `HeatDistributionControllerInformation`. Every other field is a default (mode 1, cooling threshold 20 °C, both offsets 5 K), so `preset_standard` passes `component_id` alone and the twins' seven literal lines become one preset line. Factory **deleted**: `get_default_space_heating_controller_config` (4 setups: `household_heatpump_building_sizer`, `household_heatpump_car_building_sizer`, `household_heatpump_solar_thermal_building_sizer`, `automatic_default_connections`; 3 test files). `test_heating_meter` never passed the threshold and so ran on the factory's 16 °C, two kelvin below what the emitter circuit derives there: it pins 16 °C in its context rather than change a value | — | N | |
| `…HPLibControllerDHWConfig` | done | `standard` | **converted 2026-09-15** (B5): no sizable field — the 40/60 °C band and the constant-power limit are properties of the vessel's control, not of the building — so all four values are field defaults and `preset_standard` passes `component_id` alone; the factory's two inline comments survive as field comments. Factory **deleted**: `get_default_dhw_controller_config` (the same 4 setups and 3 test files). Four twins' five literal lines become one preset line | — | N | |
| `HeatPumpHplibControllerL1Config` | del | | moves with `advanced_heat_pump_hplib.py` (D-1) | | D-1 |
| `GenericHeatPumpControllerConfig` | done | `standard` | **converted 2026-09-15** (B5): no sizable field — the setpoints are what the residents ask for, not what the building derives. The factory `get_default_generic_heat_pump_controller_config` had **zero callers** and its 18/26/0.5/mode 1 went with it (R1.1: a default is the value somebody uses); the defaults are the fleet's 19/24/0.5/mode 2, which `basic_household` runs unchanged. `default_connections` heats from 16 °C and keeps that as a one-line override after the preset; `tests/test_generic_heat_pump.py` keeps its own explicit 18/28/1/mode 1 construction. Two twins collapse, one of them to `preset: standard` alone | — | N | |
| `ElectricHeatingControllerConfig` | done | `standard` | **converted 2026-09-15** (B5): both building-derived fields are `sized_field`s. `specific_heating_load_of_building_in_watt_per_m2` carries a function law over `Size.HEATING_LOAD_IN_WATT` and `Size.CONDITIONED_FLOOR_AREA_IN_M2` — law terms carry no division, so the ratio is a callable, over the same operands in the same order the setup divided by hand — and it drops its `Optional`: no code path reads the field, neither controller nor appliance, so it is recorded provenance for the threshold and there is nothing for `None` to mean. `set_heating_threshold_outside_temperature_in_celsius` reuses `HeatDistributionControllerConfig.HEATING_THRESHOLD_LAW`, the step table over that same ratio. **This row's "removes a cross-module import" clause was wrong** and the import stays: it was written for a threshold copied from the sibling emitter circuit, but direct electric heating has no water circuit and `household_electric_heating_building_sizer` builds no heat distribution controller at all, so nothing in that scenario contributes `set_heating_threshold_outside_temperature_in_celsius` and a copy law would raise `ConfigSizingError`. The import now carries a law instead of a factory helper, which is the same step table either way. Both factories **deleted**: `get_default_electric_heating_controller_config` (1 test) and `get_electric_heating_config_based_on_building_efficiency` (1 setup). The setup resolves both facts off `my_building_information` and keeps its two `True` flags as plain overrides after the preset; the display-config test pins 16 °C and the 40 W/m² efficiency the step table maps to it, since it builds no building. Both twins (realized + grouped) collapse to `preset: standard` plus the two flags | — | N | |
| `DistrictHeatingControllerConfig` | done | `standard` | **converted 2026-09-15** (B5): `set_heating_threshold_outside_temperature_in_celsius` is a `sized_field` with the copy law `Size.SET_HEATING_THRESHOLD_OUTSIDE_TEMPERATURE_IN_CELSIUS`, read from the emitter circuit's contribution; the other three fields are defaults (no DHW, 15 K hysteresis, no parallel operation). Factory **deleted**: `get_default_district_heating_controller_config` (1 setup, 1 test). `household_district_heating_building_sizer` keeps its two `True` flags as plain overrides after the preset, so the twin's four literal lines become a preset line plus those two | — | N | |
| `AirConditionerControllerConfig`, `SimpleAirConditionerControllerConfig`, `SolarThermalSystemControllerConfig`, `NightSetbackConfig` | done | `standard` | **converted 2026-09-15** (B5): four controllers with nothing to size. Every field of every one of them is what the residents ask for or what the machine's own cycling and piping impose — a comfort band, a setpoint, a switch-on temperature difference, a night window — so none is sizable, no fact is contributed and each `preset_standard` passes `component_id` alone. `standard` stands for all four (R4): none of them names a device, a standard or a reference. Four factories **deleted**: `get_default_air_conditioner_controller_config` (1 setup, 1 test file), `get_default_simple_air_conditioner_controller_config` (1 setup, 12 call sites in `tests/test_simple_air_conditioner.py`), `get_solar_thermal_system_controller_config` (3 setups) and `NightSetbackConfig.get_default_config` (1 test call; the three configurations that test builds field by field to exercise the window modes stay). `air_conditioned_house` keeps its controller under the odd name the deleted factory's default `component_id` spelled, `AirConditionerControllerConfig`, that being what the twin and every result column of the setup carry. The night setback controller's two hour fields keep their `dc_json_config(field_name=…)` aliases, so `to_dict` still writes `night_start_hour`/`night_end_hour`; its v3 `config:` keys are the Python attribute names, which is new surface rather than a change, the class having had no preset and so no schema presence before. 9 twins collapse (5 realized re-recorded, 4 grouped patched by hand), every controller block to `preset: standard` with no `config:` left; 5 golden pairs green in both modes | — | N | |
| `L1CHPControllerConfig` | done | `gas`, `hydrogen`, `gas_with_buffer`, `hydrogen_with_buffer` | **converted 2026-09-15** (B5): no sizable field — the two bands are what the vessels are held at and the hydrogen threshold what the storage must keep, none of it derived from anything else in the scenario. D-4's preserved numbers **are** the presets' numbers, one preset per deleted factory: `gas` DHW 42/60, `hydrogen` DHW 50/60 with the 8 % hydrogen threshold, `gas_with_buffer` heating 35.0/40.0 and DHW 50/60, `hydrogen_with_buffer` heating 31.0/40.0 and DHW 42/60, the two buffer presets also starting the heating season on day 269 rather than 270. What every one of them shares is a field default (`source_weight`, the 300 W electricity threshold, the 20.0/20.5 °C building band, the 60 °C top of the drain hot water, the 270/150 season, the 4 h/2 h runtime and resting times); `use`, `h2_soc_threshold` and `t_min_dhw_in_celsius` have no shared value and so no default, which moves them in front of the defaulted fields. The three module constants become `ClassVar`s with their comments, and the buffer helper the D-4 PR left behind becomes the private classmethod the two `_with_buffer` presets share, so the asymmetry is stated once. The class gains `MAIN_CLASS` (it had no `get_main_classname` at all) and with it its first schema presence. Four factories **deleted**: `get_default_config_chp`, `get_default_config_fuel_cell`, `get_default_config_chp_with_buffer`, `get_default_config_fuel_cell_with_buffer` — 4 call sites, all in `tests/test_generic_chp.py`, no setup and no twin. The threshold pin there keys on the preset names and drops its `component_name` expectation, an instance name now being the caller's argument rather than a factory's default; the `CHPController` / `FuelCellController` spellings survive as the names the two integration tests pass | N — D-4 reversed 2026-09-11, the 42/50 and 35/31 values are preserved, so no threshold moves | D-4 |
| `L1HeatPumpConfig` (`controller_l1_heatpump`) | ? | (`space_heating`, `buffer`, `dhw`) | — | N | D-2 |
| `GenericBoilerControllerConfig` | done | `modulating`, `on_off` | 4 legacy factories **deleted 2026-09-12** (B1), 11 sites (8 setups, 3 tests): `modulating` ×8, `on_off` ×3, pellet and wood chip as `on_off` + their two runtime/resting overrides | N | |
| `GasHeaterConfig`, `GasControllerConfig`, `CHPControllerConfig`, `ExtendedControllerConfig` (+ `advanced_fuel_cell_controller`) | del | | **retired.** The last three and the controller left 2026-09-10 into `obsolete/components/configuration_fuel_cell_controller.py` and `advanced_fuel_cell_controller.py`; `GasHeaterConfig` followed **2026-09-16** (B8) into `obsolete/components/configuration_gas_heater.py`. Zero references anywhere, and a plain class of class attributes rather than a `ConfigBase`, so it could carry no preset and reach no file; `generic_boiler.GenericBoilerConfig` models the same 1–12 kW band at 0.60/0.90 efficiency as `preset_condensing_gas_12kw`. `configuration.py` now holds only the two emission-factor tables, `PhysicsConfig` and the live `HouseholdWarmWaterDemandConfig` | | | D-8 |

**Heat distribution, storages (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `HeatDistributionControllerConfig` | done | `building_derived` (was `standard` until 2026-09-14, R4) | `get_default_*` **deleted** (11 sites, D-11 executed 2026-09-11); `get_config_based_on_building_efficiency` **deleted 2026-09-12** (B1, neutral half), its 10 building-sizer setups resolve `preset_standard` against the building's facts and override `heating_system` alone; `heating_system` plain default until Q-P1.8 | threshold fact **landed 2026-09-11** (R2.1) | **P**, executed: 3 setups + 8 tests at 16 → 18 °C; week goldens unchanged (January never reaches the threshold), +0.060 % gas over a full year | D-11 |
| `HeatDistributionConfig` | done | `building_derived` (was `standard` until 2026-09-14, R4) | nothing left | | | |
| `SimpleHotWaterStorageConfig` | done | `buffer` (+ `sizing_option: HotWaterStorageSizingEnum` field, D-10) | **converted 2026-09-14** (B3): the new plain field `sizing_option` records which l/kW figure sized the vessel; the function law `_buffer_volume_in_liter` reads the generator's `maximal_thermal_power_in_watt` and the sibling `sizing_option` (`fields=`, the first law to read a sibling enum) over one class-scoped table — heat pump 50, general 20, pellet 40, wood chip 50, gas heater 20 l/kW — with the factory's two sources. Both factories **deleted**: `get_scaled_hot_water_storage` (12 setups, 7 test files / 20 calls), `get_default_simplehotwaterstorage_config` (1 reference). The boiler setups pass the boiler's resolved power; the four heat-pump setups pass the unconverted hplib's `set_thermal_output_power_in_watt` as a plain attribute — seven reads that need `concrete()` once B4 converts it. 22 twins collapse to `preset: buffer`, the option and the volume; 20 gain a `sizing_option:` line (the oil sizer's option is the preset's own), no volume moves; 24 golden pairs green. Both `PositionHotWaterStorageInSystemSetup` enums left alone (D-17) | — | N (C11 was executed 2026-09-11, before this) | D-9, D-10, D-17 |
| `SimpleHotWaterStorageControllerConfig` | del | | | | | D-16 |
| `SimpleDHWStorageConfig` | done | `standard` | **converted 2026-09-14** (B3): `volume_heating_water_storage_in_liter` is a `sized_field` with the expression law `Size.NUMBER_OF_APARTMENTS.at_least(1) * 250.0` (`at_least` exists in `laws.py` and composes, so no function law was needed). Both factories **deleted**: `get_scaled_dhw_storage` (13 setups, 6 tests), `get_default_simpledhwstorage_config` (0 sites). Every setup now resolves against the Building's `number_of_apartments`; `household_gas_solar_thermal` used to pass the archetype's dwelling count while its Building was the bare preset — both are 1, so nothing moved, and the setup can no longer disagree with itself. 25 twins collapse to `preset: standard` plus the volume, all 47 volume lines in `energy_systems/` byte-identical; 26 golden pairs green | — | N | |
| `SetTemperatureConfig`, `WarmWaterStorageConfig`, `HydrogenStorageConfig` (`configuration.py`), `LoadConfig`, `ElectricityDemandConfig`, `PVConfig` | del/ex | | `HydrogenStorageConfig` **moved** 2026-09-11 into `obsolete/components/configuration_hydrogen.py` with `AdvElectrolyzerConfig` (D-29); the other four left under D-16 | | | D-29 |
| `HouseholdWarmWaterDemandConfig` | **ex** (live constant table; supplement wrongly lists it for deletion) | | | | | |

**Electricity, EMS, meters (survey B)**

| Class | Act | Presets | `AUTO` fields ← facts | Provides | Beh. | Dec. |
|---|---|---|---|---|---|---|
| `PVSystemConfig` | done | `rooftop` | **converted 2026-09-13** (B2): `power_in_watt` is a `sized_field` whose law `_rooftop_power_in_watt` reads `roof_area_in_m2` and the own fields `share_of_maximum_pv_potential`, `module_name`, `module_database`, calling the unchanged `size_pv_system` (0.6 roof factor, two-module table, `ValueError` otherwise). Both factories **deleted**: `get_scaled_pv_system` (12 setups — 11 sizers + `automatic_default_connections` — and 8 tests) and `get_default_pv_system` (14 setups, 6 test calls + 2 references, 1 internal); D-13 executed as three demo setups pinning `power_in_watt = 10000.0`, not four (`basic_household_with_weather_data_request` no longer exists). The dead `PVSystem.get_default_config` had already gone with #680. 27 twins collapse to `preset: rooftop` plus overrides, 473 literal lines removed, no value changed; `weather_identity` now travels through the `SizingContext` at every call site. The field moved to the end of the dataclass (a defaulted field may not precede undefaulted ones) | `pv_peak_power_in_watt` contributed by `_pv_sizing_facts`; still no reader until the battery | N (fleet share is 1.0) | D-12, D-13 |
| `BatteryConfig` | done | `sized_to_pv` (was `standard` until 2026-09-14, R4) | **converted 2026-09-13** (B2): both sizable fields carry expression laws over the PV fact — capacity `(Size.PV_PEAK_POWER_IN_WATT * 1e-3).rounded(2)`, inverter `(Size.PV_PEAK_POWER_IN_WATT * 0.5).rounded(2)`; the inverter reads the fact and not the rounded sibling, so the fleet's 11 136.14 W survives (the sibling form gives 11 135.00). Both factories **deleted**: `get_scaled_battery` (11 sizers, 3 tests) and `get_default_config` (2 sites in `dynamic_components`, 2 tests). In Python mode the sizers hand the PV's resolved power into `SizingContext(pv_peak_power_in_watt=…)`; in the declarative executor `PVSystemConfig.SIZING_CONTRIBUTIONS` binds it (`Battery.pv_peak_power_in_watt <- PVSystem [UNIQUE]`). 23 twins collapse to `preset: standard` plus the two numbers, no value changed; `Battery2` in `dynamic_components` keeps its `source_weight: 2` override | — | N | D-14 |
| `WindturbineConfig` | del | | `generic_windturbine.py` → `obsolete/` with its test (D-16); the KPI vocabulary keys on `ComponentType.WINDTURBINE`, so no result moves | | | D-16 |
| `PriceSignalConfig` | del | | `generic_price_signal.py` → `obsolete/` with its test (D-16); superseded by `tariff_provider.py` | | | D-16 |
| `ElectricityMeterConfig` | done | `standard` | legacy factory **deleted 2026-09-12** (B1), 29 sites moved to `preset_standard(name)`: 17 setups, 12 calls in 10 test files | | N | |
| `GasMeterConfig` | done | `standard` | **converted 2026-09-13** (B2), per D-15 (b) and not per this row's earlier `gas`/`hydrogen`: one preset, `gas_loadtype` a `sized_field(rule=Size.ENERGY_CARRIER, value_type=lt.LoadTypes)` copied from the generator, so a meter cannot state a carrier its boiler does not burn. Factory **deleted** (5 setups — the survey's four plus `basic_household_only_heating` — and 2 tests). `total_energy_from_grid_in_kwh` **deleted from the config**: it was a runtime total living in configuration, always recorded as `0.0`; `get_cost_opex` now reports the measured gas (the meter's row in `operational_costs_co2_footprint.csv` moves from `0.0` to the real consumption; no golden KPI moves, meters being excluded from the OPEX totals). Nine twins collapse to `preset: standard` plus the recorded carrier as an override on a sizable enum field — loadable because the sizable decoder now accepts a member name (`b2_loadtypes_codec`) | — | N for KPIs; the OPEX CSV row is a bug fix | D-15 |
| `FuelMeterConfig` | done | `standard` | **converted 2026-09-13** (B2), per D-15 (b) and not per this row's earlier four carrier presets: one preset, all three fields copied from the generator — `fuel_loadtype` ← `Size.ENERGY_CARRIER`, `heating_value_of_fuel_in_kwh_per_liter` ← `Size.HEATING_VALUE_OF_FUEL_IN_KWH_PER_LITER`, `fuel_density_in_kg_per_m3` ← `Size.FUEL_DENSITY_IN_KG_PER_M3`, the two constants `optional=True`. Factory **deleted** (4 setups, 5 tests). The three boiler sizers take the constants from `GenericBoilerConfig.fuel_constants` — the same source the contribution uses — so the twins keep `9.821666666666667`/`830.0` (oil), `3.25`/`650.0` (pellets), `4.333333333333333`/`250.0` (wood chips). The district-heating setup has no provider (its class is unconverted) and pins the values itself; its twin stops carrying oil's `9.82`/`830.0` for a carrier that burns nothing and records `null`, which `get_cost_opex` guards and the goldens do not see | — | N; district heating's two `null`s are a value change in one twin with no result effect | D-15 |
| `HeatingMeterConfig` | done | `standard` | **converted 2026-09-13** (B2): `preset_standard(name)` over the class's one field; factory **deleted** (0 setups, 1 test). No recorded twin carries a heating meter; the class joins the schema's closed enum so a file can build one | — | N | |
| `EMSConfig` | done | | delete obsolete `strategy` field; align the class-side default feed for the occupancy to weight 999 (its own channel rejects weight 1, so a bare `- occupancy` under the EMS fails EF-29 today — P2 R2.5); legacy-path behaviour check first | | N/? | |
| `MpcControllerConfig`, `PIDControllerConfig` | del | | `controller_mpc.py` and `controller_pid.py` → `obsolete/` with their tests (D-16); 27 of 49 MPC fields are lists — 13 runtime result buffers and 12 forecast inputs, none of which belongs in a file — and the two were the only readers of the Building's 5R1C keys (D-32) | | | D-16, D-32 |
| `SumBuilderConfig` | done | `standard` | **converted 2026-09-15** (B6): `loadtype` and `unit` default to `ANY`/`ANY`, the only pair anyone uses, so `preset_standard(name)` passes the instance name alone and the two example twins' three-line `config:` block becomes `preset: standard`. The factory `get_sumbuilder_default_config` is **deleted** (2 setups, 3 calls in `tests/test_sumbuilder.py`); it hard-coded the instance name `Sum`, which the two setups now pass themselves. The class declares `MAIN_CLASS`; the paragraph about the `_in_config_unit` local variables left the configuration, the three `i_simulate` docstrings already carrying it. All three components of the module — `CalculateOperation`, `SumBuilderForTwoInputs`, `SumBuilderForThreeInputs` — share this configuration and so all three enter the schema | — | N | |
| `TransformerConfig` | done | `standard` | **converted 2026-09-15** (B6): the factory's values become the field defaults (95 % efficiency, 1000 kW rating, the five cost fields `None`), so `preset_standard(name)` passes the instance name alone. `get_default_transformer_config` is **deleted** (6 calls in `tests/test_transformer_rectifier.py`, 1 in `tests/test_economics_data_and_integration.py`; no setup called it). `system_setups/electrolyzer_with_renewables.py` built the class directly and now takes the preset with the one override that distinguishes it — the rating it inherits from the electrolyzer it feeds — so its twin keeps `rated_power_in_kilowatt: 1028.225` and loses the other six lines. The numpy-style `Attributes` block became field comments, and `get_main_classname` became `MAIN_CLASS` | — | N | |
| `RandomNumbersConfig` | ex | — | **no preset** `[owner, 2026-09-15]`, reversing this row's earlier `standard`: the two shipped instances share no value at all (100–200 seed 1, 10–20 seed 2), so a preset would state nothing and be overridden in full at every call site, which R1.1 forbids — a preset states only what distinguishes it. The class stays live as a test and example helper and keeps its three plain fields; B6 gave it `MAIN_CLASS` and nothing else, and its two twins do not change | — | N | D-16 |

**Occupancy, weather, building (survey C)**

| Class | Act | Presets / constructors | Remaining work | Beh. | Dec. |
|---|---|---|---|---|---|
| `WeatherConfig` | done | `aachen` (was `standard` until 2026-09-14, R4), `for_location(location, heating_reference_temperature_in_celsius, …)`, `for_data_file(path, data_source, heating_reference_temperature_in_celsius)` (D-18, D-21) | legacy factory **deleted 2026-09-13** (B1), 48 sites moved: 19 setups (6 `preset_standard`, 1 `for_location(SEVILLE)`, 5 `for_location` on the archetype's station, 7 the station-or-file branch the factory resolved internally), 29 calls in 20 test files (the one call in `obsolete/` stays, that tree being frozen); the 3 tests of the factory's own string and direct-file branches retired with it. Path-valued constructor arguments are now expanded before the call, without which `for_data_file` is uncallable from a file. **D-21 landed 2026-09-17**: a plain undefaulted field `heating_reference_temperature_in_celsius` after `data_source`, stated by `preset_aachen` (-7.0) and required by both constructors, contributed as a second `FactContribution` beside `weather_identity` — one provider, three readers (Building, HDS controller, hplib heat pump) | N | D-18, D-19, D-21 |
| `BuildingConfig` | done | `german_single_family_home` (the `config_presets` name, back since 2026-09-14 — R4, conflict 8 reversed), `for_tabula_code` (no `heating_reference_temperature_in_celsius` parameter since D-21) | `roof_area_in_m2` fact **landed 2026-09-11** (R2.1); the 14 non-parameter post-construction mutations → sparse `config:` overrides (D-22); **D-21 landed 2026-09-17**: `heating_reference_temperature_in_celsius` left `SIZING_CONTRIBUTIONS` and `sizing_facts` (six facts now, not seven) and became the class's second sizable field, `sized_field(rule=Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS)`, which moved it from second in the class to just before `weather_identity`; `SizingContext.for_building` no longer carries the fact either | N / P | D-21, D-22 |
| `UtspLpgConnectorConfig` | done | `couple_both_at_work` (was `standard` until 2026-09-14, R4), `for_household` | legacy factory **deleted 2026-09-12** (B1), 43 sites (18 setups, 23 calls in 17 test files, 2 comments): `preset_standard("UTSPConnector")` everywhere; the 11 sizers keep `USE_LOCAL_LPG` + household + `cache_dir_path` as `config:` overrides rather than `for_household(…)` arguments, because the constructor also clears `name_of_predefined_loadprofile` and would move the recorded occupancy identity — its own commit, not a factory removal | N | ~~D-20~~ |
| `WeatherDataImport` | ex | | not a config; import fails (`wetterdienst`) | | |
| `SmartDeviceConfig` | del | | defective (`KeyError` on construction); module and config **moved** to `obsolete/` (D-30, 2026-09-11) | | D-30 |

**Mobility, H₂ chain, examples (survey C)**

| Class | Act | Presets / constructors | Notes | Beh. | Dec. |
|---|---|---|---|---|---|
| `CarConfig` | done | `for_household` | `Car` built from `(parameters, config)`; the driving profile is handed over through the per-simulation `SimRepository` and the config carries its identity (`household_name`, `car_name`) | N | ~~D-23~~ |
| `CarBatteryConfig` | done | `standard` | **converted 2026-09-16** (B7): no sizable field — bslib's `SG1` characteristic, 10 kW and 30 kWh are the one pack the fleet drives — so every field is a default and `preset_standard` passes `component_id` alone; the twin's six literal lines become one preset line in both car twins. `get_default_config` deleted, its one setup building the preset under the `CarBattery_{n}` name the twin spells and keeping its `source_weight` override. The two `total_*_energy_in_kilowatthour` accumulators stay fields at `0.0`, with a comment saying the component writes them in post-processing and that they are not an author's input; **F-20** records that a config field is being used as mutable component state and names the two ways out (move them onto the component, or exclude them from recording), nothing changed here | N | |
| `ChargingStationConfig` | done | `for_charging_station_set` (no preset, D-24) | **converted 2026-09-16** (B7): `@constructor` accepts `JsonReference`, which is a dataclass with `from_dict`, so the constructor is callable from a file with the station set written as the `Name`/`Guid` mapping the twins already carry. The proposed law `Self(charging_station_set)` × 0.1 **was not minted**: the rating is parsed out of the set's *name* string ("… with 11 kW"), which no `Self` term can do, so `lower_threshold_charging_power_in_watt` stays a plain field the constructor computes, with the parsing in `charging_power_in_watt_of` (which refuses a name stating no rating) and the 10 % as the class constant `LOWER_THRESHOLD_AS_SHARE_OF_CHARGING_POWER`. `source_weight`, `battery_set_soc` and the five capex figures become field defaults, which reorders the declaration and moves two lines of the charger block in both car twins; every value is unchanged | N | D-24 |
| `ElectrolyzerConfig`, `ElectrolyzerControllerConfig` | done | `alkaline` / `standard`, `for_device(electrolyzer_name)` | **converted 2026-09-16** (B7): every field of both classes became a default, so each builder passes the component identity alone. The electrolyzer's preset is named for its technology and not `standard` (R4 as amended) -- alkaline is what tells the stated machine apart from the table's PEM and solid-oxide rows -- and carries the deleted factory's 100/110 kW, 100 kg/h, Faraday 1.0, 0.3 A/cm2 and 0.1/0.2 %/s. The controller's `standard` stands: its band belongs to the electrolyzer it is sized to and no other name would say more; it carries the dead factory's 100/10/110 kW, 5 kW standby and 70 s / 1800 s start times, the same start times the fuel-cell controller's `pem` preset carries. `config_electrolyzer` and `control_electrolyzer` became `for_device`, taking the instance name first; `read_config` stays the table reader that `tests/test_electrolyzer_manufacturer_table.py` calls directly, and D-26's refusal is untouched. Factories **deleted**: `get_default_alkaline_electrolyzer_config` (1 test call in `test_economics_data_and_integration`) and `get_default_electrolyzer_controller_config` (0 callers). Call sites moved: 2 in `system_setups/electrolyzer_with_renewables.py`, 5 in the manufacturer-table test. The electrolyzer twin is **byte-identical**: giving every field a default reordered nothing, and a constructor stamps no provenance, so both blocks still record as complete literal blocks | N | D-26 |
| `PTXControllerConfig` | done | `for_device(electrolyzer_name, operation_mode)`, no preset | **converted 2026-09-16** (B7): constructor only, as the row proposed. `control_electrolyzer` became `for_device` with the instance name first; no preset was minted because all four loads come out of one row of the manufacturer table, so there is no default plant to name, and the class joins `CLASSES_WITHOUT_PRESETS` beside the CSV loader and the wallbox. `__post_init__` keeps D-27's enum normalisation and `read_config` stays the table reader. 2 call sites in the manufacturer-table test; no setup and no twin | N | D-27 |
| `XTPControllerConfig` | done | `standard` (invented) | **converted 2026-09-16** (B7): D-25 left this class with no builder at all, its only factory having read a manufacturer JSON that is not in this repository. The invented preset carries the only figures anyone runs it at, the ones its own test states: nominal 10 kW, band 2 to 10 kW, standby 1 kW and `XtpOperationMode.STANDBY_LOAD`, the enum's first member, so the cell idles rather than switching off. `standard` is the name -- the band names no device, no manufacturer and no standard (R4). Every field became a default, `__post_init__` stays, and the class docstring's "no default builder" paragraph went with the gap it described. The test fixture builds the preset and assigns the mode it is parametrised with; the second construction, which states its own 12 kW ceiling to exercise the refusal, stays. No twin | N | D-25, D-27 |
| `FuelCellConfig`, `FuelCellControllerConfig` | done | `pem` each | **converted 2026-09-16** (B7): every value of the two hand-typed factories became a field default, so both `preset_pem` methods pass the component identity alone. The variant names the cell's preset -- PEM is what the polarization curve the component reads belongs to -- and the controller's preset is named after the machine it is sized to rather than `standard`, its 100/10/110 kW band being that cell's band, with a 10 kW standby and start times of 70 s warm and 1800 s cold. Factories **deleted**: `get_default_pem_fuel_cell_config` (0 callers) and `get_default_fuel_cell_controller_config` (1 call, the controller test's fixture, which builds the preset under the identity the factory defaulted to). `tests/test_generic_fuel_cell.py` states its own 10.3 kW machine and stays. No twin | N | ~~D-25~~ |
| ~~`RsocConfig`, `RsocControllerConfig`, `RsocBatteryControllerConfig`~~ | **obsolete** | | moved to `obsolete/components/` under D-25 with their three tests and `rSOC_efficiency_curve_data.json`: JSON absent, no other builder | | ~~D-25~~ |
| `GenericElectrolyzerConfig` | del | | **moved** 2026-09-11: `generic_electrolyzer.py` → `obsolete/components/` with `tests/test_generic_electrolyzer.py` (D-29 (c)); D-28 moot, so no `Self("max_power")` laws were minted | | D-28, D-29 |
| `L1ElectrolyzerControllerConfig`, `GenericHydrogenStorageConfig` | del | | **moved** 2026-09-11: `controller_l1_electrolyzer.py` and `generic_hydrogen_storage.py` → `obsolete/components/`, the storage with `tests/test_h2storage.py`, the controller with the electrolyzer test that built it; `controller_l1_chp` dropped its optional H₂-storage default connection (D-29) | | D-29 |
| `ElectrolyzerWithStorageConfig`, `ElectrolyzerWithHydrogenStorageConfig` | done | `standard` each | **converted 2026-09-16** (B7): every value of the two `get_default_config` factories became a field default, so both presets pass the component identity alone and nothing is derived (D-28). The machine is the 2.4 kW one -- 1.2 to 2.4 kW, which is 60 to 100 % of its rating, 300 to 5000 Nl/h, 400 W of waste heat, 30 bar -- and the tank the 500 kg one starting 400 kg full, taking and giving 2 kg an hour with neither compression work nor a daily loss. The factory literals stay `int` for `int`, so nothing either component reports moves. `standard` stands for both (R4): neither names a manufacturer, a technology or a catalogue row. Both factories **deleted** (0 callers each); the four direct constructions in `tests/test_generic_electrolyzer_and_h2_storage.py` became the two presets, with `starting_fill = 0` assigned on top in both tests, which is the one figure they disagree with the module's own default about. No setup and no twin | N | D-28, D-29 |
| `AdvElectrolyzerConfig` (`configuration.py`) | del | | **moved** 2026-09-11: the dead third copy, out of `configuration.py` into `obsolete/components/configuration_hydrogen.py` (D-29) | | D-29 |
| `CSVLoaderConfig` | done | `for_csv_file(…)`, no preset | **converted 2026-09-16** (B7, the conflict-9 precedent): every field is a parameter of the file being read, so there is no default profile to name. `sep`, `decimal`, `multiplier` and `output_description` are field defaults, the constructor's own defaults making them natural; the six that identify the file and the column stay required. `@constructor` takes all ten parameter types (`str`, `int`, `float`, `LoadTypes`, `Units`). No factory existed: the electrolyzer setup and the test's config helper constructed the class directly and both move to the constructor. The electrolyzer twin is byte-identical — a constructor stamps no provenance, so the block still records as literal values (D-3) | N | |
| `ExampleComponentConfig` | done | `standard` | **converted 2026-09-16** (B8): the factory's four values became field defaults — HEATING over WATT, a 1 kW electrical load, a 25 °C start — so `preset_standard` passes the instance name alone, and `capacity` keeps the D-31 law, `45 J/K/m² × conditioned_floor_area_in_m2`, still `AUTO` in the preset. The module-level `SPECIFIC_HEAT_CAPACITY_IN_JOULE_PER_KELVIN_PER_M2` is now a `ClassVar` above its field (the `SolarThermalSystemConfig.COLLECTOR_AREA_IN_M2_PER_APARTMENT` pattern). Factory **deleted**: `get_default_example_component` (1 call site in `tests/functions_for_testing.py`, which builds the preset and substitutes a building-carrying identity with `dataclasses.replace`; 1 assertion in `tests/test_example_component.py`). No setup and no twin | — | N | D-31 |
| `ComponentNameConfig` (template) | done | `standard` | **converted 2026-09-16** (B8): the file every new component author copies now shows all four halves at once — `MAIN_CLASS`, `@preset preset_standard`, the `sized_field` law on `rated_power_in_watt` and the D-31 comment block in place of a contribution. Step 3 of the module docstring is rewritten about the preset (what a wire name is, that it takes the instance name and nothing else, when a class writes a `@constructor` instead), and the preset's own docstring says why a sized field is never mentioned in one. `loadtype` and `unit` became field defaults so the preset can state nothing; `SPECIFIC_RATED_POWER_IN_WATT_PER_M2` became a `ClassVar`, a template carrying module-level state being a template that teaches it. Factory **deleted**: `get_default_template_component` (4 call sites in `tests/test_example_template.py`, two of the tests renamed to what they now check) | — | N | D-31 |
| `ExampleTransformerConfig`, `SimpleStorageConfig` (`thermal`), `SimpleControllerConfig` | done | `standard` / `thermal` / `standard` | **converted 2026-09-16** (B8), all three by the same move: every factory value became a field default and the preset passes the instance name alone. The transformer is ANY over ANY (2 call sites — `system_setups/simple_system_setup_two.py`, `tests/test_example_transformer.py` — and its module lost the `from __future__ import annotations` that made its field types describe as `lt.LoadTypes`); the storage is WARM_WATER / KWH / 50 kWh and its preset is `thermal`, the medium naming it where a vessel has nothing else to describe (R4; 1 call site); the controller has only `component_id`, its two switching thresholds being class constants of the component, so `standard` stands (4 call sites, three of which substitute a building-carrying identity with `dataclasses.replace`). Factories **deleted**: `get_default_transformer`, `get_default_thermal_storage`, `get_default_config`. Only the transformer reaches a twin: the `simple_system_setup_two` block collapses to `preset: standard` | — | N | |

### R4 — Naming `[decided 2026-08-26; Q-P1.9 + A1–A3]`
Names in R3 follow rules 1–5 with A1–A3. Deviations from the supplement are flagged in the survey and decided in §11 (D-5, D-6, D-13, D-14, D-24). A rating suffix is minted only for a real catalogue device; boolean flags are `config:` overrides; `standard` only where one defensible preset exists **and nothing describes it better** — amended 2026-09-14, owner: where a reference (Aachen, CHR01, the German SFH archetype), a technology or a sizing relation names what the preset pins, that name wins, so a twin reads as a description of the system rather than a list of defaults. Executed the same day on six presets: `WeatherConfig` → `aachen`, `UtspLpgConnectorConfig` → `couple_both_at_work`, `BuildingConfig` → `german_single_family_home` (reversing supplement conflict 8), `BatteryConfig` → `sized_to_pv`, `HeatDistributionConfig` and `HeatDistributionControllerConfig` → `building_derived`. The four meters and the DHW storage keep `standard`: a pure aggregator or a 250-litre vessel has nothing to describe. Names are free until P5 (EQ1).

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
- **B6 Providers** — Building `roof_area` contribution (**landed 2026-09-11** with R2.1, ahead of B2), D-22 overrides, D-21 (separate, physics); *`german_multi_family_home` clause struck — void by conflict 8*.
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
- A1 `[proposed]` Batches are reviewed against recorded-file diffs (P3 Q-P3.1 (a), decided 2026-08-28). A batch that lands before its classes have been recorded re-records as soon as the file exists. (Until 2026-09-12 such a batch could fall back on the Python setups' regenerated v1 fixtures; those retired with the v1 JSONs, so the recorded twin is the only fixture.)
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
  **Executed 2026-09-11.** All twelve callers now pass their generator's maximal thermal power, and the parameter
  keeps its name (`max_thermal_power_in_watt_of_heating_system` — "heating system" always meant the generator) with a
  docstring that says so and names C11 as the defect. Setup → generator → field: the eight boiler setups
  (`basic_household_only_heating` [`preset_condensing_gas_12kw`], `household_gas_solar_thermal`, and the
  `gas` / `gas_solar_thermal` / `hydrogen_boiler` / `oil` / `pellets` / `wood_chips` sizers) read
  `GenericBoilerConfig.maximal_thermal_power_in_watt` through `concrete()`; the four heat-pump setups
  (`automatic_default_connections`, `household_heatpump{,_car,_solar_thermal}_building_sizer`) read
  `MoreAdvancedHeatPumpHPLibConfig.set_thermal_output_power_in_watt`. Every setup already built its generator config
  before the storage, so nothing was reordered. Measured: only **three** of the boiler sizers moved, not five —
  `gas`, `gas_solar_thermal` and `hydrogen_boiler` (155.62 → 171.18 l, +10.0 %), because only those three pass
  `number_of_apartments` into the boiler's `SizingContext` and so earn the 1.1× DHW uplift in
  `GenericBoilerConfig.scale_thermal_power`. The `oil`, `pellets` and `wood_chips` sizers (and
  `household_gas_solar_thermal`) resolve their boiler with the load alone although their controllers set
  `with_domestic_hot_water_preparation=True`, so their generator *is* the load and their buffers are unchanged —
  a separate sizing defect in those four `SizingContext` calls, not D-9's to fix. `basic_household_only_heating`
  went 155.62 → **240.00 l** (+54.2 %, its 12 kW nominal boiler), the four heat-pump setups are byte-identical
  (`get_scaled_advanced_hp_lib` sizes the heat pump at exactly the load). Week-gate deviations measured with
  `scripts/golden_check.py`: `household_gas_building_sizer` 42 KPIs (buffer standby loss +7.0 %, upfront investment
  +0.50 %, gas consumption +0.025 %, indoor-temperature deviation below setpoint −10.7 %),
  `basic_household_only_heating` 39 KPIs (standby loss 7.7 → 10.4 kWh, +35 %; upfront investment +6.5 %; gas
  +0.16 %), `household_heatpump_building_sizer` **no deviations**. All twelve setups are in the golden gate
  (`scripts/golden_config.json`) — the bullet's "added to the week gate" clause was already true before this PR, so
  nothing was added here; the owner re-blesses the affected goldens with the `golden-update` workflow.
  The separate sizing defect this measurement exposed is logged as **F-6** in `roadmap/p4_random_findings.md`
  and **executed 2026-09-12**: the four setups pass `number_of_apartments` into the boiler's `SizingContext`,
  their boilers go 7780.75 → 8558.83 W and their buffers move with them (seven goldens re-blessed).
- **D-11** `[answered 2026-09-10]` **(a) convert, record the diff.** The heat-distribution controller's threshold
  becomes the computed one, 16 → 18 °C for the three setups still on the legacy factory
  (`basic_household_only_heating`, `household_gas_solar_thermal`, `automatic_default_connections`); no
  `fixed_threshold_16c` preset is minted for a value nobody chose. `basic_household_only_heating` is blessed **in
  the same commit**, so all 13 heat-distribution setups agree on one law and one of the three is gated.
  — **Executed 2026-09-11.** `get_default_heat_distribution_controller_config` is **deleted**; every one of its
  eleven call sites now spells `HeatDistributionControllerConfig.preset_standard("HeatDistributionController")
  .resolve(SizingContext(...))` with the facts of its own building.
  `get_config_based_on_building_efficiency` stayed: its ten building-sizer setups already computed the threshold
  from the same step table, and retiring it was B1 work, not a physics decision — **done 2026-09-12**, the ten
  setups resolve the same preset and override the emitter alone, and no recorded value moved.
  **The number is 18** because all three setups build the default `BuildingConfig.preset_standard("Building")` —
  7780.75 W over 121.2 m² = 64.198 W/m², the middle band of
  `set_heating_threshold_temperature_based_on_building_efficiency` (≤ 50 → 16, ≤ 80 → 18, > 80 → 20), which is
  kept and is now the preset's law. `basic_household_only_heating` keeps its two author choices explicitly: the
  `RADIATOR` emitter, and the −12.2 °C design outside temperature it has always run its heating curve against
  while its building carries the default −7.0 °C — that fact is passed into the context by hand rather than read
  off the building, so nothing but the threshold moves.
  **The eight tests** (`test_heat_distribution_system`, `test_gas_meter`, `test_fuel_meter`, `test_heating_meter`,
  `test_time_resolution`, `test_controller_l2_energy_management_system`, `test_sizing_engine`,
  `test_config_enum_serialization`) all take the computed 18: not one of them asserts an absolute number, they
  compare a meter against the component it measures within 5 %, so none of them wanted 16 and no threshold is
  pinned anywhere. `test_sizing_engine`'s pilot chain now hands the *unresolved* preset to `resolve_all`, which is
  what that test is about; its resolved numbers are unchanged. A new test in `test_heat_distribution_system.py`
  pins 18.0 for the default building and walks the three bands (16 / 18 / 20, band edges included) through the
  context, so the law is tested where the setups meet it.
  **The measured deviation is zero.** All three twins were re-recorded (they now carry `preset: standard`, the
  threshold 18.0, and the specific heating load 64.198 W/m² where the factory wrote `null`; in
  `automatic_default_connections` the heat pump's space-heating controller inherits the threshold and moves to
  18.0 as well), and `scripts/golden_check.py --param one_week_60s` **passes with an empty deviation list for all
  three** setups. The week gate is the first week of January, where the daily average outside temperature never
  reaches 16 °C, so the threshold never binds; all three are week-only in `golden_config.json`, so **no
  re-bless is required** — the bless the commit anticipated turns out to be a no-op, and the gate demonstrably
  cannot see this class of change. Measured outside the gate instead, one full year of
  `basic_household_only_heating` at 15-minute resolution: 29 of 102 KPIs move, gas consumption
  **+0.060 %** (61 289.8 → 61 326.8 kWh) with opex and CO₂ following it, and the largest relative move is an
  extremum rather than an energy — the minimum flow temperature of the distribution system, 20.16 → 14.83 °C.
  **Amended 2026-09-12 after review:** the intermediate field
  `specific_heating_load_of_building_in_watt_per_m2` is removed from `HeatDistributionControllerConfig` — the
  specific heating load is a property of the building, not of the controller, and sat on the config only so the
  threshold law could read it as a sibling — so the threshold law now reads the building's heating load and its
  conditioned floor area directly from the context and divides them there, a file carries only the threshold, and
  an explicit threshold still overrides the law; the values are unchanged (same bands, same inputs), and all 13
  twins, their grouped twins and their scenario JSONs were re-recorded with nothing but that field gone.
- **D-12** `[answered 2026-09-10]` **(a) fix.** The PV preset's law reads `Self("share_of_maximum_pv_potential")`
  and the field records the share actually applied, so a realized record re-executes (EAC2/UC5). Golden-neutral —
  the fleet's share is 1.0 — but every RenoVisor and building-sizer payload with a share below one, which came
  through the *scaled* path and was recorded as 1.0, now changes to what it always meant. The executing commit and
  the RenoVisor documentation say so explicitly; a silent correction of somebody else's stored payload is worse
  than the bug.
- **D-7** `[answered 2026-09-10]` **(a) adopt the law, record the diff.** Solar-thermal collector area becomes
  `4 m² × number_of_apartments`, which is what `household_gas_solar_thermal.py` looks like it meant when it passed
  `area_m2=4` unmultiplied and what its two sizer twins already do. That setup's week golden is re-blessed in the
  same commit; every MFH archetype in it changes. [2026-09-11: no golden moved, and no MFH archetype was
  involved; see the executed note below for what the gate actually sweeps.]

  **Executed 2026-09-11.** `SolarThermalSystemConfig.area_m2` is a sizable field with the law
  `Size.NUMBER_OF_APARTMENTS * COLLECTOR_AREA_IN_M2_PER_APARTMENT`, the constant named once at module level, and
  the class's one factory defaults the parameter to `AUTO`. (It had a second, the manually-calculated-capex one,
  which could not: it turned the area into euros and kilograms at construction time and so kept a concrete
  default. The first review of this PR deleted it — see the derived-field note below.)
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
  [2026-09-12: the review resolved it the other way. Under the D-16 rule a factory with zero callers is deleted
  rather than carried, so `get_default_solar_thermal_system_manually_calculated_capex` is gone, and with it
  `_compute_device_co2_footprint`, whose only caller it was, the €797/m² figure and the emission-factor
  breakdown, whose sources now live in git history alone; `obsolete/README.md` records the deletion. A
  `flat_plate` preset that wants capex fields of its own starts from those sources, not from this code.]
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
- **D-21** `[answered 2026-09-10; revised 2026-09-17, owner]` **(d) the Weather owns the design temperature and
  states it; the Building reads it.** The 2026-09-10 answer (c) was to add a second provider of
  `heating_reference_temperature_in_celsius` beside the Building, fed from a per-station DIN 12831 table. Three
  things overtook it. The fact acquired a provider and readers of its own — the Building contributes it, and the
  heat-distribution controller and the hplib heat pump read it — so (c)'s stated purpose, "the fact exists for
  group A's heat pumps", is already met. A second provider would therefore buy nothing and cost a
  `sizing_sources` line in every twin that carries either reader, because the engine refuses an ambiguous fact.
  And the table (c) assumed does not exist: `hisim/inputs/housing/data_processed/heating_reference_temperature_per_location.csv`
  is keyed by country, twenty ISO rows, not by any of the 45 stations of `LocationEnum`.

  **The revision.** The design temperature is a property of the *place a building stands in*, not of a weather
  year: it is the design condition a heating system is sized for and it does not move from one year to the next
  (owner, 2026-09-17). So the Weather owns it and never derives it:

  1. `WeatherConfig` gains `heating_reference_temperature_in_celsius`, a **plain field with no default**. Every
     builder states it: `preset_aachen` states one number, `for_location` and `for_data_file` take it as an
     argument, and a scenario file must write it. There is no table lookup and no law anywhere — a weather that
     nobody gave a design temperature cannot be built.
  2. `WeatherConfig` contributes the fact, beside `weather_identity`. A scenario has exactly one weather, so the
     bare fact binds with no consumer naming a source, and there is exactly **one provider**.
  3. `BuildingConfig` stops contributing the fact and reads it: the field becomes
     `sized_field(rule=Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS)` like the two generator-side readers. One
     provider, three readers, and the quantity is stated once per system instead of once per component.
  4. **No result changes.** Every call site states the number it states today: -7.0 in the twelve German setups,
     -12.2 in `basic_household_only_heating`, 5.7 in the Seville setup, which keeps reading the country table
     itself. Whether -7.0 or the table's German -8.7 is the better figure for Aachen is a separate physics
     question, not this one (owner, 2026-09-17).
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
  `__init__` derivation moves to build time (**both done 2026-09-11**, R2.1: the derivation now lives in
  `GenericBoilerConfig.fuel_constants`, which the component's `build` calls, so the two provably agree); a
  `value_type=lt.LoadTypes` codec; and the consistency aggregator for
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
  three `Self("max_power")` laws nor `for_rated_power` is ever minted. **Confirmed by the D-29 execution
  2026-09-11:** `GenericElectrolyzerConfig` left `hisim/` with its module, so the factory with the mandatory
  `p_el` argument — the last of `plan.md`'s D13 "cannot be built from its own defaults" cases outside D-25/D-30 —
  is gone from the live tree and the question closes unasked.
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
  `[count as written 2026-09-11; D-25 moved six of the nine to obsolete/ the same day, leaving three]`
  raise on an unknown device name and name the available ones, so the eight that zero-fill today stop building a
  plausible-looking config of zeros. A file that names a device which does not exist fails at build time, as
  everything else in P2's error catalogue does.

  **Executed 2026-09-12**: the three readers left after D-25 -- `ElectrolyzerConfig.read_config`,
  `ElectrolyzerControllerConfig.read_config` and `PTXControllerConfig.read_config`, all reading
  `hisim/inputs/electrolyzer_manufacturer_config.json` -- raise through one shared lookup,
  `generic_electrolyzer_h2.read_electrolyzer_variant`, which the two controller modules import. An unknown name
  raises `ValueError` naming the name, the file, a `difflib` "Did you mean" near match and the nine device names
  in sorted order, worded as `EnergySystemCatalogueError` words its alternatives (the readers are plain Python
  factories, not the codec, so the exception is plain). The two controllers' per-field `.get(key, 0.0)` zero-fill
  is gone: each declares the fields it reads as `TABLE_FIELDS`, the lookup checks them before returning, and the
  factories index the row directly, so a row missing a field is an error naming the field and the device rather
  than a load of zero. The electrolyzer's own reader requires the three fields it already read without a fallback
  (`electrolyzer_type`, `nom_load`, `max_load`). A missing table file is not caught; the `FileNotFoundError`
  carries the path. **Field check**: all nine variants carry all six fields the two controllers read, so nothing
  had to be invented; five of them (`KumaTecPEM40100`, `McPhy - McLyzer 3200`, `NEL - MC250`, `NEL - MC500`,
  `SunfireHYLINKSOEC`) write `standby_load` as `null`, which the readers passed through before and still do --
  only presence is checked, because `.get` never defaulted a written `null` either. Result-neutral: the live
  setup `electrolyzer_with_renewables` builds `HTecME450` through two of the readers and its recorded twin is
  unchanged. Tested in `tests/test_electrolyzer_manufacturer_table.py`.
- **D-32** `[answered 2026-09-11]` **(a) both key groups go, one commit each.** Postprocessing reads the Weather
  component's `config.location` directly instead of `SingletonDictKeyEnum.LOCATION`; the six Building 5R1C keys go once
  this branch's D-16 move has put `controller_mpc` and `controller_pid` in `obsolete/`, after which they have no reader
  at all. The last construction-time globals leave the Weather and the Building.
  **Part one executed 2026-09-11 (#686)**: `region_of(ppdt)` in `postprocessing_main` reads the run's own
  Weather and `LOCATION` is gone from the enum, value 6 left as a comment. **Part two executed 2026-09-11**:
  `Building.build()` stops writing the six 5R1C values and `SingletonDictKeyEnum` loses members 8-13, whose
  numbers a comment holds in place of a renumbering; the stale comment naming the PID and MPC controllers goes
  with the writes. Result-neutral -- nothing outside the retired controllers ever read them. The three
  `HEATFLUX*NODEFORECAST` keys the Building writes in its predictive branch stayed at the time: the survey
  parked them with the runtime forecast half, and dropping them would retire the whole `predictive` forecast
  path. **Part three executed 2026-09-12**: the owner decided to retire exactly that path, so the branch, the
  three keys, the `predictive` config flags and the two publications the branch consumed are gone.
- **D-25** `[answered 2026-09-11]` **(b) obsolete the RSOC trio and the fuel-cell table path.** `RsocConfig`,
  `RsocControllerConfig` and `RsocBatteryControllerConfig` move to `obsolete/` with their tests, together with the fuel
  cell's manufacturer-table builders — verified 2026-09-11 that `hisim/inputs/` still holds only
  `electrolyzer_manufacturer_config.json`, so neither missing JSON has reappeared. `FuelCellConfig` and
  `FuelCellControllerConfig` keep preset `pem`, whose hand-typed defaults do build. Consistent with D-16's rule: moved,
  not deleted.

  **Executed 2026-09-11.** Neither `hisim/inputs/fuel_cell_manufacturer_config.json` nor
  `hisim/inputs/rSOC_manufacturer_config.json` is in this repository, and as far as its history shows neither
  ever was. **Moved:** `generic_rsoc`, `controller_l1_rsoc` and `controller_l2_rsoc_battery_system` to
  `obsolete/components/`, with their three tests to `obsolete/tests/` and `rSOC_efficiency_curve_data.json` —
  read by `generic_rsoc` and nothing else — to `obsolete/inputs/`; and the six fuel-cell table functions, cut
  out of three live modules and archived verbatim in `obsolete/components/fuel_cell_manufacturer_table.py`.
  **Stayed:** the two hand-typed PEM presets, `FuelCellConfig.get_default_pem_fuel_cell_config` and
  `FuelCellControllerConfig.get_default_fuel_cell_controller_config`, which build; and
  `controller_l2_xtp_fuel_cell_ems` with its fields, its `XtpOperationMode` enum, its component and its tests —
  but **`XTPControllerConfig` now has no default builder**, the table path having been its only factory, until
  the B7 batch gives it a preset. `hisim/economics/adapter.py` loses its `Rsoc` and `RsocBatteryController` rows
  and `tests/test_economics_extraction.py` the matching two, and `docs/modules/components.rst` its three entries
  plus `docs/modules/inputs.rst` one. Nothing was deleted and no recorded result moves: no setup, energy system
  or golden reference names any of these classes. D-26 narrows with this: its "raise on an unknown device" now
  applies only to the three survivors `generic_electrolyzer_h2`, `controller_l1_electrolyzer_h2` and
  `controller_l2_ptx_energy_management_system`, all three readers of the one manufacturer table that does exist,
  `electrolyzer_manufacturer_config.json`.
- **D-29** `[answered 2026-09-11]` **(c) keep the `_and_h2_storage` pair** — the survey recommended (a), the other way
  round. `ElectrolyzerWithStorageConfig` and `ElectrolyzerWithHydrogenStorageConfig`
  (`generic_electrolyzer_and_h2_storage.py`) survive as the richer model, the one that has waste energy and part-load
  percentages, and `generic_electrolyzer.py` and `generic_hydrogen_storage.py` move to `obsolete/` with their tests.
  Follow-up decided the same day: `controller_l1_electrolyzer` (`L1GenericElectrolyzerController`, written for the
  retiring electrolyzer) and its test move to `obsolete/` too; `controller_l1_chp`'s optional default connection from
  `generic_hydrogen_storage` is dropped, not re-pointed; and `AdvElectrolyzerConfig` in `configuration.py`, the dead
  third copy, goes to `obsolete/` as well. The live setup `electrolyzer_with_renewables` uses the third electrolyzer,
  `generic_electrolyzer_h2` (the manufacturer-table one), and is untouched.
  **Executed 2026-09-11.** Five things moved and no module or class was deleted: `generic_electrolyzer.py` and
  `generic_hydrogen_storage.py` to `obsolete/components/`, with `tests/test_generic_electrolyzer.py` (which built
  the controller as well as the electrolyzer) and `tests/test_h2storage.py` to `obsolete/tests/`;
  `controller_l1_electrolyzer.py` to `obsolete/components/`; and `HydrogenStorageConfig` with
  `AdvElectrolyzerConfig` out of the shared `hisim/components/configuration.py` into the new
  `obsolete/components/configuration_hydrogen.py`, the way D-16 made `configuration_legacy.py`.
  `configuration.py` keeps everything else. `L1CHPController` lost `get_default_connections_from_h2_storage`,
  its registration and the docstring line that listed it; its optional `HydrogenSOC` input stays, and no
  connection to the surviving `HydrogenStorage` was added, so a fuel-cell setup wanting one wires it by hand.
  `DeviceEnergySpecs.BY_CLASS_NAME` lost its `GenericElectrolyzer` and `L1GenericElectrolyzerController` rows
  and `tests/test_economics_extraction.py` the two matching keys; `docs/modules/components.rst` lost three
  module entries and now names `generic_electrolyzer_and_h2_storage` as the hydrogen store.
  `tests/test_controller_l1_generic_electrolyzer.py` stayed behind despite its name — it builds the live
  `controller_l1_electrolyzer_h2.ElectrolyzerController`, not the retired one. Result-neutral: no setup, scenario
  JSON, energy-system file or golden reference named either retiring pair.
- **D-30** `[answered 2026-09-11]` **(a) move to `obsolete/`.** `generic_smart_device` and `SmartDeviceConfig` go in
  one commit, before any preset is minted for a component whose `__init__` raises `KeyError: 'utsp_reports'` on every
  construction; §6's `smart_devices_included` copy-law candidate goes with it. Under D-16's rule, moved, not deleted.
  **Executed 2026-09-11**: `obsolete/components/generic_smart_device.py`, unchanged. `DeviceEnergySpecs.BY_CLASS_NAME`
  and the class-to-module table of `tests/test_economics_extraction.py` lose their `SmartDevice` row and
  `docs/modules/components.rst` its module entry; `ComponentType.SMART_DEVICE`, `KpiTagEnumClass.SMART_DEVICE` and
  `SingletonDictKeyEnum.SMARTDEVICESINCLUDED` stay, the last because nothing ever wrote or read it (D-32) —
  and for that reason it was deleted with the rest of the dead keys on 2026-09-12.

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
| D-25 | ~~Two manufacturer JSONs absent; six H₂/RSOC classes unbuildable~~ | `[answered 2026-09-11]` **(b)** executed — the RSOC trio (plus its tests and its efficiency-curve data file) and the fuel-cell table path move to `obsolete/` (re-verified 2026-09-11), nothing deleted; `FuelCellConfig`/`FuelCellControllerConfig` keep `pem`; `XTPControllerConfig` is left with no default builder until B7 | R3 H₂, R6 |
| D-29 | ~~Two electrolyzer + two H₂-storage classes for two devices~~ | `[answered 2026-09-11]` **(c)** keep the `_and_h2_storage` pair — survey recommended (a); `generic_electrolyzer`, `generic_hydrogen_storage`, `controller_l1_electrolyzer` and `AdvElectrolyzerConfig` move to `obsolete/`, and `controller_l1_chp`'s H₂-storage default connection is dropped | R3 H₂ |
| D-30 | ~~`generic_smart_device` defective: delete or fix?~~ | `[answered 2026-09-11]` **(a)** `generic_smart_device` and `SmartDeviceConfig` move to `obsolete/` in one commit, with §6's `smart_devices_included` candidate | R6 |
| **Physics changes (R5)** | | | |
| D-9 | ~~**C11**: buffer volume from generator power (+10 % on 5 golden sizers, +54 % one ungated, ≤ +72 % MFH) or bless the load?~~ | `[answered 2026-09-10]` **(b) fix the physics** — the law reads the generator's maximal thermal power; own commit, five golden sizers re-blessed at +10 %, `basic_household_only_heating` (+54 %) added to the week gate in the same commit | R3 buffer, R5 |
| D-11 | ~~HDS controller: 16 → 18 °C for 3 ungated setups, or a `fixed_threshold_16c` preset?~~ | `[answered 2026-09-10]` **(a) convert, record the diff** — no `fixed_threshold_16c`; `basic_household_only_heating` blessed in the same commit | R3, R5 |
| D-12 | ~~PV `share_of_maximum_pv_potential` recorded as 1.0 by the scaled factory: fix, preserve, or delete the field?~~ | `[answered 2026-09-10]` **(a) fix** — the law reads `Self(...)` and the field records the real share; golden-neutral, but RenoVisor payloads with a share below one change to what they meant, stated in the commit and the RenoVisor docs | R3 PV, R5 |
| D-7 | ~~Solar-thermal `area_m2 = 4 × apartments` law (one setup passes 4 unmultiplied)~~ | `[answered 2026-09-10]` **(a) adopt, record the diff** — executed 2026-09-11; the golden did not move, the setup's `two_dwellings` probe did | R3, R5 |
| D-4 | ~~CHP controller 42/50 °C flip across axes: bug or preserve?~~ | `[answered 2026-09-10; reversed 2026-09-11 after the #683 review]` **(b) preserve** — the four factories keep their 2023 values and the asymmetry is recorded as unexplained rather than guessed, so the class converts as **four** presets (`gas`, `hydrogen`, `gas_with_buffer`, `hydrogen_with_buffer`), not two plus a shared buffer override; the buffer helper, the module-private constants, the `FuelCellController` name and the `__post_init__` min<max check stay from the PR. No result change | R3, R5 |
| D-21 | ~~`heating_reference_temperature` from the Weather in B6 (physics), defer, or plumb the fact only?~~ | `[answered 2026-09-10]` **(c) plumb the fact**; **revised 2026-09-17, owner: (d)** the Weather owns the value as a field with no default, states it at every builder, and contributes it; the Building stops contributing and reads it. One provider, three readers, no table, no law, no result change | R2.1, R3 Weather + Building |
| **Naming / shape** | | | |
| D-3 | ~~Air conditioner's 12-field database selection: constructor, multi-field law, or freeze the device?~~ | `[answered 2026-09-10]` **(a)** constructor `for_building_load(...)` runs the database search at build time; no `AUTO` field, and the selection is invisible to `sizing_sources` | R3 |
| D-5 | ~~`advanced_fuel_cell.CHPConfig`: `standard` or `hydrogen`?~~ | `[answered 2026-09-10]` **(b)** `hydrogen`, a recorded deviation from the supplement's `standard` rule | R3, R4 |
| D-6 | ~~Drop the two `air_water_8kw` presets (default arguments, not devices)?~~ | `[answered 2026-09-10]` **(a)** drop both; the four tests pass `set_thermal_output_power_in_watt` explicitly | R3, R4 |
| D-10 | ~~Buffer l/kW factor: add `sizing_option` field, five presets, or a numeric `litres_per_kilowatt` field?~~ | `[answered 2026-09-10]` **(a)** a recorded `sizing_option` enum field (five members into the wire vocabulary) and one preset `buffer`; the law reads the sibling enum, a first — survey recommended (c) | R3 |
| D-13 | ~~Keep `rooftop_10kw` (four setups depend on the default)?~~ | `[answered 2026-09-10]` **(b)** drop; the three setups that still call the factory bare get a `power_in_watt: 10000` override and are re-recorded | R3, R4 |
| D-14 | ~~`BatteryConfig` rating preset (`standard_5kwh` names a 10 kWh factory)~~ | `[answered 2026-09-10]` **(a)** `standard` only; capacity and inverter power come from the law or the caller | R3, R4 |
| D-15 | ~~Meters copy carrier and fuel constants from the generator (3 new facts + aggregator) or plain preset fields?~~ | `[answered 2026-09-11]` **(b)** copy laws — survey recommended (a); accepts three `GenericBoilerConfig` facts, a `LoadTypes` codec and the `Many` consistency aggregator, so a group-A class enters the meter group | R3 meters |
| D-17 | ~~Unify the two `PositionHotWaterStorageInSystemSetup` enums before P5?~~ | `[answered 2026-09-11]` **(b)** leave both — survey recommended (a); both spellings freeze at P5 and a file may state the topology twice | R3, C-P4.5 |
| D-33 | ~~Field defaults + sparse presets, `MAIN_CLASS`, tables as data, inline contributions, plain docstrings — and `kw_only` with a capex mixin?~~ | `[answered 2026-09-14, owner]` **the first five, yes (R1.1); `kw_only` and the mixin no** — the dataclass machinery is not varied until the failed interpreter migration is understood; revisit after P5 | R1.1, R1.2 |
| D-24 | ~~`ChargingStationConfig`: `standard` = 3.7 kW, 11 kW, or constructor only?~~ | `[answered 2026-09-11]` **(c)** no `standard`; `for_charging_station_set` only | R3 |
| D-27 | ~~`operation_mode: str` ×3 → one shared enum, three enums, or strings?~~ | `[answered 2026-09-11]` **(b)** three per-module enums (PTX controller, XTP controller, RSOC battery controller) | C-P4.5 |
| D-28 | ~~`GenericElectrolyzerConfig`: `standard` + `Self` laws, constructor, or delete?~~ | `[answered 2026-09-11]` **moot by D-29** — the survivor `ElectrolyzerWithStorageConfig` ships preset `standard` with the 2.4 kW factory values; no constructor, nothing derived | R3 |
| D-31 | ~~Template: add a `sized_field` + contribution; turn `example_component`'s `45 × 121.2` into the real law?~~ | `[answered 2026-09-11]` **(a)** both; the 11 example tests then depend on the sizing kernel. **Deviation on execution (#688):** `FactContribution.__post_init__` admits only `SizingContext` fields, so the template gets no `SIZING_CONTRIBUTIONS` entry — a documented comment block with the contribution's exact shape points at the real providers instead; the sized field, the `AUTO` factory and the example's real law ship as decided | R3, AC-P4.9 |
| **Providers and gates** | | | |
| D-18 | ~~`for_location` lacks the direct-file parameters 7 golden setups need~~ | `[answered 2026-09-11]` **(b)** a second constructor `for_data_file(path, data_source)`; `for_location` stays catalogue-shaped — survey recommended (a); one more wire name frozen at P5 | B1 Weather, R3 |
| D-19 | ~~Constructor arguments undecoded — executor fix, widen signatures, or leave constructors Python-only?~~ | `[answered 2026-09-11]` **(a)** `codec.decode_argument` shared by `config:` and `constructor:`, decoded in `_call_builder`, EF-1A at the argument's key path, and `@constructor` refuses undecodable parameter types at import | R2.3, B1 |
| D-20 | ~~`for_household` ignores its argument in the predefined-profile mode~~ | `[answered 2026-08-27, review of #592]` **(a)** implemented in P2: `data_acquisition_mode` parameter, profile derived from the household, refusal listing the shipped households and the computing modes | B1 UTSP |
| D-22 | ~~Building's 20-field post-construction mutation: `config:` overrides, wider constructor, or `for_measured_envelope`?~~ | `[answered 2026-09-11]` **(a)** the 14 non-parameters become sparse `config:` overrides; the recorder diffs against a fresh preset, so no twin carries ten nulls | R3 Building, P3 recorder |
| D-26 | ~~Five `read_config` readers zero-fill on unknown device: raise everywhere?~~ — after D-25 only three are left (`generic_electrolyzer_h2`, `controller_l1_electrolyzer_h2`, `controller_l2_ptx_energy_management_system`, all reading `electrolyzer_manufacturer_config.json`) | `[answered 2026-09-11]` **(a)** all nine readers raise, listing the available device names — **executed 2026-09-12** for the three survivors, through one shared lookup | R3 H₂ |
| D-32 | ~~Delete the `LOCATION` key and the six 5R1C keys?~~ | `[answered 2026-09-11]` **(a)** both, one commit each: postprocessing reads the Weather's `config.location`; the six 5R1C keys follow D-16's MPC/PID move, after which they have no reader | R2.4 |

## 12. Glossary

See the epic. P4-specific: **batch** — one mechanical PR converting a family of classes under R1; **gate** — an own commit that must precede a batch (R2, R6); **physics change** — a conversion that changes any existing setup's numbers (R5); **legacy factory** — a `get_default_*`/`get_scaled_*`/`config_*`/`control_*`/`read_config` classmethod replaced by a preset or constructor; **wire-format registry** — R3, the list of names EQ1 freezes at P5.
