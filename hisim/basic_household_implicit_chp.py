import inspect
import numpy as np
import os
import globals

import sys
import simulator as sim
import components as cps
from components import occupancy
from components import weather
from components import pvs
from components import chp_system
from components import advanced_battery
from components import controller
from components import heat_pump_hplib

from components import building
from components import sumbuilder
import simulator as sim
from cfg_automator import ConfigurationGenerator, SetupFunction, ComponentsConnection, ComponentsConcatenation
import loadtypes

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def basic_household_implicit_chp(my_sim: sim.Simulator):
    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)

if __name__ == '__main__':

    pvs_powers = [5E3]
    capacity = [5]
    for pvs_power in pvs_powers:
        # Create configuration object
        my_cfg = ConfigurationGenerator()

        # Set simulation param eters
        my_cfg.add_simulation_parameters()
        ####################################################################################################################
        # Set components
        my_csv_loader_warm_water = {"CSVLoader": {"component_name": "csv_load_power",
                                             "csv_filename": os.path.join("loadprofiles", "EFH_Bestand_TRY_5_Profile_1min.csv"),
                                             "column": 0,
                                             "loadtype": loadtypes.LoadTypes.Electricity,
                                             "unit": loadtypes.Units.Watt,
                                             "column_name": "power_demand",
                                             "multiplier": 3}}

        my_cfg.add_component(my_csv_loader_warm_water)
        #Weather
        my_cfg.add_component("Weather")
        #PVS
        my_pvs = {"PVSystem": {"power": pvs_power}}
        my_cfg.add_component(my_pvs)

        #CHP
        my_chp = {"CHP": {"min_operation_time": 60,
                                          "min_idle_time":15,
                                          "gas_type": "Methan",
                                          "operating_mode": "both"}}
        my_cfg.add_component(my_chp)


        #Controller
        my_controller = {"Controller": {"temperature_storage_target_warm_water": 55,
                                          "temperature_storage_target_heating_water": 40,
                                          "temperature_storage_target_hysteresis_ww": 50,
                                          "temperature_storage_target_hysteresis_hw": 35,
                                          "strategy": "optimize_own_consumption",
                                          "limit_to_shave": 0}}
        my_cfg.add_component(my_controller)

        #HeatStorage
        my_heat_storage = {"HeatStorage": {"V_SP_heating_water": 1000,
                                          "V_SP_warm_water": 200,
                                          "temperature_of_warm_water_extratcion": 35,
                                          "ambient_temperature": 15}}
        my_cfg.add_component(my_heat_storage)

        #GasHeater
        my_gas_heater = {"GasHeater": {"temperaturedelta": 10}}
        my_cfg.add_component(my_gas_heater)

        #HeatPump
        hp_manufacturer = "Generic"
        hp_type = 1  # air/water | regulated
        hp_thermal_power = 12000  # W
        hp_t_input = -7  # °C
        hp_t_output = 52  # °C
        my_heat_pump_hplib = {"HeatPumpHplib": {"model": hp_manufacturer,
                                          "group_id": hp_type,
                                          "t_in": hp_t_input,
                                          "t_out": hp_t_output,
                                          "p_th_set": hp_thermal_power}}
        my_cfg.add_component(my_heat_pump_hplib)
        ####################################################################################################################
        # Set concatenations
        ####################################################################################################################

        ###################################################################################################################


        ####################################################################################################################
        # Set connections
        my_connection_component = ComponentsConnection(first_component="Weather",
                                                       second_component="PVSystem")
        my_cfg.add_connection(my_connection_component)

        #Outputs from Weather

        my_weather_to_heat_pump_a = ComponentsConnection(first_component="Weather",
                                                 second_component="HeatPumpHplib",
                                                 method="Manual",
                                                 first_component_output="TemperatureOutside",
                                                 second_component_input="TemperatureInputPrimary")
        my_cfg.add_connection(my_weather_to_heat_pump_a)
        
        my_weather_to_heat_pump_b = ComponentsConnection(first_component="Weather",
                                                 second_component="HeatPumpHplib",
                                                 method="Manual",
                                                 first_component_output="TemperatureOutside",
                                                 second_component_input="TemperatureAmbient")
        my_cfg.add_connection(my_weather_to_heat_pump_b)

        #Outputs from PVSystem
        my_pvs_to_controller = ComponentsConnection(first_component="PVSystem",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="ElectricityOutput",
                                                 second_component_input="ElectricityOutputPvs")
        my_cfg.add_connection(my_pvs_to_controller)

        #Outputs from CSVLoader
        my_pvs_to_controller_a = ComponentsConnection(first_component="PVSystem",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="ElectricityOutput",
                                                 second_component_input="ElectricityFromCHPReal")
        #Outputs from CHP

        my_chp_to_controller_a = ComponentsConnection(first_component="CHP",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="ElectricityOutput",
                                                 second_component_input="ElectricityFromCHPReal")
        my_cfg.add_connection(my_chp_to_controller_a)
        my_chp_to_heat_storage = ComponentsConnection(first_component="CHP",
                                                 second_component="HeatStorage",
                                                 method="Manual",
                                                 first_component_output="ThermalOutputPower",
                                                 second_component_input="ThermalInputPower1")
        my_cfg.add_connection(my_chp_to_heat_storage)

        #Outputs from GasHeater
        
        my_gas_heater_to_heat_storage = ComponentsConnection(first_component="GasHeater",
                                                 second_component="HeatStorage",
                                                 method="Manual",
                                                 first_component_output="ThermalOutputPower",
                                                 second_component_input="ThermalInputPower2")
        my_cfg.add_connection(my_gas_heater_to_heat_storage)

        #Outputs from HeatPump
        '''
        my_heat_pump_to_heat_storage = ComponentsConnection(first_component="HeatPumpHplib",
                                                 second_component="HeatStorage",
                                                 method="Manual",
                                                 first_component_output="ThermalOutputPower",
                                                 second_component_input="ThermalInputPower3")
        my_cfg.add_connection(my_heat_pump_to_heat_storage)
        
        my_heat_pump_to_controller = ComponentsConnection(first_component="HeatPumpHplib",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="ElectricalInputPower",
                                                 second_component_input="ElectricityDemandHeatPump")
        my_cfg.add_connection(my_heat_pump_to_controller)
        
        '''
        #Outputs from Storage
        my_storage_to_controller_a = ComponentsConnection(first_component="HeatStorage",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="WaterOutputTemperatureHeatingWater",
                                                 second_component_input="StorageTemperatureHeatingWater")
        my_cfg.add_connection(my_storage_to_controller_a)

        my_storage_to_controller_b = ComponentsConnection(first_component="HeatStorage",
                                                 second_component="Controller",
                                                 method="Manual",
                                                 first_component_output="WaterOutputTemperatureWarmWater",
                                                 second_component_input="StorageTemperatureWarmWater")
        my_cfg.add_connection(my_storage_to_controller_b)

        my_storage_to_heat_pump = ComponentsConnection(first_component="HeatStorage",
                                                 second_component="HeatPump",
                                                 method="Manual",
                                                 first_component_output="WaterOutputStorageforHeaters",
                                                 second_component_input="TemperatureInputSecondary")
        my_cfg.add_connection(my_storage_to_heat_pump)

        my_storage_to_gas_heater= ComponentsConnection(first_component="HeatStorage",
                                                 second_component="GasHeater",
                                                 method="Manual",
                                                 first_component_output="WaterOutputStorageforHeaters",
                                                 second_component_input="MassflowInputTemperature")
        my_cfg.add_connection(my_storage_to_gas_heater)

        my_storage_to_chp= ComponentsConnection(first_component="HeatStorage",
                                                 second_component="CHP",
                                                 method="Manual",
                                                 first_component_output="WaterOutputStorageforHeaters",
                                                 second_component_input="MassflowInputTemperature")
        my_cfg.add_connection(my_storage_to_chp)
        #Outputs from Controller
        my_controller_to_chp_a = ComponentsConnection(first_component="Controller",
                                                 second_component="CHP",
                                                 method="Manual",
                                                 first_component_output="ElectricityFromCHPTarget",
                                                 second_component_input="ElectricityFromCHPTarget")
        my_cfg.add_connection(my_controller_to_chp_a)


        my_controller_to_chp_b = ComponentsConnection(first_component="Controller",
                                                 second_component="CHP",
                                                 method="Manual",
                                                 first_component_output="ControlSignalChp",
                                                 second_component_input="ControlSignal")
        my_cfg.add_connection(my_controller_to_chp_b)

        my_controller_to_gas_heater = ComponentsConnection(first_component="Controller",
                                                 second_component="GasHeater",
                                                 method="Manual",
                                                 first_component_output="ControlSignalGasHeater",
                                                 second_component_input="ControlSignal")
        my_cfg.add_connection(my_controller_to_gas_heater)

        my_controller_to_heat_pump = ComponentsConnection(first_component="Controller",
                                                 second_component="HeatPump",
                                                 method="Manual",
                                                 first_component_output="ControlSignalHeatPump",
                                                 second_component_input="Mode")
        my_cfg.add_connection(my_controller_to_heat_pump)

        my_controller_to_heat_storage = ComponentsConnection(first_component="Controller",
                                                 second_component="HeatStorage",
                                                 method="Manual",
                                                 first_component_output="ControlSignalChooseStorage",
                                                 second_component_input="ControlSignalChooseStorage")
        my_cfg.add_connection(my_controller_to_heat_storage)


        # Export configuration file
        my_cfg.dump()
        os.system("python hisim.py basic_household_implicit_chp basic_household_implicit_chp")





