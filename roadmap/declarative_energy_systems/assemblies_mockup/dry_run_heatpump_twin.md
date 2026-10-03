# Dry run: does the heat-pump composed file expand to its twin?

**Date:** 2026-10-03 · **Against:** `origin/main` 0921f9bd · **Spec:** `../assemblies_spec.md` (§2.3 is the procedure)

The owner asked whether the design "really works as planned". This document takes the composed base file for the
heat-pump generator with defaults, `composed_heatpump_default.energy_system.yaml`, expands it by hand exactly as §2.3
describes, writes the result as `expanded_heatpump_default.energy_system.yaml`, and compares that component by
component with the twin it must reproduce: `energy_systems/household_heatpump_building_sizer.grouped.energy_system.yaml`,
option `ems_with_battery` (the probe columns `baseline`, `half_pv`, `radiator`, `nineties_building`; `no_battery_ems`
is the other option, checked in §8). Every difference is classified **(i)** rename only, **(ii)** intended difference
per D9's list, or **(iii)** a GAP. The gaps are numbered G1–G18; §9 lists them with an assessment.

## 1. Input

- Site (`components:`): `Building`, `UTSPConnector`, `Weather`, `HeatDistributionController`, `HeatDistributionSystem`
  with the twin's names and values (D9: Aachen, `german_single_family_home`, `couple_both_at_work`).
- `imports:` `heating: heating/air_source_heat_pump` (`bind: {ems_modifier: control}`), `dhw: dhw/indirect_cylinder`
  (`bind: {circuit: heating.dhw}`), `pv: pv/array` (instance `pv_system`), `battery: storage/battery` (instance
  `battery`), `control: control/ems_self_consumption`, `grid: supply/electricity_grid` (`observes: [{connector:
  electricity_flow, flow: net}]`).
- Assemblies: the files of this directory. Connector types: `connectors.yaml`.

## 2. Step 1 — resolve, preset, parameters

Innermost first: none of the six imports has inner imports, so the order is the file's.

| Import | Preset | Parameters as resolved | Checks |
|---|---|---|---|
| `heating` | none (defaults = `de_twin`) | `thermal_power_in_watt: AUTO`, `scop_en14825_w35/w55: none`, `with_buffer: true`, `buffer_volume_in_liter: AUTO`, `serves_dhw: true`, `control: traditional` | `requires: {buffer_volume_in_liter: [with_buffer]}` holds. **Unit check (D16 c)** passes: `thermal_power_in_watt` (WATT) feeds `set_thermal_output_power_in_watt` (suffix `_in_watt`), `buffer_volume_in_liter` feeds `volume_heating_water_storage_in_liter` |
| `dhw` | none | `volume_in_liter: AUTO`, `tank_and_pipe_insulated: true` | `tank_and_pipe_insulated` feeds no field (†): a parameter feeding nothing must be allowed for request leaves the class cannot use yet — a load warning, not an error |
| `pv.pv_system` | none | `azimuth_in_degree: 180`, `tilt_in_degree: 30`, `power_in_watt: AUTO`, `share_of_roof_area: 1.0`, `shading_losses_in_percent: 0`, `location: AACHEN` | `at_most_one_of` holds (`power_in_watt` not given). **Unit check FAILS:** `azimuth_in_degree` (DEGREES) feeds `PVSystemConfig.azimuth`, `tilt_in_degree` feeds `tilt` — no suffix, no declared unit → load error under D16 (c). **G10** |
| `battery.battery` | none | `capacity_in_kwh: AUTO`, `power_in_watt: AUTO`, `days_to_cover: none` | `capacity_in_kwh` (KWH) feeds `custom_battery_capacity_generic_in_kilowatt_hour`: the suffix `_in_kilowatt_hour` is not in §2.6's table (`_in_kwh` is) → **G10** again |
| `control` | none | `priorities:` the default list, three offsets 2/10/10 | offsets equal `EMSConfig` defaults (`controller_l2_energy_management_system.py:54-58`). **Unit check FAILS** the same way: `*_offset_value` has no suffix → **G10** |
| `grid` | none | — | — |

## 3. Step 2 — port states and internal variants

Internal variants: `heating.dhw_side` = `with_dhw` (`serves_dhw: true`), `heating.buffer` = `parallel`
(`with_buffer: true`); the `when:` lists partition both booleans. `control: traditional` keeps `ControllerSH` on
`preset: standard`.

