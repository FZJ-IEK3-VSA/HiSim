# Spec: the hplib heat pump holds its outlet at the set point it is given, modulating down to a sourced minimum output, with sourced minimum running and idle times

Status: decided (owner, 2026-11-26: D1 to D13 of §2), measured on throwaway prototypes of the chosen design and of
the rejected one (§7), nothing built; §8 is empty. Owner: Noah Pflugradt. Epic: hisim-heat-pump-model-rework-fpqz (beads hisim-heat-pump-model-rework-fpqz.7
and hisim-60i6, both P1; the alignment above 600 s is hisim-ef6u). Builds on: `roadmap/hydronic_part_load_spec.md`
(decisions 11, 16, 21 to 23), `roadmap/two_mode_buffer_spec.md` (§1.1, Q2), `roadmap/heat_pump_model_rework_spec.md`
(decisions 5 and 15, steps b and d), `hisim/part_load.py`, the backup rod of #1113 and the buffer-size study of
2026-11-26 (`/home/noah/hisim/agent-tmp/buffer-sizing-review/REPORT.md`, bead hisim-heat-pump-buffer-plan-7e58).

Code is cited against the worktree branch `heat-pump-modulation-spec` at bad5fa8c2 (code identical to the
stratified-storage spec branch below it).

## 1. Problem

`MoreAdvancedHeatPumpHPLib` (`hisim/components/more_advanced_heat_pump/heat_pump.py`) delivers its full capacity
whenever it heats at a step length at or below the part-load threshold (`SimulationParameters.part_load_above_seconds`,
600 s), which is every 60 s run and so every reference run:

- In the parallel-buffer arrangement, the one every heat-pump twin uses, a space-heating step is
  `space_heating_at_hplib_flow`: hplib answers the buffer's return with its capacity `P_th` at a 5 K lift and the flow
  `P_th / (c · 5 K)`, and the step books the requested ratio's share of that heat (`BackupHeatingRod.heating`). The
  space-heating controller requests a ratio of exactly 1 at and below the threshold
  (`StartOfStepPartLoadControl.next_requested_raising_ratio`, decision 16 of the part-load spec), so the machine runs at
  its capacity, 6.6 to 7 kW for the 7.78 kW twin, whatever the house needs. `minimum_thermal_output_power_in_watt`
  (1800 W) is only hplib's floor: where the capacity would fall below it, hplib raises the capacity
  (`CarnotCopHeatPump.simulate`); it never lowers the power.
- In the series and no-storage arrangements (`space_heating_at_fixed_flow`, no recorded twin) the lift to the set
  temperature, at most 5 K, over the nominal flow sets the power: a modulation by the lift, capped at 5 K, with an
  unsourced start-up ramp (`START_UP_TIME_CONSTANT_IN_SECONDS`, 360 s).
- Above 600 s the ratio acts, but as a run-time fraction at full load: `r · P_th` over the step, published as `r · m` at
  the full-load outlet. hisim-ef6u asks for the split into power and time there.
- The machine keeps running or resting for `minimum_running_time_in_seconds` = `minimum_idle_time_in_seconds` =
  3600 s (`MoreAdvancedHeatPumpHPLibConfig`), ten to twenty times the anti-short-cycle times of real compressors (§4.5).

Measured consequence (buffer-size study, grouped heat-pump twin, 7.78 kW floor-heating house, full year 2021, no energy
manager): at 60 s the machine runs 6.6 kW for at least an hour, lifts a 20 l/kW buffer to 62 °C, then rests an hour while
the buffer runs empty; SPF 3.69 at 20 l/kW against 4.17 at 50 l/kW, rooms down to 16.3 °C, 1980 starts a year; at 900 s
the part-load ratio modulates (mean 0.27 to 0.30 of each step) and the size makes no difference to the SPF (3.97 to
4.02). January alone (§7, before the prototypes): 299 starts a month at 60 s with a 20 l/kW buffer against 1 at 900 s,
and 616 kWh of space-heating electricity against 582 kWh (5.9 % more). The 60 s reference therefore penalises small
buffers, inflates starts and flow temperatures, and the >600 s mode is checked against a reference that does not
modulate. The hot-water chain shows the same effect (bead hisim-60i6: 3.63 kWh of unmet hot water at 60 s against
1.04 kWh at 900 s in four January weeks, because the 3600 s idle time holds the machine off while the evening draws
come).

A real inverter heat pump controls its outlet (flow) temperature to the set point its controller gives it: it lowers
its compressor speed as the return rises towards the set point, down to a minimum output, typically a quarter to two
fifths of its capacity (§4.4), and cycles only below it; its compressor protection keeps it on for about ten minutes
and off for about ten to twenty minutes (§4.5).

## 2. Decisions

**D1 — The heat pump controls its own outlet temperature inside the device (design C of §4.8)** (owner, 2026-11-26).
It modulates its power so that its outlet reaches the supply set point its controller gives it, down to its minimum
output, and cycles only below that. Why: that is how an inverter unit's own control works, with the decision (the set
point, on/off) in the L1 controller and the execution (the power that reaches the set point within the machine's
limits) in the physics. Consequence: the device's power is a closed-form law of its return, its flow and the set point
(§4.2); hplib's constant-lift flow model is replaced by a constant nominal flow with a variable lift in every
arrangement (§4.3); the part-load ratio keeps its meaning (the fraction of the step the device runs, 1 at and below the
threshold) and the >600 s mode runs that fraction at the law's power (§4.7). Design A (the exact rule at every step
length with the ratio as a power share, the agent's recommendation, measured in §7) is rejected and kept in §4.8 as the
record.

**D2 — The minimum output is 0.25 of the rated capacity for an inverter machine and 1.0 for an on/off machine, as a
sized field** (owner, 2026-11-26; §4.4, the agent had proposed 0.30). Consequence: `minimum_thermal_output_power_in_watt`
keeps its name and unit, becomes `AUTO` in the preset with the law 0.25 × `set_thermal_output_power_in_watt` (1945 W
for the 7.78 kW twin instead of 1800 W; a stated value still wins), decided by the `compressor_control` field of rework
decision 5 (default air/water inverter, brine/water on/off), which this spec introduces ahead of rework step b.

