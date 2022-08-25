from typing import Optional, List, Union

import hisim.loadtypes as lt
import hisim.log
from hisim.simulationparameters import SystemConfig
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import controller_l3_smart_devices
from hisim.components import controller_l2_energy_management_system
from hisim.components import generic_smart_device
from hisim.components import advanced_battery_bslib
from hisim.components import generic_CHP
from hisim.components import controller_l2_generic_chp
from hisim.components import generic_electrolyzer
from hisim.components import generic_hydrogen_storage
from hisim import utils
from component_connections import *

import os
import csv


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

    ##### System Parameters #####

    # Set simulation parameters
    year = 2018
    seconds_per_timestep = 60 * 15
    
    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    heating_reference_temperature=-14

    # Set weather
    location = "Aachen"
    
    # Set occupancy
    occupancy_profile = "CH01"

    # Set PV-System
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    pvname = 'PVSystem'
    azimuth  = 180
    tilt  = 30

    # path of system config file
    system_config_filename = "system_config.json"

    #initialize components involved in production and consumption
    production = [ ]
    consumption = [ ]
    heater = [ ]
    
    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only( year = year,
                                                                      seconds_per_timestep = seconds_per_timestep )
        my_simulation_parameters.enable_all_options( )

    # try to read the system config from file
    try:
        with open(system_config_filename) as system_config_file:
            system_config = SystemConfig.from_json(system_config_file.read())  # type: ignore
        hisim.log.information(f"Read system config from {system_config_filename}")
        my_simulation_parameters.system_config = system_config
    except:
        # file does not exist or could not be parsed - use default config
        my_simulation_parameters.reset_system_config( predictive=True, prediction_horizon=24 * 3600, pv_included=True, smart_devices_included=True,
                                                      water_heating_system_installed='HeatPump', heating_system_installed='HeatPump', buffer_volume=500, battery_included=True, chp_included=True )  
    my_sim.set_simulation_parameters(my_simulation_parameters)
    
    #get system configuration
    predictive = my_simulation_parameters.system_config.predictive #True or False
    pv_included = my_simulation_parameters.system_config.pv_included #True or False
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included #True or False
    water_heating_system_installed = my_simulation_parameters.system_config.water_heating_system_installed #Electricity, Hydrogen or False
    heating_system_installed = my_simulation_parameters.system_config.heating_system_installed
    buffer_volume = my_simulation_parameters.system_config.buffer_volume
    battery_included = my_simulation_parameters.system_config.battery_included
    chp_included = my_simulation_parameters.system_config.chp_included

    """BASICS"""  
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(profile_name=occupancy_profile)
    my_occupancy = loadprofilegenerator_connector.Occupancy( config=my_occupancy_config, my_simulation_parameters = my_simulation_parameters )
    my_sim.add_component( my_occupancy )
    consumption.append( my_occupancy )

    # Build Weather
    my_weather_config = weather.WeatherConfig(location="Aachen")
    my_weather = weather.Weather( config=my_weather_config, my_simulation_parameters = my_simulation_parameters,
                                  my_simulation_repository = my_sim.simulation_repository )
    my_sim.add_component( my_weather )
    
    # Build building
    my_building_config=building.BuildingConfig( building_code = building_code,
                                                bClass = building_class,
                                                initial_temperature = initial_temperature,
                                                heating_reference_temperature = heating_reference_temperature )
    my_building = building.Building( config=my_building_config,
                                     my_simulation_parameters = my_simulation_parameters )
    my_building.connect_only_predefined_connections( my_weather, my_occupancy )   
    my_sim.add_component( my_building )
    
    #add price signal
    my_price_signal = generic_price_signal.PriceSignal( my_simulation_parameters = my_simulation_parameters )
    my_sim.add_component( my_price_signal )
    
    """PV"""
    if pv_included:
        my_photovoltaic_system_config_1 = generic_pv_system.PVSystemConfig( time = time,
                                                                            location = location,
                                                                            power = power,
                                                                            load_module_data = load_module_data,
                                                                            module_name = module_name,
                                                                            integrate_inverter = integrateInverter,
                                                                            tilt = tilt,
                                                                            azimuth = azimuth,
                                                                            inverter_name = inverter_name,
                                                                            source_weight = 1,
                                                                            name = pvname)
        my_photovoltaic_system1 = generic_pv_system.PVSystem( my_simulation_parameters = my_simulation_parameters,
                                                              my_simulation_repository = my_sim.simulation_repository,                                        
                                                              config = my_photovoltaic_system_config_1 )
        my_photovoltaic_system1.connect_only_predefined_connections( my_weather )
        my_sim.add_component( my_photovoltaic_system1 )
        production.append( my_photovoltaic_system1 )

        my_photovoltaic_system_config_2 = generic_pv_system.PVSystemConfig( time = time,
                                                                            location = location,
                                                                            power = power,
                                                                            load_module_data = load_module_data,
                                                                            module_name = module_name,
                                                                            integrate_inverter = integrateInverter,
                                                                            tilt = tilt,
                                                                            azimuth = azimuth,
                                                                            inverter_name = inverter_name,
                                                                            source_weight = 2,
                                                                            name = pvname )
        my_photovoltaic_system2 = generic_pv_system.PVSystem( my_simulation_parameters = my_simulation_parameters,
                                                              my_simulation_repository = my_sim.simulation_repository,
                                                              config = my_photovoltaic_system_config_2 )
        my_photovoltaic_system2.connect_only_predefined_connections( my_weather )
        my_sim.add_component( my_photovoltaic_system2 )
        production.append( my_photovoltaic_system2 )

    """SMART DEVICES"""
    if smart_devices_included:
        
        #read in available smart devices
        filepath = utils.HISIMPATH[ "smart_devices" ][ "device_collection" ] 
        count = 1
        device_collection = [ ]
        
        with open( filepath, 'r' ) as f:
            i = 0
            formatreader = csv.reader( f, delimiter = ';' )
            for line in formatreader:
                if i > 1:
                    device_collection.append( line[ 0 ] )
                i += 1
        
        #create all smart devices
        count = 1
        my_smart_devices : List[ generic_smart_device.SmartDevice ] = [ ]
        for device in device_collection:
            my_smart_devices.append( generic_smart_device.SmartDevice( identifier = device,
                                                                source_weight = count,
                                                                my_simulation_parameters = my_simulation_parameters ) )
            my_sim.add_component( my_smart_devices[ count - 1 ] )
            consumption.append( my_smart_devices[ count - 1 ] )
            count += 1
    
    """SURPLUS CONTROLLER"""
    if battery_included or chp_included or heating_system_installed in [ 'HeatPump', 'ElectricHeating' ] or water_heating_system_installed in [ 'HeatPump', 'ElectricHeating' ]:
        my_electricity_controller = controller_l2_energy_management_system.ControllerElectricityGeneric( my_simulation_parameters = my_simulation_parameters )

        my_electricity_controller.add_component_inputs_and_connect(source_component_classes = consumption,
                                                                   outputstring = 'ElectricityOutput',
                                                                   source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                   source_unit = lt.Units.WATT,
                                                                   source_tags = [lt.InandOutputType.CONSUMPTION],
                                                                   source_weight = 999)
        my_electricity_controller.add_component_inputs_and_connect(source_component_classes = production,
                                                                   outputstring = 'ElectricityOutput',
                                                                   source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                   source_unit = lt.Units.WATT,
                                                                   source_tags = [lt.InandOutputType.PRODUCTION],
                                                                   source_weight = 999)
        
    """WATERHEATING""" 
    consumption, count = configure_water_heating(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_occupancy=my_occupancy,
                                           my_electricity_controller=my_electricity_controller, my_weather=my_weather,
                                           water_heating_system_installed=water_heating_system_installed, consumption=consumption, count=count)
    
    """HEATING"""
    if buffer_volume > 80:
        my_heater, my_buffer, consumption, count = configure_heating_with_buffer(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building, my_electricity_controller=my_electricity_controller,
            my_weather=my_weather, heating_system_installed=heating_system_installed, buffer_volume=buffer_volume, consumption=consumption, count=count)
    else:
        my_heater, consumption, count = configure_heating(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
                                                          my_electricity_controller=my_electricity_controller, my_weather=my_weather,
                                                          heating_system_installed=heating_system_installed, consumption=consumption, count=count)
    
    heater.append( my_heater )
    
    """BATTERY"""
    if battery_included:
        my_advanced_battery_config = advanced_battery_bslib.Battery.get_default_config( )
        my_advanced_battery_config.source_weight = count
        count += 1 
        my_advanced_battery = advanced_battery_bslib.Battery( my_simulation_parameters = my_simulation_parameters, config = my_advanced_battery_config )
        
        my_electricity_controller.add_component_input_and_connect(source_component_class = my_advanced_battery,
                                                                  source_component_output = my_advanced_battery.AcBatteryPower,
                                                                  source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                  source_unit = lt.Units.WATT,
                                                                  source_tags = [lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight = my_advanced_battery.source_weight)

        electricity_to_or_from_battery_target = my_electricity_controller.add_component_output(source_output_name = lt.InandOutputType.ELECTRICITY_TARGET,
                                                                                               source_tags = [lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
                                                                                               source_weight = my_advanced_battery.source_weight,
                                                                                               source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                                               source_unit = lt.Units.WATT)
    
        my_advanced_battery.connect_dynamic_input( input_fieldname = advanced_battery_bslib.Battery.LoadingPowerInput,
                                                   src_object = electricity_to_or_from_battery_target )
        my_sim.add_component( my_advanced_battery )
        
    """CHP + H2 STORAGE + ELECTROLYSIS"""
        
    if chp_included:
        #Fuel Cell default configurations
        l2_config = controller_l2_generic_chp.L2_Controller.get_default_config( )
        l2_config.source_weight = count
        l1_config = generic_CHP.L1_Controller.get_default_config( )
        l1_config.source_weight = count
        chp_config = generic_CHP.GCHP.get_default_config( )
        chp_config.source_weight = count
        count += 1
        
        #fuel cell
        my_chp = generic_CHP.GCHP( my_simulation_parameters = my_simulation_parameters,
                                   config = chp_config )
        my_sim.add_component( my_chp )
        
        #heat controller of fuel cell
        my_chp_controller_l2 = controller_l2_generic_chp.L2_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                        config = l2_config )
        my_chp_controller_l2.connect_only_predefined_connections( my_building )
        my_sim.add_component( my_chp_controller_l2 )
        
        #run time controller of fuel cell
        my_chp_controller_l1 = generic_CHP.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                          config = l1_config)
        my_chp_controller_l1.connect_only_predefined_connections( my_chp_controller_l2 )
        my_sim.add_component( my_chp_controller_l1 )
        my_chp.connect_only_predefined_connections( my_chp_controller_l1 )
        
        #electricity controller of fuel cell
        my_electricity_controller.add_component_input_and_connect(source_component_class = my_chp,
                                                                  source_component_output = my_chp.ElectricityOutput,
                                                                  source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                  source_unit = lt.Units.WATT,
                                                                  source_tags = [lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight = my_chp.source_weight)
        electricity_from_fuelcell_target = my_electricity_controller.add_component_output(source_output_name = lt.InandOutputType.ELECTRICITY_TARGET,
                                                                                          source_tags = [lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
                                                                                          source_weight = my_chp.source_weight,
                                                                                          source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                                          source_unit = lt.Units.WATT)
        my_chp_controller_l1.connect_dynamic_input( input_fieldname = generic_CHP.L1_Controller.ElectricityTarget,
                                                    src_object = electricity_from_fuelcell_target )
        heater.append( my_chp )
        
        #electrolyzer default configuration
        l1_config = generic_electrolyzer.L1_Controller.get_default_config( )
        l1_config.source_weight = count
        electrolyzer_config = generic_electrolyzer.Electrolyzer.get_default_config( )
        electrolyzer_config.source_weight = count
        count += 1
        
        #electrolyzer
        my_electrolyzer = generic_electrolyzer.Electrolyzer( my_simulation_parameters = my_simulation_parameters,
                                                             config = electrolyzer_config )
        my_sim.add_component( my_electrolyzer )
        
        #run time controller of electrolyzer
        my_electrolyzer_controller_l1 = generic_electrolyzer.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                            config = l1_config)
        my_sim.add_component( my_electrolyzer_controller_l1 )
        my_electrolyzer.connect_only_predefined_connections( my_electrolyzer_controller_l1 )
        
        #electricity controller of fuel cell
        my_electricity_controller.add_component_input_and_connect(source_component_class = my_electrolyzer,
                                                                  source_component_output = my_electrolyzer.ElectricityOutput,
                                                                  source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                  source_unit = lt.Units.WATT,
                                                                  source_tags = [lt.ComponentType.ELECTROLYZER, lt.InandOutputType.ELECTRICITY_REAL],
                                                                  source_weight = my_electrolyzer.source_weight)
        electricity_to_electrolyzer_target = my_electricity_controller.add_component_output(source_output_name = lt.InandOutputType.ELECTRICITY_TARGET,
                                                                                            source_tags = [lt.ComponentType.ELECTROLYZER, lt.InandOutputType.ELECTRICITY_TARGET],
                                                                                            source_weight = my_electrolyzer.source_weight,
                                                                                            source_load_type = lt.LoadTypes.ELECTRICITY,
                                                                                            source_unit = lt.Units.WATT)
        my_electrolyzer_controller_l1.connect_dynamic_input( input_fieldname = generic_electrolyzer.L1_Controller.l2_ElectricityTarget,
                                                    src_object = electricity_to_electrolyzer_target )
        
        h2storage_config = generic_hydrogen_storage.HydrogenStorage.get_default_config( )
        my_h2storage = generic_hydrogen_storage.HydrogenStorage( my_simulation_parameters = my_simulation_parameters,
                                                                 config = h2storage_config )
        my_h2storage.connect_only_predefined_connections( my_electrolyzer )
        my_h2storage.connect_only_predefined_connections( my_chp )
        my_sim.add_component( my_h2storage )
        
        my_electrolyzer_controller_l1.connect_only_predefined_connections( my_h2storage )
        my_chp_controller_l1.connect_only_predefined_connections( my_h2storage )
        
    if buffer_volume > 80:
        my_buffer.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                   source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                   source_tags=[lt.InandOutputType.HEAT_TO_BUFFER], source_weight=999)
    else:   
        my_building.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                     source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                     source_tags=[lt.InandOutputType.HEAT_TO_BUILDING], source_weight=999)
        
    if battery_included or chp_included or heating_system_installed in [ 'HeatPump', 'ElectricHeating' ] or water_heating_system_installed in [ 'HeatPump', 'ElectricHeating' ]:
        my_sim.add_component( my_electricity_controller )
     
    """PREDICTIVE CONTROLLER FOR SMART DEVICES"""    
    if smart_devices_included and predictive == True:
        
        #construct predictive controller
        my_controller_l3 = controller_l3_smart_devices.L3_Controller( my_simulation_parameters = my_simulation_parameters )
            
        for elem in my_smart_devices:
            l3_ActivationSignal = my_controller_l3.add_component_output(source_output_name = lt.InandOutputType.RECOMMENDED_ACTIVATION,
                                                                        source_tags = [lt.ComponentType.SMART_DEVICE, lt.InandOutputType.RECOMMENDED_ACTIVATION],
                                                                        source_weight = elem.source_weight,
                                                                        source_load_type = lt.LoadTypes.ACTIVATION,
                                                                        source_unit = lt.Units.TIMESTEPS)
            elem.connect_dynamic_input( input_fieldname = generic_smart_device.SmartDevice.l3_DeviceActivation,
                                        src_object = l3_ActivationSignal )
            
            
            # elem.connect_dynamic_input( in)
            my_controller_l3.add_component_input_and_connect(source_component_class = elem,
                                                             source_component_output = elem.LastActivation,
                                                             source_load_type = lt.LoadTypes.ACTIVATION,
                                                             source_unit = lt.Units.TIMESTEPS,
                                                             source_tags = [lt.ComponentType.SMART_DEVICE, lt.InandOutputType.LAST_ACTIVATION],
                                                             source_weight = elem.source_weight)
            my_controller_l3.add_component_input_and_connect(source_component_class = elem,
                                                             source_component_output = elem.EarliestActivation,
                                                             source_load_type = lt.LoadTypes.ACTIVATION,
                                                             source_unit = lt.Units.TIMESTEPS,
                                                             source_tags = [lt.ComponentType.SMART_DEVICE, lt.InandOutputType.EARLIEST_ACTIVATION],
                                                             source_weight = elem.source_weight)
            my_controller_l3.add_component_input_and_connect(source_component_class = elem,
                                                             source_component_output = elem.LatestActivation,
                                                             source_load_type = lt.LoadTypes.ACTIVATION,
                                                             source_unit = lt.Units.TIMESTEPS,
                                                             source_tags = [lt.ComponentType.SMART_DEVICE, lt.InandOutputType.LATEST_ACTIVATION],
                                                             source_weight = elem.source_weight)
        
        my_sim.add_component( my_controller_l3 )
                