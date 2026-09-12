"""Test for generic pv system."""

import os

import pytest
from tests import functions_for_testing as fft
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim import simulator as sim
from hisim import log


def _run_pv_at_timestep_655(
    pvs_config: "generic_pv_system.PVSystemConfig",
    expected_power_w: float,
    seconds_per_timestep: int = 60,
) -> None:
    """Build a PVSystem + Aachen weather harness and assert power/energy at ts 655.

    Constructs the shared SimRepository, full-year SimulationParameters, Aachen
    Weather, and PVSystem from ``pvs_config``; wires the eight weather outputs to
    the PV system's inputs; assigns global indices; simulates timestep 655 with
    the weather component running before the PV system (see KB-4375); and asserts
    that the electricity output matches ``expected_power_w`` and that the energy
    output equals the power times the timestep length in hours.

    Args:
        pvs_config: A fully configured PVSystemConfig (including ``power_in_watt``).
        expected_power_w: Expected electricity output in watts at timestep 655.
        seconds_per_timestep: Simulation timestep length in seconds.

    Returns:
        None
    """
    repo = sim_repository.SimRepository()

    mysim = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Weather: 6 outputs
    # PVS:  1 output

    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    pvs_config.weather_identity = my_weather_config.identity()
    my_pvs = generic_pv_system.PVSystem(
        config=pvs_config, my_simulation_parameters=mysim
    )
    my_pvs.set_sim_repo(repo)
    my_pvs.i_prepare_simulation()
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_pvs])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    my_pvs.t_out_channel.source_output = my_weather.air_temperature_output
    my_pvs.azimuth_channel.source_output = my_weather.azimuth_output
    my_pvs.dni_channel.source_output = my_weather.dni_output
    my_pvs.dni_extra_channel.source_output = my_weather.dni_extra_output
    my_pvs.dhi_channel.source_output = my_weather.dhi_output
    my_pvs.ghi_channel.source_output = my_weather.ghi_output
    my_pvs.apparent_zenith_channel.source_output = (
        my_weather.apparent_zenith_output
    )
    my_pvs.wind_speed_channel.source_output = my_weather.wind_speed_output

    fft.add_global_index_of_components([my_weather, my_pvs])

    timestep = 655
    my_weather.i_simulate(timestep, stsv, False)
    my_pvs.i_simulate(timestep, stsv, False)
    log.information(
        f"pv electricity output [W]: {stsv.values[my_pvs.electricity_output_channel.global_index]}"
    )
    log.information(
        f"pv electricity energy output [Wh]: {stsv.values[my_pvs.electricity_energy_output_channel.global_index]}"
    )

    # check pv electricity output [W] in timestep 655
    assert (
        pytest.approx(
            stsv.values[my_pvs.electricity_output_channel.global_index]
        )
        == expected_power_w
    )

    # Check pv energy output channel [Wh] which should be the electricity
    # output in W times the timestep length in hours
    assert pytest.approx(
        stsv.values[my_pvs.electricity_energy_output_channel.global_index]
    ) == expected_power_w * (seconds_per_timestep / 3600)


@pytest.mark.extendedbase
def test_photovoltaic_sandia() -> None:
    """Test the generic PV system with the SANDIA module/inverter databases.

    Configures a PVSystem using a Hanwha HSL60P6-PA-4-250T module and an ABB
    MICRO-0.25 inverter from the SANDIA databases, wires it to an Aachen weather
    component, and asserts the electricity output (~334.88 W) and energy output
    at timestep 655.
    """
    my_pvs_config = generic_pv_system.PVSystemConfig.get_default_pv_system(
        module_name="Hanwha HSL60P6-PA-4-250T [2013]",
        module_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,  # noqa: E501
        inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
        inverter_database=generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,  # noqa: E501
    )
    my_pvs_config.power_in_watt = 10 * 1e3
    _run_pv_at_timestep_655(
        pvs_config=my_pvs_config, expected_power_w=334.8800144821672
    )


