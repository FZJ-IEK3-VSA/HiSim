# Spec: named hydraulic circuits — every water loop has an id, one declared pump owner and a conservation check after the run

- **Status:** draft; nothing is built. Owner: Noah. Epic: hisim-hydraulic-circuits-wci7 (P2, `components`,
  `owner-decision`). PR: #1122 (spec only; no code, result, golden or recorded file changes).
- **Decided** (§7): D1 to D5 and D7 (owner, 2026-10-22); D8 to D11 (owner, 2026-10-27; D11 was agreed on
  2026-10-23). D6 is an agent proposal that waits for Q0.
- **Open** (§6): Q0 to Q3 and Q5 to Q12. Q4 is decided by D8.
- **Builds on:** `roadmap/hydronic_coupling_spec.md` (§3 circuits and pump owners, §3.5 `HydronicPort`, §4.6 D25 with
  its amendment of 2026-10-22, D1 with its exception of 2026-10-22); #1117 (the boiler without a buffer heats the flow
  the heat distribution system pumps); #1119 (a storage reads the flow of every circuit whose temperature it reads);
  the open PR #1109 (`HydronicPort`, `Wiring.refuse_a_broken_wiring`; `origin/sankey-pr3-storages` at da66cfe43, in
  draft since 2026-10-27 for its rework onto the tags of D8); the building branch `sankey-pr4-building` (the
  building's energy ports, bead hisim-energy-sankeys-9uoo.6, at 2b9fc8a8c, no PR yet); the circuit audit of
  2026-10-22 (`/home/noah/hisim/agent-tmp/circuits/report.md`); the boiler investigation
  hisim-boiler-flow-mismatch-4awa (`/home/noah/hisim/agent-tmp/4awa/report.md`).
- **Line numbers** refer to main at 635404f30 unless a branch or commit is named.

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
  inputs; none knows that a mass flow published at one end of a loop must be the mass flow at the other end.
- **Who pumps is decided by position enums, per component, with nothing checking that they agree.** Three enums
  describe one topology fact: `PositionHotWaterStorageInSystemSetup` in `heat_distribution_system.py:42` (`PARALLEL`,
  `SERIES`, `NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR`, `NO_STORAGE_MASS_FLOW_FIX`), in `simple_water_storage.py:87`
  (`PARALLEL_TO_HEAT_SOURCE`, `SERIES_TO_HEAT_SOURCE`) and in `more_advanced_heat_pump_hplib.py:89` (`PARALLEL`,
  `SERIES`, `NO_STORAGE`). The HDS pumps its design flow in `PARALLEL` and `NO_STORAGE_MASS_FLOW_FIX` and reads a
  flow otherwise (`:153-156`, `:238-246`); its default is `PARALLEL`, so an HDS wired directly to a heat pump without
  a position set pumps beside the heat pump's own pump, silently. The audit found the fault wired only in
  `gas_boiler_household` and its two copies, but a heat pump without a buffer (the hplib model always pumps,
  0.333 kg/s in `NO_STORAGE`) or a `SimpleHeatSource` with `use_external_massflow_as_signal_input_for_nominal_massflow`
  (`simple_heat_source.py:436-438`, which replaces the flow it reads by its own 0.5 kg/s) would wire it again.
- **The receiving end's book is invisible.** Every loop today publishes three quantities: the pump owner's flow and
  the two temperatures. Nothing comes back, so a device that silently uses a flow of its own (the HDS before #1117)
  cannot be told apart from one that passes the flow through.
- **The one remaining gap in a recorded system is also invisible.** Without a buffer, the HDS publishes its return
  temperature and its delivered heat one step late (`heat_distribution_system.py:530`). The district-heating twin's
  substation bills `m c (T_sup − T_ret_published)` on this stale return: −17.6 kWh/yr (−0.16 %) at 900 s, −44.7 kWh
  (−0.41 %) at 3600 s (hisim-pnw7, closed hisim-energy-sankeys-9uoo.12). Only a check of each device's book against
  its own published quantities, in every step, would show it.

The rules exist: `hydronic_coupling_spec.md` §3.1 (one pump owner per circuit; the other end reads its flow),
`system_docs/component_design_principles.md` B4 (one owner per quantity; sender and receiver book the same energy),
D25. They are enforced nowhere. This spec gives HiSim the one concept that makes them checkable: the named hydraulic
circuit. It is not a bus (§3.2, Q0).

## 2. Terms

- **Circuit** (loop): one closed water loop, in which the same water passes through two or more devices in series
  and returns to the first. Recorded systems have only two-member loops (a generator and a store, a store and the
  HDS, a generator and the HDS, a collector and a tank). A buffer in series with its heat source
  (`SERIES_TO_HEAT_SOURCE`, no recorded system) is a three-member loop: generator → HDS → buffer → generator.
- **Member**: a device connected to a circuit. A device may be a member of several circuits (a buffer: its generator
  circuit, its distribution circuit, a secondary generator circuit; a boiler: `space_heating` and `dhw`).
- **Fluid loop** (`FluidLoop`, D8): the one object through which a component names a loop it can be a member of, in
  its own vocabulary: the loop's name as the component knows it, the heat the loop carries while it heats
  (`carrier`), and whether this member pumps it (`pumps`, D3), supplies it (`supplies`, D10) and lets it cool
  (`may_cool`, D10). Written once per component and loop (§3.1).
- **Loop tag** (`FluidLoopChannel`, D8): what one channel carries in its `add_input` or `add_output` call to say
  which fluid loop of the component it belongs to and which of the four quantities it is
  (`fluid_loop=self.generator_loop.flow_in`). The tag is the only declaration of a port set. The assemblies' circuit
  names (`{circuit: dhw, member: …}` in `energy_systems/assemblies/`) are a binding-time vocabulary of the assembly
  format and are not this.
- **Port set** (D1): the four channels one member has per circuit: flow in (`mass_flow_in_kg_per_second` of the water
  arriving, an input), flow out (`mass_flow_in_kg_per_second` of the water leaving, an output), temperature in
  (`temperature_in_celsius` of the water arriving, an input) and temperature out (`temperature_in_celsius` of the
  water leaving, an output). The set is the four channels of one component whose loop tags name the same
  fluid loop; the build finds it by grouping them (§3.3). In a two-member loop member A's flow out and temperature
  out are member B's flow in and temperature in, and B's are A's. "Supply" and "return" remain the names of the two
  legs: the generator's temperature out is the supply, the receiver's temperature out the return.
- **Pump owner** (D3): the one member of a circuit that decides the circuit's mass flow for the step, in kg/s, and
  publishes it as its flow out. Every other member is a **pass-through**: its flow out is its flow in, unchanged.
- **Supplier** (D10): the one member of a circuit that is its generator end. Pumping and supplying are two facts:
  the HDS pumps its loop and does not supply it; the buffer supplies its distribution loop and does not pump.
- **Receiver**: any member that is not the supplier (in a two-member loop, the member that is not the generator).
- **Circuit id**: the name of one loop, `<pump owner's component name>.<its loop name>` (`hds.distribution`,
  `CondensingGasBoiler.dhw`, `SolarThermalSystem.solar_dhw`), derived at build, never written in a file (D7). It is
  unique within one energy system and appears in every error that concerns the loop.
- **Loop discovery**: how the build finds a loop from the wiring alone (§3.3): from any port set, its flow-out output
  is read by exactly one other port set's flow-in input; follow it, and repeat, until the start is reached again.
- **Bus**: a balancing junction that collects several flows of one carrier and computes the remainder (HiSim's
  electricity meter and energy manager sum many producers and consumers through tag-selected inputs). HiSim has no
  bus, by owner decision (`roadmap/complete_energy_sankeys_spec.md`, Q-J, 2026-10-20: "HiSim explicitly does not use
  buses as a concept, since they bring a whole host of problems"), and this spec adds none (§3.2).
- **Build**: the moment after `Simulator.connect_all_components` and before step 0 (`Simulator.run_all_timesteps`,
  `simulator.py:816`), where the energy-port rules of #1109 also run. A build check refuses a system before it
  simulates.
- **Post-run check** (D11): a check of the converged values of every step, run once after the simulation over the
  result table, as one column operation per check and port set, where `FiniteResultsCheck` runs
  (`postprocessing_main.py:307`), before anything reads the table. The table holds one column per output and one row
  per step; an input is read through the output wired to it.
- **Forced step**: a step whose controllers were frozen after ten passes (`force_convergence`, `simulator.py:488-489`)
  so that the physics can settle; the physics still iterates to the convergence criterion (`|Δ| ≤ 1e-4` on every
  output, `SingleTimeStepValues.is_close_enough_to_previous`, `component.py:224-230`) or the run aborts at the pass
  limit. A forced step is intended behaviour, not a failure.
- **Tolerance**: the largest `|flow in − flow out|` in kg/s, or `|booked heat − carried heat|` in W, that the
  post-run check lets pass (Q5).

## 3. Design

### 3.1 The fluid loop and its tags: four tagged channels per member and loop

Every hydronic component has, per loop it can be a member of, one port set of four channels (D1). Nothing declares
the set: each channel carries a loop tag in its own `add_input` or `add_output` call (D8), and the build groups a
component's tagged channels by loop into its port sets (§3.3).

| Quantity | Channel kind | Unit | Loop tag | At the pump owner | At a pass-through member |
|---|---|---|---|---|---|
| flow in | `ComponentInput` | kg/s | `loop.flow_in` | the flow that came back round the loop; read only by the post-run check, through the output wired to it | the flow the previous member sent; read and passed through |
| flow out | `ComponentOutput` | kg/s | `loop.flow_out` | the flow it pumps this step | equal to flow in, by the member's own code |
| temperature in | `ComponentInput` | °C | `loop.temperature_in` | the water arriving (the return, for a generator) | the water arriving (the supply, for a receiver) |
| temperature out | `ComponentOutput` | °C | `loop.temperature_out` | the water leaving (the supply) | the water leaving (the return) |

Today a loop has three published quantities: the pump owner's flow, the supply temperature and the return
temperature (`hydronic_coupling_spec.md` §3.1; #1109's `HydronicPort` names exactly these three). The fourth, the
flow returning to the pump owner, exists nowhere, which is why a receiver that used a flow of its own was invisible.
Two members already have both flow channels on one circuit and publish the flow they read: the boiler on the
distribution system's flow (`WaterInputMassFlowSh`, `WaterOutputMassFlowSh`, #1117) and the district-heating
substation's space-heating side (`WaterInputMassFlowRateFromHeatDistributionSystem`, `WaterOutputShMassFlowRate`). The
HDS, the buffer, the hot-water tank and `SimpleHeatSource` publish no flow out on the circuits they do not pump; the
boiler, the heat pump, the electric heater, the solar collector and the substation's hot-water side read no flow in on
the circuits they pump. §8 lists what each gains.

**The types.** Three small types in `hisim/energy_port.py`, beside `EnergyPort` (the module keeps its name: it holds
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
    may_cool: bool = False             # D10: this member allows the loop to remove heat from the room side; a loop may
                                       # cool only if every member allows it (Q8)

    @property
    def flow_in(self) -> "FluidLoopChannel": ...        # FluidLoopChannel(self, LoopQuantity.FLOW_IN)
    # flow_out, temperature_in, temperature_out likewise


@dataclass(frozen=True)
class FluidLoopChannel:
    """The loop tag of one channel: which fluid loop of the component, and which quantity."""
    loop: FluidLoop
    quantity: LoopQuantity
```

`add_input` and `add_output` take `fluid_loop: Optional[FluidLoopChannel] = None`, keyword-only (two keywords beside
`energy_port=`, pending Q7). The declaration refuses, with `FluidLoopError` (§3.5): a tag on a channel of the wrong
unit (a flow is kg/s, a temperature °C); a tag of the wrong kind (`FLOW_IN` and `TEMPERATURE_IN` belong on an input,
`FLOW_OUT` and `TEMPERATURE_OUT` on an output); a tag beside an `energy_port` (a channel is a leg of a loop or a power,
never both); two channels of one component with the same tag. The component keeps no list of its loops:
`Component.port_sets()` derives them by grouping `inputs + outputs` by `tag.loop`. A loop's name is a plain string, or
a `str` Enum of the component's loop names where it has several (pending Q7).

**What enforces that a loop channel is tagged.** (i) At the declaration, a tag on a channel it cannot describe is
refused. (ii) At build, a loop with any tagged channel must be tagged whole: a `flow_out` read by an input without a
`flow_in` tag, a `flow_in` fed by an output without a `flow_out` tag, and a port set with one to three of its four
quantities are refused by name (§3.3, rules 0 and 2). (iii) A loop whose both ends are untagged is what every loop is
today: the balance reports its members as undeclared, never as closing; a recorded-files test and a class-wide test
over every kg/s channel catch it (pending Q11).

**Example: the buffer's generator loop** (`simple_water_storage.py:1073-1093` on #1109's branch; the
secondary-generator and distribution loops have the same shape). On #1109 the three channels are declared as plain
inputs and outputs and `add_hydronic_port(mass_flow=..., supply_temperature=..., return_temperature=..., role=IN,
carrier=space_heating)` names them a second time. After D8: one `FluidLoop`, a tag per channel, the fourth channel
(D1), no second declaration.

```python
self.generator_loop = FluidLoop(
    "generator", carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
    pumps=False,      # a buffer moves no water
    supplies=False,   # the generator supplies this loop; the buffer receives (its distribution loop says supplies=True)
    may_cool=True,    # a buffer takes water of any temperature; whether the loop cools is for the generator and the emitter to say
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
(`:2439` on that branch) and the HDS's one call (`heat_distribution_system.py:328` on that branch) likewise. What each
component writes:

- `pumps`: `True` fixed in code where the component can only pump (the hplib heat pump on both circuits — hplib's flow
  is authoritative, D2 of the hydronic coupling spec; the electric heater; the solar collector; the substation's and
  the boiler's hot-water sides); `False` where it never pumps (the buffer on every circuit, the tank on every coil);
  from a configuration boolean where it can do either (`pumps=self.config.pumps_space_heating_circuit` on the boiler
  and the substation, `pumps_the_circuit` on the HDS, `pumps_the_brine_circuit` on `SimpleHeatSource`; pending Q3).
- `supplies`: `True` on every loop of a boiler, a heat pump, a substation, a collector and a heater; the buffer
  `False` on its generator loops and `True` on `distribution`; the tank `False` on both coils; the HDS `False`.
- `may_cool`: the boiler, the substation and the tank `False`; the storages `True` (a buffer takes water of any
  temperature; whether the loop cools is for its generator and its emitter to say); the heat pump from its
  cooling-mode setting; the HDS `may_cool=self.config.cooling_enabled` (bead hisim-hds-cooling-option-m4g6); pending Q8.

The existing output names stay, since they are addresses in energy-system files and goldens
(`system_docs/results_and_interfaces.md`); the new flow outputs are named in the components' own idiom
(`WaterMassFlowRateToHeatGenerator` on the buffer, `WaterMassFlowOutput` on the HDS, …) and each description states
that it is the circuit's flow passed through. The fourth wire comes through the classes' default connections like the
other three, so no recorded file, setup or assembly gains an input item for it; the wires themselves stay as written
today (bare-name items, an explicit `input:/from:` pair only where no default connection exists, the assembly format's
circuit ends, which the binding lowers to bare names).

### 3.2 What a circuit is, and why it is not a bus (D6, pending Q0)

A named circuit is:

- **A name.** The loop the wiring connects gets its id from its pump owner's port set (§3.3), so the build can say
  "the loop `hds.distribution` between `boiler` and `hds`" and the post-run check can name the loop whose water does
  not add up.
- **A declaration of the one pump owner** (D3) and the one supplier (D10), made by each member's own `FluidLoop`,
  read at build.
- **A conservation check** of the values the members published on their own (D4, D11): at the pump owner, the flow
  that came back equals the flow it sent; at every member (pending Q1), the flow that left equals the flow that
  arrived; at every member (pending Q2), the heat it booked equals the heat its own flow and temperatures carry.

It is not a bus:

- **It holds no value.** There is no "circuit flow" the members read. The pump owner publishes its flow out; the next
  member reads it as its flow in and publishes it again as its flow out. Each value belongs to the output of the
  component that published it (B4), as today.
- **It computes nothing.** No mixing, no remainder, no reconciliation. If two members disagree, nobody is corrected;
  the run fails and the error names them (D2). A split of a flow into two branches is a valve component with one
  circuit per branch (`hydronic_coupling_spec.md` §3.5), not a junction.
- **It changes no result.** A system with the loop tags removed and the checks switched off simulates
  bit-identically. A bus balances; deleting one changes results.

### 3.3 The build: port sets, loop discovery and the refusals

Whether a port set pumps or supplies is stated by its `FluidLoop`; the loop itself is what the wiring connects, and
the build finds it (`Simulator.run_all_timesteps`, after `connect_all_components`, where #1109's
`refuse_a_broken_wiring` runs, `simulator.py:819` on that branch) without being told (D7):

0. **Port sets.** For every component, its tagged channels are grouped by the `FluidLoop` their tags name
   (`Component.port_sets()`). A group with exactly one channel per quantity is a port set. A group with one, two or
   three quantities (a loop tagged by half) is refused, naming the component, the loop and the missing quantities.
1. **Loop discovery.** Every port set's `flow_out` output is connected to the `flow_in` input of exactly one other
   port set (#1109's wiring index, `Wiring.readers` and `source_outputs`, answers who reads what). Starting at any port
   set not yet assigned to a loop, follow `flow_out` → the input that reads it, which must carry a `flow_in` tag, to
   the next member's port set, and on, until the start is reached: that sequence of port sets is one loop. The
   temperature wires must follow the same path (each member's `temperature_in` is connected to the previous member's
   `temperature_out`).
2. **What is refused** (`CircuitWiringError`, naming every port set involved by component and loop name, and the
   channel):
   - a loop that does not close: the path reaches a port set already on another path, a `flow_out` output nobody
     reads, or a `flow_in` input that is not connected, while the set is wired at all;
   - a port set wired by half (some of its four channels connected, others not; the #1119 rule for the storages'
     coils, generalised). A port set with none of its four channels connected is idle and allowed, like the boiler's
     hot-water side in `basic_household_only_heating`;
   - a `flow_out` output read by two `flow_in` inputs (one flow delivers its heat once; a split is a valve component
     with one loop per branch);
   - a temperature wire that does not follow the flow wire;
   - a `flow_out` read by an input that carries no loop tag, or a `flow_in` fed by an output that carries none: "the
     loop leaves `hds.WaterMassFlowHDS` for `boiler.WaterInputMassFlowSh`, which carries no fluid-loop tag";
   - members of one loop whose `FluidLoop`s state different carriers: "the loop `hds.distribution` is
     space_heating_heat at hds and domestic_hot_water_heat at buffer". The carrier is not derivable from the wiring
     (the same boiler class serves either) and is what the Sankey draws the loop as, so it stays declared, once per
     member, and checked for agreement.
3. **Exactly one pumping port set per loop** (D3). Zero (two pass-through members, nobody moves water) or two (the
   pre-#1117 `gas_boiler_household`: the boiler's `space_heating` loop and the HDS's `distribution` loop both pumping)
   is refused, naming the loop's members and which of them pump: "the loop (boiler.space_heating, hds.distribution)
   has 2 pumping port sets, boiler.space_heating and hds.distribution; exactly one member of a loop pumps".
4. **Exactly one supplying port set per loop** (D10). Zero (a loop of receivers: a buffer's generator loop wired to a
   tank's coil) or two (two generators on one loop with no store between them) is refused, naming the loop's members
   and which of them supply. Pumping and supplying are counted separately.

**The loop's id** is `<pump owner's component name>.<its loop name>`: `hds.distribution`,
`CondensingGasBoiler.space_heating`, `MoreAdvancedHeatPumpHPLib.dhw`, `SolarThermalSystem.solar_dhw`. A component
name is unique in a run and a component pumps each of its port sets at most once, so the id is unique. A loop refused
in rule 3 has no pump owner to name it by, so that message lists the members. The id is derived at build and kept on
each member's port set, so the post-run check (§3.5) puts it in its error without a lookup.

**What this replaces.** The HDS no longer reads `PARALLEL` or `NO_STORAGE_MASS_FLOW_FIX` to know whether it pumps;
its `FluidLoop` states it. The boiler no longer infers from a wired input whether it heats someone else's flow
(`GenericBoiler.space_heating_runs_on_distribution_flow`, #1117); its `FluidLoop` states it, and the build refuses a
set wired by half. In the four-wire design every member has a wired flow in, so "my flow input is wired" could not
distinguish the pump owner from a pass-through member anyway (D3 forbids inferring it; D7 forbids writing it anywhere
but at the port). #1109's `add_hydronic_port`, which named three channels a second time (`component.py:498-559` on
that branch) and checked that they were the component's own, goes: a tag is on the channel, so it cannot name a
foreign one (D8).

### 3.4 How the balance books a loop: the supplier's sign chooses the carrier (D10)

The supplier is a declared fact of the device (`supplies`, §3.1), not of the values: a boiler, a heat pump, a
substation and a collector supply every loop they are on; a buffer receives on its generator loops and supplies its
distribution loop; a tank receives on both coils; the HDS receives. What the values decide is the *direction of the
step*, from the supplier's own four columns: `Q_supplier = m_out · c · (T_out − T_in)`, with
`c = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K`.

- `Q_supplier > 0`: the supplier added heat to the water. The loop's heat is booked as the loop's `carrier` (space
  heating or hot water) from the supplier to the other members, each receiving its own `−m · c · (T_out − T_in)`.
- `Q_supplier < 0`: the supplier removed heat (a chiller, a heat pump in cooling mode). The same amounts are booked as
  `COOLING` the other way: from the room-side members, each of which put `m · c · (T_out − T_in) > 0` into the water,
  to the supplier.
- `Q_supplier = 0`: nothing is booked.

A loop that heats in one step and cools in the next (the HDS with `cooling_enabled`, bead
hisim-hds-cooling-option-m4g6; a heat pump cooling through a buffer, hisim-energy-sankeys-9uoo.17) has no one sign,
which is why the role is declared and only the direction is read off the values.

Around a closed loop the members' `m c (T_out − T_in)` sum to zero by construction once the flows agree (every `T_in`
is the previous member's `T_out`, and the mass check makes every `m` the same), in either direction; that is #1109's
"a circuit balances by construction". A loop is thus a port with two faces (§3.7, pending Q9): the positive face its
heating carrier from the supplier outward, the negative face `COOLING` from the emitters to the supplier, implemented
by the one rule that splits a column by sign. So the HDS–building wire and the heat pump–buffer loop book a cooling
step the same way: `COOLING` from the building to the HDS, from the HDS to the buffer, from the buffer to the heat
pump.

**A loop that may cool says so.** A loop whose every member's `FluidLoop` says `may_cool=True` may run either way. On
any other loop a step in which the supplier removed heat fails the run: `CircuitError` from the post-run check
(§3.5), "the loop `boiler.space_heating` heats only, and its supplier `boiler` removed 1,234 W from the water in step
1234, first of 3 steps". Before stage 2 exists, the post-run balance of stage 1a refuses the run on the same
condition from the same columns (§4). A heat pump that may cool wired to an HDS with `cooling_enabled: false` is a
heating-only loop, and a cooling step on it fails by name: the emitter cannot cool, so the heat pump must not.

### 3.5 The post-run check (D4, D11)

D4's rule, checked where D11 puts it: once, after the simulation, over the result columns of every step, beside
`FiniteResultsCheck` (`postprocessing_main.py:307`). An input is not a column, so a member's `flow_in` and
`temperature_in` are read as the columns of the outputs wired to them (the wiring the build walked). One
whole-column comparison per port set and check:

```python
class LoopCheck:
    """The loop checks of values (D4, Q1, Q2, D10), over the result table after the run (D11)."""

    @staticmethod
    def require_conserved(results: pd.DataFrame, loops: Sequence[FluidLoopWiring]) -> None:
        for loop in loops:
            for port_set in loop.port_sets:                     # every member (Q1 b); the pump owner alone under Q1 a
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

The checks, each one column operation per port set:

- **Mass check at the pump owner** (D4): the flow that came back equals the flow it sent, within the tolerance (Q5,
  recommended 1e-4 kg/s). Message: `Loop 'hds.distribution' (boiler.space_heating, hds.distribution): member 'boiler'
  sent 0.359 kg/s in step 1234 (2021-01-13 20:30) where 0.270 kg/s arrived, and so in 592 of 672 steps. Every member
  but the pump owner passes the flow through unchanged.`
- **Mass check at every member** (pending Q1, recommended): the same rule applied to each port set, so the error
  names the member that changed the flow. Under Q1 a only the pump owner is checked, and the message can say only
  that some member changed the flow.
- **Heating-only check** (D10): on a loop whose members do not all say `may_cool`, no step in which the supplier
  removed heat (§3.4).
- **Energy check at every member** (pending Q2, recommended; switched on after hisim-pnw7): `|Q_booked − m c (T_out −
  T_in)| ≤ max(1 W, 0.1 %)` in every step, the booked heat (`ThermalOutputPowerSh`, `ThermalPowerDelivered`,
  `ThermalPowerFromHeatGenerator`, …), the flow out and the temperature out being the member's own columns, the
  temperature in the column of the output wired to it (`system_docs/modelling_guide.md` §1).

A violation fails the run hard (D2): `CircuitError` names the loop, the member, the first failing step with its
time, the number of failing steps and the values at the first one; the result table is kept, so the fault can be read
off it.

**Why after the run** (D11): the check is for catching bugs. Nearly every run passes it, and one that fails is
debugged from its result table. A per-step `i_doublecheck` (`simulator.py:503`) would cost a call per member and
step — at 60 s over a year, 525,600 steps for each of a twin's members — for a check that almost never fires; the
post-run check costs one vectorized comparison per port set, as `FiniteResultsCheck` does for NaN. What it gives up: a
run with a violation in its first step simulates to its end before it fails, and the failure is reported after the
run rather than at the step. Accepted: a violation is a modelling bug, not a state a run can recover from, and its
steps are all in the table. `i_doublecheck` keeps no production use.

**Why converged values are enough** (D5): forcing freezes the controllers after ten passes (`simulator.py:488-489`),
and the physics keeps iterating until the convergence criterion holds or the run aborts at the pass limit
(`maximum_passes_per_timestep`). Mass flows and temperatures are physics outputs, so every step of the table holds
converged values, the forced ones included: no "forced" branch, no widened tolerance. The 27 to 67 forced steps of
the heat-pump twins in the January week (§5) need no exemption; the audit measured their circuits closing exactly
(0.000 kWh) with forcing on.

**Why not `i_simulate`** (D4): it runs in every pass, and in the first pass of a step a pass-through member has not
yet seen the pump owner's new flow, so the returning flow lags a pass; a check there would fire on every change of
flow.

### 3.6 The errors

- `CircuitError(HydronicsError)` in `hisim/hydronics.py` (where every hydronic refusal lives, beside
  `NegativeMassFlowError`), a `ValueError` like them: raised by the post-run check before anything reads the result
  table, left to propagate; `hisim_main` reports it as it reports any exception after the run.
- `CircuitWiringError(HydronicsError)`: the build-time twin, for a wiring that is no loop or a loop with no or two
  pump owners or suppliers (§3.3), raised beside #1109's `EnergyPortWiringError`.
- `FluidLoopError(ValueError)` in `hisim/energy_port.py`: raised at the declaration by `add_input`/`add_output` for a
  loop tag that cannot describe its channel (§3.1); it takes the place of #1109's `HydronicPortError`.
- No error codes (pending Q5): the energy-system format numbers its refusals (`EF-09`, `EF-7A`) because a file author
  looks them up; a build refusal raised over the connected components and a run-time error have no code today, and
  with D7 there is no file block whose refusal would need one.

### 3.7 Energy ports as channel tags, on inputs as on outputs (D9)

An energy port is already a channel tag: `add_output(..., energy_port=EnergyPort(role, carrier, ...))` says what one
output is in its component's balance, and the post-run balance and the Sankey read nothing else (`energy_port.py:1-9`
on the building branch). The loop tag of §3.1 is its sibling for a channel that carries water instead of a power.
What was unlike: an `EnergyPort` could sit on an output, never on an input.

**What that costs today.** A component that receives energy through an input has no channel to tag, so it invents an
output that copies the input and points the copy at the input by string. On the building branch
(`hisim/components/building/building.py:446-588` at 2b9fc8a8c) the building has four such copy-outputs:
`HeatReceivedInWatt` (its input `ThermalPowerDelivered` where positive, `peer_input=self.ThermalPowerDelivered`),
`HeatRemovedByCoolingInWatt` (the same input where negative, the same pointer), `HeatReceivedFromChpInWatt`
(`peer_input=self.ThermalPowerCHP`) and `InternalHeatGainsFromOccupancy` (`peer_input=self.HeatingByDevices`). The
sender side points the other way, from one output to another, with `peer_output=` (three uses on the building
branch): the HDS's `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt` (each `peer_output=self.ThermalPowerDelivered`,
the signed output the building reads; `heat_distribution_system.py:327-353` on that branch) and the occupancy's
`BodyHeatToBuildingInWatt` (`peer_output=self.HeatingByResidents`, `loadprofilegenerator_utsp_connector.py:542`).
Every pointer is a string naming a channel declared elsewhere, and every copy-output is a column that exists for the
balance alone — the duplication D8 removes from the loops. On main `peer_output=` has twelve uses: nine are the
generators' heat ports pointing at their own flow or temperature output (`generic_boiler.py:1659,1712`,
`more_advanced_heat_pump_hplib.py:731,743,861`, `generic_district_heating.py:352,388`,
`generic_electric_heating.py:332`, `solar_thermal_system.py:715`), which #1109 already replaces by its circuits; two
are the battery's (`advanced_battery_bslib.py:387,398`), which the building branch replaces by plain `IN`/`OUT`
ports; one is the occupancy's. `peer_input=` arrives with the building branch.

**The design.** `add_input(..., energy_port=EnergyPort(...))`, keyword-only, beside `fluid_loop=`; a channel takes
one of the two or neither. For an input port:

- *Its series* is the column of the output wired to it (`Wiring.source_outputs`), as #1109's balance follows a
  circuit leg named by an input to the output connected to it (`ports.py:686-708` on that branch). An optional input
  that is not connected is idle and left out, like an open circuit end.
- *Its peer* is the component that output belongs to. For an output port nothing changes: an `OUT` port's peer is the
  component that reads it, a metered port's the meter (`energy_port.py:11-31` on the building branch).
- *The pairing* is symmetric: a transfer between two components is the sender's `OUT` output and the receiver's `IN`
  input on the same wire; both resolve to one column, so "sender and receiver book the same energy" (B4) holds by
  construction for a wired transfer, as for a loop. What the check still proves is each side's own balance: the
  receiver's `IN` against its losses and stored change, the sender's `OUT` against what it took in. The copy-output
  was a copy of the input, so nothing that was checked is lost.
- *The ambiguity rule* (`Wiring.ambiguous_receiver_faults`, `ports.py:838-866` on #1109's branch): an `OUT` output
  read by two components that declare ports is refused today unless the port names its recipient. With input ports it
  is refused unless exactly one reader tags the reading input with an `IN` port of the same carrier; that reader is
  the peer. The message "name its recipient with peer_input or peer_output" becomes "tag the receiving input".
- `EnergyPort.peer_input` and `EnergyPort.peer_output` go; `role`, `carrier`, `environment` and `negated` stay, and
  so does everything that reads them (`is_metered`, `default_boundary`, the meter's `FLOW_TAG_OF_ROLE`,
  `wiring_checks.py:402,449-451`).

**A channel carries one port.** That rule stays, and leaves one copy-output standing: a quantity that is both taken
from a boundary node and given to a component. The occupancy's body heat enters it from the residents (`IN`,
`ROOM_HEAT`, `environment=RESIDENTS`) and leaves it to the building, which reads `HeatingByResidents`. After D9 the
two ports swap: `HeatingByResidents`, which the building reads, carries `EnergyPort(OUT, ROOM_HEAT)` and finds the
building through its reader; the copy `BodyHeatToBuildingInWatt` keeps the boundary side, `EnergyPort(IN, ROOM_HEAT,
environment=RESIDENTS)`, which needs no reader. No pointer, one extra column, as today.

**A channel whose sign means its direction** (pending Q9, recommended). `ThermalPowerDelivered` is positive while the
HDS heats the building and negative while it cools, and the two directions are two carriers of the Sankey
(`SPACE_HEATING_HEAT` to the building, `COOLING` back out of it; complete Sankey spec, Q-G). Today that split is four
copy-outputs, two per side, each `>= 0`. The tag carries the split itself: a port with two faces,
`SignedEnergyPort(positive=EnergyPort(OUT, SPACE_HEATING_HEAT), negative=EnergyPort(IN, COOLING))` on the HDS's output
and `SignedEnergyPort(positive=EnergyPort(IN, SPACE_HEATING_HEAT), negative=EnergyPort(OUT, COOLING))` on the
building's input. The balance splits the one column by sign into two series, one per face, and books each on its
carrier; the two ends tag the same wire, so they book the same numbers; the four copy-outputs go. `negated` is the
degenerate case of a port whose one face is the negative one and stays as it is. A fluid loop is the same port with
two faces, without anyone writing them (§3.4).

**Example: the building's input.** Today (`building/building.py:557-587` at 2b9fc8a8c) the input is plain and two
outputs copy it by sign and point back at it:

```python
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

After (D9 with Q9's recommendation): the input carries the port, the two copy-outputs are gone:

```python
self.thermal_power_delivered_channel = self.add_input(
    self.component_name, self.ThermalPowerDelivered, lt.LoadTypes.HEATING, lt.Units.WATT, False,
    energy_port=SignedEnergyPort(
        positive=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        negative=EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.COOLING),
    ),
)
```

The CHP input is the plain case, `energy_port=EnergyPort(IN, SPACE_HEATING_HEAT)` on `ThermalPowerCHP`, and
`HeatReceivedFromChpInWatt` goes. The two gain inputs, `HeatingByResidents` and `HeatingByDevices`, each carry
`EnergyPort(IN, ROOM_HEAT)`; `InternalHeatGainsFromOccupancy` stays as the report column it also is
(`DISPLAY_IN_WEBTOOL`) and loses its port. On the HDS, `ThermalPowerDelivered` carries the signed port's sender faces,
and `HeatDeliveredInWatt` and `HeatDrawnForCoolingInWatt` go. None of these outputs exists on main (they were added by
the unmerged #1109 and the building branch), so no file, golden or KPI names them.

**Port declarations conditional on a position enum** go with D3. The HDS declares its ports only behind a buffer
(`declares_energy_ports`, `heat_distribution_system.py:236` on the building branch: `position == PARALLEL`), because
without a buffer its outputs lag one step and its balance would not close (hisim-pnw7; §5 measures it). That
condition reads the enum D3 removes; it goes in stage 3, after pnw7, when the HDS tags its loop and its building port
unconditionally. Until then #1109's rework keeps the condition as it is.

**Two tag systems on one output** (pending Q10). Beside `energy_port`, an output carries `postprocessing_flag`, a
list of `lt.InandOutputType`, `lt.ComponentType` and `lt.OutputPostprocessingRules` members. Eleven outputs on the
building branch carry both: the occupancy's `ElectricalPowerConsumption` (`ELECTRICITY_CONSUMPTION_UNCONTROLLED` and
`EnergyPort(IN, ELECTRICITY)`), the electric heater's `ElectricOutputShPower` and `ElectricOutputDhwPower`, the solar
pump's `ElectricityConsumptionOutput` (the same pair), the PV's `ElectricityOutput` (`ELECTRICITY_PRODUCTION, PV` and
`OUT`), the battery's `StoredEnergyIncreaseInWattHour` (`STORED_ENERGY_INCREASE, BATTERY` and `STORED_CHANGE`), and
five where the flag is only `DISPLAY_IN_WEBTOOL` (the boiler's two fuel outputs, the heat pump's cooling electricity,
the building's solar gain and internal gains). Who reads what:

- The KPI computation selects the production, consumption and battery columns by the flags
  (`kpi_preparation.py:75-160`: `ELECTRICITY_PRODUCTION`, `ELECTRICITY_CONSUMPTION_UNCONTROLLED`,
  `ELECTRICITY_CONSUMPTION_EMS_CONTROLLED`, `STORED_ENERGY_INCREASE`, `CHARGE_DISCHARGE`, with `ComponentType.PV`,
  `WINDTURBINE`, `BUILDINGS`, `BATTERY`, `CAR_BATTERY` telling the columns apart).
- The electricity meter and the energy manager select their dynamic inputs by the same `InandOutputType` tags as
  wire tags (`electricity_meter.py:135-146`, `controller_l2_energy_management_system.py:258-276`).
- `DISPLAY_IN_WEBTOOL` is read by nothing in `hisim/` (a scan of the building branch finds only its declarations).
- The balance and the Sankey read the ports; so does the meter, which derives the wire tag from the port's role when
  it wires itself (`FLOW_TAG_OF_ROLE`, `electricity_meter.py:127, 347-382` on the building branch: an `IN` electricity
  port is fed as consumption, an `OUT` port as production). For the meter the port is already the one source and the
  flag the duplicate; for the KPIs the flag is still the source.
- The overlap is not exact: the heat pump's `ElectricalInputPowerSH` carries a port and no consumption flag
  (`more_advanced_heat_pump_hplib.py:618-625`); a battery's charging power is an `IN` port the KPIs must not count as
  consumption. So a merge changes what the KPIs select, and the KPIs are what the goldens compare.

### 3.8 The checks in one table

| Check | When | Where | Catches | Decided? |
|---|---|---|---|---|
| a loop tag fits its channel | declaration | `add_input`/`add_output` (§3.1) | a flow tag on a °C channel; an `_IN` tag on an output; a tag beside an energy port; two channels with one tag | D8 |
| every port set has its four quantities | build | `Component.port_sets()` (§3.3, rule 0) | a loop tagged by half | D1, D8 |
| every port set is wired whole or not at all, and its wires close a loop | build | the loop walk (§3.3, rules 1 and 2) | a loop that does not close; a half-wired set; a flow-out output nobody reads; a temperature wire that does not follow the flow | D7 |
| the far end of a tagged wire is tagged | build | the loop walk (rule 2) | a `flow_out` read by an untagged input; a `flow_in` fed by an untagged output | D8 |
| every member of a loop states the same carrier | build | the loop walk (rule 2) | a space-heating end wired to a hot-water end | D8 |
| a flow is read by one loop | build | the loop walk (rule 2) | a flow-out output read by two port sets (a split without a valve) | D3, D7 |
| exactly one pumping port set per loop | build | the loop walk, counting `pumps=True` (rule 3) | nobody pumps; two pump (the pre-#1117 `gas_boiler_household`) | D3 |
| exactly one supplying port set per loop | build | the loop walk, counting `supplies=True` (rule 4) | two generators on one loop without a store between them; a loop of two receivers | D10 |
| a loop that heats only never cools | after the run, every step | the post-run check (§3.5); the post-run balance until stage 2 | a supplier removing heat from the water on a loop whose members do not all say `may_cool` | D10, D11 |
| flow back = flow sent, at the pump owner | after the run, every step | the post-run check | any member that changed the flow (named only as "some member") | D4, D11 |
| flow out = flow in, at every member | after the run, every step | the post-run check | the member that changed the flow, by name | D11; Q1 |
| booked heat = `m c (T_out − T_in)`, at every member | after the run, every step | the post-run check | a member booking with a flow or a temperature it did not publish; the HDS's one-step lag without a buffer | D11; Q2 |

## 4. Stages

One fix per PR, stacked, in this order (the split of stage 1 into 1a and 1b is Q12's recommendation). No KPI moves
in any stage; goldens compare KPIs only (`golden_references/README.md`).

### Stage 1a — #1109 reworked onto the tags (D8)

The PR is #1109 itself, rebased onto main. Its declaration layer goes and its balance side stays:

| #1109 (`origin/sankey-pr3-storages` at da66cfe43) | After D8 |
|---|---|
| `HydronicPort` (three names, role, carrier) and `CircuitEnd` (which names are inputs), `energy_port.py:361-558` | go. The names are the channels themselves; "which are inputs" is the channel kind; the role is `supplies` on the `FluidLoop` and the direction of each step is the supplier's sign (§3.4); the carrier is on the `FluidLoop`. |
| `Component.add_hydronic_port` (`component.py:498-559`), `hydronic_ports`, `CIRCUIT_LEG_UNITS`, `HydronicPortError` | go. `add_input`/`add_output` take `fluid_loop=`; `Component.port_sets()` groups the tagged channels; `FluidLoopError` at the declaration. |
| `HydronicPort.heat_in_kilowatt_hour` and `heat_in_kilowatt_hours_per_step`, the one rule `m c (T_sup − T_ret) dt` | stay, as functions of `hisim/hydronics.py` beside `circuit_power_w`; applied per member to its own four columns, `m_out c (T_out − T_in)`. |
| `DeclaredPorts.circuit_ends_of`, `Wiring.resolve_circuit`, `CircuitColumns` (three columns per circuit) | stay in substance, adapted: an end is a port set, its columns are its four (`flow_in` and `temperature_in` followed through the wire to the previous member's outputs). |
| `Wiring.circuit_peer`, `circuits`, `pairing_fault`: the ends paired through the flow wire; one reader; both ends or neither | stay, generalised to the loop walk of §3.3: the peer of a member is the next member on the walk; "both ends" becomes "every member tagged"; "one reader" is rule 2. |
| `refuse_a_broken_wiring` at `simulator.py:819`, `EnergyPortWiringError` | stay; `CircuitWiringError` is raised beside. |
| `DeclaredPorts.add_circuits`: the heat per circuit from the result columns; an open end that pumps into an unconnected circuit is a fault | stays, per member and port set. |
| the components' circuits: the buffer's three, the tank's two coils with their own returns (#1119), the HDS's one behind a buffer, the generators' | stay, as `FluidLoop`s and tags; the fourth channel comes in 1b. |
| the HDS's exemption without a buffer (`declares_energy_ports`) | stays until stage 3. |
| the tests `tests/test_energy_port_hydronic.py`, `test_energy_balance_storages.py`, `test_energy_balance_generators.py`, the golden matrix | stay; those that build a `HydronicPort` or call `add_hydronic_port` are rewritten against the tags, the balance assertions unchanged. |

- **Implements:** `LoopQuantity`, `FluidLoop`, `FluidLoopChannel`, `FluidLoopError` in `hisim/energy_port.py`;
  `fluid_loop=` on `add_input` and `add_output` with the declaration checks of §3.1; `Component.port_sets()`; every
  `add_hydronic_port` call site (12 call sites declaring 14 ends in 7 component files on the branch: the buffer's
  three, the tank's two, the HDS's one, the boiler's two, the heat pump's two, the electric heater's, the substation's
  two, the collector's) replaced by `FluidLoop`s and tags on the channels that exist today — three per port set.
  `port_sets()` accepts a set with one flow channel (a `flow_out` at the pump owner, a `flow_in` at the reader) until
  1b tightens it to four. `pumps` is written where it is fixed in code (`True` on the generators, `False` on the
  storages) and left out of the either-way components until 1b. `supplies` and `may_cool` are written on every loop
  from the start (D10), because the balance books by them: rule 4 of §3.3 (one supplier) in `refuse_a_broken_wiring`,
  the booking rule of §3.4 and the heating-only check in the post-run balance.
- **Results:** bit-identical; no new column. The balance report books the heat pump's cooling steps as `COOLING`
  toward the heat pump instead of negative space heating away from it, which moves no total.
- **Tests:** #1109's tests rewritten at their declaration sites only (a tag on the wrong unit or kind, a tag beside
  an energy port, two channels with one tag, each refused at the declaration; a port set grouped from its tags); its
  balance tests unchanged; a loop with no or two suppliers refused at build; a cooling loop (every member `may_cool`,
  a supplier that removes heat in a step) booked as `COOLING` from the room-side member to the supplier and as the
  heating carrier the other way in a heating step, the two directions summing to the loop's `m c (T_out − T_in)` at
  the supplier; a heating-only loop whose supplier removes heat in a step fails the run naming the loop, the supplier
  and the step; the heat pump twins' cooling steps booked as `COOLING` with every total unchanged.
- **Done when:** #1109's balance report closes for every twin it closes for today.

### The D9 PR — energy ports on inputs

Its own small PR right after 1a and before the building branch opens as a PR.

- **Implements:** `energy_port=` on `add_input`; `peer_input`/`peer_output` removed from `EnergyPort` (after 1a their
  remaining uses are the seven of §3.7); the balance following an input port through the wiring; the ambiguity rule
  reworded; the occupancy's two body-heat ports swapped; with Q9's recommendation, `SignedEnergyPort`. The building
  branch is then rebased onto it and drops its copy-outputs.
- **Results:** no recorded file touched, no KPI moves.
- **Tests:** an input port resolved to the column of the output wired to it and to its source as the peer; an
  unconnected optional input port left out; the ambiguity rule with and without a tagged reader; a signed port split
  into its two faces; the building branch's balance closing as it does today, with its copy-outputs gone.
- **Depends on:** 1a merged.

### Stage 1b — the fourth wire, the pumping flag everywhere and the loop walk

- **Implements:** the new flow channels of §8 and their default connections; the configuration booleans of Q3 with
  their defaults; `pumps=` on the either-way loops; `port_sets()` requiring four quantities; the loop walk and the
  refusals of §3.3 (rules 0 to 3); `CircuitWiringError`; the three boiler lines (`pumps_space_heating_circuit: false`
  in `gas_boiler_household`, the mockup and `uc1.realized`, under Q3 a).
- **Results:** bit-identical; new result columns only (one flow-out per pass-through member and circuit, about 2 to 4
  per system). Re-recorded: the three buffer-less boiler files.
- **Tests:** two pumping port sets on one loop, none, a loop that does not close, a port set wired by half, a loop
  tagged by half, a `flow_out` read by an untagged input, two members stating different carriers, a flow read by two
  port sets, a temperature wire that does not follow the flow, each refused with the loop's members and the channel
  named; every recorded file builds and every loop gets the expected id (Q11 a); every component class's kg/s
  channels are tagged or listed (Q11 c).
- **Depends on:** 1a merged.

### Stage 2 — the mass check and the heating-only check

- **Implements:** the post-run check of §3.5 beside `FiniteResultsCheck` (D11): D4's check of the pump owners (and
  Q1's of every member), the heating-only check (D10) moved into it from the post-run balance, `CircuitError`; the
  energy check of Q2 built beside them and not switched on (stage 4).
- **Results:** bit-identical.
- **Tests:** a receiver that publishes a flow of its own on a loop it does not pump raises `CircuitError` with the loop
  id, the member, the first failing step, the number of failing steps and both flows (the pre-#1117
  `gas_boiler_household` wiring rebuilt as a fixture, with the HDS pumping 0.27 kg/s and a boiler publishing
  0.359 kg/s; stage 1b's build check already refuses two pump owners, so the fixture declares the boiler as
  pass-through and makes it publish 0.359 kg/s anyway); a boiler fixture that publishes a supply colder than its
  return for one step raises `CircuitError` naming the loop, the boiler, the step and the heat removed, and a heat
  pump fixture with `may_cool` on every member of its loop passes the same step; every recorded twin runs the January
  week with zero errors; the `test_*_time_resolution` and twin tests bit-identical.
- **Depends on:** 1b merged.

### Stage 3 — the enums go

- **Implements:** `PositionHotWaterStorageInSystemSetup` removed from all three components, the HDS's and the heat
  pump's flow source taken from their pumping flag, the HDS's `declares_energy_ports` removed (§3.7); the 6 recorded
  lines (`gas_boiler_household`, the three district-heating files, the mockup, `uc1.realized`), the two assembly
  lines (`energy_systems/assemblies/test_partners.yaml`,
  `roadmap/declarative_energy_systems/assemblies_mockup/heating/air_source_heat_pump_direct.assembly.yaml`), the
  district-heating setup's line and the 13 code and test files that name an enum. The HDS's lag half of the enum
  (today `PARALLEL` also decides whether the HDS publishes this step's or last step's values,
  `heat_distribution_system.py:510-530`) is not a pumping fact and no port set states it; it goes with pnw7's
  pipe-water node. The heat pump's fixed flow without a buffer stays its own configuration until it gets a
  heater-on-flow mode.
- **Results:** bit-identical. Re-recorded: the 6 files, the setup and the two assembly files.
- **Tests:** every test that named an enum rewritten against the pumping flag; the 6 recorded lines gone.
- **Depends on:** stage 2 and hisim-pnw7 merged.

### Stage 4 — the energy check

- **Implements:** the energy check of Q2 switched on in every member.
- **Results:** bit-identical; fails nothing on the then-current main (today it would fail exactly the two buffer-less
  HDS twins, §5, which pnw7 fixes).
- **Tests:** the energy check on every recorded twin, zero errors.
- **Depends on:** stage 3 and hisim-pnw7 merged.

## 5. Evidence: the dry run

Measured on main at 635404f30 (with #1117 and #1119), January week 2021-01-01 to 2021-01-08 at 900 s (672 steps),
through `run_energy_system`, one run per recorded family (the `.grouped` and `.composed` twins have identical hydronic
wiring, checked by the audit's script; the wiring of all 44 files plus the mockup and `uc1.realized` was dumped for
the mass column). Scripts and runs: `/home/noah/hisim/agent-tmp/rs/dryrun/` (`scripts/device_checks.py`,
`device_checks.jsonl`, `circuits_all.txt`).

*Mass check (Q1, per member):* from the wiring dump, whether each circuit has exactly one flow output read by its
other end, and, for the two members that already have both flow channels on one circuit (the boiler on the HDS's
flow, the substation's space-heating side), `|flow out − flow in|` per step from the result columns. For every other
member the fourth channel does not exist yet; "pass (wiring)" means the wiring has one pump owner whose flow the
other end reads and nobody books with another flow. *Energy check (Q2, per member):* `|Q_booked − m c (T_out − T_in)|`
per step, with the circuit's one flow column and the two temperature columns both members see, against
`max(1e-6 kWh, 0.1 %)` of the step's carried heat (the balance check's tolerance).

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

Reading: on today's main **no mass check fails** (the one two-pump wiring went with #1117; every other circuit has one
pump owner whose flow the other end reads), and **the energy check fails exactly the heat distribution system without
a buffer**, in both buffer-less twins, because it publishes last step's return and delivered heat beside this step's
flow (hisim-pnw7). The difference between the two twins is the supply: the boiler's supply moves every step, so the
lag shows in almost every step; the substation's supply sits on the heating curve and the lag shows only where the
valve switches. Everything else closes to 0.0 W in every step, forced steps included.

## 6. Open questions

Numbers are never reused. The owner decides; a decision is written into §7 with its date and into the epic's bead.

### Q0. The named circuit beside "no buses"

HiSim's rule is no buses and no implicit balancing junctions; a new modelling concept is a separate decision. §3.2
argues the named circuit is not a bus.

- **a — confirm D6 as proposed.** `system_docs/component_design_principles.md` gets one sentence under B4: "A water
  loop is a named circuit with one declared pump owner; every member passes the flow through, and a check after the
  run verifies that it came back."
- **b — reject the concept.** Keep the rules of #1109 (ports paired through the wiring) and the build rule of
  hisim-energy-sankeys-9uoo.20 (a pumping end whose flow nobody reads is refused). Catches the `gas_boiler_household`
  wiring but not a receiver that reads the flow and books with another, and not the lag.

Recommendation: a. The concept is a name and a check, and D1 to D5 already assume it.

### Q1. The per-member mass check as well

D4 checks the pump owner, which tells that *some* member changed the flow, not which. Where it runs is decided by
D11; what is open is who is checked.

- **a — pump owner only** (D4 as written). In a two-member loop the culprit is the other member, so the message can
  name it; in a three-member loop it cannot.
- **b — every member**, its flow out against its flow in (the pump owner's check being the same rule applied to it).
  The error names the member that changed the flow; a member that computes a flow of its own (the pre-#1117 HDS,
  `SimpleHeatSource` with the nominal-flow override) fails on the first step it does so. Cost: one column comparison
  per member, once per run.

Recommendation: b. Same rule, same cost after the run, and the error names the device. Dry run (§5): both members
that already have both channels pass it in every step.

### Q2. The energy check per member

Each member books a heat for the circuit; the modelling guide requires it to equal `m c (T_out − T_in)` of the
member's own port set (`system_docs/modelling_guide.md` §1).

- **a — no energy check here.** The post-run energy balance (#1109, `hisim/postprocessing/energy_balance/`) checks
  balances over the run and per step once the HDS and the storages declare ports (hydronic stage E,
  hisim-hydronics-fxix.6). Nothing fails the run.
- **b — every member is checked in the post-run check** (§3.5), `CircuitError` with the circuit, the member, the
  first failing step, the number of failing steps, the booked and the carried heat in W. It catches what the balance
  of a cannot see: a member whose booked heat does not match its *own* published quantities.

What b fails on today's main (§5): the HDS in both buffer-less systems — `gas_boiler_household` in 592 of 672 steps
(first at step 4, booked 0 W against 8,276 W carried; Σ|diff| 18.0 kWh of 548 kWh delivered, 3.3 %; net −2.2 kWh) and
the district-heating twin in 6 of 672 steps (first at step 97, 3,897 W booked against 4,612 W carried; Σ|diff| 2.1 kWh
of 542 kWh, 0.4 %; net +1.1 kWh, the −0.20 % of the audit). Every other member of every other circuit passes with
0.0 W in every step. So b fails exactly what hisim-pnw7 is for, and nothing else; it cannot land before pnw7's
pipe-water node, or the two buffer-less twins (and RenoVisor's district-heating translation) stop running.

Recommendation: b, built beside the mass check in stage 2 and switched on in stage 4, after pnw7.

### Q3. How a port set says that it pumps

Where a port set's component can only pump or never pumps, `pumps` is fixed in code (§3.1). Four port sets can pump
or pass through, depending on the system: the HDS's `distribution` set (it pumps behind a buffer, beside a
substation and beside a boiler; it would pass a heat pump's flow through without a buffer), the boiler's
`space_heating` set (it pumps into a buffer; it heats the HDS's flow without one, #1117), the substation's
`space_heating` set (it passes the HDS's flow through today; a substation with a pump of its own would pump) and
`SimpleHeatSource`'s brine set. These need a stated source, never inferred from where a storage sits, list order, a
position enum or "my flow input is wired" (D3).

- **a — a boolean field on the component's configuration**, named for the fact it states:
  `pumps_space_heating_circuit` on the boiler and the substation, `pumps_the_circuit` on the HDS,
  `pumps_the_brine_circuit` on `SimpleHeatSource`; the `FluidLoop` reads it at construction, and rule 3 refuses a loop
  in which the booleans add up to no or two pump owners. With a default where one role is the usual one (HDS `true`,
  it pumps in every recorded system; boiler `true`, the buffered boiler pumps; substation `false`, HiSim's substation
  has never pumped), a recorded file writes it only where the system departs from the usual: `gas_boiler_household`
  and its two copies get `pumps_space_heating_circuit: false` on the boiler. Against: a default is a value the file
  does not show; the loop check is what makes that safe.
- **b — inferred from the wiring** ("my flow input is wired, so someone else pumps", the boiler's rule since #1117).
  Rejected by D3, and impossible once every member has a wired flow in.
- **c — two presets or two classes per either-way component** (`HeatDistribution.preset_pumping` and
  `preset_on_generator_flow`; or `HeatDistributionPumped` beside `HeatDistributionOnGeneratorFlow`). A preset is
  option a written as a preset; two classes split one device's physics for one boolean's worth of difference, and
  `MAIN_CLASS` addresses in every recorded file change.
- **d — option a without a default**: every HDS in 32 recorded files, every buffered boiler and every substation
  writes its boolean. Nothing is hidden, at the cost of a line in every hydronic file and setup that today needs none.

How this differs from the position enums: the enum described *where a storage sits*, on three components at once,
and each component inferred from it whether it pumps, whether its outputs lag and which inputs it has, with nothing
checking that the three agreed. The boolean says *that this device pumps this loop*, on the one device it concerns,
and the loop check compares every member's answer. What the enum also carried (the HDS's lag without a buffer, the
heat pump's fixed flow without one) is not a pumping fact and is not replaced by the boolean (stage 3).

Recommendation: a. It is D3's "explicit" in the place a device's own facts live; the loop check turns a wrong or
forgotten value into a refusal naming both candidates; the defaults keep the recorded files as they are except in the
three buffer-less boiler files. If the owner prefers every value written, d is the same design without the defaults.

### Q4. Relation to #1109's `HydronicPort` — decided by D8 (2026-10-27)

Decided against the recommendation of 2026-10-22 (merge #1109, then extend `HydronicPort`): #1109 is reworked onto
the tags, stage 1a says what of it goes and stays; the options are in the decision log (D8).

### Q5. Tolerance and error codes

Every pass-through member copies its flow in to its flow out, so at convergence the returning flow equals the sent
flow up to the convergence criterion of the pass, `|Δ| ≤ 1e-4` on every output (`component.py:228`), and exactly in a
step whose flow did not change between the last two passes.

- **a — exact equality.** Fails on a step where the pass-through member's output is from the pass before the pump
  owner's last change, which `is_close_enough_to_previous` allows by up to 1e-4.
- **b — absolute, 1e-4 kg/s**, the convergence criterion itself: anything the iteration accepted passes; a flow of
  its own (0.359 against 0.270 kg/s, 0.089 kg/s apart) fails by three orders of magnitude. 1e-4 kg/s is 0.36 kg/h, far
  below any pump's flow.
- **c — relative, 0.1 % of the sent flow.** Undefined at 0 kg/s and no better than b where flows are 0.1 to 0.6 kg/s.

For the energy check (Q2): `max(1 W, 0.1 % of the carried heat)`. The post-run balance's per-step tolerance is
`max(1e-6 kWh, 0.1 %)` of the step's throughput (`modelling_guide.md` §1); at 900 s 1e-6 kWh is 4 mW, too tight
against the 1e-4 K the temperatures may still move by at convergence (0.27 kg/s × 4180 × 1e-4 K = 0.11 W). The dry run
(§5) used the balance check's tolerance and found the same failures.

Recommendation: b for the mass check, `max(1 W, 0.1 %)` for the energy check, the exception types of §3.6, no codes.

### Q6. Migration and rollout

The staging is §4 (stages 1a, D9, 1b, 2, 3, 4) and the impact per component, file, RenoVisor and golden is §8. What is
open is the owner's confirmation of that order; stage 3's wait for pnw7 is the recommendation (pnw7 is where the lag
goes). Q10's merge of the tag systems, if wanted, is a bead of its own after the Sankey stack.

### Q7. The shape of the tag: how a component names its loop, and one keyword or two

**How a component names a loop.**

- **a — a `FluidLoop` object with a plain string name**, `FluidLoop("generator", carrier=..., pumps=False)`, held as
  an attribute (`self.generator_loop`) and referenced by every tag of that loop. The name is the component's own word
  for the loop and appears in the loop id (`buffer.generator` would be the id only if the buffer pumped, which it
  never does). Carrier and flags live in the one object, stated once.
- **b — a `str` Enum of loop names per component** (`class Loop(str, Enum): SPACE_HEATING = "space_heating"; DHW =
  "dhw"` inside the boiler), passed as the `FluidLoop`'s name: a set of strings is an Enum (`coding_style.md` §4), and
  a typo is an attribute error, not a new loop. A component with one loop gains an Enum with one member.
- **c — the tag names the loop by string and carries carrier and flags itself**, on each of the four channels. The
  carrier and the flags are then written four times per loop and the build must check that they agree — the
  duplication D8 is against. Rejected.

Recommendation: a, with b where a component has two or more loops (the boiler, the heat pump, the substation, the
buffer, the tank).

**One keyword or two.** `add_output` has `energy_port=` today (about 50 uses in the components on the building branch).

- **a — two keywords**, `energy_port=` and `fluid_loop=`, on both `add_input` and `add_output`; the declaration
  refuses both on one channel. No existing call changes; the two kinds of tag are visibly different things.
- **b — one keyword `port=`**, taking either type. Renames every call site for the sake of one name.

Recommendation: a.

### Q8. Where "may cool" is stated

The first amendment's recommendation of 2026-10-27 (no declared role; the sign of each member's `m c (T_out − T_in)`
names the supplier) is withdrawn by D10: in a cooling loop the supplier has the negative sign. D10 decides that
every `FluidLoop` states `supplies` and that a loop that may cool says so. What is open is where `may_cool` lives.

- **a — on every member's `FluidLoop`; a loop may cool only if every member allows it** (§3.1). The HDS takes it from
  `cooling_enabled`, the heat pump from its cooling-mode setting, the boiler and the substation write `False`, the
  storages `True` (the tank's coils `False`). No agreement rule at build: a heat pump that may cool wired to an HDS
  with `cooling_enabled: false` is a heating-only loop, and the first cooling step fails naming the loop and the
  supplier, which is the right message.
- **b — on the supplier only.** One statement per loop; a heat pump in cooling mode on a radiator circuit that cannot
  cool would be booked as cooling without complaint (the HDS would deliver "negative heat" to the building, which the
  building's signed port books as cooling too).
- **c — derived from the carrier**: a third carrier `COOLING_LOOP` for loops that only cool. Rejected: the loops that
  matter switch between heating and cooling (the HDS with `cooling_enabled`, the heat pump through a buffer).

Names: `supplies` (this member supplies the loop's heat) and `may_cool` (this member allows the loop to remove heat);
`is_generator_end` says the same with more words, and `cools` would read as a per-step fact. The HDS's loop reads
`cooling_enabled` as spelt in hisim-hds-cooling-option-m4g6, so one configuration line decides both the HDS's
behaviour and its loop's declaration.

Recommendation: a, with the names above: explicit at every member (D3's rule for pumping, applied to cooling), no
agreement check at build, and tied to the one configuration line that already says whether the emitter can cool.

### Q9. A channel whose sign means its direction: two faces on one port, or two outputs

`ThermalPowerDelivered` is positive while the HDS heats and negative while it cools, and the two directions are two
Sankey carriers. Today both ends split it into two copy-outputs, `>= 0` each, with a pointer back at the signed
channel (§3.7).

- **a — a port with two faces**, `SignedEnergyPort(positive=..., negative=...)` on the signed channel at both ends;
  the balance splits the column by sign. No copy column, no pointer, one wire. One small type in `energy_port.py`;
  the balance's series builder learns to split a column.
- **b — keep the split outputs on the sender, and let the receiver read them**: the building gains two inputs tagged
  `IN` and `OUT`; the signed wire stays without a port. No new type, but two more wires and default connections for a
  fact the one wire already carries, and the building reads three channels for one quantity.
- **c — one carrier, signed**: tag the wire `SPACE_HEATING_HEAT` at both ends and let the heat go negative while
  cooling, as the heat pump's space-heating circuit does on #1109 (`more_advanced_heat_pump_hplib.py:696` on that
  branch). Undoes the Sankey spec's decision to show cooling as its own carrier (Q-G).

Recommendation: a. It is the tag doing what the copy-outputs did, on one wire; a fluid loop is the same port with two
faces (§3.4), so the heat pump's cooling circuit, which #1109 books as negative space heating, is booked as `COOLING`
toward the heat pump by the one rule that splits the HDS–building wire. Option c would leave the two inconsistent.

### Q10. Two tag systems on one output: `postprocessing_flag` and `energy_port`

The facts are in §3.7: eleven outputs carry both; the flag is read by the KPIs and the energy manager, the port by the
balance, the Sankey and the meter (which derives its wire tag from the port); the overlap is not exact.

- **a — keep both, and test that they agree.** One repository test asserts, over every component class, that an
  output flagged `ELECTRICITY_CONSUMPTION_*` carries `EnergyPort(IN, ELECTRICITY)`, one flagged
  `ELECTRICITY_PRODUCTION` carries `OUT`, one flagged `STORED_ENERGY_INCREASE` carries `STORED_CHANGE`, and the reverse
  where the port's component is not a storage. Nothing in KPI computation or the meters changes; bit-identical.
- **b — the KPIs and the energy manager select by the port.**
  `kpi_preparation.filter_results_according_to_postprocessing_flags` reads role and carrier instead of the three flow
  flags, with a rule for storages (a `STORED_CHANGE` port is the battery's column; an `IN`/`OUT` electricity port of a
  component that also declares `STORED_CHANGE` is charge/discharge); the energy manager derives its wire tags as the
  meter does (`FLOW_TAG_OF_ROLE`); `ELECTRICITY_CONSUMPTION_EMS_CONTROLLED` stays a flag or becomes a field of the
  port; `ComponentType` and `DISPLAY_IN_WEBTOOL` stay flags. Every KPI must come out identical, gated by the goldens;
  where one differs (the heat pump's `ElectricalInputPowerSH` is a candidate) the difference is a finding. About 100
  lines of KPI code, the manager's channel declarations and 11 component declarations.
- **c — derive the flag from the port at construction** (`ComponentOutput.__init__` appends
  `ELECTRICITY_CONSUMPTION_UNCONTROLLED` to the flags of every `IN` electricity port, …). Rejected: it would add the
  consumption flag to the battery's charging port and to the heat pump's electricity, and the KPIs would change
  silently; the storage rule has to live in the reader (b), not in the declaration.

Recommendation: a now, in its own small PR (the test holds on today's main and the building branch); b as a bead
after the Sankey stack has landed, when every electricity output carries a port. b is not a stage of this spec,
because none of its stages touches a KPI.

### Q11. Whether an untagged water channel is refused outright

§3.1 enforces tags at the declaration and at build; neither catches a loop whose both ends are untagged.

- **a — the two rules of §3.1 and the recorded-files test.** Stage 1b builds every recorded energy system and
  asserts the loops it finds, by id. The only uncaught case is two new, untagged components wired to each other in a
  system no test builds, which the balance reports as undeclared.
- **b — a third build rule**: a component that declares at least one loop and has a kg/s channel of load type
  `WARM_WATER` outside any loop is refused. Needs a production sentinel (`fluid_loop=NOT_A_LOOP`) for the tank's tap
  draw and the like, and says nothing about temperatures, which are mostly not loop legs.
- **c — a repository test over every component class**: instantiate every class from its presets, list every kg/s
  channel, and assert that each carries a loop tag or is in the test's own list of draws and taps, which may only
  shrink (the ratchet pattern of `coding_style.md` §2).

Recommendation: a plus c: a half-tagged loop is impossible, a forgotten tag in any shipped system fails, and a new
component class fails at the pull request that adds it, without a sentinel in production code.

### Q12. #1109's rework and this spec's stage 1: one pull request or two

Both touch the same 14 loop declarations.

- **a — one PR**: #1109 reworked and extended at once; port sets have four channels from the start, so `port_sets()`
  never needs a transitional three-channel form. One review; but #1109's reviewed scope (the balance side, 1,500 lines
  of tests) is mixed with this spec's first new behaviour, and the Sankey work waiting on #1109
  (hisim-energy-sankeys-9uoo) waits for both.
- **b — two PRs** (stages 1a and 1b): first #1109 on tags with its three channels and no new behaviour; then the
  fourth wire, the flags, the walk and the refusals. Two reviews; a transitional rule in `port_sets()` that 1b
  removes; the Sankey stack unblocks after 1a.

Recommendation: b (`small-prs`: one fix per PR).

## 7. Decision log

Numbers are never reused; later documents cite them as `D3 of the hydraulic circuits spec`. The owner's key sentence
is quoted; the full consequences are in §3.

| # | Decision | By, date | Owner's words; notes |
|---|---|---|---|
| D1 | Fluid circuits with an id: every member has, per circuit, a mass-flow input, a mass-flow output, a temperature input and a temperature output (a buffer or tank on several circuits has several sets). | owner, 2026-10-22 | Today's three quantities per loop become four per member (§3.1). |
| D2 | Detection, not prevention: no shared circuit-level flow value; each device publishes its own outputs through ports; a violation fails hard. | owner, 2026-10-22 | "why prevent? what advantage do we gain with the added complexity?" |
| D3 | The pump owner is explicit: no roles derived from positioning, list order or position enums; exactly one declared pump owner per circuit, checked at build; the three `PositionHotWaterStorageInSystemSetup` enums go. | owner, 2026-10-22 | "that is not salient and usually a bad design choice." The HDS's lag half of its enum goes with hisim-pnw7 (stage 3). |
| D4 | The check: `if abs(massflow_in - massflow_out) > tolerance: raise CircuitError(...)`, naming the circuit, the device, the step and both flows; not in `i_simulate`, where the returning flow can lag a pass. | owner, 2026-10-22 | Written for `i_doublecheck` every step; **where it runs is superseded by D11**. The rule stays. |
| D5 | Forced steps need no exception: only controllers are forced, physics always converges, so mass flows and temperatures are converged in every step. | owner, 2026-10-22 | No "forced" branch, no widened tolerance (§3.5). |
| D6 | *Proposed, not decided (agent, 2026-10-22; Q0):* HiSim keeps "no buses"; a named circuit is not one — it balances nothing and solves nothing; deleting every circuit declaration leaves every result bit-identical, deleting a bus changes results. | agent, 2026-10-22 | §3.2. |
| D7 | No top-level declaration: no `circuits:` block in the energy-system YAML, no `Simulator.add_circuit`, no assembly-level circuit declaration; the ports themselves declare that they belong to a fluid loop; the loop is what the wiring connects and the build finds it. | owner, 2026-10-22, after the first draft's review | "no top level declaration. the ports themselves should declare that they belong to a fluid loop". D1 to D5, Q0 to Q2, Q5 and the dry run are unchanged by it. |
| D8 | Fluid-loop tags on the channel definitions replace #1109's hydronic-port declaration: `add_hydronic_port`, `CircuitEnd` and `HydronicPort` go; a channel says in its own `add_input`/`add_output` call which loop and which quantity it is; the balance side of #1109 stays; #1109 is reworked onto the tags (stage 1a). Decides Q4 against its recommendation. | owner, 2026-10-27 | "the hydronic port thing doesn't sit right with me, since it's duplicating information about ports that then needs to be kept in sync. we've already discussed to use fluid_loops and use tagging directly on the component input/output definitions." — "yeah, update 1122." |
| D9 | Energy ports become channel tags on inputs too: `add_input(energy_port=...)`; `peer_input`/`peer_output` and the copy-outputs go (§3.7). The open choices are Q7 to Q10. | owner, 2026-10-27 | "can we also replace some of the energy port declarations with tags?" Recorded as the owner's request; the design is the agent's. |
| D10 | The supplier of a loop is declared (`supplies`, exactly one per loop, checked at build); the direction per step follows from the supplier's sign (positive: the loop's carrier from the supplier outward; negative: `COOLING` toward the supplier); a loop that may cool says so (`may_cool`), and a cooling step on any other loop fails the run. **Withdraws** the first amendment's rule of 2026-10-27 that no member declares a role and the sign names the supplier, wrong for cooling loops. | owner, 2026-10-27 | "for cooling loops this is wrong" — "yes, please rewrite it." (§3.4) |
| D11 | Every loop check of values (the mass checks of D4 and Q1, the energy check of Q2, the heating-only check of D10) runs once after the run, vectorized over the result columns beside `FiniteResultsCheck`; nothing per step or per pass; `i_doublecheck` keeps no production use; a violation still fails the run hard with the loop, the member, the first failing step, the number of failing steps and the values at the first one; the build checks are unchanged. | owner, 2026-10-23, confirmed 2026-10-27 | "maybe move the checking to post-processing as a vector-check to avoid hundreds of thousands of i_doublecheck calls?" — "i thought we agreed already to move the q1 mass checks also to postprocessing for speed reasons? i mean this will be about catching bugs, so 99.999% of runs will be fine" — "same for the energy check in q2". Agreed on 2026-10-23, written into the spec on 2026-10-27. |

**Rejected alternatives**, one line each:

- A top-level `circuits:` block, `Simulator.add_circuit`, `pump: true` on assembly ends and `Component.join_circuit`
  (the first draft's Q3): withdrawn by D7, a loop is declared at the ports and found through the wiring.
- `HydronicPort` extended into the port set, or #1109 closed and replaced (Q4 a and b): D8 reworks #1109 instead;
  a second declaration beside the channels has to be kept in sync, and closing #1109 loses a reviewed PR and the
  Sankey work on it.
- The role read off the sign of `m c (T_out − T_in)` (the first amendment's Q8): withdrawn by D10, wrong for cooling
  loops, where the supplier has the negative sign.
- Inferring the pump owner from the wiring or from a position enum (Q3 b): D3; and impossible once every member has a
  wired flow in.
- A per-step check in `i_doublecheck` (D4 as written): D11; hundreds of thousands of calls for a check that almost
  never fires.
- A circuit that holds a value or computes a remainder (a bus): the owner's "no buses" rule
  (`complete_energy_sankeys_spec.md` Q-J).

## 8. What changes and what does not

**Components (9).** Per component, its fluid loops (one `FluidLoop` per loop, a tag on each of its channels, the
loop's name the component's own), what it gains and what leaves:

| Component | Fluid loops (name: pumps?; supplies and may_cool per D10) | Gains | Loses |
|---|---|---|---|
| `GenericBoiler` | `space_heating`: either (`pumps_space_heating_circuit`, default true, Q3); `dhw`: pump owner; supplies both, cools neither | flow in on `dhw`; the flow in on `space_heating` exists (`WaterInputMassFlowSh`, #1117) and becomes the circuit's | `space_heating_runs_on_distribution_flow` inferred from the wiring |
| `MoreAdvancedHeatPumpHPLib` | `space_heating`, `dhw`: pump owner (no heater-on-flow mode yet); supplies both; `space_heating` may cool from its cooling-mode setting | flow in on both | `PositionHotWaterStorageInSystemSetup` (`PARALLEL`/`SERIES`/`NO_STORAGE`); `m_dot_ref` stays as the fixed flow of a heater-on-flow mode if one is built later |
| `DistrictHeating` | `space_heating`: either (`pumps_space_heating_circuit`, default false, Q3); `dhw`: pump owner; supplies both, cools neither | flow in on `dhw`; the space-heating pair exists | — |
| `ElectricHeating` | `dhw`: pump owner; supplies, never cools | flow in | — |
| `SolarThermalSystem` | `solar_dhw`: pump owner; supplies, never cools | flow in | — |
| `SimpleHotWaterStorage` (buffer) | `generator`, `secondary_generator`, `distribution`: never pumps; receives on the generator loops, supplies `distribution`; `may_cool=True` on all | flow out on each (pass-through) | `PositionHotWaterStorageInSystemSetup` (`PARALLEL_TO_HEAT_SOURCE`/`SERIES_TO_HEAT_SOURCE`): a series buffer is a three-member circuit |
| `SimpleDHWStorage` (tank) | `generator`, `secondary_generator`: never pumps; receives on both, never cools | flow out on each | — |
| `HeatDistribution` | `distribution`: either (`pumps_the_circuit`, default true, Q3); receives; `may_cool` from `cooling_enabled` (hisim-hds-cooling-option-m4g6) | flow out (pass-through when behind a pump owner); flow in in every role | `PositionHotWaterStorageInSystemSetup` (4 members); `pumps_its_design_flow` from the enum; `declares_energy_ports`; the lag half goes with pnw7 |
| `SimpleHeatSource` | brine circuit: either (`pumps_the_brine_circuit`, Q3); supplies | flow out | `use_external_massflow_as_signal_input_for_nominal_massflow` (a second pump owner by another name) |

**Results.** No KPI moves in any stage (the port sets are metadata on existing and new channels; the pass-through
outputs are copies; the enums are replaced by a flag that states the same fact). New result columns: one flow-out per
pass-through member and circuit (about 2 to 4 per system). Stage 4 fails nothing once pnw7 has landed.

**Public interfaces.** New: the flow channels (addresses in files); the configuration booleans of the four either-way
port sets (Q3); `CircuitError`, `CircuitWiringError`; for component authors, `FluidLoop`, `FluidLoopChannel`,
`LoopQuantity` and `FluidLoopError` in `hisim/energy_port.py`, `fluid_loop=` on `add_input` and `add_output`,
`energy_port=` on `add_input` (D9), `SignedEnergyPort` (Q9), `Component.port_sets()`. Removed: the three
`PositionHotWaterStorageInSystemSetup` enums and their configuration field (6 recorded lines, 13 code and test
files); `SimpleHeatSource.use_external_massflow_as_signal_input_for_nominal_massflow`; the HDS's
`declares_energy_ports`; from the unmerged #1109, `HydronicPort`, `CircuitEnd`, `HydronicPortError`,
`Component.add_hydronic_port`, `Component.hydronic_ports` and `CIRCUIT_LEG_UNITS` (D8); `EnergyPort.peer_output` (on
main twelve uses, nine of which #1109 already replaces), `EnergyPort.peer_input` (on the unmerged building branch) and
the five copy-outputs of §3.7 (D9). No file format changes: no new top-level key, no new assembly syntax, no new
`Simulator` method (D7); a tag lives in the class, never in a file.

**Recorded files.** Nothing is added to any file (D7). Of the 35 `energy_systems/*.energy_system.yaml` with a water
circuit (every file but the 9 without one: `air_conditioned_house`, `basic_household`, `default_connections`,
`dynamic_components`, `electrolyzer_with_renewables`, the two `simple_air_conditioner_*`, the two
`simple_system_setup_*`), plus `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` and
`tests/energy_systems/uc1.realized.energy_system.yaml`, only those change whose lines go or come: the 6 with a
`position_hot_water_storage_in_system` line (`gas_boiler_household`, the three district-heating files, the mockup,
`uc1.realized`) lose it in stage 3, and under Q3 a the three buffer-less boiler files (`gas_boiler_household`, the
mockup, `uc1.realized`) gain `pumps_space_heating_circuit: false` on the boiler in stage 1b (under Q3 d every
either-way component in every hydronic file gains its boolean). The 22 Python setups change nowhere but the
district-heating setup's enum line (no setup has a buffer-less boiler). The assemblies change nowhere but the two enum
lines (`test_partners.yaml`, the mockup assembly `air_source_heat_pump_direct.assembly.yaml`); the circuit-end binding
of `binding.py` stays as it is. The tags change no file: a tag is in the class, and the wires it is checked against
are the ones written today.

**RenoVisor.** The translator runs the grouped twins (`hisim/renovisor/translate.py`, `BY_GENERATOR`,
`WITH_SOLAR_THERMAL`: gas, oil, pellets, wood chips, hydrogen, heat pump, electric, district heating, and the two
solar twins), all among the 35; it names no hydronic input and no position enum, so it changes only where a grouped
file it copies changes: the district-heating twin's enum line in stage 3. No result moves; a heads-up on
renovisorissues for that one dropped line, since the base files RenoVisor ships change shape
(`system_docs/results_and_interfaces.md` §5).

**Goldens and twins.** Goldens compare KPIs only and stay bit-identical in every stage, except where a check finds a
real fault, which on today's main is nowhere for the mass checks (§5) and the two buffer-less HDS twins for the
energy check (Q2), which is why that check waits for pnw7. Re-recorded: the three buffer-less boiler files in stage
1b; the 6 files, the setup and the two assembly files in stage 3; everything else stays byte-identical.
`tests/assemblies/` twin gates and `tests/test_energy_system_*` parity tests are the gate.

## 9. Beads

Epic hisim-hydraulic-circuits-wci7 (P2, `components`, `owner-decision` while Q0 to Q3 and Q5 to Q12 are open). The
child beads are filed when the owner has decided, one per stage of §4. This PR files and edits no bead (a bead change
goes through `br`); the rows marked *to file* are the list for that.

| Bead | What it covers | This spec |
|---|---|---|
| hisim-hydraulic-circuits-wci7 (P2, epic, `owner-decision`) | this spec; D1 to D5 and D7 to D11; Q0 to Q3 and Q5 to Q12 | #1122 |
| hisim-hydronics-fxix.6 (P2) | stage E, `HydronicPort` on every circuit in the balance check (#870/#871) | **re-scoped by D8**: #1109 is its PR; "HydronicPort on every circuit" becomes "a loop tag on every circuit channel", the balance logic unchanged; its text is amended when the rework starts (stage 1a) |
| hisim-energy-sankeys-9uoo.3 (P1) | the energy ports of the water storages and the HDS | **#1109's second bead**, the same re-scope: the storages' and the HDS's circuits are tagged, not declared; their non-circuit ports (losses, stored change, the HDS's heat to the building) are unchanged |
| hisim-energy-sankeys-9uoo.6 (P1) | the energy ports of the building (branch `sankey-pr4-building`, no PR yet) | **reworked by D9**: the building tags its inputs instead of copying them into outputs with `peer_input=`; rebased onto the D9 PR before it opens |
| hisim-energy-sankeys-9uoo.20 (R1, P2) | build rule: refuse a pumping end whose flow nobody reads while another component reads its supply | **superseded by stage 1b**: §3.3 rules 2 and 3 refuse the same wiring and more, by walking the loop instead of a heuristic over readers of the supply temperature; close it when §3.3 lands, citing this spec |
| hisim-boiler-circuit-flow-gap-g2mx (P1) | the gas boiler's flow gap; a2 built in #1117; keeps the per-step closure, the thermostat-driven flow (hisim-component-physics-4g9.37) and the HDS enum removal | **partly absorbed**: the enum removal moves to stage 3; the per-step closure stays with pnw7; the thermostat-driven flow stays with 4g9.37; re-rate as its note says |
| hisim-pnw7 (P3) | the HDS's pipe water as a start-of-step node; D25's flow ownership; the HDS enum; district heating's bill | **a dependency and a partner**: pnw7 keeps the node and the bill; the enum removal and D25's "who pumps" move here; stages 3 and 4 follow it |
| hisim-hydronics-fxix.15 (P3) | design how a component learns its hydraulic topology, replacing the three enums | **absorbed** by §3.3 and Q3: a component learns nothing about its topology, it states whether it pumps, and the loop is found through the wiring; close it when the owner decides Q3 |
| *to file*, child of wci7 (P2, `components`) | D9: `energy_port=` on `add_input`; `peer_input`/`peer_output` and the copy-outputs go; `SignedEnergyPort` if Q9 a | the D9 PR, after stage 1a |
| *to file*, child of wci7 (P2, `components`) | stage 1b: the fourth wire, the pumping flag everywhere, the loop walk and its refusals, the Q3 booleans | after stage 1a |
| *to file*, child of wci7 (P2, `components`) | stage 2: the mass check and the heating-only check in the post-run check (D11), `CircuitError` | after 1b |
| *to file*, child of wci7 (P2, `components`) | stage 3: the three enums and `declares_energy_ports` go | after pnw7 |
| *to file*, child of wci7 (P3, `components`) | stage 4: the energy check | after pnw7 and stage 3 |
| *to file*, child of hisim-energy-sankeys-9uoo (P3, `cleanup`) | Q10 a: the test that `postprocessing_flag` and `energy_port` agree on every output | its own small PR |
| *to file*, child of hisim-energy-sankeys-9uoo (P3, `cleanup`, `owner-decision`) | Q10 b: the KPIs and the energy manager select by the port; the three flow flags retire | after the Sankey stack |
