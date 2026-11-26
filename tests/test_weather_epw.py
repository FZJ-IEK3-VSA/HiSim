"""The EnergyPlus weather file (EPW) reader and the Denver station it was added for.

An EPW holds one record per hour of a year without a column header, stamped with the hour the record
ends, in the station's own standard time. The reader lays the records at the middle of their hour in a
fixed UTC offset read from the file's LOCATION line, the station header yields the coordinates from that
line, the catalogue names the shipped Denver TMY3 of the ANSI/ASHRAE Standard 140 test cases, and the
interpolation pads the year in the series' own zone instead of a hard-coded Berlin one. The expected
numbers below were read from the file by hand (its first record, its record for 21 June hour 13, and the
column sums over its 8760 records), not from the code under test.
"""

import datetime
import pathlib
from typing import ClassVar, Dict

import pandas as pd
import pytest

from hisim import sim_repository
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.components.weather.calculation import RelativeHumidity, WeatherDataSourceEnum
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class Denver:
    """The shipped Denver TMY3 and the facts of it the tests check, read from the file by hand."""

    #: The shipped file, the one weather of the building-model verification suite.
    PATH: ClassVar[pathlib.Path] = pathlib.Path(__file__).resolve().parent.parent / "hisim" / "inputs" / "weather" / "EPW" / "Denver_Intl_AP" / "725650TYCST.epw"

    #: The station's standard time, UTC−7 all year, as its LOCATION line states it.
    STANDARD_TIME: ClassVar[datetime.timezone] = datetime.timezone(datetime.timedelta(hours=-7))

    #: The first record, 1 January hour 1: −18.0 °C, 85 %, 83 700 Pa, no sun, calm; the pressure in hPa here.
    FIRST_RECORD: ClassVar[Dict[str, float]] = {
        "T": -18.0,
        RelativeHumidity.COLUMN: 85.0,
        "Pressure": 837.0,
        "GHI": 0.0,
        "DNI": 0.0,
        "DHI": 0.0,
        "Wspd": 0.0,
    }

    #: Record 4117 of the file, 21 June hour 13 (the hour ending at 13:00): 26.7 °C, 42 %, 83 900 Pa,
    #: 723, 276 and 460 Wh/m² of global, direct normal and diffuse irradiance, calm.
    JUNE_21_HOUR_13: ClassVar[Dict[str, float]] = {
        "T": 26.7,
        RelativeHumidity.COLUMN: 42.0,
        "Pressure": 839.0,
        "GHI": 723.0,
        "DNI": 276.0,
        "DHI": 460.0,
        "Wspd": 0.0,
    }

    #: The zero-based row of that record in the raw frame: 171 full days plus 12 hours.
    JUNE_21_HOUR_13_ROW: ClassVar[int] = 171 * 24 + 12

    #: The sums of the three irradiance columns over the 8760 records, in Wh/m².
    ANNUAL_SUMS_IN_WATT_HOUR_PER_SQUARE_METER: ClassVar[Dict[str, float]] = {
        "GHI": 1_670_220.0,
        "DNI": 1_977_576.0,
        "DHI": 556_451.0,
    }


def test_the_reader_lays_the_records_at_their_half_hour_in_denvers_standard_time() -> None:
    """8760 rows from 00:30 to 23:30 of the simulated year in UTC−7 without daylight saving, with the raw columns.

    Catches: a reader that keeps the file's own years, labels the hour at its end instead of its middle, or
    stamps the rows in Berlin or UTC time, which would shift Denver's sun by seven hours.
    """
    raw = calculation.read_epw_data(str(Denver.PATH), 2021)

    assert len(raw) == 8760
    assert set(raw.columns) == set(calculation.RAW_COLUMNS)
    assert raw.index[0] == pd.Timestamp("2021-01-01 00:30", tz=Denver.STANDARD_TIME)
    assert raw.index[-1] == pd.Timestamp("2021-12-31 23:30", tz=Denver.STANDARD_TIME)
    assert raw.index[4000].utcoffset() == datetime.timedelta(hours=-7)
    assert not raw.index[4000].dst()


def test_the_reader_delivers_the_files_values_in_the_raw_frames_units() -> None:
    """The first record, the record of 21 June hour 13 and the annual irradiance sums are the file's, the pressure in hPa.

    Catches: a column read from the wrong field, the pressure left in Pa, a row shifted against its stamp.
    """
    raw = calculation.read_epw_data(str(Denver.PATH), 2021)

    for column, expected in Denver.FIRST_RECORD.items():
        assert raw[column].iloc[0] == expected, column
    for column, expected in Denver.JUNE_21_HOUR_13.items():
        assert raw[column].iloc[Denver.JUNE_21_HOUR_13_ROW] == expected, column
    assert raw.index[Denver.JUNE_21_HOUR_13_ROW] == pd.Timestamp("2021-06-21 12:30", tz=Denver.STANDARD_TIME)
    for column, expected in Denver.ANNUAL_SUMS_IN_WATT_HOUR_PER_SQUARE_METER.items():
        # Sums of whole numbers below 2^53 are exact in floating point.
        assert raw[column].sum() == expected, column


