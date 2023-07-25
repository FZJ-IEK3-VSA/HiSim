"""Calculate Opex and Capex for each component."""

import os
from typing import List
import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper


def opex_calculation(
    components: List[ComponentWrapper],
    all_outputs: List,
    postprocessing_results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
) -> List[str]:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0.0
    total_operational_cost = 0.0
    lines = []
    lines.append("component_name, costs, co2_footprint")

    for component in components:
        component_unwrapped = component.my_component
        cost, co2_footprint = component_unwrapped.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        total_operational_cost += cost
        total_operational_co2_footprint += co2_footprint

        lines.append(f"{component_unwrapped.component_name}, {cost}, {co2_footprint}")

    pathname = os.path.join(
        simulation_parameters.result_directory, "costs_co2_footprint.csv"
    )
    my_cost_df = pd.DataFrame(lines)
    my_cost_df.to_csv(pathname, index=False, header=False, encoding="utf8")

    lines.append(f"total operational cost: {total_operational_cost:3.0f} EUR")
    lines.append(
        f"total operational CO2-emissions: {total_operational_co2_footprint:3.0f} kg"
    )

    return lines


def capex_calculation(
    components: List[ComponentWrapper],
    simulation_parameters: SimulationParameters,
) -> List[str]:
    """Loops over all components and calls capex cost calculation."""
    seconds_per_year = 365 * 24 * 60 * 60
    total_investment_cost = 0.0
    total_device_co2_footprint = 0.0
    total_investment_cost_per_simulated_period = 0.0
    total_device_co2_footprint_per_simulated_period = 0.0

    lines = []
    lines.append(
        "component_name, investment_costs_per_simulated_period, device_co2_footprint_per_simulated_period, lifetime"
    )

    for component in components:
        component_unwrapped = component.my_component
        capex, co2_footprint, lifetime = component_unwrapped.get_cost_capex(
            component_unwrapped.config
        )

        if lifetime > 0:
            # lifetime is per default set to 1.0 in class cp.Component to avoid devide by zero error
            capex_per_simulated_period = (capex / lifetime) * (
                simulation_parameters.duration.total_seconds() / seconds_per_year
            )
            device_co2_footprint_per_simulated_period = (co2_footprint / lifetime) * (
                simulation_parameters.duration.total_seconds() / seconds_per_year
            )
            total_investment_cost += capex
            total_device_co2_footprint += co2_footprint
            total_investment_cost_per_simulated_period += capex_per_simulated_period
            total_device_co2_footprint_per_simulated_period += (
                device_co2_footprint_per_simulated_period
            )

            lines.append(
                f"{component_unwrapped.component_name}, {capex_per_simulated_period}, {device_co2_footprint_per_simulated_period}, {lifetime}"
            )
        else:
            log.warning(
                f"capex calculation not valid. Check lifetime in Configuration of {component}"
            )
            lines.append(f"Capex calculation of {component} not valid")

    pathname = os.path.join(
        simulation_parameters.result_directory, "investment_cost_co2_footprint.csv"
    )
    my_cost_df = pd.DataFrame(lines)
    my_cost_df.to_csv(pathname, index=False, header=False, encoding="utf8")

    lines.append(
        f"investment cost per simulated periond: {total_investment_cost_per_simulated_period:3.0f} EUR"
    )
    lines.append(
        f"CO2-emissions for production of all devices per simulated periond: {total_device_co2_footprint_per_simulated_period:3.0f} kg"
    )

    lines.append(f"total investment cost: {total_investment_cost:3.0f} EUR")
    lines.append(
        f"total CO2-emissions for production of all devices: {total_device_co2_footprint:3.0f} kg"
    )

    return lines
