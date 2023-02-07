"""Example Transformer."""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.component import ConfigBase


@dataclass_json
@dataclass
class ExampleTransformerConfig(ConfigBase):

    """Configuration of the Example Transformer."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ExampleTransformer.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    loadtype: lt.LoadTypes
    unit: lt.Units

    @classmethod
    def get_default_transformer(cls):
        """Gets a default Transformer."""
        return ExampleTransformerConfig(
            name="Example Transformer default",
            loadtype=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
        )


class ExampleTransformer(Component):

    """The Example Transformer class.

    It is used to modify input values and return them as new output values.

    Parameters
    ----------
    component_name : str
        Passed to initialize :py:class:`~hisim.component.Component`.

    loadtype : LoadType
        A :py:class:`~hisim.loadtypes.LoadTypes` object that represents
        the type of the loaded data.

    unit: LoadTypes.Units
        A :py:class:`~hisim.loadtypes.Units` object that represents
        the unit of the loaded data.

    """

    TransformerInput = "Input1"
    TransformerInput2 = "Optional Input1"
    TransformerOutput = "MyTransformerOutput"
    TransformerOutput2 = "MyTransformerOutput2"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ExampleTransformerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.transformerconfig = config
        super().__init__(
            self.transformerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
        )
        self.input1: ComponentInput = self.add_input(
            self.transformerconfig.name,
            ExampleTransformer.TransformerInput,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )
        self.input2: ComponentInput = self.add_input(
            self.transformerconfig.name,
            ExampleTransformer.TransformerInput2,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            False,
        )
        self.output1: ComponentOutput = self.add_output(
            self.transformerconfig.name,
            ExampleTransformer.TransformerOutput,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )
        self.output2: ComponentOutput = self.add_output(
            self.transformerconfig.name,
            ExampleTransformer.TransformerOutput2,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )

    def i_save_state(self) -> None:
        """Saves the current state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates the transformer."""
        startval_1 = stsv.get_input_value(self.input1)
        startval_2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, startval_1 * 5)
        stsv.set_output_value(self.output2, startval_2 * 1000)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        lines.append("Transformer: " + self.component_name)
        return lines