def _header_lines() -> str:
    """Return the eight header lines of the shipped file, line endings included."""
    with open(Denver.PATH, encoding="utf-8") as epw_file:
        return "".join(next(epw_file) for _ in range(calculation.EpwRecord.HEADER_LINES))


def _record_lines() -> list:
    """Return the 8760 record lines of the shipped file, line endings included."""
    with open(Denver.PATH, encoding="utf-8") as epw_file:
        return list(epw_file)[calculation.EpwRecord.HEADER_LINES :]


def test_a_file_with_the_wrong_number_of_records_is_refused_naming_the_count(tmp_path: pathlib.Path) -> None:
    """A file of one day is refused with its record count, not laid onto a year with 8736 rows missing."""
    path = tmp_path / "one_day.epw"
    path.write_text(_header_lines() + "".join(_record_lines()[:24]), encoding="utf-8")

    with pytest.raises(ValueError, match=r"holds 24 records .* holds 8760"):
        calculation.read_epw_data(str(path), 2021)


def test_records_out_of_order_are_refused(tmp_path: pathlib.Path) -> None:
    """A file whose second and third records are swapped is refused: the rows are laid by position, so order is data."""
    records = _record_lines()
    records[1], records[2] = records[2], records[1]
    path = tmp_path / "swapped.epw"
    path.write_text(_header_lines() + "".join(records), encoding="utf-8")

    with pytest.raises(ValueError, match="not the hours of a 365-day year in order"):
        calculation.read_epw_data(str(path), 2021)


def test_a_first_line_that_is_not_a_location_line_is_refused(tmp_path: pathlib.Path) -> None:
    """A file that starts with another header line, or a LOCATION line short of a field, is refused by name."""
    path = tmp_path / "headless.epw"
    path.write_text("DESIGN CONDITIONS,0\n" + "".join(_record_lines()[:3]), encoding="utf-8")

    with pytest.raises(ValueError, match="does not start with a LOCATION line of 10 fields"):
        calculation.StationHeader.read_epw_header(str(path))


def test_the_station_header_yields_denvers_coordinates_and_time_zone() -> None:
    """The LOCATION line gives the name, 39.83 N, 104.65 W, UTC−7 and 1650 m as the file states them.

    Catches: a field read by the wrong position, which would put the station elsewhere or in another zone.
    """
    location = calculation.get_coordinates(str(Denver.PATH), WeatherDataSourceEnum.EPW)
    header = calculation.StationHeader.read_epw_header(str(Denver.PATH))

    assert location == {"name": "DENVER INTL AP", "latitude": 39.83, "longitude": -104.65}
    assert header["Time Zone"] == "-7.0"
    assert header["Elevation"] == "1650.0"
    assert header["WMO"] == "725650"


def test_the_catalogue_station_names_the_shipped_file_and_its_reader() -> None:
    """``LocationEnum.DENVER_INTL_AP`` resolves to the shipped EPW, the EPW reader, and a typical year without a data year."""
    config = weather.WeatherConfig.for_location("Weather", weather.LocationEnum.DENVER_INTL_AP, -17.5)

    assert pathlib.Path(config.source_path) == Denver.PATH
    assert config.data_source == WeatherDataSourceEnum.EPW
    assert config.location == "Denver Intl AP"
    assert config.identity() == "Denver Intl AP/EPW/weather/EPW/Denver_Intl_AP/725650TYCST.epw"
    assert calculation.WeatherSourceFiles.SUFFIXES[WeatherDataSourceEnum.EPW] == ("",)
    assert calculation.WeatherSourceFiles.data_years(config.data_source, config.source_path) is None


