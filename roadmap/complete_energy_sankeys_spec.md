# Spec: one connected energy Sankey shows the whole house, from the grid, the fuels and the environment to every end use and loss

Status: draft, D1 to D4 decided (owner, 2026-10-19), D1 revised for meters by Q-J (2026-10-20), open questions in
§8. Nothing of §4 is built: today only the
five heat generators declare energy ports, and the Sankeys are one diagram per carrier. Owner: Noah Pflugradt. Epic:
hisim-energy-sankeys-9uoo, acceptance bead hisim-mzft. Builds on: #870 and #871 (the energy-port contract, the balance check, the
per-carrier Sankeys), `roadmap/hydronic_coupling_spec.md` (stages B to D built, stage E is part of §9),
#1019 (the Sankeys carry their numbers).

All code citations are against `origin/main` at 4ae6eb82.

## 1. Problem

A full-year run of `household_heatpump_building_sizer` at 900 s writes a Sankey with one declared node, the heat
pump (the example run shown on #1019). Ambient heat (11,651 kWh) and electricity enter it,
space heat and hot-water heat leave it, and every other node is grey and "undeclared": the buffer, the hot-water
tank, the distribution system, the building, the PV system, the battery, the occupants. The heat pump's
electricity (3,647 kWh) appears to come from `L2EMSElectricityController`, because the heat pump's electricity
ports name their peer through the output the energy manager reads (`EnergyPort(..., peer_output=ElectricalInputPowerSH)`
in `more_advanced_heat_pump_hplib.py`, resolved by `Wiring.peer_of` in
`hisim/postprocessing/energy_balance/ports.py`). The gas boiler's fuel likewise comes from "undeclared: GasMeter".
14 of 15 components are undeclared in both the heat-pump and the gas twin (`balance_report.json` of §7).

The diagrams are also split by carrier (`SankeyDiagram.per_carrier` in `sankey.py`): electricity, ambient heat,
space-heating heat and hot-water heat each get their own picture, and the heat pump joins them only in
`overall.html`, where every link is the same grey (hisim-energy-sankeys-9uoo.18).

What is wanted (hisim-mzft): for every recorded twin, one diagram from the sources (grid, sun, ambient heat, fuel,
district heat) through the devices, storages and the distribution system to the end uses and losses, with no
undeclared node and a residual of zero within the tolerance.

## 2. Decisions

**D1 — Controllers are not nodes of the energy Sankey; meters are** (owner, 2026-10-19; revised for meters by Q-J,
2026-10-20). "I don't see any way to make that make sense": a controller decides, it moves no energy. As first
decided, meters were not nodes either: electricity was to flow between the devices through a per-building "house
electricity bus", with grid import and export derived as the bus's per-step residual and the meter as a
cross-check. Q-J withdrew the bus and the residual: "HiSim explicitly does not use buses as a concept, since they
bring a whole host of problems", and a bus closes by construction, so electricity that a declared port gets wrong
or misses would disappear into grid import or export instead of showing as an imbalance. Q-J also made every meter
a node: "verifying if all the electricity is metered is a really good check". Consequence: a meter stands between
its boundary node (grid import and export, `natural gas supply`, …) and the devices whose flows it reads through
explicit connections (§4.4); grid import and export are what the electricity meter publishes, never derived; what a
meter publishes must equal what it reads in every step, and a gap is a red `unaccounted` flow at the meter.

**D2 — One connected all-carrier diagram, in which energy changes carrier along the chain** (owner, 2026-10-19).
In the style of the owner's data-center Sankey, not separate per-carrier diagrams joined by artificial couplings.
Consequence: appliance electricity becomes internal heat gains and flows into the building, where it appears once;
heat-pump electricity plus ambient heat becomes space-heating and hot-water heat, then flows through buffer and
tank and the distribution system into the building; losses branch off at every node (inverter, charging, standby,
distribution, combustion); storages show charge, discharge and the change of what they hold; the boundary flows are
grid import and export, ambient heat, sun, fuel, district heat and the building's transmission and ventilation
losses to outside. Per-carrier views may remain, as filters of the same graph.

**D3 — An imbalance a newly declared component shows is fixed, one by one; no temporary exemption for a
component whose balance does not close** (owner, 2026-10-19). "That is the main point after all." Consequence: each
device-group PR (§9) declares the ports, runs the balance on the recorded twins, and either closes or lists every
imbalance it found, each with its own bug bead and its own fix PR. Because the check fails every run with a
non-closing declared component and `golden-year.yml` runs every golden setup for a full year, a fix lands before
the PR that declares the component, or in it, never after. The exemption list (§5, Q-B) holds only components that
have no energy port to declare: controllers, data sources without energy, helpers, and a meter that declares nothing
it meters.

**D4 — The renderer is plotly, as today; an arrow-style renderer is a later step** (owner, 2026-10-19). Start
with the interactive HTML and look at how the complete diagram reads. The owner prefers arrows in the style of his
data-center picture (`matplotlib.sankey`-like, losses as side branches), but its automatic layout is involved.
Consequence: the graph model (§4) does not depend on the drawing; one graph, one renderer (plotly) now. The arrow
renderer is bead hisim-energy-sankeys-9uoo.19 (P3), with what it needs: orientation rules per node kind, losses as side branches,
sources ordered left and sinks right.

The epic's earlier decisions stand: the tolerance (decision 6 of hisim-energy-sankeys-9uoo: 0.1 % of the run's throughput, per step
`max(1e-6 kWh, 0.1 %)`), and every failing balance or one-sided link fails the run (owner, 2026-10-02).

## 3. Terms

- **Port**: an output of a component declared with an `EnergyPort` (role `IN`, `OUT`, `LOSS`, `STORED_CHANGE`, a
  carrier, a peer) or a `HydronicPort` (a water circuit: mass flow, supply and return temperature, role `IN` or
  `OUT`). Read in kWh per step (`EnergyPort.kilowatt_hours_per_step`).
- **Component node**: a physical component (kind `PhysicsComponent`, or a data source that moves energy, the
  occupancy) that declares ports. Its balance is `ΣIN − ΣOUT − ΣLOSS − ΣSTORED_CHANGE = 0` per step over all its
  ports and carriers.
- **Exempt component**: a component that has no energy port: by its kind (L1 and L2 controllers, and a meter that
  declares no metered exchange) or by a stated reason (§5). It is never a node and never a peer.
- **Meter node**: a component that declares what it publishes as the exchange of a carrier at the system boundary
  (`Component.metered_exchanges`, a `MeteredExchange`: carrier, import output, optional export output). Drawn under
  its own name (`ElectricityMeter`, `GasMeter`, `FuelMeter`) between its boundary node and the devices it reads
  (§4.4).
- **Metered port**: an `IN` or `OUT` port of electricity or of a bought carrier (`EnergyPort.is_metered`). Its flow
  must pass a meter; a metered port whose peer is no meter and no component is **unmetered** (§4.4).
- **Boundary node**: where energy enters or leaves the system: grid import, grid export, the supply of each bought
  carrier, ambient heat, sun, the residents' body heat, hot water drawn at the taps, outdoors.
- **Loss node**: one per `LOSS` port, named after its component and port (`Battery · inverter loss`).
- **Storage node**: one per component with a `STORED_CHANGE` port (`Battery · stored`).
- **Residual**: `ΣIN − ΣOUT − ΣLOSS − ΣSTORED_CHANGE` of a component over the run, drawn to or from the red
  `unaccounted` node; zero within the tolerance in every run that finishes.
