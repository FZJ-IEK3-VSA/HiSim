""" Contains functions to sum up multiple inputs. """

from dataclasses import dataclass
from typing import Any, ClassVar, List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import Component
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class SumBuilderConfig(ConfigBase):
    """Configuration of a sum builder: the quantity it adds up, and the unit that quantity is in.

    The same configuration serves all three components of this module, each of which adds its
    inputs and writes one output::

        SumBuilderConfig.preset_standard("Sum")

    builds an adder that neither states nor checks a quantity -- ``ANY`` over ``ANY`` -- which is
    what the sum of two arbitrary series is. A sum of electrical power says so instead, by
    overriding the two fields with ``ELECTRICITY`` and ``WATT``. Both fields reach every input and
    the output alike, so that the framework refuses at wiring time to add a kilowatt to a degree:
    a connected ``ComponentInput`` and ``ComponentOutput`` must agree on load type and unit (see
    ``ComponentWrapper.connect_inputs``).
    """

    MAIN_CLASS = "hisim.components.sumbuilder.SumBuilderForTwoInputs"

    component_id: ComponentID
    #: Physical quantity every input and the output carry. ``ANY`` adds up whatever it is fed.
    loadtype: lt.LoadTypes = lt.LoadTypes.ANY
    #: Unit every input and the output are in. ``ANY`` adds up whatever it is fed.
    unit: lt.Units = lt.Units.ANY

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "SumBuilderConfig":
        """Adder of an unstated quantity: ``ANY`` over ``ANY``, the field defaults.

        This is the sum builder of the example setups, which add two series of plain numbers.

        Args:
            name: Instance name of the sum builder in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class CalculateOperation(cp.Component):
    """Arbitrary mathematical operations."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Arithmetic over other components' outputs: it owns no device, so there is nothing to
    # buy and nothing to run. See Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    operations_available: List[str] = ["Sum", "Subtract", "Multiply", "Divide"]
    Output: str = "Output"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
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
            self.component_name, self.Output, config.loadtype, config.unit, output_description="Result of the calculation"
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
                raise ValueError("Operation not implemented!")
        elif num_inputs >= num_operations + 1:
            raise ValueError(
                f"Inputs connected without operation! {num_inputs - (num_operations + 1)} operations are missing!"
            )
        else:
            raise ValueError(
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
        """Simulates.

        ``val1_in_config_unit`` (each input value) and ``total_in_config_unit``
        (the running result) carry values in ``self.config.unit`` — the
        single source of truth for the physical unit.  The unit is
        runtime-determined, so the variable names use the ``_in_config_unit``
        suffix instead of a concrete unit.
        """
        total_in_config_unit: float = 0
        for index, input_channel in enumerate(self.inputs):
            val1_in_config_unit = stsv.get_input_value(input_channel)
            if index == 0:
                total_in_config_unit = val1_in_config_unit
            elif self.operations[index - 1] == "Sum":
                total_in_config_unit = total_in_config_unit + val1_in_config_unit
            elif self.operations[index - 1] == "Subtract":
                total_in_config_unit = total_in_config_unit - val1_in_config_unit
            elif self.operations[index - 1] == "Multiply":
                total_in_config_unit = total_in_config_unit * val1_in_config_unit
            elif self.operations[index - 1] == "Divide":
                total_in_config_unit = total_in_config_unit / val1_in_config_unit
            else:
                raise ValueError("Operation invalid!")
        stsv.set_output_value(self.output1, total_in_config_unit)


class SumBuilderForTwoInputs(Component):
    """Sums two component inputs into a single output.

    Reads two inputs of the same load type and unit, adds them at each
    time step, and writes the result to one output channel.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Arithmetic over other components' outputs: it owns no device, so there is nothing to
    # buy and nothing to run. See Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    SumInput1: str = "Input1"
    SumInput2: str = "Input2"
    SumOutput: str = "Sum"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
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
        """Adds the two values.

        ``val1_in_config_unit`` and ``val2_in_config_unit`` carry values in
        ``self.config.unit`` — the single source of truth for the physical
        unit.  The unit is runtime-determined, so the variable names use the
        ``_in_config_unit`` suffix instead of a concrete unit.
        """
        val1_in_config_unit = stsv.get_input_value(self.input1)
        val2_in_config_unit = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, val1_in_config_unit + val2_in_config_unit)

    def write_to_report(self) -> List[str]:
        """Writes information to the report."""
        lines = []
        lines.append(f"Sumbuilder for two inputs: {self.component_name}")
        lines.append(f"Input 1: {self.input1.fullname}")
        lines.append(f"Input 2: {self.input2.fullname}")
        return lines


class SumBuilderForThreeInputs(Component):
    """Sum builder for three inputs."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Arithmetic over other components' outputs: it owns no device, so there is nothing to
    # buy and nothing to run. See Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    SumInput1: str = "Input1"
    SumInput2: str = "Input2"
    SumInput3: str = "Input3"
    SumOutput: str = "Sum"

    def __init__(
        self,
        config: SumBuilderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
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
            output_description="Sum of values",
        )

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
        """Performs the addition of the values.

        ``val1_in_config_unit``, ``val2_in_config_unit`` and
        ``val3_in_config_unit`` carry values in ``self.config.unit`` — the
        single source of truth for the physical unit.  The unit is
        runtime-determined, so the variable names use the ``_in_config_unit``
        suffix instead of a concrete unit.
        """
        val1_in_config_unit = stsv.get_input_value(self.input1)
        val2_in_config_unit = stsv.get_input_value(self.input2)
        val3_in_config_unit = stsv.get_input_value(self.input3)
        stsv.set_output_value(self.output1, val1_in_config_unit + val2_in_config_unit + val3_in_config_unit)
