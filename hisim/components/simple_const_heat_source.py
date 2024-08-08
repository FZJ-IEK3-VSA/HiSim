""" Generic Heat Source. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import List
from enum import IntEnum
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""


class SimpleHeatSourceType(IntEnum):
    """Set Heat Source Types."""

    THERMALPOWER = 1
    TEMPERATURE = 2


@dataclass_json
@dataclass
class SimpleHeatSourceConfig(cp.ConfigBase):
    """Configuration of a generic HeatSource."""

    building: str
    name: str
    power_th_in_watt: float
    temperature_in_celsius: float
    const_source: SimpleHeatSourceType

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SimpleHeatSource.get_full_classname()

    @classmethod
    def get_default_config_const_power(
        cls,
        building: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building=building,
            name="HeatingHeatSourceConstPower",
            const_source=SimpleHeatSourceType.THERMALPOWER,
            power_th_in_watt=5000.0,
            temperature_in_celsius=0,
        )
        return config

    @classmethod
    def get_default_config_const_temperature(
        cls,
        building: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building=building,
            name="HeatingHeatSourceConstTemperature",
            const_source=SimpleHeatSourceType.TEMPERATURE,
            power_th_in_watt=0,
            temperature_in_celsius=5,
        )
        return config


class SimpleHeatSourceState:
    """Heat source state class saves the state of the heat source."""

    def __init__(self, state: int = 0):
        """Initializes state."""
        self.state = state

    def clone(self) -> "SimpleHeatSourceState":
        """Creates copy of a state."""
        return SimpleHeatSourceState(state=self.state)


class SimpleHeatSource(cp.Component):
    """Heat Source implementation."""

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    TemperatureDelivered = "TemperatureDelivered"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHeatSourceConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""

        super().__init__(
            name=config.building + "_" + config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # introduce parameters of district heating
        self.config = config
        self.state = SimpleHeatSourceState()
        self.previous_state = SimpleHeatSourceState()

        # Outputs
        if self.config.const_source == SimpleHeatSourceType.THERMALPOWER:
            self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalPowerDelivered,
                load_type=lt.LoadTypes.HEATING,
                unit=lt.Units.WATT,
                output_description="Thermal Power Delivered",
            )
        if self.config.const_source == SimpleHeatSourceType.TEMPERATURE:
            self.temperature_delivered_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureDelivered,
                load_type=lt.LoadTypes.TEMPERATURE,
                unit=lt.Units.CELSIUS,
                output_description="Temperature Delivered",
            )

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""
        lines = []
        lines.append(f"Name: {self.config.name })")
        lines.append(f"Source: {self.config.const_source})")
        if self.config.const_source == SimpleHeatSourceType.THERMALPOWER:
            lines.append(f"Power: {self.config.power_th_in_watt * 1e-3:4.0f} kW")
        if self.config.const_source == SimpleHeatSourceType.TEMPERATURE:
            lines.append(f"Temperature : {self.config.temperature_in_celsius} °C")
        return lines

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the simulation of the heat source model."""

        if self.config.const_source == SimpleHeatSourceType.THERMALPOWER:
            stsv.set_output_value(self.thermal_power_delivered_channel, self.config.power_th_in_watt)

        if self.config.const_source == SimpleHeatSourceType.TEMPERATURE:
            stsv.set_output_value(self.temperature_delivered_channel, self.config.temperature_in_celsius)
