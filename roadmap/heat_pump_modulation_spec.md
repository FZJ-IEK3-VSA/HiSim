# Spec: the hplib heat pump follows the load at every step length, down to a sourced minimum output, with sourced minimum running and idle times

Status: draft, measured on a throwaway prototype (§7), nothing built; the proposed decisions of §2 and the questions
of §8 wait for the owner. Owner: Noah Pflugradt. Epic: hisim-heat-pump-model-rework-fpqz (beads
hisim-heat-pump-model-rework-fpqz.7 and hisim-60i6, both P1; the alignment above 600 s is hisim-ef6u). Builds on:
`roadmap/hydronic_part_load_spec.md` (decisions 11, 16, 21 to 23), `roadmap/two_mode_buffer_spec.md` (§1.1, Q2),
`roadmap/heat_pump_model_rework_spec.md` (decisions 5 and 15, steps b and d), `hisim/part_load.py`, the backup rod of
#1113 and the buffer-size study of 2026-11-26 (`/home/noah/hisim/agent-tmp/buffer-sizing-review/REPORT.md`, bead
hisim-heat-pump-buffer-plan-7e58).

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
  temperature, at most 5 K, over the nominal flow sets the power; that is a modulation by the lift, capped at 5 K, with
  an unsourced start-up ramp (`START_UP_TIME_CONSTANT_IN_SECONDS`, 360 s).
- Above 600 s the ratio acts, but as a run-time fraction at full load: `r · P_th` over the step, published as `r · m` at
  the full-load outlet. hisim-ef6u asks for the split into power and time there.
- The machine keeps running or resting for `minimum_running_time_in_seconds` = `minimum_idle_time_in_seconds` =
  3600 s (`MoreAdvancedHeatPumpHPLibConfig`), ten to twenty times the anti-short-cycle times of real compressors (§4.5).

Measured consequence (buffer-size study, grouped heat-pump twin, 7.78 kW floor-heating house, full year 2021, no energy
manager): at 60 s the machine runs 6.6 kW for at least an hour, lifts a 20 l/kW buffer to 62 °C, then rests an hour while
the buffer runs empty; SPF 3.69 at 20 l/kW against 4.17 at 50 l/kW, rooms down to 16.3 °C, 1980 starts a year; at 900 s
the part-load ratio modulates (mean 0.27 to 0.30 of each step) and the size makes no difference to the SPF (3.97 to
4.02). January alone (§7, before the prototype): 299 starts a month at 60 s with a 20 l/kW buffer against 1 at 900 s,
and 616 kWh of space-heating electricity against 582 kWh (5.9 % more). The 60 s reference therefore penalises small buffers,
inflates starts and flow temperatures, and the >600 s mode is checked against a reference that does not modulate. The
hot-water chain shows the same effect (bead hisim-60i6: 3.63 kWh of unmet hot water at 60 s against 1.04 kWh at 900 s
in four January weeks, because the 3600 s idle time holds the machine off while the evening draws come).

A real inverter heat pump lowers its compressor speed to match the load down to a minimum output, typically a quarter to
two fifths of its capacity (§4.4), and cycles only below it; its compressor protection keeps it on for about ten minutes
and off for about ten to twenty minutes (§4.5).

## 2. Decisions

Proposed; each becomes `D<n> (owner, date)` when the owner takes it (§8 holds the choices).

**D1 (proposed) — One meaning of the part-load ratio at every step length: the share of a full-load step's energy the
charge delivers.** The device splits it into a power share and a run-time fraction by its own minimum output
(§4.1): `p = max(r, p_min)`, `tau = r / p`. At and below the threshold a component cannot run part of a step (D1 of
`roadmap/stratified_water_storage_spec.md`: no sub-stepping), so `tau` is 1 there and the machine runs the whole step at
`max(r, p_min)`; above the threshold `tau` may be below 1 (hisim-ef6u). Consequence: the controller's rule, the device's
booking (`r · m` at the full-load outlet, `r · P_th` of heat) and the buffer's integration are the same code at every
step length; only the device's split differs.

