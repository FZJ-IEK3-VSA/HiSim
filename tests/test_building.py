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
def test_building():
    """Test function for the building module."""

    starttime = datetime.datetime.now()
    d_four = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Test Building start @ " + d_four)

    t_one = time.perf_counter()

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    repo = component.SimRepository()
    t_two = time.perf_counter()
    log.profile(f"T2: {t_two - t_one}")

    # # check on all TABULA buildings -> run test over all building_codes
    # d_f = pd.read_csv(
    #     utils.HISIMPATH["housing"],
    #     decimal=",",
    #     sep=";",
    #     encoding="cp1252",
    #     low_memory=False,
    # )

    # for building_code in d_f["Code_BuildingVariant"]:
    #     if isinstance(building_code, str):
    #         my_residence_config.building_code = building_code

    #         my_residence = building.Building(
    #             config=my_residence_config, my_simulation_parameters=my_simulation_parameters)
    #         log.information(building_code)

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy.set_sim_repo(repo)
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
    my_weather.set_sim_repo(repo)
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
    my_residence.set_sim_repo(repo)
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

    # Test building models for various time resolutions
    #   -> assume weather and occupancy data from t=0 (time resolution 1 min)
    #   -> calculate temperature of building ( with no heating considered) for varios time steps
    #   -> check if temperature difference is proportional to time step size ( > 0.1 °C per minute)
    t_six = time.perf_counter()
    log.profile(f"T2: {t_six - t_five}")

    for seconds_per_timestep in [60, 60 * 15, 60 * 60]:

        log.trace("Seconds per Timestep: " + str(seconds_per_timestep))
        log.information("Seconds per Timestep: " + str(seconds_per_timestep))

        my_residence.seconds_per_timestep = seconds_per_timestep

        # Simulates
        stsv.values[my_residence.thermal_mass_temperature_channel.global_index] = 23

        my_occupancy.i_simulate(0, stsv, False)
        my_weather.i_simulate(0, stsv, False)
        my_residence.i_simulate(0, stsv, False)

        log.information(
            f"Fake Residence Thermal Power Delivery Output: {stsv.values[0]}"
        )
        log.information(f"Occupancy Outputs: {stsv.values[1:5]}")
        log.information(f"Weather Outputs: {stsv.values[5:14]}")
        log.information(f"Residence Outputs: {stsv.values[14:18]}\n")

        assert (
            stsv.values[my_residence.thermal_mass_temperature_channel.global_index]
            - 23.0
        ) > -0.1 * (seconds_per_timestep / 60)

    t_seven = time.perf_counter()
    log.profile(f"T2: {t_seven - t_six}")
    starttime = datetime.datetime.now()
    d_four = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Finished @ " + d_four)
