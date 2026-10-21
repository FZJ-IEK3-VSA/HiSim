"""The anchors of a run's window in a year-long profile: the offset primitive, the leap-year refusal and the weather.

A run that starts on 1 July has to read the weather of 1 July, and the only way to find that row is to
look the start up among the labels the source delivered (``hisim/window_offset.py``). These tests pin
the four anchors that make the lookup trustworthy:

1. **First-timestep identity.** For every source, the first delivered label is 1 January 00:00 and a
   run from 1 January resolves to row 0, which is what every run did before.
2. **The DST phase pins.** The clock is standard time, so 1 July resolves to row 260 640 of a minutely
   Berlin profile -- the row a full-year run labels "1 July 00:00" -- and not to the wall-clock row
   260 580 or, in a profile anchored at 00:30, to 260 550. A December window, where standard time and
   wall-clock time agree, is the mirror. No aggregate or KPI could catch an error here: it moves the
   weather by one hour and keeps every daily sum.
3. **Each reader's first label.** The six sources use three anchors -- Berlin 00:30 for the hourly test
   reference years and NSRDB files, Berlin 00:00 once the interpolation has padded them, and UTC 00:00
   for the rest -- which is why the window is resolved against the delivered frame and not counted
   from 1 January.
4. **Cold then warm.** A July window read from a freshly filed cache entry is the window computed
   without one.
"""

import datetime
import pathlib
from typing import Any, Dict

import pandas as pd
import pytest

from hisim import sim_repository
from hisim.component import SingleTimeStepValues
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.components.weather.calculation import WeatherDataSourceEnum, WeatherSeriesInputs
from hisim.simulationparameters import SimulationParameters
from hisim.window_offset import WindowOffset, WindowOffsetError

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ShippedSources:
    """The weather files of each data source the anchors are measured on, relative to the weather directory.

    ``ERA5`` ships no file, so its test writes a small one of its own (:func:`write_era5_file`).
    """

    WEATHER_DIRECTORY: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent / "hisim" / "inputs" / "weather"

    PATHS: Dict[WeatherDataSourceEnum, str] = {
        WeatherDataSourceEnum.DWD_TRY: "test-reference-years_1995-2012_1-location/data_processed/aachen_center",
        WeatherDataSourceEnum.NSRDB: "NSRDB/Madrid/Madrid",
        WeatherDataSourceEnum.NSRDB_15MIN: "NSRDB_15min/Potsdam/742114_52.41_13.06_2019.csv",
        WeatherDataSourceEnum.DWD_10MIN: "dwd_10min_data/dwd_10min_2021-2022_AACHEN_6.08_50.77.csv",
        WeatherDataSourceEnum.DWD_15MIN: "dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv",
    }

    #: The first label of each reader's raw frame: the anchor the source's own rows carry.
    RAW_FIRST_LABEL: Dict[WeatherDataSourceEnum, str] = {
        WeatherDataSourceEnum.DWD_TRY: "2021-01-01 00:30:00+01:00",
        WeatherDataSourceEnum.NSRDB: "2021-01-01 00:30:00+01:00",
        WeatherDataSourceEnum.NSRDB_15MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.DWD_10MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.DWD_15MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.ERA5: "2021-01-01 00:00:00+00:00",
    }

    #: The first label of the one-minute frame the producer computes from: the hourly sources are
    #: padded back to midnight by the interpolation, the others start where their file does.
    DELIVERED_FIRST_LABEL: Dict[WeatherDataSourceEnum, str] = {
        WeatherDataSourceEnum.DWD_TRY: "2021-01-01 00:00:00+01:00",
        WeatherDataSourceEnum.NSRDB: "2021-01-01 00:00:00+01:00",
        WeatherDataSourceEnum.NSRDB_15MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.DWD_10MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.DWD_15MIN: "2021-01-01 00:00:00+00:00",
        WeatherDataSourceEnum.ERA5: "2021-01-01 00:00:00+00:00",
    }

    @classmethod
    def path(cls, data_source: WeatherDataSourceEnum, tmp_path: pathlib.Path) -> str:
        """Return the file the reader of ``data_source`` opens, writing the ERA5 one when it is asked for.

        Args:
            data_source: the data source.
            tmp_path: where a written file goes.

        Returns:
            str: the configured source path.
        """
        if data_source == WeatherDataSourceEnum.ERA5:
            return write_era5_file(tmp_path)
        return str(cls.WEATHER_DIRECTORY / cls.PATHS[data_source])


