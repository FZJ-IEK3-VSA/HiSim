# -*- coding: utf-8 -*-
# clean

""" Generic CHP controller with minimal runtime.

Heat is transfered to the drain hot water storage and either the buffer storage or the building directly.
"""

# Owned
from dataclasses import dataclass
from typing import List
from dataclasses_json import dataclass_json

# Generic/Built-in
from hisim import component as cp
from hisim import log, utils
from hisim.component import ConfigBase
from hisim.components import building, generic_hot_water_storage_modular
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
class L1CHPControllerConfig(ConfigBase):
    """CHP Controller Config."""

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of CHP: hydrogen or gas (hydrogen than considers also SOC of hydrogen storage)
    use: LoadTypes
    #: lower set temperature of building (or buffer storage), given in °C
    t_min_heating_in_celsius: float
    #: upper set temperature of building (or buffer storage), given in °C
    t_max_heating_in_celsius: float
    #: lower set temperature of drain hot water storage, given in °C
    t_min_dhw_in_celsius: float
    #: upper set temperature of drain hot water storage, given in °C
    t_max_dhw_in_celsius: float
    # julian day of simulation year, where heating season begins
    day_of_heating_season_begin: int
    # julian day of simulation year, where heating season ends
    day_of_heating_season_end: int
    # minimal operation time of heat source
    min_operation_time_in_seconds: int
    # minimal resting time of heat source
    min_idle_time_in_seconds: int

    @staticmethod
    def get_default_config(name: str, use: LoadTypes) -> "L1CHPControllerConfig":
        """Returns default configuration for the CHP controller."""
        config = L1CHPControllerConfig(
            name=name, source_weight=1, use=use, t_min_heating_in_celsius=20.0, t_max_heating_in_celsius=20.5,
            t_min_dhw_in_celsius=42, t_max_dhw_in_celsius=60, day_of_heating_season_begin=270,
            day_of_heating_season_end=150, min_operation_time_in_seconds=3600 * 4, min_idle_time_in_seconds=3600 * 2)
        return config

    @staticmethod
    def get_default_config_with_buffer(name: str, use: LoadTypes) -> "L1CHPControllerConfig":
        """Returns default configuration for the CHP controller, when buffer storage for heating is available."""
        # minus - 1 in heating season, so that buffer heats up one day ahead, and modelling to building works.
        config = L1CHPControllerConfig(
            name=name, source_weight=1, use=use, t_min_heating_in_celsius=31.0, t_max_heating_in_celsius=40.0,
            t_min_dhw_in_celsius=42, t_max_dhw_in_celsius=60, day_of_heating_season_begin=270 - 1,
            day_of_heating_season_end=150, min_operation_time_in_seconds=3600 * 4, min_idle_time_in_seconds=3600 * 2)
        return config