**D2 (proposed) — The controller sets the ratio at every step length with the exact start-of-step rule it uses above
600 s** (`StartOfStepPartLoadRule.raising_ratio` against the buffer's end temperature and its end temperature without
the heat pump's circuit), and the buffer publishes that "without" temperature exactly in its iterated mode too
(amending Q2 of the two-mode buffer spec from (b) to (a) for the primary generator's circuit). Consequence: at 60 s the
buffer ends each step at the controller's target (supply + raise + 2.5 K) as long as the load lies between the minimum
output and the capacity; the on/off hysteresis stays as it is and ends a run only when the load falls below the
minimum output (§4.2).

**D3 (proposed) — The minimum output is a share of the rated capacity**, a sized field whose law gives 0.30 of
`set_thermal_output_power_in_watt` for an inverter machine and 1.0 for an on/off machine (§4.4), decided by the
`compressor_control` field of rework decision 5 (default air/water inverter, brine/water on/off), which this spec
introduces ahead of rework step b. Consequence: `minimum_thermal_output_power_in_watt` keeps its name and unit, becomes
`AUTO` in the preset (2334 W for the 7.78 kW twin instead of 1800 W), and the hot-water constant
`HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT` (0) goes: one minimum output per machine, in every mode.

**D4 (proposed) — The minimum running time and the minimum idle time default to 600 s each** (§4.5). Consequence: every
heat-pump golden at 60 s moves; at 900 s a step already exceeds both times, so only the ratio-0 bookkeeping of
`published_mode` and the hot-water chain's waits change.

**D5 (proposed) — Hot water charges at full load, unmodulated**, as today; the series and no-storage arrangement keeps
its lift law, floored at the same minimum output (§4.3). Consequence: no change to the hot-water controller; the
parked hot-water part load (#916) stays parked.

**D6 (proposed) — The COP of a modulated step is the Carnot-efficiency COP at the step's source and outlet temperatures,
with no part-load term** (§4.6). Cycling losses stay where rework decision 5 put them: the EN 14825 Cdh inside the
rating below the minimum output, and an explicit start-up loss per start (step b, child .4). Consequence: this spec
changes how often the machine starts, step b what a start costs, and the rating's Cdh what the datasheet's SCOP already
contains; nothing is counted twice.

## 3. Terms

- **Part-load ratio `r`**: the share of a full-load step's energy the charge delivers, 0 to 1, dimensionless
  (`Units.FRACTION`); requested by the controller (`RequestedPartLoadRatioSh`), reported by the device
  (`ActualPartLoadRatioSh`). The same quantity as today's ratio; D1 fixes its reading.
- **Full load**: hplib's capacity `P_th` at the step's source, ambient and return temperatures at the nominal 5 K lift,
  and the flow `m_full = P_th / (c · 5 K)` (`CarnotCopHeatPump.simulate`), in W and kg/s.
- **Power share `p`**: the compressor's thermal power over the full load while it runs, `p_min ≤ p ≤ 1`.
- **Run-time fraction `tau`**: the share of the step the compressor runs, `r = p · tau`.
- **Minimum output share `p_min`**: `minimum_thermal_output_power_in_watt / set_thermal_output_power_in_watt`, the
  lowest power share an inverter holds before it has to cycle; 1 for an on/off machine.
- **Modulation range**: the powers from `p_min · P_th` to `P_th`, where the machine runs continuously.
- **Minimum running time, minimum idle time**: the shortest run and the shortest rest the machine enforces after a
  start and a stop, in s; device protection, enforced by the physics component (`component_design_principles.md` §A).
- **Start**: a step in which the machine runs after a step in which it did not (`get_heatpump_cycles`, counted in
  `TimeOff`).
- **Target**: the buffer temperature the controller ends a charge at, `TargetTemperatureShInCelsius`: the heating
  curve's supply temperature plus the energy manager's raise plus half the hysteresis band's upper offset (2.5 K with the
  default 5 K band; owner, 2026-10-19).
- **Switch-on point, switch-off point**: supply − 5 K (never below the room set point) and supply + 5 K, with the raise
  (`heating_switch_temperatures_in_celsius`).

## 4. Design

### 4.1 Who does what

| Part | Kind | Today | After this spec |
|---|---|---|---|
| `SimpleHotWaterStorage` (buffer) | physics | iterated mixed node at ≤ 600 s, publishing its end temperature on all three "without a circuit" outputs | at ≤ 600 s also computes the node step without the primary generator's inflow and publishes its end temperature on `WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius`; the two other "without" outputs keep the end temperature until a reader appears (YAGNI) |
| `MoreAdvancedHeatPumpHPLibControllerSpaceHeating` | L1 controller | on/off hysteresis on the buffer's start temperature; ratio 1 at ≤ 600 s, exact rule above | the same hysteresis; the exact rule at every step length (`StartOfStepPartLoadControl.runs_fractions` no longer reads the threshold; the part keeps the threshold only for `publish_held`'s "at and below it keep the last unforced value" rule, which goes with it); PV mode unchanged (`pv_mode_part_load_ratio` already turns the energy manager's power target into a ratio with the full load) |
| `MoreAdvancedHeatPumpHPLib` | physics | runs the requested ratio as a run-time fraction above 600 s, 1 at ≤ 600 s | splits the ratio by D1: at ≤ 600 s runs the whole step at `max(r, p_min)`, also while its minimum running time keeps it on against the controller (then at `p_min`, not at full load as today); above 600 s `p = max(r, p_min)`, `tau = r / p` (hisim-ef6u) |
| `MoreAdvancedHeatPumpHPLibConfig` | config | `minimum_thermal_output_power_in_watt = 1800`, times 3600 s | the minimum output a sized field (D3), `compressor_control`, times 600 s (D4), every default with its source |

Nothing crosses a port that is not a port quantity: the buffer publishes temperatures, the controller a ratio and a
target, the device a ratio, powers, a flow and an outlet. No component reads another's heat capacity, and nothing
iterates inside a component: the rule is iterated by the simulator's passes, as the part-load rule is today.

### 4.2 The step at and below the threshold (the 60 s reference)

Every pass of a step, in the kind order (controller → heat pump → buffer):

1. The controller decides heating or off on the buffer's start temperature `T0` (unchanged). While heating it reads
   the buffer's end temperature `T_end` and its end temperature without the heat pump `T_wo` of the pass before, and
   the actual ratio `r_actual`, and requests `r = r_actual · (T_aim − T_wo) / (T_end − T_wo)`, clamped to [0, 1], with
   the rule's explicit cases (`StartOfStepPartLoadRule.raising_ratio`: 0 when the buffer ends at or above the target
   without the heat pump, the actual ratio inside the 0.05 K band, 1 when the heat pump brought nothing).
2. The heat pump, heating, runs `p = max(r, p_min)` for the whole step: it publishes `p · m_full` at the full-load
   outlet `T_ret + 5 K` and books `p · P_th`; `P_el = p · P_th / COP(T_source, T_ret + 5 K)`; it reports `p` as the actual
   ratio. The rod runs only where the request exceeds the compressor's capacity or outlet (`rod_assisted_heating`,
   unchanged). A heat pump its minimum running time keeps on against the controller runs at `p_min`.
