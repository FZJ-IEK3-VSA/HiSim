# -*- coding: utf-8 -*-
# clean
""" Generic heating controller with ping pong control and optional input for energy management system. Runtime and idle time also considered."""

from dataclasses import dataclass

# Owned
from typing import List, Any, Optional

# Generic/Built-in

from dataclasses_json import dataclass_json

from hisim.component import ConfigBase
from hisim import utils
from hisim import component as cp
from hisim import log
from hisim.components import generic_hot_water_storage_modular
from hisim.components import building
from hisim.components import controller_l2_energy_management_system
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class L1HeatPumpConfig(ConfigBase):

    """L1 Controller Config."""
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: lower set temperature of building, given in °C
    t_min_heating_in_celsius: float
    #: upper set temperature of building, given in °C
    t_max_heating_in_celsius: float
    # True if control is only considered in the heating period, False if control is needed during the entire year
    cooling_considered: bool
    # julian day of simulation year, where heating season begins
    day_of_heating_season_begin: int
    # julian day of simulation year, where heating season ends
    day_of_heating_season_end: int
    # minimal operation time of heat source
    min_operation_time_in_seconds: int
    # minimal resting time of heat source
    min_idle_time_in_seconds: int

    @staticmethod
    def get_default_config_heat_source_controller(name: str) -> "L1HeatPumpConfig":
        """ Returns default configuration for the controller of building heating. """
        config = L1HeatPumpConfig(name=name, source_weight=1, t_min_heating_in_celsius=20.0, t_max_heating_in_celsius=22.0,
                                  cooling_considered=True, day_of_heating_season_begin=270, day_of_heating_season_end=150,
                                  min_operation_time_in_seconds=1800, min_idle_time_in_seconds=1800)
        return config

    @staticmethod
    def get_default_config_heat_source_controller_buffer(name: str) -> "L1HeatPumpConfig":
        """Returns default configuration for the controller of buffer heating."""
        # minus - 1 in heating season, so that buffer heats up one day ahead, and modelling to building works.
        config = L1HeatPumpConfig(name=name, source_weight=1, t_min_heating_in_celsius=30.0, t_max_heating_in_celsius=50.0,
                                  cooling_considered=True, day_of_heating_season_begin=270 - 1, day_of_heating_season_end=150,
                                  min_operation_time_in_seconds=1800, min_idle_time_in_seconds=1800)
        return config

    @staticmethod
    def get_default_config_heat_source_controller_dhw(name: str) -> "L1HeatPumpConfig":
        """Returns default configuration for the controller of a drain hot water storage. """
        config = L1HeatPumpConfig(name=name, source_weight=1, t_min_heating_in_celsius=40.0, t_max_heating_in_celsius=60.0,
                                  cooling_considered=False, day_of_heating_season_begin=270, day_of_heating_season_end=150,
                                  min_operation_time_in_seconds=1800, min_idle_time_in_seconds=1800)
        return config


