# Component design principles

What every HiSim component follows, so that each one can be improved on its own -- a stratified tank, a dynamic
heat pump, a new controller -- without breaking the others. New components follow these rules, reviews check
them, and a component that breaks one gets a bead. Agreed with the owner on 2026-10-10.

## A. Component kinds

Every component is exactly one of four kinds; controllers come in two levels. A component class states its kind
by inheriting the one matching base class from `hisim/component.py`: `DataSourceComponent`, `PhysicsComponent`,
`L1ControllerComponent`, `L2ControllerComponent` or `MeterComponent` (for example
`class ElectricityMeter(DynamicComponent, MeterComponent)`). The class is the declaration; the simulator refuses a
component whose class inherits none or more than one.

| Kind | Examples | Reads | Sends | Decides |
|---|---|---|---|---|
| Data source | weather, CSV loader, UTSP/LPG connector | nothing from other components | data, to anyone | nothing |
| Physics | heat pump, boiler, storage tank, building, heat distribution, PV, battery | data sources, port quantities of the physics it is connected to, commands of its L1 controller | port quantities to the physics it is connected to; sensor values | nothing; it only enforces its own physical and safety limits, including minimum run and idle times (device protection) |
| L1 controller | heat pump controller, boiler controller, heat distribution controller | data sources, sensor values of physics, signals of L2 controllers | commands to exactly one device | on/off, mode, set point and run fraction of its device |
| L2 controller | energy management system, PtX energy management, heating curve | anything except meters | incentive signals to L1 controllers, never to a device | coordination between devices: priorities, prices, set-point shifts |
| Meter | electricity, gas, heat and fuel meters | port quantities of physics | reports for KPIs and post-processing; nothing in the simulation reads a meter, except the listed exceptions of bead hisim-repo-cleanup-o6a.22 | nothing; it never acts on what it reads |

