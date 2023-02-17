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
class L2GenericHeatConfig:

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

    def __init__(
        self,
        name: str,
        source_weight: int,
        t_min_heating_in_celsius: float,
        t_max_heating_in_celsius: float,
        cooling_considered: bool,
        t_min_cooling_in_celsius: Optional[float],
        t_max_cooling_in_celsius: Optional[float],
        day_of_heating_season_begin: Optional[int],
        day_of_heating_season_end: Optional[int],
    ):
        """Initializes config."""
        self.name = name
        self.source_weight = source_weight
        self.t_min_heating_in_celsius = t_min_heating_in_celsius
        self.t_max_heating_in_celsius = t_max_heating_in_celsius
        self.cooling_considered = cooling_considered
        self.t_min_cooling_in_celsius = t_min_cooling_in_celsius
        self.t_max_cooling_in_celsius = t_max_cooling_in_celsius
        self.day_of_heating_season_begin = day_of_heating_season_begin
        self.day_of_heating_season_end = day_of_heating_season_end

    @staticmethod
    def get_default_config_heating(name: str) -> Any:
        """Default config for the heating controller."""
        config = L2GenericHeatConfig(
            name="L2HeatingTemperatureController_" + name,
            source_weight=1,
            t_min_heating_in_celsius=20.0,
            t_max_heating_in_celsius=22.0,
            cooling_considered=False,
            t_min_cooling_in_celsius=23,
            t_max_cooling_in_celsius=25,
            day_of_heating_season_begin=270,
            day_of_heating_season_end=150,
        )
        return config

    @staticmethod
    def get_default_config_buffer_heating(name: str) -> Any:
        """Default Config for the buffer temperature."""
        config = L2GenericHeatConfig(
            name="L2BufferTemperatureController_" + name,
            source_weight=1,
            t_min_heating_in_celsius=30.0,
            t_max_heating_in_celsius=50.0,
            cooling_considered=False,
            t_min_cooling_in_celsius=23,
            t_max_cooling_in_celsius=25,
            day_of_heating_season_begin=270,
            day_of_heating_season_end=150,
        )
        return config

    @staticmethod
    def get_default_config_waterheating(name: str) -> Any:
        """Generate Default Config for a DHW controller."""
        config = L2GenericHeatConfig(
            name="L2DHWTemperatureController_" + name,
            source_weight=1,
            t_min_heating_in_celsius=50.0,
            t_max_heating_in_celsius=80.0,
            cooling_considered=False,
            t_min_cooling_in_celsius=None,
            t_max_cooling_in_celsius=None,
            day_of_heating_season_begin=None,
            day_of_heating_season_end=None,
        )
        return config


class L2GenericHeatControllerState:

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
        return L2GenericHeatControllerState(
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


class L2GenericHeatController(cp.Component):

    """L2 heat pump controller. Processes signals ensuring comfort temperature of building.

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
        Temperature difference the building may go below or exceed the comfort temperature band with, because of recommendations from L3. The default is 1 °C.

    """

    # Inputs
    ReferenceTemperature = "ReferenceTemperature"

    # Outputs
    l2_device_signal = "l2_DeviceSignal"

    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: L2GenericHeatConfig,
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == L2GenericHeatConfig.__name__:
            raise ValueError("Wrong config class.")
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.config: L2GenericHeatConfig = config

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
        self.state: L2GenericHeatControllerState = L2GenericHeatControllerState()
        self.previous_state: L2GenericHeatControllerState = (
            L2GenericHeatControllerState()
        )

        # Component Outputs
        self.l2_device_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.l2_device_signal, LoadTypes.ON_OFF, Units.BINARY
        )

        # Component Inputs
        self.reference_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ReferenceTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )

        self.add_default_connections(self.get_default_connections_from_buildings())
        self.add_default_connections(
            self.get_default_connections_from_generic_hot_water_storage_modular()
        )

    def get_default_connections_from_buildings(self):
        """Sets the default connections for the building."""
        log.information("setting building default connections in L2 Controller")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                L2GenericHeatController.ReferenceTemperature,
                building_classname,
                Building.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_generic_hot_water_storage_modular(self):
        """Sets default connections for the boiler."""
        log.information("setting boiler default connections in L2 Controller")
        connections = []
        hotwaterstorage_classname = (
            generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L2GenericHeatController.ReferenceTemperature,
                hotwaterstorage_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def control_cooling(
        self,
        t_control: float,
        t_min_cooling: Optional[float],
        t_max_cooling: Optional[float],
    ) -> None:
        """Controls the cooling."""
        if t_min_cooling is None:
            raise ValueError("T_min_cooling was None.")
        if t_max_cooling is None:
            raise ValueError("T_max_cooling was None.")
        """ Controls the cooling. """
        if t_control > t_max_cooling:
            # start cooling if temperature exceeds upper limit
            self.state.activate()
            self.previous_state.activate()
        elif t_control < t_min_cooling:
            # stop cooling if temperature goes below lower limit
            self.state.deactivate()
            self.previous_state.deactivate()
        else:
            if self.state.compulsory == 1:
                # use previous state if it is compulsory
                pass
            else:
                # use previous state if l3 was not available
                self.state = self.previous_state.clone()

    def control_heating(
        self, t_control: float, t_min_heating: float, t_max_heating: float
    ) -> None:
        """Controles the heating."""
        if t_control > t_max_heating:
            # stop heating if temperature exceeds upper limit
            self.state.deactivate()
            self.previous_state.deactivate()

        elif t_control < t_min_heating:
            # start heating if temperature goes below lower limit
            self.state.activate()
            self.previous_state.activate()
        else:
            if self.state.compulsory == 1:
                # use previous state if it compulsory
                pass
            else:
                # use previous state if temperature is in given limit
                self.state = self.previous_state.clone()

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
        t_control = stsv.get_input_value(self.reference_temperature_channel)

        # check if it is the first iteration and reset compulsory and timestep_of_last_activation in state and previous_state
        if self.state.is_first_iteration(timestep):
            self.previous_state.is_first_iteration(timestep)
        if self.cooling_considered:
            # check out during cooling season
            if self.heating_season_begin > timestep > self.heating_season_end:
                self.control_cooling(
                    t_control=t_control,
                    t_min_cooling=self.config.t_min_cooling_in_celsius,
                    t_max_cooling=self.config.t_max_cooling_in_celsius,
                )
            # check out during heating season
            else:
                self.control_heating(
                    t_control=t_control,
                    t_min_heating=self.config.t_min_heating_in_celsius,
                    t_max_heating=self.config.t_max_heating_in_celsius,
                )

        # check out during heating season
        else:
            self.control_heating(
                t_control=t_control,
                t_min_heating=self.config.t_min_heating_in_celsius,
                t_max_heating=self.config.t_max_heating_in_celsius,
            )
        stsv.set_output_value(self.l2_device_signal_channel, self.state.state)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        lines: List[str] = []
        lines.append(f"Name: {self.component_name + str(self.config.source_weight)}")
        lines.append(
            f"upper set temperature: {self.config.t_max_heating_in_celsius:4.0f} °C"
        )
        lines.append(
            f"lower set temperature: {self.config.t_min_heating_in_celsius:4.0f} °C"
        )
        return lines
