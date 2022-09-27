import gc
#import objgraph
import numpy as np
from typing import Optional
from hisim.simulator import SimulationParameters
from hisim.simulator import Simulator
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import PIDcontroller
from hisim.components import air_conditioner
from hisim.components import sumbuilder
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from pympler import tracker
from pympler import summary
from pympler import muppy
import os
import datetime

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


def household_AC_explicit(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters] = None, ki: float =88.2599230901655, kd: float =0, kp: float =8003.2594202044575) -> None:
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
        - Air conditioner 
    """
    ##### delete all files in cache:
    # dir = '..//hisim//inputs//cache'
    # for file in os.listdir( dir ):
    #     os.remove( os.path.join( dir, file ) )

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
    building_code ="ES.ME.SFH.05.Gen.ReEx.001.003"  #  "ES.ME.SFH.04.Gen.ReEx.001.001" # 
    building_class = "medium"
    initial_temperature = 19
    heating_reference_temperature = -14
    
    # Set Air Conditioner  controller
    t_air_heating = 21.0
    t_air_cooling = 23.0
    offset = 0.5
    
    # Set Air Conditioner 
    ac_manufacturer = "Samsung" #"Panasonic"#
    Model ="AC120HBHFKH/SA - AC120HCAFKH/SA" #"CS-TZ71WKEW + CU-TZ71WKE"# 
    hp_min_operation_time = 900 #seconds 
    hp_min_idle_time = 300 #seconds 
    control="PID" #PID or on_off

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        #my_simulation_parameters = SimulationParameters.full_year_all_options(year=year, seconds_per_timestep=seconds_per_timestep)
        my_simulation_parameters = SimulationParameters.january_only(year=year, seconds_per_timestep=seconds_per_timestep)
        keystr = "ki_" + f"{ki:.3f}" + "_kp_" + f"{kp:.3f}" + "_kd_" + f"{kd:.3f}"
        # result_directory = os.path.join("ac_results_5", "testing mpc controller with fixed price signal 24 h average" )
        # my_simulation_parameters = SimulationParameters(start_date=datetime.date(year, 8, 1),end_date=datetime.date(year, 8, 31) ,seconds_per_timestep=seconds_per_timestep,result_directory=result_directory)
        my_simulation_parameters.enable_all_options()
        # my_simulation_parameters.get_unique_key()
        # my_simulation_parameters.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
        my_simulation_parameters.result_directory = os.path.join("ac_results_5", keystr )
        #my_simulation_parameters.post_processing_options.clear()
        my_simulation_parameters.enable_all_options()
        # my_simulation_parameters.get_pid_plots_Comparison()
        

    my_sim.set_simulation_parameters(my_simulation_parameters)
    
    """ Occupancy Profile """
    my_occupancy_config= loadprofilegenerator_connector.OccupancyConfig(profile_name="CH01", name="Occupancy")
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    my_sim.add_component(my_occupancy)

    """Weather """
    my_weather_config = weather.WeatherConfig.get_default(location_entry= weather.LocationEnum.Seville)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters= my_simulation_parameters)
    my_sim.add_component(my_weather)

    """Photovoltaic System"""
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
    
    my_photovoltaic_system.connect_only_predefined_connections(my_weather)
    my_sim.add_component(my_photovoltaic_system)
   
    """Building"""
    my_building_config=building.BuildingConfig(building_code = building_code,
                                            bClass = building_class,
                                            initial_temperature = initial_temperature,
                                            heating_reference_temperature = heating_reference_temperature,
                                            name="Building1")

    my_building = building.Building(config=my_building_config,
                                    my_simulation_parameters=my_simulation_parameters)
    my_building.connect_only_predefined_connections(my_weather, my_occupancy)
    my_sim.add_component(my_building)
    
    # my_base_electricity_load_profile = sumbuilder.ElectricityGrid(name="BaseLoad",
    #                                                                   grid=[my_occupancy, "Subtract", my_photovoltaic_system ], my_simulation_parameters=my_simulation_parameters)
    # my_sim.add_component(my_base_electricity_load_profile)
    

    
    """Air Conditioner"""
    my_air_conditioner = air_conditioner.AirConditioner(manufacturer=ac_manufacturer,
                                          name=Model,
                                          min_operation_time=hp_min_operation_time,
                                          min_idle_time=hp_min_idle_time,
                                          control=control,
                                          my_simulation_parameters=my_simulation_parameters)
    my_air_conditioner.connect_input(my_air_conditioner.TemperatureOutside,
                                     my_weather.component_name,
                                     my_weather.TemperatureOutside)   
    my_air_conditioner.connect_input(my_air_conditioner.TemperatureMean,
                                     my_building.component_name,
                                     my_building.TemperatureMean)
    my_sim.add_component(my_air_conditioner) 
    

    """PID controller """
    if control=="PID":
        
        pid_controller=PIDcontroller.PIDController(my_simulation_parameters=my_simulation_parameters,ki=ki, kp=kp, kd=kd, my_simulation_repository = my_sim.simulation_repository)
        pid_controller.connect_input(pid_controller.TemperatureMean,
                                     my_building.component_name,
                                     my_building.TemperatureMean)
        pid_controller.connect_input(pid_controller.HeatFluxThermalMassNode,
                                      my_building.component_name,
                                      my_building.HeatFluxThermalMassNode)
        pid_controller.connect_input(pid_controller.HeatFluxWallNode,
                                      my_building.component_name,
                                      my_building.HeatFluxWallNode)
        my_air_conditioner.connect_input(my_air_conditioner.FeedForwardSignal,
                                          pid_controller.component_name,
                                          pid_controller.FeedForwardSignal)
        my_air_conditioner.connect_input(my_air_conditioner.ElectricityOutputPID,
                                         pid_controller.component_name,
                                         pid_controller.ElectricityOutputPID)
        my_sim.add_component(pid_controller)
        
    """Air conditioner on-off controller"""
        
    if control=="on_off":
        my_air_conditioner_controller=air_conditioner.AirConditionercontroller(t_air_heating=t_air_heating,
                                                                               t_air_cooling=t_air_cooling,
                                                                               offset=offset,
                                                                               my_simulation_parameters=my_simulation_parameters)
        my_air_conditioner_controller.connect_input(my_air_conditioner_controller.TemperatureMean,
                                                    my_building.component_name,
                                                    my_building.TemperatureMean)
        my_sim.add_component(my_air_conditioner_controller)
    
        my_air_conditioner.connect_input(my_air_conditioner.State,
                                         my_air_conditioner_controller.component_name,
                                         my_air_conditioner_controller.State)

       
      
    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_air_conditioner.component_name,
                              my_air_conditioner.ThermalEnergyDelivered)   
    
    
    
    

if __name__ == "__main__":
    y = np.logspace(-2, 3, num=5)
    # gc.set_debug(gc.DEBUG_LEAK)
    kp = 1
    kd = 1
    #sum1 = summary.summarize(muppy.get_objects())
    #summary.print_(sum1)
    for ki in y:
        #tr = tracker.SummaryTracker()
        for kp in y:
           for kd in y:
                #keystr= "ki_" + f"{ki:.2f}" + "_kp_" + f"{kp:.2f}" + "_kd_" + f"{kd:.2f}"
                my_sim: Simulator = Simulator(module_directory="ac_test_5",
                                  setup_function="household_AC_explicit",
                                  my_simulation_parameters=None )

                household_AC_explicit(my_sim, ki=ki, kp = kp, kd = kd)

                my_sim.run_all_timesteps()

                del my_sim
                gc.collect()
                # sum2 = summary.summarize(muppy.get_objects())
                # summary.print_(sum2)
                # diff = summary.get_diff(sum1, sum2)
                # summary.print_(diff)
                #print(gc.get_count())
                #objgraph.show_most_common_types(limit=30)
                #obj = objgraph.by_type('dict')
                #objgraph.show_backrefs([obj], max_depth=30)
                #tr.print_diff()
                #objgraph.show_backrefs([obj], max_depth=10)

    #for kd = 0
