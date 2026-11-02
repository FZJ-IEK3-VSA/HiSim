"""Tests that every weather source format yields the station's own latitude and longitude.

The coordinates a weather file declares decide the sun position, and with it the direct normal
irradiance and every PV yield. The DWD 10- and 15-minute files and the ERA5 export name their columns
``location,longitude,latitude``; a reader that took the second column for the latitude put Düsseldorf
near 6°N / 50.8°E. The tests below pin, per format, the values the file itself states, and that the
reader goes by the column names rather than by their order.
"""
import pathlib
from typing import Tuple

import pytest

from hisim.components.weather import calculation
from hisim.components.weather.calculation import WeatherDataSourceEnum


class ShippedStations:
    """The shipped weather files the tests read, one per format that ships one, and their stated coordinates.

    The source path is what a configuration stores: the stem of the ``.dat`` for the DWD test
    reference years and the hourly NSRDB files, the file itself for the CSV formats.
    """

    WEATHER_DIRECTORY: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent / "hisim" / "inputs" / "weather"

    #: Per case: data source, source path below the weather directory, name, latitude and longitude
    #: in degrees, as the header of the file states them.
    CASES: Tuple[Tuple[WeatherDataSourceEnum, str, str, float, float], ...] = (
        (
            WeatherDataSourceEnum.DWD_TRY,
            "test-reference-years_1995-2012_1-location/data_processed/aachen_center",
            "Aachen Innenstadt",
            50.76922839713459,
            6.0983619036603,
        ),
        (WeatherDataSourceEnum.NSRDB, "NSRDB/Seville/Seville", "Seville", 37.37, -5.98),
        (WeatherDataSourceEnum.NSRDB_15MIN, "NSRDB_15min/Dublin/165308_53.37_-6.26_2019.csv", "165308", 53.37, -6.26),
        (
            WeatherDataSourceEnum.DWD_10MIN,
            "dwd_10min_data/dwd_10min_2021-2022_AACHEN_6.08_50.77.csv",
            "AACHEN",
            50.775,
            6.083,
        ),
        (
            WeatherDataSourceEnum.DWD_15MIN,
            "dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv",
            "Duesseldorf",
            51.296,
            6.7686,
        ),
    )


@pytest.mark.base
@pytest.mark.parametrize(
    "data_source, relative_path, name, latitude_in_degrees, longitude_in_degrees",
    ShippedStations.CASES,
    ids=[case[0].value for case in ShippedStations.CASES],
)
def test_a_shipped_weather_file_yields_the_coordinates_its_header_states(
    data_source: WeatherDataSourceEnum,
    relative_path: str,
    name: str,
    latitude_in_degrees: float,
    longitude_in_degrees: float,
) -> None:
    """Each format with a shipped file returns the station's name, latitude and longitude as the file states them."""
    location = calculation.get_coordinates(str(ShippedStations.WEATHER_DIRECTORY / relative_path), data_source)
    assert location == {"name": name, "latitude": latitude_in_degrees, "longitude": longitude_in_degrees}


@pytest.mark.base
def test_an_era5_file_yields_the_coordinates_its_header_states(tmp_path: pathlib.Path) -> None:
    """No ERA5 file ships, so a small one in the exporter's layout -- longitude before latitude -- stands in."""
    path = tmp_path / "era5.csv"
    path.write_text(
        "location,longitude,latitude\nSomewhere,6.08,50.77\nyear,month,day,hour,minute,temperature\n",
        encoding="utf-8",
    )
    location = calculation.get_coordinates(str(path), WeatherDataSourceEnum.ERA5)
    assert location == {"name": "Somewhere", "latitude": 50.77, "longitude": 6.08}


@pytest.mark.base
@pytest.mark.parametrize(
    "data_source", [WeatherDataSourceEnum.DWD_10MIN, WeatherDataSourceEnum.DWD_15MIN, WeatherDataSourceEnum.ERA5]
)
def test_the_coordinates_are_read_by_column_name_and_not_by_position(
    tmp_path: pathlib.Path, data_source: WeatherDataSourceEnum
) -> None:
    """A header that lists the latitude before the longitude still yields each under its own name."""
    path = tmp_path / "station.csv"
    path.write_text("latitude,location,longitude\n51.296,Duesseldorf,6.7686\nyear,month\n", encoding="utf-8")
    location = calculation.get_coordinates(str(path), data_source)
    assert location == {"name": "Duesseldorf", "latitude": 51.296, "longitude": 6.7686}


@pytest.mark.base
def test_a_header_without_a_latitude_is_refused_naming_the_column(tmp_path: pathlib.Path) -> None:
    """A file whose header lacks a coordinate column fails with the column's name instead of reading another one."""
    path = tmp_path / "station.csv"
    path.write_text("location,longitude,lat\nDuesseldorf,6.7686,51.296\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'latitude'"):
        calculation.get_coordinates(str(path), WeatherDataSourceEnum.DWD_15MIN)


@pytest.mark.base
def test_a_dat_header_without_a_longitude_is_refused_naming_the_label(tmp_path: pathlib.Path) -> None:
    """A ``.dat`` header that lacks the longitude label fails with the label's name."""
    stem = tmp_path / "station"
    (tmp_path / "station.dat").write_text("Ort        : Aachen\nBreitengrad       : 50.77\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'Längengrad'"):
        calculation.get_coordinates(str(stem), WeatherDataSourceEnum.DWD_TRY)
