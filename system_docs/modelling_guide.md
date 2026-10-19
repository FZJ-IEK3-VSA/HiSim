# Modelling guide

How a HiSim component models physics correctly: energy is conserved at every port, signs are stated, a step
length changes nothing that a 60 s run would not show, a limit either clamps visibly or fails, and every
parameter has a source. It complements `component_design_principles.md`, which says who computes what and what
may cross a port. It applies to new components and to every component that is changed, and reviews check it.
Examples are from the hydronic coupling (`roadmap/hydronic_coupling_spec.md`, stages A to C: #890, #908, #909;
stage D in progress), the part-load rule (`roadmap/hydronic_part_load_spec.md`: #915, #917) and the solar
controller (#941).

## 1. Energy is conserved at every port

- **Sender and receiver book the same energy** (`component_design_principles.md`, part B, principle 4). A
  quantity that crosses a port is computed once, from the port quantities both ends see, by one function. A water
  circuit carries a mass flow `m` (kg/s), a supply temperature and a return temperature (°C); its heat flow is
  `P = m c (T_sup - T_ret)` in W (`hisim.hydronics.circuit_power_w`), and its heat over a step of `dt` seconds is
  `Q = m c (T_sup - T_ret) dt` (`circuit_heat_kwh`, in kWh), with `c = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K`.
  Neither end books a heat of its own making. Why: before stage B (#908) the hplib heat pump booked hplib's own thermal power, computed at a
  return rounded to 0.1 K and with 4200 J/(kg K), while the storage integrated the flow at 4180 J/(kg K); the two
  differed by up to 1.5 % per step. Now the heat pump books `m c (T_out - T_in)` of its own outputs, and
  `tests/test_heat_pump_flow_booking.py` checks it on every step.
- **A coupled store publishes its start-of-step temperature, and both ends book their flows on it** (owner,
  2026-10-15; built for the space-heating chain by hydronic stage D, hisim-fxix.5, in progress). A store that
  holds water (a buffer, a cylinder) publishes its temperature at the start of the step: a committed value that no
  pass of the step changes. The generator books its flow on it, its outlet the start temperature plus its lift;
  the distribution system books its own, its return the start temperature minus its drop. The store integrates the
  energy of these booked flows exactly over the step ("An exact step" below), so the heat each end books is the heat
  the store gains or gives. Above the part-load threshold (600 s) every end of a circuit of a start-of-step store
  runs only a fraction of the step (owner, 2026-10-15): the store publishes its end temperature and, per circuit, its
  end temperature without that circuit, and `hisim.part_load.StartOfStepPartLoadRule` sets each fraction exactly from
  them: a generator's so that the store ends at its controller's target (also when the store starts at the target but
  its draw would take it below), the distribution system's so that the store does not end below the room's
  start-of-step temperature. The space-heating buffer, its boilers and its heat pump do this since hydronic stage D;
  the heat pump's hot-water charge runs whole steps until its part load (#916). Why: a committed start value does not
  move while the step iterates, so the coupling itself needs no iteration between store and generator; the fractions
  of a store's circuit ends read each other's fractions of the pass before and settle together within a few passes.
  A start-of-step store refuses whole steps above 600 s: a run with longer steps and a part-load threshold at or above
  its step is refused when the store is built. Today the hot-water cylinder of stage C (#909) still returns the step
  mean of its temperature to its generators (`StepMeanWaterTemperatureToHeatGeneratorInCelsius`), while the
  controllers decide on its start-of-step temperature (part-load spec, decision 3; the solar controller reads
  `WaterTemperatureAtStartOfStepInCelsius` since #941). A new store follows the rule above.
- **Power outputs are step means, energy outputs step totals.** A power published for a step is its mean over the
  step, so power times step length is the step's energy (`hisim/energy_port.py` converts it so). A value at the
  start or end of the step is a sensor or a state, and its name says so (`WaterTemperatureAtEndOfStepInCelsius`).
- **Every component that moves energy declares its ports and closes its balance.** Each energy-carrying output
  declares an `EnergyPort` (role `IN`, `OUT`, `LOSS` or `STORED_CHANGE`, carrier, peer); a water circuit a
  `HydronicPort`. The check in `hisim/postprocessing/energy_balance/check.py` runs in every simulation and fails
  the run when a balance does not close: the run's residual above 0.1 % of the component's throughput, a step's
  above `max(1e-6 kWh, 0.1 %)` of the step's throughput, a transfer booked by only one of two declared components,
  or a port that is not finite (owner decision 6 of epic hisim-9uoo). A new or changed energy-moving component
  declares its ports and gets a test that runs it and reads the balance report, as
  `tests/test_energy_balance_generators.py` does for the generators. Today the generators declare ports (boiler,
  heat pump, district heating, electric heating, solar thermal); the storages and the distribution system are
  still undeclared, and the check reads `HydronicPort` from hydronic stage E on (hisim-fxix.6).
- **An exact step is preferred to a fine one.** Where a law has a closed-form solution over a step with constant
  inputs, use it: `MixedNode.step` solves `C dT/dt = Σ m_i c (T_i - T) - UA (T - T_amb)` exactly, so its heat
  balance closes to float precision at any step length, without sub-stepping.
- **Physical constants have one home.** Water's specific heat and density are `hydronics.Water`; a second literal
  (`0.992` kg/l in the occupancy connector's KPI) is a duplicate to remove (`coding_style.md` §1, DRY).

## 2. Sign conventions

Today HiSim mixes conventions:

| Quantity | Convention today | Where |
|---|---|---|
| Mass flow of a water circuit | always `>= 0`; the direction is the circuit's topology | `hisim/hydronics.py` |
| Heat of a water circuit | positive when the supply owner heats the receiver; negative for a cooling circuit (`T_sup < T_ret`) | `hydronics.circuit_power_w`, `HydronicPort` |
| Energy-port values | read with the sign of their role: positive `IN` enters, positive `OUT` and `LOSS` leave, positive `STORED_CHANGE` is stored; `EnergyPort.negated` turns round an output published the other way | `hisim/loadtypes.py` (`EnergyRole`), `hisim/energy_port.py` |
| Hot water drawn at the tap | positive (`ThermalPowerConsumptionDHW`, stated in its description since #909) | `hisim/components/simple_water_storage.py` |
| Grid balance of the energy manager | signed net, positive for a surplus to the grid, in an output whose description states no sign (`TotalElectricityToOrFromGrid`) | `hisim/components/controller_l2_energy_management_system.py` |
| Battery power | signed (`AcBatteryPowerUsed`), the description states no sign | `hisim/components/advanced_battery_bslib.py` |
| Building heat demand | signed, positive for heating and negative for cooling (`TheoreticalThermalBuildingDemand`), the description states no sign | `hisim/components/building/building.py` |

The rule for new code (and for an existing output when it is next touched, `component_design_principles.md`,
part B, principle 10):

- **A one-directional name carries a value `>= 0`** in the direction it names: `ThermalPowerDeliveredInWatt`,
  `ElectricityConsumptionInWatt`. Two directions are two outputs, or one signed output whose name names both
  (`...ToOrFromGrid`).
- **A signed output states its positive direction in its `output_description`**: "positive means electricity fed
  into the grid". A placeholder description ("here a description ... will follow") is not allowed in new code.
- **Within one port kind every component uses the same convention**: water circuits as in the table, energy ports
  by their role. `negated` is for existing outputs only; a new output is published in its role's direction.

## 3. 60 s is the reference

- **A 60 s run is the reference every coarser run is measured against.** A model must give the same result at
  every step length a run accepts (one that divides a day) up to a stated tolerance; a coarse-step adaptation lives inside the component and switches on
  only above `SimulationParameters.part_load_above_seconds` (600 s by default), so a 60 s run is unchanged
  (`component_design_principles.md`, part B, principle 6). Example: the boiler's part-load rule (#915) left every 60 s result and
  golden bit-identical, and changed the gas twin's hot-water gas at 900 s by -0.002 % against 60 s
  (`roadmap/hydronic_part_load_spec.md` §11).
- **A coarse-step model agrees with the 60 s run within 1 %** at 900 s and 3600 s, on the energy figures it is
  measured by (heat delivered, fuel or electricity, the bill), unless its spec states and justifies another
  tolerance (owner, 2026-10-15). A test runs the same system at 60 s and at the coarse steps and asserts the
  tolerance. Example: `tests/test_dhw_chain_time_resolution.py` runs six twins for four weeks at 60, 900 and
  3600 s; the heat drawn at the tap agrees within 1 %, the generators' hot-water heat within 1 % at 900 s and
  within 1.5 % at 3600 s. The spec states and justifies the 1.5 % (`roadmap/hydronic_coupling_spec.md` §9.5,
  stage C, amended by the owner on 2026-10-16): at 3600 s a hotter tank loses more standby heat, because its
  controller decides on the tank's start-of-step temperature.
- **A change is measured, not assumed.** The PR description gives the change against the 60 s run of the same
  head at 900 s and 3600 s for the twins it affects (part-load spec §11, the tables of #941). A deviation beyond the
  tolerance is a bug or a decision for the owner, never a widened tolerance.
- **A switching decision does not flip between the passes of a step.** A controller may react to the values of the
  current pass: the passes exist so that components react to each other within the step, and the owner rejected a
  general rule that controllers decide only on start-of-step values (2026-10-10, bead hisim-4g9.38). But a decision
  at an edge that its own effect moves -- a pump whose heat lowers the flow it switches on -- gets a hysteresis held
  in the controller's saved state, or holds under `force_convergence`. Why: the solar controller of #917 had no
  consistent pump state at such an edge, and the step was forced (hisim-fxix.21: 156 forced steps a year at 900 s
  in the heat pump + solar twin). Since #941 it switches on at 7 K and off at 3 K of the collector above the tank,
  whether the pump ran is saved state, and it decides on the tank's start-of-step temperature (owner, 2026-10-11,
  for this controller; `roadmap/hydronic_coupling_spec.md` §5.3).

## 4. Clamp or fail at a limit

The rule, generalised from RenoVisor's "fail loudly, except for what is written down" (`hisim/renovisor/`, CLAUDE.md):

- **Physics clamps at a limit the real device has, and shows it.** A device that hits its own physical or safety
  limit -- a maximum supply temperature, a largest flow, a state of charge of 0 or 1, a capacity -- stops at the
  limit, as the real device does, without an error (`component_design_principles.md` A: physics enforces its own
  limits). It books only what it delivered, and the shortfall is visible in an output when a consumer misses
  energy. Example: a generator's hot-water supply is throttled at the lower of its maximum and its controller's set
  temperature (`hydronics.capped_hot_water_supply_temperature_c`); the hot-water tank publishes the heat the tap
  did not get as `ThermalEnergyUnmetDHWInWattHour`, and its KPI reports it.
- **Everything else fails.** Invalid configuration, input outside the model's domain, a non-finite value, a
  contradiction between settings, an iteration without a solution: raise a specific exception that names the
  value and what was expected (`coding_style.md` §2, rule 6). Examples: `hisim/hydronics.py` refuses a negative
  mass flow, a non-positive heat capacity or a NaN and clips nothing; a hot-water set temperature above the
  generator's maximum raises `SetTemperatureAboveMaximumError`, because a charge towards it would never end; the
  energy balance check fails the run.
- **A known simplification is written down, then accepted.** A model limit the code accepts on purpose -- a
  configured minimum run time shorter than one step, a feature not modelled -- is stated in the component's
  docstring and logged once with `log.warning` when it takes effect; a missing feature also has a bead.
  Example: the boiler controller warns that a minimum time shorter than one step has no effect at that resolution
  (`GenericBoilerController`, `hisim/components/generic_boiler.py`). RenoVisor's version of the rule is
  `hisim/renovisor/not_implemented_yet.yaml`, kept honest by `tests/renovisor/test_capabilities.py`.
- **No silent clamp of an input or a result to make the numbers plausible**: a temperature forced into a range, a
  negative power set to 0, a fallback value when a lookup fails. A value outside its plausible range means that the
  model or its input is wrong; find out which and fix it.

## 5. Every parameter has a source

- **Every default parameter cites its source** where it is defined, in new and existing code alike (owner,
  2026-10-15): a preset value, a configuration field's default, a class constant with a physical or technical
  meaning, a hard-coded set point. The source is a standard (DIN EN, VDI, ISO with its
  number), a datasheet (manufacturer, model, document), a measurement (where, when), the literature, or an owner
  decision with its date. Write it in the `#:` comment above the constant or in the preset's docstring.
- **Example**, `SolarThermalSystemController.SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN` in
  `hisim/components/solar_thermal_system.py`: "Differential solar controllers switch on at a collector-to-tank
  difference of a few kelvin, adjustable from about 2 to 16 K, with factory settings of about 6 K (RESOL DeltaSol A
  and DeltaSol ES manuals, https://www.heizungsdiscount24.de/pdf/Resol-DeltaSol-A-Anleitung.pdf). The owner chose
  7 K and 3 K on 2026-10-14."
- **An estimate says it is one**, with what it rests on ("estimated from ..."). The existing values
  without a source are listed per component family in beads hisim-h2ib.11 to hisim-h2ib.25; until they have one,
  they are not copied into new code.
- **A parameter the system decides is sized, not pinned**: a power from the building's heating load is a
  `sized_field(rule=...)` (CLAUDE.md, "Adding a new component"), and a copy of another component's value is sized
  from that component's fact, never configured on its own (the heating threshold lives in the heating-curve L2
  component, and every user reads it there, hisim-4g9.36).

## 6. State and initial values

- **The initial state comes from the configuration**, and every output has a defined value before its first
  evaluation. Nothing treats 0 as "not published yet". The simulator starts each step from the values the previous
  step converged to (#914), so no step-0 guard is needed.
- **`i_simulate` changes no configuration and no derived constant** (`coding_style.md` §2, rule 4), and a cache key
  holds everything the cached result depends on. Why: the heat pump sets its minimum thermal power to 0 in hot-water
  mode and never restores it, and the hplib cache key does not contain it, so results depend on when the first
  hot-water charge happens (bead hisim-4g9.26).