- **Persistent stored change**: the change of the energy a component holds from the start to the end of a step,
  computed from the state it carries into the next step. A `STORED_CHANGE` port publishes this, never a quantity
  that only the step's own equations use (otherwise the check proves nothing; §6.4 B1).

## 4. Design: the graph model

### 4.1 Node kinds

| Kind | Formed from | Example | Colour (plotly, today's palette) |
|---|---|---|---|
| component | every component that declares at least one port | `MoreAdvancedHeatPumpHPLib`, `Building`, `UTSPConnector` | blue |
| meter | each component that declares a metered exchange (§4.4) | `ElectricityMeter`, `GasMeter` | dark grey |
| boundary | the environment of a port (§4.3) or of a meter's published exchange (§4.4) | `grid import`, `natural gas supply`, `ambient heat`, `sun`, `outdoors` | green |
| loss | each `LOSS` port | `SimpleHotWaterStorage · standby loss` | orange |
| storage change | each `STORED_CHANGE` port | `Battery · stored` | gold |
| unaccounted | each component residual, each meter residual and each link difference | `unaccounted` | red |
| undeclared | a physical component that declares no port and has no exemption: a coverage view until the acceptance PR, a failure after it (§9, PR 6) | `undeclared: Building` | light grey |

Node labels use the component's display name once hisim-9w3g gives one, else its instance name; the loss and
storage nodes add the port's display name.

### 4.2 Edge kinds and how an edge is formed

Every edge carries one carrier (`lt.EnergyBalanceCarrier`), a yearly value in kWh (always drawn positive; a negative
total turns the edge round, as `SankeyBuilder.add` does) and the ports it came from. Links are coloured by carrier
(hisim-energy-sankeys-9uoo.18; a colour-blind-safe palette such as Okabe-Ito).

1. **Transfer** between two components that both declare ports: a producer's `OUT` port and a consumer's `IN` port
   of the same carrier whose peers name each other through the wiring (`DeclaredPorts.declared_links`, unchanged).
   Drawn once, at the receiver's value; the check fails a pair whose totals differ beyond the tolerance and a pair
   only one side books (as today). A difference beyond the tolerance is drawn at the sender, to or from
   `unaccounted`.
2. **Circuit**: the two ends of a water circuit, both declaring the same `HydronicPort` (supply owner `OUT`, receiver
   `IN`), paired by who reads the circuit's mass-flow output (`hisim/energy_port.py`, "Hydronic circuits"). Its heat
   is `m c (T_sup − T_ret) dt` from the three published values, computed once for both ends, so a circuit cannot
   disagree with itself. **`HydronicPort` gains a carrier** (`SPACE_HEATING_HEAT` or `DOMESTIC_HOT_WATER_HEAT`): the
   diagram must know which heat a circuit carries. A cooling circuit keeps the carrier of its circuit and its
   negative heat, which turns the edge round (§4.6).
3. **Metered edge**: a metered port whose peer is a meter: `IN` is meter → component, `OUT` component → meter (§4.4).
4. **Meter boundary edge**: what a meter publishes: its import from its boundary node (`grid import`,
   `<carrier> supply`) and its export to `grid export` (§4.4). Never derived from the devices.
5. **Boundary edge**: an `IN` port from its environment node (fuel, district heat, ambient heat, sun, body heat), or
   an `OUT` port to its environment node (hot water drawn at the taps).
6. **Loss edge**: a `LOSS` port to its loss node. A loss node carries its total onward to `outdoors` only in the
   aggregate (the renderer may merge loss nodes, D4); the graph keeps one node per loss so that every loss is
   readable where it occurs.
7. **Storage edge**: a `STORED_CHANGE` port between the component and its storage node, component → storage when
   the run's net is positive (it charged), storage → component when negative.
8. **Residual edge**: a component's or a meter's residual, to or from `unaccounted`, drawn only where it is beyond
   the balance's tolerance, the same tolerance that fails a run (owner, 2026-10-20). A residual within the
   tolerance, float noise above all, is not drawn; the report lists every residual.

### 4.3 How a port finds its peer

Today's rule (`Wiring.peer_of`) resolves `peer_input`/`peer_output` through the wiring and takes the first reader,
preferring declared readers. Three changes make D1 and Q-J hold:

- **An exempt component is never a peer.** Readers and sources whose kind is `L1ControllerComponent` or
  `L2ControllerComponent` (`hisim.component.ComponentKinds.of`), a meter that declares no metered exchange, or a
  component that carries an exemption reason (§5) are skipped.
- **A metered port goes to the meter that reads it** (Q-J). A metered port (§3) watches its own output, whatever its
  role (or its `peer_output`), and its peer is the first that reads it through an explicit connection: (1) a
  component that reads it directly and is not exempt, a meter of the port's carrier first, then a component that
  declares ports, then the first in the order of the run; (2) else a meter of the port's carrier reached through
  exempt components, breadth first, each passing the flow on through its outputs of the same load type. In the
  household twins the energy manager reads the heat pump's, the PV system's, the battery's and the occupancy's
  electricity and gives the electricity meter one signed sum (`TotalElectricityToOrFromGrid`): each device links to
  the meter, whose input carries their sum. What a controller hands to anything but a meter is a decision (a set
  point), not the flow, so only a meter is taken through a controller. A meter is the peer only of a port of a
  carrier it meters. Nothing depends on how another component is modelled: only on published ports, declared
  metered exchanges and the explicit connections.
- **Every other port falls back to its carrier's boundary default**, used when the wiring names no peer:

| Carrier | Default for `IN` | Default for `OUT` | Default for `LOSS` |
|---|---|---|---|
| `ELECTRICITY` | none: unlinked (and unmetered) | none: unlinked (and unmetered) | outdoors |
| `NATURAL_GAS`, `HEATING_OIL`, `PELLETS`, `WOOD_CHIPS`, `HYDROGEN`, `DIESEL` | `<carrier> supply` boundary (unmetered) | none: an error | outdoors |
| `DISTRICT_HEAT` | `district heat supply` boundary (unmetered) | none: an error | outdoors |
| `AMBIENT_HEAT` | `ambient heat` (as today) | `ambient heat` (heat rejected by a cooling device) | outdoors |
| `SOLAR` | `sun` (today `solar`) | none: an error | outdoors |
| heat carriers (`SPACE_HEATING_HEAT`, `DOMESTIC_HOT_WATER_HEAT`, `COOLING`, `ROOM_HEAT`) | none: names a component, a circuit or an environment node | same | outdoors |

So the heat pump's electricity ports drop `peer_output` and link to the electricity meter through the energy manager;
the boiler's fuel ports drop `peer_output=EnergyDemandSh` and link to the gas or fuel meter that reads them; the
district-heat ports link to the fuel meter. The meters stay wired as they are and are drawn. A port that has no peer
after these rules is `unlinked`: reported today, a failure from the acceptance PR on (fail loudly: energy that goes
nowhere physical is a wiring fault).

New members of `lt.EnvironmentNode`: `GRID_IMPORT` ("grid import"), `GRID_EXPORT` ("grid export"),
`HOT_WATER_USE` ("hot water drawn at the taps"), `RESIDENTS` ("residents' body heat"); `SOLAR` is renamed in its
value to "sun". The `<carrier> supply` nodes are derived from the carrier, not listed.

New carrier `lt.EnergyBalanceCarrier.ROOM_HEAT` (Q-G): heat released into or lost from the heated zone, the carrier
of internal gains, of the building's losses and of its thermal mass. Space heating keeps `SPACE_HEATING_HEAT` up to
the building's input, so the space-heating filter shows the heating chain and not the whole building.

### 4.4 The meters (owner decision Q-J)

There is no bus and no derived residual anywhere. A meter is a node, formed from what it declares and what it reads:

- **What it publishes.** A meter declares one `MeteredExchange` per carrier it meters
  (`Component.metered_exchanges`): `ElectricityMeter` its `ElectricityFromGridInWatt` and `ElectricityToGridInWatt`,
  `GasMeter` its `GasFromGrid` (natural gas or hydrogen, as configured), `FuelMeter` its `HeatConsumption` (oil,
  pellets, wood chips or district heat, as configured). The published import enters the meter from `grid import` or
  `<carrier> supply`; the published export leaves it to `grid export`. Grid import and export are these published
  outputs, never derived. `HeatingMeter` declares no exchange (no setup uses it) and stays exempt.
- **What it reads.** Its peers: every metered port of its carrier whose peer resolves to it (§4.3). A consumer's `IN`
  port takes from it, a producer's `OUT` port gives to it. A battery is read twice, charging as `IN` and discharging
  as `OUT`.
- **The check (system balance, hisim-energy-sankeys-9uoo.8)**, per step `t`, in kWh:
  `residual_t = (import_t − export_t) − (Σ IN_t − Σ OUT_t)` over the ports it reads. It must be zero within the
  tolerance of the balance (decision 6), relative to the step's throughput `max(import + Σ OUT, export + Σ IN)`. A
  gap beyond the tolerance is the meter's residual, drawn as a red `unaccounted` flow at the meter node and listed
  as a failing entry under `meters` in the report (which lists every meter's residual and its largest per-step
  residual, within the tolerance too); it is never absorbed into the import or the export. A gap means the meter reads a
  flow no declared port carries (an undeclared device, or a device the energy manager sums but whose port is
  wrong), or a port linked to it that the meter does not count.
- **Every bought carrier passes a meter.** A metered port whose peer is neither a meter nor a component (a boiler's
  gas port that no meter reads comes straight from `natural gas supply`; an electricity port nothing reads is
  unlinked) is **unmetered**, listed under `unmetered` in the report. A metered port whose peer is a component hands
  its flow on, and that component's ports are checked in turn.
- **Enforcement (Q-H).** Both findings are computed and written in every run. While a component of the run is
  undeclared, a meter may read flows no port declares, so they are reported with `enforced: false` and the reason;
  in a run without undeclared components they fail the run. From PR 6 on undeclared components fail the run anyway.
- One meter per building follows from the wiring: each building's meter reads its own devices; nothing groups ports
  by building.

### 4.5 Carrier conversion and the per-carrier filters

A component converts carriers inside its node: its in-edges and out-edges may carry different carriers, and only its
total over all carriers balances (as `BalanceCheck.balance` does today; the per-carrier totals stay in the report).
The one connected graph is `overall`. A per-carrier view keeps the edges of one carrier; a converting node then shows
only one side, which is expected, and the view draws no residual for it (today's `single_carrier` rule). The
storyline of D2 is the connected graph; the filters are for reading one carrier's flows.

### 4.6 Signs

Port values follow `system_docs/modelling_guide.md` §2: positive `IN` enters, positive `OUT` and `LOSS` leave,
positive `STORED_CHANGE` is stored. A new output carries a value `>= 0` in the direction its name says, or is one
signed output whose description states the positive direction; `negated` is for existing outputs. Every new output
names its unit (`...InWatt`, `...InWattHour`).

- A loss may run negative in a step: the buffer's `StandbyHeatLoss` reaches −9.9 W in the gas twin, a tank colder
  than its surroundings gains heat. The check keeps the signed value per step; the diagram draws the run's net.
- A component that can heat and cool publishes two outputs (`ThermalPowerDeliveredForSpaceHeating` and
  `ThermalPowerDrawnForCooling` of the heat pump are the pattern): heating is `OUT` of a heat carrier, cooling is
  `IN` of `COOLING` (heat taken out of the receiver, positive). On a water circuit the sign stays the circuit's own
  (§4.2, 2).

## 5. Port tables per device group

Columns: carrier, role, the HiSim output that carries it (existing, or **new**), peer. "Twin" names a recorded
twin that exercises the component.

### 5.1 Water storages and the heat distribution system (PR 3; hydronic stage E)

Each circuit becomes one `HydronicPort` at both ends; the storages' and generators' heat `EnergyPort`s on the
circuit are replaced by it (`hydronic_coupling_spec.md` §3.5).

**`SimpleHotWaterStorage` (buffer)**, twins: gas, oil, pellets, wood chips, hydrogen, heat pump, solar thermal.

| Carrier | Role | Output(s) | Peer |
|---|---|---|---|
| SH heat | `IN` circuit from generator | generator's `MassFlowOutputSH`/`WaterOutputMassFlowSh`, generator's supply temperature, buffer's `WaterTemperatureToHeatGenerator` | the generator |
| SH heat | `IN` secondary circuit | `...SecondaryHeatGenerator` outputs | second generator |
| SH heat | `OUT` circuit to HDS | HDS's `WaterMassFlowHDS`, buffer's `WaterTemperatureToHeatDistribution`, HDS's `WaterTemperatureOutput` | the HDS |
| SH heat | `LOSS` | `StandbyHeatLoss` (W) | outdoors (Q-F) |
| SH heat | `STORED_CHANGE` | `ThermalEnergyIncreaseInStorage` (Wh) | its storage node |

**`SimpleDHWStorage` (hot-water tank)**, twins: every household twin.

| Carrier | Role | Output(s) | Peer |
|---|---|---|---|
| DHW heat | `IN` circuit(s) | generator's DHW mass flow and supply temperature, tank's `StepMeanWaterTemperatureToHeatGeneratorInCelsius` (and the secondary one) | the generator(s) |
| DHW heat | `OUT` | `ThermalPowerConsumptionDHW` (W) | `HOT_WATER_USE` boundary |
| DHW heat | `LOSS` | `StandbyHeatLoss` (W) | outdoors |
| DHW heat | `STORED_CHANGE` | `ThermalEnergyIncreaseInStorage` (Wh) | storage node |

The tap draw goes to a boundary node, not to the occupancy: the occupancy is a data source, which reads nothing and
so cannot publish the heat it receives. `ThermalEnergyUnmetDHWInWattHour` is not a flow (it was never delivered) and
gets no port; the report may list it beside the diagram.

**`HeatDistribution` (HDS)**, twins: every twin with a hydronic system.

| Carrier | Role | Output(s) | Peer |
|---|---|---|---|
| SH heat | `IN` circuit from the buffer (or the generator without buffer) | `WaterMassFlowHDS`, `WaterTemperatureInlet`, `WaterTemperatureOutput` | buffer or generator |
| SH heat | `OUT` | `ThermalPowerDelivered` (W, signed today: positive heating) | the building (`peer_output`) |
| SH heat | `LOSS` | none today: the model has no pipe loss | – |

Sign: `ThermalPowerDelivered` is split into a heating output (`>= 0`, `SPACE_HEATING_HEAT` `OUT`) and a cooling
output (`>= 0`, `COOLING` `IN`) when cooling through the HDS is built (hisim-energy-sankeys-9uoo.17, hisim-hydronics-fxix.7); no twin cools
through the HDS today.

The generators' circuit ends (boiler, heat pump, district heating, electric heater, solar collector) become
`HydronicPort`s in the same PR; their fuel, electricity, ambient, solar and loss ports stay `EnergyPort`s.

### 5.2 The remaining heat generators (built in #871, re-pointed in PR 1)

| Component | `IN` | `OUT` | `LOSS` | Change in PR 1 |
|---|---|---|---|---|
| `GenericBoiler` | fuel (`EnergyDemandSh`, `EnergyDemandDhw`, Wh) | SH and DHW circuits | `CombustionHeatLoss` (W) | fuel peer: the gas or fuel meter that reads it (Q-J) |
| `MoreAdvancedHeatPumpHPLib` | `ElectricalInputPowerSH`, `...DHW`, `...ForCooling`, `...BrinePump` (W); `ThermalPowerInputFromEnvironment` (W) | SH, DHW circuits; `ThermalPowerDrawnForCooling` (`COOLING` `IN`) | none (hplib is a converter: heat = electricity + ambient) | electricity peer: the electricity meter, through the energy manager (Q-J); `...ForCooling` and `...BrinePump` are read by nothing and stay unmetered until hisim-component-physics-4g9.39 |
| `DistrictHeating` | `ThermalOutputShEnergy`, `ThermalOutputDhwEnergy` as `DISTRICT_HEAT` (Wh) | SH, DHW | none today; the substation's pipe water is hisim-pnw7 | district heat peer: the fuel meter |
| `ElectricHeating` | `ElectricOutputShPower`, `...DhwPower` (W) | `ThermalOutputShPower` to the building, DHW circuit | none (efficiency 1) | electricity peer: the electricity meter |
| `SolarThermalSystem` | `SolarPowerOnCollector` (W, `SOLAR`), `ElectricityConsumptionOutput` (W) | DHW circuit | `CollectorHeatLoss` (`SOLAR`), `SolarPumpHeatLoss` (`ELECTRICITY`) | electricity peer: the electricity meter; sun node renamed |

### 5.3 Electricity devices (PR 2)

**`PVSystem`**, every twin with PV.

| Carrier | Role | Output | Peer |
|---|---|---|---|
| `ELECTRICITY` | `OUT` | `ElectricityOutput` (W, AC) | the electricity meter, through the energy manager |
| `SOLAR` | `IN` | **new** `SolarPowerOnModulesInWatt`: plane-of-array irradiance times module area (Q-D) | sun |
| `SOLAR` | `LOSS` | **new** `ModuleAndInverterLossInWatt`: solar in minus AC out | outdoors |

The two new outputs need the plane-of-array irradiance in the PV series (`produce_pv_series` in
`generic_pv_system/calculation.py` returns AC power ratios only), so the PV cache key changes; no KPI moves. If Q-D
is answered "start at the electricity", PVSystem declares only the `OUT` port and is a source of the meter.

**`Battery` (`advanced_battery_bslib`)**, every `*_building_sizer` household twin.

| Carrier | Role | Output | Peer |
|---|---|---|---|
| `ELECTRICITY` | `IN` | `ChargingPower` (W, `>= 0`; `peer_output=AcBatteryPowerUsed`, the signed output the energy manager reads) | the electricity meter, through the energy manager |
| `ELECTRICITY` | `OUT` | `DischargingPower` (W, published `<= 0`: `negated=True`; `peer_output=AcBatteryPowerUsed`) | the electricity meter, through the energy manager |
| `ELECTRICITY` | `LOSS` | **new** `InverterLossInWatt` = `AcBatteryPowerUsed − DcBatteryPowerUsed` | its loss node |
| `ELECTRICITY` | `LOSS` | **new** `CellLossInWatt` = `DcBatteryPowerUsed − StoredEnergyIncreaseInWattHour / dt` | its loss node |
| `ELECTRICITY` | `STORED_CHANGE` | `StoredEnergyIncreaseInWattHour` (Wh, from the state of charge) | storage node |

bslib is a black box with three published numbers (AC, DC, state of charge), so the two losses are differences of
them. The balance then checks that the published flows are consistent with each other (no sign or unit error), not
bslib's physics. Measured: inverter loss 808 kWh and cell loss 157 kWh a year in the heat-pump twin, never negative
in a step (§7).

**`UtspLpgConnector` (occupancy: residents and appliances)**, every household twin. A data source that moves
energy: it declares.

| Carrier | Role | Output | Peer |
|---|---|---|---|
| `ELECTRICITY` | `IN` | `ElectricalPowerConsumption` (W) | the electricity meter, through the energy manager |
| `ROOM_HEAT` | `IN` | `HeatingByResidents` (W) | `RESIDENTS` boundary |
| `ROOM_HEAT` | `OUT` | `HeatingByDevices` (W) | the building (reads it) |
| `ROOM_HEAT` | `OUT` | **new** `BodyHeatToBuildingInWatt`, equal to `HeatingByResidents` (an output carries one port, so the body heat's way out needs its own output) | the building |
| `ELECTRICITY` | `LOSS` | **new** `ApplianceElectricityNotReleasedIndoorsInWatt` = consumption − device heat gains | outdoors |

The LoadProfileGenerator gives device heat gains as a profile of its own, not as the electricity: 2,887 of
4,659 kWh a year become gains, 1,772 kWh leave with waste water, exhaust air and light (§7); the device gains never
exceed the electricity in a step. The body heat passes through the occupancy node so that the building has one peer
for all internal gains. The hot water the residents draw is the tank's `OUT` to `HOT_WATER_USE` (§5.1).

**`L2GenericEnergyManagementSystem`**: exempt (D1). **`ElectricityMeter`**: a node (Q-J), §4.4.

### 5.4 The building (PR 4)

`Building` (`hisim/components/building/building.py`), every household twin. The 5R1C model of ISO 13790 solved by
Crank-Nicolson (`calc_crank_nicolson`): the air and surface nodes have no heat capacity, the thermal mass `C_m` has.

| Carrier | Role | Output | Peer |
|---|---|---|---|
| `SPACE_HEATING_HEAT` | `IN` | **new** `HeatReceivedInWatt` = the `ThermalPowerDelivered` input when `>= 0` (`peer_input=ThermalPowerDelivered`) | HDS or electric heater |
| `SPACE_HEATING_HEAT` | `IN` | **new** `HeatReceivedFromChpInWatt` (`peer_input=ThermalPowerCHP`) | CHP |
| `COOLING` | `OUT` | **new** `HeatRemovedByCoolingInWatt` = minus the input when `< 0` | air conditioner, cooling HDS |
| `ROOM_HEAT` | `IN` | `InternalHeatGainsFromOccupancy` (W, residents plus devices; `peer_input=HeatingByDevices`) | the occupancy; the link pairs it with the sum of the occupancy's two `OUT` ports |
| `SOLAR` | `IN` | `SolarGainThroughWindows` (W) | sun |
| `ROOM_HEAT` | `LOSS` | **new** `VentilationLossInWatt` = `H_ve (θ_air − θ_e)` | outdoors |
| `ROOM_HEAT` | `LOSS` | **new** `TransmissionLossWindowsAndDoorsInWatt` = `H_tr_w (θ_s − θ_e)` | outdoors |
| `ROOM_HEAT` | `LOSS` | **new** `TransmissionLossOpaqueInWatt` = `H_tr_em (θ_m − θ_e)` | outdoors |
| `ROOM_HEAT` | `LOSS` | **new** `GainsLostThroughWindowsInWatt` = `H_tr_w / (9.1 A_t) · (0.5 Φ_int + Φ_sol)` | outdoors |
| `ROOM_HEAT` | `STORED_CHANGE` | **new** `ThermalMassStoredEnergyIncreaseInWattHour` = `C_m (θ_m,end − θ_m,start)` of the state | storage node |

Notes:

- The losses use the node temperatures the solver used, before the window opening overwrites the air temperature
  (below). `θ_m` in the opaque loss is the step's average bulk temperature, as the solver uses it.
- **The gain split of ISO 13790 (C.3)** puts `1 − A_m/A_t − H_tr_w/(9.1 A_t)` of the solar and half the internal
  gains on the surface node and `A_m/A_t` on the mass; the share `H_tr_w/(9.1 A_t)` (0.77 % in the twins) goes to no
  node. The standard means it as gains on the window surfaces that leave through the windows; the building declares
  it as the loss `GainsLostThroughWindowsInWatt`, a law of the model, not a fix. Measured 46.7 kWh a year; with it
  the building closes to 0.0000 W in every step against the step's own mass equation (§7).
- **The stored change must be the persistent one**, the energy the mass carries into the next step. Today the mass
  state carried forward is the step's *average* bulk temperature (`self.state.thermal_mass_temperature_in_celsius =
  thermal_mass_average_bulk_temperature_in_celsius` in `Building.i_simulate`), not the end temperature
  `θ_m,next` that the step's equation solved. So a step's flows put `C_m (θ_next − θ_prev)` into the mass and the
  mass keeps half of it: imbalance B1 of §6.4.
- **Window opening** (`occupants_open_the_windows`) overwrites the published indoor air temperature with
  `max(initial, outside)` after the solve and removes no heat: no node temperature, no flow and no state changes
  with it. The Sankey therefore has nothing to draw for it, and the published air temperature differs from the
  solver's by up to 7.96 K in 1,986 steps of the heat-pump year. Q-E asks how to treat it.
- **Cooling** by an air conditioner arrives as a negative `ThermalPowerDelivered` and leaves the building as
  `COOLING` `OUT` to the cooling device; the device takes it in as `COOLING` `IN` and rejects it with its
  electricity to `ambient heat` (or `outdoors`). The cooling view of hisim-i7yq is the filter of these edges.

### 5.5 The rest, and every component's verdict (PR 5)

Every component class in `hisim/components` (64, listed with `ComponentKinds.of` on 4ae6eb82). "Declares" means it
has ports after this epic; "exempt" means it is never a node.

| Component (module) | Kind | Verdict | Carriers (in / out / loss / stored) or reason | PR |
|---|---|---|---|---|
| `Battery` (advanced_battery_bslib) | physics | declares | §5.3 | 2 |
| `CarBattery` (advanced_ev_battery_bslib) | physics | declares | el in from its meter (`AcBatteryChargingPower`), el out to the car (discharge for driving), inverter and cell loss, stored | 5b |
| `CHP` (advanced_fuel_cell) | physics | declares | hydrogen or gas in; el out to its meter, SH heat out; conversion loss | 5c |
| `AirConditioner` (air_conditioner) | physics | declares | el in; heating: ambient in, SH heat out to the building; cooling: `COOLING` in from the building, heat rejected to ambient | 5a |
| `AirConditionerController` | L1 | exempt (kind) | controller | – |
| `Building` (building.building) | physics | declares | §5.4 | 4 |
| `ElectrolyzerController`, `SimpleController`, `FuelCellController`, `L1Controller` (EV charge), `GenericBoilerController`, `L1CHPController`, `DistrictHeatingController`, `ElectricHeatingController`, `GenericHeatPumpController`, `HeatDistributionController`, `IdealizedElectricHeaterController`, `MoreAdvancedHeatPumpHPLibControllerDHW`, `...SpaceHeating`, `SimpleAirConditionerController`, `SolarThermalSystemController` | L1 | exempt (kind) | controllers decide, move no energy (D1) | – |
| `L2GenericEnergyManagementSystem`, `PTXController`, `XTPController`, `HeatingCurveController`, `NightSetbackController` | L2 | exempt (kind) | D1 | – |
| `ElectricityMeter`, `GasMeter`, `FuelMeter` | meter | node (Q-J) | publishes its exchange (`metered_exchanges`), checked against what it reads, §4.4 | 1 |
| `HeatingMeter` | meter | exempt (kind) | declares no metered exchange; no setup uses it | – |
| `CSVLoader` | data source | exempt (reason) | an arbitrary series; a consumer reading energy from it takes the energy from its carrier default | 5 |
| `RandomNumbers`, `TariffProvider`, `Weather` | data source | exempt (reason) | no energy; the sun and the ambient heat are boundary nodes of the ports that take them | 5 |
| `UtspLpgConnector` | data source | declares | §5.3 | 2 |
| `ExampleComponent`, `SimpleStorage` (example_storage), `ExampleTransformer` | physics | exempt (reason) | teaching examples whose outputs have no physical carrier | 5 |
| `ComponentName` (example_template) | physics | declares one example port | the template teaches the pattern (`CLAUDE.md`, "Adding a new component") | 5 |
| `CalculateOperation`, `SumBuilderForTwoInputs`, `SumBuilderForThreeInputs` (sumbuilder) | physics | exempt (reason) | arithmetic helpers; summing energy is not moving it | 5 |
| `GenericBoiler`, `MoreAdvancedHeatPumpHPLib`, `DistrictHeating`, `ElectricHeating`, `SolarThermalSystem` | physics | declare (built) | §5.2 | 1, 3 |
| `Car` (generic_car) | physics | declares | electric: el in from the car battery, driving energy out to a boundary `driving`; fuel: `DIESEL` in from `diesel supply`, driving out | 5b |
| `SimpleCHP` (generic_chp) | physics | declares | gas or hydrogen in; el out, SH heat out; loss | 5c |
| `AdvancedElectrolyzer`, `Electrolyzer` | physics | declare | el in; hydrogen out; `EnergyLosses` loss | 5c |
| `HydrogenStorage` | physics | declares | hydrogen in, out, `HydrogenLosses`, `StorageDelta` stored | 5c |
| `FuelCell` (generic_fuel_cell) | physics | declares | hydrogen in; el out; heat as loss or out | 5c |
| `GenericHeatPump` (generic_heat_pump) | physics | declares | el, ambient in; SH heat out (`ThermalPowerDelivered` to the building); cooling as `COOLING` in | 5a |
| `HeatDistribution` | physics | declares | §5.1 | 3 |
| `IdealizedElectricHeater` | physics | declares | el in (its meter), SH heat out to the building | 5a |
| `MoreAdvancedHeatPumpHPLib` | physics | declares (built) | §5.2 | 1, 3 |
| `PVSystem` | physics | declares | §5.3 | 2 |
| `SimpleAirConditioner` | physics | declares | as `AirConditioner`, cooling only | 5a |
| `SimpleHeatSource` | physics | declares | heat out on its circuit from a boundary `heat source` (it is a fixed source by design) | 5a |
| `SimpleDHWStorage`, `SimpleHotWaterStorage` | physics | declare | §5.1 | 3 |
| `SimpleWaterStorage` | physics | n/a | abstract base of the two storages, never instantiated | – |
| `Transformer` (transformer_rectifier) | physics | declares | el in, el out, conversion loss | 5c |

Not components (helpers, no verdict): `configuration`, `dual_circuit_system`, `generic_car_profile`,
`lpg_car_information`, `lpg_local_time`, `more_advanced_heat_pump_hplib_model`, `pylpg_workspace`, the
`calculation`/`config`/`information`/`solar_gains`/`window` modules of the packages.

## 6. What changes and what does not

### 6.1 Results

- Declaring ports adds outputs and changes no KPI: a new output carries no `postprocessing_flag` that a KPI sums
  (`InandOutputType.ELECTRICITY_CONSUMPTION_*`, `ELECTRICITY_PRODUCTION`). The goldens compare KPIs only
  (`golden_references/README.md`), so a declaring PR moves no golden.
- A fix PR (D3) moves results where the fix changes the model; it follows `system_docs/results_and_interfaces.md`:
  KPI deltas in the PR description, goldens re-blessed through `golden-update.yml`, a heads-up on renovisorissues.
  The building's mass-state fix (B1) moves every building KPI of every household twin.

### 6.2 Interfaces

- `balance_report.json` gets schema `hisim.energy_balance/2`: an `exempt` list with each component's reason,
  `unlinked` ports, `meters` (each meter's published import and export, the ports it reads, its residual, failing
  steps, worst step and verdict, `enforced` and why not), `unmetered` (the metered ports that pass no meter, with
  `enforced` and why not), and `HydronicPort` circuits under `links`.
- `energy_sankeys/energy_sankey.json`: node kinds of §4.1, every edge with its carrier; `overall.html` is the
  connected diagram, one HTML per carrier as filters.
- `lt.EnergyBalanceCarrier.ROOM_HEAT`, `lt.EnvironmentNode` members of §4.3, `HydronicPort.carrier`,
  `Component.metered_exchanges()` with `MeteredExchange` (Q-J).
- New outputs of §5 (named with their units). No output is renamed.

### 6.3 What does not change

- The tolerance, the fail-hard rule, the check running in every simulation.
- The meters and the energy manager keep their wiring and outputs; the meters are drawn (Q-J), the energy manager
  is not.

### 6.4 Imbalances already found (each needs its bug bead and fix PR under D3)

| # | Component | Finding | Size (heat-pump twin, 2021, 900 s) | Fix |
|---|---|---|---|---|
| B1 | `Building` | The mass carries its step-average temperature into the next step, so it keeps half of the energy the step's flows put into it | with B2 declared: per step up to 2.56 kW; Σ\|residual\| 4,470 kWh/yr against 19,806 kWh/yr of gains; the run's net only −19.7 kWh because it telescopes; 34,903 of 35,040 steps beyond tolerance (gas twin: 3,280 kWh/yr, 34,876 steps) | carry `θ_m,next` as the state (ISO 13790 and RC_BuildingSimulator do): a model change, results move (the building's response to gains is today as slow as with twice its heat capacity) |
| B2 | `Building` | ISO 13790's gain split leaves `H_tr_w/(9.1 A_t)` of the gains on no node | 46.7 kWh/yr, 0.24 % of the gains | declared as the loss `GainsLostThroughWindowsInWatt`; no result change |
| B3 | `DistrictHeating` without buffer | bills the heat its controller asks for, not what the HDS receives (hisim-energy-sankeys-9uoo.12) | 30.8 kWh/yr (bead) | hisim-pnw7, before PR 3 declares the HDS in the district-heating twin |

The window opening (Q-E) is not an imbalance of the declared ports: it moves no energy in the model.

## 7. Measured results

Full year 2021 at 900 s, `origin/main` 4ae6eb82, local LoadProfileGenerator (CHR01, couple both at work), Aachen
test reference year; `EXPORT_ENERGY_BALANCE`, all outputs to CSV, 2026-10-19. The building's conductances and heat
capacity were read from the twin's own `Building` (`C_m = 19.998 MJ/K`, `H_ve = 61.8 W/K`, `H_tr_w = 38.0 W/K`,
`H_tr_em = 127.8 W/K`). Values in kWh over the year.

**Today's report.** In both twins the one declared generator closes (heat pump: 15,301.4 in and out, residual 0.0;
condensing gas boiler: 17,182.7, residual 0.0) and 14 components are undeclared.

### 7.1 `household_heatpump_building_sizer`

| Node | In | Out | Loss | Stored | Closes per step today |
|---|---|---|---|---|---|
| ElectricityMeter | PV 26,315.1, grid import 1,708.8, battery discharge 2,506.9 | appliances 4,658.5, heat pump SH 2,537.1, heat pump DHW 1,113.6, battery charge 3,461.3, grid export 18,760.3 | – | – | yes: what it publishes equals what it reads in all 35,040 steps (measured on 2026-10-19 as a derived bus against the meter, the same condition) |
| Battery | 3,461.3 | 2,506.9 | inverter 808.3, cell 157.2 | −11.2 | yes (losses never negative) |
| heat pump | electricity 3,650.7, ambient 11,650.6 | SH 11,980.4, DHW 3,321.0 | – | – | yes (declared today) |
| buffer | 11,980.4 | to HDS 11,408.2 | standby 575.1 | −2.8 | yes, 0 steps beyond tolerance; both circuits agree with their other end in every step |
| hot-water tank | 3,321.0 | drawn at the taps 3,037.3 | standby 286.2 | −2.5 | yes (unmet hot water 6.8, not a flow) |
| HDS | circuit 11,408.2 | to the building 11,408.2 | – | – | yes |
| occupancy | electricity 4,658.5, body heat 1,689.6 | device gains 2,886.7, body heat 1,689.6 | not released indoors 1,771.8 | – | yes, by its definition |
| Building | HDS 11,408.2, internal 4,576.3, solar 3,821.6 | – | ventilation 5,662.6, windows and doors 3,315.4, opaque 10,820.7, gains through windows 46.7 | step equation −39.4; persistent −19.7 | with B2 declared and the step's own stored change: yes, 0.0000 W; with the persistent change: no (B1) |

Sources of the connected diagram: sun 3,821.6 (windows; PV's sun input depends on Q-D), PV 26,315.1, ambient heat
11,650.6, grid import 1,708.8, body heat 1,689.6. Sinks: grid export 18,760.3, outdoors (building 19,845.4, tanks
861.3, battery 965.5, appliances not released 1,771.8), hot water drawn 3,037.3.

### 7.2 `household_gas_building_sizer`

| Node | In | Out | Loss | Stored | Closes per step today |
|---|---|---|---|---|---|
| ElectricityMeter | PV 26,315.1, grid import 511.4, battery discharge 1,928.0 | appliances 4,658.5, battery charge 2,670.6, grid export 21,425.4 | – | – | yes, in every step |
| Battery | 2,670.6 | 1,928.0 | inverter 633.5, cell 118.7 | −9.6 | yes |
| gas boiler | natural gas 17,182.7 | SH 10,903.4, DHW 3,312.1 | combustion 2,967.2 | – | yes (declared today) |
| buffer | 10,903.4 | to HDS 10,733.3 | standby 171.3 (negative in some steps, min −9.9 W) | −1.2 | yes |
| hot-water tank | 3,312.1 | drawn at the taps 3,044.2 | standby 265.5 | +2.4 | yes |
| HDS | 10,733.3 | to the building 10,733.3 | – | – | yes |
| occupancy | as in 7.1 | as in 7.1 | 1,771.8 | – | yes |
| Building | HDS 10,733.3, internal 4,576.3, solar 3,821.6 | – | ventilation 5,463.4, windows and doors 3,202.7, opaque 10,457.9, gains through windows 46.7 | step equation −39.5; persistent −19.7 | as in 7.1; B1 Σ\|residual\| 3,280 kWh/yr |

So, of everything the complete diagram of these two twins would show, only the building does not close; the
hydronic chain closes per step since hydronic stage D (#1011, #1035), and the electricity meter publishes what it
reads in every step once the electricity devices declare their ports.

## 8. Open questions

**Decided by the owner on 2026-10-19 (interview on this spec)** — they override the recommendations below where they differ:

| Question | Decision |
|---|---|
| Q-B exemption list | (a) exempt by kind plus a class attribute stating the reason, enforced by a `base` test (no objection) |
| Q-D where the PV starts | (a) at the sun: irradiance on the modules as a boundary flow, the conversion loss as a loss branch |
| Q-E window opening | neither option: window opening needs a proper ventilation model with an adjustable air-exchange rate (open windows do not make the indoor air equal the outdoor air without strong wind or fans). Bead hisim-opxv (P2). Until it lands, the Sankey draws no window flow. |
| Q-F tanks' standby losses | outdoors as modelled today (the tank in an unheated cellar); configurable later, bead hisim-af8m (P3 feature) |
| Q-G room heat | (a) the new carrier `ROOM_HEAT` |
| Q-H when the meter check fails the run (2026-10-20) | it is always computed and written to the report, and fails the run only once no component is undeclared; until then the report says `enforced: false` and why (built in PR 1, #1086). From PR 6 on undeclared components fail the run anyway, so the check is then always enforced. Under Q-J it applies to both meter findings: a meter that publishes other than it reads, and an unmetered port |
| Q-I static Sankey images for the PDF report (2026-10-20) | a P3 bead: PNG/SVG export of the connected diagram, after PR 6 and preferably with the arrow renderer (hisim-energy-sankeys-9uoo.19) |
| Q-J no bus; meters are nodes and follow their wiring (2026-10-20) | "HiSim explicitly does not use buses as a concept, since they bring a whole host of problems." The house electricity bus of the first D1 is removed with its derived residual: it closed by construction, so electricity a port got wrong or missed vanished into grid import or export. And: "verifying if all the electricity is metered is a really good check", so D1 is revised for meters: every meter is a node under its own name, between its boundary node and the devices whose flows it reads through explicit connections, traced through exempt controllers (§4.3); grid import and export are the meter's published outputs; per step what a meter publishes equals what it reads, a gap is a red `unaccounted` flow at the meter and a failing entry; every metered port passes a meter, or it is an `unmetered` finding. Both findings follow Q-H. Controllers stay exempt and are never nodes. Built in PR 1 (#1086). Follow-up answers the same day: a meter keeps its component name as its label; a residual (component, meter or transfer) is drawn only beyond the tolerance and always listed in the report; the setups that buy a carrier without a meter get one in hisim-meter-bought-carriers-lyfl, not in PR 1 |


- **Q-B. Where the exemption list lives.** A component is exempt either by kind or by a reason. (a) *Kind plus a
  class attribute*: controllers and meters are exempt by `ComponentKinds.of`; every other component that declares no
  port states `ENERGY_PORTS_EXEMPT_BECAUSE: ClassVar[str]` on its own class, and a `base` test instantiating no run
  walks every component class and refuses a physics class or a data source with neither ports nor the attribute.
  The class states it about itself (a black box), and a new component cannot slip through. (b) *One central file*,
  `hisim/postprocessing/energy_balance/exempt_components.yaml`, class name to reason, read by the check and kept
  honest by a test, like RenoVisor's `not_implemented_yet.yaml`; one place to review, but the knowledge sits away
  from the class. (c) *Kind only*: every physics component and data source must declare, the helpers too (SumBuilder
  would declare pass-through ports, Weather none and fail): no list at all, but ports with no meaning.
  Recommendation: (a). Bead: to be filed with the decision (`owner-decision`).
- **Q-D. Where the PV system starts.** (a) *At the sun*: PV declares the irradiance on its modules times their
  area as `SOLAR` `IN` and module plus inverter loss, like the solar collector does today; at about 20 % module
  efficiency the sun into PV is roughly five times its 26,315 kWh of electricity and the widest band of the diagram.
  Costs the plane-of-array irradiance in the PV series (cache key changes, no KPI moves). (b) *At its electricity*:
  PV is a source of the meter; no sun edge for PV, the narrowest diagram; inconsistent with the collector and the
  heat pump's ambient heat, which start at the environment. (c) *At its DC output* with the inverter loss only:
  between the two, needs the DC power from pvlib. Recommendation: (a), for one rule for every converter of
  environmental energy; the electricity filter shows PV without its sun.
- **Q-E. The window opening.** Today it overwrites the published air temperature (up to 7.96 K in 1,986 steps of
  the heat-pump year) and removes no heat. (a) Draw nothing for it now (it moves no energy in the model) and file a
  bug bead to model airing as an extra ventilation conductance inside the solve, so the heat leaves through
  `VentilationLossInWatt`; that fix moves summer results. (b) Book the heat the lower air temperature implies as a
  ventilation loss without changing the solver: the building would not close (refused by D3). (c) Leave it as it is
  and document that airing only shapes the reported temperature. Recommendation: (a).
- **Q-F. Where the tanks' standby losses go.** The model sends them nowhere; they leave the system. (a) Draw them to
  `outdoors`, as modelled (861 kWh a year in the heat-pump twin, 437 kWh in the gas twin).
  (b) Add them to the building's internal gains when the tank stands in the heated zone, a model change with its
  own bead. Recommendation: (a) now; (b) only as its own decision.
- **Q-G. The carrier of heat inside the zone.** (a) A new carrier `ROOM_HEAT` for internal gains, the building's
  losses and its thermal mass, so the space-heating filter shows the heating chain up to the building and nothing
  else. (b) Reuse `SPACE_HEATING_HEAT` for all of it: no new carrier, but the space-heating filter then also shows
  body heat and appliance gains, and the building's losses, which the heating system covers only in part.
  Recommendation: (a).

## 9. Pull requests

One PR per device group, stacked (AGENTS.md); each declares its group, runs the balance on the recorded twins the
group touches (the week of `test_energy_balance_generators.py` in CI, the full year at 900 s in the PR description),
and either closes or lists each imbalance with a bug bead and a fix PR below it in the stack (D3). Each closes its
beads in its own branch.

| PR | Scope | Tests | Goldens | Beads closed |
|---|---|---|---|---|
| 0 | This spec, the note on hisim-energy-sankeys-9uoo.18, bead hisim-energy-sankeys-9uoo.19 | – | none | – |
| 1 | The graph model of §4: exempt controllers are never peers, the meters as nodes with their check and the unmetered ports (§4.3, §4.4, Q-J), carrier defaults, the boundary, loss and storage nodes, `ROOM_HEAT`, the new environment nodes, links coloured by carrier, `overall` as the connected diagram, per-carrier views as filters, report schema 2. Re-points the built generators' fuel and electricity ports (§5.2). | `base`: `tests/test_energy_balance.py` gains graph tests on hand-built port sets (a device the meter reads links to it, also through an exempt controller; grid import and export are the meter's outputs; a meter publishing other than it reads has an `unaccounted` flow and fails, or is reported while a component is undeclared; an unmetered bought-carrier port is reported, then fails; exempt peer skipped, unlinked reported). `extendedbase2`: `tests/test_energy_balance_generators.py`'s expected links change (`natural gas supply` → `GasMeter` → boiler instead of `undeclared: GasMeter`, `ElectricityMeter` instead of the energy manager). | none | hisim-energy-sankeys-9uoo.18 (as decided by D1, D2, D4, Q-J); hisim-energy-sankeys-9uoo.1, .2, .4, .9 confirmed built (below) |
| 2 | Electricity devices: PV, battery, occupancy (§5.3), linked to the electricity meter through the energy manager; the electricity meter check (the electricity part of hisim-energy-sankeys-9uoo.8). | `extendedbase2`, new `tests/test_energy_balance_electricity.py`: the heat-pump and gas twins, second January week at 900 s: PV, battery and occupancy close, the electricity meter publishes what it reads in every step. | none (new outputs only) | – (hisim-energy-sankeys-9uoo.5 closes with PR 5b) |
| 3 | Water storages and HDS as `HydronicPort` circuits, the generators' circuit ends with them (§5.1) = hydronic stage E. Needs hisim-pnw7 (B3) merged for the district-heating twin. | `extendedbase2`, `tests/test_energy_balance_storages.py`: buffer, tank, HDS and every circuit close per step in the gas, heat-pump, solar and district-heating twins; `tests/test_energy_port_hydronic.py` gains the pairing rules (one reader, both ends). | none | hisim-energy-sankeys-9uoo.3, hisim-hydronics-fxix.6 |
| 4a | Fix B1: the building carries `θ_m,next` as its mass state. | `base`: the step's mass equation and the carried state agree (`C_m Δθ` of the state equals the flows' net). | re-bless all household twins; KPI deltas; renovisorissues heads-up | B1's bug bead |
| 4 | The building's ports (§5.4), including B2 as a declared loss. | `extendedbase2`, `tests/test_energy_balance_building.py`: the building closes per step in the heat-pump and gas twins, a January and a July week (heating, solar gains, window opening). | none | hisim-energy-sankeys-9uoo.6 |
| 5a | Other heat and cooling devices: `GenericHeatPump`, `AirConditioner`, `SimpleAirConditioner`, `IdealizedElectricHeater`, `SimpleHeatSource`. | `extendedbase2`: `basic_household`, `air_conditioned_house`, `simple_air_conditioner_household_building_sizer` close. | none | – (hisim-i7yq's view needs hisim-energy-sankeys-9uoo.17 too) |
| 5b | Car and car battery; `L1Controller` exempt. | `extendedbase2`: `household_heatpump_car_building_sizer` closes, the car's charging is read by the electricity meter. | none | hisim-energy-sankeys-9uoo.5 |
| 5c | Hydrogen and CHP: electrolyzers, hydrogen storage, fuel cell, both CHPs, transformer. | `extendedbase2`: `dynamic_components`, `electrolyzer_with_renewables` close. | none | – (hisim-svh4's view follows) |
| 5d | The exemptions (Q-B), the example template's port, the `base` test that every component is declared or exempt. | `base`: walk every component class. | none | hisim-energy-sankeys-9uoo.7 |
| 6 | System balance and acceptance: undeclared physics and unlinked ports fail the run, so every meter check and the unmetered ports are enforced (§4.4), the acceptance test. | `extendedbase2`, `tests/test_complete_energy_sankeys.py`: heat-pump and gas twin, a week: no `undeclared` node, `unaccounted` within tolerance, every meter publishes what it reads, no unmetered port. The full year of every golden setup runs in `golden-year.yml` with the check on. | none | hisim-energy-sankeys-9uoo.8, hisim-energy-sankeys-9uoo.10, hisim-energy-sankeys-9uoo.11, hisim-mzft (after the owner has reviewed one twin per heating system) |

Order: PR 1 first, because every later PR's links resolve through it. PR 2 before PR 4, because the building's
internal gains come from the occupancy. PR 3 before PR 4, because the building's heat comes from the HDS. The
special views (hisim-svh4 hydrogen, hisim-cw22 car, hisim-i7yq cooling) are filters of the connected graph after
PRs 5a to 5c. The arrow renderer (hisim-energy-sankeys-9uoo.19, D4) comes after PR 6.

**Foundation beads already built** (checked on 4ae6eb82):

- hisim-energy-sankeys-9uoo.1 (the contract): `hisim/energy_port.py` (`EnergyPort` with role, carrier and peer; `HydronicPort`),
  `lt.EnergyRole` and `lt.EnergyBalanceCarrier` with every carrier the bead names plus diesel and cooling,
  `Component.add_output(energy_port=...)` with unit validation; #870.
- hisim-energy-sankeys-9uoo.2 (the check): `hisim/postprocessing/energy_balance/check.py` computes the residual per component and
  step with the owner's tolerance, runs in every simulation (`PostProcessor.check_energy_balance`), lists every
  failing component and link in one error rather than stopping at the first, and writes `balance_report.json`
  (`export.py`); #870.
- hisim-energy-sankeys-9uoo.4 (the generators): boiler, heat pump, district heating, electric heating and solar thermal declare
  their ports and close, `tests/test_energy_balance_generators.py`; #871. PR 1 re-points their fuel and
  electricity peers (D1), which is new work, not a reopening.
- hisim-energy-sankeys-9uoo.9 (the Sankeys): `sankey.py` writes one diagram per carrier plus `overall`, the red `unaccounted` and
  grey `undeclared` nodes, under `PostProcessingOptions.EXPORT_ENERGY_BALANCE`; #870, numbers on the nodes since
  #1019. D2 replaces the per-carrier diagrams by the connected graph in PR 1, under hisim-energy-sankeys-9uoo.18.

hisim-energy-sankeys-9uoo.11 (CI fails on an imbalance) is met by construction once everything declares: the check fails the run,
and `golden-year.yml` runs every golden setup for a year; it closes with PR 6.

## 10. Beads

Epic hisim-energy-sankeys-9uoo, acceptance hisim-mzft.

| Bead | Content | PR |
|---|---|---|
| hisim-energy-sankeys-9uoo.18 (P1) | D1, D2 recorded; colours by carrier, electricity from its physical source | 1 |
| hisim-energy-sankeys-9uoo.19 (P3) | The arrow-style renderer (D4) | after 6 |
| hisim-energy-sankeys-9uoo.3 (P1), hisim-hydronics-fxix.6 | Storages and HDS, hydronic stage E | 3 |
| hisim-energy-sankeys-9uoo.5 (P1) | Electricity devices | 2, 5b |
| hisim-energy-sankeys-9uoo.6 (P1) | The building | 4 (4a first) |
| hisim-energy-sankeys-9uoo.7 (P1) | The rest, exemptions | 5a to 5d |
| hisim-energy-sankeys-9uoo.8 (P1) | System balance per carrier | 2 (electricity), 6 |
| hisim-energy-sankeys-9uoo.10, .11 (P1) | Sweep, CI | 6 |
| hisim-energy-sankeys-9uoo.12 (P1), hisim-pnw7 | District heating without buffer (B3) | before 3 |
| hisim-energy-sankeys-9uoo.17 (P1) | Cooling into a buffer | hydronic stage F, before hisim-i7yq |
| hisim-mzft (P1) | Acceptance | 6 |
| hisim-meter-bought-carriers-lyfl (P2) | Every setup that buys a carrier meters it: `gas_boiler_household`, `dynamic_components`, `simple_air_conditioner_household_building_sizer` (and `.grouped`), `electrolyzer_with_renewables` (Q-J) | before 6 |
| to file with PR 4a | B1, the building's mass state | 4a |
| to file with the decision | Q-B, Q-D, Q-E, Q-F, Q-G (`owner-decision`) | – |
