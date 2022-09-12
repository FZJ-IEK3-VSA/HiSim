# Generic/Built-in
import copy
import numpy as np

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Any
__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""
@dataclass_json
@dataclass
class HeatStorageConfig:
    V_SP_heating_water : float
    V_SP_warm_water : float
    temperature_of_warm_water_extratcion :float
    ambient_temperature :float
    T_sp_ww : float
    T_sp_hw :float

@dataclass_json
@dataclass
class HeatStorageControllerConfig:
    initial_temperature_building : float
    initial_temperature_heating_storage : float

class HeatStorageState:
    def __init__(self, T_sp_ww: float, T_sp_hw: float) -> None:
        self.T_sp_ww = T_sp_ww
        self.T_sp_hw = T_sp_hw
    def clone( self ) -> Any:
        return HeatStorageState( T_sp_ww = self.T_sp_ww,
                                 T_sp_hw = self.T_sp_hw )

class HeatStorage(Component):
    """
    This is a combined storage: buffer storage for heating, and hot water storage for hot water demand.
    It needs, hot water demand, heating demand, building temperature and a control signal choosing which of the two storages to heat as inputs.
    In addition it relies on Outside Temperature, and ThermalInputs of up to 5 heat sources.
    Based on this it evaluates the temperature in the storages based on the energy balance.
    """
    # Inputs
    ThermalDemandHeatingWater = "ThermalDemandHeatingWater"  # Heating Water to regulate room Temperature
    ThermalDemandWarmWater = "ThermalDemandHeating"  # Warmwater for showering, washing etc...
    ControlSignalChooseStorage = "ControlSignalChooseStorage"
    BuildingTemperature = "BuildingTemperature"

    OutsideTemperature = "OutsideTemperature"
    ThermalInputPower1 = "ThermalInputPower1"
    ThermalInputPower2 = "ThermalInputPower2"
    ThermalInputPower3 = "ThermalInputPower3"
    ThermalInputPower4 = "ThermalInputPower4"
    ThermalInputPower5 = "ThermalInputPower5"

    # Outputs
    WaterOutputTemperatureHeatingWater = "WaterOutputTemperatureHeatingWater"
    WaterOutputTemperatureWarmWater = "WaterOutputTemperatureWarmWater"
    WaterOutputStorageforHeaters = "WaterOutputStorageforHeaters"
    # StorageWarmWaterTemperature="StorageWarmWaterTemperature"
    StorageEnergyLoss = "StorageEnergyLoss"
    RealHeatForBuilding = "RealHeatForBuilding"

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config: HeatStorageConfig) -> None:
        super().__init__(name="HeatStorage", my_simulation_parameters=my_simulation_parameters)
        self.V_SP_heating_water = config.V_SP_heating_water
        self.V_SP_warm_water = config.V_SP_warm_water
        self.temperature_of_warm_water_extratcion = config.temperature_of_warm_water_extratcion
        self.ambient_temperature = config.ambient_temperature
        self.cw = 4812

        self.state = HeatStorageState(config.T_sp_ww, config.T_sp_hw)
        self.previous_state = self.state.clone( )

        self.thermal_demand_heating_water: ComponentInput = self.add_input(self.component_name,
                                                                           self.ThermalDemandHeatingWater,
                                                                           lt.LoadTypes.WARM_WATER,
                                                                           lt.Units.WATT,
                                                                           False)

        self.thermal_demand_warm_water: ComponentInput = self.add_input(self.component_name,
                                                                        self.ThermalDemandWarmWater,
                                                                        lt.LoadTypes.WARM_WATER,
                                                                        lt.Units.WATT,
                                                                        False)
        self.control_signal_choose_storage: cp.ComponentInput = self.add_input(self.component_name,
                                                                               self.ControlSignalChooseStorage,
                                                                               lt.LoadTypes.ANY,
                                                                               lt.Units.ANY,
                                                                               False)
        self.building_temperature: cp.ComponentInput = self.add_input(self.component_name,
                                                                      self.BuildingTemperature,
                                                                      lt.LoadTypes.TEMPERATURE,
                                                                      lt.Units.CELSIUS,
                                                                      False)
        self.thermal_input_power1: ComponentInput = self.add_input(self.component_name,
                                                                   self.ThermalInputPower1,
                                                                   lt.LoadTypes.HEATING,
                                                                   lt.Units.WATT,
                                                                   False)
        self.thermal_input_power2: ComponentInput = self.add_input(self.component_name,
                                                                   self.ThermalInputPower2,
                                                                   lt.LoadTypes.HEATING,
                                                                   lt.Units.WATT,
                                                                   False)
        self.thermal_input_power3: ComponentInput = self.add_input(self.component_name,
                                                                   self.ThermalInputPower3,
                                                                   lt.LoadTypes.HEATING,
                                                                   lt.Units.WATT,
                                                                   False)
        self.thermal_input_power4: ComponentInput = self.add_input(self.component_name,
                                                                   self.ThermalInputPower4,
                                                                   lt.LoadTypes.HEATING,
                                                                   lt.Units.WATT,
                                                                   False)
        self.thermal_input_power5: ComponentInput = self.add_input(self.component_name,
                                                                   self.ThermalInputPower5,
                                                                   lt.LoadTypes.HEATING,
                                                                   lt.Units.WATT,
                                                                   False)

        self.T_sp_C_hw: ComponentOutput = self.add_output(self.component_name,
                                                          self.WaterOutputTemperatureHeatingWater,
                                                          lt.LoadTypes.TEMPERATURE,
                                                          lt.Units.CELSIUS)
        self.T_sp_C_ww: ComponentOutput = self.add_output(self.component_name,
                                                          self.WaterOutputTemperatureWarmWater,
                                                          lt.LoadTypes.TEMPERATURE,
                                                          lt.Units.CELSIUS)
        self.UA_SP_C: ComponentOutput = self.add_output(self.component_name,
                                                        self.StorageEnergyLoss,
                                                        lt.LoadTypes.ANY,
                                                        lt.Units.WATT)
        self.T_sp_C: ComponentOutput = self.add_output(self.component_name,
                                                       self.WaterOutputStorageforHeaters,
                                                       lt.LoadTypes.TEMPERATURE,
                                                       lt.Units.CELSIUS)
        self.real_heat_for_building: ComponentOutput = self.add_output(self.component_name,
                                                                       self.RealHeatForBuilding,
                                                                       lt.LoadTypes.HEATING,
                                                                       lt.Units.WATT)
    @staticmethod
    def get_default_config():
        config=HeatStorageConfig(
                V_SP_heating_water = 1000,
                V_SP_warm_water = 100,
                temperature_of_warm_water_extratcion = 32,
                ambient_temperature = 15,
                T_sp_ww=40,
                T_sp_hw=40)
        return config
    def write_to_report(self) -> None:
        pass
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def i_save_state(self) -> None:
        self.previous_state = self.state.clone( )

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def adding_all_possible_mass_flows(self, stsv: cp.SingleTimeStepValues, c_w: float) -> Any:
        production:float = 0
        # function to add all possible mass flows

        if self.thermal_input_power1.source_output is not None:
            production = stsv.get_input_value(self.thermal_input_power1) + production

        if self.thermal_input_power2.source_output is not None:
            production = stsv.get_input_value(self.thermal_input_power2) + production

        if self.thermal_input_power3.source_output is not None:
            production = stsv.get_input_value(self.thermal_input_power3) + production

        if self.thermal_input_power4.source_output is not None:
            production = stsv.get_input_value(self.thermal_input_power4) + production

        if self.thermal_input_power5.source_output is not None:
            production = stsv.get_input_value(self.thermal_input_power5) + production

        return production

    def calculate_new_storage_temperature(self, seconds_per_timestep: int, T_sp: float, production: float, last: float,
                                          c_w: float, V_SP: float) -> Any:

        T_ext_SP = self.ambient_temperature

        m_SP_h = V_SP * 0.99  # Vereinfachung
        UA_SP = 0.0038 * V_SP + 0.85  # Heatloss Storage
        dt = seconds_per_timestep

        # calcutae new Storage Temp.
        T_SP = T_sp + (1 / (m_SP_h * c_w)) * (production - last - UA_SP * (T_sp - T_ext_SP)) * dt
        # T_SP = T_sp + (dt/(m_SP_h*c_w))*(P_h_HS*(T_sp-T_ext_SP) - last*(T_sp-T_ext_SP) - UA_SP*(T_sp-T_ext_SP))
        # Correction Calculation
        # T_sp_k = (T_sp+T_SP)/2
        # T_vl = T_sp_k+2.5

        # calcutae new Storage Temp.
        # T_SP = T_sp + (1/(m_SP_h*c_w))*( production- last - UA_SP*(T_sp_k-T_ext_SP))*dt

        return T_SP, UA_SP

    # def regarding_heating_water_storage (self, T_sp: int):

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:

        T_sp_var_ww = self.state.T_sp_ww  # Start-Temp-Storage
        T_sp_var_hw = self.state.T_sp_hw  # Start-Temp-Storage

        last_var_ww = stsv.get_input_value(self.thermal_demand_warm_water)
        last_var_hw = stsv.get_input_value(self.thermal_demand_heating_water)

        result_ww = [T_sp_var_ww, 0]
        result_hw = [T_sp_var_hw, 0]
        T_sp_C = (T_sp_var_ww + T_sp_var_hw) / 2

        if stsv.get_input_value(self.control_signal_choose_storage) == 1:  # choose to heat up warm water storage
            production_var = self.adding_all_possible_mass_flows(stsv, c_w=self.cw)
            result_ww = self.calculate_new_storage_temperature(
                seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep, T_sp=T_sp_var_ww,
                production=production_var, last=last_var_ww, c_w=self.cw, V_SP=self.V_SP_warm_water)
            T_sp_C = result_ww[0]
            production_var = 0
            result_hw = self.calculate_new_storage_temperature(
                seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep, T_sp=T_sp_var_hw,
                production=production_var, last=last_var_hw, c_w=self.cw, V_SP=self.V_SP_heating_water)

        elif stsv.get_input_value(self.control_signal_choose_storage) == 2:  # choose to heat up heating water storage
            production_var = self.adding_all_possible_mass_flows(stsv, c_w=self.cw)
            result_hw = self.calculate_new_storage_temperature(
                seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep, T_sp=T_sp_var_hw,
                production=production_var, last=last_var_hw, c_w=self.cw, V_SP=self.V_SP_heating_water)

            T_sp_C = result_hw[0]
            production_var = 0
            result_ww = self.calculate_new_storage_temperature(
                seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep, T_sp=T_sp_var_ww,
                production=production_var, last=last_var_ww, c_w=self.cw, V_SP=self.V_SP_warm_water)

        self.state.T_sp_ww = result_ww[0]
        self.state.T_sp_hw = result_hw[0]
        stsv.set_output_value(self.T_sp_C_ww, self.state.T_sp_ww)
        stsv.set_output_value(self.T_sp_C_hw, self.state.T_sp_hw)
        stsv.set_output_value(self.T_sp_C, T_sp_C)
        stsv.set_output_value(self.UA_SP_C, result_ww[1] + result_hw[1])
        stsv.set_output_value(self.real_heat_for_building, last_var_hw)

        # Output Massenstrom von Wasser entspricht dem Input Massenstrom. Nur Temperatur hat sich geändert. Wie ist das zu behandelN?