def write_era5_file(tmp_path: pathlib.Path) -> str:
    """Write an hourly ERA5 export of 2021 with the header the reader expects, constant in every column.

    Args:
        tmp_path: the directory to write into.

    Returns:
        str: the file's path.
    """
    hours = pd.date_range("2021-01-01", periods=8760, freq="h")
    rows = pd.DataFrame(
        {
            "year": hours.year,
            "month": hours.month,
            "day": hours.day,
            "hour": hours.hour,
            "minute": 0,
            "temperature": 5.0,
            "pressure": 1013.0,
            "wind_direction": 180.0,
            "wind_speed": 3.0,
            "global_irradiance": 0.0,
            "direct_irradiance": 0.0,
            "relative_humidity": 80.0,
        }
    )
    path = tmp_path / "era5_2021.csv"
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("location,longitude,latitude\nSomewhere,6.08,50.77\n")
        rows.to_csv(stream, index=False)
    return str(path)


def one_minute_index(data_source: WeatherDataSourceEnum, tmp_path: pathlib.Path, year: int = 2021) -> pd.Series:
    """Read a source and bring it to one minute, exactly as the producer does before the sun position.

    Args:
        data_source: the data source.
        tmp_path: where the ERA5 file goes.
        year: the simulated year.

    Returns:
        pd.Series: the one-minute direct normal irradiance, whose index every produced column shares.
    """
    inputs = WeatherSeriesInputs(
        data_source=data_source,
        source_content_hash="not read here",
        year=year,
        seconds_per_timestep=60,
        latitude_in_degrees=50.78,
        longitude_in_degrees=6.08,
        source_path=ShippedSources.path(data_source, tmp_path),
    )
    raw = calculation.read_source_data(inputs)
    one_minute: pd.Series = calculation.resample_to_one_minute(raw, inputs)["DNI"]
    return one_minute


def berlin_wall_clock(labels: pd.DatetimeIndex, row: int) -> pd.Timestamp:
    """Return a standard-time label of a Berlin profile as German wall-clock time.

    Args:
        labels: standard-time labels of a Berlin profile (UTC+1 all year).
        row: the row.

    Returns:
        pd.Timestamp: the same instant in Europe/Berlin.
    """
    return (labels[row] - pd.Timedelta(hours=1)).tz_localize("UTC").tz_convert("Europe/Berlin")


@pytest.mark.base
def test_standard_time_labels_skip_no_hour_in_spring_and_repeat_none_in_autumn() -> None:
    """A Berlin index becomes evenly spaced labels in UTC+1; a UTC or naive index keeps its clock.

    Catches: wall-clock labels, which skip 02:00 on the last Sunday of March and repeat it in October,
    so an even grid of timesteps could not be laid over them.
    """
    berlin = pd.date_range("2021-01-01", "2021-12-31 23:00", freq="h", tz="Europe/Berlin")
    labels = WindowOffset.standard_time_labels(berlin)

    assert labels.tz is None
    assert len(labels) == 8760
    assert set(labels.to_series().diff().dropna()) == {pd.Timedelta(hours=1)}
    july = berlin.get_loc(pd.Timestamp("2021-07-01 02:00", tz="Europe/Berlin"))
    assert labels[july] == pd.Timestamp("2021-07-01 01:00")

    utc = pd.date_range("2021-01-01", periods=24, freq="h", tz="UTC")
    assert list(WindowOffset.standard_time_labels(utc)) == list(utc.tz_localize(None))
    naive = pd.date_range("2021-01-01", periods=24, freq="h")
    assert WindowOffset.standard_time_labels(naive) is naive


