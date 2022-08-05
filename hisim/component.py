# Generic

from typing import List, Optional, Any, Dict, Union
import typing
from hisim.simulationparameters import SimulationParameters
# Package
from hisim import loadtypes as lt
import dataclasses as dc
from dataclasses import dataclass
from hisim import log
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
        # commented for performance reasons: this is called hundreds of millions of times and even
        # this small check for better error messages is taking seconds
        #if component_input.SourceOutput.GlobalIndex < 0:
        #    raise  Exception("Globalindex for input was -1: " + component_input.SourceOutput.FullName)
        return self.values[component_input.SourceOutput.GlobalIndex]

    def set_output_value(self, output: ComponentOutput, value: float):
        # commented for performance reasons: this is called hundreds of millions of times and
        # even this small check for better error messages is taking seconds
        # if(output.GlobalIndex < 0):
        #     raise Exception("Output Index was not set correctly for " + output.FullName + ". GlobalIndex was " +str(output.GlobalIndex))
        # if(output.GlobalIndex > len(self.values)-1):
        #    raise Exception("Output Index was not set correctly for " + output.FullName)
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

    #def prin1t(self):
     #   prin1t()
      #  prin1t(*self.values, sep=", ")


class SimRepository:
    def __init__( self ):
        self.my_dict : dict[ str, Any ] = { }
        self.my_dynamic_dict : dict[ lt.ComponentType, dict[ int, Any ] ] = { elem : { } for elem in lt.ComponentType }

    def set_entry(self, key: str, entry: Any ):
        self.my_dict[ key ] = entry

    def get_entry(self, key: str) -> Any:
        return self.my_dict[key]
    
    def exist_entry( self, key : str ) -> bool:
        try:
            self.get_entry( key )
            return True
        except:
            return False
    
    def delete_entry( self, key : str ):
        self.my_dict.pop( key )
        
    def set_dynamic_entry( self, component_type: lt.ComponentType, source_weight: int, entry ):
        self.my_dynamic_dict[ component_type ][ source_weight ] = entry

    def get_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int ) -> Any:
        try:
            return self.my_dynamic_dict[ component_type ][ source_weight ]
        except:
            return None
    
    def get_dynamic_component_weights( self, component_type : lt.ComponentType ) -> list :
        return list( self.my_dynamic_dict[ component_type ].keys( ) )
    
    def delete_dynamic_entry( self, component_type: lt.ComponentType, source_weight: int ) -> Any:
        self.my_dynamic_dict[ component_type ].pop( source_weight )

class Component:
    @classmethod
    def get_classname(cls):
        return cls.__name__

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
        classname: str = component.get_classname()
        self.default_connections[classname] = connections
        log.trace("added connections: " + str(self.default_connections))

    def set_sim_repo(self, simulation_repository: SimRepository):
        """ """
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
        log.debug("adding output: " + field_name + " to component " + object_name)
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

    def connect_dynamic_input(self,input_fieldname: str,src_object: ComponentOutput ):
        src_object_name= src_object.ObjectName
        src_field_name = src_object.FieldName
        self.connect_input(input_fieldname=input_fieldname,
                           src_object_name=src_object_name,
                           src_field_name=src_field_name)

    #added variable input length and loop to be able to set default connections in one line in examples
    def connect_only_predefined_connections(self, *source_components ):
        for source_component in source_components:
            connections = self.get_default_connections(source_component)
            self.connect_with_connections_list(connections)

    def connect_with_connections_list(self, connections: List[ComponentConnection]):
         for connection in connections:
             src_name:str = typing.cast(str, connection.SourceInstanceName)
             self.connect_input(connection.TargetInputName,src_name , connection.SourceOutputName)

    def get_default_connections(self, source_component) -> List[ComponentConnection]:

        source_classname:str = source_component.get_classname()
        target_classname: str = self.get_classname()
        if not source_classname in self.default_connections:
            raise ValueError("No default connections for " + source_classname + " in the connections for " + target_classname + ". content:\n" + str(self.default_connections))
        connections = self.default_connections[source_classname]
        new_connections: List[ComponentConnection] = []
        for connection in connections:
            connection_copy = dc.replace(connection )
            connection_copy.SourceInstanceName = source_component.ComponentName
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
        """ delivers a list of inputs """
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
    SourceTags:list
    SourceWeight: int
    SourceLoadType: lt.LoadTypes
    SourceUnit: lt.Units

