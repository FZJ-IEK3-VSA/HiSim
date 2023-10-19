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
class TransformerConfig(ConfigBase):

    """Configuration of the Example Transformer."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Transformer.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    efficiency: float

    @classmethod
    def get_default_transformer(cls):
        """Gets a default Transformer."""
        return TransformerConfig(
            name="Generic Transformer and rectifier Unit", efficiency=0.95
        )


class Transformer(Component):

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
    TransformerOutput = "MyTransformerOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: TransformerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.transformerconfig = config
        super().__init__(
            self.transformerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.input1: ComponentInput = self.add_input(
            self.transformerconfig.name,
            Transformer.TransformerInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        self.output1: ComponentOutput = self.add_output(
            self.transformerconfig.name,
            Transformer.TransformerOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            postprocessing_flag=[lt.InandOutputType.ELECTRICITY_PRODUCTION],
            output_description="Output 1",
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
        # print(f"Input from CSV: {startval_1}")
        efficiency = self.transformerconfig.efficiency
        # print(f"individual efficiency: {efficiency}")

        stsv.set_output_value(self.output1, float(startval_1 * efficiency))

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        lines.append("Transformer: " + self.component_name)
        return lines
