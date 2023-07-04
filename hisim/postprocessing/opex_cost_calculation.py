import pandas as pd
from typing import List
from hisim.component_wrapper import ComponentWrapper


def opex_calculation(components: List[ComponentWrapper], all_outputs: List, postprocessing_results: pd.DataFrame) -> None:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0.0
    total_operational_cost = 0.0

    for component in components:
        component_unwrapped = component.my_component
        cost, co2_footprint = component_unwrapped.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results,)
        total_operational_cost += cost
        total_operational_co2_footprint += co2_footprint
