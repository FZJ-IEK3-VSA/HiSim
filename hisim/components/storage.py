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
    ThermalDemandHeatingWater="ThermalDemandHeating" # Heating Water to regulate room Temperature
    ThermalDemandWarmWater="ThermalDemandHeating" # Warmwater for showering, washing etc...

    OutsideTemperature="OutsideTemperature"
    InputMass1="InputMass1"
    InputMass2="InputMass2"
    InputMass3="InputMass3"
    InputMass4="InputMass4"
    InputMass5="InputMass5"
    InputTemp1="InputTemp1"
    InputTemp2="InputTemp2"
    InputTemp3="InputTemp3"
    InputTemp4="InputTemp4"
    InputTemp5="InputTemp5"

    # Outputs
    HeatingWaterOutputTemperature="WaterOutputTemperature"
    WaterOutputTemperature="WaterOutputTemperature"

    #StorageWarmWaterTemperature="StorageWarmWaterTemperature"
    StorageEnergyLoss="StorageEnergyLoss"

    def __init__(self,
                 timesteps: int,
                 V_SP= 500,
                 temperature_of_warm_water_extratcion=35,
                 ambient_temperature = 15,
                 sim_params=None):
        super().__init__("HeatStorageTjarko")
        self.V_SP = V_SP
        self.sim_params = sim_params
        self.temperature_of_warm_water_extratcion = temperature_of_warm_water_extratcion
        self.ambient_temperature = ambient_temperature




        self.state = HeatStorageTjarkoState(T_sp=30)
        self.previous_state = copy.copy(self.state)


        self.temperature_outside : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.OutsideTemperature,
                                                                         lt.LoadTypes.Temperature,
                                                                         lt.Units.Celcius,
                                                                         True)

        self.thermal_demand_heating_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandHeatingWater,
                                                                         lt.LoadTypes.Water,
                                                                         lt.Units.Watt,
                                                                         False)
        self.demand_warm_water : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalDemandWarmWater,
                                                                         lt.LoadTypes.Water,
                                                                         lt.Units.Liter,
                                                                         False)

        self.input_mass1 : ComponentInput = self.add_input(self.ComponentName,
                                                           self.InputMass1,
                                                           lt.LoadTypes.Water,
                                                           lt.Units.kg_per_sec,
                                                           False)
        self.input_mass2: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputMass2,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.kg_per_sec,
                                                          False)
        self.input_mass3: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputMass3,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.kg_per_sec,
                                                          False)
        self.input_mass4: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputMass4,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.kg_per_sec,
                                                          False)
        self.input_mass5: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputMass5,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.kg_per_sec,
                                                          False)

        self.input_temp1: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputTemp1,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.Celcius,
                                                          False)
        self.input_temp2: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputTemp2,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.Celcius,
                                                          False)
        self.input_temp3: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputTemp3,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.Celcius,
                                                          False)
        self.input_temp4: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputTemp4,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.Celcius,
                                                          False)
        self.input_temp5: ComponentInput = self.add_input(self.ComponentName,
                                                          self.InputTemp5,
                                                          lt.LoadTypes.Water,
                                                          lt.Units.Celcius,
                                                          False)

        self.T_sp_C : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.WaterOutputTemperature,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celcius)
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

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def adding_all_possible_mass_flows(self):
        production=0
        #function to add all possible mass flows
        if self.input_mass1.SourceOutput and self.input_temp1.SourceOutput is not None:
            production=stsv.get_input_value(self.input_mass1)*c_w*(stsv.get_input_value(self.input_temp1)-T_sp)+production

        if self.input_mass2.SourceOutput and self.input_temp2.SourceOutput is not None:
            production=stsv.get_input_value(self.input_mass2)*c_w*(stsv.get_input_value(self.input_temp2)-T_sp)+production

        if self.input_mass3.SourceOutput and self.input_temp3.SourceOutput is not None:
            production=stsv.get_input_value(self.input_mass3)*c_w*(stsv.get_input_value(self.input_temp3)-T_sp)+production

        if self.input_mass4.SourceOutput and self.input_temp4.SourceOutput is not None:
            production=stsv.get_input_value(self.input_mass4)*c_w*(stsv.get_input_value(self.input_temp4)-T_sp)+production

        if self.input_mass5.SourceOutput and self.input_temp5.SourceOutput is not None:
            production=stsv.get_input_value(self.input_mass5)*c_w*(stsv.get_input_value(self.input_temp5)-T_sp)+production

        return production

    def calculate_new_storage_temperature (self,seconds_per_timestep: int, T_sp: float, production: float, last = float ):

        T_ext_SP = self.ambient_temperature

        m_SP_h =  self.V_SP *0.99 # Vereinfachung
        UA_SP = 0.0038 * self.V_SP + 0.85 #Heatloss Storage

        dt=seconds_per_timestep
        c_w=4182

        #calcutae new Storage Temp.
        T_SP = T_sp + (1/(m_SP_h*c_w))*(production - last - UA_SP*(T_sp-T_ext_SP))*dt
        #T_SP = T_sp + (dt/(m_SP_h*c_w))*(P_h_HS*(T_sp-T_ext_SP) - last*(T_sp-T_ext_SP) - UA_SP*(T_sp-T_ext_SP))
        #Correction Calculation
        T_sp_k = (T_sp+T_SP)/2
        T_vl = T_sp_k+2.5

        # calcutae new Storage Temp.
        T_SP = T_sp + (1/(m_SP_h*c_w))*( production- last - UA_SP*(T_sp_k-T_ext_SP))*dt

        return T_SP, UA_SP

    #def regarding_heating_water_storage (self, T_sp: int):


    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):

        T_sp_var=self.state.T_sp #Start-Temp-Storage

        last_var =stsv.get_input_value(self.thermal_demand_heating_water)+4182*stsv.get_input_value(self.demand_warm_water)*self.temperature_of_warm_water_extratcion


        production_var = adding_all_possible_mass_flows()


        result = calculate_new_storage_temperature(T_sp=T_sp_var, production = production_var, last=last_var)


        self.state.T_sp=result(0)

        stsv.set_output_value(self.T_sp_C, self.state.T_sp)
        stsv.set_output_value(self.UA_SP_C, result(1))
        #Output Massenstrom von Wasser entspricht dem Input Massenstrom. Nur Temperatur hat sich geändert. Wie ist das zu behandelN?