@pytest.mark.extendedbase
def test_photovoltaic_cec() -> None:
    """Test the generic PV system with the default CEC module/inverter databases.

    Configures a PVSystem using the default PVLib module and inverter parameters,
    wires it to an Aachen weather component, and asserts the electricity output
    (~340.55 W) and energy output at timestep 655.
    """
    my_pvs_config = generic_pv_system.PVSystemConfig.get_default_pv_system()
    my_pvs_config.power_in_watt = 10 * 1e3
    _run_pv_at_timestep_655(
        pvs_config=my_pvs_config, expected_power_w=340.552602382255
    )


@pytest.mark.extendedbase
def test_photovoltaic_cache_roundtrip(tmp_path) -> None:
    """Test that the PV cache is written at prepare time and read back identically.

    Builds an Aachen weather component and a default (CEC) PV system with an
    isolated cache directory, runs both i_prepare_simulation calls, and asserts
    that the PV cache file exists immediately after preparation — before a
    single i_simulate call — so that even interrupted simulations populate the
    cache. Then prepares a second PV system with the identical configuration
    and asserts that it takes the cache-hit path and yields the same
    per-timestep AC power ratios as the freshly computed run (up to the
    precision of the CSV text serialization of the cache file).
    """
    my_sim_params = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=3600
    )
    my_sim_params.cache_dir_path = str(tmp_path)

    repo = sim_repository.SimRepository()
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_sim_params
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    my_pvs_config = generic_pv_system.PVSystemConfig.get_default_pv_system()
    my_pvs_config.weather_identity = my_weather_config.identity()
    my_pvs = generic_pv_system.PVSystem(
        config=my_pvs_config, my_simulation_parameters=my_sim_params
    )
    my_pvs.set_sim_repo(repo)

    # The entry the run will look for, derived the way the component derives it: from the producer's
    # code and inputs (roadmap/cache_service_spec.md §3), the weather's artifact key among them.
    entry = my_pvs.cache_entry(my_pvs.build_calculation_inputs())
    assert not entry.exists, "The isolated cache directory must start out empty."

    my_pvs.i_prepare_simulation()

    # The cache must be written during preparation, not at the end of the
    # simulation loop, so that interrupted runs still populate it.
    assert os.path.exists(entry.path)
    assert (
        len(my_pvs.ac_power_ratios_for_all_timesteps_output)
        == my_sim_params.timesteps
    )

    my_pvs_config.weather_identity = my_weather_config.identity()
    my_pvs_cached = generic_pv_system.PVSystem(
        config=my_pvs_config, my_simulation_parameters=my_sim_params
    )
    my_pvs_cached.set_sim_repo(repo)
    my_pvs_cached.i_prepare_simulation()

    assert my_pvs_cached.ac_power_ratios_for_all_timesteps_output == pytest.approx(
        my_pvs.ac_power_ratios_for_all_timesteps_output
    )


