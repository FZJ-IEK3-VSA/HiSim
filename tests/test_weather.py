"""Tests for the Weather component and WeatherConfig.

Covers full-year DNI output sanity checks, enum-vs-string location
configuration consistency, direct-filepath configuration including
validation that a data source is required when a direct filepath is given,
and the one schema the component reads a produced frame under.
"""
import importlib
import pathlib
import pickle

import pandas as pd
import pytest
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.simulationparameters import SimulationParameters
from hisim.config import DisplayConfig
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

    # Add Global Index to component outputs.
    fft.add_global_index_of_components([my_weather])
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    # Simulate.  The global_index is constant for the whole simulation, so
    # hoist the attribute lookup out of the 525 600-iteration loop.  A running
    # accumulator adds the per-timestep DNI values in the same left-to-right
    # order as sum(dni) would, so the result is bit-identical while avoiding a
    # ~525 K-element list allocation and a second O(n) reduction pass.
    idx: int = my_weather.dni_output.global_index
    total_dni: float = 0.0
    for i in range(60 * 24 * 365):
        my_weather.i_simulate(i, stsv, False)
        total_dni += stsv.values[idx]

    # Each per-timestep DNI value is an irradiance in W/m^2. Summing it
    # across timesteps gives W/m^2*timesteps; multiply by the timestep
    # length (seconds) to get W*s/m^2, then convert W*s to kWh so the
    # compared quantity is an annual energy density in kWh/m^2/year.
    seconds_per_hour: int = 3600
    watt_second_to_kwh: float = 1 / (1000 * seconds_per_hour)  # W*s -> kWh
    annual_dni_kwh_per_m2: float = (
        total_dni * mysim.seconds_per_timestep * watt_second_to_kwh
    )
    assert annual_dni_kwh_per_m2 > 950  # kWh/m^2/year


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