3. The buffer integrates the inflows exactly (`MixedNode.step`) and publishes `T0`, the step mean to its circuits,
   `T_end` and `T_wo`.

At 60 s the iterated node's end temperature is almost linear in `p` (the node's own change within a 60 s step is about
5 % of its 1170 s time constant for the 389 l buffer at 0.333 kg/s), so the rule lands within the band in two or three
passes; the prototype needed fewer passes per step than the unpatched run (January, 20 l/kW: 7.5 against 8.3 on
average, the largest 14 against 12; §7), since a buffer held at its target moves less within a step. The modulation
floor at `p_min` makes a request below it a whole step at `p_min`: the buffer then rises past the target until the
hysteresis's switch-off point ends the run, and the machine rests until the switch-on point, protected by the minimum
times. That is the explicit cycling of the fine model; its cycles are long (a 10 K band on 389 l holds 4.5 kWh, two and
a half hours at 1.8 kW with no load) and well inside the minimum times.

A ratio of 0 while the controller holds heating cannot happen at ≤ 600 s: the device floors it at `p_min`, so
`published_mode` sees a running machine and the start counters count real starts only.

### 4.3 The other arrangements and the hot-water mode

- **Series and no storage** (`space_heating_at_fixed_flow`): the device already modulates by the lift to its set
  temperature at the nominal flow, `P = m_nom · c · min(T_set − T_ret, 5 K)`, floored at the minimum output; no store
  is wired to the controller, so the ratio stays 1 at every step length ("a device feeding a consumer without a store
  in between runs whole steps"). This spec changes only the floor (D3) and leaves the 5 K cap and the start-up ramp
  as they are; the ramp's time constant is unsourced and belongs with the start-up loss of rework step b
  (hisim-heat-pump-model-rework-fpqz.4). No recorded twin uses these arrangements.
- **Hot water** (`hot_water_at_hplib_flow`): a real unit charges its cylinder at high compressor speed and ends the
  charge on the cylinder's sensor; the charge is short and at a high lift, where modulating down would only lengthen
  it. The mode keeps the full-load charge (D5). The constant `HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT` (0 W) only
  switched hplib's floor off for this mode; with one minimum output per machine the floor applies in every mode, which
  changes nothing while the capacity at the cylinder's return lies above it (it does for the 7.78 kW twin at every
  return hplib accepts). The hot-water part load above 600 s (#916, parked) is not touched.
- **Cooling**: unchanged (hplib's EER, whole steps).

### 4.4 The minimum output: 0.30 of the rated capacity for an inverter, 1.0 for an on/off machine

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
which share the next size's compressor. The default is therefore `p_min` = 0.30 of the rated capacity, as a sized field
(`sized_field(rule=...)`, 0.30 × `set_thermal_output_power_in_watt`), with this table as its source. The study's 1800 W
on 7.78 kW (0.23) lies at the low edge. A request or preset that knows the machine states the watts. An on/off machine
has no modulation range: `p_min` = 1, and it cycles at every load below its capacity, as the part-load mode already
does above 600 s. Rework decision 5 names air/water inverter and brine/water on/off as the defaults; the Fraunhofer ISE
field test found no connection between a unit's speed control and its number of starts (§4.5), which says nothing
against the default but warns that the controller and hydraulics, not the compressor, decide the cycling in the field.

The `compressor_control` field (enum `INVERTER` / `ON_OFF`, default by source type, as rework step b describes it)
is introduced here, since the minimum output depends on it; step b reads the same field for its Cdh.

### 4.5 The minimum running and idle times: 600 s each

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
(Trane 5 min, the consumer guides 5 to 10 min); 600 s is the middle of that range and equal to the running time, which
keeps one number for both. An on/off brine preset, when one exists (hisim-ztt7), takes Stiebel's 1200 s idle time. The
compressor makers' 6 to 12 starts per hour are limits, not targets, and are not used.

Field reference for the starts KPI (hisim-heat-pump-starts-plausible-100b), Fraunhofer ISE 2025 §6.3, 51 air/water
units heating and preparing hot water, year 2024: 540 to 15,820 starts a year; 90 % of the units below 5,500; about a
third at most 2,000; units below 2,000 are only the tightly sized ones; the number of starts does not depend on the
speed control (inverter, fixed, two-stage) nor on the specific buffer volume (figure 66: no connection), but on the
hydraulics (a hydraulic separator, a heat-pump flow far above the circuit's flow), year-round heating release and the
control (a start/stop on the flow temperature, the hysteresis). A plausible range for a well-controlled twin is
therefore a few hundred to about 2,000 a year, with 5,500 as the upper bound of what the field accepts.

### 4.6 The COP at a modulated operating point

The Carnot-efficiency COP of rework step a (`CarnotEfficiencyCop.cop`, `η · T_out / (max(T_out − T_source, 12 K) + d)`)
depends on the source and the outlet only, and the outlet is `T_ret + 5 K` at every power share (hplib holds the lift
and scales the flow). A modulated step therefore has the COP of the full-load step at the same temperatures, and its
electricity is `p · P_th / COP`. No part-load factor is added, for three reasons: the pooled Keymark fit behind η and d
runs over the ten EN 14825 and EN 14511 points, of which A2, A7 and A12 are part-load points of mostly inverter units,
so the fit already carries their part-load efficiency on average; RWTH Aachen's measurements (Römer, Vering, Göbel,
Müller 2026, doi:10.1038/s44406-026-00028-6, reported at https://refindustry.com/news/market-research/rwth-aachen-researchers-call-for-better-heat-pump-performance-data/)
found the EN 14825 method overestimating the COP at two part-load points by 12.7 % and 18.9 %, so a part-load bonus on
top of the fit is not evidenced; and a term that depends on the power share would be a second model of the same
machine beside the rating's. A buffer that sits nearer its target at 60 s than today lowers the outlet, and that is the
whole COP gain the prototype shows (§7).

Cycling losses, without double counting (rework decision 5):

- **Inside the rating** (`StandardizedSeasonalCop`, step b): a bin whose demand lies below the minimum output gets
  `COP · CR / (Cdh · CR + 1 − Cdh)`, Cdh 0.9 for an on/off machine (EN 14825's default where Cdh is not measured, EHPA
  testing regulation for air/water heat pumps v2.4a, https://www.ehpa.org/wp-content/uploads/2022/07/EHPA_TestReg_AW_HP_V2.4a_20210607_.pdf),
  1.0 for an inverter, with `CR` = demand over the minimum output of D3. That reproduces what the datasheet's SCOP
  contains, so the calibration factor stops absorbing it.
- **In the simulation**: no Cdh. The machine's own cycles are explicit (this spec at ≤ 600 s; the ratio's split above,
  hisim-ef6u), and each start books the start-up loss of step b (`E_start = P_el,nominal · t_start_loss`, derived
  from the Cdh test cycles, rework decision 15). This spec reduces the number of starts (§7); step b prices them.
- **The rating's own minimum output** is D3's share, so the rating and the simulation cycle at the same load.

The air/water fit of step a has no humidity or defrost term; those are step c and do not interact with the modulation
beyond the time fraction f, which applies to a modulated step as to a full-load one.

### 4.7 Above the threshold: the split of hisim-ef6u

Above 600 s the exact rule and the start-of-step buffer stay as decisions 21 to 23 built them, and the device applies
D1's split: it runs `p = max(r, p_min)` for `tau = r / p` of the step. The booked energy, the averaged flow `r · m_full`,
the outlet and the buffer's integration do not change, and neither does the electricity, since the COP depends on
temperatures only; what changes is the bookkeeping the KPIs read:

- the published running time grows by `tau · dt`, so a modulating machine at `r ≥ p_min` runs the whole step and a step
  at `0 < r < p_min` runs part of it;
- a step at `r ≥ p_min` is continuous running and no start; in the cycling range the starts per step follow from the
  minimum times, `dt / max(t_run,min / tau, t_idle,min / (1 − tau))`, which makes the starts KPI at 900 s an estimate
  of the 60 s count instead of today's count of steps with ratio 0 (today's 900 s "starts" of the study are the artefact
  the report names: a larger buffer more often needs no charge in a step, each such step ends a "run");
- `published_mode` keeps its ratio-0 rule for a step that runs nothing.

This is PR 3 (§9). It is bookkeeping, so the 900 s energies and goldens of PRs 1 and 2 do not move again; the running
time and starts KPIs do.

### 4.8 Why not the alternatives

Three designs were compared for the ≤ 600 s step:

| Design | Mechanism | Pros | Cons |
|---|---|---|---|
| **A, recommended**: the exact rule at every step length, the ratio as a power share (§4.2) | the buffer publishes `T_wo` from a second node step; the controller applies the rule it already applies above 600 s; the device floors at `p_min` | one mechanism and one meaning of the ratio at every step length; lands the buffer on the controller's target each step, so 60 s and 900 s charge to the same temperature; the controller decides, the device executes its limits; no new constant; measured (§7) | one more node step per pass at ≤ 600 s (the two-mode spec estimated 5 % of a pass per node step; the prototype's 60 s months took 3 to 17 % longer, 42 to 54 s against 36 to 49 s, with fewer passes per step); the power follows the draw minute by minute, faster than a real inverter's ramp (energy-neutral; the start count does not see it) |
| B: a proportional law in the controller on `T0` | `r = (T_target − T0) / band`, no iteration, like the boiler's modulating controller (`GenericBoilerController`, "a power proportional to the lift") | no pass cost, trivially stable | steady-state offset: the buffer settles `band · L / P_th` below the target (2.9 K at 3 kW with a 7.5 K band, 7.5 K at full load), so the 60 s and 900 s buffers differ by several kelvin and the 1 % tolerance is at risk; a gain to source; a second rule beside the exact one |
| C: outlet-temperature control inside the device | the device holds its outlet at the controller's target over the nominal flow, `P = m_nom · c · (T_target − T_ret)` clamped to the modulation range, the series arrangement's law uncapped | no controller change; physically the fixed-speed-pump unit; the lift shrinks at part load, so the COP improves by about 5 % at a 3 K smaller lift | a proportional law too (offset `L / (m_nom · c)`, 5 K at full load); replaces hplib's constant-lift flow model in the parallel mode, so the outlet differs from the 900 s mode's `T0 + 5 K` unless that mode changes too; a modelling decision about the pump (constant flow against constant lift) that rework step d should take with the capacity law, not this spec |

B and C both leave a load-dependent offset between the buffer and the target that A does not have, and A reuses what
is built. C is the right shape for a later decision on the secondary pump (constant flow with a variable lift is how many
units run; hplib's constant 5 K lift is the assumption this spec keeps, stated in §4.6).

## 5. What changes and what does not

- **Results.** Every run with `MoreAdvancedHeatPumpHPLib` moves: at 60 s through D2 to D4 (fewer starts, lower buffer
  and flow temperatures, higher SPF, less unmet hot water; §7), at 900 s only through D4 where the 3600 s times bound
  (the hot-water chain's waits; in the prototype's January and April the 900 s results are bit-identical) and through
  D3 (the published minimum-load electricity the energy manager reads, 2334 W instead of 1800 W for the twin; hplib's
  floor, rarely). 60 s results are not bit-identical: that is the purpose. The part-load rule above 600 s, the
  boiler, district heating, the collector and the distribution system do not change.
- **Public interfaces.** No output is renamed. New configuration field `compressor_control`; the field
  `minimum_thermal_output_power_in_watt` becomes sizable (`AUTO` in the preset; a stated value still wins); the
  defaults of `minimum_running_time_in_seconds` and `minimum_idle_time_in_seconds` change. The energy-system schema
  gains the new field; recorded twins pin none of the three (checked: no `minimum_*_time_in_seconds` in
  `energy_systems/` or `hisim/renovisor/`), so no twin is re-recorded. The starts and running-time KPIs change their
  meaning at 900 s with PR 3 (an estimate instead of a count of ratio-0 steps).
- **RenoVisor** (900 s): every heat-pump calculation's electricity moves a little with PR 1 (hot water) and PR 2 (the
  energy-manager variant), and the starts and running-time KPIs with PR 3; a heads-up note on renovisorissues per PR
  (`system_docs/results_and_interfaces.md` §5), stating that the 900 s energies move by well under 1 % and the
  cycle KPIs by design. The capability document and the translation map do not change (no new measure, no new field a
  request states).
- **Goldens.** Re-blessed through `golden-update.yml` per PR: the twenty files of `household_heatpump_building_sizer`,
  `household_heatpump_car_building_sizer`, `household_heatpump_solar_thermal_building_sizer` and
  `automatic_default_connections` (five parameter sets each, `golden_references/`); the assemblies with a heat pump
  (`tests/assemblies/`, their own CI job) and `tests/test_dhw_chain_time_resolution.py`, whose heat-pump entry in
  `KNOWN_DIVERGENCES` (the 60 s / 900 s unmet hot water, bead hisim-60i6) is expected to pass and is then removed.
- **Beads touched.** hisim-60i6 and hisim-heat-pump-model-rework-fpqz.7 close with their PRs; hisim-ef6u closes with PR 3;
  hisim-heat-pump-starts-plausible-100b gets the field range of §4.5 and is judged on the measurement of PR 4;
  hisim-component-physics-4g9.61 (RunTimeGuard) is not a prerequisite and stays as it is (PR 1 changes two defaults, not
  the timers); hisim-agent-rules-h2ib.11 loses the three fields this spec sources; hisim-3y10's point (3) is covered by
  §4.6; hisim-heat-pump-buffer-plan-7e58 reruns the study after PR 4.

## 6. Verification

- **Unit tests of the pure functions** (`tests/test_more_advanced_heat_pump_hplib*.py`, `tests/test_part_load_rule.py`,
  `tests/test_simple_hot_water_storage*.py`): the device's split (`p`, `tau`) at the band edges (`r = p_min`, `r = 0`,
  `r = 1`) and at both step lengths; the floor while the minimum running time holds the machine on; the buffer's
  `T_wo` at ≤ 600 s equals a node step without the generator's inflow (and still `T_end` on the other two outputs);
  the rule at 60 s lands within the band in a two-circuit example; `published_mode` never sees ratio 0 at ≤ 600 s;
  the sized minimum output (0.30 × rated, a stated value wins, `ON_OFF` gives the rated power); the refusals (negative
  or non-finite times, a share outside (0, 1]).
