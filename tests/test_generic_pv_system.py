"""Test for generic pv system."""
import pytest
from tests import functions_for_testing as fft
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim import simulator as sim
from hisim import log


@pytest.mark.base
def test_photovoltaic():
    """Test generic pv system."""
    # Sets inputs
    # weather_location = "Aachen"
    seconds_per_timestep = 60
    power_in_watt = 10 * 1e3

    repo = sim_repository.SimRepository()

    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Weather: 6 outputs
    # PVS:  1 output

    # Sets Occupancy
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    my_pvs_config = generic_pv_system.PVSystem.get_default_config()
    my_pvs_config.power_in_watt = power_in_watt
    my_pvs = generic_pv_system.PVSystem(
        config=my_pvs_config, my_simulation_parameters=mysim
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
    my_pvs.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
    my_pvs.wind_speed_channel.source_output = my_weather.wind_speed_output

    fft.add_global_index_of_components([my_weather, my_pvs])

    timestep = 655
    my_weather.i_simulate(timestep, stsv, False)
    my_pvs.i_simulate(timestep, stsv, False)
    log.information("pv electricity output [W]: " + str(stsv.values[my_pvs.electricity_output_channel.global_index]))

    # check pv electricity output [W] in timestep 655
    assert stsv.values[my_pvs.electricity_output_channel.global_index] == 334.8800144821672
