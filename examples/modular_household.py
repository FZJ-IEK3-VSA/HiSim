from typing import Optional, List, Union

from hisim.simulator import SimulationParameters
from hisim.components import occupancy
from hisim.components import price_signal
from hisim.components import weather
from hisim.components import pvs
from hisim.components import predictive_controller
from hisim.components import smart_device
from hisim.components import building
from hisim.components import heat_pump
from hisim.components import simple_bucket_boiler
from hisim.components import oil_heater
from hisim.components import district_heating
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

def append_to_electricity_load_profiles( my_sim, operation_counter : int, electricity_load_profiles : List[ Union[ sumbuilder.ElectricityGrid, occupancy.Occupancy ] ], elem_to_append : sumbuilder.ElectricityGrid ):
    electricity_load_profiles = electricity_load_profiles + [ elem_to_append ]
    my_sim.add_component( electricity_load_profiles[ operation_counter ] )
    operation_counter += 1
    return my_sim, operation_counter, electricity_load_profiles

def modular_household_explicit( my_sim, my_simulation_parameters: Optional[SimulationParameters] = None ):
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

    ##### delete all files in cache:
    dir = '..//hisim//inputs//cache'
    for file in os.listdir( dir ):
        os.remove( os.path.join( dir, file ) )

    ##### System Parameters #####

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60 * 15
    #seconds_per_timestep = 60
    
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
    my_simulation_parameters.reset_system_config( predictive = True, smart_devices_included = True )    
    
    my_sim.SimulationParameters = my_simulation_parameters
    
    #get system configuration
    predictive = my_simulation_parameters.system_config.predictive #True or False
    pv_included = my_simulation_parameters.system_config.pv_included #True or False
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included #True or False
    boiler_included = my_simulation_parameters.system_config.boiler_included #Electricity, Hydrogen or False
    heating_device_included = my_simulation_parameters.system_config.heating_device_included 
    
    # Set photovoltaic system
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
        t_air_heating = 16.0
        t_air_cooling = 24.0
        offset = 0.5
        hp_mode = 2
        # Set heat pump
        hp_manufacturer = "Viessmann Werke GmbH & Co KG"
        hp_name = "Vitocal 300-A AWO-AC 301.B07"
        hp_min_operation_time = 60
        hp_min_idle_time = 15      
    elif heating_device_included == 'oil_heater':
        # Set Oil heater controller
        t_air_heating = 21.0
        offset = 3.0
        # Set Oil Heater
        max_power = 5000
        min_on_time = 60
        min_off_time = 15
    elif heating_device_included == 'district_heating':
        t_air_heating = 21.0
        tol = 1e-2 #tolerance of set point -> considered in control
        max_power = 15000
        min_power = 1000
        efficiency = 0.85
    
    elif heating_device_included:
        raise NameError( 'Heating Device definition', heating_device_included, 'not known. Choose heat_pump, oil_heater, district_heating, or False.' )

    ##### Build Components #####
    
    # Build occupancy
    my_occupancy = occupancy.Occupancy( profile = occupancy_profile, my_simulation_parameters = my_simulation_parameters )
    my_sim.add_component( my_occupancy )
    
    # Add price signal
    if predictive == True:
        my_price_signal = price_signal.PriceSignal( my_simulation_parameters = my_simulation_parameters )
        my_sim.add_component( my_price_signal )
    
    #initialize list of components representing the actual load profile and operation counter
    operation_counter = 0
    electricity_load_profiles : List[ Union[ sumbuilder.ElectricityGrid, occupancy.Occupancy ] ] = [ my_occupancy ]
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
        my_photovoltaic_system = pvs.PVSystem( my_simulation_parameters = my_simulation_parameters,
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
        my_smart_device = smart_device.SmartDevice( my_simulation_parameters = my_simulation_parameters )
        my_sim.add_component( my_smart_device )
        if predictive == True:
            my_predictive_controller = predictive_controller.PredictiveController( my_simulation_parameters = my_simulation_parameters )
            my_sim.add_component( my_predictive_controller )  
            my_smart_device.connect_only_predefined_connections( my_predictive_controller )
            my_predictive_controller.connect_only_predefined_connections( my_smart_device )
        my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
                my_sim = my_sim,
                operation_counter = operation_counter,
                electricity_load_profiles = electricity_load_profiles, 
                elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
                                                              grid = [ electricity_load_profiles[ operation_counter - 1 ], "Sum", my_smart_device ], 
                                                              my_simulation_parameters = my_simulation_parameters )
                )
    
    if boiler_included:  
        my_boiler = simple_bucket_boiler.Boiler( definition = definition, fuel = boiler_included, my_simulation_parameters = my_simulation_parameters )
        # my_boiler.connect_input( my_boiler.WaterConsumption,
        #                          my_occupancy.ComponentName, 
        #                          my_occupancy.WaterConsumption )
        my_boiler.connect_only_predefined_connections( my_occupancy )
        my_sim.add_component( my_boiler )
        
        my_boiler_controller = simple_bucket_boiler.BoilerController( smart = smart, my_simulation_parameters = my_simulation_parameters )
        # my_boiler_controller.connect_input( my_boiler_controller.StorageTemperature,
        #                                     my_boiler.ComponentName,
        #                                     my_boiler.StorageTemperature )
        my_boiler_controller.connect_only_predefined_connections( my_boiler )
        my_sim.add_component( my_boiler_controller )
        
        # my_boiler.connect_input( my_boiler.State,
        #                          my_boiler_controller.ComponentName,
        #                          my_boiler_controller.State )
        my_boiler.connect_only_predefined_connections( my_boiler_controller )

        if boiler_included == 'electricity':
            my_boiler_controller.connect_input( my_boiler_controller.ElectricityInput,
                                        electricity_load_profiles[ - 1 ].ComponentName,
                                        electricity_load_profiles[ - 1 ].ElectricityOutput )
            
            my_sim, operation_counter, electricity_load_profiles = append_to_electricity_load_profiles( 
                my_sim = my_sim,
                operation_counter = operation_counter,
                electricity_load_profiles = electricity_load_profiles, 
                elem_to_append = sumbuilder.ElectricityGrid( name = "BaseLoad" + str( operation_counter ),
                                                             grid = [ electricity_load_profiles[ operation_counter - 1 ], "Sum", my_boiler ], 
                                                             my_simulation_parameters = my_simulation_parameters ) 
                )
            
    if heating_device_included:
        my_heating : Union[ heat_pump.HeatPump, oil_heater.OilHeater, district_heating.DistrictHeating ]
        my_heating_controller : Union[ heat_pump.HeatPumpController, oil_heater.OilHeaterController, district_heating.DistrictHeatingController ]
        #initialize and connect controller
        if heating_device_included == 'heat_pump':
            my_heating_controller = heat_pump.HeatPumpController( t_air_heating = t_air_heating,
                                                                  t_air_cooling = t_air_cooling,
                                                                  offset = offset,
                                                                  mode = hp_mode,
                                                                  my_simulation_parameters = my_simulation_parameters )
            hc : heat_pump.HeatPumpController = my_heating_controller # type: ignore
            hc.connect_input( hc.ElectricityInput,
                                                 electricity_load_profiles[ operation_counter - 1 ].ComponentName,
                                                 electricity_load_profiles[ operation_counter - 1 ].ElectricityOutput )
        elif heating_device_included == 'oil_heater':
            my_heating_controller = oil_heater.OilHeaterController( t_air_heating = t_air_heating,
                                                                    offset = offset, 
                                                                    my_simulation_parameters = my_simulation_parameters ) 
            my_heating_controller.connect_only_predefined_connections( my_weather )
        elif heating_device_included == 'district_heating':
            my_heating_controller = district_heating.DistrictHeatingController( max_power = max_power,
                                                                                min_power = min_power,
                                                                                t_air_heating = t_air_heating,
                                                                                tol = tol,
                                                                                my_simulation_parameters = my_simulation_parameters )
        my_heating_controller.connect_only_predefined_connections( my_building )
        my_sim.add_component( my_heating_controller)
        
        #initialize and connect heating device
        if heating_device_included == 'heat_pump':
            my_heating = heat_pump.HeatPump( manufacturer = hp_manufacturer,
                                             name = hp_name,
                                             min_operation_time = hp_min_operation_time,
                                             min_idle_time = hp_min_idle_time,
                                             my_simulation_parameters = my_simulation_parameters )
            my_heating.connect_only_predefined_connections( my_weather )    
        elif heating_device_included == 'oil_heater':
            my_heating = oil_heater.OilHeater( max_power = max_power,
                                               min_off_time = min_off_time,
                                               min_on_time = min_on_time, 
                                               my_simulation_parameters = my_simulation_parameters )      
        elif heating_device_included == 'district_heating':
            my_heating = district_heating.DistrictHeating( max_power = max_power,
                                                           min_power = min_power,
                                                           efficiency = efficiency,
                                                           my_simulation_parameters = my_simulation_parameters )
        my_heating.connect_only_predefined_connections( my_heating_controller ) 
        my_sim.add_component( my_heating )

        my_building.connect_input( my_building.ThermalEnergyDelivered,
                                   my_heating.ComponentName,
                                   my_heating.ThermalEnergyDelivered )