- **The state machine**: the minimum-time transitions of `on_off_after_minimum_times` keep their table test at the new
  defaults (10 min on, 10 min off, exactly at the edge).
- **Energy balance**: `tests/test_heat_pump_flow_booking.py` (the heat pump books `m c (T_out − T_in)` of its own
  outputs on every step, at a modulated flow too) and the balance check of every run.
- **Step-length agreement** (modelling guide §3): a test in the manner of `tests/test_dhw_chain_time_resolution.py`
  runs the grouped heat-pump twin for four January weeks at 60, 900 and 3600 s and asserts the space-heating heat
  delivered and the heat pump's space-heating and hot-water electricity within 1 % at 900 s (the tolerance of the
  guide and of rework decision 14); 3600 s is recorded. The prototype's 60 s months lie within 1 % of 900 s in January
  at both buffer sizes and in April at 20 l/kW, and at −1.8 % in April at 50 l/kW, the cycling range (§7); PR 4 measures
  the cycling range over the year and either fixes what lies outside or brings the owner a decision (Q5); a wider
  tolerance is not an option (guide §3).
- **Starts**: the twin's yearly starts at 60 s and 900 s lie in the field range of §4.5 (a few hundred to 2,000, below
  5,500); PR 4 asserts the upper bound.
- **Golden gate** per PR, with the deltas in the PR description.
- **Convergence**: the mean passes per step and the forced steps at 60 s and 900 s are reported per PR (recorded, not
  asserted, owner rule of 2026-10-17); the prototype shows no increase at 60 s (§7).

