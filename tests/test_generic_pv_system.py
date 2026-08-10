"""Test for generic pv system."""

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
