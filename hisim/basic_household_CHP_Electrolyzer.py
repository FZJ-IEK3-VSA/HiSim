import os
import sys
import globals
import numpy as np
import simulator as sim
import loadtypes
import start_simulation
import components as cps
from components import occupancy
from components import weather
from components import building
from components import heat_pump_hplib
from components import controller
from components import storage
from components import pvs
from components import advanced_battery
from components import configuration
from components import chp_system
from components.hydrogen_generator import Electrolyzer ,HydrogenStorage
from components.csvloader import CSVLoader
from components.configuration import HydrogenStorageConfig, ElectrolyzerConfig


__authors__ = "Max Hillen, Tjarko Tjaden"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Max Hillen"
__email__ = "max.hillen@fz-juelich.de"
__status__ = "development"
#power=E3 10E3 25E3 100E3
power = 1000E3
#capacitiy=  25 100
capacitiy=100
def basic_household(my_sim,capacity=capacitiy,power=power):
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

    # Set photovoltaic system
    time = 2019

    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"
    # Set Battery

    #Set CHP
    min_operation_time = 60
    min_idle_time = 15
    gas_type = "Hydrogen"
    #Set Electrolyzer
    electrolyzer_c = ElectrolyzerConfig()

    #Set Hydrogen Storage
    hydrogen_storage_c=HydrogenStorageConfig()

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

    #ElectricityDemand
    csv_load_power_demand = CSVLoader(component_name="csv_load_power",
                                      csv_filename="Lastprofile/SOSO/Orginal/EFH_Bestand_TRY_5_Profile_1min.csv",
                                      column=0,
                                      loadtype=loadtypes.LoadTypes.Electricity,
                                      unit=loadtypes.Units.Watt,
                                      column_name="power_demand",
                                      simulation_parameters=my_sim_params,
                                      multiplier=6)
    my_sim.add_component(csv_load_power_demand)

    # Build occupancy
    #my_occupancy = occupancy.Occupancy(profile=occupancy_profile)
    #my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(location=location)
    my_sim.add_component(my_weather)

    # Build CHP
    my_chp = chp_system.CHP(min_operation_time=min_operation_time,
                            min_idle_time=min_idle_time,
                            gas_type=gas_type)


    #Build Electrolyzer

    my_electrolyzer = Electrolyzer(component_name="Electrolyzer",
                                    config=electrolyzer_c,
                                    seconds_per_timestep=my_sim_params.seconds_per_timestep)
    my_hydrogen_storage = HydrogenStorage(component_name="HydrogenStorage",
                                        config=hydrogen_storage_c,
                                        seconds_per_timestep=my_sim_params.seconds_per_timestep)

    # Build building
    '''
    my_building = building.Building(building_code=building_code,
                                        bClass=building_class,
                                        initial_temperature=initial_temperature,
                                        sim_params=my_sim_params,
                                        seconds_per_timestep=seconds_per_timestep)
                                        
    #Build Battery
    fparameter = np.load(globals.HISIMPATH["bat_parameter"])
    my_battery = advanced_battery.AdvancedBattery(parameter=fparameter,sim_params=my_sim_params,capacity=capacity)                                    
    '''


    #Build Controller
    my_controller = controller.Controller(strategy="seasonal_storage")
    '''
        residual_power = CSVLoader(component_name="residual_power",
                               csv_filename="advanced_battery/Pr_ideal_1min.csv",
                               column=0,
                               loadtype=loadtypes.LoadTypes.Electricity,
                               unit=loadtypes.Units.Watt,
                               column_name="Pr_ideal_1min",
                               simulation_parameters=sim_param)

        sim.add_component(residual_power)
    '''

    '''
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
                                          
    my_heat_pump.connect_input(my_heat_pump.OnOffSwitch,
                               my_controller.ComponentName,
                               my_controller.ControlSignalHeatPump)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputPrimary,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputSecondary,
                               my_heat_storage.ComponentName,eir
                               my_heat_storage.WaterOutputTemperature)
    my_heat_pump.connect_input(my_heat_pump.TemperatureInputPrimary,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)
    my_heat_pump.connect_input(my_heat_pump.TemperatureAmbient,
                               my_weather.ComponentName,
                               my_weather.TemperatureOutside)
    my_sim.add_component(my_heat_pump)


    # Build heat storage

    my_heat_storage.connect_input(my_heat_storage.InputMass1,
                               my_heat_pump.ComponentName,
                               my_heat_pump.MassFlowOutput)
    my_heat_storage.connect_input(my_heat_storage.InputTemp1,
                               my_heat_pump.ComponentName,
                               my_heat_pump.TemperatureOutput)
    my_heat_storage.connect_input(my_heat_storage.InputTemp1,
                               my_heat_pump.ComponentName,
                               my_heat_pump.TemperatureOutput)

    my_sim.add_component(my_heat_storage)
    '''
    my_photovoltaic_system = pvs.PVSystem(time=time,
                                          location=location,
                                          power=power,
                                          load_module_data=load_module_data,
                                          module_name=module_name,
                                          integrateInverter=integrateInverter,
                                          inverter_name=inverter_name,
                                          sim_params=my_sim_params)
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


    '''
    my_battery.connect_input(my_battery.LoadingPowerInput,
                               my_controller.ComponentName,
                               my_controller.ElectricityToOrFromBatteryTarget)

    my_controller.connect_input(my_controller.ElectricityToOrFromBatteryReal,
                               my_battery.ComponentName,
                               my_battery.ACBatteryPower)
    '''

    my_controller.connect_input(my_controller.ElectricityConsumptionBuilding,
                               csv_load_power_demand.ComponentName,
                               csv_load_power_demand.Output1)

    my_controller.connect_input(my_controller.ElectricityOutputPvs,
                               my_photovoltaic_system.ComponentName,
                               my_photovoltaic_system.ElectricityOutput)



    my_electrolyzer.connect_input(my_electrolyzer.ElectricityInput,
                               my_controller.ComponentName,
                               my_controller.ElectricityToElectrolyzerTarget)


    my_electrolyzer.connect_input(my_electrolyzer.HydrogenNotStored,
                               my_hydrogen_storage.ComponentName,
                               my_hydrogen_storage.HydrogenNotStored)

    my_controller.connect_input(my_controller.ElectricityToElectrolyzerReal,
                               my_electrolyzer.ComponentName,
                               my_electrolyzer.UnusedPower)
    my_controller.connect_input(my_controller.ElectricityFromCHPReal,
                               my_chp.ComponentName,
                               my_chp.ElectricityOutput)


    my_hydrogen_storage.connect_input(my_hydrogen_storage.ChargingHydrogenAmount,
                                   my_electrolyzer.ComponentName,
                                   my_electrolyzer.HydrogenOutput)
    my_hydrogen_storage.connect_input(my_hydrogen_storage.DischargingHydrogenAmountTarget,
                                   my_chp.ComponentName,
                                   my_chp.GasDemandTarget)
    '''
    my_hydrogen_storage.connect_input(my_hydrogen_storage.DischargingHydrogenAmount,
                                   my_chp.ComponentName,
                                   my_chp.GasDemand)
    '''
    my_chp.connect_input(my_chp.HydrogenNotReleased,
                           my_hydrogen_storage.ComponentName,
                           my_hydrogen_storage.HydrogenNotReleased)

    my_chp.connect_input(my_chp.ControlSignal,
                           my_controller.ComponentName,
                           my_controller.ControlSignalChp)                               
    my_chp.connect_input(my_chp.ElectricityFromCHPTarget,
                           my_controller.ComponentName,
                           my_controller.ElectricityFromCHPTarget)





    #my_sim.add_component(my_battery)

    my_sim.add_component(my_controller)


    my_sim.add_component(my_chp)
    my_sim.add_component(my_electrolyzer)
    my_sim.add_component(my_hydrogen_storage)




def basic_household_implicit(my_sim):
    pass