## 7. Measured results

Prototype of 2026-11-26 on the worktree at bad5fa8c2, a throwaway patch of 34 lines (design A: the buffer's exact
`T_wo` at ≤ 600 s, the rule at every step length, the device's floor at `p_min`, minimum times 600 s; the minimum output
left at the study's 1800 W, 0.23 of rated, so that D3's 0.30 is not mixed in), never committed; patch and scripts under
`/home/noah/hisim/agent-tmp/hp-modulation/` (`apply_patch.py`, `run_set.sh`, `analyse.py`, `results.csv`). Grouped
heat-pump twin, variant `metered_directly` (no energy manager), floor heating, 7.78 kW, buffers of 20 and 50 l/kW
(155.6 and 389.0 l), January and April 2021, 60 s and 900 s. "Before" is the unpatched worktree. Starts are the KPI
"Number of heat pump starts" (all modes); the mean run is of `OnOffStateHeatpump != 0`; electricity is the heat pump's
space-heating electricity; the buffer temperatures are the month's mean and maximum of the step mean.

| Month, step, l/kW | Starts a month | Mean run, h | Mean power when on, kW | SPF SH | SH electricity, kWh | Hot-water el., kWh | Buffer loss, kWh | Delivered heat, kWh | Degree-h > 1 K below | Min room, °C | Mean flow, °C | Buffer mean / max, °C | Passes mean / max | Wall, s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Jan, 60 s, 20, before | 299 | 1.16 | 6.97 | 3.43 | 616.4 | 107.0 | 55.8 | 2059.4 | 9.8 | 16.3 | 36.0 | 34.0 / 62.1 | 8.3 / 12 | 49 |
| Jan, 60 s, 20, **after** | 15 | 48.3 | 3.11 | 3.61 | 586.7 | 108.0 | 50.6 | 2066.8 | 0.0 | 19.9 | 36.5 | 31.9 / 36.0 | 7.5 / 14 | 54 |
| Jan, 900 s, 20 (before = after) | 1 | 741 | 3.05 | 3.64 | 582.2 | 109.1 | 50.5 | 2066.8 | 0.0 | 20.0 | 36.5 | 31.9 / 36.0 | – / 28 | 5 |
| Jan, 60 s, 50, before | 232 | 1.52 | 6.98 | 3.78 | 568.8 | 107.6 | 83.9 | 2066.5 | 0.1 | 18.5 | 32.0 | 29.9 / 44.9 | 6.8 / 9 | 41 |
| Jan, 60 s, 50, **after** | 5 | 146 | 3.15 | 3.60 | 600.5 | 108.0 | 93.9 | 2066.8 | 0.0 | 19.9 | 36.7 | 32.1 / 36.0 | 6.5 / 10 | 48 |
| Jan, 900 s, 50 (before = after) | 4 | 185 | 3.12 | 3.61 | 598.4 | 109.1 | 93.7 | 2066.8 | 0.0 | 20.0 | 36.7 | 32.0 / 36.0 | – | 5 |
| Apr, 60 s, 20, before | 168 | 1.18 | 5.78 | 4.26 | 194.6 | 92.2 | 44.8 | 783.4 | 0.6 | 18.3 | 34.7 | 33.8 / 55.8 | 6.7 / 10 | 41 |
| Apr, 60 s, 20, **after** | 109 | 3.5 | 2.48 | 4.69 | 172.9 | 93.2 | 29.3 | 784.4 | 0.0 | 19.2 | 29.6 | 27.3 / 35.0 | 5.8 / 12 | 42 |
| Apr, 900 s, 20 (before = after) | 117 | 5.67 | 1.34 | 4.67 | 174.5 | 93.1 | 31.9 | 784.4 | 0.0 | 19.4 | 32.6 | 28.4 / 35.0 | – | 5 |
| Apr, 60 s, 50, before | 140 | 1.41 | 5.85 | 4.98 | 167.3 | 92.3 | 52.9 | 783.5 | 0.0 | 18.7 | 28.1 | 27.1 / 35.0 | 5.3 / 8 | 36 |
| Apr, 60 s, 50, **after** | 57 | 6.76 | 2.52 | 4.69 | 177.7 | 93.3 | 54.8 | 784.6 | 0.0 | 19.5 | 29.8 | 27.5 / 35.0 | 4.3 / 9 | 39 |
| Apr, 900 s, 50 (before = after) | 149 | 4.34 | 1.43 | 4.65 | 180.9 | 93.1 | 59.2 | 784.3 | 0.0 | 18.9 | 32.6 | 28.5 / 35.0 | – / 23 | 5 |

