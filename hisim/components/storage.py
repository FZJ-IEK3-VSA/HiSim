# Generic/Built-in
import copy
import numpy as np

# Owned
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import component as cp
import loadtypes as lt

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""

class HeatStorageState:
    def __init__(self, T_sp_ww: float,T_sp_hw: float):
        self.T_sp_ww = T_sp_ww
        self.T_sp_hw = T_sp_hw


class HeatStorage(Component):
    """
    In this class is WarmWater and HeatingWater Storage,
    gets demand as Input and calculates new storage temperature
    """
    # Inputs
    ThermalDemandHeatingWater="ThermalDemandHeatingWater" # Heating Water to regulate room Temperature
    ThermalDemandWarmWater="ThermalDemandHeating" # Warmwater for showering, washing etc...
    ControlSignalChooseStorage="ControlSignalChooseStorage"

    OutsideTemperature="OutsideTemperature"
    ThermalInputPower1="ThermalInputPower1"
    ThermalInputPower2="ThermalInputPower2"
    ThermalInputPower3="ThermalInputPower3"
    ThermalInputPower4="ThermalInputPower4"
    ThermalInputPower5="ThermalInputPower5"

    # Outputs
    WaterOutputTemperatureHeatingWater="WaterOutputTemperatureHeatingWater"
    WaterOutputTemperatureWarmWater="WaterOutputTemperatureWarmWater"
    WaterOutputStorageforHeaters="WaterOutputStorageforHeaters"
    #StorageWarmWaterTemperature="StorageWarmWaterTemperature"
    StorageEnergyLoss="StorageEnergyLoss"

    def __init__(self,
                 V_SP_heating_water= 1000,
                 V_SP_warm_water=200,
                 temperature_of_warm_water_extratcion=32,
                 ambient_temperature = 15,
                 sim_params=None):
        super().__init__("HeatStorage")
        self.V_SP_heating_water = V_SP_heating_water
        self.V_SP_warm_water = V_SP_warm_water
        self.sim_params = sim_params
        self.temperature_of_warm_water_extratcion = temperature_of_warm_water_extratcion
        self.ambient_temperature = ambient_temperature
        self.cw=4812



        self.state = HeatStorageState(T_sp_ww=40,T_sp_hw=40)
        self.previous_state = copy.copy(self.state)


        self.thermal_demand_heating_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandHeatingWater,
                                                                         lt.LoadTypes.WarmWater,
                                                                         lt.Units.Watt,
                                                                         False)


        self.thermal_demand_warm_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandWarmWater,
                                                                         lt.LoadTypes.WarmWater,
                                                                         lt.Units.Watt,
                                                                         False)
        self.control_signal_choose_storage: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ControlSignalChooseStorage,
                                                                         lt.LoadTypes.Any,
                                                                         lt.Units.Any,
                                                                         False)

        self.thermal_input_power1 : ComponentInput = self.add_input(self.ComponentName,
                                                           self.ThermalInputPower1,
                                                           lt.LoadTypes.Heating,
                                                           lt.Units.Watt,
                                                           False)
        self.thermal_input_power2: ComponentInput = self.add_input(self.ComponentName,
                                                          self.ThermalInputPower2,
                                                          lt.LoadTypes.Heating,
                                                          lt.Units.Watt,
                                                          False)
        self.thermal_input_power3: ComponentInput = self.add_input(self.ComponentName,
                                                          self.ThermalInputPower3,
                                                          lt.LoadTypes.Heating,
                                                          lt.Units.Watt,
                                                          False)
        self.thermal_input_power4: ComponentInput = self.add_input(self.ComponentName,
                                                          self.ThermalInputPower4,
                                                          lt.LoadTypes.Heating,
                                                          lt.Units.Watt,
                                                          False)
        self.thermal_input_power5: ComponentInput = self.add_input(self.ComponentName,
                                                          self.ThermalInputPower5,
                                                          lt.LoadTypes.Heating,
                                                          lt.Units.Watt,
                                                          False)


        self.T_sp_C_hw : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.WaterOutputTemperatureHeatingWater,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)
        self.T_sp_C_ww : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.WaterOutputTemperatureWarmWater,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)
        self.UA_SP_C : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.StorageEnergyLoss,
                                                      lt.LoadTypes.Any,
                                                      lt.Units.Watt)
        self.T_sp_C : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.WaterOutputStorageforHeaters,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)

    def write_to_report(self):
        pass
    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def adding_all_possible_mass_flows(self, stsv: cp.SingleTimeStepValues, c_w:float):
        production=0
        #function to add all possible mass flows
        if self.thermal_input_power1.SourceOutput is not None:
            production=stsv.get_input_value(self.thermal_input_power1)+ production

        if self.thermal_input_power2.SourceOutput is not None:
            production=stsv.get_input_value(self.thermal_input_power2)+ production
            
        if self.thermal_input_power3.SourceOutput is not None:
            production=stsv.get_input_value(self.thermal_input_power3)+ production
            
        if self.thermal_input_power4.SourceOutput is not None:
            production=stsv.get_input_value(self.thermal_input_power4)+ production
            
        if self.thermal_input_power5.SourceOutput is not None:
            production=stsv.get_input_value(self.thermal_input_power5)+ production
            
        return production

    def calculate_new_storage_temperature (self,seconds_per_timestep: int,T_sp: float, production: float, last : float,c_w: float,V_SP: float ):

        T_ext_SP = self.ambient_temperature

        m_SP_h =  V_SP *0.99 # Vereinfachung
        UA_SP = 0.0038 *V_SP + 0.85 #Heatloss Storage
        dt=seconds_per_timestep


        #calcutae new Storage Temp.
        T_SP = T_sp + (1/(m_SP_h*c_w))*(production - last - UA_SP*(T_sp-T_ext_SP))*dt
        #T_SP = T_sp + (dt/(m_SP_h*c_w))*(P_h_HS*(T_sp-T_ext_SP) - last*(T_sp-T_ext_SP) - UA_SP*(T_sp-T_ext_SP))
        #Correction Calculation
        #T_sp_k = (T_sp+T_SP)/2
        #T_vl = T_sp_k+2.5

        # calcutae new Storage Temp.
        #T_SP = T_sp + (1/(m_SP_h*c_w))*( production- last - UA_SP*(T_sp_k-T_ext_SP))*dt

        return T_SP, UA_SP

    #def regarding_heating_water_storage (self, T_sp: int):


    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):

        T_sp_var_ww=self.state.T_sp_ww #Start-Temp-Storage
        T_sp_var_hw=self.state.T_sp_hw #Start-Temp-Storage

        last_var_ww =stsv.get_input_value(self.thermal_demand_warm_water)
        last_var_hw =stsv.get_input_value(self.thermal_demand_heating_water)

        result_ww=[T_sp_var_ww,0]
        result_hw=[T_sp_var_hw,0]
        T_sp_C=(T_sp_var_ww+T_sp_var_hw)/2

        if stsv.get_input_value(self.control_signal_choose_storage) == 1: #choose to heat up warm water storage
            production_var = self.adding_all_possible_mass_flows(stsv, c_w=self.cw)
            result_ww = self.calculate_new_storage_temperature(seconds_per_timestep=seconds_per_timestep, T_sp=T_sp_var_ww,
                                                            production=production_var, last=last_var_ww, c_w=self.cw,V_SP=self.V_SP_warm_water)
            T_sp_C=result_ww[0]
            production_var=0
            result_hw = self.calculate_new_storage_temperature(seconds_per_timestep=seconds_per_timestep, T_sp=T_sp_var_hw,
                                                            production=production_var, last=last_var_hw, c_w=self.cw,V_SP=self.V_SP_heating_water)

        elif stsv.get_input_value(self.control_signal_choose_storage)== 2: #choose to heat up heating water storage
            production_var = self.adding_all_possible_mass_flows(stsv, c_w=self.cw)
            result_hw = self.calculate_new_storage_temperature(seconds_per_timestep=seconds_per_timestep, T_sp=T_sp_var_hw,
                                                            production=production_var, last=last_var_hw, c_w=self.cw,V_SP=self.V_SP_heating_water)

            T_sp_C = result_hw[0]
            production_var=0
            result_ww = self.calculate_new_storage_temperature(seconds_per_timestep=seconds_per_timestep, T_sp=T_sp_var_ww,
                                                            production=production_var, last=last_var_ww, c_w=self.cw,V_SP=self.V_SP_warm_water)




        self.state.T_sp_ww=result_ww[0]
        self.state.T_sp_hw=result_hw[0]
        stsv.set_output_value(self.T_sp_C_ww, self.state.T_sp_ww)
        stsv.set_output_value(self.T_sp_C_hw, self.state.T_sp_hw)
        stsv.set_output_value(self.T_sp_C, T_sp_C)
        stsv.set_output_value(self.UA_SP_C, result_ww[1]+result_hw[1])
        #Output Massenstrom von Wasser entspricht dem Input Massenstrom. Nur Temperatur hat sich geändert. Wie ist das zu behandelN?



