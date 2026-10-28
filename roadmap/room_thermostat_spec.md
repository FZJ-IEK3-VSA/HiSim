# Spec: the room thermostat is the heat-distribution L1 controller, the building publishes its room temperature and receives heat through an emitter law

Status: draft, nothing is built; every question of §8 is open. Owner: Noah. Epic: hisim-component-physics-4g9
(bead hisim-component-physics-4g9.37, P1). Builds on: `system_docs/component_design_principles.md` (A, B1-B4),
`system_docs/modelling_guide.md` (§3, §5), `roadmap/hydronic_coupling_spec.md` (§4.6, D25 as amended 2026-10-22),
`roadmap/fluid_loops_spec.md` on `origin/hydraulic-circuits-spec` (#1122: D10, D16, D19, D20), PR #1165
(`cooling_enabled`, the two non-negative heat outputs), PR #1142 (`ExplicitStateController`), bead hisim-pnw7
(owner decisions of 2026-10-20). Line numbers refer to main at 7cc5f7a41 unless a branch is named.

## 1. Problem

Today the building decides how much heat it gets, and the heat distribution system delivers exactly that. Finding F1
of the component review of 2026-10-10 (bead hisim-component-physics-4g9.37), checked against main:

- **The building owns the room set points and the energy manager's raise.** `BuildingConfig` holds
  `set_heating_temperature_in_celsius` and `set_cooling_temperature_in_celsius` (`building/config.py:70-71`), and
  `Building.i_simulate` adds the energy manager's `BuildingTemperatureModifierInKelvin` to the heating set point
  (`building/building.py:742-744`). An L2 signal reaches physics (principle 3); the set point's owner is physics, not a
  controller (principle 4).
- **The building publishes a what-if heat.** `TheoreticalThermalBuildingDemand` (`building.py:427-432`) is the heat
  that would hold the air node at the set point, computed by ISO 13790 C.4: the air temperature without heating, the air
  temperature with a 10 W/m2 probe (`calc_indoor_air_temperature_ten_step_two`, `building.py:2203-2244`, the probe at
  `:2212`), and the linear interpolation to the set point (`:2246-2261`). It is a model-form quantity (principle 2): a
  building with an air capacity, a second zone or another solver gives it another meaning.
- **The heat distribution system delivers that demand.** `HeatDistribution.i_simulate`
  (`heat_distribution_system.py:437-556`) reads the demand and computes the return as `T_sup - Q_dem / (m c)`,
  clamped at the room temperature
  (`determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power`, `:649-716`).
  The emitter has no law of its own: whatever the supply temperature, the room gets what it asked for as long as the
  water is warmer than the room. Physics decides the room's heat (principle 3).
- **The controller switches on the same value.** `HeatDistributionController.next_valve_mode` (`:1265-1295`) opens
  the valve on a positive demand and closes it on exactly zero. It is a sign detector on a physics output, not a
  thermostat. (The sized copy of the set point and the second application of the modifier the review listed are gone
  since then; the controller reads the demand only.)
- **A second consumer of the demand.** `ElectricHeating` reads `TheoreticalHeatingDemand` and
  `TheoreticalHeatingEnergyDemand` as inputs (`generic_electric_heating.py:247-260`, read at `:528-529`) and delivers
  the demand up to its power; the same defect without a water circuit.

Measured on main (7cc5f7a41), the first week of January 2021, `gas_boiler_household` (no buffer, the boiler heats the
flow the heat distribution system pumps) and `household_heatpump_building_sizer` (a buffer in front of the
distribution system), export to CSV:

| Run | Steps | Delivered equals the demand exactly | Room temperature p5 / p50 / p95 | Valve switches | Mean valve-on conductance `Q / (T_sup - T_room)` |
|---|---|---|---|---|---|
| gas, 900 s | 672 | 100 % of heating steps, one step later (the no-buffer outputs lag one step) | 19.78 / 20.00 / 20.26 °C | 1 | 288 W/K (p5 199, p95 337) |
| gas, 60 s | 10,080 | 100 %, one step later | 19.96 / 20.00 / 20.04 °C | 1 | 288 W/K |
| heat pump, 900 s | 672 | 100 % in the same step | 20.00 / 20.00 / 20.00 °C (p99 22.00: the energy manager's +2 K) | 1 | 251 W/K (p5 157, p95 321) |
| heat pump, 60 s | 10,080 | 99.4 % | 20.00 / 20.00 / 20.00 °C | 7 | 290 W/K |

The room sits at exactly 20.00 °C whenever the circuit can follow, the valve opens once at the start of the week and
stays open, and the conductance the circuit shows is not a property of the emitter but whatever the demand needs
that step. Over a year the goldens agree: in `household_heatpump_building_sizer` (full year, 60 s) the theoretical
heating demand is 11,864 kWh and the distribution system's output 11,702 kWh; in `household_gas_building_sizer`
10,953 against 10,951 kWh; the minimum room temperature of the year is 18.3 to 19.2 °C in every hydronic twin.

Why it matters now: the generator's controller without a buffer (hisim-pnw7, hisim-boiler-circuit-flow-gap-g2mx)
needs a cue for *whether* the circuit runs, and the owner decided on 2026-10-20 that it is the room thermostat in the
heat-distribution L1 controller, never the standing pipe water's temperature. There is no such thermostat today.

## 2. Decisions

Already taken, recorded here because the design rests on them:

**D0 — whether the distribution circuit flows is decided by the room thermostat in the heat-distribution L1
controller; the generator decides only how hot** (owner, 2026-10-20, in bead hisim-pnw7). A prototype that let the
boiler controller decide on the pipe water's start temperature deadlocked. Consequence: the heat-distribution
controller becomes a thermostat (§4.2), and pnw7 and g2mx build on it (§5).

New decisions are taken from the questions of §8 and recorded here as D1, D2, ... with the owner's date.

## 3. Terms

- **Thermostat**: the heat-distribution L1 controller. It owns the room set points, reads the room temperature and
  commands the circuit's valve: open for heating, open for cooling, or shut.
- **Set point**: the room temperature the thermostat holds, in °C; a heating set point and a cooling set point.
- **Switching differential**: the band of a two-point thermostat, in K: it switches on below `T_set - band/2` and
  off above `T_set + band/2`.
- **Emitter**: the floor heating or the radiators that hand the circuit's heat to the room. Its **design point**
  (`EmitterDesign`, `heating_curve.py:68-113`) is the supply and return temperature on the design day: 35/28 °C for
  floor heating, 55/45 °C for low-temperature radiators, 70/55 °C for radiators, at a 20 °C room.
- **Emitter law**: the heat the emitter gives the room from the supply temperature, the mass flow and the room
  temperature; it replaces the demand as the thing the distribution system computes.
- **Theoretical demand**: the ISO 13790 C.4 what-if heat; it stays as a report output and is read by nothing in the
  simulation except the idealized heater, the measuring device built for it.

## 4. Design

### 4.1 Who owns what

| Quantity | Today | After |
|---|---|---|
| room set points | `BuildingConfig` | `HeatDistributionControllerConfig` (`set_heating_temperature_in_celsius`, `set_cooling_temperature_in_celsius`); published as outputs each step |
| the energy manager's raise, the night setback | an input of `Building` | an input of the thermostat; the heating curve keeps its own copy of the signal (L2 to L2) |
| whether the circuit flows, and heating or cooling | the sign of the building's demand | the thermostat, from the room temperature with a switching differential |
| how much heat the room gets | the building's demand, delivered by the circuit | the emitter law in `HeatDistribution`, from the supply temperature, the flow and the room temperature |
| the room temperature | published (`TemperatureIndoorAir`, `TemperatureIndoorAirAtStartOfStepInCelsius`) | unchanged; the thermostat reads it |
| `TheoreticalThermalBuildingDemand` and its splits | read by the circuit and its controller | a report output (Q5 says against which set point) |
| the sizing fact `set_heating_temperature_in_celsius` (read by the heating curve, `heating_curve.py:192`, and the heat pump's space-heating controller, `more_advanced_heat_pump_hplib.py:2971`) | contributed from `BuildingConfig` (`config/context.py:140`) | contributed by `HeatDistributionControllerConfig.SIZING_CONTRIBUTIONS` |

### 4.2 The thermostat: `HeatDistributionController`

An L1 controller (`L1ControllerComponent`) and an `ExplicitStateController` (#1142): its whole memory is one
`NamedTuple` state, `decide` returns the next state from the inputs, `publish` writes the outputs of a state, and a
forced pass republishes the state of the last free pass.

Inputs: the room temperature (Q1 says which output), the heating season of the heating curve, the modifier of the
energy manager or the night setback (optional, 0 K when unwired), and behind a buffer the buffer's end temperatures
for the part-load fraction, as today.

State: `valve_mode` (`OFF`, `HEATING`, `COOLING`), as today's `HeatDistributionControllerState`.

Decision (pure, a `@staticmethod` with a transition table and the table-generated tests of
`system_docs/coding_style.md` §3), with `T_heat = set_heating + modifier`, `T_cool = set_cooling`, the band `b`:

| From | Condition | To |
|---|---|---|
| OFF | `T_room < T_heat - b/2` and heating season | HEATING |
| OFF | `T_room > T_cool + b/2` and `cooling_enabled` | COOLING |
| HEATING | `T_room > T_heat + b/2`, or not heating season | OFF |
| COOLING | `T_room < T_cool - b/2` | OFF |
| any | otherwise | unchanged |

`cooling_enabled` is the controller's field of PR #1165 (the distribution system copies it as a sized field and tags
its loop `may_cool` with it, fluid loops D19). A thermostat whose set points overlap (`T_cool - b/2 <= T_heat + b/2`)
is refused when built.

Outputs: `State` as today (1 heating, -1 cooling, 0 shut), `HeatingSetPointInCelsius` and `CoolingSetPointInCelsius`
(the set points in force this step, the modifier included), `RequestedPartLoadRatio` as today. The part-load
fraction behind a buffer above the threshold keeps today's floor rule (the buffer may not end below the room's
start-of-step temperature); `publish_forced` republishes it as `publish_fraction` does today.

The modifier: the energy manager's `BuildingTemperatureModifierInKelvin` and the night setback's output are wired to
the thermostat's one modifier input by default connection; the building's input goes. A setup with both would need an
L2 that sums them; none exists today (no energy-system file or setup wires the night setback).

### 4.3 The emitter law: `HeatDistribution`

The distribution system keeps its ports (`WaterTemperatureInput`, the flow, `State`, the room temperature; after #1165
the two outputs `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt` beside the signed `ThermalPowerDelivered`) and
loses `TheoreticalThermalBuildingDemand`. While the valve is open and water flows it computes, with `m` the mass flow
in kg/s, `c` water's specific heat, `T_sup` the supply, `T_room` the room temperature:

    T_ret = T_room + (T_sup - T_room) * exp(-UA / (m c))
    Q     = m c (T_sup - T_ret)                                   (positive heating, negative cooling)

the exact solution of a counter-flow exchanger against a room at constant temperature (the form `MixedNode` and the
hydronics module already use for an exact step). `UA` in W/K is sized once at the emitter's design point: the design
flow `m_d` (today's sized `water_mass_flow_rate_in_kg_per_second`, the heating load over `c` times the design lift)
and the design temperatures at the 20 °C design room:

    UA = m_d c ln((T_sup,d - 20) / (T_ret,d - 20))

| Emitter | Design point | `UA / Q_load` | Worked example, `Q_load` 7,781 W (the sizer twins) |
|---|---|---|---|
| floor heating | 35/28 °C | `ln(15/8) / 7 K` = 0.0898 /K | 699 W/K; at 34.2 °C supply and 20 °C room (the heat-pump week's median) 7.4 kW, against a median demand of 3.3 kW: a duty cycle near 45 % |
| low-temperature radiator | 55/45 °C | `ln(35/25) / 10 K` = 0.0336 /K | 262 W/K |
| radiator | 70/55 °C | `ln(50/35) / 15 K` = 0.0238 /K | 185 W/K |

So the emitter delivers the heating load on the design day at its design temperatures by construction, and on a
milder day more than the room needs at the heating curve's supply: the thermostat trims it by cycling, as the real
pair does. The law is a subcomponent (`hisim/subcomponents/emitters.py`, `DesignPointEmitter`, a frozen dataclass
built once from `EmitterDesign` and the design flow; `heat_in_watt(...)` returns a `NamedTuple` of return temperature
and heat), tested alone. Q3 offers the alternatives: the EN 442 power law with the emitter exponent, and the existing
screed model.

Valve shut, or the water not on the right side of the room (`T_sup <= T_room` while heating, `>=` while cooling):
no heat, the return is the supply. The free-convection decay of the standing water (`:596-647`) and the pipe water's
mass (8.8 m of 16 mm pipe per m2 of floor, `:619-624`) stay as they are for hisim-pnw7 and
hisim-pipe-water-free-convection-6vdp; they are not the emitter law (Q3 says why).

### 4.4 The building

- Loses the set points, the modifier input and the decision; keeps the 5R1C model and its outputs.
- Receives heat through its inputs as today (`ThermalPowerDelivered`; after D20 of the fluid loops spec the two
  tagged inputs `HeatReceivedInWatt` and `HeatRemovedByCoolingInWatt`).
- Where the heat enters is the building's own business, but it decides the thermostat's behaviour: ISO 13790 puts the
  whole heating on the air node (`calc_temperature_of_the_inside_air_in_celsius`, `building.py:1988-2015`, eq. C.11),
  which has no heat capacity, so the air answers a switch within the step. Measured on the sizer twins' building
  (`preset_german_single_family_home`, 121.2 m2, `H_ve` 62 W/K, `H_is` 1,882 W/K): **0.843 K per kW of delivered
  heat, at any step length.** A floor heating at 7.4 kW would move the air by 6.2 K within the step it switches on,
  twelve times a 0.5 K band. Q4 decides where the emitter's heat enters (floor heating into the thermal mass, whose
  165 kJ/(m2 K) of the medium class, 20 MJ/K here, rises 0.33 K per 900 s step at 7.4 kW).
- `TheoreticalThermalBuildingDemand`, its heating and cooling splits and the energy outputs stay as report outputs,
  computed against the set points of Q5; `calc_theoretical_thermal_building_demand_for_building` is unchanged.
- The comfort KPIs (`Temperature deviation ... below set temperature`, `Degree-hours ... more than 1.0 K below its
  heating set temperature`, `building.py:1402-1520`) read the set point of Q5 per step, so a raise or a setback counts
  as what the residents asked for in that step.
- Window opening (`occupants_open_the_windows`) is occupant behaviour, not a thermostat setting; it keeps a threshold of
  its own (Q5).

### 4.5 The signal paths

    energy manager (L2) --BuildingTemperatureModifierInKelvin--> thermostat (L1) --State--> HeatDistribution
                        \--(the same signal)--> heating curve (L2) --supply set point--> generator L1
    night setback  (L2) --modifier--> thermostat (L1)
    building (physics) --TemperatureIndoorAirAtStartOfStepInCelsius--> thermostat (L1)
    HeatDistribution (physics) --HeatDeliveredInWatt / HeatDrawnForCoolingInWatt--> building (physics)

No L2 signal reaches physics, every set point has one owner, the building's interface carries temperatures and heat.

### 4.6 The electric heating

`ElectricHeating` delivers the demand up to its power (`generic_electric_heating.py:528-529`). It gets the same cure in
its own bead (Q8): `ElectricHeatingController` becomes the thermostat (the same transition table, the same set
points), and the heater delivers its rated space-heating power while commanded. Not a water circuit, no emitter law.

## 5. Interaction with the fluid loops (#1122) and pnw7

- **Fluid loops.** Unchanged by this spec: the distribution loop's tags (D16 `pumps_the_distribution_loop`, D19
  `may_cool` from `cooling_enabled`), D20's two non-negative outputs read by the building's two inputs. The emitter law
  feeds those outputs; the heating-only check of D10/D24 holds because a heating-only thermostat never opens for
  cooling. Stage 3 of that spec removes `PositionHotWaterStorageInSystemSetup`; this spec does not touch it.
- **pnw7 and g2mx (chains without a buffer).** The thermostat gives the generator's controller its cue for *whether*
  without reading the standing water: the circuit flows when the valve is open, and the distribution system publishes
  that flow (`WaterMassFlowHDS`, a physics port). The generator's L1 fires when the circuit flows and the supply set
  point of the heating curve is not met, and books on the pipe water's start-of-step temperature once pnw7 makes the
  pipe water a start-of-step node. What pnw7 then needs from this spec: the valve state and the flow as the cue (Q7),
  nothing else. The deadlock of the prototype cannot recur: the flow no longer waits for the water to cool.
- **The heat pump's space-heating controller** keeps deciding on the buffer's start-of-step temperature against the
  heating curve's set point; nothing changes for it in chains with a buffer.

## 6. Stages and pull requests

Small PRs, each one bead, stacked (AGENTS.md); the goldens are re-blessed through `golden-update.yml` where results
move, and every recorded twin that moves is re-recorded.

| Stage | PR | Content | Results |
|---|---|---|---|
| 0 | this spec | the design and the decisions of §8 | none |
| 1 | emitter subcomponent | `hisim/subcomponents/emitters.py` with `DesignPointEmitter` (Q3's law), its design-point sizing, unit tests of the law and of the sizing (design day reproduces the load; the return never crosses the room; cooling sign) | none, not wired |
| 2a | ownership moves | the set points, the modifier input and the sizing fact move from `BuildingConfig` to `HeatDistributionControllerConfig`; the controller publishes the set points; the building computes its report demand and comfort KPIs from them (Q5); the energy manager and the night setback re-pointed; the RenoVisor translator writes the new target (§6.1); the controller becomes an `ExplicitStateController` (after #1142 lands) still switching on the demand | KPIs within rounding: the same set point reaches the same demand; goldens re-blessed for the shuffled columns |
| 2b | the thermostat and the emitter law | the transition table of §4.2 on the room temperature (Q1, Q2); `HeatDistribution` computes from the emitter law and drops the demand input; the building's receiving node (Q4, or stage 2c) | every hydronic twin moves: delivered heat, room temperature band, valve cycling, generator starts; goldens re-blessed; twins re-recorded; heads-up on renovisorissues |
| 2c | the receiving node | if Q6 separates it: the fraction of the received heat per node by emitter type, the emitter type a sized field of the building | moves again |
| 3 | the electric heating | §4.6 under its own bead (Q8) | the electric-heating twin moves |
| then | pnw7, g2mx | the generator's cue without a buffer (§5) | their own specs |

### 6.1 RenoVisor

The translator writes `building.set_heating_temperature_in_celsius` from the request
(`hisim/renovisor/translate.py:1323-1330`, `Targets.SET_HEATING_TEMPERATURE` at `:820`); the capability document bounds
it (`capabilities.py:747`); the measure that sets it (`apply.py:55`) names the request's path and stays. Stage 2a moves
the target to `HeatDistributionController.config.set_heating_temperature_in_celsius`, regenerates the translation map
(`python -m hisim.renovisor map`) and the capability document, and the `renovisor_probes` tests run in their CI job.
Stage 2b changes every RenoVisor result (the room is no longer pinned at the set point); its heads-up names the new
room-temperature band and the moved KPIs.

## 7. Verification

- **Unit tests of the pure functions**: the transition table (table-generated, one test per row, including the
  refusals); the emitter law (design day reproduces the load within 1e-9; `T_ret` between `T_room` and `T_sup`;
  the cooling sign; zero flow gives zero heat); the sizing per emitter type (the table of §4.3).
- **The controller**: `tests/test_explicit_state_controllers.py` of #1142 finds no memory outside the state; a forced
  step commits the state of its last free pass.
- **Energy balance**: the distribution system and the building declare their ports as before; the post-run check
  closes every step (sender and receiver book `m c (T_sup - T_ret)`).
- **Step length**: the same week at 60, 900 and 3,600 s on `gas_boiler_household` and
  `household_heatpump_building_sizer`: delivered heat and fuel or electricity within 1 % of the 60 s run (modelling
  guide §3), the room temperature's degree-hours below the band stated per step length; any larger deviation is a
  decision for the owner, never a widened tolerance.
- **Comfort**: over the full year of the two twins, the degree-hours more than 1 K below the heating set point stay
  below a bound the owner sets after stage 2b's first measurement (today 0 to 1.2 °C h, a number the pinned room
  makes meaningless).
- **Goldens**: every hydronic twin at one week 60 s, July week 60 s, full year 60 s, one week 900 s and full year 900 s
  through the golden gate.
- **RenoVisor**: `tests/renovisor/` base tests and the `renovisor_probes` job; `verify --out DIR` exits 0.

## 8. Open questions

- **Q1. Which room temperature does the thermostat decide on?**
  (a) `TemperatureIndoorAirAtStartOfStepInCelsius`, the committed start-of-step value the controller already reads for
  its part-load floor. It cannot cycle within the step: the decision of a step never depends on the heat of the same
  step. (b) `TemperatureIndoorAir` of the current pass, with the switching differential and a hold under
  `force_convergence`. The air node has no capacity, so the pass's own heat moves it by 0.84 K/kW (§4.4), more than
  any plausible band; the decision would flip between passes and the simulator would force steps.
  **Recommendation: (a)**, the pattern of the buffer controllers (owner, 2026-10-15) and the solar controller
  (2026-10-11); the owner's rejection of start-of-step deciding as a *general* rule (2026-10-10) leaves it open per
  controller, and here the edge the decision sits on is moved by its own effect.
- **Q2. The switching differential.** A two-point room thermostat's differential is in the order of 0.5 to 1 K
  (EN 60730-2-9 defines and tests it; manufacturers state about 0.5 K for electronic and about 1 K for mechanical
  thermostats). In the code: the simple air conditioner's controller uses `deadband_k` 0.5 K around its set point
  (`simple_air_conditioner.py:547-549`). Options: (a) a band of 1 K (`T_set` ± 0.5 K); (b) 0.5 K (± 0.25 K);
  (c) 2 K (± 1 K), the proportional band of a thermostatic radiator valve (EN 215). A configuration field with the
  chosen default and its source in the `#:` comment. **Recommendation: (a)**, 1 K, the same half-band as the air
  conditioner; the width hardly changes the energy, only the cycling period, and 2b's measurement reports the period.
- **Q3. The emitter law.** (a) The exponential exchanger law of §4.3 with `UA` sized at the design point: closed form,
  one parameter whose source is the design table already in `EmitterDesign` plus the sized heating load, no
  iteration. (b) The EN 442-2 power law `Q = Q_d (dT_lm / dT_lm,d)^n` with the emitter exponent `n` of `EmitterDesign`
  (1.1, 1.2, 1.3), the return found per pass by a few Newton steps on `m c (T_sup - T_ret) = Q`; more faithful at low
  over-temperature (at half the design over-temperature a radiator gives 0.5^1.3 = 41 % of its design heat instead of
  50 %), at the price of an iteration in the hot path. (c) The screed model of `:596-647` (40 mm screed at 1.4 W/(m K),
  5.8 W/(m2 K) screed to still air, 16 mm pipe, 8.8 m/m2): its conductance is 2.48 W/K per m2 of floor, 300 W/K for
  121 m2, which at the floor heating's design over-temperature of 11.5 K gives 3.5 kW, under half the 7.8 kW load; it is
  not the emitter law, and it says nothing about radiators. **Recommendation: (a)** now; (b) as a follow-up bead if 2b's
  measurement shows the duty cycle of the radiator twins matters for a KPI.
- **Q4. Where the emitter's heat enters the building.** (a) The air node, as today: the thermostat sees 0.84 K/kW
  within the step and a floor heating cycles every step at 900 s. (b) By emitter type, a sized field of the building
  from the heat-distribution controller's fact `heat_distribution_system_type`: floor heating into the thermal mass
  node (the floor is part of the mass; ISO 13790 C.2 allows the heating's radiative share on the surface and mass
  nodes instead of the air node, and ISO 52016-1 gives convective fractions per emitter type, to be cited with the
  exact clause in the PR), radiators split between the air node and the surface node by a convective fraction, the
  electric heating fully convective. (c) A two-node floor (screed capacity) inside the distribution system: more
  state, more parameters, nothing for radiators. **Recommendation: (b)**; without it the thermostat design does not
  work at 900 s, and it is the building's own modelling, visible only through the room temperature.
- **Q5. The report demand's and the comfort KPIs' set points.** (a) The building reads `HeatingSetPointInCelsius`
  and `CoolingSetPointInCelsius` from the thermostat through optional inputs (default connections from the
  heat-distribution controller, the idealized heater's controller and the air conditioners' controllers publish
  theirs); unwired, the demand output is 0 and the comfort KPIs are skipped with one `log.warning`, which the four
  setups without a thermostat (`basic_household`, `default_connections`, `air_conditioned_house`,
  `simple_air_conditioner_household_building_sizer`) show today only as a meaningless 20/25 °C report. The window
  opening keeps a field of its own, `window_opening_temperature_in_celsius`, default today's 25 °C. (b) The building
  keeps `set_heating_temperature_in_celsius` and `set_cooling_temperature_in_celsius` as report-only fields: two
  owners of the number the review objected to, and a raise or a setback is not seen by the KPI.
  **Recommendation: (a).**
- **Q6. Fold or split.** (a) Stage 2b carries the thermostat, the emitter law and the receiving node of Q4 in one PR,
  because 2b without Q4 is a result nobody wants to bless. (b) 2b and 2c separate, 2c right behind, the intermediate
  goldens blessed once and overwritten. **Recommendation: (a)**, with the receiving-node change as its own commit in
  the PR.
- **Q7. The generator's cue without a buffer (for pnw7 and g2mx).** (a) The distribution system's published flow
  (`WaterMassFlowHDS > 0`) and return: a physics port, the real boiler's flow switch; the generator's L1 reads
  physics only. (b) The thermostat's `State` output read by the generator's L1: an L1-to-L1 signal, which the
  principles do not list. (c) An L2 "heat demand" relay between them: a component for one wire.
  **Recommendation: (a)**; recorded for pnw7's spec, which decides it.
- **Q8. Scope of the electric heating.** (a) A sibling bead under this epic, built as stage 3 after 2b. (b) Left to
  its own finding. **Recommendation: (a)**: it is the same defect and the same transition table.

## 9. Beads

Epic hisim-component-physics-4g9.

| Bead | Content | PR |
|---|---|---|
| hisim-component-physics-4g9.37 (P1) | this spec; stages 2a and 2b | – |
| *to file* (P1, `components`, child of 4g9) | stage 1, the emitter subcomponent | – |
| *to file* (P2, `components`, child of 4g9) | stage 2c, the receiving node, if Q6 splits it | – |
| *to file* (P2, `components`, child of 4g9) | stage 3, the electric heating's thermostat (Q8) | – |
| *to file* (P3, `components`, child of 4g9) | the EN 442 power law (Q3 b), if 2b's measurement asks for it | – |
| hisim-pnw7 (P3) | the pipe water as a start-of-step node and district heating's bill; takes Q7 | – |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | D25 without a buffer on the thermostat's flow; takes Q7 | – |
| hisim-hds-cooling-option-m4g6 | `cooling_enabled` and the two heat outputs this spec builds on | #1165 |
| hisim-component-physics-4g9.34 (P2) | `ExplicitStateController`, the base of the thermostat | #1142 |
| hisim-pipe-water-free-convection-6vdp (P3) | the standing water's heat to the room; untouched here | – |
| hisim-component-physics-4g9.38 (P1) | the energy manager signals L1 controllers only; the building's modifier part closes with stage 2a | – |
