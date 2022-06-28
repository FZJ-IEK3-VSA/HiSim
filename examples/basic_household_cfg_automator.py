import inspect
import numpy as np
import os
from hisim import utils
from typing import Optional
from hisim.simulator import SimulationParameters

import sys
import hisim.simulator as sim
import hisim.components as cps
import hisim.loadtypes

from hisim.components import controller_l2_energy_management_system
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import csvloader
from hisim.components import advanced_battery_bslib
from hisim.components import generic_hot_water_storage
from hisim.components import generic_gas_heater
from hisim.components import building
from hisim.components import loadprofilegenerator_connector
from hisim.components import advanced_fuel_cell
from hisim.components import generic_electrolyzer_and_h2_storage
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

# to start mutliple simulations a name for different cfg.json files has to be added

def basic_household_implicit(my_sim: sim.Simulator,my_simulation_parameters: Optional[sim.SimulationParameters] = None):
    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    my_setup_function = SetupFunction()
    my_setup_function.build(my_sim)

if __name__ == '__main__':

    pvs_powers = [5E3, 10E3]
    capacity = [5]
    name="cfg1"
    for pvs_power in pvs_powers:
        for capacity_i in capacity:
            # Create configuration object
            my_cfg = ConfigurationGenerator()

            # Set simulation param
            SimulationParameters = {"year": 2019,
                                    "seconds_per_timestep": 60 * 15,
                                    "method": "full_year"}
            my_cfg.add_simulation_parameters(my_simulation_parameters=SimulationParameters)
            '''
            todo
            - heat- & electric-demand, pv-system über csv
            - warmwater über building
            - Komponenten:
                - Storage
                - heatpump
                - Battery
                - GasHeater
                - CHP
                - Electrolyzer
                
            
            '''
            ####################################################################################################################
            # Set components
            # CSV-Loaders
            my_csv_loader_electric_config = csvloader.CSVLoaderConfig(component_name="csv_load_loader_electric",
                                                                csv_filename=os.path.join("loadprofiles\\electrical-loadprofiles-households",
                                                                                          "LP_W_EFH.csv"),
                                                                column= 1,
                                                                loadtype= loadtypes.LoadTypes.Electricity,
                                                                unit= loadtypes.Units.Watt,
                                                                column_name= "power_demand",
                                                                multiplier= 3,
                                                                sep = ",",
                                                                decimal = "."
                                                                )
            my_cfg.add_component({my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_electric_config})

            my_csv_loader_thermal_config = csvloader.CSVLoaderConfig(component_name="csv_load_loader_thermal",
                                                                csv_filename=os.path.join("loadprofiles\\thermal-loadprofiles-households",
                                                                                          "dhw_1.csv"),
                                                                column= 1,
                                                                loadtype= loadtypes.LoadTypes.Heating,
                                                                unit= loadtypes.Units.Watt,
                                                                column_name= "power_demand",
                                                                multiplier= 3,
                                                                sep = ",",
                                                                decimal = "."
                                                                )
            my_cfg.add_component({my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_thermal_config})

            my_csv_loader_pv_config = csvloader.CSVLoaderConfig(component_name="csv_load_loader_pv",
                                                                csv_filename=os.path.join("loadprofiles\\photovoltaic",
                                                                                          "pv_1_a_2015.csv"),
                                                                column= 1,
                                                                loadtype= loadtypes.LoadTypes.Electricity,
                                                                unit= loadtypes.Units.Watt,
                                                                column_name= "power_demand",
                                                                multiplier= 3,
                                                                sep = ",",
                                                                decimal = "."
                                                                )
            my_cfg.add_component({my_cfg.set_name(csvloader.CSVLoader): my_csv_loader_pv_config})

            # Weather
            my_weather_config=weather.WeatherConfig(location="Aachen")
            my_weather= {my_cfg.set_name(weather.Weather): my_weather_config}
            my_cfg.add_component(my_weather)

            # PVS
            my_pvs_config=generic_pv_system.PVSystemConfig(name="PVSystem2",
                                                           source_weight="")
            my_pvs = {my_cfg.set_name(generic_pv_system.PVSystem): my_pvs_config}
            #my_cfg.add_component(my_pvs)

            # Battery

            my_battery_config=advanced_battery_bslib.Battery.get_default_config()
            my_battery = {my_cfg.set_name(advanced_battery_bslib.Battery): my_battery_config}
            my_cfg.add_component(my_battery)


            # Controller
            my_controller_heat = {my_cfg.set_name(controller_l2_energy_management_system.ControllerHeat):
                                      controller_l2_energy_management_system.ControllerHeatConfig()}
            my_cfg.add_component(my_controller_heat)

            my_controller_electricity = {my_cfg.set_name(controller_l2_energy_management_system.ControllerElectricity):
                                             controller_l2_energy_management_system.ControllerElectricityConfig()}
            my_cfg.add_component(my_controller_electricity)

            # Heat and WarmWaterStorage
            my_storage_config=generic_hot_water_storage.HeatStorage.get_default_config()
            my_storage = {my_cfg.set_name(generic_hot_water_storage.HeatStorage): my_storage_config}
            my_cfg.add_component(my_storage)

            my_storage_controller_config=generic_hot_water_storage.HeatStorageController.get_default_config()
            my_storage_controller= {my_cfg.set_name(generic_hot_water_storage.HeatStorageController): my_storage_controller_config}
            my_cfg.add_component(my_storage_controller)

            # Gas Heater
            my_gas_heater_config=generic_gas_heater.GasHeater.get_default_config()
            my_gas_heater = {my_cfg.set_name(generic_gas_heater.GasHeater): my_gas_heater_config}
            my_cfg.add_component(my_gas_heater)

            # CHP
            my_chp_config=advanced_fuel_cell.CHP.get_default_config()
            my_chp = {my_cfg.set_name(advanced_fuel_cell.CHP): my_chp_config}
            my_cfg.add_component(my_chp)

            # Electrolyzer
            my_electrolyzer_config = generic_electrolyzer_and_h2_storage.Electrolyzer.get_default_config()
            my_electrolyzer = {my_cfg.set_name(generic_electrolyzer_and_h2_storage.Electrolyzer): my_electrolyzer_config}
            my_cfg.add_component(my_electrolyzer)

            # H2-Storage
            my_h2_storage_config = generic_electrolyzer_and_h2_storage.HydrogenStorage.get_default_config()
            my_h2_storage = {my_cfg.set_name(generic_electrolyzer_and_h2_storage.HydrogenStorage): my_h2_storage_config}
            my_cfg.add_component(my_h2_storage)

            # Bulding
            my_bulding_config=building.Building.get_default_config()
            my_bulding = {my_cfg.set_name(building.Building): my_bulding_config}
            my_cfg.add_component(my_bulding)

            my_bulding_controller_config=building.BuildingController.get_default_config()
            my_bulding_controller = {my_cfg.set_name(building.BuildingController): my_bulding_controller_config}
            my_cfg.add_component(my_bulding_controller)

            # Occupancy
            my_occupancy_config = loadprofilegenerator_connector.Occupancy.get_default_config()
            my_occupancy = {my_cfg.set_name(loadprofilegenerator_connector.Occupancy): my_occupancy_config}
            my_cfg.add_component(my_occupancy)
            ####################################################################################################################
            # Set groupings
            ####################################################################################################################
            # Set connections

            # HINT:
            # The name of the Component (f.e. first_component) has to be the same as the Class Name,
            # out of the Component got a differen name-> see PVSystem2

            my_connection_component = ComponentsConnection(first_component="Weather",
                                                           second_component="PVSystem2")

            # Outputs from Weather
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="Azimuth",
                                                     second_component_input="Altitude")
            my_cfg.add_connection(my_electric_demand_to_controller)

            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="Azimuth",
                                                     second_component_input="Azimuth")
            my_cfg.add_connection(my_electric_demand_to_controller)

            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="ApparentZenith",
                                                     second_component_input="ApparentZenith")
            my_cfg.add_connection(my_electric_demand_to_controller)
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="DirectNormalIrradiance",
                                                     second_component_input="DirectNormalIrradiance")
            my_cfg.add_connection(my_electric_demand_to_controller)
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="DirectNormalIrradianceExtra",
                                                     second_component_input="DirectNormalIrradianceExtra")
            my_cfg.add_connection(my_electric_demand_to_controller)
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="DiffuseHorizontalIrradiance",
                                                     second_component_input="DiffuseHorizontalIrradiance")
            my_cfg.add_connection(my_electric_demand_to_controller)
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="GlobalHorizontalIrradiance",
                                                     second_component_input="GlobalHorizontalIrradiance")
            my_cfg.add_connection(my_electric_demand_to_controller)
            my_electric_demand_to_controller = ComponentsConnection(first_component="Weather",
                                                                    second_component="Building",
                                                                    method="Manual",
                                                                    first_component_output="TemperatureOutside",
                                                                    second_component_input="TemperatureOutside")
            my_cfg.add_connection(my_electric_demand_to_controller)

            # Outputs from CSV Loaders
            my_electric_demand_to_controller = ComponentsConnection(first_component="csv_load_loader_electric",
                                                     second_component="ControllerElectricity",
                                                     method="Manual",
                                                     first_component_output="Output1",
                                                     second_component_input="ElectricityConsumptionBuilding")
            my_cfg.add_connection(my_electric_demand_to_controller)

            my_pvs_to_controller = ComponentsConnection(first_component="csv_load_loader_pv",
                                                     second_component="ControllerElectricity",
                                                     method="Manual",
                                                     first_component_output="Output1",
                                                     second_component_input="ElectricityOutputPvs")
            my_cfg.add_connection(my_pvs_to_controller)

            # Output from Occupancy
            my_electric_demand_to_controller = ComponentsConnection(first_component="Occupancy",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="HeatingByResidents",
                                                     second_component_input="HeatingByResidents")
            my_cfg.add_connection(my_electric_demand_to_controller)

            # Output from  ControllerHeat
            my_storage_controller = ComponentsConnection(first_component="ControllerHeat",
                                                     second_component="HeatStorage",
                                                     method="Manual",
                                                     first_component_output="ControlSignalChooseStorage",
                                                     second_component_input="ControlSignalChooseStorage")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="ControllerHeat",
                                                     second_component="GasHeater",
                                                     method="Manual",
                                                     first_component_output="ControlSignalGasHeater",
                                                     second_component_input="ControlSignal")
            my_cfg.add_connection(my_storage_controller)
            my_storage_controller = ComponentsConnection(first_component="ControllerHeat",
                                                     second_component="CHP",
                                                     method="Manual",
                                                     first_component_output="ControlSignalChp",
                                                     second_component_input="ControlSignal")
            my_cfg.add_connection(my_storage_controller)

            # Output from  ControllerElectricity
            my_storage_controller = ComponentsConnection(first_component="ControllerElectricity",
                                                     second_component="Battery",
                                                     method="Manual",
                                                     first_component_output="ElectricityToOrFromBatteryTarget",
                                                     second_component_input="LoadingPowerInput")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="ControllerElectricity",
                                                     second_component="Electrolyzer",
                                                     method="Manual",
                                                     first_component_output="ElectricityToElectrolyzerTarget",
                                                     second_component_input="ElectricityInput")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="ControllerElectricity",
                                                     second_component="CHP",
                                                     method="Manual",
                                                     first_component_output="ElectricityFromCHPTarget",
                                                     second_component_input="ElectricityFromCHPTarget")
            my_cfg.add_connection(my_storage_controller)

            # Output from Storage
            my_storage_controller = ComponentsConnection(first_component="HeatStorage",
                                                     second_component="HeatStorageController",
                                                     method="Manual",
                                                     first_component_output="WaterOutputTemperatureHeatingWater",
                                                     second_component_input="TemperatureHeatingStorage")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="HeatStorage",
                                                     second_component="CHP",
                                                     method="Manual",
                                                     first_component_output="WaterOutputStorageforHeaters",
                                                     second_component_input="MassflowInputTemperature")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="HeatStorage",
                                                     second_component="Building",
                                                     method="Manual",
                                                     first_component_output="RealHeatForBuilding",
                                                     second_component_input="ThermalEnergyDelivered")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="HeatStorage",
                                                     second_component="ControllerHeat",
                                                     method="Manual",
                                                     first_component_output="WaterOutputTemperatureHeatingWater",
                                                     second_component_input="StorageTemperatureHeatingWater")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="HeatStorage",
                                                     second_component="GasHeater",
                                                     method="Manual",
                                                     first_component_output="WaterOutputStorageforHeaters",
                                                     second_component_input="MassflowInputTemperature")
            my_cfg.add_connection(my_storage_controller)

            # Output from StorageController
            my_storage_controller = ComponentsConnection(first_component="HeatStorageController",
                                                     second_component="HeatStorage",
                                                     method="Manual",
                                                     first_component_output="RealThermalDemandHeatingWater",
                                                     second_component_input="ThermalDemandHeatingWater")
            my_cfg.add_connection(my_storage_controller)

            # Output from  Building
            my_storage_controller = ComponentsConnection(first_component="Building",
                                                     second_component="HeatStorageController",
                                                     method="Manual",
                                                     first_component_output="TemperatureMean",
                                                     second_component_input="BuildingTemperature")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="Building",
                                                     second_component="ControllerHeat",
                                                     method="Manual",
                                                     first_component_output="TemperatureMean",
                                                     second_component_input="ResidenceTemperature")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="Building",
                                                     second_component="BuildingController",
                                                     method="Manual",
                                                     first_component_output="TemperatureMean",
                                                     second_component_input="ResidenceTemperature")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="Building",
                                                     second_component="HeatStorageController",
                                                     method="Manual",
                                                     first_component_output="ReferenceMaxHeatBuildingDemand",
                                                     second_component_input="ReferenceMaxHeatBuildingDemand")
            my_cfg.add_connection(my_storage_controller)

            my_storage_controller = ComponentsConnection(first_component="Building",
                                                     second_component="BuildingController",
                                                     method="Manual",
                                                     first_component_output="ReferenceMaxHeatBuildingDemand",
                                                     second_component_input="ReferenceMaxHeatBuildingDemand")
            my_cfg.add_connection(my_storage_controller)

            # Output from  BuildingController
            my_storage_controller = ComponentsConnection(first_component="BuildingController",
                                                     second_component="HeatStorageController",
                                                     method="Manual",
                                                     first_component_output="RealHeatBuildingDemand",
                                                     second_component_input="RealHeatBuildingDemand")
            my_cfg.add_connection(my_storage_controller)


            # Output from GasHeater
            my_storage_controller = ComponentsConnection(first_component="GasHeater",
                                                     second_component="HeatStorage",
                                                     method="Manual",
                                                     first_component_output="ThermalOutputPower",
                                                     second_component_input="ThermalInputPower1")
            my_cfg.add_connection(my_storage_controller)

            # Output from CHP
            my_storage_controller = ComponentsConnection(first_component="CHP",
                                                     second_component="HeatStorage",
                                                     method="Manual",
                                                     first_component_output="ThermalOutputPower",
                                                     second_component_input="ThermalInputPower2")
            my_cfg.add_connection(my_storage_controller)
            my_storage_controller = ComponentsConnection(first_component="CHP",
                                                     second_component="ControllerElectricity",
                                                     method="Manual",
                                                     first_component_output="ElectricityOutput",
                                                     second_component_input="ElectricityFromCHPReal")
            my_cfg.add_connection(my_storage_controller)
            my_storage_controller = ComponentsConnection(first_component="CHP",
                                                     second_component="HydrogenStorage",
                                                     method="Manual",
                                                     first_component_output="GasDemandTarget",
                                                     second_component_input="DischargingHydrogenAmountTarget")
            my_cfg.add_connection(my_storage_controller)

            # Output from Electrolyzer
            my_storage_controller = ComponentsConnection(first_component="Electrolyzer",
                                                     second_component="ControllerElectricity",
                                                     method="Manual",
                                                     first_component_output="UnusedPower",
                                                     second_component_input="ElectricityToElectrolyzerUnused")
            my_cfg.add_connection(my_storage_controller)
            my_storage_controller = ComponentsConnection(first_component="Electrolyzer",
                                                     second_component="HydrogenStorage",
                                                     method="Manual",
                                                     first_component_output="HydrogenOutput",
                                                     second_component_input="ChargingHydrogenAmount")
            my_cfg.add_connection(my_storage_controller)

            # Outputs from HydrogenStorage
            my_storage_controller = ComponentsConnection(first_component="HydrogenStorage",
                                                         second_component="CHP",
                                                         method="Manual",
                                                         first_component_output="HydrogenNotReleased",
                                                         second_component_input="HydrogenNotReleased")
            my_cfg.add_connection(my_storage_controller)
            my_storage_controller = ComponentsConnection(first_component="HydrogenStorage",
                                                         second_component="Electrolyzer",
                                                         method="Manual",
                                                         first_component_output="HydrogenNotStored",
                                                         second_component_input="HydrogenNotStored")
            my_cfg.add_connection(my_storage_controller)

            # Output from Battery
            my_controller_to_battery = ComponentsConnection(first_component="Battery",
                                                     second_component="ControllerElectricity",
                                                     method="Manual",
                                                     first_component_output="AcBatteryPower",
                                                     second_component_input="ElectricityToOrFromBatteryReal")
            my_cfg.add_connection(my_controller_to_battery)



            # Export configuration file
            my_cfg.dump()
            #os.chdir("..")
            #os.chdir("hisim")
            os.system("python ../hisim/hisim_main.py basic_household_implicit_advanced_battery basic_household_implicit")





