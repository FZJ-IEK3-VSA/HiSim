""" Contains functions to sum up multiple inputs. """

# clean
from dataclasses import dataclass
from typing import Any, List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import Component
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class SumBuilderConfig(cp.ConfigBase):
    """Electricity Grid Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SumBuilderForTwoInputs.get_full_classname()

    building_name: str
    name: str
    loadtype: lt.LoadTypes
    unit: lt.Units

    @classmethod
    def get_sumbuilder_default_config(cls):
        """Gets a default Sumbuilder."""
        return SumBuilderConfig(building_name="BUI1", name="Sum", loadtype=lt.LoadTypes.ANY, unit=lt.Units.ANY)


class CalculateOperation(cp.Component):
    """Arbitrary mathematical operations."""

    operations_available = ["Sum", "Subtract", "Multiply", "Divide"]
    Output = "Output"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initializes the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.operations: List[str] = []
        self.loadtype = config.loadtype
        self.unit = config.unit
        self.output1: cp.ComponentOutput = self.add_output(
            self.component_name, self.Output, config.loadtype, config.unit
        )

    def add_numbered_input(self) -> cp.ComponentInput:
        """Adds a numbered input."""
        num_inputs = len(self.inputs)
        label = f"Input{num_inputs + 1}"
        vars(self)[label] = label
        myinput = cp.ComponentInput(self.component_name, label, self.loadtype, self.unit, True)
        self.inputs.append(myinput)
        return myinput

    def connect_arbitrary_input(self, src_object_name: str, src_field_name: str) -> None:
        """Connect arbitrary inputs."""
        next_input = self.add_numbered_input()
        next_input.src_object_name = src_object_name
        next_input.src_field_name = src_field_name

    def add_operation(self, operation: str) -> Any:
        """Adds the operation."""
        num_operations = len(self.operations)
        num_inputs = len(self.inputs)
        if num_inputs == num_operations + 1:
            if operation in self.operations_available:
                self.operations.append(operation)
            else:
                raise Exception("Operation not implemented!")
        elif num_inputs >= num_operations + 1:
            raise Exception(
                f"Inputs connected without operation! {num_inputs - (num_operations + 1)} operations are missing!"
            )
        else:
            raise Exception(
                f"Inputs connected without operation! {(num_operations + 1) - num_inputs} operations are missing!"
            )
        return operation

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double checks the results."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates."""
        total: float = 0
        for index, input_channel in enumerate(self.inputs):
            val1 = stsv.get_input_value(input_channel)
            if index == 0:
                total = val1
            elif self.operations[index - 1] == "Sum":
                total = total + val1
            elif self.operations[index - 1] == "Subtract":
                total = total - val1
            elif self.operations[index - 1] == "Multiply":
                total = total * val1
            elif self.operations[index - 1] == "Divide":
                total = total / val1
            else:
                raise Exception("Operation invalid!")
        stsv.set_output_value(self.output1, total)


class SumBuilderForTwoInputs(Component):
    """Adds two outputs."""

    SumInput1 = "Input 1"
    SumInput2 = "Input 2"
    SumOutput = "Sum"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initializes the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.input1: cp.ComponentInput = self.add_input(
            self.component_name,
            SumBuilderForTwoInputs.SumInput1,
            config.loadtype,
            config.unit,
            True,
        )
        self.input2: cp.ComponentInput = self.add_input(
            self.component_name,
            SumBuilderForTwoInputs.SumInput2,
            config.loadtype,
            config.unit,
            False,
        )
        self.output1: cp.ComponentOutput = self.add_output(
            self.component_name,
            SumBuilderForTwoInputs.SumOutput,
            config.loadtype,
            config.unit,
            output_description="Sum of values",
        )

    def i_save_state(self) -> None:
        """For saving state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """For double checking results."""
        pass

    def i_restore_state(self) -> None:
        """Restores state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Adds the two values."""
        val1 = stsv.get_input_value(self.input1)
        val2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, val1 + val2)

    def write_to_report(self) -> List[str]:
        """Writes information to the report."""
        lines = []
        lines.append(f"Sumbuilder for two inputs: {self.component_name}")
        lines.append(f"Input 1: {self.input1.fullname}")
        lines.append(f"Input 2: {self.input2.fullname}")
        return lines


class SumBuilderForThreeInputs(Component):
    """Sum builder for three inputs."""

    SumInput1 = "Input 1"
    SumInput2 = "Input 2"
    SumInput3 = "Input 3"
    SumOutput = "Sum"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initializes the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.input1: cp.ComponentInput = self.add_input(
            self.component_name,
            SumBuilderForThreeInputs.SumInput1,
            config.loadtype,
            config.unit,
            True,
        )
        self.input2: cp.ComponentInput = self.add_input(
            self.component_name,
            SumBuilderForThreeInputs.SumInput2,
            config.loadtype,
            config.unit,
            False,
        )
        self.input3: cp.ComponentInput = self.add_input(
            self.component_name,
            SumBuilderForThreeInputs.SumInput3,
            config.loadtype,
            config.unit,
            False,
        )
        self.output1: cp.ComponentOutput = self.add_output(
            self.component_name,
            SumBuilderForThreeInputs.SumOutput,
            config.loadtype,
            config.unit,
        )

        self.state = 0
        self.previous_state = 0

    def i_save_state(self) -> None:
        """Saves the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restores a state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """For double checking results."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the addition of the values."""
        val1 = stsv.get_input_value(self.input1)
        val2 = stsv.get_input_value(self.input2)
        val3 = stsv.get_input_value(self.input3)
        stsv.set_output_value(self.output1, val1 + val2 + val3)
