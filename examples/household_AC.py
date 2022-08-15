from typing import Optional
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import generic_heat_pump
from hisim.components import building
# from hisim.components import PIDcontroller
from hisim.components import air_conditioner
from hisim.components import sumbuilder
from hisim import utils

import os

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def basic_household_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    """
    This setup function emulates an household including
    the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic
    system and the heat pump.

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
    year = 2021
    seconds_per_timestep = 60

    # Set weather
    location = "Seville"

    # Set photovoltaic system
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = 'PVSystem'
    azimuth  = 180
    tilt  = 30
    source_weight  = -1


    # Set occupancy
    occupancy_profile = "CH01"

    # Set building
    building_code = "ES.ME.SFH.05.Gen.ReEx.001.003"
    building_class = "medium"
    initial_temperature = 19
    heating_reference_temperature = -14

    # # Set heat pump controller
    # t_air_heating = 16.0
    # t_air_cooling = 24.0
    # offset = 0.5
    # hp_mode = 2
    
    # Set air conditioner controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5

    # # Set heat pump
    # hp_manufacturer = "Viessmann Werke GmbH & Co KG"
    # hp_name = "Vitocal 300-A AWO-AC 301.B07"
    # hp_min_operation_time = 60
    # hp_min_idle_time = 15
    
    # Set Air Conditioner  controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5
    
    # Set Air Conditioner 
    ac_manufacturer = "Samsung" #"Panasonic"#
    Model ="AC120HBHFKH/SA - AC120HCAFKH/SA" #"CS-TZ71WKEW + CU-TZ71WKE"# 
    hp_min_operation_time = 900 #seconds 
    hp_min_idle_time = 300 #seconds 
    control="on_off"  #either on_off or PID 

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Build occupancy
    my_occupancy_config= loadprofilegenerator_connector.OccupancyConfig(profile_name="CH01")
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig(location= "Seville")
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters= my_simulation_parameters)
    my_sim.add_component(my_weather)

    #Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(time=time,
                                          location=location,
                                          power=power,
                                          load_module_data=load_module_data,
                                          module_name=module_name,
                                          integrate_inverter=integrateInverter,
                                          tilt=tilt,
                                          azimuth = azimuth,
                                          inverter_name=inverter_name,
                                          source_weight = source_weight,
                                          name=name)
    my_photovoltaic_system=generic_pv_system.PVSystem(config=my_photovoltaic_system_config,
                                                      my_simulation_parameters=my_simulation_parameters)

    # Build Building
    my_building_config=building.BuildingConfig(building_code = building_code,
                                            bClass = building_class,
                                            initial_temperature = initial_temperature,
                                            heating_reference_temperature = heating_reference_temperature )


    my_photovoltaic_system.connect_input(my_photovoltaic_system.TemperatureOutside,
                                         my_weather.ComponentName,
                                         my_weather.TemperatureOutside)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DirectNormalIrradiance,
                                         my_weather.ComponentName,
                                         my_weather.DirectNormalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DirectNormalIrradianceExtra,
                                         my_weather.ComponentName,
                                         my_weather.DirectNormalIrradianceExtra)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DiffuseHorizontalIrradiance,
                                         my_weather.ComponentName,
                                         my_weather.DiffuseHorizontalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.GlobalHorizontalIrradiance,
                                         my_weather.ComponentName,
                                         my_weather.GlobalHorizontalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.Azimuth,
                                         my_weather.ComponentName,
                                         my_weather.Azimuth)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.ApparentZenith,
                                         my_weather.ComponentName,
                                         my_weather.ApparentZenith)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.WindSpeed,
                                         my_weather.ComponentName,
                                         my_weather.WindSpeed)
    my_sim.add_component(my_photovoltaic_system)


    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(name="BaseLoad",
                                                                      grid=[my_occupancy, "Subtract", my_photovoltaic_system ], my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_base_electricity_load_profile)

    my_building = building.Building(config=my_building_config,
                                        my_simulation_parameters=my_simulation_parameters)
    my_building.connect_input(my_building.Altitude,
                              my_weather.ComponentName,
                              my_building.Altitude)
    my_building.connect_input(my_building.Azimuth,
                              my_weather.ComponentName,
                              my_building.Azimuth)
    my_building.connect_input(my_building.DirectNormalIrradiance,
                              my_weather.ComponentName,
                              my_building.DirectNormalIrradiance)
    my_building.connect_input(my_building.DiffuseHorizontalIrradiance,
                              my_weather.ComponentName,
                              my_building.DiffuseHorizontalIrradiance)
    my_building.connect_input(my_building.GlobalHorizontalIrradiance,
                              my_weather.ComponentName,
                              my_building.GlobalHorizontalIrradiance)
    my_building.connect_input(my_building.DirectNormalIrradianceExtra,
                              my_weather.ComponentName,
                              my_building.DirectNormalIrradianceExtra)
    my_building.connect_input(my_building.ApparentZenith,
                             my_weather.ComponentName,
                             my_building.ApparentZenith)
    my_building.connect_input(my_building.TemperatureOutside,
                              my_weather.ComponentName,
                              my_weather.TemperatureOutside)
    my_building.connect_input(my_building.HeatingByResidents,
                              my_occupancy.ComponentName,
                              my_occupancy.HeatingByResidents)
    my_sim.add_component(my_building)
    
    # if control=="PID":
    
    #     pid_controller=PIDcontroller.PIDController(t_air_heating=t_air_heating,
    #                                                     t_air_cooling=t_air_cooling,
    #                                                     offset=offset,
    #                                                     my_simulation_parameters=my_simulation_parameters)
    #     pid_controller.connect_input(pid_controller.TemperatureMean,
    #                                           my_building.ComponentName,
    #                                           my_building.TemperatureMean)
    #     pid_controller.connect_input(pid_controller.HeatingByResidents,
    #                                           my_occupancy.ComponentName,
    #                                           my_occupancy.HeatingByResidents)
    #     pid_controller.connect_input(pid_controller.SolarGainThroughWindows,
    #                                           my_building.ComponentName,
    #                                           my_building.SolarGainThroughWindows)
    #     pid_controller.connect_input(pid_controller.TemperatureOutside,
    #                                           my_weather.ComponentName,
    #                                           my_weather.TemperatureOutside)
    #     pid_controller.connect_input(pid_controller.TemperatureAir,
    #                                           my_building.ComponentName,
    #                                           my_building.TemperatureAir)
    if control=="on_off":
        my_air_conditioner_controller=air_conditioner.AirConditionercontroller(t_air_heating=t_air_heating,
                                                                t_air_cooling=t_air_cooling,
                                                                offset=offset,
                                                                my_simulation_parameters=my_simulation_parameters)
        my_air_conditioner_controller.connect_input(my_air_conditioner_controller.TemperatureMean,
                                              my_building.ComponentName,
                                              my_building.TemperatureMean)

    my_air_conditioner = air_conditioner.AirConditioner(manufacturer=ac_manufacturer,
                                          name=Model,
                                          min_operation_time=hp_min_operation_time,
                                          min_idle_time=hp_min_idle_time,
                                          control=control,
                                          my_simulation_parameters=my_simulation_parameters)
    my_air_conditioner.connect_input(my_air_conditioner.TemperatureOutside,
                                my_weather.ComponentName,
                                my_weather.TemperatureOutside)
    
    if control=="on_off":
        my_air_conditioner.connect_input(my_air_conditioner.State,
                                my_air_conditioner_controller.ComponentName,
                                my_air_conditioner_controller.State)

        my_sim.add_component(my_air_conditioner_controller)
    # if control=="PID":
    #     my_air_conditioner.connect_input(my_air_conditioner.ElectricityOutputPID,
    #                             pid_controller.ComponentName,
    #                             pid_controller.ElectricityOutputPID)
        
    #     my_sim.add_component(pid_controller)
    my_sim.add_component(my_air_conditioner)
    # pid_controller.connect_input(pid_controller.COP,
                                          # my_heat_pump.ComponentName,
                                          # my_heat_pump.COP) 
        
    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_air_conditioner.ComponentName,
                              my_air_conditioner.ThermalEnergyDelivered)
    
    # pid_controller.connect_input(pid_controller.TemperatureMeanPrevious,
    #                                       my_building.ComponentName,
    #                                       my_building.TemperatureMeanPrevious)  



def basic_household_with_default_connections(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
    """
    This setup function emulates an household including
    the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic
    system and the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
    """

    ##### delete all files in cache:
    #dir = '..//hisim//inputs//cache'
    #for file in os.listdir( dir ):
     #   os.remove( os.path.join( dir, file ) )

    ##### System Parameters #####

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    name = 'PVSystem'
    azimuth  = 180
    tilt  = 30
    source_weight  = 0

    # Set occupancy
    occupancy_profile = "CH01"

    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    heating_reference_temperature = -14

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

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(profile_name="CH01")
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config,
                                                            my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather_config = weather.WeatherConfig(location="Aachen")
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig(time=time,
                                                                     location=location,
                                                                     power=power,
                                                                     load_module_data=load_module_data,
                                                                     module_name=module_name,
                                                                     integrate_inverter=integrateInverter,
                                                                     tilt=tilt,
                                                                     azimuth=azimuth,
                                                                     inverter_name=inverter_name,
                                                                     source_weight=source_weight,
                                                                     name=name)
    my_photovoltaic_system = generic_pv_system.PVSystem(config=my_photovoltaic_system_config,
                                                        my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_photovoltaic_system)
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    # Build Building
    my_building_config = building.BuildingConfig(building_code=building_code,
                                                 bClass=building_class,
                                                 initial_temperature=initial_temperature,
                                                 heating_reference_temperature=heating_reference_temperature)

    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(name="BaseLoad",
                                                                      grid=[my_occupancy, "Subtract", my_photovoltaic_system ], my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_base_electricity_load_profile)

    my_building = building.Building(config=my_building_config,
                                        my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections( my_weather, my_occupancy )   
    my_sim.add_component(my_building)

    my_heat_pump_controller = generic_heat_pump.HeatPumpController(t_air_heating=t_air_heating,
                                                           t_air_cooling=t_air_cooling,
                                                           offset=offset,
                                                           mode=hp_mode,
                                                           my_simulation_parameters=my_simulation_parameters)
    my_heat_pump_controller.connect_only_predefined_connections( my_building )
    
    #depending on previous loads, hard to define default connections
    my_heat_pump_controller.connect_input(my_heat_pump_controller.ElectricityInput,
                                          my_base_electricity_load_profile.ComponentName,
                                          my_base_electricity_load_profile.ElectricityOutput)
    my_sim.add_component(my_heat_pump_controller)

    my_heat_pump = generic_heat_pump.HeatPump(manufacturer=hp_manufacturer,
                                          name=hp_name,
                                          min_operation_time=hp_min_operation_time,
                                          min_idle_time=hp_min_idle_time,
                                      my_simulation_parameters=my_simulation_parameters)
    my_heat_pump.connect_only_predefined_connections( my_weather, my_heat_pump_controller )

    my_sim.add_component(my_heat_pump)

    #depending on type of heating device, hard to define default connections
    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_heat_pump.ComponentName,
                              my_heat_pump.ThermalEnergyDelivered)


