import pandas as pd
from typing import List
from hisim.component_wrapper import ComponentWrapper
from hisim import log

def opex_calculation(components: List[ComponentWrapper], all_outputs: List, postprocessing_results: pd.DataFrame) -> None:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0
    total_operational_cost = 0
  
    for component in components:
        if "get_cost_opex" in dir(component.my_component):
            cost, co2_footprint = component.my_component.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results,)
            total_operational_cost += cost
            total_operational_co2_footprint += co2_footprint
        else:
            log.warning(f"No method get_cost_opex() exists for component {component.my_component.component_name}.")
