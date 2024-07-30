"""Test for generic windturbine."""
import pytest
from tests import functions_for_testing as fft
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components import generic_windturbine
from hisim import simulator as sim
from hisim import log


@pytest.mark.base
def test_windturbine():
    """Test generic windturbine."""
    # Sets inputs
    # weather_location = "Aachen"
    seconds_per_timestep = 60
    turbine_type = "V126/3300"  # Vestas - V126-3.3 MW

    repo = sim_repository.SimRepository()

    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Weather: 3 outputs
    # Windturbine:  1 output

    # Sets Occupancy
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    my_windturbine_config = generic_windturbine.WindturbineConfig.get_default_windturbine_config()
    my_windturbine_config.turbine_type = turbine_type
    my_windturbine = generic_windturbine.Windturbine(
        config=my_windturbine_config, my_simulation_parameters=mysim
    )
    my_windturbine.set_sim_repo(repo)
    my_windturbine.i_prepare_simulation()
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_windturbine])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    my_windturbine.t_out_channel.source_output = my_weather.air_temperature_output
    my_windturbine.wind_speed_channel.source_output = my_weather.wind_speed_output
    my_windturbine.pressure_channel.source_output = my_weather.pressure_output

    fft.add_global_index_of_components([my_weather, my_windturbine])

    timestep = 55505
    my_weather.i_simulate(timestep, stsv, False)
    my_windturbine.i_simulate(timestep, stsv, False)
    log.information("windturbine electricity output [W]: " + str(
        stsv.values[my_windturbine.electricity_output_channel.global_index]))

    # check windturbine electricity output [W] in timestep 55505
    assert stsv.values[my_windturbine.electricity_output_channel.global_index] == 18816.25770544808
