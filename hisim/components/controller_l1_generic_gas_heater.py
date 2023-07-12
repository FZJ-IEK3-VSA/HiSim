"""Controller for generic_gas_heater according to advanced_heat_pump-Controller."""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import Any, List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim import log
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ConfigBase,
    ComponentConnection,
)
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.simple_hot_water_storage import SimpleHotWaterStorage
from hisim.components.weather import Weather
from hisim.components.heat_distribution_system import HeatDistributionController

# from hisim.components.generic_gas_heater import GasHeater

__authors__ = "Markus Blasberg"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Markus Blasberg"
__email__ = "m.blasberg@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class GenericGasHeaterControllerL1Config(ConfigBase):

    """Gas-heater Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GenericGasHeaterControllerL1.get_full_classname()

    name: str
    mode: int
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    minimal_thermal_power_in_watt: float  # [W]
    maximal_thermal_power_in_watt: float  # [W]
    set_temperature_difference_for_full_power: float

    @classmethod
    def get_default_generic_gas_heater_controller_config(cls):
        """Gets a default Generic Heat Pump Controller."""
        return GenericGasHeaterControllerL1Config(
            name="GenericGasHeaterController",
            mode=1,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            minimal_thermal_power_in_watt=1_000,  # [W] # Todo: get information from GasHeater.gasheater_config.minimal_thermal_power_in_watt
            maximal_thermal_power_in_watt=12_000,  # [W] # Todo: get information fromGasHeater.gasheater_config.maximal_thermal_power_in_watt
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )


class GenericGasHeaterControllerL1(Component):

    """Gas Heater Controller based on HeatPumpHplibControllerL1 (in advanced_heat_oump_hplib).

    It takes data from other
    components and sends signal to the generic_gas_heater for
    activation or deactivation.
    Modulating Power with respect to water temperature from storage.

    Parameters
    ----------
    mode : int
        Mode index for operation type for this heat pump--> should be 1 only for gas_heater


    Components to connect to:
    (1) generic_gas_heater (control_signal)

    """

    # Inputs
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )

    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = (
        "HeatingFlowTemperatureFromHeatDistributionSystem"
    )

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    ControlSignalToGasHeater = "ControlSignalToGasHeater"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericGasHeaterControllerL1Config,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.gas_heater_controller_config = config
        super().__init__(
            self.gas_heater_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.build(
            mode=self.gas_heater_controller_config.mode,
        )

        # input channel
        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.DailyAverageOutsideTemperature,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        )

        self.control_signal_to_gasheater_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToGasHeater,
            LoadTypes.ANY,
            Units.PERCENT,
            output_description=f"here a description for {self.ControlSignalToGasHeater} will follow.",
        )

        self.controller_gasheatermode: Any
        self.previous_gasheater_mode: Any

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )
        self.add_default_connections(
            self.get_default_connections_from_heat_distribution_controller()
        )

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_hot_water_storage default connections."""
        log.information("setting simple_hot_water_storage default connections")
        connections = []
        storage_classname = SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                GenericGasHeaterControllerL1.WaterTemperatureInputFromHeatWaterStorage,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_weather(
        self,
    ):
        """Get simple_hot_water_storage default connections."""
        log.information("setting weather default connections")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                GenericGasHeaterControllerL1.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_controller(
        self,
    ):
        """Get heat distribution controller default connections."""
        log.information("setting heat distribution controller default connections")
        connections = []
        hds_controller_classname = HeatDistributionController.get_classname()
        connections.append(
            ComponentConnection(
                GenericGasHeaterControllerL1.HeatingFlowTemperatureFromHeatDistributionSystem,
                hds_controller_classname,
                HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def build(
        self,
        mode: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_gasheatermode = "off"
        self.previous_gasheater_mode = self.controller_gasheatermode

        # Configuration
        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_gasheater_mode = self.controller_gasheatermode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_gasheatermode = self.previous_gasheater_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.gas_heater_controller_config.get_string_dict()

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the Gas Heater comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_from_heat_water_storage_in_celsius = (
                stsv.get_input_value(self.water_temperature_input_channel)
            )

            heating_flow_temperature_from_heat_distribution_system = (
                stsv.get_input_value(
                    self.heating_flow_temperature_from_heat_distribution_system_channel
                )
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            # turning gas_heater off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.gas_heater_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # mode 1 is on/off controller
            if self.mode == 1:
                self.conditions_on_off(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    summer_heating_mode=summer_heating_mode,
                )

            else:
                raise ValueError("Gas Heater has no cooling. Set mode==1")

            if self.controller_gasheatermode == "heating":
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            elif self.controller_gasheatermode == "off":
                control_signal = 0
            else:
                raise ValueError("Gas Heater Controller control_signal unknown.")

            stsv.set_output_value(
                self.control_signal_to_gasheater_channel, control_signal
            )

    def modulate_power(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linear between minimial_thermal_power and max_thermal_power of Gas Heater.

        only used if gasheatermode is "heating".
        """

        minimal_percentage = (
            self.gas_heater_controller_config.minimal_thermal_power_in_watt
            / self.gas_heater_controller_config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius
            - self.gas_heater_controller_config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage
        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1 - (
                (
                    self.gas_heater_controller_config.set_temperature_difference_for_full_power
                    - (
                        set_heating_flow_temperature_in_celsius
                        - water_temperature_input_in_celsius
                    )
                )
                / self.gas_heater_controller_config.set_temperature_difference_for_full_power
            )
            percentage = max(minimal_percentage, linear_fit)
            return percentage
        if (
            water_temperature_input_in_celsius
            <= set_heating_flow_temperature_in_celsius + 0.5
        ):  # use same hysteresis like in conditions_on_off()
            percentage = minimal_percentage
            return percentage

        # if something went wrong
        raise ValueError("modulation of Gas Heater needs some adjustments")

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> None:
        """Set conditions for the gas heater controller mode."""

        if self.controller_gasheatermode == "heating":
            if (
                water_temperature_input_in_celsius
                > (set_heating_flow_temperature_in_celsius + 0.5)
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_gasheatermode = "off"
                return

        elif self.controller_gasheatermode == "off":
            # gas heater is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius
                < (set_heating_flow_temperature_in_celsius - 1.0)
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_gasheatermode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the gas_heater."""

        # if no heating threshold is set, the gas_heater is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "off"

        # it is cold enough for heating
        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
        return heating_mode
