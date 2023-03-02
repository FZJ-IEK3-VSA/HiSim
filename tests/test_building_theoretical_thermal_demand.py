"""Test for heat demand calculation in the building module."""

# clean
import os
from typing import Optional
import numpy as np

import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import fake_heater


__authors__ = "Katharina Rieck, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../examples/household_for_test_building_theoretical_heat_demand.py"
FUNC = "house_with_fake_heater_for_heating_test"


def test_house_with_fake_heater_for_heating_test(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Test for heating energy demand.

    This setup function emulates an household including the basic components. Here the residents have their
    heating needs covered by a fake heater that returns exactly the heat that the building needs.

    - Simulation Parameters
    - Components
        - Occupancy (Residents' Demands)
        - Weather
        - Building
        - Fake Heater
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    occupancy_profile = "CH01"

    # Set Fake Heater
    set_heating_temperature_for_building_in_celsius = 20
    set_cooling_temperature_for_building_in_celsius = 22

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.full_year(
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
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )

    # Build Fake Heater
    my_fake_heater = fake_heater.FakeHeater(
        my_simulation_parameters=my_simulation_parameters,
        set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius
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
        my_fake_heater.component_name,
        my_fake_heater.ThermalPowerDelivered,
    )
    my_building.connect_input(
        my_building.SetHeatingTemperature,
        my_fake_heater.component_name,
        my_fake_heater.SetHeatingTemperatureForBuilding,
    )
    my_building.connect_input(
        my_building.SetCoolingTemperature,
        my_fake_heater.component_name,
        my_fake_heater.SetCoolingTemperatureForBuilding,
    )

    # Fake Heater
    my_fake_heater.connect_input(
        my_fake_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_fake_heater)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Test Air Temperature of Building

    building_indoor_air_temperatures = my_sim.results_data_frame[
        "Building_1 - TemperatureIndoorAir [Temperature - °C]"
    ]

    for air_temperature in building_indoor_air_temperatures.values:
        # check if air temperature in building is held between set temperatures
        assert (
            my_building.set_heating_temperature_in_celsius
            <= np.round(air_temperature)
            <= my_building.set_cooling_temperature_in_celsius
        )
