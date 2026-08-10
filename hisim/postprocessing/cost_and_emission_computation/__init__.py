"""Cost and emission computation for HiSim post-processing.

This sub-package computes the capital expenditure (CAPEX), operational
expenditure (OPEX), and associated CO2 emissions for every component of a
finished HiSim simulation. The computed values feed the post-processing
report tables and the CSV exports written to the simulation result
directory.

CAPEX model
-----------

Capital expenditure describes the one-off investment in a component. For each
component a :class:`~hisim.component.CapexCostDataClass` is assembled by
:meth:`~hisim.postprocessing.cost_and_emission_computation.capex_computation.CapexComputationHelperFunctions.compute_capex_costs_and_emissions`
with the following inputs and assumptions:

* When all capex-related fields on the component ``config`` are ``None``, the
  per-unit investment cost and CO2 footprint are looked up from
  :class:`~hisim.components.configuration.EmissionFactorsAndCostsForDevicesConfig`
  for the simulation year and country, then scaled by the component's
  ``size_of_energy_system``. The ``unit`` of that size selects the matching
  per-unit factor (per kW, per kWh, per litre, per square metre, or a unitless
  ``ANY`` factor).
* When the capex fields on the component ``config`` are already populated, they
  are used directly, either as plain numbers or as
  :class:`~hisim.units.Quantity` objects.

The total values are then prorated to the simulated period by dividing by the
technical lifetime in years and multiplying by the simulated duration as a
fraction of a year (``duration.total_seconds() / seconds_per_year``). This is
straight-line (linear) proration -- no discount rate is applied and the values
are not converted to a net present value (NPV) or expressed as a levelised
cost of energy (LCOE). The returned data class therefore carries both the
full lifetime investment and the share attributable to the simulated period.

OPEX model
----------

Operational expenditure is computed per component from its simulation outputs.
:func:`~hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation.opex_calculation`
calls each component's ``get_cost_opex`` to obtain an
:class:`~hisim.component.OpexCostDataClass` and collects the total energy
consumption, the CO2 emissions of that consumption, the energy cost, and the
annual maintenance cost. The per-fuel CO2 emission factors and energy prices
underlying these consumptions are sourced per component from
:class:`~hisim.components.configuration.EmissionFactorsAndCostsForFuelsConfig`
for the simulation year and country (via its ``get_values_for_year`` lookup,
which raises :class:`KeyError` for year/country pairs that have no tabulated
factors). The components are grouped into an ``all_components`` total and a
``without_hp`` total that excludes heat pumps, while meters (which already
aggregate the consumptions of other components) are tracked separately to
avoid double counting. The results are written to
``operational_costs_co2_footprint.csv``.
:func:`~hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation.capex_calculation`
assembles the corresponding CAPEX table and writes it to
``investment_cost_co2_footprint.csv``.

Unit conventions
----------------

The following units are used consistently throughout the sub-package:

* Monetary values are given in euro (EUR); no currency conversion or
  inflation adjustment is performed.
* CO2 footprints and emissions are given in kilograms (kg).
* Energy consumption is given in kilowatt-hours (kWh).
* Component sizes are given in kilowatt (kW), kilowatt-hour (kWh), litre (L),
  or square metre (m2), or are unitless (``Units.ANY``).
* Technical lifetimes are given in years, and maintenance costs are given in
  EUR per year.
* Subsidies are expressed as a percentage of the investment cost.

All monetary and mass values written to the report tables and CSV exports are
rounded to two decimal places.

Emission factor sources, discounting, and temporal scope
--------------------------------------------------------

The techno-economic factors are tabulated per year and country and retrieved
through ``get_values_for_year`` lookups, so the valid simulation time horizons
are exactly the years tabulated for the requested country:

* CAPEX device factors (investment cost and CO2 footprint per unit, technical
  lifetime, maintenance share, and subsidy) come from
  :class:`~hisim.components.configuration.EmissionFactorsAndCostsForDevicesConfig`.
  An unsupported country or device raises :class:`KeyError`; a missing year
  falls back to the earliest tabulated year for that country and emits a
  warning.
* OPEX fuel factors (energy price and CO2 footprint per kWh or per litre for
  electricity, gas, oil, diesel, pellets, wood chips, district heating, and
  green hydrogen) come from
  :class:`~hisim.components.configuration.EmissionFactorsAndCostsForFuelsConfig`.
  An unsupported country or year raises :class:`KeyError`.

No discounting is applied at any stage: the CAPEX investment cost, CO2
footprint, and maintenance cost are prorated linearly (straight-line) over the
technical lifetime to the simulated duration, as described in the CAPEX model
above. OPEX energy consumption, cost, and emissions are accumulated directly
over the simulated period without annualisation or discounting.

Submodules
----------

* :mod:`~hisim.postprocessing.cost_and_emission_computation.capex_computation`
  provides
  :class:`~hisim.postprocessing.cost_and_emission_computation.capex_computation.CapexComputationHelperFunctions`
  for building and prorating CAPEX data classes.
* :mod:`~hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation`
  provides
  :func:`~hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation.opex_calculation`
  and
  :func:`~hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation.capex_calculation`,
  which loop over all components and emit the cost and emission CSV tables.
"""
