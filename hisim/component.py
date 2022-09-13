""" Defines the component class and helpers.

The component class is the base class for all other components.
"""
# clean
from __future__ import annotations
from typing import List, Optional, Dict, Any, Type

import typing
import dataclasses as dc
from dataclasses import dataclass
from dataclass_wizard import JSONWizard

# Package

from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import log
from hisim.sim_repository import SimRepository


@dataclass
class ConfigBase(JSONWizard):

    """ Base class for all configurations. """

    name: str

    @classmethod
    def get_main_classname(cls):
        """ Returns the fully qualified class name for the class that is getting configured. Used for Json. """
        raise NotImplementedError("Missing a definition of the ")

    @classmethod
    def get_config_classname(cls):
        """ Gets the class name. Helper function for default connections. """
        return cls.__module__ + "." + cls.__name__

    def get_string_dict(self) -> List[str]:
        """ Turns the config into a str list for the report. """
        my_dict = self.to_dict()
        my_list = []
        if len(my_dict) > 0:
            for entry in my_dict.items():
                my_list.append(entry[0] + ": " + str(entry[1]))
        return my_list


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
                 postprocessing_flag: Optional[List[Any]] = None,
                 sankey_flow_direction: Optional[bool] = None):
        """ Defines a component output. """
        self.full_name: str = object_name + " # " + field_name
        self.component_name: str = object_name  # ComponentName
        self.field_name: str = field_name
        self.display_name: str = field_name
        self.load_type: lt.LoadTypes = load_type
        self.unit: lt.Units = unit
        self.global_index: int = -1
        self.postprocessing_flag: Optional[List[Any]] = postprocessing_flag
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

    def get_input_value(self, component_input: ComponentInput) -> float:
        """ Gets a value for an input from the single time step values. """
        if component_input.source_output is None:
            return 0
        return self.values[component_input.source_output.global_index]

    def set_output_value(self, output: ComponentOutput, value: float) -> None:
        """ Sets a single output value in the single time step values array. """
        self.values[output.global_index] = value

    def is_close_enough_to_previous(self, previous_values: "SingleTimeStepValues") -> bool:
        """ Checks if the values are sufficiently similar to another array. """
        count = len(self.values)
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                return False
        return True

    def get_differences_for_error_msg(self, previous_values: Any, outputs: List[ComponentOutput]) -> str:
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

    @classmethod
    def get_full_classname(cls):
        """ Gets the class name. Helper function for default connections. """
        return cls.__module__ + "." + cls.__name__

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters) -> None:
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

    def add_default_connections(self, component: Type[Component], connections: List[ComponentConnection]) -> None:
        """ Adds a default connection list definition. """
        classname: str = component.get_classname()
        self.default_connections[classname] = connections
        log.trace("added connections: " + str(self.default_connections))

    def i_prepare_simulation(self) -> None:
        """ Gets called before the simulation to prepare the calculation. """
        raise NotImplementedError("Simulation preparation is missing for " + self.component_name + " (" + self.get_full_classname() + ")")

    def set_sim_repo(self, simulation_repository: SimRepository) -> None:
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
                   postprocessing_flag: Optional[List[Any]] = None, sankey_flow_direction: bool = None) -> ComponentOutput:
        """ Adds an output definition. """
        log.debug("adding output: " + field_name + " to component " + object_name)
        outp = ComponentOutput(object_name, field_name, load_type, unit, postprocessing_flag, sankey_flow_direction)
        self.outputs.append(outp)
        return outp

    def connect_input(self, input_fieldname: str, src_object_name: str, src_field_name: str) -> None:
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

    def connect_dynamic_input(self, input_fieldname: str, src_object: ComponentOutput) -> None:
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

    def connect_with_connections_list(self, connections: List[ComponentConnection]) -> None:
        """ Connect all inputs based on a connections list. """
        for connection in connections:
            src_name: str = typing.cast(str, connection.source_instance_name)
            self.connect_input(connection.target_input_name, src_name, connection.source_output_name)

    def get_default_connections(self, source_component: Type[Component]) -> List[ComponentConnection]:
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

    def get_input_definitions(self) -> List[ComponentInput]:
        """ Gets the input definitions. """
        return self.inputs

    def get_outputs(self) -> List[ComponentOutput]:
        """ Delivers a list of outputs. """
        if len(self.outputs) == 0:
            raise Exception("Error: Component " + self.component_name + " has no outputs defined")
        return self.outputs

    def i_save_state(self) -> None:
        """ Abstract. Gets called at the beginning of a timestep to save the state. """
        raise NotImplementedError()

    def i_restore_state(self) -> None:
        """ Abstract. Restores the state of the component. Can be called many times while iterating. """
        raise NotImplementedError()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """ Performs the actual calculation. """
        raise NotImplementedError()

    def write_to_report(self) -> Any:
        """ Abstract function for writing the report entry for this component. """
        raise NotImplementedError("In " + self.component_name)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """ Abstract. Gets called after the iterations are finished at each time step for potential debugging purposes. """
        pass  # noqa
