"""The weather year, PRs A and B of ``roadmap/weather_year.md``.

PR A adds ``SimulationParameters.weather_year`` without behaviour: validated, keyed and recorded
only when set -- every unset key, record and file stays byte-identical -- and refused by the weather
component by name until selecting a year is implemented (hisim-wps1.3).

PR B reads the years a weather file declares (``WeatherSourceFiles.data_years``), publishes the
effective weather year into the simulation repository and says at INFO when the file's year is not
the calendar year it is laid onto by position.
"""

from __future__ import annotations

import datetime
import pathlib
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from hisim import log
from hisim import sim_repository
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.components.weather.calculation import WeatherDataSourceEnum, WeatherSourceFiles
from hisim.components.weather.weather import WeatherYearNotImplementedError
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.parameters_format import (
    ParameterFileName,
    ParameterFileWriter,
    ParameterNormalisation,
)
from hisim.simulationparameters import SimulationParameters, WeatherYearError
from hisim import utils

#: The shipped weather directory.
WEATHER_DIRECTORY = pathlib.Path(utils.get_input_directory()) / "weather"


def one_day(weather_year: Optional[int] = None, year: int = 2021) -> SimulationParameters:
    """One hourly day of ``year``, with the weather year given.

    Args:
        weather_year: the weather year, or None.
        year: the calendar year.

    Returns:
        The parameters.
    """
    return SimulationParameters(
        datetime.datetime(year, 1, 1),
        datetime.datetime(year, 1, 2),
        3600,
        weather_year=weather_year,
    )


# --------------------------------------------------------------------------------------------------
# PR A: the parameter
# --------------------------------------------------------------------------------------------------


@pytest.mark.base
@pytest.mark.parametrize("value", [1900, 2019, 2100, None])
def test_a_year_in_range_or_none_is_accepted(value: Optional[int]) -> None:
    """The range is inclusive at both ends, and ``None`` is the default."""
    assert one_day(value).weather_year == value
    assert one_day(value).year == 2021


@pytest.mark.base
@pytest.mark.parametrize("value", [1899, 2101, True, False, "2019", 2019.0])
def test_a_value_that_is_not_a_year_in_range_is_refused_by_name(value: Any) -> None:
    """Out of range, a bool (which Python accepts as an int), a string and a float are all refused."""
    with pytest.raises(WeatherYearError, match="weather_year"):
        one_day(value)


@pytest.mark.base
def test_the_unique_key_is_unchanged_when_unset_and_extended_when_set() -> None:
    """Every cache keyed on the parameters keeps its entries while the weather year is unset."""
    unset = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    assert unset.weather_year is None
    assert unset.get_unique_key() == "2021-01-01 00:00:00###2021-01-02 00:00:00###3600###2021###24###DE"
    assert unset.get_unique_key_as_list() == [
        "Start date: 2021-01-01 00:00:00",
        "End date: 2021-01-02 00:00:00",
        "Simulation year: 2021",
        "Seconds per timestep: 3600",
        "Total number of timesteps: 24",
        "Country: DE",
    ]

    set_ = one_day(2019)
    assert set_.get_unique_key() == unset.get_unique_key() + "###weather=2019"
    assert set_.get_unique_key_as_list() == unset.get_unique_key_as_list() + ["Weather year: 2019"]


@pytest.mark.base
def test_the_record_is_byte_identical_when_unset() -> None:
    """The parameter record and its name say nothing of a weather year nobody set."""
    normalised = ParameterNormalisation.normalise(one_day())
    assert ParameterNormalisation.WEATHER_YEAR_KEY not in normalised
    assert ParameterFileName.stem(normalised) == "one_day_hourly_plain"
    assert ParameterFileWriter.text(normalised, ParameterFileWriter.RUN_HEADER) == (
        "# one_day at hourly resolution, post-processing for plain. What this run\n"
        "# was given, written beside the realized record of what it built; the two together are\n"
        "# the arguments that re-run it. Machine-specific settings -- the cache and result\n"
        "# directories above all -- are deliberately absent.\n"
        'start_date: "2021-01-01T00:00:00"\n'
        'end_date: "2021-01-02T00:00:00"\n'
        "seconds_per_timestep: 3600\n"
        'country: "DE"\n'
        "logging_level: 3\n"
        "post_processing_options: []\n"
    )


