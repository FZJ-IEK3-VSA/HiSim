"""The outdoor relative humidity the weather publishes, from every data source.

The defrosting of an air-source heat pump depends on the humidity of the outdoor air, so every weather
source publishes it (hisim-heat-pump-model-rework-fpqz.6): the DWD test reference years from their own
``RF`` column, the NSRDB files and the DWD ten- and fifteen-minute files from the humidity column added
to them from the same upstream data as their temperature (``hisim/inputs/weather/humidity_provenance.md``),
and an ERA5 export from its ``relative_humidity`` column. These tests pin four things:

1. **Every shipped file carries a plausible humidity**: 0 to 100 %, no gap, an annual mean in the band of
   its climate, and a series that varies.
2. **The component publishes it** as the ``RelativeHumidity`` output, for every data source.
3. **A window reads its own dates**: a July run reads the humidity of July, from the same row as the
   temperature, for an hourly and for a sub-hourly source.
4. **A file without humidity, or with an impossible one, is refused** rather than filled in.
"""

import pathlib
from typing import ClassVar, Dict, List, Tuple

import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim import sim_repository
from hisim.component import SingleTimeStepValues
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.components.weather.calculation import RelativeHumidity, WeatherDataSourceEnum, WeatherSeriesInputs
from hisim.simulationparameters import SimulationParameters

from tests.test_window_offset import write_era5_file

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ShippedWeatherFiles:
    """Every weather file this repository ships, with its data source and the climate it belongs to.

    The climate decides the band the annual mean relative humidity of the file has to lie in. The bands
    are wide on purpose: they catch a column read from the wrong place -- a temperature, a fraction
    instead of a percentage, a constant -- and not the weather of a particular year.
    """

    WEATHER_DIRECTORY: ClassVar[pathlib.Path] = (
        pathlib.Path(__file__).resolve().parent.parent / "hisim" / "inputs" / "weather"
    )

    #: The band of the annual mean relative humidity, in percent, of a humid temperate climate:
    #: Germany and the rest of central, western and northern Europe, around 70 to 85 %.
    TEMPERATE_BAND_IN_PERCENT: ClassVar[Tuple[float, float]] = (65.0, 90.0)

    #: The band of the annual mean relative humidity, in percent, of the drier climates of southern
    #: Europe and the Balkans, around 55 to 70 %.
    SOUTHERN_BAND_IN_PERCENT: ClassVar[Tuple[float, float]] = (45.0, 80.0)

    #: The files of the southern band, by their path relative to :attr:`WEATHER_DIRECTORY`. Every
    #: other shipped file belongs to the temperate band.
    SOUTHERN_FILES: ClassVar[Tuple[str, ...]] = (
        "NSRDB/Athens/Athens",
        "NSRDB/Belgrade/Belgrade",
        "NSRDB/Cyprus/Cyprus",
        "NSRDB/Madrid/Madrid",
        "NSRDB/Milan/Milan",
        "NSRDB/Sarajevo/Sarajevo",
        "NSRDB/Seville/Seville",
        "NSRDB/Vranje/Vranje",
        "NSRDB_15min/Athens/1291832_37.97_23.74_2019.csv",
        "NSRDB_15min/Belgrad/1108363_44.77_20.46_2019.csv",
        "NSRDB_15min/Malaga/213028_36.73_-4.42_2019.csv",
        "NSRDB_15min/Nicosia/1809004_35.17_33.38_2019.csv",
        "NSRDB_15min/Rome/718838_41.89_12.50_2019.csv",
        "NSRDB_15min/Sofia/1267064_42.69_23.30_2019.csv",
    )

    #: Where each data source's shipped files are, as glob patterns relative to the weather directory,
    #: and the suffix its configured path leaves off.
    PATTERNS: ClassVar[Dict[WeatherDataSourceEnum, Tuple[str, str]]] = {
        WeatherDataSourceEnum.DWD_TRY: ("test-reference-years_*/data_processed/*.dat", ".dat"),
        WeatherDataSourceEnum.NSRDB: ("NSRDB/*/*.dat", ".dat"),
        WeatherDataSourceEnum.NSRDB_15MIN: ("NSRDB_15min/*/*.csv", ""),
        WeatherDataSourceEnum.DWD_10MIN: ("dwd_10min_data/*.csv", ""),
        WeatherDataSourceEnum.DWD_15MIN: ("dwd_15min_data/*.csv", ""),
    }

    @classmethod
    def all(cls) -> List[Tuple[WeatherDataSourceEnum, str]]:
        """Return every shipped file as its data source and its configured path relative to the directory.

        Returns:
            List[Tuple[WeatherDataSourceEnum, str]]: one pair per file, in a stable order.
        """
        files = []
        for data_source, (pattern, suffix) in cls.PATTERNS.items():
            for path in sorted(cls.WEATHER_DIRECTORY.glob(pattern)):
                relative = path.relative_to(cls.WEATHER_DIRECTORY).as_posix()
                files.append((data_source, relative[: len(relative) - len(suffix)] if suffix else relative))
        return files

    @classmethod
    def band_of(cls, relative_path: str) -> Tuple[float, float]:
        """Return the band the annual mean relative humidity of a shipped file must lie in.

        Args:
            relative_path: the configured path relative to :attr:`WEATHER_DIRECTORY`.

        Returns:
            Tuple[float, float]: the lowest and the highest annual mean, in percent.
        """
        return cls.SOUTHERN_BAND_IN_PERCENT if relative_path in cls.SOUTHERN_FILES else cls.TEMPERATE_BAND_IN_PERCENT