class L1CHPControllerState:
    """Data class that saves the state of the CHP controller."""
    def __init__(
        self,
        on_off: int,
        mode: int,
        activation_time_step: int,
        deactivation_time_step: int,
    ) -> None:
        """Initializes CHP Controller state.

        :param on_off: 0 if turned off, 1 if running.
        :type on_off: int
        :param mode: 0 if water heating, 1 if heating.
        :type mode: int
        :param activation_time_step: timestep of activation (simulation time step).
        :type activation_time_step: int
        :param deactivation_time_step: timestep of deactivation
        :type deactivation_time_step: int
        """
        self.on_off: int = on_off
        self.mode: int = mode
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step

    def clone(self) -> "L1CHPControllerState":
        """Copies the current instance."""
        return L1CHPControllerState(
            on_off=self.on_off,
            mode=self.mode,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
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


class L1CHPController(cp.Component):
    """L1 CHP Controller.

    Is activated when both Electricity and Heat are demanded. Decides if heat is transferred to
    Building or HotWaterStorage.
    When it is a fuel cell, also the SOC of the hydrogen storage is checked.
    """

    # Inputs
    BuildingTemperature = "BuildingTemperature"
    HotWaterStorageTemperature = "HotWaterStorageTemperature"

    # Outputs
    CHPControllerOnOffSignal = "CHPControllerOnOffSignal"
    CHPControllerHeatingModeSignal = "CHPControllerHeatingModeSignal"

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: L1CHPControllerConfig
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == L1CHPControllerConfig.__name__:
            raise ValueError("Wrong config class. Got a " + config.__class__.__name__)
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.config: L1CHPControllerConfig = config
        self.minimum_runtime_in_timesteps = int(
            config.min_operation_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            config.min_idle_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        """ Initializes the class. """
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
        self.state: L1CHPControllerState = L1CHPControllerState(0, 0, 0, 0)
        self.previous_state: L1CHPControllerState = self.state.clone()
        self.processed_state: L1CHPControllerState = self.state.clone()

        # Component Outputs
        self.chp_onoff_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.CHPControllerOnOffSignal,
            LoadTypes.ON_OFF,
            Units.BINARY,
            output_description="On off signal from CHP controller.",
        )

        self.chp_heatingmode_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.CHPControllerHeatingModeSignal,
            LoadTypes.ANY,
            Units.BINARY,
            output_description="Heating mode signal from CHP controller.",
        )

        # Component Inputs
        self.building_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )
        self.dhw_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HotWaterStorageTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )

        self.add_default_connections(
            self.get_default_connections_generic_hot_water_storage_modular()
        )
        self.add_default_connections(self.get_default_connections_from_building())

    def get_default_connections_generic_hot_water_storage_modular(self):
        """Sets default connections for the boiler."""
        log.information("setting buffer default connections in L1 building Controller")
        connections = []
        boiler_classname = (
            generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1CHPController.HotWaterStorageTemperature,
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
                L1CHPController.BuildingTemperature,
                building_classname,
                building.Building.TemperatureMeanThermalMass,
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
            t_building = stsv.get_input_value(self.building_temperature_channel)
            t_dhw = stsv.get_input_value(self.dhw_temperature_channel)
            self.determine_heating_mode(timestep, stsv, t_building, t_dhw)
            self.calculate_state(timestep, stsv, t_building, t_dhw)
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.chp_onoff_signal_channel, self.state.on_off)
        stsv.set_output_value(self.chp_heatingmode_signal_channel, self.state.mode)

    def determine_heating_mode(self, timestep: int, stsv: cp.SingleTimeStepValues, t_building: float, t_dhw: float) -> None:
        """Determines if hot water or building should be heated.

        The mode in the state is 0 if water heating is considered and 1 if heating is enforced."""
        if (
            self.heating_season_begin > timestep > self.heating_season_end
            and t_building >= self.config.t_min_heating_in_celsius - 30
        ):
            # only consider water heating in summer
            self.state.mode = 0
            return
        # calculate heating level of DHW storage and building
        soc_dhw = (t_dhw - self.config.t_min_dhw_in_celsius) / (self.config.t_max_dhw_in_celsius - self.config.t_min_dhw_in_celsius)
        soc_building = (t_building - self.config.t_min_heating_in_celsius) / (self.config.t_max_heating_in_celsius - self.config.t_min_heating_in_celsius)
        
        if soc_building >= soc_dhw:
            self.state.mode = 0
            return
        self.state.mode = 1

    def calculate_state(self, timestep: int, stsv: cp.SingleTimeStepValues, t_building: float, t_dhw: float) -> None:
        """Calculate the CHP state and activate / deactives."""
        # return device on if minimum operation time is not fulfilled and device was on in previous state
        if (
            self.state.on_off == 1
            and self.state.activation_time_step + self.minimum_runtime_in_timesteps
            >= timestep
        ):
            # mandatory on, minimum runtime not reached
            return
        if (
            self.state.on_off == 0
            and self.state.deactivation_time_step
            + self.minimum_resting_time_in_timesteps
            >= timestep
        ):
            # mandatory off, minimum resting time not reached
            return
        if (
            self.heating_season_begin > timestep > self.heating_season_end
            and t_building >= self.config.t_min_heating_in_celsius - 30
        ):
            # only consider water heating in summer
            if t_dhw < self.config.t_min_dhw_in_celsius:
                self.state.activate(timestep)  # activate CHP when storage temperature is too low
                return
            if t_dhw > self.config.t_max_heating_in_celsius:
                self.state.deactivate(timestep)  # deactivate CHP when storage temperature is too high
                return
        else:
            if t_building < self.config.t_min_heating_in_celsius or t_dhw < self.config.t_min_dhw_in_celsius:
                # activate heating when either dhw storage or building temperature is too low
                self.state.activate(timestep)
                return
            if t_building > self.config.t_max_heating_in_celsius and t_dhw > self.config.t_max_dhw_in_celsius:
                # deactivate heating when dhw storage and building temperature is too high
                self.state.deactivate(timestep)
                return

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