def _build_weather_with_cache(
    tmp_path: pathlib.Path,
    omitted_column: str = "",
) -> weather.Weather:
    """Build a Weather component whose cache file has already been written.

    Writes a small cache CSV into *tmp_path* so that
    ``i_prepare_simulation`` takes the cached-file branch instead of
    re-processing the raw weather data.  The frame carries every column the
    producer produces, except *omitted_column* when one is named.

    The entry goes in through the cache entry's own ``writing`` rather than
    straight to the path, because a lookup only counts an entry as present when
    the companion metadata beside it hashes to the name it is filed under.
    Writing the CSV alone would leave an entry nothing describes, which the
    lookup deletes on sight, and the cached branch this fixture exists to reach
    would never be taken.  The entry is filed under the producer key the
    component itself derives, so the fixture cannot drift away from the key the
    component looks up.
    """
    mysim = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    mysim.cache_dir_path = str(tmp_path)
    my_config: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather: weather.Weather = weather.Weather(
        config=my_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(sim_repository.SimRepository())

    location_dict = weather.get_coordinates(
        filepath=my_config.source_path, source_enum=my_config.data_source
    )
    entry = my_weather.cache_entry(
        my_weather.series_cache_key(my_weather.build_calculation_inputs(location_dict))
    )
    columns = [column for column in calculation.PRODUCED_COLUMNS if column != omitted_column]
    data = {col: [float(i) * 10 for i in range(5)] for col in columns}
    with entry.writing() as temporary_cache_filepath:
        pd.DataFrame(data).to_csv(temporary_cache_filepath, index=False)
    return my_weather


@pytest.mark.base
def test_the_package_answers_to_every_name_it_publishes_and_to_no_other() -> None:
    """The lazy facade resolves each public name, from the module it says, and refuses the rest.

    The package imports none of its submodules at import time (PEP 562), so that importing the
    producer does not drag the component and the post-processing in with it. The names still have to
    resolve -- ``from hisim.components.weather import Weather``, ``importlib.import_module`` plus
    ``getattr`` on a recorded class path, ``describe``, pickling -- and a name that is not published
    has to fail like a missing module attribute rather than silently.
    """
    published = getattr(weather, "_MODULE_PER_NAME")
    assert sorted(weather.__all__) == sorted(published)
    for name, module_name in published.items():
        resolved = getattr(weather, name)
        assert resolved is getattr(importlib.import_module(module_name), name), name

    with pytest.raises(AttributeError, match="produce_weather_series"):
        getattr(weather, "produce_weather_series")


@pytest.mark.base
def test_the_component_class_carries_the_package_as_its_public_name() -> None:
    """``Weather.__module__`` is the package, however the class was reached.

    33 recorded twins and the generated JSON schema spell the weather
    ``hisim.components.weather.Weather``, and both ``get_full_classname`` and the recorder read
    ``__module__`` to say so. The pin sits beside the class rather than in the lazy facade, because a
    direct import of the submodule does not run the facade.
    """
    from hisim.components.weather.weather import Weather as DirectlyImported  # pylint: disable=import-outside-toplevel

    assert DirectlyImported.__module__ == "hisim.components.weather"
    assert DirectlyImported.get_full_classname() == "hisim.components.weather.Weather"
    assert pickle.loads(pickle.dumps(DirectlyImported)) is DirectlyImported


@pytest.mark.base
def test_the_component_reads_exactly_the_columns_the_producer_writes() -> None:
    """The frame has one schema, and both ends of it are the same list.

    ``Weather.LIST_ATTRIBUTE_PER_COLUMN`` is what the component reads; ``PRODUCED_COLUMNS`` is what the
    producer writes. A column in one and not the other is either a series nothing reads or a read that
    fails mid-run, and both used to be possible because the reader spelled its columns out one literal
    at a time.
    """
    assert set(weather.Weather.LIST_ATTRIBUTE_PER_COLUMN) == set(calculation.PRODUCED_COLUMNS)
    assert len(set(weather.Weather.LIST_ATTRIBUTE_PER_COLUMN.values())) == len(calculation.PRODUCED_COLUMNS)


@pytest.mark.base
def test_weather_cache_missing_column_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """A cached frame that lacks a produced column stops the run and says which file to look at.

    An entry filed under the producer's key was written by that producer and carries every column it
    writes, so a frame without one was not written by it. Reading it anyway -- the pressure used to be
    filled in with zeros and a warning -- puts a fabricated series into a run whose results are then
    reported as if they had been computed.
    """
    my_weather = _build_weather_with_cache(tmp_path, omitted_column="Pressure")

    with pytest.raises(KeyError, match="Pressure") as refusal:
        my_weather.i_prepare_simulation()
    assert str(tmp_path) in str(refusal.value)


@pytest.mark.base
def test_weather_cache_pressure_present_read_from_cache(
    tmp_path: pathlib.Path,
) -> None:
    """A cached weather file with a 'Pressure' column reads the cached values."""
    my_weather = _build_weather_with_cache(tmp_path)
    my_weather.i_prepare_simulation()
    assert my_weather.pressure_list == pytest.approx([0.0, 10.0, 20.0, 30.0, 40.0])


@pytest.mark.base
def test_weather_default_display_config_is_not_shared() -> None:
    """Two Weather instances using the default display config get distinct instances.

    Regression test for the mutable default argument ``DisplayConfig()`` in
    ``Weather.__init__``: each call relying on the default must construct a
    fresh ``DisplayConfig`` rather than sharing a single definition-time
    instance (see KB-3284 for the identity-isolation testing approach).
    """
    mysim: SimulationParameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=3600
    )
    my_config: weather.WeatherConfig = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    first: weather.Weather = weather.Weather(
        config=my_config, my_simulation_parameters=mysim
    )
    second: weather.Weather = weather.Weather(
        config=my_config, my_simulation_parameters=mysim
    )

    assert isinstance(first.my_display_config, DisplayConfig)
    assert isinstance(second.my_display_config, DisplayConfig)
    # Identity check: must not be the same shared instance.
    assert first.my_display_config is not second.my_display_config

    # Mutation must not propagate across instances.
    first.my_display_config.pretty_name = "first"
    assert second.my_display_config.pretty_name is None
