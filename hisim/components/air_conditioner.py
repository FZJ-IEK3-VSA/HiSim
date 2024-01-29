"""Air Conditioner Component."""

# clean

from dataclasses import dataclass
from typing import Any
from dataclasses_json import dataclass_json

# from typing import Optional
import numpy as np
from hisim import log

# owned
from hisim import component as cp
from hisim.component import ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.loadtypes import LoadTypes, Units
from hisim.components.weather import Weather
from hisim.components.building import Building
from hisim.components.controller_pid import PIDController
from hisim import utils
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

__authors__ = "Marwa Alfouly"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Marwa Alfouly"
__email__ = "m.alfouly@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class AirConditionerConfig(ConfigBase):

    """Class for configuration of air-conditioner."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return AirConditioner.get_full_classname()

    name: str
    manufacturer: str
    model_name: str
    min_operation_time: int
    min_idle_time: int
    control: str
    # my_simulation_repository: Optional[cp.SimRepository] = None

    @classmethod
    def get_default_air_conditioner_config(cls) -> Any:
        """Get default configuration of air-conditioner."""
        config = AirConditionerConfig(
            name="AirConditioner",
            manufacturer="Panasonic",
            model_name="CS-RE18JKE/CU-RE18JKE",
            min_operation_time=60 * 60,
            min_idle_time=15 * 60,
            control="on_off",
            # my_simulation_repository=None,
        )
        return config


@dataclass_json
@dataclass
class AirConditionerControllerConfig(ConfigBase):

    """Class for configuration of air-conditioner controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return AirConditionerController.get_full_classname()

    name: str
    t_air_heating: float
    t_air_cooling: float
    offset: float

    @classmethod
    def get_default_air_conditioner_controller_config(cls) -> Any:
        """Get default configuration of air-conditioner controller."""
        config = AirConditionerControllerConfig(
            name="AirConditioner",
            t_air_heating=18.0,
            t_air_cooling=26.0,
            offset=0.0,
        )
        return config


class AirConditionerState:

    """Data class for saving the state of the air conditioner."""

    def __init__(
        self,
        timestep_actual: int = -1,
        state: int = 0,
        timestep_of_last_action: int = 0,
    ):
        """Initializes the state of the air conditioner."""
        self.timestep_actual = timestep_actual
        self.state = state
        self.timestep_of_last_action = timestep_of_last_action

    def clone(self):
        """Storing last timestep state."""
        return AirConditionerState(
            timestep_actual=self.timestep_actual,
            state=self.state,
            timestep_of_last_action=self.timestep_of_last_action,
        )

    def is_first_iteration(self, timestep):
        """Check if it is the first iteration in order to ensure minimal on and off times."""
        bool_is_first_iteration = False
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            bool_is_first_iteration = True
        else:
            bool_is_first_iteration = False
        return bool_is_first_iteration

    def activation(self, timestep):
        """Take action based on the operation time / idle time."""
        self.state = 1
        self.timestep_of_last_action = timestep

    def deactivation(self, timestep):
        """Take action based on the operation time / idle time."""
        self.state = 0
        self.timestep_of_last_action = timestep


