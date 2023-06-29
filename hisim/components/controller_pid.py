"""PI controller Tuned for 5R1C Network."""

import numpy as np
import control
from typing import Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
# from hisim.components.weather import Weather
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building

# from hisim.components.weather import Weather
# from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim import log

__authors__ = "Marwa Alfouly"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Marwa Alfouly"
__email__ = "m.alfouly@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class PIDControllerConfig(cp.ConfigBase):

    """Configuration of the PID Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return PIDController.get_full_classname()

    name: str

    @classmethod
    def get_default_config(cls):
        """Gets a default pid controller."""
        return PIDControllerConfig(
            name="PIDController",
        )


class PIDState:

    """Represents the current internal state of the PID."""

    def __init__(
        self,
        integrator: float,
        derivator: float,
        integrator_d3: float,
        integrator_d4: float,
        integrator_d5: float,
        manipulated_variable: float,
    ):
        """Initializes the state of the PID."""
        self.Integrator: float = integrator
        self.Derivator: float = derivator
        self.Integrator_d3: float = integrator_d3
        self.Integrator_d4: float = integrator_d4
        self.Integrator_d5: float = integrator_d5
        self.manipulated_variable: float = manipulated_variable

    def clone(self):
        """Storing last timestep errors."""
        return PIDState(
            self.Integrator,
            self.Derivator,
            self.Integrator_d3,
            self.Integrator_d4,
            self.Integrator_d5,
            self.manipulated_variable,
        )


class PIDController(cp.Component):

    """PID Controller class.

    The controller has a derivative gain = 0 which makes it PI Only
    Thermal power delived by the air consitioner is manipulated to achieve desired setpoint temperature
    """

    # Inputs
    TemperatureMean = "Residence Temperature"  # uncontrolled temperature
    TemperatureMeanPrevious = "TemperatureMeanPrevious"
    TemperatureAir = "TemperatureAir"
    HeatFluxWallNode = "HeatFluxWallNode"
    HeatFluxThermalMassNode = "HeatFluxThermalMassNode"

    # ouput
    ThermalPowerPID = "ThermalPowerPID"
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    error_pvalue = "error_p_value"
    error_dvalue = "error_d_value"
    error_ivalue = "error_i_value"
    error = "error_value"
    derivator = "derivator"
    integrator = "integrator"
    FeedForwardSignal = "FeedForwardSignal"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        my_simulation_repository: Optional[cp.SimRepository],
        config: PIDControllerConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        super().__init__(
            config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.my_simulation_parameters = my_simulation_parameters
        self.build(my_simulation_repository)
        proportional_gain, integral_gain, derivative_gain = self.PIDtuning()
        # --------------------------------------------------
        # control saturation
        self.MV_min = 0
        self.MV_max = 5000
        self.ki = integral_gain
        self.kp = proportional_gain
        self.kd = derivative_gain
        self.state = PIDState(
            integrator=0,
            integrator_d3=0,
            integrator_d4=0,
            integrator_d5=0,
            derivator=0,
            manipulated_variable=0,
        )
        self.previous_state = self.state.clone()

        self.t_mC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureMean,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.phi_stC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatFluxWallNode,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )

        self.phi_mC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatFluxThermalMassNode,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )

        self.thermal_power: cp.ComponentOutput = self.add_output(
            self.component_name, self.ThermalPowerPID, LoadTypes.HEATING, Units.WATT
        )
        self.error_pvalue_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.error_pvalue, LoadTypes.HEATING, Units.WATT
        )

        self.error_ivalue_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.error_ivalue, LoadTypes.HEATING, Units.WATT
        )
        self.error_dvalue_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.error_dvalue, LoadTypes.HEATING, Units.WATT
        )
        self.error_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.error, LoadTypes.ANY, Units.CELSIUS
        )

        self.derivator_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.derivator, LoadTypes.ANY, Units.CELSIUS
        )

        self.integrator_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.integrator, LoadTypes.ANY, Units.CELSIUS
        )
        self.feed_forward_signalC: cp.ComponentOutput = self.add_output(
            self.component_name, self.FeedForwardSignal, LoadTypes.HEATING, Units.WATT
        )

    def get_building_default_connections(self):
        """get default inputs from the building component."""
        log.information("setting building default connections in PID controller")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                PIDController.TemperatureMean,
                building_classname,
                Building.TemperatureMean,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PIDController.HeatFluxWallNode,
                building_classname,
                Building.HeatFluxWallNode,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PIDController.HeatFluxThermalMassNode,
                building_classname,
                Building.HeatFluxThermalMassNode,
            )
        )

        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def build(self, my_simulation_repository):
        """For calculating internal things and preparing the simulation."""
        """ getting building physical properties for state space model """
        self.h_tr_w = my_simulation_repository.get_entry(
            Building.Thermal_transmission_coefficient_glazing
        )
        self.h_tr_ms = my_simulation_repository.get_entry(
            Building.Thermal_transmission_coefficient_opaque_ms
        )
        self.h_tr_em = my_simulation_repository.get_entry(
            Building.Thermal_transmission_coefficient_opaque_em
        )
        self.h_ve_adj = my_simulation_repository.get_entry(
            Building.Thermal_transmission_coefficient_ventillation
        )
        self.h_tr_is = my_simulation_repository.get_entry(
            Building.Thermal_transmission_Surface_IndoorAir
        )
        self.c_m = my_simulation_repository.get_entry(
            Building.Thermal_capacity_envelope
        )

    def i_save_state(self):
        """Saves the internal state at the beginning of each timestep."""
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """Restores the internal state after each iteration."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self):
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("PID Controller")
        lines.append("Control algorithm of the Air conditioner is: PI \n")
        lines.append(f"Controller Proportional gain is {self.kp:4.2f} \n")
        lines.append(f"Controller Integral gain is {self.ki:4.2f} \n")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Core smulation function."""
        if force_convergence:
            return
        # Retrieve Disturbance forecast
        phi_m = stsv.get_input_value(self.phi_mC)
        phi_st = stsv.get_input_value(self.phi_stC)

        # Retrieve building temperature
        building_temperature_t_mc = stsv.get_input_value(self.t_mC)
        # log.information('building_temperature_t_mc {}'.format(building_temperature_t_mc))
        feed_forward_signal = self.feedforward(phi_st, phi_m)

        set_point: float = 24.0

        Kp: float = self.kp  # controller Proportional gain
        Ki: float = self.ki  # integral gain
        Kd: float = self.kd  # Derivative gain

        error = set_point - building_temperature_t_mc  # e(tk)

        p_value = Kp * error
        d_value = Kd * (error - self.state.Derivator)

        self.state.Derivator = error
        self.state.Integrator = self.state.Integrator + error

        limit = 500
        if self.state.Integrator > limit:
            self.state.Integrator = limit
        elif self.state.Integrator < -limit:
            self.state.Integrator = -limit

        i_value = self.state.Integrator * Ki
        manipulated_variable = p_value + i_value + d_value

        """ Un-comment to prevent heating and cooling in specific periods  """

        # if timestep <= (151-20)*24*3600/self.my_simulation_parameters.seconds_per_timestep:
        #     if manipulated_variable + feed_forward_signal < 0:
        #         manipulated_variable=0
        # if timestep >= 304*24*3600/self.my_simulation_parameters.seconds_per_timestep:
        #     if manipulated_variable + feed_forward_signal < 0:
        #         manipulated_variable=0
        # if timestep > (151-20)*24*3600/self.my_simulation_parameters.seconds_per_timestep and timestep <= 243*24*3600/self.my_simulation_parameters.seconds_per_timestep:
        #     if manipulated_variable + feed_forward_signal > 0:
        #         manipulated_variable = 0

        self.state.manipulated_variable = manipulated_variable

        stsv.set_output_value(self.error_pvalue_output, p_value)
        stsv.set_output_value(self.error_dvalue_output, d_value)
        stsv.set_output_value(self.error_ivalue_output, i_value)
        stsv.set_output_value(self.error_output, error)
        stsv.set_output_value(self.integrator_output, self.state.Integrator)
        stsv.set_output_value(self.derivator_output, self.state.Derivator)
        stsv.set_output_value(self.thermal_power, manipulated_variable)
        stsv.set_output_value(self.feed_forward_signalC, feed_forward_signal)

    def feedforward(self, phi_st, phi_m):
        """the following gains are computed using the state space model in the function PIDtuning()."""
        process_gain: float = self.process_gain
        phi_st_gain: float = self.phi_st_gain
        phi_m_gain: float = self.phi_m_gain

        feed_forward_signal = (
            -((phi_st_gain * phi_st) + (phi_m_gain * phi_m)) / process_gain
        )

        return feed_forward_signal

    def PIDtuning(self):
        """State space model of a building with 5R1C configuration.

        The model is used to:
            1. analyze open loop response,
            2. get system time constant,
            3. get steady state value,
        given these data one could find an acceptable tuning of a PI controller.
        """
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        X = ((self.h_tr_w + self.h_tr_ms) * (self.h_ve_adj + self.h_tr_is)) + (
            self.h_ve_adj * self.h_tr_is
        )
        A11 = (
            ((self.h_tr_ms**2) * (self.h_tr_is + self.h_ve_adj) / X)
            - self.h_tr_ms
            - self.h_tr_em
        ) / (
            self.c_m / seconds_per_timestep
        )  # ((self.c_m_ref * self.A_f) * 3600)

        b11 = (self.h_tr_ms * self.h_tr_is) / ((self.c_m / seconds_per_timestep) * X)

        b_d11 = (
            (self.h_tr_ms * self.h_tr_w * (self.h_tr_is + self.h_ve_adj) / X)
            + self.h_tr_em
        ) / (self.c_m / seconds_per_timestep)
        b_d12 = (self.h_tr_ms * self.h_tr_is * self.h_ve_adj) / (
            (self.c_m / seconds_per_timestep) * X
        )
        b_d13 = (self.h_tr_ms * self.h_tr_is) / ((self.c_m / seconds_per_timestep) * X)
        b_d14 = (self.h_tr_ms * (self.h_tr_is + self.h_ve_adj)) / (
            (self.c_m / seconds_per_timestep) * X
        )
        b_d15 = 1 / (self.c_m / seconds_per_timestep)

        """ #comment out due to pylint warning W0612 (unused-variable)
        c11=(self.h_tr_ms*self.h_tr_is)/X
        c21=(self.h_tr_ms*(self.h_tr_is+self.h_ve_adj))/X

        d11=(self.h_tr_ms+self.h_tr_w+self.h_tr_is)/X
        d21=self.h_tr_is/X

        d_d11=(self.h_tr_w*self.h_tr_is)/X
        d_d12=(self.h_tr_ms+self.h_tr_is+self.h_tr_w)*self.h_ve_adj/X
        d_d13=(self.h_tr_ms+self.h_tr_is+self.h_tr_w)/X
        d_d14=self.h_tr_is/X
        d_d15=0
        d_d21=(self.h_tr_w*(self.h_tr_is+self.h_ve_adj))/X
        d_d22=(self.h_tr_is*self.h_ve_adj)/X
        d_d23=self.h_tr_is/X
        d_d24=(self.h_tr_is+self.h_ve_adj)/X
        d_d25=0
        """

        A = np.matrix([[A11]])  # transition matrix
        B = np.matrix([[b11, b_d11, b_d12, b_d13, b_d14, b_d15]])  # selection matrix
        # C=np.matrix([[c11],[c21]]) #design matrix #comment out due to pylint warning W0612 (unused-variable)
        # D=np.matrix([[d11,d_d11, d_d12,d_d13,d_d14,d_d15],[d21,d_d21, d_d22,d_d23,d_d24,d_d25]]) #comment out due to pylint warning W0612 (unused-variable)

        A = A * 0.5
        B = B * 0.5

        """ Gains of the uncontrolled systems, used for feedforward implementation """

        # Tm(s)/Pth(s)= K/Ts+1
        self.process_gain = B[0, 0] / -A[0, 0]
        # Tm(s)/Tout(s)= K/Ts+1
        self.t_out_gain = B[0, 1] / -A[0, 0]
        # Tm(s)/phi_ia(s)= K/Ts+1
        self.phi_ia_gain = B[0, 3] / -A[0, 0]
        # Tm(s)/phi_st(s)= K/Ts+1
        self.phi_st_gain = B[0, 4] / -A[0, 0]
        # Tm(s)/phi_m(s)= K/Ts+1
        self.phi_m_gain = B[0, 5] / -A[0, 0]

        """ time scale and arbitrary input signal to observe the open loop repsone """

        # choosing a sufficiently long interval
        ns = 20000
        t = np.linspace(0, ns, ns + 1)
        # input step signal
        u = np.zeros(ns + 1)
        for i in range(ns):
            if i == 0:
                u = 0
            else:
                u = 22

        """ Converting the state space model into transfer function.
        We have one state variable wich is the thermal mass temperature and 6 inputs...
        Thermal power delivered is the only controlled input, rest are disturbances.
        Therefore, the transfer function below shows the change in T_m in reponse the constrolled input (thermal power)
        """
        # transfer function:
        tf_tm = control.TransferFunction([B[0, 0]], [1, -(A[0, 0])])

        # open loop step response:
        timestep_tm_o, tm_o = control.forced_response(tf_tm, t, u)
        dummy1 = timestep_tm_o  # save 'timestep_tm_o' in dummy variable due to pylint warning W0612 (unused-variable) since function 'control.forced_response' can only be used with a return value with a tuple of length 2

        # steady state value:
        tm_steady_state = tm_o[ns]

        # time constant "value at 63.2%" :
        t_m_initial = 0
        tm_at_tau = t_m_initial - 0.632 * (t_m_initial - tm_steady_state)

        # find time constant tau_p

        def find_nearest(array, value):
            array = np.asarray(array)
            idx = (np.abs(array - value)).argmin()
            return array[idx]

        tm_at_tau_tf_tm = find_nearest(tm_o, tm_at_tau)
        for i in range(ns):
            if tm_o[i] == tm_at_tau_tf_tm:
                time_constant_tm = i

        """ PI Controller tuning with Pole Placement for controlling t_m:


        The following description is based on slides 12 to 14 in the file: https://fac.ksu.edu.sa/sites/default/files/control_design_by_pole_placement.pdf

        settling time (Ts) = 4/ (damping ratio * natural frequency (omega_n))
        damping frequency (omega_d)=natural frequency * sqrt(1-damping ratio^2)

        desired pole = (- natural frequency * damping ratio) +/-  j (natural frequency * sqrt(1-damping ratio^2))

        Closed loop transfer function = (transfer function plant * transfer function controller ) / (1+(transfer function plant * transfer function controller ))

        simplified Closed loop transfer function of a first order system 1/ms+b= (Kp s + Ki) / ms^2+(b+Kp)s+Ki

        denominator = s^2 + (2 * damping ratio * natural frequency ) s + (natural frequency)^2

        """

        # Few assumptions
        settling_time = time_constant_tm * 0.3
        over_shooting = 20

        damping_ratio = -np.log(over_shooting / 100) / (
            np.pi**2 + (np.log(over_shooting / 100)) ** 2
        ) ** (1 / 2)
        natural_frequency = 4 / (settling_time * damping_ratio)
        # damping_frequency=natural_frequency * np.sqrt(1-damping_ratio**2) #comment out due to pylint warning W0612 (unused-variable)

        m = 1 / B[0, 0]
        b = -A[0, 0] / B[0, 0]

        integral_gain = natural_frequency**2 * m
        proportional_gain = (natural_frequency * damping_ratio * 2 * m) - b
        derivative_gain = 0

        log.information("gain Ki= {}".format(integral_gain))
        log.information("gain Kp= {}".format(proportional_gain))
        return proportional_gain, integral_gain, derivative_gain

    def determine_conditions(self, current_temperature: float, set_point: float) -> str:
        """For determining heating and cooling mode and implementing a dead zone. Currently disabled."""
        offset_in_degree_celsius = 0.5
        maximum_heating_set_temp = set_point + offset_in_degree_celsius
        minimum_cooling_set_temp = set_point - offset_in_degree_celsius

        mode = "off"
        if (
            mode == "heating" and current_temperature > maximum_heating_set_temp
        ):  # 23 22.5
            return "off"
        if (
            mode == "cooling" and current_temperature < minimum_cooling_set_temp
        ):  # 24 21.5
            mode = "off"
        if mode == "off":
            if current_temperature < set_point:  # 21
                mode = "heating"
            if current_temperature > set_point:  # 26
                mode = "cooling"
        return mode


"""Possible future work.

How to enhance receeding PI implementation?

Even though PI controller cannot react to a dynamic electricity tariff or shift the cooling load to maximize PV self-consumption,
it is possible to achieve a reduction in total energy consumption compared to an on-off controller. Through this work, only a small
reduction was observed. It may be interesting to investigate whether higher savings can be achieved using other techniques for tuning
the PID parameters. Additionally, it is advised to consider the effect of sampling time on control performance. Larger sampling time
negatively influence the performance, see: doi: 10.1515/eletel-2016-0005

In HiSim, minimum sampling time is one minute. It is suggested to research approaches which could compensate for sampling at 60 seconds.

Another suggestion is to add reference generation (currently fix reference; possible: track optimized reference)
"""