What the numbers say:

- **The 60 s machine follows the load.** Its mean power when on falls from 7.0 kW to 3.1 kW in January and from 5.8 kW
  to 2.5 kW in April, the same as the 900 s mode's mean of `r · P_th` (3.05 to 3.12 kW in January); its mean ratio when
  on is 0.43 to 0.44 at both step lengths in January.
- **Starts.** January: 299 → 15 (20 l/kW) and 232 → 5 (50 l/kW); April: 168 → 109 and 140 → 57. In January the
  machine modulates and starts only around hot-water charges; in April it cycles below its minimum output, in long
  cycles (mean run 3.5 and 6.8 h). Projected over a year that is a few hundred to about a thousand starts, inside the
  field range of §4.5; PR 4 measures the year. The 900 s starts (117 and 149 in April) are counts of ratio-0 steps
  and are fixed by PR 3.
- **Comfort and buffer.** The 20 l/kW buffer at 60 s no longer overshoots (maximum 36.0 °C instead of 62.1 °C), the
  rooms stay above 19.9 °C instead of 16.3 °C, degree-hours 9.8 → 0.0. The penalty the buffer-size study found at 60 s
  for small buffers is gone: the two sizes differ in January by 14 kWh of electricity (2.4 %, the larger buffer's extra
  standby loss of 43 kWh) and in April by 5 kWh.
