# Generic
import logging
from typing import List, Optional, Any, Dict
import typing
from hisim.simulationparameters import SimulationParameters
# Package
from hisim import loadtypes as lt
import dataclasses as dc
#from dataclasses import  dataclass
from dataclasses import dataclass

@dataclass
class ComponentConnection:
    TargetInputName: str
    SourceClassName: str
    SourceOutputName: str
    SourceInstanceName: Optional[str] = None
    

class ComponentOutput:
    def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                 sankey_flow_direction: Optional[bool] = None):
        self.FullName: str = object_name + " # " + field_name
        self.ObjectName: str = object_name  # ComponentName
        self.FieldName: str = field_name
        self.DisplayName: str = field_name
        self.LoadType: lt.LoadTypes = load_type
        self.Unit: lt.Units = unit
        self.GlobalIndex: int = -1
        self.SankeyFlowDirection: Optional[bool] = sankey_flow_direction

    def get_pretty_name(self):
        return self.ObjectName + " - " + self.DisplayName + " [" + self.LoadType + " - " + self.Unit + "]"

class ComponentInput:
    def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units, mandatory: bool):
        self.FullName: str = object_name + " # " + field_name
        self.ObjectName: str = object_name
        self.FieldName: str = field_name
        self.LoadType: lt.LoadTypes = load_type
        self.Unit: lt.Units = unit
        self.GlobalIndex: int = -1
        self.src_object_name: Optional[str] = None
        self.src_field_name: Optional[str] = None
        self.SourceOutput: Optional[ComponentOutput] = None
        self.Mandatory = mandatory


class SingleTimeStepValues:
    def __init__(self, number_of_values: int):
        self.values = [0.0] * number_of_values  # np.ndarray([number_of_values], dtype=float)
        #self.dict = {}

    def copy_values_from_other(self, other):
        self.values = other.values[:]  # [x for x in other.values]

    def get_input_value(self, component_input: ComponentInput):
        if component_input.SourceOutput is None:
            return 0
        return self.values[component_input.SourceOutput.GlobalIndex]

    def set_output_value(self, output: ComponentOutput, value: float):
        self.values[output.GlobalIndex] = value

    def is_close_enough_to_previous(self, previous_values):
        count = len(self.values)
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                return False
        return True

    def get_differences_for_error_msg(self, previous_values, outputs: List[ComponentOutput]):
        count = len(self.values)
        error_msg = ""
        for i in range(count):
            if abs(previous_values.values[i] - self.values[i]) > 0.0001:
                error_msg += outputs[i].get_pretty_name() + " previously: " + str(previous_values.values[i]) + " currently: " + str(self.values[i])
        return error_msg

    def print(self):
        print()
        print(*self.values, sep=", ")


class SimRepository:
    def __init__(self):
        self.my_dict = {}

    def set_entry(self, key: str, entry: Any):
        self.my_dict[key] = entry

    def get_entry(self, key: str) -> Any:
        return self.my_dict[key]

class Component:
    
    def __init__(self, name: str,my_simulation_parameters: SimulationParameters ):
        self.ComponentName: str = name
        self.inputs: List[ComponentInput] = []
        self.outputs: List[ComponentOutput] = []
        self.outputs_initialized: bool = False
        self.inputs_initialized: bool = False
        self.my_simulation_parameters:SimulationParameters = my_simulation_parameters
        self.simulation_repository: SimRepository
        self.default_connections: Dict[str, List[ComponentConnection]] = {}

    def add_default_connections(self, component, connections: List[ComponentConnection]):
        classname: str = component.__class__.__name__
        self.default_connections[classname] = connections

    def set_sim_repo(self, simulation_repository: SimRepository):
        if simulation_repository is None:
            raise ValueError("simulation repository was none")
        self.simulation_repository = simulation_repository

    def add_input(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                  mandatory: bool) -> ComponentInput:
        myinput = ComponentInput(object_name, field_name, load_type, unit, mandatory)
        self.inputs.append(myinput)
        return myinput

    def add_output(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                   sankey_flow_direction: bool = None) -> ComponentOutput:
        logging.debug("adding output: " + field_name + " to component " + object_name)
        outp = ComponentOutput(object_name, field_name, load_type, unit, sankey_flow_direction)
        self.outputs.append(outp)
        return outp

    def connect_input(self, input_fieldname: str, src_object_name: str, src_field_name: str):
        if len(self.inputs) == 0:
            raise ValueError("The component " + self.ComponentName + " has no inputs.")
        component_input: ComponentInput
        input_to_set = None
        for component_input in self.inputs:
            if component_input.FieldName == input_fieldname:
                if input_to_set is not None:
                    raise ValueError("The input " + input_fieldname +" of the component " + self.ComponentName + " was already set." )
                input_to_set = component_input
        if input_to_set is None:
            raise ValueError("The component " + self.ComponentName + " has no input with the name " + input_fieldname)
        input_to_set.src_object_name = src_object_name
        input_to_set.src_field_name = src_field_name

    def connect_only_predefined_connections(self,source_component ):
        connections = self.get_default_connections(source_component)
        self.connect_with_connections_list(connections)

    def connect_with_connections_list(self, connections: List[ComponentConnection]):
         for connection in connections:
             src_name:str = typing.cast(str, connection.SourceInstanceName)
             self.connect_input(connection.TargetInputName,src_name , connection.SourceOutputName)

    def get_default_connections(self, source_component) -> List[ComponentConnection]:
        classname:str = source_component.__class__.__name__
        if not classname in self.default_connections:
            raise ValueError("No default connections for " + classname)
        connections = self.default_connections[classname]
        new_connections: List[ComponentConnection] = []
        for connection in connections:
            connection_copy = dc.replace(connection, SourceInstanceName=source_component.name )
            new_connections.append(connection_copy)
        return new_connections

    def connect_electricity(self, component):
        if isinstance(component, Component) is False:
            raise Exception("Input has to be a component!")
        elif hasattr(component, "ElectricityOutput") is False:
            raise Exception("Input Component does not have Electricity Output!")
        elif hasattr(self, "ElectricityInput") is False:
            raise Exception("This self Component does not have Electricity Input!")
        self.connect_input(self.ElectricityInput, component.ComponentName, component.ElectricityOutput) # type: ignore

    def connect_similar_inputs(self, components):
        if len(self.inputs) == 0:
            raise Exception("The component " + self.ComponentName + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            for cinput in self.inputs:
                for output in component.outputs:
                    if cinput.FieldName == output.FieldName:
                        has_not_been_connected = False
                        self.connect_input(cinput.FieldName, component.ComponentName, output.FieldName)
            if has_not_been_connected:
                raise Exception(
                    "No similar inputs from {} are compatible with the outputs of {}!".format(self.ComponentName,
                                                                                              component.ComponentName))

    def get_input_definitions(self) -> List[ComponentInput]:
        # delivers a list of inputs
        return self.inputs

    def get_outputs(self) -> List[ComponentOutput]:
        # delivers a list of outputs
        if len(self.outputs) == 0:
            raise Exception("Error: Component " + self.ComponentName + " has no outputs defined")
        return self.outputs

    def i_save_state(self):
        # gets called at the beginning of a timestep to save the state
        raise NotImplementedError()

    def i_restore_state(self):
        # can be called many times while iterating
        raise NotImplementedError()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool):
        # performs the actual calculation
        raise NotImplementedError()

    def i_doublecheck(self, timestep: int,  stsv: SingleTimeStepValues):
        pass

## This doesn't do anything
if __name__ == "__main__":
    pass
