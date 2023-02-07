"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""
# clean
import os
from typing import Optional
import numpy as np
# import pandas as pd

import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import generic_heat_pump
from hisim import log
# from hisim import utils

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../examples/household_for_test_building_heat_demand.py"
FUNC = "house_with_pv_and_hp_for_heating_test"


def test_house_with_pv_and_hp_for_heating_test(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Test for heating energy demand.

    This setup function emulates an household including the basic components. Here the residents have their
    heating needs covered by the heat pump.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Heat Pump
        - Heat Pump Controller
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    occupancy_profile = "CH01"

    # Set Building
    building_code = "DE.N.SFH.05.Gen.ReEx.001.002"
    building_heat_capacity_class = "medium"
    initial_temperature_in_celsius = 23
    heating_reference_temperature_in_celsius = -14
    absolute_conditioned_floor_area_in_m2 = 218.9
    total_base_area_in_m2 = None

    # Set Heat Pump Controller
    temperature_air_heating_in_celsius = 19.5
    temperature_air_cooling_in_celsius = 20.5
    offset = 0.5
    hp_mode = 2

    # Set Heat Pump
    # hp_min_operation_time = 1
    # hp_min_idle_time = 1

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year_all_options(
            year=year, seconds_per_timestep=seconds_per_timestep
        )


    # # in case ou want to check on all TABULA buildings -> run test over all building_codes
    # d_f = pd.read_csv(
    #     utils.HISIMPATH["housing"],
    #     decimal=",",
    #     sep=";",
    #     encoding="cp1252",
    #     low_memory=False,
    # )

    # for building_code in d_f["Code_BuildingVariant"]:
    #     if isinstance(building_code, str):
    #         log.information("building code " + str(building_code))

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        setup_function=FUNC,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
        profile_name=occupancy_profile, name="Occupancy"
    )
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Building
    my_building_config = building.BuildingConfig(
        building_code=building_code,
        building_heat_capacity_class=building_heat_capacity_class,
        initial_internal_temperature_in_celsius=initial_temperature_in_celsius,
        heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        name="Building1",
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=total_base_area_in_m2,
    )
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(
        config=generic_heat_pump.GenericHeatPumpConfig.get_default_generic_heat_pump_config(),
        my_simulation_parameters=my_simulation_parameters,
    )

    # Build Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(
        config=generic_heat_pump.GenericHeatPumpControllerConfig(
        name="GenericHeatpumpController",
        temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
        temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
        offset=offset,
        mode=hp_mode),
        my_simulation_parameters=my_simulation_parameters,
    )
    # =========================================================================================================================================================
    # Connect Components

    # Building
    my_building.connect_input(
        my_building.Altitude, my_weather.component_name, my_weather.Altitude
    )
    my_building.connect_input(
        my_building.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_building.connect_input(
        my_building.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_building.connect_input(
        my_building.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_building.connect_input(
        my_building.ApparentZenith, my_weather.component_name, my_weather.ApparentZenith
    )
    my_building.connect_input(
        my_building.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_building.connect_input(
        my_building.HeatingByResidents,
        my_occupancy.component_name,
        my_occupancy.HeatingByResidents,
    )
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_heat_pump.component_name,
        my_heat_pump.ThermalPowerDelivered,
    )

    # Heat Pump
    my_heat_pump.connect_input(
        my_heat_pump.State,
        my_heat_pump_controller.component_name,
        my_heat_pump_controller.State,
    )
    my_heat_pump.connect_input(
        my_heat_pump.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )

    # Heat Pump Controller
    my_heat_pump_controller.connect_input(
        my_heat_pump_controller.TemperatureMean,
        my_building.component_name,
        my_building.TemperatureMean,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_heat_pump)
    my_sim.add_component(my_heat_pump_controller)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Calculate annual heat pump heating energy

    results_heatpump_heating = my_sim.results_data_frame[
        "HeatPump - Heating [Heating - W]"
    ]
    sum_heating_in_watt_timestep = sum(results_heatpump_heating)
    log.information("sum hp heating [W*timestep] " + str(sum_heating_in_watt_timestep))
    timestep_factor = seconds_per_timestep / 3600
    sum_heating_in_watt_hour = sum_heating_in_watt_timestep * timestep_factor
    sum_heating_in_kilowatt_hour = sum_heating_in_watt_hour / 1000
    # =========================================================================================================================================================
    # Test annual floor related heating demand

    energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2 = (
        my_building.buildingdata["q_h_nd"].values[0]
    )

    energy_need_for_heating_from_heat_pump_in_kilowatt_hour_per_year_per_m2 = (
        sum_heating_in_kilowatt_hour / absolute_conditioned_floor_area_in_m2
    )
    log.information(
        "energy need for heating from tabula [kWh/(a*m2)] "
        + str(energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2)
    )
    log.information(
        "energy need for heating from heat pump [kWh/(a*m2)] "
        + str(energy_need_for_heating_from_heat_pump_in_kilowatt_hour_per_year_per_m2)
    )
    # test whether tabula energy demand for heating is equal to energy demand for heating generated from heat pump with a tolerance of 30%
    np.testing.assert_allclose(
        energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2,
        energy_need_for_heating_from_heat_pump_in_kilowatt_hour_per_year_per_m2,
        rtol=0.3,
    )
