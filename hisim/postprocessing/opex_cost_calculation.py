import os
import pandas as pd
from typing import List
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

        lines.append(
            f"{component_unwrapped.component_name}, {cost}, {co2_footprint}"
        )

    pathname = os.path.join(simulation_parameters.result_directory, "costs_co2_footprint.csv")
    df = pd.DataFrame(lines)
    df.to_csv(pathname, index=False, header=False, encoding="utf8")

    lines.append(f"total operational cost: {total_operational_cost:3.0f} EUR")
    lines.append(f"total operational CO2-emissions: {total_operational_co2_footprint:3.0f} kg")

    return lines