def inputs_for(
    data_source: WeatherDataSourceEnum, source_path: str, seconds_per_timestep: int = 3600
) -> WeatherSeriesInputs:
    """The producer's inputs for one file of 2021, with coordinates that only the sun position reads.

    Args:
        data_source: the data source.
        source_path: the configured path.
        seconds_per_timestep: the timestep.

    Returns:
        WeatherSeriesInputs: the inputs.
    """
    return WeatherSeriesInputs(
        data_source=data_source,
        source_content_hash="not read here",
        year=2021,
        seconds_per_timestep=seconds_per_timestep,
        latitude_in_degrees=50.78,
        longitude_in_degrees=6.08,
        source_path=source_path,
    )


def prepared_weather(
    config: weather.WeatherConfig, parameters: SimulationParameters, cache_directory: pathlib.Path
) -> weather.Weather:
    """Build a weather component from ``config`` and run its preparation against ``cache_directory``.

    Args:
        config: the weather configuration.
        parameters: the run's simulation parameters.
        cache_directory: the cache directory the series is filed in.

    Returns:
        weather.Weather: the prepared component, its outputs numbered so that they can be simulated.
    """
    parameters.cache_dir_path = str(cache_directory)
    component: weather.Weather = weather.Weather(my_simulation_parameters=parameters, config=config)
    component.set_sim_repo(sim_repository.SimRepository())
    component.i_prepare_simulation()
    for global_index, output in enumerate(component.get_outputs()):
        output.global_index = global_index
    return component


def published_humidity(component: weather.Weather, timesteps: List[int]) -> List[float]:
    """Simulate the given timesteps and return the ``RelativeHumidity`` output at each.

    Args:
        component: a prepared weather component.
        timesteps: the timesteps of the run to simulate.

    Returns:
        List[float]: the published relative humidity per timestep, in percent.
    """
    stsv = SingleTimeStepValues(len(component.get_outputs()))
    values = []
    for timestep in timesteps:
        component.i_simulate(timestep, stsv, False)
        values.append(stsv.values[component.relative_humidity_output.global_index])
    return values