@pytest.mark.base
def test_the_offset_is_the_row_labelled_with_the_start_and_the_window_must_fit() -> None:
    """A start is found among the labels, and a window the profile does not cover is refused with its reason.

    Catches: an offset computed from 1 January, a window read past the profile's end, and a profile at
    another resolution than the run's.
    """
    hourly = pd.date_range("2021-01-01", periods=8760, freq="h")

    assert WindowOffset.resolve(hourly, datetime.datetime(2021, 1, 1), 24, 3600, "the profile") == 0
    assert WindowOffset.resolve(hourly, datetime.datetime(2021, 7, 1), 168, 3600, "the profile") == 4344
    with pytest.raises(WindowOffsetError, match="has no row labelled 2022-01-01"):
        WindowOffset.resolve(hourly, datetime.datetime(2022, 1, 1), 24, 3600, "the profile")
    with pytest.raises(WindowOffsetError, match="ends before the run does"):
        WindowOffset.resolve(hourly, datetime.datetime(2021, 12, 31), 48, 3600, "the profile")
    with pytest.raises(WindowOffsetError, match="even grid of 900 s"):
        WindowOffset.resolve(hourly, datetime.datetime(2021, 1, 1), 96, 900, "the profile")
    with pytest.raises(WindowOffsetError, match="time zone"):
        WindowOffset.resolve(hourly.tz_localize("UTC"), datetime.datetime(2021, 1, 1), 24, 3600, "the profile")
    with pytest.raises(WindowOffsetError, match="has no rows"):
        WindowOffset.resolve(pd.DatetimeIndex([]), datetime.datetime(2021, 1, 1), 24, 3600, "the profile")
    with pytest.raises(WindowOffsetError, match="carries a time zone"):
        WindowOffset.resolve(
            hourly, datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc), 24, 3600, "the profile"
        )


@pytest.mark.base
def test_a_365_day_source_refuses_the_last_day_of_a_leap_year_and_serves_the_rest() -> None:
    """Laid onto 2020 by position, a 365-day source covers 1 January to 30 December 2020.

    A January week of 2020 is served; 31 December and a full year of 2020 are refused at the lookup,
    which says that the year is a leap year. Before the lookup existed, a full-year run of 2020 failed
    at timestep 8760 with an index error, or published series one day short.
    """
    leap_year_profile = pd.date_range("2020-01-01", periods=8760, freq="h")

    assert WindowOffset.resolve(leap_year_profile, datetime.datetime(2020, 1, 1), 168, 3600, "the profile") == 0
    with pytest.raises(WindowOffsetError, match="2020 is a leap year of 366 days and this source delivers 365"):
        WindowOffset.resolve(leap_year_profile, datetime.datetime(2020, 12, 31), 24, 3600, "the profile")
    with pytest.raises(WindowOffsetError, match="2020 is a leap year"):
        WindowOffset.resolve(leap_year_profile, datetime.datetime(2020, 1, 1), 8784, 3600, "the profile")


