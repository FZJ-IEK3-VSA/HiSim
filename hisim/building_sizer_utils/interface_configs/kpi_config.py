"""Key performance indicator (KPI) configuration for the building sizer.

This module defines the :class:`KPIConfig` dataclass and the
:class:`KPIForRatingInOptimization` enum that exchange scalar key performance
indicators (KPIs) between HiSim post-processing and the Building Sizer
evolutionary optimizer. A *KPI* is a single scalar value that summarises one
aspect of a building's energy, cost, emission, or thermal-comfort performance
over a finished simulation. :class:`KPIConfig` is populated by
:mod:`hisim.postprocessing.postprocessing_main` from the KPI collection
computed in :mod:`hisim.postprocessing.kpi_computation` and the cost and
emission tables from
:mod:`hisim.postprocessing.cost_and_emission_computation`, then serialized to
JSON for the optimizer. :class:`KPIForRatingInOptimization` selects which KPI
the optimizer uses as its fitness objective.

Temporal aggregation
--------------------

Every KPI is aggregated over the **simulated period** -- the duration spanned
by the simulation, expressed as a fraction of a fixed 365-day year
(``seconds_per_year = 365 * 24 * 60 * 60``); leap years are deliberately
ignored. The values are therefore attributable to the simulated span and only
coincide with a true annual value when the simulation covers exactly one year.
The "annualized" prefix on several field names denotes this
per-simulated-period convention rather than a guaranteed calendar-year
quantity:

* **CAPEX** -- one-time investment costs and device (embodied) CO2 footprints
  are prorated by straight-line depreciation: each component value is divided
  by its technical lifetime in years and multiplied by the simulated duration
  as a fraction of a year. No discount rate is applied.
* **OPEX** -- energy costs, purchased energy consumption, and operational CO2
  emissions are accumulated directly over the simulated period without lifetime
  proration or discounting.
* **Maintenance costs** are prorated to the simulated period within the CAPEX
  computation; the exact rule is documented in
  :mod:`hisim.postprocessing.cost_and_emission_computation`.

Spatial normalization
---------------------

Fields whose name ends in ``_per_m2`` are normalized by the building's
**conditioned floor area** (the "Conditioned floor area" KPI, in m2).
Normalization is always per floor area, never per capita or per dwelling.
Fields without the ``_per_m2`` suffix carry an absolute quantity for the whole
building.

KPI formulas and units
----------------------

Energy quantities integrate the corresponding power time series as
``E_kWh = sum(P_W) * seconds_per_timestep / 3.6e6``. Let ``A`` be the
conditioned floor area in m2, ``T_h`` the heating set temperature (default
20.0 °C) and ``T_c`` the cooling set temperature (default 25.0 °C).

Self-sufficiency rates (unit ``%``, valid range 0 to 100; a value above 100
raises ``ValueError``):

* ``self_sufficiency_rate_electricity_in_percent`` -- electricity
  self-sufficiency after Weniger et al. (2017), HTW Berlin:
  ``100 - 100 * E_from_grid_kWh / E_consumption_kWh``.
* ``self_sufficiency_rate_all_energy_in_percent`` -- consumption-weighted
  self-sufficiency across electricity, gas and other fuels:
  ``(ss_elec * E_elec + ss_gas * E_gas) / (E_elec + E_gas + E_other)``, where
  ``ss_gas = 100 * (1 - E_gas_from_grid / E_gas)`` and other fuels score 0 %
  (all purchased).

Costs (unit ``€`` before normalization, ``€/m2`` after; non-negative):

* ``total_upfront_net_investment_costs_in_euro`` -- one-time net investment
  (investment cost minus subsidies), not prorated and not area-normalized.
* ``annualized_total_costs_in_euro_per_m2`` --
  ``(maintenance + investment_prorated + energy_costs) / A``.
* ``annualized_energy_costs_in_euro_per_m2`` --
  ``(electricity_costs + gas_costs + other_heating_fuel_costs) / A``.
* ``annualized_electricity_costs_in_euro_per_m2`` -- ``electricity_costs / A``.
* ``annualized_gas_costs_in_euro_per_m2`` -- ``gas_costs / A``.
* ``annualized_heat_costs_in_euro_per_m2`` -- ``other_heating_fuel_costs / A``.
* ``annualized_maintenance_costs_in_euro_per_m2`` -- ``maintenance / A``.
* ``annualized_investment_costs_in_euro_per_m2`` -- ``investment_prorated / A``.
* ``annualized_net_investment_costs_in_euro_per_m2`` --
  ``(investment - subsidies)_prorated / A``.

CO2 emissions (unit ``kg`` before normalization, ``kg/m2`` after;
non-negative):

* ``annualized_total_co2_emissions_in_kg_per_m2`` --
  ``(device_co2_prorated + electricity_co2 + gas_co2 + other_fuel_co2) / A``.
* ``annualized_co2_emissions_for_devices_in_kg_per_m2`` --
  ``device_co2_prorated / A``.
* ``annualized_energy_co2_emissions_in_kg_per_m2`` --
  ``(electricity_co2 + gas_co2 + other_fuel_co2) / A``.
* ``annualized_electricity_co2_emissions_in_kg_per_m2`` --
  ``electricity_co2 / A``.
* ``annualized_gas_co2_emissions_in_kg_per_m2`` -- ``gas_co2 / A``.
* ``annualized_heat_co2_emissions_in_kg_per_m2`` -- ``other_fuel_co2 / A``.

Energy (unit ``kWh`` before normalization, ``kWh/m2`` after; non-negative):

* ``annualized_purchased_energy_consumption_in_kwh_per_m2`` --
  ``(gas_from_grid + electricity_from_grid + other_fuel_consumption) / A``.
* ``annualized_electricity_to_grid_in_kwh_per_m2`` -- ``E_to_grid / A``.
* ``annualized_electricity_from_grid_in_kwh_per_m2`` -- ``E_from_grid / A``.

Thermal comfort (not area-normalized):

* ``minimum_indoor_temperature_in_celsius`` -- minimum indoor air temperature
  over the simulation (unit ``°C``).
* ``maximum_indoor_temperature_in_celsius`` -- maximum indoor air temperature
  over the simulation (unit ``°C``).
* ``deviation_from_min_indoor_temperature_in_celsius_hour`` -- integral of the
  temperature deficit below the heating set point:
  ``sum_t max(0, T_h - T_t) * seconds_per_timestep / 3600`` (unit ``°C*h``).
* ``deviation_from_max_indoor_temperature_in_celsius_hour`` -- integral of the
  temperature excess above the cooling set point:
  ``sum_t max(0, T_t - T_c) * seconds_per_timestep / 3600`` (unit ``°C*h``).
"""

