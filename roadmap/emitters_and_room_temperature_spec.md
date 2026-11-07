# Spec: emitters and room temperature — the emitter has a size, the heating curve follows from it, the room gets what the emitter can give, and a generator that cannot reach the emitters' need runs anyway and is flagged

Status: decided (owner, 2026-11-03), amended 2026-11-07 (D3 and D5: the 1.1 margin sits on the new emitters' size as
well as on the curve; D10: Q1 of §9 decided, the energy manager's raise is decided with information about its own
effect), nothing is built; blocked (owner, 2026-11-07) until the step-length dependence of case B is
understood (§7, Appendix A.7). Owner: Noah. Epics: hisim-hydronics-fxix (bead hisim-s9h7),
hisim-renovisor-translator-epc (hisim-renovisor-base-emitters-dwm9, hisim-renovisor-translator-epc.1),
hisim-component-physics-4g9 (hisim-component-physics-4g9.37, hisim-airing-books-no-heat-uws3,
hisim-ems-raise-within-step-er89), hisim-heat-pump-model-rework-fpqz (hisim-heat-pump-outlet-limit-2vq7,
hisim-heat-pump-model-rework-fpqz.2). Builds on:
`system_docs/component_design_principles.md`, `system_docs/modelling_guide.md`, `roadmap/hydronic_coupling_spec.md`
(D25), PR #1142 (`ExplicitStateController`), PR #1165 (`cooling_enabled`). Replaces `roadmap/room_thermostat_spec.md`
of 2026-10-28 (the same file, renamed; §4.7 says what of it still holds). Code is cited against main at d3a1ddc3c.

## 1. Problem

Today the emitters have no size and the heating curve ignores how big they are.

- **Ideal delivery.** The building publishes `TheoreticalThermalBuildingDemand`, the heat that would hold its air
  node at the set point (ISO 13790 C.4, `hisim/components/building/building.py`). `HeatDistribution.i_simulate`
  delivers exactly that demand as long as the water is warmer than the room
  (`determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power`,
  `hisim/components/heat_distribution_system.py`). At the sizer twins' 0.27 kg/s, water 5 K above the room delivers
  5.6 kW; a real radiator 5 K above the room delivers a few per cent of its rating. So a house is never underheated by
  its emitters, the supply never has to rise because the radiators are too small, and the room sits at 20.00 °C
  (bead hisim-s9h7).
- **The circuit is sized to the house it simulates.** The circuit's design flow is the building's heating load over
  the emitter type's design spread (`HeatDistributionControllerInformation.water_mass_flow_rate_for`). The heating
  curve runs up to the emitter type's design supply on the design day (`HeatingCurveController.supply_temperature_set_in_celsius`,
  DIN/TS 18599-12:2021-04 eq. 127): 70 °C for radiators, whatever their size.
- **RenoVisor's results are wrong for packages that keep their radiators.** A package that renovates the envelope
  and keeps the old radiators runs them on a 70 °C curve. Measured (Appendix A, heat pump, full year at 900 s): old
  radiators sized for the unrenovated house, with a curve derived from their size, give a SCOP of 3.51–4.40; today's
  70 °C curve gives 2.19–2.22. The derived curve needs 40–55 % less electricity than today's curve: today's results
  overstate the heat pump's electricity of such packages by 68–120 % (today's electricity ÷ the derived curve's =
  1.68–2.20, Appendix A.5). By AGENTS.md that is P1: live results are wrong.

## 2. Decisions

| ID | Decision | Owner | Date |
|---|---|---|---|
| D0 | Whether the distribution circuit flows is decided by the room thermostat in the heat-distribution L1 controller; the generator decides only how hot | Noah | 2026-10-20 |
| D1 | The emitter has a rating, the emitter design load, a sized field defaulting to the building's heating load; the design flow follows from it | Noah | 2026-11-03 |
| D2 | A RenoVisor package without the emitter measure keeps the base-case emitters: the translator writes the base-case heating load as the emitter design load | Noah | 2026-11-03 |
| D3 | A package with the emitter measure sizes the new emitters to the package's own load at the new type's design point; amended 2026-11-07: at 1.1 × the package's load | Noah | 2026-11-03, 2026-11-07 |
| D4 | The circuit delivers the building's ideal demand, capped by the EN 442 law at full flow with the actual supply temperature | Noah | 2026-11-03 |
| D5 | The heating curve is derived from the emitter: the load fraction is scaled by (building heating load / emitter design load) × 1.1; amended 2026-11-07: the same 1.1 also sizes every new emitter, so a new emitter runs at its design point on the design day | Noah | 2026-11-03, 2026-11-07 |
| D6 | A generator that cannot reach the emitters' need runs anyway, and the result flags it; underheating is a KPI | Noah | 2026-11-03 |
| D7 | The delivered heat enters the building's air node, for every emitter type | Noah | 2026-11-03 |
| D8 | The room thermostat (P/PI valve in the heat-distribution controller) is a later step, bead hisim-component-physics-4g9.37 | Noah | 2026-11-03 |
| D9 | The low-temperature radiator gets its cost row and the translator's substitution by floor heating goes, after the sizing | Noah | 2026-11-03 |
| D10 | The energy manager decides its set-point raise with information about its own effect (Q1, option e): the budget is the surplus after the battery with the heat pump's own need, taken from the step's start, in place of its actual draw; PV mode switches on with hysteresis and sends an electrical power target the space-heating controller combines with its own ratio; no latch; the minimum run and idle times stay in the heat pump | Noah | 2026-11-07 |

**D0 — whether the distribution circuit flows is decided by the room thermostat in the heat-distribution L1
controller; the generator decides only how hot** (owner, 2026-10-20, bead hisim-pnw7). A prototype that let the boiler
controller decide on the pipe water's start temperature deadlocked. Consequence: the thermostat of D8 lives in the
heat-distribution controller; until it is built, that controller keeps opening the valve on the building's demand.

**D1 — the emitter has a rating, the emitter design load Q_n in W, a sized field on the heat-distribution controller
that defaults to the building's heating load; the design mass flow follows from Q_n and the emitter type's design
point** (owner, 2026-11-03). The heat distribution is sized properly for the base case, the house before any RenoVisor
measure. Consequence: an emitter has a size the room can run short of, and the circuit's flow is the emitter's design
flow, not the house's.

**D2 — a RenoVisor package without `heating_installation.type_of_system` keeps the base-case emitters: the translator
writes the base-case heating load as the emitter design load** (owner, 2026-11-03). Consequence: an envelope
renovation leaves the radiators oversized, and the derived curve (D5) lowers their supply.

**D3 — a package with the measure sizes the new emitters to the package's own load at the new type's design point**
(owner, 2026-11-03). That is standard practice: the load by EN 12831, the output by EN 442. Consequence, stated plainly:
replacing oversized old radiators with new 55/45 °C radiators sized to the smaller load can need a higher supply
(55 °C, 57.9 °C with the margin of D5) than keeping the old ones (about 43 °C, 45 °C with the margin).

*Amendment (owner, 2026-11-07):* the new emitters are sized at 1.1 × the package's load, Q_n = 1.1 · L, the 1.1
being the installer's margin on the emitter size (`EmitterDesign.EMITTER_SIZING_MARGIN`, §4.1). The rule is the
default of the sized field itself, so it reaches every emitter sized to its house: the translator with the emitter
measure (the field stays `AUTO`), the sizer twins and every setup that leaves the field `AUTO`. Kept emitters are not
sized by the rule: the translator writes the base-case heating load as Q_n (D2), unchanged. Reason: with the margin on
the curve alone (D5 as decided on 2026-11-03) new 55/45 °C radiators sized to the house needed 57.9 °C on the design
day, above the heat pump's rod-only switch of 56.8 °C at −7 °C (Appendix A.4), so flag (a) of §4.5 would have fired for
every heat pump on newly sized low-temperature radiators and the heat pump would have run hotter than the emitters were
designed for; and installer practice puts the margin on the emitter size, not on the curve. Consequence: the example
above reads 55 °C for the new 55/45 °C radiators against 45 °C for the kept ones.

**D4 — the circuit delivers the building's ideal demand, capped by the EN 442 law at full flow with the actual
supply temperature; the heat enters the air node as today** (owner, 2026-11-03). Measured: −0.3 to 0 % yearly heat
against ideal delivery, no runtime cost, 1.7 µs per call of the law (Appendix A.4, A.8). Consequence: a too-small
emitter or a too-low supply gives a colder room.

**D5 — the heating curve is derived from the emitter: the load fraction is multiplied by (building heating load /
Q_n) × 1.1, the 1.1 being an installer's safety margin** (owner, 2026-11-03). Why the margin: without it the law leaves
about 4 % reserve on the design day (Appendix A.4), the generators run below the curve (the boiler settles 1.5–3 K
under it, the heat pump switches on at curve − 5 K), and the curve follows the daily mean while nights are colder.
Consequence: oversized emitters run cooler; a correctly sized emitter runs 1.4–3.8 K above its design supply on the
design day (§4.3).

*Amendment (owner, 2026-11-07):* the curve keeps x_e = x · (L / Q_n) · 1.1, and the same 1.1 now also sizes every new
emitter (D3 amended: Q_n = 1.1 · L). For a new emitter the two cancel on the design day, x_e = x · (1 / 1.1) · 1.1 = x,
so the curve reaches exactly the type's design supply there: 55 °C for 55/45 °C, 35 °C for floor heating, 70 °C for
70/55 °C, with 10 % emitter surface in reserve. Kept old radiators (D2) keep the curve part's reserve as decided on
2026-11-03: 45.1 °C instead of 43.3 °C in the owner's example. Reason: the curve-only margin pushed new 55/45 °C
radiators to 57.9 °C on the design day, above the heat pump's rod-only switch of 56.8 °C at −7 °C, and contradicted
installer practice, where the margin sits on the emitter size; the question of 2026-11-03 had mislabelled the curve
margin as "the usual installer safety factor". Consequence: a new emitter runs at its design point on the design day,
not 1.4–3.8 K above it; the owner's three variants and their reasons are in §8.

*Correction (2026-11-07):* the boiler settles 0.5–3.4 K under the curve in Appendix A.5 (02.003 gas on r70o −0.5 K;
02.002 gas on r55 and on r70 −3.4 K), not 1.5–3 K as written above; the reasons for the margin stand.

**D6 — a generator that cannot reach the emitters' need runs anyway, and the result flags it** (owner, 2026-11-03).
RenoVisor compares packages, so a package with a heat pump on old 70/55 °C radiators is a result, not a refusal.
Consequence: the underheating KPIs and the "generator cannot reach" flags of §4.5.

