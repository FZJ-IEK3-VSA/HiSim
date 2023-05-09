"""Test for scalability in building and occupancy.

The aim is to test whether the occupancy outputs scale with the number of apartments of the building.
"""
# clean
from typing import Any, List, Tuple
import pytest

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
def test_occupancy_scalability():
    """Test function for the scability of the occupancy."""

    # calculate occupancy outputs and respective scaling factor when
    # the abs. cond. floor area is scaled with factor=1
    (
        original_occupancy_outputs,
        scaling_factor_apartments_for_area_factor_one,
    ) = simulation_for_one_time_step(
        scaling_factor_for_absolute_conditioned_floor_area=1
    )
    log.information(
        str(original_occupancy_outputs) + " " + str(scaling_factor_apartments_for_area_factor_one)
    )
    # calculate occupancy outputs and respective scaling factor when
    # the abs. cond. floor area is scaled with factor=3
    (
        scaled_occupancy_outputs,
        scaling_factor_apartments_for_area_factor_three,
    ) = simulation_for_one_time_step(
        scaling_factor_for_absolute_conditioned_floor_area=3
    )
    log.information(
        str(scaled_occupancy_outputs) + " " + str(scaling_factor_apartments_for_area_factor_three)
    )
    # now compare the two results and test if occupancy outputs are upscaled correctly
    assert scaled_occupancy_outputs == [
        output * scaling_factor_apartments_for_area_factor_three for output in original_occupancy_outputs
    ]


def simulation_for_one_time_step(
    scaling_factor_for_absolute_conditioned_floor_area: int,
) -> Tuple[List[float], Any]:
    """Simulate for one timestep and one scaling factor for the floor area."""
    # Sets inputs
    absolute_conditioned_floor_area_in_m2 = 121.2
    number_of_apartments = None
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    building_conditioned_floor_area_in_m2_scaled = (
        scaling_factor_for_absolute_conditioned_floor_area
        * absolute_conditioned_floor_area_in_m2
    )

    repo = component.SimRepository()

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
        building_conditioned_floor_area_in_m2_scaled
    )
    my_residence_config.number_of_apartments = number_of_apartments
    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    log.information("Set Building Code " + my_residence_config.building_code)
    log.information(
        "Set Building Config Number of Apartments "
        + str(my_residence_config.number_of_apartments)
    )
    log.information(
        "Set Building Config Absolute Conditioned Floor Area: "
        + str(my_residence_config.absolute_conditioned_floor_area_in_m2)
    )

    building_number_of_apartments = my_residence.number_of_apartments

    # Set Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    )
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )

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
        [my_weather, my_residence, my_occupancy]
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

    fft.add_global_index_of_components([my_weather, my_residence, my_occupancy])

    log.information("Seconds per Timestep: " + str(seconds_per_timestep))
    log.information(
        "Building Conditioned Floor Area "
        + str(building_conditioned_floor_area_in_m2_scaled)
    )
    log.information(
        "Building Number of apartments " + str(building_number_of_apartments)
    )

    my_residence.seconds_per_timestep = seconds_per_timestep

    # First simulation

    my_weather.i_simulate(1, stsv, False)
    my_residence.i_simulate(1, stsv, False)
    my_occupancy.i_simulate(1, stsv, False)

    scaling_factor_number_of_apartments = (
        building_number_of_apartments
    )

    occupancy_outputs = stsv.values[-4:]

    return occupancy_outputs, scaling_factor_number_of_apartments
