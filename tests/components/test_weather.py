"""Test for weather."""
import pytest
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_weather():
    """Test weather."""
    mysim: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    repo = sim_repository.SimRepository()
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )

    number_of_outputs = fft.get_number_of_outputs([my_weather])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_weather])
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    # Simulate
    dni = []
    for i in range(60 * 24 * 365):
        my_weather.i_simulate(i, stsv, False)
        dni.append(stsv.values[my_weather.dni_output.global_index])

    assert sum(dni) > 950