- A data source does not react to the simulation: its outputs for a step are the same in every iteration.
- A command to a device comes only from an L1 controller, and every device has exactly one L1 controller, which
  decides between all its services (a heat pump's space heating and hot water are modes of one controller). An
  L2 controller only changes what an L1 controller aims for; how the device responds within its limits is the
  L1 controller's decision.
- A set point several L1 controllers share, such as the heating curve's supply temperature, comes from an L2
  controller, never from one L1 controller to another.
- Minimum run and idle times protect the device, so physics enforces them, like a maximum temperature. A
  controller may hold stricter times of its own.
- Nothing in the simulation reads a meter, except the listed exceptions of bead hisim-repo-cleanup-o6a.22 (for example
  `GenericHeatPumpController` reading `ElectricityMeter.ElectricityAvailable`, hisim-gpim). A controller that needs
  a balance, such as the energy manager's grid balance, sums the physics ports itself; the meter sums them
  independently for the bill.
- The simulator evaluates a pass kind by kind: data sources, L2 controllers, L1 controllers, physics, meters
  (`Simulator.EVALUATION_ORDER_OF_KINDS`). Within one kind the components keep the order they were added in, except
  that an L2 controller goes after the L2 controllers it reads, and every device an L1 controller commands goes
  before the other physics that controller reads (`hisim/evaluation_order.py`). Components that require one another
  first in a cycle are placed as one block, in the order added, where the first of them would go, and the simulator
  warns of the cycle; so an ordering rule is broken only between the members of a cycle. An L2 signal read by
  another L2 controller, such as the energy manager's raise the heating curve applies, thus reaches the reader in
  the same pass; a command reaches its device in the same pass, and the device's answer reaches the controller in the
  next pass together with the answer of the store it charges.

## B. Principles

1. **A component is a black box.** Others see it only through its wired inputs and outputs (and, at
   configuration time, through the sizing contributions it declares). No component imports another's model to
   compute with, reads its config or state, or uses the SimRepository to bypass the wiring.
2. **Interfaces carry port quantities, not model internals.** Allowed: mass flows, temperatures at a defined
   port or sensor, heat and power, on/off and mode signals, set points and incentive signals. Not allowed: model
   coefficients of another component (slopes, UA, heat capacity, time constants), linearisations, "what if"
   quantities that assume a model form. Test: would the output still mean the same with a more detailed model
   behind it, for example a stratified tank with ten layers?
3. **Each kind keeps to its role (A).** Physics simulates and never decides. An L1 controller decides for its one
   device. An L2 controller coordinates only through signals to L1 controllers. A meter reads and never acts. A
   priority between devices is therefore either static configuration of the L1 controllers (for example
   different set points) or a signal of an L2 controller, never physics.
4. **Every quantity has one owner.** The set point belongs to its controller; everyone else reads it from the
   controller's output instead of keeping a copy. At a port, sender and receiver book the same energy.
5. **Components are safe to iterate.** `i_simulate` is a function of the inputs and the saved state; the state
   advances only in `i_save_state`. A converged step does not depend on the order the components are evaluated in.
6. **The step length is the component's own business.** A component behaves the same at every step length. A
   coarse-step adaptation stays inside the component, switches on above `part_load_above_seconds`, and 60 s is
   the reference without any adaptation.
7. **Shared mechanisms stay generic.** Helpers such as `MixedNode.step` or the part-load iteration know nothing
   about a particular component.
8. **Units are in the names** (AGENTS.md, "Guidelines for coding").
9. **The SimRepository shares data that exists before the time loop.** Whole series for a precomputation (the
   weather's year for the PV system and the building's solar gains), profiles (the occupancy's driving profiles
   for the cars) and their digests for chained cache keys. The producer declares the keys as class constants and
   writes them in `i_prepare_simulation`; readers use those constants. A value that changes from step to step goes
   through a port, never through the repository.
10. **One sign convention per port kind.** Every port kind has one sign convention, stated in its output's
    description (for example: positive means energy into the component). A signed net value never goes into an
    output whose name means one direction ("production", "consumption"). Existing outputs are aligned when they
    are next touched.
11. **A component's KPIs come from its own outputs and its own configuration.** A KPI does not re-derive another
    component's physics or repeat its constants; it reads that component's outputs or KPIs instead.

## C. Subcomponents

A **subcomponent** is a plain class that models part of a device's behaviour: a burner's modulation band, a
boiler's circulation pump, a collector's efficiency curve, an emitter's design point. Agreed with the owner on
2026-10-23 (bead hisim-repo-cleanup-o6a.28).

- **Used inside a component.** A component builds its subcomponents from its own configuration and calls them in
  its own code, in `i_simulate` and its helpers. A subcomponent has no ports and no lifecycle, and the simulator
  never sees it, so it costs no convergence pass. It is tested alone, without building a simulation.
- **Hierarchy: assembly -> component -> subcomponent.** An assembly wires components, a component owns its
  subcomponents. A subcomponent never holds or calls a component.
- **Classes are shared, instances are not.** Any component may use a subcomponent class: one circulation pump
  class can serve the boiler, the heat pump, the solar collector and the heat distribution system. Each component
  builds its own instances; an instance is never reachable from two components. Two components that must agree on
  a value do so through the same configuration value, a sizing rule or a port, never through a shared object.
- **Marked.** Every subcomponent class inherits `hisim.subcomponents.base.Subcomponent`, a marker without behaviour.
  `tests/subcomponents/` checks that no subcomponent is also a component and that no instance is shared.
- **Location.** A subcomponent used by two or more components lives in the package `hisim/subcomponents/`. One
  used by a single component stays in that component's module and moves into the package when a second user
  appears. `hisim/hydronics.py` stays where it is: plain physics functions and constants that subcomponents use.
- **Imports.** A module in `hisim/subcomponents/` imports only the standard library, numpy, `hisim.config`,
  `hisim.hydronics`, `hisim.loadtypes`, `hisim.units` and other subcomponent modules; never `hisim.component`,
  `hisim.components`, the simulator, post-processing, `hisim.energy_system` or `hisim.renovisor`.
  `tests/test_subcomponent_import_lint.py` checks it.
- **Not a sizing law.** A sizing law (`hisim/config/laws.py`: `SizingLaw`, the `*_LAW` constants) says how a
  configuration value follows from the surrounding system before the run. It is a different concept and keeps its
  name.
- **Not every helper class is a subcomponent.** Per-step results (`BoilerFiring`, `CollectorOperatingPoint`,
  `HplibResult`), state records (`HotWaterPriorityTimer`, `HeatPumpRunTimers`), cache keys and input bundles
  (`PvSeriesInputs`), channel groups (`BoilerCircuitChannels`) and infrastructure (`PylpgWorkspace`) are not.

### Interface

- **Plain values in.** Arguments are plain values, keyword-only, with their units in their names
  (`heat_power_in_watt=...`). No `SingleTimeStepValues`, no `ComponentInput` or `ComponentOutput`, no simulation
  parameters object: the component reads its inputs and passes the values.
- **Built once, checked when built.** A component builds its subcomponents once, through classmethod factories, from
  values it got from its configuration or a sizing rule (`BoilerPump.sized_for(...)`). `__post_init__` refuses an
  invalid setting, so a bad subcomponent fails when its component is built, not in the first step.
- **Stateless.** A subcomponent holds no state across steps. State that changes from step to step stays in the
  component's state dataclass, and the component passes it in.

### Performance

A subcomponent is called in every pass of every step, so its result objects are part of the hot path. Measured on
2026-10-23 with Python 3.12, the cost per call of returning a three-field result on top of a pump-sized law of
179 ns: a tuple +28 ns, a slots dataclass +110 ns, a `NamedTuple` +152 ns, a frozen dataclass +271 ns, a frozen
dataclass built with keywords +361 ns (its `__init__` goes through `object.__setattr__`). Hence:

1. **Instances are frozen dataclasses.** They are built once, so their construction cost does not matter.
2. **Per-call results in the hot path are `NamedTuple`s**: immutable, named and typed, at about half the cost of a
   frozen dataclass. A single value is returned as a plain float, its unit in the method name
   (`lift_in_kelvin(...)`).
3. **No frozen dataclass is constructed per call in an `i_simulate` path** (`coding_style.md`, §2 rule 12).
