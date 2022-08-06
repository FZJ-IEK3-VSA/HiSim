import os
import pandas as pd
from typing import Optional
import hisim.simulator as sim
import hisim.loadtypes as loadtypes
from hisim.components import controller_l2_energy_management_system
from hisim.components import csvloader
from hisim.components import advanced_battery_bslib
from hisim.cfg_automator import (
    ConfigurationGenerator,
    SetupFunction,
    ComponentsConnection,
)

__authors__ = "Tjarko Tjaden"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt, Tjarko Tjaden"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"

def simulation_settings(my_sim: sim.Simulator,
                        my_simulation_parameters: Optional[sim.SimulationParameters] = None,
):
    """
    This setup function represents the simulations for 
    VDI guideline 4657 sheet 3, chapter 9.2.3.1
    """
    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)

if __name__ == "__main__":

    ######################
    # parameter variation
    ######################

    # normalized time series profiles
    os.chdir('../')
    rootpath = os.getcwd()
    profilepath = (rootpath+'/hisim/inputs/HiSim-Data-Package-for-PIEG-Strom')
    electrical_loadprofiles_path = (profilepath+'/electrical-loadprofiles/data_processed/15min/')
    electrical_loadprofiles = os.listdir(electrical_loadprofiles_path)
    photovoltaic_profiles_path = (profilepath+'/photovoltaic/data_processed/15min/')
    # weather
    weather_region = ['4']  # 1-15
    weather_year = ['2015'] # 2015,2045
    weather_type = ['a']    # (a)verage, extreme (s)ummer, extreme (w)inter
    # normalized photovoltaic power in kWp/MWh
    p_pv = [0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]
    # normalized battery capacity in kWh/MWh
    e_bat = [0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]

    name = "cfg"
    for electrical_loadprofile in electrical_loadprofiles:
        for region in weather_region:
            for year in weather_year:
                for type in weather_type:
                    for pv in p_pv:
                        for bat in e_bat:
                            # Create configuration object
                            my_cfg = ConfigurationGenerator()

                            # Set simulation param
                            simulation_parameters = {
                                "year": 2019,
                                "seconds_per_timestep": 60 * 15,
                                "method": "full_year_only_kpi",
                            }
                            my_cfg.add_simulation_parameters(
                                my_simulation_parameters=simulation_parameters
                            )

                            ####################################################################################################################
                            # Set components
                            ####################################################################################################################
                            
                            # CSV-Loaders
                            my_csv_loader_electric_config = csvloader.CSVLoaderConfig(
                                component_name=electrical_loadprofile[:-4],
                                csv_filename=(electrical_loadprofiles_path+electrical_loadprofile),
                                column=1,
                                loadtype=loadtypes.LoadTypes.Electricity,
                                unit=loadtypes.Units.Watt,
                                column_name="power_demand",
                                multiplier=1,
                                sep=",",
                                decimal=".",
                            )
                            my_cfg.add_component(
                                {my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_electric_config}
                            )

                            my_csv_loader_pv_config = csvloader.CSVLoaderConfig(
                                component_name="pv_'{}'_kWp/MWh_region:'{}'_type:'{}'_year:'{}'".format(str(pv),region,type,year),
                                csv_filename=(photovoltaic_profiles_path+'pv_'+region+'_'+type+'_'+year+'.csv'),
                                column=3,
                                loadtype=loadtypes.LoadTypes.Electricity,
                                unit=loadtypes.Units.Watt,
                                column_name="pv_power_production",
                                multiplier=pv,
                                sep=",",
                                decimal=".",
                            )
                            my_cfg.add_component(
                                {my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_pv_config}
                            )

                            # Battery
                            my_battery_config = advanced_battery_bslib.BatteryConfig(system_id='SG1',
                                                                                    p_inv_custom=min(pv,bat)*0.75*1000,
                                                                                    e_bat_custom=bat,
                                                                                    name=str(bat),
                                                                                    source_weight=1)

                            my_cfg.add_component({my_cfg.set_name(advanced_battery_bslib.Battery): my_battery_config})

                            # Controller
                            my_controller_electricity = {
                                my_cfg.set_name(
                                    controller_l2_energy_management_system.ControllerElectricity
                                ): controller_l2_energy_management_system.ControllerElectricityConfig()
                            }
                            my_cfg.add_component(my_controller_electricity)

                            ####################################################################################################################
                            # Set groupings
                            ####################################################################################################################
                            # Set connections
                            ####################################################################################################################

                            # HINT:
                            # The name of the Component (f.e. first_component) has to be the same as the Class Name,
                            # out of the Component got a differen name

                            # Outputs from CSV Loaders
                            component_connection = ComponentsConnection(
                                first_component=electrical_loadprofile[:-4],
                                second_component="ControllerElectricity",
                                method="Manual",
                                first_component_output="Output1",
                                second_component_input="ElectricityConsumptionBuilding",
                            )
                            my_cfg.add_connection(component_connection)

                            my_pvs_to_controller = ComponentsConnection(
                                first_component="pv_'{}'_kWp/MWh_region:'{}'_type:'{}'_year:'{}'".format(str(pv),region,type,year),
                                second_component="ControllerElectricity",
                                method="Manual",
                                first_component_output="Output1",
                                second_component_input="ElectricityOutputPvs",
                            )
                            my_cfg.add_connection(my_pvs_to_controller)

                            # Output from  ControllerElectricity

                            component_connection = ComponentsConnection(
                                first_component="ControllerElectricity",
                                second_component=str(bat),
                                method="Manual",
                                first_component_output="ElectricityToOrFromBatteryTarget",
                                second_component_input="LoadingPowerInput",
                            )
                            my_cfg.add_connection(component_connection)

                            # Output from Battery
                            my_controller_to_battery = ComponentsConnection(
                                first_component=str(bat),
                                second_component="ControllerElectricity",
                                method="Manual",
                                first_component_output="AcBatteryPower",
                                second_component_input="ElectricityToOrFromBatteryReal",
                            )
                            my_cfg.add_connection(my_controller_to_battery)

                            # Export configuration file
                            my_cfg.dump()
                            os.system(
                                "python hisim/hisim_main.py examples/vdi4657_chapter_9-2-3-1 simulation_settings"
                            )
