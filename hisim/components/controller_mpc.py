"""Model Predictive Controller."""

# clean

import datetime
from typing import List, Optional, Any

# from typing import Any
from dataclasses import dataclass

# from statistics import mean
import numpy as np

# from numpy.linalg import inv
from dataclasses_json import dataclass_json

# from scipy.ndimage import interpolation
import casadi as ca

# Owned
from hisim import utils
from hisim import component as cp
from hisim.component import ConfigBase
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components.weather import Weather

# from hisim.components.generic_battery import GenericBattery
# from hisim.components.loadprofilegenerator_connector import Occupancy
# from hisim.components.generic_price_signal import PriceSignal
# from hisim.components.air_conditioner import AirConditioner
# from hisim.components.generic_pv_system import PVSystem
from hisim import log
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

__authors__ = "Marwa Alfouly"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class MpcControllerConfig(ConfigBase):
    """Configuration of the MPC Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return MpcController.get_full_classname()

    building_name: str
    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    mpc_scheme: str
    min_comfort_temp: float
    max_comfort_temp: float
    optimizer_sampling_rate: int
    initial_temeperature: float
    flexibility_element: str
    initial_state_of_charge: float
    # my_simulation_repository: Optional[ cp.SimRepository ]
    # getting forecasted disturbance (weather)
    temp_forecast: List[float]
    phi_m_forecast: List[float]
    phi_st_forecast: list
    phi_ia_forecast: list
    # getting pv forecast
    pv_forecast_yearly: list
    # getting battery specifications
    maximum_storage_capacity: float
    minimum_storage_capacity: float
    maximum_charging_power: float
    maximum_discharging_power: float
    battery_efficiency: float
    inverter_efficiency: float
    # forecasts
    temperature_forecast_24h_1min: list
    phi_m_forecast_24h_1min: list
    phi_ia_forecast_24h_1min: list
    phi_st_forecast_24h_1min: list
    pv_forecast_24h_1min: list
    price_purchase_forecast_24h_1min: list
    price_injection_forecast_24h_1min: list
    optimal_cost: list
    revenues: list
    air_conditioning_electricity: list
    cost_optimal_temperature_set_point: list
    pv2load: list
    electricity_from_grid: list
    electricity_to_grid: list
    battery_to_load: list
    pv_to_battery_timestep: list
    battery_power_flow_timestep: list
    battery_control_state: list
    batt_soc_actual_timestep: list
    batt_soc_normalized_timestep: list
    # thermal building model
    h_tr_w: float
    h_tr_ms: float
    h_tr_em: float
    h_ve_adj: float
    h_tr_is: float
    c_m: float
    cop_coef: List
    eer_coef: List
    predictive: bool
    prediction_horizon: Optional[int]

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default MPC controller."""
        return MpcControllerConfig(
            building_name=building_name,
            name="MpcController",
            mpc_scheme="optimization_once_aday_only",
            min_comfort_temp=21.0,
            max_comfort_temp=23.0,
            optimizer_sampling_rate=15,
            initial_temeperature=22.0,
            flexibility_element="basic_buidling_configuration",
            initial_state_of_charge=10 / 15,
            # my_simulation_repository = [],
            # getting forecasted disturbance (weather)
            temp_forecast=[],
            phi_m_forecast=[],
            phi_st_forecast=[],
            phi_ia_forecast=[],
            # getting pv forecast
            pv_forecast_yearly=[],
            # getting battery specifications
            maximum_storage_capacity=0.0,
            minimum_storage_capacity=0.0,
            maximum_charging_power=0.0,
            maximum_discharging_power=0.0,
            battery_efficiency=0.0,
            inverter_efficiency=0.0,
            # forecasts
            temperature_forecast_24h_1min=[],
            phi_m_forecast_24h_1min=[],
            phi_ia_forecast_24h_1min=[],
            phi_st_forecast_24h_1min=[],
            pv_forecast_24h_1min=[],
            price_purchase_forecast_24h_1min=[],
            price_injection_forecast_24h_1min=[],
            optimal_cost=[],
            revenues=[],
            air_conditioning_electricity=[],
            cost_optimal_temperature_set_point=[],
            pv2load=[],
            electricity_from_grid=[],
            electricity_to_grid=[],
            battery_to_load=[],
            pv_to_battery_timestep=[],
            battery_power_flow_timestep=[],
            battery_control_state=[],
            batt_soc_actual_timestep=[],
            batt_soc_normalized_timestep=[],
            h_tr_w=0.0,
            h_tr_ms=0.0,
            h_tr_em=0.0,
            h_ve_adj=0.0,
            h_tr_is=0.0,
            c_m=0.0,
            cop_coef=[0] * 2,
            eer_coef=[0] * 2,
            predictive=True,
            prediction_horizon=0,
        )


class MPCcontrollerState:
    """Controller state."""

    def __init__(self, t_m: float, soc: float, cost_optimal_thermal_power: list):
        """Constructs all the neccessary attributes for the MPCcontrollerState object."""
        self.t_m: float = t_m
        self.soc: float = soc
        self.cost_optimal_thermal_power: list = cost_optimal_thermal_power

    def clone(self):
        """Copies the Controller State."""
        return MPCcontrollerState(self.t_m, self.soc, self.cost_optimal_thermal_power)