class AirConditioner(cp.Component):

    """Class for air-conditioner."""

    # inputs
    State = "State"
    # weather
    TemperatureOutside = "TemperatureOutside"
    # controller_pid
    ThermalPowerPID = "ThermalPowerPID"
    # controller_mpc
    OperatingMode = "OperatingMode"
    GridImport = "GridImport"
    PV2load = "PV2load"
    Battery2Load = "Battery2Load"
    # building
    TemperatureMean = "Residence Temperature"
    FeedForwardSignal = "FeedForwardSignal"
    # ElectricityOutputPID = "ElectricityOutputPID"

    # outputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricityOutput = "ElectricityOutput"
    PidManipulatedVariable = "PidManipulatedVariable"
    EER = "EER"

    cop_coef_heating = "cop_coef_heating"
    eer_coef_cooling = "eer_coef_cooling"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Constructs all the necessary attributes."""

        self.air_conditioner_config = config
        # self.state = 0
        # self.state0 = 0
        # self.previous_state = 0
        self.state = AirConditionerState()
        self.state0 = AirConditionerState()
        self.previous_state = AirConditionerState()

        super().__init__(
            name=self.air_conditioner_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.build(
            manufacturer=self.air_conditioner_config.manufacturer,
            model_name=self.air_conditioner_config.model_name,
            min_operation_time=self.air_conditioner_config.min_operation_time,
            min_idle_time=self.air_conditioner_config.min_idle_time,
            # my_simulation_repository=self.air_conditioner_config.my_simulation_repository,
        )
        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.t_m_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureMean,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.State,
            LoadTypes.ANY,
            Units.ANY,
            False,
        )
        self.feed_forward_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.FeedForwardSignal,
            LoadTypes.HEATING,
            Units.WATT,
            False,
        )
        self.thermal_power: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerPID,
            LoadTypes.HEATING,
            Units.WATT,
            False,
        )
        self.operating_mode_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.OperatingMode,
            LoadTypes.ANY,
            Units.ANY,
            False,
        )
        self.optimal_electric_power_pv_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.PV2load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_grid_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GridImport,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_battery_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Battery2Load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.pid_thermal_power_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PidManipulatedVariable,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for {self.PidManipulatedVariable} will follow.",
        )
        self.thermal_energy_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for Air Conditioner {self.ThermalEnergyDelivered} will follow.",
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for Air Conditioner {self.ElectricityOutput} will follow.",
        )
        self.cooling_eer_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.EER,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.EER} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_air_condition_controller())

        self.control = self.air_conditioner_config.control

    def get_default_connections_from_weather(self):
        """Get default inputs from the weather component."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditioner.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def get_default_connections_from_air_condition_controller(self):
        """Get default inputs from the on_off controller."""

        connections = []
        controller_classname = AirConditionerController.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditioner.State,
                controller_classname,
                AirConditionerController.State,
            )
        )
        return connections

    def get_pid_controller_default_connections(self):
        """Get default inputs from the PID controller component."""

        connections = []
        controller_classname = AirConditionerController.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditioner.ThermalPowerPID,
                controller_classname,
                PIDController.ThermalPowerPID,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(
        self,
        manufacturer,
        model_name,
        min_operation_time,
        min_idle_time,
        # my_simulation_repository,
    ):
        """Build function: The function retrieves air conditioner from databasesets sets important constants and parameters for the calculations."""
        # Simulation parameters

        # Retrieves air conditioner from database - BEGIN
        air_conditioners_database = utils.load_smart_appliance("Air Conditioner")

        air_conditioner = None
        for air_conditioner_iterator in air_conditioners_database:
            if (
                air_conditioner_iterator["Manufacturer"] == manufacturer
                and air_conditioner_iterator["Model"] == model_name
            ):
                air_conditioner = air_conditioner_iterator
                break

        if air_conditioner is None:
            raise Exception("Air conditioner model not registered in the database")

        self.manufacturer = manufacturer
        self.model = model_name
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time

        # Interpolates COP, cooling capacities, power input data from the database
        self.eer_ref = []
        self.t_out_cooling_ref = []
        self.t_out_heating_ref = []
        self.cooling_capacity_ref = []
        self.heating_capacity_ref = []
        self.cop_ref = []

        """
        Typical realtion between COPs and Heating capacities are found here:  https://www.everysolarthing.com/blog/heat-pumps/

        """

        for air_conditioner_tout in air_conditioner["Outdoor temperature range - cooling"]:
            self.t_out_cooling_ref.append([air_conditioner_tout][0])
        for air_conditioner_tout in air_conditioner["Outdoor temperature range - heating"]:
            self.t_out_heating_ref.append([air_conditioner_tout][0])

        for air_conditioner_eers in air_conditioner["EER W/W"]:
            self.eer_ref.append([air_conditioner_eers][0])
        for air_conditioner_cops in air_conditioner["COP W/W"]:
            self.cop_ref.append([air_conditioner_cops][0])

        for air_conditioner_cooling_capacities in air_conditioner["Cooling capacity W"]:
            self.cooling_capacity_ref.append([air_conditioner_cooling_capacities][0])
        for air_conditioner_heating_capacities in air_conditioner["Heating capacity W"]:
            self.heating_capacity_ref.append([air_conditioner_heating_capacities][0])
        print(str(self.t_out_cooling_ref))
        print(str(self.eer_ref))
        self.eer_coef = np.polyfit(self.t_out_cooling_ref, self.eer_ref, 1)
        self.cooling_capacity_coef = np.polyfit(self.t_out_cooling_ref, self.cooling_capacity_ref, 1)

        self.cop_coef = np.polyfit(self.t_out_heating_ref, self.cop_ref, 1)
        self.heating_capacity_coef = np.polyfit(self.t_out_heating_ref, self.heating_capacity_ref, 1)

        # Retrieves air conditioner from database - END

        SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.COPCOEFFICIENTHEATING, entry=self.cop_coef)
        SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.EERCOEFFICIENTCOOLING, entry=self.eer_coef)
        # self.simulation_repository.set_entry(self.cop_coef_heating, self.cop_coef)
        # self.simulation_repository.set_entry(self.eer_coef_cooling, self.eer_coef)

        # Sets the time operation restricitions
        self.on_time = self.min_operation_time / self.my_simulation_parameters.seconds_per_timestep
        self.off_time = self.min_idle_time / self.my_simulation_parameters.seconds_per_timestep

        # self.state = AirConditionerState()
        # self.previous_state = AirConditionerState()

    def cal_eer(self, t_out):
        """Calculate cooling energy efficiency ratio as a function of outside temperature."""
        return np.polyval(self.eer_coef, t_out)

    def cal_cooling_capacity(self, t_out):
        """Calculate cooling capacity as a function of outside temperature."""
        return np.polyval(self.cooling_capacity_coef, t_out)

    def cal_cop(self, t_out):
        """Calculate heating coefficient of performance as a function of outside temperature."""
        return np.polyval(self.cop_coef, t_out)

    def cal_heating_capacity(self, t_out):
        """Calculate heating capacity as a function of outside temperature."""
        return np.polyval(self.heating_capacity_coef, t_out)

    def i_save_state(self) -> None:
        """Saves the internal state at the beginning of each timestep."""
        self.previous_state = self.state.clone()
        pass

    def i_restore_state(self) -> None:
        """Restores the internal state after each iteration."""
        self.state = self.previous_state.clone()
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self):
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("Name: Air Conditioner")
        lines.append(f"Manufacturer: {self.manufacturer}")
        lines.append(f"Model {self.model}")
        lines.append(f"Min Operation Time [Sec]: {self.min_operation_time}")
        lines.append(f"Min Idle Time [Sec]: {self.min_idle_time}")
        lines.append(f"Control: {self.control}")
        return self.air_conditioner_config.get_string_dict() + lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:  # noqa: C901
        """Core simulation function."""
        # Inputs
        t_out = stsv.get_input_value(self.t_out_channel)
        on_off_state = stsv.get_input_value(self.state_channel)

        if self.control == "on_off":
            # Heating Season:
            cop = self.cal_cop(t_out)
            heating_power = self.cal_heating_capacity(t_out)

            # Cooling Season:
            eer = self.cal_eer(t_out)
            cooling_power = self.cal_cooling_capacity(t_out)

            # save reference state state0 in first iteration
            if self.state.is_first_iteration(timestep):
                self.state0 = self.state.clone()

            # return device on if minimum operation time is not fulfilled and device was on in previous state
            if self.state0.state == 1 and self.state0.timestep_of_last_action + self.on_time >= timestep:
                self.state.state = 1
            elif self.state0.state == -1 and self.state0.timestep_of_last_action + self.on_time >= timestep:
                self.state.state = -1
            # return device off if minimum idle time is not fulfilled and device was off in previous state
            elif self.state0.state == 0 and self.state0.timestep_of_last_action + self.off_time >= timestep:
                self.state.state = 0
            # check signal from l2 and turn on or off if it is necesary
            else:
                if on_off_state == 0 and (self.state0.state in (1, -1)):
                    self.state.deactivation(timestep)
                elif (on_off_state in (1, -1)) and self.state0.state == 0:
                    self.state.activation(timestep)

            if self.state.state == 1 and on_off_state == 1:
                thermal_energy_delivered = on_off_state * heating_power
                electricity_output = heating_power / cop
            elif self.state.state == 1 and on_off_state == -1:
                thermal_energy_delivered = on_off_state * cooling_power
                electricity_output = cooling_power / eer
            else:
                thermal_energy_delivered = 0
                electricity_output = 0

            # log.information("thermal_energy_delivered {}".format(thermal_energy_delivered))
            stsv.set_output_value(self.thermal_energy_delivered_channel, thermal_energy_delivered)
            stsv.set_output_value(self.electricity_output_channel, electricity_output)
            stsv.set_output_value(self.cooling_eer_channel, eer)

        if self.control == "PID":
            feed_forward_signal = stsv.get_input_value(self.feed_forward_signal_channel)
            thermal_load = stsv.get_input_value(self.thermal_power)

            if thermal_load > 0:
                cop = self.cal_cop(t_out)
                thermal_energy_delivered = thermal_load + feed_forward_signal
                electricity_output = thermal_energy_delivered / cop
            elif thermal_load < 0:
                eer = self.cal_eer(t_out)
                thermal_energy_delivered = thermal_load + feed_forward_signal
                electricity_output = -thermal_energy_delivered / eer
            else:
                thermal_energy_delivered = 0
                electricity_output = 0

            if thermal_energy_delivered > 15000:
                thermal_energy_delivered = 15000
            elif thermal_energy_delivered < -15000:
                thermal_energy_delivered = -15000

            stsv.set_output_value(self.electricity_output_channel, electricity_output)
            stsv.set_output_value(self.thermal_energy_delivered_channel, thermal_energy_delivered)

        if self.control == "MPC":
            mode = stsv.get_input_value(self.operating_mode_channel)
            optimal_electric_power_grid = stsv.get_input_value(self.optimal_electric_power_grid_channel)
            optimal_electric_power_pv = stsv.get_input_value(self.optimal_electric_power_pv_channel)
            optimal_electric_power_battery = stsv.get_input_value(self.optimal_electric_power_battery_channel)
            optimal_electric_power_total = (
                optimal_electric_power_grid + optimal_electric_power_pv + optimal_electric_power_battery
            )

            if mode == 1:
                cop = self.cal_cop(t_out)
                thermal_energy_delivered = optimal_electric_power_total * cop
                electricity_output = optimal_electric_power_total
            elif mode == -1:
                eer = self.cal_eer(t_out)
                thermal_energy_delivered = -optimal_electric_power_total * eer
                electricity_output = optimal_electric_power_total
            else:
                thermal_energy_delivered = 0
                electricity_output = 0

            stsv.set_output_value(self.electricity_output_channel, electricity_output)
            stsv.set_output_value(self.thermal_energy_delivered_channel, thermal_energy_delivered)


