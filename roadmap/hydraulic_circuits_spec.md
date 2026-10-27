# Spec: named hydraulic circuits — every water loop has an id, one declared pump owner and a conservation check of every step, after the run

Status: draft; the owner's decisions of 2026-10-22 (§2: D1 to D5, and D7 after the first draft's review) and of
2026-10-27 (D8: fluid-loop tags on the channel definitions replace #1109's hydronic-port declaration, which decides Q4
against its recommendation; D9: energy ports become channel tags on inputs too; D10: the supplier of a loop is declared,
the direction per step follows from the supplier's sign, a loop that may cool says so — which withdrew the first
amendment's role-from-sign rule, wrong for cooling loops; D11: every loop check of values runs once after the run,
vectorized over the result columns, not per step in `i_doublecheck`, as agreed on 2026-10-23 and confirmed for Q1 and
Q2 on 2026-10-27) are recorded. The open questions Q0 to
Q3, Q5, Q6 and the new Q7 to Q12 (§5) wait for the owner. Nothing is built; #1109 is back in draft for its rework onto
the tags (§5, Q6). Owner: Noah. Epic: hisim-hydraulic-circuits-wci7 (`owner-decision`). Builds on:
`roadmap/hydronic_coupling_spec.md` (§3 circuits and pump owners, §3.5 `HydronicPort`, §4.6 D25 with its amendment
of 2026-10-22, D1 with its exception of 2026-10-22), #1117 (the boiler without a buffer heats the flow the heat
distribution system pumps), #1119 (a storage reads the flow of every circuit whose temperature it reads), the open
PR #1109 (`HydronicPort`, `Wiring.refuse_a_broken_wiring`; `origin/sankey-pr3-storages` at da66cfe43, in draft since
2026-10-27), the building branch `sankey-pr4-building` (the building's energy ports, bead hisim-energy-sankeys-9uoo.6,
at 2b9fc8a8c, no PR yet), the circuit audit of 2026-10-22
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
  (−0.41 %) at 3600 s (hisim-pnw7, closed hisim-energy-sankeys-9uoo.12). Only a check of every step of each device's book
  against its own published quantities would show it.

The rules exist: `hydronic_coupling_spec.md` §3.1 (one pump owner per circuit; the other end reads its flow),
`system_docs/component_design_principles.md` B4 (one owner per quantity; sender and receiver book the same energy),
D25. They are enforced nowhere. This spec gives HiSim the one concept that makes them checkable: the named hydraulic
circuit. It is not a bus (§4.2, Q0).

## 2. Decisions

The owner's decisions of 2026-10-22 and 2026-10-27 (Noah), verbatim. Numbers are never reused; later documents cite
them as `D3 of the hydraulic circuits spec`.