@pytest.fixture(name="no_shared_cache")
def fixture_no_shared_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the run's cache in the test's own directory, whatever the environment configures."""
    for variable in ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR"):
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.base
def test_every_data_source_ships_files_and_every_reader_names_its_humidity_column() -> None:
    """Each data source but ERA5 ships at least one file here, and every source says where its humidity is.

    Catches: a data source added to the enum without a humidity column, and a shipped file set moved
    away from the patterns the plausibility test below walks.
    """
    sources_with_files = {data_source for data_source, _ in ShippedWeatherFiles.all()}
    assert sources_with_files == set(WeatherDataSourceEnum) - {WeatherDataSourceEnum.ERA5}
    assert set(RelativeHumidity.FILE_COLUMNS) == set(WeatherDataSourceEnum)


@pytest.mark.base
@pytest.mark.parametrize(
    "data_source, relative_path", ShippedWeatherFiles.all(), ids=[path for _, path in ShippedWeatherFiles.all()]
)
def test_every_shipped_file_carries_a_plausible_relative_humidity(
    data_source: WeatherDataSourceEnum, relative_path: str
) -> None:
    """The raw humidity of every shipped file lies in 0 to 100 %, has no gap, varies and has a plausible mean.

    Catches: a file shipped without the humidity column, a column read from the wrong place (a
    temperature, a fraction instead of a percentage), and a file whose humidity was shifted against
    the climate it belongs to.
    """
    raw = calculation.read_source_data(
        inputs_for(data_source, str(ShippedWeatherFiles.WEATHER_DIRECTORY / relative_path))
    )
    humidity = raw[RelativeHumidity.COLUMN]
    low, high = ShippedWeatherFiles.band_of(relative_path)

    assert humidity.notna().all()
    assert humidity.min() >= 0.0
    assert humidity.max() <= 100.0
    assert low <= humidity.mean() <= high, humidity.mean()
    # The humidity of a real year swings between dry afternoons and saturated nights.
    assert humidity.std() > 5.0
    assert humidity.max() - humidity.min() > 40.0


@pytest.mark.base
@pytest.mark.parametrize(
    "data_source", list(WeatherDataSourceEnum), ids=[member.value for member in WeatherDataSourceEnum]
)
def test_the_component_publishes_the_humidity_of_every_data_source(
    data_source: WeatherDataSourceEnum, tmp_path: pathlib.Path, no_shared_cache: None
) -> None:
    """A one-day hourly run publishes the ``RelativeHumidity`` output, in percent, from each data source.

    ERA5 ships no file, so the test writes one whose humidity is 80 % throughout.

    Catches: the output not declared, not set in ``i_simulate``, or set from another series.
    """
    del no_shared_cache
    if data_source == WeatherDataSourceEnum.ERA5:
        source_path = write_era5_file(tmp_path)
    else:
        source_path = next(
            str(ShippedWeatherFiles.WEATHER_DIRECTORY / path)
            for source, path in ShippedWeatherFiles.all()
            if source == data_source
        )
    config = weather.WeatherConfig.for_data_file(
        "Weather",
        source_path + calculation.WeatherSourceFiles.SUFFIXES[data_source][0],
        data_source,
        -10.0,
    )
    component = prepared_weather(
        config, SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600), tmp_path
    )

    assert component.relative_humidity_output.unit == lt.Units.PERCENT
    assert component.relative_humidity_output.load_type == lt.LoadTypes.HUMIDITY
    values = published_humidity(component, list(range(24)))
    assert values == component.relative_humidity_list_in_percent[:24]
    assert all(0.0 <= value <= 100.0 for value in values)
    if data_source == WeatherDataSourceEnum.ERA5:
        assert values == [pytest.approx(80.0)] * 24


@pytest.mark.base
def test_a_july_window_reads_the_test_reference_years_rf_of_its_own_hour(
    tmp_path: pathlib.Path, no_shared_cache: None
) -> None:
    """A minutely July week of Aachen publishes, at 00:30 and 01:30, the file's ``RF`` of the first two July hours.

    The test reference year labels each hour at its middle, so the minute at 00:30 standard time of
    1 July is the sample of the file's row 4344 (hour 1 MEZ of 1 July) itself, and 01:30 is row 4345.
    The rows are read here from the ``.dat`` with a plain split, independently of the reader.

    Catches: a humidity laid onto the year from another row than the temperature, a window that reads
    January's humidity, and an ``RF`` column read from the wrong position.
    """
    del no_shared_cache
    config = weather.WeatherConfig.preset_aachen("Weather")
    component = prepared_weather(
        config, SimulationParameters.one_week_july(year=2021, seconds_per_timestep=60), tmp_path
    )
    with open(config.source_path + ".dat", encoding="utf-8") as data_file:
        lines = data_file.readlines()
    header_row = next(index for index, line in enumerate(lines) if line.split()[:3] == ["RW", "HW", "MM"])
    columns = lines[header_row].split()
    july_rows = [lines[header_row + 1 + row].split() for row in (4344, 4345)]
    assert [(row[columns.index("MM")], row[columns.index("DD")], row[columns.index("HH")]) for row in july_rows] == [
        ("7", "1", "1"),
        ("7", "1", "2"),
    ]
    file_humidity = [float(row[columns.index("RF")]) for row in july_rows]
    file_temperature = [float(row[columns.index("t")]) for row in july_rows]

    assert component.window_offset_in_timesteps == 4344 * 60
    assert published_humidity(component, [30, 90]) == pytest.approx(file_humidity, abs=1e-9)
    assert [component.temperature_list[4344 * 60 + 30], component.temperature_list[4344 * 60 + 90]] == pytest.approx(
        file_temperature, abs=1e-9
    )


@pytest.mark.base
def test_a_july_window_reads_the_dwd_quarter_hourly_humidity_of_its_own_row(
    tmp_path: pathlib.Path, no_shared_cache: None
) -> None:
    """A minutely July week from the Duesseldorf file publishes its ``relative_humidity`` of the July rows.

    The file of 2024 is laid onto 2021 by position, one quarter hour per row from 1 January 00:00 UTC,
    so 1 July 2021 00:00 is the file's row 17376 and 00:15 its row 17377 -- the same rows the
    temperature is read from.

    Catches: a sub-hourly humidity read from another row than the temperature, and a window that
    reads January's humidity.
    """
    del no_shared_cache
    relative_path = "dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv"
    path = str(ShippedWeatherFiles.WEATHER_DIRECTORY / relative_path)
    config = weather.WeatherConfig.for_data_file("Weather", path, WeatherDataSourceEnum.DWD_15MIN, -10.0)
    component = prepared_weather(
        config, SimulationParameters.one_week_july(year=2021, seconds_per_timestep=60), tmp_path
    )
    file_rows = pd.read_csv(path, skiprows=[0, 1]).iloc[[17376, 17377]]

    assert published_humidity(component, [0, 15]) == pytest.approx(list(file_rows["relative_humidity"]), abs=1e-9)
    offset = component.window_offset_in_timesteps
    assert [component.temperature_list[offset], component.temperature_list[offset + 15]] == pytest.approx(
        list(file_rows["temperature"]), abs=1e-9
    )
    assert component.relative_humidity_list_in_percent[offset: offset + 16] != (
        component.relative_humidity_list_in_percent[:16]
    )


@pytest.mark.base
def test_an_hourly_source_starts_the_year_at_its_first_humidity_and_not_at_zero() -> None:
    """The half hour before the first hourly sample holds that sample, where the irradiances ramp up from zero.

    Catches: the zero padding of the irradiances applied to the humidity, which would publish air at
    0 % on the first minutes of every hourly run.
    """
    inputs = inputs_for(
        WeatherDataSourceEnum.DWD_TRY,
        weather.WeatherConfig.preset_aachen("Weather").source_path,
        seconds_per_timestep=60,
    )
    raw = calculation.read_source_data(inputs)
    one_minute = calculation.resample_to_one_minute(raw, inputs)[RelativeHumidity.COLUMN]

    first_sample = raw[RelativeHumidity.COLUMN].iloc[0]
    assert list(one_minute.iloc[:31]) == pytest.approx([first_sample] * 31)


@pytest.mark.base
def test_a_file_without_humidity_is_refused_with_the_column_it_lacks(tmp_path: pathlib.Path) -> None:
    """A quarter-hourly DWD file written before the humidity was added is refused, naming the column.

    Catches: a missing humidity silently filled with a constant or with zeros.
    """
    path = tmp_path / "dwd_15min_without_humidity.csv"
    rows = pd.date_range("2021-01-01", periods=96, freq="15min")
    frame = pd.DataFrame(
        {
            "year": rows.year,
            "month": rows.month,
            "day": rows.day,
            "hour": rows.hour,
            "minute": rows.minute,
            "temperature": 5.0,
            "pressure": 1000.0,
            "wind_direction": 180.0,
            "wind_speed": 3.0,
            "diffuse_irradiance": 0.0,
            "global_irradiance": 0.0,
        }
    )
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("location,longitude,latitude\nSomewhere,6.08,50.77\n")
        frame.to_csv(stream, index=False)

    with pytest.raises(ValueError, match="no relative humidity column 'relative_humidity'"):
        calculation.read_source_data(inputs_for(WeatherDataSourceEnum.DWD_15MIN, str(path)))

    frame["relative_humidity"] = 80.0
    frame.loc[10, "relative_humidity"] = 120.0
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("location,longitude,latitude\nSomewhere,6.08,50.77\n")
        frame.to_csv(stream, index=False)
    with pytest.raises(ValueError, match="outside 0.0 to 100.0 % at 1 of 96 rows"):
        calculation.read_source_data(inputs_for(WeatherDataSourceEnum.DWD_15MIN, str(path)))
