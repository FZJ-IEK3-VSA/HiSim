# Component design principles

What every HiSim component follows, so that each one can be improved on its own -- a stratified tank, a dynamic
heat pump, a new controller -- without breaking the others. New components follow these rules, reviews check
them, and a component that breaks one gets a bead. Agreed with the owner on 2026-10-10.

## A. Component kinds

Every component is exactly one of four kinds; controllers come in two levels.

| Kind | Examples | Reads | Sends | Decides |
|---|---|---|---|---|
| Data source | weather, CSV loader, UTSP/LPG connector | nothing from other components | data, to anyone | nothing |
| Physics | heat pump, boiler, storage tank, building, heat distribution, PV, battery | data sources, port quantities of the physics it is connected to, commands of its L1 controller | port quantities to the physics it is connected to; sensor values | nothing; it only enforces its own physical and safety limits, including minimum run and idle times (device protection) |
| L1 controller | heat pump controller, boiler controller, heat distribution controller | data sources, sensor values of physics, signals of L2 controllers | commands to exactly one device | on/off, mode, set point and run fraction of its device |
| L2 controller | energy management system, PtX energy management, heating curve | anything except meters | incentive signals to L1 controllers, never to a device | coordination between devices: priorities, prices, set-point shifts |
| Meter | electricity, gas, heat and fuel meters | port quantities of physics | reports for KPIs and post-processing; nothing in the simulation reads a meter | nothing; it never acts on what it reads |

- A data source does not react to the simulation: its outputs for a step are the same in every iteration.
- A command to a device comes only from an L1 controller, and every device has exactly one L1 controller, which
  decides between all its services (a heat pump's space heating and hot water are modes of one controller). An
  L2 controller only changes what an L1 controller aims for; how the device responds within its limits is the
  L1 controller's decision.
- A set point several L1 controllers share, such as the heating curve's supply temperature, comes from an L2
  controller, never from one L1 controller to another.
- Minimum run and idle times protect the device, so physics enforces them, like a maximum temperature. A
  controller may hold stricter times of its own.
- Nothing in the simulation reads a meter. A controller that needs a balance, such as the energy manager's grid
  balance, sums the physics ports itself; the meter sums them independently for the bill.

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
