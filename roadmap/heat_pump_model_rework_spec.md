# Spec: the heat pump model gets a Carnot-efficiency COP, a part-load penalty, defrosting and a sourced capacity law

Status: decided; implementation per step. Decisions (1) to (7) taken by the owner on 2026-10-21, decisions (8) to
(17) on 2026-11-07 (§2); §8 keeps the review questions with their answers, none is open.
Owner: Noah Pflugradt. Epic: hisim-heat-pump-model-rework-fpqz (P1); children .1 research (closed), .2 COP layer,
.3 defrost, .4 part load and cycling, .5 capacity law, .6 outdoor humidity, hisim-heat-pump-backup-rod-a121 the
backup heating rod (decision 8). Evidence: hisim-x2mu (closed).

The model in question is `MoreAdvancedHeatPumpHPLib` (`hisim/components/more_advanced_heat_pump_hplib.py`, model
parts in `more_advanced_heat_pump_hplib_model.py`). It calls hplib (https://github.com/FZJ-IEK3-VSA/hplib) for the
thermal power, the electrical power and the COP of each step, and since hisim-component-physics-4g9.15 scales the
COP to a stated EN 14825 SCOP pair (`ScopCalibration`). Every RenoVisor heat-pump calculation runs this model.

## 1. Problem

hplib fits the COP of a heat pump group (for `model="Generic"`: all air/water or all brine/water units of its
database) as a plane in source, outlet and ambient temperature. hisim-x2mu checked that plane, with HiSim's SCOP
calibration on top, against the certified rating points of about 1,930 Keymark units (1,743 air/water, 176
brine/water; hplib's own raw Keymark exports), three datasheets (Vaillant aroTHERM plus, Viessmann Vitocal 250-A,
alpha innotec Hybrox 8) and Fraunhofer ISE's field study "WP-QS im Bestand". Calibrated to each unit's own two
SCOPs, the Generic air/water plane misses the certified COP (median over units):

| Rating point | Error of hplib + calibration | Why it matters |
|---|---|---|
| A-7/W52 | -33 % | the cold design point of every radiator house |
| A7/W55 | +16 % | mild-day radiator heating and hot water |
| A2/W42 | +11 % | the most heat-weighted bin of the W55 rating |
| A12/W24 | -17 % | mild part load (cycling, see below) |
| A-7/W65 | rod alone (COP 1) | real units reach about 2.0-2.4 |
| hot water | calibrated 3.29 at A7/W55 vs 2.81 certified | field hot-water seasonal factor about 2.8 |

Mean absolute error over all ten points: 12.2 % (air), 10.0 % (brine). The error changes sign with the outdoor
temperature, not with the outlet, so no outlet-dependent calibration factor or cap can remove it; the two-anchor
calibration itself is confirmed (its mean-outlet anchors were the subject of hisim-x2mu).

Three further gaps:

* **No defrosting.** hplib has none. An air/water unit around 0-5 °C at high humidity loses capacity and COP to
  frost on its evaporator, and a reverse-cycle defrost takes heat back out of the water circuit. Field studies put
  the defrost heat at about 4-7 % of the seasonal heat.
* **No part-load or cycling penalty.** The SCOP calibration factor absorbs the cycling losses at the mild rating
  bins, which pulls brine units' W55 COP about 15 % too low (2.5 vs 3.0); certified part-load points show on/off
  brine units +17 to +28 % too high at A12/W30 and air units -17 % at A12/W24.
* **An inverted capacity surface.** hplib's Generic air/water capacity rises towards cold outdoor temperatures at
  low outlets (sized to 8 kW at A-7/W52: 13.2 kW at A-7/W35, 5.75 kW at A7/W35), the opposite of real units
  (Vitocal 251: A-7 6.47 / A2 6.79 / A7 8.00 kW at W35). At the bivalent point hplib's capacity is +49 % above the
  certificates (median, IQR +28 to +70 %).

Data and scripts: `/home/noah/hisim/agent-tmp/x2mu/` (Keymark set `all.json`, `scripts/compare.py`) and
`/home/noah/hisim/agent-tmp/hp-research/` (model benchmark `keymark_eval/`, defrost material `defrost/`, literature
`lit/`). These live on the owner's machine, outside the repository; the fit is reproduced by a script cited in the
step-(a) PR.

## 2. Decisions

| # | Date | Decision | Reason |
|---|---|---|---|
| 1 | 2026-10-21 | **Hybrid, then replace.** First an own COP layer with hplib's thermal power kept; last, hplib's capacity surface is replaced by a sourced law and the hplib dependency dropped. | The COP error is the largest and is fixed by a closed-form law; the capacity needs its own sourced data. Doing the COP first keeps each step measurable on its own. |
| 2 | 2026-10-21 | **COP = η · T_out / (T_out − T_source + d)** (temperatures in K, the difference in K), one pooled Keymark fit per source type: air η 0.451, d 4.45 K; brine η 0.568, d 4.50 K. The two-anchor SCOP calibration stays on top, and a minimum-lift guard limits the law at small lifts. | It is the constant-Carnot-effectiveness model of IBPSA/AixLib and the principle of EN 15316-4-2. On the Keymark set it cuts the air error from 12.2 % to 7.7 % (A-7/W52 -33 → +12 %, A7/W55 +16 → 0 %, A2/W42 +11 → -2 %) and the brine error from 10.0 to 9.6 % (B0/W55 -15 → +1 %). It beats the quadratic-in-lift fits (when2heat 11.1 %, Staffell 9.1 % calibrated). Field studies find a stable Carnot efficiency (0.32-0.44 air, 0.44-0.52 ground). |
| 3 | 2026-10-21 | **Defrost in two modes, like the space-heating buffer:** at steps ≤ 600 s a dynamic ice-mass model (one ice-mass state, discrete reverse-cycle defrost events drawing heat from the buffer; Zanetti et al. 2025, AixLib style); above 600 s the EnergyPlus/DOE-2 defrost-time fraction from the humidity ratio, without its constant 0.917 derate. Both calibrated to the same targets (A2 about ×0.90; field defrost heat share about 4-7 %) so 60 s and 900 s agree over a year. Certified COPs are treated as defrost-free (the SCOP rating runs without defrost). | A 60 s run can resolve single defrost cycles and should; a 900 s step cannot, and a time fraction is the established averaged form. Certificates hardly contain defrost: units pass the EN 14511 test window without defrosting (Nordsyn 2019), and the Keymark A2 points dip by only about 1 % (median), so no double counting. |
| 4 | 2026-10-21 | **Outdoor humidity for all weather sources:** the TRY reader keeps RF and x; the NSRDB and DWD minute sources fetch humidity. No fixed fallback humidity as the plan. | Defrost depends on humidity; a fixed value would hide exactly the weather dependence the model is for. |
| 5 | 2026-10-21 | **Cycling:** no degradation coefficient on simulated steps (HiSim cycles explicitly: minimum running and idle times, part-load ratio); the EN 14825 Cdh (0.9, or 1.0 for an inverter) only inside `StandardizedSeasonalCop` below the minimum output; an explicit start-up loss per start. Default machine type: air/water = inverter, brine/water = on/off; configurable. | A Cd on top of explicit cycling counts the loss twice. Inside the rating the bin method needs Cdh to reproduce what the datasheet rated, so the calibration factor stops absorbing cycling losses. The start-up loss is the physical cost HiSim's own cycles must carry. |
| 6 | 2026-10-21 | **Hot water: no separate model;** the same curve at the hot-water outlet. EN 16147 serves as a check. | The pooled air curve at A7 and the EN 16147 reference outlet is -4 % from the certified hot-water COP (median of 456 units). A separate model would need data a request cannot state. |
| 7 | 2026-10-21 | **Capacity:** replace hplib's inverted capacity surface (+49 % at the bivalent point) in the last step. | The capacity decides when the backup heater runs and how often the unit cycles; it needs EN 14511 capacity tables, a separate data job. |
| 8 | 2026-11-07 | **The backup heating rod stays, as a real backup inside the heat-pump component.** Real units ship with an electric heating rod for the coldest days, so the component keeps one: its own rated electrical power (a sized field, default in §3.1), its own electricity output with its own energy port, its heat into the same circuit as the compressor's. It runs only where the compressor falls short, when the requested heat exceeds the compressor's thermal capacity at the current temperatures or the required outlet exceeds the compressor's maximal outlet, and adds only the missing heat at COP 1, up to its rated power. hplib's COP-1 switch goes, as decision 2 implies. Amends the rod paragraph of §3.1 and Q4; bead hisim-heat-pump-backup-rod-a121. | The 2026-10-21 reading dropped the rod with hplib's switch ("the rod no longer appears in the model's answer"); the owner had understood that as removing hplib's fit artefact, not the backup element every real unit has. Without a backup, a heat pump at its limit (undersized, a day colder than the design point, an outlet above its maximum) delivers less than the demand with nothing to cover it, and the rod's electricity, which decides the SPF of such a unit (hisim-heat-pump-outlet-limit-2vq7: 16 % of a year's space-heating electricity on 70/55 radiators), is missing from the balance. |
| 9 | 2026-11-07 | **Minimum-lift guard (Q1): a lift floor of 12 K for both source types.** Brine then extrapolates below its smallest certified lift (24 K, B0/W24) on rare mild days; the spec says so. | 12 K is the smallest certified lift of the air set (A12/W24); holding the lift keeps the COP at the edge of the data instead of growing without bound. A brine unit sees lifts below 24 K only on rare mild days with a low outlet, where little heat is made. |
| 10 | 2026-11-07 | **Water/water units (Q2) use brine's parameters** (η 0.568, d 4.50 K), flagged as an assumption in the docs. | No Keymark data; a liquid source behaves like brine. Refusing water/water would block a request that states one. |
| 11 | 2026-11-07 | **Named hplib models (Q3): the Carnot law replaces every model's COP;** a named model's datasheet SCOPs calibrate it. | One law for every machine keeps the calibration and the rating consistent; the datasheet SCOPs carry what the named model knows. |
| 12 | 2026-11-07 | **Capacity at hplib's former rod switch (Q4): superseded by decision 8.** The compressor's capacity at the switch stays `P_el,fit` as the interim until step d's capacity law; the backup rod covers the rest, up to its rated power. | With the backup rod the shortfall above the switch is covered instead of lost, so the low interim value only moves heat from the compressor to the rod until step d replaces the capacity surface. |
| 13 | 2026-11-07 | **Uncalibrated default (Q5): the pooled Keymark unit** (air W35 4.46 / W55 3.32); the heat-pump goldens are re-blessed. | A machine without a stated SCOP should be the population's unit, not hplib's Generic plane the law replaces. |
| 14 | 2026-11-07 | **Defrost modes (Q6): the 900 s mode within 1 % of the 60 s mode in yearly heat-pump electricity,** checked in step c's PR. | 1 % is the step-length tolerance the other models hold (emitter spec §7); a larger band would let the step length decide the result. |
| 15 | 2026-11-07 | **Start-up loss (Q7): `t_start_loss` derived from the EN 14825 Cdh test cycles.** | Consistent with the rating method: the same cycles that give Cdh inside `StandardizedSeasonalCop` give the loss per start outside it. |
| 16 | 2026-11-07 | **Brine (Q8): keep the pooled brine fit now, re-judge after step b's cycling term.** | The brine error moves little (10.0 → 9.6 %), and its mild-bin errors are cycling errors that step b's Cdh addresses; judging before that would mix the two. |
| 17 | 2026-11-07 | **Cooling in step d (Q9): a Carnot-efficiency EER fitted to the Keymark cooling data.** | hplib's EER plane goes with hplib; the same law shape with its own fit keeps cooling in the model without a second model family. |

## 3. Design per step

Each step is its own PR (or a short stack), with its own measurement and golden re-bless. Units are in every name.

### 3.1 Step (a): the Carnot-efficiency COP layer (child .2)

**Law.** For heating (hplib mode 1), every operating point's COP is

    cop_carnot = η · (T_out + 273.15) / (max(T_out − T_source, lift_floor) + d)

with `T_out` the outlet (hplib's: return plus its lift, 5 K nominal), `T_source` the source the heat pump reads
(outdoor air for air/water; brine or well water for brine and water/water), all in °C. Parameters per source type,
as class constants with their source (no configuration fields, since no request or preset states them):

| Source | η | d in K | Fit |
|---|---|---|---|
| air (hplib groups 1, 4) | 0.451 | 4.45 | pooled least squares over the ten EN 14825 / EN 14511 points of 1,743 Keymark units |
| brine (groups 2, 5) | 0.568 | 4.50 | the same over 176 units, brine at 0 °C |
| water (groups 3, 6) | brine's | brine's | no Keymark data; liquid source like brine (decision 10, flagged as an assumption in the docs) |

**Minimum-lift guard.** The law grows without bound as the lift shrinks (it turns negative where the source is
warmer than the outlet, e.g. A30/W25). The lift in the denominator is held at no less than `lift_floor` = 12 K, the
smallest lift any certified point has (A12/W24). Below that the law is not evidenced; holding the lift keeps the
COP at its value at the edge of the data (air: 8.15 at W24 uncalibrated) instead of extrapolating. Decided for both
source types (decision 9); brine has no certified point below a 24 K lift (B0/W24), so between 12 K and 24 K the
brine law extrapolates, which happens on rare mild days with a low outlet.

**Calibration.** `ScopCalibration` and `StandardizedSeasonalCop` are unchanged in method; they rate the machine
with the new COP. With a stated SCOP pair the calibrated machine still rates exactly at the stated SCOPs. Without a
stated SCOP the machine is the pooled Keymark unit: EN 14825 rates it at W35 4.46 / W55 3.32 (air; Keymark median
4.60 / 3.39) and 4.74 / 3.94 (brine; median 5.20 / 3.90). hplib's Generic plane rated 4.74 / 3.40 and 4.85 / 4.02.
The law replaces the COP of every hplib model, also a named manufacturer model, whose datasheet SCOPs calibrate it
(decision 11).

**Thermal power and flow.** hplib still gives the thermal power, the mass flow and the outlet; the electrical power
becomes `P_el = P_th / cop`. hplib's own COP plane only shapes its capacity (`P_th = P_el,fit · COP_fit`).

**The heating rod.** hplib switches to its heating rod alone where its own COP plane reaches 1 (for the 8 kW
Generic air unit: A-7/W65, A0/W69; it then books its reference power, 8.1 kW, at COP 1), and adds the rod to the
compressor where the compressor alone cannot reach the minimum power. The new COP stays well above 1 at every
realistic lift (air A-15/W75: 1.66), so the rod no longer appears in the model's answer: where hplib's plane
switches to the rod, the capacity continues as the compressor's at hplib's switch, `P_th = P_el,fit` (continuous
there, so no 3.3 kW jump), and the compressor-plus-rod branch becomes the compressor raised to the minimum power.
A backup heater, where a system has one, is a separate component. Consequences: the straddle handling around the
rod switch (#930; its fix #1097, hisim-g0dp) no longer meets a jump, and hisim-z0v4 (calibrated compressor below
COP 1 next to the switch) is moot. Both stay consistent: whichever of #1097 and step (a) merges second removes or
keeps the straddle path as dead-but-harmless.

*Amendment (owner, 2026-11-07, decision 8):* the paragraph above stays as the record of the 2026-10-21 reading; what
is built is this. The model keeps a backup heating rod, as a real unit ships with one, but as a real backup and not
as hplib's COP-1 fit artefact:

* **Where.** The rod is part of the heat-pump component. Its rated electrical power is a configuration field
  `heating_rod_rated_electrical_power_in_watt`, a `sized_field`; the default rule gives 9 kW, reduced to the
  machine's rated thermal power at the design point where that is smaller. Source: the residential indoor units of
  the main manufacturers carry one element class, 6 or 9 kW, switchable in 3 kW stages by the installer (Vaillant
  uniTOWER plus / VWZ MEH 97: 9 kW; Viessmann Vitocal 250-A: 9 kW in 3/6/9 kW stages; Daikin Altherma 3: 3, 6 or
  9 kW; Bosch Compress 7000i AW indoor unit: 9 kW; Stiebel Eltron HPA-O and WPL: 8.8 kW), independent of the
  outdoor unit's size, and the installer's stage setting keeps the rod below the compressor's rated heat. This is an
  assumption to verify against the datasheets in the step PR. The field is in `describe`; a preset or a request may
  state it. The rod's electricity is its own output, `ElectricalInputPowerHeatingRod` (W), with its own energy port
  (consumption of electricity), and is also counted in `ElectricalInputPowerTotalHeatpump`, which the meter reads;
  its heat goes into the same flow as the compressor's (the space-heating or the hot-water outlet), so the heat
  ports do not change. The balance and the KPIs thereby see the rod's electricity apart from the compressor's.
* **When.** Only where the compressor falls short, in one of two cases: (1) the heat the controller asks for in the
  step exceeds the compressor's thermal capacity at the current source and outlet temperature (hplib's thermal
  power until step d replaces the capacity surface); (2) the required outlet exceeds the compressor's maximal
  outlet. That limit is the configured maximal supply temperature of hisim-heat-pump-outlet-limit-2vq7 once it
  exists; until then the outlet at which hplib's fitted plane reaches COP 1 at the current source temperature
  stands in (for the 8 kW Generic air unit about 65 °C at A-7 and 69 °C at A0), the line at which hplib switched
  to its rod. In both cases the compressor runs at its continuous COP (the law above) and its full capacity, and
  the rod adds only the missing heat, at COP 1, up to its rated power: in case (1) the requested heat minus the
  compressor's capacity, in case (2) the heat that lifts the compressor's flow from its maximal outlet to the
  required outlet, `m_flow · c_p · (T_required − T_max)`. What the rod cannot cover is not delivered. Where the
  compressor copes, the rod does not run at all: no rod on a mild day, and no rod inside the rating (the bins of
  `StandardizedSeasonalCop` are covered by the compressor; the rod is not part of the calibration).
* **Per step.** The rod decision is an energy decision per step, requested heat against the compressor's capacity
  over the step, not a fixed return temperature as hplib's switch was. Above 600 s, where the heating side runs
  the compressor for a part of the step (the start-of-step part-load rule), the rod covers only the missing share
  of the step as well: `E_rod = min(Q_requested − Q_compressor,max, P_rod · Δt)`, never the whole step because the
  return crossed a line. This is the fix of hisim-rod-share-step-length-67f0: its 37-41 % rod share at 60 s against
  59-60 % at 900 s (case B, January week) came from hplib's whole-step switch at a fixed return, which the new
  trigger replaces; the step's verification measures case B at 60, 300, 900 and 3600 s (§5).
* **hplib's switch.** hplib's COP-1 switch and its compressor-plus-rod branch go (the Carnot COP stays above 1 at
  every realistic lift, decision 2); the capacity continues as the compressor's at the switch, `P_el,fit` (decision 12),
  and the rod covers the shortfall above it. The straddle handling (#930, #1097, hisim-g0dp) and hisim-z0v4 stay
  moot as stated above. #1113 (child .2, open) keeps its Carnot COP and, instead of removing the rod, adds the
  backup rod: the sized field, the rod output and its port, the two triggers, the per-step share and the tests of
  §5; bead hisim-heat-pump-backup-rod-a121 holds the work.

**Cooling** (mode 2) stays hplib's EER.

**Hot water** uses the same law at the hot-water outlet (decision 6).

**Hot path.** The law is closed-form and is evaluated where hplib is evaluated today, once per cached 0.1 K grid
point; the result cache stays.

### 3.2 Step (b): part load and cycling (child .4)

* `StandardizedSeasonalCop`: where a bin's demand is below the machine's minimum output, the bin's COP becomes
  `COP_full · CR / (Cdh · CR + 1 − Cdh)` (EN 14825:2022) with `CR` = demand / minimum output and Cdh
  0.9 for an on/off machine, 1.0 for an inverter (decision 5). The minimum output is the configuration's
  `minimum_thermal_output_power_in_watt` for an inverter and the full output for an on/off unit.
* A configuration field `compressor_control` (enum `INVERTER` / `ON_OFF`); default by source type: air/water
  inverter, brine/water on/off. RenoVisor states nothing about it and takes the default.
* A start-up loss per start: each start books an extra electrical energy `E_start = P_el,nominal · t_start_loss`
  (an equivalent time of full-power electricity for the pressure build-up and the cold heat exchanger), taken in
  the first step of a run so the energy balance closes. `t_start_loss` is derived from the EN 14825 Cdh test cycles
  (decision 15); the derivation is in the step's PR.
* Verified against the certified A12 points (§5).

### 3.3 Step (c): humidity and defrost (children .6 and .3)

* **Humidity (.6).** The weather publishes `RelativeHumidity` (%) and the humidity ratio (g/kg) for every source:
  TRY keeps RF and x (dropped today by `RAW_COLUMNS` in `hisim/components/weather/calculation.py`); NSRDB and DWD
  minute sources fetch humidity from the upstream services (NSRDB "Relative Humidity", wetterdienst). The weather
  cache keys change with it. A test per source.
* **Defrost, steps ≤ 600 s.** One state, the ice mass on the evaporator, in kg. It grows while the coil
  (`T_coil = 0.82 · T_out − 8.589` °C, EnergyPlus's coil-temperature correlation) is below 0 °C and below the air's
  dew point, at a rate proportional to the humidity-ratio difference between the air and saturation at the coil
  and to the evaporator heat flow. Its effect while it grows: capacity and COP fall with the ice mass (blocked air
  flow). At a threshold mass the unit defrosts by reversing its cycle for a fixed time: the compressor draws
  electricity, the outlet delivers no heat, and the melt heat (latent plus sensible, over a defrost efficiency) is
  drawn from the water circuit (the buffer sees a negative heat flow). Then the ice mass is 0. One calibration
  parameter (the growth coefficient), fitted to the targets below. (Zanetti et al. 2025; AixLib's ice-mass
  approach.)
* **Defrost, steps > 600 s.** The EnergyPlus/DOE-2 time fraction `f = 1 / (1 + 0.01446 / Δw)`, `Δw` the
  humidity-ratio difference air minus saturation at `T_coil` (kg/kg, at least 0), below 5 °C outdoor; per step the
  heat delivered is reduced by the share `f`, and the defrost heat `f`-weighted is drawn from the circuit, without
  EnergyPlus's constant 0.917 derate. One scale parameter on `f`, fitted to the same targets.
* **Targets for both modes:** COP at A2 / 84 % relative humidity about ×0.90 of the defrost-free COP; defrost heat
  4-7 % of the seasonal heat on a German TRY (prototype on TRY: +5 to +8 % seasonal electricity). The 900 s mode
  agrees with the 60 s mode within 1 % of the heat pump's yearly electricity, checked in step c's PR (decision 14).
* Certified COPs and the SCOP rating stay defrost-free (decision 3), so the calibration does not change.
* Brine and water sources: no defrost.

### 3.4 Step (d): capacity law and dropping hplib (child .5)

* The thermal power at full load becomes a sourced law relative to the rated capacity, e.g. linear in the source
  temperature and weakly falling with the outlet, from EN 14511 capacity tables of the Keymark set or AixLib's
  air-to-water records. Sizing stays: the machine is sized to the building's heating load at the design point.
* The minimum output (modulation floor) becomes a share of the rated capacity.
* hplib, its parameter tables and the result cache are removed; `CalculationRequest` and the 0.1 K grid with
  them. Cooling gets a Carnot-efficiency EER fitted to the Keymark cooling data in the same step (decision 17).
* Verified: capacity at the bivalent point vs certificates median within ±10 % (today +49 %).

## 4. Literature

Key references (the full 2020-2026 review of 41 entries with DOIs is in the research report of child .1):

* Rogeau, Vieubled, Ruche, Girard (2024): A generic methodology for mapping the performance of various heat pumps
  configurations considering part-load behavior. Energy and Buildings 318, 114490. doi:10.1016/j.enbuild.2024.114490
* Römer, Vering, Göbel, Müller (2026): Towards optimal heat pump system design: with a little help from the
  manufacturers. npj Clean Energy. doi:10.1038/s44406-026-00028-6
* Wüllhorst, Jansen, Mehrfeld, Müller (2021): Modelica heat pump models for system applications. Proc. 14th
  Modelica Conference. doi:10.3384/ecp21181561 (AixLib `ModularReversible`; BESMod 2023)
* Zanetti et al. (2025): fast model for air-source heat pump frosting and defrosting behaviour. Building Simulation
  2025. doi:10.26868/25222708.2025.1497
* Klingebiel et al. (2025), defrost initiation of air-source heat pumps, experimental. Energy, article 135871
  (DOI to be confirmed).
* Roccatello et al. (2023): defrost regression on temperature and relative humidity. Journal of Building
  Engineering (ScienceDirect pii S2352710222017739).
* Nordsyn (2019): heat pumps in humid Nordic climates; defrost in standard tests. TemaNord 2019:502.
  doi:10.6027/TN2019-502
* Tang, Wang, Wang, Yang (2021): frosting types and degrees of air-source heat pumps. Front. Energy Res. 9:708478.
  doi:10.3389/fenrg.2021.708478
* Baster (2017): air-source heat pump modelling from standard test data, PhD thesis, Strathclyde.
  doi:10.48730/ndz6-0r38
* Ruhnau, Hirth, Praktiknjo (2019): when2heat, Scientific Data 6:189. doi:10.1038/s41597-019-0199-y (the
  quadratic-in-lift comparison)
* Staffell, Brett, Brandon, Hawkes (2012): a review of domestic heat pumps. Energy Environ. Sci. 5:9291.
  doi:10.1039/c2ee22653g (the other quadratic comparison)
* EnergyPlus Engineering Reference, DX heating coil defrost (DOE-2 time fraction, coil-temperature correlation).
* Fraunhofer ISE (2025): WP-QS im Bestand, field monitoring of heat pumps in existing buildings.
* Standards: EN 14825:2022 (SCOP bin method, Cdh), EN 14511 (rating points), EN 16147 (hot water),
  EN 15316-4-2 (Carnot-efficiency method).

## 5. Verification per step

| Step | Check | Pass |
|---|---|---|
| a | The implemented law, calibrated per unit, against the Keymark population (same ten points as hisim-x2mu) | mean abs. error air ≈ 7.7 %, brine ≈ 9.6 %; A-7/W52 median within ±15 % |
| a | Unit tests: the curve at the Keymark points, exact rating at stated SCOPs (air and brine, several pairs), the lift guard, a calibrated generic air unit at A-7/W52 vs a certificate (fails on main at about -33 %) | pass |
| a | Full year 2021, `household_heatpump_building_sizer` at 60 s and 900 s, solar variant at 900 s: space-heating and hot-water electricity, SPF, cycles, before/after | reported in the PR |
| a | The backup rod (decision 8), unit tests: the rod runs only on a shortfall (requested heat above the compressor's capacity, or the required outlet above the compressor's maximal outlet); it adds only the missing heat at COP 1, up to its rated power; its electricity is on its own output and port and in the total; a mild day runs no rod; the outlet-limit trigger fires at the interim limit and, once hisim-heat-pump-outlet-limit-2vq7 exists, at the configured limit | pass |
| a | Case B of hisim-rod-share-step-length-67f0 (heat pump on 70/55 radiators, January week) at 60, 300, 900 and 3600 s: rod share, reached supply, delivered heat, electricity; the energy balance with the rod's port | rod share agrees across step lengths within the 1 % step-length tolerance of the emitter spec's §7; balance closes |
| b | Keymark A12/W24 and A12/W30 points with the rating's Cdh, by machine type | brine A12/W30 error from +15 % towards 0; air not worse |
| b | Starts × start-up loss in a full year, energy balance closes | reported |
| c | Humidity per weather source (test per source) | present, plausible range |
| c | Defrost: A2/84 % multiplier; seasonal defrost heat share on TRY; 60 s vs 900 s annual electricity; the buffer's energy balance with the defrost draw | ×0.90 ± 0.02; 4-7 %; the 900 s mode within 1 % of the 60 s mode in yearly heat-pump electricity (decision 14); balance closes |
| d | Capacity at the bivalent point vs certificates; run time; no hplib import left | median within ±10 %; not slower |

## 6. What moves

* **Goldens.** Every golden of a setup with `MoreAdvancedHeatPumpHPLib` moves at steps a, b (start-up loss), c
  and d. The coordinator blesses them per step; the PR lists which. At step a they move with the uncalibrated
  default, the pooled Keymark unit (decision 13).
* **RenoVisor.** Every heat-pump calculation's electricity, SPF and costs move at steps a, c and d (b mostly for
  brine). Each such PR carries a heads-up note for the frontend (renovisorissues). The stated SCOP pair still
  rates exactly; the range of SCOP pairs the calibration can reach changes with the new shape, so a pair refused
  today may be accepted and the other way round (reported in step a). A calculation whose compressor falls short
  (an undersized unit, an outlet above the compressor's maximum, a day colder than the design point) shows the
  backup rod's electricity and its share of the heat in the result from step a on (decision 8); the heads-up note
  of step a says so.
* **Energy balance and KPIs.** The rod's electricity is its own output with its own energy port, so the balance
  check sees it apart from the compressor's; the total electricity the meter reads includes it. A KPI reports the
  rod's electricity in kWh per year and its share of the heat pump's heat in percent.
* **Report lines.** The SCOP calibration factors in the component report change (they now scale the Carnot law).
* **Legacy cost and CO2 KPIs** are placeholders and are not checked.

## 7. Out of scope

Cooling beyond keeping hplib's EER until step d; air/air heat pumps; hybrid heat pump control; the SPF-based
calibration (hisim-f79l, waits on the frontend); wiring a brine machine for RenoVisor's ground-source heat pump
(hisim-ztt7, separate).

## 8. Questions for review

Every question was answered by the owner on 2026-11-07 (decisions 9 to 17 of §2); no question is open. The
questions stay here with their answers, so a reader of an earlier draft finds what became of each.

* **Q1 — Minimum-lift guard.** Proposed: a lift floor of 12 K (the smallest certified lift, A12/W24), for both
  source types. Alternatives: a COP cap (e.g. 8 for air) or no guard above a 1 K floor. Brine has no certified
  point below a 24 K lift (B0/W24); is 12 K acceptable there?
  *Decided (decision 9):* 12 K for both; brine extrapolates between 12 K and 24 K on rare mild days, stated in §3.1.
* **Q2 — Water/water units** (hplib group 3) have no Keymark data. Proposed: brine's parameters. Alternative:
  refuse water/water with a stated SCOP until data exists.
  *Decided (decision 10):* brine's parameters (η 0.568, d 4.50 K), flagged as an assumption in the docs.
* **Q3 — Named hplib models.** The law replaces the COP of every hplib model, a named manufacturer model too; its
  datasheet SCOPs then calibrate the pooled law. Acceptable, or keep hplib's own plane for named models?
  *Decided (decision 11):* the law replaces every model's COP; the datasheet SCOPs calibrate it.
* **Q4 — Capacity where hplib's plane switched to its rod** (step a only, until step d): the compressor's capacity
  at the switch, `P_el,fit`, continuous but low (about 4.8 kW of 8 kW at A0/W69). Acceptable as an interim?
  *Superseded (decisions 8 and 12):* the backup rod covers the shortfall above the switch, up to its rated power,
  so the house no longer loses the difference; `P_el,fit` stays the compressor's interim capacity until step d.
* **Q5 — Uncalibrated default.** Without a stated SCOP every machine becomes the pooled Keymark unit (air W35 4.46 /
  W55 3.32) instead of hplib's Generic plane (4.74 / 3.40). Goldens move with it. Agreed?
  *Decided (decision 13):* the pooled Keymark unit; the heat-pump goldens are re-blessed.
* **Q6 — Defrost targets and the 60 s / 900 s band.** A2 ×0.90 and a 4-7 % defrost heat share are the targets;
  what annual difference between the two modes is acceptable (proposed: 1 % of the heat pump's electricity)?
  *Decided (decision 14):* the 900 s mode within 1 % of the 60 s mode in yearly heat-pump electricity, checked in
  step c's PR.
* **Q7 — Start-up loss.** Which source for `t_start_loss` (EN 14825 Cdh test cycles, field data, AixLib)? Order of
  magnitude 1-2 min of full electrical power per start.
  *Decided (decision 15):* derived from the EN 14825 Cdh test cycles, consistent with the rating method.
* **Q8 — Brine.** The pooled brine fit gains little (10.0 → 9.6 %) and its hot-water COP is +13 % against EN 16147
  (29 units). Keep the pooled brine fit, or wait for step b's Cdh before judging?
  *Decided (decision 16):* keep the pooled brine fit now; re-judge after step b's cycling term.
* **Q9 — Cooling in step d.** hplib's EER plane goes with hplib. A Carnot-efficiency EER fitted to Keymark cooling
  data, or keep cooling out of the model until needed?
  *Decided (decision 17):* a Carnot-efficiency EER fitted to the Keymark cooling data, in step d.
