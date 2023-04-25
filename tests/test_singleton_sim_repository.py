"""Test for building module."""

# clean

import datetime
import time
import pytest

from hisim import component
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft


@pytest.mark.base
@utils.measure_execution_time
def test_singleton_sim_repository():
    """Test function for the singleton sim module."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    repo1 = component.SimRepository()
    repo2 = component.SingletonSimRepository(test_value="test")

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy.i_prepare_simulation()

    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo1)
    my_weather.i_prepare_simulation()


    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )

    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_residence.i_prepare_simulation()

    # Fake power delivered
    thermal_power_delivered_output = component.ComponentOutput(
        "FakeThermalDeliveryMachine",
        "ThermalDelivery",
        LoadTypes.HEATING,
        Units.WATT,
    )


    number_of_outputs = fft.get_number_of_outputs(
        [my_occupancy, my_weather, my_residence, thermal_power_delivered_output]
    )
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )
    my_residence.temperature_outside_channel.source_output = (
        my_weather.air_temperature_output
    )
    my_residence.altitude_channel.source_output = my_weather.altitude_output
    my_residence.azimuth_channel.source_output = my_weather.azimuth_output
    my_residence.direct_normal_irradiance_channel.source_output = (
        my_weather.DNI_output
    )
    my_residence.direct_horizontal_irradiance_channel.source_output = (
        my_weather.DHI_output
    )
    my_residence.occupancy_heat_gain_channel.source_output = (
        my_occupancy.heating_by_residentsC
    )
    my_residence.thermal_power_delivered_channel.source_output = (
        thermal_power_delivered_output
    )

    fft.add_global_index_of_components(
        [my_occupancy, my_weather, my_residence, thermal_power_delivered_output]
    )

    my_occupancy.i_simulate(0, stsv, False)
    my_weather.i_simulate(0, stsv, False)
    my_residence.i_simulate(0, stsv, False)

    repo4 = component.SingletonSimRepository(test_value="test_new")

    log.information("Singleton Sim Repository Dict " + str(repo2.my_dict))

    # test if sim repository is singleton with same test_value
    print(repo2.test_value, repo4.test_value)
    assert repo2.test_value == repo4.test_value

