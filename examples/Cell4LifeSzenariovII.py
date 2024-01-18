"""Cell4Life full model"""

# clean

# Generic
from typing import Optional

# Owned
import copy
import graphviz
import graphlib
import dot_parser
import pydot
import csv
import os
import json
import openpyxl
import shutil  # Needed for copying of excel file
from datetime import datetime
from openpyxl.styles import NamedStyle
import pandas as pd
import sys
import math
#sys.path.append("C://Users//Standard//4ward Energy Dropbox//Christof Bernsteiner//PC//Desktop//hisim//HiSim//hisim//")
#os.chdir('C:\\Users\\Standard\\Desktop\\hisim\\HiSim\\hisim\\')
from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
#from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
# from hisim.components.example_transformer import (
#     ExampleTransformer,
#     ExampleTransformerConfig,
# )
from hisim.components.sumbuilder import SumBuilderForTwoInputs, SumBuilderConfig
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components.csvloader_electricityconsumption import CSVLoader_electricityconsumption, CSVLoader_electricityconsumptionConfig
from hisim.components.csvloader_photovoltaic import CSVLoader_photovoltaic, CSVLoader_photovoltaicConfig
from hisim.components import advanced_battery_bslib
#from hisim.components import generic_pv_system
from hisim.components import generic_hydrogen_storage
from hisim.components import (controller_C4L_electrolyzer, C4L_electrolyzer, controller_predicitve_C4L_electrolyzer_fuelcell)
from hisim.components import (controller_l1_chp_CB, generic_CHP) 
from hisim.components import (controller_l1_example_controller_C4L_1a_1b, controller_l1_example_controller_C4L_2a)
from hisim.modular_household import component_connections
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim import loadtypes
from hisim import postprocessingoptions



