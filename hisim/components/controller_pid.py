"""PI controller Tuned for 5R1C Network."""

# clean

from dataclasses import dataclass
from typing import Optional

from dataclasses_json import dataclass_json
import control
import numpy as np

# Owned

from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
from hisim import log
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.config import ConfigBase, ComponentID, DisplayConfig

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
class PIDControllerConfig(ConfigBase):
    """Configuration of the PID Controller."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return PIDController.get_full_classname()

    component_id: ComponentID

    @classmethod
    def get_default_config(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "PIDControllerConfig":
        """Gets a default pid controller."""
        if component_id is None:
            component_id = ComponentID(name="PIDController")
        return PIDControllerConfig(
            component_id=component_id,
        )


class PIDState:
    """Represents the current internal state of the PID."""

    def __init__(
        self,
        integrator_in_celsius: float,
        derivator_in_celsius: float,
        integrator_d3_in_celsius: float,
        integrator_d4_in_celsius: float,
        integrator_d5_in_celsius: float,
        manipulated_variable_in_watt: float,
    ) -> None:
        """Initializes the state of the PID."""
        self.integrator_in_celsius: float = integrator_in_celsius
        self.derivator_in_celsius: float = derivator_in_celsius
        self.integrator_d_three_in_celsius: float = integrator_d3_in_celsius
        self.integrator_d_four_in_celsius: float = integrator_d4_in_celsius
        self.integrator_d_five_in_celsius: float = integrator_d5_in_celsius
        self.manipulated_variable_in_watt: float = manipulated_variable_in_watt

    def clone(self) -> "PIDState":
        """Storing last timestep errors."""
        return PIDState(
            self.integrator_in_celsius,
            self.derivator_in_celsius,
            self.integrator_d_three_in_celsius,
            self.integrator_d_four_in_celsius,
            self.integrator_d_five_in_celsius,
            self.manipulated_variable_in_watt,
        )


class BuildingThermalModel5R1C:
    """5R1C building thermal model used to identify the PID controller tuning.

    Owns the 5R1C thermal-transmission coefficients and the building thermal
    capacity, builds the single-state state-space representation of the
    thermal-mass node, and derives the process gain, time constant, and
    disturbance gains consumed by the PI controller and its feed-forward path.

    The coefficients are read from the singleton simulation repository, which
    must be pre-populated (e.g. by the building component) before a model is
    constructed.
    """

    def __init__(
        self,
        h_tr_w_in_watt_per_kelvin: float,
        h_tr_ms_in_watt_per_kelvin: float,
        h_tr_em_in_watt_per_kelvin: float,
        h_ve_adj_in_watt_per_kelvin: float,
        h_tr_is_in_watt_per_kelvin: float,
        c_m_in_joule_per_kelvin: float,
        seconds_per_timestep: float,
    ) -> None:
        """Initializes the 5R1C thermal model and identifies its open-loop gains."""
        self.h_tr_w_in_watt_per_kelvin: float = h_tr_w_in_watt_per_kelvin
        self.h_tr_ms_in_watt_per_kelvin: float = h_tr_ms_in_watt_per_kelvin
        self.h_tr_em_in_watt_per_kelvin: float = h_tr_em_in_watt_per_kelvin
        self.h_ve_adj_in_watt_per_kelvin: float = h_ve_adj_in_watt_per_kelvin
        self.h_tr_is_in_watt_per_kelvin: float = h_tr_is_in_watt_per_kelvin
        self.c_m_in_joule_per_kelvin: float = c_m_in_joule_per_kelvin
        self.seconds_per_timestep: float = seconds_per_timestep
        # Derived state-space and open-loop quantities (populated by identify()).
        self.transition_matrix: np.ndarray
        self.selection_matrix: np.ndarray
        self.process_gain_in_kelvin_per_watt: float
        self.t_out_gain_dimensionless: float
        self.phi_ia_gain_in_kelvin_per_watt: float
        self.phi_st_gain_in_kelvin_per_watt: float
        self.phi_m_gain_in_kelvin_per_watt: float
        self.time_constant_in_seconds: float
        self.identify()

    @classmethod
    def from_sim_repository(cls, seconds_per_timestep: float) -> "BuildingThermalModel5R1C":
        """Reads the 5R1C thermal coefficients from the singleton simulation repository."""
        repo = SingletonSimRepository()
        return cls(
            h_tr_w_in_watt_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING),
            h_tr_ms_in_watt_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS),
            h_tr_em_in_watt_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM),
            h_ve_adj_in_watt_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION),
            h_tr_is_in_watt_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONSURFACEINDOORAIR),
            c_m_in_joule_per_kelvin=repo.get_entry(key=SingletonDictKeyEnum.THERMALCAPACITYENVELOPE),
            seconds_per_timestep=seconds_per_timestep,
        )

    def _build_state_space(self) -> tuple[np.ndarray, np.ndarray]:
        """Builds the single-state 5R1C state-space matrices of the thermal-mass node.

        Returns the transition (state) matrix and the selection (input) matrix.
        The 0.5 scaling applied to both matrices is a deliberate factor baked
        into the original gain calculation and must be preserved when the
        thermal model parameters or timestep change.
        """
        seconds_per_timestep = self.seconds_per_timestep
        x_value = (
            (self.h_tr_w_in_watt_per_kelvin + self.h_tr_ms_in_watt_per_kelvin)
            * (self.h_ve_adj_in_watt_per_kelvin + self.h_tr_is_in_watt_per_kelvin)
        ) + (self.h_ve_adj_in_watt_per_kelvin * self.h_tr_is_in_watt_per_kelvin)
        a11 = (
            (
                (self.h_tr_ms_in_watt_per_kelvin**2)
                * (self.h_tr_is_in_watt_per_kelvin + self.h_ve_adj_in_watt_per_kelvin)
                / x_value
            )
            - self.h_tr_ms_in_watt_per_kelvin
            - self.h_tr_em_in_watt_per_kelvin
        ) / (
            self.c_m_in_joule_per_kelvin / seconds_per_timestep
        )  # ((self.c_m_ref * self.A_f) * 3600)

        b11 = (self.h_tr_ms_in_watt_per_kelvin * self.h_tr_is_in_watt_per_kelvin) / (
            (self.c_m_in_joule_per_kelvin / seconds_per_timestep) * x_value
        )

        b_d11 = (
            (
                self.h_tr_ms_in_watt_per_kelvin
                * self.h_tr_w_in_watt_per_kelvin
                * (self.h_tr_is_in_watt_per_kelvin + self.h_ve_adj_in_watt_per_kelvin)
                / x_value
            )
            + self.h_tr_em_in_watt_per_kelvin
        ) / (self.c_m_in_joule_per_kelvin / seconds_per_timestep)
        b_d12 = (self.h_tr_ms_in_watt_per_kelvin * self.h_tr_is_in_watt_per_kelvin * self.h_ve_adj_in_watt_per_kelvin) / (
            (self.c_m_in_joule_per_kelvin / seconds_per_timestep) * x_value
        )
        b_d13 = (self.h_tr_ms_in_watt_per_kelvin * self.h_tr_is_in_watt_per_kelvin) / (
            (self.c_m_in_joule_per_kelvin / seconds_per_timestep) * x_value
        )
        b_d14 = (self.h_tr_ms_in_watt_per_kelvin * (self.h_tr_is_in_watt_per_kelvin + self.h_ve_adj_in_watt_per_kelvin)) / (
            (self.c_m_in_joule_per_kelvin / seconds_per_timestep) * x_value
        )
        b_d15 = 1 / (self.c_m_in_joule_per_kelvin / seconds_per_timestep)

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

        transition_matrix = np.array([[a11]])  # transition matrix
        selection_matrix = np.array([[b11, b_d11, b_d12, b_d13, b_d14, b_d15]])  # selection matrix
        # C=np.matrix([[c11],[c21]]) #design matrix #comment out due to pylint warning W0612 (unused-variable)
        # D=np.matrix([[d11,d_d11, d_d12,d_d13,d_d14,d_d15],[d21,d_d21, d_d22,d_d23,d_d24,d_d25]]) #comment out due to pylint warning W0612 (unused-variable)

        transition_matrix = transition_matrix * 0.5
        selection_matrix = selection_matrix * 0.5

        return transition_matrix, selection_matrix

    def _compute_open_loop_response(
        self, transition_matrix: np.ndarray, selection_matrix: np.ndarray
    ) -> float:
        """Simulates the open-loop step response and returns the time constant.

        Converts the single-state state-space model into a transfer function,
        drives it with a step input, and extracts the time constant as the time
        at which the response reaches 63.2% of its steady-state value.
        """
        """ time scale and arbitrary input signal to observe the open loop repsone """

        # choosing a sufficiently long interval
        time_interval_ns = 20000
        time_scale = np.linspace(0, time_interval_ns, time_interval_ns + 1)
        # input step signal
        input_step_signal_in_watt = np.zeros(time_interval_ns + 1)
        for i in range(time_interval_ns):
            if i == 0:
                input_step_signal_in_watt = 0 * np.ones(time_interval_ns + 1)
            else:
                input_step_signal_in_watt = 22 * np.ones(time_interval_ns + 1)

        """ Converting the state space model into transfer function.
        We have one state variable wich is the thermal mass temperature and 6 inputs...
        Thermal power delivered is the only controlled input, rest are disturbances.
        Therefore, the transfer function below shows the change in T_m in reponse the constrolled input (thermal power)
        """
        # transfer function:
        tf_tm = control.TransferFunction([selection_matrix[0, 0]], [1, -(transition_matrix[0, 0])])

        # open loop step response (tm_o_in_kelvin is the thermal-mass temperature response in K):
        # timestep_tm_o, tm_o_in_kelvin = control.forced_response(tf_tm, t, u)
        _, tm_o_in_kelvin = control.forced_response(tf_tm, time_scale, input_step_signal_in_watt)
        # save 'timestep_tm_o' in dummy variable due to pylint warning W0612 (unused-variable)
        # since function 'control.forced_response' can only be used with a return value with a tuple of length 2
        # dummy1 = timestep_tm_o

        # steady state value:
        tm_steady_state_in_kelvin = tm_o_in_kelvin[time_interval_ns]

        # time constant "value at 63.2%" :
        t_m_initial_in_kelvin = 0
        tm_at_tau_in_kelvin = t_m_initial_in_kelvin - 0.632 * (t_m_initial_in_kelvin - tm_steady_state_in_kelvin)

        # find time constant tau_p

        def find_nearest(array: np.ndarray, value: float) -> float:
            array = np.asarray(array)
            idx = (np.abs(array - value)).argmin()
            return float(array[idx])

        tm_at_tau_tf_tm_in_kelvin = find_nearest(tm_o_in_kelvin, tm_at_tau_in_kelvin)
        time_constant_in_seconds = 0
        for i in range(time_interval_ns):
            if tm_o_in_kelvin[i] == tm_at_tau_tf_tm_in_kelvin:
                time_constant_in_seconds = i

        return time_constant_in_seconds

    def identify(self) -> None:
        """Builds the state-space model and derives the open-loop gains and time constant."""
        self.transition_matrix, self.selection_matrix = self._build_state_space()

        """ Gains of the uncontrolled systems, used for feedforward implementation """

        # Tm(s)/Pth(s)= K/Ts+1
        self.process_gain_in_kelvin_per_watt = self.selection_matrix[0, 0] / -self.transition_matrix[0, 0]
        # Tm(s)/Tout(s)= K/Ts+1  (dimensionless: dT_m/dT_out in K/K)
        self.t_out_gain_dimensionless = self.selection_matrix[0, 1] / -self.transition_matrix[0, 0]
        # Tm(s)/phi_ia(s)= K/Ts+1
        self.phi_ia_gain_in_kelvin_per_watt = self.selection_matrix[0, 3] / -self.transition_matrix[0, 0]
        # Tm(s)/phi_st(s)= K/Ts+1
        self.phi_st_gain_in_kelvin_per_watt = self.selection_matrix[0, 4] / -self.transition_matrix[0, 0]
        # Tm(s)/phi_m(s)= K/Ts+1
        self.phi_m_gain_in_kelvin_per_watt = self.selection_matrix[0, 5] / -self.transition_matrix[0, 0]

        self.time_constant_in_seconds = self._compute_open_loop_response(self.transition_matrix, self.selection_matrix)


def compute_pi_gains(thermal_model: BuildingThermalModel5R1C) -> tuple[float, float, float]:
    """Computes PI controller gains via pole placement from the identified thermal model.

    The derivative gain is zero, so the controller is PI only. The settling
    time and overshoot assumptions translate the identified open-loop time
    constant into the desired closed-loop pole locations.

    The following description is based on slides 12 to 14 in the file:
    https://fac.ksu.edu.sa/sites/default/files/control_design_by_pole_placement.pdf

    settling time (Ts) = 4/ (damping ratio * natural frequency (omega_n))
    damping frequency (omega_d)=natural frequency * sqrt(1-damping ratio^2)

    desired pole = (- natural frequency * damping ratio) +/-  j (natural frequency * sqrt(1-damping ratio^2))

    Closed loop transfer function =
        (transfer function plant * transfer function controller ) / (1+(transfer function plant * transfer function controller ))

    simplified Closed loop transfer function of a first order system 1/ms+b= (Kp s + Ki) / ms^2+(b+Kp)s+Ki

    denominator = s^2 + (2 * damping ratio * natural frequency ) s + (natural frequency)^2
    """
    time_constant_in_seconds = thermal_model.time_constant_in_seconds
    selection_matrix = thermal_model.selection_matrix
    transition_matrix = thermal_model.transition_matrix

    # Few assumptions
    settling_time_in_seconds = time_constant_in_seconds * 0.3
    over_shooting = 20

    damping_ratio = -np.log(over_shooting / 100) / (np.pi**2 + (np.log(over_shooting / 100)) ** 2) ** (1 / 2)
    natural_frequency_in_rad_per_s = 4 / (settling_time_in_seconds * damping_ratio)
    # damping_frequency=natural_frequency_in_rad_per_s * np.sqrt(1-damping_ratio**2) #comment out due to pylint warning W0612 (unused-variable)

    # m_value (1/b11) and b_value (-a11/b11) are first-order plant coefficients.
    # As computed they are in W/K, but the pole-placement formulas below assume a
    # continuous-time plant (where m_value would be in W*s/K); the discrete-time
    # implementation applies the resulting gains as W/K. The unit is left
    # unsuffixed intentionally rather than masking this ambiguity with a fabricated suffix.
    m_value = 1 / selection_matrix[0, 0]
    b_value = -transition_matrix[0, 0] / selection_matrix[0, 0]

    integral_gain_in_watt_per_kelvin = natural_frequency_in_rad_per_s**2 * m_value
    proportional_gain_in_watt_per_kelvin = (
        natural_frequency_in_rad_per_s * damping_ratio * 2 * m_value
    ) - b_value
    derivative_gain_in_watt_per_kelvin = 0

    log.information(f"gain Ki= {integral_gain_in_watt_per_kelvin}")
    log.information(f"gain Kp= {proportional_gain_in_watt_per_kelvin}")
    return proportional_gain_in_watt_per_kelvin, integral_gain_in_watt_per_kelvin, derivative_gain_in_watt_per_kelvin


class PIDController(cp.Component):
    """PID Controller class.

    The controller has a derivative gain = 0 which makes it PI Only
    Thermal power delived by the air consitioner is manipulated to achieve desired setpoint temperature
    """

    # Inputs
    TemperatureMean: str = "ResidenceTemperature"  # uncontrolled temperature
    TemperatureMeanPrevious: str = "TemperatureMeanPrevious"
    TemperatureAir: str = "TemperatureAir"
    HeatFluxWallNode: str = "HeatFluxWallNode"
    HeatFluxThermalMassNode: str = "HeatFluxThermalMassNode"

    # ouput
    ThermalPowerPID: str = "ThermalPowerPID"
    ThermalEnergyDelivered: str = "ThermalEnergyDelivered"
    error_pvalue: str = "error_p_value"
    error_dvalue: str = "error_d_value"
    error_ivalue: str = "error_i_value"
    error: str = "error_value"
    derivator: str = "derivator"
    integrator: str = "integrator"
    FeedForwardSignal: str = "FeedForwardSignal"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: PIDControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # Identify the building thermal model and tune the PI controller from it.
        self.thermal_model: BuildingThermalModel5R1C = BuildingThermalModel5R1C.from_sim_repository(
            self.my_simulation_parameters.seconds_per_timestep
        )
        (
            proportional_gain_in_watt_per_kelvin,
            integral_gain_in_watt_per_kelvin,
            derivative_gain_in_watt_per_kelvin,
        ) = compute_pi_gains(self.thermal_model)
        # --------------------------------------------------
        # control saturation (manipulated-variable power limits in W)
        self.mv_min_in_watt: float = 0
        self.mv_max_in_watt: float = 5000
        self.integral_gain_in_watt_per_kelvin: float = integral_gain_in_watt_per_kelvin
        self.proportional_gain_in_watt_per_kelvin: float = proportional_gain_in_watt_per_kelvin
        self.derivative_gain_in_watt_per_kelvin: float = derivative_gain_in_watt_per_kelvin
        self.state: PIDState = PIDState(
            integrator_in_celsius=0,
            integrator_d3_in_celsius=0,
            integrator_d4_in_celsius=0,
            integrator_d5_in_celsius=0,
            derivator_in_celsius=0,
            manipulated_variable_in_watt=0,
        )
        self.previous_state: PIDState = self.state.clone()

        self.temperature_mean_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureMean,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heat_flow_rate_to_internal_surface_node_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatFluxWallNode,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )

        self.heat_flow_rate_to_internal_mass_node_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatFluxThermalMassNode,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )

        self.thermal_power_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerPID,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for PV {self.ThermalPowerPID} will follow.",
        )
        self.error_pvalue_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.error_pvalue,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for PV {self.error_pvalue} will follow.",
        )
        self.error_ivalue_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.error_ivalue,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for PV {self.error_ivalue} will follow.",
        )
        self.error_dvalue_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.error_dvalue,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for PV {self.error_dvalue} will follow.",
        )
        self.error_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.error,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for PV {self.error} will follow.",
        )
        self.derivator_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.derivator,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for PV {self.derivator} will follow.",
        )
        self.integrator_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.integrator,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for PV {self.integrator} will follow.",
        )
        self.feed_forward_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.FeedForwardSignal,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for PV {self.FeedForwardSignal} will follow.",
        )

    def get_building_default_connections(self) -> list[cp.ComponentConnection]:
        """Get default inputs from the building component."""

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

    def i_save_state(self) -> None:
        """Saves the internal state at the beginning of each timestep."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the internal state after each iteration."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self) -> list[str]:
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("PID Controller")
        lines.append("Control algorithm of the Air conditioner is: PI \n")
        lines.append(f"Controller Proportional gain is {self.proportional_gain_in_watt_per_kelvin:4.2f} \n")
        lines.append(f"Controller Integral gain is {self.integral_gain_in_watt_per_kelvin:4.2f} \n")
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Core smulation function."""
        if force_convergence:
            return
        # Retrieve Disturbance forecast
        phi_m_in_watt = stsv.get_input_value(self.heat_flow_rate_to_internal_mass_node_channel)
        phi_st_in_watt = stsv.get_input_value(self.heat_flow_rate_to_internal_surface_node_channel)

        # Retrieve building temperature
        building_temperature_in_celsius = stsv.get_input_value(self.temperature_mean_channel)
        # log.information('building_temperature_in_celsius {}'.format(building_temperature_in_celsius))
        feed_forward_signal_in_watt = self.feedforward(phi_st_in_watt, phi_m_in_watt)

        set_point_in_celsius: float = 24.0

        proportional_gain_in_watt_per_kelvin: float = self.proportional_gain_in_watt_per_kelvin  # controller Proportional gain
        integral_gain_in_watt_per_kelvin: float = self.integral_gain_in_watt_per_kelvin  # integral gain
        derivative_gain_in_watt_per_kelvin: float = self.derivative_gain_in_watt_per_kelvin  # Derivative gain

        error_in_celsius = set_point_in_celsius - building_temperature_in_celsius  # e(tk)

        p_value_in_watt = proportional_gain_in_watt_per_kelvin * error_in_celsius
        d_value_in_watt = derivative_gain_in_watt_per_kelvin * (error_in_celsius - self.state.derivator_in_celsius)

        self.state.derivator_in_celsius = error_in_celsius
        self.state.integrator_in_celsius = self.state.integrator_in_celsius + error_in_celsius

        integrator_limit_in_celsius = 500
        if self.state.integrator_in_celsius > integrator_limit_in_celsius:
            self.state.integrator_in_celsius = integrator_limit_in_celsius
        elif self.state.integrator_in_celsius < -integrator_limit_in_celsius:
            self.state.integrator_in_celsius = -integrator_limit_in_celsius

        i_value_in_watt = self.state.integrator_in_celsius * integral_gain_in_watt_per_kelvin
        manipulated_variable_in_watt = p_value_in_watt + i_value_in_watt + d_value_in_watt

        """ Un-comment to prevent heating and cooling in specific periods  """

        # if timestep <= (151-20)*24*3600/self.my_simulation_parameters.seconds_per_timestep:
        #     if manipulated_variable_in_watt + feed_forward_signal_in_watt < 0:
        #         manipulated_variable_in_watt=0
        # if timestep >= 304*24*3600/self.my_simulation_parameters.seconds_per_timestep:
        #     if manipulated_variable_in_watt + feed_forward_signal_in_watt < 0:
        #         manipulated_variable_in_watt=0
        # if (timestep > (151-20)*24*3600/self.my_simulation_parameters.seconds_per_timestep and
        # timestep <= 243*24*3600/self.my_simulation_parameters.seconds_per_timestep):
        #     if manipulated_variable_in_watt + feed_forward_signal_in_watt > 0:
        #         manipulated_variable_in_watt = 0

        self.state.manipulated_variable_in_watt = manipulated_variable_in_watt

        stsv.set_output_value(self.error_pvalue_output_channel, p_value_in_watt)
        stsv.set_output_value(self.error_dvalue_output_channel, d_value_in_watt)
        stsv.set_output_value(self.error_ivalue_output_channel, i_value_in_watt)
        stsv.set_output_value(self.error_output_channel, error_in_celsius)
        stsv.set_output_value(self.integrator_output_channel, self.state.integrator_in_celsius)
        stsv.set_output_value(self.derivator_output_channel, self.state.derivator_in_celsius)
        stsv.set_output_value(self.thermal_power_channel, manipulated_variable_in_watt)
        stsv.set_output_value(self.feed_forward_signal_channel, feed_forward_signal_in_watt)

    def feedforward(self, phi_st_in_watt: float, phi_m_in_watt: float) -> float:
        """Computes the feed-forward disturbance compensation signal in W.

        The gains are derived from the 5R1C state-space model identified by
        :class:`BuildingThermalModel5R1C`.
        """
        process_gain_in_kelvin_per_watt: float = self.thermal_model.process_gain_in_kelvin_per_watt
        phi_st_gain_in_kelvin_per_watt: float = self.thermal_model.phi_st_gain_in_kelvin_per_watt
        phi_m_gain_in_kelvin_per_watt: float = self.thermal_model.phi_m_gain_in_kelvin_per_watt

        feed_forward_signal_in_watt = -(
            (phi_st_gain_in_kelvin_per_watt * phi_st_in_watt)
            + (phi_m_gain_in_kelvin_per_watt * phi_m_in_watt)
        ) / process_gain_in_kelvin_per_watt

        return feed_forward_signal_in_watt

    def determine_conditions(self, current_temperature_in_celsius: float, set_point_in_celsius: float) -> str:
        """For determining heating and cooling mode and implementing a dead zone. Currently disabled."""
        offset_in_degree_celsius = 0.5
        maximum_heating_set_temp_in_celsius = set_point_in_celsius + offset_in_degree_celsius
        minimum_cooling_set_temp_in_celsius = set_point_in_celsius - offset_in_degree_celsius

        mode = "off"
        if mode == "heating" and current_temperature_in_celsius > maximum_heating_set_temp_in_celsius:  # 23 22.5
            return "off"
        if mode == "cooling" and current_temperature_in_celsius < minimum_cooling_set_temp_in_celsius:  # 24 21.5
            mode = "off"
        if mode == "off":
            if current_temperature_in_celsius < set_point_in_celsius:  # 21
                mode = "heating"
            if current_temperature_in_celsius > set_point_in_celsius:  # 26
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
