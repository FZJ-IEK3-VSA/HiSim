# Spec: part-load operation of hydronic generators at coarse steps

Status: design agreed with the owner on 2026-10-09, revised on 2026-10-10 twice (§2): first to a ratio the L1
controller iterates against the store's end temperature, then to a memoryless rule the simulator's plain iteration
solves. Built as a stack of four draft PRs (#915 to #918, §10). Builds on the hydronic coupling
(`roadmap/hydronic_coupling_spec.md`, stages A to C), the simulator warm start (hisim-4g9.23, #914) and the
simulator's kind order (#922, below this stack since 2026-10-12, §4.3). This document describes what the stack builds;
the measured results are in §11.

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
- **Pass**: one evaluation of every component, kind by kind (data sources, L2 controllers, L1 controllers, physics,
  meters) and within one kind in the order they were added, except that every device an L1 controller commands goes
  before the other physics the controller reads (§4.3); the simulator repeats passes until no output moves by more
  than 1e-4, and forces convergence on the passes after the twelfth.
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
  point with a period of six passes and settles only by chance. Measured on the gas twin, full year at 900 s, on the
  stack before the kind order moved under it: 1013 forced steps in the twin's order, 46 with the controller moved
  before the boiler (§11). Owner decision (2026-10-10, hisim-fxix.16, closed): the simulator evaluates every L1
  controller before the physics, whatever the order of the file. Controller first is necessary, not sufficient: the
  condition is the cyclic order controller → device → store. `household_gas_solar_thermal` adds its hot-water tank
  before the boiler and the collector, so with the kind order alone a pass ran controller → tank → device, and on the
  old stack the year at 900 s still had 1292 forced steps (54 in 1–21 January). Owner decision (2026-10-11): within
  the physics, every device an L1 controller commands is evaluated before every other physics component the
  controller reads, a stable topological sort that uses only the kinds and the wiring, so the controller always reads
  the store's answer to the ratio its device reports; constraints that form a cycle (two controllers each reading the
  other's device) are reported in a warning, and the components involved keep the order they were added in. On the
  old stack this took gas + solar from 54 to 3 forced steps in the window and from 1292 to 165 over the year at
  900 s (§11). Both are #922, which since 2026-10-12 sits below this stack: a component class inherits its kind base
  class (`PhysicsComponent`, `L1ControllerComponent`, … in `hisim/component.py`), and `hisim/evaluation_order.py`
  orders the physics. With it, every partial step of the gas twin in the window of `tests/test_part_load_twins.py`
  converges (15 of 15), and over the full year 2021 718 of 759 partial steps converge at 900 s and 614 of 643 at
  3600 s, each ending 0.000 to 0.050 K above the target (measured 2026-10-12 on this branch; §11 of the stack's last
  part).
- **Two devices**: §7.

## 5. Devices

| Device | Controller (owner of the PLR and the target) | Target | Booking below PLR 1 |
|---|---|---|---|
| `GenericBoiler`, hot water, every fuel | `GenericBoilerController` | its warm-water aim, 60 °C (the charge ends at `T0 ≥ 60 °C`) | `PLR m_full` at the full-load supply; heat `m c (T_sup − T_ret)`; fuel `PLR · fuel_full` |
| `MoreAdvancedHeatPumpHPLib`, hot water | `MoreAdvancedHeatPumpHPLibControllerDHW` | its set temperature (with the energy manager's raise) less the 0.5 K switch-off tolerance | `PLR m_full` at hplib's capped outlet; `P_th = m c (T_out − T̄)`, `P_el = P_th / COP(T̄)`; the brine pump's electricity `PLR · P_pump` |
| `ElectricHeating`, hot water | `ElectricHeatingController` | the 60 °C warm-water aim, where the diverter valve ends a charge alone and beside space heating (its parallel mode latches like the hot-water mode) | `PLR m_full` at the full-load supply; heat and electricity `m c (T_sup − T_ret)` |
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

### 11.1 On the kind order (2026-10-12)

Full year 2021, the shipped predefined occupancy profile, every twin at 60 s, 900 s and 3600 s, measured on
2026-10-12 with the simulator's kind order (#922) below the stack; each figure is against the 60 s run of the same
head. Tables 1 and 2 are this branch (#918), table 3 is each part of the stack on its own branch. The 60 s year does
not change through the stack: it is bit-identical, in every common column and with the same passes, from #922 to #915
(gas, gas + solar, pellets), #915 to #916 (gas, electric, heat pump), #915 to #917 (gas + solar), #916 to #917 (gas,
district heating) and #917 to #918 (gas, gas + solar, district heating, heat pump + solar), and #916 to #918 (electric,
heat pump). "Flagged" counts the steps the simulator returns with `force_convergence` set; it sets it after the
eleventh pass, so a step that converges on its twelfth pass is flagged without having run a forced pass. "Forced"
counts the steps with at least one forced pass (13 passes or more). A partial step is one whose controller's ratio lies
strictly between 0 and 1; it converged when it is not flagged.

**Table 1**, against 60 s. "Space heating" is the heat the generator books for space heating (the district-heating
substation: the space-heating heat it draws); "delivered" is the heat the distribution system gives the building.

| twin | step | hot-water heat | hot-water fuel or electricity | space heating | delivered | collector | unmet hot water (60 s) |
|---|---|---|---|---|---|---|---|
| gas | 900 s | -0.00 % | -0.00 % | +5.69 % | -0.17 % | – | 0.0 kWh (0.0 kWh) |
| gas | 3600 s | -0.01 % | -0.01 % | +21.32 % | -0.66 % | – | 0.0 kWh (0.0 kWh) |
| heat pump | 900 s | -0.15 % | +0.96 % | +19.61 % | -0.55 % | – | 4.4 kWh (3.8 kWh) |
| heat pump | 3600 s | -0.34 % | +8.89 % | +43.92 % | -0.94 % | – | 8.0 kWh (3.8 kWh) |
| electric | 900 s | -0.07 % | -0.07 % | -0.03 % | – | – | 9.0 kWh (7.4 kWh) |
| electric | 3600 s | -0.26 % | -0.26 % | -0.05 % | – | – | 9.7 kWh (7.4 kWh) |
| gas + solar | 900 s | +0.28 % | +0.28 % | +5.59 % | -0.17 % | -1.75 % | 0.0 kWh (0.0 kWh) |
| gas + solar | 3600 s | +0.14 % | +0.14 % | +21.13 % | -0.67 % | -1.00 % | 0.0 kWh (0.0 kWh) |
| heat pump + solar | 900 s | -0.38 % | +1.22 % | +17.17 % | -0.60 % | +1.57 % | 16.9 kWh (17.0 kWh) |
| heat pump + solar | 3600 s | -0.35 % | +13.64 % | +35.30 % | -1.58 % | +0.76 % | 22.0 kWh (17.0 kWh) |
| district heating | 900 s | -0.12 % | -0.12 % | -0.41 % | -0.09 % | – | 8.2 kWh (7.2 kWh) |
| district heating | 3600 s | +0.09 % | +0.09 % | -0.43 % | -0.24 % | – | 6.4 kWh (7.2 kWh) |

The 60 s references: hot-water fuel or electricity 3463.3 kWh (gas), 1067.8 kWh (heat pump), 3090.8 kWh (electric),
3002.3 kWh (gas + solar, with 418.8 kWh of collector heat), 693.6 kWh (heat pump + solar, with 595.8 kWh), and
3090.9 kWh of hot-water heat for district heating. The oil, pellet, wood-chip and hydrogen boiler twins (#915) give the
gas twin's hot-water figures and step counts exactly.

**Table 2**, the iteration. Wall time with two runs sharing six cores, at times beside two runs of another agent.

| twin | step | mean passes | flagged / forced | partial steps converged | end above the target, mean / max | wall time |
|---|---|---|---|---|---|---|
| gas | 60 s | 3.18 | 0 / 0 | – | – | 153 s |
| gas | 900 s | 3.29 | 94 / 59 | boiler 718 of 759 | 0.032 / 0.050 K | 16 s |
| gas | 3600 s | 3.67 | 47 / 45 | boiler 614 of 643 | 0.037 / 0.050 K | 9 s |
| heat pump | 60 s | 3.88 | 5191 / 5191 | – | – | 258 s |
| heat pump | 900 s | 4.63 | 1784 / 1759 | heat pump 586 of 872 | 0.032 / 0.050 K | 25 s |
| heat pump | 3600 s | 5.84 | 887 / 831 | heat pump 83 of 581 | 0.026 / 0.050 K | 11 s |
| electric | 60 s | 4.09 | 0 / 0 | – | – | 115 s |
| electric | 900 s | 5.29 | 73 / 73 | heater 409 of 482 | 0.018 / 0.049 K | 13 s |
| electric | 3600 s | 6.95 | 72 / 70 | heater 403 of 474 | 0.028 / 0.050 K | 7 s |
| gas + solar | 60 s | 3.23 | 14 / 14 | – | – | 215 s |
| gas + solar | 900 s | 3.61 | 165 / 128 | boiler 617 of 671, collector 1446 of 1458 | 0.032 / 0.050 K, 0.012 / 0.050 K | 19 s |
| gas + solar | 3600 s | 4.14 | 223 / 208 | boiler 548 of 583, collector 168 of 284 | 0.037 / 0.050 K, 0.024 / 0.050 K | 8 s |
| heat pump + solar | 60 s | 3.74 | 216 / 216 | – | – | 373 s |
| heat pump + solar | 900 s | 4.58 | 1310 / 1292 | heat pump 305 of 313, collector 1158 of 1200 | 0.035 / 0.050 K, 0.012 / 0.050 K | 34 s |
| heat pump + solar | 3600 s | 6.20 | 719 / 696 | heat pump 43 of 299, collector 185 of 309 | 0.033 / 0.050 K, 0.023 / 0.044 K | 14 s |
| district heating | 60 s | 3.13 | 0 / 0 | – | – | 104 s |
| district heating | 900 s | 5.42 | 0 / 0 | – | – | 15 s |
| district heating | 3600 s | 7.03 | 23 / 23 | – | – | 7 s |

**Table 3**, each part on its own branch, 900 s / 3600 s against the 60 s run of the same head. #922 is the kind order
without part load; #915 adds the boiler's part load, so for the heat-pump, electric and district-heating twins it equals
#922; #916 the heat pump's and the electric heater's; #917 the collector's; #918 the quasi-steady loop.

| twin: quantity | #922 | #915 | #916 | #917 | #918 |
|---|---|---|---|---|---|
| gas: hot-water gas | +0.17 % / +0.63 % | -0.00 % / -0.01 % | the same | the same | the same |
| gas: flagged steps | 38 / 0 | 94 / 47 | the same | the same | the same |
| heat pump: hot-water electricity | – | +0.59 % / +9.36 % | +0.96 % / +8.89 % | – | the same as #916 |
| heat pump: flagged steps | – | 1601 / 618 | 1784 / 887 | – | the same as #916 |
| electric: hot-water electricity | – | -0.14 % / +0.04 % | -0.07 % / -0.26 % | – | the same as #916 |
| electric: flagged steps | – | 0 / 0 | 73 / 72 | – | the same as #916 |
| gas + solar: hot-water gas | +1.17 % / +4.35 % | -0.18 % / -0.66 % | the same as #915 | +0.28 % / +0.14 % | the same as #917 |
| gas + solar: collector | -6.19 % / -24.01 % | +1.28 % / +4.31 % | the same as #915 | -1.75 % / -1.00 % | the same as #917 |
| gas + solar: flagged steps | 46 / 9 | 123 / 56 | the same as #915 | 165 / 223 | the same as #917 |
| heat pump + solar: hot-water electricity | – | +1.42 % / +13.67 % | +0.92 % / +12.99 % | +1.22 % / +13.64 % | the same as #917 |
| heat pump + solar: collector | – | +2.85 % / +0.85 % | +5.00 % / +1.29 % | +1.57 % / +0.76 % | the same as #917 |
| heat pump + solar: flagged steps | – | 1225 / 385 | 1234 / 573 | 1310 / 719 | the same as #917 |
| district heating: heat drawn | – | – | -0.03 % / +0.03 % | – | -0.35 % / -0.31 % |
| district heating: delivered | – | – | -0.08 % / -0.21 % | – | -0.09 % / -0.24 % |
| district heating: passes, flagged steps | – | – | 3.16 / 3.24, 0 / 0 | – | 5.42 / 7.03, 0 / 23 |

The heat pump + solar columns of #915 and #916 are against the 60 s run of #917, which those branches were not run at.

Findings:

1. **Boiler hot water** (#915). The rule takes the gas twin's hot-water heat and gas from +0.17 % / +0.63 % (whole-step
   charges) to -0.002 % / -0.012 %, and every boiler fuel gives the same hot-water figures. The price is iteration:
   the flagged steps rise from 38 to 94 at 900 s and from 0 to 47 at 3600 s, the mean passes from 3.22 to 3.29 and from
   3.32 to 3.67. Of the 94 flagged steps at 900 s 41 are partial steps and 53 whole-step charges.
2. **Converged partial steps land in the band.** On this branch every converged partial step of every twin ends 0.000
   to 0.050 K above its controller's target, 0.012 to 0.037 K on average. (On #915, where the collector still runs
   whole steps, 4 of the gas + solar twin's converged partial boiler steps end above the band, the worst 5.3 K: the
   collector lifts the tank past the boiler's band while the boiler's ratio decays towards 0, §7.)
3. **Heat pump** (#916). Whole-step charges on the same base already give +0.59 % / +9.36 % hot-water electricity; with
   the rule it is +0.96 % / +8.89 %, and the flagged steps rise from 1601 / 618 to 1784 / 887. The rule moves the year
   by less than 0.5 percentage points either way. The 3600 s excess is the COP above 63 °C under the energy manager's
   raise decided once per hour (hisim-3y10, open point 2), which the ratio does not reach. 1383 of the 1784 flagged
   steps at 900 s run the whole step: the energy manager's loop at the heat pump's own draw (hisim-4g9.28), not the
   rule. At 60 s the twin has 5191 flagged steps itself.
4. **Electric heater** (#916). Whole-step charges give -0.14 % / +0.04 % with no flagged step; the rule gives -0.07 % /
   -0.26 % with 73 / 72, all partial last steps of a charge whose draw takes more than the rise the step still needs
   (§4.3, hisim-fxix.17). On this base the rule brings the electric heater no accuracy.
5. **Collector and backup** (#917). Without any part load the gas + solar collector yields -6.19 % / -24.01 %; the
   boiler's rule alone takes it to +1.28 % / +4.31 %, and the collector's own rule to -1.75 % / -1.00 %, with the
   boiler's hot-water gas at +0.28 % / +0.14 %. The flagged steps rise from 123 / 56 to 165 / 223; at 3600 s 116 of the
   collector's 284 partial steps are flagged. In the heat pump + solar twin the collector's rule takes the collector
   from +5.00 % / +1.29 % to +1.57 % / +0.76 %, and the heat pump's hot-water electricity stays at +1.22 % / +13.64 %.
6. **The buffer-less loop** (#918). On this base the district-heating twin's dynamic, one-step-lagged loop (#916, #917)
   already meets hisim-hhj2's criterion: heat drawn -0.03 % / +0.03 %, delivered -0.08 % / -0.21 %, no flagged step,
   3.16 / 3.24 passes. The quasi-steady loop also meets it, with heat drawn -0.35 % / -0.31 %, delivered -0.09 % /
   -0.24 %, 5.42 / 7.03 passes and 23 flagged steps at 3600 s. The many iterations it was built against at 900 s
   (hisim-hhj2: 16 to 24 per step) no longer occur.
7. **Space heating is not part of this stack.** The generators' space-heating heat is +5.7 % / +21.3 % (gas), +19.6 % /
   +43.9 % (heat pump) and -3.3 % / -9.7 % (pellets) against 60 s, while the heat delivered to the building stays
   within 0.2 % / 0.7 % (gas) and 0.6 % / 0.9 % (heat pump): the space-heating buffer does not close its balance at
   coarse steps (stage D, hisim-fxix.5; its part load is hisim-ign9). The same holds on #922 without part load (gas
   +5.34 % / +19.65 %). In the pellet and wood-chip twins the buffer does not close it at 60 s either: their boilers
   book 51.7 MWh of space-heating heat a year for 11.0 MWh delivered, on #922 too.
8. **Wall time**: a year at 900 s takes 13 to 34 s, at 3600 s 7 to 14 s, at 60 s 104 to 373 s (table 2).

Against each part's "done when":

- #915, hisim-naze (the gas twin's hot-water heat and gas within 1 % at 900 s and 3600 s, 60 s unchanged, forced
  steps and run time reported): met; closed.
- #916, hisim-fxix.12 (the heat pump's hot-water electricity within 1 % at 900 s and 3600 s): met at 900 s (+0.96 %),
  not at 3600 s (+8.89 %); open.
- #917, hisim-fxix.11 (collector yield and the backup's fuel or electricity within 1 % at 900 s and 3600 s, no step
  at `force_convergence`): not met: the gas + solar collector is -1.75 % at 900 s, the heat pump + solar hot-water
  electricity +1.22 % / +13.64 %, and both twins have flagged steps; open.
- #918, hisim-hhj2 (district heating within 1 % on delivered heat and the bill, no forced step at 900 s, 60 s
  unchanged): met, and met without the quasi-steady loop too (finding 6); open for the owner's review.

### 11.2 On the old stack (2026-10-10)

Measured on 2026-10-10, full year 2021, before the kind order moved under the stack, so the components ran in the
order the files add them; the 60 s base differs from 11.1 (#914, before the energy manager's and the simulator's later
changes). R is the memoryless rule in the twins' order, R′ the same code with every part-load controller moved before
its device in the file, I the pass-counting search this rule replaced (on the stack before #909's review fixes, a
different 60 s base again). "Forced" counts flagged steps.

| twin | step | hot-water fuel or electricity R / R′ / I | collector R / R′ / I | passes R / R′ / I | forced R / R′ / I |
|---|---|---|---|---|---|
| gas | 900 s | +0.27 % / -0.00 % / -0.00 % | – | 4.26 / 3.42 / 4.20 | 1013 / 46 / 24 |
| gas | 3600 s | +0.18 % / -0.01 % / -0.06 % | – | 4.85 / 3.89 / 4.66 | 679 / 50 / 71 |
| heat pump | 900 s | -1.17 % / – / -1.21 % | – | 5.66 / – / 6.00 | 4504 / – / 4556 |
| heat pump | 3600 s | +13.00 % / – / +6.01 % | – | 7.24 / – / 8.04 | 2082 / – / 2438 |
| gas + solar | 900 s | +2.14 % / +0.27 % / +0.21 % | -11.87 % / -1.74 % / -1.41 % | 4.40 / 3.74 / 4.57 | 1193 / 121 / 73 |
| gas + solar | 3600 s | +1.74 % / -0.08 % / +0.97 % | -9.62 % / +0.43 % / -6.76 % | 5.23 / 4.34 / 5.12 | 839 / 212 / 191 |

What it decided: in the twins' order the boiler and the collector ran before their controllers, and the rule settled
on almost no partial step (5 of 739 partial boiler steps of the gas twin at 900 s); with the controller first it
settled on 718 of 759 and needed fewer passes than the search (3.42 against 4.20). That led to the kind order and the
physics order (§4.3, #922, hisim-fxix.16, closed). The electric heater's figures of that day (a 45 °C target beside
space heating) and a first measurement of the kind order on the old stack are superseded by 11.1.

## 12. Beads

Epic hisim-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-naze (P1, closed) | threshold, rule, the boiler | #915 |
| hisim-fxix.16 (P1, closed) | the evaluation order that kept the rule from settling in the boiler and collector twins: the kind order and the physics order | #922, below the stack |
| hisim-fxix.12 (P1) | the heat pump's hot-water side | #916 |
| hisim-fxix.11 (P1) | the collector | #917 |
| hisim-hhj2 (P1) | the quasi-steady buffer-less distribution loop | #918 |
| hisim-bq3h (P2) | solar priority as a control strategy | – |
| hisim-ign9 (P2) | the space-heating buffer, after stage D (hisim-fxix.5) | – |
| hisim-3y10 (P2) | the open points of §2.5 | measured in #915 to #918 |
| hisim-w510 (P4) | room-temperature devices (parent hisim-4g9) | – |