@pytest.mark.base
def test_a_set_weather_year_is_compared_named_and_written() -> None:
    """Set, the weather year is part of the comparison, the file name and the file."""
    normalised = ParameterNormalisation.normalise(one_day(2019))
    assert normalised[ParameterNormalisation.WEATHER_YEAR_KEY] == 2019
    assert normalised != ParameterNormalisation.normalise(one_day())
    assert ParameterFileName.stem(normalised) == "one_day_hourly_plain_weather2019"
    assert "\nweather_year: 2019\n" in ParameterFileWriter.text(normalised)


@pytest.mark.base
@pytest.mark.parametrize("weather_year", [None, 2019])
def test_a_simulation_yaml_round_trips_with_and_without_the_key(
    tmp_path: pathlib.Path, weather_year: Optional[int]
) -> None:
    """What the writer writes, the reader reads back as the same parameters."""
    written = ParameterNormalisation.normalise(one_day(weather_year))
    path = tmp_path / "round_trip.simulation.yaml"
    path.write_text(ParameterFileWriter.text(written), encoding="utf-8")

    read = SimulationParametersReader.read(path)

    assert read.weather_year == weather_year
    assert read.year == 2021
    assert ParameterNormalisation.normalise(read) == written
    assert ("weather_year" in path.read_text(encoding="utf-8")) == (weather_year is not None)


@pytest.mark.base
def test_the_json_spelling_carries_the_key_too(tmp_path: pathlib.Path) -> None:
    """A ``*.simulation.json`` passes the key through ``SimulationParameters(**data)`` like the YAML."""
    path = tmp_path / "weather.simulation.json"
    path.write_text(
        '{"start_date": "2021-01-01T00:00:00", "end_date": "2021-01-02T00:00:00", '
        '"seconds_per_timestep": 3600, "weather_year": 2019}',
        encoding="utf-8",
    )
    assert SimulationParametersReader.read(path).weather_year == 2019


@pytest.mark.base
@pytest.mark.parametrize(
    "extra, match",
    [({"weather_yeer": 2019}, "weather_yeer"), ({"weather_year": 1850}, "weather_year")],
)
def test_an_unknown_key_and_a_bad_weather_year_are_refused_as_format_errors(
    extra: Dict[str, Any], match: str
) -> None:
    """A misspelt key is refused as before, and a bad weather year in a file names the file."""
    values = {"start_date": "2021-01-01T00:00:00", "end_date": "2021-01-02T00:00:00", "seconds_per_timestep": 3600}
    values.update(extra)
    with pytest.raises(EnergySystemFormatError, match=match) as refusal:
        SimulationParametersReader.build(values, "somewhere.simulation.yaml")
    assert "somewhere.simulation.yaml" in str(refusal.value)


@pytest.mark.base
def test_the_weather_refuses_a_set_weather_year_by_name() -> None:
    """Until selecting a year is implemented, the weather does not silently read by position."""
    with pytest.raises(WeatherYearNotImplementedError, match="weather_year=2019.*hisim-wps1.3"):
        weather.Weather(
            my_simulation_parameters=one_day(2019),
            config=weather.WeatherConfig.preset_aachen("Weather"),
        )


# --------------------------------------------------------------------------------------------------
# PR B: declared data years
# --------------------------------------------------------------------------------------------------


#: The years each shipped data set declares, as read from the files on 2026-09-27.
DECLARED_PER_SOURCE: Dict[WeatherDataSourceEnum, Optional[frozenset]] = {
    WeatherDataSourceEnum.DWD_TRY: None,
    WeatherDataSourceEnum.NSRDB: frozenset({2019}),
    WeatherDataSourceEnum.NSRDB_15MIN: frozenset({2019}),
}


@pytest.mark.base
@pytest.mark.parametrize("location", list(weather.LocationEnum), ids=lambda location: location.name)
def test_every_catalogue_station_declares_the_years_expected_of_its_source(location: Any) -> None:
    """Every shipped station of the catalogue: 2019 for both NSRDB sets, no year for a TRY."""
    config = weather.WeatherConfig.for_location("Weather", location, -10.0)
    assert WeatherSourceFiles.data_years(config.data_source, config.source_path) == (
        DECLARED_PER_SOURCE[config.data_source]
    )


