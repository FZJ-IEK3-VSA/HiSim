"""Calculate Opex and Capex for each component."""

import os
from typing import List

import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper
from hisim.component import OpexCostDataClass
from hisim.components.advanced_battery_bslib import Battery


def opex_calculation(
    components: List[ComponentWrapper],
    all_outputs: List,
    postprocessing_results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
) -> List:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0.0
    total_operational_cost = 0.0
    headline: List[object] = [
        "Component",
        "Operational Costs in EUR",
        "Operational C02 footprint in kg",
        "Consumption in kWh",
    ]
    opex_table_as_list_of_list = []

    for component in components:
        component_unwrapped = component.my_component
        # cost and co2_footprint are calculated per simulated period
        opex_cost_data_class: OpexCostDataClass = component_unwrapped.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        cost = opex_cost_data_class.opex_cost
        co2_footprint = opex_cost_data_class.co2_footprint
        consumption = opex_cost_data_class.consumption
        total_operational_cost += cost
        total_operational_co2_footprint += co2_footprint

        opex_table_as_list_of_list.append(
            [
                component_unwrapped.component_name,
                round(cost, 2),
                round(co2_footprint, 2),
                consumption,
            ]
        )

    opex_table_as_list_of_list.append(
        [
            "Total",
            round(total_operational_cost, 2),
            round(total_operational_co2_footprint, 2),
            "---",
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
    seconds_per_year = 365 * 24 * 60 * 60
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
        capex, co2_footprint, lifetime = component_unwrapped.get_cost_capex(
            config=component_unwrapped.config,
        )

        if lifetime > 0:
            # lifetime is per default set to 1.0 in class cp.Component to avoid devide by zero error

            # battery costs and emissions are calculated per used cycles not per simulation period  # better aproximation of aging
            if isinstance(component_unwrapped, Battery) and hasattr(
                component_unwrapped, "get_battery_aging_information"
            ):
                (
                    virtual_number_of_full_charge_cycles,
                    lifetime_in_cycles,
                ) = component_unwrapped.get_battery_aging_information()
                if lifetime_in_cycles > 0:
                    capex_per_simulated_period = (capex / lifetime_in_cycles) * (virtual_number_of_full_charge_cycles)
                    device_co2_footprint_per_simulated_period = (co2_footprint / lifetime_in_cycles) * (
                        virtual_number_of_full_charge_cycles
                    )
                else:
                    log.warning(
                        f"capex calculation not valid. Check lifetime_in_cycles in Configuration of {component}"
                    )
            else:
                capex_per_simulated_period = (capex / lifetime) * (
                    simulation_parameters.duration.total_seconds() / seconds_per_year
                )
                device_co2_footprint_per_simulated_period = (co2_footprint / lifetime) * (
                    simulation_parameters.duration.total_seconds() / seconds_per_year
                )
            total_investment_cost += capex
            total_device_co2_footprint += co2_footprint
            total_investment_cost_per_simulated_period += capex_per_simulated_period
            total_device_co2_footprint_per_simulated_period += device_co2_footprint_per_simulated_period

            capex_table_as_list_of_list.append(
                [
                    component_unwrapped.component_name,
                    round(capex_per_simulated_period, 2),
                    round(device_co2_footprint_per_simulated_period, 2),
                    lifetime,
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
