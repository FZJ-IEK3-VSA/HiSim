"""Test for weather."""
import pathlib

import pytest
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_weather() -> None:
    """Verify Weather component produces total annual DNI above sanity threshold over full-year simulation."""
    mysim: SimulationParameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    repo: sim_repository.SimRepository = sim_repository.SimRepository()
    my_weather_config: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather: weather.Weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )

    number_of_outputs: int = fft.get_number_of_outputs([my_weather])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_weather])
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    # Simulate
    dni: list[float] = []
    for i in range(60 * 24 * 365):
        my_weather.i_simulate(i, stsv, False)
        dni.append(stsv.values[my_weather.dni_output.global_index])

    assert sum(dni) > 950


def test_weather_config_enum_vs_string_consistency() -> None:
    """Test consistency of enum vs. string configuration setup."""
    my_weather_config_enum: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )

    my_weather_config_string: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry="AACHEN"
    )

    assert my_weather_config_enum.location == my_weather_config_string.location
    assert my_weather_config_enum.data_source == my_weather_config_string.data_source
    assert isinstance(my_weather_config_enum.source_path, str)
    assert len(my_weather_config_enum.source_path) > 0
    assert my_weather_config_enum.source_path == my_weather_config_string.source_path


def test_weather_config_with_direct_filepath(tmp_path: pathlib.Path) -> None:
    """Test weather config with direct filepath and direct data source."""
    weather_file: pathlib.Path = tmp_path / "weather.csv"
    weather_file.write_text("dummy weather data", encoding="utf-8")

    my_weather_config: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry="CUSTOM_LOCATION",
        weather_direct_filepath=str(weather_file),
        weather_direct_data_source=weather.WeatherDataSourceEnum.DWD_10MIN
    )

    assert my_weather_config.location == "CUSTOM_LOCATION"
    assert my_weather_config.source_path == str(weather_file)[:-4]
    assert my_weather_config.data_source == weather.WeatherDataSourceEnum.DWD_10MIN


def test_weather_config_with_direct_filepath_without_data_source(tmp_path: pathlib.Path) -> None:
    """Test weather config fails for direct filepath without data source."""
    weather_file: pathlib.Path = tmp_path / "weather.csv"
    weather_file.write_text("dummy weather data", encoding="utf-8")

    with pytest.raises(ValueError):
        weather.WeatherConfig.get_default(
            location_entry="CUSTOM_LOCATION",
            weather_direct_filepath=str(weather_file)
        )
