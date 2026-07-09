"""Module for capex and emission computation."""

from typing import Optional
from hisim.component import CapexCostDataClass, ConfigBase
from hisim.components.configuration import EmissionFactorsAndCostsForDevicesConfig
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.loadtypes import ComponentType, Units
from hisim.units import Quantity


class CapexComputationHelperFunctions:
    """Helper functions for capex and emission computation."""

    @staticmethod
    def compute_capex_costs_and_emissions(
        simulation_parameters: SimulationParameters,
        component_type: ComponentType,
        unit: Units,
        size_of_energy_system: float,
        config: ConfigBase,  # these are component configs
        kpi_tag: Optional[KpiTagEnumClass] = None,
    ) -> CapexCostDataClass:
        """Compute CAPEX costs and CO2 emissions for a component over the simulated period.

        If all capex-related fields on ``config`` are ``None``, values are looked up from
        ``EmissionFactorsAndCostsForDevicesConfig`` for the simulation year and country and
        scaled by ``size_of_energy_system`` according to ``unit``. Otherwise the values
        already present on ``config`` (plain numbers or ``Quantity`` objects) are used directly.

        Args:
            simulation_parameters: Provides year, country, and simulated duration used
                for yearly-cost proration and device-factor lookup.
            component_type: The component type whose device cost/emission factors are
                retrieved when config values are absent.
            unit: Unit of ``size_of_energy_system`` (e.g. ``Units.KILOWATT``, ``Units.KWH``,
                ``Units.LITER``, ``Units.SQUARE_METER``, ``Units.ANY``); selects the
                per-unit cost/emission factor.
            size_of_energy_system: Capacity or size of the component in the given unit.
            config: Component config whose capex fields are either all ``None`` (triggering
                device-factor lookup) or all set to numeric/``Quantity`` values (used directly).
            kpi_tag: Optional KPI category tag attached to the returned data class.

        Returns:
            A ``CapexCostDataClass`` with total and per-simulated-period investment cost,
            CO2 footprint, maintenance cost, lifetime, and subsidy percentage (rounded to
            2 decimals).

        Raises:
            ValueError: If ``unit`` is not one of the supported units.
            ValueError: If the looked-up per-unit investment cost or CO2 footprint is ``None``.
            ValueError: If config capex values are present but neither all numeric nor all
                ``Quantity``.
        """
        list_of_config_capex_variables = [
            config.investment_costs_in_euro,
            config.device_co2_footprint_in_kg,
            config.lifetime_in_years,
            config.maintenance_costs_in_euro_per_year,
            config.subsidy_as_percentage_of_investment_costs,
        ]
        # if config capex values are None, use values from EmissionFactorsAndCostsForDevicesConfig
        if all(v is None for v in list_of_config_capex_variables):
            log.debug(
                f"Using EmissionFactorsAndCostsForDevicesConfig for {config.get_main_classname()} capex calculation."
            )
            # Get capex costs and CO2 footprint from EmissionFactorsAndCostsForDevicesConfig
            emissions_and_cost_factors_for_devices = EmissionFactorsAndCostsForDevicesConfig.get_values_for_year(
                year=simulation_parameters.year, device=component_type, country=simulation_parameters.country
            )
            # Depending on unit of the energy system size, get respective capex and emissions value
            if unit == Units.KILOWATT:
                # Size of energy system is in kW
                # Use the values in kW
                investment_costs_in_euro_per_size_unit = (
                    emissions_and_cost_factors_for_devices.investment_costs_in_euro_per_kw
                )
                co2_footprint_in_kg_per_size_unit = emissions_and_cost_factors_for_devices.co2_footprint_in_kg_per_kw

            elif unit == Units.KWH:
                # Size of energy system is in kWh
                # Use the values in kWh
                investment_costs_in_euro_per_size_unit = (
                    emissions_and_cost_factors_for_devices.investment_costs_in_euro_per_kwh
                )
                co2_footprint_in_kg_per_size_unit = emissions_and_cost_factors_for_devices.co2_footprint_in_kg_per_kwh

            elif unit == Units.LITER:
                # Size of energy system is in l
                # Use the values in l
                investment_costs_in_euro_per_size_unit = (
                    emissions_and_cost_factors_for_devices.investment_costs_in_euro_per_liter
                )
                co2_footprint_in_kg_per_size_unit = emissions_and_cost_factors_for_devices.co2_footprint_in_kg_per_liter
            elif unit == Units.SQUARE_METER:
                # Size of energy system is in m2
                # Use the values in m2
                investment_costs_in_euro_per_size_unit = (
                    emissions_and_cost_factors_for_devices.investment_costs_in_euro_per_m2
                )
                co2_footprint_in_kg_per_size_unit = emissions_and_cost_factors_for_devices.co2_footprint_in_kg_per_m2
            elif unit == Units.ANY:
                # Size of energy system has no unit
                # Use values with general unit
                investment_costs_in_euro_per_size_unit = emissions_and_cost_factors_for_devices.investment_costs_in_euro
                co2_footprint_in_kg_per_size_unit = emissions_and_cost_factors_for_devices.co2_footprint_in_kg
            else:
                raise ValueError(f"Unit {unit} of the energy system is not valid or not implemented yet.")

            # make safety checks explicit so they fire even under python -O
            if investment_costs_in_euro_per_size_unit is None:
                raise ValueError(
                    f"investment_costs_in_euro_per_size_unit for component {component_type} "
                    f"with unit {unit} must not be None."
                )
            if co2_footprint_in_kg_per_size_unit is None:
                raise ValueError(
                    f"co2_footprint_in_kg_per_size_unit for component {component_type} "
                    f"with unit {unit} must not be None."
                )

            # these values are independent of size unit of the energy system
            maintenance_costs_as_percentage_of_investment_per_year = (
                emissions_and_cost_factors_for_devices.maintenance_costs_as_percentage_of_investment_per_year
            )
            technical_lifetime_in_years = emissions_and_cost_factors_for_devices.technical_lifetime_in_years
            subsidy_as_percentage_of_investment_costs = (
                emissions_and_cost_factors_for_devices.subsidy_as_percentage_of_investment_costs
            )

            # Calculate total values
            capex_investment_cost_in_euro = investment_costs_in_euro_per_size_unit * size_of_energy_system
            device_co2_footprint_in_kg = co2_footprint_in_kg_per_size_unit * size_of_energy_system
            maintenance_costs_in_euro = (
                capex_investment_cost_in_euro * maintenance_costs_as_percentage_of_investment_per_year
            )
        else:
            log.debug(f"Using config values for {config.get_main_classname()} capex calculation.")
            # Use values from config
            if all(isinstance(v, (float, int)) for v in list_of_config_capex_variables):
                capex_investment_cost_in_euro = config.investment_costs_in_euro
                device_co2_footprint_in_kg = config.device_co2_footprint_in_kg
                technical_lifetime_in_years = config.lifetime_in_years
                maintenance_costs_in_euro = config.maintenance_costs_in_euro_per_year
                subsidy_as_percentage_of_investment_costs = config.subsidy_as_percentage_of_investment_costs

            elif all(isinstance(v, Quantity) for v in list_of_config_capex_variables):
                # if config values are Quantity objects, extract the values
                capex_investment_cost_in_euro = config.investment_costs_in_euro.value
                device_co2_footprint_in_kg = config.device_co2_footprint_in_kg.value
                technical_lifetime_in_years = config.lifetime_in_years.value
                maintenance_costs_in_euro = config.maintenance_costs_in_euro_per_year.value
                subsidy_as_percentage_of_investment_costs = config.subsidy_as_percentage_of_investment_costs.value
            else:
                raise ValueError(f"Config values have wrong type: {[type(v) for v in list_of_config_capex_variables]}")

        # Calculate values per simulated period
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (capex_investment_cost_in_euro / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (device_co2_footprint_in_kg / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        maintenance_costs_per_simulated_period_in_euro = (maintenance_costs_in_euro / technical_lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=round(capex_investment_cost_in_euro, 2),
            device_co2_footprint_in_kg=round(device_co2_footprint_in_kg, 2),
            lifetime_in_years=round(technical_lifetime_in_years, 2),
            capex_investment_cost_for_simulated_period_in_euro=round(capex_per_simulated_period, 2),
            device_co2_footprint_for_simulated_period_in_kg=round(device_co2_footprint_per_simulated_period, 2),
            maintenance_costs_in_euro=round(maintenance_costs_in_euro, 2),
            maintenance_cost_per_simulated_period_in_euro=round(maintenance_costs_per_simulated_period_in_euro, 2),
            subsidy_as_percentage_of_investment_costs=round(subsidy_as_percentage_of_investment_costs, 2),
            kpi_tag=kpi_tag,
        )
        return capex_cost_data_class

    @staticmethod
    def overwrite_config_values_with_new_capex_values(config: ConfigBase, capex_cost_data_class: CapexCostDataClass) -> ConfigBase:
        """Overwrite capex-related fields on ``config`` with values from a ``CapexCostDataClass``.

        Args:
            config: The component config to mutate in place.
            capex_cost_data_class: Source of the new investment cost, CO2 footprint,
                lifetime, maintenance cost, and subsidy percentage values.

        Returns:
            The same ``config`` object, with updated capex fields.
        """
        log.debug(f"Overwriting {config.get_main_classname()} config values with new capex values.")
        config.investment_costs_in_euro = capex_cost_data_class.capex_investment_cost_in_euro
        config.device_co2_footprint_in_kg = capex_cost_data_class.device_co2_footprint_in_kg
        config.lifetime_in_years = capex_cost_data_class.lifetime_in_years
        config.maintenance_costs_in_euro_per_year = capex_cost_data_class.maintenance_costs_in_euro
        config.subsidy_as_percentage_of_investment_costs = (
            capex_cost_data_class.subsidy_as_percentage_of_investment_costs
        )
        return config
