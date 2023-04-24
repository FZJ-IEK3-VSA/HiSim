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

    starttime = datetime.datetime.now()
    d_four = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Test Singleton Sim Repository start @ " + d_four)

    t_one = time.perf_counter()

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    repo1 = component.SimRepository()
    repo2 = component.SingletonSimRepository(test_value="test")
    t_two = time.perf_counter()
    log.profile(f"T2: {t_two - t_one}")

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy.set_singleton_sim_repo(repo2)
    # my_occupancy.simulation_repository.set_entry(key="Occupancy Number of Residents", entry=my_occupancy.number_of_residents[0])
    my_occupancy.singleton_simulation_repository.set_entry(key="Occupancy Number of Residents", entry=my_occupancy.number_of_residents[0])
    my_occupancy.i_prepare_simulation()
    t_three = time.perf_counter()
    log.profile(f"T2:{t_three - t_two}")

    # Set Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo1)
    my_weather.set_singleton_sim_repo(repo2)
    my_weather.singleton_simulation_repository.set_entry(key="Weather component name ", entry=my_weather.component_name)
    my_weather.i_prepare_simulation()
    t_four = time.perf_counter()
    log.profile(f"T2: {t_four - t_three}")

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )

    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_residence.set_singleton_sim_repo(repo2)
    my_residence.singleton_simulation_repository.set_entry(key="Building Number of apartment ", entry=my_residence.number_of_apartments)
    my_residence.i_prepare_simulation()

    # Fake power delivered
    thermal_power_delivered_output = component.ComponentOutput(
        "FakeThermalDeliveryMachine",
        "ThermalDelivery",
        LoadTypes.HEATING,
        Units.WATT,
    )
    t_five = time.perf_counter()
    log.profile(f"T2: {t_four - t_five}")

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

    log.information("sim repos before simu " + str(repo2.my_dict))
    my_occupancy.i_simulate(0, stsv, False)
    my_weather.i_simulate(0, stsv, False)
    my_residence.i_simulate(0, stsv, False)
    my_residence.singleton_simulation_repository.set_entry(key="Building Number of apartment ", entry=2)
    log.information("sim repos after simu " + str(repo2.my_dict))
    t_six = time.perf_counter()
    log.profile(f"T2: {t_six - t_five}")

    t_seven = time.perf_counter()
    log.profile(f"T2: {t_seven - t_six}")
    starttime = datetime.datetime.now()
    d_four = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Finished @ " + d_four)

    repo3 = component.SimRepository()
    repo4 = component.SingletonSimRepository(test_value="test_new")
    my_residence.set_sim_repo(repo4)
    my_residence.singleton_simulation_repository.set_entry(key="Building Number of apartment ", entry=4)
    log.information("sim repos after simu " + repo4.test_value)