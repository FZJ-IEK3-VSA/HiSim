"""Test for scalability in building module.

The aim is to make the building module scalable via a factor which is the absolute conditioned floor area (or total base area)
divided by the conditioned floor area given by TABULA.
The window areas are scaled via the ratio of window area to wall area.
"""
# clean
import numpy as np
import pytest
from hisim import component
from hisim.components import loadprofilegenerator_utsp_connector
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
    absolute_conditioned_floor_area_in_m2 = 121.2
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    repo = component.SimRepository()

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
    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
        absolute_conditioned_floor_area_in_m2
    )
    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_residence.set_sim_repo(repo)
    my_residence.i_prepare_simulation()

    log.information(my_residence_config.building_code)

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    my_occupancy.set_sim_repo(repo)
    my_occupancy.i_prepare_simulation()

    # Set Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

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
    my_residence.direct_normal_irradiance_channel.source_output = my_weather.dni_output
    my_residence.direct_horizontal_irradiance_channel.source_output = (
        my_weather.dhi_output
    )
    my_residence.occupancy_heat_gain_channel.source_output = (
        my_occupancy.heating_by_residents_channel
    )

    fft.add_global_index_of_components([my_occupancy, my_weather, my_residence])

    log.information("Seconds per Timestep: " + str(seconds_per_timestep))
    log.information(
        "Absolute conditioned floor area without scaling "
        + str(absolute_conditioned_floor_area_in_m2)
        + "\n"
    )

    my_residence.seconds_per_timestep = seconds_per_timestep

    # Simulates
    my_occupancy.i_simulate(0, stsv, False)
    my_weather.i_simulate(0, stsv, False)
    my_residence.i_simulate(0, stsv, False)

    # some variables to test
    opaque_surfaces_without_scaling = [my_residence.my_building_information.facade_area_in_m2,
                                       my_residence.my_building_information.roof_area_in_m2,
                                       my_residence.my_building_information.floor_area_in_m2]

    window_and_door_surfaces_without_scaling = [my_residence.my_building_information.window_area_in_m2,
                                                my_residence.my_building_information.door_area_in_m2]

    window_areas_without_scaling = my_residence.my_building_information.scaled_window_areas_in_m2

    log.information(str(window_areas_without_scaling))

    # check building test with different absolute conditioned floor areas
    scaling_factors = [1, 5, 12]
    for factor in scaling_factors:
        absolute_conditioned_floor_area_in_m2_scaled = (
            factor * absolute_conditioned_floor_area_in_m2
        )
        log.information(
            "Absolute conditioned floor area "
            + str(factor)
            + " times upscaled: "
            + str(absolute_conditioned_floor_area_in_m2_scaled)
        )
        my_residence_config.absolute_conditioned_floor_area_in_m2 = (
            absolute_conditioned_floor_area_in_m2_scaled
        )
        my_residence = building.Building(
            config=my_residence_config,
            my_simulation_parameters=my_simulation_parameters,
        )
        my_residence.set_sim_repo(repo)
        my_residence.i_prepare_simulation()
        my_residence.i_simulate(0, stsv, False)

        opaque_surfaces_with_scaling = [my_residence.my_building_information.facade_area_in_m2,
                                       my_residence.my_building_information.roof_area_in_m2,
                                       my_residence.my_building_information.floor_area_in_m2]

        window_and_door_surfaces_with_scaling = [my_residence.my_building_information.window_area_in_m2,
                                                my_residence.my_building_information.door_area_in_m2,]

        # scaled_window_area = window_area / total_wall_area * scaled_total_wall_area
        # = window_area / (4 * sqrt(conditioned_floor_area) * room_height) * 4 * sqrt(scaled_conditioned_floor_area) * room_height
        # = window_area * sqrt(scaled_conditioned_floor_area / conditioned_floor_area)
        scaling_factor_for_window_areas = my_residence.my_building_information.window_scaling_factor

        window_areas_with_scaling = [
            x * scaling_factor_for_window_areas for x in window_areas_without_scaling
        ]
        log.information(
            "Opaque surface areas "
            + str(factor)
            + " times upscaled: "
            + str(opaque_surfaces_with_scaling)
            + "\n"
        )
        log.information(
            "window areas "
            + str(factor)
            + " times upscaled: "
            + str(window_areas_with_scaling)
            + "\n"
        )

        # test if opaque envelope surface areas of building scale with conditioned floor area
        np.testing.assert_allclose(
            [x * factor for x in opaque_surfaces_without_scaling],
            opaque_surfaces_with_scaling,
            rtol=0.01,
        )

        # test if window and door envelope surface areas of building scale with conditioned floor area
        np.testing.assert_allclose(
            [x * factor for x in window_and_door_surfaces_without_scaling],
            window_and_door_surfaces_with_scaling,
            rtol=0.01,
        )

        # test if window areas of building scale with conditioned floor area
        np.testing.assert_allclose(
            my_residence.my_building_information.scaled_window_areas_in_m2,
            window_areas_with_scaling,
            rtol=0.01,
        )