**D7 — the delivered heat enters the building's air node, for every emitter type** (owner, 2026-11-03). Floor heat
into the 5R1C mass node is not physical: +10 to +14 % yearly heat in the measurement (Appendix A.8); radiant shares
would require comfort control on the operative temperature. Consequence: the thermostat of D8 reads an air
temperature that answers 0.84 K per kW within the step, which its measured settings account for.

**D8 — the room thermostat is a later step** (owner, 2026-11-03). Bead hisim-component-physics-4g9.37 keeps it, with
the measured settings of §4.7 and the prerequisites hisim-airing-books-no-heat-uws3 and
hisim-ems-raise-within-step-er89.

**D9 — the low-temperature radiator's cost row is added and the translator's substitution (written as floor heating)
dropped, after the sizing** (owner, 2026-11-03, bead hisim-renovisor-translator-epc.1, P2). With emitter sizing the
substitution distorts more: a package with low-temperature radiators would run at floor heating's 35 °C instead of
55 °C.

**D10 — the energy manager decides its set-point raise with information about its own effect: the budget is the
surplus after the battery with the heat pump's own need in place of its actual draw, the own need is taken from the
step's start, "PV mode" switches on with hysteresis and sends an electrical power target, and the heat pump's L1
space-heating controller combines that target with its own ratio; no latch; the heat pump's minimum run and idle times
stay in the heat pump** (owner, 2026-11-07, Q1 of §9, bead hisim-ems-raise-within-step-er89). Cause, stated plainly:
the energy manager raised the set point on any positive surplus after the battery, but the raise makes the heat pump
draw more; when the surplus is smaller than that extra draw, the decision undoes its own condition within the step, a
limit cycle. Measured (the er89 measurement of 2026-11-07, `/home/noah/hisim/agent-tmp/er89-measure/README.md` and
`README_de.md`, not committed; three house-years at 900 s, the heat-pump sizer twin with PV, battery and the P-valve
prototype): today 952 valve-moving forced steps, 2,383 real switching cycles and 38 kWh of grid electricity bought
during the raise. The rule:

1. **Budget.** The budget in W is the surplus after the battery, with the heat pump's own need in W (its electricity
   without the raise) in place of its actual draw. Battery first, the owner's intention: the priority chain
   `DEFAULT_WEIGHTS` / `control_electricity_component_iterative` stays as it is, the heat pump's own heating demand at
   rank 2 is served before the battery, and the raise is extra consumption that gets only what is left after the
   battery.
2. **Own need from the step's start.** The space-heating controller runs its own on/off logic with the unraised switch
   points on the step's start state and, above 600 s, the part-load ratio to the unraised target; the hot-water
   controller reports whether its charge would run without the raise. Taken from the step's start, the own need does
   not depend on the raise.
3. **Enable and target.** The energy manager enables PV mode when the budget in W ≥ the heat pump's minimum electrical
   power in W + a margin of 100 W, with hysteresis across steps (its previous step's decision as memory), and sends an
   electrical power target in W = the budget, clamped to [P_min, P_max]. The heat pump's L1 space-heating controller
   runs the heat pump at ratio = max(its own ratio, min(target / full-load electrical power, the ratio to the raised
   buffer limit)).
4. **The heat pump's powers.** The heat pump publishes its electrical power in W at full load and at minimum load,
   from the step's start temperatures, so both are constant within the step.
5. **No latch, no early publication.** A latch would freeze the iteration. The energy manager (L2) runs before the L1
   controller in a pass, so its first pass sees the previous step's own need and the second pass corrects it; the
   correction converges because the own need no longer depends on the raise.
6. **Minimum run and idle times stay in the heat pump**, as in real devices (compressor protection): a PV run cut short
   continues on grid electricity until its minimum run time ends, and the results show that honestly. The owner's
   principle: model real systems, not theoretical optimal operation.

Measured (round 2, the "e, own need frozen" row, no latch, the same three house-years): valve-moving forced steps
952 → 83 (64 of them unrelated to the raise), real switching cycles 2,383 → 52, grid electricity during the raise
38 → 2.7 kWh, PV self-consumption 39.54 → 39.27 %, heat-pump starts 1,745 → 1,316, hours below 19.5 °C 34.5 → 31.3;
modulation against plain on/off differs by ≤ 20 kWh of self-consumption (the storage, not the power, limits the raise).
What it cannot fix: about 7,800 forced steps in the three year runs that are unrelated to the raise (the valve, the
hydronics); they need their own work. Consequences: the prerequisite of D8 that Q1 held is decided (§4.7); the
implementation is one PR with all three steps, independent of PR 1 and 2 of this spec and built on main (§6); the
rejected options (a) to (d) and the controller-side run times are in §8; bead hisim-ems-raise-within-step-er89 is
retyped from a question to the feature (§10).

## 3. Terms

- **Emitter**: the radiators or the floor heating that hand the circuit's heat to the room. Three types
  (`HeatDistributionSystemType`): radiator, low-temperature radiator, floor heating.
- **Design point**: the supply and return temperature of an emitter type on the design day at a 20 °C room, and its
  exponent n (`EmitterDesign.for_type`, `hisim/components/heating_curve.py`): floor heating 35/28 °C, n = 1.1;
  low-temperature radiator 55/45 °C, n = 1.2; radiator 70/55 °C, n = 1.3.
- **Emitter design load, Q_n** (W): the emitter's rating at its design point. A sized field of
  `HeatDistributionControllerConfig`, by default the building's heating load. *Amended 2026-11-07:* by default
  1.1 × the building's heating load (a new emitter, D3); a kept emitter has the Q_n the translator writes (D2).
- **Emitter sizing margin** (dimensionless), 1.1, `EmitterDesign.EMITTER_SIZING_MARGIN` (`hisim/components/heating_curve.py`):
  the installer's margin, on the size of a new emitter (D3, amended 2026-11-07) and on the heating curve (D5).
- **New emitter**: one sized to its house by the sized field's rule (the field `AUTO`): the translator's with the emitter
  measure, the sizer twins', every setup's that writes no Q_n. **Kept emitter**: one with a written Q_n, the base-case
  emitters of D2.
- **Building heating load, L** (W): the building's heat loss on the design day, the fact `heating_load_in_watt` the
  building sizer contributes.
- **Design day**: the day at the heating reference temperature T_ref (°C), the outside temperature the emitter is
  designed for (−7 °C in the sizer twins).
- **Design flow, m_d** (kg/s): Q_n / (c · (T_sup,d − T_ret,d)), with c = 4180 J/(kg K) water's specific heat
  (`hydronics.Water`). Example: 16,270 W radiators at 70/55 °C: 0.259 kg/s.
- **Excess, θ** (K): T_sup − T_room, the supply above the room.
- **Log-mean excess, ΔT_lm** (K): (θ_sup − θ_ret) / ln(θ_sup / θ_ret), the EN 442-2 temperature difference of an
  emitter. At the design point: radiator 42.06 K, low-temperature radiator 29.72 K, floor heating 11.14 K.
- **EN 442 law**: Q = Q_n · (ΔT_lm / ΔT_lm,n)^n, the emitter's heat over its log-mean excess (EN 442-2, the
  characteristic equation).
- **Number of transfer units, u**: ln(θ_sup / θ_ret), the form the law is solved in (§4.2).
- **Ideal demand** (W): `TheoreticalThermalBuildingDemand`, the heat that holds the air node at the set point
  (ISO 13790 C.4); positive for heating, negative for cooling.
- **Capped delivery**: the ideal demand, limited by the EN 442 law at full flow (D4).
- **Heating curve**: the supply set point over the day's mean outside temperature (`HeatingCurveController`, an L2
  controller); **load fraction x** = (T_room − T_out,day) / (T_room − T_ref).
- **Heating season**: the days whose mean outside temperature is at or below the curve's threshold.
- **Base case**: the house of a RenoVisor request before any measure. **Package**: the request's list of measures.
  **Emitter measure**: `heating_installation.type_of_system`, which replaces the emitters.
- **Generator**: the heat pump, the boiler or the substation that heats the circuit's water.
- **Underheating**: the air temperature below 19.5 °C (the 20 °C set point − 0.5 K, without the energy manager's
  raise) in the heating season.
- **Heating rod**: hplib's electric backup in the heat pump; it runs alone at COP 1 above hplib's switch (56.8 °C
  outlet at −7 °C, Appendix A.4).
- **SCOP**: the heat pump's space-heating heat over its space-heating electricity for the year.
- **Air node**: the node of the building's 5R1C model (ISO 13790) that holds the room air; it has no heat capacity.
- **TABULA house**: a German residential archetype `DE.N.SFH.<class>.Gen.ReEx.001.<variant>`; `.001` existing,
  `.002` usual refurbishment, `.003` advanced refurbishment.

## 4. Design

### 4.1 Sizing (D1, D2, D3)

`HeatDistributionControllerConfig` gets the field `emitter_design_load_in_watt`, a `sized_field` whose rule is
`Size.HEATING_LOAD_IN_WATT` (the `#:` comment cites D1). The preset leaves it `AUTO`. The controller contributes it as
a fact, `emitter_design_load_in_watt`, in `SIZING_CONTRIBUTIONS` beside the emitter type and the mass flow.

The design flow follows from Q_n, not from the building's load: `water_mass_flow_rate_for(heating_load_in_watt=Q_n,
emitter_design=...)` in `HeatDistributionControllerInformation`; the contributed fact
`water_mass_flow_rate_in_kg_per_second` is that flow. `HeatDistribution` keeps reading the flow as a sized field.
`heating_load_of_building_in_watt` stays on the controller: it is the building's load L, which the curve and the
generators size on.

*Amendment (owner, 2026-11-07, D3 amended):* the rule of `emitter_design_load_in_watt` is
`EmitterDesign.EMITTER_SIZING_MARGIN * Size.HEATING_LOAD_IN_WATT` (a law scaled by a constant, `hisim/config/laws.py`),
so an emitter left `AUTO` is sized at Q_n = 1.1 · L, as an installer sizes a new emitter. The constant
`EMITTER_SIZING_MARGIN = 1.1` lives on `EmitterDesign` (`hisim/components/heating_curve.py`), the subcomponent both the
heat-distribution controller (the design flow) and the heating curve already read; the sizing rule and the curve's scale
(§4.3) read the same name, and the `#:` comment of the constant cites D3 and D5 with the amendment of 2026-11-07. Which
emitters the 1.1 sizes: every emitter whose field stays `AUTO` — the translator's with the emitter measure (§4.6), the
sizer twins' and every energy-system file's or Python setup's that writes no Q_n. Which it does not: a written Q_n is
taken as it is, so the kept emitters of D2 (the translator writes the base-case heating load) and any setup that states
its emitters' rating get no margin on the size. `describe` prints the law as `1.1 * heating_load_in_watt`. The heat
pump and the boiler keep sizing on L, not on Q_n.

