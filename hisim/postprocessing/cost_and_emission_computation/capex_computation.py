"""Module for capex and emission computation."""

from typing import Optional, Any
from hisim.component import CapexCostDataClass
from hisim.components.configuration import EmissionFactorsAndCostsForDevicesConfig
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.loadtypes import ComponentType, Units


class CapexComputationHelperFunctions:
    """Helper functions for capex and emission computation."""

    @staticmethod
    def compute_capex_costs_and_emissions(
        simulation_parameters: SimulationParameters,
        component_type: ComponentType,
        unit: Units,
        size_of_energy_system: float,
        config: Any,  # these are component configs
        kpi_tag: Optional[KpiTagEnumClass] = None,
    ) -> CapexCostDataClass:
        """Compute capex costs and emissions for a given component type."""
        # if config capex values are None, use values from EmissionFactorsAndCostsForDevicesConfig
        if all(
            v is None
            for v in [
                config.cost,
                config.co2_footprint,
                config.lifetime,
                config.maintenance_cost_as_percentage_of_investment,
            ]
        ):
            log.debug(
                f"Using EmissionFactorsAndCostsForDevicesConfig for {config.get_main_classname()} capex calculation."
            )
            # Get capex costs and CO2 footprint from EmissionFactorsAndCostsForDevicesConfig
            emissions_and_cost_factors_for_devices = EmissionFactorsAndCostsForDevicesConfig.get_values_for_year(
                year=simulation_parameters.year, unit=unit, device=component_type
            )
            # Dependend on unit of the energy system size, get respective capex and emissions value
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

            # make assertion for safety
            assert investment_costs_in_euro_per_size_unit is not None
            assert co2_footprint_in_kg_per_size_unit is not None

            # these values are independent of size unit of the energy system
            maintenance_costs_as_percentage_of_investment_per_year = (
                emissions_and_cost_factors_for_devices.maintenance_costs_as_percentage_of_investment_per_year
            )
            technical_lifetime_in_years = emissions_and_cost_factors_for_devices.technical_lifetime_in_years
            subsidy_as_percentage_of_investment_costs = (
                emissions_and_cost_factors_for_devices.subsidy_as_percentage_of_investment_costs
            )

            # Calculate total values
            capex_investment_cost_in_euro = investment_costs_in_euro_per_size_unit * size_of_energy_system - (
                investment_costs_in_euro_per_size_unit
                * size_of_energy_system
                * subsidy_as_percentage_of_investment_costs
            )
            device_co2_footprint_in_kg = co2_footprint_in_kg_per_size_unit * size_of_energy_system
            maintenance_costs_in_euro = (
                capex_investment_cost_in_euro * maintenance_costs_as_percentage_of_investment_per_year
            )
        else:
            log.debug(f"Using config values for {config.get_main_classname()} capex calculation.")
            # Use values from config
            if all(
                isinstance(v, float)
                for v in [
                    config.cost,
                    config.co2_footprint,
                    config.lifetime,
                    config.maintenance_cost_as_percentage_of_investment,
                ]
            ):
                capex_investment_cost_in_euro = config.cost
                device_co2_footprint_in_kg = config.co2_footprint
                technical_lifetime_in_years = config.lifetime
                maintenance_costs_in_euro = (
                    config.maintenance_cost_as_percentage_of_investment * capex_investment_cost_in_euro
                )
            else:
                # if config values are Quantity objects, extract the values
                capex_investment_cost_in_euro = config.cost.value
                device_co2_footprint_in_kg = config.co2_footprint.value
                technical_lifetime_in_years = config.lifetime.value
                maintenance_costs_in_euro = (
                    config.maintenance_cost_as_percentage_of_investment.value * capex_investment_cost_in_euro
                )

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
            capex_investment_cost_in_euro=capex_investment_cost_in_euro,
            device_co2_footprint_in_kg=device_co2_footprint_in_kg,
            lifetime_in_years=technical_lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            maintenance_costs_in_euro=maintenance_costs_in_euro,
            maintenance_cost_per_simulated_period_in_euro=maintenance_costs_per_simulated_period_in_euro,
            kpi_tag=kpi_tag,
        )
        return capex_cost_data_class
