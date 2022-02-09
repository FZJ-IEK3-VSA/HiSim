# Generic/Built-in
import copy
from typing import List
# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import component as cp
from hisim import loadtypes as lt



class CalculateOperation(cp.Component):
    operations_available = ["Sum", "Subtract", "Multiply", "Divide"]
    Output = "Output"

    def __init__(self, name: str, loadtype: lt.LoadTypes, unit: lt.Units):
        super().__init__(name)
        self.operations: List[str] = []
        self.loadtype = loadtype
        self.unit = unit
        self.output1: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                        self.Output,
                                                        loadtype,
                                                        unit)

    def add_numbered_input(self) -> cp.ComponentInput:
        num_inputs = len(self.inputs)
        label = "Input{}".format(num_inputs + 1)
        vars(self)[label] = label
        myinput = cp.ComponentInput(self.ComponentName, label, self.loadtype, self.unit, True)
        self.inputs.append(myinput)
        return myinput

    def connect_arbitrary_input(self, src_object_name: str, src_field_name: str):
        next_input = self.add_numbered_input()
        next_input.src_object_name = src_object_name
        next_input.src_field_name = src_field_name

    def add_operation(self, operation: str):
        num_operations = len(self.operations)
        num_inputs = len(self.inputs)
        if num_inputs == num_operations + 1:
            if operation in self.operations_available:
                self.operations.append(operation)
            else:
                raise Exception("Operation not implemented!")
        elif num_inputs >= num_operations + 1:
            raise Exception("Inputs connected without operation! {} operations are missing!".format(num_inputs - (num_operations + 1)))
        else:
            raise Exception("Inputs connected without operation! {} operations are missing!".format((num_operations + 1) - num_inputs))
        return operation

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        total = 0
        for index, input in enumerate(self.inputs):
            val1 = stsv.get_input_value(input)
            if index == 0:
                total = val1
            elif self.operations[index-1] == "Sum":
                    total = total + val1
            elif self.operations[index-1] == "Subtract":
                    total = total - val1
            elif self.operations[index-1] == "Multiply":
                    total = total * val1
            elif self.operations[index-1] == "Divide":
                    total = total / val1
            else:
                raise Exception("Operation invalid!")
        stsv.set_output_value(self.output1, total)

class ElectricityGrid(Component):
    operations_available = ["Sum", "Subtract"]
    ElectricityOutput = "ElectricityOutput"

    def __init__(self, name: str, grid=None, signal=None):
        super().__init__(name="{}_{}".format("ElectricityGrid", name))
        self.signal=signal
        self.operations: List[str] = []
        self.loadtype = lt.LoadTypes.Electricity
        self.unit = lt.Units.Watt

        if grid is not None:
            self.connect_all(grid)

        self.electricity_outputC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                   self.ElectricityOutput,
                                                                   lt.LoadTypes.Electricity,
                                                                   lt.Units.Watt)
    def add_numbered_input(self) -> cp.ComponentInput:
        num_inputs = len(self.inputs)
        label = "Input{}".format(num_inputs + 1)
        vars(self)[label] = label
        myinput = cp.ComponentInput(self.ComponentName, label, self.loadtype, self.unit, True)
        self.inputs.append(myinput)
        return myinput

    def __add__(self, other_electricity_grid):
        return ElectricityGrid(name="{}Sum{}".format(self.ComponentName, other_electricity_grid.ComponentName),
                               grid=[self, "Sum", other_electricity_grid])

    def __sub__(self, other_electricity_grid):
        return ElectricityGrid(name="{}Subtract{}".format(self.ComponentName, other_electricity_grid.ComponentName),
                               grid=[self, "Subtract", other_electricity_grid])

    def connect_electricity_input(self, component: Component):
        if hasattr(component, 'ElectricityOutput') is False:
            raise Exception("Component does not contain electricity output.")
        next_input = self.add_numbered_input()
        next_input.src_object_name = component.ComponentName
        next_input.src_field_name = component.ElectricityOutput # type: ignore

    def add_operation(self, operation: str):
        num_operations = len(self.operations)
        num_inputs = len(self.inputs)
        if num_inputs == num_operations + 1:
            if operation in self.operations_available:
                self.operations.append(operation)
            else:
                raise Exception("Operation not implemented!")
        elif num_inputs >= num_operations + 1:
            raise Exception("Inputs connected without operation! {} operations are missing!".format(num_inputs - (num_operations + 1)))
        else:
            raise Exception("Inputs connected without operation! {} operations are missing!".format((num_operations + 1) - num_inputs))
        return operation

    def connect_all(self, list_of_operations):
        if isinstance(list_of_operations, list) is False:
            raise Exception("Input has to be a list!")
        elif len(list_of_operations) % 2 == 0:
            raise Exception("List of operations is incomplete!")

        for index, element in enumerate(list_of_operations):
            if index % 2 == 0:
                self.connect_input(element)
            else:
                self.add_operation(element)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        total = 0
        for index, input in enumerate(self.inputs):
            val1 = stsv.get_input_value(input)
            if index == 0:
                total = val1
            elif self.operations[index-1] == "Sum":
                total = total + val1
            elif self.operations[index-1] == "Subtract":
                total = total - val1
            else:
                raise Exception("Operation invalid!")
        if self.signal == "Positive":
            if total <= 0:
                total = 0
        if self.signal == "Negative":
            if total >= 0:
                total = 0
        stsv.set_output_value(self.electricity_outputC, total)