@pytest.mark.parametrize("zone", ["Europe/Berlin", Denver.STANDARD_TIME], ids=["berlin", "utc_minus_7"])
def test_the_interpolation_pads_the_year_in_the_series_own_zone(zone: object) -> None:
    """The padded one-minute series runs from 1 January 00:00 to 31 December 23:59 of the series' zone.

    For Berlin these are the instants the former hard-coded padding used (31 December 23:00 UTC of the year
    before and 31 December 22:59 UTC), so the German sources are unchanged; for a fixed offset the padding
    moves with the station instead of landing seven hours into its day.

    Catches: padding timestamps built in another zone than the series, which would leave the first or the
    last hours of the station's year without values.
    """
    hourly = pd.Series(
        [5.0 + hour for hour in range(8760)],
        index=pd.date_range("2021-01-01 00:30", periods=8760, freq="h", tz=zone),  # type: ignore[arg-type]  # a str or a tzinfo
    )

    padded_with_zero = calculation.interpolate(hourly, 2021)
    padded_with_sample = calculation.interpolate(hourly, 2021, pad_start_with_first_sample=True)

    assert len(padded_with_zero) == 525_600
    assert padded_with_zero.index[0] == pd.Timestamp("2021-01-01 00:00", tz=zone)  # type: ignore[arg-type]  # a str or a tzinfo
    assert padded_with_zero.index[-1] == pd.Timestamp("2021-12-31 23:59", tz=zone)  # type: ignore[arg-type]  # a str or a tzinfo
    assert padded_with_zero.iloc[0] == 0.0
    assert padded_with_zero.iloc[30] == 5.0
    assert padded_with_zero.iloc[-1] == 5.0 + 8759
    assert padded_with_sample.iloc[0] == 5.0
    if zone == "Europe/Berlin":
        assert padded_with_zero.index[0] == pd.Timestamp("2020-12-31 23:00", tz="UTC")
        assert padded_with_zero.index[-1] == pd.Timestamp("2021-12-31 22:59", tz="UTC")


@pytest.fixture(name="denver_day", scope="module")
def fixture_denver_day(tmp_path_factory: pytest.TempPathFactory) -> weather.Weather:
    """Prepare one hourly run of 1 January 2021 over the catalogue station, once for the tests that read it.

    Preparing the component produces the station's whole year at one-minute resolution (about 15 s), so
    the two tests below share one preparation instead of producing the year twice.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(tmp_path_factory.mktemp("denver_cache"))
    config = weather.WeatherConfig.for_location("Weather", weather.LocationEnum.DENVER_INTL_AP, -17.5)
    component: weather.Weather = weather.Weather(my_simulation_parameters=parameters, config=config)
    component.set_sim_repo(sim_repository.SimRepository())
    component.i_prepare_simulation()
    return component


def test_the_weather_component_reads_a_denver_day_from_the_catalogue(denver_day: weather.Weather) -> None:
    """The run finds 1 January at row 0 of the produced year, publishes 24 values and the first hour's humidity.

    Catches: a catalogue entry, a content hash, a header read or a window lookup that does not fit the EPW reader.
    """
    assert denver_day.window_offset_in_timesteps == 0
    assert denver_day.standard_time_labels[0] == pd.Timestamp("2021-01-01 00:00")
    assert len(denver_day.standard_time_labels) == 8760
    assert len(denver_day.run_window_of(denver_day.temperature_list)) == 24
    # The first hour's mean: 85 % from the padded midnight to the 00:30 sample, then the line towards the
    # 83 % of hour 2 at 01:30, of which the hour covers the first 30 of 60 minutes (mean offset 14.5/60).
    expected_first_hour_in_percent = 85.0 + (83.0 - 85.0) * 14.5 / 120.0
    # An exact linear interpolation and mean; 1e-9 leaves room for the summation order only.
    assert denver_day.relative_humidity_list_in_percent[0] == pytest.approx(expected_first_hour_in_percent, rel=1e-9)


def test_the_produced_denver_year_puts_the_sun_where_denver_has_it(denver_day: weather.Weather) -> None:
    """On 21 June the hourly sun altitude is below −10° at 03:00, above 5° at 05:00 and 18:00, below −10° at 21:00, and about 72° at noon.

    Denver's sun on 21 June rises at 04:31 and sets at 19:31 standard time (UTC−7), and culminates at
    73.6° (90° − 39.83° + 23.44°) close to 12:00. The hour from 12:00 covers hour angles 0 to 15°, so
    its mean altitude lies between 69° and 73.6°; the bounds leave 2° to the sides for that mean. A sun
    position computed one hour off -- the file's hours taken as UTC−6, or the padding in Berlin time --
    moves the 05:00 hour to before sunrise and fails here.
    """
    altitude = denver_day.altitude_list
    june_21 = 171 * 24

    assert len(altitude) == 8760
    assert altitude[june_21 + 3] < -10.0
    assert altitude[june_21 + 5] > 5.0
    assert 67.0 < altitude[june_21 + 12] < 75.6
    assert altitude[june_21 + 18] > 5.0
    assert altitude[june_21 + 21] < -10.0