class L1HeatPumpControllerState:

    """Data class that saves the state of the controller."""

    def __init__(
        self,
        on_off: int,
        activation_time_step: int,
        deactivation_time_step: int,
        percentage: float,
    ) -> None:
        """Initializes the heat pump controller state."""
        self.on_off: int = on_off
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step
        self.percentage: float = percentage

    def clone(self) -> "L1HeatPumpControllerState":
        """Copies the current instance."""
        return L1HeatPumpControllerState(
            on_off=self.on_off,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
            percentage=self.percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate(self, timestep: int) -> None:
        """Activates the heat pump and remembers the time step."""
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        self.on_off = 0
        self.deactivation_time_step = timestep


class L1HeatPumpController(cp.Component):

    """L1 building controller. Processes signals ensuring comfort temperature of building/buffer or boiler.

    Gets available surplus electricity and the temperature of the storage or building to control as input,
    and outputs control signal 0/1 for turn off/switch on based on comfort temperature limits and available electricity.
    In addition, run time control is considered, so that e. g. heat pumps do not continuosly turn on and off.
    It is optionally only activated during the heating season.

    """

    # Inputs
    StorageTemperature = "StorageTemperature"
    StorageTemperatureModifier = "StorageTemperatureModifier"
    FlexibileElectricity = "FlexibleElectricity"
    # Outputs
    HeatControllerTargetPercentage = "HeatControllerTargetPercentage"
    OnOffState = "OnOffState"

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: L1HeatPumpConfig
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == L1HeatPumpConfig.__name__:
            raise ValueError("Wrong config class. Got a " + config.__class__.__name__)
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.config: L1HeatPumpConfig = config
        self.minimum_runtime_in_timesteps = int(
            config.min_operation_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            config.min_idle_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        """ Initializes the class. """
        self.source_weight: int = config.source_weight
        self.cooling_considered: bool = config.cooling_considered
        if self.cooling_considered:
            if config.day_of_heating_season_begin is None:
                raise ValueError("Day of heating season begin was None")
            if config.day_of_heating_season_end is None:
                raise ValueError("Day of heating season end was None")
            self.heating_season_begin = (
                config.day_of_heating_season_begin
                * 24
                * 3600
                / self.my_simulation_parameters.seconds_per_timestep
            )
            self.heating_season_end = (
                config.day_of_heating_season_end
                * 24
                * 3600
                / self.my_simulation_parameters.seconds_per_timestep
            )
        self.state: L1HeatPumpControllerState = L1HeatPumpControllerState(0, 0, 0, 0)
        self.previous_state: L1HeatPumpControllerState = self.state.clone()
        self.processed_state: L1HeatPumpControllerState = self.state.clone()

        # Component Outputs
        self.heat_pump_target_percentage_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatControllerTargetPercentage,
            LoadTypes.ANY,
            Units.PERCENT,
        )
        self.on_off_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.OnOffState, LoadTypes.ANY, Units.ANY
        )

        # Component Inputs
        self.storage_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StorageTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )
        self.storage_temperature_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.flexible_electricity_input: cp.ComponentInput = self.add_input(
            self.component_name,
            self.FlexibileElectricity,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            mandatory=False,
        )

        self.add_default_connections(
            self.get_default_connections_generic_hot_water_storage_modular()
        )
        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(
            self.get_default_connections_from_controller_l2_energy_management_system()
        )

    def get_default_connections_from_controller_l2_energy_management_system(self):
        """Sets the default connections for the building."""
        log.information(
            "setting building default connections in L1 building Controller"
        )
        connections = []
        ems_classname = (
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1HeatPumpController.FlexibileElectricity,
                ems_classname,
                controller_l2_energy_management_system.L2GenericEnergyManagementSystem.FlexibleElectricity,
            )
        )
        return connections

    def get_default_connections_generic_hot_water_storage_modular(self):
        """Sets default connections for the boiler."""
        log.information("setting buffer default connections in L1 building Controller")
        connections = []
        boiler_classname = (
            generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1HeatPumpController.StorageTemperature,
                boiler_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_building(self):
        """Sets default connections for the boiler."""
        log.information("setting buffer default connections in L1 building Controller")
        connections = []
        building_classname = building.Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1HeatPumpController.StorageTemperature,
                building_classname,
                building.Building.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """For double checking results."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Core Simulation function."""
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
        else:
            self.calculate_state(timestep, stsv)
            self.processed_state = self.state.clone()
        modulating_signal = self.state.percentage * self.state.on_off
        stsv.set_output_value(
            self.heat_pump_target_percentage_channel, modulating_signal
        )
        stsv.set_output_value(self.on_off_channel, self.state.on_off)

    def calc_percentage(self, t_storage: float, temperature_modifier: float) -> None:
        """Calculate the heat pump target percentage."""
        if t_storage < self.config.t_min_heating_in_celsius:
            self.state.percentage = 1
            return
        if (
            t_storage < self.config.t_max_heating_in_celsius
            and temperature_modifier == 0
        ):
            self.state.percentage = 0.75
            return
        if (
            t_storage >= self.config.t_max_heating_in_celsius
            and temperature_modifier == 0
        ):
            self.state.percentage = 0.5
            return
        t_max_target = self.config.t_min_heating_in_celsius + temperature_modifier
        if t_storage < t_max_target:
            self.state.percentage = 0.5
            return

    def calculate_state(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Calculate the heat pump state and activate / deactives."""
        t_storage = stsv.get_input_value(self.storage_temperature_channel)
        temperature_modifier = stsv.get_input_value(
            self.storage_temperature_modifier_channel
        )
        # return device on if minimum operation time is not fulfilled and device was on in previous state
        if (
            self.state.on_off == 1
            and self.state.activation_time_step + self.minimum_runtime_in_timesteps
            >= timestep
        ):
            # mandatory on, minimum runtime not reached
            self.calc_percentage(t_storage, temperature_modifier)
            return
        if (
            self.state.on_off == 0
            and self.state.deactivation_time_step
            + self.minimum_resting_time_in_timesteps
            >= timestep
        ):
            # mandatory off, minimum resting time not reached
            self.calc_percentage(t_storage, temperature_modifier)
            return
        # check signals and turn on or off if it is necessary
        t_min_target = self.config.t_min_heating_in_celsius + temperature_modifier
        # prevent heating in summer
        if self.cooling_considered:
            if (
                self.heating_season_begin > timestep > self.heating_season_end
                and t_storage >= t_min_target - 40
            ):
                self.state.deactivate(timestep)
                return
        if t_storage < t_min_target:
            self.state.activate(timestep)
            self.calc_percentage(t_storage, temperature_modifier)
            return
        t_max_target = self.config.t_max_heating_in_celsius + temperature_modifier
        if t_storage > t_max_target:
            self.calc_percentage(t_storage, temperature_modifier)
            self.state.deactivate(timestep)
            return
        if temperature_modifier > 0 and t_storage < self.config.t_max_heating_in_celsius:
            self.state.activate(timestep)
            self.calc_percentage(t_storage, temperature_modifier)
            return


    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        lines: List[str] = []
        lines.append(f"Name: {self.component_name + str(self.config.source_weight)}")
        lines.append(self.config.get_string_dict())  # type: ignore
        return lines