class SumBuilderForTwoInputs(Component):
    SumInput1 = "Input 1"
    SumInput2 = "Input 2"
    SumOutput = "Sum"

    def __init__(self, name: str, loadtype: lt.LoadTypes, unit: lt.Units):
        super().__init__(name)
        self.input1: cp.ComponentInput = self.add_input(self.ComponentName,
                                                     SumBuilderForTwoInputs.SumInput1,
                                                     loadtype,
                                                     unit,
                                                     True)
        self.input2: cp.ComponentInput = self.add_input(self.ComponentName,
                                                     SumBuilderForTwoInputs.SumInput2,
                                                     loadtype,
                                                     unit,
                                                     False)
        self.output1: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                        SumBuilderForTwoInputs.SumOutput,
                                                        loadtype,
                                                        unit)

    def i_save_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        val1 = stsv.get_input_value(self.input1)
        val2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, val1+val2)

class SumBuilderForThreeInputs(Component):
    SumInput1 = "Input 1"
    SumInput2 = "Input 2"
    SumInput3 = "Input 3"
    SumOutput = "Sum"

    def __init__(self, name: str, loadtype: lt.LoadTypes, unit: lt.Units):
        super().__init__(name)
        self.input1: cp.ComponentInput = self.add_input(self.ComponentName, SumBuilderForThreeInputs.SumInput1,
                                                     loadtype, unit, True)
        self.input2: cp.ComponentInput = self.add_input(self.ComponentName, SumBuilderForThreeInputs.SumInput2,
                                                     loadtype, unit, False)
        self.input3: cp.ComponentInput = self.add_input(self.ComponentName, SumBuilderForThreeInputs.SumInput3,
                                                     loadtype, unit, False)
        self.output1: cp.ComponentOutput = self.add_output(self.ComponentName, SumBuilderForThreeInputs.SumOutput,
                                                        loadtype, unit)

        self.state = 0
        self.previous_state = 0

    def i_save_state(self):
        pass
        #self.previous_state = copy.copy(self.state)

    def i_restore_state(self):
        pass
        #self.state = copy.copy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        val1 = stsv.get_input_value(self.input1)
        val2 = stsv.get_input_value(self.input2)
        val3 = stsv.get_input_value(self.input3)
        stsv.set_output_value(self.output1, val1+val2+val3)


