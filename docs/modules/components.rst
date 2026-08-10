:orphan:

Components Package
==================

The ``hisim.components`` package supplies every simulation primitive used by
ETHOS.HiSim. Each component represents a discrete element of a building
energy system — from weather and building thermal dynamics to storage
devices, generation units, meters, and controllers.

Working Principle
-----------------

All components inherit from :py:class:`~hisim.component.Component` and
follow a uniform interface:

* **Configuration** — a ``ConfigBase``-derived dataclass holds all
  parameters required to instantiate the component (name, energy carrier,
  sizing, economic data, etc.).
* **Inputs / Outputs** — components declare their data requirements and
  results as :py:class:`~hisim.component.ComponentInput` and
  :py:class:`~hisim.component.ComponentOutput` instances, each carrying a
  :py:class:`~hisim.loadtypes.LoadTypes` and
  :py:class:`~hisim.loadtypes.Units` tag.
* **State** — a dedicated state class persists values across timesteps
  (e.g. stored energy, internal temperature).
* **Simulation step** — the abstract :py:meth:`~hisim.component.Component.i_simulate`
  method reads inputs from the shared
  :py:class:`~hisim.component.SingleTimeStepValues` container, performs
  calculations, and writes outputs back. The orchestrator calls this
  method iteratively until all connected components converge at each
  timestep.

Components are wired together in setup functions by connecting outputs to
inputs; the resulting graph is evaluated timestep by timestep by the
simulator core.

Component Categories
--------------------

The package is organised around the physical and logical roles of each
component.  Every module listed below lives under ``hisim/components/`` and
is documented via autodoc.

* **Weather & environment** —
  :py:mod:`~hisim.components.weather`,
  :py:mod:`~hisim.components.weather_data_import`

* **Building physics** —
  :py:mod:`~hisim.components.building`
  (RC-model thermal simulation from EPISCOPE/TABULA data)

* **Energy generation** —
  PV (:py:mod:`~hisim.components.generic_pv_system`),
  wind (:py:mod:`~hisim.components.generic_windturbine`),
  CHP (:py:mod:`~hisim.components.generic_chp`),
  solar thermal (:py:mod:`~hisim.components.solar_thermal_system`)

* **Heating** —
  heat pumps (:py:mod:`~hisim.components.generic_heat_pump`,
  :py:mod:`~hisim.components.generic_heat_pump_modular`,
  :py:mod:`~hisim.components.advanced_heat_pump_hplib`,
  :py:mod:`~hisim.components.more_advanced_heat_pump_hplib`),
  boilers (:py:mod:`~hisim.components.generic_boiler`),
  electric heating (:py:mod:`~hisim.components.generic_electric_heating`,
  :py:mod:`~hisim.components.idealized_electric_heater`,
  :py:mod:`~hisim.components.simple_heat_source`),
  district heating (:py:mod:`~hisim.components.generic_district_heating`),
  heat distribution (:py:mod:`~hisim.components.heat_distribution_system`)

* **Cooling** —
  :py:mod:`~hisim.components.air_conditioner`,
  :py:mod:`~hisim.components.simple_air_conditioner`

* **Power-to-gas / hydrogen** —
  electrolyzers (:py:mod:`~hisim.components.generic_electrolyzer`,
  :py:mod:`~hisim.components.generic_electrolyzer_h2`,
  :py:mod:`~hisim.components.generic_electrolyzer_and_h2_storage`),
  fuel cells (:py:mod:`~hisim.components.generic_fuel_cell`,
  :py:mod:`~hisim.components.advanced_fuel_cell`),
  hydrogen storage (:py:mod:`~hisim.components.generic_hydrogen_storage`)

* **Storage** —
  batteries (:py:mod:`~hisim.components.generic_battery`,
  :py:mod:`~hisim.components.advanced_battery_bslib`),
  thermal stores (:py:mod:`~hisim.components.generic_heat_water_storage`,
  :py:mod:`~hisim.components.simple_water_storage`,
  :py:mod:`~hisim.components.dual_circuit_system`)

* **Electric mobility** —
  :py:mod:`~hisim.components.generic_car`,
  :py:mod:`~hisim.components.generic_ev_charger`,
  :py:mod:`~hisim.components.advanced_ev_battery_bslib`

* **Meters** —
  electricity (:py:mod:`~hisim.components.electricity_meter`),
  gas (:py:mod:`~hisim.components.gas_meter`),
  fuel (:py:mod:`~hisim.components.fuel_meter`),
  heating (:py:mod:`~hisim.components.heating_meter`)

* **Controllers** —
  L1 device controllers
  (:py:mod:`~hisim.components.controller_l1_heatpump`,
  :py:mod:`~hisim.components.controller_l1_building_heating`,
  :py:mod:`~hisim.components.controller_l1_chp`,
  :py:mod:`~hisim.components.controller_l1_fuel_cell`,
  :py:mod:`~hisim.components.controller_l1_electrolyzer`,
  :py:mod:`~hisim.components.controller_l1_electrolyzer_h2`,
  :py:mod:`~hisim.components.controller_l1_generic_ev_charge`,
  :py:mod:`~hisim.components.controller_l1_generic_runtime`,
  :py:mod:`~hisim.components.controller_l1_rsoc`,
  :py:mod:`~hisim.components.controller_l1_example_controller`),
  L2 energy-management systems
  (:py:mod:`~hisim.components.controller_l2_energy_management_system`,
  :py:mod:`~hisim.components.controller_l2_ptx_energy_management_system`,
  :py:mod:`~hisim.components.controller_l2_rsoc_battery_system`,
  :py:mod:`~hisim.components.controller_l2_smart_controller`,
  :py:mod:`~hisim.components.controller_l2_xtp_fuel_cell_ems`),
  MPC (:py:mod:`~hisim.components.controller_mpc`),
  PID (:py:mod:`~hisim.components.controller_pid`),
  night setback (:py:mod:`~hisim.components.night_setback_controller`),
  advanced fuel cell (:py:mod:`~hisim.components.advanced_fuel_cell_controller`)

* **Signal sources & utilities** —
  price signals (:py:mod:`~hisim.components.generic_price_signal`),
  RSoC (:py:mod:`~hisim.components.generic_rsoc`),
  smart device (:py:mod:`~hisim.components.generic_smart_device`),
  CSV loader (:py:mod:`~hisim.components.csvloader`),
  sum-builder (:py:mod:`~hisim.components.sumbuilder`),
  transformer/rectifier (:py:mod:`~hisim.components.transformer_rectifier`),
  random numbers (:py:mod:`~hisim.components.random_numbers`),
  UTSP load-profile connector
  (:py:mod:`~hisim.components.loadprofilegenerator_utsp_connector`)

* **Configuration & templates** —
  :py:mod:`~hisim.components.configuration`,
  :py:mod:`~hisim.components.example_template`,
  :py:mod:`~hisim.components.example_component`,
  :py:mod:`~hisim.components.example_storage`,
  :py:mod:`~hisim.components.example_transformer`

For the full API reference, see the :doc:`../components` autodoc page.
