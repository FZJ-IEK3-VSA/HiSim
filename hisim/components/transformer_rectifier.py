"""Example Transformer."""
from __future__ import annotations

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput, DisplayConfig
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.component import ConfigBase


@dataclass_json
@dataclass
class TransformerConfig(ConfigBase):
    """Configuration of the Example Transformer.

    Attributes
    ----------
    efficiency : float
        Conversion efficiency of the transformer/rectifier, expressed as a
        dimensionless fraction in the range [0, 1] (e.g. ``0.95`` for 95 %).
        It is applied as a direct multiplicative scalar on the input power,
        so passing a percentage (e.g. ``95``) would silently scale the output
        by 100x. Use a fraction, not a percentage.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return str(Transformer.get_full_classname())

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    building_name: str
    name: str
    efficiency: float  # conversion efficiency as a fraction in [0, 1] (not a percentage)

    @classmethod
    def get_default_transformer(cls) -> TransformerConfig:
        """Gets a default Transformer."""
        return TransformerConfig(building_name="BUI1", name="Generic Transformer and rectifier Unit", efficiency=0.95)


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

    TransformerInput: str = "Input1"
    TransformerOutput: str = "MyTransformerOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: TransformerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.transformerconfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        if my_display_config is None:
            my_display_config = DisplayConfig()
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.input1: ComponentInput = self.add_input(
            self.component_name,
            Transformer.TransformerInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        self.output1: ComponentOutput = self.add_output(
            self.component_name,
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

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
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
