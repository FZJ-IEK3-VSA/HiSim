from typing import Optional
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim.components import building
from hisim.components import advanced_battery
from hisim.components import controller_l2_energy_management_system
from hisim.components import generic_hot_water_storage
from hisim.components import generic_gas_heater
from hisim.components.building import Building

from hisim import utils
from hisim import loadtypes as lt
import os
import numpy as np
import pandas as pd

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"
def calculate_max_massflow_heat_storage(building_code:str,
                                        initial_temperature:float,
                                        t_out_min:float,
                                        floor_area):

    df = pd.read_csv(utils.HISIMPATH["housing"],
                     decimal=",",
                     sep=";",
                     encoding="cp1252",
                     low_memory=False)
    buildingdata = df.loc[df["Code_BuildingVariant"] == building_code]
    if floor_area is None:
        max_thermal_building_demand = (
                                              buildingdata["h_Transmission"].values[0] +
                                              buildingdata["h_Ventilation"].values[
                                                  0]) * (initial_temperature - t_out_min)*buildingdata["A_C_Ref"].values[0]
    else:
        max_thermal_building_demand = (buildingdata["h_Transmission"].values[0] +
                                       buildingdata["h_Ventilation"].values[
                                           0]) * (initial_temperature - t_out_min) * floor_area
    max_mass_flow_heat_storage=max_thermal_building_demand/(4.1851*1000*(45-19))
    return max_mass_flow_heat_storage

def basic_household_explicit_gernic_building_size(my_sim, my_simulation_parameters: Optional[SimulationParameters] = None):
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
    seconds_per_timestep = 60*15

    # Set weather
    location = "Aachen"

    # Set photovoltaic system
    time = 2019
    power = 10E3
    load_module_data = False
    module_name = "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrateInverter = True
    inverter_name = "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"

    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"

    # Set occupancy
    occupancy_profile = "CH01"

    # Controller
    strategy = "optimize_own_consumption"
    floor_area=150
    initial_temperature=22
    t_out_min=-14

    # Building
    minimal_building_temperature=20
    # Heat Storage
    max_mass_flow_heat_storage=calculate_max_massflow_heat_storage(building_code=building_code,
                                        floor_area=floor_area,
                                        initial_temperature=initial_temperature,
                                        t_out_min=t_out_min)

    ##### Build Components #####

    # Build system parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)
    my_sim.SimulationParameters = my_simulation_parameters

    # Build occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(profile_name=occupancy_profile, my_simulation_parameters=my_simulation_parameters)

    # Build Weather
    my_weather = weather.Weather(location=location, my_simulation_parameters= my_simulation_parameters)

    # Build Gas Heater
    my_gas_heater = generic_gas_heater.GasHeater(my_simulation_parameters=my_simulation_parameters)

    # Build Building
    my_building = building.Building(my_simulation_parameters= my_simulation_parameters)
    my_building_controller = building.BuildingController(my_simulation_parameters= my_simulation_parameters,
                                                         minimal_building_temperature=minimal_building_temperature)

    # Build Storage
    my_storage = generic_hot_water_storage.HeatStorage(my_simulation_parameters=my_simulation_parameters,
                                                       max_mass_flow_heat_storage=max_mass_flow_heat_storage)
    # Build Controller
    my_controller_heat= controller_l2_energy_management_system.ControllerHeat( my_simulation_parameters= my_simulation_parameters)

    my_building.connect_only_predefined_connections( my_weather, my_occupancy )


    my_storage.connect_input(my_storage.ThermalDemandHeatingWater,
                              my_building_controller.ComponentName,
                              my_building_controller.RealHeatPowerToBuilding)
    my_storage.connect_input(my_storage.ControlSignalChooseStorage,
                              my_controller_heat.ComponentName,
                              my_controller_heat.ControlSignalChooseStorage)

    my_building_controller.connect_input(my_building_controller.MaximalHeatingForBuilding,
                              my_storage.ComponentName,
                              my_storage.MaximalHeatingForBuilding)
    my_building_controller.connect_input(my_building_controller.ResidenceTemperature,
                              my_building.ComponentName,
                              my_building.TemperatureMean)
    my_building.connect_input(my_building.ThermalEnergyDelivered,
                              my_building_controller.ComponentName,
                              my_building_controller.RealHeatPowerToBuilding)

    my_controller_heat.connect_input(my_controller_heat.StorageTemperatureHeatingWater,
                              my_storage.ComponentName,
                              my_storage.WaterOutputTemperatureHeatingWater)

    my_controller_heat.connect_input(my_controller_heat.ResidenceTemperature,
                              my_building.ComponentName,
                              my_building.TemperatureMean)

    my_gas_heater.connect_input(my_gas_heater.ControlSignal,
                              my_controller_heat.ComponentName,
                              my_controller_heat.ControlSignalGasHeater)
    my_gas_heater.connect_input(my_gas_heater.MassflowInputTemperature,
                              my_storage.ComponentName,
                              my_storage.WaterOutputStorageforHeaters)
    my_storage.connect_input(my_storage.ThermalInputPower1,
                              my_gas_heater.ComponentName,
                              my_gas_heater.ThermalOutputPower)

    my_sim.add_component(my_building_controller)
    my_sim.add_component(my_controller_heat)
    my_sim.add_component(my_storage)
    my_sim.add_component(my_gas_heater)
    my_sim.add_component(my_building)
    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