class DynamicComponent(Component):
    def __init__(self ,my_component_inputs,my_component_outputs,name,my_simulation_parameters):
        super().__init__(name=name,my_simulation_parameters=my_simulation_parameters)

        self.MyComponentInputs=my_component_inputs
        self.MyComponentOutputs = my_component_outputs
    def add_component_input_and_connect(self,
                                        source_component_class: Component,
                                        source_component_output: str,
                                        source_load_type: lt.LoadTypes,
                                        source_unit: lt.Units,
                                        source_tags: List[ Union[ lt.ComponentType, lt.InandOutputType ] ],
                                        source_weight: int):

        # Label Input and generate variable
        num_inputs = len(self.inputs)
        label = "Input{}".format(num_inputs)
        vars(self)[label] = label

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
                self.MyComponentInputs.append(DynamicConnectionInput(SourceComponentClass=label,
                                                                     SourceComponentOutput=source_component_output,
                                                                     SourceLoadType=source_load_type,
                                                                     SourceUnit=source_unit,
                                                                     SourceTags=source_tags,
                                                                     SourceWeight=source_weight))
                
    def add_component_inputs_and_connect(self,
                                        source_component_classes: List[ Component ],
                                        outputstring: str,
                                        source_load_type: lt.LoadTypes,
                                        source_unit: lt.Units,
                                        source_tags: List[ Union[ lt.ComponentType, lt.InandOutputType ] ],
                                        source_weight: int):
        """finds all outputs of listed components containing outputstring in outputname, adds inputs to dynamic component and connects the outputs"""

        # Label Input and generate variable
        num_inputs = len(self.inputs)

        # Connect Input and define it as DynamicConnectionInput
        for component in source_component_classes:
            for output_var in component.outputs:
                if outputstring in output_var.DisplayName :
                    source_component_output = output_var.DisplayName
                    
                    label = "Input{}".format(num_inputs)
                    vars( self )[ label ] = label
            
                    # Define Input as Component Input and add it to inputs
                    myinput = ComponentInput( self.ComponentName, label, source_load_type, source_unit, True )
                    self.inputs.append( myinput )
                    myinput.src_object_name = component.ComponentName
                    myinput.src_field_name = str( source_component_output )
                    self.__setattr__( label, myinput )
                    num_inputs += 1
                    
                    self.connect_input(label,
                                       component.ComponentName,
                                       output_var.FieldName)
                    self.MyComponentInputs.append(DynamicConnectionInput(SourceComponentClass=label,
                                                                         SourceComponentOutput=source_component_output,
                                                                         SourceLoadType=source_load_type,
                                                                         SourceUnit=source_unit,
                                                                         SourceTags=source_tags,
                                                                         SourceWeight=source_weight))
                
    def get_dynamic_input( self, stsv : SingleTimeStepValues,
                                 tags : List[ Union[ lt.ComponentType, lt.InandOutputType ] ],
                                 weight_counter : int ) -> Any:
        """returns input value from first dynamic input with component type and weight"""
        inputvalue = None
    
        #check if component of component type is available
        for index, element in enumerate( self.MyComponentInputs ): #loop over all inputs
            if all( tag in element.SourceTags for tag in tags ) and weight_counter == element.SourceWeight:
                inputvalue = stsv.get_input_value( self.__getattribute__( element.SourceComponentClass ) )
                break
            else:
                continue
        return inputvalue
    
    def get_dynamic_inputs( self, stsv : SingleTimeStepValues,
                                  tags : List[ Union[ lt.ComponentType, lt.InandOutputType ] ] ) -> List:
        """returns input values from all dynamic inputs with component type and weight"""
        inputvalues = [ ]
    
        #check if component of component type is available
        for index, element in enumerate( self.MyComponentInputs ): #loop over all inputs
            if all( tag in element.SourceTags for tag in tags ):
                inputvalues.append( stsv.get_input_value( self.__getattribute__( element.SourceComponentClass ) ) )
            else:
                continue
        return inputvalues
    
    def set_dynamic_output( self, stsv : SingleTimeStepValues,
                                  tags : List[ Union[ lt.ComponentType, lt.InandOutputType ] ],
                                  weight_counter : int,
                                  output_value : float ):
        """sets all output values with given component type and weight"""
    
        #check if component of component type is available
        for index, element in enumerate( self.MyComponentOutputs ): #loop over all inputs
            if all( tag in element.SourceTags for tag in tags ) and weight_counter == element.SourceWeight:
                stsv.set_output_value( self.__getattribute__( element.SourceComponentClass ), output_value )
            else:
                continue
    
    def add_component_output(self, source_output_name: str,
                             source_tags: list,
                             source_load_type: lt.LoadTypes,
                             source_unit: lt.Units,
                             source_weight:int):

        # Label Output and generate variable
        num_inputs = len(self.outputs)
        label = "Output{}".format(num_inputs + 1)
        vars(self)[label] = label

        # Define Output as Component Input and add it to inputs
        myoutput = ComponentOutput(self.ComponentName, source_output_name + label, source_load_type, source_unit,
                                      True)
        self.outputs.append(myoutput)
        self.__setattr__(label, myoutput)

        # Define Output as DynamicConnectionInput
        self.MyComponentOutputs.append(DynamicConnectionOutput(SourceComponentClass=label,
                                                               SourceOutputName=source_output_name + label,
                                                               SourceTags=source_tags,
                                                               SourceLoadType=source_load_type,
                                                               SourceUnit=source_unit,
                                                               SourceWeight=source_weight))
        return myoutput

## This doesn't do anything
if __name__ == "__main__":
    pass