# Christof:
def Cell4Life(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Cell4Life-Simulation Model
    
    

    """
    log.information("Starting Cell4Life-Simulation Model: ")
    
    #---Loading Input Data from Function---
    input_variablen = InputParameter()

    
    
    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_Cell4Life(
            year=2021, seconds_per_timestep=3600
        )
    
    my_simulation_parameters.predictive_control = "False"
    
    # #Just needed if prediciton controlled is activated
    # if input_variablen["szenario"]["value"] == "2a":
    #     my_simulation_parameters.predictive_control = "True"
    #     my_simulation_parameters.prediction_horizon = input_variablen["prediction_horizon"]["value"]  
    
    my_sim.set_simulation_parameters(my_simulation_parameters)
    

    # Build Results Path
    name = "VII_" + input_variablen["szenario"]["value"] +  "_S" + str(input_variablen["PreResultNumber"]["value"])+"_BCap._" + str(math.ceil(input_variablen["battery_capacity"]["value"])) + "kWh_Inv_" + str(math.ceil(input_variablen["battery_inverter_power"]["value"]/1000)) + "kW_FCPow_" + str(math.ceil(input_variablen["fuel_cell_power"]["value"]/1000)) +"kW"
    ResultPathProviderSingleton().set_important_result_path_information(
        module_directory = "C://Users//Standard//Desktop//hisim//C4LResults",
        model_name= name,
        variant_name=my_sim.setup_function,
        sorting_option=SortingOptionEnum.VARPARAMETERNAMED,
        hash_number=None,
    )
    del name
 
    # Postprocessing Options****
    
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    #my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_LINE)
    #my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS)
    #my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_CARPET)
    
  
            #******************************************************************
            #***Loading of Input Data****
            #******************************************************************
    
    #Loading Electricity consumption in W per m2 NGF****
    #my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "Current needed", "01Simulation.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,input_variablen["NGFm2"]["value"], "CurrentConspumtioninWperm2NGF")
    #my_electricityconsumption = CSVLoader(my_electricityconsumptionConfig, my_simulation_parameters)
       
    my_electricityconsumptionConfig = CSVLoader_electricityconsumptionConfig("Current total", "Current needed", "01Simulation.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,input_variablen["NGFm2"]["value"], "CurrentConspumtioninWperm2NGF")
    my_electricityconsumption = CSVLoader_electricityconsumption(my_electricityconsumptionConfig, my_simulation_parameters)

    #******************************************************************   
    #Loading Photovoltaic System  (PV Output transformed from kW in Watt)
    #my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "01Simulation.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000*input_variablen["PV_Faktor"]["value"], "OutputPVinW")
    #my_photovoltaic_system = CSVLoader(my_photovoltaic_systemConfig, my_simulation_parameters)

    my_photovoltaic_systemConfig = CSVLoader_photovoltaicConfig("PV", "PVComponent", "01Simulation.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000*input_variablen["PV_Faktor"]["value"], "OutputPVinW")
    my_photovoltaic_system = CSVLoader_photovoltaic(my_photovoltaic_systemConfig, my_simulation_parameters)
  
    #******************************************************************   
    #Loading heat demand (warm water)
    my_wartmwaterConfig = CSVLoaderConfig("WarmWater", "WarmWaterComponent", "01Simulation.csv", 2, loadtypes.LoadTypes.HEATING, loadtypes.Units.WATT, "Warmwasser", ";", ",",input_variablen["NGFm2"]["value"], "HotWaterThermischinW")
    my_wartmwater_system = CSVLoader(my_wartmwaterConfig, my_simulation_parameters)
    #******************************************************************   
    #Loading heat demand (heating system)
    my_heatingsystem_systemConfig = CSVLoaderConfig("HeatingSystem", "HeatingSystemComponent", "01Simulation.csv", 3, loadtypes.LoadTypes.HEATING, loadtypes.Units.WATT, "Waerme", ";", ",",input_variablen["NGFm2"]["value"], "HeatinW")
    my_heatingsystem_system = CSVLoader(my_heatingsystem_systemConfig , my_simulation_parameters)

    	
    #Calculate Sum of energy needed hot water and heating 
    # Create sum builder object
    my_sum_of_heat_energy_demand = SumBuilderForTwoInputs(
        config=SumBuilderConfig.get_sumbuilder_default_config(),
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sum_of_heat_energy_demand.config.name = 'Sum_of_heat_energy_demand'
    my_sum_of_heat_energy_demand.config.unit = loadtypes.Units.WATT
    my_sum_of_heat_energy_demand.output1.component_name = 'Sum_of_heat_energy_demand'

    # Connect inputs from sum object to both previous outputs
    my_sum_of_heat_energy_demand.connect_input(
        input_fieldname=my_sum_of_heat_energy_demand.SumInput1,
        src_object_name= my_wartmwater_system.component_name,
        src_field_name= my_wartmwater_system.Output1,
    )
    my_sum_of_heat_energy_demand.connect_input(
        input_fieldname=my_sum_of_heat_energy_demand.SumInput2,
        src_object_name= my_heatingsystem_system.component_name,
        src_field_name= my_heatingsystem_system.Output1,
    )


            #******************************************************************
            # Building Components of Modell
            #****************************************************************** 
    
    #******************************************************************
    #Build EMS****
    #First Controller
  
    my_electricity_controller_config = (
    controller_l1_example_controller_C4L_1a_1b.SimpleControllerConfig.get_default_config()
    )
    my_electricity_controller = (
        controller_l1_example_controller_C4L_1a_1b.SimpleController(
        name = "Elect_Controller", my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config)
    )
    my_electricity_controller.config.szenario = input_variablen["szenario"]["value"]
    
    # Prediction Controlled
    # elif input_variablen["szenario"]["value"] == "2a": #and input_variablen["prediciton_horizon"]["value"] > 0:
    #     my_electricity_controller_config = (
    #     controller_l1_example_controller_C4L_2a.SimpleControllerConfig.get_default_config()
    #     )
    #     my_electricity_controller = (
    #         controller_l1_example_controller_C4L_2a.SimpleController(
    #         name = "Elect_Controller", my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config)
    #     )
    #     my_electricity_controller.config.szenario = input_variablen["szenario"]["value"]   



    #******************************************************************   
    #Build Battery****
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour = input_variablen["battery_capacity"]["value"]
    my_advanced_battery_config.custom_pv_inverter_power_generic_in_watt = input_variablen["battery_inverter_power"]["value"]
    my_advanced_battery_config.source_weight = input_variablen["init_source_weight_battery"]["value"]
    my_advanced_battery = advanced_battery_bslib.Battery(my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config)

    #******************************************************************
    #Build Electrolyzer****

    if input_variablen["szenario"]["value"] == "1a" or input_variablen["szenario"]["value"] == "1b":
        electrolyzer_controller_config = controller_C4L_electrolyzer.C4LelectrolyzerControllerConfig.get_default_config_electrolyzer()
        electrolyzer_controller_config.off_on_SOEC = input_variablen["off_on_SOEC"]["value"]
        electrolyzer_controller_config.on_off_SOEC = input_variablen["on_off_SOEC"]["value"]
        electrolyzer_controller_config.h2_soc_upper_threshold_electrolyzer = input_variablen["h2_soc_upper_threshold_electrolyzer"]["value"]
        my_electrolyzer_controller = controller_C4L_electrolyzer.C4LelectrolyzerController(
            my_simulation_parameters=my_simulation_parameters, config=electrolyzer_controller_config)
    
    elif input_variablen["szenario"]["value"] == "2a":
        electrolyzerfuelcell_controller_config = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveControllerConfig.get_default_config_electrolyzerfuelcell()
        electrolyzerfuelcell_controller_config.off_on_SOEC = input_variablen["off_on_SOEC"]["value"]
        electrolyzerfuelcell_controller_config.on_off_SOEC = input_variablen["on_off_SOEC"]["value"]
        electrolyzerfuelcell_controller_config.h2_soc_upper_threshold_electrolyzer = input_variablen["h2_soc_upper_threshold_electrolyzer"]["value"]
        electrolyzerfuelcell_controller_config.h2_soc_lower_threshold_fuelcell = input_variablen["h2_soc_lower_threshold_chp"]["value"]

        my_electrolyzerfuelcellcontroller = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController(
            my_simulation_parameters=my_simulation_parameters, config=electrolyzerfuelcell_controller_config)



    #*Electrolyzer*s
    electrolyzer_config = C4L_electrolyzer.C4LElectrolyzerConfig.get_default_config()
    electrolyzer_config.source_weight = input_variablen["electrolyzer_source_weight"]["value"]
    electrolyzer_config.p_el = input_variablen["p_el_elektrolyzer"]["value"]

    my_electrolyzer = C4L_electrolyzer.C4LElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config
    )
    
   
    #******************************************************************
    #Build Fuel Cell****
    #First Controller
    if input_variablen["szenario"]["value"] == "1a" or input_variablen["szenario"]["value"] == "1b":
        chp_controller_config = controller_l1_chp_CB.L1CHPControllerConfig.get_default_config_fuel_cell()
    
        #Build Chp Controller
        chp_controller_config.source_weight = input_variablen["init_source_weight_chp"]["value"]
        chp_controller_config.electricity_threshold = input_variablen["electricity_threshold"]["value"]
        chp_controller_config.min_operation_time_in_seconds = input_variablen["min_operation_time_in_seconds_chp"]["value"]
        chp_controller_config.min_idle_time_in_seconds = input_variablen["min_resting_time_in_seconds_chp"]["value"]
        chp_controller_config.h2_soc_threshold = input_variablen["h2_soc_lower_threshold_chp"]["value"]
        chp_controller_config.off_on_SOEC = input_variablen["off_on_SOEC"]["value"]
        chp_controller_config.on_off_SOEC = input_variablen["on_off_SOEC"]["value"]
        my_chp_controller = controller_l1_chp_CB.L1CHPController(
            my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
        )

    chp_config = generic_CHP.CHPConfig.get_default_config_fuelcell_p_el_based(fuel_cell_power=input_variablen["fuel_cell_power"]["value"])
    chp_config.source_weight = input_variablen["init_source_weight_chp"]["value"]
    chp_config.h_fuel = input_variablen["h_fuel"]["value"]


    my_chp = generic_CHP.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config, 
    )

     #******************************************************************
    #Build Hydrogen Storage****
    #First Controller
    h2_storage_config = generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config(
        
        max_charging_rate = 10e32,  #Storage Charging Rate in kg/s: 
        max_discharging_rate = 10e32,        #Storage Discharging Rate in kg/s
        #max_charging_rate=electrolyzer_power / (3.6e3 * 3.939e4),
        #max_discharging_rate=input_variablen["fuel_cell_power"] / (3.6e3 * 3.939e4), 
        source_weight=input_variablen["init_source_weight_hydrogenstorage"]["value"],
    )
    h2_storage_config.energy_for_charge_based_on_massflow_h_fuel = input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]
    h2_storage_config.energy_for_discharge_based_on_massflow_h_fuel = input_variablen["h2storage_energy_for_discharge_based_on_massflow_h_fuel"]["value"]
    h2_storage_config.energy_for_operation = input_variablen["h2storage_energy_for_operation"]["value"]
    h2_storage_config.h_fuel = input_variablen["h_fuel"]["value"]
    h2_storage_config.loss_factor_per_day = input_variablen["h2_storage_losses"]["value"] #H2 Storage losses per Day
    h2_storage_config.max_capacity = input_variablen["h2_storage_capacity_max"]["value"]
    
    my_h2storage = generic_hydrogen_storage.GenericHydrogenStorage(
        my_simulation_parameters=my_simulation_parameters, config=h2_storage_config
    )
            
            #******************************************************************
            #****Connect Component Inputs with Outputs****
            #******************************************************************



    if input_variablen["szenario"]["value"] == "1a" or input_variablen["szenario"]["value"] == "1b":
        my_h2storage.connect_only_predefined_connections(my_chp)
        my_h2storage.connect_only_predefined_connections(my_electrolyzer)
        my_chp_controller.connect_only_predefined_connections(my_h2storage)
        my_electrolyzer_controller.connect_only_predefined_connections(my_h2storage)
        my_chp.connect_only_predefined_connections(my_chp_controller)
        my_electrolyzer.connect_only_predefined_connections(my_electrolyzer_controller)

        my_electricity_controller.connect_only_predefined_connections(my_h2storage)
        my_electricity_controller.connect_only_predefined_connections(my_electrolyzer)
        my_electricity_controller.connect_only_predefined_connections(my_electricityconsumption)
        my_electricity_controller.connect_only_predefined_connections(my_photovoltaic_system)
        my_electricity_controller.connect_only_predefined_connections(my_chp)
    elif input_variablen["szenario"]["value"] == "2a":
        my_h2storage.connect_only_predefined_connections(my_chp)
        my_h2storage.connect_only_predefined_connections(my_electrolyzer)
        my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_h2storage)
        my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_h2storage)
        my_chp.connect_only_predefined_connections(my_electrolyzerfuelcellcontroller)
        my_electrolyzer.connect_only_predefined_connections(my_electrolyzerfuelcellcontroller)

        my_electricity_controller.connect_only_predefined_connections(my_h2storage)
        my_electricity_controller.connect_only_predefined_connections(my_electrolyzer)
        my_electricity_controller.connect_only_predefined_connections(my_electricityconsumption)
        my_electricity_controller.connect_only_predefined_connections(my_photovoltaic_system)
        my_electricity_controller.connect_only_predefined_connections(my_chp)

    #battery input connection
    my_electricity_controller.connect_input(
        input_fieldname=my_electricity_controller.BatteryStateOfCharge,
        src_object_name=my_advanced_battery.component_name,
        src_field_name=my_advanced_battery.StateOfCharge,
    )

        
    my_electricity_controller.connect_input(
        input_fieldname=my_electricity_controller.BatteryAcBatteryPower,
        src_object_name=my_advanced_battery.component_name,
        src_field_name=my_advanced_battery.AcBatteryPower,
    )

    my_electricity_controller.connect_input(
        input_fieldname=my_electricity_controller.BatteryDcBatteryPower,
        src_object_name=my_advanced_battery.component_name,
        src_field_name=my_advanced_battery.DcBatteryPower,
    )

    my_advanced_battery.connect_input(
        input_fieldname=my_advanced_battery.LoadingPowerInput,
        src_object_name=my_electricity_controller.component_name,
        src_field_name=my_electricity_controller.BatteryLoadingPowerWish,
    )



        #******************************************************************
        # Add Components to Simulation Parameters
        #******************************************************************

    if input_variablen["szenario"]["value"] == "1a" or input_variablen["szenario"]["value"] == "1b":
        my_sim.add_component(my_photovoltaic_system)
        my_sim.add_component(my_electricityconsumption)
        my_sim.add_component(my_h2storage)
        my_sim.add_component(my_advanced_battery)
        my_sim.add_component(my_electrolyzer_controller)
        my_sim.add_component(my_electrolyzer)
        my_sim.add_component(my_chp_controller)
        my_sim.add_component(my_chp)
        my_sim.add_component(my_electricity_controller)
            
        my_sim.add_component(my_wartmwater_system)
        my_sim.add_component(my_heatingsystem_system)
        my_sim.add_component(my_sum_of_heat_energy_demand)
    elif input_variablen["szenario"]["value"] == "2a":
        my_sim.add_component(my_photovoltaic_system)
        my_sim.add_component(my_electricityconsumption)
        my_sim.add_component(my_h2storage)
        my_sim.add_component(my_advanced_battery)
        my_sim.add_component(my_electrolyzerfuelcellcontroller)
        my_sim.add_component(my_electrolyzer)
        my_sim.add_component(my_chp)
        my_sim.add_component(my_electricity_controller)
            
        my_sim.add_component(my_wartmwater_system)
        my_sim.add_component(my_heatingsystem_system)
        my_sim.add_component(my_sum_of_heat_energy_demand)

def InputParameter():
    """
    Loading Funktion for all Input Parameters
    --There exists static Variables, which are not varied in the parameter study
    
    -- "Variation Parameters": Parameters, which should be variied: This parameters are loaded with the csv loader; Variation Parameters are defined in the Cell4Life-SimulationExecutor.py class
        and are just loaded within this function for the execution for the simulation. 
    
    --Parameters witch depends on variation parameters: Some parameters depends on variation parameters...
    
    
    Variation parameters:
        fuel_cell_power  = param_1 #Electricity Power of Fuel Cell Power in Watt
        battery_capacity: Optional[float] = param_2   #Total Capacity of Battery in kWh

    Depending on variation parameters:    
        battery_inverter_power = battery_capacity/12*1000 #in Watt: Batterie Inverter power is assumed to depend on Battery Capacity 

    
    Static Parameters within this example:

        #NettoGesamtfläche (total area in squaremeters of building(s))
        NGFm2 = 26804.8
        PV_Faktor = 1.6 #Multiplier for PV-power (1--> given PV power; 2--> double of given PV power; 3--> 3 times given PV power)
        init_source_weight_battery = 1
        electricity_threshold = 0 #Minium required power to activate fuel cell
        init_source_weight_hydrogenstorage = 999 #init_source_weight_electrolyzer
        init_source_weight_chp = 2
        p_el_elektrolyzer = fuel_cell_power*2 #Electrical Operating Power in Watt
        electrolyzer_source_weight = 999
        h2_storage_capacity_max = 12000  #Maximum of hydrogen storage in kg
        h2_storage_losses = 0 # % of Hydrogen Losses per day in %
        h2_soc_upper_threshold_electrolyzer = 0  #Electrolyzer works just until H2 storage goes up to this threshold
        min_operation_time_in_seconds_chp = 0 #It is not working well so let it be "0"
        min_resting_time_in_seconds_chp = 0 # This does not work well so let it be 0
        h2_soc_lower_threshold_chp = 0 # Minimum state of charge to start operating the fuel cell in %
        on_off_SOEC = 183 #timestep: Turn off Electrolyzer and turn on Fuel Cell // Variable name should be read: turn SOEC from "on" to "off" // Day Depends on starting date: e.g. timestep of the year 2021 is 10. Januar if the simulation year starts with 1st Jannuar;
        off_on_SOEC = 500 #timestep: Turn on Electrolyzer and turn off on Fuel Cell

    Integration of csvload 
    ##Loading of Project Data
    """
    
    
    #Loading of variation parameters
    param_df = pd.read_csv("examples/params_to_loop.csv")
    
    PreResultNumber  = param_df["PreResultNumber"][0]
    FuelCellPowerW = param_df["FuelCellPowerW"][0]
    BatteryCapkWh = param_df["BatteryCapkWh"][0]
    Inverter_Ratio = param_df["Inverter_Ratio"][0]

    PreResultNumberUnit = param_df["PreResultNumberUnit"][0]
    FuelCellPowerWUnit = param_df["FuelCellPowerWUnit"][0]
    BatteryCapkWhUnit = param_df["BatteryCapkWhUnit"][0]
    Inverter_RatioUnit = param_df["Inverter_RatioUnit"][0]

    szenario = param_df["szenario"][0]
    szenarioUnit = param_df["szenarioUnit"][0]

    prediction_horizon = param_df["prediction_horizon"][0]
    prediction_horizonUnit = param_df["prediction_horizonUnit"][0]
    #Variation Parameters:
    battery_capacity: Optional[float] = BatteryCapkWh   #Total Capacity of Battery in kWh
    battery_capacityUnit = BatteryCapkWhUnit
    

    #Test Christof:

    fuel_cell_power  = FuelCellPowerW #Electricity Power of Fuel Cell Power in Watt
    fuel_cell_powerUnit = FuelCellPowerWUnit
    
    

    
    del BatteryCapkWh, FuelCellPowerW, BatteryCapkWhUnit, FuelCellPowerWUnit 
    
    #Following parameter depends on a "variation parameter"
    battery_inverter_power = battery_capacity*1000*Inverter_Ratio #in Watt: Batterie Inverter power is assumed to depend on Battery Capacity which is given in kWh!
    battery_inverter_powerUnit = "W"

  

    #Static Parameters:
    NGFm2 = 26804.8 #NettoGesamtfläche (total area in squaremeters of building(s))
    NGFm2Unit = "m2"
    
    PV_Faktor = 1.6 #Multiplier for PV-power (1--> given PV power; 2--> double of given PV power; 3--> 3 times given PV power)
    PV_FaktorUnit = "-"
    
    init_source_weight_battery = 1
    init_source_weight_batteryUnit = "-"
    
    electricity_threshold = 0 #Minium required power to activate fuel cell
    electricity_thresholdUnit = "W"
    
    init_source_weight_hydrogenstorage = 999 #init_source_weight_electrolyzer
    init_source_weight_hydrogenstorageUnit = "-"
    
    init_source_weight_chp = 2
    init_source_weight_chpUnit = "W"
    
    p_el_elektrolyzer = fuel_cell_power*2.1 #Electrical Operating Power in Watt
    p_el_elektrolyzerUnit = "W"
    
    electrolyzer_source_weight = 999
    electrolyzer_source_weightUnit = "-"
    

    
    min_operation_time_in_seconds_chp = 0 #It is not working well so let it be "0"
    min_operation_time_in_seconds_chpUnit = "s"
    
    min_resting_time_in_seconds_chp = 0 # This does not work well so let it be 0
    min_resting_time_in_seconds_chpUnit = "s"
    
    min_operation_time_in_seconds_electrolyzer = 0 #It is not working well so let it be "0"
    min_operation_time_in_seconds_electrolyzerUnit = "s"
    
    min_resting_time_in_seconds_electrolyzer  = 0 # This does not work well so let it be 0
    min_resting_time_in_seconds_electrolyzerUnit = "s"

    h2_soc_lower_threshold_chp = 0 # Minimum state of charge to start operating the fuel cell in %
    h2_soc_lower_threshold_chpUnit = "%"
    
    h_fuel = 33.3 #heatng value ("Heizwert/Brennwert") of the choosen fuel in kWh/kg; upper value for H2 = 39,39 kWh/kg (3.939e4 Wh/kg); lower value for H2 = 33,3 
    h_fuelUnit = "kWh/kg"
    
    on_off_SOEC = 4391 #timestep: Turn off Electrolyzer and turn on Fuel Cell // Variable name should be read: turn SOEC from "on" to "off" // timestep Depends on starting date: e.g. timestep 10 of the year 2021 is 10. Januar if the simulation year starts with 1st Jannuar;
    on_off_SOECUnit = "timesteps (Turn off Electrolyzer and turn on Fuel Cell)"
    
    off_on_SOEC = 100000 #timestep: Turn on Electrolyzer and turn off on Fuel Cell
    off_on_SOECUnit = "timesteps (Turn on Electrolyzer and turn off on Fuel Cell"
    
    #h2_storage_capacity_max = 50000  #Maximum of hydrogen storage in kg
    h2_storage_capacity_max = p_el_elektrolyzer / (3600*40000) *3600 * (on_off_SOEC+96) #Storage Capacity based on Electrolyzer Production Rate --> + some storage capacity of 96 hours more
    
    
    h2_storage_capacity_maxUnit = "kg"
    h2_storage_losses = 0 # % of Hydrogen Losses per day in %
    h2_storage_lossesUnit = "%"
    
    h2_soc_upper_threshold_electrolyzer = 99  #Electrolyzer works just until H2 storage goes up to this threshold
    h2_soc_upper_threshold_electrolyzerUnit = "%"

    #H2 storage energy demands; the energy demand is covered by electricit; 
    # -in general: if storage is empty, no energy is needed!
    # -energy for charging, discharging and for operation of the tank is considered; 
    # -the operation energy demand is always considererd and added to charging/discharging energy demand if there is fuel stored; 
    
    #charging: in % in relation to the energy content of the fuel which is stored in the time step; e.g. xx % of energy of energy contend of the fuel stored in the time step
    h2storage_energy_for_charge_based_on_massflow_h_fuel = 12  #Energieaufwand nach [19] Wikipedia: 12 % des Energieinhalts des Wasserstoffs gehen für Komprimierung auf 700 bar auf
    h2storage_energy_for_charge_based_on_massflow_h_fuelUnit = "%" #of given h_fuel heat value
    #discharging: in % in relation to the energy content of the fuel which is withdrawn in the time step 
    h2storage_energy_for_discharge_based_on_massflow_h_fuel = 0
    h2storage_energy_for_discharge_based_on_massflow_h_fuelUnit = "%" #of given h_fuel heat value
    #operation
    h2storage_energy_for_operation = 0 #in Watt, energy demand just for operation, if there isb fuel stored in the tank; this energy amount is always added to charging & discharging energy; if no fuel is in the tank, this energy is not considered! (h2 storage does not need energy)
    h2storage_energy_for_operationUnit = "W" 

    
    input_variablen = {
        "PreResultNumber": {
            "value": PreResultNumber,
            "unit": PreResultNumberUnit,
        },

        "battery_capacity": {
            "value": battery_capacity,
            "unit": battery_capacityUnit,
        },

        "battery_inverter_power": {
            "value": battery_inverter_power,
            "unit": battery_inverter_powerUnit,
        },

        "fuel_cell_power": {
            "value": fuel_cell_power,
            "unit": fuel_cell_powerUnit,
        },

        "NGFm2": {
            "value": NGFm2,
            "unit": NGFm2Unit,
        },

        "PV_Faktor": {
            "value": PV_Faktor,
            "unit": PV_FaktorUnit,
        },

        "init_source_weight_battery": {
            "value": init_source_weight_battery,
            "unit": init_source_weight_batteryUnit,
        },

        "electricity_threshold": {
            "value": electricity_threshold,
            "unit": electricity_thresholdUnit,
        },
        "init_source_weight_hydrogenstorage": {
            "value": init_source_weight_hydrogenstorage,
            "unit":init_source_weight_hydrogenstorageUnit,
        },

        "init_source_weight_chp": {
            "value": init_source_weight_chp,
            "unit": init_source_weight_chpUnit,
        },
        
        "p_el_elektrolyzer": {
            "value": p_el_elektrolyzer,
            "unit": p_el_elektrolyzerUnit,
        },
        "electrolyzer_source_weight":{
            "value": electrolyzer_source_weight,
            "unit": electrolyzer_source_weightUnit,
        },
        "h2_storage_capacity_max":{
            "value": h2_storage_capacity_max,
            "unit": h2_storage_capacity_maxUnit,
        },

        "h2_storage_losses": {
            "value": h2_storage_losses,
            "unit": h2_storage_lossesUnit,
        },
        "h2_soc_upper_threshold_electrolyzer": {
            "value":h2_soc_upper_threshold_electrolyzer,
            "unit": h2_soc_upper_threshold_electrolyzerUnit,
        },
        "min_operation_time_in_seconds_chp": {
            "value": min_operation_time_in_seconds_chp,
            "unit": min_operation_time_in_seconds_chpUnit,
        },
        "min_resting_time_in_seconds_chp": {
            "value": min_resting_time_in_seconds_chp,
            "unit": min_resting_time_in_seconds_chpUnit,
        },

        "min_operation_time_in_seconds_electrolyzer": {
            "value": min_operation_time_in_seconds_electrolyzer,
            "unit": min_operation_time_in_seconds_electrolyzerUnit,
        },
        "min_resting_time_in_seconds_electrolyzer": {
            "value": min_resting_time_in_seconds_electrolyzer,
            "unit": min_resting_time_in_seconds_electrolyzerUnit,
        },

        "h2_soc_lower_threshold_chp": {
            "value": h2_soc_lower_threshold_chp,
            "unit": h2_soc_lower_threshold_chpUnit,
        },
        "on_off_SOEC": {
            "value": on_off_SOEC,
            "unit": on_off_SOECUnit,
        },
        "off_on_SOEC": {
            "value": off_on_SOEC,
            "unit": off_on_SOECUnit,
        },
        "h_fuel": {
            "value": h_fuel,
            "unit": h_fuelUnit,
        },
        
        "h2storage_energy_for_charge_based_on_massflow_h_fuel": {
            "value": h2storage_energy_for_charge_based_on_massflow_h_fuel,
            "unit": h2storage_energy_for_charge_based_on_massflow_h_fuelUnit,
        },
        
        "h2storage_energy_for_discharge_based_on_massflow_h_fuel": {
            "value": h2storage_energy_for_discharge_based_on_massflow_h_fuel,
            "unit": h2storage_energy_for_discharge_based_on_massflow_h_fuelUnit,
        },
        "h2storage_energy_for_operation": {
            "value": h2storage_energy_for_operation,
            "unit": h2storage_energy_for_operationUnit,
        },
        "Inverter_Ratio": {
            "value": Inverter_Ratio,
            "unit": Inverter_RatioUnit,
        },

        "szenario": {
            "value": szenario,
            "unit": szenarioUnit,
        },

        "prediction_horizon": {
            "value": prediction_horizon,
            "unit": prediction_horizonUnit,
        },
        
    }
        

    return input_variablen