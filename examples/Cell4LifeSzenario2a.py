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
from hisim.components import (C4L_electrolyzer, controller_predicitve_C4L_electrolyzer_fuelcell)
from hisim.components import (generic_CHP) 
from hisim.components import controller_l1_example_controller_C4L_2a
from hisim.modular_household import component_connections
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum
from hisim import loadtypes
from hisim import postprocessingoptions



# Christof:
def Cell4Life(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """hisim example for Cell4Life-Simulation Model: Szenario 2a!
    
    

    """
    log.information("Starting hisim example: Cell4LifeSzenario2a.py ")
    
    #---Loading Input Data from Function---
    input_variablen = InputParameter()

    
    
    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_Cell4Life(
            year=2021, seconds_per_timestep=3600
        )
    
    my_simulation_parameters.predictive_control = "True"
    my_simulation_parameters.prediction_horizon = input_variablen["prediction_horizon"]["value"]  
    
    
    my_sim.set_simulation_parameters(my_simulation_parameters)
    

    # Build Results Path
    name = "VII_" + input_variablen["szenario"]["value"] +  "_S" + str(input_variablen["PreResultNumber"]["value"])+"_BCap._" + str(math.ceil(input_variablen["battery_capacity"]["value"])) + "kWh_Inv_" + str(math.ceil(input_variablen["battery_inverter_power"]["value"]/1000)) + "kW_FCPow_" + str(math.ceil(input_variablen["fuel_cell_power"]["value"]/1000)) +"kW"
    
    name = "TestvII2"

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
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_LINE)
    #my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_CARPET)
    

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
    #Build Energy Management System (EMS)****
    #Defines where the energies flow in the system.

    my_electricity_controller_config = (
    controller_l1_example_controller_C4L_2a.SimpleControllerConfig.get_default_config()
    )
    
    my_electricity_controller_config.off_on_SOEC = input_variablen["off_on_SOEC"]["value"]
    my_electricity_controller_config.on_off_SOEC = input_variablen["on_off_SOEC"]["value"]


    my_electricity_controller = (
        controller_l1_example_controller_C4L_2a.SimpleController( # [x] Energy Managment System in case of szenario 2 needs a new name --> do not delete the old version
        name = "Elect_Controller", my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config)
    )
    my_electricity_controller.config.szenario = input_variablen["szenario"]["value"]


    #******************************************************************   
    #Build Battery****
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.get_default_config()
    my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour = input_variablen["battery_capacity"]["value"]
    my_advanced_battery_config.custom_pv_inverter_power_generic_in_watt = input_variablen["battery_inverter_power"]["value"]
    my_advanced_battery_config.source_weight = input_variablen["init_source_weight_battery"]["value"]
    my_advanced_battery = advanced_battery_bslib.Battery(my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config)

    #******************************************************************
    #Build Predictive Electrolyzer & fuel cell controller****

    electrolyzerfuelcell_controller_config = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveControllerConfig.get_default_config_electrolyzerfuelcell()
    electrolyzerfuelcell_controller_config.off_on_SOEC = input_variablen["off_on_SOEC"]["value"]
    electrolyzerfuelcell_controller_config.on_off_SOEC = input_variablen["on_off_SOEC"]["value"]
    electrolyzerfuelcell_controller_config.h2_soc_upper_threshold_electrolyzer = input_variablen["h2_soc_upper_threshold_electrolyzer"]["value"]
    electrolyzerfuelcell_controller_config.h2_soc_lower_threshold_fuelcell = input_variablen["h2_soc_lower_threshold_chp"]["value"]
    electrolyzerfuelcell_controller_config.p_el_elektrolyzer = input_variablen["p_el_elektrolyzer"]["value"]
    electrolyzerfuelcell_controller_config.fuel_cell_power = input_variablen["fuel_cell_power"]["value"]

    electrolyzerfuelcell_controller_config.Electrical_power_surplus_related_to_electrolzyer_percentage = input_variablen["Electrical_power_surplus_related_to_electrolzyer_percentage"]["value"]
    electrolyzerfuelcell_controller_config.Electrical_power_demand_related_to_fuelcell_percentage = input_variablen["Electrical_power_demand_related_to_fuelcell_percentage"]["value"]


    electrolyzerfuelcell_controller_config.Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage = input_variablen["Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage"]["value"] 
    electrolyzerfuelcell_controller_config.Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage = input_variablen["Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage"]["value"]

    electrolyzerfuelcell_controller_config.Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage = input_variablen["Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage"]["value"]
    electrolyzerfuelcell_controller_config.Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage = input_variablen["Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage"]["value"]


    electrolyzerfuelcell_controller_config.minruntime_electrolyzer = input_variablen["min_operation_time_in_seconds_electrolyzer"]["value"] # [x]  gehört nicht zu input variablen hinzugefügt
    electrolyzerfuelcell_controller_config.minstandbytime_electrolyzer = input_variablen["minstandbytime_electrolyzer"]["value"] # [x] gehört noch zu input variablen hinzugefügt
    electrolyzerfuelcell_controller_config.minbatterystateofcharge_electrolyzer_turnon = input_variablen["minbatterystateofcharge_electrolyzer_turnon"]["value"]  #minimal battery state of charge, which is necessary, to turn on electrolyzer... in % 
    electrolyzerfuelcell_controller_config.minbatterystateofcharge_let_electrolyzer_staysturnedon = input_variablen["minbatterystateofcharge_let_electrolyzer_staysturnedon"]["value"] #Minimal battery state of charge, which is necessary, that electrolyzer stays turned on, in %
    
    electrolyzerfuelcell_controller_config.maxbatterystateofcharge_fuelcell_turnon = input_variablen["maxbatterystateofcharge_fuelcell_turnon"]["value"] #maximum battery state of charge (upper threshold) --> to turn on fuel cell..if the actual state of charge of battery is above this state of charge, than to not turn on fuel cell, in %
    electrolyzerfuelcell_controller_config.minruntime_fuelcell = input_variablen["min_operation_time_in_seconds_chp"]["value"]
    electrolyzerfuelcell_controller_config.minstandbytime_fuelcell = input_variablen["minstandbytime_fuelcell"]["value"]
    
    electrolyzerfuelcell_controller_config.maxbatterystateofcharge_let_fuelcell_staysturnedon = input_variablen["maxbatterystateofcharge_let_fuelcell_staysturnedon"]["value"]

    my_electrolyzerfuelcellcontroller = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzerfuelcell_controller_config)



    #Build Electrolyzer*
    electrolyzer_config = C4L_electrolyzer.C4LElectrolyzerConfig.get_default_config()
    electrolyzer_config.source_weight = input_variablen["electrolyzer_source_weight"]["value"]
    electrolyzer_config.p_el = input_variablen["p_el_elektrolyzer"]["value"]
    electrolyzer_config.p_el_percentage_standby_electrolyzer = input_variablen["p_el_percentage_standby_electrolyzer"]["value"]
    my_electrolyzer = C4L_electrolyzer.C4LElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config
    )
    
    #Build Fuel Cell****

    chp_config = generic_CHP.CHPConfig.get_default_config_fuelcell_p_el_based(fuel_cell_power=input_variablen["fuel_cell_power"]["value"])
    chp_config.source_weight = input_variablen["init_source_weight_chp"]["value"]
    chp_config.h_fuel = input_variablen["h_fuel"]["value"]
    chp_config.p_el_percentage_standby_fuelcell = input_variablen["p_el_percentage_standby_fuelcell"]["value"]

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

    my_h2storage.connect_only_predefined_connections(my_chp)
    my_h2storage.connect_only_predefined_connections(my_electrolyzer)
    
    my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_h2storage)
    my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_advanced_battery)
    my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_electricityconsumption)
    my_electrolyzerfuelcellcontroller.connect_only_predefined_connections(my_photovoltaic_system)

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
    
    -- "Variation Parameters": Parameters, which should be variied: This parameters are loaded with the csv loader; Variation Parameters are defined in the Cell4LifeSimulationExecutorvII.py class
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
        p_el_elektrolyzer = fuel_cell_power*2.1 #Electrical Operating Power in Watt
        electrolyzer_source_weight = 999
        h2_storage_capacity_max = 12000  #Maximum of hydrogen storage in kg
        h2_storage_losses = 0 # % of Hydrogen Losses per day in %
        h2_soc_upper_threshold_electrolyzer = 0  #Electrolyzer works just until H2 storage goes up to this threshold
        min_operation_time_in_seconds_chp = 0 #It is not working well so let it be "0"
        minstandbytime_fuelcell = 0 # This does not work well so let it be 0
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
    
    # [ ] Change to fuel consumption if it is in standby!
    p_el_percentage_standby_fuelcell = 10 #If fuel cell is running in standby, it needs so much electricity power in % of its electricitiy production power if it is running
    p_el_percentage_standby_fuelcellUnit = "%"

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

    p_el_percentage_standby_electrolyzer = 10 #if electrolyzer runs in standby, than it needs "p_el_percentage_standby_electrolyzer" (%) electricity power of the operating power 
    p_el_percentage_standby_electrolyzerUnit = "%"
    
    electrolyzer_source_weight = 999
    electrolyzer_source_weightUnit = "-"

    min_operation_time_in_seconds_chp = 0 #It is not working well so let it be "0"
    min_operation_time_in_seconds_chpUnit = "s"
    
    minstandbytime_fuelcell = 0 # This does not work well so let it be 0
    minstandbytime_fuelcellUnit = "s"
    
    min_operation_time_in_seconds_electrolyzer = 0 #It is not working well so let it be "0"
    min_operation_time_in_seconds_electrolyzerUnit = "s"
    
    minstandbytime_electrolyzer  = 0 # This does not work well so let it be 0
    minstandbytime_electrolyzerUnit = "s"

    h2_soc_lower_threshold_chp = 0 # Minimum state of charge to start operating the fuel cell in %
    h2_soc_lower_threshold_chpUnit = "%"
    
    h_fuel = 33.3 #heatng value ("Heizwert/Brennwert") of the choosen fuel in kWh/kg; upper value for H2 = 39,39 kWh/kg (3.939e4 Wh/kg); lower value for H2 = 33,3 
    h_fuelUnit = "kWh/kg"
    
    on_off_SOEC = 4391 #timestep: Turn off Electrolyzer and turn on Fuel Cell // Variable name should be read: turn SOEC from "on" to "off" // timestep Depends on starting date: e.g. timestep 10 of the year 2021 is 10. Januar if the simulation year starts with 1st Jannuar;
    on_off_SOECUnit = "timesteps (Defines season where Electrolyzer or Fuel Cell is running: Turn off Electrolyzer and turn on Fuel Cell)"
    
    off_on_SOEC = 100000 #timestep: Turn on Electrolyzer and turn off on Fuel Cell 
    off_on_SOECUnit = "timesteps (Turn on Electrolyzer and turn off on Fuel Cell"
    
    #h2_storage_capacity_max = 50000  #Maximum of hydrogen storage in kg
    h2_storage_capacity_max = p_el_elektrolyzer / (3600*40000) *3600 * (on_off_SOEC+96) #[ ] Just an estimation used!  Storage Capacity estimation based on Electrolyzer Production Rate --> + some storage capacity of 96 hours more
    
    h2_storage_capacity_maxUnit = "kg"
    h2_storage_losses = 0 # % of Hydrogen Losses per day in %
    h2_storage_lossesUnit = "%"
    
    h2_soc_upper_threshold_electrolyzer = 100  #Electrolyzer works just until H2 storage goes up to this threshold
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
    h2storage_energy_for_operation = 0 #in Watt, energy demand just for operation, if there is fuel stored in the tank; this energy amount is always added to charging & discharging energy; if no fuel is in the tank, this energy is not considered! (h2 storage does not need energy)
    h2storage_energy_for_operationUnit = "W" 

    #Predictive Controller Fuel Cell Electrolyzer factors
    Electrical_power_surplus_related_to_electrolzyer_percentage = 100 
    Electrical_power_surplus_related_to_electrolzyer_percentageUnit = "%" #Faktor in % which represents the ratio  between [PV-HouseConsumption]/Electrolzyer ...--> 100 % means, that the surpluse energy (PV-houseconsumption) is equivalent to electrolyzer; at this ratio, the electrolyzer could be turned on if the next decisions in the decision tree are answered positively 
    
    Electrical_power_demand_related_to_fuelcell_percentage = 100
    Electrical_power_demand_related_to_fuelcell_percentageUnit = "%" #Faktor in % which represents the ratio  between [HouseConsumption-PV]/FuelCell (each in Watt)...--> 100 % means, that the  energy demand (houseconsumption-PV) is equivalent to fuel cell output; at this ratio, the fuel cell could be turned on if the next decisions in the decision tree are answered positively, or the Fuel Cell stays turned on if it is already running

    minbatterystateofcharge_electrolyzer_turnon = 80 #minimal battery state of charge, which is necessary, to turn on electrolyzer... in % 
    minbatterystateofcharge_electrolyzer_turnonUnit = "%" 

    minbatterystateofcharge_let_electrolyzer_staysturnedon = 0.1 #Minimal battery state of charge, which is necessary, that electrolyzer stays turned on, in %
    minbatterystateofcharge_let_electrolyzer_staysturnedonUnit = "%"

    maxbatterystateofcharge_fuelcell_turnon = 80 #maximum battery state of charge; if the actual battery state of charge is above this level, then the fuel cell will not be turned on
    maxbatterystateofcharge_fuelcell_turnonUnit = "%"

    maxbatterystateofcharge_let_fuelcell_staysturnedon = 100 #Maximum battery state of charge; if that threshold is exceeded, then the electrolyseur will be turned off
    maxbatterystateofcharge_let_fuelcell_staysturnedonUnit = "%"
    #FOLLOWING FACTORS Electrolyzer:
    #****
    Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage = 75 
    Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentageUnit = "%" 
    
    Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage = Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage
    Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentageUnit = "%"

    #Faktor in % which represents the ratio between useable surplus energy amount and electricity consumption electrolyzer amount, both for prediction horizon OR minimum standby time of electrolyzer;
    #100 means, in the prediciton horizion, the useable energy amout pv production fully covers the energy demand of the electrolyzer
    #Please consider the controller-predicitve for fuel cell & electrolyzer, where it is explained, what useable energy amount of pv production means!

    #Following Factors  are for fuel cell:
    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage = 75
    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentageUnit = "%"

    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage = Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage
    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentageUnit = "%"
    
    #Factro in % represents how much energy from the fuel cell delivered within the prediction horizon can be used to cover the energy demand; if the energy demand in one time step is smaller than
    #the energy delivered by the fuel cell, only a part of the fuel cell energy can be used directy to cover the energy demand of the house (rest will be stored in battery or in the grid)
    #if the energy demand is higher than or is equal to that what the fuel cell delivers at a timestep, all energy delivered by the fuel cell can be used to cover the energy demand of the house.

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
        "minstandbytime_fuelcell": {
            "value": minstandbytime_fuelcell,
            "unit": minstandbytime_fuelcellUnit,
        },

        "min_operation_time_in_seconds_electrolyzer": {
            "value": min_operation_time_in_seconds_electrolyzer,
            "unit": min_operation_time_in_seconds_electrolyzerUnit,
        },
        "minstandbytime_electrolyzer": {
            "value": minstandbytime_electrolyzer,
            "unit": minstandbytime_electrolyzerUnit,
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


        "Electrical_power_surplus_related_to_electrolzyer_percentage": {
            "value": Electrical_power_surplus_related_to_electrolzyer_percentage,
            "unit": Electrical_power_surplus_related_to_electrolzyer_percentageUnit,
        },

        "Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage": {
            "value": Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage,
            "unit": Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentageUnit,
        },

        "Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage": {
            "value": Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage,
            "unit": Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentageUnit,
        },

        "Electrical_power_demand_related_to_fuelcell_percentage": {
            "value": Electrical_power_demand_related_to_fuelcell_percentage,
            "unit": Electrical_power_demand_related_to_fuelcell_percentageUnit,
        },


        "minbatterystateofcharge_electrolyzer_turnon": {
            "value": minbatterystateofcharge_electrolyzer_turnon,
            "unit": minbatterystateofcharge_electrolyzer_turnonUnit,
        },

        "minbatterystateofcharge_let_electrolyzer_staysturnedon": {
            "value": minbatterystateofcharge_let_electrolyzer_staysturnedon,
            "unit": minbatterystateofcharge_let_electrolyzer_staysturnedonUnit,
        },

        "maxbatterystateofcharge_fuelcell_turnon": {
            "value": maxbatterystateofcharge_fuelcell_turnon,
            "unit": maxbatterystateofcharge_fuelcell_turnonUnit,
        },

        "maxbatterystateofcharge_let_fuelcell_staysturnedon": {
            "value": maxbatterystateofcharge_let_fuelcell_staysturnedon,
            "unit": maxbatterystateofcharge_let_fuelcell_staysturnedonUnit,
        },


        "Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage": {
            "value": Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage,
            "unit": Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentageUnit,
        },

        "Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage": {
            "value": Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage,
            "unit": Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentageUnit,
        },

        "p_el_percentage_standby_electrolyzer": {
            "value": p_el_percentage_standby_electrolyzer,
            "unit": p_el_percentage_standby_electrolyzerUnit,
        },

        "p_el_percentage_standby_fuelcell": {
            "value": p_el_percentage_standby_fuelcell,
            "unit": p_el_percentage_standby_fuelcellUnit,
        },


    }
        

    return input_variablen