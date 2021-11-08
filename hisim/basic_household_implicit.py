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
from components import advanced_battery
from components import controller
from components import heat_pump_hplib

from components import building
#from components import heat_pump
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

def basic_household_implicit(my_sim: sim.Simulator):
    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)

if __name__ == '__main__':

    pvs_powers = [5E3, 10E3, 15E3, 20E3]
    capacity = [5, 10]
    for pvs_power in pvs_powers:
        for capacity in capacity:
            # Create configuration object
            my_cfg = ConfigurationGenerator()

            # Set simulation param eters
            my_cfg.add_simulation_parameters()
            ####################################################################################################################
            # Set components
            my_csv_loader = {"CSVLoader": {"component_name": "csv_load_power",
                                                 "csv_filename": os.path.join("loadprofiles", "EFH_Bestand_TRY_5_Profile_1min.csv"),
                                                 "column": 0,
                                                 "loadtype": loadtypes.LoadTypes.Electricity,
                                                 "unit": loadtypes.Units.Watt,
                                                 "column_name": "power_demand",
                                                 "multiplier": 3}}

            my_cfg.add_component(my_csv_loader)
            #Weather
            my_cfg.add_component("Weather")
            #PVS
            my_pvs = {"PVSystem": {"power": pvs_power}}
            my_cfg.add_component(my_pvs)

            #Battery
            fparameter = np.load(globals.HISIMPATH["bat_parameter"])

            my_battery = {"AdvancedBattery": {"parameter": 0,
                                              "sim_params":my_cfg.SimulationParameters,
                                              "capacity": capacity}}
            my_cfg.add_component(my_battery)


            #Controller
            my_controller = {"Controller": {"temperature_storage_target_warm_water": 50,
                                              "temperature_storage_target_heating_water": 40,
                                              "temperature_storage_target_hysteresis": 40,
                                              "strategy": "optimize_own_consumption",
                                              "limit_to_shave": 0}}
            my_cfg.add_component(my_controller)

            ####################################################################################################################
            # Set concatenations
            ####################################################################################################################
            # Set connections
            my_connection_component = ComponentsConnection(first_component="Weather",
                                                           second_component="PVSystem")
            my_cfg.add_connection(my_connection_component)

            my_pvs_to_controller = ComponentsConnection(first_component="PVSystem",
                                                     second_component="Controller",
                                                     method="Manual",
                                                     first_component_output="ElectricityOutput",
                                                     second_component_input="ElectricityOutputPvs")
            my_cfg.add_connection(my_pvs_to_controller)

            my_battery_to_controller = ComponentsConnection(first_component="Controller",
                                                     second_component="AdvancedBattery",
                                                     method="Manual",
                                                     first_component_output="ElectricityToOrFromBatteryTarget",
                                                     second_component_input="LoadingPowerInput")
            my_cfg.add_connection(my_battery_to_controller)

            my_controller_to_battery = ComponentsConnection(first_component="AdvancedBattery",
                                                     second_component="Controller",
                                                     method="Manual",
                                                     first_component_output="ACBatteryPower",
                                                     second_component_input="ElectricityToOrFromBatteryReal")
            my_cfg.add_connection(my_controller_to_battery)


            # Export configuration file
            my_cfg.dump()
            os.system("python hisim.py basic_household_implicit basic_household_implicit")





