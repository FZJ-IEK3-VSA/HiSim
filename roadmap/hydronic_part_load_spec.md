# Spec: part-load operation of hydronic generators at coarse steps

Status: design agreed with the owner on 2026-10-09, revised on 2026-10-10 twice (§2): first to a ratio the L1
controller iterates against the store's end temperature, then to a memoryless rule the simulator's plain iteration
solves. Built as four draft PRs (#915 to #918, §10); after the year was measured on the kind order, the owner decided
on 2026-10-12 (§2, decisions 17 to 19) to keep #915, the threshold, the rule and the boiler, to park #916 (the heat
pump and the electric heater) and #917 (the collector), and to drop #918 (the buffer-less loop). On 2026-10-14 the
owner brought #917 back on its own, directly on #915 (decision 20); #916 stays parked. Builds on the
hydronic coupling (`roadmap/hydronic_coupling_spec.md`, stages A to C), the simulator warm start (hisim-4g9.23, #914)
and the simulator's kind order (#922, §4.3). This document describes the design of all four parts and marks what is
built; the measured results are in §11.

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
10. **The buffer-less loop's quasi-steady path stays** (§8); it publishes a step-mean return at its port. Withdrawn by
    decision 19.

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

Owner, 2026-10-12, after the year on the kind order (§11):

17. **The boiler's part load goes ahead** (#915, directly on #922).
18. **The heat pump's and the electric heater's (#916) and the collector's (#917) part load are parked** as draft
    branches until the energy manager's raise cycle (hisim-4g9.28) and a fix of the car's controller under #922 are in;
    then they are measured again. The collector's part load came back on 2026-10-14 (decision 20).
19. **The buffer-less loop's quasi-steady path (#918) is dropped**: on the kind order the district-heating twin meets
    hisim-hhj2's criterion without it (§11).

Owner, 2026-10-14, after the review of the 80 °C solar maximum (#942):

20. **The collector's part load (#917) comes back first**, on #915 and below the solar pump's hysteresis (#941) and the
    80 °C solar maximum (#942), without #916. With the pump running whole steps, a step that starts just below the
    solar maximum ends well above it: `household_gas_solar_thermal` with a 20 m² collector, inside the solar assembly's
    declared range, stopped at 3600 s with the tank's "way too high" check (above 90 °C). The collector's part load
    ends such a step at the controller's target instead. The heat pump's and the electric heater's part load (#916)
    stay parked under decision 18.

This is a hybrid by design: one model whose controllers set a run fraction above the threshold, not two models.
The cost is a small, documented jump in results at the threshold itself, a step length nobody runs.

## 3. Terms

- **Part-load ratio (PLR)**: the fraction of a step a device runs at full load, `0 ≤ PLR ≤ 1`, a dimensionless
  output with `Units.FRACTION` (averaged by the post-processing, never summed). `PLR = 1` is the whole step.
- **Ratio run** (`r_run`): the PLR the device reports it ran with in a pass: the commanded ratio while it follows its
  controller, 1 for a whole step it runs on its own against its controller (a minimum running time kept inside the
  device, as the parked heat pump's), 0 while it does not run. A minimum running time the controller keeps, as the
  boiler's controller does, is not such a run: the step stays in the mode and gets the rule's ratio, 0 for a store
  that starts at or above the target, so the boiler does not fire on it while a 60 s run keeps firing (the gas twin's
  year at 900 s, §11, shows no effect worth a change).
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
controller (a minimum running time kept inside the device) runs the whole step and reports 1; a minimum running time
kept by the controller is cut to the rule's ratio (§3, "Ratio run").

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
  tank's tap valve keeps the draw's effect smaller than this estimate
  (`test_a_tank_with_a_tap_draw_ends_in_the_band_and_the_draw_raises_the_ratio`: a 30 l draw at 57 °C lands in nine
  passes).
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
  900 s. Both are #922, which since 2026-10-12 sits below #915: a component class inherits its kind base
  class (`PhysicsComponent`, `L1ControllerComponent`, … in `hisim/component.py`), and `hisim/evaluation_order.py`
  orders the physics. With it, every partial step of the gas twin in the window of `tests/test_part_load_twins.py`
  converges (15 of 15), and over the full year 2021 718 of 759 partial steps converge at 900 s and 614 of 643 at
  3600 s, each ending 0.000 to 0.050 K above the target (measured 2026-10-12 on this branch, §11).
- **Two devices**: §7.

## 5. Devices

| Device | Controller (owner of the PLR and the target) | Target | Booking below PLR 1 |
|---|---|---|---|
| `GenericBoiler`, hot water, every fuel | `GenericBoilerController` | its warm-water aim, 60 °C (the charge ends at `T0 ≥ 60 °C`) | `PLR m_full` at the full-load supply; heat `m c (T_sup − T_ret)`; fuel `PLR · fuel_full` |
| `MoreAdvancedHeatPumpHPLib`, hot water (parked, #916) | `MoreAdvancedHeatPumpHPLibControllerDHW` | its set temperature (with the energy manager's raise) less the 0.5 K switch-off tolerance | `PLR m_full` at hplib's capped outlet; `P_th = m c (T_out − T̄)`, `P_el = P_th / COP(T̄)`; the brine pump's electricity `PLR · P_pump` |
| `ElectricHeating`, hot water (parked, #916) | `ElectricHeatingController` | the 60 °C warm-water aim, where the diverter valve ends a charge alone and beside space heating (its parallel mode latches like the hot-water mode) | `PLR m_full` at the full-load supply; heat and electricity `m c (T_sup − T_ret)` |
| `SolarThermalSystem`, pump (#917) | `SolarThermalSystemController` | its warm-water aim, 60 °C (the pump stops at `T0 > 60 °C`) | `PLR m_full` at the collector's full-load outlet; heat `m c (T_out − T_in)`; pump electricity `PLR · P_pump` |

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

## 8. The buffer-less distribution loop (dropped)

#918 computed a loop the distribution system pumps itself (`NO_STORAGE_MASS_FLOW_FIX`, district heating) quasi-steady
within the step above its turnover criterion (`a = m_design dt / M_pipe > 1`) and the threshold: the emitters
exchanged the building's demand, the return followed from the balance, and the pipe water ended the step at the mean
of supply and return. It was built against 16 to 24 passes per step at 900 s (hisim-hhj2). On the kind order the
dynamic, one-step-lagged loop needs 3.16 passes at 900 s and 3.24 at 3600 s and agrees better with 60 s than the
quasi-steady one (§11), so the owner dropped it on 2026-10-12 (decision 19). The loop keeps its dynamic behaviour at
every step length; the code stays on the branch `part-load-hds`.

## 9. Thresholds

- **Stores with a node** (cylinder, buffer): `dt > part_load_above_seconds` (default 600 s).
- **Buffer-less loop** (dropped, §8): was `a = m_design dt / M_pipe > 1` and `dt > part_load_above_seconds`; for the
  district-heating twin (121.2 m², 212.7 kg of pipe water, 0.27 kg/s) `a` is 0.076 at 60 s, 1.142 at 900 s and 4.569
  at 3600 s.

## 10. What changes and what does not

| Part | Change | PR |
|---|---|---|
| Simulator, `MixedNode.step`, `SimpleDHWStorage` | none | – |
| `SimulationParameters` | `part_load_above_seconds` (default 600) | #915 |
| `hisim/part_load.py` | the rule, the controller part and the device part | #915 |
| `GenericBoilerController`, `GenericBoiler` | the PLR of the hot-water charge and its report | #915 |
| `MoreAdvancedHeatPumpHPLibControllerDHW`, `MoreAdvancedHeatPumpHPLib` | the PLR of the hot-water charge and its report | #916, parked |
| `ElectricHeatingController`, `ElectricHeating` | the PLR of the hot-water charge and its report | #916, parked |
| `SolarThermalSystemController`, `SolarThermalSystem` | the PLR of the pump and its report | #917 |
| `HeatDistribution` | none (the quasi-steady path for the buffer-less loop, #918, is dropped) | – |
| Controllers' on/off and mode decisions | none | – |
| 60 s results and goldens | none | – |

The same pattern serves the devices that will come (a separate hot-water heat pump, hisim-lenz; direct electric hot
water, hisim-epc.21; the hybrid heat pump, hisim-epc.20), and the room-temperature devices with the room as their store
(hisim-w510).

Amendments to the hydronic spec, to be written into `hydronic_coupling_spec.md` when #915 is accepted:

- **D3** stays as it is: the quasi-steady loop is dropped (§8).
- **D4**: controllers still decide on `T0`; above the threshold the controller of a device that is on also sets the
  fraction of the step it runs, by a memoryless rule on the store's end temperature and the ratio its device ran.
- **D6**: one circuit per step stays; above the threshold that circuit may run for a fraction of the step.

## 11. Measured results

Full year 2021, the shipped predefined occupancy profile, measured on 2026-10-12 with the simulator's kind order
(#922) below, at 60 s, 900 s and 3600 s; every figure is against the 60 s run of the same head. "Flagged" counts the
steps the simulator returns with `force_convergence` set; it sets it after the eleventh pass, so a step that converges
on its twelfth pass is flagged without having run a forced pass. "Forced" counts the steps with at least one forced
pass (13 passes or more). A partial step is one whose controller's ratio lies strictly between 0 and 1; it converged
when it is not flagged.

**The boiler (#915).** The 60 s year of the gas, gas + solar and pellet twins is bit-identical to #922 in every common
column, with the same passes (3.18 for gas). Gas twin, with the rule / on #922 without it (whole-step charges):

| step | hot-water heat and gas | mean passes | flagged | forced | partial steps converged | converged steps end above the target, mean / max | wall time |
|---|---|---|---|---|---|---|---|
| 900 s | -0.002 % / +0.17 % | 3.29 / 3.22 | 94 / 38 | 59 / 12 | 718 of 759 | 0.032 / 0.050 K | 16 s / 15 s |
| 3600 s | -0.012 % / +0.63 % | 3.67 / 3.32 | 47 / 0 | 45 / 0 | 614 of 643 | 0.037 / 0.050 K | 9 s / 8 s |

The oil, pellet, wood-chip and hydrogen twins give exactly these hot-water figures and step counts. Wall times are
with two runs sharing six cores; the 60 s year takes 124 to 158 s. The rule removes the step-length error of the hot
water; its price is iteration: 41 of the 94 flagged steps at 900 s are partial steps, 53 whole-step charges. In the
gas + solar twin, whose collector runs whole steps, the hot-water gas is -0.18 % / -0.66 % and the collector
+1.28 % / +4.31 % at 900 s / 3600 s, with 123 / 56 flagged steps (on #922: +1.17 % / +4.35 %, -6.19 % / -24.01 %,
46 / 9). Four of its converged partial boiler steps end above the band, the worst 5.3 K: the collector lifts the tank
past the boiler's band while the boiler's ratio decays towards 0 (§7). Space heating is not part of the rule: the
boiler books +5.7 % / +21.3 % space-heating heat while the building receives -0.17 % / -0.66 %, on #922 too, because
the space-heating buffer does not close its balance at coarse steps (stage D, hisim-fxix.5, hisim-ign9).

**Parked (#916; decision 18)**, measured on its branch the same way, 900 s / 3600 s. The heat pump's
hot-water electricity is +0.96 % / +8.89 % with the rule and +0.59 % / +9.36 % with whole-step charges, with
1784 / 887 against 1601 / 618 flagged steps; 1383 of the 1784 flagged steps at 900 s run the whole step, the energy
manager's raise cycle at the heat pump's own draw (hisim-4g9.28), and the 3600 s excess is the COP above 63 °C under
that raise. The electric heater is -0.07 % / -0.26 % with the rule and 73 / 72 flagged steps (hisim-fxix.17), against
-0.14 % / +0.04 % and none with whole-step charges. On this base the rule does not improve the heat pump and the
electric heater, and what is left for the heat pump comes from the energy manager's loop, so both wait for it. (On
2026-10-12 the collector was measured on #916's branch: -1.75 % / -1.00 % with its rule, and the heat pump + solar
twin's hot-water electricity +1.22 % / +13.64 % with the heat pump's rule.)

**The collector (#917; decision 20)**, measured 2026-10-14 on its branch, directly on #915 without #916, full year
2021 with the predefined occupancy, against the 60 s run of the same head. The 60 s years of both solar twins are those
of #915 (the ratio is exactly 1): gas + solar 418.8 kWh of collector heat and 3002.3 kWh of hot-water gas, 14 flagged
steps; heat pump + solar 595.8 kWh and 693.6 kWh of hot-water electricity, 216 flagged steps. Each cell gives #917,
then #915 (the pump runs whole steps), 900 s / 3600 s:

| twin: quantity | #917 | #915 |
|---|---|---|
| gas + solar: collector heat | -1.75 % / -1.00 % | +1.28 % / +4.31 % |
| gas + solar: hot-water gas | +0.28 % / +0.14 % | -0.18 % / -0.66 % |
| gas + solar: mean passes | 3.49 / 4.02 | 3.31 / 3.75 |
| gas + solar: flagged steps (with a forced pass) | 165 (128) / 223 (208) | 123 (91) / 56 (48) |
| gas + solar: partial collector steps converged | 1446 of 1458 / 168 of 284 | – |
| gas + solar: partial boiler steps converged | 617 of 671 / 548 of 583 | 615 of 672 / 540 of 581 |
| gas + solar: the tank's highest temperature | 61.44 / 69.48 °C (60 s: 60.46 °C) | – |
| heat pump + solar: collector heat | -0.71 % / -1.75 % | +2.85 % / +0.85 % |
| heat pump + solar: hot-water electricity | +2.10 % / +14.57 % | +1.42 % / +13.67 % |
| heat pump + solar: mean passes | 4.56 / 6.00 | 4.41 / 5.75 |
| heat pump + solar: flagged steps (with a forced pass) | 1260 (1240) / 534 (518) | 1225 (1206) / 385 (375) |
| heat pump + solar: partial collector steps converged | 1245 of 1255 / 168 of 273 | – |

The gas + solar figures equal those of 2026-10-12 on #916's branch to every digit given: #916 does not touch them. The
collector's rule takes the gas + solar collector within 1.75 % of 60 s at both steps and the boiler's hot-water gas
within 0.3 %; the price is flagged steps at 3600 s, where 116 of the collector's 284 partial steps do not converge.
In the heat pump + solar twin the heat pump runs whole steps here (#916 is parked); its hot-water electricity keeps
the error of the energy manager's raise (hisim-4g9.28). hisim-fxix.11's done-when (collector and backup within 1 %
at both steps, no step at `force_convergence`) is not met; it stays open.

**Dropped (#918; decision 19).** District-heating twin, the quasi-steady loop / the dynamic one-step-lagged loop on the
same base: heat drawn (the bill) -0.35 % / -0.03 % at 900 s and -0.31 % / +0.03 % at 3600 s, heat delivered to the
building -0.09 % / -0.08 % and -0.24 % / -0.21 %, mean passes 5.42 / 3.16 and 7.03 / 3.24, flagged steps 0 / 0 and
23 / 0. The dynamic loop meets hisim-hhj2's criterion (delivered heat and bill within 1 %, no forced step at 900 s) on
its own, and the 16 to 24 passes per step at 900 s the quasi-steady path was built against no longer occur.

**The old stack (2026-10-10)**, before the kind order, for the decision it led to. Gas twin, full year against 60 s of
the same head (then #914): in the twins' order, where the boiler ran before its controller, the rule settled on 5 of
739 partial steps at 900 s and flagged 1013 / 679 steps at 900 s / 3600 s, with hot-water gas +0.27 % / +0.18 %; with
the controller moved first it settled on 718 of 759 and flagged 46 / 50 steps (-0.00 % / -0.01 %), with fewer passes
than the pass-counting search it replaced (3.42 against 4.20 at 900 s). That led to the kind order and the physics
order (§4.3, #922, hisim-fxix.16, closed).

**hisim-fxix.19's flow hysteresis alone, on the step mean, on the physics order (K′), 2026-10-11.** The same year
with the physics order of §4.3 (each L1 controller's device before the other physics the controller reads), measured
on the old stack with #922 on top (after the removal of the simulator's fixed-point acceleration, #927, and with the
solar pump's first hysteresis, hisim-fxix.19: the flow band alone, decided on the step mean, before the start-of-step
decision and the restart band of hisim-fxix.21 and before the differential controller of 2026-10-14), against the
60 s run of the same head, with the collector's part load (#917) and the heat pump's and the electric heater's (#916)
on the branch. K is the kind order alone on the same base. Deviations are from the 60 s run, "forced" counts the
flagged steps, and "partial steps converged" the partial steps that were not flagged. The physics order is part of
#922 since, so every measurement above is on K′:

| twin | step | hot-water fuel or electricity K / K′ | collector K / K′ | passes K / K′ | forced K / K′ | partial steps converged K / K′ |
|---|---|---|---|---|---|---|
| gas | 900 s | -0.00 % / the same | – | 3.29 / the same | 94 / the same | 718 of 759 / the same |
| gas | 3600 s | -0.01 % / the same | – | 3.67 / the same | 47 / the same | 614 of 643 / the same |
| heat pump | 900 s | +0.96 % / the same | – | 4.63 / the same | 1784 / the same | 586 of 872 / the same |
| heat pump | 3600 s | +8.89 % / the same | – | 5.84 / the same | 887 / the same | 83 of 581 / the same |
| electric | 900 s | -0.07 % / the same | – | 5.29 / the same | 73 / the same | 409 of 482 / the same |
| electric | 3600 s | -0.26 % / the same | – | 6.95 / the same | 72 / the same | 403 of 474 / the same |
| gas + solar | 900 s | +2.58 % / -0.10 % | -14.45 % / +0.58 % | 4.35 / 3.50 | 1298 / 172 | boiler 6 of 637, collector 0 of 214 / boiler 621 of 665, collector 1453 of 1486 |
| gas + solar | 3600 s | +0.57 % / +0.00 % | -2.35 % / -0.14 % | 5.24 / 4.03 | 880 / 228 | boiler 3 of 521, collector 1 of 185 / boiler 549 of 582, collector 175 of 299 |
| heat pump + solar | 900 s | +1.39 % / the same | +0.66 % / the same | 4.58 / the same | 1294 / the same | collector 1205 of 1237, heat pump 300 of 306 / the same |
| heat pump + solar | 3600 s | +13.48 % / the same | +0.23 % / the same | 6.20 / the same | 735 / the same | collector 190 of 332, heat pump 51 of 305 / the same |

The 60 s references: gas 3463.3 kWh of hot-water gas; heat pump 1067.8 kWh of hot-water electricity; electric
3090.8 kWh; gas + solar 2997.0 kWh of hot-water gas and 423.9 kWh of collector heat; heat pump + solar 690.1 kWh and
614.3 kWh. Only `household_gas_solar_thermal` changes: the other twins already evaluated each device before the
stores its controller reads, and their results are bit for bit those of K. In K′ `household_gas_solar_thermal` and
`household_gas_solar_thermal_building_sizer`, which differ only in the order their components are added, give
identical results. Without the pump's hysteresis K′ forces 165 steps of the gas + solar year at 900 s (1292 in K);
the 172 that remain are slow boiler heat-ups, alternations where the draw takes more than the rise the step still
needs (hisim-fxix.17), and about 55 steps in which the collector pump switched on with no fixed point (hisim-fxix.21).

**The solar pump's hysteresis on #917 (#941), measured 2026-10-14**, full year 2021 with the predefined occupancy,
#917 → #941, each against the 60 s run of its own head. #941 decides on the start-of-step sensors with the two
hysteresis bands of §5.3 of the hydronic coupling spec, and its full-tank stop still sits at the 60 °C aim, 3 K
restart band included:

| twin, step | flagged steps | pump switch-ons | collector heat, kWh (against 60 s) | backup's hot-water heat, kWh |
|---|---|---|---|---|
| gas + solar, 60 s | 14 → 0 | 1855 → 253 | 418.8 → 358.7 | 2702.0 → 2761.5 |
| gas + solar, 900 s | 165 → 95 | 1638 → 220 | 411.5 → 341.3 (-1.75 % → -4.85 %) | 2709.5 → 2778.3 |
| gas + solar, 3600 s | 223 → 61 | 428 → 180 | 414.6 → 302.6 (-1.00 % → -15.64 %) | 2705.8 → 2816.0 |
| heat pump + solar, 60 s | 216 → 194 | 1677 → 353 | 595.8 → 584.6 | 2473.3 → 2486.5 |
| heat pump + solar, 900 s | 1260 → 1243 | 1466 → 336 | 591.6 → 584.7 (-0.71 % → +0.02 %) | 2476.3 → 2483.6 |
| heat pump + solar, 3600 s | 534 → 410 | 449 → 286 | 585.4 → 539.0 (-1.75 % → -7.81 %) | 2482.4 → 2525.3 |

The hysteresis removes most pump switch-ons and, in the gas + solar twin, most flagged steps. The 60 s years equal
those of #941 measured on #915 (the collector's ratio is exactly 1 at 60 s). The restart band 3 K below a 60 °C stop
costs collector heat at every step length (hisim-fxix.22), and the coarser the step the more (gas + solar at 3600 s:
-15.6 % against 60 s). #942 moves the stop to the solar controller's own 80 °C maximum.

**The differential controller with a speed-controlled pump (#941), measured 2026-10-14**, full year 2021 with the
predefined occupancy, the flow hysteresis above → the differential controller (§5.3 of the hydronic coupling spec:
on at 7 K and off at 3 K of the collector's sensor above the tank, a pump of 5 to 40 kg/(m² h) with a 20 K target
lift, the curve at half the lift above the inlet). "Flips" counts the steps whose pump signal changes between passes
(the first pass reads the previous step's sensors), "flagged" the steps at `force_convergence` and, in brackets,
those of them with a flip; the backup is the tank's heat from the boiler or the heat pump:

| twin, step | collector heat, kWh | backup, kWh | pump switch-ons | pump hours | flips | flagged | mean passes | max tank, °C |
|---|---|---|---|---|---|---|---|---|
| gas + solar, 60 s | 358.7 → 400.6 | 2761.5 → 2719.9 | 253 → 348 | 426 → 673 | 506 → 713 | 0 → 0 | 3.09 → 3.09 | 60.47 → 60.47 |
| gas + solar, 900 s | 341.3 → 393.7 | 2778.3 → 2726.1 | 220 → 321 | 407 → 657 | 450 → 647 | 95 (1) → 92 (2) | 3.27 → 3.30 | 61.51 → 61.00 |
| gas + solar 20 m², 60 s | 633.1 → 680.7 | 2489.8 → 2442.2 | 337 → 367 | 280 → 330 | 696 → 755 | 0 → 0 | 3.09 → 3.09 | 60.58 → 60.58 |
| gas + solar 20 m², 900 s | 624.2 → 666.9 | 2497.5 → 2455.2 | 320 → 355 | 279 → 321 | 652 → 720 | 98 (15) → 87 (14) | 3.27 → 3.29 | 60.75 → 60.40 |
| heat pump + solar, 60 s | 537.5 → 607.2 | 2723.9 → 2652.6 | 342 → 458 | 626 → 1022 | 690 → 933 | 211 (0) → 371 (0) | 3.75 → 3.74 | 60.12 → 60.12 |
| heat pump + solar, 900 s | 514.0 → 571.7 | 2746.2 → 2689.4 | 321 → 433 | 596 → 970 | 656 → 876 | 1328 (13) → 1295 (22) | 4.46 → 4.47 | 60.05 → 60.05 |

The pump starts at a lower sun than at the old switch-on flow and runs at its minimum flow on weak sun, so the
collector gives 7 to 15 % more heat, the backup correspondingly less, and the pump runs about 60 % longer. The
switch-ons a day hardly depend on the area any more: 0.95 at 4 m² against 1.01 at 20 m² at 60 s (before 0.69
against 0.92); at 20 m² the 60 °C stop holds the pump most of the summer. The collector heat at 900 s lies 1.7 %
(gas + solar) and 5.8 % (heat pump + solar) below 60 s, against 4.9 % and 4.4 % before. The heat pump + solar twin
flags 160 more steps at 60 s, none of them with a flipping pump signal. The gas + solar "before" column reproduces
the table above to the digit; the heat pump + solar one does not (537.5 against 584.6 kWh of collector heat at
60 s). Both columns here come from one script on the same day, so the comparison holds; the cause of the difference
to the earlier table was not traced.


## 12. Beads

Epic hisim-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-naze (P1, closed) | threshold, rule, the boiler | #915 |
| hisim-fxix.16 (P1, closed) | the evaluation order that kept the rule from settling in the boiler and collector twins: the kind order and the physics order | #922, below #915 |
| hisim-fxix.12 (P1) | the heat pump's hot-water side | #916, parked |
| hisim-fxix.11 (P1) | the collector | #917 |
| hisim-fxix.19 (P2, closed) | the solar pump's hysteresis | #941 |
| hisim-fxix.21 (P3, closed) | the solar pump's switch-on step without a fixed point: decided on the tank's start-of-step temperature | #941 |
| hisim-hhj2 (P1, closed) | the buffer-less distribution loop: on the kind order it needs no quasi-steady path | #918, dropped |
| hisim-bq3h (P3) | solar priority as a control strategy | – |
| hisim-ign9 (P2) | the space-heating buffer, after stage D (hisim-fxix.5) | – |
| hisim-3y10 (P2) | the open points of decision 5 | measured on the old stack (#915 to #918) |
| hisim-w510 (P4) | room-temperature devices (parent hisim-4g9) | – |
