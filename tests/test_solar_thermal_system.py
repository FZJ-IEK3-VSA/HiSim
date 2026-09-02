"""Tests for the solar thermal system component."""

import datetime
from typing import Any
import pandas as pd
import pytest
from oemof.thermal.solar_thermal_collector import flat_plate_precalc
from hisim import sim_repository, component, log, simulator as sim
from hisim.components import weather, solar_thermal_system
from hisim.loadtypes import LoadTypes, Units
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_solar_thermal_system() -> None:
    """Verify a SolarThermalSystem produces the expected thermal power on a summer noon step.

    Builds a Weather and SolarThermalSystem with a 4 m² collector, drives them with a
    fake binary control signal at a 3rd-of-July noon timestep, and asserts the
    thermal power output matches the reference value (~3260.38 W).
    """
    # Inputs
    seconds_per_timestep = 60

    repo = sim_repository.SimRepository()
    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Configure weather
    my_weather_config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=mysim)
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Configure solar thermal
    my_sts_config = solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system(area_m2=4)
    my_sts = solar_thermal_system.SolarThermalSystem(config=my_sts_config, my_simulation_parameters=mysim)

    state_controller = component.ComponentOutput(
        "FakeControlState",
        "ControlSignal",
        LoadTypes.ANY,
        Units.BINARY,
        component_id=ComponentID("FakeControlState"),
    )
    my_sts.control_signal_channel.source_output = state_controller

    my_sts.set_sim_repo(repo)
    my_sts.i_prepare_simulation()

    # Outputs
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_sts, state_controller])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(number_of_outputs)

    my_sts.t_out_channel.source_output = my_weather.air_temperature_output
    my_sts.dhi_channel.source_output = my_weather.dhi_output
    my_sts.ghi_channel.source_output = my_weather.ghi_output

    # Simulate
    fft.add_global_index_of_components([my_weather, my_sts, state_controller])
    stsv.values[state_controller.global_index] = 1
    timestep = 12 * 60 + 60 * 24 * 183  # 3rd July at noon
    my_weather.i_simulate(timestep, stsv, False)
    my_sts.i_simulate(timestep, stsv, False)
    log.information(f"heat power output [W]: {stsv.values[my_sts.thermal_power_w_output_channel.global_index]}")
    print(stsv.values)

    assert pytest.approx(stsv.values[my_sts.thermal_power_w_output_channel.global_index]) == 3260.3754293283737


@pytest.mark.base
def test_precalc() -> None:
    """Verify oemof's flat_plate_precalc yields zero heat under zero irradiance.

    Calls flat_plate_precalc with Aachen coordinates, a 30° tilt, and zero
    global/diffuse irradiance at ambient temperature 0 °C, then asserts the
    resulting collector heat output is 0.
    """
    azimuth = (180.0,)
    tilt: float = 30.0
    eta_0: float = 0.78
    a_1_w_m2_k: float = 3.2  # W/(m2*K)
    a_2_w_m2_k: float = 0.015  # W/(m2*K2)
    coordinates = component.Coordinates(latitude_in_degrees=50.78, longitude_in_degrees=6.08)

    temperature_collector_inlet_deg_c = 55
    delta_temperature_n_k = 10

    global_horizontal_irradiance_w_m2 = 0
    diffuse_horizontal_irradiance_w_m2 = 0
    ambient_air_temperature_deg_c = 0
    timestep = 0
    time_ind = datetime.datetime(2021, 1, 1) + datetime.timedelta(0, 60 * timestep)

    precalc_data = flat_plate_precalc(
        lat=coordinates.latitude_in_degrees,
        long=coordinates.longitude_in_degrees,
        collector_tilt=tilt,
        collector_azimuth=azimuth,
        eta_0=eta_0,  # optical efficiency of the collector
        a_1=a_1_w_m2_k,  # thermal loss parameter 1
        a_2=a_2_w_m2_k,  # thermal loss parameter 2
        temp_collector_inlet=temperature_collector_inlet_deg_c,  # collectors inlet temperature
        delta_temp_n=delta_temperature_n_k,  # temperature difference between collector inlet and mean temperature
        irradiance_global=pd.Series(global_horizontal_irradiance_w_m2, index=[time_ind]),
        irradiance_diffuse=pd.Series(diffuse_horizontal_irradiance_w_m2, index=[time_ind]),
        temp_amb=pd.Series(ambient_air_temperature_deg_c, index=[time_ind]),
    )

    assert precalc_data["collectors_heat"].iloc[0] == pytest.approx(0, abs=1e-9)


