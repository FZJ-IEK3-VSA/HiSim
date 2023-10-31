"""Get capacities for each componente."""

from typing import Dict, List

from hisim import log
from hisim.component_wrapper import ComponentWrapper


def get_capacities(
    components: List[ComponentWrapper],
) -> Dict[str, float]:
    capacities: Dict[str, float] = {}
    for component in components:
        component_unwrapped = component.my_component
        try:
            capacity, unit = component_unwrapped.config.get_capacity()
            capacities[
                component_unwrapped.component_name + f" [Capacity in {unit.value}]"
            ] = capacity
        except NotImplementedError:
            log.debug(
                f"No `get_capacity` method for {component_unwrapped.component_name}"
            )
    return capacities
