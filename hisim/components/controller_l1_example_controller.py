"""Example L1 controller demonstrating a simple hysteresis gas-heater controller.

Provides ``SimpleController`` and ``SimpleControllerConfig`` as a reference
level-1 controller that toggles a gas heater on/off based on a storage
fill-level threshold.
"""

# Generic/Built-in

from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class SimpleControllerConfig(ConfigBase):
    """Configuration of the example L1 controller: nothing but the controller's identity.

    The two fill levels the controller switches on are class constants of
    :class:`SimpleController`, not fields, so there is nothing to configure and the one preset
    the class ships::

        SimpleControllerConfig.preset_standard("SimpleController")

    takes the instance name and nothing else. ``standard`` is the name because a controller
    with no parameter has nothing to describe.
    """

    MAIN_CLASS = "hisim.components.controller_l1_example_controller.SimpleController"

    #: Structured identity (name, building, unit) of this controller.
    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "SimpleControllerConfig":
        """The only configuration this controller has: its identity.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has neither a plain field beside the
            identity nor a sizable one.
        """
        return cls(component_id=ComponentID(name=name))


class SimpleController(Component):
    """Example L1 controller that toggles a gas heater via storage fill-level hysteresis.

    Reads a storage fill-level percentage input and sets a gas-heater power
    output to 1 (on) when the level drops below the low threshold and to 0
    (off) when it exceeds the high threshold.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    StorageFillLevel: str = "FillLevelPercent"
    GasHeaterPowerPercent: str = "GasHeaterPowerLevel"

    FILL_LEVEL_LOW_THRESHOLD: float = 0.4
    FILL_LEVEL_HIGH_THRESHOLD: float = 0.99

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleControllerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the controller and register its input/output channels.

        Args:
            my_simulation_parameters: Parameters of the current simulation.
            config: Configuration providing the structured component identity.
            my_display_config: Optional display configuration; defaults to a new
                DisplayConfig when None.
        """

        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.storage_fill_level_channel: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.StorageFillLevel,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            True,
        )
        self.gas_heater_power_channel: ComponentOutput = self.add_output(
            self.component_name,
            SimpleController.GasHeaterPowerPercent,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Requested gas heater power level in percent (0 = off, 100 = full power).",
        )
        self.heater_state: int = 0
        self.previous_heater_state: int = self.heater_state

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_heater_state = self.heater_state

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.heater_state = self.previous_heater_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate one time step: read fill level and set gas-heater power output.

        When the storage fill level is below the low threshold the heater state
        is set to 1 (on); when above the high threshold it is set to 0 (off).
        The state is written to the gas-heater power output channel.

        Args:
            timestep: Current simulation time-step index.
            stsv: Container for the current step's input and output values.
            force_convergence: If True, skip computation and return immediately.
        """

        if force_convergence:
            return
        fill_level = stsv.get_input_value(self.storage_fill_level_channel)
        if fill_level < SimpleController.FILL_LEVEL_LOW_THRESHOLD:
            self.heater_state = 1
        if fill_level > SimpleController.FILL_LEVEL_HIGH_THRESHOLD:
            self.heater_state = 0
        stsv.set_output_value(self.gas_heater_power_channel, self.heater_state)
