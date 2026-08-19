"""The ``template`` module.

It serves as a template for creating new component modules.
It shows with a simplified example which steps are necessary to create a new component.
Additionally it contains examples for doc strings according to the sphinx format.

"""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import ComponentID, Component, ComponentInput, ComponentOutput, SingleTimeStepValues, DisplayConfig
from hisim import loadtypes
from hisim.simulationparameters import SimulationParameters
from hisim.component import ConfigBase

__authors__ = "Tjarko Tjaden, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class ComponentNameConfig(ConfigBase):
    """Configuration of the ComponentName."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return ComponentName.get_full_classname()

    component_id: ComponentID
    loadtype: loadtypes.LoadTypes
    unit: loadtypes.Units

    @classmethod
    def get_default_template_component(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "ComponentNameConfig":
        """Gets a default ComponentName."""
        if component_id is None:
            component_id = ComponentID(name="ComponentName default")
        return ComponentNameConfig(
            component_id=component_id,
            loadtype=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
        )


class ComponentName(Component):
    """Example template component showing how to build a new HiSim component.

    This class is a simplified template demonstrating the steps required to
    create a new component module (config, inputs/outputs, state, simulate).
    It has no functional purpose in HiSim beyond serving as a reference.

    Attributes:
        InputFromOtherComponent: Name of the input field read from another component.
        OutputWithState: Name of the output field whose value is held in state.
        OutputWithoutState: Name of the stateless output field.
    """

    # Inputs
    InputFromOtherComponent: str = "InputFromState"

    # Outputs
    OutputWithState: str = "OutputWithState"
    OutputWithoutState: str = "OutputWithoutState"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ComponentNameConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Initialize the ComponentName template component.

        Args:
            my_simulation_parameters: Simulation parameters for the current run.
            config: :py:class:`ComponentNameConfig` providing name, loadtype and unit.
            my_display_config: Optional display configuration; defaults to a new
                :py:class:`DisplayConfig` when ``None``.
        """
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.componentnameconfig: ComponentNameConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: ComponentNameConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # If a component requires states, this can be implemented here.
        self.state: "ComponentNameState" = ComponentNameState()
        self.previous_state: "ComponentNameState" = deepcopy(self.state)
        # Initialized variables
        self.factor: float = 1.0

        self.input_from_other_component: ComponentInput = self.add_input(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.InputFromOtherComponent,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            mandatory=True,
        )

        self.output_with_state: ComponentOutput = self.add_output(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.OutputWithState,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT_HOUR,
            output_description="Output with State",
        )

        self.output_without_state: ComponentOutput = self.add_output(
            object_name=self.componentnameconfig.component_id.name,
            field_name=self.OutputWithoutState,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT_HOUR,
            output_description="Output without State",
        )

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = ComponentNameState(output_with_state=self.state.output_with_state)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = ComponentNameState(output_with_state=self.previous_state.output_with_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """No-op hook for optional post-simulation consistency checks.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values for the current timestep.
        """
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Compute outputs for the current timestep.

        Reads the external input and the previous in-state output, computes the
        two outputs (one accumulated into state, one stateless) and writes them
        back to ``stsv`` and to :py:attr:`state`.

        Args:
            timestep: Current simulation timestep index.
            stsv: Container to read inputs from and write outputs to.
            force_convergence: Whether to force convergence (unused in this template).
        """
        # define local variables
        input_1 = stsv.get_input_value(self.input_from_other_component)
        input_2 = self.state.output_with_state

        # do your calculations
        output_1 = input_2 + input_1 * self.my_simulation_parameters.seconds_per_timestep
        output_2 = input_1 + self.factor

        # write values for output time series
        stsv.set_output_value(self.output_with_state, output_1)
        stsv.set_output_value(self.output_without_state, output_2)

        # write values to state
        self.state.output_with_state = output_1


@dataclass
class ComponentNameState:
    """The data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state : int
        Stores the state of the output_with_state value from
        :py:class:`~hisim.component.ComponentName`.

    """

    output_with_state: float = 0