- **60 s against 900 s.** Space-heating electricity at 60 s relative to 900 s, before → after: January 20 l/kW +5.9 %
  → −0.8 %; January 50 l/kW −4.9 % → +0.4 %; April 20 l/kW +11.5 % → −0.9 %; April 50 l/kW −7.5 % → −1.8 %. Delivered
  heat agrees to 0.1 % after, hot-water electricity within 1 %. The buffer's mean temperature and the mean flow
  temperature agree to 0.2 K in January; in April the 900 s mode charges to the target every step while the 60 s
  machine rests between the switch points, so its buffer and flow run 1 to 3 K warmer (Q5).
- **The SPF at 50 l/kW falls at 60 s** (3.78 → 3.60 in January, 4.98 → 4.69 in April): the old on/off cycle coasted the
  large buffer down to 25 °C before each restart and so charged at a cooler mean (29.9 °C), where the modulating machine
  holds it at the target (32.1 °C, like 900 s). The better SPF of the large buffer at 60 s in the study was this
  coasting, which the target of supply + 2.5 K now fixes at both step lengths (Q8).
- **Cost.** The 60 s months took 3 to 17 % longer with one more node step per pass and fewer passes per step; the
  largest pass count rose by one or two. The 900 s runs are bit-identical before and after: the minimum times of
  3600 s did not bind at 900 s in these months, and the patch acts only at and below the threshold.

## 8. Open questions

- **Q1. The design at ≤ 600 s** (D1, D2). (a) Design A, the exact rule at every step length and the device's floor,
  as measured. (b) Design B, a proportional law in the controller: no pass cost, but a load-dependent offset of up to
  the band below the target at 60 s against a buffer that ends at the target at 900 s, so the two step lengths charge
  to different temperatures and the 1 % band is at risk. (c) Design C, outlet control inside the device: changes
  hplib's constant-lift flow model and belongs with the pump decision of rework step d. **Recommendation: (a).** Bead
  hisim-heat-pump-model-rework-fpqz.7 (`owner-decision`).