@pytest.mark.base
def test_scaled_pv_system_records_the_share_it_applied() -> None:
    """Test that a rooftop-scaled config states the share it was sized with, applied exactly once.

    ``get_scaled_pv_system`` applies ``share_of_maximum_pv_potential`` inside ``size_pv_system`` and
    deliberately does not pass it on to ``get_default_pv_system``, which would multiply the power by
    it a second time. The share is stamped onto the finished config afterwards instead, so this test
    asserts both halves: a config scaled at half the rooftop potential carries ``0.5`` rather than
    the ``1.0`` default of ``get_default_pv_system``, and its power is still the singly-applied
    power - exactly what ``size_pv_system`` returns for the same share, and half (up to the two
    decimals that function rounds to) of the power the same rooftop yields at a share of ``1.0``.
    """
    rooftop_area_in_m2 = 120.0
    module_name = "Trina Solar TSM-435NE09RC.05"
    module_database = generic_pv_system.PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE

    half_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=rooftop_area_in_m2,
        share_of_maximum_pv_potential=0.5,
        module_name=module_name,
        module_database=module_database,
    )
    full_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=rooftop_area_in_m2,
        share_of_maximum_pv_potential=1.0,
        module_name=module_name,
        module_database=module_database,
    )

    assert half_config.share_of_maximum_pv_potential == 0.5
    assert full_config.share_of_maximum_pv_potential == 1.0

    expected_power_in_watt = generic_pv_system.PVSystemConfig.size_pv_system(
        rooftop_area_in_m2=rooftop_area_in_m2,
        share_of_maximum_pv_potential=0.5,
        module_name=module_name,
        module_database=module_database,
    )
    assert half_config.power_in_watt == expected_power_in_watt
    assert half_config.power_in_watt == pytest.approx(full_config.power_in_watt / 2, abs=0.01)


@pytest.mark.base
def test_a_scaled_pv_record_re_executes_to_the_same_power() -> None:
    """Catches a scaled array whose own record would rebuild a differently sized array.

    A share is only worth recording if the record it lands in reproduces the run it describes.
    The share must survive because it is the provenance, and the power must survive *unscaled*,
    because the block already carries the scaled number: a reader that applied the recorded share
    to the recorded power a second time would halve a half-share array on every re-run.
    """
    scaled_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=120.0,
        share_of_maximum_pv_potential=0.5,
    )

    re_executed = fft.round_trip_config_block(
        scaled_config, generic_pv_system.PVSystemConfig, "PVSystem"
    )

    assert re_executed.share_of_maximum_pv_potential == 0.5
    assert re_executed.power_in_watt == scaled_config.power_in_watt
    assert re_executed == scaled_config


@pytest.mark.base
def test_the_default_pv_factory_reads_its_power_argument_as_the_unscaled_maximum() -> None:
    """Pins the one factory whose power argument is a maximum rather than a result.

    ``get_default_pv_system`` multiplies the power it is given by the share, so the argument
    states the array's maximum while the field it writes is what the share left of it -- a
    distinction the argument's name carries and this test keeps honest.
    """
    config = generic_pv_system.PVSystemConfig.get_default_pv_system(
        maximum_power_in_watt=10e3,
        share_of_maximum_pv_potential=0.5,
    )

    assert config.power_in_watt == 5000.0
    assert config.share_of_maximum_pv_potential == 0.5


@pytest.mark.base
@pytest.mark.parametrize("impossible_share", [2.0, -0.5])
def test_the_default_pv_factory_refuses_a_share_that_is_not_a_share(impossible_share: float) -> None:
    """Catches a share outside [0, 1] sizing an array in silence instead of stopping.

    The share is multiplied onto the maximum, so a percentage typed as a fraction or a negative
    value produces a run that finishes and reports plausible numbers for an array nobody asked
    for. The refusal has to name the value, because the number that is wrong is the only clue.
    """
    with pytest.raises(ValueError, match=str(impossible_share)):
        generic_pv_system.PVSystemConfig.get_default_pv_system(
            share_of_maximum_pv_potential=impossible_share
        )


@pytest.mark.base
@pytest.mark.parametrize("impossible_share", [2.0, -0.5])
def test_the_scaled_pv_factory_refuses_a_share_that_is_not_a_share(impossible_share: float) -> None:
    """Catches the same value slipping past on the rooftop path, where it is stamped afterwards.

    The scaled factory applies the share in ``size_pv_system`` and writes it onto the finished
    configuration, so it reaches the field by a second route; the check has to hold on both or it
    holds on neither.
    """
    with pytest.raises(ValueError, match=str(impossible_share)):
        generic_pv_system.PVSystemConfig.get_scaled_pv_system(
            rooftop_area_in_m2=120.0, share_of_maximum_pv_potential=impossible_share
        )
