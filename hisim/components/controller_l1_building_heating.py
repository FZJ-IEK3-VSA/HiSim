# -*- coding: utf-8 -*-
# clean
""" Generic heating controller. """

from dataclasses import dataclass

# Owned
from typing import List, Optional, Any

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

    """L2 Controller Config."""

    name: str
    source_weight: int
    t_min_heating_in_celsius: float
    t_max_heating_in_celsius: float
    cooling_considered: bool
    t_min_cooling_in_celsius: Optional[float]
    t_max_cooling_in_celsius: Optional[float]
    day_of_heating_season_begin: Optional[int]
    day_of_heating_season_end: Optional[int]

    @staticmethod
    def get_default_config_heating(name: str) -> Any:
        """ Default config for the heating controller. """
        config = L1BuildingHeatingConfig(name='L1 Building TemperatureController' + name, source_weight=1, t_min_heating_in_celsius=20.0, t_max_heating_in_celsius=22.0,
                                         cooling_considered=False, t_min_cooling_in_celsius=23, t_max_cooling_in_celsius=25, day_of_heating_season_begin=270,
                                         day_of_heating_season_end=150)
        return config


class L1BuildingHeatControllerState:

    """Data class that saves the state of the controller."""

    def __init__(
        self,
        timestep_actual: int = -1,
        state: int = 0,
        compulsory: int = 0,
        count: int = 0,
    ):
        """Initializes the class."""
        self.timestep_actual: int = timestep_actual
        self.state: int = state
        self.compulsory: int = compulsory
        self.count: int = count

    def clone(self):
        """Clones itself."""
        return L1BuildingHeatControllerState(
            timestep_actual=self.timestep_actual,
            state=self.state,
            compulsory=self.compulsory,
            count=self.count,
        )

    def is_first_iteration(self, timestep):
        """Only called for first iteration."""
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            self.compulsory = 0
            self.count = 0
            return True
        return False

    def is_compulsory(self):
        """Returns compulsory value."""
        if self.count <= 1:
            self.compulsory = 0
        else:
            self.compulsory = 1

    def activate(self):
        """Ativates."""
        self.state = 1
        self.compulsory = 1
        self.count += 1

    def deactivate(self):
        """Deactivates the control signal."""
        self.state = 0
        self.compulsory = 1
        self.count += 1


class L1BuildingHeatController(cp.Component):

    """L1 building controller. Processes signals ensuring comfort temperature of building.

    Gets available surplus electricity and the temperature of the storage or building to control as input,
    and outputs control signal 0/1 for turn off/switch on based on comfort temperature limits and available electricity.
    It optionally has different modes for cooling and heating selected by the time of the year.

    Parameters
    ----------
    source_weight : int, optional
        Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
    T_min_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 19 °C.
    T_max_heating: float, optional
        Maximum comfortable temperature for residents during heating period, in °C. The default is 23 °C.
    T_tolerance : float, optional
        Temperature difference the building may go below or exceed the comfort temperature band with, because of recommendations from L3.
        The default is 1 °C.

    """

    # Inputs
    BuildingTemperature = "BuildingTemperature"
    BuildingTemperatureModifier = "BuildingTemperatureModifier"
    # Outputs
    boiler_signal = "l2_DeviceSignal"

    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump

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
        self.state: L1BuildingHeatControllerState = L1BuildingHeatControllerState()
        self.previous_state: L1BuildingHeatControllerState = (
            L1BuildingHeatControllerState()
        )

        # Component Outputs
        self.l2_device_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.boiler_signal, LoadTypes.ON_OFF, Units.BINARY
        )

        # Component Inputs
        self.building_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
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
                Building.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_ems(self):
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
                L1BuildingHeatController.BuildingTemperatureModifier,
                ems_classname,
                controller_l2_energy_management_system.L2GenericEnergyManagementSystem.BuildingTemperatureModifier,
            )
        )
        return connections

    def get_default_connections_from_hot_water_storage(self):
        """Sets default connections for the boiler."""
        log.information("setting buffer default connections in L1 building Controller")
        connections = []
        boiler_classname = (
            generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1BuildingHeatController.BuildingTemperature,
                boiler_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def control_heating(self, t_control: float, t_min_heating: float, t_max_heating: float) -> None:
        """ Controlls the building heating. """
        if t_control > t_max_heating:
            self.previous_state.state = 0
            return
        if t_control < t_min_heating:
            # start heating if temperature goes below lower limit
            self.previous_state.state = 1

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
            return
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        t_control = stsv.get_input_value(self.building_temperature_channel)
        temperature_modifier = stsv.get_input_value(
            self.building_temperature_modifier_channel
        )
        t_min_target = self.config.t_min_heating_in_celsius + temperature_modifier
        t_max_target = self.config.t_max_heating_in_celsius + temperature_modifier
        self.control_heating(
            t_control=t_control, t_min_heating=t_min_target, t_max_heating=t_max_target
        )
        stsv.set_output_value(self.l2_device_signal_channel, self.state.state)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        lines: List[str] = []
        lines.append(f"Name: {self.component_name + str(self.config.source_weight)}")
        lines.append(self.config.get_string_dict())  # type: ignore
        return lines