The emitter's law constants are built once, in a frozen subcomponent (`component_design_principles.md` C) in the
heat-distribution module (one user): Q_n, ΔT_lm,n, n, K = Q_n / ΔT_lm,n^n, and m_d. Refused when built: Q_n ≤ 0, a
design point whose return is not above the room or not below the supply.

RenoVisor (bead hisim-renovisor-base-emitters-dwm9):

| Package | Q_n the translator writes | Design flow | Curve on the design day |
|---|---|---|---|
| without the emitter measure | the base-case heating load: the request's house without measures, sized by the same path as the package | the base case's | lower than the type's design supply (D5) |
| with the emitter measure | nothing: the field stays `AUTO`, the package's own load | the package's | the type's design supply × the margin |
| with the emitter measure, amended 2026-11-07 | nothing: the field stays `AUTO`, its rule gives 1.1 × the package's own load; the mapping report states the rating and the margin | 1.1 × the package's | the type's design supply exactly |

Example (TABULA 02.002, 6,030 W, radiators sized for 02.001 at 16,270 W): L / Q_n = 0.37, design flow 0.259 kg/s.
Example with the emitter measure (amended): 02.002 with new 55/45 °C radiators: Q_n = 1.1 · 6,030 W = 6,633 W, design flow
6,633 / (4180 · 10) = 0.159 kg/s, L / Q_n = 0.91, 55.0 °C on the design day.

### 4.2 Delivery: the ideal demand capped by the EN 442 law (D4)

`HeatDistribution.i_simulate` keeps its inputs (the demand, the supply temperature, the valve state, the room's
start-of-step temperature) and its outputs. While the valve is open for heating and water flows at m_d:

1. the law gives the emitter's capacity at full flow, Q_law(T_sup, T_room, m_d), and its return;
2. the delivered heat is Q = min(Q_ideal, Q_law), with Q = 0 when θ ≤ 0 (dead zone: the water is not warmer than
   the room);
3. the return is T_ret = T_sup − Q / (m_d · c). It never crosses the room, since Q_law < m_d · c · θ.

Behind a buffer the part-load ratio multiplies the flow and the heat as today. For cooling the same law applies with
the excess reversed (θ = T_room − T_sup) and the demand's sign, so cooling through radiators is limited too
(hisim-6irf).

**The law in NTU form.** With u = ln(θ_sup / θ_ret): θ_ret = θ · e^−u, Q = m c θ (1 − e^−u), and ΔT_lm = θ (1 − e^−u) / u.
Setting the two expressions of Q equal gives one equation in u:

    f(u) = n · ln u + (1 − n) · ln(1 − e^−u) − ln R = 0,    R = K · θ^(n−1) / (m c)

f increases strictly in u, so the root is unique. Newton runs on ln u from u₀ = R (exact for n = 1), with the
derivative df/d(ln u) = n + (1 − n) · u · e^−u / (1 − e^−u), each step clamped to ±2 in ln u, to a step tolerance of
1e-10, at most 20 iterations; the iteration not converging raises (`modelling_guide.md` §4: an iteration without a
solution fails). Then Q = m c θ (1 − e^−u) and T_ret = T_sup − Q / (m c). The flow's capacity m c θ is built in: at
u → ∞ the return reaches the room.

Measured (Appendix A.4): 1.7 µs per call, relative error below 2e-14 against a bracketed root finder, 2.8–3.5
iterations on average. The law is a pure `@staticmethod` with tests of its own (`coding_style.md` §1).

**Energy balance.** The circuit books m c (T_sup − T_ret) at both ends as today; the building receives Q through its
heat input. The post-run check closes every step.

### 4.3 The heating curve derived from the emitter (D5)

`HeatingCurveControllerConfig` gets two sized fields: the building's heating load (`Size.HEATING_LOAD_IN_WATT`, which it
already reads for the threshold) and the emitter design load (the controller's new fact). The class constant
`EMITTER_SIZING_MARGIN = 1.1` carries its source: owner decision 2026-11-03, an installer's safety margin. *Amended
2026-11-07:* the constant is `EmitterDesign.EMITTER_SIZING_MARGIN` (§4.1), one name for the margin on the size and on the
curve; its source line adds the amendment of 2026-11-07.

    x      = (T_room − T_out,day) / (T_room − T_ref)
    x_e    = x · (L / Q_n) · 1.1
    T_sup  = T_room + x_e^(1/n) · (T_sup,d − T_room)

`supply_temperature_set_in_celsius` takes the scale L / Q_n · 1.1 as an argument; the raise of the energy manager
lifts T_room as today; the cooling branch is unchanged. x_e is not clamped at 1 (today's code does not clamp x
either).

| Emitter, Q_n | House L | L / Q_n | Design-day supply without margin | With the 1.1 margin |
|---|---|---|---|---|
| radiator 70/55 °C, 16,270 W | 6,030 W | 0.37 | 43.3 °C | 45.1 °C |
| radiator 70/55 °C, 16,270 W | 3,596 W | 0.22 | 35.7 °C | 36.8 °C |
| radiator 70/55 °C, L | L | 1.00 | 70.0 °C | 73.8 °C |
| low-temperature radiator 55/45 °C, L | L | 1.00 | 55.0 °C | 57.9 °C |
| floor heating 35/28 °C, L | L | 1.00 | 35.0 °C | 36.4 °C |
| new radiator 70/55 °C, 1.1 L (amended 2026-11-07) | L | 0.91 | 66.5 °C | 70.0 °C |
| new low-temperature radiator 55/45 °C, 1.1 L (amended 2026-11-07) | L | 0.91 | 52.3 °C | 55.0 °C |
| new floor heating 35/28 °C, 1.1 L (amended 2026-11-07) | L | 0.91 | 33.8 °C | 35.0 °C |

The first row is the owner's example with the table's inputs, 16,270 W radiators and a 6,030 W house: 43.3 °C on the
design day without the margin, and at that supply and full flow the law gives 6,296 W for the 6,030 W need.
`scripts/law_check.py` runs the example rounded to 16 kW radiators and a 6 kW house: 43.51 °C and 6,262 W for a 6,000 W
need (Appendix A.4). Either pair of inputs gives the 4 % reserve of D5.

*Amendment (owner, 2026-11-07):* the three "L" rows with 73.8, 57.9 and 36.4 °C describe an emitter whose Q_n is written
as exactly L; since the amendment of D3 the default sizing gives 1.1 L, the last three rows, and a new emitter reaches
its design supply exactly on the design day. The first two rows, the kept old radiators of D2, are unchanged. The
measurement of Appendix A ran with Q_n = L and no curve margin, x · 1 · 1, which is the same curve as the amended rule's
x · (1 / 1.1) · 1.1 for a new emitter: its curve temperatures are the amended rule's for new emitters, and the amendment
only adds 10 % emitter surface at the same supply.

The design-day supply T_dd = T_room + (L / Q_n · 1.1)^(1/n) · (T_sup,d − T_room) is a pure function the translator
and the flag of §4.5 call; it is the curve at T_out,day = T_ref.

### 4.4 Heat entry (D7)

The building's heat input goes into the air node, as today (ISO 13790 C.11). No emitter-type split, no mass or
surface node share.

### 4.5 KPIs and flags (D6)

Building KPIs, every run, in the heating season:

| KPI | Definition | Unit |
|---|---|---|
| hours below 19.5 °C | steps with the air temperature < 19.5 °C (20 °C − 0.5 K, the raise not counted), as hours | h |
| kelvin-hours below 19.5 °C | Σ (19.5 °C − T_air)⁺ · Δt | K·h |
| minimum air temperature | the lowest air temperature | °C |
| 5th percentile of the air temperature | over the heating-season steps | °C |

"Generator cannot reach" KPIs and flag:

| ID | What | Where | Unit |
|---|---|---|---|
| (a) | static: T_dd of §4.3 against the generator's maximal supply: the boiler's `maximal_flow_temperature_in_celsius` (80 °C default, `generic_boiler.py`); for the heat pump hplib's rod-only switch, 56.8 °C at −7 °C (Appendix A.4), until hisim-heat-pump-outlet-limit-2vq7 gives a configured limit | result.json of RenoVisor: required supply, reachable supply, flag | °C, bool |
| (b) | heating-season hours with the circuit's supply below the curve − 2 K | heat-distribution KPI | h |
| (c) | hours and energy where the law at full flow, the actual supply and the room's start-of-step temperature give less than the ideal demand | heat-distribution KPI | h, kWh |
| (d) | heat pumps: the heating rod's share of the space-heating electricity, its hours, the maximal outlet temperature | heat-pump KPI | %, h, °C |

RenoVisor writes the underheating KPIs, (a) to (d) and the flag into result.json (names final in the PR of bead
hisim-renovisor-base-emitters-dwm9). The run never refuses: a heat pump on 70/55 °C radiators in an unrenovated house
runs with its rod and is flagged (Appendix A.6: SCOP 2.3–2.4, rod share 16 %, 40–60 % in January; the room is no
colder than with gas, because the heat-pump model has no space-heating outlet limit and the rod gives the full
rated power, bead 2vq7).

*Amendment (owner, 2026-11-07), flag (a):* with the new emitters sized at 1.1 L (D3 amended) T_dd is the type's design
supply: a heat pump on new 55/45 °C radiators has T_dd = 55.0 °C < 56.8 °C and is not flagged, and on new floor heating
35.0 °C. The flag still fires where it should: a heat pump on kept 70/55 °C radiators of an unrenovated house
(Q_n = L written by the translator, T_dd = 73.8 °C) and on new 70/55 °C radiators (T_dd = 70.0 °C), both above 56.8 °C.
Before the amendment, T_dd = 57.9 °C would have flagged every heat pump on newly sized low-temperature radiators.

### 4.6 RenoVisor (D2, D3, D9)

- The translator computes the base-case heating load (the request's house without measures, through the same
  building sizing as the package) and writes it as `HeatDistributionController.config.emitter_design_load_in_watt`
  when the package has no emitter measure. With the measure it writes nothing; the mapping report says which.
  *Amended 2026-11-07:* with the measure the field stays `AUTO` and its rule sizes the new emitters at 1.1 × the
  package's load (§4.1); the mapping report states the rating Q_n it resolved to, the margin 1.1 and its source (D3
  amended), beside the "base-case load kept" note of the other case.
