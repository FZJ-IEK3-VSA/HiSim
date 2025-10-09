""" Dynamic components are able to have an arbitrary number of inputs and outputs. """
# clean

from typing import Any, List, Union, Dict
import hisim.loadtypes as lt
from hisim.component import Component, ConfigBase, SingleTimeStepValues, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.dynamic_component import (
    DynamicConnectionInput,
    DynamicConnectionOutput,
    DynamicComponentConnection,
    search_and_compare,
    tags_search_and_compare,
)


class ObsoleteDynamicComponent(Component):

    """Class for components with a dynamic number of inputs and outputs."""

    def __init__(
        self,
        my_component_inputs: List[DynamicConnectionInput],
        my_component_outputs: List[DynamicConnectionOutput],
        name: str,
        my_simulation_parameters: SimulationParameters,
        my_config: ConfigBase,
        my_display_config: DisplayConfig,
    ):
        """Initializes a dynamic component."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=my_config,
            my_display_config=my_display_config,
        )

        self.my_component_inputs = my_component_inputs
        self.my_component_outputs = my_component_outputs
        self.dynamic_default_connections: Dict[str, List[DynamicComponentConnection]] = {}

    def obsolete_get_dynamic_input_value(
        self,
        stsv: SingleTimeStepValues,
        tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        weight_counter: int,
    ) -> Any:
        """Returns input value from first dynamic input with component type and weight."""
        inputvalue = None

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags,
            ):
                inputvalue = stsv.get_input_value(getattr(self, element.source_component_class))
                break
        return inputvalue

    def obsolete_get_dynamic_input_values(
        self,
        stsv: SingleTimeStepValues,
        tags: List[Union[lt.ComponentType, lt.InandOutputType]],
    ) -> List:
        """Returns input values from all dynamic inputs with component type and weight."""
        inputvalues = []

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if tags_search_and_compare(tags_to_search=tags, tags_of_component=element.source_tags):
                inputvalues.append(stsv.get_input_value(getattr(self, element.source_component_class)))
            else:
                continue
        return inputvalues

    def obsolete_set_dynamic_output_value(
        self,
        stsv: SingleTimeStepValues,
        tags: List[Union[lt.ComponentType, lt.InandOutputType]],
        weight_counter: int,
        output_value: float,
    ) -> None:
        """Sets all output values with given component type and weight."""

        # check if component of component type is available
        for _, element in enumerate(self.my_component_outputs):  # loop over all inputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags,
            ):
                stsv.set_output_value(getattr(self, element.source_component_label), output_value)
            else:
                continue
