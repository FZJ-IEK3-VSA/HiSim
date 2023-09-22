"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""
# clean
import os
from typing import Optional
import pytest
import numpy as np

import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.components import idealized_electric_heater
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim import log
from hisim import utils

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2022, FZJ-IEK-3"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "1.0"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"

# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../examples/household_for_test_building_heat_demand.py"
FUNC = "house_with_idealized_electric_heater_for_heating_test"


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_house_with_idealized_electric_heater_for_testing_heating_demand(
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
        - Idealized Electric Heater
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # Set Fake Heater
    set_heating_temperature_for_building_in_celsius = 19.5
    set_cooling_temperature_for_building_in_celsius = 20.5

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
        module_filename="household_for_test_building_heat_demand.py",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # set heating and cooling temperatures for building, because calculation with building temperature default values delivers different result
    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.SETHEATINGTEMPERATUREFORBUILDING, entry=20.0
    )
    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.SETCOOLINGTEMPERATUREFORBUILDING, entry=23.0
    )

    # Build Building
    my_building_config = building.BuildingConfig.get_default_german_single_family_home()
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
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
    # Build Fake Heater Config
    my_idealized_electric_heater_config = idealized_electric_heater.IdealizedHeaterConfig(
        name="IdealizedElectricHeater",
        set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
        set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
    )
    # Build Fake Heater
    my_idealized_electric_heater = idealized_electric_heater.IdealizedElectricHeater(
        my_simulation_parameters=my_simulation_parameters,
        config=my_idealized_electric_heater_config,
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
        my_building.HeatingByDevices,
        my_occupancy.component_name,
        my_occupancy.HeatingByDevices,
    )
    my_building.connect_input(
        my_building.ThermalPowerDelivered,
        my_idealized_electric_heater.component_name,
        my_idealized_electric_heater.ThermalPowerDelivered,
    )

    # Fake Heater
    my_idealized_electric_heater.connect_input(
        my_idealized_electric_heater.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)
    my_sim.add_component(my_idealized_electric_heater)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Calculate annual heat pump heating energy

    results_heating = my_sim.results_data_frame[
        "IdealizedElectricHeater - HeatingPowerDelivered [Heating - W]"
    ]

    sum_heating_in_watt_timestep = sum(results_heating)
    log.information("sum heating [W*timestep] " + str(sum_heating_in_watt_timestep))
    timestep_factor = seconds_per_timestep / 3600
    sum_heating_in_watt_hour = sum_heating_in_watt_timestep * timestep_factor
    sum_heating_in_kilowatt_hour = sum_heating_in_watt_hour / 1000
    # =========================================================================================================================================================
    # Test annual floor related heating demand

    energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2 = (
        my_building.my_building_information.buildingdata["q_h_nd"].values[0]
    )

    energy_need_for_heating_from_idealized_electric_heater_in_kilowatt_hour_per_year_per_m2 = (
        sum_heating_in_kilowatt_hour
        / my_building_config.absolute_conditioned_floor_area_in_m2
    )
    log.information(
        "energy need for heating from tabula [kWh/(a*m2)] "
        + str(energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2)
    )
    log.information(
        "energy need for heating from idealized electric heater [kWh/(a*m2)] "
        + str(
            energy_need_for_heating_from_idealized_electric_heater_in_kilowatt_hour_per_year_per_m2
        )
    )

    # test whether tabula energy demand for heating is equal to energy demand for heating generated from idealized electric heater with a tolerance of 15%
    np.testing.assert_allclose(
        energy_need_for_heating_given_by_tabula_in_kilowatt_hour_per_year_per_m2,
        energy_need_for_heating_from_idealized_electric_heater_in_kilowatt_hour_per_year_per_m2,
        rtol=0.3,
    )
