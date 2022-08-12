from dataclasses import dataclass
from typing import List, Union, Any

import hisim.loadtypes as lt
from hisim.component import Component, ComponentInput, SingleTimeStepValues, ComponentOutput


@dataclass
class DynamicConnectionInput:
    SourceComponentClass: str
    SourceComponentOutput: str
    SourceLoadType: lt.LoadTypes
    SourceUnit: lt.Units
    SourceTags: list
    SourceWeight: int


@dataclass
class DynamicConnectionOutput:
    SourceComponentClass: str
    SourceOutputName: str
    SourceTags: list
    SourceWeight: int
    SourceLoadType: lt.LoadTypes
    SourceUnit: lt.Units  # noqa


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
                                        source_weight: int):
        """ Adds a component input and connects it at once. """
        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = f"Input{num_inputs}"
        vars(self)[label] = label
        print("Added component input and connect: " + source_component_class.ComponentName + " - " + source_component_output)
        # Define Input as Component Input and add it to inputs
        myinput = ComponentInput(self.ComponentName, label, source_load_type, source_unit, True)
        self.inputs.append(myinput)
        myinput.src_object_name = source_component_class.ComponentName
        myinput.src_field_name = str(source_component_output)
        self.__setattr__(label, myinput)

        # Connect Input and define it as DynamicConnectionInput
        for output_var in source_component_class.outputs:
            if output_var.DisplayName == source_component_output:
                self.connect_input(label,
                                   source_component_class.ComponentName,
                                   output_var.FieldName)
                self.my_component_inputs.append(DynamicConnectionInput(SourceComponentClass=label,
                                                                       SourceComponentOutput=source_component_output,
                                                                       SourceLoadType=source_load_type,
                                                                       SourceUnit=source_unit,
                                                                       SourceTags=source_tags,
                                                                       SourceWeight=source_weight))

    def add_component_inputs_and_connect(self,
                                         source_component_classes: List[Component],
                                         outputstring: str,
                                         source_load_type: lt.LoadTypes,
                                         source_unit: lt.Units,
                                         source_tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                                         source_weight: int):
        """ Adds and connects inputs.

        Finds all outputs of listed components containing outputstring in outputname,
        adds inputs to dynamic component and connects the outputs.
        """

        # Label Input and generate variable
        num_inputs = len(self.inputs)

        # Connect Input and define it as DynamicConnectionInput
        for component in source_component_classes:
            for output_var in component.outputs:
                if outputstring in output_var.DisplayName:
                    source_component_output = output_var.DisplayName

                    label = f"Input{num_inputs}"
                    vars(self)[label] = label

                    # Define Input as Component Input and add it to inputs
                    myinput = ComponentInput(self.ComponentName, label, source_load_type, source_unit, True)
                    self.inputs.append(myinput)
                    myinput.src_object_name = component.ComponentName
                    myinput.src_field_name = str(source_component_output)
                    self.__setattr__(label, myinput)
                    num_inputs += 1
                    print("Added component inputs and connect: " + myinput.src_object_name + " - " + myinput.src_field_name)
                    self.connect_input(label,
                                       component.ComponentName,
                                       output_var.FieldName)
                    self.my_component_inputs.append(DynamicConnectionInput(SourceComponentClass=label,
                                                                           SourceComponentOutput=source_component_output,
                                                                           SourceLoadType=source_load_type,
                                                                           SourceUnit=source_unit,
                                                                           SourceTags=source_tags,
                                                                           SourceWeight=source_weight))

    def get_dynamic_input(self, stsv: SingleTimeStepValues,
                          tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                          weight_counter: int) -> Any:
        """ Returns input value from first dynamic input with component type and weight. """
        inputvalue = None

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if all(tag in element.SourceTags for tag in tags) and weight_counter == element.SourceWeight:
                inputvalue = stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
                break
        return inputvalue

    def get_dynamic_inputs(self, stsv: SingleTimeStepValues,
                           tags: List[Union[lt.ComponentType, lt.InandOutputType]]) -> List:
        """ Returns input values from all dynamic inputs with component type and weight. """
        inputvalues = []

        # check if component of component type is available
        for _, element in enumerate(self.my_component_inputs):  # loop over all inputs
            if all(tag in element.SourceTags for tag in tags):
                inputvalues.append(stsv.get_input_value(self.__getattribute__(element.SourceComponentClass)))
            else:
                continue
        return inputvalues

    def set_dynamic_output(self, stsv: SingleTimeStepValues,
                           tags: List[Union[lt.ComponentType, lt.InandOutputType]],
                           weight_counter: int,
                           output_value: float):
        """ Sets all output values with given component type and weight. """

        # check if component of component type is available
        for _, element in enumerate(self.my_component_outputs):  # loop over all inputs
            if all(tag in element.SourceTags for tag in tags) and weight_counter == element.SourceWeight:
                print(element.SourceTags)
                print(element.SourceComponentClass)
                stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), output_value)
            else:
                continue

    def add_component_output(self, source_output_name: str,
                             source_tags: list,
                             source_load_type: lt.LoadTypes,
                             source_unit: lt.Units,
                             source_weight: int):
        """ Adds an output channel to a component. """
        # Label Output and generate variable
        num_inputs = len(self.outputs)
        label = f"Output{num_inputs + 1}"
        vars(self)[label] = label

        # Define Output as Component Input and add it to inputs
        myoutput = ComponentOutput(self.ComponentName, source_output_name + label, source_load_type, source_unit,
                                   True)
        self.outputs.append(myoutput)
        self.__setattr__(label, myoutput)

        # Define Output as DynamicConnectionInput
        self.my_component_outputs.append(DynamicConnectionOutput(SourceComponentClass=label,
                                                                 SourceOutputName=source_output_name + label,
                                                                 SourceTags=source_tags,
                                                                 SourceLoadType=source_load_type,
                                                                 SourceUnit=source_unit,
                                                                 SourceWeight=source_weight))
        return myoutput
