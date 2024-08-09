"""Test for weather data import."""
import datetime
import shutil
import pytest
from hisim.components.weather_data_import import WeatherDataImport


@pytest.mark.base
def test_weather_data_import():
    """Test weather data import."""

    location = "AACHEN"
    latitude = 50.775
    longitude = 6.083

    start_date = datetime.datetime(year=2023, month=1, day=1, hour=0, minute=0, second=0)
    end_date = datetime.datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)

    result_directory = "test"

    WeatherDataImport(
        start_date=start_date,
        end_date=end_date,
        location=location,
        latitude=latitude,
        longitude=longitude,
        path_input_folder=result_directory,
        distance_weather_stations=30,
        weather_data_source="DWD_10MIN",
    )

    shutil.rmtree(result_directory)  # remove result directory