| Port | State | Reason |
|---|---|---|
| `heating.sh` | required, active | `active_when: {with_buffer: [true]}` |
| `heating.dhw` (provided circuit) | required, active | `serves_dhw: true` |
| `heating.electricity_dhw`, `heating.ems_modifier.dhw_storage_temperature_offset` | active | `serves_dhw: true` |
| `heating.solar_coil`, `dhw.solar_coil` | optional, **unbound** | no candidate (no solar import); no fallback to apply |
| `heating.ems_modifier` | optional, **bound** `control` | candidate `control__EMS` exists, intent written (D8 i) |
| `Building.temperature_offset`, `HeatDistributionController.temperature_offset`, `UTSPConnector.electricity_use` | optional, **bound** `control` | intent written on the site entry (`bind: control`) — **G13** |
| `control.flows` (observer port) | default selection | `electricity_flow` of flow production, consumption, storage |
| `grid.reading` (observer port) | **written** | `observes: [{connector: electricity_flow, flow: net}]` on the import (spec §4.3; G4 resolved) |
| everything else | required, active | — |

Inactive ports: none. Declined ports: none. Fallbacks applied: none.

## 4. Step 3 — addresses and the rename map

| Twin name | Expanded name | Structured address |
|---|---|---|
| `Building`, `UTSPConnector`, `Weather`, `HeatDistributionController`, `HeatDistributionSystem` | unchanged | site (path `[]`) |
| `MoreAdvancedHeatPumpHPLibControllerSH` | `heating__ControllerSH` | `[heating]`, `ControllerSH` |
| `MoreAdvancedHeatPumpHPLib` | `heating__HeatPump` | `[heating]`, `HeatPump` |
| `HeatPumpControllerDHW` | `heating__ControllerDHW` | `[heating]`, `ControllerDHW` |
| `SimpleHotWaterStorage` | `heating__Buffer` | `[heating]`, `Buffer` |
| `DHWStorage` | `dhw__DHWStorage` | `[dhw]`, `DHWStorage` |
| `PVSystem` | `pv__pv_system__PVSystem` | `[pv/pv_system]`, `PVSystem` |
| `Battery` | `battery__battery__Battery` | `[battery/battery]`, `Battery` |
| `L2EMSElectricityController` | `control__EMS` | `[control]`, `EMS` |
| `ElectricityMeter` | `grid__ElectricityMeter` | `[grid]`, `ElectricityMeter` |

Every internal reference is rewritten (`Buffer` → `heating__Buffer` in `ControllerSH`, `HeatPump`; `ControllerDHW`
and `ControllerSH` in `HeatPump`; `HeatPump` in `Buffer`). No member of the unselected variants exists, so no input
item has to be dropped.

## 5. Step 4 — binding, port by port

"Default" means the port bound its one compatible partner (§3.3), "written" that the file named it. The last column
is what the lowering wrote, decided by the lowering-form rule of G2.