@pytest.mark.base
def test_a_full_leap_year_of_weather_is_refused_when_the_weather_prepares(tmp_path: pathlib.Path) -> None:
    """The Aachen test reference year cannot serve 2020 in full, and the Weather says so before the first timestep.

    Catches: the refusal living anywhere but at the lookup, where the missing date is discovered.
    """
    parameters = SimulationParameters.full_year(year=2020, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(tmp_path)
    component = weather.Weather(
        my_simulation_parameters=parameters, config=weather.WeatherConfig.preset_aachen("Weather")
    )
    component.set_sim_repo(sim_repository.SimRepository())

    with pytest.raises(WindowOffsetError, match="2020 is a leap year"):
        component.i_prepare_simulation()


@pytest.mark.base
@pytest.mark.parametrize("data_source", list(ShippedSources.RAW_FIRST_LABEL), ids=lambda source: source.value)
def test_each_reader_starts_where_its_source_does(data_source: WeatherDataSourceEnum, tmp_path: pathlib.Path) -> None:
    """Each reader's first label, before and after the interpolation, and the run's first timestep.

    The six sources use three anchors. Whatever the anchor, the produced frame's first label is
    1 January 00:00 in the source's standard time at every resolution, and a run from 1 January
    resolves to row 0 -- the first-timestep identity that keeps every January run unchanged.
    """
    inputs_path = ShippedSources.path(data_source, tmp_path)
    raw = calculation.read_source_data(
        WeatherSeriesInputs(
            data_source=data_source,
            source_content_hash="not read here",
            year=2021,
            seconds_per_timestep=60,
            latitude_in_degrees=50.78,
            longitude_in_degrees=6.08,
            source_path=inputs_path,
        )
    )
    assert raw.index[0] == pd.Timestamp(ShippedSources.RAW_FIRST_LABEL[data_source])

    one_minute = one_minute_index(data_source, tmp_path)
    assert one_minute.index[0] == pd.Timestamp(ShippedSources.DELIVERED_FIRST_LABEL[data_source])

    for seconds_per_timestep in (60, 900, 3600):
        labels = calculation.timestep_labels(one_minute, seconds_per_timestep)
        assert labels[0] == pd.Timestamp("2021-01-01 00:00")
        assert WindowOffset.resolve(labels, datetime.datetime(2021, 1, 1), 24, seconds_per_timestep, "the source") == 0


@pytest.mark.base
def test_the_dwd_15min_reader_lays_the_whole_file_onto_the_year() -> None:
    """The quarter-hourly DWD reader has one label per row of the file, whatever the run's span.

    The shipped file is a leap year, 366 days of quarter hours. The reader used to size its index by
    the run's span, so only a run of exactly 366 days could read it at all.
    """
    path = str(ShippedSources.WEATHER_DIRECTORY / ShippedSources.PATHS[WeatherDataSourceEnum.DWD_15MIN])

    raw = calculation.read_dwd_15min_data(path, 2024)

    assert len(raw.index) == 366 * 96
    assert raw.index[-1] == pd.Timestamp("2024-12-31 23:45", tz="UTC")


@pytest.mark.base
def test_a_utc_source_whose_last_sample_is_before_midnight_refuses_a_minutely_full_year(tmp_path: pathlib.Path) -> None:
    """The quarter-hourly NSRDB file ends at 23:45, so its one-minute frame lacks the year's last 14 minutes.

    A full minutely year is refused at the lookup, naming the missing end, instead of failing at
    timestep 525 586; at 900 s the same file covers the year.
    """
    one_minute = one_minute_index(WeatherDataSourceEnum.NSRDB_15MIN, tmp_path)

    with pytest.raises(WindowOffsetError, match="ends before the run does"):
        WindowOffset.resolve(
            calculation.timestep_labels(one_minute, 60), datetime.datetime(2021, 1, 1), 525600, 60, "the source"
        )
    assert (
        WindowOffset.resolve(
            calculation.timestep_labels(one_minute, 900), datetime.datetime(2021, 1, 1), 35040, 900, "the source"
        )
        == 0
    )


@pytest.mark.base
def test_the_dst_phase_pins_on_a_profile_anchored_at_half_past() -> None:
    """On a minutely Berlin profile from 00:30, 1 July is row 260 610; 260 550 and 260 640 are the wrong answers.

    The profile is the hourly test reference year's own labels at one-minute resolution, before the
    interpolation pads it to midnight. In standard time its rows are 181 days of minutes after 1 January
    00:00, less the 30 minutes before its first label: 260 610. Reading 1 July as German summer time
    gives the row an hour earlier, 260 550, which a full-year run labels "30 June 23:00". Counting 181
    days of minutes from the first row, as if it were midnight, gives 260 640, half an hour late.
    """
    profile = pd.date_range("2021-01-01 00:30", "2021-12-31 23:30", freq="min", tz="Europe/Berlin")
    labels = WindowOffset.standard_time_labels(profile)

    july = WindowOffset.resolve(labels, datetime.datetime(2021, 7, 1), 10080, 60, "the profile")

    assert july == 260610
    wall_clock_row = profile.get_loc(pd.Timestamp("2021-07-01 00:00", tz="Europe/Berlin"))
    assert wall_clock_row == 260550
    counted_from_the_first_row = 181 * 24 * 60
    assert counted_from_the_first_row == 260640
    assert july not in (wall_clock_row, counted_from_the_first_row)


@pytest.mark.base
def test_the_dst_phase_pins_on_the_delivered_aachen_frame(tmp_path: pathlib.Path) -> None:
    """On the delivered Aachen frame, 1 July is row 260 640 at 60 s, 17 376 at 900 s and 4344 at 3600 s.

    The delivered frame starts at midnight, so the standard-time row of 1 July is 181 days of
    timesteps after it. In German summer time that row is 01:00: it is the row a full-year run labels
    "1 July 00:00", so a July window is the same slice of the year as the full-year run's. The
    wall-clock reading would give 260 580. The December mirror: 1 December is row 480 960 at 60 s and
    is 00:00 on the wall clock as well, since standard time and wall-clock time agree in winter.
    """
    one_minute = one_minute_index(WeatherDataSourceEnum.DWD_TRY, tmp_path)
    expected: Dict[int, Any] = {60: (260640, 480960), 900: (17376, 32064), 3600: (4344, 8016)}

    for seconds_per_timestep, (july_row, december_row) in expected.items():
        labels = calculation.timestep_labels(one_minute, seconds_per_timestep)
        july = WindowOffset.resolve(labels, datetime.datetime(2021, 7, 1), 24, seconds_per_timestep, "Aachen")
        december = WindowOffset.resolve(labels, datetime.datetime(2021, 12, 1), 24, seconds_per_timestep, "Aachen")
        assert (july, december) == (july_row, december_row)
        assert berlin_wall_clock(labels, july) == pd.Timestamp("2021-07-01 01:00", tz="Europe/Berlin")
        assert berlin_wall_clock(labels, december) == pd.Timestamp("2021-12-01 00:00", tz="Europe/Berlin")

    wall_clock_row = one_minute.index.get_loc(pd.Timestamp("2021-07-01 00:00", tz="Europe/Berlin"))
    assert wall_clock_row == 260580


@pytest.mark.base
def test_a_utc_source_resolves_july_without_a_daylight_saving_correction(tmp_path: pathlib.Path) -> None:
    """A UTC source has no daylight saving: 1 July is 181 days of quarter hours after 1 January, 17 376."""
    one_minute = one_minute_index(WeatherDataSourceEnum.NSRDB_15MIN, tmp_path)
    labels = calculation.timestep_labels(one_minute, 900)

    assert WindowOffset.resolve(labels, datetime.datetime(2021, 7, 1), 96, 900, "Potsdam") == 17376
    assert WindowOffset.resolve(labels, datetime.datetime(2021, 12, 1), 96, 900, "Potsdam") == 32064


def prepared_july_weather(cache_directory: pathlib.Path) -> weather.Weather:
    """Prepare an Aachen weather for the first week of July 2021 at 3600 s, with ``cache_directory`` as its cache.

    Args:
        cache_directory: the cache directory.

    Returns:
        weather.Weather: the prepared component.
    """
    parameters = SimulationParameters.one_week_july(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(cache_directory)
    component: weather.Weather = weather.Weather(
        my_simulation_parameters=parameters, config=weather.WeatherConfig.preset_aachen("Weather")
    )
    component.set_sim_repo(sim_repository.SimRepository())
    component.i_prepare_simulation()
    return component


@pytest.mark.base
def test_a_july_window_from_a_warm_cache_is_the_window_from_a_cold_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same July week prepared twice over one cache directory publishes the same series, read from row 4344.

    The first preparation computes the year and files it; the second reads it back. Both resolve the
    window to row 4344 and publish the 168 values from there, and those are not January's.

    Catches: a window lost on the way through the cache -- labels not written or not read back, which
    would resolve a warm run to another row or refuse it -- and the window not moving at all.
    """
    for variable in ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR"):
        monkeypatch.delenv(variable, raising=False)
    cold = prepared_july_weather(tmp_path)
    warm = prepared_july_weather(tmp_path)

    assert cold.window_offset_in_timesteps == warm.window_offset_in_timesteps == 4344
    for key in (
        weather.Weather.RUN_WINDOW_TEMPERATURE_OUTSIDE,
        weather.Weather.RUN_WINDOW_GLOBAL_HORIZONTAL_IRRADIANCE,
        weather.Weather.RUN_WINDOW_APPARENT_ZENITH,
    ):
        cold_series = cold.simulation_repository.get_entry(key)
        assert len(cold_series) == 168
        assert list(cold_series) == list(warm.simulation_repository.get_entry(key)), key
    july_temperatures = cold.simulation_repository.get_entry(weather.Weather.RUN_WINDOW_TEMPERATURE_OUTSIDE)
    assert list(july_temperatures) == cold.temperature_list[4344:4512]
    assert list(july_temperatures) != cold.temperature_list[:168]


@pytest.mark.base
def test_a_july_window_feeds_july_into_the_weather_outputs_at_every_timestep(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-timestep outputs of a July week read row 4344 + t, not row t.

    The published window is pinned above; this pins the other path the weather serves, the outputs
    other components are wired to. Catches: ``i_simulate`` dropping the window offset, which would hand
    every wired consumer January's temperature and irradiance while the published series say July.
    """
    for variable in ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR"):
        monkeypatch.delenv(variable, raising=False)
    july = prepared_july_weather(tmp_path)
    outputs = july.get_outputs()
    for global_index, output in enumerate(outputs):
        output.global_index = global_index
    stsv = SingleTimeStepValues(len(outputs))

    for timestep in (0, 1, 167):
        july.i_simulate(timestep, stsv, False)
        assert stsv.values[july.air_temperature_output.global_index] == july.temperature_list[4344 + timestep]
        assert stsv.values[july.ghi_output.global_index] == july.ghi_list[4344 + timestep]
    assert july.temperature_list[4344:4347] != july.temperature_list[0:3]
