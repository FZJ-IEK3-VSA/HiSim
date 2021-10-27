import os
import sys
import simulator as sim
import loadtypes
import components as cps
from components import occupancy
from components import weather
from components import building
from components import heat_pump_hplib
from components import controller
from components import storage
from components import random_numbers
from components import gas_heater
from components import chp_system
from components.csvloader import CSVLoader
__authors__ = "Max Hillen, Tjarko Tjaden"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Max Hillen"
__email__ = "max.hillen@fz-juelich.de"
__status__ = "development"

def basic_household(my_sim):
    """
    This setup function represents an household including
    electric and thermal consumption and a heatpump.

    - Simulation Parameters
    - Components
        - Weather
        - Building
        - Occupancy (Residents' Demands)
        - Heat Pump
    """

    ##### System Parameters #####

    # Set simulation parameters
    year = 2021
    seconds_per_timestep = 60

    # Set weather
    location = "Aachen"

    # Set occupancy
    occupancy_profile = "CH01"

    # Set building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_class = "medium"
    initial_temperature = 23
    #Heat demand

    # Set heat pump
    hp_manufacturer = "Generic"
    hp_type = 1 # air/water | regulated
    hp_thermal_power = 12000 # W
    hp_t_input = -7 # °C
    hp_t_output = 52 # °C

    # Set warm water storage
    wws_volume = 500 # l
    wws_temp_outlet=35
    wws_temp_ambient=15

    ##### Build Components #####


    # Build system parameters
    my_sim_params: sim.SimulationParameters = sim.SimulationParameters.full_year(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.set_parameters(my_sim_params)

     # Build heat demand
    csv_load_heat_demand = CSVLoader(component_name="csv_load_heat_demand",
                                      csv_filename="loadprofiles/EFH_Bestand_TRY_5_Profile_1min.csv",
                                      column=1,
                                      loadtype=loadtypes.LoadTypes.WarmWater,
                                      unit=loadtypes.Units.Watt,
                                      column_name="heat_demand",
                                      simulation_parameters=my_sim_params,
                                      multiplier=10000/ 1000)
    my_sim.add_component(csv_load_heat_demand)
    my_rn1 = random_numbers.RandomNumbers (name="Random numbers 100-200",
                        timesteps=my_sim_params.timesteps,
                        minimum=1000,
                        maximum=2000)
    my_sim.add_component(my_rn1)
    '''                                    
    # Build occupancy
    my_occupancy = occupancy.Occupancy(profile=occupancy_profile)
    my_sim.add_component(my_occupancy)
    '''
    # Build Weather
    my_weather = weather.Weather(location=location)
    my_sim.add_component(my_weather)
    '''
    # Build building
    my_building = building.Building(building_code=building_code,
                                        bClass=building_class,
                                        initial_temperature=initial_temperature,
                                        sim_params=my_sim_params,
                                        seconds_per_timestep=seconds_per_timestep)
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
    '''
    # Build heat pump 
    my_heat_pump = heat_pump_hplib.HeatPumpHplib(model=hp_manufacturer, 
                                                    group_id=hp_type,
                                                    t_in=hp_t_input,
                                                    t_out=hp_t_output,
                                                    p_th_set=hp_thermal_power)
    my_heat_storage = storage.HeatStorage(V_SP = wws_volume,
                                          temperature_of_warm_water_extratcion = wws_temp_outlet,
                                          ambient_temperature=wws_temp_ambient)
    my_controller = controller.Controller()
    my_chp_system = chp_system.CHP()

    my_controller.connect_input(my_controller.StorageTemperature,
                               my_heat_storage.ComponentName,
                               my_heat_storage.WaterOutputTemperature)




    my_heat_pump.connect_input(my_heat_pump.OnOffSwitch,
                               my_controller.ComponentName,
                               my_controller.ControlSignalHeatPump)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputPrimary,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputSecondary,
                               my_heat_storage.ComponentName,
                               my_heat_storage.WaterOutputTemperature)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputPrimary,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)
    my_heat_pump.connect_input(my_heat_pump.TemperatureAmbient,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)

    # Build heat storage



    """
    my_gas_heater=gas_heater.GasHeater()
    my_gas_heater.connect_input(my_gas_heater.ControlSignal,
                               my_controller.ComponentName,
                               my_controller.ControlSignalGasHeater)

    my_gas_heater.connect_input(my_gas_heater.MassflowInputTemperature,
                               my_heat_storage.ComponentName,
                               my_heat_storage.WaterOutputTemperature)

    my_chp_system.connect_input(my_chp_system.ControlSignal,
                               my_controller.ComponentName,
                               my_controller.ControlSignalChp)
    my_chp_system.connect_input(my_chp_system.MassflowInputTemperature,
                               my_heat_storage.ComponentName,
                               my_heat_storage.WaterOutputTemperature)

    """
    my_heat_storage.connect_input(my_heat_storage.ThermalDemandHeatingWater,
                               csv_load_heat_demand.ComponentName,
                               csv_load_heat_demand.Output1)



    my_heat_storage.connect_input(my_heat_storage.ThermalInputPower1,
                               my_heat_pump.ComponentName,
                               my_heat_pump.ThermalOutputPower)


    #Demand an Heating Water anschließen
    my_sim.add_component(my_heat_storage)
    my_sim.add_component(my_heat_pump)
    #my_sim.add_component(my_gas_heater)
    #my_sim.add_component(my_chp_system)


    my_sim.add_component(my_controller)



def basic_household_implicit(my_sim):
    pass