| # | Decision (verbatim) | By, date | Consequence |
|---|---|---|---|
| D1 | **Fluid circuits with an id.** Every device connected to a circuit has, per circuit, a mass-flow input, a mass-flow output, a temperature input and a temperature output (one such set per circuit, so a buffer or tank on several circuits has several sets). | owner, 2026-10-22 | A loop is a named thing in the model, and every member publishes what left it, not only what it decided. Today's three quantities per loop become four per member (§4.1); every receiver gains a mass-flow output and every pump owner a mass-flow input. |
| D2 | **Detection, not prevention.** No shared circuit-level flow value. Each device publishes its own outputs through ports (black boxes). A violation fails hard. Owner: "why prevent? what advantage do we gain with the added complexity?" | owner, 2026-10-22 | The circuit holds no value and computes nothing (§4.2). The components stay black boxes that talk through ports; a device that breaks conservation is caught, not corrected. |
| D3 | **The pump owner is explicit.** No implicit pumps or roles derived from positioning, list order or position enums. Owner: "that is not salient and usually a bad design choice." Each circuit has exactly one explicitly declared pump owner, checked at build. The `PositionHotWaterStorageInSystemSetup` enums (three of them) then go. | owner, 2026-10-22 | Who pumps is stated by the pump owner's own port set, in code or in its configuration (§4.3, Q3; D7). The build refuses a loop with zero or two pumping port sets. The three enums are removed (§7; the HDS's lag half of its enum goes with hisim-pnw7). |
| D4 | **The check runs every step, in the pump component:** `if abs(massflow_in - massflow_out) > tolerance: raise CircuitError(...)`. It goes in `i_doublecheck`, which the simulator calls once per step after convergence (`simulator.py:503`), not in `i_simulate`, which runs every pass, where the returning flow can lag a pass. The error names the circuit, the device, the step and both flows. | owner, 2026-10-22 | **Where it runs is superseded by D11:** the rule (the pump owner's returning flow equals the flow it sent, tolerance, `CircuitError` naming the circuit, the device, the step and both flows) stays; it is checked once after the run over the result columns of every step (§4.4), not in `i_doublecheck`, which keeps no production use. |
| D5 | **Forced steps need no exception.** Only controllers are forced; physics always converges (a physics non-convergence aborts at 100 passes). Mass flows and temperatures are physics outputs, so they are converged in every step. | owner, 2026-10-22 | The check has no "forced" branch and no tolerance widening for forced steps (§4.4). |
| D7 | **No top-level declaration.** Owner, verbatim: "no top level declaration. the ports themselves should declare that they belong to a fluid loop". That is: no top-level `circuits:` block in the energy-system YAML, no `Simulator.add_circuit`, no assembly-level circuit declaration. A component's port set itself declares that it belongs to a fluid loop. | owner, 2026-10-22 | A loop is what the wiring connects: the build finds it by following the port sets' wires from member to member (§4.3), and refuses a loop that does not close, a port set wired outside a loop, or a flow read by two loops. The loop's id is derived from its pump owner's component and port-set name (§4.3). Nothing is told to a component at build; whether a port set pumps is stated in code or in the component's configuration (Q3). The first draft's `circuits:` block, `add_circuit`, `pump: true` on assembly ends and `join_circuit` are withdrawn. D1 to D5, Q0, Q1, Q2, Q5 and the dry run (§6) are unchanged by it. |
| D8 | **Fluid-loop tags on the channel definitions replace #1109's hydronic-port declaration.** Owner, on #1109: "the hydronic port thing doesn't sit right with me, since it's duplicating information about ports that then needs to be kept in sync. we've already discussed to use fluid_loops and use tagging directly on the component input/output definitions. wouldn't that completely replace the hydronic ports in 1109?" — and, after the answer that it replaces the declaration layer and not the balance logic that consumes it: "yeah, update 1122." | owner, 2026-10-27 | Q4 is decided against its recommendation of 2026-10-22: #1109 is not merged as it is and extended later. Its declaration layer goes: `Component.add_hydronic_port`, `CircuitEnd` and `HydronicPort` as a declared object, which named three channels a second time (by string, beside their `add_input`/`add_output` calls) and had to be kept in step with them. Instead, a channel of a loop says itself, in its own `add_input` or `add_output` call, which loop of the component and which of D1's four quantities it is (§4.1); the build groups the tagged channels into port sets (§4.3). What stays of #1109 is the balance side: the ends paired through the wiring (`Wiring`), the heat derived from the result columns and never from a value a component wrote for the balance, the rules one reader / both ends or neither, and the build-time `refuse_a_broken_wiring`; and the components' loops themselves (the buffer's three, the tank's two coils with their own returns, the distribution system's), now fed by the tags. #1109 is back in draft and is reworked onto the tags (Q4 as decided, Q6 stage 1a). |
| D9 | **Energy ports become channel tags too, on inputs as on outputs.** Owner: "can we also replace some of the energy port declarations with tags?" Recorded as the owner's request; the design is §4.7, the choices it leaves open are Q7 to Q10. | owner, 2026-10-27 | An `EnergyPort` already sits on the channel (`add_output(energy_port=...)`); what was missing is that only an output could carry one. A component that receives energy through an *input* therefore invented a copy-output and pointed it at the input by string (`peer_input=`, four uses on the building branch), or pointed one output at another (`peer_output=`, three uses). With `add_input(energy_port=...)` the input carries the port, the balance reads the column of the output wired to it, and the two pointers and the copy-outputs go (§4.7; the one case that keeps a second output is a quantity that is both taken from a boundary node and given to a component). A channel whose sign means its direction gets a port with two faces instead of two copy-outputs (Q9). The two tag systems that overlap on about a dozen outputs, `postprocessing_flag` and `energy_port`, are an open question with a recommendation (Q10), because merging them reaches into the KPI computation and the meters' selection. The distribution system's `declares_energy_ports`, a port declaration that reads a position enum, goes with D3's enum removal (Q6, stage 3). |
| D10 | **The supplier of a loop is declared; the direction per step follows from the supplier's sign; a loop that may cool says so.** Owner, on the first amendment's rule that no member declares a role because "each member's heat into the loop is m·c·(T_out − T_in) from its own four values, positive at the supplier": "for cooling loops this is wrong" — and, on the correction below: "yes, please rewrite it." | owner, 2026-10-27 | `m c (T_out − T_in)` is the heat a member adds to the water, in any loop; "positive at the supplier" holds only while the loop heats. In a cooling loop the supplier (a chiller, a heat pump in cooling mode) sends the water out colder than it came back and the room-side member warms it, so a role read off the sign would make the emitter the supplier and book cooling as space heating flowing the wrong way — what the Sankey's `COOLING` carrier exists to avoid; and a loop that heats in one step and cools in the next (the HDS with `cooling_enabled`, bead hisim-hds-cooling-option-m4g6; a heat pump cooling through a buffer, hisim-energy-sankeys-9uoo.17) has no one sign. So (1) every `FluidLoop` states whether its member supplies the loop (`supplies`, a declared fact like `pumps`, and a separate one: the HDS pumps its loop and does not supply it), and the build refuses a loop with zero or two suppliers; (2) in a step in which the supplier adds heat to the water its loop's heat is booked as the loop's stated heating carrier from the supplier to the other members, in a step in which it removes heat as `COOLING` from the room-side members to the supplier — the rule of Q9's signed energy port, applied to loops; (3) a loop that can only heat states that, and a step in which it cools anyway fails the run; a loop that may also cool says so explicitly (`may_cool`, from the HDS's `cooling_enabled` where the configuration has it). §4.1, §4.3, §4.6, §4.7, Q8, Q9 and §7 are rewritten; the first amendment's Q8 recommendation is withdrawn. |
| D11 | **The loop checks of values run after the run, vectorized, not every step.** Owner, 2026-10-23, on the per-step `i_doublecheck` of D4: "maybe move the checking to post-processing as a vector-check to avoid hundreds of thousands of i_doublecheck calls?" — agreed the same day and not written into this spec until the owner's reminder of 2026-10-27, for Q1: "i thought we agreed already to move the q1 mass checks also to postprocessing for speed reasons? i mean this will be about catching bugs, so 99.999% of runs will be fine and we don't need to waste the compute of doing it every step. if it fails we can easily go back and debug it." — and for Q2: "same for the energy check in q2". | owner, 2026-10-23 and 2026-10-27 | Every check of §4.6 that reads the values of a step — the mass check (D4, Q1), the energy check (Q2) and the heating-only check (D10) — runs once, after the simulation and before anything reads the result table, beside `FiniteResultsCheck` (`postprocessing_main.py:307`), as whole-column operations over all steps; nothing runs per step or per pass, and `i_doublecheck` keeps no production use. A violation still fails the run hard (D2), with the loop, the member, the first failing step, the number of failing steps and the values at the first one (§4.4, §4.5); the run's result table is kept, so the fault can be read off it. The build checks of §4.3 are unchanged: they run once, before step 0. |

*Proposed, not decided (agent, 2026-10-22; the owner's confirmation is Q0):* **D6 — HiSim keeps "no buses"; a named
circuit is not one.** A bus balances: it takes several contributions and solves for what is missing. A named circuit
balances nothing and solves nothing: it is a name shared by the ends of one series loop, the declaration of the one
member that pumps it, and a check of what the members published. Deleting every circuit declaration from a system
leaves every simulation result bit-identical; only the build check, the post-run check and the names in error
messages go. Deleting a bus changes results.

## 3. Terms

- **Circuit**: one closed water loop, in which the same water passes through two or more devices in series and
  returns to the first. Recorded systems have only two-member loops (a generator and a store, a store and the HDS, a
  generator and the HDS, a collector and a tank). A buffer in series with its heat source (`SERIES_TO_HEAT_SOURCE`,
  no recorded system) is a three-member loop: generator → HDS → buffer → generator.
- **Circuit id**: the name of one loop, derived at build from its pump owner's component name and loop name
  (`hds.distribution`, `CondensingGasBoiler.dhw`, `SolarThermalSystem.solar_dhw`; §4.3), never written in a file (D7).
  It is unique within one energy system, because a component name is and a component pumps each of its port sets at
  most once, and it appears in every error that concerns the loop.
- **Fluid loop** (D8): the one object through which a component names a loop it can be a member of, in its own
  vocabulary (`FluidLoop(name="generator", carrier=SPACE_HEATING_HEAT, pumps=False)`, §4.1): the loop's name as the
  component knows it, the heat the loop carries while it heats, and whether this component pumps it, supplies it
  (D10) and lets it cool. It is written once per component and loop; the component's channels point at it.
- **Loop tag** (D8): what one channel carries in its `add_input` or `add_output` call to say which fluid loop of the
  component it belongs to and which of the four quantities it is (`fluid_loop=self.generator_loop.flow_in`, §4.1). A
  tag is the only declaration of a port set; there is no second list to keep in step with the channels. The assemblies' circuit names (`{circuit: dhw,
  member: …}` in `energy_systems/assemblies/`) are a binding-time vocabulary of the assembly format and stay what they
  are; they are not this id.
- **Loop discovery**: how the build finds a loop from the wiring alone (§4.3): from any port set, its flow-out output
  is read by exactly one other port set's flow-in input; follow it, and repeat, until the start is reached again.
- **Member**: a device connected to a circuit. A device may be a member of several circuits (a buffer: its generator
  circuit, its distribution circuit, a secondary generator circuit; a boiler: `space_heating` and `dhw`).
- **Circuit port set** (D1): the four quantities one member has per circuit: `mass_flow_in_kg_per_second` in (the
  water arriving), `mass_flow_in_kg_per_second` out (the water leaving), `temperature_in_celsius` in (of the water
  arriving) and `temperature_in_celsius` out (of the water leaving). The set is the four channels of one component
  whose loop tags name the same fluid loop (D8); the build finds it by grouping them (§4.3). In the four-wire picture,
  member A's flow out and temperature out are member B's flow in and temperature in, and B's are A's. "Supply" and "return" remain the names of
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
- **Post-run check** (D11): a check of the converged values of every step, run once after the simulation over the
  result table, one column operation per check and port set, where `FiniteResultsCheck` runs
  (`postprocessing_main.py:307`), before anything reads the table. The table holds one column per output and one row
  per step; an input is read through the output wired to it.
- **Forced step**: a step whose controllers were frozen after ten passes (`force_convergence`, `simulator.py:488-489`)
  so that the physics can settle; the physics still iterates to the convergence criterion (`|Δ| ≤ 1e-4` on every
  output, `SingleTimeStepValues.is_close_enough_to_previous`, `component.py:222-228`) or the run aborts at the pass
  limit. A forced step is intended behaviour, not a failure.
- **Tolerance**: the largest `|flow in − flow out|` in kg/s, or `|booked heat − carried heat|` in W, that the step
  check lets pass (Q5).

## 4. Design

### 4.1 The circuit port set: four tagged channels per member and loop

Every hydronic component has, per loop it can be a member of, one circuit port set of four channels (D1). Nothing
declares the set: each of its four channels carries, in its own `add_input` or `add_output` call, a loop tag that
says which fluid loop of the component it belongs to and which of the four quantities it is (D8), and the build
groups a component's tagged channels by loop into its port sets (§4.3).

| Quantity | Channel kind | Unit | Loop tag | Pump owner | Pass-through member |
|---|---|---|---|---|---|
| flow in | `ComponentInput` | kg/s | `loop.flow_in` | the flow that came back round the loop; read only by the post-run check, through the output wired to it | the flow the previous member sent; read and passed through |
| flow out | `ComponentOutput` | kg/s | `loop.flow_out` | the flow it pumps this step | equal to flow in, by the member's own code |
| temperature in | `ComponentInput` | °C | `loop.temperature_in` | the water arriving (the return, for a generator) | the water arriving (the supply, for a receiver) |
| temperature out | `ComponentOutput` | °C | `loop.temperature_out` | the water leaving (the supply) | the water leaving (the return) |

Today every loop has three published quantities: the pump owner's flow, the supply temperature and the return
temperature (`hydronic_coupling_spec.md` §3.1; `HydronicPort` of #1109 names exactly these three). The fourth, the
flow *returning* to the pump owner, exists nowhere, and that is why a receiver that used a flow of its own was
invisible. Two members already have both flow channels on one circuit: the boiler on the distribution system's flow
(`WaterInputMassFlowSh`, `WaterOutputMassFlowSh`, #1117) and the district-heating substation's space-heating side
(`WaterInputMassFlowRateFromHeatDistributionSystem`, `WaterOutputShMassFlowRate`); both publish the flow they read.
The HDS, the buffer, the hot-water tank and `SimpleHeatSource` publish no flow out on the circuits they do not pump;
the boiler, the heat pump, the electric heater, the solar collector and the substation's hot-water side read no flow
in on the circuits they pump. §7 lists what each gains.

**The tag.** Three small types in `hisim/energy_port.py`, beside `EnergyPort` (the module keeps its name: it holds
what a channel is in its component's energy exchange; `HydronicPort` and `CircuitEnd` leave it, D8):

```python
class LoopQuantity(str, Enum):
    """Which of a member's four quantities a channel is: what the water does at it."""
    FLOW_IN = "flow_in"                  # kg/s, the water arriving; an input
    FLOW_OUT = "flow_out"                # kg/s, the water leaving; an output
    TEMPERATURE_IN = "temperature_in"    # °C, of the water arriving; an input
    TEMPERATURE_OUT = "temperature_out"  # °C, of the water leaving; an output


@dataclass(frozen=True)
class FluidLoop:
    """One loop as its member names it: its own name for it, the heat it carries, and what this member is to it."""
    name: str                          # the component's own vocabulary: "space_heating", "dhw", "generator", "distribution", ...
    carrier: lt.EnergyBalanceCarrier   # the heat the loop carries while it heats: SPACE_HEATING_HEAT or DOMESTIC_HOT_WATER_HEAT
    pumps: bool                        # D3: this member moves the loop's water; fixed in code, or from the configuration (Q3)
    supplies: bool                     # D10: this member is the loop's generator end; exactly one per loop
    may_cool: bool = False             # D10: this member allows the loop to remove heat from the room side (a chiller,
                                       # a heat pump in cooling mode, an HDS with cooling_enabled); a loop may cool only
                                       # if every member allows it (Q8)

    @property
    def flow_in(self) -> "FluidLoopChannel": ...        # FluidLoopChannel(self, LoopQuantity.FLOW_IN)
    # flow_out, temperature_in, temperature_out likewise


@dataclass(frozen=True)
class FluidLoopChannel:
    """The loop tag of one channel: which fluid loop of the component, and which quantity."""
    loop: FluidLoop
    quantity: LoopQuantity
```

`add_input` and `add_output` take `fluid_loop: Optional[FluidLoopChannel] = None`, keyword-only, and refuse at the
declaration (`FluidLoopError`, a `ValueError`, §4.5): a tag on a channel of the wrong unit (a flow is kg/s, a
temperature °C), a tag of the wrong kind (`FLOW_IN` and `TEMPERATURE_IN` belong on an input, `FLOW_OUT` and
`TEMPERATURE_OUT` on an output), a tag beside an `energy_port` (a channel is a leg of a loop or a power, never both),
and two channels of one component with the same tag. The component keeps no list of its loops: `Component.port_sets()`
derives them by grouping `inputs + outputs` by `tag.loop`, so there is nothing to keep in step. The loop's name, its
carrier, and whether this member pumps it, supplies it and lets it cool are written once, in the `FluidLoop`; each
channel points at it. How a component names a loop
(a plain string, or a `str` Enum of its loop names that `FluidLoop` takes as `name`), and whether the loop tag and the
energy port share one keyword argument, is Q7.

**What enforces that a loop channel is tagged.** A tag is not optional metadata: (i) at the declaration, a tag on a
channel it cannot describe is refused (above); (ii) at build, a loop whose any channel is tagged must be tagged whole:
a `flow_out` read by an input without a `flow_in` tag, a `flow_in` fed by an output without a `flow_out` tag, and a
port set with one to three of its four quantities are refused by name (§4.3, rules 0 and 2), so a forgotten tag at
either end of a wired loop is a build error, not a silent gap; (iii) a loop whose *both* ends are untagged is what
every loop is today: the balance reports its members as undeclared, never as closing. Whether a third rule should
refuse an untagged kg/s channel outright is Q11.

**Before and after, the buffer** (`simple_water_storage.py:1060-1093` on #1109's branch; the generator loop shown,
the secondary-generator and distribution loops are the same shape). Today, on #1109: the four channels are declared
as plain inputs and outputs, then `declare_circuits` names three of them a second time:

```python
self.water_mass_flow_rate_heat_generator_input_channel = self.add_input(
    self.component_name, self.WaterMassFlowRateFromHeatGenerator, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True
)
self.water_temperature_heat_generator_input_channel = self.add_input(
    self.component_name, self.WaterTemperatureFromHeatGenerator, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True
)
self.water_temperature_heat_generator_output_channel = self.add_output(
    self.component_name, self.WaterTemperatureToHeatGenerator, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, output_description=...
)
...
self.add_hydronic_port(                                     # names the three channels again, by object
    mass_flow=self.water_mass_flow_rate_heat_generator_input_channel,
    supply_temperature=self.water_temperature_heat_generator_input_channel,
    return_temperature=self.water_temperature_heat_generator_output_channel,
    role=lt.EnergyRole.IN,
    carrier=space_heating,
)
```

After (D8): one `FluidLoop` per loop, the tag on each channel, the fourth channel (the flow passed through, D1), no
second declaration:

```python
self.generator_loop = FluidLoop(
    "generator", carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
    pumps=False,      # a buffer moves no water
    supplies=False,   # the generator supplies this loop; the buffer receives (its distribution loop says supplies=True)
    may_cool=True,    # a buffer takes water of any temperature; whether the loop cools is the generator's and the emitter's say (Q8)
)

self.water_mass_flow_rate_heat_generator_input_channel = self.add_input(
    self.component_name, self.WaterMassFlowRateFromHeatGenerator, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True,
    fluid_loop=self.generator_loop.flow_in,
)
self.water_temperature_heat_generator_input_channel = self.add_input(
    self.component_name, self.WaterTemperatureFromHeatGenerator, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True,
    fluid_loop=self.generator_loop.temperature_in,
)
self.water_temperature_heat_generator_output_channel = self.add_output(
    self.component_name, self.WaterTemperatureToHeatGenerator, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS,
    output_description="The water returning to the generator, in °C.",
    fluid_loop=self.generator_loop.temperature_out,
)
self.water_mass_flow_rate_to_heat_generator_output_channel = self.add_output(    # new: the fourth channel (D1)
    self.component_name, self.WaterMassFlowRateToHeatGenerator, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC,
    output_description="The generator loop's flow, in kg/s, passed through unchanged: the buffer pumps nothing.",
    fluid_loop=self.generator_loop.flow_out,
)
```

The buffer's three `add_hydronic_port` calls become three `FluidLoop` objects and twelve tags; the tank's two coils
(`:2439`) and the HDS's one call (`heat_distribution_system.py:328`) likewise. A component that can pump or pass
through writes `pumps=self.config.pumps_space_heating_circuit` (Q3); the HDS writes `supplies=False` and
`may_cool=self.config.cooling_enabled` (D10, Q8); a boiler writes `supplies=True, may_cool=False` on both its loops, the
heat pump `supplies=True` with `may_cool` from its cooling-mode setting. The existing output names stay as they are, since
they are addresses in energy-system files and goldens (`system_docs/results_and_interfaces.md`); the new flow outputs
are named in the components' own idiom (`WaterMassFlowRateToHeatGenerator` on the buffer, `WaterMassFlowOutput` on the
HDS, …) and each description states that it is the circuit's flow passed through.

### 4.2 What a circuit is, and why it is not a bus

HiSim has no bus and no implicit balancing junction, by owner decision (`roadmap/complete_energy_sankeys_spec.md`, Q-J,
2026-10-20), and this spec keeps it so (Q0).
What the named circuit is:

- **A name.** The loop the wiring connects gets an id from its pump owner's port set (D7, §4.3), so the build can say
  "the loop `hds.distribution` between `boiler` and `hds`" and the post-run check can name the loop whose water does not add
  up.
- **A declaration of the one pump owner** (D3), made by the pump owner's own port set (D7, Q3), read at build.
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
- **It changes no result.** A system with the loop tags removed and the checks switched off simulates
  bit-identically. The declaration is a tag on the component's own channels, like an energy port, and
  the checks read published outputs after the fact.

The distinction matters for the owner's "no buses" rule: a bus is an invisible component with its own law (a
balance), which hides who decided what. A circuit adds no law; it makes a wiring fact nameable and checkable.

### 4.3 The pump owner is declared by its fluid loop, the loop is found through the wiring, both are checked at build

There is no declaration of a loop anywhere but on the components' channels (D7, D8). Whether a port set pumps is
stated by its `FluidLoop` (`pumps=`): fixed in code for a component that can only pump or never pumps, from the
component's configuration for one that can do either (Q3). The loop itself is what the wiring connects, and the build
finds it (`Simulator.run_all_timesteps`, after `connect_all_components`, where #1109's `refuse_a_broken_wiring` runs,
`simulator.py:819` on that branch) without being told:

0. **Port sets.** For every component, its tagged channels are grouped by the `FluidLoop` their tags name
   (`Component.port_sets()`). A group with exactly one channel per quantity is a port set. A group with one, two or
   three quantities (a loop tagged by half) is refused, naming the component, the loop and the missing quantities;
   the declaration itself already refuses two channels with one tag (§4.1).
1. **Loop discovery.** Every port set's `flow_out` output is connected to the `flow_in` input of exactly one other
   port set (the wiring index of #1109's `Wiring`, `readers` and `source_outputs`, already answers who reads what).
   Starting at any port set not yet assigned to a loop, follow `flow_out` → the input that reads it, which must carry
   a `flow_in` tag, to the next member's port set, and on, until the start is reached: that sequence of port sets is
   one loop. The temperature wires must follow the same path (each member's `temperature_in` is connected to the
   previous member's `temperature_out`). In a two-member loop this is the four-wire picture of §4.1; in a three-member
   loop (a series buffer) each member's flow in comes from the previous member and its flow out goes to the next.
2. **What is refused** (`CircuitWiringError`, naming every port set involved by component and loop name, and the
   channel): a loop that does not close (the path reaches a port set already on another path, or a `flow_out` output
   that nobody reads, or a `flow_in` input that is not connected, while the set is wired at all); a port set wired by
   half (some of its four channels connected, others not — the #1119 rule for the storages' coils, generalised; a port
   set with none of its four channels connected is idle and allowed, like the boiler's hot-water side in
   `basic_household_only_heating`); a `flow_out` output read by two `flow_in` inputs (one flow delivers its heat once;
   a split is a valve component with one loop per branch); a temperature wire that does not follow the flow wire; a
   `flow_out` read by an input that carries no loop tag, or a `flow_in` fed by an output that carries none (the far end
   forgot its tags: "the loop leaves `hds.WaterMassFlowHDS` for `boiler.WaterInputMassFlowSh`, which carries no fluid-loop
   tag"); members of one loop whose `FluidLoop`s state different carriers ("the loop `hds.distribution` is
   space_heating_heat at hds and domestic_hot_water_heat at buffer").
3. **Exactly one pumping port set per loop.** Zero (two pass-through members, nobody moves water) or two (the
   pre-#1117 `gas_boiler_household`: the boiler's `space_heating` loop and the HDS's `distribution` loop both pumping)
   is refused, naming the loop's members and which of them pump.
4. **Exactly one supplying port set per loop** (D10). Zero (a loop of receivers: a buffer's generator loop wired to
   a tank's coil) or two (two generators on one loop with no store between them) is refused, naming the loop's members
   and which of them supply. Pumping and supplying are counted separately: the HDS pumps and does not supply, the buffer
   supplies its distribution loop and does not pump.

**The loop's id** is `<pump owner's component name>.<its loop name>`: `hds.distribution`,
`CondensingGasBoiler.space_heating`, `MoreAdvancedHeatPumpHPLib.dhw`, `SolarThermalSystem.solar_dhw`. A component
name is unique in a run and a component pumps each of its port sets at most once, so the id is unique. A loop that is
refused in rule 3 has no pump owner to name it by, so that message lists the members instead:
"the loop (boiler.space_heating, hds.distribution) has 2 pumping port sets, boiler.space_heating and
hds.distribution; exactly one member of a loop pumps". The id is derived at build and kept on each member's port set,
so the post-run check (§4.4) can put it in its error without any lookup.

**The carrier** (space heating or hot water) is stated once per loop and member, in the `FluidLoop`, and rule 2
checks that every member of a discovered loop states the same one. It is not derivable from the wiring (the same
boiler class serves either), and it is what the Sankey draws the loop as; so it stays declared, once.

**The supplier is declared, the direction per step is read off the supplier's sign** (D10). #1109's `HydronicPort`
carried `role=IN|OUT` to say which end supplies the circuit's heat; the `FluidLoop` carries the same fact as
`supplies`, and the build refuses a loop with no or two supplying port sets, as rule 3 refuses no or two pumping ones
(rule 4). It is a fact of the device, not of the values: a boiler, a heat pump, a substation and a collector supply
every loop they are on; a buffer receives on its generator loops and supplies its distribution loop; a tank receives on
both coils; the HDS receives (it pumps its loop and does not supply it: pumping and supplying are two facts). The first
amendment's rule of 2026-10-27, "the role is not declared, each member's heat into the loop is `m c (T_out − T_in)`
from its own four values, positive at the supplier", is withdrawn (owner, 2026-10-27: "for cooling loops this is
wrong"): `m_out · c · (T_out − T_in)` is the heat a member adds to the water in any loop, but it is positive at the
supplier only while the loop heats; in a cooling loop the chiller sends the water out colder than it came back and the
room circuit warms it, so the sign would have named the emitter the supplier and booked cooling as space heating
flowing the wrong way.

What the values decide is the *direction of the step*, from the supplier's own four columns: `Q_supplier =
m_out · c · (T_out − T_in)` at the supplier. In a step in which it is positive the supplier added heat to the water,
and the loop's heat is booked as the loop's stated heating carrier (`carrier`: space heating or hot water) from the
supplier to the other members, each receiving its own `−m · c · (T_out − T_in)`. In a step in which it is negative the
supplier removed heat, and the same amounts are booked as `COOLING` the other way: from the room-side members, each
of which put `m · c · (T_out − T_in) > 0` into the water, to the supplier, which is the cooling source. In a step in
which it is zero nothing is booked. Around a closed loop the members' `m c (T_out − T_in)` sum to zero by construction
once the flows agree (every `T_in` is the previous member's `T_out`, and the mass check makes every `m` the same), in
either direction; that is #1109's "a circuit balances by construction". This is the rule of Q9's signed energy port
applied to a loop: a loop is a port with two faces, the positive face its heating carrier from the supplier outward,
the negative face `COOLING` from the emitters to the supplier, and the two are implemented by one rule (§4.7).

**A loop that may cool says so** (D10, Q8). A loop whose every member's `FluidLoop` says `may_cool=True` may run
either way. On any other loop a step in which the supplier removed heat is a failure: the post-run check raises
`CircuitError` beside the mass check of §4.4 ("the loop `boiler.space_heating` heats only, and its supplier
`boiler` removed 1,234 W from the water in step 1234, first of 3 steps") (D11); before stage 2 exists, the post-run
balance of stage 1a refuses the run on the same condition from the same columns (§4.7). The storages say `may_cool=True` (they take water of
any temperature; whether a loop cools is for its generator and its emitter to say), the hot-water tank `False` on
both coils, the boiler and the substation `False`, the heat pump from its cooling-mode setting, the HDS from its
configuration (`cooling_enabled`, bead hisim-hds-cooling-option-m4g6), so that a heat pump that may cool wired to an
HDS that may not is a heating-only loop, and a cooling step on it fails by name.

What this replaces: the HDS no longer reads `PARALLEL` or `NO_STORAGE_MASS_FLOW_FIX` to know whether it pumps; its
`FluidLoop` states it (Q3). The boiler no longer infers from a wired input whether it heats someone else's flow
(`GenericBoiler.space_heating_runs_on_distribution_flow`, #1117); its `FluidLoop` states it, and the build refuses the
set wired by half. In the four-wire design every member has a wired flow in, so "my flow input is wired" could not
distinguish the pump owner from a pass-through member anyway (D3 forbids inferring it; D7 forbids writing it anywhere
but at the port). And #1109's `add_hydronic_port`, which named three channels a second time (`component.py:498-559`
on that branch) and checked that they were the component's own, goes: a tag is on the channel, so it cannot name a
foreign one (D8).

### 4.4 The post-run check (D4, D11)

D4's rule, checked where D11 puts it: once, after the simulation, over the result columns of every step. The result
table has one column per output and one row per step (`FiniteResultsCheck` relies on the same layout); an input is
not a column, so a member's `flow_in` and `temperature_in` are read as the columns of the outputs wired to them (the
wiring the build walked, §4.3). One whole-column comparison per port set and check:

```python
class LoopCheck:
    """The loop checks of values (D4, Q1, Q2, D10), over the result table after the run (D11)."""

    @staticmethod
    def require_conserved(results: pd.DataFrame, loops: Sequence[FluidLoopWiring]) -> None:
        for loop in loops:
            for port_set in loop.port_sets:                     # every member (Q1); the pump owner alone under Q1 a
                flow_out = results[port_set.flow_out.column]
                flow_in = results[port_set.flow_in.source_column]   # the output wired to the member's flow in
                failing = (flow_out - flow_in).abs() > port_set.tolerance_in_kg_per_second
                if failing.any():
                    raise CircuitError.of_mass(loop, port_set, failing, flow_in, flow_out)
            heat_added_in_watt = loop.supplier.heat_added_in_watt(results)   # m_out c (T_out − T_in), its own columns
            if not loop.may_cool:
                failing = heat_added_in_watt < -loop.supplier.tolerance_in_watt
                if failing.any():
                    raise CircuitError.of_cooling(loop, failing, heat_added_in_watt)   # "the loop heats only" (D10)
```

The message names the loop, the member, the first failing step with its time, how many steps fail and the two values
at the first one: `Loop 'hds.distribution' (boiler.space_heating, hds.distribution): member 'boiler' sent 0.359 kg/s
in step 1234 (2021-01-13 20:30) where 0.270 kg/s arrived, and so in 592 of 672 steps. Every member but the pump owner
passes the flow through unchanged.` Under Q1 a only the pump owner is checked, and the message can say only that
some member changed the flow.

Why after the run (D11): the check is for catching bugs. Nearly every run passes it, and one that fails is debugged
from its result table, which the run keeps. A per-step `i_doublecheck` would cost a call per member and step — at
60 s over a year, 525,600 steps for each of a twin's members — for a check that almost never fires; the post-run
check costs one vectorized comparison per port set over the whole run, as `FiniteResultsCheck` does for NaN. What
it gives up: a run with a violation in its first step still simulates to its end before it fails, and the failure
is reported after the run rather than at the step. That is accepted: a violation is a modelling bug, not a state a
run can recover from, and its steps are all in the table.

Why converged values are enough (D5): the table holds each step's values after its passes converged; forcing freezes
the controllers after ten passes (`simulator.py:488-489`), and the physics keeps iterating until the convergence
criterion holds or the run aborts at the pass limit (`maximum_passes_per_timestep`). Mass flows and temperatures are
physics outputs, so in every step of the table they are converged, the forced ones included. The 38 to 67 forced
steps of the heat-pump twins in the January week (§6) need no exemption; the audit measured their circuits closing
exactly (0.000 kWh) with forcing on.

Why not `i_simulate` (unchanged from D4): it runs in every pass, and in the first pass of a step a pass-through
member has not yet seen the pump owner's new flow, so the returning flow lags a pass; a check there would fire on
every change of flow.

### 4.5 The error

`CircuitError(HydronicsError)` in `hisim/hydronics.py` (where every hydronic refusal lives), a `ValueError` like
them, raised by the post-run check (§4.4) before anything reads the result table and left to propagate: the run
fails with the circuit, the member, the first failing step, the number of failing steps and both values at the
first one in the message (D4, D11), and `hisim_main` reports it as it reports any exception after the run.
`CircuitWiringError(HydronicsError)` is the build-time twin for a wiring that is no loop or a loop with no or two
pump owners (§4.3), beside #1109's `EnergyPortWiringError`. `FluidLoopError(ValueError)` in `hisim/energy_port.py` is
raised at the declaration, by `add_input`/`add_output`, for a loop tag that cannot describe its channel (§4.1); it
takes the place of #1109's `HydronicPortError`, which goes with `HydronicPort`. Q5 discusses whether a code (as the
energy-system format numbers its refusals `EF-…`) is wanted.

### 4.6 The checks in one table

| Check | When | Where | Catches | Decided? |
|---|---|---|---|---|
| a loop tag fits its channel | declaration | `add_input`/`add_output` (§4.1) | a flow tag on a °C channel; an `_IN` tag on an output; a tag beside an energy port; two channels with one tag | D8 |
| every port set has its four quantities | build | `Component.port_sets()` (§4.3, rule 0) | a loop tagged by half (a member that tagged its flows and forgot its temperatures) | D1, D8 |
| every port set is wired whole or not at all, and its wires close a loop | build | simulator, by following the port sets' wires (§4.3, rules 1 and 2) | a loop that does not close; a half-wired set; a flow-out output nobody reads; a temperature wire that does not follow the flow | D7 |
| the far end of a tagged wire is tagged | build | the same walk (§4.3, rule 2) | a `flow_out` read by an untagged input; a `flow_in` fed by an untagged output | D8 |
| every member of a loop states the same carrier | build | the same walk (§4.3, rule 2) | a space-heating end wired to a hot-water end | D8 |
| a flow is read by one loop | build | the same walk | a flow-out output read by two port sets (a split without a valve) | D3, D7 |
| exactly one pumping port set per loop | build | the same walk, counting the `FluidLoop`s that say `pumps=True` (Q3) | nobody pumps; two pump (the pre-#1117 `gas_boiler_household`) | D3 |
| exactly one supplying port set per loop | build | the same walk, counting the `FluidLoop`s that say `supplies=True` (§4.3, rule 4) | two generators on one loop without a store between them; a loop of two receivers | D10 |
| a loop that heats only never cools | after the run, every step | the post-run check (§4.4); the post-run balance until stage 2 | a supplier removing heat from the water on a loop whose members do not all say `may_cool` | D10, D11 |
| flow back = flow sent, at the pump owner | after the run, every step | the post-run check (§4.4) | any member that changed the flow (named only as "some member") | D4, D11 |
| flow out = flow in, at every member | after the run, every step | the post-run check (§4.4) | the member that changed the flow, by name | D11; Q1 |
| booked heat = `m c (T_out − T_in)`, at every member | after the run, every step | the post-run check (§4.4) | a member booking with a flow or a temperature it did not publish; the HDS's one-step lag without a buffer | D11; Q2 |

### 4.7 Energy ports as channel tags, on inputs as on outputs (D9)

An energy port is already a channel tag: `add_output(..., energy_port=EnergyPort(role, carrier, ...))` says what one
output is in its component's balance, and the post-run check and the Sankey read nothing else (`energy_port.py:1-9` on
#1109's branch). The loop tag of §4.1 is its sibling for a channel that carries water instead of a power. What D9 asks
is where the two are still unlike, and that is only this: an `EnergyPort` can sit on an output, never on an input.

**What that costs today.** A component that *receives* energy through an input has no channel to tag, so it invents
an output that copies the input and points the copy at the input by string. On the building branch
(`building.py:449-588` at 2b9fc8a8c) the building has four such copy-outputs: `HeatReceivedInWatt` (its input
`ThermalPowerDelivered` where positive, `peer_input=self.ThermalPowerDelivered`), `HeatRemovedByCoolingInWatt` (the
same input where negative, `peer_input=` the same), `HeatReceivedFromChpInWatt` (`peer_input=self.ThermalPowerCHP`) and
`InternalHeatGainsFromOccupancy` (`peer_input=self.HeatingByDevices`). The sender side points the other way, from one
output to another, with `peer_output=` (three uses): the HDS's `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt`
(each `peer_output=self.ThermalPowerDelivered`, the signed output the building actually reads;
`heat_distribution_system.py:325-355`) and the occupancy's `BodyHeatToBuildingInWatt`
(`peer_output=self.HeatingByResidents`, `loadprofilegenerator_utsp_connector.py:542`). Every pointer is a string that
names a channel declared elsewhere, and every copy-output is a column that exists for the balance alone — the same
duplication D8 removes from the loops.

**The design.** `add_input(..., energy_port=EnergyPort(...))`, keyword-only, beside `fluid_loop=`; a channel takes one
of the two or neither. For an input port:

- *Its series* is the column of the output wired to it (`Wiring.source_outputs`), exactly as #1109's balance follows a
  circuit leg named by an input to the output connected to it (`ports.py:686-708`). An optional input that is not
  connected is idle and left out, like an open circuit end.
- *Its peer* is the component that output belongs to. For an output port nothing changes: an `OUT` port's peer is the
  component that reads it, a metered port's the meter (`energy_port.py:11-31`).
- *The pairing* becomes symmetric. A transfer between two components is the sender's `OUT` output and the receiver's
  `IN` input on the same wire; both resolve to one column, so "sender and receiver book the same energy" (B4) holds by
  construction for a wired transfer, as it does for a loop (§4.3). What the check still proves is each side's own
  balance: the receiver's `IN` against its losses and stored change, the sender's `OUT` against what it took in. (The
  copy-output it replaces was a copy of the input, so nothing that was checked is lost.)
- *The ambiguity rule* (`Wiring.ambiguous_receiver_faults`, `ports.py:838-866`): an `OUT` output read by two components
  that declare ports is refused today unless the port names its recipient. With input ports it is refused unless
  exactly one reader tags the reading input with an `IN` port of the same carrier; that reader is the peer. The message
  "name its recipient with peer_input or peer_output" becomes "tag the receiving input".
- `EnergyPort.peer_input` and `EnergyPort.peer_output` go; `role`, `carrier`, `environment` and `negated` stay, and so
  does everything that reads them (`is_metered`, `default_boundary`, the meter's `FLOW_TAG_OF_ROLE`,
  `wiring_checks.py:402,449-451`). `EnergyPort` stays the tag's energy half; nothing new is invented for it.

**A channel carries one port.** That rule stays, and it leaves one copy-output standing: a quantity that is both taken
from a boundary node and given to a component. The occupancy's body heat enters it from the residents (`IN`,
`ROOM_HEAT`, `environment=RESIDENTS`) and leaves it to the building, which reads `HeatingByResidents`. Today the
output the building reads carries the boundary port and the copy carries the `OUT` port with `peer_output=`. After D9
the two swap: `HeatingByResidents`, which the building reads, carries `EnergyPort(OUT, ROOM_HEAT)` and finds the
building through its reader; the copy keeps the boundary side, `EnergyPort(IN, ROOM_HEAT, environment=RESIDENTS)`,
which needs no reader. No pointer, one extra column, as today.

**A channel whose sign means its direction** (recommendation; Q9). `ThermalPowerDelivered` is positive while the HDS
heats the building and negative while it cools, and the two directions are two carriers of the Sankey
(`SPACE_HEATING_HEAT` to the building, `COOLING` back out of it; complete Sankey spec, Q-G). Today that split is four
copy-outputs, two per side, each `>= 0`. The tag can carry the split itself: a port with two faces,
`SignedEnergyPort(positive=EnergyPort(OUT, SPACE_HEATING_HEAT), negative=EnergyPort(IN, COOLING))` on the HDS's output
and `SignedEnergyPort(positive=EnergyPort(IN, SPACE_HEATING_HEAT), negative=EnergyPort(OUT, COOLING))` on the building's
input. The balance splits the one column by sign into two series, one per face, and books each on its carrier; the two
ends tag the same wire, so they book the same numbers; the four copy-outputs go. (`negated` is the degenerate case of a
port whose one face is the negative one; it stays as it is.) A fluid loop is the same port with two faces, without
anyone writing them: its positive face is its `carrier` from the supplier to the other members, its negative face
`COOLING` from the room-side members to the supplier, and the supplier's `m c (T_out − T_in)` in the step chooses the
face (§4.3, D10). The balance's series builder splits a column by sign once, for both; a loop whose members do not all
say `may_cool` has no negative face, and a step that would need it fails (the post-run check from stage 2, the
post-run balance in stage 1a). So the HDS–building wire and the heat pump–buffer loop book a cooling step the
same way: `COOLING` from the building to the HDS, from the HDS to the buffer, from the buffer to the heat pump.

**Before and after, the building's input.** Today (`building.py:300-306, 555-588` at 2b9fc8a8c): the input is a plain
input, and two outputs copy it by sign and point back at it:

```python
self.thermal_power_delivered_channel = self.add_input(
    self.component_name, self.ThermalPowerDelivered, lt.LoadTypes.HEATING, lt.Units.WATT, False
)
...
self.heat_received_channel = self.add_output(
    self.component_name, self.HeatReceivedInWatt, lt.LoadTypes.HEATING, lt.Units.WATT,
    output_description="The space heating the building receives, in W, >= 0: its input ThermalPowerDelivered in a step in which it heats, else 0.",
    energy_port=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT, peer_input=self.ThermalPowerDelivered),
)
self.heat_removed_by_cooling_channel = self.add_output(
    self.component_name, self.HeatRemovedByCoolingInWatt, lt.LoadTypes.COOLING, lt.Units.WATT,
    output_description="The heat a cooling device takes out of the building, in W, >= 0: minus its input ThermalPowerDelivered in a step in which that is negative, else 0.",
    energy_port=EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.COOLING, peer_input=self.ThermalPowerDelivered),
)
```

After (D9, with Q9's recommendation): the input carries the port, and the two copy-outputs are gone:

```python
self.thermal_power_delivered_channel = self.add_input(
    self.component_name, self.ThermalPowerDelivered, lt.LoadTypes.HEATING, lt.Units.WATT, False,
    energy_port=SignedEnergyPort(
        positive=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        negative=EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.COOLING),
    ),
)
```

The CHP input is the plain case, `energy_port=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT)`
on `ThermalPowerCHP`, and `HeatReceivedFromChpInWatt` goes. The two gain inputs, `HeatingByResidents` and
`HeatingByDevices`, each carry `EnergyPort(IN, ROOM_HEAT)`; `InternalHeatGainsFromOccupancy` stays as the report column
it also is (`DISPLAY_IN_WEBTOOL`) and loses its port. On the HDS, `ThermalPowerDelivered` carries the signed port's
sender faces, and `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt` go. None of these outputs exists on main: they
were added by the unmerged #1109 and the building branch, so no file, golden or KPI names them.

**Two tag systems on one output** (Q10). Beside `energy_port`, an output carries `postprocessing_flag`, a list of
`lt.InandOutputType`, `lt.ComponentType` and `lt.OutputPostprocessingRules` members. Eleven outputs on the building
branch carry both: the occupancy's `ElectricalPowerConsumption` (`ELECTRICITY_CONSUMPTION_UNCONTROLLED` and
`EnergyPort(IN, ELECTRICITY)`), the electric heater's `ElectricOutputShPower` and `ElectricOutputDhwPower`, the solar
pump's `ElectricityConsumptionOutput` (the same pair), the PV's `ElectricityOutput` (`ELECTRICITY_PRODUCTION, PV` and
`OUT`), the battery's `StoredEnergyIncreaseInWattHour` (`STORED_ENERGY_INCREASE, BATTERY` and `STORED_CHANGE`), and
five where the flag is only `DISPLAY_IN_WEBTOOL` (the boiler's two fuel outputs, the heat pump's cooling electricity,
the building's solar gain and internal gains). Who reads the flags: the KPI computation selects the production,
consumption and battery columns by them
(`kpi_preparation.py:75-160`, `ELECTRICITY_PRODUCTION`, `ELECTRICITY_CONSUMPTION_UNCONTROLLED`,
`ELECTRICITY_CONSUMPTION_EMS_CONTROLLED`, `STORED_ENERGY_INCREASE`, `CHARGE_DISCHARGE`, with `ComponentType.PV`,
`WINDTURBINE`, `BUILDINGS`, `BATTERY`, `CAR_BATTERY` telling the columns apart); the electricity meter and the energy
manager select their dynamic inputs by the same `InandOutputType` tags as *wire* tags (`electricity_meter.py:135-146`,
`controller_l2_energy_management_system.py:258-276`); `DISPLAY_IN_WEBTOOL` is read by nothing in `hisim/` (a scan of
the building branch finds only its declarations). Who reads the ports: the balance and the Sankey, and the meter,
which already *derives* the wire tag from the port's role when it wires itself (`FLOW_TAG_OF_ROLE`,
`electricity_meter.py:128-133, 343-387`: an `IN` electricity port is fed as consumption, an `OUT` port as production).
So for the meter the port is already the one source, and the flag on the component's output is the duplicate; for the
KPIs the flag is still the source, and the two do not mean quite the same thing (the heat pump's `ElectricalInputPowerSH`
carries a port and no consumption flag, `more_advanced_heat_pump_hplib.py:618-625`; a battery's charging power is an
`IN` port that the KPIs must not count as consumption). What to do with that is Q10.

**Port declarations conditional on a position enum** go with D3. The HDS declares its ports only behind a buffer
(`declares_energy_ports`, `heat_distribution_system.py:234-238`: `position == PARALLEL`), because without a buffer its
outputs lag one step and its balance would not close (hisim-pnw7; the dry run of §6 measures it). That condition reads
the enum D3 removes; it goes in the same PR (Q6, stage 3, after pnw7), when the HDS tags its loop and its building port
unconditionally. Until then #1109's rework keeps the condition as it is.

## 5. Open questions

Each question lists the options, what each would mean, and a recommendation. The owner decides; the decision is
written into §2 with its date and into the epic's bead.

### Q0. The named circuit beside "no buses"

HiSim's rule is: no buses, no implicit balancing junctions; a new modelling concept is a separate decision. The
named circuit is a new concept. §4.2 argues it is not a bus, because it holds no value, computes nothing and changes
no result.

- **Option a — confirm D6 as proposed:** HiSim keeps "no buses" and adds the named circuit as port sets that declare
  themselves plus checks. The guideline `system_docs/component_design_principles.md` gets one sentence under B4: "A
  water loop is a named circuit with one declared pump owner; every member passes the flow through and the pump owner
  checks that it came back."
- **Option b — reject the concept:** keep the rules of #1109 (ports paired through the wiring) and the build rule of
  hisim-energy-sankeys-9uoo.20 (a pumping end whose flow nobody reads is refused). This catches the `gas_boiler_household`
  wiring but not a receiver that reads the flow and books with another (the pre-#1117 HDS read nothing; a future one
  might read and ignore), and not the lag.

Recommendation: a. The concept is a name and a check, and the owner's D1 to D5 already assume it.

### Q1. The per-member mass check as well

D4 puts the check in the pump owner. The pump owner's check tells that *some* member changed the flow, not which.
Where it runs is decided by D11: after the run, over the result columns. The question left is only who is checked.

- **Option a — pump owner only (D4 as written).** One check per circuit. In a two-member loop the culprit is the
  other member, so the message can name it; in a three-member loop it cannot.
- **Option b — every member checks its own flow out against its flow in, the pump owner's check being the same rule
  applied to it** (its flow out is what it pumped, its flow in what came back). The error then names the member that
  changed the flow. Cost: one column comparison per member, once per run (D11). For a pass-through member the check is trivially true
  if its code copies the input to the output, which is exactly the point: a member that *computes* a flow of its own
  (the pre-#1117 HDS, `SimpleHeatSource` with the nominal-flow override) fails on the first step it does so.

Recommendation: b. It is the same rule, costs the same after the run as option a does, and the error names the device. Dry run (§6): on today's main
both members that already have both channels pass it in every step.

### Q2. The energy check per member

Each member books a heat for the circuit (`ThermalOutputPowerSh`, `ThermalPowerDelivered`,
`ThermalPowerFromHeatGenerator`, …). The modelling guide requires it to equal `m c (T_out − T_in)` of the member's
own port set (`system_docs/modelling_guide.md` §1), with `c = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K`.

- **Option a — no energy check here.** The post-run energy balance (#1109, `hisim/postprocessing/energy_balance/`)
  checks balances over the run and per step from the result columns, once the HDS and the storages declare ports
  (hydronic stage E, hisim-hydronics-fxix.6). Nothing fails during the run.
- **Option b — every member is checked, in the post-run check of §4.4 (D11),** `|Q_booked − m c (T_out − T_in)| ≤
  tolerance` (Q5) over every step, raising `CircuitError` with the circuit, the member, the first failing step, the
  number of failing steps, the booked and the carried heat in W. The booked heat, the flow out and the temperature
  out are the member's own columns, the temperature in is the column of the output wired to it. It catches one thing
  the balance of option a cannot see today: a member whose booked heat does not match its *own* published quantities
  (the HDS without a buffer, which publishes last step's return and heat beside this step's flow).

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

Recommendation: b, staged after hisim-pnw7 (§7). Until then the energy check is built beside the mass check in the
post-run check and switched on when pnw7 closes the lag; the two systems it would fail are the two pnw7 is measured
on.

### Q3. How a port set says that it pumps

D7 settles where a loop is declared: nowhere but at the components' port sets; the loop is what the wiring connects
(§4.3). The wires themselves stay as they are written today: bare-name items that expand to the classes' default
connections (`inputs: [HeatDistributionSystem, CondensingGasBoiler]` on the buffer), an explicit `input:/from:` pair
only where no default connection exists (`boiler.WaterInputMassFlowSh <- hds.WaterMassFlowHDS` in
`gas_boiler_household`), `connect_automatically=True` in Python setups, the circuit ends of the assembly format
(`{circuit: dhw, member: Boiler}`), which the binding lowers to bare names. The fourth wire, the returning flow, comes
through the classes' default connections like the other three, so no recorded file, setup or assembly gains an input
item for it.

What remains open is how a port set states, **explicitly**, that it pumps — never inferred from where a storage sits,
from list order, from a position enum, or from "my flow input is wired" (D3).

**Fixed in code, where the component has no choice.** A port set whose component can only pump its loop writes
`pumps=True` in its `FluidLoop` (§4.1): the hplib heat pump (both circuits; hplib's flow is authoritative, D2 of the hydronic
coupling spec), the electric heater, the solar collector, the district-heating substation's hot-water side, the
boiler's hot-water side. A port set whose component never pumps declares `pumps=False`: the buffer (every circuit) and
the hot-water tank (every coil); they hold water and move none. Nothing is configured, because nothing can vary.

**Stated by the configuration, where the component can do either.** Four port sets can pump or pass through,
depending on the system: the HDS's `distribution` set (it pumps its design flow behind a buffer, beside a substation
and beside a boiler; it would pass a heat pump's flow through without a buffer), the boiler's `space_heating` set (it
pumps into a buffer; it heats the HDS's flow without one, #1117), the substation's `space_heating` set (it passes the
HDS's flow through today; a substation with a pump of its own would pump) and `SimpleHeatSource`'s brine set. These
need a stated source:

- **Option a — an explicit boolean field on the component's configuration**, named for the fact it states:
  `pumps_space_heating_circuit: true` on the boiler and the substation, `pumps_the_circuit: true` on the HDS,
  `pumps_the_brine_circuit` on `SimpleHeatSource`. The `FluidLoop` reads it at construction
(`FluidLoop("space_heating", carrier=..., pumps=self.config.pumps_space_heating_circuit)`); the build check (§4.3, rule 3)
  refuses a loop in which the booleans add up to no or two pump owners, naming both. The field has a default where one
  role is the usual one (HDS `true`: it pumps in every recorded system; boiler `true`: the buffered boiler pumps;
  substation `false`: HiSim's substation has never pumped), so a recorded file writes it only where the system departs
  from the usual (`gas_boiler_household` and its two copies: `pumps_space_heating_circuit: false` on the boiler; nothing
  on the HDS). For: one field, one fact, visible in the file where it matters, checked against the loop so a wrong
  value is a named refusal, not a silent 33 %. Against: a default is a value the file does not show; the check is what
  makes that safe, and a reviewer who wants every value written can set the field without a default (option d).
- **Option b — inferred from the wiring** ("my flow input is wired, so someone else pumps"), the boiler's rule since
  #1117. Rejected by D3, and impossible once every member has a wired flow in (§4.1).
- **Option c — two presets or two classes per either-way component** (`HeatDistribution.preset_pumping` and
  `preset_on_generator_flow`; or `HeatDistributionPumped` beside `HeatDistributionOnGeneratorFlow`). A preset is a
  configuration under a name, so it is option a written as a preset; two classes split one device's physics in two
  files for one boolean's worth of difference (and `MAIN_CLASS` addresses in every recorded file change).
- **Option d — option a without a default**: the field is required on every either-way component, so every HDS in
  32 recorded files, every buffered boiler and every substation writes its boolean. Nothing is hidden, at the cost of a
  line in every hydronic file and setup that today needs none.

**How this differs from the position enums.** `PositionHotWaterStorageInSystemSetup` described *where a storage
sits* (`PARALLEL`, `SERIES`, `NO_STORAGE_*`), on three components at once, and each component *inferred* from that
whether it pumps, whether its outputs lag, and which inputs it has, with nothing checking that the three agreed. The
boolean says directly *that this device pumps this loop*, on the one device it concerns, and the loop check compares
every member's answer. The enum was a topology description used as a role; the boolean is the role. What the enum also
carried — the HDS's one-step lag without a buffer and the heat pump's fixed flow without one — is not a pumping fact and
is not replaced by the boolean: the lag goes with hisim-pnw7 (§7, stage 3), the heat pump's fixed flow stays its own
configuration until it gets a heater-on-flow mode.

**Nothing is told at build.** With the role in code or in the configuration, the first draft's `join_circuit` has no
purpose and is dropped (D7). The build check is: discover every loop by following the wires, and refuse a loop that
does not have exactly one pumping port set (§4.3).

Recommendation: a. It is D3's "explicit" in the one place a device's own facts live, its configuration; the loop
check turns a wrong or forgotten value into a refusal that names both candidates; and the usual-case defaults keep the
recorded files as they are except in the three buffer-less boiler files. If the owner prefers every value written,
d is the same design with the defaults removed.

### Q4. Relation to #1109's `HydronicPort` and to beads R1, pnw7, g2mx, fxix.15 — decided (D8, 2026-10-27)

#1109 (`origin/sankey-pr3-storages`, in draft since 2026-10-27) gives the storages and generators a `HydronicPort`:
the three output names of a circuit (`mass_flow`, `supply_temperature`, `return_temperature`), a role (`IN`/`OUT`)
and a carrier, declared by `Component.add_hydronic_port` beside the channels it names
(`component.py:498-559` on that branch) and kept in `Component.hydronic_ports`; `Wiring.circuits` /
`refuse_a_broken_wiring` pair the ends through the wiring at build (`ports.py:686-900`). It serves the post-run energy
balance and the Sankey. With D7 the two designs are the same shape: a port that a component declares on its own
channels, paired with its other end through the wiring, with no declaration anywhere else. What #1109 does not have:
the fourth quantity (the returning flow), a pumping flag, a loop walked to closure (it pairs two ends through the one
flow wire), a loop id, and the HDS without a buffer (its docstring exempts that position, citing pnw7 and g2mx).

The options of 2026-10-22, kept as history:

- **Option a — merge #1109, then extend `HydronicPort` into the circuit port set** (recommended on 2026-10-22;
  *decided against*, D8). The port set would have been a `HydronicPort` plus a second mass-flow channel and
  `pumps: bool`; `add_hydronic_port` would have grown those two arguments and kept its `CircuitEnd`. The owner's
  objection: `add_hydronic_port` names three channels a second time, by object, after their `add_input`/`add_output`
  calls, and that second naming "then needs to be kept in sync".
- **Option b — replace #1109** (*rejected*): close it; the circuit port set alone feeds the step check and the balance.
  Loses a reviewed PR and the Sankey work that depends on it (hisim-energy-sankeys-9uoo).
- **Option c — rework #1109 onto the tags** (*decided*, D8). The declaration layer goes and the balance logic stays:

| #1109 | After D8 |
|---|---|
| `HydronicPort` (three names, role, carrier) and `CircuitEnd` (which names are inputs), `energy_port.py:361-558` | go. The names are the channels themselves; "which are inputs" is the channel kind; the role is `supplies` on the `FluidLoop`, and the direction of each step is read off the supplier's sign (§4.3, D10); the carrier is on the `FluidLoop` (§4.1). |
| `Component.add_hydronic_port`, `hydronic_ports`, `CIRCUIT_LEG_UNITS`, `HydronicPortError` | go. `add_input`/`add_output` take `fluid_loop=`; `Component.port_sets()` groups the tagged channels; `FluidLoopError` at the declaration (§4.1, §4.5). |
| `HydronicPort.heat_in_kilowatt_hour` and `heat_in_kilowatt_hours_per_step`, the one rule `m c (T_sup − T_ret) dt` | stay, as functions of `hisim/hydronics.py` beside `circuit_power_w`; applied per member to its own four columns, `m_out c (T_out − T_in)` (§4.3). |
| `DeclaredPorts.circuit_ends_of`, `Wiring.resolve_circuit`, `CircuitColumns` (three columns per circuit) | stay in substance, adapted: an end is a port set, its columns are its four (`flow_in` and `temperature_in` followed through the wire to the previous member's outputs). |
| `Wiring.circuit_peer`, `circuits`, `pairing_fault`: the ends paired through the flow wire; one reader; both ends or neither | stay, generalised to the loop walk of §4.3 (rules 1 to 3): the peer of a member is the next member on the walk; "both ends" becomes "every member tagged"; "one reader" is rule 2. |
| `refuse_a_broken_wiring` at `simulator.py:819`, `EnergyPortWiringError` | stay; `CircuitWiringError` is raised beside (§4.5). |
| `DeclaredPorts.add_circuits`: the heat per circuit from the result columns, an open end that pumps into an unconnected circuit is a fault | stays, per member and port set. |
| the components' circuits: the buffer's three, the tank's two coils with their own returns (#1119), the HDS's one behind a buffer, the generators' | stay, as `FluidLoop`s and tags, each gaining its fourth channel (§4.1, §7). |
| the HDS's exemption without a buffer (`declares_energy_ports`) | stays until stage 3 (§4.7, last paragraph). |
| the tests: `tests/test_energy_port_hydronic.py`, `test_energy_balance_storages.py`, `test_energy_balance_generators.py`, the golden matrix | stay; the ones that build a `HydronicPort` or call `add_hydronic_port` are rewritten against the tags, the balance assertions are unchanged. |

How the rework and this spec's stage 1 relate, and whether they are one PR or two, is Q12.

The beads:

| Bead | What it covers | This spec |
|---|---|---|
| hisim-energy-sankeys-9uoo.20 (R1, P2) | build rule: refuse a pumping end whose flow nobody reads while another component reads its supply | **superseded**: §4.3 rules 2 and 3 (every port set wired whole, the loop walked to closure, exactly one pumping set) refuse the same wiring and more, by walking the loop instead of a heuristic over readers of the supply temperature. Close it when §4.3 lands, citing this spec. |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | the gas boiler's flow gap; a2 built in #1117; keeps the per-step closure, the thermostat-driven flow (hisim-component-physics-4g9.37) and the HDS enum removal | **partly absorbed**: the enum removal moves here (D3, §7); the per-step closure stays with pnw7; the thermostat-driven flow stays with 4g9.37. Re-rate as its note says. |
| hisim-pnw7 (P3) | the HDS's pipe water as a start-of-step node; D25's flow ownership; the HDS enum; district heating's bill | **a dependency and a partner**: pnw7 keeps the node and the bill; the enum removal and D25's "who pumps" move here (the port set's own declaration replaces inference from the circuit). The energy check of Q2 is switched on after pnw7 (§7, stage 4). |
| hisim-hydronics-fxix.15 (P3) | design how a component learns its hydraulic topology, replacing the three enums | **absorbed**: §4.3 and Q3 are that design — a component learns nothing about its topology; it states whether it pumps, and the loop is found through the wiring. Close it when the owner decides Q3; its text points here. |
| hisim-hydronics-fxix.6 (P2) | stage E, `HydronicPort` on every circuit in the balance check (#870/#871) | **re-scoped by D8**: #1109 is its PR; its "HydronicPort on every circuit" becomes "a loop tag on every circuit channel", with the balance logic unchanged. Its text is amended when the rework starts (stage 1a). |
| hisim-energy-sankeys-9uoo.3 (P1) | the energy ports of the water storages and the heat distribution system | **#1109's second bead**, the same re-scope: the storages' and the HDS's circuits are tagged, not declared; their non-circuit ports (losses, stored change, the HDS's heat to the building) are unchanged. |
| hisim-energy-sankeys-9uoo.6 (P1) | the energy ports of the building (branch `sankey-pr4-building`, no PR yet) | **reworked by D9**: the building tags its inputs instead of copying them into outputs with `peer_input=` (§4.7); it is rebased onto the D9 PR (Q6) before it opens. |

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
hydronic refusals, a `ValueError` like them; a build-time `CircuitWiringError(HydronicsError)` (§4.3). A code: the
energy-system format numbers its refusals (`EF-09`, `EF-7A`) because a file author looks them up; a build refusal
raised by the simulator over the connected components (#1109's `EnergyPortWiringError`) and a run-time component error
have no code today, and with D7 there is no file block whose refusal would need one.
Recommendation: b for the mass check, `max(1 W, 0.1 %)` for the energy check, the two exception types above, no
codes.

### Q6. Migration and rollout

What changes, counted on main at 635404f30 (details in §7):

- **Components (9):** `GenericBoiler`, `MoreAdvancedHeatPumpHPLib`, `DistrictHeating`, `ElectricHeating`,
  `SolarThermalSystem`, `SimpleHotWaterStorage` (buffer), `SimpleDHWStorage` (tank), `HeatDistribution`,
  `SimpleHeatSource`. Each writes its fluid loops with their pumping flag (Q3) and tags their channels (D8); pass-through members gain a flow
  output per circuit, pump owners a flow input per circuit; default connections gain the fourth wire; the three enums
  go.
- **Recorded files:** nothing is added to any file (D7). Of the 35 `energy_systems/*.energy_system.yaml` with a water
  circuit (every file but the 9 without one: `air_conditioned_house`, `basic_household`, `default_connections`,
  `dynamic_components`, `electrolyzer_with_renewables`, the two `simple_air_conditioner_*`, the two
  `simple_system_setup_*`), plus `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` and
  `tests/energy_systems/uc1.realized.energy_system.yaml`, only those change whose lines go or come: the 6 that carry a
  `position_hot_water_storage_in_system` line (`gas_boiler_household`, the three district-heating files, the mockup,
  `uc1.realized`) lose it, and under Q3 option a the three buffer-less boiler files (`gas_boiler_household`, the mockup,
  `uc1.realized`) gain `pumps_space_heating_circuit: false` on the boiler (under option d every either-way component in
  every hydronic file gains its boolean). The 22 Python setups change nowhere but the district-heating setup's enum
  line (and the boiler's boolean where a setup has a buffer-less boiler: none today). The assemblies change nowhere
  but the two enum lines (`test_partners.yaml`, the mockup assembly
  `roadmap/declarative_energy_systems/assemblies_mockup/heating/air_source_heat_pump_direct.assembly.yaml`); the
  circuit-end binding of `binding.py` stays as it is. The tags change no file either: a tag is in the class, never in
  a file, and the wires it is checked against are the ones written today.
- **RenoVisor:** the translator runs the grouped twins (`hisim/renovisor/translate.py`, `BY_GENERATOR`,
  `WITH_SOLAR_THERMAL`: gas, oil, pellets, wood chips, hydrogen, heat pump, electric, district heating, and the two
  solar twins), all among the 35; it names no hydronic input and no position enum, so it changes only where a grouped
  file it copies changes: the district-heating twin's enum line in stage 3. No result moves; the heads-up on
  renovisorissues is for that one dropped line, since the base files RenoVisor ships change shape.
- **Goldens:** compare KPIs only (`golden_references/README.md`); new flow-out columns and the port sets move no KPI.
  Expected bit-identical everywhere, except where a check finds a real fault, which on today's main is nowhere for the
  mass checks (§6) and the two buffer-less HDS twins for the energy check (Q2), which is why that check waits for pnw7.
- **Staging into PRs** (one fix per PR, stacked; the split of stage 1 into 1a and 1b is Q12's recommendation):
  1a. *#1109 reworked onto the tags* (D8; the PR is #1109 itself, rebased onto main). `LoopQuantity`, `FluidLoop`,
     `FluidLoopChannel`, `FluidLoopError` in `hisim/energy_port.py`; `fluid_loop=` on `add_input` and `add_output`
     with the declaration checks of §4.1; `Component.port_sets()`; `HydronicPort`, `CircuitEnd`, `add_hydronic_port`,
     `hydronic_ports` and `HydronicPortError` removed; the balance's circuit side reading port sets (the Q4 table);
     every `add_hydronic_port` call site (the buffer's three, the tank's two, the HDS's one, the boiler's two, the heat
     pump's two, the electric heater's, the substation's two, the collector's: 12 call sites declaring 14 ends in 7
     files on the branch)
     replaced by `FluidLoop`s and tags on the channels that exist today — three per port set, since the fourth channel
     comes in 1b; until then `port_sets()` accepts a set with one flow channel (a `flow_out` at the pump owner, a
     `flow_in` at the reader) and 1b tightens it to four; the pumping flag is written where it is fixed in code
     (`pumps=True` on the generators, `False` on the storages) and left out of the either-way components until 1b;
     `supplies` and `may_cool` are written on every loop from the start (D10), because the balance books by them, with
     rule 4 of §4.3 (one supplier) in `refuse_a_broken_wiring` and the heating-only check in the post-run balance.
     Result change: none; the balance report books the heat pump's cooling steps as `COOLING` toward the heat pump
     instead of negative space heating away from it, which moves no total.
     The tests of #1109 rewritten at their declaration sites only. Bit-identical; no new column (the ports it declares
     are on existing channels). Done when #1109's balance report closes for every twin it closes for today.
  1b. *The fourth wire, the pumping flag everywhere and the loop walk* (this spec's own first stage): the new flow
     channels of §7 and their default connections, the config booleans of Q3 with their defaults, `pumps=` on the
     either-way loops, `port_sets()` requiring four quantities, the walk and the refusals of §4.3 (rules 0 to 3),
     `CircuitWiringError`, the three boiler lines; bit-identical, new result columns only. Depends on 1a merged.
  2. *The mass check*: D4's check of the pump owners (and Q1's of every member) and the heating-only check (D10),
     in the post-run check of §4.4 beside `FiniteResultsCheck` (D11), `CircuitError`; bit-identical.
  3. *The enums go*: `PositionHotWaterStorageInSystemSetup` × 3 removed, the HDS's and the heat pump's flow source
     from their pumping flag, the HDS's `declares_energy_ports` with them (§4.7); the 6 recorded lines, the two
     assembly lines, the setup line and the 13 code and test files that name an enum; bit-identical.
     Depends on hisim-pnw7 for the HDS's lag half (today `PARALLEL` also decides whether the HDS publishes this step's
     or last step's values, `heat_distribution_system.py:510-530`); that half is not a pumping fact and no port set
     states it. Recommendation: wait; pnw7 is where the lag goes.
  4. *The energy check* (Q2), after pnw7: switched on in every member; fails nothing on the then-current main.

  Beside the stack, D9 (§4.7) is its own small PR right after 1a and before the building branch opens as a PR:
  `energy_port=` on `add_input`, `peer_input`/`peer_output` removed from `EnergyPort`, the balance following an input
  port through the wiring, the ambiguity rule reworded, the occupancy's two body-heat ports swapped, and, with Q9's
  recommendation, `SignedEnergyPort`; the building branch is then rebased onto it and drops its copy-outputs. On main
  `peer_output=` has twelve uses: nine are the generators' heat ports pointing at their own flow or temperature output
  (`generic_boiler.py:1597,1650`, `more_advanced_heat_pump_hplib.py:738,750,868`, `generic_district_heating.py:358` and its
  hot-water twin, `generic_electric_heating.py:337`, `solar_thermal_system.py`), which #1109 already replaces by its
  circuits, two are the battery's, which the building branch replaces by plain `IN`/`OUT` ports, and one is the
  occupancy's (§4.7); `peer_input=` arrives with the building branch. So after 1a the pointers' remaining uses are the
  seven of §4.7, and the D9 PR removes both fields. It touches recorded files nowhere and moves no KPI. Q10's merge of the tag systems, if the owner wants it, is a bead of
  its own after the Sankey stack.

### Q7. The shape of the tag: how a component names its loop, and one keyword or two

§4.1 proposes a `FluidLoop` value object per loop (name, carrier, pumps), written once, and a `FluidLoopChannel` tag
per channel that points at it and names the quantity. Two choices in that shape are the owner's.

**How a component names a loop.**

- **Option a — a `FluidLoop` object with a plain string name**, `FluidLoop("generator", carrier=..., pumps=False)`,
  held as an attribute (`self.generator_loop`) and referenced by every tag of that loop. The name is the component's
  own word for the loop and appears in the loop id (`buffer.generator` would be the id only if the buffer pumped,
  which it never does; `hds.distribution`, `CondensingGasBoiler.dhw`). The carrier and the pumping flag live in the
  same object, so they are stated once and cannot disagree between the four channels.
- **Option b — a `str` Enum of loop names per component** (`class Loop(str, Enum): SPACE_HEATING = "space_heating";
  DHW = "dhw"` inside the boiler), passed as the `FluidLoop`'s name. The coding style wants a set of strings to be an
  Enum (`coding_style.md` §4), and a typo in a loop name is then an attribute error, not a new loop. It is option a
  with the name typed; the object still carries the carrier and the flag. A component with one loop (the HDS, the
  collector, the heater) gains an Enum with one member.
- **Option c — the tag names the loop by string and carries carrier and pumps itself**,
  `fluid_loop=FluidLoopChannel("generator", LoopQuantity.FLOW_IN, carrier=..., pumps=False)` on each of the four
  channels. No shared object; but the carrier and the flag are then written four times per loop and the build must
  check that the four agree — the duplication D8 is against.

Recommendation: a, with b where a component has two or more loops (the boiler, the heat pump, the substation, the
buffer, the tank): the object is what keeps carrier and flag in one place, and the Enum is the style's rule for a
set of names. Option c is rejected.

**One keyword or two.** `add_output` has `energy_port=` today (about 60 uses on the building branch).

- **Option a — two keywords**, `energy_port=` (an `EnergyPort`, or with Q9 a `SignedEnergyPort`) and `fluid_loop=`
  (a `FluidLoopChannel`), on both `add_input` and `add_output`; the declaration refuses both on one channel. No
  existing call changes; the two kinds of tag are visibly different things (a power with a role, or a leg of a loop
  with a quantity).
- **Option b — one keyword**, `port=`, taking either type. Renames 60 call sites for the sake of one name, and the
  type says which kind it is anyway.

Recommendation: a.

### Q8. Which member supplies a loop, and whether a loop may cool

*Withdrawn 2026-10-27 (wrong for cooling loops, owner):* the first amendment recommended that no member declares a
role, because "the heat a member gives to its loop is `m_out · c · (T_out − T_in)` from its own four columns,
positive at a supplier, negative at a receiver", and the Sankey would draw the link in the direction of the sign. In a
cooling loop the supplier is the member with the negative sign (a chiller sends the water out colder than it came
back; the room circuit warms it), so that rule would have named the emitter the supplier and booked cooling as space
heating flowing the wrong way, and a loop that heats in one step and cools in the next has no one sign at all. The
owner's correction is D10; §4.3 holds the design. What this question still asks is where the two new facts live and
what they are called.

**Decided by D10:** every `FluidLoop` states `supplies` (exactly one per loop, checked at build like `pumps`); the
direction of each step is the sign of the supplier's own `m c (T_out − T_in)` (positive: the loop's heating carrier
from the supplier outward; negative: `COOLING` from the emitters to the supplier — the rule of Q9's signed port); a
loop that may cool says so, and a cooling step on any other loop fails the run.

**Where "may cool" is stated.**

- **Option a — on every member's `FluidLoop`, and a loop may cool only if every member allows it** (§4.3). The HDS
  takes it from its configuration (`may_cool=self.config.cooling_enabled`), the heat pump from its cooling-mode
  setting, the boiler and the substation write `False`, the storages write `True` (a buffer takes water of any
  temperature; a tank's coils `False`). The build needs no agreement rule: a heat pump that may cool wired to an HDS
  with `cooling_enabled: false` is a heating-only loop, and the first cooling step fails naming the loop and the
  supplier, which is the right message (the emitter cannot cool, so the heat pump must not). Four booleans per
  two-member loop, each a fact of its own device.
- **Option b — on the supplier only.** One statement per loop; the emitter's `cooling_enabled` is then not part of
  the loop's declaration, and a heat pump in cooling mode on a radiator circuit that cannot cool would be booked as
  cooling without complaint until the building's own balance notices nothing at all (the HDS would deliver "negative
  heat" to the building, which the building's signed port of Q9 books as cooling too). Fewer words, one check less.
- **Option c — derived from the carrier**: a third carrier `COOLING_LOOP` for loops that only cool, and `may_cool`
  implied for the space-heating carrier. Rejected: the loops that matter switch between the two (the HDS with
  `cooling_enabled`, the heat pump through a buffer), so the carrier cannot say it.

**Names.** `supplies` (reads as a statement, `coding_style.md` §5: this member supplies the loop's heat) and
`may_cool` (this member allows the loop to remove heat); the alternative `is_generator_end` says the same with more
words, and `cools` would read as "this member cools", which is a per-step fact, not a declaration. For the HDS, the
loop reads `cooling_enabled` as it is spelt in hisim-hds-cooling-option-m4g6, so one configuration line decides both
the HDS's own behaviour and its loop's declaration; the heat pump's loop reads whatever names its cooling mode on that
class.

**Where the heating-only violation is raised.** In the post-run check, beside the mass check (D4, D11, §4.4), from
stage 2; until then in the post-run balance of stage 1a, which reads the same four columns. Both name the loop,
the supplier, the step and the heat removed in W. It is a `CircuitError` like the mass violation: a loop that
removes heat it declared it never would is the same kind of fault as a member that changes a flow it declared it would
pass through.

Recommendation: a, with the names above. It is explicit at every member (D3's rule for pumping, applied to cooling),
it needs no agreement check at build, and it ties the HDS's loop to the one configuration line that already says
whether the emitter can cool.

### Q9. A channel whose sign means its direction: two faces on one port, or two outputs

`ThermalPowerDelivered` is positive while the HDS heats and negative while it cools, and the two directions are two
carriers of the Sankey (`SPACE_HEATING_HEAT` into the building, `COOLING` out of it). Today both the HDS and the
building split it into two copy-outputs, `>= 0` each, with a pointer back at the signed channel (§4.7). The same
need will come back wherever one wire carries heat in both directions over the year.

- **Option a — a port with two faces** (§4.7): `SignedEnergyPort(positive=EnergyPort(...), negative=EnergyPort(...))`
  on the signed channel at both ends; the balance splits the column by sign into two series, one per face. No copy
  column, no pointer, one wire, both ends on the same numbers. One new small type in `energy_port.py`; the balance's
  series builder learns to split a column.
- **Option b — keep the split outputs on the sender, and let the receiver read them.** The HDS keeps
  `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt` (each with its port, each finding its reader through its own
  output), and the building gains two inputs that read them, tagged `IN` and `OUT`; the signed `ThermalPowerDelivered`
  stays the physics wire without a port. No new type; but two more wires and two more default connections between the
  HDS and the building for a fact the one wire already carries, and the building then reads three channels for one
  quantity.
- **Option c — one carrier, signed.** Tag the wire `SPACE_HEATING_HEAT` at both ends and let the heat go negative
  while cooling, as the heat pump's space-heating circuit already does on #1109 ("one circuit whose heat turns
  negative while it cools", `more_advanced_heat_pump_hplib.py:695-696`). Simplest, but it undoes the Sankey spec's
  decision to show cooling as its own carrier (Q-G), and the building's cooling would disappear into negative heating.

Recommendation: a. It is the tag doing what the copy-outputs did, at both ends, on one wire. A fluid loop is the same
port with two faces (§4.3, D10): the heat pump's cooling circuit, which #1109 books as negative space heating, is then
booked as `COOLING` from the buffer to the heat pump in the steps in which the heat pump removes heat, by the one rule
that splits the HDS–building wire. Option c would leave the two inconsistent.

### Q10. Two tag systems on one output: `postprocessing_flag` and `energy_port`

§4.7 counts eleven outputs on the building branch that carry both a `postprocessing_flag` and an `energy_port`, and
names who reads which. The two overlap on three facts — an electricity output is consumption
(`ELECTRICITY_CONSUMPTION_UNCONTROLLED` ↔ `EnergyPort(IN, ELECTRICITY)`), production (`ELECTRICITY_PRODUCTION` ↔
`OUT`) or a storage's stored-energy change (`STORED_ENERGY_INCREASE` ↔ `STORED_CHANGE`) — and differ elsewhere: the
flag also says *which device* for the KPIs (`ComponentType.PV`, `BATTERY`, `CAR_BATTERY`, `BUILDINGS`), whether the
energy manager controls the consumer (`ELECTRICITY_CONSUMPTION_EMS_CONTROLLED`, a control fact no port knows), and
`DISPLAY_IN_WEBTOOL`, which nothing reads. And the overlap is not exact: the heat pump's `ElectricalInputPowerSH`
carries an `IN` port and no consumption flag; a battery's charging power is an `IN` port the KPIs must not count as
consumption. So a merge changes what the KPIs select, and the KPIs are what the goldens compare.

- **Option a — keep both, and test that they agree.** The flag stays the KPI computation's and the energy manager's
  selection, the port the balance's and the meter's. One repository test asserts, over every component class, that an
  output flagged `ELECTRICITY_CONSUMPTION_*` carries `EnergyPort(IN, ELECTRICITY)`, one flagged `ELECTRICITY_PRODUCTION`
  carries `OUT`, one flagged `STORED_ENERGY_INCREASE` carries `STORED_CHANGE`, and the reverse where the port's component
  is not a storage. Drift between the two becomes a failing test; nothing in KPI computation or the meters changes;
  bit-identical. The eleven double declarations stay.
- **Option b — the KPIs and the energy manager select by the port.** `kpi_preparation.filter_results_according_to_
  postprocessing_flags` reads role and carrier instead of the three flow flags, with a rule for storages (a
  `STORED_CHANGE` port is the battery's column; an `IN`/`OUT` electricity port of a component that also declares a
  `STORED_CHANGE` is charge/discharge, not consumption/production); the energy manager's channels keep their wire tags,
  which the meter already derives from the port (`FLOW_TAG_OF_ROLE`) and the manager would derive the same way;
  `ELECTRICITY_CONSUMPTION_EMS_CONTROLLED` stays a flag (or becomes a field of the port, `ems_controlled: bool`, which
  is a control fact on an energy tag). The three flow flags leave every output; `ComponentType` and
  `DISPLAY_IN_WEBTOOL` stay as flags. Every KPI must come out identical, which the goldens gate; where one differs (the
  heat pump's untagged `ElectricalInputPowerSH` is a candidate), the difference is a finding, not a tolerance. About
  100 lines of KPI code, the manager's channel declarations and 11 component declarations.
- **Option c — derive the flag from the port at construction** (`ComponentOutput.__init__` appends
  `ELECTRICITY_CONSUMPTION_UNCONTROLLED` to the flags of every `IN` electricity port, …). Rejected: it would add the
  flag to the battery's charging port and to the heat pump's electricity, and the KPIs would change silently; the
  rule for storages has to live in the reader (option b), not in the declaration.

Recommendation: a now, in its own small PR (the test; it holds on today's main and the building branch), and b as a
bead after the Sankey stack has landed, when every electricity output carries a port and the KPI rule can be written
against a complete set. Option b is the end state the owner's question points at; it is not a step of this spec,
because none of its stages touches a KPI.

### Q11. Whether an untagged water channel is refused outright

§4.1 enforces the tags at the declaration (a tag that cannot describe its channel) and at build (the far end of a
tagged wire must be tagged; a port set must be whole). What neither catches is a loop whose both ends are untagged:
it stays what every loop is today, undeclared in the balance report.

- **Option a — the two rules of §4.1 and the recorded files' test.** Stage 1b's test builds every recorded energy
  system and asserts the loops it finds, by id, against the expected list; a forgotten tag in any recorded system
  fails it, and a new component wired into a recorded system meets a tagged far end. The only uncaught case is two
  new, untagged components wired to each other in a system no test builds — which the balance reports as undeclared.
- **Option b — a third rule at build**: a component that declares at least one loop and has a kg/s channel of load
  type `WARM_WATER` outside any loop is refused. Catches a forgotten fourth tag inside a component that tagged its
  other loops; but it needs an explicit way to say "this kg/s channel is no loop leg" for the tank's tap draw and the
  like (a sentinel such as `fluid_loop=NOT_A_LOOP`), and it says nothing about temperatures, which are mostly not loop
  legs (the building's indoor air, the weather, every set point).
- **Option c — a repository test over every component class** instead of a build rule: instantiate every class from
  its presets, list every kg/s channel, and assert that each carries a loop tag or is in the test's own list of draws
  and taps. Explicit, no production sentinel, and the list may only shrink (the ratchet pattern of
  `coding_style.md` §2).

Recommendation: a plus c. The build rules make a half-tagged loop impossible, the recorded-files test makes a
forgotten tag in any shipped system a failure, and the class-wide test makes the remaining case — a new component
class — a failure at the pull request that adds it, without a sentinel in production code.

### Q12. #1109's rework and this spec's stage 1: one pull request or two

D8 reworks #1109 onto the tags; stage 1 of this spec adds the fourth wire, the pumping flag everywhere and the loop
walk. Both touch the same 14 loop declarations.

- **Option a — one PR**: #1109 reworked and extended at once; its port sets have four channels from the start, so
  `port_sets()` never needs a transitional three-channel form. One review instead of two; but #1109's reviewed scope
  (the balance side, 1,500 lines of tests) is then mixed with this spec's first new behaviour (the refusals of §4.3,
  the config booleans of Q3, the three boiler lines), and the Sankey work waiting on #1109 (hisim-energy-sankeys-9uoo)
  waits for both.
- **Option b — two PRs** (Q6, stages 1a and 1b): first #1109 on tags with the three channels it has today and no new
  behaviour (bit-identical, no new column, its tests rewritten at their declaration sites only); then the fourth
  wire, the flags, the walk and the refusals as this spec's own first PR. Two reviews; a transitional rule in
  `port_sets()` (a set with one flow channel is whole until 1b) that 1b removes; the Sankey stack unblocks after 1a.

Recommendation: b. It keeps #1109 the PR it was reviewed as, lands the Sankey stack sooner, and gives this spec's
first behaviour its own review and its own tests (`small-prs`: one fix per PR).

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

**Components.** Per component, the fluid loops (D8: one `FluidLoop` per loop, a tag on each of its channels; the
loop's name is the component's own), the new channels and what leaves:

| Component | Fluid loops (name: can pump?; supplies and may_cool per D10) | Gains | Loses |
|---|---|---|---|
| `GenericBoiler` | `space_heating`: either (`pumps_space_heating_circuit`, default true, Q3); `dhw`: pump owner; supplies both, cools neither | flow in on `dhw`; the flow in on `space_heating` exists (`WaterInputMassFlowSh`, #1117) and becomes the circuit's | `space_heating_runs_on_distribution_flow` inferred from wiring (the port set's flag instead, Q3) |
| `MoreAdvancedHeatPumpHPLib` | `space_heating`, `dhw`: pump owner (no heater-on-flow mode yet); supplies both; `space_heating` may cool from its cooling-mode setting | flow in on both | `PositionHotWaterStorageInSystemSetup` (`PARALLEL`/`SERIES`/`NO_STORAGE`); `m_dot_ref` stays as the fixed flow of a heater-on-flow mode if one is built later |
| `DistrictHeating` | `space_heating`: either (`pumps_space_heating_circuit`, default false, Q3); `dhw`: pump owner; supplies both, cools neither | flow in on `dhw`; the space-heating pair exists | — |
| `ElectricHeating` | `dhw`: pump owner; supplies, never cools | flow in | — |
| `SolarThermalSystem` | `solar_dhw`: pump owner; supplies, never cools | flow in | — |
| `SimpleHotWaterStorage` (buffer) | `generator`, `secondary_generator`, `distribution`: never pumps; receives on the generator loops, supplies `distribution`; `may_cool=True` on all | flow out on each (pass-through) | `PositionHotWaterStorageInSystemSetup` (`PARALLEL_TO_HEAT_SOURCE`/`SERIES_TO_HEAT_SOURCE`): a series buffer is a three-member circuit |
| `SimpleDHWStorage` (tank) | `generator`, `secondary_generator`: never pumps; receives on both, never cools | flow out on each | — |
| `HeatDistribution` | `distribution`: either (`pumps_the_circuit`, default true, Q3); receives; `may_cool` from `cooling_enabled` (hisim-hds-cooling-option-m4g6) | flow out (pass-through when behind a pump owner); flow in in every role | `PositionHotWaterStorageInSystemSetup` (4 members); `pumps_its_design_flow` from the enum (from the port set's flag instead); the lag half goes with pnw7 |
| `SimpleHeatSource` | brine circuit: either (`pumps_the_brine_circuit`, Q3); supplies | flow out | `use_external_massflow_as_signal_input_for_nominal_massflow` (a second pump owner by another name) |

**Results.** No KPI moves in any stage (the port sets are metadata on existing and new channels; the pass-through
outputs are copies; the enums are replaced by a flag that states the same fact). New result columns: one flow-out per
pass-through member and circuit (about 2 to 4 per system). Stage 4 (the energy check) fails nothing once pnw7 has
landed.

**Public interfaces.** New: the new flow channels (addresses in files), the config booleans of the four either-way
port sets (Q3), `CircuitError`, `CircuitWiringError`; for component authors, `FluidLoop`, `FluidLoopChannel`,
`LoopQuantity` and `FluidLoopError` in `hisim/energy_port.py`, `fluid_loop=` on `add_input` and `add_output`,
`energy_port=` on `add_input` (D9), `SignedEnergyPort` (Q9), `Component.port_sets()`. Removed: the three
`PositionHotWaterStorageInSystemSetup` enums and their config field (6 recorded lines, 13 code and test files),
`SimpleHeatSource.use_external_massflow_as_signal_input_for_nominal_massflow`, the HDS's `declares_energy_ports`;
from the unmerged #1109, `HydronicPort`, `CircuitEnd`, `HydronicPortError`, `Component.add_hydronic_port`,
`Component.hydronic_ports` and `CIRCUIT_LEG_UNITS` (D8); `EnergyPort.peer_output` (on main, twelve uses, nine of
which #1109 already replaces), `EnergyPort.peer_input` (on the unmerged building branch) and the five copy-outputs of
§4.7 (D9). No file format changes: no new top-level key, no new
assembly syntax, no new `Simulator` method (D7); a tag lives in the class, never in a file. RenoVisor: a heads-up on renovisorissues when
the enum line leaves the district-heating twin it copies, since the translator's base files change shape
(`system_docs/results_and_interfaces.md` §5); no result changes.

**Goldens and twins.** Goldens compare KPIs only and stay bit-identical in every stage. Re-recorded: the three
buffer-less boiler files in stage 1 (the boiler's boolean), the 6 files, the setup and the two assembly files with an
enum line in stage 3; everything else stays byte-identical. `tests/assemblies/` twin gates and
`tests/test_energy_system_*` parity tests are the gate.

**Verification per stage.** Stage 1a: #1109's tests at their declaration sites (a tag on the wrong unit or kind, a
tag beside an energy port, two channels with one tag, each refused at the declaration; a port set grouped from its
tags), its balance tests unchanged, its balance report closing for every twin it closes for today; a loop with no or
two suppliers refused at build; a cooling loop (a supplier that removes heat in a step, every member `may_cool`)
booked as `COOLING` from the room-side member to the supplier and as the heating carrier the other way in a heating
step, the two directions summing to the loop's `m c (T_out − T_in)` at the supplier; a heating-only loop (one member
without `may_cool`) whose supplier removes heat in a step fails the run naming the loop, the supplier and the step;
the heat pump twins' cooling steps booked as `COOLING` with every total unchanged. Stage 1b: a test
that wires two pumping port sets on one loop, none, a loop that does not close, a port set wired by half, a loop
tagged by half, a `flow_out` read by an untagged input, two members stating different carriers, a flow read by two
port sets, a temperature wire that does not follow the flow, each refused with the loop's members and the channel
named; every recorded file builds and every loop gets the expected id (Q11, option a), and every component class's
kg/s channels are tagged or listed (Q11, option c). The D9 PR: an input port resolved to the column of the output
wired to it and to its source as the peer; an unconnected optional input port left out; the ambiguity rule with and
without a tagged reader; a signed port split into its two faces; the building branch's balance closing as it does
today, with its copy-outputs gone. Stage 2: a test that wires a receiver which publishes a flow of its own on a loop it does not pump and asserts
`CircuitError` from the post-run check with the loop id, the member, the first failing step, the number of failing steps and both flows (the pre-#1117 `gas_boiler_household`
wiring rebuilt as a fixture, with the HDS pumping 0.27 kg/s and a boiler pumping 0.359 kg/s — which stage 1's build
check already refuses, so the fixture declares the boiler as pass-through and makes it publish 0.359 kg/s anyway);
the heating-only check moved into the post-run check: a boiler fixture that publishes a supply colder
than its return for one step raises `CircuitError` naming the loop, the boiler, the step and the heat removed, and a
heat pump fixture with `may_cool` on every member of its loop passes the same step;
every recorded twin runs the January week with zero errors; the `test_*_time_resolution` and twin tests
bit-identical. Stage 3: every test that named an enum rewritten against the pumping flag; the 6 recorded lines gone.
Stage 4: the energy check on every recorded twin, zero errors, after pnw7.

## 8. Beads

Epic hisim-hydraulic-circuits-wci7 (P2, `components`, `owner-decision` while Q0 to Q3 and Q5 to Q12 are open). The
child beads are filed when the owner has decided, one per stage of Q6; the related beads and what happens to them are
in Q4. This PR files and edits no bead (a bead change goes through `br`); the rows marked *to file* are the list for
that.

| Bead | Content | PR |
|---|---|---|
| hisim-hydraulic-circuits-wci7 (P2, epic, `owner-decision`) | this spec; the owner's decisions D1 to D5 and D7 to D11; the questions Q0 to Q3 and Q5 to Q12 | #1122 |
| hisim-hydronics-fxix.6 (P2) | stage E, every circuit in the balance check; re-scoped by D8 from `HydronicPort` to loop tags, the balance logic unchanged | #1109, reworked onto the tags (stage 1a, Q4, Q12) |
| hisim-energy-sankeys-9uoo.3 (P1) | the energy ports of the water storages and the HDS; the same re-scope | #1109 (stage 1a) |
| hisim-energy-sankeys-9uoo.6 (P1) | the energy ports of the building; reworked by D9 onto input ports, no copy-outputs | branch `sankey-pr4-building`, rebased onto the D9 PR |
| hisim-energy-sankeys-9uoo.20 (P2) | R1, the build rule for a pumping end whose flow nobody reads | superseded by stage 1b (Q4) |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | the gas boiler's flow gap; a2 built | #1117; its enum half moves to stage 3 (Q4) |
| hisim-pnw7 (P3) | the pipe-water node and district heating's bill | partner of stages 3 and 4 (Q4) |
| hisim-hydronics-fxix.15 (P3) | how a component learns its topology | absorbed by §4.3 and Q3 (Q4) |
| *to file*, child of wci7 (P2, `components`) | D9: `energy_port=` on `add_input`; `peer_input`/`peer_output` and the copy-outputs go; `SignedEnergyPort` if Q9 a | the D9 PR, after stage 1a |
| *to file*, child of wci7 (P2, `components`) | stage 1b: the fourth wire, the pumping flag everywhere, the loop walk and its refusals, the Q3 booleans | after stage 1a |
| *to file*, child of wci7 (P2, `components`) | stage 2: the mass check and the heating-only check (D10) in the post-run check (D11), `CircuitError` | after 1b |
| *to file*, child of wci7 (P2, `components`) | stage 3: the three enums and `declares_energy_ports` go | after pnw7 |
| *to file*, child of wci7 (P3, `components`) | stage 4: the energy check | after pnw7 and stage 3 |
| *to file*, child of hisim-energy-sankeys-9uoo (P3, `cleanup`) | Q10 a: the test that `postprocessing_flag` and `energy_port` agree on every output | its own small PR |
| *to file*, child of hisim-energy-sankeys-9uoo (P3, `cleanup`, `owner-decision`) | Q10 b: the KPIs and the energy manager select by the port; the three flow flags retire | after the Sankey stack |
