# Generic/Built-in
import copy
import numpy as np

# Owned
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import component as cp
import loadtypes as lt

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Tjarko Tjaden"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class HeatStorageTjarkoState:
    def __init__(self, T_sp: float):
        self.T_sp = T_sp


class HeatStorage(Component):

    # Inputs
    ThermalDemandHeatingWater="ThermalDemandHeatingWater" # Heating Water to regulate room Temperature
    ThermalDemandWarmWater="ThermalDemandHeating" # Warmwater for showering, washing etc...

    OutsideTemperature="OutsideTemperature"
    ThermalInputPower1="ThermalInputPower1"
    ThermalInputPower2="ThermalInputPower2"
    ThermalInputPower3="ThermalInputPower3"
    ThermalInputPower4="ThermalInputPower4"
    ThermalInputPower5="ThermalInputPower5"

    # Outputs
    WaterOutputTemperature="WaterOutputTemperature"

    #StorageWarmWaterTemperature="StorageWarmWaterTemperature"
    StorageEnergyLoss="StorageEnergyLoss"

    def __init__(self,
                 V_SP= 1000,
                 temperature_of_warm_water_extratcion=35,
                 ambient_temperature = 15,
                 sim_params=None):
        super().__init__("HeatStorageTjarko")
        self.V_SP = V_SP
        self.sim_params = sim_params
        self.temperature_of_warm_water_extratcion = temperature_of_warm_water_extratcion
        self.ambient_temperature = ambient_temperature
        self.cw=4812



        self.state = HeatStorageTjarkoState(T_sp=30)
        self.previous_state = copy.copy(self.state)


        self.thermal_demand_heating_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandHeatingWater,
                                                                         lt.LoadTypes.WarmWater,
                                                                         lt.Units.Watt,
                                                                         False)
        self.demand_warm_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandWarmWater,
                                                                         lt.LoadTypes.Water,
                                                                         lt.Units.Liter,
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


        self.T_sp_C : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.WaterOutputTemperature,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)
        self.UA_SP_C : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.StorageEnergyLoss,
                                                      lt.LoadTypes.Any,
                                                      lt.Units.Watt)


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

    def calculate_new_storage_temperature (self,seconds_per_timestep: int,T_sp: float, production: float, last : float,c_w: float ):

        T_ext_SP = self.ambient_temperature

        m_SP_h =  self.V_SP *0.99 # Vereinfachung
        UA_SP = 0.0038 * self.V_SP + 0.85 #Heatloss Storage
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

        T_sp_var=self.state.T_sp #Start-Temp-Storage

        last_var =stsv.get_input_value(self.thermal_demand_heating_water)+4182*stsv.get_input_value(self.demand_warm_water)*self.temperature_of_warm_water_extratcion


        production_var = self.adding_all_possible_mass_flows(stsv, c_w=self.cw)


        result = self.calculate_new_storage_temperature(seconds_per_timestep=seconds_per_timestep ,T_sp=T_sp_var, production = production_var, last=last_var,c_w=self.cw)


        self.state.T_sp=result[0]

        stsv.set_output_value(self.T_sp_C, self.state.T_sp)
        stsv.set_output_value(self.UA_SP_C, result[1])
        #Output Massenstrom von Wasser entspricht dem Input Massenstrom. Nur Temperatur hat sich geändert. Wie ist das zu behandelN?



