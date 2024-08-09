"""Calculate Opex and Capex for each component."""

import os
from typing import List

import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.gas_meter import GasMeter


def opex_calculation(
    components: List[ComponentWrapper],
    all_outputs: List,
    postprocessing_results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
) -> List:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0.0
    total_opex_energy_cost = 0.0
    total_opex_maintenance_cost = 0.0
    total_consumption_in_kwh = 0.0
    headline: List[object] = [
        "Component",
        "Maintenance Costs in EUR",
        "Fuel CO2 Emissions in kg",
        "Consumption in kWh",
    ]
    opex_table_as_list_of_list = []

    for component in components:
        component_unwrapped = component.my_component
        # cost and co2_footprint are calculated per simulated period
        component_class_name = component_unwrapped.get_classname()
        # get opex data for all components except Electricity and Gas Meter
        if component_class_name != ElectricityMeter.get_classname() and component_class_name != GasMeter.get_classname():
            opex_cost_data_class: OpexCostDataClass = component_unwrapped.get_cost_opex(
                all_outputs=all_outputs,
                postprocessing_results=postprocessing_results,
            )
            cost_energy = opex_cost_data_class.opex_energy_cost_in_euro
            cost_maintenance = opex_cost_data_class.opex_maintenance_cost_in_euro
            co2_footprint = opex_cost_data_class.co2_footprint_in_kg
            consumption = opex_cost_data_class.consumption_in_kwh
            total_opex_energy_cost += cost_energy
            total_opex_maintenance_cost += cost_maintenance
            total_operational_co2_footprint += co2_footprint
            total_consumption_in_kwh += consumption

            opex_table_as_list_of_list.append(
                [
                    component_unwrapped.component_name,
                    round(cost_maintenance, 2),
                    round(co2_footprint, 2),
                    round(consumption, 2),
                ]
            )

    opex_table_as_list_of_list.append(
        [
            "Total",
            round(total_opex_maintenance_cost, 2),
            round(total_operational_co2_footprint, 2),
            round(total_consumption_in_kwh, 2),
        ]
    )
    pathname = os.path.join(simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    opex_df = pd.DataFrame(opex_table_as_list_of_list, columns=headline)
    opex_df.to_csv(pathname, index=False, header=True, encoding="utf8")

    opex_table_as_list_of_list.insert(0, headline)

    return opex_table_as_list_of_list


def capex_calculation(
    components: List[ComponentWrapper],
    simulation_parameters: SimulationParameters,
) -> List:
    """Loops over all components and calls capex cost calculation."""
    total_investment_cost = 0.0
    total_device_co2_footprint = 0.0
    total_investment_cost_per_simulated_period = 0.0
    total_device_co2_footprint_per_simulated_period = 0.0

    headline: List[object] = [
        "Component",
        "Investment in EUR",
        "Device CO2-footprint in kg",
        "Lifetime in years",
    ]
    capex_table_as_list_of_list = []

    for component in components:
        component_unwrapped = component.my_component
        # capex, co2_footprint, lifetime = component_unwrapped.get_cost_capex(
        #     config=component_unwrapped.config,
        # )
        capex_cost_data_class: CapexCostDataClass = component_unwrapped.get_cost_capex(
            config=component_unwrapped.config, simulation_parameters=simulation_parameters
        )

        if capex_cost_data_class.lifetime_in_years > 0:

            total_investment_cost += capex_cost_data_class.capex_investment_cost_in_euro
            total_device_co2_footprint += capex_cost_data_class.device_co2_footprint_in_kg
            total_investment_cost_per_simulated_period += capex_cost_data_class.capex_investment_cost_for_simulated_period_in_euro
            total_device_co2_footprint_per_simulated_period += capex_cost_data_class.device_co2_footprint_for_simulated_period_in_kg

            capex_table_as_list_of_list.append(
                [
                    component_unwrapped.component_name,
                    round(capex_cost_data_class.capex_investment_cost_for_simulated_period_in_euro, 2),
                    round(capex_cost_data_class.device_co2_footprint_for_simulated_period_in_kg, 2),
                    capex_cost_data_class.lifetime_in_years,
                ]
            )
        else:
            log.warning(f"capex calculation not valid. Check lifetime in Configuration of {component}")

    capex_table_as_list_of_list.append(
        [
            "Total",
            round(total_investment_cost, 2),
            round(total_device_co2_footprint, 2),
            0,
        ]
    )
    capex_table_as_list_of_list.append(
        [
            "Total_per_simualted_period",
            round(total_investment_cost_per_simulated_period, 2),
            round(total_device_co2_footprint_per_simulated_period, 2),
            0,
        ]
    )

    pathname = os.path.join(simulation_parameters.result_directory, "investment_cost_co2_footprint.csv")
    capex_df = pd.DataFrame(capex_table_as_list_of_list, columns=headline)
    capex_df.to_csv(pathname, index=False, header=True, encoding="utf8")

    capex_table_as_list_of_list.insert(0, headline)

    return capex_table_as_list_of_list
