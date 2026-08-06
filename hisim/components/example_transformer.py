"""Example Transformer."""

# clean

from __future__ import annotations


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
class ExampleTransformerConfig(ConfigBase):
    """Configuration of the Example Transformer."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return str(ExampleTransformer.get_full_classname())

    building_name: str
    name: str
    loadtype: lt.LoadTypes
    unit: lt.Units

    @classmethod
    def get_default_transformer(
        cls,
        building_name: str = "BUI1",
    ) -> ExampleTransformerConfig:
        """Gets a default Transformer."""
        return ExampleTransformerConfig(
            building_name=building_name,
            name="Example Transformer default",
            loadtype=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
        )


class ExampleTransformer(Component):
    """The Example Transformer class.

    It is used to modify input values and return them as new output values.

    Each input is scaled by a fixed factor to produce its corresponding
    output, so the two input/output pairs carry an implicit unit contract:

    - ``output1`` is ``input1`` multiplied by :attr:`OUTPUT1_GAIN` (5). This
      factor is *dimensionless*, therefore ``output1`` keeps the same unit as
      ``input1`` (for example W -> W, or kW -> kW).
    - ``output2`` is ``input2`` multiplied by :attr:`KW_TO_W` (1000). This
      factor converts a value given in kilowatts (kW) into watts (W), i.e.
      ``output2 [W] = input2 [kW] * 1000``.

    The inputs and outputs are declared with :attr:`lt.LoadTypes.ANY` /
    :attr:`lt.Units.ANY` so the transformer can be wired generically, but the
    unit relationships above are the intended contract: a caller must feed
    ``input2`` in kW and read ``output2`` in W, otherwise the result is
    silently wrong. The contract is documented here and in :meth:`i_simulate`
    rather than enforced by the declared types.

    Parameters
    ----------
    my_simulation_parameters : SimulationParameters
        Passed to initialize :py:class:`~hisim.component.Component`.

    config : ExampleTransformerConfig
        The :py:class:`ExampleTransformerConfig` object that holds the
        transformer configuration (name, loadtype, and unit).

    my_display_config : DisplayConfig, optional
        A :py:class:`~hisim.component.DisplayConfig` object that controls
        how the component is displayed in the simulation results.
        Defaults to an empty :py:class:`~hisim.component.DisplayConfig`.

    """

    TransformerInput: str = "Input1"
    TransformerInput2: str = "Optional Input1"
    TransformerOutput: str = "MyTransformerOutput"
    TransformerOutput2: str = "MyTransformerOutput2"

    # Dimensionless gain applied to ``input1`` to produce ``output1``.
    # ``output1`` keeps the same unit as ``input1`` (e.g. W -> W, kW -> kW).
    OUTPUT1_GAIN: float = 5

    # Conversion factor from kilowatts (kW) to watts (W): 1 kW = 1000 W.
    # Used to compute ``output2`` from ``input2``: output2 [W] = input2 [kW] * KW_TO_W.
    KW_TO_W: int = 1000

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ExampleTransformerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Constructs all the necessary attributes."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.transformerconfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
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
            output_description="Output 1",
        )
        self.output2: ComponentOutput = self.add_output(
            self.transformerconfig.name,
            ExampleTransformer.TransformerOutput2,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Output 2",
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
        """Simulates the transformer for the given timestep.

        Reads both inputs and writes the scaled outputs. The unit
        relationship between each input and its corresponding output is:

        - ``output1 = input1 * OUTPUT1_GAIN``. :attr:`OUTPUT1_GAIN` (5) is a
          dimensionless gain, so ``output1`` has the *same* unit as
          ``input1`` (for example W -> W, or kW -> kW).
        - ``output2 = input2 * KW_TO_W``. :attr:`KW_TO_W` (1000) converts a
          value in kilowatts (kW) to watts (W); the contract is therefore
          ``output2 [W] = input2 [kW] * 1000``. Because the declared unit is
          :attr:`lt.Units.ANY`, feeding ``input2`` in the wrong unit or
          reading ``output2`` as kW silently yields incorrect results.

        """
        input_value_1 = stsv.get_input_value(self.input1)
        input_value_2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, input_value_1 * ExampleTransformer.OUTPUT1_GAIN)
        stsv.set_output_value(self.output2, input_value_2 * ExampleTransformer.KW_TO_W)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        lines.append("Transformer: " + self.component_name)
        return lines