- **Q2. The minimum output** (D3). (a) 0.30 of the rated capacity for an inverter, 1.0 for an on/off machine, as a sized
  field; the twin goes from 1800 W to 2334 W. (b) Keep 1800 W as a plain default with the Vitocal 250-A's 2.6 kW at
  A7/W35 as its source (0.23 of the twin's rating): no sizing law, but a 15 kW machine would then modulate to 0.12,
  which no datasheet supports. (c) 0.25, the Bosch range's low edge. **Recommendation: (a)**; every machine keeps a
  modulation range of the same shape, and the rating's Cdh bins (step b) use the same share. Bead
  hisim-heat-pump-model-rework-fpqz.7.
- **Q3. The minimum times** (D4). (a) 600 s running, 600 s idle. (b) 600 s running, 1200 s idle (Stiebel's on/off default
  for every machine): longer rests, the hot-water chain waits twice as long after a stop, fewer starts. (c) 900 s and
  900 s (the middle of the BWP's 10 to 20 min, one 900 s step): simple at RenoVisor's step, but above every manufacturer
  minimum found. **Recommendation: (a)** for the default (air/water inverter) and (b)'s 1200 s idle for the on/off
  brine preset when it exists. Bead hisim-60i6 (`owner-decision`).
- **Q4. Hot water** (D5). (a) Full-load charging, unmodulated, one minimum output per machine (the 0 W hot-water
  constant goes). (b) Modulate the hot-water charge to the cylinder's target with the memoryless rule of decision 11
  at every step length: fewer hot-water starts, but the cylinder is a step-mean store whose rule alternates under a
  draw (hisim-hydronics-fxix.17), and the heat pump's hot-water part load is parked (#916). **Recommendation: (a)**; (b) is a
  bead for after #916. Bead hisim-heat-pump-model-rework-fpqz.7.
- **Q5. The 60 s / 900 s tolerance for the heat pump's electricity in the cycling range.** With the prototype the
  60 s space-heating electricity lies within 1 % of 900 s in January at both sizes and in April at 20 l/kW, and 1.8 %
  below it in April at 50 l/kW (§7), against 5 to 12 % off before; the guide asks for 1 %. In the cycling range the
  60 s machine rests between the switch points while the 900 s machine charges to the target every step, so the two
  buffers' mean temperatures and outlets differ. (a) Keep 1 % and have PR 4 measure the full year; if the cycling
  range stays outside, decide then between a 900 s charge that keeps the 60 s band (the exact rule runs the machine
  only while the start-of-step buffer lies below the switch-off point, as the hysteresis does) and a stated wider
  tolerance. (b) State 2 % for the heat pump's space-heating electricity now, with the reason, as the hot-water chain's
  1.5 % at 3600 s was stated. **Recommendation: (a)**; a widened tolerance before the year is measured lets the step
  length decide the result (guide §3). Bead hisim-ef6u.
- **Q8. The charge target with a modulating machine.** The target lies half the upper offset (2.5 K) above the
  heating curve's supply temperature because the old 60 s on/off cycle swung the buffer up to supply + 5 K and down
  to supply − 5 K and charged at that mean (owner, 2026-10-19, hisim-hydronics-fxix.27). A modulating machine holds the
  buffer at the target without a swing, so the offset no longer stands for anything, and it costs COP: every kelvin of
  target is a kelvin of outlet (about 1.5 % of electricity at the twin's lifts). (a) Keep supply + 2.5 K; the 60 s and
  900 s results stay comparable with today's. (b) Measure supply + 0 K (`SHARE_OF_UPPER_OFFSET_IN_TARGET` = 0) in
  PR 4 at both step lengths with comfort, and decide on the numbers. **Recommendation: (b)** as a measurement inside
  PR 4, no change before it; the switch-on and switch-off points stay where they are either way. Bead
  hisim-heat-pump-model-rework-fpqz.7 (`owner-decision`).
- **Q6. The ratio's name.** With D1 the ratio is an energy share at every step length, no longer "the fraction of the
  step a device runs at full load". (a) Keep the output names (`RequestedPartLoadRatioSh`, `ActualPartLoadRatioSh`) and
  reword their descriptions and the module docstring of `hisim/part_load.py`. (b) Rename to `...EnergyShareSh` in a PR
  of its own with a heads-up (the names are addresses in goldens and the energy manager's wiring).
  **Recommendation: (a)**; the quantity is the same number in every case the code handles today. Bead
  hisim-heat-pump-model-rework-fpqz.7.
- **Q7. The 900 s starts KPI after PR 3.** (a) The estimate of §4.7 from the minimum times. (b) Keep today's count of
  ratio-0 steps and name it in the KPI's description. **Recommendation: (a)**; a count of empty steps is not a count of
  starts, and the buffer study read it as one. Bead hisim-ef6u.

## 9. Beads and pull requests

Epic hisim-heat-pump-model-rework-fpqz.

| Bead | Content | PR |
|---|---|---|
| hisim-60i6 (P1, `owner-decision`) | PR 1: the sourced minimum times, 600 s and 600 s, with the sources of §4.5 in the field comments; the two unit tests of the edges; `KNOWN_DIVERGENCES` of the hot-water chain test re-measured; goldens re-blessed; heads-up on renovisorissues | – |
| hisim-heat-pump-model-rework-fpqz.7 (P1) | PR 2: the buffer's exact `T_wo` at ≤ 600 s; the rule at every step length in the space-heating controller; the device's split with `tau = 1` and the floor at `p_min` (also under the minimum running time); `compressor_control` and the sized minimum output (0.30, sources of §4.4); the hot-water constant removed; the tests of §6; goldens; heads-up | – |
| hisim-ef6u (P3 → P2) | PR 3: the split above 600 s (`tau = r / p`), the published running time and the starts estimate; its tests; the 900 s goldens' KPI columns; heads-up | – |
| hisim-ef6u, hisim-heat-pump-starts-plausible-100b (P1) | PR 4: the step-length test of §6 (60 / 900 / 3600 s, four weeks, 1 %) and the yearly starts against the field range; whatever the measurement shows is fixed in it or brought to the owner (Q5) | – |
| hisim-heat-pump-buffer-plan-7e58 (P2) | the buffer-size study rerun at 20 / 35 / 50 l/kW, full year, 60 s and 900 s, after PR 4 | – |
| hisim-heat-pump-model-rework-fpqz.4 (P1) | step b: Cdh in the rating with D3's minimum output, the start-up loss per start; reads `compressor_control` | – |
| hisim-component-physics-4g9.61 (P3) | RunTimeGuard; not a prerequisite | – |
| hisim-agent-rules-h2ib.11 (P3) | the three fields sourced here leave its list | – |

Each PR is small, stacked on the one before, opened as soon as it is ready, with its goldens re-blessed on current
main and a Problem / What was done / Result description; the tests per PR are listed in §6.
