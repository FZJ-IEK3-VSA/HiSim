"""Example L1 controller demonstrating a simple hysteresis gas-heater controller.

Provides ``SimpleController`` and ``SimpleControllerConfig`` as a reference
level-1 controller that toggles a gas heater on/off based on a storage
fill-level threshold.
"""

# clean

# Generic/Built-in

from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput, ConfigBase, DisplayConfig
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class SimpleControllerConfig(ConfigBase):
    """Configuration dataclass for the SimpleController example controller.

    Attributes:
        building_name: Identifier for the building this controller belongs to.
        name: Human-readable name of the controller instance.
    """

    building_name: str
    name: str

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        return str(SimpleController.get_full_classname())

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleControllerConfig":
        """Returns default config."""
        config = SimpleControllerConfig(name="SimpleController", building_name=building_name)
        return config


class SimpleController(Component):
    """Example L1 controller that toggles a gas heater via storage fill-level hysteresis.

    Reads a storage fill-level percentage input and sets a gas-heater power
    output to 1 (on) when the level drops below the low threshold and to 0
    (off) when it exceeds the high threshold.
    """

    StorageFillLevel: str = "Fill Level Percent"
    GasHeaterPowerPercent: str = "Gas Heater Power Level"

    FILL_LEVEL_LOW_THRESHOLD: float = 0.4
    FILL_LEVEL_HIGH_THRESHOLD: float = 0.99

    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        config: SimpleControllerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the controller and register its input/output channels.

        Args:
            name: Name of the controller instance.
            my_simulation_parameters: Parameters of the current simulation.
            config: Configuration providing the building name and component name.
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
            lt.LoadTypes.GAS,
            lt.Units.PERCENT,
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