| Port | Relation | Partner | How | Lowered to |
|---|---|---|---|---|
| `heating.weather` (→ `ControllerSH`) | link (data) | `Weather.weather` | default | bare `Weather` in `heating__ControllerSH` |
| `heating.weather` (→ `HeatPump`) | link (data) | `Weather.weather` | default | explicit `TemperatureAmbient`, `TemperatureInputPrimary` ← `Weather.DailyAverageOutsideTemperatures` (the class's weather defaults cover only `TemperatureAmbient`, `more_advanced_heat_pump_hplib.py` default connections) |
| `heating.flow_temperature` | link (signal) | `HeatDistributionController.flow_temperature` | default | bare `HeatDistributionController` in `heating__ControllerSH` — **G1** |
| `heating.sh` | link (circuit) | `HeatDistributionSystem.sh` | default | bare `HeatDistributionSystem` in `heating__Buffer` (return temperature + mass flow, both the HDS's outputs); bare `heating__Buffer` in `HeatDistributionSystem` (supply temperature, the buffer's) |
| `heating.electricity` | existence check | `grid.connection` | exists | nothing: electricity has no link; the two `electricity_flow` ports it covers are observed (below) |
| `heating.electricity_sh`, `.electricity_dhw` | observed + controlled | `control.flows` | via `ems_modifier` | two EMS feeds, `dispatch: {}` |
| `heating.ems_modifier` | actuate | `control` | written | bare `control__EMS` in `heating__ControllerSH` (`SimpleHotWaterStorageTemperatureModifier`) and in `heating__ControllerDHW` (`DHWStorageTemperatureModifier`) |
| `heating.dhw` ↔ `dhw.circuit` | link (circuit) | each other | written (`dhw`) | bare `heating__HeatPump` in `dhw__DHWStorage` (supply + mass flow, the heat pump's); bare `dhw__DHWStorage` in `heating__HeatPump` and in `heating__ControllerDHW` (return leg = cylinder temperature, read by the generator and by its L1) |
| `heating.heat_load`, `.design_temperature`, `.room_setpoint`, `.emitter_type`, `.heating_threshold` | sizing | `Building`, `Weather`, `Building`, `HeatDistributionController` ×2 | default | nothing: the bare-fact rule binds the same single providers — **G7** |
| `dhw.hot_water_demand` | link (data) | `UTSPConnector.hot_water_demand` | default | bare `UTSPConnector` in `dhw__DHWStorage` |
| `dhw.apartments` | sizing | `Building` | default | nothing |
| `pv.pv_system.weather` | link (data) | `Weather` | default | bare `Weather` |
| `pv.pv_system.roof_area`, `.weather_id` | sizing | `Building`, `Weather` | default | nothing |
| `pv.pv_system.production` | observed | `control.flows` | selection | EMS feed, production |
| `battery.battery.pv_peak_power` | sizing, many | `[pv__pv_system__PVSystem]` | default | nothing (one provider) — **G7** |
| `battery.battery.electricity` | observed + controlled (required) | `control.flows`, `control__EMS` | selection | EMS feed with `dispatch: {target_input: LoadingPowerInput}`; no input on the battery itself |
| `UTSPConnector.electricity_use` | observed + controlled | `control.flows`, `control__EMS` | written on the site | EMS feed, `dispatch: {}` |
| `Building.temperature_offset` | actuate | `control` | written on the site | bare `control__EMS` in `Building` |
| `HeatDistributionController.temperature_offset` | actuate | `control` | written on the site | bare `control__EMS` in `HeatDistributionController` |
| `control.flows` | observe | the four flows above + residents | default selection | the five feeds of §6 |
| `grid.reading` | observe | `control.grid_net` (`flow: net`, `covers: flows`) | written selection | `grid__ElectricityMeter`'s one input `control__EMS.TotalElectricityToOrFromGrid`, tags `[ELECTRICITY_PRODUCTION]`, weight 999 (the meter's declaration maps `net` to its production channel) — **G6** |

Double-count check (spec §4.3): the meter observes `grid_net` and none of the flows it covers — passes. Had the `grid`
import kept the default selection (every `electricity_flow`), it would match `grid_net` and the five flows: load error.

Where each lowered item lands inside the target's `inputs` list is the position of the `{$port: …}` /
`{$provides: …}` placeholder (G3).

### The three circuit quantities, who owns which today

| Circuit | Mass flow (pump) | Supply temperature | Return temperature |
|---|---|---|---|
| site HDS ↔ heating buffer (`sh`, crosses site–heating) | HDS: `WaterMassFlowHDS` → buffer `WaterMassFlowRateFromHeatDistributionSystem` | buffer: `WaterTemperatureToHeatDistribution` → HDS `WaterTemperatureInput` | HDS: `WaterTemperatureOutput` → buffer `WaterTemperatureFromHeatDistribution` |
| heat pump ↔ buffer (internal to heating) | heat pump: `MassFlowOutputSH` | heat pump: `TemperatureOutputSH` | buffer: `WaterTemperatureToHeatGenerator` → heat pump `TemperatureInputSecondarySH` and `ControllerSH.WaterTemperatureInput` |
| heat pump ↔ cylinder (`dhw`, crosses heating–dhw) | heat pump: `MassFlowOutputDHW` | heat pump: `TemperatureOutputDHW` | cylinder: `WaterTemperatureToHeatGenerator` → heat pump `TemperatureInputSecondaryDHW` and `ControllerDHW.WaterTemperatureInputFromDHWStorage` |

So the `sh` port of the heating assembly owns the supply leg only and its partner owns pump and return
(`heat_distribution_system.py:206-281`, `simple_water_storage.py:643-711`); the `dhw` port owns pump and supply, the
cylinder the return. This holds for `PARALLEL` only: with no buffer the HDS grows `WaterMassFlowInput`
(`heat_distribution_system.py:230-243`) and the pump moves to the generator — **G8**. The return leg of `dhw` is the
measured variable of the generator's L1 controller: an L1 loop of the heating assembly closes over a value of another
import. That is legal (it travels through the circuit port), but the heating assembly's isolation test must stub the
cylinder temperature, not only a heat sink.

## 6. Step 4 — selectors: every feed, with tags and derived weight

The EMS's feeds are the matches of its own selection, `control.flows` (§4.3). Priority list (default) → weights
(§4.4):

| Entry | Matches | Class default | Derived weight |
|---|---|---|---|
| `RESIDENTS` | `UTSPConnector.electricity_use` | 1 (`controller_l2_energy_management_system.py:409`) | 1 |
| `[HEAT_PUMP_BUILDING, ELECTRIC_HEATING_SH]` | `heating.electricity_sh` | 2 (`:444`) | 2 |
| `[HEAT_PUMP_DHW, ELECTRIC_HEATING_DHW]` | `heating.electricity_dhw` | 3 (`:466`) | 3 |
| `SOLAR_THERMAL_SYSTEM` | — | 4 | — |
| `CAR_BATTERY` | — | 5 | — |
| `BATTERY` | `battery.battery.electricity` | 6 (`:555`) | 6 |

| Feed (expanded) | `component_type` | tags | weight | dispatch | Twin line | Equal? |
|---|---|---|---|---|---|---|
| `battery__battery__Battery.AcBatteryPowerUsed` | BATTERY | EMS_CONTROLLED | 6 | `target_input: LoadingPowerInput` | :117-123 | yes (i) |
| `heating__HeatPump.ElectricalInputPowerDHW` | HEAT_PUMP_DHW | EMS_CONTROLLED | 3 | `{}` | :124-129 | yes (i) |
| `heating__HeatPump.ElectricalInputPowerSH` | HEAT_PUMP_BUILDING | EMS_CONTROLLED | 2 | `{}` | :130-135 | yes (i) |
| `pv__pv_system__PVSystem.ElectricityOutput` | PV | PRODUCTION | 999 | — | :136-140 | yes (i) |
| `UTSPConnector.ElectricalPowerConsumption` | RESIDENTS | EMS_CONTROLLED | 1 | `{}` | :141-146 | yes |
| `control__EMS.TotalElectricityToOrFromGrid` → meter | — | PRODUCTION | 999 | — | :106-109 | yes (i), flow `net` (G6) |

The tags follow from flow and binding (connectors.yaml, `electricity_flow`); they are equal to the EMS's declared
default feeds (`:364-591`), which is what the twin's explicit lines record. The `dispatch: {}` blocks are required by
the EMS's controlled channel (`DispatchRule.REQUIRED`, `:214`, `:227`): a `via:` port and the residents' `controllable: {}`
port both lower to an empty dispatch — G18. Feed resolution sorts by weight, participant, output
(`feed_resolution.py:10-14`), so the written order does not matter; the derived dispatch port names
`DispatchTo<instance>_<input>` (`controller_l2_energy_management_system.py:906`) carry the new instance names (i).

## 7. Step 4–5 — sizing_sources and the import record

No `sizing_sources` line is written: every fact read across a boundary has exactly one provider in the expanded
system (`heating_load_in_watt`, `number_of_apartments`, `roof_area_in_m2`, `set_heating_temperature_in_celsius` from
`Building`; `heating_reference_temperature_in_celsius`, `weather_identity` from `Weather`;
`heat_distribution_system_type`, `set_heating_threshold_outside_temperature_in_celsius`,
`water_mass_flow_rate_in_kg_per_second` from `HeatDistributionController`; `maximal_thermal_power_in_watt` from
`heating__HeatPump`, read only inside `heating`; `pv_peak_power_in_watt` from the one array), and the twin writes none.
This needs the rule "a port lowers to no line when the bare-fact rule binds the same providers" (G7), and the battery's
many-cardinality read needs §13 step 3 (`Many` with `Sum`, `laws.py:228-241` raise today) to yield the same number
for one array. The import record is the comment block at the end of `expanded_heatpump_default.energy_system.yaml`.

## 8. Comparison with the twin

### 8.1 Component by component (`ems_with_battery`)

| # | Twin component (line) | Expanded | Class, preset, config | Inputs | Class |
|---|---|---|---|---|---|
| 1 | `Building` (:8-19) | `Building` | equal | `Weather`, `L2EMSElectricityController`→`control__EMS`, `UTSPConnector`, `HeatDistributionSystem`; same order | (i) |
| 2 | `UTSPConnector` (:20-24) | `UTSPConnector` | equal | none in both (its `WarmWaterMassInput`/`WarmWaterTemperatureInput` are optional, `loadprofilegenerator_utsp_connector.py:449-462`, and unbound in both) | equal |
| 3 | `Weather` (:25-32) | `Weather` | equal | none | equal |
| 4 | `PVSystem` (:33-39) | `pv__pv_system__PVSystem` | equal (`location: AACHEN` from the parameter default; azimuth/tilt/share/power write nothing, G9) | `Weather` | (i), but **position** moves: G14 |
| 5 | `HeatDistributionController` (:40-46) | same name | equal | `L2EMS…`→`control__EMS`, `Weather`, `Building` | (i) |
| 6 | `MoreAdvancedHeatPumpHPLibControllerSH` (:47-54) | `heating__ControllerSH` | equal | `Weather`, `HeatDistributionController`, `control__EMS`, `heating__Buffer`; same order | (i) |
| 7 | `HeatPumpControllerDHW` (:55-60) | `heating__ControllerDHW` | equal | `control__EMS`, `dhw__DHWStorage` | (i); position: G14 |
| 8 | `MoreAdvancedHeatPumpHPLib` (:61-74) | `heating__HeatPump` | equal (`with_domestic_hot_water_preparation: true`; AUTO power, `none` SCOPs write nothing: G9) | 4 bare + 2 explicit weather wires, same order | (i) |
| 9 | `DHWStorage` (:75-80) | `dhw__DHWStorage` | equal | `UTSPConnector`, `heating__HeatPump` | (i); position: G14 |
| 10 | `SimpleHotWaterStorage` (:81-88) | `heating__Buffer` | equal (`sizing_option` written, volume AUTO not) | `HeatDistributionSystem`, `heating__HeatPump` | (i) |
| 11 | `HeatDistributionSystem` (:89-95) | same name | equal | `Building`, `HeatDistributionController`, `heating__Buffer` | (i); position: G14 |
| 12 | `ElectricityMeter` (:102-109) | `grid__ElectricityMeter` | equal | one feed, equal | (i); position: G14 |
| 13 | `Battery` (:110-112) | `battery__battery__Battery` | equal | none in both | (i) |
| 14 | `L2EMSElectricityController` (:113-146) | `control__EMS` | equal | five feeds, equal (§6) | (i) |

Anything the twin wires that no port carries: **nothing left** once `flow_temperature` (G1) is a port — the earlier
mockup lacked it, and the Building/HDS controller modifiers needed site `provides` entries (G13). Anything the
expansion adds: nothing beyond `metadata.imports` (ii, "the EMS as an import").

### 8.2 Beyond the components

| Difference | Class |
|---|---|
| Component names, result columns, KPI source names, economics subjects, the EMS's dispatch port names, every test or translator target that names a twin component (`hisim/renovisor/translate.py` `Targets`, `tests/renovisor/test_twin_cost_declarations.py`) | (i) under the rename map |
| `metadata.imports` and the source maps | (ii) |
| **Component order.** Twin: Building, UTSP, Weather, **PV**, HDSCtrl, CtrlSH, CtrlDHW, HP, **DHWStorage**, Buffer, **HDS**, Meter, Battery, EMS. Expanded: Building, UTSP, Weather, HDSCtrl, **HDS**, CtrlSH, HP, CtrlDHW, Buffer, **DHWStorage**, **PV**, Battery, EMS, Meter | **(iii) G14** |

### 8.3 The other twin shape (`metered_directly`, :147-171)

Composed file: drop the `control` and `battery` imports, `heating`'s `bind: {ems_modifier: control}` and — G13 — the
three `bind: control` lines on site entries (otherwise the file names a missing import), and the `grid` import's
`observes:` line, which would now match nothing. Expansion: the meter observes its default selection, every
`electricity_flow`; `heating.ems_modifier` has no candidate (unbound, legal); the four flows become meter feeds with tags from flow and binding: HP DHW and SH `ELECTRICITY_CONSUMPTION_UNCONTROLLED` 999 with
`HEAT_PUMP_DHW`/`HEAT_PUMP_BUILDING`, PV `ELECTRICITY_PRODUCTION` 999 with `PV`, the occupancy
`ELECTRICITY_CONSUMPTION_UNCONTROLLED` 999 **with `RESIDENTS`** — the twin's line :168-171 has no `component_type`,
because the meter's declared feed for `UtspLpgConnector` carries none (`electricity_meter.py:304-326`) while its feeds
for the heat pump and PV do. Both shapes are two selections of the meter, up to that tag (G15) and G13.

## 9. Gaps (iii), with assessment

"Not written yet" means the design holds and the spec or a class owes a sentence or a declaration; "design change"
means a decision is needed.

| Gap | Finding | Assessment |
|---|---|---|
| **G1** | The heat pump's SH L1 reads its set point `HeatingFlowTemperatureFromHeatDistributionSystem` from the site's `HeatDistributionController` (twin :52). This crosses the site–heating boundary outside the `sh` circuit; the previous mockup had no port for it. | Not written yet: a `control_signal` **link** (`flow_temperature`). The spec should say that a `control_signal` port is also a plain set-point link, not only an actuation, and that every heating assembly with an L1 on the HDS curve has this port. |
| **G2** | §3.2 lowers `hydronic_circuit` and `control_signal` to explicit wires; the twin writes every crossing edge as a bare name, except the heat pump's two weather wires, which are explicit. | Design change (small, spec text): lower to a **bare name iff the target class's default connections from the partner's class are exactly the wires the binding makes**, else explicit wires. Checked against all 14 input items of the twin that cross an import boundary: it reproduces each, including the explicit weather pair. |
| **G3** | Nothing in the spec says where a port's lowered items go inside a member's `inputs`, or how a member refers to a port. Order of items is part of byte identity. | Not written yet: `{$port: <name>}` / `{$provides: <name>}` placeholders in `inputs` (as in these files); a placeholder of an inactive or unbound port expands to nothing. |
| **G4** | The meter's wiring seemed to depend on whether a `control` import exists, which D8's principle forbids. | **Resolved: no decision needed** (owner, 2026-10-03). Electricity has no link end; meter and EMS each write their own `observes:`, and the twin shapes are two meter selector configurations (flows, or the EMS's `net`). Adding `control` changes the `grid` import only through its author; left at the default, the double-count check fails the file. |
| **G5** | The EMS's modifier outputs are fixed per component type, not per feed: `SpaceHeatingWaterStorageTemperatureModifier` and `BuildingIndoorTemperatureModifier` move only while a `HEAT_PUMP_BUILDING` feed is ranked, the DHW modifier only for `HEAT_PUMP_DHW`/`HEAT_PUMP` (`controller_l2_energy_management_system.py:747-790`). Hence (a) the site's `temperature_offset` ports are inert unless the heating import is a heat pump with `ems_modifier` bound — a coupling of three imports no port shows; (b) an immersion heater's `ems_modifier` (`dhw/storage_water_heater`) has no EMS output; (c) two heat pumps would share one modifier. | Design change in the **component**, not the format: modifier outputs per controlled feed (as dispatch outputs already are), then `ems_modifier` lowers like a dispatch target. Not needed for the twin (wiring is equal); needed before any second heat-pump-like consumer or the immersion heater. |
| **G6** | The meter reads the EMS's grid balance as `ELECTRICITY_PRODUCTION` at 999 (twin :106-109); no connector flow said "grid balance". | Written: flow `net` with `covers:` in the registry (spec §3.2), mapped by the meter's declaration to its production channel; no `feed:` override. |
| **G7** | The battery's many-cardinality `pv_peak_power` and every single-provider fact read across a boundary must write **no** `sizing_sources` line, or the expansion differs from the twin. | Not written yet: "a `sizing_fact` port lowers to nothing when the engine's bare-fact rule binds the same providers". §13 step 3 (`Many` + `Sum`) must land first. |
| **G8** | `with_buffer: false` needs the site's `HeatDistribution.position_hot_water_storage_in_system` (and the heat pump's) to switch, and moves the pump to the generator: a parameter of an import changing a site config. Not exercised by the defaults (`PARALLEL` on both sides). | Design change (small, with hydronic stage D): the circuit binding carries pump ownership (`end.pump`), and the HDS's storage position becomes a sized field fed by a fact the heating assembly exports — never a direct patch of a site entry. |
| **G9** | A parameter default (`AUTO`, `none`, the EMS offsets, PV azimuth/tilt/share) substituted into a field writes a line the twin does not have. | Not written yet: "a substitution equal to the value the member's preset already gives is omitted from the expanded config". |
| **G10** | D16 (c) refuses real fields: `PVSystemConfig.azimuth`/`tilt` (no suffix), the battery's `_in_kilowatt_hour`, the EMS's `*_temperature_offset_value`, the air conditioner's `nominal_cooling_power_w`, the collector's `area_m2`. As written, **the default composed file fails to load** on `pv/array` and `control`. | Not written yet, but blocking: declare units on these fields (`field(metadata={"unit": …})`) or extend the suffix table, before §13 step 4. |
| **G11** | `PVSystemConfig.location` repeats a site value in an import (documentation only today). | Minor: drop the parameter or read a site fact; does not affect results. |
| **G12** | (EV, not in this twin.) An EV's commuting distance and charging power select the LPG travel route set and charging station — occupancy settings; `CarConfig.source_weight` must differ per car (`generic_car.py:122-124`). | Design change: an import whose parameters change the occupancy needs a declared occupancy-side contract (the request decides both at once); the per-instance weight is derived like the EMS weights. |
| **G13** | Optional ports on **site** entries (residents' consumption, the two `BuildingTemperatureModifier`s) need intent (D8), and the only place the format offers is the site entry (`bind: control`). The site then names an import, so the no-EMS world needs a different site file — against D17's one site. | Design change (small): intents for site ports are written on the controller import (`control: {…, controls: [UTSPConnector.electricity_use, Building.temperature_offset, HeatDistributionController.temperature_offset]}`); the site only publishes. |
| **G14** | Component order cannot be reproduced: imports follow the site, so the twin's PV-before-HDS-controller and HDS-last order is out of reach. File order is registration order, hence the order components are iterated within a time step (`executor.py:304-311`); convergence is absolute 1e-4 per output (`component.py:197-203`), the goldens compare at relative 1e-9 (`tests/test_golden_kpis.py:117-118`). The D9 gate "one-week run equals the existing golden" may fail on order alone. | Design decision, must be settled before §13 step 4: (a) reorder the Python setup once to the composed order and re-record twin and golden in a PR of its own, then gate byte-for-byte; or (b) gate on a canonical (sorted) comparison of the files and a tolerance for the run. Recommendation (a): it keeps the 1e-9 policy. Run the twin in both orders first to measure the effect. |
| **G15** | The meter's occupancy feed has no `component_type` in the twin, while the port carries `RESIDENTS` (only `metered_directly`). | Intended difference to add to D9's reviewed list (the meter channels match by subset, so it lands in the same channel); or the meter declaration drops component types. |
| **G16** | §3.2 gave `hot_water_demand` as kg/s and °C; the occupancy's output is `WaterConsumption`, litres per step, `WARM_WATER` (`loadprofilegenerator_utsp_connector.py:503`). | Fixed in the spec with this dry run. |
| **G17** | `serves_dhw` must agree with whether the `dhw` import binds `heating.dhw`. | No gap: both directions fail loudly (an active required circuit unbound; a `bind:` to an inactive port). Recorded because the translator writes the same fact twice. |
| **G18** | D21 says the EMS actuates only L1 modifiers and the battery, but it also *ranks* consumers it cannot steer: residents (weight 1), solar thermal (4), and the heat pump feeds themselves, all with `dispatch: {}` (twin :129, :135, :146). | Not written yet: a `controllable: {}` port (rank-only) and a `via:` port both lower to `dispatch: {}`; one sentence in §4.4. |

### Verdict

The design reproduces the twin's **graph** exactly: every component, class, preset, config line, input item, feed tag,
weight and dispatch block of `ems_with_battery` comes out of the composed file under the rename map, and
`metered_directly` comes out of the same file without `control`, `battery` and the grid's `observes:` line, up to one
tag (G15). It does **not yet
reproduce the file byte for byte, nor guarantee the golden**: G14 (component order) is the one finding that can make
the D9 gate fail on numbers, and G10 makes the composed file fail to load before anything else runs. Three points need
an owner decision (G8, G13, G14; G4 is resolved), one is a component change (G5, EMS modifiers per feed), and the rest are rules
and declarations the spec or the classes owe (G1–G3, G6, G7, G9, G10, G18).
