import inspect
import os
import sys
import simulator as sim
import components as cps
from components import occupancy
from components import weather
from components import pvs
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
    battery_models = ["sonnenBatterie 10 - 5,5 kWh",
                      "sonnenBatterie 10 - 11,5 kWh",
                      "sonnenBatterie 10 - 16,5 kWh",
                      "sonnenBatterie 10 - 22 kWh"]
    for pvs_power in pvs_powers:
        for battery_model in battery_models:
            # Create configuration object
            my_cfg = ConfigurationGenerator()

            # Set simulation parameters
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
            my_cfg.add_component("Weather")

            my_pvs = {"PVSystem": {"power": pvs_power}}
            my_cfg.add_component(my_pvs)
            #my_cfg.add_component("PVSystem")

            my_cfg.add_component("BatteryController")

            my_bat = {"Battery": {"model": battery_model}}
            my_cfg.add_component(my_bat)
            #my_cfg.add_component("Battery")

            ####################################################################################################################
            # Set concatenations
            my_concatenation = ComponentsConcatenation(component_name="Sum_PVSystem_CSVLoader",
                                                       operation="Subtract",
                                                       first_component="CSVLoader",
                                                       second_component="PVSystem",
                                                       first_component_output="Output1",
                                                       second_component_output="ElectricityOutput")
            my_cfg.add_concatenation(my_concatenation)

            ####################################################################################################################
            # Set connections
            my_connection_component = ComponentsConnection(first_component="Weather",
                                                           second_component="PVSystem")
            my_cfg.add_connection(my_connection_component)
            my_bat_con = ComponentsConnection(first_component="BatteryController",
                                              second_component="Battery")
            my_cfg.add_connection(my_bat_con)
            my_bat_controller = ComponentsConnection(first_component="Sum_PVSystem_CSVLoader",
                                                     second_component="BatteryController",
                                                     method="Manual",
                                                     first_component_output="Output",
                                                     second_component_input="ElectricityInput")
            my_cfg.add_connection(my_bat_controller)
            my_connection_component_pvs_bat = ComponentsConnection(first_component="Sum_PVSystem_CSVLoader",
                                                                   second_component="Battery",
                                                                   method="Manual",
                                                                   first_component_output="Output",
                                                                   second_component_input="ElectricityInput")
            my_cfg.add_connection(my_connection_component_pvs_bat)

            # Export configuration file
            my_cfg.dump()
            os.system("python hisim.py basic_household_implicit basic_household_implicit")





