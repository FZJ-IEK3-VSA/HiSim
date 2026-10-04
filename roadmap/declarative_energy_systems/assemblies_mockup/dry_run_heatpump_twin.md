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
- Site binding verbs: `optional-bind: {temperature_modifier: control}` on `Building` and `HeatDistributionController`.
- `imports:` `heating: heating/air_source_heat_pump` (`optional-bind: {ems_modifier: control}`), `dhw: dhw/indirect_cylinder`
  (`bind: {circuit: heating.dhw}`), `pv: pv/array` (instance `pv_system`), `battery: storage/battery` (instance
  `battery`), `control: control/ems_self_consumption`, `grid: supply/electricity_grid` (`observes: [{output:
  TotalElectricityToOrFromGrid}]`).
- Evaluation order (spec §2.3, D23): `order:` Building 1, UTSPConnector 2, Weather 3, `pv` 4,
  HeatDistributionController 5, `heating` 6, `dhw` 9, HeatDistributionSystem 11, `grid` 12, `battery` 13, `control` 14;
  `heating/air_source_heat_pump` orders its members ControllerSH, ControllerDHW, HeatPump, Buffer (`order:` 1–4).
- Assemblies: the files of this directory. Ports lower to the member classes' default connections from the partner's
  class, observers select by the existing tags (spec §3.2, §4.1; owner, 2026-10-03: no connector types).

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
| `heating.electricity_dhw`, `heating.dhw_temperature`, `heating.ems_modifier` into `ControllerDHW` | active | `serves_dhw: true` |
| `heating.solar_coil`, `dhw.solar_coil` | optional, **unbound** | no candidate (no solar import); no fallback to apply |
| `heating.ems_modifier` | optional, **bound** `control` | `optional-bind:`, partner `control-EMS` exists (D8 i) |
| `Building.temperature_modifier`, `HeatDistributionController.temperature_modifier` | optional, **bound** `control` | `optional-bind:` on the site entry, partner exists — **G13 resolved** |
| `UTSPConnector.ElectricalPowerConsumption` | observed (no port) | an electricity output has no link end; a `bind:` on it is refused (spec §3.3) |
| `control.flows` (observer port) | default selection | every electricity output the EMS class declares dynamic default connections from |
| `grid.reading` (observer port) | **written** | `observes: [{output: TotalElectricityToOrFromGrid}]` on the import (spec §4.3; G4 resolved) |
| everything else | required, active | — |

Inactive ports: none. Declined ports: none. Fallbacks applied: none.

## 4. Step 3 — addresses and the rename map

| Twin name | Expanded name | Structured address |
|---|---|---|
| `Building`, `UTSPConnector`, `Weather`, `HeatDistributionController`, `HeatDistributionSystem` | unchanged | site (path `[]`) |
| `MoreAdvancedHeatPumpHPLibControllerSH` | `heating-ControllerSH` | `[heating]`, `ControllerSH` |
| `MoreAdvancedHeatPumpHPLib` | `heating-HeatPump` | `[heating]`, `HeatPump` |
| `HeatPumpControllerDHW` | `heating-ControllerDHW` | `[heating]`, `ControllerDHW` |
| `SimpleHotWaterStorage` | `heating-Buffer` | `[heating]`, `Buffer` |
| `DHWStorage` | `dhw-DHWStorage` | `[dhw]`, `DHWStorage` |
| `PVSystem` | `pv-pv_system-PVSystem` | `[pv/pv_system]`, `PVSystem` |
| `Battery` | `battery-battery-Battery` | `[battery/battery]`, `Battery` |
| `L2EMSElectricityController` | `control-EMS` | `[control]`, `EMS` |
| `ElectricityMeter` | `grid-ElectricityMeter` | `[grid]`, `ElectricityMeter` |

Order paths and the flat sequence (§9.1): Building 1, UTSPConnector 2, Weather 3, `pv-pv_system-PVSystem` 4.1.1,
HeatDistributionController 5, `heating-ControllerSH` 6.1, `heating-ControllerDHW` 6.2, `heating-HeatPump` 6.3,
`heating-Buffer` 6.4, `dhw-DHWStorage` 9.1, HeatDistributionSystem 11, `grid-ElectricityMeter` 12.1,
`battery-battery-Battery` 13.1.1, `control-EMS` 14.1.

