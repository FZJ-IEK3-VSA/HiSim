"""Test for scalability in building and occupancy.

The aim is to test whether the occupancy outputs scale with the number of apartments of the building.
"""
# clean
import pytest
import pandas as pd
from hisim import component
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_building_scalability():
    """Test function for the building module."""

    # Sets inputs
    absolute_conditioned_floor_area_in_m2 = None
    number_of_apartments = None
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    repo = component.SimRepository()

    # Set Residence
    my_residence_config = (
    building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
    absolute_conditioned_floor_area_in_m2
    )
    my_residence_config.number_of_apartments = (
    number_of_apartments
    )
    my_residence = building.Building(
    config=my_residence_config,
    my_simulation_parameters=my_simulation_parameters,
    )
    my_residence.set_sim_repo(repo)
    my_residence.i_prepare_simulation()

    log.information("Building Code " + my_residence_config.building_code)
    log.information("Building Config Number of Apartments " + str(my_residence_config.number_of_apartments))
    log.information("Building Config Absolute Conditioned Floor Area: " + str(my_residence_config.absolute_conditioned_floor_area_in_m2))

    building_conditioned_floor_area_in_m2 = my_residence.scaled_conditioned_floor_area_in_m2
    building_number_of_apartments = my_residence.number_of_apartments

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    my_occupancy = loadprofilegenerator_connector.Occupancy(
    config=my_occupancy_config,
    my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy.set_sim_repo(repo)
    my_occupancy.i_prepare_simulation()

    # Set Weather
    my_weather_config = weather.WeatherConfig.get_default(
    location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
    config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Set inputs
    number_of_outputs = fft.get_number_of_outputs(
    [my_occupancy, my_weather, my_residence]
    )
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
    number_of_outputs
    )
    my_residence.temperature_outside_channel.source_output = (
    my_weather.air_temperature_output
    )
    my_residence.altitude_channel.source_output = my_weather.altitude_output
    my_residence.azimuth_channel.source_output = my_weather.azimuth_output
    my_residence.direct_normal_irradiance_channel.source_output = my_weather.DNI_output
    my_residence.direct_horizontal_irradiance_channel.source_output = (
    my_weather.DHI_output
    )
    my_residence.occupancy_heat_gain_channel.source_output = (
    my_occupancy.heating_by_residentsC
    )
    my_occupancy.real_number_of_apartments_channel.source_output = my_residence.number_of_apartments_channel

    fft.add_global_index_of_components([my_occupancy, my_weather, my_residence])

    log.information("Seconds per Timestep: " + str(seconds_per_timestep))
    log.information(
    "Building Conditioned Floor Area "
    + str(building_conditioned_floor_area_in_m2)
    )
    log.information(
    "Building Number of apartments "
    + str(building_number_of_apartments)
    + "\n"
    )


    my_residence.seconds_per_timestep = seconds_per_timestep

    # Simulates

    my_weather.i_simulate(0, stsv, False)
    my_residence.i_simulate(0, stsv, False)
    my_occupancy.i_simulate(0, stsv, False)

    occupancy_outputs = stsv.values[:4]
    log.information("Occupancy Config Number of Apartments " + str(my_occupancy_config.number_of_apartments))
    log.information("Occupancy Outputs: " + str(occupancy_outputs))

    # check for different absolute conditioned floor areas
    scaling_factors = [1, 5, 10]
    for factor in scaling_factors:
        building_conditioned_floor_area_in_m2_scaled = (
            factor * building_conditioned_floor_area_in_m2
        )
        log.information(
            "Building conditioned floor area "
            + str(factor)
            + " times upscaled: "
            + str(building_conditioned_floor_area_in_m2_scaled)
        )
        my_residence_config.absolute_conditioned_floor_area_in_m2 = building_conditioned_floor_area_in_m2_scaled
        my_residence = building.Building(
            config=my_residence_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        my_weather.i_simulate(0, stsv, False)
        my_residence.i_simulate(0, stsv, False)
        my_occupancy.i_simulate(0, stsv, False)

        number_of_apartments_building_upscaled = my_residence.number_of_apartments
        scaling_factor_number_of_apartments = number_of_apartments_building_upscaled / my_occupancy_config.number_of_apartments
        log.information("Scaling factor apartments " + str(scaling_factor_number_of_apartments))
        occupancy_outputs_upscaled = stsv.values[0:4]

        log.information(
            "Number of apartments in building upscaled "
            + str(number_of_apartments_building_upscaled)
        )


        log.information(
            "Occupancy outputs upscaled "
            + str(occupancy_outputs_upscaled)
            + "\n"
        )

        # test if occupancy outputs are upscaled correctly
        assert occupancy_outputs_upscaled == [x * scaling_factor_number_of_apartments for x in occupancy_outputs]
