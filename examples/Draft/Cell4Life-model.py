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
    battery_capacity: Optional[float] = 10   # in kWh
    print(NGFm2, " m2")
    

    # Set the simulation parameters for the simulation
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only(
            year=2021, seconds_per_timestep=3600
        )
     
    # testmy_sim = copy.deepcopy(my_sim)
    my_sim.set_simulation_parameters(my_simulation_parameters)
   
    # Postprocessing Options
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_LINE)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.MAKE_NETWORK_CHARTS)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.EXPORT_TO_CSV)
    my_simulation_parameters.post_processing_options.append(postprocessingoptions.PostProcessingOptions.PLOT_CARPET)
    
    # Integration of csvloader 
    # PV Output in kW

    
    #my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000, "OutputPVinW")
    my_photovoltaic_systemConfig = CSVLoaderConfig("PV", "PVComponent", "OneDayTestDataSet.csv", 6, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Photovoltaik", ";", ",",1000, "OutputPVinW")

    my_photovoltaic_system = CSVLoader(my_photovoltaic_systemConfig, my_simulation_parameters)
  
    # Electricity consumption in W per m2 NGF


    #my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "CurrentConsumptionper total", "Simulationsdaten_Pilzgasse_230705-Input-HiSim.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,NGFm2, "CurrentConspumtioninWperm2NGF")
    my_electricityconsumptionConfig = CSVLoaderConfig("Current total", "CurrentConsumptionper total", "OneDayTestDataSet.csv", 1, loadtypes.LoadTypes.ELECTRICITY, loadtypes.Units.WATT, "Strom", ";", "," ,NGFm2, "CurrentConspumtioninWperm2NGF")
    my_electricityconsumption = CSVLoader(my_electricityconsumptionConfig, my_simulation_parameters)
    # consumption.append(my_electricityconsumption)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.get_electricity_meter_default_config(),
    )

       
    # Build Battery
    my_advanced_battery_config = (
        advanced_battery_bslib.BatteryConfig.get_default_config()
    )
    print(my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour)

    my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour = battery_capacity
    print(my_advanced_battery_config.custom_battery_capacity_generic_in_kilowatt_hour)
    
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters,
        config=my_advanced_battery_config,
    )

    # Build EMS
    my_electricity_controller_config = (
        controller_l2_energy_management_system.EMSConfig.get_default_config_ems()
    )
    my_electricity_controller = (
        controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
            my_simulation_parameters=my_simulation_parameters,
            config=my_electricity_controller_config,
        )
    )

    #------------
    #Connect Component Inputs with Outputs
    '''
    Basis für diesen Code: household_hplib_hws_hds_pv_battery_ems_config.py
    '''

   
    #Electricity Grid
    my_electricity_meter.add_component_input_and_connect(
        source_component_class=my_photovoltaic_system,
        source_component_output=my_photovoltaic_system.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[
            loadtypes.ComponentType.PV,
            loadtypes.InandOutputType.ELECTRICITY_PRODUCTION,
        ],
        source_weight=999,
    )
    print(my_photovoltaic_system.Output1)
    my_electricity_meter.add_component_input_and_connect(
        source_component_class=my_electricityconsumption,
        source_component_output=my_electricityconsumption.Output1,
        source_load_type=loadtypes.LoadTypes.ELECTRICITY,
        source_unit=loadtypes.Units.WATT,
        source_tags=[loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=999,
    )

    #***Bis hier her Standard

    #Connect EMS
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
        source_weight=2,
    )

    electricity_to_or_from_battery_target = (
        my_electricity_controller.add_component_output(
            source_output_name=loadtypes.InandOutputType.ELECTRICITY_TARGET,
            source_tags=[
                loadtypes.ComponentType.BATTERY,
                loadtypes.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=2,
            source_load_type=loadtypes.LoadTypes.ELECTRICITY,
            source_unit=loadtypes.Units.WATT,
            output_description="Target electricity for Battery Control. ",
        )
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Connect Battery
    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=electricity_to_or_from_battery_target,
    )
    

    # Add Components to Simulation Parameters

    my_sim.add_component(my_photovoltaic_system)
    my_sim.add_component(my_electricityconsumption)
 
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_electricity_controller)

    print("Cell4Life Model d1")