Every internal reference is rewritten (`Buffer` → `heating-Buffer` in `ControllerSH`, `HeatPump`; `ControllerDHW`
and `ControllerSH` in `HeatPump`; `HeatPump` in `Buffer`). No member of the unselected variants exists, so no input
item has to be dropped.

## 5. Step 4 — binding, port by port

"Default" means the port bound its one partner (§3.3: of its partner class, the other end of its circuit, the provider
of its carrier), "written" that the file named it. The last column is what the lowering wrote: the receiving class's
default connections from the partner's class as a bare name, or the explicit wires the assembly names (G2).

| Port | Relation | Partner | How | Lowered to |
|---|---|---|---|---|
| `heating.weather` (→ `ControllerSH`) | link (data) | `Weather` | default | bare `Weather` in `heating-ControllerSH` |
| `heating.weather` (→ `HeatPump`) | link (data) | `Weather` | default | explicit `TemperatureAmbient`, `TemperatureInputPrimary` ← `Weather.DailyAverageOutsideTemperatures`, named in the assembly's placeholder: the last resort of spec §3.2, since the class's default connections from `Weather` are not these two (`more_advanced_heat_pump_hplib.py` default connections); fixed by adding them to the class |
| `heating.flow_temperature` | link (set point) | `HeatDistributionController` | default | bare `HeatDistributionController` in `heating-ControllerSH` — **G1** |
| `heating.sh` | link (circuit) | `HeatDistributionSystem.sh` | default | bare `HeatDistributionSystem` in `heating-Buffer` (return temperature + mass flow, both the HDS's outputs); bare `heating-Buffer` in `HeatDistributionSystem` (supply temperature, the buffer's) |
| `heating.electricity` | existence check | `grid.connection` | exists | nothing: electricity has no link; the two electricity outputs it names are observed (below) |
| `heating.electricity_sh`, `.electricity_dhw` | observed + controlled | `control.flows` | via `ems_modifier` | two EMS feeds, `dispatch: {}` |
| `heating.ems_modifier` | actuate | `control` | `optional-bind:` | bare `control-EMS` in `heating-ControllerSH` (`SimpleHotWaterStorageTemperatureModifier`) and in `heating-ControllerDHW` (`DHWStorageTemperatureModifier`) |
| `heating.dhw` ↔ `dhw.circuit` | link (circuit) | each other | written (`dhw`) | bare `heating-HeatPump` in `dhw-DHWStorage` (supply + mass flow, the heat pump's); bare `dhw-DHWStorage` in `heating-HeatPump` (return leg = cylinder temperature). The circuit's ends are `HeatPump` and `DHWStorage` only: `ControllerDHW` reads none of its three outputs |
| `heating.dhw_temperature` (→ `ControllerDHW`) | link (data) | `dhw-DHWStorage` (class `SimpleDHWStorage`) | default | bare `dhw-DHWStorage` in `heating-ControllerDHW` (`WaterTemperatureInputFromDHWStorage`, its default connections from `SimpleDHWStorage`, as the twin's bare `DHWStorage`) |
| `heating.heat_load`, `.design_temperature`, `.room_setpoint`, `.emitter_type`, `.heating_threshold` | sizing | `Building`, `Weather`, `Building`, `HeatDistributionController` ×2 | default | nothing: the bare-fact rule binds the same single providers — **G7** |
| `dhw.hot_water_demand` | link (data) | `UTSPConnector` | default | bare `UTSPConnector` in `dhw-DHWStorage` |
| `dhw.apartments` | sizing | `Building` | default | nothing |
| `pv.pv_system.weather` | link (data) | `Weather` | default | bare `Weather` |
| `pv.pv_system.roof_area`, `.weather_id` | sizing | `Building`, `Weather` | default | nothing |
| `pv.pv_system.production` | observed | `control.flows` | selection | EMS feed, production |
| `battery.battery.pv_peak_power` | sizing, many | `[pv-pv_system-PVSystem]` | default | nothing (one provider) — **G7** |
| `battery.battery.electricity` | observed + controlled (required) | `control.flows`, `control-EMS` | selection | EMS feed with `dispatch: {target_input: LoadingPowerInput}`; no input on the battery itself |
| `UTSPConnector.ElectricalPowerConsumption` | observed, ranked | `control.flows` | selection (rank-only by the EMS's declaration) | EMS feed, `dispatch: {}` |
| `Building.temperature_modifier` | actuate | `control` | `optional-bind:` on the site | bare `control-EMS` in `Building` |
| `HeatDistributionController.temperature_modifier` | actuate | `control` | `optional-bind:` on the site | bare `control-EMS` in `HeatDistributionController` |
| `control.flows` | observe | the four flows above + residents | default selection | the five feeds of §6 |
| `grid.reading` | observe | `control-EMS.TotalElectricityToOrFromGrid` (by name) | written selection | `grid-ElectricityMeter`'s one input `control-EMS.TotalElectricityToOrFromGrid`, tags `[ELECTRICITY_PRODUCTION]`, weight 999 (the meter's declaration from the EMS class, †) — **G6** |

Double-count check (spec §4.3): the meter observes the EMS's grid balance and none of the outputs the EMS observes —
passes. Had the `grid` import kept the default selection (every electricity output the meter declares), it would match
the grid balance and the five flows: load error.

Where each lowered item lands inside the target's `inputs` list is the position of the `{$port: …}` placeholder, in an
assembly member or a site entry alike (G3).

### The three circuit quantities, who owns which today

| Circuit | Mass flow (pump) | Supply temperature | Return temperature |
|---|---|---|---|
| site HDS ↔ heating buffer (`space_heating`, crosses site–heating) | HDS: `WaterMassFlowHDS` → buffer `WaterMassFlowRateFromHeatDistributionSystem` | buffer: `WaterTemperatureToHeatDistribution` → HDS `WaterTemperatureInput` | HDS: `WaterTemperatureOutput` → buffer `WaterTemperatureFromHeatDistribution` |
| heat pump ↔ buffer (internal to heating) | heat pump: `MassFlowOutputSH` | heat pump: `TemperatureOutputSH` | buffer: `WaterTemperatureToHeatGenerator` → heat pump `TemperatureInputSecondarySH` and `ControllerSH.WaterTemperatureInput` |
| heat pump ↔ cylinder (`dhw`, crosses heating–dhw) | heat pump: `MassFlowOutputDHW` | heat pump: `TemperatureOutputDHW` | cylinder: `WaterTemperatureToHeatGenerator` → heat pump `TemperatureInputSecondaryDHW`; `ControllerDHW.WaterTemperatureInputFromDHWStorage` reads the cylinder through the need `dhw_temperature`, not the circuit |

So the `space_heating` port of the heating assembly owns the supply leg only and its partner owns pump and return
(`heat_distribution_system.py:206-281`, `simple_water_storage.py:643-711`); the `dhw` port owns pump and supply, the
cylinder the return. This holds for `PARALLEL` only: with no buffer the HDS grows `WaterMassFlowInput`
(`heat_distribution_system.py:230-243`) and the pump moves to the generator — **G8**. The return leg of `dhw` is the
measured variable of the generator's L1 controller: an L1 loop of the heating assembly closes over a value of another
import. That is legal (it travels through the need `dhw_temperature`, partner class `SimpleDHWStorage`, not through the
circuit, whose three outputs the L1 does not read), but the heating assembly's isolation test must stub the cylinder
temperature, not only a heat sink.

## 6. Step 4 — selectors: every feed, with tags and derived weight

The EMS's feeds are the matches of its own selection, `control.flows` (§4.3). Priority list (default) → weights
(§4.4):

| Entry | Matches | Class default | Derived weight |
|---|---|---|---|
| `RESIDENTS` | `UTSPConnector.ElectricalPowerConsumption` | 1 (`controller_l2_energy_management_system.py:409`) | 1 |
| `[HEAT_PUMP_BUILDING, ELECTRIC_HEATING_SH]` | `heating.electricity_sh` | 2 (`:444`) | 2 |
| `[HEAT_PUMP_DHW, ELECTRIC_HEATING_DHW]` | `heating.electricity_dhw` | 3 (`:466`) | 3 |
| `SOLAR_THERMAL_SYSTEM` | — | 4 | — |
| `CAR_BATTERY` | — | 5 | — |
| `BATTERY` | `battery.battery.electricity` | 6 (`:555`) | 6 |

| Feed (expanded) | `component_type` | tags | weight | dispatch | Twin line | Equal? |
|---|---|---|---|---|---|---|
| `battery-battery-Battery.AcBatteryPowerUsed` | BATTERY | EMS_CONTROLLED | 6 | `target_input: LoadingPowerInput` | :117-123 | yes (i) |
| `heating-HeatPump.ElectricalInputPowerDHW` | HEAT_PUMP_DHW | EMS_CONTROLLED | 3 | `{}` | :124-129 | yes (i) |
| `heating-HeatPump.ElectricalInputPowerSH` | HEAT_PUMP_BUILDING | EMS_CONTROLLED | 2 | `{}` | :130-135 | yes (i) |
| `pv-pv_system-PVSystem.ElectricityOutput` | PV | PRODUCTION | 999 | — | :136-140 | yes (i) |
| `UTSPConnector.ElectricalPowerConsumption` | RESIDENTS | EMS_CONTROLLED | 1 | `{}` | :141-146 | yes |
| `control-EMS.TotalElectricityToOrFromGrid` → meter | — | PRODUCTION | 999 | — | :106-109 | yes (i), selected by name (G6) |

The tags and weights are the EMS's own dynamic default connections (`:364-591`), the declarations its selectors
match by (spec §4.1), and that is what the twin's explicit lines record. The `dispatch: {}` blocks are required by the
EMS's controlled channel (`DispatchRule.REQUIRED`, `:214`, `:227`): a `via:` output and the residents' rank-only feed
both lower to an empty dispatch — G18. Feed resolution sorts by weight, participant, output
(`feed_resolution.py:10-14`), so the written order does not matter; the derived dispatch port names
`DispatchTo<instance>_<input>` (`controller_l2_energy_management_system.py:906`) carry the new instance names (i).

## 7. Step 4–5 — sizing_sources and the import record

No `sizing_sources` line is written: every fact read across a boundary has exactly one provider in the expanded
system (`heating_load_in_watt`, `number_of_apartments`, `roof_area_in_m2`, `set_heating_temperature_in_celsius` from
`Building`; `heating_reference_temperature_in_celsius`, `weather_identity` from `Weather`;
`heat_distribution_system_type`, `set_heating_threshold_outside_temperature_in_celsius`,
`water_mass_flow_rate_in_kg_per_second` from `HeatDistributionController`; `maximal_thermal_power_in_watt` from
`heating-HeatPump`, read only inside `heating`; `pv_peak_power_in_watt` from the one array), and the twin writes none.
This needs the rule "a port lowers to no line when the bare-fact rule binds the same providers" (G7), and the battery's
many-cardinality read needs §13 step 3 (`Many` with `Sum`, `laws.py:228-241` raise today) to yield the same number
for one array. The import record is the comment block at the end of `expanded_heatpump_default.energy_system.yaml`.

## 8. Comparison with the twin

### 8.1 Component by component (`ems_with_battery`)

| # | Twin component (line) | Expanded | Class, preset, config | Inputs | Class |
|---|---|---|---|---|---|
| 1 | `Building` (:8-19) | `Building` | equal | `Weather`, `L2EMSElectricityController`→`control-EMS`, `UTSPConnector`, `HeatDistributionSystem`; same order | (i) |
| 2 | `UTSPConnector` (:20-24) | `UTSPConnector` | equal | none in both (its `WarmWaterMassInput`/`WarmWaterTemperatureInput` are optional, `loadprofilegenerator_utsp_connector.py:449-462`, and unbound in both) | equal |
| 3 | `Weather` (:25-32) | `Weather` | equal | none | equal |
| 4 | `PVSystem` (:33-39) | `pv-pv_system-PVSystem` | equal (`location: AACHEN` from the parameter default; azimuth/tilt/share/power write nothing, G9) | `Weather` | (i) |
| 5 | `HeatDistributionController` (:40-46) | same name | equal | `L2EMS…`→`control-EMS`, `Weather`, `Building` | (i) |
| 6 | `MoreAdvancedHeatPumpHPLibControllerSH` (:47-54) | `heating-ControllerSH` | equal | `Weather`, `HeatDistributionController`, `control-EMS`, `heating-Buffer`; same order | (i) |
| 7 | `HeatPumpControllerDHW` (:55-60) | `heating-ControllerDHW` | equal | `control-EMS`, `dhw-DHWStorage` | (i) |
| 8 | `MoreAdvancedHeatPumpHPLib` (:61-74) | `heating-HeatPump` | equal (`with_domestic_hot_water_preparation: true`; AUTO power, `none` SCOPs write nothing: G9) | 4 bare + 2 explicit weather wires, same order | (i) |
| 9 | `DHWStorage` (:75-80) | `dhw-DHWStorage` | equal | `UTSPConnector`, `heating-HeatPump` | (i); position: **neutral swap** with #10 (ii, §9.1) |
| 10 | `SimpleHotWaterStorage` (:81-88) | `heating-Buffer` | equal (`sizing_option` written, volume AUTO not) | `HeatDistributionSystem`, `heating-HeatPump` | (i) |
| 11 | `HeatDistributionSystem` (:89-95) | same name | equal | `Building`, `HeatDistributionController`, `heating-Buffer` | (i) |
| 12 | `ElectricityMeter` (:102-109) | `grid-ElectricityMeter` | equal | one feed, equal | (i) |
| 13 | `Battery` (:110-112) | `battery-battery-Battery` | equal | none in both | (i) |
| 14 | `L2EMSElectricityController` (:113-146) | `control-EMS` | equal | five feeds, equal (§6) | (i) |

Anything the twin wires that no port carries: **nothing left** once `flow_temperature` (G1) is a port — the earlier
mockup lacked it, and the Building/HDS controller modifiers needed site `ports:` entries with `optional-bind:` (G13). Anything the
expansion adds: nothing beyond `metadata.imports` (ii, "the EMS as an import").

### 8.2 Beyond the components

| Difference | Class |
|---|---|
| Component names, result columns, KPI source names, economics subjects, the EMS's dispatch port names, every test or translator target that names a twin component (`hisim/renovisor/translate.py` `Targets`, `tests/renovisor/test_twin_cost_declarations.py`) | (i) under the rename map |
| `metadata.imports` and the source maps | (ii) |
| **Component order.** Twin: Building, UTSP, Weather, PV, HDSCtrl, CtrlSH, CtrlDHW, HP, **DHWStorage**, **Buffer**, HDS, Meter, Battery, EMS. Expanded (`order:`, §9.1): the same, with **Buffer** before **DHWStorage** | (ii) neutral swap (§9.1) |

### 8.3 The other twin shape (`metered_directly`, :147-171)

Composed file: drop the `control` and `battery` imports and the `grid` import's `observes:` line, which would now
match nothing. Nothing else changes: the site's two `optional-bind: {temperature_modifier: control}` lines and
`heating`'s `optional-bind: {ems_modifier: control}` stay and are inert, recorded "not bound: partner absent" (G13
resolved). Expansion: the meter observes its default selection, every electricity output its class declares; the
three modifier inputs stay unwired, as in the twin; the four flows become meter feeds with the tags of the meter's own
declarations: HP DHW and SH `ELECTRICITY_CONSUMPTION_UNCONTROLLED` 999 with `HEAT_PUMP_DHW`/`HEAT_PUMP_BUILDING`, PV
`ELECTRICITY_PRODUCTION` 999 with `PV`, the occupancy `ELECTRICITY_CONSUMPTION_UNCONTROLLED` 999 with no
`component_type`, because the meter's declared feed for `UtspLpgConnector` carries none (`electricity_meter.py:304-326`)
— exactly the twin's line :168-171. Both shapes are one site and two selections of the meter (G15 resolved).

## 9. Gaps (iii), with assessment

"Not written yet" means the design holds and the spec or a class owes a sentence or a declaration; "design change"
means a decision is needed.

| Gap | Finding | Assessment |
|---|---|---|
| **G1** | The heat pump's SH L1 reads its set point `HeatingFlowTemperatureFromHeatDistributionSystem` from the site's `HeatDistributionController` (twin :52). This crosses the site–heating boundary outside the `space_heating` circuit; the previous mockup had no port for it. | Written (spec §3.2): a need `flow_temperature` with partner `HeatDistributionController`; a set-point input is the same kind of need as any other, lowered to the L1's default connections from that class. Every heating assembly with an L1 on the HDS curve has this port. |
| **G2** | The earlier spec lowered circuits and signals to explicit wires; the twin writes every crossing edge as a bare name, except the heat pump's two weather wires, which are explicit. | Written (spec §3.2, owner 2026-10-03): a port lowers to the receiving class's **default connections** from the partner's class, written as a bare name; explicit wires only where the assembly names them, the last resort, which leaks the partner's output names and is fixed by adding the default connection. Checked against all 14 input items of the twin that cross an import boundary: it reproduces each, the explicit weather pair as the one named in `heating/air_source_heat_pump`. |
| **G3** | Nothing in the spec says where a port's lowered items go inside a member's `inputs`, or how a member refers to a port. Order of items is part of byte identity. | Not written yet: `{$port: <name>}` placeholders in `inputs`, in assembly members and site entries alike (as in these files); a placeholder of an inactive or unbound port expands to nothing. |
| **G4** | The meter's wiring seemed to depend on whether a `control` import exists, which D8's principle forbids. | **Resolved: no decision needed** (owner, 2026-10-03). Electricity has no link end; meter and EMS each write their own `observes:`, and the twin shapes are two meter selector configurations (flows, or the EMS's grid balance). Adding `control` changes the `grid` import only through its author; left at the default, the double-count check fails the file. |
| **G5** | The EMS's modifier outputs are fixed per component type, not per feed: `SpaceHeatingWaterStorageTemperatureModifier` and `BuildingIndoorTemperatureModifier` move only while a `HEAT_PUMP_BUILDING` feed is ranked, the DHW modifier only for `HEAT_PUMP_DHW`/`HEAT_PUMP` (`controller_l2_energy_management_system.py:747-790`). Hence (a) the site's `temperature_modifier` ports are inert unless the heating import is a heat pump with `ems_modifier` bound — a coupling of three imports no port shows; (b) an immersion heater's `ems_modifier` (`dhw/storage_water_heater`) has no EMS output; (c) two heat pumps would share one modifier. | Design change in the **component**, not the format: modifier outputs per controlled feed (as dispatch outputs already are), then `ems_modifier` lowers like a dispatch target. Not needed for the twin (wiring is equal); needed before any second heat-pump-like consumer or the immersion heater. |
| **G6** | The meter reads the EMS's grid balance as `ELECTRICITY_PRODUCTION` at 999 (twin :106-109); the format had no word for "grid balance". | Written (spec §4.1, §4.3): the `grid` import selects the EMS's output by name, `TotalElectricityToOrFromGrid`; the meter class declares a dynamic default connection from the EMS (†, to add) that puts it on the production channel at 999, so no `feed:` override; the double-count check compares the meter's selection with the EMS's. |
| **G7** | The battery's many-cardinality `pv_peak_power` and every single-provider fact read across a boundary must write **no** `sizing_sources` line, or the expansion differs from the twin. | Not written yet: "a `sizing_fact` port lowers to nothing when the engine's bare-fact rule binds the same providers". §13 step 3 (`Many` + `Sum`) must land first. |
| **G8** | `with_buffer: false` needs the site's `HeatDistribution.position_hot_water_storage_in_system` (and the heat pump's) to switch, and moves the pump to the generator: a parameter of an import changing a site config. Not exercised by the defaults (`PARALLEL` on both sides). | Design change (small, with hydronic stage D): the circuit naming convention carries pump ownership, and the HDS's storage position becomes a sized field fed by a fact the heating assembly exports — never a direct patch of a site entry. |
| **G9** | A parameter default (`AUTO`, `none`, the EMS offsets, PV azimuth/tilt/share) substituted into a field writes a line the twin does not have. | Not written yet: "a substitution equal to the value the member's preset already gives is omitted from the expanded config". |
| **G10** | D16 (b) refuses fed fields without a declared unit: `PVSystemConfig.azimuth`/`tilt` (no suffix), the battery's `_in_kilowatt_hour`, the EMS's `*_temperature_offset_value`, the air conditioner's `nominal_cooling_power_w`, the collector's `area_m2`. As written, **the default composed file fails to load** on `pv/array` and `control`. | Not written yet, but blocking: declare units on these fields (`field(metadata={"unit": …})`) in the §13 step 1 sweep; the suffix is documentation only (D16 b). |
| **G11** | `PVSystemConfig.location` repeats a site value in an import (documentation only today). | Minor: drop the parameter or read a site fact; does not affect results. |
| **G12** | (EV, not in this twin.) An EV's commuting distance and charging power select the LPG travel route set and charging station — occupancy settings; `CarConfig.source_weight` must differ per car (`generic_car.py:122-124`). | Design change: an import whose parameters change the occupancy needs a declared occupancy-side contract (the request decides both at once); the per-instance weight is derived like the EMS weights. |
| **G13** | Optional ports on **site** entries (the two `BuildingTemperatureModifier`s) need intent (D8), and with `bind:` as the only verb the site names an import, so the no-EMS world needs a different site file — against D17's one site. | **Resolved** (owner, 2026-10-03; spec §3.1, §3.3, D8 (i)): three verbs alike on site entries and imports — `bind:` (partner must exist), `optional-bind:` (binds if the partner exists, else inert and recorded "not bound: partner absent"), `none:` (declines). The site carries `optional-bind: {temperature_modifier: control}` on `Building` and `HeatDistributionController`; `heating` and `dhw` write `optional-bind: {ems_modifier: control}`. The residents' `ElectricalPowerConsumption` is observed, never bound: a `bind:` on electricity is refused. |
| **G14** | Component order: with imports after the site, the twin's sequence (PV before the HDS controller, the HDS after the storages) was out of reach, and order is the evaluation order within a time step, which moves results (`roadmap/convergence_order_findings.md`). | **Resolved** (owner, 2026-10-03; spec §2.3, D23): a declared, nested `order:`. The composed file reproduces the twin's sequence up to one exchange, buffer before cylinder, proven neutral in §9.1 and listed in D9 as a "neutral swap". No twin or golden is re-recorded. |
| **G15** | The meter's occupancy feed has no `component_type` in the twin, while the old connector port carried `RESIDENTS` (only `metered_directly`). | **Resolved** (owner, 2026-10-03, D2): a feed's tags are the observer's own declaration, which carries no component type for the occupancy (`electricity_meter.py:304-326`), so the feed equals the twin's line. |
| **G16** | An earlier §3.2 gave `hot_water_demand` as kg/s and °C; the occupancy's output is `WaterConsumption`, litres per step, `WARM_WATER` (`loadprofilegenerator_utsp_connector.py:503`). | Moot: there are no connector quantities; the tank's default connections from the occupancy carry the class's own unit, and the existing unit check guards the wire. |
| **G17** | `serves_dhw` must agree with whether the `dhw` import binds `heating.dhw`. | No gap: both directions fail loudly (an active required circuit unbound; a `bind:` to an inactive port). Recorded because the translator writes the same fact twice. |
| **G18** | D21 says the EMS actuates only L1 modifiers and the battery, but it also *ranks* consumers it cannot steer: residents (weight 1), solar thermal (4), and the heat pump feeds themselves, all with `dispatch: {}` (twin :129, :135, :146). | Written (spec §4.4): a rank-only feed comes from the EMS's own declaration and a `via:` output lowers to the feed plus its modifier; both carry `dispatch: {}`. |

### 9.1 G14 resolved: the evaluation order and the neutral swap

**Sequence.** The composed file sets `order:` (§1), so the expansion writes Building, UTSPConnector, Weather, PV,
HeatDistributionController, ControllerSH, ControllerDHW, HeatPump, **Buffer**, **DHWStorage**, HeatDistributionSystem,
ElectricityMeter, Battery, EMS (order paths in §4). The twin has the same fourteen components in the same places except
that it evaluates DHWStorage (its 9th) before SimpleHotWaterStorage (its 10th). No number puts the cylinder between 6.3
and 6.4: it belongs to the `dhw` import, and an importer never orders inside an assembly (spec §2.3).

**Claim.** In a Gauss-Seidel pass, exchanging two adjacent components A and B that read no output of each other gives a
bit-identical vector after the pass, for the same vector before it.

**Proof.** A pass calls, for each component in sequence, `restore_state` and then `calculate_component` on one shared
vector `stsv` (`hisim/simulator.py:397-401`); a component reads its inputs from the slots of their source outputs and
writes only its own outputs' slots (`hisim/component.py:191`, `:193-195`). Let v be the vector after the components
before A and B. Order A, B: A reads v and writes O_A; B reads v with O_A written, but none of its inputs is in O_A, so
it reads exactly what it would read from v and writes O_B. Order B, A: B reads v, the same inputs, and writes the same
O_B (same code, same floating-point operations, its own state only); A reads v with O_B written, none of which it reads,
and writes the same O_A. The slots are disjoint, so the vector after both is identical, and every later component sees
an identical vector. By induction every pass is identical; the convergence test is element-wise over the whole vector
against a fixed 1e-4 (`component.py:197-203`), so the step ends after the same pass, `force_convergence` is set on the
same try (`simulator.py:416`), and `i_doublecheck` and the saved states are per component. So every time step, and the
whole run, is bit-identical. ∎

**The premise holds here.** DHWStorage reads `UTSPConnector` and `MoreAdvancedHeatPumpHPLib` (twin :75-80);
SimpleHotWaterStorage reads `HeatDistributionSystem` and `MoreAdvancedHeatPumpHPLib` (twin :81-88). A bare name wires
only from the component it names, so neither reads the other; `simple_water_storage.py` uses no SimRepository and no
class-level mutable state, so they share nothing outside the vector either. The heat pump, which reads both, is
evaluated before both in either order and so reads both from the previous pass, as before.

**What moves, and does not matter.** The output slots' global indices and the order of the two blocks of result columns
(columns are compared by name); a post-processing sum over components adds the same terms in another order, which can
change a last bit for three or more terms, far inside the goldens' relative 1e-9 (`tests/test_golden_kpis.py:117-118`).

**Conclusion.** The exchange is a **neutral swap**, an intended difference on D9's reviewed list. The D9 gate passes
byte for byte under the rename map and that list; the existing golden stands; G14 is resolved without re-recording any
twin.

**The other base files** (spec §13). The gas, hydrogen, oil, pellet and wood-chip twins evaluate site, generator,
controller, DHWStorage, SimpleHotWaterStorage, HDS, fuel meter; district heating the same without a buffer; electric
heating site, controller, generator, DHWStorage. With the heat-pump numbering (a boiler assembly ordering generator,
controller, buffer) each comes out equal up to the same neutral swap where there is a buffer (DHWStorage reads occupancy
and generator, the buffer HDS and generator, in all five) and up to the fuel provider: it takes 15, after `control`,
where the twins evaluate the fuel meter before the electricity meter, battery and EMS. That is a chain of neutral swaps:
the meter reads only the generator, and no component of the ten twins reads a gas or fuel meter. `gas_solar_thermal`
evaluates solar thermal and the cylinder after the HDS; its composed file numbers `solar_thermal` 12, `dhw` 13, `gas`
14, `grid` 15, `battery` 16, `control` 17 and equals its twin exactly. `heatpump_solar_thermal` is the exception: its
twin evaluates the heat pump and buffer before the HDS, solar thermal and DHWStorage, and the two heat-pump controllers
last. No contiguous `heating` block reaches that by neutral swaps — `ControllerDHW` reads DHWStorage, the heat pump
reads both controllers — so that one setup is reordered once and its twin and golden re-recorded, in its PR of spec §13
step 5.

### Verdict

The design reproduces the twin's **graph** exactly: every component, class, preset, config line, input item, feed tag,
weight and dispatch block of `ems_with_battery` comes out of the composed file under the rename map, and
`metered_directly` comes out of the same file without `control`, `battery` and the grid's `observes:` line (G15
resolved). It still holds without connector types (owner, 2026-10-03): every binding lowered to the default connections
the twin's classes already declare (the bare names the twin writes), the two weather wires excepted, which the
assembly names; every feed's tags and weight are the meter's or the EMS's own declaration. With the declared `order:` (G14 resolved, §9.1) it also reproduces the twin's **sequence**, up to one neutral
swap that leaves every pass bit-identical, so the D9 gate can pass byte for byte against the existing golden. What still
stands between the composed file and that gate: G10 makes it fail to load before anything else runs. **One point needs
an owner decision: G8** (G4, G13, G14 and G15 are resolved; G1, G2, G6 and G18 are written). One is a component change
(G5, EMS modifiers per feed); the rest are rules and declarations the spec or the classes owe (G3, G7, G9, G10, the
meter's default connection from the EMS of G6, the heat pump's weather default connections of G2), plus G11 (minor)
and G12 (EV, outside this twin).