@pytest.mark.base
@pytest.mark.parametrize(
    "relative, source, expected",
    [
        ("dwd_10min_data/dwd_10min_2021-2022_AACHEN_6.08_50.77.csv", WeatherDataSourceEnum.DWD_10MIN, {2021}),
        ("dwd_10min_data/dwd_10min_2023-2024_Aachen_6.08_50.77.csv", WeatherDataSourceEnum.DWD_10MIN, {2023}),
        ("dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv", WeatherDataSourceEnum.DWD_15MIN, {2024}),
    ],
)
def test_the_shipped_dwd_files_declare_their_year(
    relative: str, source: WeatherDataSourceEnum, expected: set
) -> None:
    """The DWD files outside the catalogue: each one holds a single year, whatever its name spans."""
    assert WeatherSourceFiles.data_years(source, str(WEATHER_DIRECTORY / relative)) == frozenset(expected)


@pytest.mark.base
def test_every_shipped_dwd_file_is_covered() -> None:
    """A new file in the two DWD directories is a new case above, not an unread one."""
    shipped = sorted(
        path.relative_to(WEATHER_DIRECTORY).as_posix()
        for directory in ("dwd_10min_data", "dwd_15min_data")
        for path in (WEATHER_DIRECTORY / directory).glob("*.csv")
    )
    assert shipped == [
        "dwd_10min_data/dwd_10min_2021-2022_AACHEN_6.08_50.77.csv",
        "dwd_10min_data/dwd_10min_2023-2024_Aachen_6.08_50.77.csv",
        "dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv",
    ]


@pytest.mark.base
def test_an_era5_file_declares_the_years_of_its_rows(tmp_path: pathlib.Path) -> None:
    """No ERA5 file ships, so a small one in the reader's layout stands in; several years are all read."""
    path = tmp_path / "era5.csv"
    path.write_text(
        "location,longitude,latitude\n"
        "Somewhere,6.0,50.0\n"
        "year,month,day,hour,minute,temperature\n"
        "2018,12,31,23,0,1.0\n"
        "2019,1,1,0,0,2.0\n",
        encoding="utf-8",
    )
    assert WeatherSourceFiles.data_years(WeatherDataSourceEnum.ERA5, str(path)) == frozenset({2018, 2019})


@pytest.mark.base
def test_a_file_without_its_year_column_is_refused(tmp_path: pathlib.Path) -> None:
    """A file in the wrong layout names the column it lacks rather than declaring nothing."""
    path = tmp_path / "no_year.csv"
    path.write_text("a\nb\nmonth,day\n1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'year'"):
        WeatherSourceFiles.data_years(WeatherDataSourceEnum.DWD_15MIN, str(path))


