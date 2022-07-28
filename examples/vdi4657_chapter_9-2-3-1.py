import os
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

    p_pv = [1]
    e_bat = [1]
    name = "cfg"
    for p in p_pv:
        for e in e_bat:
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
                component_name="csv_load_loader_electric",
                csv_filename=os.path.join(
                    r"HiSim-Data-Package-for-PIEG-Strom/electrical-loadprofiles/data_processed/15min/LP_G_G.csv"
                ),
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

            my_csv_loader_thermal_config = csvloader.CSVLoaderConfig(
                component_name="csv_load_loader_thermal",
                csv_filename=os.path.join(
                    r"HiSim-Data-Package-for-PIEG-Strom/thermal-loadprofiles/data_processed/15min/dhw_1.csv"
                ),
                column=1,
                loadtype=loadtypes.LoadTypes.Heating,
                unit=loadtypes.Units.Watt,
                column_name="hotwater_demand",
                multiplier=1,
                sep=",",
                decimal=".",
            )
            my_cfg.add_component(
                {my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_thermal_config}
            )

            my_csv_loader_pv_config = csvloader.CSVLoaderConfig(
                component_name="csv_load_loader_pv",
                csv_filename=os.path.join(
                    r"HiSim-Data-Package-for-PIEG-Strom/photovoltaic/data_processed/15min/pv_1_a_2015.csv"
                ),
                column=3,
                loadtype=loadtypes.LoadTypes.Electricity,
                unit=loadtypes.Units.Watt,
                column_name="power_production",
                multiplier=1,
                sep=",",
                decimal=".",
            )
            my_cfg.add_component(
                {my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_pv_config}
            )

            # Battery
            my_battery_config = advanced_battery_bslib.Battery.get_default_config()
            my_battery = {
                my_cfg.set_name(advanced_battery_bslib.Battery): my_battery_config
            }
            my_cfg.add_component(my_battery)

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
            # out of the Component got a differen name-> see PVSystem2

            my_connection_component = ComponentsConnection(
                first_component="Weather", second_component="PVSystem2"
            )

            # Outputs from CSV Loaders
            component_connection = ComponentsConnection(
                first_component="csv_load_loader_electric",
                second_component="ControllerElectricity",
                method="Manual",
                first_component_output="Output1",
                second_component_input="ElectricityConsumptionBuilding",
            )
            my_cfg.add_connection(component_connection)

            my_pvs_to_controller = ComponentsConnection(
                first_component="csv_load_loader_pv",
                second_component="ControllerElectricity",
                method="Manual",
                first_component_output="Output1",
                second_component_input="ElectricityOutputPvs",
            )
            my_cfg.add_connection(my_pvs_to_controller)

            # Output from  ControllerElectricity

            component_connection = ComponentsConnection(
                first_component="ControllerElectricity",
                second_component="Battery",
                method="Manual",
                first_component_output="ElectricityToOrFromBatteryTarget",
                second_component_input="LoadingPowerInput",
            )
            my_cfg.add_connection(component_connection)

            # Output from Battery
            my_controller_to_battery = ComponentsConnection(
                first_component="Battery",
                second_component="ControllerElectricity",
                method="Manual",
                first_component_output="AcBatteryPower",
                second_component_input="ElectricityToOrFromBatteryReal",
            )
            my_cfg.add_connection(my_controller_to_battery)

            # Export configuration file
            my_cfg.dump()
            os.system(
                 "python ../hisim/hisim_main.py vdi4657_chapter_9-2-3-1 simulation_settings"
            )
