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
from hisim.loadtypes import Units
from hisim.simulationparameters import SimulationParameters
from hisim.component import ComponentInput, ComponentConnection
from hisim.components import weather


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

    CONSTANTTHERMALPOWER = 1
    CONSTANTTEMPERATURE = 2
    BRINETEMPERATURE = 3


@dataclass_json
@dataclass
class SimpleHeatSourceConfig(cp.ConfigBase):
    """Configuration of a generic HeatSource."""

    building_name: str
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
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatingHeatSourceConstPower",
            const_source=SimpleHeatSourceType.CONSTANTTHERMALPOWER,
            power_th_in_watt=5000.0,
            temperature_in_celsius=5,
        )
        return config

    @classmethod
    def get_default_config_const_temperature(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatingHeatSourceConstTemperature",
            const_source=SimpleHeatSourceType.CONSTANTTEMPERATURE,
            power_th_in_watt=0,
            temperature_in_celsius=5,
        )
        return config

    @classmethod
    def get_default_config_var_brinetemperature(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatingHeatSourceVarBrinetemperature",
            const_source=SimpleHeatSourceType.BRINETEMPERATURE,
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

    # Inputs
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

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
            name=config.building_name + "_" + config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # introduce parameters of district heating
        self.config = config
        self.state = SimpleHeatSourceState()
        self.previous_state = SimpleHeatSourceState()

        # Inputs
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.DailyAverageOutsideTemperature,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )
        # Outputs
        if self.config.const_source == SimpleHeatSourceType.CONSTANTTHERMALPOWER:
            self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalPowerDelivered,
                load_type=lt.LoadTypes.HEATING,
                unit=lt.Units.WATT,
                output_description="Thermal Power Delivered",
            )
        elif self.config.const_source in [SimpleHeatSourceType.CONSTANTTEMPERATURE,
                                          SimpleHeatSourceType.BRINETEMPERATURE]:
            self.temperature_delivered_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.TemperatureDelivered,
                load_type=lt.LoadTypes.TEMPERATURE,
                unit=lt.Units.CELSIUS,
                output_description="Temperature Delivered",
            )

        self.add_default_connections(self.get_default_connections_from_weather())

    def get_default_connections_from_weather(
        self,
    ):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                SimpleHeatSource.DailyAverageOutsideTemperature,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""
        lines = []
        lines.append(f"Name: {self.config.name })")
        lines.append(f"Source: {self.config.const_source})")
        if self.config.const_source == SimpleHeatSourceType.CONSTANTTHERMALPOWER:
            lines.append(f"Power: {self.config.power_th_in_watt * 1e-3:4.0f} kW")
        if self.config.const_source == SimpleHeatSourceType.CONSTANTTEMPERATURE:
            lines.append(f"Temperature : {self.config.temperature_in_celsius} °C")
        if self.config.const_source == SimpleHeatSourceType.BRINETEMPERATURE:
            lines.append("Temperature : .... °C")
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

        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_avg_outside_temperature_input_channel
        )

        if self.config.const_source == SimpleHeatSourceType.CONSTANTTHERMALPOWER:
            stsv.set_output_value(self.thermal_power_delivered_channel, self.config.power_th_in_watt)

        if self.config.const_source == SimpleHeatSourceType.CONSTANTTEMPERATURE:
            stsv.set_output_value(self.temperature_delivered_channel, self.config.temperature_in_celsius)

        if self.config.const_source == SimpleHeatSourceType.BRINETEMPERATURE:
            """From hplib: Calculate the soil temperature by the average Temperature of the day.
            Source: „WP Monitor“ Feldmessung von Wärmepumpenanlagen S. 115, Frauenhofer ISE, 2014
            added 9 points at -15°C average day at 3°C soil temperature in order to prevent higher
            temperature of soil below -10°C."""

            t_brine = (
                -0.0003 * daily_avg_outside_temperature_in_celsius**3
                + 0.0086 * daily_avg_outside_temperature_in_celsius**2
                + 0.3047 * daily_avg_outside_temperature_in_celsius
                + 5.0647
            )

            stsv.set_output_value(self.temperature_delivered_channel, t_brine)