class AirConditionerController(cp.Component):

    """Class for air-conditioner controller."""

    """It takes data from other
    components and sends signal to the air conditioner for
    activation or deactivation.

    Parameters
    --------------
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this air conditioner
    """

    # Inputs
    TemperatureMean = "Residence Temperature"
    ElectricityInput = "ElectricityInput"

    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Constructs all the neccessary attributes."""

        self.air_conditioner_controller_config = config
        self.aircon_controller_mode: str = "not initalized yet"
        self.previous_aircon_controller_mode: str = "not initalized yet"

        super().__init__(
            name=self.air_conditioner_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build(
            t_air_cooling=self.air_conditioner_controller_config.t_air_cooling,
            t_air_heating=self.air_conditioner_controller_config.t_air_heating,
            offset=self.air_conditioner_controller_config.offset,
        )

        self.t_m_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureMean,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.electricity_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityInput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_building())

    def get_default_connections_from_building(self):
        """Get default inputs from the building component."""
        log.information("setting building default connections in AirConditionerController")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditionerController.TemperatureMean,
                building_classname,
                Building.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self, t_air_heating, t_air_cooling, offset):
        """Build function: important settings such as comforatable temperature range and deadband width."""
        # Sth
        self.aircon_controller_mode = "off"
        self.previous_aircon_controller_mode = self.aircon_controller_mode

        # Configuration
        self.t_set_heating = t_air_heating
        self.t_set_cooling = t_air_cooling
        self.offset = offset

    def i_save_state(self):
        """Saves the internal state at the beginning of each timestep."""
        self.previous_aircon_controller_mode = self.aircon_controller_mode

    def i_restore_state(self):
        """Restores the internal state after each iteration."""
        self.aircon_controller_mode = self.previous_aircon_controller_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self):
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("Air Conditioner Controller")
        lines.append("Control algorith of the Air conditioner is: on-off control\n")
        lines.append(f"Controller heating set temperature is {format(self.t_set_heating)} Deg C \n")
        lines.append(f"Controller cooling set temperature is {format(self.t_set_cooling)} Deg C \n")
        return self.air_conditioner_controller_config.get_string_dict() + lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Core simulation."""
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            t_m_old = stsv.get_input_value(self.t_m_channel)
            # electricity_input = stsv.get_input_value(self.electricity_inputC)

            self.conditions(t_m_old)

        if self.aircon_controller_mode == "heating":
            state = 1
        if self.aircon_controller_mode == "cooling":
            state = -1
        if self.aircon_controller_mode == "off":
            state = 0

        stsv.set_output_value(self.state_channel, state)
        # log.information("state {}".format(state))

    def conditions(self, set_temp):
        """Controller takes action to maintain defined comfort range given a certain deadband."""
        maximum_heating_set_temp = self.t_set_heating + self.offset
        minimum_heating_set_temp = self.t_set_heating
        minimum_cooling_set_temp = self.t_set_cooling - self.offset
        maximum_cooling_set_temp = self.t_set_cooling

        if self.aircon_controller_mode == "heating":  # and daily_avg_temp < 15:
            if set_temp > maximum_heating_set_temp:  # 16.5
                self.aircon_controller_mode = "off"
                return
        if self.aircon_controller_mode == "cooling":
            if set_temp < minimum_cooling_set_temp:  # 23.5
                self.aircon_controller_mode = "off"
                return
        if self.aircon_controller_mode == "off":
            # if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
            if set_temp < minimum_heating_set_temp:  # 21
                self.aircon_controller_mode = "heating"
                return
            if set_temp > maximum_cooling_set_temp:  # 26
                self.aircon_controller_mode = "cooling"
                return

    def prin1t_outpu1t(self, t_m, state):
        """Print output of air-conditioner controller."""
        log.information("==========================================")
        log.information(f"T m: {format(t_m)}")
        log.information(f"State: {format(state)}")