import enum
from dataclasses import dataclass
from typing import ClassVar, cast

from dataclasses_json import dataclass_json


@enum.unique
class KPIForRatingInOptimization(str, enum.Enum):
    """Choose KPI that will be optimized with building sizer."""

    # Annualized KPis (values are divided by component lifetime)
    # Costs
    # ------------------------------------------------------------
    TOTAL_UPFRONT_NET_INVESTMENT_COSTS = "Total Upfront Net Investment Costs [€]"
    ANNUALIZED_TOTAL_COSTS = "Annualized Total Costs [€/m2]"
    ANNUALIZED_ENERGY_COSTS = "Annualized Energy Costs [€/m2]"
    ANNUALIZED_MAINTENANCE_COSTS = "Annualized Maintenance Costs [€/m2]"
    ANNUALIZED_INVESTMENT_COSTS = "Annualized Investment Costs [€/m2]"
    ANNUALIZED_NET_INVESTMENT_COSTS = "Annualized Net Investment Costs [€/m2]"
    # CO2
    # ------------------------------------------------------------
    ANNUALIZED_TOTAL_CO2_EMISSIONS = "Annualized Total CO2 Emissions [kg/m2]"
    ANNUALIZED_ENERGY_CO2_EMISSIONS = "Annualized Energy CO2 Emissions [kg/m2]"
    # Other
    # ------------------------------------------------------------
    SELFSUFFICIENCY_ELECTRICITY = "Self-Sufficiency Rate For Electricity [%]"
    SELFSUFFICIENCY_ALL_ENERGY = "Self-Sufficiency Rate All Energy [%]"
    ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION = "Annualized Energy Consumption [kWh/m2]"
    ANNUALIZED_ELECTRICITY_TO_GRID = "Annualized Electricity To Grid [kWh/m2]"
    ANNUALIZED_ELECTRICITY_FROM_GRID = "Annualized Electricity From Grid [kWh/m2]"
    MIN_BUILDING_INDOOR_TEMP = "Minimum Indoor Temperature [°C]"
    MAX_BUILDING_INDOOR_TEMP = "Maximum Indoor Temperature [°C]"
    DEVIATION_FROM_MIN_BUILDING_INDOOR_TEMP = "Deviation From Minimum Indoor Temperature [°C*h]"
    DEVIATION_FROM_MAX_BUILDING_INDOOR_TEMP = "Deviation From Maximum Indoor Temperature [°C*h]"


