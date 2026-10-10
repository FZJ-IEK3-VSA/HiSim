# Spec: part-load operation of hydronic generators at coarse steps

Status: design agreed with the owner on 2026-10-09, revised on 2026-10-10 twice (§2): first to a ratio the L1
controller iterates against the store's end temperature, then to a memoryless rule the simulator's plain iteration
solves. Built as a stack of four draft PRs (#915 to #918, §10). Builds on the hydronic coupling
(`roadmap/hydronic_coupling_spec.md`, stages A to C) and the simulator warm start (hisim-4g9.23, #914). This document
describes what the stack builds; the measured results are in §11.

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
   pump's start and stop rule. The cap of the hot-water supply at the controller's set temperature stays as the
   safety limit.
4. **The buffer-less distribution loop is computed quasi-steady above its turnover criterion** (§8).
5. **Open points are measured, not assumed**: restarts missed inside a coarse step, the heat pump's COP above 63 °C,
   cycling losses.

Owner, 2026-10-10, replacing the prototype's design of 2026-10-09 (heat-to-target lines published by the store):

6. **No linearisation.** No component publishes a line, a slope or a "heat to target".
7. **The store is a black box.** It publishes only port and sensor quantities. The part-load mechanism reads its
   controlled temperature at the end of the step, `WaterTemperatureAtEndOfStepInCelsius`, which any tank model (a
   stratified one included) can publish. The store does not know which inflow is a backup or a collector and decides
   nothing.
8. **The L1 controller owns the fraction and its target.** It already owns the device's on/off, mode and set point;
   it now also sets the fraction (§4). The target is published by the controller and by nobody else.
9. **Collector and backup both set their ratio** against their own targets on the same tank. No priority rule is
   added; what happens is measured (§7, §11). The solar priority is a separate control strategy, hisim-bq3h.
10. **The buffer-less loop's quasi-steady path stays** (§8); it publishes a step-mean return at its port.

Owner, 2026-10-10, replacing the controller's search over the simulator's passes (a bracket with line, parabola and
Illinois estimates that counted passes and was reset in `i_save_state`), which broke component design principle 5:

11. **A memoryless rule** (§4.2). The controller computes the next ratio from its inputs only:
    `r_next = r_run · (T_aim − T_start) / (T_end − T_start)`, clamped to [0, 1], with the degenerate cases decided
    explicitly. Inside the target band it publishes `r_run` unchanged.
12. **The device reports the ratio it ran with** as an ordinary output (`Units.FRACTION`), and the controller reads it
    as an input: a port quantity, not memory.
13. **Plain iteration.** No acceleration (`is_accelerated`) and no other convergence aid on the ratio.
14. **The 0.05 K target band** above the target stays the acceptance band.
15. **Forced passes**: under `force_convergence` the controller publishes the ratio its device reports, so the loop
    stops moving.
16. **At and below 600 s** the ratio is exactly 1 while the device runs and 0 while it is off; 60 s results stay
    bit-identical to #914.

This is a hybrid by design: one model whose controllers set a run fraction above the threshold, not two models.
The cost is a small, documented jump in results at the threshold itself, a step length nobody runs.

## 3. Terms

- **Part-load ratio (PLR)**: the fraction of a step a device runs at full load, `0 ≤ PLR ≤ 1`, a dimensionless
  output with `Units.FRACTION` (averaged by the post-processing, never summed). `PLR = 1` is the whole step.
- **Ratio run** (`r_run`): the PLR the device reports it ran with in a pass: the commanded ratio while it follows its
  controller, 1 for a whole step it runs on its own (a minimum running time), 0 while it does not run.
- **Full load**: what the device does when it runs: its flow `m_full` at its supply temperature `T_sup`, its heat and
  its fuel or electricity by its own law.
- **Target**: the store temperature at which the controller ends the charge; the controller publishes it.
- **Target band**: `[target, target + 0.05 K]`, the end temperatures the controller accepts
  (`PartLoadRule.TARGET_BAND_IN_KELVIN`); the **aim** is its middle, target + 0.025 K.
- **Pass**: one evaluation of every component in the order they were added; the simulator repeats passes until no
  output moves by more than 1e-4, and forces convergence on the passes after the twelfth.
- **Threshold**: `SimulationParameters.part_load_above_seconds`, default 600 s. Above it, PLR may be below 1.

## 4. The mechanism (`hisim/part_load.py`)

### 4.1 Who does what

| Part | Role |
|---|---|
| Store (physics) | Integrates its node with the inflows it receives and publishes `WaterTemperatureAtEndOfStepInCelsius`. Nothing new. |
| L1 controller | Decides on/off and mode as before. Through a `PartLoadControl` it reads the store's end temperature and the ratio its device ran, applies `PartLoadRule` and publishes the PLR and its target. |
| Device (physics) | Reads the PLR through a `PartLoadCommand`. Below 1 it publishes `PLR · m_full` at the full-load supply temperature and books the heat that flow carries; fuel or electricity follow from its own law. It publishes the ratio it ran with. |

`PartLoadControl` publishes, in every pass:

- 0 while the controller does not run its device;
- exactly 1 at and below the threshold whenever the device runs, so a 60 s run computes what it did before;
- above the threshold, `PartLoadRule.next_part_load_ratio(r_run, T_start, T_end, target)`;
- under `force_convergence`, above the threshold, the ratio run (`publish_held`); at and below it the ratio keeps the
  value of the last unforced pass, as every other output of the controller does.

Neither part keeps state; nothing is reset in `i_save_state`. A run that the device enforces itself against its
controller (a minimum running time) runs the whole step and reports 1.

### 4.2 The rule

The store's start temperature `T_start` stands in for its end temperature at ratio 0, so the line from `(0, T_start)`
through `(r_run, T_end)` meets the aim at

`r_next = r_run · (T_aim − T_start) / (T_end − T_start)`, clamped to [0, 1].

Its fixed point is `T_end = T_aim` whatever `T_start` is; `T_start` changes how fast the iteration gets there. For a
store whose end temperature rises in proportion to the ratio and that loses nothing to a draw, the line is exact and
the second pass lands. The cases the line cannot answer, decided in this order (the first that applies):

| case | `r_next` |
|---|---|
| the store starts at or above the target (`T_start ≥ target`) | 0: it needs no heat |
| the store ends in the band (`target ≤ T_end ≤ target + 0.05 K`) | `r_run` unchanged |
| the store ends at or below its start (`T_end ≤ T_start`): the draw takes more than the device brings | 1 |
| the device ran nothing and the store ends below the target (`r_run = 0`, `T_end < target`) | 1, so the rule cannot stay at 0 |
| otherwise | the line, clamped to [0, 1] |

A device that ran nothing while another device heats the store past the band gets 0 from the line, which is right:
the store needs nothing from it. `tests/test_part_load_rule.py` checks every row at its edges.

### 4.3 Convergence

- **One device, a store without a draw**: the line is exact; the iteration lands on the second reading.
- **A draw.** With a draw the store's end temperature at ratio 0 lies below `T_start`, by `d`. Linearised, the
  iteration's error shrinks by the factor `d / (T_aim − T_start)` per pass and alternates in sign: it converges while
  the draw takes less than the rise the step still needs, and a large draw at the end of a charge oscillates. The real
  tank's tap valve keeps the draw's effect smaller than this estimate (`test_a_tank_with_a_tap_draw_ends_in_the_band`:
  a 30 l draw at 57 °C lands in nine passes).
- **The evaluation order.** The controller pairs the ratio run with the end temperature it reads in the same pass.
  The two belong to the same run only in the cyclic order controller → device → store. In the order device →
  controller → store, which the boiler and the collector have in their recorded twins, the device has already run
  the controller's newer ratio when the controller reads the store's answer to the older one. The iteration then
  follows `x_k = x_{k−1} − x_{k−2} + c` in `x = ln r`, whose roots lie on the unit circle: the ratio circles its fixed
  point with a period of six passes and settles only by chance. Measured on the gas twin, full year at 900 s: 1013
  forced steps in the twin's order, 46 with the controller moved before the boiler (§11). This is an open decision
  (hisim-fxix.16): the order of the files (which moves 60 s results by about 1e-12) or a port that tells the controller
  which flow the store's reading answers.
- **Two devices**: §7.

## 5. Devices

| Device | Controller (owner of the PLR and the target) | Target | Booking below PLR 1 |
|---|---|---|---|
| `GenericBoiler`, hot water, every fuel | `GenericBoilerController` | its warm-water aim, 60 °C (the charge ends at `T0 ≥ 60 °C`) | `PLR m_full` at the full-load supply; heat `m c (T_sup − T_ret)`; fuel `PLR · fuel_full` |
| `MoreAdvancedHeatPumpHPLib`, hot water | `MoreAdvancedHeatPumpHPLibControllerDHW` | its set temperature (with the energy manager's raise) less the 0.5 K switch-off tolerance | `PLR m_full` at hplib's capped outlet; `P_th = m c (T_out − T̄)`, `P_el = P_th / COP(T̄)`; the brine pump's electricity `PLR · P_pump` |
| `ElectricHeating`, hot water | `ElectricHeatingController` | where its mode ends the charge: the 60 °C aim alone, the 45 °C switch-on point beside space heating (the diverter valve's parallel mode; pending the owner's question on the diverter valve) | `PLR m_full` at the full-load supply; heat and electricity `m c (T_sup − T_ret)` |
| `SolarThermalSystem`, pump | `SolarThermalSystemController` | its warm-water aim, 60 °C (the pump stops at `T0 > 60 °C`) | `PLR m_full` at the collector's full-load outlet; heat `m c (T_out − T_in)`; pump electricity `PLR · P_pump` |

Each device publishes its ratio run (`PartLoadRatioRunDhw`, the solar pump `PartLoadRatioRun`), and its controller
reads it. A device whose target is where its controller ends the charge, rather than its supply set temperature
(70 °C for the boiler), lands the store where the controller's next decision ends the charge: a supply capped at the
set temperature never lands the store on that temperature. Cycling losses are not modelled (open point 3).

## 6. The hot-water cylinder (`SimpleDHWStorage`)

Unchanged against #914. It integrates every inflow it receives with `MixedNode.step` and solves its tap valve on its
own step mean. The controllers read its start temperature (`WaterTemperatureAtStartOfStepInCelsius`, `T0`) to decide
and its end temperature (`WaterTemperatureAtEndOfStepInCelsius`) to set the ratio.

The space-heating buffer (`SimpleHotWaterStorage`) follows once it is a node (stage D, hisim-fxix.5, hisim-ign9).

## 7. Two devices on one store

The gas + solar and heat pump + solar twins charge one cylinder with a collector and a backup. Both controllers apply
the rule to the same end temperature, each with its own ratio run and target, and nothing ranks them (decision 9):

- with equal targets (gas boiler and collector, both 60 °C) the rule scales both ratios by the same factor, so the
  store's rise follows the same line as with one device and lands as fast; the split is the one the two ratios had
  when they started, not a unique one;
- with different targets (heat pump: 59.5 °C, 69.5 °C under the energy manager's raise; collector: 60 °C) the device
  with the lower target sees the store above its band once the other reaches its own aim, and its ratio decays
  geometrically towards 0, by the factor `(T_aim,low − T_start) / (T_end − T_start)` per pass, without swinging back;
  it reaches 0 only in the limit, so such a step may run into the simulator's forced passes
  (`test_a_device_whose_target_another_device_overshoots_decays_towards_zero_without_oscillating`).

What the split is and how many passes it takes is measured in §11.

## 8. The buffer-less distribution loop (`HeatDistribution` without a buffer)

Above the turnover criterion (`a = m_design dt / M_pipe > 1`) and the threshold, a loop the distribution system pumps
itself (`NO_STORAGE_MASS_FLOW_FIX`, district heating) is quasi-steady within the step:

- **Heating on**: the emitters exchange the building's demand with the supply by the emitter law; the return
  follows as `T_ret = T_sup − Q_dem / (m c)`, bounded by the room temperature (§4.6 of the hydronic spec). The pipe
  water ends the step at the mean of supply and return, and the return the generator sees is the step mean that closes
  the loop's balance, `m c (T_sup − T_ret,mean) = Q_delivered + C_pipe (T_pipe,end − T_pipe,0) / dt`. A generator
  regulating its supply temperature therefore bills the delivered heat plus the pipe water's change of stored heat.
- **Heating off**: the loop stands. Nothing is exchanged with the building, the pipe water keeps its temperature, and
  the generator sees its own supply back, so it bills nothing. Physically the standing pipe water would cool towards the
  room over about 50 minutes (the standstill time constant below the criterion); that heat would reach the room, and
  the next heating step would buy it back. The quasi-steady loop leaves both out, which keeps every joule accounted for
  and the delivered heat equal to what the building asked for while heating was on.
- **Cooling on**: computed on the dynamic, one-step-lagged path at every step length, as below the threshold. A
  substation that cannot cool passes its return through as its supply, and computed quasi-steady such a step has no
  fixed point within the step (a June step of the district-heating twin ran into the simulator's 100-pass limit).
  The step function itself exchanges a cooling demand symmetrically.
- **Start**: the pipe water starts at the loop's initial supply and return temperature, 21 °C, one class constant.

The pipe's length, the pipe water's mass and its standstill time constant are computed in one place, shared with the
loop's standstill below the criterion; they are computed only for a loop that runs quasi-steady. The pipe water's
energy output (`ThermalEnergyIncreaseOfPipeWaterInWattHour`) is stored heat, not delivered heat; no meter or KPI counts
it. Below the criterion the loop keeps its dynamic, one-step-lagged behaviour, the reference.

## 9. Thresholds

- **Stores with a node** (cylinder, buffer): `dt > part_load_above_seconds` (default 600 s).
- **Buffer-less loop**: `a = m_design dt / M_pipe > 1` and `dt > part_load_above_seconds`. For the district-heating
  twin (121.2 m², 212.7 kg of pipe water, 0.27 kg/s) `a` is 0.076 at 60 s, 1.142 at 900 s and 4.569 at 3600 s.

## 10. What changes and what does not

| Part | Change | PR |
|---|---|---|
| Simulator, `MixedNode.step`, `SimpleDHWStorage` | none | – |
| `SimulationParameters` | `part_load_above_seconds` (default 600) | #915 |
| `hisim/part_load.py` | the rule, the controller part and the device part | #915 |
| `GenericBoilerController`, `GenericBoiler` | the PLR of the hot-water charge and its report | #915 |
| `MoreAdvancedHeatPumpHPLibControllerDHW`, `MoreAdvancedHeatPumpHPLib` | the PLR of the hot-water charge and its report | #916 |
| `ElectricHeatingController`, `ElectricHeating` | the PLR of the hot-water charge and its report | #916 |
| `SolarThermalSystemController`, `SolarThermalSystem` | the PLR of the pump and its report | #917 |
| `HeatDistribution` | the quasi-steady path for the buffer-less loop | #918 |
| Controllers' on/off and mode decisions | none | – |
| 60 s results and goldens | none | – |

The same pattern serves the devices that will come (a separate hot-water heat pump, hisim-lenz; direct electric hot
water, hisim-epc.21; the hybrid heat pump, hisim-epc.20), and the room-temperature devices with the room as their store
(hisim-w510).

Amendments to the hydronic spec, to be written into `hydronic_coupling_spec.md` when the stack is accepted:

- **D3**: the buffer-less loop is quasi-steady above its turnover criterion (§8); the circuit laws (hisim-4g9.22) are
  no longer the planned remedy.
- **D4**: controllers still decide on `T0`; above the threshold the controller of a device that is on also sets the
  fraction of the step it runs, by a memoryless rule on the store's end temperature and the ratio its device ran.
- **D6**: one circuit per step stays; above the threshold that circuit may run for a fraction of the step.

## 11. Measured results

Full year 2021, the shipped predefined occupancy profile, measured on 2026-10-10 on the stack's last part (#918),
against the 60 s run of the same head. The 60 s runs of the gas and the gas + solar twin equal #914 bit for bit in
every common column, with the same passes. "Forced" counts the steps the simulator flags at `force_convergence`
(twelve passes or more). R is the memoryless rule of §4 in the twins' component order; R′ the same code with every
part-load controller moved before its device in the file (§4.3); I the pass-counting search this rule replaced
(measured on the stack before #909's review fixes, so on a different 60 s base).

| twin | step | hot-water fuel or electricity R / R′ / I | collector R / R′ / I | passes R / R′ / I | forced R / R′ / I |
|---|---|---|---|---|---|
| gas | 900 s | +0.27 % / -0.00 % / -0.00 % | – | 4.26 / 3.42 / 4.20 | 1013 / 46 / 24 |
| gas | 3600 s | +0.18 % / -0.01 % / -0.06 % | – | 4.85 / 3.89 / 4.66 | 679 / 50 / 71 |
| heat pump | 900 s | -1.17 % / – / -1.21 % | – | 5.66 / – / 6.00 | 4504 / – / 4556 |
| heat pump | 3600 s | +13.00 % / – / +6.01 % | – | 7.24 / – / 8.04 | 2082 / – / 2438 |
| electric | 900 s | +0.12 % / – / +0.01 % | – | 6.37 / – / 6.66 | 742 / – / 532 |
| electric | 3600 s | +0.74 % / – / -0.07 % | – | 8.46 / – / 8.43 | 1015 / – / 562 |
| gas + solar | 900 s | +2.14 % / +0.27 % / +0.21 % | -11.87 % / -1.74 % / -1.41 % | 4.40 / 3.74 / 4.57 | 1193 / 121 / 73 |
| gas + solar | 3600 s | +1.74 % / -0.08 % / +0.97 % | -9.62 % / +0.43 % / -6.76 % | 5.23 / 4.34 / 5.12 | 839 / 212 / 191 |
| heat pump + solar | 900 s | +1.83 % / +1.73 % / +1.61 % | +1.18 % / +0.08 % / +0.34 % | 4.64 / 4.63 / 5.26 | 2254 / 1876 / 2122 |
| heat pump + solar | 3600 s | +12.92 % / +13.67 % / +13.79 % | +2.76 % / -0.64 % / -2.70 % | 5.75 / 5.63 / 6.31 | 1064 / 953 / 1191 |

The heat-pump and electric-heating twins already list each controller before its device, so R′ equals R there.
District heating has no part load; its loop (§8) gives the same results as before (hot-water heat +0.35 % / +0.99 %,
no forced step at 900 s, 23 at 3600 s).

Findings:

1. **The evaluation order decides.** In the boiler and solar twins the device is evaluated before its controller and
   the tank after both, so the controller pairs a ratio with the tank's answer to the one before (§4.3). There the
   rule's plain iteration settles on almost no partial step: 5 of 739 partial boiler steps of the gas twin converge
   at 900 s, none at 3600 s; the others end at `force_convergence` on average 0.5 to 0.7 K from the target. With the
   controller first (R′) 718 of 759 converge, and the gas twin needs fewer passes than with the search (3.42 against
   4.20 at 900 s). Open decision: hisim-fxix.16.
2. **A draw larger than the rise the step still needs** makes the iteration alternate (§4.3). The electric heater,
   whose controller is evaluated first, tops the tank up beside space heating at 45 °C in a third of its winter steps;
   with the draws of those steps 730 of its 900 s steps need a forced pass (the search: 212), and its hot-water
   electricity rises from +0.01 % to +0.12 % at 900 s and from -0.07 % to +0.74 % at 3600 s.
3. **Converged partial steps land in the band**: in every twin and run the tank ends 0.01 to 0.04 K above the target
   on average, and every converged partial step but one ends at most 0.05 K above it (the one, a heat-pump step of the
   heat pump + solar twin at 3600 s in R′, ended 0.53 K off).
4. **Heat pump**: the energy manager's raise still switches the target from one pass to the next; 206 of 1129 partial
   steps converge at 900 s (the search: 192 of 1292) and 51 of 596 at 3600 s, where the hot-water electricity ends at
   +13.0 % (the search: +6.0 %).
5. **Collector and backup** (with the controllers first, R′): the collector yields -1.7 % / +0.4 % against 60 s and the
   boiler's hot-water gas is +0.27 % / -0.08 %; in the heat pump + solar twin the heat pump's lower target lets the
   collector take the step, its ratio decaying towards 0 as §7 describes.
6. **Wall time**: the year at 900 s takes 17 s for the gas twin (21 s with the controller first, measured with six
   runs sharing six cores), 33 s for the heat pump; at 60 s 178 s and 336 s.

## 12. Beads

Epic hisim-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-naze (P1) | threshold, rule, the boiler | #915 |
| hisim-fxix.16 (P1) | the evaluation order that keeps the rule from settling in the boiler and collector twins | owner decision |
| hisim-fxix.12 (P1) | the heat pump's hot-water side | #916 |
| hisim-fxix.11 (P1) | the collector | #917 |
| hisim-hhj2 (P1) | the quasi-steady buffer-less distribution loop | #918 |
| hisim-bq3h (P2) | solar priority as a control strategy | – |
| hisim-ign9 (P2) | the space-heating buffer, after stage D (hisim-fxix.5) | – |
| hisim-3y10 (P2) | the open points of §2.5 | measured in #915 to #918 |
| hisim-w510 (P4) | room-temperature devices (parent hisim-4g9) | – |
