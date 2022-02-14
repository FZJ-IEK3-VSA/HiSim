import inspect
import os
import sys
from hisim.simulator import Simulator
from hisim import components as cps
from hisim.components import occupancy
from hisim.components import weather
from hisim.components import pvs
from hisim.components import building
from hisim.components import heat_pump
from hisim.components import simple_bucket_boiler
from hisim.components import sumbuilder
from hisim.simulationparameters import SimulationParameters
__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def basic_household_boiler_explicit( my_sim: Simulator, my_simulation_parameters: SimulationParameters = None):
    """
    This setup function emulates an household including
    the basic components. Here the residents have their
    electricity and heating needs covered by the photovoltaic
    system and the heat pump. Hot water is provided by a simple elecrical boiler.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Photovoltaic System
        - Building
        - Heat Pump
        - electric boiler
    """

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

    # Set occupancy
    occupancy_profile = "CH01"

    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23

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
    
    # # Set hydrogen boiler
    # definition = 'hydrogen-boiler'
    # fuel = 'hydrogen'
    
    # Set smart electric boiler
    definition = '0815-boiler'
    fuel = 'electricity'
    smart = 1

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year, seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters
    # Build occupancy
    my_occupancy = occupancy.Occupancy(profile=occupancy_profile, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(location=location, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_weather)

    my_photovoltaic_system = pvs.PVSystem(time=time,
                                              location=location,
                                              power=power,
                                              load_module_data=load_module_data,
                                              module_name=module_name,
                                              integrateInverter=integrateInverter,
                                              inverter_name=inverter_name,
                                              my_simulation_parameters=my_simulation_parameters)
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
    
    my_base_electricity_load_profile = sumbuilder.ElectricityGrid( name = "BaseLoad",
                                                                      grid = [ my_occupancy, "Subtract", my_photovoltaic_system ], my_simulation_parameters=my_simulation_parameters )
    my_sim.add_component( my_base_electricity_load_profile )

    my_building = building.Building(building_code=building_code,
                                        bClass=building_class,
                                        initial_temperature=initial_temperature,
                                        my_simulation_parameters=my_simulation_parameters)
                                        #seconds_per_timestep=seconds_per_timestep)
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
    
    my_boiler = simple_bucket_boiler.Boiler( definition = definition, fuel = fuel, my_simulation_parameters=my_simulation_parameters )
    my_boiler.connect_input( my_boiler.WaterConsumption,
                             my_occupancy.ComponentName, 
                             my_occupancy.WaterConsumption )
    my_sim.add_component( my_boiler )
    my_boiler_controller = simple_bucket_boiler.BoilerController( smart = smart, my_simulation_parameters=my_simulation_parameters )
    my_boiler_controller.connect_input( my_boiler_controller.StorageTemperature,
                                        my_boiler.ComponentName,
                                        my_boiler.StorageTemperature )
    my_boiler.connect_input( my_boiler.State,
                             my_boiler_controller.ComponentName,
                             my_boiler_controller.State )
    my_sim.add_component( my_boiler_controller )
    
    if fuel == 'electricity':
        my_boiler_controller.connect_input( my_boiler_controller.ElectricityInput,
                                        my_base_electricity_load_profile.ComponentName,
                                        my_base_electricity_load_profile.ElectricityOutput )
        my_boiler_substracted_electricity_load_profile = sumbuilder.ElectricityGrid( name = "BoilerSubstracted",
                                                                      grid = [ my_base_electricity_load_profile, "Sum", my_boiler ] , my_simulation_parameters=my_simulation_parameters)
        my_sim.add_component( my_boiler_substracted_electricity_load_profile )

    my_heat_pump_controller = heat_pump.HeatPumpController(t_air_heating=t_air_heating,
                                                           t_air_cooling=t_air_cooling,
                                                           offset=offset,
                                                           mode=hp_mode, my_simulation_parameters=my_simulation_parameters)
    my_heat_pump_controller.connect_input(my_heat_pump_controller.TemperatureMean,
                                          my_building.ComponentName,
                                          my_building.TemperatureMean)
    if fuel == 'electricity':
        my_heat_pump_controller.connect_input( my_heat_pump_controller.ElectricityInput,
                                          my_boiler_substracted_electricity_load_profile.ComponentName,
                                          my_boiler_substracted_electricity_load_profile.ElectricityOutput )
    else:
        my_heat_pump_controller.connect_input( my_heat_pump_controller.ElectricityInput,
                                          my_base_electricity_load_profile.ComponentName,
                                          my_base_electricity_load_profile.ElectricityOutput )
    my_sim.add_component(my_heat_pump_controller)

    my_heat_pump = heat_pump.HeatPump(manufacturer=hp_manufacturer,
                                          name=hp_name,
                                          min_operation_time=hp_min_operation_time,
                                          min_idle_time=hp_min_idle_time, my_simulation_parameters=my_simulation_parameters)
    my_heat_pump.connect_input(my_heat_pump.State,
                               my_heat_pump_controller.ComponentName,
                               my_heat_pump_controller.State)
    my_heat_pump.connect_input(my_heat_pump.TemperatureOutside,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)

    my_sim.add_component(my_heat_pump)

    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_heat_pump.ComponentName,
                              my_heat_pump.ThermalEnergyDelivered)
    if fuel == 'electricity':
        my_heat_pump_substracted_electricity_load_profile = sumbuilder.ElectricityGrid( name = "HeatPumpSubstracted",
                                                                      grid = [ my_boiler_substracted_electricity_load_profile, "Sum", my_heat_pump ], my_simulation_parameters=my_simulation_parameters )
    else:
        my_heat_pump_substracted_electricity_load_profile = sumbuilder.ElectricityGrid( name = "HeatPumpSubstracted",
                                                                      grid = [ my_base_electricity_load_profile, "Sum", my_heat_pump ], my_simulation_parameters=my_simulation_parameters )
    my_sim.add_component( my_heat_pump_substracted_electricity_load_profile )

