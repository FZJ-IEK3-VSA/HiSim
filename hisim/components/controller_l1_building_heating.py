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
from __future__ import annotations

import importlib
from dataclasses import dataclass

# Owned
from typing import List

from dataclasses_json import dataclass_json

from hisim import utils
from hisim import component as cp
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
    """Configuration of Building Controller."""

    building_name: str
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
    def get_default_config_heating(
        name: str,
        building_name: str = "BUI1",
    ) -> L1BuildingHeatingConfig:
        """Default config for the heating controller."""
        config = L1BuildingHeatingConfig(
            building_name=building_name,
            name="L1BuildingTemperatureController" + name,
            source_weight=1,
            t_min_heating_in_celsius=19.5,
            t_max_heating_in_celsius=20.5,
            t_buffer_activation_threshold_in_celsius=40.0,
            day_of_heating_season_begin=270,
            day_of_heating_season_end=150,
        )
        return config


class L1BuildingHeatControllerState:
    """Data class that saves the state of the controller."""

    def __init__(self, heating_percentage: float = 0):
        """Initialize the controller state.

        Args:
            heating_percentage: Initial heating control percentage (0-1).
        """
        self.heating_percentage: float = heating_percentage

    def clone(self) -> "L1BuildingHeatControllerState":
        """Create a copy of the current state.

        Returns:
            A new L1BuildingHeatControllerState instance with the same heating percentage.
        """
        return L1BuildingHeatControllerState(heating_percentage=self.heating_percentage)


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
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the L1 building heat controller.

        Args:
            my_simulation_parameters: Simulation parameters providing timestep info.
            config: Configuration holding set temperatures, heating-season bounds, and source weight.
            my_display_config: Display configuration for the component.

        Raises:
            ValueError: If `config` is not an `L1BuildingHeatingConfig` instance.
        """
        if not isinstance(config, L1BuildingHeatingConfig):
            raise ValueError("Wrong config class.")
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.config: L1BuildingHeatingConfig = config

        """ Initializes the class. """
        self.source_weight: int = config.source_weight
        self.heating_season_begin = (
            config.day_of_heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        )
        self.heating_season_end = (
            config.day_of_heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
        )
        self.state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()
        self.previous_state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()
        self.processed_state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()

        # Component Outputs
        self.heat_controller_target_percentage_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatControllerTargetPercentage,
            LoadTypes.ON_OFF,
            Units.BINARY,
            output_description="Heating controller of buffer storage.",
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
        self.add_default_connections(self.get_default_connections_from_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_ems())

    def get_building_default_connections(self) -> List[cp.ComponentConnection]:
        """Sets the default connections for the building."""

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

    def get_default_connections_from_ems(self) -> List[cp.ComponentConnection]:
        """Sets the default connections for the energy management system."""

        connections = []
        ems_classname = controller_l2_energy_management_system.L2GenericEnergyManagementSystem.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BuildingTemperatureModifier,
                ems_classname,
                controller_l2_energy_management_system.L2GenericEnergyManagementSystem.BuildingIndoorTemperatureModifier,
            )
        )
        return connections

    def get_default_connections_from_hot_water_storage(self) -> List[cp.ComponentConnection]:
        """Sets default connections for the buffer."""
        # use importlib for importing the other component in order to avoid circular-import errors
        storage_module_name = "hisim.components.generic_hot_water_storage_modular"
        storage_module = importlib.import_module(name=storage_module_name)
        storage_class = getattr(storage_module, "HotWaterStorage")
        connections = []
        storage_classname = storage_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BufferTemperature,
                storage_classname,
                storage_class.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def _control_heating(
        self,
        timestep: int,
        building_temperature_in_celsius: float,
        t_buffer_in_celsius: float,
        temperature_modifier_in_celsius: float,
    ) -> None:
        """Set the controller state based on building/buffer temperatures and EMS modifier.

        Args:
            timestep: Current simulation timestep.
            building_temperature_in_celsius: Mean building temperature in °C.
            t_buffer_in_celsius: Buffer storage temperature in °C (0 if unconnected).
            temperature_modifier_in_celsius: EMS-provided temperature offset in °C; >0 signals surplus heating.

        The controller sets `self.state.heating_percentage` to one of {0, 0.5, 0.75, 1}:
          - 0: heating off (summer, or building above upper target).
          - 1: full power (building below lower target).
          - 0.75 / 0.5: partial surplus heating when buffer is hot and modifier > 0.
        """
        # prevent heating in summer
        if self.heating_season_begin > timestep > self.heating_season_end:
            self.state.heating_percentage = 0
            return
        # activate heating when building temperature is below lower threshold
        if building_temperature_in_celsius < self.config.t_min_heating_in_celsius:
            # start heating if temperature goes below lower limit
            self.state.heating_percentage = 1
            return
        # deactivate heating when building temperature is above upper threshold
        if building_temperature_in_celsius > self.config.t_max_heating_in_celsius + temperature_modifier_in_celsius:
            self.state.heating_percentage = 0
            return
        # deactivate heating when temperature modifier is zero and signal comes from surplus control.
        # states 0.5 and 0.75 are only activated when temperature modifier is greater than zero, which is only the case in surplus control.
        if self.state.heating_percentage in [0.5, 0.75] and temperature_modifier_in_celsius == 0:
            self.state.heating_percentage = 0
            return
        # "surplus heat control" when storage is getting hot
        if temperature_modifier_in_celsius > 0 and t_buffer_in_celsius > self.config.t_buffer_activation_threshold_in_celsius:
            # heat with 75 % power and building can still be heated
            if building_temperature_in_celsius < self.config.t_max_heating_in_celsius + temperature_modifier_in_celsius / 2:
                self.state.heating_percentage = 0.75
            # heat with 50 % power when storage is getting hot and building can still be heated, but is already on the upper side of the tolerance interval
            elif building_temperature_in_celsius < self.config.t_max_heating_in_celsius + temperature_modifier_in_celsius:
                self.state.heating_percentage = 0.5
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

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate one timestep of building-temperature control.

        Args:
            timestep: Current simulation timestep index.
            stsv: Single-time-step values object for reading inputs and writing outputs.
            force_convergence: If True, skip control logic and hold the current output.
        """
        if force_convergence:
            pass
        else:
            # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
            building_temperature_in_celsius = stsv.get_input_value(self.building_temperature_channel)
            if self.buffer_temperature_channel.source_output is not None:
                t_buffer_in_celsius = stsv.get_input_value(self.buffer_temperature_channel)
            else:
                t_buffer_in_celsius = 0
            temperature_modifier_in_celsius = stsv.get_input_value(self.building_temperature_modifier_channel)
            self._control_heating(
                timestep=timestep,
                building_temperature_in_celsius=building_temperature_in_celsius,
                t_buffer_in_celsius=t_buffer_in_celsius,
                temperature_modifier_in_celsius=temperature_modifier_in_celsius,
            )
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.heat_controller_target_percentage_channel, self.processed_state.heating_percentage)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
