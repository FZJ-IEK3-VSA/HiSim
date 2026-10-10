# Spec: part-load operation of hydronic generators at coarse steps

Status: design agreed with the owner on 2026-10-09 and revised on 2026-10-10 (§2); built as a stack of four draft
PRs (#915 to #918, §10). Builds on the hydronic coupling (`roadmap/hydronic_coupling_spec.md`, stages A to C) and
the simulator warm start (hisim-4g9.23, #914). This document describes what the stack builds; the measured results
are in §11.

## 1. Problem

Stage C made the hot-water chain conserve energy and agree across step lengths for heat, but three things still
depend on the step length, because a controller decides once per step on the start temperature `T0` (D4) and a
generator then runs the whole step (D6):

- **Heat pump, hot water** (hisim-fxix.12). Full year 2021 against 60 s: hot-water heat +0.09 % at 900 s and
  +0.27 % at 3600 s, but hot-water electricity **+1.73 %** and **+18.1 %**. An hour-long charge keeps the cylinder
  hot, so more of the heat is made above about 63 °C, where hplib runs near a COP of 1.
- **Solar thermal** (hisim-fxix.11). Collector yield −7 % / −24 % (gas + solar) at 900 s / 3600 s; the backup makes
  up the difference. The pump runs a whole step or none of it, and the backup heats the cylinder for a whole step
  beside the collector.
- **Buffer-less heat distribution** (stage D, D3). Without a buffer the distribution system's pipe water is the only
  store. It turns over in minutes, so at 900 s the generator and the pipe water need many iterations to agree, and
  the result is biased.

RenoVisor runs at 900 s.

## 2. Decisions

Owner, 2026-10-09:

1. **Fine steps stay exactly as they are.** At and below a step-length threshold every device runs whole steps.
   60 s results, and so every golden reference, are bit-identical to #914, and a 60 s run is the reference every
   coarse run is checked against.
2. **Above the threshold, a device its controller has switched on runs only a fraction of the step** (the part-load
   ratio, PLR). The averaged flow at the full-load supply temperature enters the unchanged store node
   (`hydronics.MixedNode.step`).
3. **The on/off and mode decisions stay as they are**: on `T0`, hysteresis, minimum run and rest times, the solar
   switch-on on `T̄` and its stop on `T0`. The cap of the hot-water supply at the controller's set temperature stays
   as the safety limit.
4. **The buffer-less distribution loop is computed quasi-steady above its turnover criterion** (§8).
5. **Open points are measured, not assumed**: restarts missed inside a coarse step, the heat pump's COP above 63 °C,
   cycling losses.

Owner, 2026-10-10, replacing the prototype's design of 2026-10-09 (heat-to-target lines published by the store):

6. **No linearisation.** No component publishes a line, a slope or a "heat to target". The interface of the
   prototype (`TargetHeatLine`, the store's four line outputs, `heat_to_reach_target_j`, the fraction search) is gone.
7. **The store is a black box.** It publishes only port and sensor quantities. The part-load mechanism reads its
   controlled temperature at the end of the step, `WaterTemperatureAtEndOfStepInCelsius`, which any tank model (a
   stratified one included) can publish. The store does not know which inflow is a backup or a collector and decides
   nothing; "solar first" is no longer part of the physics.
8. **The L1 controller owns the fraction.** It already owns the device's on/off, mode and set point; it now also
   iterates the fraction over the simulator's passes so that the store ends the step at its target (§4). The target
   is published by the controller and by nobody else.
9. **Collector and backup both iterate** to their own targets on the same tank. No priority rule is added; what
   happens is measured (§7, §11). The solar priority is a separate control strategy, hisim-bq3h.
10. **The buffer-less loop's quasi-steady path stays** (§8); it publishes a step-mean return at its port.

This is a hybrid by design: one model whose controllers regulate a run fraction above the threshold, not two models.
The cost is a small, documented jump in results at the threshold itself, a step length nobody runs.

## 3. Terms

- **Part-load ratio (PLR)**: the fraction of a step a device runs at full load, `0 ≤ PLR ≤ 1`, a dimensionless
  output with `Units.FRACTION` (averaged by the post-processing, never summed). `PLR = 1` is the whole step.
- **Full load**: what the device does when it runs: its flow `m_full` at its supply temperature `T_sup`, its heat and
  its fuel or electricity by its own law.
- **Target**: the store temperature at which the controller ends the charge; the controller publishes it.
- **Target band**: `[target, target + 0.05 K]`, the end temperatures the controller accepts
  (`PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN`).
- **Pass**: one evaluation of every component in the order they were added; the simulator repeats passes until no
  output moves by more than 1e-4, and forces convergence on the passes after the twelfth.
- **Threshold**: `SimulationParameters.part_load_above_seconds`, default 600 s. Above it, PLR may be below 1.

## 4. The mechanism (`hisim/part_load.py`)

### 4.1 Who does what

| Part | Role |
|---|---|
| Store (physics) | Integrates its node with the inflows it receives and publishes `WaterTemperatureAtEndOfStepInCelsius`. Nothing new. |
| L1 controller | Decides on/off and mode as before. Through a `PartLoadControl` it reads the store's end temperature, runs a `PartLoadRatioRegulator` and publishes the PLR and its target. |
| Device (physics) | Reads the PLR through a `PartLoadCommand`. Below 1 it publishes `PLR · m_full` at the full-load supply temperature and books the heat that flow carries; fuel or electricity follow from its own law. |

`PartLoadControl` publishes, in every pass:

- 0 while the controller does not run its device, and its search restarts when the device runs again (the passes
  of the step already run still count towards the fourth, the first one read);
- exactly 1 at and below the threshold whenever the device runs, so a 60 s run computes what it did before;
- above the threshold, the regulator's current trial; under `force_convergence` the trial of the last pass.

The controller calls `start_step()` in `i_save_state`, so every step starts a fresh search; a step never inherits a
ratio from the step before. A run that the device enforces itself against its controller (a minimum running time)
runs the whole step: the controller commanded no fraction for it.

### 4.2 The search

The store's end temperature rises with the PLR of a device that heats it, so the PLR that ends the step in the target
band is the root of a rising function on [0, 1]. The regulator searches it trial by trial:

1. The first trial is the whole step. A store that ends at or below the band keeps the whole step.
2. While every trial so far has left the store too hot, the next trial is estimated below them with the store's
   start temperature standing in for its end temperature without the device: on the line from `T0` at PLR 0 through
   the one too-hot trial, then on the parabola through `T0` and the two latest too-hot trials, which follows the
   flattening rise of a device that holds its lift. After four such estimates the trial is 0.
3. Once one trial left the store too cold and one too hot, every trial interpolates between the closest of each
   (regula falsi with the Illinois weighting).

An end that no longer lies on its side of the aim (the target moved, as the heat pump's does under the energy
manager's raise, or a second device changed its ratio) is dropped from the bracket. The search stops after eight
trials or on a closed bracket.

### 4.3 Reading a trial in every evaluation order

In one pass the controller reads the store's end temperature as the store computed it in the same pass or the one
before, from the device's flow of that pass or the one before; how far behind it is depends only on the order in
which the three components were added. For a cycle controller → device → store → controller it is one or two passes.
A trial is therefore published in two passes before the end temperature read in the next pass is paired with it, and
nothing is read before the step's fourth pass (in the first pass a controller evaluated before the store reads the
values of the step before). The search never pairs a trial with the answer to another one, so the PLR it settles on
does not depend on the evaluation order; only the number of passes does.

### 4.4 What this deliberately differs from

The regulator keeps memory across the passes of one step, which `i_restore_state` does not undo. That is what lets a
controller regulate within a step at all; the memory is discarded at every step start, and the PLR it settles on is a
function of the store's answer only. No other state of a controller or a device changes within a step.

## 5. Devices

| Device | Controller (owner of the PLR and the target) | Target | Booking below PLR 1 |
|---|---|---|---|
| `GenericBoiler`, hot water, every fuel | `GenericBoilerController` | its warm-water aim, 60 °C (the charge ends at `T0 ≥ 60 °C`) | `PLR m_full` at the full-load supply; heat `m c (T_sup − T_ret)`; fuel `PLR · fuel_full` |
| `MoreAdvancedHeatPumpHPLib`, hot water | `MoreAdvancedHeatPumpHPLibControllerDHW` | its set temperature (with the energy manager's raise) less the 0.5 K switch-off tolerance | `PLR m_full` at hplib's capped outlet; `P_th = m c (T_out − T̄)`, `P_el = P_th / COP(T̄)`; the brine pump's electricity `PLR · P_pump` |
| `ElectricHeating`, hot water | `ElectricHeatingController` | its warm-water aim, 60 °C | `PLR m_full` at the full-load supply; heat and electricity `m c (T_sup − T_ret)` |
| `SolarThermalSystem`, pump | `SolarThermalSystemController` | its warm-water aim, 60 °C (the pump stops at `T0 > 60 °C`) | `PLR m_full` at the collector's full-load outlet; heat `m c (T_out − T_in)`; pump electricity `PLR · P_pump` |

A device whose target is where its controller ends the charge, rather than its supply set temperature (70 °C for the
boiler), lands the store where the controller's next decision ends the charge: a supply capped at the set temperature
never lands the store on that temperature. The landing band above the target replaces the prototype's 0.25 K margin.
Cycling losses are not modelled (open point 3).

## 6. The hot-water cylinder (`SimpleDHWStorage`)

Unchanged against #914. It integrates every inflow it receives with `MixedNode.step` and solves its tap valve on its
own step mean. The controllers read its start temperature (`WaterTemperatureAtStartOfStepInCelsius`, `T0`) to decide and its
end temperature (`WaterTemperatureAtEndOfStepInCelsius`) to regulate.

The space-heating buffer (`SimpleHotWaterStorage`) follows once it is a node (stage D, hisim-fxix.5, hisim-ign9).

## 7. Two devices on one store

The gas + solar and heat pump + solar twins charge one cylinder with a collector and a backup. Both controllers run
their own search on the same end temperature, each to its own target, and nothing ranks them (decision 9):

- with equal targets (gas boiler and collector, both 60 °C) any split with the right total ends the tank on the target,
  so the split is not unique; the two searches move together and settle on one of them;
- with different targets (heat pump: 59.5 °C; collector: 60 °C) the device with the higher target ends up carrying the
  last kelvin and the other backs off, which is a priority set by the targets, not by the physics.

What the split is, how many passes it takes and whether it depends on the evaluation order is measured in §11.

## 8. The buffer-less distribution loop (`HeatDistribution` without a buffer)

Above the turnover criterion (`a = m_design dt / M_pipe > 1`) and the threshold, a loop the distribution system pumps
itself (`NO_STORAGE_MASS_FLOW_FIX`, district heating) is quasi-steady within the step:

- **Heating or cooling on**: the emitters exchange the building's demand with the supply, heating or cooling alike;
  the return follows as `T_ret = T_sup − Q_dem / (m c)`, bounded by the room temperature (§4.6 of the hydronic spec).
  The pipe water ends the step at the mean of supply and return, and the return the generator sees is the step mean
  that closes the loop's balance, `m c (T_sup − T_ret,mean) = Q_delivered + C_pipe (T_pipe,end − T_pipe,0) / dt`. A
  generator regulating its supply temperature therefore bills the delivered heat plus the pipe water's change of
  stored heat.
- **Heating off**: the pipe water stands and cools towards the room by free convection, with the same law and time
  constant as the loop's standstill below the criterion; the heat it gives off reaches the room and is booked as
  delivered heat, and the generator sees its own supply back, so it bills nothing.
- **Start**: the pipe water starts at the mean of the loop's initial supply and return temperatures, 25 °C, like the
  dynamic loop's state.

The pipe water's mass and its standstill time constant are computed in one place. Below the criterion the loop keeps
its dynamic, one-step-lagged behaviour, the reference.

## 9. Thresholds

- **Stores with a node** (cylinder, buffer): `dt > part_load_above_seconds` (default 600 s).
- **Buffer-less loop**: `a = m_design dt / M_pipe > 1` and `dt > part_load_above_seconds`. For the district-heating
  twin (121.2 m², 212.7 kg of pipe water, 0.27 kg/s) `a` is 0.076 at 60 s, 1.142 at 900 s and 4.569 at 3600 s.

## 10. What changes and what does not

| Part | Change | PR |
|---|---|---|
| Simulator, `MixedNode.step`, `SimpleDHWStorage` | none | – |
| `SimulationParameters` | `part_load_above_seconds` (default 600) | #915 |
| `hisim/part_load.py` | the regulator, the controller part and the device part | #915 |
| `GenericBoilerController`, `GenericBoiler` | the PLR of the hot-water charge | #915 |
| `MoreAdvancedHeatPumpHPLibControllerDHW`, `MoreAdvancedHeatPumpHPLib` | the PLR of the hot-water charge | #916 |
| `ElectricHeatingController`, `ElectricHeating` | the PLR of the hot-water charge | #916 |
| `SolarThermalSystemController`, `SolarThermalSystem` | the PLR of the pump | #917 |
| `HeatDistribution` | the quasi-steady path for the buffer-less loop | #918 |
| Controllers' on/off and mode decisions | none | – |
| 60 s results and goldens | none | – |

The same pattern serves the devices that will come (a separate hot-water heat pump, hisim-lenz; direct electric hot
water, hisim-epc.21; the hybrid heat pump, hisim-epc.20), and the room-temperature devices with the room as their store
(hisim-w510).

Amendments to the hydronic spec, to be written into `hydronic_coupling_spec.md` when the stack is accepted:

- **D3**: the buffer-less loop is quasi-steady above its turnover criterion (§8); the circuit laws (hisim-4g9.22) are
  no longer the planned remedy.
- **D4**: controllers still decide on `T0`; above the threshold the controller of a device that is on also iterates the
  fraction of the step it runs.
- **D6**: one circuit per step stays; above the threshold that circuit may run for a fraction of the step.

## 11. Measured results

Added with the stack's last part (#918), full year 2021 against 60 s.

## 12. Beads

Epic hisim-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-naze (P1) | threshold, regulator, the boiler | #915 |
| hisim-fxix.12 (P1) | the heat pump's hot-water side | #916 |
| hisim-fxix.11 (P1) | the collector | #917 |
| hisim-hhj2 (P1) | the quasi-steady buffer-less distribution loop | #918 |
| hisim-bq3h (P2) | solar priority as a control strategy | – |
| hisim-ign9 (P2) | the space-heating buffer, after stage D (hisim-fxix.5) | – |
| hisim-3y10 (P2) | the open points of §2.5 | measured in #915 to #918 |
| hisim-w510 (P4) | room-temperature devices (parent hisim-4g9) | – |
