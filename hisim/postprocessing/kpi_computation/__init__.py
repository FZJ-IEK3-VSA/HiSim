"""Key performance indicator (KPI) computation for HiSim postprocessing.

This sub-package turns the time-series outputs collected by a finished HiSim
simulation into scalar key performance indicators (KPIs) that summarise the
energy, cost, and emission performance of each building (and, where several
buildings are simulated together, of the surrounding district).

KPIs
----

The following indicator families are produced:

* **Electricity** -- total consumption, total production (split into PV,
  wind turbine, and generic building production), self-consumption, grid
  injection, and the electricity drawn from / fed into the grid.
* **Self-consumption and self-sufficiency rates** -- the share of on-site
  generation consumed locally and the share of demand covered by on-site
  generation, including the variants defined by the HTW Berlin methodology
  for PV-battery systems.
* **Ratios** -- production-to-consumption ratios for total, PV, wind
  turbine, and building production versus total consumption.
* **Component-specific KPIs** -- indicators reported by individual
  components (heat pumps, batteries, boilers, meters, ...), grouped under
  the tags defined in
  :class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiTagEnumClass`.
* **Costs and emissions** -- CAPEX/OPEX and emission figures aggregated per
  building and per district.

KPI definitions and units
-------------------------

Every computed indicator is stored as a
:class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiEntry` carrying
an explicit ``unit`` field. The KPIs, grouped by their physical domain, are:

* **Energy quantities** (unit ``kWh``), obtained by integrating the
  corresponding power time series (see *Calculation methodology* below):

  - ``Total electricity consumption`` -- sum of all EMS-controlled and
    uncontrolled electricity consumption, plus battery round-trip losses.
  - ``Total electricity production`` -- sum of all electricity production.
  - ``PV production`` / ``Windturbine production`` / ``Building production``
    -- production split by source.
  - ``Battery charging energy`` / ``Battery discharging energy`` / ``Battery
    losses`` -- battery charge, discharge, and the difference (losses).
  - ``Self-consumption of electricity`` -- on-site generation consumed
    locally.
  - ``Grid injection of electricity`` -- surplus generation fed into the
    grid.
  - ``Total energy from grid`` / ``Total energy to grid`` -- electricity
    exchange read from an
    :class:`~hisim.components.electricity_meter.ElectricityMeter`.
  - ``Purchased energy consumption for simulated period`` -- gas + grid
    electricity + other fuels drawn from external grids.

* **Dimensionless rates and ratios** (unit ``%``):

  - ``Self-consumption rate of electricity`` -- self-consumption divided by
    total production.
  - ``Self-sufficiency rate of electricity`` -- self-consumption divided by
    total consumption.
  - ``Self-consumption rate according to solar htw berlin`` -- (production -
    grid feed-in) / production, after the HTW Berlin definition.
  - ``Self-sufficiency according to solar htw berlin`` -- 100 % minus the
    relative grid demand.
  - ``Relative electricity demand from grid`` -- grid draw divided by total
    consumption.
  - ``Ratio between ... production and total consumption`` -- production /
    consumption for total, PV, wind turbine, and building production.
  - ``Total energy self-sufficiency`` -- load-type-weighted self-sufficiency
    across electricity, gas, and other fuels.

* **Costs** (unit ``EUR``): ``Investment costs for equipment ...``,
  ``Maintenance costs ...``, ``Energy grid costs for simulated period``,
  ``Total costs for simulated period``, and the ``Opex costs of ...``
  entries reported by the meters, each aggregated per simulated period and,
  for districts, summed across buildings.

* **Emissions** (unit ``kg CO2-eq``): ``CO2 footprint for equipment ...``,
  ``Total CO2 emissions for simulated period``, and the meter-reported
  ``CO2 footprint of ... consumption from grid`` entries, again aggregated
  per simulated period and per district.

Component-specific KPIs (e.g. heat-pump seasonal performance, boiler output,
meter totals) declare their own units in their
:class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiEntry` and are
collected verbatim through
:meth:`~hisim.postprocessing.kpi_computation.kpi_preparation.KpiPreparation.get_all_component_kpis`.

Calculation methodology
-----------------------

Energy figures are derived from power time series by integrating over the
simulation time step (see
:meth:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiHelperClass.compute_total_energy_from_power_timeseries`)::

    E [kWh] = sum(P [W]) * dt [s] / 3.6e6

where ``dt`` is ``simulation_parameters.seconds_per_timestep``. An empty
series yields ``0.0`` kWh. Component outputs are classified as production,
consumption, or storage charge/discharge through the ``InandOutputType``
postprocessing flags set on each :class:`~hisim.component.ComponentOutput`,
and the results are filtered per building before the KPIs are computed.

The following energy-balance and conservation assumptions underpin the
electricity KPIs (implemented in
:meth:`~hisim.postprocessing.kpi_computation.kpi_preparation.KpiPreparation.compute_self_consumption_injection_self_sufficiency`
and
:meth:`~hisim.postprocessing.kpi_computation.kpi_preparation.KpiPreparation.compute_battery_kpis`):

* Battery charge power is counted as consumption and battery discharge
  power as production, so storage is represented on both sides of the
  balance. To avoid double counting, self-consumption is evaluated on the
  *production side only* (battery discharge is included, battery charge is
  not added again).
* Battery losses are ``charging energy - discharging energy`` and are added
  to the total electricity consumption, because they represent energy
  dissipated by the battery independent of the charge/discharge flows.
* Per time step, self-consumption is ``min(production_with_battery,
  consumption_with_battery)`` and grid injection is the positive part of
  ``production_with_battery - consumption_with_battery``.
* ``Self-consumption rate = self-consumption / production`` and
  ``Self-sufficiency rate = self-consumption / consumption``. A
  self-sufficiency rate above 100 % raises a ``ValueError``, because more
  on-site consumption than total demand is physically inconsistent.

The self-consumption and self-sufficiency rates follow the definitions for
PV-battery systems in households published by HTW Berlin
(https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf).
Cost and emission figures are read from the ``investment_cost_co2_footprint``
and ``operational_costs_co2_footprint`` CSV files written by the CAPEX/OPEX
post-processing step and aggregated per simulated period (see
:meth:`~hisim.postprocessing.kpi_computation.kpi_preparation.KpiPreparation.read_opex_and_capex_costs_from_results`).

Modules
-------

* :mod:`hisim.postprocessing.kpi_computation.compute_kpis` exposes
  :class:`~hisim.postprocessing.kpi_computation.compute_kpis.KpiGenerator`,
  the entry point that orchestrates the computation and sorts the results
  for the report.
* :mod:`hisim.postprocessing.kpi_computation.kpi_preparation` provides
  :class:`~hisim.postprocessing.kpi_computation.kpi_preparation.KpiPreparation`,
  which filters the result data frame and implements the consumption,
  production, self-consumption, and self-sufficiency calculations.
* :mod:`hisim.postprocessing.kpi_computation.kpi_structure` defines the
  data structures
  (:class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiEntry`,
  :class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiTagEnumClass`)
  and the static helper functions in
  :class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiHelperClass`
  used to store and aggregate the KPIs.

Dependencies
------------

The computation reads all simulation results from a
:class:`~hisim.postprocessing.postprocessing_datatransfer.PostProcessingDataTransfer`
instance (the result data frame, the wrapped components, the full output
list, and the simulation parameters) and relies on
:class:`~hisim.component.ComponentOutput`,
:class:`~hisim.component_wrapper.ComponentWrapper`, and the
:class:`~hisim.loadtypes.InandOutputType` postprocessing flags to classify
outputs. Grid exchange and self-sufficiency figures additionally depend on
an :class:`~hisim.components.electricity_meter.ElectricityMeter` component
being present in the system setup.
"""
