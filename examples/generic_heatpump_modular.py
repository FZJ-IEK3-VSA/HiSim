from typing import Optional, List, Union

import hisim.loadtypes as lt

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import controller_l3_predictive
from hisim.components import generic_smart_device_2
from hisim.components import building
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heatpump_modular
from hisim.components import controller_l3_generic_heatpump_modular
from hisim.components import generic_dhw_boiler
from hisim.components import generic_oil_heater
from hisim.components import generic_district_heating
from hisim.components import sumbuilder
from hisim import utils

import os

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def append_to_electricity_load_profiles( my_sim, operation_counter : int,
                                         electricity_load_profiles : List[ Union[ sumbuilder.ElectricityGrid,
                                                                                  loadprofilegenerator_connector.Occupancy ] ], elem_to_append : sumbuilder.ElectricityGrid ):
    electricity_load_profiles = electricity_load_profiles + [ elem_to_append ]
    my_sim.add_component( electricity_load_profiles[ operation_counter ] )
    operation_counter += 1
    return my_sim, operation_counter, electricity_load_profiles

def generic_heatpump_modular_explicit( my_sim, my_simulation_parameters: Optional[SimulationParameters] = None ):
    """
    This setup function emulates an household including
    the basic components "building", "occupancy" and "weather". Here it can be freely chosen if a PV system or a boiler are included or not.
    The heating system can be either a heat pump, an Oilheater or Districtheating

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    ##### System Parameters #####

    # Set simulation parameters
    year = 2018
    seconds_per_timestep = 60 * 15
    
    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23

    # Set weather
    location = "Aachen"
    
    # Set occupancy
    occupancy_profile = "CH01"
    
    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options( year = year,
                                                                               seconds_per_timestep = seconds_per_timestep )
    my_simulation_parameters.reset_system_config( predictive = True, pv_included = True, smart_devices_included = False, boiler_included = None, heating_device_included = 'heat_pump' )    
    my_sim.SimulationParameters = my_simulation_parameters
    
    #get system configuration
    predictive = my_simulation_parameters.system_config.predictive #True or False
    pv_included = my_simulation_parameters.system_config.pv_included #True or False
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included #True or False
    boiler_included = my_simulation_parameters.system_config.boiler_included #Electricity, Hydrogen or False
    heating_device_included = my_simulation_parameters.system_config.heating_device_included 
    
    # Set photovoltaic system
    if pv_included == True:
        time = 2019
        power = 10E3
        load_module_data = False
        module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
        integrateInverter = True
        inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    
    #set boiler
    if boiler_included == 'electricity':
        definition = '0815-boiler'
        smart = 1
    elif boiler_included == 'hydrogen':
        definition = 'hydrogen-boiler'
        smart = 0
    elif boiler_included:
        raise NameError( 'Boiler definition', boiler_included, 'not known. Choose electricity, hydrogen, or False.' )

    #Set heating system
    if heating_device_included == 'heat_pump':
        # Set heat pump controller
        T_min_heating = 19.0
        T_max_heating = 23.0
        T_min_cooling = 23.0
        T_max_cooling = 26.0
        T_tolerance = 1.0
        min_operation_time = 3600
        min_idle_time = 2700
        heating_season_begin = 240
        heating_season_end = 150
        # Set heat pump
        hp_manufacturer = "Viessmann Werke GmbH & Co KG"
        hp_name = "Vitocal 300-A AWO-AC 301.B07"    
    elif heating_device_included in [ 'district_heating', 'oil_heater' ]:
        efficiency = 0.85
        T_min = 20.0
        T_max = 21.0
        P_on = 5000
        on_time = 2700
        off_time = 1800
        heating_season_begin = 240
        heating_season_end = 150
    
    elif heating_device_included:
        raise NameError( 'Heating Device definition', heating_device_included, 'not known. Choose heat_pump, oil_heater, district_heating, or False.' )

    ##### Build Components #####
    
    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy( profile_name=occupancy_profile, my_simulation_parameters = my_simulation_parameters )
    my_sim.add_component( my_occupancy )
    
    # Add price signal
    if predictive == True:
        my_price_signal = generic_price_signal.PriceSignal( my_simulation_parameters = my_simulation_parameters )
        my_sim.add_component( my_price_signal )
    
    #initialize list of components representing the actual load profile and operation counter
    operation_counter = 0
    electricity_load_profiles : List[ Union[ sumbuilder.ElectricityGrid, loadprofilegenerator_connector.Occupancy ] ] = [ my_occupancy ]
    operation_counter = 1


    # Build Weather
    my_weather = weather.Weather( location=location, my_simulation_parameters = my_simulation_parameters, 
                                  my_simulation_repository = my_sim.simulation_repository )
    my_sim.add_component( my_weather )
    
    # Build building
    my_building = building.Building( building_code = building_code,
                                     bClass = building_class,
                                     initial_temperature = initial_temperature,
                                     my_simulation_parameters = my_simulation_parameters )
    my_building.connect_only_predefined_connections( my_weather, my_occupancy )   
    my_sim.add_component( my_building )

    if pv_included:
        my_photovoltaic_system = generic_pv_system.PVSystem( my_simulation_parameters = my_simulation_parameters,
                                               my_simulation_repository = my_sim.simulation_repository,
                                               time = time,
                                               location = location,
                                               power = power,
                                               load_module_data = load_module_data,
                                               module_name = module_name,
                                               integrateInverter = integrateInverter,
                                               inverter_name = inverter_name )
        my_photovoltaic_system.connect_only_predefined_connections( my_weather )
        my_sim.add_component( my_photovoltaic_system )
        my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
                my_sim = my_sim,
                operation_counter = operation_counter,
                electricity_load_profiles = electricity_load_profiles, 
                elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
                                                              grid = [ electricity_load_profiles[ operation_counter - 1 ], "Subtract", my_photovoltaic_system ], 
                                                              my_simulation_parameters = my_simulation_parameters )
                )

    if smart_devices_included:
        pass
        # my_smart_device = generic_smart_device_2.SmartDevice( my_simulation_parameters = my_simulation_parameters )
        # my_sim.add_component( my_smart_device )
        # my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
        #         my_sim = my_sim,
        #         operation_counter = operation_counter,
        #         electricity_load_profiles = electricity_load_profiles, 
        #         elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
        #                                                       grid = [ electricity_load_profiles[ operation_counter - 1 ], "Sum", my_smart_device ], 
        #                                                       my_simulation_parameters = my_simulation_parameters )
        #         )
    
    if boiler_included:  
        pass
        # my_boiler = generic_dhw_boiler.Boiler( definition = definition, fuel = boiler_included, my_simulation_parameters = my_simulation_parameters )
        # my_boiler.connect_only_predefined_connections( my_occupancy )
        # my_sim.add_component( my_boiler )
        
        # my_boiler_controller = generic_dhw_boiler.BoilerController( my_simulation_parameters = my_simulation_parameters )
        # my_boiler_controller.connect_only_predefined_connections( my_boiler )
        # my_sim.add_component( my_boiler_controller )

        # my_boiler.connect_only_predefined_connections( my_boiler_controller )
        
        # if boiler_included == 'electricity':
        #     my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
        #             my_sim = my_sim,
        #             operation_counter = operation_counter,
        #             electricity_load_profiles = electricity_load_profiles, 
        #             elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
        #                                                           grid = [ electricity_load_profiles[ operation_counter - 1 ], "Sum", my_boiler ], 
        #                                                           my_simulation_parameters = my_simulation_parameters )
        #             )
            
    if heating_device_included == 'heat_pump':
        
        my_heating_controller_l2 = controller_l2_generic_heatpump_modular.L2_Controller(    my_simulation_parameters = my_simulation_parameters,
                                                                                            T_min_heating = T_min_heating,
                                                                                            T_max_heating = T_max_heating,
                                                                                            T_min_cooling = T_min_cooling,
                                                                                            T_max_cooling = T_max_cooling,
                                                                                            T_tolerance = T_tolerance,
                                                                                            heating_season_begin = heating_season_begin,
                                                                                            heating_season_end = heating_season_end )
        my_heating_controller_l2.connect_only_predefined_connections( my_building )
        my_sim.add_component( my_heating_controller_l2 )
        
        my_heating_controller_l1 = controller_l1_generic_runtime.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                                min_operation_time = min_operation_time,
                                                                                min_idle_time = min_idle_time )
        my_heating_controller_l1.connect_only_predefined_connections( my_heating_controller_l2 )
        my_sim.add_component( my_heating_controller_l1 )
        my_heating = generic_heat_pump_modular.HeatPump( manufacturer = hp_manufacturer,
                                                         name = hp_name,
                                                         heating_season_begin = heating_season_begin,
                                                         heating_season_end = heating_season_end,
                                                         my_simulation_parameters = my_simulation_parameters )
        my_heating.connect_only_predefined_connections( my_weather ) 
        my_heating.connect_only_predefined_connections( my_heating_controller_l1 )
        my_sim.add_component( my_heating )
        if predictive == True:
            my_heating_controller_l3 = controller_l3_generic_heatpump_modular.L3_Controller( my_simulation_parameters = my_simulation_parameters )
            
            l3_HeatPumpSignal = my_heating_controller_l3.add_component_output( source_output_name = lt.InandOutputType.ControlSignal,
                                                                               source_tags = [ lt.ComponentType.HeatPump ],
                                                                               source_weight = 1,
                                                                               source_load_type = lt.LoadTypes.OnOff,
                                                                               source_unit = lt.Units.binary )
            
            my_heating_controller_l2.connect_dynamic_input( input_fieldname = controller_l2_generic_heatpump_modular.L2_Controller.l3_DeviceSignal,
                                                            src_object = l3_HeatPumpSignal )
            
            my_heating_controller_l3.add_component_input_and_connect( source_component_class = my_heating_controller_l1,
                                                                      source_component_output = my_heating_controller_l1.l1_DeviceSignal,
                                                                      source_load_type= lt.LoadTypes.OnOff,
                                                                      source_unit= lt.Units.binary,
                                                                      source_tags = [ lt.ComponentType.HeatPump, lt.InandOutputType.ControlSignal ],
                                                                      source_weight = 1 )
            
            my_sim.add_component( my_heating_controller_l3 )
            
        my_building.connect_input( my_building.ThermalEnergyDelivered,
                                    my_heating.ComponentName,
                                    my_heating.ThermalEnergyDelivered )
        
        #construct new baseload
        my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
                my_sim = my_sim,
                operation_counter = operation_counter,
                electricity_load_profiles = electricity_load_profiles, 
                elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
                                                             grid = [ electricity_load_profiles[ operation_counter - 1 ], "Sum", my_heating ], 
                                                             my_simulation_parameters = my_simulation_parameters ) ) 
        
        # if smart_devices_included:
        #     my_smart_device.connect_only_predefined_connections( my_predictive_controller )
        #     my_predictive_controller.connect_only_predefined_connections( my_smart_device )
        # if boiler_included == 'electricity':
        #     my_boiler_controller.connect_only_predefined_connections( my_predictive_controller )
        #     my_predictive_controller.connect_only_predefined_connections( my_boiler_controller )
                