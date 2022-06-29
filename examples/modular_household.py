from typing import Optional, List, Union

import hisim.loadtypes as lt

from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import generic_smart_device
from hisim.components import building
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heatpump_modular
from hisim.components import controller_l3_generic_heatpump_modular
from hisim.components import generic_dhw_boiler
from hisim.components import controller_l2_generic_dhw_boiler
from hisim.components import generic_oil_heater
from hisim.components import generic_district_heating
from hisim.components import sumbuilder
from hisim.components import controller_l2_energy_management_system
from hisim.components import advanced_battery_bslib
from hisim.components import generic_CHP
from hisim.components import controller_l2_generic_chp
from hisim import utils

import os
import csv

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

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

    #initialize components involved in production and consumption
    production = [ ]
    consumption = [ ]
    heater = [ ]
    
    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.january_only( year = year,
                                                                      seconds_per_timestep = seconds_per_timestep )
        my_simulation_parameters.enable_all_options( )
    my_simulation_parameters.reset_system_config( predictive = True, prediction_horizon = 24 * 3600, pv_included = True, smart_devices_included = True, boiler_included = 'electricity', 
                                                  heating_device_included = 'heat_pump', battery_included = True, chp_included = True )  
    my_sim.SimulationParameters = my_simulation_parameters
    
    #get system configuration
    predictive = my_simulation_parameters.system_config.predictive #True or False
    pv_included = my_simulation_parameters.system_config.pv_included #True or False
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included #True or False
    boiler_included = my_simulation_parameters.system_config.boiler_included #Electricity, Hydrogen or False
    heating_device_included = my_simulation_parameters.system_config.heating_device_included  
    battery_included = my_simulation_parameters.system_config.battery_included
    chp_included = my_simulation_parameters.system_config.chp_included
      
    #set heating system and boiler
    min_operation_time = 3600
    min_idle_time = 2700

    #Set heating system
    if heating_device_included == 'heat_pump':
        # Set heat pump controller
        T_min_heating = 19.0
        T_max_heating = 23.0
        T_min_cooling = 23.0
        T_max_cooling = 26.0
        T_tolerance = 1.0
        heating_season_begin = 240
        heating_season_end = 150
        
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
        
    if battery_included:
        system_id = 'SG1'
        p_inv_custom = 5.0
        e_bat_custom = 10.0
        batteryname = "Battery"

    ##### Build Components #####
    
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
    
    if boiler_included: 
        
        my_boiler_controller_l2 = controller_l2_generic_dhw_boiler.L2_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                                  source_weight = count )
        my_boiler_controller_l1 = controller_l1_generic_runtime.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                               min_operation_time = min_operation_time,
                                                                               min_idle_time = min_idle_time,
                                                                               source_weight = count,
                                                                               name = 'Boiler' )
        my_boiler_controller_l1.connect_only_predefined_connections( my_boiler_controller_l2 )
        my_sim.add_component( my_boiler_controller_l1 )
        config = generic_dhw_boiler.Boiler.get_default_config( )
        config.source_weight = count
        my_boiler = generic_dhw_boiler.Boiler( my_simulation_parameters = my_simulation_parameters,
                                               config = config )
        my_boiler.connect_only_predefined_connections( my_boiler_controller_l1 )
        my_boiler.connect_only_predefined_connections( my_occupancy )
        my_sim.add_component( my_boiler )
        
        my_boiler_controller_l2.connect_only_predefined_connections( my_boiler )
        my_sim.add_component( my_boiler_controller_l2 )
        consumption.append( my_boiler )
        count += 1
        
    if heating_device_included == 'heat_pump':
        
        my_heatpump_controller_l2 = controller_l2_generic_heatpump_modular.L2_Controller(    my_simulation_parameters = my_simulation_parameters,
                                                                                            T_min_heating = T_min_heating,
                                                                                            T_max_heating = T_max_heating,
                                                                                            T_min_cooling = T_min_cooling,
                                                                                            T_max_cooling = T_max_cooling,
                                                                                            T_tolerance = T_tolerance,
                                                                                            heating_season_begin = heating_season_begin,
                                                                                            heating_season_end = heating_season_end,
                                                                                            source_weight = count )
        my_heatpump_controller_l2.connect_only_predefined_connections( my_building )
        my_sim.add_component( my_heatpump_controller_l2 )
        
        my_heatpump_controller_l1 = controller_l1_generic_runtime.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                                min_operation_time = min_operation_time,
                                                                                min_idle_time = min_idle_time,
                                                                                source_weight = count,
                                                                                name = 'HeatPump' )
        my_heatpump_controller_l1.connect_only_predefined_connections( my_heatpump_controller_l2 )
        my_sim.add_component( my_heatpump_controller_l1 )
        my_heatpump = generic_heat_pump_modular.HeatPump( my_simulation_parameters = my_simulation_parameters,
                                                         heating_season_begin = heating_season_begin,
                                                         heating_season_end = heating_season_end,
                                                         source_weight = count )
        my_heatpump.connect_only_predefined_connections( my_weather ) 
        my_heatpump.connect_only_predefined_connections( my_heatpump_controller_l1 )
        my_sim.add_component( my_heatpump )
      
        count += 1
        consumption.append( my_heatpump )
        heater.append( my_heatpump )

    if battery_included or chp_included :
        my_electricity_controller = controller_l2_energy_management_system.ControllerElectricityGeneric( my_simulation_parameters = my_simulation_parameters )
        my_electricity_controller.add_component_inputs_and_connect(  source_component_classes = consumption,
                                                                     outputstring = 'ElectricityOutput',
                                                                     source_load_type = lt.LoadTypes.Electricity,
                                                                     source_unit = lt.Units.Watt,
                                                                     source_tags = [ lt.InandOutputType.Consumption ],
                                                                     source_weight = 999 )
        my_electricity_controller.add_component_inputs_and_connect(  source_component_classes = production,
                                                                     outputstring = 'ElectricityOutput',
                                                                     source_load_type = lt.LoadTypes.Electricity,
                                                                     source_unit = lt.Units.Watt,
                                                                     source_tags = [ lt.InandOutputType.Production ],
                                                                     source_weight = 999 )
        
    if battery_included:
        my_advanced_battery_config = advanced_battery_bslib.BatteryConfig( system_id = system_id,
                                                                           p_inv_custom = p_inv_custom,
                                                                           e_bat_custom = e_bat_custom,
                                                                           name = batteryname,
                                                                           source_weight = count )
        count += 1 
        my_advanced_battery = advanced_battery_bslib.Battery( my_simulation_parameters = my_simulation_parameters, config = my_advanced_battery_config )
        
        my_electricity_controller.add_component_input_and_connect(  source_component_class = my_advanced_battery,
                                                                    source_component_output = my_advanced_battery.AcBatteryPower,
                                                                    source_load_type = lt.LoadTypes.Electricity,
                                                                    source_unit = lt.Units.Watt,
                                                                    source_tags = [lt.ComponentType.Battery,lt.InandOutputType.ElectricityReal],
                                                                    source_weight = my_advanced_battery.source_weight )

        electricity_to_or_from_battery_target = my_electricity_controller.add_component_output( source_output_name = lt.InandOutputType.ElectricityTarget,
                                                                                                source_tags = [ lt.ComponentType.Battery ],
                                                                                                source_weight = my_advanced_battery.source_weight,
                                                                                                source_load_type = lt.LoadTypes.Electricity,
                                                                                                source_unit = lt.Units.Watt )
    
        my_advanced_battery.connect_dynamic_input( input_fieldname = advanced_battery_bslib.Battery.LoadingPowerInput,
                                                   src_object = electricity_to_or_from_battery_target )
        my_sim.add_component( my_advanced_battery )
        
    if chp_included:
        l2_config = controller_l2_generic_chp.L2_Controller.get_default_config( )
        l2_config.source_weight = count
        l1_config = generic_CHP.L1_Controller.get_default_config( )
        l1_config.source_weight = count
        chp_config = generic_CHP.GCHP.get_default_config( )
        chp_config.source_weight = count
        count += 1
        my_chp_controller_l2 = controller_l2_generic_chp.L2_Controller( my_simulation_parameters = my_simulation_parameters,
                                                                        config = l2_config )
        my_chp_controller_l2.connect_only_predefined_connections( my_building )
        my_sim.add_component( my_chp_controller_l2 )
        
        my_chp_controller_l1 = generic_CHP.L1_Controller( my_simulation_parameters = my_simulation_parameters,
                                                          config = l1_config)
        my_chp_controller_l1.connect_only_predefined_connections( my_chp_controller_l2 )
        my_sim.add_component( my_chp_controller_l1 )
        my_chp = generic_CHP.GCHP( my_simulation_parameters = my_simulation_parameters,
                                   config = chp_config )
        my_chp.connect_only_predefined_connections( my_chp_controller_l1 )
        my_sim.add_component( my_chp )
      
        production.append( my_chp )
        heater.append( my_chp )
        
    my_building.add_component_inputs_and_connect(  source_component_classes = heater,
                                                   outputstring = 'ThermalEnergyDelivered',
                                                   source_load_type = lt.LoadTypes.Heating,
                                                   source_unit = lt.Units.Watt,
                                                   source_tags = [ lt.InandOutputType.HeatToBuilding ],
                                                   source_weight = 999 )
        
    if battery_included or chp_included:
        my_sim.add_component( my_electricity_controller )
        
    if predictive == True:
        
        #construct predictive controller
        my_controller_l3 = controller_l3_generic_heatpump_modular.L3_Controller( my_simulation_parameters = my_simulation_parameters )
        
        #connect boiler
        if boiler_included == 'electricity':
            l3_BoilerSignal = my_controller_l3.add_component_output(    source_output_name = lt.InandOutputType.ControlSignal,
                                                                        source_tags = [ lt.ComponentType.Boiler ],
                                                                        source_weight = my_boiler.source_weight,
                                                                        source_load_type = lt.LoadTypes.OnOff,
                                                                        source_unit = lt.Units.binary )
        
            my_boiler_controller_l2.connect_dynamic_input( input_fieldname = controller_l2_generic_dhw_boiler.L2_Controller.l3_DeviceSignal,
                                                           src_object = l3_BoilerSignal )
            
            my_controller_l3.add_component_input_and_connect(   source_component_class = my_boiler_controller_l1,
                                                                source_component_output = my_boiler_controller_l1.l1_DeviceSignal,
                                                                source_load_type= lt.LoadTypes.OnOff,
                                                                source_unit= lt.Units.binary,
                                                                source_tags = [ lt.ComponentType.Boiler, lt.InandOutputType.ControlSignal ],
                                                                source_weight = my_boiler_controller_l1.source_weight )
            count += 1
            
        #connect heat pump    
        if heating_device_included == 'heat_pump':
        
            l3_HeatPumpSignal = my_controller_l3.add_component_output( source_output_name = lt.InandOutputType.ControlSignal,
                                                                       source_tags = [ lt.ComponentType.HeatPump ],
                                                                       source_weight = my_heatpump.source_weight,
                                                                       source_load_type = lt.LoadTypes.OnOff,
                                                                       source_unit = lt.Units.binary )
            
            my_heatpump_controller_l2.connect_dynamic_input( input_fieldname = controller_l2_generic_heatpump_modular.L2_Controller.l3_DeviceSignal,
                                                             src_object = l3_HeatPumpSignal )
            
            my_controller_l3.add_component_input_and_connect( source_component_class = my_heatpump_controller_l1,
                                                              source_component_output = my_heatpump_controller_l1.l1_DeviceSignal,
                                                              source_load_type = lt.LoadTypes.OnOff,
                                                              source_unit = lt.Units.binary,
                                                              source_tags = [ lt.ComponentType.HeatPump, lt.InandOutputType.ControlSignal ],
                                                              source_weight = my_heatpump_controller_l1.source_weight )
            count += 1
            
        if smart_devices_included:
            for elem in my_smart_devices:
                l3_ActivationSignal = my_controller_l3.add_component_output( source_output_name = lt.InandOutputType.RecommendedActivation,
                                                                             source_tags = [ lt.ComponentType.SmartDevice, lt.InandOutputType.RecommendedActivation ],
                                                                             source_weight = elem.source_weight,
                                                                             source_load_type = lt.LoadTypes.Activation,
                                                                             source_unit = lt.Units.timesteps )  
                elem.connect_dynamic_input( input_fieldname = generic_smart_device.SmartDevice.l3_DeviceActivation,
                                            src_object = l3_ActivationSignal )
                
                
                # elem.connect_dynamic_input( in)
                my_controller_l3.add_component_input_and_connect( source_component_class = elem,
                                                                  source_component_output = elem.LastActivation,
                                                                  source_load_type = lt.LoadTypes.Activation,
                                                                  source_unit = lt.Units.timesteps,
                                                                  source_tags = [ lt.ComponentType.SmartDevice, lt.InandOutputType.LastActivation ],
                                                                  source_weight = elem.source_weight )
                my_controller_l3.add_component_input_and_connect( source_component_class = elem,
                                                                  source_component_output = elem.EarliestActivation,
                                                                  source_load_type = lt.LoadTypes.Activation,
                                                                  source_unit = lt.Units.timesteps,
                                                                  source_tags = [ lt.ComponentType.SmartDevice, lt.InandOutputType.EarliestActivation ],
                                                                  source_weight = elem.source_weight )
                my_controller_l3.add_component_input_and_connect( source_component_class = elem,
                                                                  source_component_output = elem.LatestActivation,
                                                                  source_load_type = lt.LoadTypes.Activation,
                                                                  source_unit = lt.Units.timesteps,
                                                                  source_tags = [ lt.ComponentType.SmartDevice, lt.InandOutputType.LatestActivation ],
                                                                  source_weight = elem.source_weight )
        
        my_sim.add_component( my_controller_l3 )
                