from typing import Optional
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
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

def household_AC_explicit(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
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


    # Set air conditioner controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5

    
    # Set Air Conditioner  controller
    t_air_heating = 16.0
    t_air_cooling = 24.0
    offset = 0.5
    
    # Set Air Conditioner 
    ac_manufacturer = "Samsung" #"Panasonic"#
    Model ="AC120HBHFKH/SA - AC120HCAFKH/SA" #"CS-TZ71WKEW + CU-TZ71WKE"# 
    hp_min_operation_time = 900 #seconds 
    hp_min_idle_time = 300 #seconds 
    control="on_off"

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
                                         my_weather.component_name,
                                         my_weather.TemperatureOutside)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DirectNormalIrradiance,
                                         my_weather.component_name,
                                         my_weather.DirectNormalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DirectNormalIrradianceExtra,
                                         my_weather.component_name,
                                         my_weather.DirectNormalIrradianceExtra)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.DiffuseHorizontalIrradiance,
                                         my_weather.component_name,
                                         my_weather.DiffuseHorizontalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.GlobalHorizontalIrradiance,
                                         my_weather.component_name,
                                         my_weather.GlobalHorizontalIrradiance)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.Azimuth,
                                         my_weather.component_name,
                                         my_weather.Azimuth)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.ApparentZenith,
                                         my_weather.component_name,
                                         my_weather.ApparentZenith)
    my_photovoltaic_system.connect_input(my_photovoltaic_system.WindSpeed,
                                         my_weather.component_name,
                                         my_weather.WindSpeed)
    my_sim.add_component(my_photovoltaic_system)


    my_base_electricity_load_profile = sumbuilder.ElectricityGrid(name="BaseLoad",
                                                                      grid=[my_occupancy, "Subtract", my_photovoltaic_system ], my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_base_electricity_load_profile)

    my_building = building.Building(config=my_building_config,
                                        my_simulation_parameters=my_simulation_parameters)
    my_building.connect_input(my_building.Altitude,
                              my_weather.component_name,
                              my_building.Altitude)
    my_building.connect_input(my_building.Azimuth,
                              my_weather.component_name,
                              my_building.Azimuth)
    my_building.connect_input(my_building.DirectNormalIrradiance,
                              my_weather.component_name,
                              my_building.DirectNormalIrradiance)
    my_building.connect_input(my_building.DiffuseHorizontalIrradiance,
                              my_weather.component_name,
                              my_building.DiffuseHorizontalIrradiance)
    my_building.connect_input(my_building.GlobalHorizontalIrradiance,
                              my_weather.component_name,
                              my_building.GlobalHorizontalIrradiance)
    my_building.connect_input(my_building.DirectNormalIrradianceExtra,
                              my_weather.component_name,
                              my_building.DirectNormalIrradianceExtra)
    my_building.connect_input(my_building.ApparentZenith,
                             my_weather.component_name,
                             my_building.ApparentZenith)
    my_building.connect_input(my_building.TemperatureOutside,
                              my_weather.component_name,
                              my_weather.TemperatureOutside)
    my_building.connect_input(my_building.HeatingByResidents,
                              my_occupancy.component_name,
                              my_occupancy.HeatingByResidents)
    my_sim.add_component(my_building)
    
    if control=="on_off":
        my_air_conditioner_controller=air_conditioner.AirConditionercontroller(t_air_heating=t_air_heating,
                                                                t_air_cooling=t_air_cooling,
                                                                offset=offset,
                                                                my_simulation_parameters=my_simulation_parameters)
        my_air_conditioner_controller.connect_input(my_air_conditioner_controller.TemperatureMean,
                                              my_building.component_name,
                                              my_building.TemperatureMean)

    my_air_conditioner = air_conditioner.AirConditioner(manufacturer=ac_manufacturer,
                                          name=Model,
                                          min_operation_time=hp_min_operation_time,
                                          min_idle_time=hp_min_idle_time,
                                          control=control,
                                          my_simulation_parameters=my_simulation_parameters)
    my_air_conditioner.connect_input(my_air_conditioner.TemperatureOutside,
                                my_weather.component_name,
                                my_weather.TemperatureOutside)
    
    if control=="on_off":
        my_air_conditioner.connect_input(my_air_conditioner.State,
                                my_air_conditioner_controller.component_name,
                                my_air_conditioner_controller.State)

        my_sim.add_component(my_air_conditioner_controller)

    my_sim.add_component(my_air_conditioner)

        
    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_air_conditioner.component_name,
                              my_air_conditioner.ThermalEnergyDelivered)
    




