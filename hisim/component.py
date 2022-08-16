""" Defines the component class and helpers.

The component class is the base class for all other components.
"""

from typing import List, Optional, Dict
import typing
import dataclasses as dc
from dataclasses import dataclass

# Package
from hisim import utils
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from hisim.sim_repository import SimRepository


@dataclass
class ComponentConnection:

    """ Used in the component class for defining a connection. """

    target_input_name: str
    source_class_name: str
    source_output_name: str
    source_instance_name: Optional[str] = None


class ComponentOutput:  # noqa: too-few-public-methods

    """ Used in the component class for defining an output. """

    def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                 sankey_flow_direction: Optional[bool] = None):
        """ Defines a component output. """
        self.full_name: str = object_name + " # " + field_name
        self.component_name: str = object_name  # ComponentName
        self.field_name: str = field_name
        self.display_name: str = field_name
        self.load_type: lt.LoadTypes = load_type
        self.unit: lt.Units = unit
        self.global_index: int = -1
        self.sankey_flow_direction: Optional[bool] = sankey_flow_direction

    def get_pretty_name(self):
        """ Gets a pretty name for a component output. """
        return self.component_name + " - " + self.display_name + " [" + self.load_type + " - " + self.unit + "]"


class ComponentInput:  # noqa: too-few-public-methods

    """ Used in the component class for defining an input. """

    def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units, mandatory: bool):
        """ Initializes a component input. """
        self.fullname: str = object_name + " # " + field_name
        self.component_name: str = object_name
        self.field_name: str = field_name
        self.loadtype: lt.LoadTypes = load_type
        self.unit: lt.Units = unit
        self.global_index: int = -1
        self.src_object_name: Optional[str] = None
        self.src_field_name: Optional[str] = None
        self.source_output: Optional[ComponentOutput] = None
        self.is_mandatory = mandatory


class SingleTimeStepValues:

    """ Contains the values for a single time step. """

    def __init__(self, number_of_values: int):
        """ Initializes a new single time step values class. """
        self.values = [0.0] * number_of_values

    def copy_values_from_other(self, other):
        """ Copy all values from a single time step values. """
        self.values = other.values[:]

    def get_input_value(self, component_input: ComponentInput):
        """ Gets a value for an input from the single time step values. """
        if component_input.source_output is None:
            return 0
        # commented for performance reasons: this is called hundreds of millions of times and even
        # this small check for better error messages is taking seconds
        # if component_input.SourceOutput.GlobalIndex < 0:
        #    raise  Exception("Globalindex for input was -1: " + component_input.SourceOutput.FullName)
        return self.values[component_input.source_output.global_index]

    def set_output_value(self, output: ComponentOutput, value: float):
        """ Sets a single output value in the single time step values array. """
        # commented for performance reasons: this is called hundreds of millions of times and
        # even this small check for better error messages is taking seconds
        # if(output.GlobalIndex < 0):
        #     raise Exception("Output Index was not set correctly for " + output.FullName + ". GlobalIndex was " +str(output.GlobalIndex))
        # if(output.GlobalIndex > len(self.values)-1):
        #    raise Exception("Output Index was not set correctly for " + output.FullName)
        self.values[output.global_index] = value

    def is_close_enough_to_previous(self, previous_values):
        """ Checks if the values are sufficiently similar to another array. """
        count = len(self.values)
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                return False
        return True

    def get_differences_for_error_msg(self, previous_values, outputs: List[ComponentOutput]):
        """ Gets a pretty error message for the differences between two time steps. """
        count = len(self.values)
        error_msg = ""
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                error_msg += outputs[i].get_pretty_name() + " previously: " + str(
                    previous_values.values[i]) + " currently: " + str(self.values[i])
        return error_msg