@dataclass_json
@dataclass
class KPIConfig:
    """Scalar KPI values from one finished simulation, exchanged with the optimizer.

    Each attribute holds a single key performance indicator (KPI) as a
    ``float``.  The physical unit is encoded in the attribute name's
    ``_in_<unit>`` suffix -- for example ``_in_percent``, ``_in_euro``,
    ``_in_euro_per_m2``, ``_in_kg_per_m2``, ``_in_kwh_per_m2``,
    ``_in_celsius``, or ``_in_celsius_hour``.  See the module docstring for
    the mathematical definition, SI unit, temporal aggregation, and spatial
    normalization of every KPI.  :meth:`get_kpi_for_rating` exposes the value
    of the KPI selected by :class:`KPIForRatingInOptimization` for use as the
    evolutionary optimizer's fitness objective.
    """

    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_electricity_in_percent: float
    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_all_energy_in_percent: float
    #: total upfront net investment costs, given in euro
    total_upfront_net_investment_costs_in_euro: float
    #: annual cost for investment and operation in the considered technology, given in euros per m2
    annualized_total_costs_in_euro_per_m2: float
    #: annual cost for energy from grid or from onsite consumption (electricty, gas, heat) given in euros per m2
    annualized_energy_costs_in_euro_per_m2: float
    #: annual cost for energy from grid (electricty) given in euros per m2
    annualized_electricity_costs_in_euro_per_m2: float
    #: annual cost for energy from grid (gas) given in euros per m2
    annualized_gas_costs_in_euro_per_m2: float
    #: annual cost for energy from grid or onsite consumption (heat) given in euros per m2
    annualized_heat_costs_in_euro_per_m2: float
    #: annual cost for maintenance in the considered technology, given in euros per m2
    annualized_maintenance_costs_in_euro_per_m2: float
    #: annual cost for investment in the considered technology, given in euros per m2
    annualized_investment_costs_in_euro_per_m2: float
    #: annual net cost for investment in the considered technology, given in euros per m2
    annualized_net_investment_costs_in_euro_per_m2: float
    #: annual total CO2 emissions in the considered technology, given in kg per m2
    annualized_total_co2_emissions_in_kg_per_m2: float
    #: annual C02 emmissions due to production of the considered technology, given in kg per m2
    annualized_co2_emissions_for_devices_in_kg_per_m2: float
    #: annual C02 emmissions due to operation of the considered technology, given in kg per m2
    annualized_energy_co2_emissions_in_kg_per_m2: float
    #: annual CO2 emissions from electricity consumption, given in kg per m2
    annualized_electricity_co2_emissions_in_kg_per_m2: float
    #: annual CO2 emissions from gas consumption, given in kg per m2
    annualized_gas_co2_emissions_in_kg_per_m2: float
    #: annual CO2 emissions from other heating fuels, given in kg per m2
    annualized_heat_co2_emissions_in_kg_per_m2: float
    #: annual energy consumption, given in kwh per m2
    annualized_purchased_energy_consumption_in_kwh_per_m2: float
    #: annual electricity to grid per m2
    annualized_electricity_to_grid_in_kwh_per_m2: float
    #: annual electricity from grid per m2
    annualized_electricity_from_grid_in_kwh_per_m2: float

    # KPIs for thermal comfort
    #: minimum indoor air temperature reached during the simulation, given in °C
    minimum_indoor_temperature_in_celsius: float
    #: maximum indoor air temperature reached during the simulation, given in °C
    maximum_indoor_temperature_in_celsius: float
    #: integral of the temperature deficit below the heating set point, given in °C*h
    deviation_from_min_indoor_temperature_in_celsius_hour: float
    #: integral of the temperature excess above the cooling set point, given in °C*h
    deviation_from_max_indoor_temperature_in_celsius_hour: float

    _KPI_TO_FIELD: ClassVar[dict[KPIForRatingInOptimization, str]] = {
        KPIForRatingInOptimization.SELFSUFFICIENCY_ELECTRICITY: "self_sufficiency_rate_electricity_in_percent",
        KPIForRatingInOptimization.SELFSUFFICIENCY_ALL_ENERGY: "self_sufficiency_rate_all_energy_in_percent",
        KPIForRatingInOptimization.TOTAL_UPFRONT_NET_INVESTMENT_COSTS: "total_upfront_net_investment_costs_in_euro",
        KPIForRatingInOptimization.ANNUALIZED_TOTAL_COSTS: "annualized_total_costs_in_euro_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_ENERGY_COSTS: "annualized_energy_costs_in_euro_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_MAINTENANCE_COSTS: "annualized_maintenance_costs_in_euro_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_INVESTMENT_COSTS: "annualized_investment_costs_in_euro_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_NET_INVESTMENT_COSTS: "annualized_net_investment_costs_in_euro_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_TOTAL_CO2_EMISSIONS: "annualized_total_co2_emissions_in_kg_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_ENERGY_CO2_EMISSIONS: "annualized_energy_co2_emissions_in_kg_per_m2",
        KPIForRatingInOptimization.ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION: "annualized_purchased_energy_consumption_in_kwh_per_m2",
    }

    def get_kpi_for_rating(self, chosen_kpi: KPIForRatingInOptimization) -> float:
        """Return the scalar KPI value used to rate a building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm
        of the building sizer.

        The returned ``float`` is a physical quantity whose unit is
        **polymorphic**: it is determined entirely by ``chosen_kpi`` and matches
        the unit recorded in the ``_in_<unit>`` suffix of the mapped dataclass
        field (e.g. ``_in_percent``, ``_in_euro``, ``_in_euro_per_m2``,
        ``_in_kg_per_m2`` or ``_in_kwh_per_m2``) as well as the unit embedded in
        the member's value string (in square brackets, e.g. ``"[€/m2]"``). No
        single ``_in_<unit>`` suffix on this accessor would be correct for every
        member, so callers must consult the mapped field's unit suffix — or the
        :class:`KPIForRatingInOptimization` value string — to interpret the
        magnitude of the returned value rather than assuming a fixed unit.

        Args:
            chosen_kpi: The KPI to read. Must be one of the members mapped in
                ``_KPI_TO_FIELD``; any other member raises ``ValueError``.

        Returns:
            float: The value of the dataclass field mapped to ``chosen_kpi``,
            in the unit encoded by that field's ``_in_<unit>`` suffix.

        Raises:
            ValueError: If ``chosen_kpi`` is not present in ``_KPI_TO_FIELD``.
        """
        field_name = self._KPI_TO_FIELD.get(chosen_kpi)
        if field_name is None:
            raise ValueError(f"Chosen KPI {chosen_kpi} not recognized.")
        return cast(float, getattr(self, field_name))