- `_flow_temperature` (`hisim/renovisor/translate.py`) writes T_dd of §4.3 as the heat pump's
  `flow_temperature_in_celsius`, instead of the request's old generator's flow temperature.
- result.json gets the fields of §4.5. The capability probes get the pair "envelope package with and without the
  emitter measure"; the translation map is regenerated (`python -m hisim.renovisor map`).
- Afterwards (D9): the cost database gets the low-temperature radiator's row, `EmitterSubstitution` and the
  `not_implemented_yet.yaml` line go, and `LOW_TEMPERATURE_RADIATOR` is written as itself.

Heads-up on renovisorissues (`system_docs/results_and_interfaces.md` §5): every heat-pump and gas package without the
emitter measure changes its electricity or gas (Appendix A.5: SCOP 2.2 → 3.5–4.4; 7–12 % less gas through
condensing), and result.json gains fields.

### 4.7 The room thermostat, later (D8, D0)

Bead hisim-component-physics-4g9.37 builds the thermostat after this spec's two PRs. What the old spec decided and
still holds:

- **Ownership.** The room set points (`set_heating_temperature_in_celsius`, `set_cooling_temperature_in_celsius`)
  move from `BuildingConfig` to `HeatDistributionControllerConfig`; the controller publishes them each step; the
  sizing fact `set_heating_temperature_in_celsius` comes from the controller's `SIZING_CONTRIBUTIONS`.
- **Signal path.** The energy manager's raise and the night setback go to the thermostat (L1), the raise also to the
  heating curve (L2 to L2); the building publishes its air temperature; no L2 signal reaches physics; the demand
  becomes a report output.

      energy manager (L2) --BuildingTemperatureModifierInKelvin--> thermostat (L1) --valve opening--> HeatDistribution
                          \--(the same signal)--> heating curve (L2) --supply set point--> generator L1
      building (physics) --TemperatureIndoorAir--> thermostat (L1)
      HeatDistribution (physics) --HeatDeliveredInWatt--> building (physics)

