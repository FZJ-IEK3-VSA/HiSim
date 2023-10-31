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

from hisim import log
from hisim.simulator import Simulator
from hisim.simulationparameters import SimulationParameters
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.components.example_transformer import (
    ExampleTransformer,
    ExampleTransformerConfig,
)
from hisim.components.sumbuilder import SumBuilderForTwoInputs, SumBuilderConfig
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.components import electricity_meter
from hisim.components import advanced_battery_bslib
from hisim.components import generic_pv_system
from hisim.components import (controller_l1_electrolyzer, generic_electrolyzer, generic_hydrogen_storage)
from hisim.components import (controller_l1_chp, generic_CHP) 
from hisim.components import controller_l2_energy_management_system
from hisim.modular_household import component_connections
from hisim import loadtypes
from hisim import postprocessingoptions


# Christof:
def Cell4Life(
    my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters]
) -> None:
    """Cell4Life-Simulation Model

    
    Integration of csvload 
    Loading of Project Data

    """
    log.information("Starting Cell4Life-Simulation Model: ")
    ###------Config Data 
    
    #NettoGesamtfläche (total area in squaremeters of building(s))
    NGFm2 = 26804.8
    NGFm2 = 1
    #: capacity of the considered battery in kWh
    battery_capacity: Optional[float] = 0.00001   # in kWh
    init_source_weight_battery = 1
    init_source_weight_electrolyzer = 2
    init_source_weight_hydrogenstorage = 999 #init_source_weight_electrolyzer
    init_source_weight_chp = 3
    p_el_elektrolyzer = 1000 #Maximum Electrical Operating Power in Watt // Minimum at half of input value
    h2_storage_capacity_max = 1  #Maximum of hydrogen storage in kg
    min_operation_time_in_seconds_chp = 0
    h2_soc_threshold_chp = 1 # Minimum state of charge to start operating the fuel cell in %
    h2_soc_threshold_electrolyzer = 99 # Maximal allowed content of hydrogen storage for turning the electrolyzer on in %

    electrolyzer_power = p_el_elektrolyzer #Initiale ANNAHMEN ZUM TESTEN in Watt
    fuel_cell_power  = 1000 #Initiale ANNAHMEN ZUM TESTEN in Watt
    electricity_threshold = 0 #Minium required power to activate fuel cell 

    print(NGFm2, " m2")
    

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only(
            year=2021, seconds_per_timestep=3600
        )
     
    # testmy_sim = copy.deepcopy(my_sim)
    my_sim.set_simulation_parameters(my_simulation_parameters)
   
    # Postprocessing Options****
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_LINE)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_CARPET)
    

    
            #******************************************************************
            #***Loading of Input Data****
            #******************************************************************
    
    #Loading Electricity consumption in W per m2 NGF****
    #my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "CurrentConsumptionper total", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,NGFm2, "CurrentConspumtioninWperm2NGF")
    my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "CurrentConsumptionper total", "OneDayTestDataSet.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,NGFm2, "CurrentConspumtioninWperm2NGF")
    my_electricityconsumption = CSVLoader(my_electricityconsumptionConfig, my_simulation_parameters)
       
    #******************************************************************   
    #Loading Photovoltaic System  (PV Output in kW)
    #my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000, "OutputPVinW")
    my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "OneDayTestDataSet.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000, "OutputPVinW")
    my_photovoltaic_system = CSVLoader(my_photovoltaic_systemConfig, my_simulation_parameters)
  

            #******************************************************************
            # Building Components of Modell
            #****************************************************************** 
    
    #******************************************************************
    #Build EMS****
    #First Controller
    my_electricity_controller_config = (
        controller_l2_energy_management_system.EMSConfig.get_default_config_ems()
    )
    my_electricity_controller = (
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters,
            config=my_electricity_controller_config,
        )
    )

    #******************************************************************   
    #Build Battery****
    my_advanced_battery_config = (advanced_battery_bslib.BatteryConfig.get_default_config())
    my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour = battery_capacity
    my_advanced_battery_config.source_weight = init_source_weight_battery
    my_advanced_battery = advanced_battery_bslib.Battery(my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config)

    #******************************************************************
    #Build Electrolyzer****
    #First Controller
    electrolyzer_controller_config = controller_l1_electrolyzer.L1ElectrolyzerConfig.get_default_config()
    electrolyzer_controller_config.source_weight = init_source_weight_electrolyzer
    electrolyzer_controller_config.min_operation_time_in_seconds = 0
    electrolyzer_controller_config.min_idle_time_in_seconds=0
    electrolyzer_controller_config.P_min_electrolyzer = 0
    electrolyzer_controller_config.h2_soc_threshold = h2_soc_threshold_electrolyzer
       
    my_electrolyzer_controller = (
        controller_l1_electrolyzer.L1GenericElectrolyzerController(
        my_simulation_parameters=my_simulation_parameters,
        config=electrolyzer_controller_config,
        )
    )

    electrolyzer_config = generic_electrolyzer.GenericElectrolyzerConfig.get_default_config(p_el = p_el_elektrolyzer)
    electrolyzer_config.source_weight = init_source_weight_electrolyzer
    #*Electrolyzer*s
    my_electrolyzer = generic_electrolyzer.GenericElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=electrolyzer_config
    )
    
    #******************************************************************
    #Build Hydrogen Storage****
    #First Controller
    h2_storage_config = generic_hydrogen_storage.GenericHydrogenStorageConfig.get_default_config(
        max_charging_rate=electrolyzer_power / (3.6e3 * 3.939e4),
        max_discharging_rate=fuel_cell_power / (3.6e3 * 3.939e4),
        source_weight=init_source_weight_hydrogenstorage,
    )
    
    h2_storage_config.max_capacity = h2_storage_capacity_max
    my_h2storage = generic_hydrogen_storage.GenericHydrogenStorage(
        my_simulation_parameters=my_simulation_parameters, config=h2_storage_config
    )

    #******************************************************************
    #Build Fuel Cell****
    #First Controller
    chp_controller_config = controller_l1_chp.L1CHPControllerConfig.get_default_config_fuel_cell()
    #Build Chp Controller
    chp_controller_config.source_weight = init_source_weight_chp
    chp_controller_config.thermalpowerneeded = False
    chp_controller_config.electricity_threshold = electricity_threshold
    chp_controller_config.min_operation_time_in_seconds = min_operation_time_in_seconds_chp
    chp_controller_config.h2_soc_threshold = h2_soc_threshold_chp
    my_chp_controller = controller_l1_chp.L1CHPController(
        my_simulation_parameters=my_simulation_parameters, config=chp_controller_config
    )

    chp_config = generic_CHP.CHPConfig.get_default_config_fuelcell(thermal_power=fuel_cell_power*(0.43 / 0.48))
    chp_config.source_weight = init_source_weight_chp
    chp_config.thermalpowerneeded = False
    my_chp = generic_CHP.SimpleCHP(
        my_simulation_parameters=my_simulation_parameters, config=chp_config, 
    )

            
            #******************************************************************
            #****Connect Component Inputs with Outputs****
            #******************************************************************

    #****Connect EMS****
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_electricityconsumption,
        source_component_output=my_electricityconsumption.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_photovoltaic_system,
        source_component_output=my_photovoltaic_system.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=999,
    )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_advanced_battery,
        source_component_output=my_advanced_battery.AcBatteryPower,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.ComponentType.BATTERY, loadtypes.InandOutputType.ELECTRICITY_REAL],
        source_weight=my_advanced_battery.source_weight,
    )

    # electricity controller of fuel cell
    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_electrolyzer,
        source_component_output=my_electrolyzer.ElectricityOutput,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.ELECTROLYZER,
            loadtypes.InandOutputType.ELECTRICITY_REAL,
        ],
        source_weight=my_electrolyzer.config.source_weight,
    )

    my_electricity_controller.add_component_input_and_connect(
        source_component_class=my_chp,
        source_component_output="ElectricityOutput",
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.ComponentType.CHP, loadtypes.InandOutputType.ELECTRICITY_PRODUCTION],
        source_weight=my_chp.config.source_weight,
    )

    #Electricity to battery or from battery
    electricity_to_or_from_battery_target = (
        my_electricity_controller.add_component_output(
            source_output_name=loadtypes.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                loadtypes.ComponentType.BATTERY,
                loadtypes.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=my_advanced_battery.source_weight,
            source_load_type=loadtypes.LoadTypes.ELECTRICITY,
            source_unit=loadtypes.Units.WATT,
            output_description="Target electricity for Battery Control. ",
        )
    )

       

    #Electricity to electrolyzer
    electricity_to_electrolyzer_target = my_electricity_controller.add_component_output(
        source_output_name=loadtypes.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
            loadtypes.ComponentType.ELECTROLYZER,
            loadtypes.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_weight=my_electrolyzer.config.source_weight,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        output_description="Target electricity for electrolyzer. ",
    )

    ems_target_electricity = my_electricity_controller.add_component_output(
        source_output_name=loadtypes.InandOutputType.ELECTRICITY_TARGET,
        source_tags=[
                loadtypes.ComponentType.CHP,
                loadtypes.InandOutputType.ELECTRICITY_TARGET,
            ],
        source_weight=my_chp.config.source_weight,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        output_description="Target electricity for CHP. ",
        )



    #Connect Battery****
    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target,
    )
    #Connect Electrolyzer
    my_electrolyzer_controller.connect_dynamic_input(
        input_fieldname=controller_l1_electrolyzer.L1GenericElectrolyzerController.ElectricityTarget,
        src_object=electricity_to_electrolyzer_target,
    )
    
    #Connect Fuel Cell
    my_chp_controller.connect_dynamic_input(
            input_fieldname=my_chp_controller.ElectricityTarget,
            src_object=ems_target_electricity,
        )

    my_electrolyzer.connect_only_predefined_connections(my_electrolyzer_controller)
    my_h2storage.connect_only_predefined_connections(my_electrolyzer)
    my_h2storage.connect_only_predefined_connections(my_chp)
    my_chp_controller.connect_only_predefined_connections(my_h2storage)
    my_electrolyzer_controller.connect_only_predefined_connections(my_h2storage)
    my_chp.connect_only_predefined_connections(my_chp_controller)


        #******************************************************************
        # Add Components to Simulation Parameters
        #******************************************************************
    
    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_electricityconsumption)
    my_sim.add_component(my_electrolyzer_controller)
    my_sim.add_component(my_electrolyzer)
    my_sim.add_component(my_h2storage)
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_chp_controller)
    my_sim.add_component(my_chp)
    my_sim.add_component(my_electricity_controller)
    print("Cell4Life Model d2")