class MpcController(cp.Component):
    """MPC Controller class."""

    # Inputs
    # weather
    TemperatureOutside = "TemperatureOutside"
    # building
    TemperatureMean = "Residence Temperature"

    # Outputs
    TemperatureMeanStateSpace = "TemperatureMeanStateSpace"
    TemperatureSurfaceStateSpace = "TemperatureSurfaceStateSpace"
    TemperatureAirStateSpace = "TemperatureAirStateSpace"
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricityOutput = "ElectricityOutput"
    OperatingMode = "OperatingMode"
    PV2load = "PV2load"
    GridImport = "GridImport"
    GridExport = "GridExport"
    Battery2Load = "Battery2Load"
    PV2Battery = "PV2Battery"
    BatteryChargingDischargingPower = "BatteryChargingDischargingPower"
    BatteryControlState = "BatteryControlState"
    BatteryEnergyContent = "BatteryEnergyContent"
    BatterySoC = "BatterySoC"
    ElectricityCost = "ElectricityCost"
    GenerationRevenue = "GenerationRevenue"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MpcControllerConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
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

        self.my_simulation_parameters = my_simulation_parameters

        self.mpcconfig = config

        self.h_tr_w = self.mpcconfig.h_tr_w
        self.h_tr_ms = self.mpcconfig.h_tr_ms
        self.h_tr_em = self.mpcconfig.h_tr_em
        self.h_ve_adj = self.mpcconfig.h_ve_adj
        self.h_tr_is = self.mpcconfig.h_tr_is
        self.c_m = self.mpcconfig.c_m
        self.cop_coef = self.mpcconfig.cop_coef
        self.eer_coef = self.mpcconfig.eer_coef

        self.build()

        self.statespace()

        self.t_m_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureMean,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.operating_mode_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.OperatingMode,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.OperatingMode} will follow.",
        )

        self.p_th_mpc_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for {self.ThermalEnergyDelivered} will follow.",
        )
        self.p_elec_mpc_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.ElectricityOutput} will follow.",
        )

        self.pv_consumption_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PV2load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.PV2load} will follow.",
        )

        self.grid_import_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.GridImport,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.GridImport} will follow.",
        )
        self.grid_export_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.GridExport,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.GridExport} will follow.",
        )

        self.battery_to_load_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Battery2Load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.Battery2Load} will follow.",
        )

        self.pv_to_battery_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PV2Battery,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.PV2Battery} will follow.",
        )

        self.batt_soc_actual_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.BatteryEnergyContent,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.BatteryEnergyContent} will follow.",
        )
        self.battery_control_state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.BatteryControlState,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.BatteryControlState} will follow.",
        )
        self.battery_charging_discharging_power_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.BatteryChargingDischargingPower,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for {self.BatteryChargingDischargingPower} will follow.",
        )

        self.batt_soc_normalized_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.BatterySoC,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.BatterySoC} will follow.",
        )

        self.costs_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityCost,
            LoadTypes.PRICE,
            Units.EUR_PER_KWH,
            output_description=f"here a description for {self.ElectricityCost} will follow.",
        )

        self.revenues_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.GenerationRevenue,
            LoadTypes.PRICE,
            Units.EUR_PER_KWH,
            output_description=f"here a description for {self.GenerationRevenue} will follow.",
        )

        self.add_default_connections(self.get_weather_default_connections())

        if self.mpcconfig.prediction_horizon is not None:
            self.prediction_horizon = int(
                self.mpcconfig.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
        self.state: MPCcontrollerState = MPCcontrollerState(
            t_m=self.mpcconfig.initial_temeperature,
            soc=self.mpcconfig.initial_state_of_charge,
            cost_optimal_thermal_power=self.prediction_horizon * [0],
        )
        self.previous_state = self.state.clone()

        self.mpc_scheme = self.mpcconfig.mpc_scheme
        self.min_comfort_temp = self.mpcconfig.min_comfort_temp
        self.max_comfort_temp = self.mpcconfig.max_comfort_temp
        self.sampling_rate = self.mpcconfig.optimizer_sampling_rate
        self.flexibility_element = self.mpcconfig.flexibility_element

        self.temp_forecast = self.mpcconfig.temp_forecast
        self.phi_m_forecast = self.mpcconfig.phi_m_forecast
        self.phi_st_forecast = self.mpcconfig.phi_st_forecast
        self.phi_ia_forecast = self.mpcconfig.phi_ia_forecast
        self.pv_forecast_yearly = self.mpcconfig.pv_forecast_yearly
        self.maximum_storage_capacity = self.mpcconfig.maximum_storage_capacity
        self.minimum_storage_capacity = self.mpcconfig.minimum_storage_capacity
        self.maximum_charging_power = self.mpcconfig.maximum_charging_power
        self.maximum_discharging_power = self.mpcconfig.maximum_discharging_power
        self.battery_efficiency = self.mpcconfig.battery_efficiency
        self.inverter_efficiency = self.mpcconfig.inverter_efficiency

        self.temperature_forecast_24h_1min = self.mpcconfig.temperature_forecast_24h_1min
        self.phi_m_forecast_24h_1min = self.mpcconfig.phi_m_forecast_24h_1min
        self.phi_ia_forecast_24h_1min = self.mpcconfig.phi_ia_forecast_24h_1min
        self.phi_st_forecast_24h_1min = self.mpcconfig.phi_st_forecast_24h_1min
        self.pv_forecast_24h_1min = self.mpcconfig.pv_forecast_24h_1min
        self.price_purchase_forecast_24h_1min = self.mpcconfig.price_purchase_forecast_24h_1min
        self.price_injection_forecast_24h_1min = self.mpcconfig.price_injection_forecast_24h_1min
        self.optimal_cost = self.mpcconfig.optimal_cost
        self.revenues = self.mpcconfig.revenues
        self.air_conditioning_electricity = self.mpcconfig.air_conditioning_electricity
        self.cost_optimal_temperature_set_point = self.mpcconfig.investment_costs_in_euro_optimal_temperature_set_point
        self.pv2load = self.mpcconfig.pv2load
        self.electricity_from_grid = self.mpcconfig.electricity_from_grid
        self.electricity_to_grid = self.mpcconfig.electricity_to_grid
        self.battery_to_load = self.mpcconfig.battery_to_load
        self.pv_to_battery_timestep = self.mpcconfig.pv_to_battery_timestep
        self.battery_power_flow_timestep = self.mpcconfig.battery_power_flow_timestep
        self.battery_control_state = self.mpcconfig.battery_control_state
        self.batt_soc_actual_timestep = self.mpcconfig.batt_soc_actual_timestep
        self.batt_soc_normalized_timestep = self.mpcconfig.batt_soc_normalized_timestep

    def get_weather_default_connections(self):
        """Get default connections from the weather component."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                MpcController.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        if self.mpcconfig.predictive:
            """Get forecasted disturbance (weather)"""
            self.temp_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST
            )[: self.my_simulation_parameters.timesteps]
            self.phi_m_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATFLUXTHERMALMASSNODEFORECAST
            )
            self.phi_st_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATFLUXSURFACENODEFORECAST
            )
            self.phi_ia_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATFLUXINDOORAIRNODEFORECAST
            )

            """"getting pv forecast"""
            self.pv_forecast_yearly = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.PVFORECASTYEARLY)

            """ getting battery specifications """
            self.maximum_storage_capacity = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.MAXIMUMBATTERYCAPACITY
            )
            self.minimum_storage_capacity = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.MINIMUMBATTERYCAPACITY
            )
            self.maximum_charging_power = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.MAXIMALCHARGINGPOWER
            )
            self.maximum_discharging_power = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.MAXIMALDISCHARGINGPOWER
            )
            self.battery_efficiency = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.BATTERYEFFICIENCY)
            self.inverter_efficiency = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.INVERTEREFFICIENCY)
            log.information(f"self.inverter_efficiency {format(self.inverter_efficiency)}")

    def build(self):
        """Build function: The function sets important constants and parameters for the calculations."""
        if self.mpcconfig.predictive:
            """getting building physical properties for state space model"""
            self.h_tr_w = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING
            )
            self.h_tr_ms = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS
            )
            self.h_tr_em = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM
            )
            self.h_ve_adj = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION
            )
            self.h_tr_is = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.THERMALTRANSMISSIONSURFACEINDOORAIR
            )
            self.c_m = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.THERMALCAPACITYENVELOPE)
            """"
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
            """

            """ getting cop_coef and eer_coef from the air conditioner omponenent to be used in the cost optimization"""
            self.cop_coef = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.COEFFICIENT_OF_PERFORMANCE_HEATING)
            self.eer_coef = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.ENERGY_EFFICIENY_RATIO_COOLING)

    def statespace(self):
        """State Space Model of the 5R1C network, Used as a prediction model to the building behavior in the MPC."""

        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep

        state_space_matrix_coefficient_x = ((self.h_tr_w + self.h_tr_ms) * (self.h_ve_adj + self.h_tr_is)) + (
            self.h_ve_adj * self.h_tr_is
        )

        # Entries for system matrix
        a11 = (
            ((self.h_tr_ms**2) * (self.h_tr_is + self.h_ve_adj) / state_space_matrix_coefficient_x)
            - self.h_tr_ms
            - self.h_tr_em
        ) / (
            self.c_m / seconds_per_timestep
        )  # ((self.c_m_ref * self.A_f) * 3600)

        # Entries for input matrix
        b11 = (self.h_tr_ms * self.h_tr_is) / ((self.c_m / seconds_per_timestep) * state_space_matrix_coefficient_x)

        b_d11 = (
            (self.h_tr_ms * self.h_tr_w * (self.h_tr_is + self.h_ve_adj) / state_space_matrix_coefficient_x)
            + self.h_tr_em
        ) / (self.c_m / seconds_per_timestep)
        b_d12 = (self.h_tr_ms * self.h_tr_is * self.h_ve_adj) / (
            (self.c_m / seconds_per_timestep) * state_space_matrix_coefficient_x
        )
        b_d13 = (self.h_tr_ms * self.h_tr_is) / ((self.c_m / seconds_per_timestep) * state_space_matrix_coefficient_x)
        b_d14 = (self.h_tr_ms * (self.h_tr_is + self.h_ve_adj)) / (
            (self.c_m / seconds_per_timestep) * state_space_matrix_coefficient_x
        )
        b_d15 = 1 / (self.c_m / seconds_per_timestep)

        # Entries for output matrix
        c11 = (self.h_tr_ms * self.h_tr_is) / state_space_matrix_coefficient_x
        c21 = (self.h_tr_ms * (self.h_tr_is + self.h_ve_adj)) / state_space_matrix_coefficient_x

        # Entries for feedthrough matrix
        d11 = (self.h_tr_ms + self.h_tr_w + self.h_tr_is) / state_space_matrix_coefficient_x
        d21 = self.h_tr_is / state_space_matrix_coefficient_x

        d_d11 = (self.h_tr_w * self.h_tr_is) / state_space_matrix_coefficient_x
        d_d12 = (self.h_tr_ms + self.h_tr_is + self.h_tr_w) * self.h_ve_adj / state_space_matrix_coefficient_x
        d_d13 = (self.h_tr_ms + self.h_tr_is + self.h_tr_w) / state_space_matrix_coefficient_x
        d_d14 = self.h_tr_is / state_space_matrix_coefficient_x
        d_d15 = 0
        d_d21 = (self.h_tr_w * (self.h_tr_is + self.h_ve_adj)) / state_space_matrix_coefficient_x
        d_d22 = (self.h_tr_is * self.h_ve_adj) / state_space_matrix_coefficient_x
        d_d23 = self.h_tr_is / state_space_matrix_coefficient_x
        d_d24 = (self.h_tr_is + self.h_ve_adj) / state_space_matrix_coefficient_x
        d_d25 = 0

        # Build arrays for state space representation
        state_space_system_matrix_a = np.array([[a11]])  # system matrix
        state_space_input_matrix_b_d = np.array([[b_d11, b_d12, b_d13, b_d14, b_d15]])  # input matrix discrete
        state_space_input_matrix_b = np.array([[b11, b_d11, b_d12, b_d13, b_d14, b_d15]])  # input matrix
        state_space_output_matrix_c = np.array([[c11], [c21]])  # output matrix
        state_space_feedthrough_matrix_d_d = np.array(
            [[d_d11, d_d12, d_d13, d_d14, d_d15], [d_d21, d_d22, d_d23, d_d24, d_d25]]
        )  # feedthrough matrix discrete
        state_space_feedthrough_matrix_d = np.array(
            [
                [d11, d_d11, d_d12, d_d13, d_d14, d_d15],
                [d21, d_d21, d_d22, d_d23, d_d24, d_d25],
            ]
        )  # feedthrough matrix

        self.state_space_system_matrix_a = state_space_system_matrix_a * 0.5
        self.state_space_system_matrix_b = state_space_input_matrix_b * 0.5

        return (
            state_space_system_matrix_a,
            state_space_input_matrix_b,
            state_space_input_matrix_b_d,
            state_space_output_matrix_c,
            state_space_feedthrough_matrix_d,
            state_space_feedthrough_matrix_d_d,
            a11,
            b11,
            b_d11,
            b_d12,
            b_d13,
            b_d14,
            b_d15,
        )

    def i_save_state(self):
        """Saves the current state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def write_to_report(self):
        """Writes a report."""
        lines = []
        lines.append(f"Name: {format('Model Predictive Controller')}")
        lines.append("tbd")
        return lines

    def get_forecast_24h(self, start_horizon, sampling_rate):
        """Get yearly weather forecast."""
        # slicing yearly forecast to extract data points for the prediction horizon (24 hours)
        # number of data points extracted equals= self.prediction_horizon = 3600 * 24 h / seconds_per_timestep
        self.temperature_forecast_24h_1min = self.temp_forecast[start_horizon : start_horizon + self.prediction_horizon]
        self.phi_m_forecast_24h_1min = self.phi_m_forecast[start_horizon : start_horizon + self.prediction_horizon]
        self.phi_ia_forecast_24h_1min = self.phi_ia_forecast[start_horizon : start_horizon + self.prediction_horizon]
        self.phi_st_forecast_24h_1min = self.phi_st_forecast[start_horizon : start_horizon + self.prediction_horizon]
        self.pv_forecast_24h_1min = self.pv_forecast_yearly[start_horizon : start_horizon + self.prediction_horizon]

        self.price_purchase_forecast_24h_1min = SingletonSimRepository().get_entry(
            key=SingletonDictKeyEnum.PRICEPURCHASEFORECAST24H
        )
        self.price_injection_forecast_24h_1min = SingletonSimRepository().get_entry(
            key=SingletonDictKeyEnum.PRICEINJECTIONFORECAST24H
        )

        # sampling of the data: Useful if you run hisim at 60 sec per time step and you want fast optimization:
        # Recommended to use 15 min or 20 min

        temperature_forecast_24h = self.temperature_forecast_24h_1min[0::sampling_rate]
        phi_m_forecast_24h = self.phi_m_forecast_24h_1min[0::sampling_rate]
        phi_ia_forecast_24h = self.phi_ia_forecast_24h_1min[0::sampling_rate]
        phi_st_forecast_24h = self.phi_st_forecast_24h_1min[0::sampling_rate]
        price_purchase_forecast_24h = self.price_purchase_forecast_24h_1min[0::sampling_rate]
        price_injection_forecast_24h = self.price_injection_forecast_24h_1min[0::sampling_rate]
        pv_forecast_24h = self.pv_forecast_24h_1min[0::sampling_rate]

        # only take positive values from PV, otherwise mpc will have an ill-posed problem which will raise errors
        pv_forecast_24h = [max(0, value) for value in pv_forecast_24h]
        return (
            temperature_forecast_24h,
            phi_ia_forecast_24h,
            phi_st_forecast_24h,
            phi_m_forecast_24h,
            price_purchase_forecast_24h,
            price_injection_forecast_24h,
            pv_forecast_24h,
        )

    @utils.measure_execution_time
    def optimize(  # noqa: C901
        self,
        temperature_forecast_24h,
        phi_ia_forecast_24h,
        phi_st_forecast_24h,
        phi_m_forecast_24h,
        price_purchase_forecast_24h,
        price_injection_forecast_24h,
        pv_forecast_24h,
        scaled_horizon,
    ):
        """MPC implementation."""
        sampling_rate = int(self.prediction_horizon / scaled_horizon)
        # scaled_horizon = scaled_horizon  # scaled prediction horizon

        # Discretization of the state space model:
        identity_matrix = np.identity(self.state_space_system_matrix_a.shape[0])  # this is an identity matrix
        matrix_a_d = np.array(np.exp(self.state_space_system_matrix_a * sampling_rate))
        matrix_b_d = (
            np.linalg.inv(self.state_space_system_matrix_a)
            * (matrix_a_d - identity_matrix)
            * self.state_space_system_matrix_b
        )

        # numerical values of the disturbances
        disturbance_values = ca.horzcat(
            temperature_forecast_24h,
            temperature_forecast_24h,
            phi_ia_forecast_24h,
            phi_st_forecast_24h,
            phi_m_forecast_24h,
        ).T

        # Numerical values of cop and eer sampled (casadi fromat)

        cop_timestep = []
        eer_timestep = []
        for k in range(int(self.prediction_horizon)):
            cop_timestep.append(self.cop_coef[0] * self.temperature_forecast_24h_1min[k] + self.cop_coef[1])  # cop
            eer_timestep.append(self.eer_coef[0] * self.temperature_forecast_24h_1min[k] + self.eer_coef[1])  # eer

        cop_sampled = cop_timestep[0::sampling_rate]
        eer_sampled = eer_timestep[0::sampling_rate]

        cop_sampled_array: np.ndarray = np.reshape(np.array(cop_sampled), (1, len(cop_sampled)))
        eer_sampled_array: np.ndarray = np.reshape(np.array(eer_sampled), (1, len(eer_sampled)))

        # Numerical values of pv forecast (casadi fromat)
        pv_forecast_24h = np.reshape(np.array(pv_forecast_24h), (1, len(pv_forecast_24h)))

        p_el: np.ndarray = np.reshape(np.array(price_purchase_forecast_24h), (1, len(price_purchase_forecast_24h)))

        feed_in_tariff: np.ndarray = np.reshape(
            np.array(price_injection_forecast_24h),
            (1, len(price_injection_forecast_24h)),
        )

        # symbolic defenition of system variables:

        # 1. manipulated variable
        phi_hc = ca.MX.sym("phi_hc")

        # 2. state: controlled variable (thermal power delivered)
        t_m = ca.MX.sym("t_m")

        # 3. Disturbances (outside temperature - solar gains - internal gains - dynamic price signal)
        t_out = ca.MX.sym("t_out")
        t_sup = ca.MX.sym("t_sup")
        phi_ia = ca.MX.sym("phi_ia")
        phi_st = ca.MX.sym("phi_st")
        phi_m = ca.MX.sym("phi_m")

        disturbances = ca.vertcat(t_out, t_sup, phi_ia, phi_st, phi_m)
        n_disturbances = disturbances.numel()

        t_m_discrete = (
            matrix_a_d * t_m
            + matrix_b_d[0, 0] * phi_hc
            + matrix_b_d[0, 1] * t_out
            + matrix_b_d[0, 2] * t_sup
            + matrix_b_d[0, 3] * phi_ia
            + matrix_b_d[0, 4] * phi_st
            + matrix_b_d[0, 5] * phi_m
        )

        supporting_points_multiple_shooting_continuity_condition = ca.Function(
            "F",
            [t_m, phi_hc, t_out, t_sup, phi_ia, phi_st, phi_m],
            [t_m_discrete],
            ["t_m", "phi_hc", "t_out", "t_sup", "phi_ia", "phi_st", "phi_m"],
            ["t_m_next"],
        )

        # multiple shooting approach: more than one decision variable (state variables, thermal power, grid import, ...)

        opti = ca.Opti()

        optvar_temperature = opti.variable(1, scaled_horizon + 1)  # state variable: controlled temperature
        optvar_power_thermal_delivered = opti.variable(
            1, scaled_horizon
        )  # manipulated variable: thermal power delivered
        optvar_disturbances = opti.variable(n_disturbances, scaled_horizon)
        # disturbances:
        # 1. ambient temperature
        # 2. supply temperature = ambient temperature
        # 3. heat flux to the node Ti (indoor air)
        # 4. heat flux to the node s (internal surfaces)
        # 5. heat flux to thermal mass node
        optvar_power_bought_from_grid = opti.variable(1, scaled_horizon)

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            optvar_power_pv = opti.variable(1, scaled_horizon)
            optvar_power_sold_to_grid = opti.variable(1, scaled_horizon)
            optvar_power_pv_generation_forecasted = opti.variable(1, scaled_horizon)

        if self.flexibility_element == "PV_and_Battery":
            optvar_battery_soc = opti.variable(1, scaled_horizon + 1)
            optvar_battery_power_charging = opti.variable(1, scaled_horizon)
            optvar_battery_power_discharging = opti.variable(1, scaled_horizon)
            optvar_battery_power_flow = opti.variable(1, scaled_horizon)
            # flow=opti.variable(1,N)

        x_init = opti.parameter(1, 1)
        # u_init=opti.parameter(1,1)
        disturbance_forecast = opti.parameter(n_disturbances, scaled_horizon)
        cop_values = opti.parameter(
            1, scaled_horizon
        )  # coefiiecient of performance: heating air conditioner efficiency
        eer_values = opti.parameter(1, scaled_horizon)  # energy efficiency ratio: cooling air conditioner efficiency

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            pv_production = opti.parameter(1, scaled_horizon)

        if self.flexibility_element == "PV_and_Battery":
            soc_init = opti.parameter(1, 1)

        # Cost Function

        if self.flexibility_element == "basic_buidling_configuration":
            opti.minimize(sum(ca.horzsplit(p_el * optvar_power_bought_from_grid, 1)))

        if self.flexibility_element == "PV_only":
            # a weighting factor of 0.5 is added to the revenue to priortize using the pv production instead of selling to the grid
            opti.minimize(
                sum(
                    ca.horzsplit(
                        (p_el * optvar_power_bought_from_grid - 0.5 * feed_in_tariff * optvar_power_sold_to_grid)
                    )
                )
            )

        if self.flexibility_element == "PV_and_Battery":
            opti.minimize(
                sum(
                    ca.horzsplit(
                        (p_el * optvar_power_bought_from_grid - 0.5 * feed_in_tariff * optvar_power_sold_to_grid)
                    )
                )
            )

        # Constraints
        for k in range(scaled_horizon):
            opti.subject_to(
                optvar_temperature[:, k + 1]
                == supporting_points_multiple_shooting_continuity_condition(
                    optvar_temperature[:, k],
                    optvar_power_thermal_delivered[:, k],
                    disturbance_forecast[0, k],
                    disturbance_forecast[1, k],
                    disturbance_forecast[2, k],
                    disturbance_forecast[3, k],
                    disturbance_forecast[4, k],
                )
            )
            if self.flexibility_element == "PV_and_Battery":
                opti.subject_to(
                    optvar_battery_soc[:, k + 1]
                    == optvar_battery_soc[:, k]
                    + (optvar_battery_power_flow[:, k])
                    * (self.my_simulation_parameters.seconds_per_timestep * sampling_rate / 3600)
                )

        opti.subject_to(opti.bounded(self.min_comfort_temp, optvar_temperature, self.max_comfort_temp))

        """ a Terminal Constraint is added if Hisim resolution is different than the optimizer resolution:
            e.g, if you run hisim at 60 sec per time step and you would like to reduce the optimization is done by sampling each 20 or 15 min
            This ensures that inital guess at the following optimization is within the constraint"""

        if sampling_rate != 1:
            opti.subject_to(optvar_temperature[-1] > self.min_comfort_temp + 0.3)

        opti.subject_to(opti.bounded(0, ca.fabs(optvar_power_thermal_delivered), 16000))

        if self.flexibility_element == "basic_buidling_configuration":
            opti.subject_to(
                optvar_power_bought_from_grid
                == ca.if_else(
                    optvar_power_thermal_delivered > 0,
                    ca.fabs(optvar_power_thermal_delivered) / cop_values,
                    ca.fabs(optvar_power_thermal_delivered) / eer_values,
                )
            )

        if self.flexibility_element == "PV_only":
            """Energy Balance constraint for Grid , PV  interaction"""
            opti.subject_to(
                optvar_power_bought_from_grid
                == ca.if_else(
                    optvar_power_thermal_delivered > 0,
                    ca.fabs(optvar_power_thermal_delivered) / cop_values - (pv_production - optvar_power_sold_to_grid),
                    ca.fabs(optvar_power_thermal_delivered) / eer_values - (pv_production - optvar_power_sold_to_grid),
                )
            )
            opti.subject_to(optvar_power_pv == pv_production - optvar_power_sold_to_grid)

        if self.flexibility_element == "PV_and_Battery":
            """Battery charging and discharging bounds / making sure that charging and discharging doesn't occur at the same time"""
            opti.subject_to(
                opti.bounded(
                    self.minimum_storage_capacity,
                    optvar_battery_soc,
                    self.maximum_storage_capacity,
                )
            )
            opti.subject_to(
                opti.bounded(
                    -self.maximum_discharging_power,
                    optvar_battery_power_flow,
                    self.maximum_charging_power,
                )
            )
            opti.subject_to(
                optvar_battery_power_charging
                == ca.if_else(
                    optvar_battery_power_flow > 0,
                    optvar_battery_power_flow * self.battery_efficiency,
                    0,
                )
            )
            opti.subject_to(
                optvar_battery_power_discharging
                == ca.if_else(
                    optvar_battery_power_flow < 0,
                    -optvar_battery_power_flow / (self.battery_efficiency * self.inverter_efficiency),
                    0,
                )
            )
            opti.subject_to(opti.bounded(0, optvar_battery_power_charging, self.maximum_charging_power))

            """ Energy Balance constraint for Grid , PV , Battery interaction"""

            opti.subject_to(
                optvar_power_bought_from_grid
                == ca.if_else(
                    optvar_power_thermal_delivered > 0,
                    ca.fabs(optvar_power_thermal_delivered) / cop_values
                    - optvar_power_pv
                    - optvar_battery_power_discharging,
                    ca.fabs(optvar_power_thermal_delivered) / eer_values
                    - optvar_power_pv
                    - optvar_battery_power_discharging,
                )
            )
            opti.subject_to(
                optvar_power_sold_to_grid == pv_production - optvar_battery_power_charging - optvar_power_pv
            )

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            opti.subject_to(opti.bounded(0, optvar_power_sold_to_grid, pv_production))
            opti.subject_to(optvar_power_bought_from_grid >= 0)
            opti.subject_to(
                optvar_power_bought_from_grid
                <= ca.if_else(
                    optvar_power_thermal_delivered > 0,
                    ca.fabs(optvar_power_thermal_delivered) / cop_values,
                    ca.fabs(optvar_power_thermal_delivered) / eer_values,
                )
            )

        """ Initial conditions """
        opti.subject_to(optvar_temperature[:, 0] == x_init)  # controlled temperature temperature
        opti.subject_to(
            optvar_disturbances == disturbance_forecast
        )  # building disturbances (solar gains / internal gains / ambient temperature)

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            opti.subject_to(
                optvar_power_pv_generation_forecasted == pv_production
            )  # forecasted PV generation by the generic_pv_component

        if self.flexibility_element == "PV_and_Battery":
            opti.subject_to(optvar_battery_soc[:, 0] == soc_init)  # battery state of charge

        """ Choose a concerete solver: The default linear solver used with ipopt is mumps (MUltifrontal Massively Parallel Solver).
        For a faster solution, the default solver is replaced with HSL solver 'ma27' (see 'sol_opts' > 'linear_solver').
        A free version is available for Academic Purposes ONLY. Please follow the steps provided at the end of the script to obtain,
        compile, and interface HSL solver.

        * Remark: When including the battery in the building energy system one optimization time step with { HiSim time_step = 60 sec
        and sampling rate = 1 } takes around 57 sec using the ma27 solver. The aforementioned is only for the optimization and not
        the entire household.

        This's  reduced to 0.5 sec with { HiSim time_step = 60 sec and sampling rate = 15 }
        or { HiSim time_step = 60*20 sec and sampling rate = 1 } and 'ma27' solver and even less with sampling
        rate of 20 min.

        Also it is not guarnteed that a one year simulation will work for all systems with the default solver.

        However, it is possible to do the simulation with the default solver for a building with PV installation
        ONLY.
        """

        sol_opts = {
            "ipopt": {
                "max_iter": 500,
                # "max_iter": 2000,
                "print_level": 5,
                # "print_level": 0,
                "sb": "yes",
                # "acceptable_tol": 4.0e+005,
                # "acceptable_tol": 1e-2,
                "linear_solver": "mumps",  # options: 'mumps','ma27'
                # 'linear_solver':'ma27',
                # "acceptable_obj_change_tol": 4.0e+005,
                # "acceptable_obj_change_tol": 1e-3,
            },
            "print_time": False,
        }
        opti.solver("ipopt", sol_opts)

        # numerical values of the parameter

        opti.set_value(x_init, self.state.t_m)
        opti.set_value(disturbance_forecast, disturbance_values)
        opti.set_value(cop_values, cop_sampled_array)
        opti.set_value(eer_values, eer_sampled_array)

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            opti.set_value(pv_production, pv_forecast_24h)

        if self.flexibility_element == "PV_and_Battery":
            opti.set_value(soc_init, self.state.soc)

        print("Starting solve", datetime.datetime.now())
        sol = opti.solve()
        # opti.debug.value

        # solution optimize resolution
        # t_m_opt=sol.value(x)
        p_th_opt = sol.value(optvar_power_thermal_delivered)
        grid_import = sol.value(optvar_power_bought_from_grid)

        # solution for actual HiSim timestep
        p_th_opt_timstep = np.repeat(p_th_opt, sampling_rate).tolist()
        grid_import_timestep = np.repeat(grid_import, sampling_rate).tolist()

        energy_efficiency_timestep = [0] * int(self.prediction_horizon)
        for i in range(int(self.prediction_horizon)):
            if p_th_opt_timstep[i] > 0:
                energy_efficiency_timestep[i] = cop_timestep[i]
            else:
                energy_efficiency_timestep[i] = eer_timestep[i]
        airconditioning_electrcitiy_consumption = [
            abs(p_th_opt_timstep[i]) / energy_efficiency_timestep[i] for i in range(int(self.prediction_horizon))
        ]

        # optimizer solution might lead to values like 1.5e-9 ---> these are replaces with zeros
        for i in range(self.prediction_horizon):
            if abs(p_th_opt_timstep[i]) < 0.1:
                p_th_opt_timstep[i] = 0
            if abs(grid_import_timestep[i]) < 0.1:
                grid_import_timestep[i] = 0
            if abs(airconditioning_electrcitiy_consumption[i]) < 0.1:
                airconditioning_electrcitiy_consumption[i] = 0

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            # solution optimize resolution
            pv_consumption = sol.value(optvar_power_pv)
            grid_export = sol.value(optvar_power_sold_to_grid)

            # solution for actual HiSim timestep
            pv_consumption_timestep = np.repeat(pv_consumption, sampling_rate).tolist()
            grid_export_timestep = np.repeat(grid_export, sampling_rate).tolist()

            # optimizer solution might lead to values like 1.5e-9 ---> these are replaces with zeros
            for i in range(self.prediction_horizon):
                if abs(pv_consumption_timestep[i]) < 0.1:
                    pv_consumption_timestep[i] = 0

                if abs(grid_export_timestep[i]) < 0.1:
                    grid_export_timestep[i] = 0

        if self.flexibility_element == "PV_and_Battery":
            # solution optimize resolution
            battery_to_load = sol.value(optvar_battery_power_discharging)
            pv_to_battery = sol.value(optvar_battery_power_charging)
            optvar_battery_power_flow = sol.value(optvar_battery_power_flow)
            batt_soc_actual = sol.value(optvar_battery_soc)
            batt_soc_normalized = sol.value(optvar_battery_soc) / self.maximum_storage_capacity
            if self.mpc_scheme == "optimization_once_aday_only":
                self.state.soc = batt_soc_actual[-1]
                if self.state.soc < 0.2 * self.maximum_storage_capacity:
                    self.state.soc = 0.2 * self.maximum_storage_capacity
            if self.mpc_scheme == "moving_horizon_control":
                self.state.soc = batt_soc_actual[1]

            # solution for actual HiSim timestep
            battery_to_load_timstep = np.repeat(battery_to_load, sampling_rate).tolist()
            pv_to_battery_timestep = np.repeat(pv_to_battery, sampling_rate).tolist()
            batt_soc_actual_timestep = np.repeat(batt_soc_actual, sampling_rate).tolist()
            batt_soc_normalized_timestep = np.repeat(batt_soc_normalized, sampling_rate).tolist()
            battery_power_flow_timestep = np.repeat(optvar_battery_power_flow, sampling_rate).tolist()

            # optimizer solution might lead to values like 1.5e-9 ---> these are replaces with zeros
            for i in range(self.prediction_horizon):
                if abs(battery_to_load_timstep[i]) < 0.1:
                    battery_to_load_timstep[i] = 0

                if abs(pv_to_battery_timestep[i]) < 0.1:
                    pv_to_battery_timestep[i] = 0

                if abs(batt_soc_actual_timestep[i]) < 0.1:
                    batt_soc_actual_timestep[i] = 0

                if abs(batt_soc_normalized_timestep[i]) < 0.1:
                    batt_soc_normalized_timestep[i] = 0

                if abs(battery_power_flow_timestep[i]) < 0.1:
                    battery_power_flow_timestep[i] = 0

        # calculated and can be used later if the control structure is modified to casacaded PID-
        t_m_opt_timestep = []
        t_m_init = self.state.t_m
        matrix_a_d = np.linalg.inv(identity_matrix - self.state_space_system_matrix_a)
        matrix_b_d = matrix_a_d * self.state_space_system_matrix_b
        for i in range(int(self.prediction_horizon)):
            t_m_next = (
                matrix_a_d * t_m_init
                + matrix_b_d[0, 0] * p_th_opt_timstep[i]
                + matrix_b_d[0, 1] * self.temperature_forecast_24h_1min[i]
                + matrix_b_d[0, 2] * self.temperature_forecast_24h_1min[i]
                + matrix_b_d[0, 3] * self.phi_ia_forecast_24h_1min[i]
                + matrix_b_d[0, 4] * self.phi_st_forecast_24h_1min[i]
                + matrix_b_d[0, 5] * self.phi_m_forecast_24h_1min[i]
            )
            t_m_init = t_m_next
            t_m_opt_timestep.append(t_m_next[0, 0])

        if self.flexibility_element == "basic_buidling_configuration":
            return (
                p_th_opt_timstep,
                airconditioning_electrcitiy_consumption,
                t_m_opt_timestep,
            )

        if self.flexibility_element == "PV_only":
            return (
                p_th_opt_timstep,
                airconditioning_electrcitiy_consumption,
                pv_consumption_timestep,
                grid_import_timestep,
                grid_export_timestep,
                t_m_opt_timestep,
            )

        if self.flexibility_element == "PV_and_Battery":
            return (
                p_th_opt_timstep,
                airconditioning_electrcitiy_consumption,
                pv_consumption_timestep,
                grid_import_timestep,
                grid_export_timestep,
                battery_to_load_timstep,
                pv_to_battery_timestep,
                battery_power_flow_timestep,
                batt_soc_actual_timestep,
                batt_soc_normalized_timestep,
                t_m_opt_timestep,
            )

        return None

    def cost_calculation(self, grid_export_timestep, grid_import_timestep):
        """Calculate cost of cooling consumption and revenue for buildings with renewables."""
        optimal_cost = []
        revenue = []
        for i in range(int(self.prediction_horizon)):
            costs = grid_import_timestep[i] * self.price_purchase_forecast_24h_1min[i]
            revenues = grid_export_timestep[i] * self.price_injection_forecast_24h_1min[i]
            optimal_cost.append(costs)
            revenue.append(revenues)

        return optimal_cost, revenue

    def cost_calculation_no_flexibility_element(self, airconditioning_electrcitiy_consumption):
        """Calculate cost of cooling for buildings without renewables."""
        optimal_cost = []
        for i in range(int(self.prediction_horizon)):
            costs = airconditioning_electrcitiy_consumption[i] * self.price_purchase_forecast_24h_1min[i]
            optimal_cost.append(costs)

        return optimal_cost

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:  # noqa: C901
        """Start simulation of the MPC here."""
        # t_m_old = stsv.get_input_value(self.t_m_channel)
        if self.mpcconfig.predictive:
            if (self.mpc_scheme == "optimization_once_aday_only" and timestep % self.prediction_horizon == 0) or (
                self.mpc_scheme == "moving_horizon_control"
                and timestep <= self.my_simulation_parameters.timesteps - self.prediction_horizon
            ):
                if self.my_simulation_parameters.seconds_per_timestep >= 15 * 60:
                    sampling_rate = 1
                else:
                    sampling_rate = self.sampling_rate
                scaled_horizon = int(
                    self.prediction_horizon / sampling_rate
                )  # number of points are reduced from 1440 to this value

                (
                    temperature_forecast_24h,
                    phi_ia_forecast_24h,
                    phi_st_forecast_24h,
                    phi_m_forecast_24h,
                    price_purchase_forecast_24h,
                    price_injection_forecast_24h,
                    pv_forecast_24h,
                ) = self.get_forecast_24h(timestep, sampling_rate)

                if self.flexibility_element == "basic_buidling_configuration":
                    (
                        p_th_opt_timstep,
                        airconditioning_electrcitiy_consumption,
                        t_m_opt_timestep,
                    ) = self.optimize(
                        temperature_forecast_24h,
                        phi_ia_forecast_24h,
                        phi_st_forecast_24h,
                        phi_m_forecast_24h,
                        price_purchase_forecast_24h,
                        price_injection_forecast_24h,
                        pv_forecast_24h,
                        scaled_horizon,
                    )
                    self.optimal_cost = self.cost_calculation_no_flexibility_element(
                        airconditioning_electrcitiy_consumption
                    )
                    self.revenues = [0] * self.prediction_horizon

                    self.state.cost_optimal_thermal_power = p_th_opt_timstep
                    self.air_conditioning_electricity = airconditioning_electrcitiy_consumption
                    self.cost_optimal_temperature_set_point = t_m_opt_timestep

                if self.flexibility_element == "PV_only":
                    (
                        p_th_opt_timstep,
                        airconditioning_electrcitiy_consumption,
                        pv_consumption_timestep,
                        grid_import_timestep,
                        grid_export_timestep,
                        t_m_opt_timestep,
                    ) = self.optimize(
                        temperature_forecast_24h,
                        phi_ia_forecast_24h,
                        phi_st_forecast_24h,
                        phi_m_forecast_24h,
                        price_purchase_forecast_24h,
                        price_injection_forecast_24h,
                        pv_forecast_24h,
                        scaled_horizon,
                    )
                    self.optimal_cost, self.revenues = self.cost_calculation(grid_export_timestep, grid_import_timestep)

                    self.state.cost_optimal_thermal_power = p_th_opt_timstep
                    self.air_conditioning_electricity = airconditioning_electrcitiy_consumption
                    self.pv2load = pv_consumption_timestep
                    self.electricity_from_grid = grid_import_timestep
                    self.electricity_to_grid = grid_export_timestep
                    self.cost_optimal_temperature_set_point = t_m_opt_timestep

                if self.flexibility_element == "PV_and_Battery":
                    (
                        p_th_opt_timstep,
                        airconditioning_electrcitiy_consumption,
                        pv_consumption_timestep,
                        grid_import_timestep,
                        grid_export_timestep,
                        battery_to_load_timstep,
                        pv_to_battery_timestep,
                        battery_power_flow_timestep,
                        batt_soc_actual_timestep,
                        batt_soc_normalized_timestep,
                        t_m_opt_timestep,
                    ) = self.optimize(
                        temperature_forecast_24h,
                        phi_ia_forecast_24h,
                        phi_st_forecast_24h,
                        phi_m_forecast_24h,
                        price_purchase_forecast_24h,
                        price_injection_forecast_24h,
                        pv_forecast_24h,
                        scaled_horizon,
                    )
                    self.optimal_cost, self.revenues = self.cost_calculation(grid_export_timestep, grid_import_timestep)

                    self.state.cost_optimal_thermal_power = p_th_opt_timstep
                    self.air_conditioning_electricity = airconditioning_electrcitiy_consumption
                    self.pv2load = pv_consumption_timestep
                    self.electricity_from_grid = grid_import_timestep
                    self.electricity_to_grid = grid_export_timestep
                    self.battery_to_load = battery_to_load_timstep
                    self.pv_to_battery_timestep = pv_to_battery_timestep
                    self.battery_power_flow_timestep = battery_power_flow_timestep

                    self.battery_control_state = []
                    for i in range(int(self.prediction_horizon)):
                        if battery_to_load_timstep[i] != 0:
                            state = -1  # inform the battery that energy is withdrawn
                            self.battery_control_state.append(state)
                        elif pv_to_battery_timestep[i] != 0:
                            state = 1
                            self.battery_control_state.append(state)  # inform the battery that energy is charged
                        else:
                            state = 0  # No cahrging or discharging
                            self.battery_control_state.append(state)

                    self.batt_soc_actual_timestep = batt_soc_actual_timestep
                    self.batt_soc_normalized_timestep = batt_soc_normalized_timestep
                    self.cost_optimal_temperature_set_point = t_m_opt_timestep

        if self.mpc_scheme == "optimization_once_aday_only":
            applied_optimal_solution_index = timestep % self.prediction_horizon
            self.state.t_m = self.cost_optimal_temperature_set_point[-1]
        elif self.mpc_scheme == "moving_horizon_control":
            applied_optimal_solution_index = 0
            self.state.t_m = self.cost_optimal_temperature_set_point[0]
        if (
            self.mpc_scheme == "moving_horizon_control"
            and timestep > self.my_simulation_parameters.timesteps - self.prediction_horizon
        ):
            applied_optimal_solution_index = timestep % self.prediction_horizon

        """ the air conditioner recieves electric power signal (always +ve). The binary variable operating_mode will distinguish
        heating and cooling: heating = 1 , cooling = -1 , off = 0"""
        if self.state.cost_optimal_thermal_power[applied_optimal_solution_index] > 0:
            operating_mode = 1
        elif self.state.cost_optimal_thermal_power[applied_optimal_solution_index] < 0:
            operating_mode = -1
        else:
            operating_mode = 0

        stsv.set_output_value(self.operating_mode_channel, operating_mode)
        stsv.set_output_value(
            self.p_th_mpc_channel,
            self.state.cost_optimal_thermal_power[applied_optimal_solution_index],
        )
        stsv.set_output_value(
            self.p_elec_mpc_channel,
            self.air_conditioning_electricity[applied_optimal_solution_index],
        )
        stsv.set_output_value(self.costs_channel, self.optimal_cost[applied_optimal_solution_index])
        stsv.set_output_value(self.revenues_channel, self.revenues[applied_optimal_solution_index])

        if self.flexibility_element == "basic_buidling_configuration":
            stsv.set_output_value(
                self.grid_import_channel,
                self.air_conditioning_electricity[applied_optimal_solution_index],
            )

        if self.flexibility_element in {"PV_only", "PV_and_Battery"}:
            stsv.set_output_value(
                self.pv_consumption_channel,
                self.pv2load[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.grid_import_channel,
                self.electricity_from_grid[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.grid_export_channel,
                self.electricity_to_grid[applied_optimal_solution_index],
            )

        if self.flexibility_element == "PV_and_Battery":
            stsv.set_output_value(
                self.battery_to_load_channel,
                self.battery_to_load[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.pv_to_battery_channel,
                self.pv_to_battery_timestep[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.batt_soc_actual_channel,
                self.batt_soc_actual_timestep[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.batt_soc_normalized_channel,
                self.batt_soc_normalized_timestep[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.battery_control_state_channel,
                self.battery_control_state[applied_optimal_solution_index],
            )
            stsv.set_output_value(
                self.battery_charging_discharging_power_channel,
                abs(self.battery_power_flow_timestep[applied_optimal_solution_index]),
            )

        """end MPC here """


"""Follow the steps below to install HSL solvers.

HSL Solvers: "HSL. A collection of Fortran codes for large scale scientific computation. http://www.hsl.rl.ac.uk/"


How to install HSL solver for Windows and interface to Casadi?

1. Submit an application for Academic Licence through: https://www.hsl.rl.ac.uk/download/coinhsl/2021.05.05/

2. Wait for an email containing a download link (No longer than one working day)

3. Download msys64 from https://www.msys2.org/

4. Follow the instruction to install mingw-w64 GCC
   pacman -S mingw-w64-ucrt-x86_64-gcc

After the download you will need pakages like OPENBLAS, LAPACK, cmake,fortran. These can be installed in Mingw64 command window with the following commands:
   pacman -S mingw-w64-x86_64-gcc-fortran
   pacman -S mingw-w64-x86_64-cmake
   pacman -S mingw-w64-x86_64-openblas
   pacman -S mingw-w64-x86_64-lapack

Depending on what is available in you machine, you may need to install other package. You will get an error if
something was missing. It is most likely easy to find a suitable solution by googling the error you got.

5. In Mingw64 command prompt, download Metis-4.0 then upack it by the following commands:
   - wget http://glaros.dtc.umn.edu/gkhome/fetch/sw/metis/OLD/metis-4.0.3.tar.gz
   - tar -xvf metis-4.0.3.tar.gz

6. Unpacked the HSL source code you obtained in step 2 and rename coinhsl-x.y.z to coinhsl

7. Move the unpacked Metis-4.03 (step 5) into the resulting folder in step 6

8. Run the command: git clone https://github.com/coin-or-tools/ThirdParty-HSL.git

9. Move the folder coinhsl to the folder ThirdParty-HSL
   - You should have a path similar to "C:----------/ThirdParty-HSL/coinhsl/metis-5.1.0"

10. Excute the following commands:
    - cd ThirdParty-HSL
    - /configure  --with-blas="-lopenblas" CXXFLAGS="-O3 -fopenmp" FCFLAGS="-O3 -fopenmp" CFLAGS="-O3 -fopenmp"
    - make
    - make install
11. After step 10, you should find a folder called ".libs". In this folder you have "libcoinhsl-0.dll"

12. You need to create a symbolic link usig the follwoign command (if needed adjust the path)

    - ln -s .libs/libcoinhsl-0.dll      .libs/libhsl.dll

13. Last to be able to use HSL with HiSim
    copy the content of the file .libs and place in  "------/.conda/envs/hisimvenv/Lib/site-packages/casadi"

Remark: After sucessfully performing the steps 1 to 13, I needed to wait for few hours until it was possible for
ipopt to see solvers. If you run into some issues after following the above step, you may need to wait for
sometime as well.

For further assistance you may write to: marwa.alfouly@tum.de
If you intend to wrok with casadi, it is helpful to check the group https://groups.google.com/g/casadi-users.

"""


"""Future Wrok.

How to enhnce receeding horizon implementaion?

Receding horizon simulation where the optimal control problem is solved at each time step is computationally
intensive. Several limitations emerged during the course of this research.
•	Performing annual simulations for economic studies of air conditioning loads with PV and battery installations
require hours.
•	Moreover, a considerable difference (could exceed one hour) in the simulation time for different regions is
observed despite the identical formulation of the optimal control problem.
•	In addition, while simulations were successfully carried out for seven locations, this is not guaranteed for
all future attempts at different sites. Even though recursive feasibility of the optimization is ensured, IPOPT
throws restoration phase failed message. After excluding possible causes like bad formulation or non-existing
derivatives, it is observed that proper scaling of the optimal control can ensure successful simulation.


Overall, proper scaling of the objective function, decision variables and other uncontrolled parameters can
facilitate the computational burden and thereby reduce simulation time. The developer of CasADi, Dr. Andersson,
highlighted the importance of scaling in optimal control even when using IPOPT that provides auto-scaling [110].
However, dynamics of heat transfer in different buildings and in different climatic conditions will be different.
For best performance, scaling factors should be simulation-specific.

For future work, it is suggested to investigate techniques for autoscaling that are customized to building HVAC
systems. It is also interesting to investigate solutions to reach 100% self-sufficiency of the air conditioning
load.

"""
