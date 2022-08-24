from typing import Optional, List, Union

import hisim.loadtypes as lt
import hisim.log
from hisim.simulationparameters import SystemConfig
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_price_signal
from hisim.components import weather
from hisim.components import building
from hisim.components import controller_l2_energy_management_system
from hisim.components import advanced_battery_bslib
from component_connections import *

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

    # path of system config file
    system_config_filename = "system_config.json"

    count = 1  # initialize source_weight with one 
    production = []  # initialize list of components involved in production
    consumption = []# initialize list of components involved in consumption
    heater = [] # initialize list of components used for heating
    
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
        my_simulation_parameters.reset_system_config(
            prediction_horizon=24 * 3600, pv_included=True, smart_devices_included=True, water_heating_system_installed=lt.HeatingSystems.HEAT_PUMP,
            heating_system_installed=lt.HeatingSystems.HEAT_PUMP, buffer_volume=500, battery_included=True, chp_included=True, current_mobility=lt.Cars.NO_CAR,
            mobility_distance=lt.MobilityDistance.RURAL)  
    my_sim.set_simulation_parameters(my_simulation_parameters)
    
    #get system configuration
    location = my_simulation_parameters.system_config.location
    occupancy_profile = my_simulation_parameters.system_config.occupancy_profile
    building_code = my_simulation_parameters.system_config.building_code
    pv_included = my_simulation_parameters.system_config.pv_included #True or False
    smart_devices_included = my_simulation_parameters.system_config.smart_devices_included #True or False
    water_heating_system_installed = my_simulation_parameters.system_config.water_heating_system_installed #Electricity, Hydrogen or False
    heating_system_installed = my_simulation_parameters.system_config.heating_system_installed
    buffer_volume = my_simulation_parameters.system_config.buffer_volume
    battery_included = my_simulation_parameters.system_config.battery_included
    chp_included = my_simulation_parameters.system_config.chp_included

    """BASICS"""  
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(profile_name=occupancy_profile.value)
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)
    consumption.append(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig(location=location.value)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters,
                                 my_simulation_repository=my_sim.simulation_repository )
    my_sim.add_component(my_weather)
    
    # Build building
    my_building_config=building.Building.get_default_config()
    my_building_config.building_code = building_code.value
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)   
    my_sim.add_component(my_building)
    
    #add price signal
    my_price_signal = generic_price_signal.PriceSignal(my_simulation_parameters = my_simulation_parameters)
    my_sim.add_component(my_price_signal)
    
    """PV"""
    if pv_included:
        my_pv_system1, production, count = configure_pv_system(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters,
                                                               my_weather=my_weather, production=production,count=count)
        my_pv_system2, production, count = configure_pv_system(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters,
                                                               my_weather=my_weather, production=production,count=count)

    """SMART DEVICES"""
    my_smart_devices, consumption, count = configure_smart_devices(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters,
                                                                   consumption=consumption, count=count)
    
    """SURPLUS CONTROLLER"""
    if battery_included or chp_included or heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING] \
        or water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
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
    count = configure_water_heating(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_occupancy=my_occupancy, my_electricity_controller=my_electricity_controller,
                                    my_weather=my_weather, water_heating_system_installed=water_heating_system_installed, count=count)
    
    """HEATING"""
    if buffer_volume > 80:
        my_heater, my_buffer, count = configure_heating_with_buffer(
            my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building, my_electricity_controller=my_electricity_controller,
            my_weather=my_weather, heating_system_installed=heating_system_installed, buffer_volume=buffer_volume, count=count)
    else:
        my_heater, count = configure_heating(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
                                             my_electricity_controller=my_electricity_controller, my_weather=my_weather,
                                             heating_system_installed=heating_system_installed, count=count)
    heater.append( my_heater )
    
    """BATTERY"""
    if battery_included:
        my_battery, count = configure_battery(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_electricity_controller=my_electricity_controller,count=count)
        
    """CHP + H2 STORAGE + ELECTROLYSIS"""
    if chp_included:
        my_chp, count = configure_elctrolysis_h2storage_chp_system(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_building=my_building,
                                                                   my_electricity_controller=my_electricity_controller, count=count)
        heater.append(my_chp)
        
    if buffer_volume > 80:
        my_buffer.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                   source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                   source_tags=[lt.InandOutputType.HEAT_TO_BUFFER], source_weight=999)
    else:   
        my_building.add_component_inputs_and_connect(source_component_classes=heater, outputstring='ThermalPowerDelivered',
                                                     source_load_type=lt.LoadTypes.HEATING, source_unit=lt.Units.WATT,
                                                     source_tags=[lt.InandOutputType.HEAT_TO_BUILDING], source_weight=999)
        
    if battery_included or chp_included or heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING] \
        or water_heating_system_installed in [lt.HeatingSystems.HEAT_PUMP, lt.HeatingSystems.ELECTRIC_HEATING]:
        my_sim.add_component(my_electricity_controller)
     
    """PREDICTIVE CONTROLLER FOR SMART DEVICES"""    
    #use predictive controller if smart devices are included and do not use it if it is false
    if smart_devices_included:
        my_simulation_parameters.system_config.predictive = True
        configure_smart_controller_for_smart_devices(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters, my_smart_devices=my_smart_devices)
    else:
        my_simulation_parameters.system_config.predictive = False
