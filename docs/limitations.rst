:orphan:

Modeling Limitations and Assumptions
=====================================

This document describes the modeling assumptions, known limitations, and valid
operating ranges of the HiSim building-energy-system simulator. It is intended to
help users understand what results can and cannot be used for, and to flag cases
where outputs should be interpreted with caution.

Temporal-Resolution Caveats
----------------------------

- **Fixed-timestep integration.** HiSim advances the simulation in fixed steps
  defined by :py:attr:`hisim.simulationparameters.SimulationParameters.seconds_per_timestep`.
  Sub-timestep transients (e.g., rapid load spikes that last only seconds) are
  not resolved. Power values within a timestep are treated as constant over that
  interval, and energy is computed as power × *seconds_per_timestep*.

- **Recommended resolutions.** Hourly timesteps (3600 s) are sufficient for
  annual energy assessments. Finer resolutions (e.g. 15 min or 1 min) are needed
  when evaluating components with fast dynamics such as battery charge/discharge
  cycling, electric-vehicle charging, or heat-pump minimum on/off times.

- **Timestep-dependent component behaviour.** Some components scale their
  calculations by *seconds_per_timestep* (for example,
  :py:attr:`hisim.components.electric_charging_station.ElectricChargingStation`
  scales state-of-charge increments by ``seconds_per_timestep / 3600.0``).
  Changing the timestep can therefore alter simulated component behaviour
  qualitatively, not just quantitatively.

Building Thermal Model
-----------------------

- **5R1C lumped-capacitance model.** The
  :py:class:`hisim.components.building.Building` component uses a
  five-resistor, one-capacitance (5R1C) electrical analogy based on
  EN ISO 13790. The entire building is represented as a single
  thermal zone with one internal temperature.

- **Single thermal zone.** No spatial temperature gradients within the building
  are modeled. Rooms, floors, and orientations share the same internal
  temperature. Multi-zone effects (e.g., solar gains on a south-facing room
  not affecting a north-facing room) are not captured.

- **TABULA/EPISCOPE reference typologies.** Default building parameters are
  drawn from the TABULA/EPISCOPE database of European building typologies.
  These represent averaged characteristics for specific construction eras,
  building types, and refurbishment levels — not individual buildings.

- **Linearised heat transfer.** The RC model assumes linear heat transfer
  through envelope elements. Radiative heat exchange, wind-dependent infiltration,
  and non-linear ventilation losses are not explicitly modeled.

Convergence and Iteration
--------------------------

- **Circular substitution.** When components have circular dependencies
  (e.g., building ↔ heat pump ↔ thermal storage), HiSim iterates within each
  timestep until outputs converge. The convergence criterion is an absolute
  tolerance of **0.0001** across all output values
  (see :py:meth:`hisim.component.SingleTimeStepValues.is_close_enough_to_previous`).

- **Anti-oscillation switch.** If convergence is not reached after **10
  iterations** within a single timestep, an anti-oscillation mechanism forces
  oscillating components to retain their last computed value. Results for that
  timestep are approximate, not exact.

- **Hard iteration limit.** If more than **100 iterations** are required
  within a timestep, the simulation raises a
  :py:exc:`ValueError`. This typically indicates a modeling error
  (e.g., conflicting controllers or extreme parameter values) rather than a
  legitimate numerical challenge.

Storage Models
---------------

- **Thermal energy storage.** Water-based thermal storage components
  (e.g., :py:class:`hisim.components.generic_heat_water_storage.HeatStorage`)
  use simplified well-mixed tank assumptions. Thermal stratification and
  internal heat losses are approximated, not resolved with computational
  fluid dynamics.

Heat Pump and HVAC Models
--------------------------

- **Generic heat pump.** The
  :py:class:`hisim.components.generic_heat_pump.GenericHeatPump` component
  uses tabulated COP values based on manufacturer performance curves. Part-load
  performance, defrost cycles, and auxiliary heating elements are approximated.

- **HPLib-based models.** The advanced heat-pump components
  (e.g., :py:class:`hisim.components.advanced_heat_pump_hplib.AdvancedHeatPumpHPLib`)
  delegate to external physics libraries. Their accuracy depends on the
  underlying library and its calibration data.

- **Minimum on/off times.** Generic heat pumps enforce configurable minimum
  operation and idle times, but start-up transients and ramp rates are not
  modeled explicitly.

Photovoltaic Generation
------------------------

- **pvlib backend.** The
  :py:class:`hisim.components.generic_pv_system.PVSystem` component uses the
  :py:mod:`pvlib` library with the CEC module and inverter database.
  Soiling, degradation over time, and shading from nearby structures (beyond
  static system losses) are not modeled unless explicitly configured.

- **Weather-driven irradiance.** PV output is driven entirely by the weather
  component's irradiance timeseries. Intra-timestep cloud transients are not
  resolved.

Weather Data
-------------

- **Pre-aggregated datasets.** Weather inputs come from pre-processed datasets
  (DWD Test Reference Years, NSRDB, ERA5). Individual station measurements
  are averaged to produce the gridded or point data used by simulations.
  Microclimate effects (urban heat island, local shading) are not captured.

- **Temporal interpolation.** When the weather data resolution differs from
  the simulation timestep, HiSim may interpolate or aggregate values. The
  method used depends on the data source and component.

Load Profiles
--------------

- **Standardized profiles.** Electrical load profiles are generated from
  standardized templates (e.g., UTSP load-profile data). They represent
  statistical averages for household types, not measured consumption for
  individual occupants.

- **Deterministic behaviour.** Once a load profile is selected, appliance
  usage is deterministic across simulations. Stochastic variation between
  identical households is not modeled.

Grid and System Interactions
------------------------------

- **No grid dynamics.** HiSim does not model grid frequency, voltage,
  impedance, or network topology. Components connected to the "grid" see an
  ideal infinite bus with no constraints beyond what is explicitly configured.

- **Single-node assumption.** All grid-connected components in a simulation
  share the same electrical node. Voltage drop, line losses, and protection
  device coordination are not modeled.

Economic and Lifecycle Assumptions
------------------------------------

- **Static cost inputs.** Capital and operational costs are configured as
  fixed values per component. Inflation, market dynamics, and learning-curve
  cost reductions over the analysis period are not modeled.

- **No discounting in core simulation.** Economic post-processing can apply
  discount rates for NPV calculations, but the core time-step simulation
  does not incorporate economic signals into physical behaviour unless
  explicitly wired through a controller.

Valid Result Ranges
---------------------

Results are most reliable when:

- The timestep resolution matches the fastest dynamics of interest.
- Components are operated within their manufacturer-rated ranges.
- Weather data represents the simulation location adequately.
- Circular dependencies converge without triggering the anti-oscillation
  mechanism (check iteration logs if available).

Results should be treated with caution when:

- Convergence required the anti-oscillation switch (10+ iterations per
  timestep). This indicates the simulation made approximations within that
  step.
- Extremely short timesteps (sub-minute) are used with components designed
  for hourly operation; numerical artefacts may appear.
- Load profiles do not represent the actual occupancy or behaviour of the
  simulated household.
- Multi-building or district-scale simulations use the single-building
  thermal model without adjustment; results may not capture inter-building
  thermal interactions.