class WhatMayBeCached:
    """The columns the collector is allowed to remember between runs.

    ``flat_plate_precalc`` computes in three stages and only the first can be known before the run:
    the sun's position follows from the timestamps and the coordinates. The plane-of-array irradiance
    needs the weather, which arrives through wired inputs, and the collector efficiency needs the
    storage's inlet temperature, which is the simulation's own state feeding back.

    This component used to cache the output of all three, including ``eta_c`` and
    ``collectors_heat``. Those are functions of a trajectory the run had not taken yet, so no key
    could describe them and any hit replayed one system's storage behaviour into another's. Pinning
    the column list is what stops a later precompute quietly reacquiring that dependency -- the
    original mistake is easy to make again, because caching more looks like caching better.
    """

    COLUMNS = ("apparent_zenith", "azimuth")


@pytest.mark.base
def test_only_the_sun_is_cached(tmp_path: Any) -> None:
    """The cached artefact holds the solar position and nothing downstream of it."""
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    simulation_parameters.cache_dir_path = str(tmp_path)
    collector = solar_thermal_system.SolarThermalSystem(
        config=solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system(),
        my_simulation_parameters=simulation_parameters,
    )

    collector.i_prepare_simulation()

    assert tuple(collector.solar_position.columns) == WhatMayBeCached.COLUMNS, (
        f"the collector precomputed {tuple(collector.solar_position.columns)}; anything beyond "
        f"{WhatMayBeCached.COLUMNS} depends on the run and cannot be keyed"
    )
    assert len(collector.solar_position) == simulation_parameters.timesteps
    written = list(tmp_path.glob("*.cache"))
    assert len(written) == 1, f"expected one cache file, found {written}"
    assert tuple(pd.read_csv(written[0]).columns) == WhatMayBeCached.COLUMNS


@pytest.mark.base
def test_the_cached_sun_round_trips_exactly(tmp_path: Any) -> None:
    """A cached run and an uncached one must agree bit for bit, so the file keeps full precision.

    The default CSV float format does not round-trip a float64, which is the defect
    roadmap/pylpg_flakiness.md records for the photovoltaic cache: a cached run and an uncached one
    are then not the same run. Seventeen significant digits do round-trip, and this asserts it on
    the values actually written rather than trusting the format string.
    """
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    simulation_parameters.cache_dir_path = str(tmp_path)
    config = solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system()

    computed = solar_thermal_system.SolarThermalSystem(config=config, my_simulation_parameters=simulation_parameters)
    computed.i_prepare_simulation()

    from_cache = solar_thermal_system.SolarThermalSystem(config=config, my_simulation_parameters=simulation_parameters)
    from_cache.i_prepare_simulation()

    for column in WhatMayBeCached.COLUMNS:
        assert list(from_cache.solar_position[column]) == list(computed.solar_position[column]), (
            f"'{column}' changed on the way through the cache, so a cached run is not the same run"
        )


@pytest.mark.base
def test_every_timestep_gets_an_instant() -> None:
    """The timestamps are the cache key's claim about the contents, so they must match the run."""
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    collector = solar_thermal_system.SolarThermalSystem(
        config=solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system(),
        my_simulation_parameters=simulation_parameters,
    )

    timestamps = collector.timestamps_of_the_run()

    assert len(timestamps) == simulation_parameters.timesteps
    assert timestamps[0] == simulation_parameters.start_date
    assert (timestamps[1] - timestamps[0]).total_seconds() == simulation_parameters.seconds_per_timestep
