# -*- coding: utf-8 -*-
# clean
""" Generic heating controller with configuration and state.

It controls the heating system (heat transfer from buffer storage to building)
only during the heating period.
It is a ping pong control with an optional input from the Energy Management System,
which enforces heating with electricity from PV.
The buffer is controlled accoring to four modes:
    (a) 0.5 * power when buffer temperature is within the upper half between upper target and increased upper target from Energy Management System (only in surplus case),
    (b) 0.75 * power when buffer temperature is within the lower half beweet upper target and increase upper target from Energy Management System (only in surplus case),
    (c) full power when building temperature is below lower target,
    (d) off when temperature is higher than upper target.
"""

from dataclasses import dataclass

# Owned
from typing import List, Any

from dataclasses_json import dataclass_json

from hisim import utils
from hisim import component as cp
from hisim import log
from hisim.components import generic_hot_water_storage_modular
from hisim.components.building import Building
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
class L1BuildingHeatingConfig(cp.ConfigBase):

    """Configuration of Building Controller. """

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: lower set temperature of building, given in °C
    t_min_heating_in_celsius: float
    #: upper set temperature of building, given in °C
    t_max_heating_in_celsius: float
    #: upper temperature of buffer, where heating of building is enforced.
    t_buffer_activation_threshold_in_celsius: float
    # julian day of simulation year, where heating season begins
    day_of_heating_season_begin: int
    # julian day of simulation year, where heating season ends
    day_of_heating_season_end: int

    @staticmethod
    def get_default_config_heating(name: str) -> Any:
        """ Default config for the heating controller. """
        config = L1BuildingHeatingConfig(name='L1BuildingTemperatureController' + name, source_weight=1, t_min_heating_in_celsius=19.5,
                                         t_max_heating_in_celsius=20.5, t_buffer_activation_threshold_in_celsius=40.0, day_of_heating_season_begin=270,
                                         day_of_heating_season_end=150)
        return config


class L1BuildingHeatControllerState:

    """Data class that saves the state of the controller."""

    def __init__(
        self,
        state: float = 0
    ):
        """Initializes the class."""
        self.state: float = state

    def clone(self) -> "L1BuildingHeatControllerState":
        """Clones itself."""
        return L1BuildingHeatControllerState(
            state=self.state
        )


class L1BuildingHeatController(cp.Component):

    """L1 building controller. Processes signals ensuring comfort temperature of building.

    Gets temperature of building to control as input, as well as a signal from the energy management system to increase the set temperatur of the buffer storage.
    It outputs a control signal with four modes (0, 0.5, 0.75 and 1) for zero, half, three quarter and full power accordingly.
    It is only activated during the heating season.

    Components to connect to:
    (1) Buffer (generic_hot_water_storage_modular)
    (2) Building (building)
    (3) Energy Management System (controller_l2_energy_management_system) - optional
    """

    # Inputs
    BuildingTemperature = "BuildingTemperature"
    BuildingTemperatureModifier = "BuildingTemperatureModifier"
    BufferTemperature = "BufferTemperature"
    # Outputs
    HeatControllerTargetPercentage = "HeatControllerTargetPercentage"

    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: L1BuildingHeatingConfig,
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == L1BuildingHeatingConfig.__name__:
            raise ValueError("Wrong config class.")
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.config: L1BuildingHeatingConfig = config

        """ Initializes the class. """
        self.source_weight: int = config.source_weight
        self.heating_season_begin = config.day_of_heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.heating_season_end = config.day_of_heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        self.state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()
        self.previous_state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()
        self.processed_state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()

        # Component Outputs
        self.heat_controller_target_percentage_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.HeatControllerTargetPercentage, LoadTypes.ON_OFF, Units.BINARY, output_description="Heating controller of buffer storage."
        )

        # Component Inputs
        self.building_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )

        self.buffer_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BufferTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.building_temperature_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.add_default_connections(self.get_building_default_connections())
        self.add_default_connections(
            self.get_default_connections_from_hot_water_storage()
        )
        self.add_default_connections(self.get_default_connections_from_ems())

    def get_building_default_connections(self):
        """Sets the default connections for the building."""
        log.information(
            "setting building default connections in L1 building Controller"
        )
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BuildingTemperature,
                building_classname,
                Building.TemperatureMeanThermalMass,
            )
        )
        return connections

    def get_default_connections_from_ems(self):
        """Sets the default connections for the energy management system."""
        log.information(
            "setting energy management system default connections in L1 building Controller"
        )
        connections = []
        ems_classname = (
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BuildingTemperatureModifier,
                ems_classname,
                controller_l2_energy_management_system.L2GenericEnergyManagementSystem.BuildingTemperatureModifier,
            )
        )
        return connections

    def get_default_connections_from_hot_water_storage(self):
        """Sets default connections for the buffer."""
        log.information("setting buffer default connections in L1 building Controller")
        connections = []
        boiler_classname = (
            generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BufferTemperature,
                boiler_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def control_heating(self, timestep: int, t_control: float, t_buffer: float, temperature_modifier: float) -> None:
        """ Controls the heating from buffer to building. """
        # prevent heating in summer
        if self.heating_season_begin > timestep > self.heating_season_end:
            self.state.state = 0
            return
        # activate heating when building temperature is below lower threshold
        if t_control < self.config.t_min_heating_in_celsius:
            # start heating if temperature goes below lower limit
            self.state.state = 1
            return
        # deactivate heating when building temperature is above upper threshold
        if t_control > self.config.t_max_heating_in_celsius + temperature_modifier:
            self.state.state = 0
            return
        # deactivate heating when temperature modifier is zero and signal comes from surplus control.
        # states 0.5 and 0.75 are only activated when temperature modifier is greater than zero, which is only the case in surplus control.
        if self.state.state in [0.5, 0.75] and temperature_modifier == 0:
            self.state.state = 0
            return
        # "surplus heat control" when storage is getting hot
        if temperature_modifier > 0 and t_buffer > self.config.t_buffer_activation_threshold_in_celsius:
            # heat with 75 % power and building can still be heated
            if t_control < self.config.t_max_heating_in_celsius + temperature_modifier / 2:
                self.state.state = 0.75
            # heat with 50 % power when storage is getting hot and building can still be heated, but is already on the upper side of the tolerance interval
            elif t_control < self.config.t_max_heating_in_celsius + temperature_modifier:
                self.state.state = 0.5
        return

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
        """Simulates the control of the building temperature, when building is heated from buffer."""
        if force_convergence:
            pass
        else:
            # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
            t_control = stsv.get_input_value(self.building_temperature_channel)
            if self.buffer_temperature_channel.source_output is not None:
                t_buffer = stsv.get_input_value(self.buffer_temperature_channel)
            else:
                t_buffer = 0
            temperature_modifier = stsv.get_input_value(
                self.building_temperature_modifier_channel
            )
            self.control_heating(
                timestep=timestep, t_control=t_control, t_buffer=t_buffer, temperature_modifier=temperature_modifier
            )
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.heat_controller_target_percentage_channel, self.processed_state.state)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
