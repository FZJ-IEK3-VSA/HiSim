# Spec: named hydraulic circuits — every water loop has an id, one declared pump owner and a conservation check in every step

Status: draft; the owner's five decisions of 2026-10-22 are recorded (§2), the open questions Q0 to Q6 (§5) wait
for the owner. Nothing is built. Owner: Noah. Epic: hisim-hydraulic-circuits-wci7 (`owner-decision`). Builds on:
`roadmap/hydronic_coupling_spec.md` (§3 circuits and pump owners, §3.5 `HydronicPort`, §4.6 D25 with its amendment
of 2026-10-22, D1 with its exception of 2026-10-22), #1117 (the boiler without a buffer heats the flow the heat
distribution system pumps), #1119 (a storage reads the flow of every circuit whose temperature it reads), the open
PR #1109 (`HydronicPort`, `Wiring.refuse_a_broken_wiring`), the circuit audit of 2026-10-22
(`/home/noah/hisim/agent-tmp/circuits/report.md`) and the boiler investigation hisim-boiler-flow-mismatch-4awa
(`/home/noah/hisim/agent-tmp/4awa/report.md`). Line numbers below refer to main at 635404f30 unless a commit is
named.

## 1. Problem

**One water loop, two pumps, nothing noticed.** Until #1117, `gas_boiler_household` (the reference system of
`energy_systems/`, documented in the README and covered by a golden) wired a gas boiler and a heat distribution
system (HDS) on one space-heating loop without a buffer. The boiler pumped its own flow, `P_max · η_full / (c · 5 K)`
= 0.359 kg/s, and booked `m c (T_sup − T_ret)` with it; the HDS pumped its design flow, heating load / (c · 7 K) =
0.27 kg/s, and delivered `m c (T_sup − T_ret)` with that. Same temperatures, two flows: in the full year 2021 at
900 s the boiler booked 14,905 kWh of space heat for 11,205 kWh delivered, +3,700 kWh (+33.0 %), and the gas was
overstated to match (15,274 kWh instead of 11,470 kWh). With radiators (15 K design lift) the same file overstated
the gas by +125 %. No check saw it: the boiler's own balance closes (gas in = heat out + combustion loss), the HDS
declares no energy ports, and the boiler's `WaterOutputMassFlowSh` was read by nobody, which the energy-balance
check lists as "unlinked", not as a failure. `balance_report.json` said "closes".

#1117 fixed that one wiring: the boiler reads `hds.WaterMassFlowHDS` through its new input `WaterInputMassFlowSh`
and heats that flow. The class of fault stays open, because nothing in HiSim knows what a water loop is:

- **Nothing checks who pumps.** The simulator, the energy-system wiring checks (`hisim/energy_system/wiring_checks.py`)
  and the assembly binding (`hisim/energy_system/assemblies/binding.py`) check ports, load types, units and mandatory
  inputs. None of them knows that a mass flow published at one end of a loop must be the mass flow at the other end.
- **Who pumps is decided by position enums, per component, with nothing checking that they agree.** Three enums
  describe one topology fact: `PositionHotWaterStorageInSystemSetup` in `heat_distribution_system.py:42` (`PARALLEL`,
  `SERIES`, `NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR`, `NO_STORAGE_MASS_FLOW_FIX`), in `simple_water_storage.py:75`
  (`PARALLEL_TO_HEAT_SOURCE`, `SERIES_TO_HEAT_SOURCE`) and in `more_advanced_heat_pump_hplib.py:89` (`PARALLEL`,
  `SERIES`, `NO_STORAGE`). The HDS pumps its design flow in `PARALLEL` and `NO_STORAGE_MASS_FLOW_FIX` and reads a
  flow otherwise (`:153-156`, `:238-246`); its default is `PARALLEL`, so an HDS wired directly to a heat pump without
  setting a position pumps beside the heat pump's own pump, silently. The audit found the fault wired in only
  `gas_boiler_household` and its two copies, but a heat pump without a buffer (the hplib model always pumps, 0.333 kg/s
  in `NO_STORAGE`), or a `SimpleHeatSource` with `use_external_massflow_as_signal_input_for_nominal_massflow`
  (`simple_heat_source.py:436-438`, which replaces the flow it reads by its own 0.5 kg/s) would wire it again.