@pytest.mark.base
def test_the_years_are_read_once_per_file_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second question about an unchanged file is answered from the cache; a rewritten one is re-read."""
    reads: List[str] = []
    read_csv = pd.read_csv

    def counting_read_csv(path: Any, *args: Any, **kwargs: Any) -> Any:
        reads.append(str(path))
        return read_csv(path, *args, **kwargs)

    monkeypatch.setattr(calculation.pd, "read_csv", counting_read_csv)
    path = tmp_path / "era5.csv"
    header = "location,longitude,latitude\nSomewhere,6.0,50.0\nyear,month\n"
    path.write_text(header + "2019,1\n", encoding="utf-8")
    assert WeatherSourceFiles.data_years(WeatherDataSourceEnum.ERA5, str(path)) == frozenset({2019})
    assert WeatherSourceFiles.data_years(WeatherDataSourceEnum.ERA5, str(path)) == frozenset({2019})
    assert len(reads) == 1

    path.write_text(header + "2019,1\n2020,1\n", encoding="utf-8")
    assert WeatherSourceFiles.data_years(WeatherDataSourceEnum.ERA5, str(path)) == frozenset({2019, 2020})
    assert len(reads) == 2


def prepared_weather(
    tmp_path: pathlib.Path, location: Any, year: int, monkeypatch: pytest.MonkeyPatch
) -> tuple:
    """Run ``i_prepare_simulation`` of a weather over ``location`` in ``year``, from a filed cache entry.

    The series come from a small frame filed under the component's own key, so that the test measures
    what the preparation publishes and logs rather than waiting for the producer.

    Returns:
        The repository and the INFO messages logged.
    """
    parameters = one_day(year=year)
    parameters.cache_dir_path = str(tmp_path)
    config = weather.WeatherConfig.for_location("Weather", location, -10.0)
    component = weather.Weather(my_simulation_parameters=parameters, config=config)
    repository = sim_repository.SimRepository()
    component.set_sim_repo(repository)
    location_dict = weather.get_coordinates(filepath=config.source_path, source_enum=config.data_source)
    entry = component.cache_entry(component.series_cache_key(component.build_calculation_inputs(location_dict)))
    frame = pd.DataFrame({column: [0.0] * 24 for column in calculation.PRODUCED_COLUMNS})
    with entry.writing() as temporary_cache_filepath:
        frame.to_csv(temporary_cache_filepath, index=False)

    messages: List[str] = []
    monkeypatch.setattr(log, "information", lambda message, *args, **kwargs: messages.append(message))
    component.i_prepare_simulation()
    return repository, messages


@pytest.mark.base
def test_a_2021_run_over_a_2019_file_says_so_at_info(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mismatch is one INFO line, and the file's year is what the run's weather is published as."""
    monkeypatch.setattr(log, "warning", lambda *args, **kwargs: pytest.fail("the mismatch is not a warning"))
    repository, messages = prepared_weather(tmp_path, weather.LocationEnum.DE, 2021, monkeypatch)

    notes = [message for message in messages if "by position" in message]
    assert notes == [
        "weather of 2019 laid onto 2021 by position; set weather_year to select a year (hisim-wps1.3)"
    ]
    assert repository.get_entry(weather.Weather.WEATHER_YEAR_EFFECTIVE_KEY) == 2019


@pytest.mark.base
def test_a_run_in_the_files_own_year_says_nothing(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 2019 run over a 2019 file is what the file is, and is not remarked on."""
    repository, messages = prepared_weather(tmp_path, weather.LocationEnum.DE, 2019, monkeypatch)
    assert not [message for message in messages if "by position" in message]
    assert repository.get_entry(weather.Weather.WEATHER_YEAR_EFFECTIVE_KEY) == 2019


@pytest.mark.base
def test_a_test_reference_year_has_no_weather_year(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TRY belongs to no year: nothing is remarked on and the effective year is None."""
    repository, messages = prepared_weather(tmp_path, weather.LocationEnum.AACHEN, 2021, monkeypatch)
    assert not [message for message in messages if "by position" in message]
    assert repository.get_entry(weather.Weather.WEATHER_YEAR_EFFECTIVE_KEY) is None


@pytest.mark.base
def test_an_unreadable_year_leaves_an_unset_run_as_it_was(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A data file whose year cannot be read (a user's own file without the column) still runs unset."""

    def unreadable(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("no year column 'Year'")

    monkeypatch.setattr(calculation.WeatherSourceFiles, "data_years", classmethod(unreadable))
    repository, messages = prepared_weather(tmp_path, weather.LocationEnum.DE, 2021, monkeypatch)
    assert repository.get_entry(weather.Weather.WEATHER_YEAR_EFFECTIVE_KEY) is None
    assert [message for message in messages if "no readable year" in message]


@pytest.mark.base
@pytest.mark.parametrize(
    "weather_year, declared, expected",
    [
        (None, None, None),
        (None, frozenset({2019}), 2019),
        (None, frozenset({2019, 2020}), None),
        (2018, frozenset({2019}), 2018),
        (2018, None, 2018),
    ],
)
def test_the_effective_weather_year(
    weather_year: Optional[int], declared: Optional[frozenset], expected: Optional[int]
) -> None:
    """A set weather year wins; otherwise the file's single year; otherwise nothing."""
    assert weather.Weather.effective_weather_year(weather_year, declared) == expected