class HeatStorageController(cp.Component):
    """
    HeatStorageController class calculates on base of the maximal Building
    Thermal Demand and the TemperatureHeatingStorage and Building Tempreature
    the real thermal demand for the Heating Storage.
    This Output is called "RealThermalDemandHeatingWater".

    Parameters
    ----------
    sim_params : Simulator
        Simulator object used to carry the simulation using this class
    """

    # Inputs
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"
    TemperatureHeatingStorage = "TemperatureHeatingStorage"
    BuildingTemperature = "BuildingTemperature"
    RealHeatBuildingDemand= "RealHeatBuildingDemand"
    # Outputs
    RealThermalDemandHeatingWater = "RealThermalDemandHeatingWater"


    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config: HeatStorageControllerConfig
                 ) -> None:
        super().__init__(name="HeatStorageController", my_simulation_parameters=my_simulation_parameters)
        self.initial_temperature_heating_storage = config.initial_temperature_heating_storage
        self.initial_temperature_building = config.initial_temperature_building
        # ===================================================================================================================
        # Inputs
        self.ref_max_thermal_build_demand: ComponentInput = self.add_input(self.component_name,
                                                                           self.ReferenceMaxHeatBuildingDemand,
                                                                           lt.LoadTypes.HEATING,
                                                                           lt.Units.WATT,
                                                                           False)
        self.heating_storage_temperature: ComponentInput = self.add_input(self.component_name,
                                                                          self.TemperatureHeatingStorage,
                                                                          lt.LoadTypes.TEMPERATURE,
                                                                          lt.Units.CELSIUS,
                                                                          False)
        self.building_temperature: ComponentInput = self.add_input(self.component_name,
                                                                   self.BuildingTemperature,
                                                                   lt.LoadTypes.TEMPERATURE,
                                                                   lt.Units.CELSIUS,
                                                                   False)
        self.real_thermal_demand_building = self.add_input(self.component_name,
                                                           self.RealHeatBuildingDemand,
                                                           lt.LoadTypes.HEATING,
                                                           lt.Units.WATT,
                                                           False)
        # Outputs
        self.real_thermal_demand_heating_water: ComponentOutput = self.add_output(self.component_name,
                                                                                  self.RealThermalDemandHeatingWater,
                                                                                  lt.LoadTypes.HEATING,
                                                                                  lt.Units.WATT)
    @staticmethod
    def get_default_config() -> HeatStorageControllerConfig:
        config=HeatStorageControllerConfig(
                initial_temperature_building = 20,
                initial_temperature_heating_storage = 35)
        return config
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def build(self):
        pass

    def write_to_report(self) -> None:
        pass

    def i_save_state(self) -> None:
        pass

    def i_restore_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        T_sp_var_hw = stsv.get_input_value(self.heating_storage_temperature)  # Start-Temp-Storage
        last_var_hw = stsv.get_input_value(self.real_thermal_demand_building)
        max_mass_flow_heat_storage = stsv.get_input_value(self.ref_max_thermal_build_demand) / (
                    4.1851 * 1000 * (self.initial_temperature_heating_storage - self.initial_temperature_building))

        max_last_var_hw = max_mass_flow_heat_storage * 4.185 * 1000 * (
                    T_sp_var_hw - stsv.get_input_value(self.building_temperature))

        if max_last_var_hw < last_var_hw:
            last_var_hw = max_last_var_hw


        stsv.set_output_value(self.real_thermal_demand_heating_water, last_var_hw)
