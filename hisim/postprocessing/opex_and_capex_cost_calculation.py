"""Calculate Opex and Capex for each component."""

import os
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper


@dataclass_json
@dataclass
class OPEXConfig:
    total_operational_cost: float
    total_operational_co2_footprint: float


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
    lines.append("Component, Operational Costs in EUR, Operational C02 footprint in kg")
    opex_table = []

    for component in components:
        component_unwrapped = component.my_component
        # cost and co2_footprint are calculated per simulated period
        cost, co2_footprint = component_unwrapped.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        total_operational_cost += cost
        total_operational_co2_footprint += co2_footprint

        opex_table.append([component_unwrapped.component_name, cost, co2_footprint])
        lines.append(
            f"{component_unwrapped.component_name}, {cost:.2f}, {co2_footprint:.2f}"
        )

    pathname = os.path.join(
        simulation_parameters.result_directory, "costs_co2_footprint.csv"
    )
    opex_table.append(
        ["Total", total_operational_cost, total_operational_co2_footprint]
    )
    opex_df = pd.DataFrame(
        opex_table,
        columns=[
            "Component",
            "Operational Costs in EUR",
            "Operational C02 footprint in kg",
        ],
    )

    lines.append(f"total operational cost: {total_operational_cost:3.0f} EUR")
    lines.append(
        f"total operational CO2-emissions: {total_operational_co2_footprint:3.0f} kg"
    )

    opex_df.to_csv(pathname, index=False, header=True, encoding="utf8")

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
        "Component, Investment for simulated period in EUR, Devices CO2 footprint for simulated period in kg, Lifetime in years"
    )
    capex_table = []

    for component in components:
        component_unwrapped = component.my_component
        capex, co2_footprint, lifetime = component_unwrapped.get_cost_capex(
            config=component_unwrapped.config,
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

            capex_table.append(
                [
                    component_unwrapped.component_name,
                    capex_per_simulated_period,
                    device_co2_footprint_per_simulated_period,
                    lifetime,
                ]
            )
            lines.append(
                f"{component_unwrapped.component_name}, {capex_per_simulated_period:.2f}, {device_co2_footprint_per_simulated_period:.2f}, {lifetime}"
            )
        else:
            log.warning(
                f"capex calculation not valid. Check lifetime in Configuration of {component}"
            )
            lines.append(f"Capex calculation of {component} not valid")

    lines.append(
        f"investment cost for simulated periond: {total_investment_cost_per_simulated_period:3.0f} EUR"
    )
    lines.append(
        f"CO2-emissions for production of all devices for simulated periond: {total_device_co2_footprint_per_simulated_period:3.0f} kg"
    )
    capex_table.append(["Total", total_investment_cost, total_device_co2_footprint, 0])
    capex_table.append(
        [
            "Total_per_simualted_period",
            total_investment_cost_per_simulated_period,
            total_device_co2_footprint_per_simulated_period,
            0,
        ]
    )
    capex_df = pd.DataFrame(
        capex_table,
        columns=[
            "Component",
            "Investment for simulated period in EUR",
            "Devices CO2 footprint for simulated period in kg",
            "Lifetime in years",
        ],
    )

    pathname = os.path.join(
        simulation_parameters.result_directory, "investment_cost_co2_footprint.csv"
    )

    capex_df.to_csv(pathname, index=False, header=True, encoding="utf8")

    lines.append(f"total investment cost: {total_investment_cost:3.0f} EUR")
    lines.append(
        f"total CO2-emissions for production of all devices: {total_device_co2_footprint:3.0f} kg"
    )

    return lines
