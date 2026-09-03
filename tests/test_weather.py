"""Tests for the Weather component and WeatherConfig.

Covers full-year DNI output sanity checks, enum-vs-string location
configuration consistency, direct-filepath configuration including
validation that a data source is required when a direct filepath is given,
and the cached-file pressure-column fallback in ``i_prepare_simulation``.
"""
import math
import pathlib

import pandas as pd
import pvlib
import pytest
from hisim import sim_repository
from hisim import component
from hisim import utils
from hisim.caching import atomic_cache_write
from hisim.components import weather
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


# Columns that the cached-file branch of ``Weather.i_prepare_simulation`` reads
# before the optional "Pressure" column.
_CACHE_COLUMNS = [
    "t_out",
    "t_out_daily_average",
    "DHI",
    "DNI",
    "DNIextra",
    "GHI",
    "altitude",
    "azimuth",
    "apparent_zenith",
    "Wspd",
]


def _build_weather_with_cache(
    tmp_path: pathlib.Path,
    include_pressure: bool = True,
) -> weather.Weather:
    """Build a Weather component whose cache file has already been written.

    Writes a small cache CSV into *tmp_path* so that
    ``i_prepare_simulation`` takes the cached-file branch instead of
    re-processing the raw weather data.  When *include_pressure* is ``True``
    a ``Pressure`` column is added; otherwise it is omitted.

    The entry goes in through ``atomic_cache_write`` rather than straight to the
    path, because ``get_cache_file`` only counts an entry as present when the
    companion metadata beside it hashes to the name it is filed under.  Writing
    the CSV alone would leave an entry nothing describes, which the lookup
    deletes on sight, and the cached-file branch this fixture exists to reach
    would never be taken.
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

    _, cache_filepath = utils.get_cache_file(
        my_weather.config.component_id.name, my_config, mysim
    )
    columns = list(_CACHE_COLUMNS)
    if include_pressure:
        columns.append("Pressure")
    data = {col: [float(i) * 10 for i in range(5)] for col in columns}
    with atomic_cache_write(
        cache_filepath, utils.build_cache_key_string(my_config, mysim)
    ) as temporary_cache_filepath:
        pd.DataFrame(data).to_csv(temporary_cache_filepath, index=False)
    return my_weather


@pytest.mark.base
def test_weather_cache_pressure_missing_falls_back_to_zeros(
    tmp_path: pathlib.Path,
) -> None:
    """A cached weather file without a 'Pressure' column falls back to zeros.

    This exercises the ``KeyError`` fallback in the cached-file branch of
    ``i_prepare_simulation``: when the column is absent pandas raises
    ``KeyError``, which is caught and replaced with a zero array.
    """
    my_weather = _build_weather_with_cache(tmp_path, include_pressure=False)
    my_weather.i_prepare_simulation()
    assert my_weather.pressure_list == [0] * 5
    assert len(my_weather.pressure_list) == len(my_weather.wind_speed_list)


@pytest.mark.base
def test_weather_cache_pressure_present_read_from_cache(
    tmp_path: pathlib.Path,
) -> None:
    """A cached weather file with a 'Pressure' column reads the cached values."""
    my_weather = _build_weather_with_cache(tmp_path, include_pressure=True)
    my_weather.i_prepare_simulation()
    assert my_weather.pressure_list == pytest.approx([0.0, 10.0, 20.0, 30.0, 40.0])


@pytest.mark.base
def test_weather_cache_pressure_non_keyerror_propagates(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-``KeyError`` while reading 'Pressure' from cache must propagate.

    Before narrowing the ``except`` clause to ``KeyError``, any exception
    (e.g. a corrupted cache, dtype problem) was silently swallowed and
    replaced with a zero array.  This test verifies that a ``ValueError``
    now propagates instead of being masked.
    """
    my_weather = _build_weather_with_cache(tmp_path, include_pressure=True)

    original_read_csv = weather.pd.read_csv

    class _PressureCorruptDataFrame(pd.DataFrame):
        """DataFrame whose ``'Pressure'`` access raises ``ValueError``."""

        _metadata = pd.DataFrame._metadata  # pylint: disable=protected-access

        def __getitem__(self, key: object) -> pd.Series:  # type: ignore[override]
            if key == "Pressure":
                raise ValueError("simulated cache corruption")
            return super().__getitem__(key)

    def fake_read_csv(*args: object, **kwargs: object) -> _PressureCorruptDataFrame:
        df = original_read_csv(*args, **kwargs)
        return _PressureCorruptDataFrame(df)

    monkeypatch.setattr(weather.pd, "read_csv", fake_read_csv)
    with pytest.raises(ValueError, match="simulated cache corruption"):
        my_weather.i_prepare_simulation()


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


@pytest.mark.base
def test_the_zenith_clamp_bounds_the_direct_normal_irradiance_near_the_horizon() -> None:
    """The clamp at ``zenith_tol`` must actually apply, so DNI is bounded and never negative.

    The direct normal irradiance is the horizontal one divided by the cosine of the zenith angle, which
    explodes toward the horizon and turns negative past it. ``zenith_tol`` exists to cap the angle before
    the division. It used to be written as a chained assignment, which under copy-on-write writes into a
    temporary and is discarded -- pandas reports it, and every golden log carried the report -- so the cap
    never applied and low-sun timesteps went out unbounded. A constant, deliberately non-zero horizontal
    irradiance over a full day makes the failure unmistakable: night timesteps come out negative and the
    dusk ones enormous.

    Catches: the clamp regressing to a form that does not write, which nothing else in the suite notices.
    """
    index = pd.date_range("2021-06-21", periods=24 * 12, freq="5min", tz="UTC")
    horizontal = pd.Series(50.0, index=index)
    zenith_tol = 87.0

    dni = weather.calculate_direct_normal_radiation(horizontal, lon=6.08, lat=50.78, zenith_tol=zenith_tol)

    bound = 50.0 / math.cos(math.radians(zenith_tol))
    zenith = pvlib.solarposition.get_solarposition(index, 50.78, 6.08)["apparent_zenith"]
    assert (zenith > zenith_tol).any(), "the day must contain low-sun timesteps for the clamp to matter"
    assert (dni >= 0).all(), "past the horizon the divisor went negative, so the clamp did not apply"
    assert dni.max() <= bound + 1e-9, f"DNI {dni.max():.1f} exceeds the clamped bound {bound:.1f}"
    assert dni[zenith > zenith_tol].round(6).nunique() == 1, "every clamped timestep must divide by the same cosine"