**D3 — The minimum running time and the minimum idle time default to 600 s each** for the default machine, the
air/water inverter (owner, 2026-11-26; §4.5); the on/off brine preset takes 1200 s of idle time when it exists
(hisim-ztt7). Consequence: every heat-pump golden at 60 s moves; at 900 s a step already exceeds both times, so only
where the 3600 s times bound (the hot-water chain's waits) do results move.

**D4 — Hot water charges at full load, unmodulated, with one minimum output per machine** (owner, 2026-11-26; §4.3).
Consequence: the hot-water constant `HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT` (0 W) goes; the hot-water controller
and the parked hot-water part load (#916) are not touched; a modulated hot-water charge is a bead for after #916.

**D5 — The 60 s / 900 s tolerance stays the guide's 1 %; PR 4 measures the full year and the owner decides on it**
(owner, 2026-11-26; the old Q5). The prototype's cycling range lies 1.5 to 3.9 % apart (§7.1); a wider tolerance
before the year is measured would let the step length decide the result.

**D6 — Above the threshold the starts are the hysteresis cycle, computed in the space-heating L1 controller** (owner,
2026-11-26; the old Q7). The agent's estimate from the minimum times, `dt / max(t_run,min / tau, t_idle,min /
(1 − tau))`, is the most starts the minimum times allow, an upper bound, not the expected count, and is withdrawn. Below
the minimum output the machine cycles on the buffer's hysteresis band: `t_on = C_buf · ΔT_band / (P_min − P̄)`,
`t_off = C_buf · ΔT_band / P̄`, each at least its minimum time, starts per step `dt / (t_on + t_off)`. The buffer
publishes its heat capacity `C_buf` (J/K) as a port quantity; the controller owns the band, reads the delivered mean
power `P̄` and the minimum output `P_min` from the heat pump's published outputs, and carries a fractional start count
across steps in its state, so long cycles at 900 s count correctly (§4.7, which also says who publishes the starts and
why). Consequence: the buffer gains an output, the controller an output and a state field; the 900 s starts KPI becomes
this estimate (PR 3); §7.3 checks the formula against the prototype's 60 s cycles.

*Amendment (owner, 2026-11-26):* the buffer's heat capacity is not a port quantity and principle B.2 of
`component_design_principles.md` is not amended. The buffer hands it over at configuration time instead, as the
sizing contribution principle B.1 already allows ("at configuration time, through the sizing contributions it
declares"): the buffer's config class declares a `FactContribution` of a new sizing fact,
`storage_heat_capacity_in_joule_per_kelvin` (its resolved water mass times `c`), and the space-heating controller's
config reads it through a `sized_field`; a stated value wins (§4.7). The buffer publishes no output for it, and the
bead that would have amended B.2 is not filed.

**D13 — PR 4 asserts the yearly 900 s starts estimate within ±25 % of the 60 s count and records the monthly ratios**
(owner, 2026-11-26; the old Q13, option (a)). The starts KPI is an order-of-magnitude figure for the cycling bead and
the buffer study, not an energy.

**D7 — The set point is the heating curve's supply plus the raise plus 2.5 K now; PR 4 measures supply + 5 K and the
curve itself on design-day comfort and electricity; the curve itself once the stratified buffer exists** (owner,
2026-11-26; the old Q8).

**D8 — The start-up ramp stays and is sourced** (owner, 2026-11-26; the old Q9, against the agent's recommendation to
drop it). §4.3 gives the sources and the time constant, and §4.6 reconciles the ramp with rework step b's start-up loss
so that a start is not priced twice.

**D9 — The nominal flow is sized from the rated capacity over `c` × 5 K; a stated value wins** (owner, 2026-11-26; the
old Q10). 0.372 kg/s for the 7.78 kW twin.

**D10 — Hot water keeps hplib's flow at a 5 K lift until rework step d** (owner, 2026-11-26; the old Q11).

**D11 — The backup rod runs only on the compressor's outlet limit in PR 2** (owner, 2026-11-26; the old Q12, option
(a)). Case (1) of rework decision 8, the rod filling a request above the capacity, does not fit a control target and
is switched off under C; the controller's release of case (1) is a bead, judged on PR 4's design-day comfort (its text
is in §9).

**D12 — The ratio keeps its name and meaning** (owner, 2026-11-26; the old Q6, moot under C). No question is open
(§8).

## 3. Terms

- **Set point `T_set`**: the outlet (supply) temperature the L1 controller tells the device to hold, in °C; today the
  controller's `TargetTemperatureShInCelsius`, the heating curve's supply temperature plus the energy manager's raise
  plus half the hysteresis band's upper offset (2.5 K with the default 5 K band; owner, 2026-10-19).
- **Return `T_ret`**: the temperature of the water entering the device's secondary side, in °C: the buffer's step mean
  at and below the threshold (the iterated buffer, two-mode spec), its start-of-step temperature above it, the
  distribution's return in the series and no-storage arrangements. Read at the device's port in every pass; the
  simulator's passes settle it, the device iterates nothing.
- **Nominal flow `m_nom`**: the secondary pump's constant flow, in kg/s (`massflow_nominal_secondary_side_in_kg_per_s`;
  D9 sizes it from the rated capacity over the 5 K design lift, 0.372 kg/s for the twin).
- **Required lift**: `T_set − T_ret`, in K; **requested power**: `m_nom · c · (T_set − T_ret)`, in W.
- **Capacity `P_cap(T_source, T_out)`**: the compressor's largest thermal power at the step's source and outlet, in W:
  hplib's `P_th` evaluated at that outlet until rework step d replaces it by the sourced capacity law.
- **Minimum output `P_min`**: the lowest power the compressor holds before it has to cycle, in W, D2's share of the
  rated capacity; **modulation range**: `P_min` to `P_cap`.
- **Part-load ratio `r`**: the fraction of a step the device runs, 0 to 1 (`RequestedPartLoadRatioSh`,
  `ActualPartLoadRatioSh`); exactly 1 at and below the threshold while the device runs, the exact rule's fraction above
  it. Unchanged in meaning.
- **Minimum running time, minimum idle time**: the shortest run and rest the device enforces after a start and a
  stop, in s; device protection (`component_design_principles.md` §A).
- **Start**: a step in which the machine runs after a step in which it did not (`get_heatpump_cycles`, `TimeOff`).
- **Switch-on point, switch-off point**: supply − 5 K (never below the room set point) and supply + 5 K, with the
  raise (`heating_switch_temperatures_in_celsius`), on the buffer's start-of-step temperature; **band `ΔT_band`**:
  their difference, in K (10 K by default, less where the room set point floors the switch-on point).
- **Buffer heat capacity `C_buf`**: the water mass of the buffer times `c`, in J/K, a sizing fact the buffer's config
  contributes and the controller's config reads at configuration time (`storage_heat_capacity_in_joule_per_kelvin`,
  D6 as amended): 650 kJ/K for 155.6 l, 1.63 MJ/K for 389 l.
- **Delivered mean power `P̄`**: the heat pump's space-heating heat averaged over the step, in W
  (`ThermalOutputPowerSH`; above the threshold `r · P`).

## 4. Design

### 4.1 Who does what

| Part | Kind | Today | After this spec |
|---|---|---|---|
| `MoreAdvancedHeatPumpHPLibControllerSpaceHeating` | L1 controller | on/off hysteresis on the buffer's start temperature; publishes its target (`TargetTemperatureShInCelsius`) and the requested ratio (1 at ≤ 600 s, the exact rule above) | the same decisions and outputs; its target is wired to the device's set-point input by default (today that input exists only in the series and no-storage arrangements and is fed by the heating curve there). Which offset the set point carries is D7. PV mode unchanged |
| `MoreAdvancedHeatPumpHPLib` | physics | parallel arrangement: hplib's capacity at a 5 K lift and hplib's flow; series: the lift to the set point capped at 5 K over the nominal flow, with a start-up ramp | every arrangement: the outlet-control law of §4.2 over the nominal flow, the lift uncapped, the power within `[P_min, P_cap]`, the rod only where the compressor cannot reach the set point; the capacity evaluated at the actual outlet and cached per outlet; the ramp per D8 |
| `SimpleHotWaterStorage` (buffer) | physics | iterated mixed node at ≤ 600 s, start-of-step node above | unchanged |
| `MoreAdvancedHeatPumpHPLibConfig` | config | `minimum_thermal_output_power_in_watt = 1800`, flow 0.333 kg/s plain, times 3600 s | the minimum output a sized field (D2), `compressor_control`, the nominal flow sized (D9), times 600 s (D3), every default with its source |

Nothing crosses a port that is not a port quantity (a set point, a return, a flow, an outlet, powers, a ratio), no
component reads another's model constant, and nothing iterates inside a component: the law below is closed-form in
the pass's inputs, and the mixed buffer's step mean settles over the simulator's passes as it does today.

### 4.2 The device's law, one closed form per pass

While its controller runs it in space heating, from the return `T_ret` it reads, the set point `T_set`, its nominal flow
`m_nom` and the step's source and ambient temperatures:

1. **Requested power**: `P_req = m_nom · c · max(T_set − T_ret, 0)`. A return at or above the set point requests
   nothing, and the machine, which its controller holds on, runs at its minimum output (step 2).
2. **Compressor power**: `P = min(max(P_req, P_min), P_cmax)` with `P_cmax = min(P_cap(T_source, T_out), m_nom · c ·
   (T_max,out − T_ret))`, the smaller of the capacity at the outlet and what the flow carries up to the compressor's
   maximal outlet (`CarnotCopHeatPump.maximal_outlet_temperature_in_celsius`, today hplib's COP-1 line; the configured
   limit once hisim-heat-pump-outlet-limit-2vq7 exists). `BackupHeatingRod.heating` already does this clamp for a
   required outlet at a flow; the only change is the required outlet, now `T_set` itself (at least the outlet the
   minimum output gives) instead of the capped 5 K lift. Its case (1), the rod filling the gap between the request
   and the capacity, is a demand rule and does not fit a control target: a cold return after a hot-water charge
   requests `m_nom · c · 10 K` and the rod would fill it (measured, §7.1). Under C the rod runs on case (2) only, above
   the compressor's maximal outlet (D11); the controller's release of case (1) for a house whose load exceeds the
   capacity is a bead (§9).
3. **Outlet**: `T_out = T_ret + P / (m_nom · c)`: the set point while `P_req` lies in the modulation range; above it
   while the minimum output overheats; below it while the capacity or the maximal outlet caps the compressor, where
   the rod adds `min(P_req − P, P_rod)` and lifts the outlet on towards the set point.
4. **Electricity**: `P_el = P / COP(T_source, T_out)`, the Carnot-efficiency COP at the actual outlet (§4.6); the rod at
   COP 1 on its own output.
5. **Booked heat**: `m_nom · c · (T_out − T_ret)`, the heat the published flow carries, as the flow-booking rule
   requires (`tests/test_heat_pump_flow_booking.py`); the published flow is `m_nom` at and below the threshold.

The capacity depends on the outlet, and the outlet on the power: `P_cap(T_source, T_out)` is taken at the requested
outlet `T_set` where the request lies within the capacity, and the clamp of step 2 then holds at the outlet the capacity
gives; hplib's capacity falls by about 2 % per kelvin of outlet, so the second evaluation changes `P` by less than the
0.1 K grid the cache rounds to, and no iteration is needed. The hplib cache key gains the outlet (today it keys on the
return and the mode and assumes a 5 K lift, which a variable lift breaks).

**Hysteresis and switch-off.** The controller's on/off decision stays what it is: heating starts when the buffer's
start-of-step temperature falls below the switch-on point and ends when it rises above the switch-off point. Within a
run the device follows the law above. While the house needs at least the minimum output the return settles at
`T_set − L / (m_nom · c)` (2.0 K below the set point at 3 kW with 0.372 kg/s, 5 K at the design load), the outlet at
the set point, and the run does not end. When the need falls below the minimum output the device delivers `P_min`, the
buffer rises past the set point to the switch-off point, the controller stops the machine, the buffer coasts down to
the switch-on point and the machine restarts: the explicit cycling of the fine model, in cycles of hours (a 10 K band on
389 l holds 4.5 kWh, two and a half hours at 1.9 kW with no load), well inside the minimum times. The device needs no
stop rule of its own; a real unit's outlet hysteresis is the same decision taken on the buffer sensor here.

**At and below the threshold** the device runs whole steps (`r` = 1), reads the iterated buffer's step mean as its
return and the loop settles in the passes as today; the law contracts the loop (a change of the return moves the
power by `m_nom · c` per kelvin, and the 389 l buffer's step mean by `m_nom · dt / M` ≈ 0.06 K per kelvin of inflow at
60 s), so the passes do not grow (§7).

### 4.3 Arrangements, hot water, the flow model

- **Parallel buffer** (every twin): the law of §4.2 with the buffer's published return. hplib's flow
  (`P_th / (c · 5 K)`) is no longer used for space heating.
- **Series and no storage**: the same law, which the arrangement had in `space_heating_at_fixed_flow` with the lift
  capped at 5 K; the cap goes, so the machine delivers up to its capacity at a cold return instead of 5 K over the
  nominal flow.
- **The start-up ramp** (`fixed_flow_operating_point`: the law's power times `1 − e^(−t / τ)`, `t` the time the
  machine has been heating) stays in every arrangement (D8) with a sourced time constant. What the literature gives:
  Uhlmann and Bertsch's measurement of a 16 kW air/water unit after ten hours off, reported by Baster (2017, PhD
  thesis, Strathclyde, §2.3, doi:10.48730/ndz6-0r38): the electrical power settles within 5 s, the thermal output
  "takes nearly a minute to reach its steady state value", the pressure difference is re-established within 15 s and
  the delay is the thermal inertia of the condenser; Riederer et al. (CLIMA 2000, 1997, arXiv:1212.5665) fitted a
  single time constant of 2 min to their own split unit and cite Murphy (0.32 to 0.47 min, cooling) and O'Neal (more
  than 2 min, cooling), with the power "relatively constant during start-up"; AixLib's `ModularReversible` models the
  same inertia as a critically damped delay of order 3 whose cut-off frequency is calibrated per unit (Wüllhorst et al.
  2021, doi:10.3384/ecp21181561; no published default); field and modelling studies of inverter units report 10 to
  20 min to full output, which is the inverter's frequency ramp, a compressor-protection setting of the manufacturer,
  not thermal inertia (Eze et al. 2026, air/water heat pumps in alpine regions, "up to about 20 min, depending on the
  inverter's steady-state frequency"; an AWHP control study of 2023, doi:10.3390/su15118664, "steady state in less
  than 10 min"). No manufacturer document states a ramp rate, and the EN 14825 Cdh test reports only the cyclic COP.
  The time constant is set to 120 s, the measured thermal inertia of the refrigerant cycle (Riederer's 2 min; Baster's
  "nearly a minute" to the plateau is a 95 % rise, about 20 s of time constant, on a unit twice the twin's size): a
  60 s step delivers 39 % of the law's power in the first minute, 63 % after two, 92 % after five; at 900 s the ramp is
  spent within the first step. The inverter's frequency ramp is not modelled; an owner who wants it states a longer
  constant. The 360 s of today was unsourced and lay between the two effects.
- **The flow model**: a constant nominal flow with a variable lift, in every arrangement, instead of hplib's constant
  5 K lift with a variable flow. Why: outlet control needs a handle on the power at a given return, and at a fixed
  flow that handle is the lift; a pump that held a constant lift would fix the outlet at `T_ret + 5 K` whatever the
  compressor does, so the set point could not be held. Real units with a speed-controlled pump hold a design lift
  only at high load and fall to the pump's minimum speed at part load, where the lift shrinks as modelled here; the
  constant flow is the simpler of the two and the one the series arrangement already has. The flow is sized from the
  rated capacity over the 5 K design lift of the EN 14511 W35 rating condition ("Spreizung 5 K" in the Vitocal datasheet
  of §4.4): 0.372 kg/s for the 7.78 kW twin instead of the plain 0.333 kg/s (D9). A stated value wins. The secondary
  pump's electricity is not modelled today and stays unmodelled (a bead if wanted).
- **Hot water** (D4): a real unit charges its cylinder at high compressor speed and ends the charge on the cylinder's
  sensor; the charge is short and at a high lift. The mode keeps the full-load charge, at hplib's flow and 5 K lift
  until step d (D10), capped at the maximal hot-water supply as today. The 0 W hot-water constant only switched
  hplib's floor off for this mode; with one minimum output per machine the floor applies in every mode, which changes
  nothing while the capacity at the cylinder's return lies above it (it does for the twin at every return hplib
  accepts).
- **Cooling**: unchanged (hplib's EER, whole steps).

### 4.4 The minimum output: 0.25 of the rated capacity for an inverter, 1.0 for an on/off machine (D2)

Datasheets state the modulation range at the EN 14511 points. Against the capacity at A-7/W35, the point HiSim sizes
its machine at (the design load at the heating reference temperature):

| Unit | A-7/W35 capacity, kW | Modulation range at A-7/W35, kW | Minimum share | Source |
|---|---|---|---|---|
| Bosch Compress 7000i AW 5 OR-S | 4.70 | 1.5 to 4.7 | 0.32 | Bosch installation manual 6721830633 (2021/02), technical data, https://www.worcester-bosch.co.uk/support/literature/download/6721830633 |
| Compress 7000i AW 7 OR-S | 5.93 | 1.5 to 5.9 | 0.25 | same |
| Compress 7000i AW 9 OR-S | 6.21 (rated 8.3 at 100 % speed) | 2.0 to 8.3 | 0.24 | same |
| Compress 7000i AW 13 OR-S/T | 11.50 | 4.0 to 11.5 | 0.35 | same |
| Compress 7000i AW 17 OR-T | 13.02 | 4.0 to 13.0 | 0.31 | same |
| Viessmann Vitocal 250-A 251.A10 | 9.7 | 2.6 to 12.0 (stated at A7/W35) | 0.27 | Viessmann datasheet Vitocal 250-A (2023-07), "Leistungsregelung", https://www.loebbeshop.de/media/69018/file/static/pdf/viessmann/datenblatt-vitocal-250-a.pdf |
| Vitocal 250-A 251.A13 | 11.1 | 3.0 to 13.4 | 0.27 | same |
| Vitocal 250-A 251.A08 | 6.5 | 2.1 to 8.0 | 0.32 | same |
| Vitocal 250-A 251.A06 / A04 | 5.6 / 3.8 | 2.1 to 6.0 / 2.1 to 4.0 | 0.38 / 0.55 | same (the smallest sizes share one compressor) |
| Vaillant aroTHERM plus VWL 75/6 A | 7.0 | 3.0 to 7.0 | 0.43 | dealer datasheet summary (condition of the 3.0 kW not stated), https://www.energiefluss24.de/waermepumpe/vaillant-arotherm-plus |

Nine of the ten units lie between 0.24 and 0.43, the median at 0.31; the outliers are the smallest sizes of a range,
which share the next size's compressor. The owner set the default at 0.25 of the rated capacity, the low edge of the
Bosch range (the agent had proposed 0.30, the median), as a sized field (`sized_field(rule=...)`, 0.25 ×
`set_thermal_output_power_in_watt`) with this table as its source: 1945 W for the 7.78 kW twin (the study's 1800 W was
0.23). A request or preset that knows the machine states the watts. An on/off machine has no modulation range:
`P_min` = `P_cap`, and it cycles at every load below its capacity. Rework decision 5 names air/water inverter and
brine/water on/off as the defaults; the Fraunhofer ISE field test found no connection between a unit's speed control and
its number of starts (§4.5), which warns that the controller and hydraulics, not the compressor, decide the cycling in
the field. The `compressor_control` field (enum `INVERTER` / `ON_OFF`, default by source type, as rework step b describes
it) is introduced here; step b reads it for its Cdh.

### 4.5 The minimum running and idle times: 600 s each (D3)

Sources, from manuals and field data (none of the Vaillant or Daikin manuals for the fleet's reference units could be
read behind their bot protection; the Daikin value below is from the installer reference guide of a sister model):

| Source | Running time | Idle time | Note |
|---|---|---|---|
| Stiebel Eltron WPF 04-16 (brine/water, on/off) and WPW 10-22 Trend, installation manuals: "Nach Abschalten einer Wärmepumpe wird eine Stillstandszeit gesetzt, um den Verdichter zu schützen. Die voreingestellte Stillstandszeit von 20 Minuten darf im normalen Betrieb nicht unterschritten werden"; parameter STILLSTANDSZEIT, 1 to 120 min, default 20 min; "Mindestlaufzeit der Wärmepumpe von ca. 10 Minuten" (WPF, commissioning of the brine pump) | about 10 min | 20 min | https://assets.heizung-billiger.de/documents/sima_189.pdf (WPF), https://www.stiebel-eltron.de/toolbox/content/docs/anleitungen/installation/WPW_Trend/326127-41811-9532_WPW_10-22_Trend_de.pdf |
| Daikin Altherma 3 R F (ERLA11 with EBVH16), installer reference guide: field setting [8-01] minimum running timer, 5 to 95 min, "it is NOT allowed to set [8-01] to a value below 10 minutes" | ≥ 10 min | – | search summary of https://manualshive.com/en/manual/daikin/altherma-3-r-f/501204?page=221 (page itself behind bot protection); the EPRA08-12 guide (4PEN634887-1) gives the hot-water timers only: [8-00] minimum running 0 to 20 min, default 1 min, [8-02] anti-recycle 0 to 10 h, at least 0.5 h |
| Bundesverband Wärmepumpe, quoted by 42watt: "Hersteller empfehlen laut Bundesverband Wärmepumpe Laufzeiten von etwa 10 bis 20 Minuten am Stück"; Danfoss: at least 2 min of running for motor cooling and oil return; EA Technology 2012 lab test: at least about 6 min (a 1.6 min run gave a COP 15 % lower) | 10 to 20 min (target), 2 min (hard floor), 6 min (efficiency) | – | https://42watt.de/magazin/taktung-warmepumpe |
| flexiheatuk.com (buffer sizing guide) | 6 min lower bound, 10 to 15 min recommended | – | cited in the buffer-size study |
| Compressor makers, quoted in Fraunhofer ISE 2025 §6.3: RIES at most 6 starts/h, Bitzer 6 or 12 starts/h, Copeland 10 starts/h; "VDI 4645 enthält keine Vorgaben zu Mindestlaufzeiten und Schalthäufigkeiten" | – | – | Fraunhofer ISE, Abschlussbericht "WP-QS im Bestand" (2025), p. 98, https://www.stadtwerke-stuttgart.de/filer/canonical/1778916148/6352/ |
| Trane residential glossary: 3 min minimum run, 5 min minimum off (US on/off units) | 3 min | 5 min | https://www.trane.com/residential/en/resources/glossary/hvac-short-cycling/ |

The running time: 600 s. It is Daikin's lowest allowed setting, Stiebel's commissioning figure and the lower end of the
BWP's target, and well above the 2 to 6 min the compressor needs. The idle time: 600 s. Stiebel's 20 min default is an
on/off brine unit's; the air/water inverter units protect their compressor with restart delays of a few minutes
(Trane 5 min, the consumer guides 5 to 10 min); 600 s is the middle of that range and equal to the running time. The
on/off brine preset, when one exists (hisim-ztt7), takes Stiebel's 1200 s idle time. The compressor makers' 6 to 12
starts per hour are limits, not targets, and are not used.

Field reference for the starts KPI (hisim-heat-pump-starts-plausible-100b), Fraunhofer ISE 2025 §6.3, 51 air/water
units heating and preparing hot water, year 2024: 540 to 15,820 starts a year; 90 % of the units below 5,500; about a
third at most 2,000; units below 2,000 are only the tightly sized ones; the number of starts depends neither on the
speed control (inverter, fixed, two-stage) nor on the specific buffer volume (figure 66), but on the hydraulics (a
hydraulic separator, a heat-pump flow far above the circuit's flow), year-round heating release and the control (a
start/stop on the flow temperature, the hysteresis). A plausible range for a well-controlled twin is therefore a few
hundred to about 2,000 a year, with 5,500 as the upper bound of what the field accepts.

### 4.6 The COP at a modulated operating point

The Carnot-efficiency COP of rework step a (`CarnotEfficiencyCop.cop`, `η · T_out / (max(T_out − T_source, 12 K) + d)`)
depends on the source and the outlet only. Under design C the outlet of a modulating step is the set point, a few
kelvin below today's `T_ret + 5 K` whenever the lift at the needed power is below 5 K; that is the COP gain the
prototype shows (§7), and it is the physics of a smaller lift, not a part-load bonus. No part-load factor is added,
for three reasons: the pooled Keymark fit behind η and d runs over the ten EN 14825 and EN 14511 points, of which A2, A7
and A12 are part-load points of mostly inverter units, so the fit already carries their part-load efficiency on
average; RWTH Aachen's measurements (Römer, Vering, Göbel, Müller 2026, doi:10.1038/s44406-026-00028-6, reported at
https://refindustry.com/news/market-research/rwth-aachen-researchers-call-for-better-heat-pump-performance-data/)
found the EN 14825 method overestimating the COP at two part-load points by 12.7 % and 18.9 %, so a bonus on top of
the fit is not evidenced; and a term in the power share would be a second model of the same machine beside the
rating's.

Cycling losses, without double counting (rework decision 5):

- **Inside the rating** (`StandardizedSeasonalCop`, step b): a bin whose demand lies below the minimum output gets
  `COP · CR / (Cdh · CR + 1 − Cdh)`, Cdh 0.9 for an on/off machine (EN 14825's default where Cdh is not measured, EHPA
  testing regulation for air/water heat pumps v2.4a, https://www.ehpa.org/wp-content/uploads/2022/07/EHPA_TestReg_AW_HP_V2.4a_20210607_.pdf),
  1.0 for an inverter, with `CR` = demand over D2's minimum output. That reproduces what the datasheet's SCOP contains,
  so the calibration factor stops absorbing it.
- **In the simulation**: no Cdh. The machine's own cycles are explicit (this spec at ≤ 600 s; the ratio's bookkeeping
  above, hisim-ef6u), and each start books the start-up loss of step b (`E_start = P_el,nominal · t_start_loss`, derived
  from the Cdh test cycles, rework decision 15). This spec reduces the number of starts (§7); step b prices them.
- **The ramp and step b's loss do not count the same thing twice.** The ramp books less heat in the first minutes of a
  run at proportionally less electricity (the law's power times `1 − e^(−t/τ)`, over the same COP), so it moves heat
  later in the run and changes neither the electricity per kWh of heat nor the run's total: the buffer is lower by
  what the ramp withheld and the law delivers it in the next minutes. It is a capacity delay, not a loss. The
  measured deficit of a start, electricity drawn without heat while the compressor builds its pressure difference
  and the condenser warms (Baster: "additional energy input at start up", partly reclaimed at shutdown; Uhlmann and
  Bertsch: at most 2 % of COP for runs above 15 min), is what the Cdh test cycles contain and what step b books as
  `E_start`. Step b therefore derives `t_start_loss` from the whole Cdh deficit, without subtracting anything for the
  ramp, and the ramp stays a timing effect. Under design C the ramp also lengthens the on phase of a cycle below the
  minimum output, which the controller's cycle estimate of §4.7 ignores (one of the reasons the estimate over-counts
  in §7.3).
- **The rating's own minimum output** is D2's share, so the rating and the simulation cycle at the same load.

The air/water fit of step a has no humidity or defrost term; those are step c and do not interact with the law beyond
the time fraction f, which applies to a modulated step as to a full-load one.

### 4.7 Above the threshold: the part-load mode and hisim-ef6u

Above 600 s the start-of-step buffer and the controller's exact rule stay as decisions 21 to 23 built them. The device
computes its power `P` and outlet from the buffer's start-of-step temperature `T0` by the law of §4.2 and runs the
requested fraction `r` of the step at it: averaged flow `r · m_nom` at the outlet, heat `r · m_nom · c · (T_out − T0)`
(`rod_assisted_heating` takes `r` as today), electricity `r · P / COP(T_source, T_out)`. The rule lands the buffer's end
temperature on the controller's target, which is also the set point (D7). Since the inflow is at the set point, the
buffer cannot end above it; the rule saturates at 1 when the step's draw takes more than a whole step brings, as it does
for the boiler today.

Why the 900 s totals stay near the 60 s ones: the outlet is the set point in both modes whenever the needed power lies
in the modulation range, so the COP is the same; the delivered heat is the house's. What differs is the buffer: at 60 s
it sits at `T_set − L / (m_nom · c)` continuously, at 900 s it ends each step at the set point and starts it the draw
lower, so its mean lies between; for the 389 l buffer at 3 kW the difference is about 1.3 K, for 156 l about 0.1 K. The
prototype measures what that costs (§7, D5).

hisim-ef6u's split becomes bookkeeping on the delivered mean power `P̄ = r · P`: a step whose `P̄` lies at or above the
minimum output is continuous running at the power share `P̄ / P_cap`, no start, running time `dt`; a step with
`P̄ < P_min` cycles, running `tau = P̄ / P_min` of the step at `P_min`. The starts of such a step are the hysteresis
cycle (D6), not a bound from the minimum times: below the minimum output the machine heats the buffer through its band
at `P_min − P̄` and the house drains it at `P̄`, so

    t_on = max(C_buf · ΔT_band / (P_min − P̄), t_run,min),  t_off = max(C_buf · ΔT_band / P̄, t_idle,min),
    starts per step = dt / (t_on + t_off),

0 for a step at or above the minimum output or with no load. Who computes and publishes it:

- **The buffer** hands over its heat capacity `C_buf` at configuration time, not through a port (D6 as amended;
  principle B.1: a component is seen "at configuration time, through the sizing contributions it declares").
  `SimpleHotWaterStorageConfig` declares `FactContribution(facts=("storage_heat_capacity_in_joule_per_kelvin",))`
  computed from its resolved `volume_heating_water_storage_in_liter` times the water density and `c`, as the heat
  pump's config contributes `maximal_thermal_power_in_watt` from its resolved power
  (`MoreAdvancedHeatPumpHPLibConfig.sizing_facts`); the stratified buffer's config declares the same fact when it
  exists (the total water mass, since the band's layers are not known at configuration time). The fact is a new
  `SizingContext` field with its `Size` term (`Size.STORAGE_HEAT_CAPACITY_IN_JOULE_PER_KELVIN`; one term per field,
  `tests/test_sizing.py`), named like the existing `water_mass_flow_rate_in_kg_per_second`; the hot-water cylinder
  does not declare it (nothing reads it there). The controller's config gets an optional sized field,
  `storage_heat_capacity_in_joule_per_kelvin: Sizable[Optional[float]] = sized_field(rule=Size.STORAGE_HEAT_CAPACITY_IN_JOULE_PER_KELVIN)`;
  a stated value wins. Binding (`hisim/config/engine.py`): a bare fact binds if and only if exactly one config in the
  set declares it; in every heat-pump twin exactly one `SimpleHotWaterStorage` exists beside the `SimpleDHWStorage`
  of another class, so the pairing resolves without a `sizing_sources` entry (the engine resolves the buffer's volume
  from the heat pump's power first, then the controller's field, in its fixed-point sweep). A setup with two buffers
  is refused with the paste-ready `sizing_sources` snippet the engine prints, and the author names the buffer the
  controller charges. A setup without a buffer (the series and no-storage arrangements, no recorded twin) has no
  provider, which the engine refuses for an `AUTO` field whatever its optionality; the author states the field
  (`storage_heat_capacity_in_joule_per_kelvin: null`), and with `None` the controller computes no cycle estimate and
  commands 0 starts above the threshold, which is right there: without a store the heat pump runs whole steps and
  counts its own starts, as at and below the threshold.
- **The controller** computes the estimate: it owns the band (its switch points), decides on/off, and reads `P̄`
  (`ThermalOutputPowerSH`) and `P_min` (the heat pump publishes its minimum thermal output as an output beside its
  minimum-load electricity) from the heat pump, and holds `C_buf` from its resolved config. It carries the fractional count in its
  state (`cycle_starts_accumulated`, advanced in `i_save_state` like its other state) and publishes, per step, the
  whole starts the accumulator crosses (`CompressorStartsInStep`, 0 or more), so that a cycle of six hours at 900 s
  gives one start every 24 steps instead of a fraction nothing counts. At and below the threshold it publishes 0: the
  heat pump counts its real starts from its own state there.
- **The heat pump** reads `CompressorStartsInStep` as a command-like input of its controller and adds it to its
  `CounterOnOff` (and to `CounterSwitchToSH`), and the starts KPI (`get_heatpump_cycles`) reads that counter instead of
  the `TimeOff` transitions. Why the device publishes the count and not the controller: the KPI stays one KPI from one
  component's own outputs at every step length (principle B.11), the counter is what a real unit's board shows, and
  the controller tells its device what it switched, as it tells it on/off; the estimate itself stays the controller's
  decision. The published running time grows by `tau · dt` on such a step.

The 60 s count is the reference: at 60 s the controller's own hysteresis produces the cycles step by step. Energies,
flows and outlets do not move with PR 3; the running time, the starts and the brine pump's share do.

### 4.8 The alternatives, and the owner's choice

Three designs were compared for the ≤ 600 s step. The agent recommended A; the owner chose C on 2026-11-26 (D1).

| Design | Mechanism | Pros | Cons |
|---|---|---|---|
| **C, chosen**: outlet control inside the device (§4.2) | the controller sends a set point; the device holds its outlet at it over the nominal flow, `P = clamp(m_nom · c · (T_set − T_ret), P_min, P_cap)` | how an inverter unit's own control works; closed form per pass, no rule iteration, no extra node step; one law for every arrangement (the series arrangement already had it, capped); the outlet falls with the load, so the COP gains the physics of a smaller lift; above 600 s the existing rule applies unchanged | a proportional law: the return settles `L / (m_nom · c)` below the set point, so the mixed buffer feeds the emitters below the set point at high load (5 K at the design load with the set point at supply + 2.5 K: 2.5 K below the curve, D7); replaces hplib's constant-lift flow model, which was the assumption the 900 s mode rested on; the 60 s and 900 s buffers differ by up to about 1.3 K at 50 l/kW (§4.7) |
| A, rejected: the exact rule at every step length with the ratio as a power share | the buffer publishes its end temperature without the heat pump's circuit from a second node step at ≤ 600 s; the controller applies `StartOfStepPartLoadRule.raising_ratio` at every step length; the device floors at `p_min` and runs the whole step at `max(r, p_min)` | one mechanism at every step length; lands the buffer on the controller's target each step, so 60 s and 900 s charge to the same temperature (measured within 1 % in three of four cases, §7); the controller decides the power, the device only its limits | the power is decided in the controller through a rule iterated over the simulator's passes rather than by the machine's own control; one more node step per pass at ≤ 600 s (+3 to 17 % wall time); the ratio's meaning changes (an energy share); hplib's 5 K lift stays, so the outlet at part load is 5 K above the buffer, which no inverter at a fixed pump speed shows; the power follows the draw minute by minute |
| B, rejected: a proportional law in the controller on `T0` | `r = (T_target − T0) / band`, like the boiler's modulating controller | no pass cost, trivially stable | the same offset as C but on the buffer's start temperature and with a gain to source; a second rule beside the exact one; hplib's lift stays |

The owner's reason for C (2026-11-26): the heat pump controls its own outlet temperature inside the device, as the real
machine does; the controller gives it the set point and the on/off decision. A's prototype numbers stay in §7 as the
comparison.

## 5. What changes and what does not

- **Results.** Every run with `MoreAdvancedHeatPumpHPLib` moves: at 60 s through D1 to D3 (fewer starts, lower buffer
  and flow temperatures, a higher SPF, less unmet hot water; §7); at 900 s through D1 (the outlet is the set point
  instead of `T0 + 5 K`, the flow the nominal one, the capacity at the outlet), through D3 where the 3600 s times bound
  (the hot-water chain's waits) and through D2 (the published minimum-load electricity the energy manager reads, 1945 W
  instead of 1800 W for the twin; hplib's floor, rarely). 60 s results are not bit-identical: that is the purpose. The
  part-load rule above 600 s, the boiler, district heating, the collector, the distribution system and the buffer do
  not change.
- **Public interfaces.** No output is renamed. The device's set-point input `SetHeatingTemperatureSH` exists in every
  arrangement and is wired by default from the controller's `TargetTemperatureShInCelsius` (today only the series and
  no-storage arrangements have it, from the heating curve). New outputs: the heat pump's minimum thermal output in W
  and the controller's `CompressorStartsInStep`, which the heat pump reads (D6, PR 3); the starts KPI reads
  `CounterOnOff`. New sizing fact `storage_heat_capacity_in_joule_per_kelvin` (a `SizingContext` field and `Size`
  term), contributed by the buffer's config and read by the controller's new optional sized field of the same name
  (D6 as amended); `describe` lists both. New configuration field `compressor_control`; the fields
  `minimum_thermal_output_power_in_watt` and `massflow_nominal_secondary_side_in_kg_per_s` become sizable (`AUTO` in
  the preset; a stated value still wins); the defaults of `minimum_running_time_in_seconds` and
  `minimum_idle_time_in_seconds` change; the constant `HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT` goes. The energy-system
  schema gains the new field; recorded twins pin none of the fields (checked: no `minimum_*_time_in_seconds`,
  `minimum_thermal_output_power_in_watt` or `massflow_nominal_*` in `energy_systems/` or `hisim/renovisor/`), so no twin
  is re-recorded; the parallel twins gain the default connection, which the recorder adds on the next re-record. The
  starts and running-time KPIs change their meaning at 900 s with PR 3 (an estimate instead of a count of ratio-0
  steps).
- **RenoVisor** (900 s): every heat-pump calculation's space-heating electricity falls with PR 2 by about 10 % in
  winter and 14 % in the shoulder season on the floor-heating twin (§7.1: the outlet at the set point instead of the
  buffer plus 5 K; less on radiators, whose lift at part load stays larger), and moves a little with PR 1 (hot water);
  the SPF and the costs with it; the starts and
  running-time KPIs with PR 3. A heads-up note on renovisorissues per PR (`system_docs/results_and_interfaces.md`
  §5), with the measured deltas. The capability document and the translation map do not change (no new measure, no
  new field a request states).
- **Goldens.** Re-blessed through `golden-update.yml` per PR: the twenty files of `household_heatpump_building_sizer`,
  `household_heatpump_car_building_sizer`, `household_heatpump_solar_thermal_building_sizer` and
  `automatic_default_connections` (five parameter sets each, `golden_references/`); the assemblies with a heat pump
  (`tests/assemblies/`, their own CI job) and `tests/test_dhw_chain_time_resolution.py`, whose heat-pump entry in
  `KNOWN_DIVERGENCES` (the 60 s / 900 s unmet hot water, bead hisim-60i6) is expected to pass and is then removed.
- **Beads touched.** hisim-60i6 and hisim-heat-pump-model-rework-fpqz.7 close with their PRs; hisim-ef6u closes with
  PR 3; hisim-heat-pump-starts-plausible-100b gets the field range of §4.5 and is judged on the measurement of PR 4;
  hisim-heat-pump-model-rework-fpqz.5 (step d) loses two items that D1 and D2 settle here, the minimum output as a share
  of the rated capacity and the pump decision (constant nominal flow, variable lift), and keeps the capacity law, the
  hplib removal and the cooling EER (§9); hisim-component-physics-4g9.61 (RunTimeGuard) is not a prerequisite;
  hisim-agent-rules-h2ib.11 loses the four fields this spec sources; hisim-3y10's point (3) is covered by §4.6;
  hisim-heat-pump-buffer-plan-7e58 reruns the study after PR 4.

## 6. Verification

- **Unit tests of the pure functions** (`tests/test_more_advanced_heat_pump_hplib*.py`,
  `tests/test_heat_pump_flow_booking.py`, `tests/test_backup_rod.py`): the law of §4.2 at its edges, a return at the set
  point (minimum output, outlet above the set point), a return far below it (capacity cap, outlet below the set point,
  the rod only above the compressor's maximal outlet or capacity), inside the range (outlet exactly the set point,
  `P = m_nom · c · (T_set − T_ret)`), an on/off machine (full capacity whenever on); the capacity taken at the actual
  outlet and the cache keyed on it (two lifts at one return give two capacities); the sized fields (0.25 × rated, a
  stated value wins, `ON_OFF` gives the rated power; the flow from the rated capacity over 5 K); the default
  connection of the set point in the parallel arrangement; the rod silent on a cold return within the compressor's
  outlet (D11); the refusals (negative or non-finite times, a share outside (0, 1], a non-positive flow).
- **The state machine**: the minimum-time transitions of `on_off_after_minimum_times` keep their table test at the new
  defaults (10 min on, 10 min off, exactly at the edge).
- **Energy balance**: `tests/test_heat_pump_flow_booking.py` (the heat pump books `m c (T_out − T_in)` of its own
  outputs on every step, at a modulated lift too) and the balance check of every run.
- **Step-length agreement** (modelling guide §3): a test in the manner of `tests/test_dhw_chain_time_resolution.py`
  runs the grouped heat-pump twin for four January weeks at 60, 900 and 3600 s and asserts the space-heating heat
  delivered and the heat pump's space-heating and hot-water electricity within 1 % at 900 s (the tolerance of the
  guide and of rework decision 14); 3600 s is recorded. The prototype's months (§7) say where the cycling range lies
  against that band; PR 4 measures the year and either fixes what lies outside or brings the owner a decision (D5); a
  wider tolerance is not an option (guide §3).
- **Comfort at the design load**: the same test records the lowest room temperature and the degree-hours at 60 s and
  900 s, since under C the mixed buffer feeds the emitters below the set point at high load (D7).
- **The cycle estimate** (PR 3): a unit test of the formula at its edges (a step above the minimum output gives 0; the
  minimum times floor `t_on` and `t_off`; a 6 h cycle at 900 s gives one start every 24 steps through the accumulator;
  the accumulator survives `i_save_state` / `i_restore_state`); the heat pump adds the commanded starts to its counter
  and the KPI reads it. The sizing fact: the buffer's config contributes `storage_heat_capacity_in_joule_per_kelvin`
  equal to its resolved volume times density and `c` (the stratified config too, when it exists); the controller's
  field resolves to it in the grouped heat-pump twin, a stated value wins, two buffers are refused with the engine's
  `sizing_sources` snippet, a setup without a buffer is refused on the `AUTO` field and resolves with a stated `null`,
  with which the controller commands 0 starts; `tests/test_sizing.py`'s registry test covers the new term. PR 4 runs
  the twin's year at 60 s and 900 s and asserts the yearly 900 s estimate within ±25 % of the 60 s count of real
  cycles and records the monthly ratios in its description (D13; §7.3 measured the formula at +18 to +36 % on daily
  means).
- **The ramp**: a unit test of the law times `1 − e^(−t / 120 s)` at `t` = 0, 60, 120 and 600 s, and that a 900 s step
  books the full power.
- **Starts**: the twin's yearly starts at 60 s and 900 s lie in the field range of §4.5 (a few hundred to 2,000, below
  5,500); PR 4 asserts the upper bound.
- **Golden gate** per PR, with the deltas in the PR description.
- **Convergence**: the mean passes per step and the forced steps at 60 s and 900 s are reported per PR (recorded, not
  asserted, owner rule of 2026-10-17); the prototypes show no increase at 60 s (§7).

## 7. Measured results

Two throwaway prototypes of 2026-11-26 on the worktree at bad5fa8c2, never committed; patches and scripts under
`/home/noah/hisim/agent-tmp/hp-modulation/` (`apply_patch.py` for A, `apply_patch_c.py` for C, `run_set.sh`,
`analyse.py`, `results.csv`). Grouped heat-pump twin, variant `metered_directly` (no energy manager), floor heating,
7.78 kW, buffers of 20 and 50 l/kW (155.6 and 389.0 l), January and April 2021, 60 s and 900 s. "Before" is the
unpatched worktree. Starts are the KPI "Number of heat pump starts" (all modes); the mean run is of
`OnOffStateHeatpump != 0`; electricity is the heat pump's space-heating electricity; the buffer temperatures are the
month's mean and maximum of the step mean. Both prototypes set the minimum times to 600 s.

- **Prototype C** (15 lines in `heat_pump.py` plus two values in the energy-system copies): the parallel arrangement
  routed through the fixed-flow law with the set-point input in every arrangement, wired by default from the
  controller's target (supply + raise + 2.5 K), the lift uncapped, hplib's capacity cached per lift; the nominal flow
  0.3723 kg/s (7780.75 W over `c` × 5 K, D9) and the minimum output 1945 W (D2) set in the energy-system copies; the
  start-up ramp kept (the series arrangement's, D8); hot water unchanged (D4).
- **Prototype A** (34 lines): the buffer's exact `T_wo` at ≤ 600 s, the exact rule at every step length, the device's
  floor at `p_min`; the minimum output left at the study's 1800 W.

### 7.1 Prototype C (the chosen design)

| Month, step, l/kW | Starts a month | Mean run, h | Mean power when on, kW | SPF SH | SH electricity, kWh | Rod, kWh | Hot-water el., kWh | Buffer loss, kWh | Delivered heat, kWh | Degree-h > 1 K below | Min room, °C | Mean flow, °C | Buffer mean / max, °C | Passes mean / max | Wall, s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Jan, 60 s, 20, before | 299 | 1.16 | 6.97 | 3.43 | 616.4 | 0.0 | 107.0 | 55.8 | 2059.4 | 9.8 | 16.3 | 36.0 | 34.0 / 62.1 | 8.3 / 12 | 49 |
| Jan, 60 s, 20, **C** | 13 | 55.9 | 3.10 | 3.96 | 533.2 | 15.4 | 108.0 | 46.0 | 2066.8 | 0.0 | 19.8 | 31.9 | 30.1 / 35.0 | 6.4 / 10 | 48 |
| Jan, 900 s, 20, before | 1 | 741 | 3.05 | 3.64 | 582.2 | 0.0 | 109.1 | 50.5 | 2066.8 | 0.0 | 20.0 | 36.5 | 31.9 / 36.0 | – / 28 | 5 |
| Jan, 900 s, 20, **C** | 1 | 741 | 3.05 | 4.02 | 525.6 | 5.4 | 109.1 | 46.8 | 2066.8 | 0.0 | 20.0 | 32.3 | 30.4 / 36.0 | – / 28 | 6 |
| Jan, 60 s, 50, before | 232 | 1.52 | 6.98 | 3.78 | 568.8 | 0.0 | 107.6 | 83.9 | 2066.5 | 0.1 | 18.5 | 32.0 | 29.9 / 44.9 | 6.8 / 9 | 41 |
| Jan, 60 s, 50, **C** | 4 | 183 | 3.12 | 4.03 | 533.5 | 3.2 | 108.0 | 85.1 | 2066.8 | 0.0 | 19.9 | 32.0 | 30.2 / 35.0 | 5.6 / 9 | 43 |
| Jan, 900 s, 50, before | 4 | 185 | 3.12 | 3.61 | 598.4 | 0.0 | 109.1 | 93.7 | 2066.8 | 0.0 | 20.0 | 36.7 | 32.0 / 36.0 | – | 5 |
| Jan, 900 s, 50, **C** | 2 | 370 | 3.10 | 4.04 | 532.4 | 2.5 | 109.1 | 85.4 | 2066.8 | 0.0 | 20.0 | 32.1 | 30.2 / 35.0 | – / 28 | 6 |
| Apr, 60 s, 20, before | 168 | 1.18 | 5.78 | 4.26 | 194.6 | 0.0 | 92.2 | 44.8 | 783.4 | 0.6 | 18.3 | 34.7 | 33.8 / 55.8 | 6.7 / 10 | 41 |
| Apr, 60 s, 20, **C** | 107 | 3.67 | 2.41 | 5.29 | 153.3 | 3.9 | 93.1 | 28.4 | 784.3 | 0.0 | 19.1 | 27.7 | 27.0 / 35.0 | 6.8 / 10 | 42 |
| Apr, 900 s, 20, before | 117 | 5.67 | 1.34 | 4.67 | 174.5 | 0.0 | 93.1 | 31.9 | 784.4 | 0.0 | 19.4 | 32.6 | 28.4 / 35.0 | – | 5 |
| Apr, 900 s, 20, **C** | 117 | 5.67 | 1.34 | 5.44 | 149.7 | 0.2 | 93.1 | 31.3 | 784.3 | 0.0 | 19.3 | 29.3 | 28.2 / 35.0 | – / 23 | 5 |
| Apr, 60 s, 50, before | 140 | 1.41 | 5.85 | 4.98 | 167.3 | 0.0 | 92.3 | 52.9 | 783.5 | 0.0 | 18.7 | 28.1 | 27.1 / 35.0 | 5.3 / 8 | 36 |
| Apr, 60 s, 50, **C** | 51 | 7.84 | 2.41 | 5.17 | 160.8 | 8.2 | 93.5 | 52.6 | 784.5 | 0.0 | 19.3 | 27.8 | 27.1 / 35.0 | 4.9 / 8 | 37 |
| Apr, 900 s, 50, before | 149 | 4.34 | 1.43 | 4.65 | 180.9 | 0.0 | 93.1 | 59.2 | 784.3 | 0.0 | 18.9 | 32.6 | 28.5 / 35.0 | – / 23 | 5 |
| Apr, 900 s, 50, **C** | 147 | 4.40 | 1.42 | 5.42 | 154.7 | 1.1 | 93.1 | 57.4 | 784.3 | 0.0 | 18.9 | 29.2 | 28.1 / 35.0 | – / 23 | 5 |

"SH electricity" is the KPI "Total electrical input energy of SH heat pump" (the compressor's, `ElectricalInputPowerSH`);
"Rod" the KPI of the backup heating rod's electricity, which the energy balance and the meter count on top of it.

What the numbers say:

- **The machine holds its outlet at the set point and follows the load.** Mean power when on 7.0 → 3.1 kW in
  January and 5.8 → 2.4 kW in April at 60 s, the same as the 900 s mode's mean of `r · P` (3.05 to 3.10 kW). The mean
  flow temperature at 60 s falls from 36.0 to 31.9 °C (January, 20 l/kW) and from 34.7 to 27.7 °C (April): the outlet
  is the set point instead of the buffer plus 5 K.
- **Starts.** January 299 → 13 (20 l/kW) and 232 → 4 (50 l/kW); April 168 → 107 and 140 → 51: as with A, the machine
  modulates in January and cycles in long cycles in April (mean run 3.7 and 7.8 h). Projected over a year a few hundred
  to about a thousand starts, inside the field range of §4.5; PR 4 measures the year. The 900 s starts (117 and 147 in
  April) are counts of ratio-0 steps, fixed by PR 3.
- **Comfort and buffer.** No overshoot (35.0 °C instead of 62.1 °C), rooms above 19.8 °C instead of 16.3 °C, no
  degree-hours; the two buffer sizes differ in January by 0.3 kWh of compressor electricity (the larger buffer's extra
  standby loss of 39 kWh is paid by a lower return). The 60 s and 900 s buffers differ by 0.3 K in January (30.1 against
  30.4 °C at 20 l/kW), less than §4.7's estimate, because the 900 s rule saturates at 1 on most steps (mean ratio 0.98).
- **The SPF rises at both step lengths**: January 3.43 → 3.96 at 60 s and 3.64 → 4.02 at 900 s (20 l/kW); April
  4.26 → 5.29 and 4.67 → 5.44. The 900 s space-heating electricity falls by 9.7 % (January) and 14 % (April) against
  today: the heads-up for RenoVisor (§5). That is the smaller lift at the outlet (§4.6), not a part-load bonus.
- **60 s against 900 s.** Compressor electricity at 60 s relative to 900 s, before → C: January 20 l/kW +5.9 % → +1.5 %;
  January 50 l/kW −4.9 % → +0.2 %; April 20 l/kW +11.5 % → +2.4 %; April 50 l/kW −7.5 % → +3.9 %. Compressor and rod
  together: +3.3 %, +0.3 %, +4.9 % and +8.5 %. With the rod's heat moved to the compressor at a COP of 4 (what the
  compressor would have drawn for it): +1.9 %, +0.2 %, +3.0 % and +5.0 %. Delivered heat agrees to 0.1 % (the heat pump's
  own space-heating heat to 0.8 %), hot-water electricity within 1 %. So C's two step lengths agree in January at
  50 l/kW and lie 2 to 5 % apart in the cycling range, outside the guide's 1 %; A's agreed within 1 % in three of four
  cases. Candidates for the gap, to be separated in PR 4 (D5): the rod's firing (next point); the start-up ramp, which
  the prototype kept and which holds the power below the minimum output for the first minutes of each of the 107 and
  51 April starts; the hplib capacity and COP cached per 0.1 K of lift in the prototype; and the buffer held
  differently in the two modes (§4.7).
- **The rod fires where it should not.** Under the prototype's law the request `m_nom · c · (T_set − T_ret)` is a
  control target, and `BackupHeatingRod.heating`'s case (1) reads it as a demand: on a cold return after a hot-water
  charge or a restart the request exceeds the capacity, and the rod fills the gap up to its 9 kW: 15.4 kWh in January
  at 60 s (2.9 % of the compressor's electricity) against 5.4 kWh at 900 s, where fewer steps start cold, and 8.2 against
  1.1 kWh in April at 50 l/kW. A real unit's backup is released by its controller (a bivalence temperature or a
  shortfall timer), not by a cold return after a hot-water charge. The rod's trigger under C is D11.
- **Cost.** The 60 s months took 2 % less to 4 % more wall time, with fewer passes per step (6.4 against 8.3 in
  January at 20 l/kW) and the largest pass count unchanged or lower; the 900 s runs took the same.

Against prototype A (§7.2): C starts about as often (13 against 15, 4 against 5 in January), holds a cooler buffer
(30.1 against 31.9 °C) and a cooler outlet (31.9 against 36.5 °C), and so uses 9 % less compressor electricity in January
(533 against 587 kWh at 20 l/kW) and 11 % less in April; A's 60 s / 900 s agreement was tighter (within 1 % against 1.5
to 3.9 %), since A kept hplib's lift and the same charge target at both step lengths and its rod never fired.

### 7.2 Prototype A (rejected), for comparison

| Month, step, l/kW | Starts a month | Mean run, h | Mean power when on, kW | SPF SH | SH electricity, kWh | Hot-water el., kWh | Buffer loss, kWh | Delivered heat, kWh | Degree-h > 1 K below | Min room, °C | Mean flow, °C | Buffer mean / max, °C | Passes mean / max | Wall, s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Jan, 60 s, 20, before | 299 | 1.16 | 6.97 | 3.43 | 616.4 | 107.0 | 55.8 | 2059.4 | 9.8 | 16.3 | 36.0 | 34.0 / 62.1 | 8.3 / 12 | 49 |
| Jan, 60 s, 20, A | 15 | 48.3 | 3.11 | 3.61 | 586.7 | 108.0 | 50.6 | 2066.8 | 0.0 | 19.9 | 36.5 | 31.9 / 36.0 | 7.5 / 14 | 54 |
| Jan, 900 s, 20 (before = A) | 1 | 741 | 3.05 | 3.64 | 582.2 | 109.1 | 50.5 | 2066.8 | 0.0 | 20.0 | 36.5 | 31.9 / 36.0 | – / 28 | 5 |
| Jan, 60 s, 50, before | 232 | 1.52 | 6.98 | 3.78 | 568.8 | 107.6 | 83.9 | 2066.5 | 0.1 | 18.5 | 32.0 | 29.9 / 44.9 | 6.8 / 9 | 41 |
| Jan, 60 s, 50, A | 5 | 146 | 3.15 | 3.60 | 600.5 | 108.0 | 93.9 | 2066.8 | 0.0 | 19.9 | 36.7 | 32.1 / 36.0 | 6.5 / 10 | 48 |
| Jan, 900 s, 50 (before = A) | 4 | 185 | 3.12 | 3.61 | 598.4 | 109.1 | 93.7 | 2066.8 | 0.0 | 20.0 | 36.7 | 32.0 / 36.0 | – | 5 |
| Apr, 60 s, 20, before | 168 | 1.18 | 5.78 | 4.26 | 194.6 | 92.2 | 44.8 | 783.4 | 0.6 | 18.3 | 34.7 | 33.8 / 55.8 | 6.7 / 10 | 41 |
| Apr, 60 s, 20, A | 109 | 3.5 | 2.48 | 4.69 | 172.9 | 93.2 | 29.3 | 784.4 | 0.0 | 19.2 | 29.6 | 27.3 / 35.0 | 5.8 / 12 | 42 |
| Apr, 900 s, 20 (before = A) | 117 | 5.67 | 1.34 | 4.67 | 174.5 | 93.1 | 31.9 | 784.4 | 0.0 | 19.4 | 32.6 | 28.4 / 35.0 | – | 5 |
| Apr, 60 s, 50, before | 140 | 1.41 | 5.85 | 4.98 | 167.3 | 92.3 | 52.9 | 783.5 | 0.0 | 18.7 | 28.1 | 27.1 / 35.0 | 5.3 / 8 | 36 |
| Apr, 60 s, 50, A | 57 | 6.76 | 2.52 | 4.69 | 177.7 | 93.3 | 54.8 | 784.6 | 0.0 | 19.5 | 29.8 | 27.5 / 35.0 | 4.3 / 9 | 39 |
| Apr, 900 s, 50 (before = A) | 149 | 4.34 | 1.43 | 4.65 | 180.9 | 93.1 | 59.2 | 784.3 | 0.0 | 18.9 | 32.6 | 28.5 / 35.0 | – / 23 | 5 |

### 7.3 The hysteresis-cycle formula of D6 against the prototypes' 60 s cycles

Computed on the existing 60 s results (`raw/*.columns.csv.gz`) with daily means, since the formula is meant for a
step: per day, `P̄` is the day's mean space-heating heat of the heat pump, the day is a cycling day where `0 < P̄ <
P_min`, `C_buf` is the buffer's water mass times `c`, `ΔT_band` 10 K (the default band; the daily swing of the buffer's
step mean, 9 to 10 K, gives the same numbers), the minimum times 600 s, and the day's starts `86400 / (t_on + t_off)`;
"actual" are the space-heating starts (`OnOffStateHeatpump` 0 → 1) on those days. January has one cycling day per
case (its starts are hot-water switches) and is left out.

| Prototype, month, l/kW | `P_min`, W | Cycling days | Actual starts on them | Formula | Formula / actual |
|---|---|---|---|---|---|
| A, April, 20 | 1800 | 24 | 79 | 93.5 | 1.18 |
| A, April, 50 | 1800 | 21 | 31 | 36.6 | 1.18 |
| C, April, 20 | 1945 | 26 | 83 | 113.2 | 1.36 |
| C, April, 50 | 1945 | 25 | 34 | 43.3 | 1.27 |

The formula follows the buffer size (a 2.5 times larger buffer gives 2.5 times fewer starts, as measured) and the
order of magnitude, and over-counts by 18 % (A) to 27 to 36 % (C). Known reasons: the daily mean `P̄` blurs a load
that varies over the day, and `dt / (t_on + t_off)` is convex in `P̄`, so the mean of the estimate over the day exceeds
the estimate at the mean; under C the on phase is longer than `C_buf · ΔT_band / (P_min − P̄)` because the law runs
above the minimum output while the buffer lies below the set point and the ramp holds the power down after each
start; hot-water charges interrupt and extend cycles. A per-step evaluation in the controller sees the load of each
step and will lie nearer the 60 s count; PR 4 measures it over the year against D13's ±25 % (§6).

Prototype A, in words: the 60 s machine followed the load (mean power when on 7.0 → 3.1 kW in January); starts fell
from 299 to 15 and 232 to 5 in January, 168 to 109 and 140 to 57 in April; the 20 l/kW buffer no longer overshot
(36.0 °C instead of 62.1 °C) and the rooms stayed above 19.9 °C; the 60 s space-heating electricity against 900 s went
from +5.9 / −4.9 / +11.5 / −7.5 % (January 20, January 50, April 20, April 50 l/kW) to −0.8 / +0.4 / −0.9 / −1.8 %; the
SPF at 50 l/kW fell at 60 s (3.78 → 3.60) because the old on/off cycle had coasted the large buffer to 25 °C and
charged at a cooler mean, where A held it at the target like 900 s; A's 900 s runs were bit-identical to before.

## 8. Open questions

None. Q1 to Q13 of the earlier drafts are decided (D1 to D13, §2); the measurements PR 4 brings back (the full-year
60 s / 900 s agreement, the set-point alternatives, the design-day comfort with the rod on its outlet limit) are
decided on their numbers in that PR.

## 9. Beads and pull requests

Epic hisim-heat-pump-model-rework-fpqz.

| Bead | Content | PR |
|---|---|---|
| hisim-60i6 (P1) | PR 1: the sourced minimum times, 600 s and 600 s (D3), with the sources of §4.5 in the field comments; the two unit tests of the edges; `KNOWN_DIVERGENCES` of the hot-water chain test re-measured; goldens re-blessed; heads-up on renovisorissues | – |
| hisim-heat-pump-model-rework-fpqz.7 (P1) | PR 2, design C (D1): the set-point input in every arrangement, wired by default from the controller's target (D7); the parallel arrangement routed through the outlet-control law with the lift uncapped; the capacity at the actual outlet and the cache keyed on it; the rod on its outlet limit only (D11); the ramp kept with its 120 s time constant and sources (D8); `compressor_control` and the sized minimum output (0.25, D2, sources of §4.4); the sized nominal flow (D9); the hot-water constant removed (D4), hot water on hplib's flow (D10); the tests of §6; goldens; heads-up. The smallest correct first PR; it needs neither step b nor step d, since the capacity cap takes hplib's `P_th` at the outlet until step d replaces it | – |
| hisim-ef6u (P3 → P2) | PR 3: the bookkeeping above 600 s on the delivered mean power (continuous running at or above the minimum output, the cycling estimate of D6 below it): the sizing fact `storage_heat_capacity_in_joule_per_kelvin` (context field, `Size` term, the buffer's contribution, the controller's optional sized field), the heat pump's minimum-output output, the controller's accumulator and `CompressorStartsInStep`, the heat pump's counter and the KPI on it, the published running time; its tests (§6); the 900 s goldens' KPI columns; heads-up | – |
| hisim-ef6u, hisim-heat-pump-starts-plausible-100b (P1) | PR 4: the step-length test of §6 (60 / 900 / 3600 s, four weeks, 1 %, D5), the yearly starts against the field range and the 900 s estimate within ±25 % of the 60 s count with the monthly ratios (D13), and the set-point measurement of D7 (supply + 2.5 K against supply + 5 K and the curve, at both step lengths, with comfort at the design day); whatever the measurement shows is fixed in it or brought to the owner | – |
| hisim-heat-pump-buffer-plan-7e58 (P2) | the buffer-size study rerun at 20 / 35 / 50 l/kW, full year, 60 s and 900 s, after PR 4 | – |
| hisim-heat-pump-model-rework-fpqz.5 (P1) | step d, scope reduced: the capacity law `P_cap(T_source, T_out)` relative to the rated capacity, the hplib removal (the result cache and the 0.1 K grid with it), the cooling EER, and the hot-water charge on the nominal flow (D10); the minimum output share and the pump decision are D2 and D1 of this spec | – |
| hisim-heat-pump-model-rework-fpqz.4 (P1) | step b: Cdh in the rating with D2's minimum output; the start-up loss per start derived from the whole Cdh deficit, beside the ramp (§4.6); reads `compressor_control` | – |
| hisim-heat-pump-model-rework-fpqz.8 (P3, new): **the space-heating controller releases the backup rod for a house whose load exceeds the compressor's capacity** | Under design C the rod runs only above the compressor's maximal outlet (D11); a house whose heat load exceeds the capacity on a cold day, an undersized machine or a day colder than the design point, is under-supplied, and the room temperature shows it. Wanted: the L1 space-heating controller publishes a release signal (`BackupRodReleased`, 0 or 1) when the buffer's start-of-step temperature has stayed below the switch-on point for longer than the heat pump's minimum running time while the heat pump heats (the compressor at full output cannot keep up); the heat pump reads it and, while released, lets the rod add the shortfall between the law's request and the compressor's capacity up to its rated power (case (1) of rework decision 8, re-stated as a controller decision); the release ends when the buffer passes the switch-on point. The timer is state of the controller, saved and restored with it. Tests: the release fires only after the minimum running time below the switch-on point, never on a cold return after a hot-water charge, and the rod's heat and electricity are booked on their ports. Judged on PR 4's design-day comfort measurement: filed as P2 if the rooms fall below the set point on the design day at 60 s or 900 s with the rod on its outlet limit only, P3 otherwise | – |
| hisim-component-physics-4g9.61 (P3) | RunTimeGuard; not a prerequisite | – |
| hisim-agent-rules-h2ib.11 (P3) | the five values sourced here (the two times, the minimum output, the nominal flow, the ramp's time constant) leave its list | – |

Each PR is small, stacked on the one before, opened as soon as it is ready, with its goldens re-blessed on current
main and a Problem / What was done / Result description; the tests per PR are listed in §6.