- **The receiving end's book is invisible.** Every loop today publishes three quantities: the pump owner's flow and
  the two temperatures. The flow is published once and read once; nothing comes back. A device that silently uses a
  flow of its own (the HDS before #1117) cannot be told apart from one that passes the flow through, because neither
  publishes what left it.
- **The one remaining gap in a recorded system is also invisible.** Without a buffer, the HDS publishes its return
  temperature and its delivered heat one step late (`heat_distribution_system.py:529-530`). The district-heating twin's
  substation bills `m c (T_sup − T_ret_published)` on this stale return: −17.6 kWh/yr (−0.16 %) at 900 s, −44.7 kWh
  (−0.41 %) at 3600 s (hisim-pnw7, closed hisim-energy-sankeys-9uoo.12). Only a per-step check of each device's book
  against its own published quantities would show it.

The rules exist: `hydronic_coupling_spec.md` §3.1 (one pump owner per circuit; the other end reads its flow),
`system_docs/component_design_principles.md` B4 (one owner per quantity; sender and receiver book the same energy),
D25. They are enforced nowhere. This spec gives HiSim the one concept that makes them checkable: the named hydraulic
circuit. It is not a bus (§4.2, Q0).

## 2. Decisions

The owner's decisions of 2026-10-22 (Noah), verbatim. Numbers are never reused; later documents cite them as
`D3 of the hydraulic circuits spec`.

| # | Decision (verbatim) | By, date | Consequence |
|---|---|---|---|
| D1 | **Fluid circuits with an id.** Every device connected to a circuit has, per circuit, a mass-flow input, a mass-flow output, a temperature input and a temperature output (one such set per circuit, so a buffer or tank on several circuits has several sets). | owner, 2026-10-22 | A loop is a named thing in the model, and every member publishes what left it, not only what it decided. Today's three quantities per loop become four per member (§4.1); every receiver gains a mass-flow output and every pump owner a mass-flow input. |
| D2 | **Detection, not prevention.** No shared circuit-level flow value. Each device publishes its own outputs through ports (black boxes). A violation fails hard. Owner: "why prevent? what advantage do we gain with the added complexity?" | owner, 2026-10-22 | The circuit holds no value and computes nothing (§4.2). The components stay black boxes that talk through ports; a device that breaks conservation is caught, not corrected. |
| D3 | **The pump owner is explicit.** No implicit pumps or roles derived from positioning, list order or position enums. Owner: "that is not salient and usually a bad design choice." Each circuit has exactly one explicitly declared pump owner, checked at build. The `PositionHotWaterStorageInSystemSetup` enums (three of them) then go. | owner, 2026-10-22 | Who pumps is written in the system description, once per circuit (§4.3, Q3). The build refuses a circuit with zero or two pump owners. The three enums are removed (§7; the HDS's lag half of its enum goes with hisim-pnw7). |
| D4 | **The check runs every step, in the pump component:** `if abs(massflow_in - massflow_out) > tolerance: raise CircuitError(...)`. It goes in `i_doublecheck`, which the simulator calls once per step after convergence (`simulator.py:503`), not in `i_simulate`, which runs every pass, where the returning flow can lag a pass. The error names the circuit, the device, the step and both flows. | owner, 2026-10-22 | `Component.i_doublecheck` gets its first production use (§4.4). The check costs one subtraction per circuit per step, outside the convergence loop. |
| D5 | **Forced steps need no exception.** Only controllers are forced; physics always converges (a physics non-convergence aborts at 100 passes). Mass flows and temperatures are physics outputs, so they are converged in every step. | owner, 2026-10-22 | The check has no "forced" branch and no tolerance widening for forced steps (§4.4). |

*Proposed, not decided (agent, 2026-10-22; the owner's confirmation is Q0):* **D6 — HiSim keeps "no buses"; a named
circuit is not one.** A bus balances: it takes several contributions and solves for what is missing. A named circuit
balances nothing and solves nothing: it is a name shared by the ends of one series loop, the declaration of the one
member that pumps it, and a check of what the members published. Deleting every circuit declaration from a system
leaves every simulation result bit-identical; only the build check, the per-step check and the names in error
messages go. Deleting a bus changes results.

## 3. Terms

- **Circuit**: one closed water loop, in which the same water passes through two or more devices in series and
  returns to the first. Recorded systems have only two-member loops (a generator and a store, a store and the HDS, a
  generator and the HDS, a collector and a tank). A buffer in series with its heat source (`SERIES_TO_HEAT_SOURCE`,
  no recorded system) is a three-member loop: generator → HDS → buffer → generator.
- **Circuit id**: the name the system description gives one circuit (`space_heating`, `dhw`, `solar_dhw`,
  `buffer_to_distribution`). It is unique within one energy system and appears in every error that concerns the loop.
  Assemblies already use these ids for their circuit ends (`energy_systems/assemblies/`, `{circuit: dhw, member: …}`).
- **Member**: a device connected to a circuit. A device may be a member of several circuits (a buffer: its generator
  circuit, its distribution circuit, a secondary generator circuit; a boiler: `space_heating` and `dhw`).
- **Circuit port set** (D1): the four quantities one member has per circuit: `mass_flow_in_kg_per_second` in (the
  water arriving), `mass_flow_in_kg_per_second` out (the water leaving), `temperature_in_celsius` in (of the water
  arriving) and `temperature_in_celsius` out (of the water leaving). In the four-wire picture, member A's flow out and
  temperature out are member B's flow in and temperature in, and B's are A's. "Supply" and "return" remain the names of
  the two legs of a two-member loop: the generator's temperature out is the supply, the receiver's temperature out
  the return.
- **Pump owner** (D3): the one member of a circuit that decides the circuit's mass flow for the step, in kg/s, and
  publishes it as its flow out. Every other member is a **pass-through**: its flow out is its flow in, unchanged.
- **Receiver**: in a two-member loop, the member that is not the generator; in general, any member that is not the
  pump owner.
- **Bus**: a balancing junction that collects several flows of one carrier and computes the remainder (HiSim's
  electricity meter and energy manager sum many producers and consumers through tag-selected inputs). HiSim has no
  bus, by owner decision (`roadmap/complete_energy_sankeys_spec.md`, Q-J, 2026-10-20: "HiSim explicitly does not use
  buses as a concept, since they bring a whole host of problems"), and this spec adds none (§4.2).
- **Build**: the moment after `Simulator.connect_all_components` and before step 0 (`Simulator.run_all_timesteps`,
  `simulator.py:816`), where the energy-port rules of #1109 also run. A build check refuses a system before it
  simulates.
- **Step check**: a check of converged values, once per step, in `Component.i_doublecheck`
  (`component.py:820`), which the simulator calls for every component after the passes of a step have converged
  (`simulator.py:502-503`).
- **Forced step**: a step whose controllers were frozen after ten passes (`force_convergence`, `simulator.py:488-489`)
  so that the physics can settle; the physics still iterates to the convergence criterion (`|Δ| ≤ 1e-4` on every
  output, `SingleTimeStepValues.is_close_enough_to_previous`, `component.py:222-228`) or the run aborts at the pass
  limit. A forced step is intended behaviour, not a failure.
- **Tolerance**: the largest `|flow in − flow out|` in kg/s, or `|booked heat − carried heat|` in W, that the step
  check lets pass (Q5).

## 4. Design

### 4.1 The circuit port set: four quantities per member and circuit

Every hydronic component declares, per circuit it can be a member of, one circuit port set of four channels (D1):

| Quantity | Channel kind | Unit | Pump owner | Pass-through member |
|---|---|---|---|---|
| flow in | `ComponentInput` | kg/s | the flow that came back round the loop; read only for the step check | the flow the previous member sent; read and passed through |
| flow out | `ComponentOutput` | kg/s | the flow it pumps this step | equal to flow in, by the member's own code |
| temperature in | `ComponentInput` | °C | the water arriving (the return, for a generator) | the water arriving (the supply, for a receiver) |
| temperature out | `ComponentOutput` | °C | the water leaving (the supply) | the water leaving (the return) |

Today every loop has three published quantities: the pump owner's flow, the supply temperature and the return
temperature (`hydronic_coupling_spec.md` §3.1; `HydronicPort` of #1109 names exactly these three). The fourth, the
flow *returning* to the pump owner, exists nowhere, and that is why a receiver that used a flow of its own was
invisible. Two members already have both flow channels on one circuit: the boiler on the distribution system's flow
(`WaterInputMassFlowSh`, `WaterOutputMassFlowSh`, #1117) and the district-heating substation's space-heating side
(`WaterInputMassFlowRateFromHeatDistributionSystem`, `WaterOutputShMassFlowRate`); both publish the flow they read.
The HDS, the buffer, the hot-water tank and `SimpleHeatSource` publish no flow out on the circuits they do not pump;
the boiler, the heat pump, the electric heater, the solar collector and the substation's hot-water side read no flow
in on the circuits they pump. §7 lists what each gains.

A port set is declared in `__init__` beside the channels it groups, with the circuit *role* the component knows it
by (a boiler's `space_heating` and `dhw`, a buffer's `generator`, `distribution` and `secondary_generator`), and
whether the component *can* pump that circuit (a buffer never; the HDS, the boiler and the substation's space-heating
side either way; the heat pump, the electric heater and the collector only as pump owner today). The role is the
component's own vocabulary; the circuit *id* is the system's (§4.3). The existing output names stay as they are, since
they are addresses in energy-system files and goldens (`system_docs/results_and_interfaces.md`); the new flow outputs
are named in the components' own idiom (`WaterMassFlowRateToHeatGenerator` on the buffer, `WaterMassFlowOutput` on the
HDS, …) and each description states that it is the circuit's flow passed through.

### 4.2 What a circuit is, and why it is not a bus

HiSim has no bus and no implicit balancing junction, by owner decision (`roadmap/complete_energy_sankeys_spec.md`, Q-J,
2026-10-20), and this spec keeps it so (Q0).
What the named circuit is:

- **A name.** The ends of one loop share an id, so the build can say "the circuit `space_heating` between `boiler`
  and `hds`" and the step check can name the loop whose water does not add up.
- **A declaration of the one pump owner** (D3), written in the system description, read at build.
- **A conservation check** (D4) of the values the members published on their own: at the pump owner, the flow that
  came back equals the flow it sent; with Q1, at every member, the flow that left equals the flow that arrived; with
  Q2, at every member, the heat it booked equals the heat its own flow and temperatures carry.

What it is not:

- **It holds no value.** There is no "circuit flow" the members read. The pump owner publishes its flow out; the
  next member reads it as its flow in and publishes it again as its flow out; the water goes round. Each value belongs
  to the output of the component that published it (B4), as today.
- **It computes nothing.** No mixing, no remainder, no reconciliation. If two members disagree, nobody is corrected;
  the run fails and the error names them (D2). A split of a flow into two branches is a valve component with one
  circuit per branch (`hydronic_coupling_spec.md` §3.5), not a junction.
- **It changes no result.** A system with the declarations removed and the checks switched off simulates
  bit-identically. The declaration is metadata about the wiring, like the energy ports of #1109, and the checks read
  published outputs after the fact.

The distinction matters for the owner's "no buses" rule: a bus is an invisible component with its own law (a
balance), which hides who decided what. A circuit adds no law; it makes a wiring fact nameable and checkable.

### 4.3 The pump owner is declared, told, and checked at build

The pump owner is written in the system description, once per circuit, beside the circuit's id and members (how, per
input mode, is Q3). Three things follow at build:

1. **Exactly one pump owner per circuit.** Zero or two is refused, naming the circuit and the members
   (`CircuitDeclarationError`).
2. **Each member is told its role.** Before `i_prepare_simulation`, the simulator hands every member its circuit id
   and whether it pumps (`Component.join_circuit(circuit_id, role, pumps)`, Q3c). A member refuses a role it cannot
   play: a buffer told to pump, a heat pump told to pass through (until it has a heater-on-flow mode like the boiler's
   of #1117). This replaces the position enums: the HDS no longer reads `PARALLEL` or `NO_STORAGE_MASS_FLOW_FIX` to
   know whether it pumps; it is told. The boiler no longer infers from a wired input whether it heats someone else's
   flow (`GenericBoiler.space_heating_runs_on_distribution_flow`, #1117); it is told, and the build refuses the flow
   input unwired when it is told to pass through.
3. **The declaration agrees with the wires.** For every pair of consecutive members, the second's flow in and
   temperature in are wired to the first's flow out and temperature out, and the loop closes back to the first. A
   member's port set that is wired to a component outside the circuit, a port set the declaration does not list, or a
   loop that does not close is refused, naming the circuit, both members and the channel. The same flow output read by
   members of two circuits is refused (one flow delivers its heat once).

In a two-member loop this is the four-wire picture of §4.1; in a three-member loop (a series buffer) each member's
flow in comes from the previous member and its flow out goes to the next.

Why the owner is *told* and not left to infer: in the four-wire design every member has a wired flow in, so "my flow
input is wired" no longer distinguishes the pump owner from a pass-through member, as it did for the boiler in
#1117. The one place the fact is written is the circuit declaration; the simulator carries it to the component.

### 4.4 The step check in `i_doublecheck`

D4, verbatim: in the pump owner, once per step, after convergence,

```python
def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
    for circuit in self.circuits_pumped:
        mass_flow_out_in_kg_per_second = stsv.get_output_value(circuit.flow_out)
        mass_flow_in_in_kg_per_second = stsv.get_input_value(circuit.flow_in)
        if abs(mass_flow_in_in_kg_per_second - mass_flow_out_in_kg_per_second) > circuit.tolerance_in_kg_per_second:
            raise CircuitError(circuit_id=..., device=self.component_name, timestep=timestep,
                               mass_flow_out_in_kg_per_second=..., mass_flow_in_in_kg_per_second=...)
```

The message: `Circuit 'space_heating': the pump owner 'hds' sent 0.270 kg/s in step 1234 (2021-01-13 20:30) and
0.359 kg/s came back. Some member of the circuit changed the flow; every member but the pump owner passes the flow
through unchanged.` With Q1 the member that changed it is named instead.

Why `i_doublecheck` and not `i_simulate` (D4): `i_simulate` runs in every pass of the step, and in the first pass of
a step the pass-through member has not yet seen the pump owner's new flow, so the returning flow lags a pass and a
check there would fire on every change of flow. `i_doublecheck` runs once, after the step's passes converged
(`simulator.py:502-503`), on values that satisfy `|Δ| ≤ 1e-4` against the previous pass for every output; the
returning flow is then the sent flow passed through pass-through members that have all seen it.

Why no forced-step branch (D5): forcing freezes the controllers after ten passes (`simulator.py:488-489`); the
physics keeps iterating until the convergence criterion holds, or the run aborts at the pass limit
(`maximum_passes_per_timestep`). Mass flows and temperatures are physics outputs, so in every step that finishes they
are converged. The 38 to 67 forced steps of the heat-pump twins in the January week (§6) therefore need no exemption;
the audit measured their circuits closing exactly (0.000 kWh) with forcing on.

Cost: the check is one subtraction per pumped circuit per step, outside the convergence loop; it does not touch the
hot path (`simulator-hot-path`: components about 80 %, `is_close_enough` about 3 %).

### 4.5 The error

`CircuitError(HydronicsError)` in `hisim/hydronics.py` (where every hydronic refusal lives), a `ValueError` like
them, raised from the member's `i_doublecheck` and left to propagate: the run fails with the circuit, the device,
the step and both values in the message (D4), and `hisim_main` reports it as it reports any component exception.
`CircuitDeclarationError(HydronicsError)` is the build-time twin for a declaration that is wrong (§4.3). Q5 discusses
whether a code (`EC-1`, `EC-2`, as the energy-system format numbers its refusals `EF-…`) is wanted.

### 4.6 The checks in one table

| Check | When | Where | Catches | Decided? |
|---|---|---|---|---|
| exactly one pump owner per circuit | build | simulator, from the declaration | an undeclared or a doubly declared pump | D3 |
| each member accepts its role | build | `Component.join_circuit` | a buffer told to pump; a boiler told to pass through with its flow input unwired | D3 (Q3c for the mechanism) |
| declaration agrees with the wires | build | simulator, from the declaration and the inputs | a flow wired across circuits; a loop that does not close; a port set outside any circuit | D3 |
| flow back = flow sent, at the pump owner | every step | `i_doublecheck` of the pump owner | any member that changed the flow (named only as "some member") | D4 |
| flow out = flow in, at every member | every step | `i_doublecheck` of every member | the member that changed the flow, by name | Q1 |
| booked heat = `m c (T_out − T_in)`, at every member | every step | `i_doublecheck` of every member | a member booking with a flow or a temperature it did not publish; the HDS's one-step lag without a buffer | Q2 |

## 5. Open questions

Each question lists the options, what each would mean, and a recommendation. The owner decides; the decision is
written into §2 with its date and into the epic's bead.

### Q0. The named circuit beside "no buses"

HiSim's rule is: no buses, no implicit balancing junctions; a new modelling concept is a separate decision. The
named circuit is a new concept. §4.2 argues it is not a bus, because it holds no value, computes nothing and changes
no result.

- **Option a — confirm D6 as proposed:** HiSim keeps "no buses" and adds the named circuit as a declaration plus
  checks. The guideline `system_docs/component_design_principles.md` gets one sentence under B4: "A water loop is a
  named circuit with one declared pump owner; every member passes the flow through and the pump owner checks that it
  came back."
- **Option b — reject the concept:** keep the rules of #1109 (ports paired through the wiring) and the build rule of
  hisim-energy-sankeys-9uoo.20 (a pumping end whose flow nobody reads is refused). This catches the `gas_boiler_household`
  wiring but not a receiver that reads the flow and books with another (the pre-#1117 HDS read nothing; a future one
  might read and ignore), and not the lag.

Recommendation: a. The concept is a name and a check, and the owner's D1 to D5 already assume it.

### Q1. The per-member mass check as well

D4 puts the check in the pump owner. The pump owner's check tells that *some* member changed the flow, not which.

- **Option a — pump owner only (D4 as written).** One check per circuit. In a two-member loop the culprit is the
  other member, so the message can name it; in a three-member loop it cannot.
- **Option b — every member checks its own flow out against its flow in, the pump owner's check being the same rule
  applied to it** (its flow out is what it pumped, its flow in what came back). The error then names the member that
  changed the flow. Cost: one subtraction per member per step. For a pass-through member the check is trivially true
  if its code copies the input to the output, which is exactly the point: a member that *computes* a flow of its own
  (the pre-#1117 HDS, `SimpleHeatSource` with the nominal-flow override) fails on the first step it does so.

Recommendation: b. It is the same rule, costs the same, and the error names the device. Dry run (§6): on today's main
both members that already have both channels pass it in every step.

### Q2. The energy check per member

Each member books a heat for the circuit (`ThermalOutputPowerSh`, `ThermalPowerDelivered`,
`ThermalPowerFromHeatGenerator`, …). The modelling guide requires it to equal `m c (T_out − T_in)` of the member's
own port set (`system_docs/modelling_guide.md` §1), with `c = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K`.

- **Option a — no energy check here.** The post-run energy balance (#1109, `hisim/postprocessing/energy_balance/`)
  checks balances over the run and per step from the result columns, once the HDS and the storages declare ports
  (hydronic stage E, hisim-hydronics-fxix.6). Nothing fails during the run.
- **Option b — every member checks, in the same `i_doublecheck`,** `|Q_booked − m c (T_out − T_in)| ≤ tolerance`
  (Q5), raising `CircuitError` with the circuit, the member, the step, the booked and the carried heat in W. It
  catches during the run what the post-run check finds afterwards, and it catches one thing the post-run check cannot
  see today: a member whose booked heat does not match its *own* published quantities (the HDS without a buffer, which
  publishes last step's return and heat beside this step's flow).

What b would fail on today's main (§6, January week at 900 s): the HDS in both buffer-less systems. In
`gas_boiler_household` it fails 592 of 672 steps (first at step 4, booked 0 W against 8,276 W carried; summed
|difference| 18.0 kWh of 548 kWh delivered, 3.3 %; net −2.2 kWh); in the district-heating twin 6 of 672 steps (first
at step 97, 3,897 W booked against 4,612 W carried; summed |difference| 2.1 kWh of 542 kWh, 0.4 %; net +1.1 kWh,
the −0.20 % of the audit). The difference between the two is the supply: the boiler's supply moves every step, so the
lag shows in almost every step; the substation's supply sits on the heating curve and the lag shows only where the
valve switches. Every other member of every other circuit passes with a difference of 0.0 W in every step (every
generator, both storages, the HDS behind a buffer, the collector, the heater). So b fails exactly what hisim-pnw7 is
for, and nothing else; it cannot land before pnw7's pipe-water node, or the two buffer-less twins (and RenoVisor's
district-heating translation) stop running.

Recommendation: b, staged after hisim-pnw7 (§7). Until then the energy check is built behind the mass check and
switched on when pnw7 closes the lag; the two systems it would fail are the two pnw7 is measured on.

### Q3. How a circuit is declared

Three input modes write a system: the energy-system YAML (schema v3 for the recorded files, v4 for the composed
files, `hisim/energy_system/`), Python setups (`system_setups/*.py`, `Simulator`), and assemblies
(`energy_systems/assemblies/`, `hisim/energy_system/assemblies/binding.py`). Three sub-questions.

**Q3a. Does the declaration wire the four inputs, or do the wires stay explicit and the declaration is checked
against them?**

How the recorded files wire hydronics today: almost entirely through bare-name items that expand to the classes'
default connections (`inputs: [HeatDistributionSystem, CondensingGasBoiler]` on the buffer), with an explicit
`input:/from:` pair only where a default connection does not exist (`boiler.WaterInputMassFlowSh <- hds.WaterMassFlowHDS`
in `gas_boiler_household`). Python setups do the same with `connect_automatically=True`. So "explicit wires" in HiSim
means "declared by class default connection and expanded by the loader", not a wire per channel.

- **Option a — the declaration wires.** A top-level block names the circuit, its members in loop order with the port
  set each uses, and the pump owner; the loader connects the four channels of each consecutive pair itself, and the
  members' `inputs:` lists carry no hydronic items:

  ```yaml
  circuits:
    space_heating:
      pump: hds
      loop: [{boiler: space_heating}, {hds: distribution}]
  ```

  For: one place states the loop, the wires cannot disagree with it, no fourth-wire boilerplate, three lines per
  circuit. Against: the water wires vanish from the `inputs:` lists a reader scans; a second wiring mechanism beside
  bare names and `input:/from:`; the recorder (`hisim/energy_system/recording/`) must emit the block from the wires it
  observed; the owner dislikes behaviour the file does not show.
- **Option b — the wires stay, the declaration is checked.** The `inputs:` items stay as they are (bare names; the
  fourth wire, the returning flow, comes from the classes' default connections like the other three, so the recorded
  files need no new input items). The block above is still written, and at build the simulator checks it against the
  wires (§4.3, rule 3): every consecutive pair wired, loop closed, no cross-circuit reader, every port set in some
  circuit. For: nothing hidden, each fact visible where it is used today, a disagreement is a named build error.
  Against: the same topology stated twice (the wires and the block); 37 recorded files gain a block (§7).
- **Option c — no block; the pump owner is a component setting.** `pumps_circuit: true` in the HDS's config, and the
  circuit id derived from the wiring. Rejected by D3: a role carried as a per-component site setting is the position
  enum again under another name, and the id would be implicit.

Recommendation: b. It fits "explicit wires plus a check" and today's files; the redundancy is what the check is for.

**Q3b. Where the block goes, per input mode.**

- *Energy-system YAML (v3 and v4):* a top-level `circuits:` key beside `components:`, `imports:`, `groups:`,
  `variants:` (`hisim/energy_system_v3.schema.json`), one entry per circuit as above; `pump:` names a component key
  (or a composed member address `heating-Buffer`), `loop:` lists `{component: port_set}` pairs in the water's order.
  The loader validates it (`hisim/energy_system/validation.py`), the wiring checks hand it to the simulator, and the
  recorder writes it back (`recording/`), so every recorded twin carries it.
- *Python setups:* `my_sim.add_circuit(Circuit(id="space_heating", pump=my_hds, loop=[(my_boiler, "space_heating"),
  (my_hds, "distribution")]))` after the components are added; `Simulator.run_all_timesteps` runs the same build
  checks on it. The 22 setups with a recorded twin get one call per circuit.
- *Assemblies:* the circuit end `{circuit: c, member: X}` already exists on `provides:` and `serves:`; it gains
  `pump: true` on exactly one end (`serves: {circuit: space_heating, other_end: [SimpleHotWaterStorage], pump: true}`
  on the site's HDS; `provides: dhw: {circuit: dhw, member: Boiler, pump: true}` on the boiler assembly). The binding
  (`binding.py`, `_lower_circuit`) lowers the joined ends to the composed file's `circuits:` block and refuses a circuit
  with no or two `pump: true` ends (a new `EF-7M`). The composed file then carries the block explicitly like any other
  file, so expansion changes nothing about Q3a.

**Q3c. How a member learns its role.**

- **Option a — the simulator tells it** (`Component.join_circuit(circuit_id, role, pumps)` before
  `i_prepare_simulation`; §4.3 rule 2). One source, the declaration; the component refuses a role it cannot play.
- **Option b — the component infers it from its wiring**, as the boiler does since #1117 ("my flow input is wired, so
  someone else pumps"). Fails in the four-wire design, where every member's flow in is wired (§4.3).
- **Option c — a config field** (`pumps_the_circuit`) that must agree with the declaration. Two places for one fact;
  D3 names such settings as the design to leave.

Recommendation: a. It is one new lifecycle call, and the only option consistent with D3 and D1 together.

### Q4. Relation to #1109's `HydronicPort` and to beads R1, pnw7, g2mx, fxix.15

#1109 (`origin/sankey-pr3-storages`, open) gives the storages and generators a `HydronicPort`: the three output
names of a circuit (`mass_flow`, `supply_temperature`, `return_temperature`), a role (`IN`/`OUT`) and a carrier, and
`Wiring.circuits` / `refuse_a_broken_wiring` pair the ends through the wiring at build (`ports.py:757-900` on that
branch). It serves the post-run energy balance and the Sankey. It names no circuit id and no pump owner, declares the
HDS only behind a buffer, and knows three quantities, not four.

- **Option a — merge #1109, then build on it.** The circuit port set of §4.1 *contains* a `HydronicPort`: its three
  names are the pump owner's flow out (or the pass-through's flow in, which resolves to the same column) and the two
  temperatures. `Component.add_hydronic_port` (`component.py:498` on the branch) becomes `add_circuit_port_set`, which
  takes the fourth channel and the role and still registers the `HydronicPort` for the balance check. `Wiring.circuits`
  keeps pairing by the wiring for the post-run check and additionally reads the declaration for the build check of §4.3.
  Nothing of #1109 is thrown away; its 1,500 lines of tests keep running.
- **Option b — replace #1109.** Close it; the circuit port set alone feeds both the step check and the post-run
  balance. Loses a reviewed PR that is nearly ready and the Sankey work that depends on it (hisim-energy-sankeys-9uoo).

Recommendation: a. The two agree on the three quantities and on "the ends pair through the wiring"; this spec adds the
id, the pump owner, the fourth quantity and the step check.

The beads:

| Bead | What it covers | This spec |
|---|---|---|
| hisim-energy-sankeys-9uoo.20 (R1, P2) | build rule: refuse a pumping end whose flow nobody reads while another component reads its supply | **superseded**: §4.3 rule 3 (every consecutive pair wired, loop closed) refuses the same wiring and more, from the declaration instead of a heuristic over readers. Close it when §4.3 lands, citing this spec. |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | the gas boiler's flow gap; a2 built in #1117; keeps the per-step closure, the thermostat-driven flow (hisim-component-physics-4g9.37) and the HDS enum removal | **partly absorbed**: the enum removal moves here (D3, §7); the per-step closure stays with pnw7; the thermostat-driven flow stays with 4g9.37. Re-rate as its note says. |
| hisim-pnw7 (P3) | the HDS's pipe water as a start-of-step node; D25's flow ownership; the HDS enum; district heating's bill | **a dependency and a partner**: pnw7 keeps the node and the bill; the enum removal and D25's "who pumps" move here (the declaration replaces inference). The energy check of Q2 is switched on after pnw7 (§7, stage 4). |
| hisim-hydronics-fxix.15 (P3) | design how a component learns its hydraulic topology, replacing the three enums | **absorbed**: §4.3 and Q3c are that design. Close it when the owner decides Q3; its text points here. |
| hisim-hydronics-fxix.6 (P?) | stage E, `HydronicPort` on every circuit in the balance check | unchanged; #1109 is its PR; this spec builds on it (Q4 a). |

### Q5. Tolerance, error type and code

The flow is passed through unchanged by every pass-through member (a copy of the input to the output), so at
convergence the returning flow equals the sent flow up to the convergence criterion of the pass, `|Δ| ≤ 1e-4` on
every output (`component.py:226`), and in a step whose flow did not change between the last two passes it is exactly
equal.

- **Option a — exact equality** (`!=`). Fails on a step where the pass-through member's output is from the pass before
  the pump owner's last change, which `is_close_enough_to_previous` allows by up to 1e-4.
- **Option b — absolute, 1e-4 kg/s**, the convergence criterion itself: anything the iteration accepted as converged
  passes; a flow of its own (0.359 against 0.270 kg/s, a difference of 0.089 kg/s) fails by three orders of
  magnitude. 1e-4 kg/s is 0.36 kg/h, far below any pump's flow.
- **Option c — relative**, 0.1 % of the sent flow. Undefined at 0 kg/s and no better than b where flows are 0.1 to
  0.6 kg/s.

For the energy check (Q2) the same reasoning gives `max(1 W, 0.1 % of the carried heat)`: the post-run balance's own
per-step tolerance is `max(1e-6 kWh, 0.1 %)` of the step's throughput (`modelling_guide.md` §1), and at 900 s
1e-6 kWh is 4 mW, too tight against the 1e-4 K the temperatures may still move by at convergence (0.27 kg/s × 4180 ×
1e-4 K = 0.11 W). 1 W is the floor; the dry run of §6 used the balance check's tolerance and found the same failures.

Error type: `CircuitError(HydronicsError)` in `hisim/hydronics.py`, beside `NegativeMassFlowError` and the other
hydronic refusals, a `ValueError` like them; a build-time `CircuitDeclarationError(HydronicsError)`. A code: the
energy-system format numbers its refusals (`EF-09`, `EF-7A`); a run-time component error has no code today.
Recommendation: b for the mass check, `max(1 W, 0.1 %)` for the energy check, the two exception types above, a code
only for the loader's refusal of a bad `circuits:` block (the next free `EF-` number), none for the step check.

### Q6. Migration and rollout

What changes, counted on main at 635404f30 (details in §7):

- **Components (9):** `GenericBoiler`, `MoreAdvancedHeatPumpHPLib`, `DistrictHeating`, `ElectricHeating`,
  `SolarThermalSystem`, `SimpleHotWaterStorage` (buffer), `SimpleDHWStorage` (tank), `HeatDistribution`,
  `SimpleHeatSource`. Each declares its port sets; pass-through members gain a flow output per circuit, pump owners a
  flow input per circuit; default connections gain the fourth wire; the three enums go.
- **Recorded files (37):** the 35 `energy_systems/*.energy_system.yaml` with a water circuit (every file but the 9
  without one: `air_conditioned_house`, `basic_household`, `default_connections`, `dynamic_components`,
  `electrolyzer_with_renewables`, the two `simple_air_conditioner_*`, the two `simple_system_setup_*`),
  `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` and `tests/energy_systems/uc1.realized.energy_system.yaml`;
  each gains a `circuits:` block, and the 6 that carry a `position_hot_water_storage_in_system` line
  (`gas_boiler_household`, the three district-heating files, the mockup, `uc1.realized`) lose it. Plus the 22 Python
  setups' `add_circuit` calls (the district-heating setup also loses its enum line) and the assemblies' `pump: true`
  (the 10 heating assemblies, the dhw cylinder assembly, `test_partners.yaml`, which also carries an enum line, and the
  mockup assembly `roadmap/declarative_energy_systems/assemblies_mockup/heating/air_source_heat_pump_direct.assembly.yaml`).
- **RenoVisor:** the translator runs the grouped twins (`hisim/renovisor/translate.py`, `BY_GENERATOR`,
  `WITH_SOLAR_THERMAL`: gas, oil, pellets, wood chips, hydrogen, heat pump, electric, district heating, and the two
  solar twins), all among the 35; it names no hydronic input and no position enum, so it changes only if the grouped
  files it copies change — they do (the block), and the re-record is a routine one. No result moves, so no
  renovisorissues announcement for the declaration; the enum removal of the district-heating twin is a file-format
  change RenoVisor sees only through the base files it ships with.
- **Goldens:** compare KPIs only (`golden_references/README.md`); new flow-out columns and the declaration move no
  KPI. Expected bit-identical everywhere, except where a check finds a real fault, which on today's main is nowhere
  for the mass checks (§6) and the two buffer-less HDS twins for the energy check (Q2), which is why that check
  waits for pnw7.
- **Staging into PRs** (one fix per PR, stacked):
  1. *Declaration and build check*: `Circuit`, the port set, `add_circuit`/`circuits:`/`pump: true`, `join_circuit`,
     the build rules of §4.3, the 37 blocks, the 22 calls; no new channels yet; bit-identical. Depends on #1109 merged
     (Q4 a).
  2. *The fourth wire and the mass check*: the flow outputs and inputs, the default connections, D4's check in the
     pump owners (and Q1's in every member); bit-identical, new result columns only.
  3. *The enums go*: `PositionHotWaterStorageInSystemSetup` × 3 removed, the HDS's and the heat pump's flow source
     from their role; the 6 recorded lines, the two assembly lines, the setup line and the 13 code and test files that
     name an enum; bit-identical.
     Depends on hisim-pnw7 for the HDS's lag half (today `PARALLEL` also decides whether the HDS publishes this step's
     or last step's values, `heat_distribution_system.py:510-530`); until pnw7 lands, that half becomes the HDS's own
     question "do I sit behind a store", answered by the simulator from the circuit (its other member declares a
     store's port set) — or PR 3 waits for pnw7. Recommendation: wait; pnw7 is where the lag goes.
  4. *The energy check* (Q2), after pnw7: switched on in every member; fails nothing on the then-current main.

## 6. Dry run

Measured on main at 635404f30 (with #1117 and #1119), January week 2021-01-01 to 2021-01-08 at 900 s (672 steps),
through `run_energy_system`, one run per recorded family (the `.grouped` and `.composed` twins have identical hydronic
wiring, checked by the audit's script; the wiring of all 44 files plus the mockup and `uc1.realized` was dumped for
the mass column). Scripts and runs: `/home/noah/hisim/agent-tmp/rs/dryrun/` (`scripts/device_checks.py`,
`device_checks.jsonl`, `circuits_all.txt`).

**What was computed.** *Mass check (Q1, per member):* from the wiring dump, whether each circuit has exactly one flow
output read by its other end (the pump owner's), and, for the two members that already have both flow channels on one
circuit (the boiler on the HDS's flow, the substation's space-heating side), `|flow out − flow in|` per step from the
result columns. For every other member the fourth channel does not exist yet, so its check is not computable; "pass
(wiring)" means the wiring has one pump owner whose flow the other end reads and nobody books with another flow
(the audit's finding, re-checked on this main). *Energy check (Q2, per member):* `|Q_booked − m c (T_out − T_in)|`
per step, with the circuit's one flow column and the two temperature columns both members see, against
`max(1e-6 kWh, 0.1 %)` of the step's carried heat.

| System (twins) | Circuit | Pump owner | Mass check | Energy check: sender | Energy check: receiver |
|---|---|---|---|---|---|
| gas_boiler_household (+ mockup_minimal, uc1.realized) | boiler → HDS, no buffer | HDS (0.27 kg/s) | **pass**: boiler flow out = flow in, max diff 0.0 kg/s, 672 steps | pass, 0.0 W every step (550.4 kWh) | **FAIL**: HDS, 592 of 672 steps; first step 4 (2021-01-01 01:00), booked 0 W, carried 8,276 W; Σ\|diff\| 18.0 kWh of 548.3 kWh (3.3 %), net −2.2 kWh |
| household_district_heating_building_sizer (base, .grouped, .composed) | substation → HDS, no buffer | HDS (0.27 kg/s) | **pass**: substation flow out = flow in, max diff 0.0 kg/s | pass, 0.0 W (540.8 kWh) | **FAIL**: HDS, 6 of 672 steps; first step 97 (2021-01-02 00:15), booked 3,897 W, carried 4,612 W; Σ\|diff\| 2.1 kWh of 541.9 kWh (0.4 %), net +1.1 kWh |
| same | substation → tank (dhw) | substation (0.019 kg/s) | pass (wiring) | pass, 0.0 W (69.8 kWh) | pass, 0.0 W |
| gas, oil, pellets, wood_chips, hydrogen building_sizer (each base, .grouped, .composed); basic_household_only_heating; gas_solar_thermal (+ _building_sizer, both .grouped) | boiler → buffer | boiler (0.359 to 0.504 kg/s) | pass (wiring) | pass, 0.0 W (545.6 to 1,839.4 kWh) | pass, 0.0 W |
| same | buffer → HDS | HDS (0.12 or 0.27 kg/s) | pass (wiring) | pass, 0.0 W (541.3 to 1,832.0 kWh) | pass, 0.0 W |
| same, except basic_household_only_heating | boiler → tank (dhw; the secondary coil in the solar files) | boiler (0.124 to 0.147 kg/s) | pass (wiring) | pass, 0.0 W (69.5 to 70.6 kWh) | pass, 0.0 W |
| heatpump_building_sizer (base, .grouped, .composed), heatpump_car (+ .grouped), heatpump_solar_thermal (+ .grouped), automatic_default_connections | heat pump → buffer | heat pump (0.59 kg/s) | pass (wiring) | pass, 0.0 W (554.8 to 559.7 kWh); 27 to 67 forced steps, no effect | pass, 0.0 W |
| same | buffer → HDS | HDS (0.27 kg/s) | pass (wiring) | pass, 0.0 W (543.5 to 548.4 kWh) | pass, 0.0 W |
| same | heat pump → tank (dhw) | heat pump (0.47 to 0.51 kg/s) | pass (wiring) | pass, 0.0 W (65.8 to 67.9 kWh) | pass, 0.0 W |
| gas_solar_thermal (4 files), heatpump_solar_thermal (2 files) | collector → tank | solar pump (0.006 kg/s) | pass (wiring) | pass, 0.0 W (0.2 to 0.4 kWh in January) | pass, 0.0 W |
| electric_heating_building_sizer (base, .grouped, .composed) | heater → tank (dhw) | heater (0.019 kg/s) | pass (wiring) | pass, 0.0 W (69.8 kWh) | pass, 0.0 W |
| air_conditioned_house, basic_household, default_connections, dynamic_components, electrolyzer_with_renewables, simple_air_conditioner (2), simple_system_setup_one, _two | — | no water circuit | — | — | — |

Reading: on today's main **no mass check fails** (the one two-pump wiring went with #1117; every other circuit has
one pump owner whose flow the other end reads), and **the energy check fails exactly the heat distribution system
without a buffer**, in both buffer-less twins, because it publishes last step's return and delivered heat beside this
step's flow (hisim-pnw7). Everything else closes to 0.0 W in every step, forced steps included.

## 7. What changes and what does not

**Components.** Per component, the port sets, the new channels and what leaves:

| Component | Port sets (role: can pump?) | Gains | Loses |
|---|---|---|---|
| `GenericBoiler` | `space_heating`: either; `dhw`: pump owner | flow in on `dhw`; the flow in on `space_heating` exists (`WaterInputMassFlowSh`, #1117) and becomes the circuit's | `space_heating_runs_on_distribution_flow` inferred from wiring (told instead, Q3c) |
| `MoreAdvancedHeatPumpHPLib` | `space_heating`, `dhw`: pump owner (no heater-on-flow mode yet) | flow in on both | `PositionHotWaterStorageInSystemSetup` (`PARALLEL`/`SERIES`/`NO_STORAGE`); `m_dot_ref` stays as the fixed flow of a heater-on-flow mode if one is built later |
| `DistrictHeating` | `space_heating`: either (pass-through today); `dhw`: pump owner | flow in on `dhw`; the space-heating pair exists | — |
| `ElectricHeating` | `dhw`: pump owner | flow in | — |
| `SolarThermalSystem` | `solar_dhw`: pump owner | flow in | — |
| `SimpleHotWaterStorage` (buffer) | `generator`, `secondary_generator`, `distribution`: never pumps | flow out on each (pass-through) | `PositionHotWaterStorageInSystemSetup` (`PARALLEL_TO_HEAT_SOURCE`/`SERIES_TO_HEAT_SOURCE`): a series buffer is a three-member circuit |
| `SimpleDHWStorage` (tank) | `generator`, `secondary_generator`: never pumps | flow out on each | — |
| `HeatDistribution` | `distribution`: either | flow out (pass-through when behind a pump owner); flow in in every role | `PositionHotWaterStorageInSystemSetup` (4 members); `pumps_its_design_flow` from the enum (told instead); the lag half goes with pnw7 |
| `SimpleHeatSource` | brine circuit: either | flow out | `use_external_massflow_as_signal_input_for_nominal_massflow` (a second pump owner by another name) |

**Results.** No KPI moves in any stage (the declaration is metadata; the pass-through outputs are copies; the enums
are replaced by the same facts told at build). New result columns: one flow-out per pass-through member and
circuit (about 2 to 4 per system). Stage 4 (the energy check) fails nothing once pnw7 has landed.

**Public interfaces.** New: the `circuits:` block (schema v3 and v4), `Simulator.add_circuit`, the assembly end's
`pump: true`, the new flow channels (addresses in files), `CircuitError`, `CircuitDeclarationError`. Removed: the
three `PositionHotWaterStorageInSystemSetup` enums and their config field (6 recorded lines, 13 code and test files),
`SimpleHeatSource.use_external_massflow_as_signal_input_for_nominal_massflow`. RenoVisor: the grouped twins it copies
change (the block, the dropped line); a heads-up on renovisorissues when the enum line leaves the district-heating
twin, since the translator's base files change shape (`system_docs/results_and_interfaces.md` §5), no result changes.

**Goldens and twins.** Goldens compare KPIs only and stay bit-identical in every stage. The 37 recorded files, the
22 setups and the assemblies are re-recorded in stage 1 (the block) and stage 3 (the enum line); `tests/assemblies/`
twin gates and `tests/test_energy_system_*` parity tests are the gate.

**Verification per stage.** Stage 1: a test that declares two pump owners, none, a loop that does not close, a flow
wired across circuits, a buffer told to pump, each refused with the circuit and members named; every recorded file
builds. Stage 2: a test that wires a receiver which publishes a flow of its own on a circuit it does not pump and
asserts `CircuitError` in its `i_doublecheck` with the step and both flows (the pre-#1117 `gas_boiler_household`
wiring rebuilt as a fixture, with the HDS pumping 0.27 kg/s and a boiler pumping 0.359 kg/s); every recorded twin runs
the January week with zero errors; the `test_*_time_resolution` and twin tests bit-identical. Stage 3: every test that
named an enum rewritten against the declaration; the 5 recorded lines gone. Stage 4: the energy check on every
recorded twin, zero errors, after pnw7.

## 8. Beads

Epic hisim-hydraulic-circuits-wci7 (P2, `components`, `owner-decision` while Q0 to Q6 are open). The child beads are
filed when the owner has decided, one per stage of Q6; the related beads and what happens to them are in Q4.

| Bead | Content | PR |
|---|---|---|
| hisim-hydraulic-circuits-wci7 (P2, epic, `owner-decision`) | this spec; the owner's decisions D1 to D5; the questions Q0 to Q6 | this PR |
| hisim-energy-sankeys-9uoo.20 (P2) | R1, the build rule for a pumping end whose flow nobody reads | superseded by stage 1 (Q4) |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | the gas boiler's flow gap; a2 built | #1117; its enum half moves to stage 3 (Q4) |
| hisim-pnw7 (P3) | the pipe-water node and district heating's bill | partner of stages 3 and 4 (Q4) |
| hisim-hydronics-fxix.15 (P3) | how a component learns its topology | absorbed by §4.3 and Q3c (Q4) |
| hisim-hydronics-fxix.6 | stage E, `HydronicPort` in the balance check | #1109, built on (Q4) |
