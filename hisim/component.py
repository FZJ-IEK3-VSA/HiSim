# Generic
import logging
from typing import List, Optional
import datetime

# Owned
import loadtypes as lt


class SimulationParameters:
    def __init__(self, start_date, end_date, seconds_per_timestep):
        self.start_date = start_date
        self.end_date = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds / seconds_per_timestep)

    @classmethod
    def full_year(cls, year: int = 2019, seconds_per_timestep: int = 60):
        return cls(datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1), seconds_per_timestep)

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 31), seconds_per_timestep)

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int):
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 2), seconds_per_timestep)


class ComponentOutput:
    def __init__(self, object_name: str, field_name: str, load_type: lt.LoadTypes, unit: lt.Units,
                 sankey_flow_direction: bool = None):
        self.FullName: str = object_name + " # " + field_name
        self.ObjectName: str = object_name  # ComponentName
        self.FieldName: str = field_name
        self.DisplayName: str = field_name
        self.LoadType: lt.LoadTypes = load_type
        self.Unit: lt.Units = unit
        self.GlobalIndex: int = -1
        self.SankeyFlowDirection: bool = sankey_flow_direction


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

    def print(self):
        print()
        print(*self.values, sep=", ")


class Component:
    
    def __init__(self, name: str):
        self.ComponentName: str = name
        self.inputs: List[ComponentInput] = []
        self.outputs: List[ComponentOutput] = []
        self.outputs_initialized: bool = False
        self.inputs_initialized: bool = False

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
            raise Exception("The component " + self.ComponentName + " has no inputs.")
        componenet_input: ComponentInput
        input_to_set = None
        for componenet_input in self.inputs:
            if componenet_input.FieldName == input_fieldname:
                input_to_set = componenet_input
        if input_to_set is None:
            raise Exception("The component " + self.ComponentName + " has no input with the name " + input_fieldname)
        input_to_set.src_object_name = src_object_name
        input_to_set.src_field_name = src_field_name

    def connect_electricity(self, component):
        if isinstance(component, Component) is False:
            raise Exception("Input has to be a component!")
        elif hasattr(component, "ElectricityOutput") is False:
            raise Exception("Input Component does not have Electricity Output!")
        elif hasattr(self, "ElectricityInput") is False:
            raise Exception("This self Component does not have Electricity Input!")
        self.connect_input(self.ElectricityInput, component.ComponentName, component.ElectricityOutput)

    def connect_similar_inputs(self, components):
        if len(self.inputs) == 0:
            raise Exception("The component " + self.ComponentName + " has no inputs.")

        if isinstance(components, list) is False:
            components = [components]

        for component in components:
            if isinstance(component, Component) is False:
                raise Exception("Input variable is not a component")
            has_not_been_connected = True
            for input in self.inputs:
                for output in component.outputs:
                    if input.FieldName == output.FieldName:
                        has_not_been_connected = False
                        self.connect_input(input.FieldName, component.ComponentName, output.FieldName)
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

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        # performs the actual calculation
        raise NotImplementedError()

## This doesn't do anything
if __name__ == "__main__":
    pass