- **The controller** is an `ExplicitStateController` (#1142): one immutable state, `decide` and `publish`.

Measured settings (Appendix A.9, A.10, A.11; 646 runs, 9 TABULA houses):

| Setting | Value | Measured |
|---|---|---|
| proportional band | 2 K | 1.5 K marginal with a heat pump (259 valve-moving forced steps in 14 weeks), 1 K fails (1,371 forced steps, 13.55 °C) |
| integral action | T_i = 1 h, updated once per step from the previous step's converged error, anti-windup (no integration into a saturated valve) | the band's position stops mattering; PI with heat into the air node lands within +0.8 % of ideal delivery (pvpiair: −0.1 to +0.8 %) |
| step within the iteration | a bracketed secant step on the residual P(u) − u, the bracket starting at [0, 1] | valve-moving forced steps → 0 |
| hold rule | the opening is held once a pass moves it by less than 1e-4 | outputs repeat exactly, the step converges |
| runtime | ×1.04–1.12 against ideal delivery | PI, year at 900 s ×1.04, January at 60 s ×1.12 (corrected 2026-11-07 from ×1.11; recomputed mean 1.117) |

Prerequisites: hisim-airing-books-no-heat-uws3 (airing resets the air temperature without booking heat; a thermostat
reading it heats for nothing, up to 15 % of the yearly heat) and hisim-ems-raise-within-step-er89 (the raise switched
within a step; decided 2026-11-07, D10: the energy manager decides it with information about its own effect, one PR
of §6).

Replaced parts of the old spec, one line each:

- the exponential UA law `T_ret = T_room + (T_sup − T_room) · exp(−UA / (m c))` (old §4.3, Q3 a): replaced by the EN 442
  law of §4.2 (D4);
- the plain two-point thermostat with a 1 K band (old §4.2, Q2): replaced by the PI valve with a 2 K band above;
- heat entry by emitter type into the mass and surface nodes (old Q4 b): rejected (D7);
- `HeatDistribution` dropping the demand input (old stage 2b): not now; the capped delivery reads it (D4); the
  thermostat replaces it later;
- the stages 1, 2a, 2b, 2c, 3 (old §6): replaced by §6 below;
- the open questions Q1–Q8: Q2, Q3, Q4 decided here; Q1 (which air temperature) measured: the pass's air temperature,
  before the airing reset (uws3); Q5 (report set points), Q6 (fold or split) and Q8 (electric heating) stay with bead
  4g9.37; Q7 (the generator's cue without a buffer) goes to pnw7's spec.

## 5. What changes and what does not

- **Results.** Every hydronic twin moves: the cap takes −0.3 to 0 % of the yearly heat (Appendix A.8, `cap`), and
  the derived curve with the margin moves every supply set point: an emitter sized to its house runs 1.4–3.8 K above
  its design supply on the design day (§4.3), oversized emitters far below it. 60 s results move too: the law and the
  curve have no step-length branch. *Amended 2026-11-07:* with the margin on the size too, an emitter left `AUTO` is
  sized at 1.1 × its house's load and its curve reaches exactly the type's design supply on the design day, so the sizer
  twins keep today's design-day supply and gain 10 % emitter surface and design flow (§4.3). Gas twins: slightly more or less condensing by the curve's move; heat-pump twins:
  the SCOP follows the supply.
- **Public interfaces.** New configuration field `emitter_design_load_in_watt`, new sizing fact, new KPIs (§4.5), new
  result.json fields; `water_mass_flow_rate_in_kg_per_second` keeps its name and is sized from Q_n; the curve's static
  function gets the scale argument. Heads-up on renovisorissues (§4.6).
- **Goldens and twins.** Re-blessed once through `golden-update.yml` with PR 1 of §6; every recorded twin whose
  result moves is re-recorded.
- **Unchanged.** The building's set points and demand (until 4g9.37); the buffer, the part-load rule, the generators'
  controllers; the fluid-loops tags (#1122); the heat pump's missing outlet limit (2vq7).

## 6. Implementation plan

Pull requests on main, in this order, one bead each (AGENTS.md):

| PR | Bead | Content | Tests | Results |
|---|---|---|---|---|
| 1 | hisim-s9h7 (components, P1) | the emitter design load sized field and its fact (§4.1), its rule `EMITTER_SIZING_MARGIN * Size.HEATING_LOAD_IN_WATT` on `EmitterDesign` (amended 2026-11-07); the EN 442 capped delivery in `HeatDistribution` (§4.2); the derived heating curve with the 1.1 margin (§4.3); the underheating KPIs and KPIs (b), (c) (§4.5) | an undersized emitter gives a colder room; the oversized case gives the derived supply; the law against a bracketed reference root (error < 1e-12, the dead zone, the return between room and supply, the design point reproduces Q_n); the table-generated tests of the curve's scale; an emitter left `AUTO` is sized at 1.1 · L and its curve gives exactly the type's design supply on the design day (amended 2026-11-07) | goldens re-blessed once |
| 2 | hisim-renovisor-base-emitters-dwm9 (renovisor, P1) | the base-case heating load in the translator; `_flow_temperature` writes T_dd; result.json fields, flag (a) and KPI (d); probes and the translation map (§4.6) | the probe pair with and without the emitter measure; the flag set for a heat pump on base-case 70/55 °C radiators in an unrenovated house; T-CAP, the mapping report and the map agree | RenoVisor results change; heads-up |
| independent | hisim-ems-raise-within-step-er89 (components, P2) | D10 in one PR, all three steps (owner decision), independent of PR 1 and 2, built on main: the heat pump publishes its electrical power in W at full and at minimum load from the step's start temperatures; the space-heating controller publishes its own need in W and the hot-water controller whether its charge would run without the raise; the energy manager's budget, the PV-mode enable with the 100 W margin and hysteresis, and the electrical power target; the L1 space-heating controller combines the target with its own ratio | a surplus smaller than the raise's extra draw gives one converged decision (the limit cycle is gone); the own need is the same with and without the raise; PV mode stays off below P_min + 100 W and holds with hysteresis across steps; the target is clamped to [P_min, P_max] and the ratio to the raised buffer limit; a PV run cut short continues until the heat pump's minimum run time ends | the heat-pump twins with PV and the energy manager move; goldens re-blessed |
| later | hisim-renovisor-translator-epc.1 (P2) | the low-temperature radiator's cost row; the substitution goes (D9) | the blocker test turns into the positive case | |
| later | hisim-heat-pump-outlet-limit-2vq7 (P2) | a maximal space-heating supply and a rated rod power; flag (a) reads the limit | a heat pump on undersized emitters delivers less and the room falls | |
| later | hisim-component-physics-4g9.37 (P1) | the room thermostat (§4.7), after uws3 and er89 | | every hydronic twin moves again |

## 7. Verification

- **Pure functions, tested alone**: the NTU law (§4.2) against `scipy.optimize.brentq` on a grid of θ, m and n; the
  design flow from Q_n; the curve's supply with the scale (the table of §4.3, including L / Q_n = 1 with the margin);
  T_dd.
- **The sizing margin (amended 2026-11-07)**: a heat-distribution controller with the field `AUTO` resolves Q_n to
  1.1 · L (`describe` prints `1.1 * heating_load_in_watt`); its design flow is 1.1 × the flow of Q_n = L; the curve at
  T_out,day = T_ref gives exactly the type's design supply for each of the three types (55.0 °C for 55/45 °C, 35.0 °C,
  70.0 °C), and T_dd of a new 55/45 °C radiator stays below hplib's 56.8 °C, so flag (a) is off; a written Q_n = L
  keeps the 73.8 / 57.9 / 36.4 °C rows.
- **Component tests**: an emitter half the building's load at the design supply leaves the room below 19.5 °C on the
  design day; an emitter at 2.7 × the load (02.002 on 02.001's radiators) gets 45.1 °C from the curve on the design
  day and holds the room; a supply below the curve − 2 K is counted by KPI (b); KPI (c) counts the steps where the cap
  binds.
- **Energy balance**: the post-run check closes every step at the circuit's ports.
- **Step length**: the same week at 60, 900 and 3,600 s on `gas_boiler_household` and
  `household_heatpump_building_sizer`: delivered heat and fuel or electricity within 1 % of the 60 s run
  (`modelling_guide.md` §3). Case B (the heat pump on 70/55 °C radiators sized to the unrenovated house, Appendix A.7)
  is outside that tolerance today: the rod's share of the week's electricity is about 40 % at 60 s and about 60 % at
  900 s, and the reached supply lies below the curve at 60 s and above it at 900 s. The owner decided (2026-11-07) that
  this spec is blocked until the step-length dependence of case B is understood; an investigation is running.
  Investigation: see bead hisim-rod-share-step-length-67f0.
- **Goldens**: every hydronic twin through the golden gate after the re-bless.
- **RenoVisor**: `tests/renovisor/` base tests, the `renovisor_probes` job, `verify --out DIR` exits 0, the flag
  probe of §6.

## 8. Rejected alternatives

- The exponential constant-UA law (old spec Q3 a): no emitter exponent; the EN 442 law costs the same (1.7 µs) and is
  the standard's own law (pvexp: +7.4 to +14.4 % heat with the P-valve, Appendix A.8).
- A P or PI room thermostat now: +1.5 to +21 % yearly heat until the airing (uws3) and the raise (er89) are fixed
  (Appendix A.8, `pv`, `pvpi`); later, D8.
- Floor heat into the mass node, radiator heat split 70/30 between air and surface: +10 to +14 % and +1 to +10 % yearly
  heat (Appendix A.8, `node%`: the radiator share is 1.1–10.2 % over the 20 `pv` radiator cases, 1.9–6.4 % on the four
  with a `pvair` twin; corrected 2026-11-07 from +2 to +6 %); not physical in a 5R1C model; D7.
- Sizing new emitters to the base-case load: not standard practice (EN 12831 load, EN 442 output); D3.
- Today's curve at the type's design supply whatever the size: overstates the heat pump's electricity by 68–120 %
  (the derived curve needs 40–55 % less, Appendix A.5; corrected 2026-11-07, the earlier text had the 40–55 % the
  wrong way round).
- A 1 K or 1.5 K band: fails or is marginal (Appendix A.9).
- Refusing a generator that cannot reach the emitters' need: RenoVisor needs the comparison; it runs and is flagged
  (D6).
- Clamping the heat-pump outlet in this spec: its own bead, 2vq7, with a sourced limit and a rod power.
- The 1.1 margin on the curve only, variant A, the D5 of 2026-11-03 (owner, 2026-11-07): new 55/45 °C radiators sized
  to the house need 57.9 °C on the design day, above hplib's rod-only switch of 56.8 °C at −7 °C, so flag (a) fires
  for every heat pump on newly sized low-temperature radiators and the heat pump runs hotter than the emitters were
  designed for; and the margin an installer applies sits on the emitter size, not on the curve.
- The 1.1 margin on the emitter size only, variant B (owner, 2026-11-07): the curve then reaches the design supply
  with no reserve beyond the law's about 4 % on the design day, while the generators run below the curve and the
  curve follows the daily mean (D5's reasons), which costs a few cold hours. Chosen: variant C, the margin on the size
  and on the curve, D3 and D5 as amended.
- Q1 (a), latching the raise at the step's first pass (owner, 2026-11-07): it freezes the iteration and hides the
  energy manager's switching from the valve, but not the switching itself (4,601 switching steps in the three
  house-years against today's 4,589) or its grid purchases (35.7 kWh against 38.4 kWh); D10.
- Q1 (b), hysteresis bands on the surplus (owner, 2026-11-07): a fixed band cannot match the heat pump's extra draw,
  so the limit cycle is only made short, not impossible; 242 valve-moving forced steps at best (2 kW band) over the
  three house-years, and the band keeps the raise on while it draws up to 1 kW from the grid; D10.
- Q1 (c), today's rule, the raise held under forcing only (owner, 2026-11-07): it is the limit cycle itself, 952
  valve-moving forced steps and 2,383 real switching cycles; D10.
- Q1 (d), on/off with the full-load threshold (owner, 2026-11-07): it works (93 valve-moving forced steps, about
  200 kWh less self-consumption over the three house-years because it waits for full-load surplus), but modulation is
  how real devices are controlled (SG-Ready, EEBUS); the owner chose (e); D10.
- Moving the heat pump's minimum run and idle times into the controller (owner, 2026-11-07): rejected; they stay in
  the heat pump as in real devices, and a PV run cut short continues on grid electricity until its minimum run time
  ends; D10, point 6.

## 9. Open questions

- **Q1. How the energy manager's set-point raise stops switching within a step** — *decided (owner, 2026-11-07):
  D10, option (e), the energy manager decides the raise with information about its own effect; the rejected options
  (a) to (d) are in §8, the implementation is the er89 row of §6, and bead hisim-ems-raise-within-step-er89 is the
  feature.* The question as posed (bead hisim-ems-raise-within-step-er89, P2): the raise depends on the heat pump's
  electricity, which depends on the valve it moves. Options: (a) latch the raise at the step's first pass (measured:
  709 → 83 valve-moving forced steps, lowest air 16.61 → 18.76 °C; contradicts the decision of 2026-10-10 in 4g9.38
  for this one signal); (b) hysteresis on the surplus, keeping same-iteration coupling (measured 2026-11-07: 952 → 242
  at best); (c) hold the raise under `force_convergence` only (today's behaviour); (d) and (e), the raise decided with
  information about its own effect, on/off at the full-load threshold or modulating (measured 2026-11-07, round 2).
  A prerequisite of D8, not of PR 1 or 2.

**Known limitation, recorded 2026-11-07 (no decision open here).** The heat pump runs about 2 K above the curve: its
charge target lies above the supply set point (#1083; measured on new 55/45 °C radiators, Appendix A.5: reached
53.1–53.9 °C against the curve's 51.7 °C on the run's coldest day, −4.0 °C daily mean). On a day at −7 °C the curve of a
new 55/45 °C radiator is 55 °C and the heat pump reaches about 57 °C, at hplib's rod-only switch of 56.8 °C; below −7 °C
the switch falls (51.5 °C at −10 °C, Appendix A.4) while the curve rises (58.2 °C at −10 °C), so the rod takes over on
the coldest days although flag (a) is off. The 2021 measurement did not reach −7 °C. The cause is hplib's generic fit,
whose COP is at or below 1 at W55/A−10; bead hisim-heat-pump-outlet-limit-2vq7 (a configured outlet limit and a rod
power of its own) and the Carnot COP shape of hisim-heat-pump-model-rework-fpqz.2 address it.

## 10. Beads

| Bead | Content | PR |
|---|---|---|
| hisim-s9h7 (P1, components) | PR 1 of §6: sizing, capped delivery, derived curve, KPIs | – |
| hisim-renovisor-base-emitters-dwm9 (P1, renovisor) | PR 2 of §6: base-case load, T_dd, result fields, flag, probes | – |
| hisim-renovisor-translator-epc.1 (P2, renovisor) | D9: the low-temperature radiator's cost row, the substitution goes | – |
| hisim-heat-pump-outlet-limit-2vq7 (P2, components) | the heat pump's maximal space-heating supply and rod power | – |
| hisim-heat-pump-model-rework-fpqz.2 (P1, components) | the Carnot COP shape; with 2vq7 it answers the limitation of §9 (the rod switch below −7 °C) | – |
| hisim-component-physics-4g9.37 (P1, components) | the room thermostat (§4.7) | – |
| hisim-airing-books-no-heat-uws3 (P2, components) | airing booked as a heat flow; prerequisite of 4g9.37 | – |
| hisim-ems-raise-within-step-er89 (P2, components) | D10, the er89 row of §6: the raise decided with information about its own effect (Q1 of §9, decided); prerequisite of 4g9.37 | – |
| hisim-floor-heating-60s-underheats-so91 (P3, components) | the floor-heating house underheats at 60 s with ideal delivery (Appendix A.9) | – |
| hisim-pnw7 (P3) | the generator's cue without a buffer; takes the old spec's Q7 | – |

## Appendix A. Measurement of 2026-11-03

### A.1 Setup

- Date: 2026-11-03. Base: main at d3a1ddc3c (`HiSim runs on pandas 3.1.0 ...`, #1157), in a worktree with
  `prototype_emitter.patch` applied. **The prototype is not committed**; it lives in
  `/home/noah/hisim/agent-tmp/emitter-runs/` with the run script (`scripts/run_one.sh`), the analysis
  (`scripts/analyse.py`, `scripts/decompose.py`, `scripts/summary.py`), every run's step log (`runs/`, 646 runs) and
  the tables (`table_all.csv`, `decompose.csv`, `scripts/summary.md`). Every number below is from
  `scripts/summary.md`.
- Twins: `household_gas_building_sizer` and `household_heatpump_building_sizer` (the heat pump behind a buffer with
  the energy manager's +2 K raise on PV surplus), the building code replaced per house, the emitter type on the
  controller, the emitter design load through the prototype's `EMITTER_DESIGN_LOAD_W`.
- Periods: the first January week and an October week at 60 and 900 s, the full year 2021 at 900 s. The design day
  of the year runs is the coldest daily mean, −4.0 °C; the derived curve's "expected" supply is at T_ref = −7 °C.
- The prototype's curve had no 1.1 margin (D5 was taken after the measurement): every "curve" and "expected" supply
  below is without it. With the amendment of 2026-11-07 (the margin on the size and on the curve) those supplies are
  exactly the amended rule's for the emitters sized to their own load (r70, r55, f35: x · (1 / 1.1) · 1.1 = x); the
  amendment adds 10 % emitter surface at the same supply, which this measurement did not run.

### A.2 Cases

| house | TABULA class | heating load W | W/m² | emitter | Q_n W | design flow kg/s | L / Q_n | design-day supply °C (no margin) |
|---|---|---|---|---|---|---|---|---|
| 02.002 | 1860–1918, usual refurb. | 6,030 | 50 | r70o (radiator 70/55, sized for .001) | 16,270 | 0.259 | 0.37 | 43.3 |
| 02.003 | 1860–1918, advanced refurb. | 3,596 | 30 | r70o | 16,270 | 0.259 | 0.22 | 35.7 |
| 06.002 | 1969–1978, usual refurb. | 5,804 | 48 | r70o | 11,790 | 0.188 | 0.49 | 49.0 |
| 06.003 | 1969–1978, advanced refurb. | 3,786 | 31 | r70o | 11,790 | 0.188 | 0.32 | 40.9 |
| 02.002 … 06.003 | as above | as above | | r55 (low-temperature radiator 55/45, own load) | = L | 0.086–0.144 | 1.00 | 55.0 |
| 02.002 … 06.003 | as above | as above | | f35 (floor heating 35/28, own load) | = L | 0.123–0.206 | 1.00 | 35.0 |
| 02.001 | 1860–1918, existing | 16,270 | 134 | r70 (radiator 70/55, own load) | 16,270 | 0.259 | 1.00 | 70.0 |
| 06.001 | 1969–1978, existing | 11,790 | 97 | r70 | 11,790 | 0.188 | 1.00 | 70.0 |
| 12.001 | 2016–, existing | 4,475 | 37 | f35 | 4,475 | 0.153 | 1.00 | 35.0 |

Emitter codes: r70o = the class's unrenovated radiators kept after the refurbishment (D2); r70, r55, f35 = radiators,
low-temperature radiators, floor heating sized to the house's own load (D3). Generators: gas = the condensing boiler,
hp = the hplib heat pump sized at A−7/W52 (16,270 W → P_th,ref 16,544 W; 11,790 W → 11,989 W).

### A.3 Variants

| Variant | Meaning |
|---|---|
| base | ideal delivery, heat into the air node, the derived curve (today's delivery) |
| today | ideal delivery, today's curve at the type's design supply |
| cap | the ideal demand capped by the EN 442 law at full flow (D4) |
| pv | P-valve, band 2 K, closes 0.5 K above the set point, heat enters by emitter type (radiator 70 % air / 30 % surface, floor into the mass node), raise latched |
| pvair, pvc, pvcair, pv0, pv0air | pv with heat into the air; band centred (closes 1 K above); closes at the set point; each with heat into the air |
| pvexp | pv with the old exponential constant-UA law |
| pvb1, pvb15 | centred band 1 K, 1.5 K |
| pvpi, pvpiair | centred band 2 K plus integral action T_i = 1 h; with heat into the air |
| suffix A | the valve reads the air temperature before the occupants' airing reset (uws3) |
| pvsurf, pvpisurfA | floor heat into the surface node |
| pvsb | pv with the bracketed secant step |
| pvmix, pvmixsb, pvpimixsbA | exploratory: pv with a mixing valve that blends return water into the buffer's water so the emitter's supply is at most the heating curve's set point (`EMITTER_MIX` in `prototype_emitter.patch`, `scripts/run_one.sh`); pvmixsb adds the bracketed secant step; pvpimixsbA is the centred 2 K band with T_i = 1 h, the secant step, the mixing valve and the pre-airing air temperature. They are in `table_all.csv` and `decompose.csv` and left out of the tables below (added 2026-11-07) |

### A.4 The law and the heat pump's limits (`scripts/law_check.txt`, `scripts/hplib_limits.txt`)

| Emitter | Q_n W | ΔT_lm,n K | max. rel. error vs brentq | mean Newton iterations | cost per call |
|---|---|---|---|---|---|
| radiator 70/55 | 16,270 | 42.06 | 1.5e-14 | 3.49 | 1.75 µs |
| low-temperature radiator 55/45 | 6,030 | 29.72 | 1.8e-14 | 3.40 | 1.71 µs |
| floor heating 35/28 | 4,475 | 11.14 | 7.8e-15 | 2.80 | 1.78 µs |

Example (`scripts/law_check.py`, the owner's example rounded to 16 kW radiators and a 6 kW house): the derived curve's
design-day supply 43.51 °C; the law at the design flow gives 6,262 W for a 6,000 W need (4 % reserve). With the table's
inputs, 16,270 W radiators and the 6,030 W house of 02.002, it is 43.3 °C and 6,296 W for 6,030 W, the same 4 %
(stated 2026-11-07; §4.3 quotes the table's pair).

hplib, the machine sized at A−7/W52 (16,270 W): rod-only switch (fitted COP ≤ 1) at the outlet 51.5 °C (−10 °C),
56.8 °C (−7 °C), 62.1 °C (−4 °C), 69.1 °C (0 °C), 72.6 °C (2 °C), 81.4 °C (7 °C); above it the rod gives the full
rated heat (16.5 kW) at COP 1.

### A.5 Case A: refurbished houses, full year at 900 s

Design day = coldest daily mean, −4.0 °C; r70 = today's 70/55 sized to the house with ideal delivery; eff = boiler
efficiency or heat-pump SCOP; expected = the derived curve at −7 °C, curve = its set supply on the run's design day,
reached = the circuit's supply while water flows (pv runs; base for r70); h, K·h, min, p5 in the heating season.

| house | gen | emitter | expected °C | curve °C | reached °C | Qdel base kWh | Gen base kWh | Gen pv kWh | Gen pvpi kWh | eff base | eff pv | h<19.5 pv | K·h<19.5 pv | h<19.5 pvpi | min pv °C | p5 pv °C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 02.002 | gas | r70o | 43.30 | 41.28 | 39.19 | 8162.44 | 8818.65 | 9183.88 | 8999.76 | 0.96 | 0.95 | 10.25 | 1.27 | 8.25 | 18.82 | 19.94 |
| 02.002 | gas | r55 | 55.00 | 51.73 | 48.33 | 8164.28 | 9104.69 | 9420.29 | 9292.57 | 0.94 | 0.94 | 10.00 | 0.74 | 2.25 | 19.19 | 19.85 |
| 02.002 | gas | r70 | 70.00 | 65.67 | 62.32 | 8166.78 | 9595.23 |  |  | 0.91 |  |  |  |  |  |  |
| 02.002 | hp | r70o | 43.30 | 41.28 | 43.64 | 8519.38 | 2343.22 | 2456.56 | 2391.57 | 3.93 | 3.93 | 4.50 | 0.37 | 15.25 | 19.22 | 20.09 |
| 02.002 | hp | r55 | 55.00 | 51.73 | 53.89 | 8513.57 | 2901.76 | 3009.69 | 2966.50 | 3.24 | 3.24 | 2.75 | 0.30 | 8.00 | 19.16 | 20.00 |
| 02.002 | hp | f35 | 35.00 | 33.48 | 35.88 | 8528.57 | 2015.10 | 2219.17 | 2226.23 | 4.49 | 4.52 | 62.50 | 8.37 | 63.75 | 18.97 | 19.70 |
| 02.002 | hp | r70 | 70.00 | 65.67 | 67.90 | 8500.06 | 4367.81 |  |  | 2.22 |  |  |  |  |  |  |
| 02.003 | gas | r70o | 35.66 | 34.30 | 33.77 | 3805.10 | 4070.89 | 4256.18 | 4123.39 | 0.97 | 0.97 | 2.75 | 0.12 | 11.50 | 19.38 | 20.04 |
| 02.003 | gas | r55 | 55.00 | 51.73 | 49.93 | 3810.22 | 4351.38 | 4506.52 | 4405.18 | 0.94 | 0.94 | 0.00 | 0.00 | 0.25 | 19.51 | 19.95 |
| 02.003 | gas | r70 | 70.00 | 65.67 | 63.13 | 3811.62 | 4633.90 |  |  | 0.91 |  |  |  |  |  |  |
| 02.003 | hp | r70o | 35.66 | 34.30 | 36.54 | 4015.30 | 999.93 | 1123.52 | 1033.73 | 4.40 | 4.44 | 0.00 | 0.00 | 3.00 | 19.53 | 20.21 |
| 02.003 | hp | r55 | 55.00 | 51.73 | 53.59 | 4010.60 | 1439.32 | 1557.86 | 1482.76 | 3.22 | 3.24 | 0.00 | 0.00 | 0.25 | 19.55 | 20.07 |
| 02.003 | hp | f35 | 35.00 | 33.48 | 35.74 | 4008.85 | 972.85 | 1098.82 | 1087.62 | 4.48 | 4.53 | 2.00 | 0.04 | 4.25 | 19.47 | 19.85 |
| 02.003 | hp | r70 | 70.00 | 65.67 | 67.90 | 4011.71 | 2198.41 |  |  | 2.21 |  |  |  |  |  |  |
| 06.002 | gas | r70o | 48.99 | 46.48 | 44.87 | 6838.95 | 7578.99 | 7903.19 | 7721.73 | 0.94 | 0.94 | 0.00 | 0.00 | 0.50 | 19.52 | 19.98 |
| 06.002 | gas | r55 | 55.00 | 51.73 | 49.96 | 6839.73 | 7696.26 | 7991.98 | 7841.78 | 0.94 | 0.94 | 0.00 | 0.00 | 1.25 | 19.51 | 19.93 |
| 06.002 | gas | r70 | 70.00 | 65.67 | 63.52 | 6842.62 | 8137.33 |  |  | 0.91 |  |  |  |  |  |  |
| 06.002 | hp | r70o | 48.99 | 46.48 | 48.81 | 7239.49 | 2293.41 | 2421.87 | 2344.91 | 3.51 | 3.52 | 0.50 | 0.00 | 3.50 | 19.50 | 20.11 |
| 06.002 | hp | r55 | 55.00 | 51.73 | 53.84 | 7232.90 | 2549.83 | 2680.94 | 2618.11 | 3.19 | 3.18 | 0.50 | 0.05 | 1.75 | 19.36 | 20.05 |
| 06.002 | hp | f35 | 35.00 | 33.49 | 35.83 | 7236.79 | 1745.62 | 1931.01 | 1929.63 | 4.45 | 4.49 | 19.75 | 2.23 | 40.50 | 19.12 | 19.77 |
| 06.002 | hp | r70 | 70.00 | 65.67 | 67.78 | 7230.86 | 3852.53 |  |  | 2.19 |  |  |  |  |  |  |
| 06.003 | gas | r70o | 40.87 | 39.06 | 37.44 | 3737.64 | 4078.60 | 4267.52 | 4133.22 | 0.96 | 0.96 | 1.25 | 0.06 | 1.00 | 19.42 | 20.05 |
| 06.003 | gas | r55 | 55.00 | 51.73 | 49.92 | 3740.05 | 4285.83 | 4453.64 | 4345.15 | 0.94 | 0.93 | 0.00 | 0.00 | 0.25 | 19.60 | 19.97 |
| 06.003 | gas | r70 | 70.00 | 65.67 | 62.68 | 3741.64 | 4570.73 |  |  | 0.91 |  |  |  |  |  |  |
| 06.003 | hp | r70o | 40.87 | 39.06 | 41.38 | 4000.32 | 1109.82 | 1281.75 | 1159.87 | 4.05 | 4.10 | 0.00 | 0.00 | 0.75 | 19.69 | 20.19 |
| 06.003 | hp | r55 | 55.00 | 51.73 | 53.09 | 3997.95 | 1452.78 | 1617.50 | 1516.83 | 3.21 | 3.23 | 0.00 | 0.00 | 0.00 | 19.68 | 20.09 |
| 06.003 | hp | f35 | 35.00 | 33.48 | 35.70 | 3985.52 | 984.49 | 1149.35 | 1140.34 | 4.44 | 4.52 | 2.50 | 0.10 | 7.75 | 19.42 | 19.85 |
| 06.003 | hp | r70 | 70.00 | 65.71 | 66.33 | 3992.72 | 2214.58 |  |  | 2.20 |  |  |  |  |  |  |

Key results (heat pump): old oversized radiators with the derived curve SCOP 3.51–4.40; new 55/45 radiators
3.19–3.24; floor heating 35/28 4.44–4.49; today's 70 °C curve 2.19–2.22, so the derived curve needs 40–55 % less
electricity than today's, and today overstates the electricity by 68–120 % (today's `Gen base` ÷ the derived curve's,
r70 against r70o: 02.002 1.86, 02.003 2.20, 06.002 1.68, 06.003 2.00; corrected 2026-11-07, the earlier text had the
40–55 % the wrong way round). Gas with the derived curve: 7–12 % less gas than on the 70 °C curve, through condensing.

Reading notes (2026-11-07):

- The "reached" column mixes variants: the r70 rows are from the `base` runs (no `pv` run exists for them), every
  other row from the `pv` run. The boiler settles 0.5–3.4 K under the curve (02.003 r70o −0.5 K; 02.002 r55 and r70
  −3.4 K); the heat pump runs 1.4–2.4 K above it.
- "eff" is the generator's own heat over its energy (boiler efficiency, heat-pump SCOP on `Qgen`), above the delivered
  heat over the energy because the buffer and the storage lose heat between the generator and the circuit: 02.002 hp
  r70o 3.93 against `Qdel base` / `Gen base` = 3.64.
- The heat pump delivers 4–7 % more heat than gas to the same house (`Qdel base`, r70o and r55 rows: 02.002 +4.4 %,
  02.003 +5.5 %, 06.002 +5.9 %, 06.003 +7.0 %) because the energy manager raises its set point by 2 K on PV surplus.
- PI against P comfort differs by case: in case A the PI valve often has more hours below 19.5 °C than the P valve
  (02.002 hp r70o 4.50 → 15.25 h; 02.003 gas r70o 2.75 → 11.50 h), in cases
  B and C fewer (A.6: 02.001 hp 163.25 → 110.75 h, 02.001 gas 384.75 → 179.25 h, 06.001 hp 62.00 → 41.50 h), except the
  floor-heating house 12.001 (19.50 → 29.75 h).

### A.6 Cases B and C: unrenovated houses on 70/55 radiators, and the 2016 house on floor heating, full year at 900 s

| house | gen | emitter | variant | curve °C | reached °C | hp outlet max °C | Qdel kWh | Gen kWh | eff | rod % | rod h | h<19.5 | K·h<19.5 | min °C | p5 °C | supply<curve−2K h | law<demand h | valve-moving forced |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 02.001 | hp | r70 | base | 65.67 | 68.06 | 79.32 | 30444.60 | 13824.63 | 2.38 | 16.61 | 240.50 | 0.00 | 0.00 | 20.00 | 20.00 | 280.25 | 149.50 |  |
| 02.001 | hp | r70 | pv | 65.67 | 68.04 | 74.80 | 32656.36 | 14649.34 | 2.39 | 16.07 | 233.75 | 163.25 | 52.77 | 17.36 | 19.74 | 302.75 | 105.50 | 313.00 |
| 02.001 | hp | r70 | pvsb | 65.67 | 68.04 | 74.41 | 32688.39 | 14652.86 | 2.39 | 16.07 | 233.75 | 168.75 | 55.20 | 17.36 | 19.73 | 315.75 | 107.00 | 0.00 |
| 02.001 | hp | r70 | pvpi | 65.67 | 68.03 | 74.69 | 32351.68 | 14612.62 | 2.38 | 16.08 | 232.25 | 110.75 | 43.78 | 17.28 | 19.85 | 310.50 | 115.75 | 366.00 |
| 06.001 | hp | r70 | base | 65.67 | 68.07 | 77.04 | 20768.23 | 9849.94 | 2.32 | 16.21 | 230.75 | 0.00 | 0.00 | 20.00 | 20.00 | 290.00 | 133.00 |  |
| 06.001 | hp | r70 | pv | 65.67 | 67.97 | 76.04 | 22017.43 | 10297.52 | 2.33 | 15.67 | 226.25 | 62.00 | 11.98 | 18.45 | 19.82 | 280.50 | 80.75 | 105.00 |
| 06.001 | hp | r70 | pvsb | 65.67 | 67.97 | 78.22 | 22032.53 | 10305.07 | 2.33 | 15.70 | 225.75 | 65.00 | 12.03 | 18.45 | 19.81 | 277.00 | 81.50 | 0.00 |
| 06.001 | hp | r70 | pvpi | 65.67 | 67.75 | 78.22 | 21704.32 | 10227.46 | 2.32 | 15.48 | 220.75 | 41.50 | 9.48 | 18.50 | 19.90 | 267.50 | 86.25 | 133.00 |
| 02.001 | gas | r70 | base | 65.67 | 62.20 |  | 29614.62 | 33644.11 | 0.91 |  |  | 0.00 | 0.00 | 20.00 | 20.00 | 762.50 | 252.50 |  |
| 02.001 | gas | r70 | pv | 65.67 | 62.00 |  | 31382.57 | 35575.99 | 0.91 |  |  | 384.75 | 104.06 | 17.98 | 19.51 | 694.00 | 181.75 | 5.00 |
| 02.001 | gas | r70 | pvsb | 65.67 | 61.92 |  | 31438.48 | 35701.37 | 0.91 |  |  | 456.75 | 146.41 | 17.96 | 19.46 | 379.00 | 181.00 | 0.00 |
| 02.001 | gas | r70 | pvpi | 65.67 | 61.93 |  | 31465.77 | 35674.26 | 0.91 |  |  | 179.25 | 68.18 | 17.93 | 19.75 | 689.50 | 188.50 | 17.00 |
| 06.001 | gas | r70 | base | 65.67 | 62.16 |  | 19821.45 | 22690.16 | 0.91 |  |  | 0.00 | 0.00 | 20.00 | 20.00 | 902.75 | 149.75 |  |
| 06.001 | gas | r70 | pv | 65.67 | 62.05 |  | 20767.89 | 23734.18 | 0.91 |  |  | 204.50 | 33.44 | 18.72 | 19.62 | 735.75 | 92.50 | 2.00 |
| 06.001 | gas | r70 | pvpi | 65.67 | 62.01 |  | 20730.17 | 23707.51 | 0.91 |  |  | 72.00 | 14.26 | 18.68 | 19.83 | 702.00 | 99.00 | 8.00 |
| 12.001 | hp | f35 | base | 33.48 | 35.73 | 51.11 | 5474.73 | 1326.06 | 4.45 | 0.00 | 0.00 | 7.00 | 1.19 | 19.08 | 20.00 | 238.75 | 285.75 |  |
| 12.001 | hp | f35 | pv | 33.48 | 35.82 | 51.13 | 6249.72 | 1479.64 | 4.49 | 0.00 | 0.00 | 19.50 | 1.69 | 19.27 | 19.75 | 275.25 | 55.75 | 0.00 |
| 12.001 | hp | f35 | pvsb | 33.48 | 35.81 | 51.13 | 6249.84 | 1479.16 | 4.50 | 0.00 | 0.00 | 19.50 | 1.69 | 19.27 | 19.75 | 280.50 | 55.25 | 0.00 |
| 12.001 | hp | f35 | pvpi | 33.48 | 35.50 | 50.65 | 6309.88 | 1487.00 | 4.51 | 0.00 | 0.00 | 29.75 | 2.66 | 19.11 | 19.78 | 271.75 | 55.00 | 0.00 |

Unrenovated houses on 70/55 °C with a heat pump: SCOP 2.3–2.4, rod share 16 %, outlet up to 79 °C; the room is no
colder than with gas (bead 2vq7). The columns "supply<curve−2K h" and "law<demand h" are KPIs (b) and (c) of §4.5.

### A.7 Case B, January week (heat pump on 70/55 radiators, own load)

| house | dt s | variant | curve °C (design day) | reached °C | reached max °C | hp outlet max °C | SCOP | rod % | h<19.5 | min °C | p5 °C |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 02.001 | 60 | base | 65.71 | 64.84 | 70.72 | 75.72 | 1.41 | 40.46 | 0.00 | 20.00 | 20.00 |
| 02.001 | 60 | pv | 65.71 | 64.15 | 70.74 | 75.74 | 1.39 | 37.05 | 7.32 | 19.13 | 19.52 |
| 02.001 | 900 | base | 65.67 | 68.06 | 68.25 | 73.25 | 1.31 | 59.63 | 0.00 | 20.00 | 20.00 |
| 02.001 | 900 | pv | 65.67 | 68.04 | 68.25 | 73.25 | 1.32 | 58.81 | 2.50 | 19.12 | 19.67 |
| 06.001 | 60 | base | 65.71 | 63.94 | 70.70 | 75.70 | 1.41 | 41.04 | 0.00 | 20.00 | 20.00 |
| 06.001 | 60 | pv | 65.70 | 64.77 | 70.74 | 75.74 | 1.39 | 39.46 | 1.90 | 19.19 | 19.62 |
| 06.001 | 900 | base | 65.67 | 68.07 | 68.25 | 73.25 | 1.32 | 60.02 | 0.00 | 20.00 | 20.00 |
| 06.001 | 900 | pv | 65.67 | 67.97 | 68.25 | 73.25 | 1.32 | 59.53 | 1.75 | 19.25 | 19.68 |

Stated plainly (2026-11-07): this case depends on the step length. The rod's share of the week's electricity is about
40 % at 60 s (37.05–41.04 %) and about 60 % at 900 s (58.81–60.02 %), and the SCOP 1.39–1.41 against 1.31–1.32. The
reached supply lies below the curve at 60 s (63.94–64.84 °C against 65.71 °C) and above it at 900 s (67.75–68.07 °C
against 65.67 °C). The curve's design-day value itself differs between the step lengths, 65.71 °C at 60 s against
65.67 °C at 900 s, and 06.001 at 60 s reads 65.70 °C in the `pv` run against 65.71 °C in `base`; neither difference is
explained here. The delivered heat and the electricity of this case are outside the 1 %
step-length tolerance of §7; the owner decided on 2026-11-07 that the spec is blocked until the cause is understood
(§7, "Investigation: see bead hisim-rod-share-step-length-67f0").

### A.8 Yearly heat against ideal delivery, by variant (900 s, % of the ideal-delivery heat)

The decomposition (`decompose.csv`) splits the extra heat into the band (the room's mean air temperature above
ideal delivery's), the node (heat booked into the mass or surface node and lost through the envelope), the
ventilation (airing) and a residual.

| variant | cases | min % | median % | max % |
|---|---|---|---|---|
| cap | 5 | −0.26 | −0.17 | −0.00 |
| pv | 25 | 3.68 | 5.97 | 21.43 |
| pvair | 5 | 0.23 | 3.54 | 8.01 |
| pvc | 5 | 10.68 | 11.36 | 25.84 |
| pvcair | 5 | 4.87 | 9.48 | 18.27 |
| pv0 | 5 | −1.26 | −0.35 | 2.82 |
| pv0air | 5 | −4.47 | −2.93 | −2.28 |
| pvexp | 2 | 7.41 | 10.93 | 14.44 |
| pvpi | 25 | 1.49 | 2.80 | 20.95 |
| pvpiair | 5 | −0.11 | 0.35 | 0.80 |
| pvA | 8 | 4.35 | 5.54 | 9.07 |
| pvpiA | 8 | 1.67 | 2.15 | 9.94 |
| pvpiairA | 8 | 0.19 | 0.44 | 0.61 |
| pvsurf | 3 | 8.38 | 12.53 | 19.48 |
| pvpisurfA | 3 | 5.83 | 6.07 | 7.57 |
| pvsb | 5 | 5.57 | 6.16 | 14.16 |

The five `cap` cases (D4):

| house | gen | emitter | Q_base kWh | ΔQ % | band % | node % | vent % | residual % | ΔT_air K |
|---|---|---|---|---|---|---|---|---|---|
| 02.001 | gas | r70 | 29614.62 | −0.25 | −0.25 | −0.00 | 0.00 | −0.00 | −0.02 |
| 02.002 | hp | r70o | 8519.38 | −0.12 | −0.12 | −0.00 | −0.00 | −0.00 | −0.01 |
| 06.002 | gas | r70o | 6838.95 | −0.00 | −0.00 | −0.00 | 0.00 | −0.00 | −0.00 |
| 06.002 | hp | r55 | 7232.90 | −0.17 | −0.17 | −0.00 | −0.00 | −0.00 | −0.01 |
| 12.001 | hp | f35 | 5474.73 | −0.26 | −0.26 | −0.00 | −0.00 | −0.00 | −0.01 |

Heat entry (D7), the node share of the extra heat: floor heating into the mass node 9.7–13.6 % (`pv` and `pvpi`,
f35), into the surface node 6.5–12.1 % (`pvsurf`); radiators 70/30 air/surface 1.1–10.2 % over the 20 `pv` radiator
cases of `decompose.csv` (the largest 06.003 hp r70o 10.2 %, 06.003 hp r55 8.0 %, 02.003 hp r70o 7.7 %; 1.1–6.4 % in
`pvpi`), 1.9–6.4 % on the four radiator cases that also ran `pvair`, where everything into the air gives 0.05–1.0 %
(corrected 2026-11-07: the earlier 2.0–6.4 % was those four cases only); everything into the air 0.05–0.6 % (`pvpiair`).

### A.9 Band study at 60 s (January and October weeks; pvb1 = 1 K, pvb15 = 1.5 K, pvc = 2 K centred, pv = 2 K closing 0.5 K above)

| gen | variant | runs | passes mean | passes max | valve-moving forced steps (sum) | (max per week) | G50 | G90 | closures per day | hourly swing | min air °C |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gas | pv | 12 | 6.59 | 16 | 0 | 0 | 1.70 | 3.35 | 4.96 | 0.07 | 19.03 |
| gas | pvb1 | 10 | 6.86 | 19 | 191 | 99 | 2.89 | 5.56 | 6.79 | 0.09 | 14.55 |
| gas | pvb15 | 10 | 6.68 | 18 | 8 | 3 | 1.68 | 3.60 | 6.03 | 0.08 | 19.33 |
| gas | pvc | 10 | 6.74 | 17 | 5 | 2 | 1.24 | 2.54 | 6.63 | 0.08 | 19.33 |
| hp | pv | 22 | 5.93 | 19 | 78 | 50 | 1.01 | 3.93 | 9.90 | 0.17 | 18.76 |
| hp | pvb1 | 14 | 6.73 | 18 | 1371 | 402 | 3.78 | 10.82 | 9.92 | 0.21 | 13.55 |
| hp | pvb15 | 14 | 6.53 | 19 | 259 | 52 | 2.29 | 6.87 | 9.11 | 0.19 | 18.94 |
| hp | pvc | 14 | 6.43 | 19 | 54 | 25 | 1.52 | 4.82 | 8.13 | 0.17 | 19.50 |
| gas | pv, paired weeks | 10 | 6.52 | 16 | 0 | 0 | 1.55 | 2.93 | 5.41 | 0.06 | 19.03 |
| hp | pv, paired weeks | 14 | 6.30 | 19 | 78 | 50 | 1.78 | 5.28 | 7.65 | 0.15 | 18.76 |

The `pv` rows above are not like-for-like with the band rows (noted 2026-11-07): `pv` averages every week of the set
(heat pump 22: the r70, r70o and f35 weeks of all houses; gas 12, with 06.001), the bands only the weeks that ran them
(heat pump 14: 02.001 and 06.001 r70, 02.002–06.003 r70o, 12.001 f35; gas 10: 02.001 r70, 02.002–06.003 r70o). The
two "paired weeks" rows recompute `pv` on exactly the bands' weeks (`table_all.csv`, summary.py's method: medians for
G50 and G90, means for the rest). The forced steps and the lowest air temperature are the same on both sets; the
closures per day (heat pump 9.90 → 7.65, gas 4.96 → 5.41) and G50/G90 (heat pump 1.01/3.93 → 1.78/5.28) are not, so
the band comparison reads from the paired rows.

G50, G90: the loop gain −d(u_P)/d(u) between consecutive passes (percentiles); above 1 the plain iteration would
oscillate, the secant step is what converges. The energy manager's raise read on every pass against latched, heat-pump
P-valve weeks: valve-moving forced steps 709 → 83, lowest air 16.61 → 18.76 °C (er89). With today's ideal delivery the
floor-heating house 12.001 falls to 19.03 °C at 60 s (bead so91).

### A.10 P-valve (pv) convergence per emitter, generator and step length (January and October weeks)

| emitter | gen | dt s | runs | passes mean | passes max | valve-moving forced steps (sum) | (max per week) | G50 | G90 | closures per day | hourly swing | min air °C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| f35 | hp | 60 | 10 | 5.28 | 18 | 0 | 0 | 0.00 | 0.01 | 14.04 | 0.20 | 19.23 |
| f35 | hp | 900 | 10 | 6.02 | 26 | 0 | 0 | 0.05 | 0.09 | 3.04 | 0.10 | 19.40 |
| r55 | gas | 60 | 8 | 6.31 | 16 | 0 | 0 | 1.35 | 2.39 | 6.96 | 0.06 | 19.23 |
| r55 | gas | 900 | 8 | 4.77 | 18 | 0 | 0 | 1.54 | 2.81 | 1.36 | 0.04 | 19.20 |
| r55 | hp | 60 | 8 | 5.94 | 19 | 0 | 0 | 1.48 | 3.67 | 9.43 | 0.13 | 19.51 |
| r55 | hp | 900 | 8 | 5.95 | 26 | 1 | 1 | 2.20 | 4.13 | 2.04 | 0.05 | 19.48 |
| r70 | gas | 60 | 4 | 6.98 | 16 | 0 | 0 | 2.49 | 5.16 | 1.43 | 0.07 | 19.03 |
| r70 | gas | 900 | 4 | 5.16 | 19 | 0 | 0 | 2.74 | 5.65 | 0.18 | 0.04 | 18.95 |
| r70 | hp | 60 | 4 | 7.01 | 19 | 61 | 50 | 2.87 | 6.77 | 1.29 | 0.14 | 18.82 |
| r70 | hp | 900 | 4 | 6.17 | 45 | 3 | 2 | 3.80 | 7.50 | 0.21 | 0.04 | 19.12 |
| r70o | gas | 60 | 8 | 6.39 | 16 | 0 | 0 | 1.39 | 2.76 | 6.73 | 0.06 | 19.03 |
| r70o | gas | 900 | 8 | 4.85 | 18 | 0 | 0 | 1.83 | 3.38 | 1.46 | 0.04 | 18.82 |
| r70o | hp | 60 | 8 | 6.20 | 19 | 17 | 5 | 1.73 | 5.21 | 9.02 | 0.14 | 18.76 |
| r70o | hp | 900 | 8 | 6.07 | 25 | 1 | 1 | 3.03 | 5.18 | 2.07 | 0.04 | 19.50 |

Passes per step, ideal delivery (base) against the P-valve: gas 60 s 6.42 / 6.48, gas 900 s 5.47 / 4.88, hp 60 s
5.78 / 5.93, hp 900 s 6.32 / 6.04.

### A.11 Runtime: loop wall time in s (median of 3 serial repeats) and ratio to ideal delivery

| house | gen | emitter | period | dt s | base | pv | pvpi | pv/base | pvpi/base |
|---|---|---|---|---|---|---|---|---|---|
| 02.001 | gas | r70 | jan | 60 | 5.28 | 5.67 | 6.22 | 1.07 | 1.18 |
| 02.001 | gas | r70 | jan | 900 | 0.34 | 0.34 | 0.36 | 0.99 | 1.04 |
| 02.002 | gas | r70o | jan | 60 | 5.60 | 5.87 | 6.28 | 1.05 | 1.12 |
| 02.002 | gas | r70o | jan | 900 | 0.36 | 0.34 | 0.38 | 0.96 | 1.07 |
| 02.002 | gas | r70o | year | 900 | 14.40 | 13.81 | 14.42 | 0.96 | 1.00 |
| 02.002 | hp | r70o | jan | 60 | 8.16 | 9.62 | 9.65 | 1.18 | 1.18 |
| 02.002 | hp | r70o | jan | 900 | 0.66 | 0.64 | 0.71 | 0.98 | 1.08 |
| 02.002 | hp | r70o | year | 900 | 27.73 | 29.88 | 29.97 | 1.08 | 1.08 |
| 12.001 | hp | f35 | jan | 60 | 8.12 | 8.00 | 8.00 | 0.98 | 0.99 |
| 12.001 | hp | f35 | jan | 900 | 0.64 | 0.66 | 0.66 | 1.03 | 1.02 |

Means: January 60 s pv ×1.07 (0.98–1.18), pvpi ×1.12 (0.99–1.18; recomputed 1.117, corrected 2026-11-07 from ×1.11);
January 900 s pv ×0.99, pvpi ×1.05; year 900 s pv
×1.02 (0.96–1.08), pvpi ×1.04 (1.00–1.08). Mean passes per step in this set: 60 s base 6.85, pv 6.87, pvpi 7.16;
900 s base 6.02, pv 5.65, pvpi 5.99. The cap (D4) adds no pass and no measurable time.
