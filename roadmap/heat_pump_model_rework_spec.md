# Spec: the heat pump model gets a Carnot-efficiency COP, a part-load penalty, defrosting and a sourced capacity law

Status: draft for review. Decisions (1) to (7) taken by the owner on 2026-10-21 (§2); questions for review in §8.
Owner: Noah Pflugradt. Epic: hisim-heat-pump-model-rework-fpqz (P1); children .1 research (closed), .2 COP layer,
.3 defrost, .4 part load and cycling, .5 capacity law, .6 outdoor humidity. Evidence: hisim-x2mu (closed).

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
| water (groups 3, 6) | brine's | brine's | no Keymark data; liquid source like brine (question Q2) |

**Minimum-lift guard.** The law grows without bound as the lift shrinks (it turns negative where the source is
warmer than the outlet, e.g. A30/W25). The lift in the denominator is held at no less than `lift_floor` = 12 K, the
smallest lift any certified point has (A12/W24). Below that the law is not evidenced; holding the lift keeps the
COP at its value at the edge of the data (air: 8.15 at W24 uncalibrated) instead of extrapolating. Choice and
alternatives in Q1.

**Calibration.** `ScopCalibration` and `StandardizedSeasonalCop` are unchanged in method; they rate the machine
with the new COP. With a stated SCOP pair the calibrated machine still rates exactly at the stated SCOPs. Without a
stated SCOP the machine is the pooled Keymark unit: EN 14825 rates it at W35 4.46 / W55 3.33 (air; Keymark median
4.60 / 3.39) and 4.74 / 3.94 (brine; median 5.20 / 3.90). hplib's Generic plane rated 4.74 / 3.40 and 4.85 / 4.02.
The law replaces the COP of every hplib model, also a named manufacturer model (Q3).

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
  the first step of a run so the energy balance closes. `t_start_loss` is sourced in the step's PR (Q7).
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
  4-7 % of the seasonal heat on a German TRY (prototype on TRY: +5 to +8 % seasonal electricity). The two modes
  agree over a year within a band to be set in the PR (Q6).
* Certified COPs and the SCOP rating stay defrost-free (decision 3), so the calibration does not change.
* Brine and water sources: no defrost.

### 3.4 Step (d): capacity law and dropping hplib (child .5)

* The thermal power at full load becomes a sourced law relative to the rated capacity, e.g. linear in the source
  temperature and weakly falling with the outlet, from EN 14511 capacity tables of the Keymark set or AixLib's
  air-to-water records. Sizing stays: the machine is sized to the building's heating load at the design point.
* The minimum output (modulation floor) becomes a share of the rated capacity.
* hplib, its parameter tables and the result cache are removed; `CalculationRequest` and the 0.1 K grid with
  them. Cooling needs an EER law in the same step (Q9).
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
| b | Keymark A12/W24 and A12/W30 points with the rating's Cdh, by machine type | brine A12/W30 error from +15 % towards 0; air not worse |
| b | Starts × start-up loss in a full year, energy balance closes | reported |
| c | Humidity per weather source (test per source) | present, plausible range |
| c | Defrost: A2/84 % multiplier; seasonal defrost heat share on TRY; 60 s vs 900 s annual electricity; the buffer's energy balance with the defrost draw | ×0.90 ± 0.02; 4-7 %; agree within the band of Q6; balance closes |
| d | Capacity at the bivalent point vs certificates; run time; no hplib import left | median within ±10 %; not slower |

## 6. What moves

* **Goldens.** Every golden of a setup with `MoreAdvancedHeatPumpHPLib` moves at steps a, b (start-up loss), c
  and d. The coordinator blesses them per step; the PR lists which.
* **RenoVisor.** Every heat-pump calculation's electricity, SPF and costs move at steps a, c and d (b mostly for
  brine). Each such PR carries a heads-up note for the frontend (renovisorissues). The stated SCOP pair still
  rates exactly; the range of SCOP pairs the calibration can reach changes with the new shape, so a pair refused
  today may be accepted and the other way round (reported in step a).
* **Report lines.** The SCOP calibration factors in the component report change (they now scale the Carnot law).
* **Legacy cost and CO2 KPIs** are placeholders and are not checked.

## 7. Out of scope

Cooling beyond keeping hplib's EER until step d; air/air heat pumps; hybrid heat pump control; the SPF-based
calibration (hisim-f79l, waits on the frontend); wiring a brine machine for RenoVisor's ground-source heat pump
(hisim-ztt7, separate).

## 8. Questions for review

* **Q1 — Minimum-lift guard.** Proposed: a lift floor of 12 K (the smallest certified lift, A12/W24), for both
  source types. Alternatives: a COP cap (e.g. 8 for air) or no guard above a 1 K floor. Brine has no certified
  point below a 24 K lift (B0/W24); is 12 K acceptable there?
* **Q2 — Water/water units** (hplib group 3) have no Keymark data. Proposed: brine's parameters. Alternative:
  refuse water/water with a stated SCOP until data exists.
* **Q3 — Named hplib models.** The law replaces the COP of every hplib model, a named manufacturer model too; its
  datasheet SCOPs then calibrate the pooled law. Acceptable, or keep hplib's own plane for named models?
* **Q4 — Capacity where hplib's plane switches to its rod** (step a only, until step d): the compressor's capacity
  at the switch, `P_el,fit`. It is continuous but low (about 4.8 kW of 8 kW at A0/W69). Acceptable as an interim?
* **Q5 — Uncalibrated default.** Without a stated SCOP every machine becomes the pooled Keymark unit (air W35 4.46 /
  W55 3.33) instead of hplib's Generic plane (4.74 / 3.40). Goldens move with it. Agreed?
* **Q6 — Defrost targets and the 60 s / 900 s band.** A2 ×0.90 and a 4-7 % defrost heat share are the targets;
  what annual difference between the two modes is acceptable (proposed: 1 % of the heat pump's electricity)?
* **Q7 — Start-up loss.** Which source for `t_start_loss` (EN 14825 Cdh test cycles, field data, AixLib)? Order of
  magnitude 1-2 min of full electrical power per start.
* **Q8 — Brine.** The pooled brine fit gains little (10.0 → 9.6 %) and its hot-water COP is +13 % against EN 16147
  (29 units). Keep the pooled brine fit, or wait for step b's Cdh before judging?
* **Q9 — Cooling in step d.** hplib's EER plane goes with hplib. A Carnot-efficiency EER fitted to Keymark cooling
  data, or keep cooling out of the model until needed?
