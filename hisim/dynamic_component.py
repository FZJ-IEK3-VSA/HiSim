""" Dynamic components are able to have an arbitrary number of inputs and outputs. """
# clean

from dataclasses import dataclass
from typing import List, Union, Any
from hisim import log

import hisim.loadtypes as lt
from hisim.component import Component, ComponentInput, SingleTimeStepValues, ComponentOutput


@dataclass
class DynamicConnectionInput:

    """ Class for describing a single component input. """

    source_component_class: str
    source_component_output: str
    source_load_type: lt.LoadTypes
    source_unit: lt.Units
    source_tags: list
    source_weight: int


@dataclass
class DynamicConnectionOutput:

    """ Describes a single component output for dynamic component. """

    source_component_class: str
    source_output_name: str
    source_tags: list
    source_weight: int
    source_load_type: lt.LoadTypes
    source_unit: lt.Units  # noqa

def search_and_compare(weight_to_search: int, weight_of_component: int,
    tags_to_search: List[Union[lt.ComponentType, lt.InandOutputType]],
    tags_of_component: List[Union[lt.ComponentType, lt.InandOutputType]]) -> bool:
    """Compares weight and tags of component inputs and outputs. """
    if weight_to_search != weight_of_component:
        return False
    else:
        for tag_search in tags_to_search:
            if tag_search not in tags_of_component:
                    return False
        return True

def tags_search_and_compare(tags_to_search: List[Union[lt.ComponentType, lt.InandOutputType]],
    tags_of_component: List[Union[lt.ComponentType, lt.InandOutputType]]) -> bool:
    """ Compares tags of component inputs and outputs. """
    for tag_search in tags_to_search:
        if tag_search not in tags_of_component:
                return False
    return True


class DynamicComponent(Component):

    """ Class for components with a dynamic number of inputs and outputs. """

    def __init__(self, my_component_inputs, my_component_outputs, name, my_simulation_parameters):
        """ Initializes a dynamic component. """
        super().__init__(name=name, my_simulation_parameters=my_simulation_parameters)

        self.my_component_inputs = my_component_inputs
        self.my_component_outputs = my_component_outputs

    def add_component_input_and_connect(self,
                                        source_component_class: Component,
                                        source_component_output: str,
                                        source_load_type: lt.LoadTypes,
                                        source_unit: lt.Units,
                                        source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                                        source_weight: int) -> None:
        """ Adds a component input and connects it at once. """
        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = f"Input{num_inputs}"
        vars(self)[label] = label
        log.trace("Added component input and connect: " + source_component_class.component_name + " - " + source_component_output)
        # Define Input as Component Input and add it to inputs
        myinput = ComponentInput(self.component_name, label, source_load_type, source_unit, True)
        self.inputs.append(myinput)
        myinput.src_object_name = source_component_class.component_name
        myinput.src_field_name = str(source_component_output)
        setattr(self, label, myinput)

        # Connect Input and define it as DynamicConnectionInput
        for output_var in source_component_class.outputs:
            if output_var.display_name == source_component_output:
                self.connect_input(label,
                                   source_component_class.component_name,
                                   output_var.field_name)
                self.my_component_inputs.append(DynamicConnectionInput(source_component_class=label,
                                                                       source_component_output=source_component_output,
                                                                       source_load_type=source_load_type,
                                                                       source_unit=source_unit,
                                                                       source_tags=source_tags,
                                                                       source_weight=source_weight))

    def add_component_inputs_and_connect(self,
                                         source_component_classes: List[Component],
                                         outputstring: str,
                                         source_load_type: lt.LoadTypes,
                                         source_unit: lt.Units,
                                         source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                                         source_weight: int) -> None:
        """ Adds and connects inputs.

        Finds all outputs of listed components containing outputstring in outputname,
        adds inputs to dynamic component and connects the outputs.
        """

        # Label Input and generate variable
        num_inputs = len(self.inputs)

        # Connect Input and define it as DynamicConnectionInput
        for component in source_component_classes:
            for output_var in component.outputs:
                if outputstring in output_var.display_name:
                    source_component_output = output_var.display_name

                    label = f"Input{num_inputs}"
                    vars(self)[label] = label

                    # Define Input as Component Input and add it to inputs
                    myinput = ComponentInput(self.component_name, label, source_load_type, source_unit, True)
                    self.inputs.append(myinput)
                    myinput.src_object_name = component.component_name
                    myinput.src_field_name = str(source_component_output)
                    setattr(self, label, myinput)
                    num_inputs += 1
                    log.trace("Added component inputs and connect: " + myinput.src_object_name + " - " + myinput.src_field_name)
                    self.connect_input(label,
                                       component.component_name,
                                       output_var.field_name)
                    self.my_component_inputs.append(DynamicConnectionInput(source_component_class=label,
                                                                           source_component_output=source_component_output,
                                                                           source_load_type=source_load_type,
                                                                           source_unit=source_unit,
                                                                           source_tags=source_tags,
                                                                           source_weight=source_weight))

    def get_dynamic_input(self, stsv: SingleTimeStepValues,
                          tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                          weight_counter: int) -> Any:
        """ Returns input value from first dynamic input with component type and weight. """
        inputvalue = None

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags
                ):
                inputvalue = stsv.get_input_value(getattr(self, element.source_component_class))
                break
        return inputvalue

    def get_dynamic_inputs(self, stsv: SingleTimeStepValues,
                           tags: List[Union[lt.ComponentType, lt.InandOutputType]]) -> List:
        """ Returns input values from all dynamic inputs with component type and weight. """
        inputvalues = []

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if tags_search_and_compare(
                tags_to_search=tags,
                tags_of_component=element.source_tags
                ):
                inputvalues.append(stsv.get_input_value(getattr(self, element.source_component_class)))
            else:
                continue
        return inputvalues

    def set_dynamic_output(self, stsv: SingleTimeStepValues,
                           tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                           weight_counter: int,
                           output_value: float) -> None:
        """ Sets all output values with given component type and weight. """

        # check if component of component type is available
        for _, element in enumerate(self.my_component_outputs):  # loop over all inputs
            if search_and_compare(
                weight_to_search=weight_counter,
                weight_of_component=element.source_weight,
                tags_to_search=tags,
                tags_of_component=element.source_tags
                ):
                stsv.set_output_value(getattr(self, element.source_component_class), output_value)
            else:
                continue

    def add_component_output(self, source_output_name: str,
                             source_tags: list,
                             source_load_type: lt.LoadTypes,
                             source_unit: lt.Units,
                             source_weight: int) -> ComponentOutput:
        """ Adds an output channel to a component. """
        # Label Output and generate variable
        num_inputs = len(self.outputs)
        label = f"Output{num_inputs + 1}"
        vars(self)[label] = label

        # Define Output as Component Input and add it to inputs
        myoutput = ComponentOutput(object_name=self.component_name, field_name=source_output_name + label, load_type=source_load_type,
                                   unit=source_unit, sankey_flow_direction=True)
        self.outputs.append(myoutput)
        setattr(self, label, myoutput)

        # Define Output as DynamicConnectionInput
        self.my_component_outputs.append(DynamicConnectionOutput(source_component_class=label,
                                                                 source_output_name=source_output_name + label,
                                                                 source_tags=source_tags,
                                                                 source_load_type=source_load_type,
                                                                 source_unit=source_unit,
                                                                 source_weight=source_weight))
        return myoutput