class Component:

    """ Base class for all components. """

    @classmethod
    def get_classname(cls):
        """ Gets the class name. Helper function for default connections. """
        return cls.__name__

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters):
        """ Initializes the component class. """
        self.component_name: str = name
        self.inputs: List[ComponentInput] = []
        self.outputs: List[ComponentOutput] = []
        self.outputs_initialized: bool = False
        self.inputs_initialized: bool = False
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        if my_simulation_parameters is None:
            raise ValueError("My Simulation parameters was None.")
        self.simulation_repository: SimRepository
        self.default_connections: Dict[str, List[ComponentConnection]] = {}

    def add_default_connections(self, component, connections: List[ComponentConnection]):
        """ Adds a default connection list definition. """
        classname: str = component.get_classname()
        self.default_connections[classname] = connections
        log.trace("added connections: " + str(self.default_connections))

    def set_sim_repo(self, simulation_repository: SimRepository):
        """ Sets the SimRepository. """
        if simulation_repository is None:
            raise ValueError("simulation repository was none")
        self.simulation_repository = simulation_repository

    def add_input(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                  mandatory: bool) -> ComponentInput:
        """ Adds an input definition. """
        myinput = ComponentInput(object_name, field_name, load_type, unit, mandatory)
        self.inputs.append(myinput)
        return myinput

    def add_output(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                   sankey_flow_direction: bool = None) -> ComponentOutput:
        """ Adds an output definition. """
        log.debug("adding output: " + field_name + " to component " + object_name)
        outp = ComponentOutput(object_name, field_name, load_type, unit, sankey_flow_direction)
        self.outputs.append(outp)
        return outp

    def connect_input(self, input_fieldname: str, src_object_name: str, src_field_name: str):
        """ Connecting an input to an output. """
        if len(self.inputs) == 0:
            raise ValueError("The component " + self.component_name + " has no inputs.")
        component_input: ComponentInput
        input_to_set = None
        for component_input in self.inputs:
            if component_input.field_name == input_fieldname:
                if input_to_set is not None:
                    raise ValueError(
                        "The input " + input_fieldname + " of the component " + self.component_name + " was already set.")
                input_to_set = component_input
        if input_to_set is None:
            raise ValueError("The component " + self.component_name + " has no input with the name " + input_fieldname)
        input_to_set.src_object_name = src_object_name
        input_to_set.src_field_name = src_field_name

    def connect_dynamic_input(self, input_fieldname: str, src_object: ComponentOutput):
        """ For connecting an input to a dynamic output. """
        src_object_name = src_object.component_name
        src_field_name = src_object.field_name
        self.connect_input(input_fieldname=input_fieldname, src_object_name=src_object_name, src_field_name=src_field_name)

    # added variable input length and loop to be able to set default connections in one line in examples
    def connect_only_predefined_connections(self, *source_components):
        """ Wrapper for default connections and connect with connections list. """
        for source_component in source_components:
            connections = self.get_default_connections(source_component)
            self.connect_with_connections_list(connections)

    def connect_with_connections_list(self, connections: List[ComponentConnection]):
        """ Connect all inputs based on a connections list. """
        for connection in connections:
            src_name: str = typing.cast(str, connection.source_instance_name)
            self.connect_input(connection.target_input_name, src_name, connection.source_output_name)

    def get_default_connections(self, source_component) -> List[ComponentConnection]:
        """ Gets the default connections for this component. """
        source_classname: str = source_component.get_classname()
        target_classname: str = self.get_classname()
        if source_classname not in self.default_connections:
            raise ValueError(
                "No default connections for " + source_classname + " in the connections for " + target_classname + ". content:\n" + str(
                    self.default_connections))
        connections = self.default_connections[source_classname]
        new_connections: List[ComponentConnection] = []
        for connection in connections:
            connection_copy = dc.replace(connection)
            connection_copy.source_instance_name = source_component.component_name
            new_connections.append(connection_copy)
        return new_connections

    @utils.deprecated("connect_similar_inputs is deprecated. witch to using default connections.")
    def connect_electricity(self, component):
        """ Connect electricity outputs and inputs. """
        if isinstance(component, Component) is False:
            raise Exception("Input has to be a component!")
        if hasattr(component, "ElectricityOutput") is False:
            raise Exception("Input Component does not have Electricity Output!")
        if hasattr(self, "ElectricityInput") is False:
            raise Exception("This self Component does not have Electricity Input!")
        self.connect_input(self.ElectricityInput, component.component_name, component.ElectricityOutput)  # type: ignore

    @utils.deprecated("connect_similar_inputs is deprecated. witch to using default connections.")
    def connect_similar_inputs(self, components):
        """ Connects all inputs with identical names. """
        if len(self.inputs) == 0:
            raise Exception("The component " + self.component_name + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            for cinput in self.inputs:
                for output in component.outputs:
                    if cinput.field_name == output.field_name:
                        has_not_been_connected = False
                        self.connect_input(cinput.field_name, component.component_name, output.field_name)
            if has_not_been_connected:
                raise Exception(
                    f"No similar inputs from {self.component_name} are compatible with the outputs of {component.component_name}!")

    def get_input_definitions(self) -> List[ComponentInput]:
        """ Gets the input definitions. """
        return self.inputs

    def get_outputs(self) -> List[ComponentOutput]:
        """ Delivers a list of outputs. """
        if len(self.outputs) == 0:
            raise Exception("Error: Component " + self.component_name + " has no outputs defined")
        return self.outputs

    def i_save_state(self):
        """ Abstract. Gets called at the beginning of a timestep to save the state. """
        raise NotImplementedError()

    def i_restore_state(self):
        """ Abstract. Restores the state of the component. Can be called many times while iterating. """
        raise NotImplementedError()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool):
        """ Performs the actual calculation. """
        raise NotImplementedError()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        """ Abstract. Gets called after the iterations are finished at each time step for potential debugging purposes. """
        pass  # noqa

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
