import inspect
import numpy as np
import os
from hisim import utils
from typing import Optional
from hisim.simulator import SimulationParameters

import sys
import hisim.simulator as sim
import hisim.components as cps
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import advanced_battery_bslib
import hisim.simulator as sim
from hisim.cfg_automator import ConfigurationGenerator, SetupFunction, ComponentsConnection, ComponentsGrouping
import hisim.loadtypes as loadtypes

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def basic_household_implicit(my_sim: sim.Simulator,my_simulation_parameters: Optional[sim.SimulationParameters] = None):
    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)

if __name__ == '__main__':

    pvs_powers = [5E3]
    capacity = [5]
    for pvs_power in pvs_powers:
        for capacity_i in capacity:
            # Create configuration object
            my_cfg = ConfigurationGenerator()

            # Set simulation param eters
            my_cfg.add_simulation_parameters()

            SimulationParameters = {"year": 2019,
                                    "seconds_per_timestep": 60 * 15,
                                    "method": "full_year"}
            ####################################################################################################################
            # Set components
            my_csv_loader = {"CSVLoader": {"component_name": "csv_load_power",
                                                 "csv_filename": os.path.join("loadprofiles", "EFH_Bestand_TRY_5_Profile_1min.csv"),
                                                 "column": 0,
                                                 "loadtype": loadtypes.LoadTypes.Electricity,
                                                 "unit": loadtypes.Units.Watt,
                                                 "column_name": "power_demand",
                                                 "multiplier": 3}}
            my_weather= {"Weather": {"location": "Aachen"}}
            my_cfg.add_component(my_weather)
            #my_cfg.add_component(my_csv_loader)
            # Weather

            # PVS
            my_pvs_config = generic_pv_system.PVSystemConfig(name="PVSystem2")

            #my_pvs_config = { generic_pv_system.PVSystemConfig :{generic_pv_system.PVSystemConfig.name : "PVSystem"}}

            #my_pvs = {generic_pv_system.PVSystem : my_pvs_config}

            my_cfg.add_component({generic_pv_system.PVSystem.__name__:generic_pv_system.PVSystem.get_config(generic_pv_system.PVSystemConfig(name="PVSystem2"))})

                # Battery
            my_battery = {"AdvancedBattery": {}}
            #my_cfg.add_component(advanced_battery.get_config(BatteryConfig(....)))
            # get config als static_function jeder Component hinzufügen (vorteil battery nicht mehr zu intialisieren)

            # siehe PVSystem Config
            # aus json datei Config rausziehen und hiermit Componente intialisieren. Dazu sollten Name von Componente und ComponenteConfig gleich sein
            # Controller
            my_controller = {"Controller": {"temperature_storage_target_warm_water": 50,
                                              "temperature_storage_target_heating_water": 40,
                                              "temperature_storage_target_hysteresis": 40,
                                              "strategy": "optimize_own_consumption",
                                              "limit_to_shave": 0}}
            #my_cfg.add_component(my_controller)

            ####################################################################################################################
            # Set groupings
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
            #my_cfg.add_connection(my_pvs_to_controller)

            my_battery_to_controller = ComponentsConnection(first_component="Controller",
                                                     second_component="AdvancedBattery",
                                                     method="Manual",
                                                     first_component_output="ElectricityToOrFromBatteryTarget",
                                                     second_component_input="LoadingPowerInput")
            #my_cfg.add_connection(my_battery_to_controller)

            my_controller_to_battery = ComponentsConnection(first_component="AdvancedBattery",
                                                     second_component="Controller",
                                                     method="Manual",
                                                     first_component_output="ACBatteryPower",
                                                     second_component_input="ElectricityToOrFromBatteryReal")
            #my_cfg.add_connection(my_controller_to_battery)

            # Export configuration file
            my_cfg.dump()
            #os.chdir("..")
            #os.chdir("hisim")
            os.system("python ../hisim/hisim_main.py basic_household_implicit_advanced_battery basic_household_implicit")





