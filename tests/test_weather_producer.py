"""Tests for the weather producer: the layering rule it obeys and the key its code is fingerprinted into.

The weather series is the first artifact keyed under ``roadmap/cache_service_spec.md`` §3, and it is
keyed that way because of the #628 cache finding: a fix to the direct normal irradiance moved every KPI
it touched locally -- 33 of them at the time -- and none in CI, where the cache directory is restored
from the previous run and the old key, a hash of the configuration and the simulation parameters, still
matched. These tests pin the promises that make that impossible now. The producer's import closure stays
small and free of component machinery, so hashing all of it is affordable; an edit to the producer's
source moves the key; the inputs identify the data file by its contents rather than by where the
checkout happens to be, and carry the simulated span only for the reader that is sized by it; and the
series the producer computes are pinned by value, so a regression in the calculation itself fails here
rather than waiting for a golden pair.
"""

# clean

import dataclasses
import importlib
import pathlib
import subprocess
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict, Optional

import pytest

from hisim import sim_repository
from hisim.caching import CacheKey, Fingerprints, ImportClosure, ProducerLayering
from hisim.components import weather
from hisim.components.weather import calculation
from hisim.components.weather.calculation import (
    ARTIFACT_KIND,
    WeatherDataSourceEnum,
    WeatherSeriesInputs,
    WeatherSourceFiles,
)
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ProducerCopy:
    """A copy of the real producer module in a package of its own, so a test may edit it.

    The point of copying rather than writing a toy module is that the fingerprint is measured on the
    code that actually computes the weather: a test that only proves a synthetic module's hash moves
    would pass even if the real producer had been made unhashable. The copy lives under its own root
    package, which is also the root the closure is computed against, so the real ``hisim`` imports
    inside it are recorded as outside names rather than followed -- :meth:`code_fingerprint` hashes
    the sources of the copied package alone.
    """

    #: The package the copy lives in.
    ROOT: ClassVar[str] = "weather_producer_probe"

    #: The module name the producer is copied to.
    PRODUCER: ClassVar[str] = "producer"

    def __init__(self, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the package and put it on the import path for the test's lifetime.

        Args:
            directory: the directory to write the package into.
            monkeypatch: used to extend ``sys.path``.
        """
        self.directory = directory / self.ROOT
        self.directory.mkdir()
        (self.directory / "__init__.py").write_text("", encoding="utf-8")
        self.write(self.PRODUCER, pathlib.Path(calculation.__file__).read_text(encoding="utf-8"))
        monkeypatch.syspath_prepend(str(directory))

    def write(self, module: str, source: str) -> None:
        """Write or overwrite one module of the package.

        Args:
            module: the module name below the root.
            source: its source.
        """
        (self.directory / f"{module}.py").write_text(source, encoding="utf-8")

    def source(self, module: str) -> str:
        """Return one module's current source.

        Args:
            module: the module name below the root.

        Returns:
            str: the source.
        """
        return (self.directory / f"{module}.py").read_text(encoding="utf-8")

    def load(self, module: str) -> ModuleType:
        """Import one module of the package freshly.

        Args:
            module: the module name below the root.

        Returns:
            ModuleType: the imported module.
        """
        for name in list(sys.modules):
            if name == self.ROOT or name.startswith(self.ROOT + "."):
                del sys.modules[name]
        importlib.invalidate_caches()
        return importlib.import_module(f"{self.ROOT}.{module}")

    def code_fingerprint(self) -> str:
        """Return the code fingerprint of the copied producer as it stands on disk.

        Returns:
            str: the fingerprint.
        """
        return Fingerprints.code(ImportClosure.of(self.load(self.PRODUCER), root_package=self.ROOT))


def calculation_inputs(**overrides: Any) -> WeatherSeriesInputs:
    """A baseline DTO for the Aachen test reference year, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        WeatherSeriesInputs: the DTO.
    """
    baseline: Dict[str, Any] = {
        "data_source": WeatherDataSourceEnum.DWD_TRY,
        "source_content_hash": "0" * 64,
        "year": 2021,
        "seconds_per_timestep": 900,
        "latitude_in_degrees": 50.78,
        "longitude_in_degrees": 6.09,
        "duration_in_days": None,
        "source_path": "/home/somebody/HiSim/hisim/inputs/weather/aachen_center",
    }
    baseline.update(overrides)
    return WeatherSeriesInputs(**baseline)


def digest_of(inputs: WeatherSeriesInputs) -> str:
    """The cache key digest the weather component would look the series up under.

    Args:
        inputs: the calculation's inputs.

    Returns:
        str: the digest.
    """
    return CacheKey.for_producer(ARTIFACT_KIND, calculation, inputs).digest


@pytest.mark.base
def test_the_producer_obeys_the_layering_rule() -> None:
    """The producer imports nothing from the component or simulator machinery, and little else.

    This is the lint the whole scheme rests on (spec §3): the fingerprint hashes 100% of the closure,
    which is only affordable because the closure is tiny. The set is asserted exactly, not merely
    checked for violations, because under §3 every import carries a visible cache cost: each module in
    the closure is hashed into the key, so every edit to any of them throws away every cached weather
    series on every machine, whether or not it could have changed a number. An import that is worth
    that price can be added here in the same commit that makes it; one that is not has to stay out of
    the producer, and a plain value through the DTO is the way in.

    Catches: a producer reaching for ``Component``, ``loadtypes`` or the singleton repository, which
    would make it uncallable without a simulator and its key hostage to unrelated edits.
    """
    closure = ImportClosure.of(calculation)

    ProducerLayering.check(closure)
    assert not ProducerLayering.violations(closure)
    assert set(closure.package_modules) == {
        "hisim.components.weather.calculation",
        "hisim.caching.keys",
    }, (
        "every HiSim module in the closure is hashed into the weather's cache key, so adding one means "
        "its edits invalidate every cached weather series; pass plain values through the DTO instead"
    )
    assert set(closure.third_party_top_levels) == {"numpy", "pandas", "pvlib"}
    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_importing_the_producer_loads_neither_the_component_nor_the_post_processing() -> None:
    """The layering rule is about loading as much as about hashing.

    The closure keeps the fingerprint small; this keeps the import cheap. A prewarm run fills the
    cache by importing ``hisim.components.weather.calculation``, and the component, the simulator and
    the post-processing must not come with it -- which is why the package facade resolves its names
    lazily. Checked in a fresh interpreter, because in this one everything is imported already.

    Catches: an eager re-export in the package ``__init__``, which is how the component used to be
    pulled in by every importer of the producer.
    """
    probe = (
        "import hisim.components.weather.calculation, sys; "
        "print(' '.join(sorted(m for m in sys.modules if m.startswith('hisim.'))))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.split()

    assert "hisim.components.weather.calculation" in loaded
    for module_name in ("hisim.component", "hisim.components.weather.weather", "hisim.simulator"):
        assert module_name not in loaded, module_name
    assert not [module_name for module_name in loaded if module_name.startswith("hisim.postprocessing")]


@pytest.mark.base
def test_an_edit_to_the_producer_changes_its_code_fingerprint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One changed constant in the producer's own source is a different fingerprint, with no bump anywhere.

    The edit is the kind #628 made: a constant inside the direct-normal-irradiance calculation. Under
    the old key nothing about it reached the filename, so CI kept serving the frame computed before the
    fix; under this key the frame is filed somewhere else the moment the line changes.

    Catches: a fingerprint that hashes anything other than the producer's current source -- a module
    name, an mtime, a stale ``__pycache__`` -- which would put the staleness back.
    """
    probe = ProducerCopy(tmp_path, monkeypatch)
    before = probe.code_fingerprint()

    source = probe.source(ProducerCopy.PRODUCER)
    edited = source.replace("zenith_tol_in_degrees: float = 87.0", "zenith_tol_in_degrees: float = 88.0")
    assert edited != source, "the line the edit targets has moved; point this test at another constant"
    probe.write(ProducerCopy.PRODUCER, edited)

    assert probe.code_fingerprint() != before


@pytest.mark.base
def test_the_data_file_enters_the_key_by_its_contents_and_not_by_its_path() -> None:
    """Two checkouts of the same file share a key; two different files do not.

    Spec §3.1: an input data file is referenced by content hash, and the path travels as a payload
    field that is excluded from the key material. That is what lets a laptop, the cluster and a
    container share one entry, and what makes an edited weather file invalidate everything downstream
    without any version discipline.

    Catches: the path slipping back into the key (no cross-machine hit ever again), or the content hash
    falling out of it (an edited data file served from the old entry).
    """
    here = calculation_inputs()
    there = calculation_inputs(source_path="/opt/container/hisim/inputs/weather/aachen_center")
    other_data = calculation_inputs(source_content_hash="1" * 64)

    assert digest_of(here) == digest_of(there)
    assert digest_of(here) != digest_of(other_data)
    assert here.source_path not in CacheKey.for_producer(ARTIFACT_KIND, calculation, here).material


@pytest.mark.base
def test_what_changes_the_series_changes_the_key_and_what_does_not_does_not() -> None:
    """Every DTO field moves the key, and the simulated span moves it only where a reader reads it.

    The duration is key material for ``DWD_15MIN`` alone, whose reader sizes its raw frame by it. For
    every other source the produced frame covers the full year whatever the run's span, so a one-week
    run and a one-year run of the same station deliberately share one entry.

    Catches: a field that decides the series being left out of the DTO (two different series under one
    key), and the simulated span being hashed for sources that do not read it (a cache that never hits
    across horizons).
    """
    baseline = calculation_inputs()

    assert digest_of(baseline) != digest_of(calculation_inputs(year=2022))
    assert digest_of(baseline) != digest_of(calculation_inputs(seconds_per_timestep=60))
    assert digest_of(baseline) != digest_of(calculation_inputs(latitude_in_degrees=41.9))
    assert digest_of(baseline) != digest_of(calculation_inputs(longitude_in_degrees=12.5))
    assert digest_of(baseline) != digest_of(calculation_inputs(data_source=WeatherDataSourceEnum.NSRDB))

    quarter_hourly = calculation_inputs(data_source=WeatherDataSourceEnum.DWD_15MIN, duration_in_days=7)
    assert digest_of(quarter_hourly) != digest_of(dataclasses.replace(quarter_hourly, duration_in_days=365))


@pytest.mark.base
def test_the_component_carries_the_simulated_span_only_for_the_reader_sized_by_it() -> None:
    """What the component puts in the DTO decides whether two horizons share an entry.

    The claim above -- that a one-week run and a one-year run of an ordinary station share one cached
    series -- is not a property of the DTO but of what ``build_calculation_inputs`` fills in, so it is
    asserted there: the same station over two different spans has to produce the same inputs, and
    therefore the same key, for a source whose reader never sees the span.

    Catches: the duration folded into the inputs for every source (no cache hit across horizons ever
    again), and dropped for ``DWD_15MIN``, whose reader builds exactly ``24 * 4 * days`` rows and would
    otherwise be asked for a frame of no length at all.
    """
    one_day = _inputs_of_a_run(SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600))
    one_week = _inputs_of_a_run(SimulationParameters.one_week_only(year=2021, seconds_per_timestep=3600))

    assert one_day.duration_in_days is None
    assert one_week.duration_in_days is None
    assert digest_of(one_day) == digest_of(one_week)

    quarter_hourly = _inputs_of_a_run(
        SimulationParameters.one_week_only(year=2021, seconds_per_timestep=3600),
        data_source=WeatherDataSourceEnum.DWD_15MIN,
    )
    assert quarter_hourly.duration_in_days == 7


@pytest.mark.base
def test_the_inputs_refuse_a_span_that_does_not_match_the_reader() -> None:
    """Both ways of getting the span wrong are refused where the DTO is built.

    An unset span for ``DWD_15MIN`` would size its raw frame by nothing; a set span for a source that
    reads the whole year would file the same series under one key per horizon, because the field is key
    material whether or not a reader reads it. Neither shows up in the result, so neither is allowed to
    be built.

    Catches: a source added to ``DURATION_DEPENDENT_SOURCES`` without the component being taught to
    fill the field, and a caller passing the run's length in for every source because it is there.
    """
    with pytest.raises(ValueError, match="duration_in_days"):
        calculation_inputs(data_source=WeatherDataSourceEnum.DWD_15MIN, duration_in_days=None)

    with pytest.raises(ValueError, match="duration_in_days"):
        calculation_inputs(data_source=WeatherDataSourceEnum.DWD_TRY, duration_in_days=7)


def _inputs_of_a_run(
    parameters: SimulationParameters,
    data_source: WeatherDataSourceEnum = WeatherDataSourceEnum.DWD_TRY,
) -> WeatherSeriesInputs:
    """The DTO a weather component over the Aachen station builds for one run.

    The station header is passed in rather than read: ``build_calculation_inputs`` takes it as an
    argument, and the point here is which of the simulation parameters reach the inputs.

    Args:
        parameters: the run's simulation parameters.
        data_source: the data source to configure; the default is the station's own.

    Returns:
        WeatherSeriesInputs: the inputs the component would look its series up under.
    """
    config = weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN)
    if data_source != config.data_source:
        # Every source but DWD_TRY names the data file itself rather than the stem of a pair, and the
        # content hash insists the file is there; the station's own ``.dat`` is a file like any other.
        config = dataclasses.replace(config, data_source=data_source, source_path=config.source_path + ".dat")
    component: weather.Weather = weather.Weather(config=config, my_simulation_parameters=parameters)
    inputs: WeatherSeriesInputs = component.build_calculation_inputs(
        {"name": "Aachen", "latitude": 50.78, "longitude": 6.09}
    )
    return inputs


@pytest.mark.base
def test_the_content_hash_covers_every_file_the_reader_may_open(tmp_path: pathlib.Path) -> None:
    """A DWD test reference year is a ``.dat`` and an optional ``.csv``, and both decide the result.

    The ``.csv``, where one has been generated beside the ``.dat``, is read *instead* of it and already
    carries a computed DNI, so its appearance changes the series completely. It has to be part of the
    hash by its absence as much as by its contents.

    Catches: hashing only the file that happens to exist, which would serve the ``.dat``-derived series
    after a ``.csv`` appeared beside it.
    """
    stem = str(tmp_path / "station")
    pathlib.Path(stem + ".dat").write_text("header\n", encoding="utf-8")
    only_dat = WeatherSourceFiles.content_hash(WeatherDataSourceEnum.DWD_TRY, stem)

    pathlib.Path(stem + ".csv").write_text("already processed\n", encoding="utf-8")
    with_csv = WeatherSourceFiles.content_hash(WeatherDataSourceEnum.DWD_TRY, stem)
    pathlib.Path(stem + ".dat").write_text("a different station\n", encoding="utf-8")
    edited_dat = WeatherSourceFiles.content_hash(WeatherDataSourceEnum.DWD_TRY, stem)

    assert len({only_dat, with_csv, edited_dat}) == 3
    with pytest.raises(FileNotFoundError, match="names no file that exists"):
        WeatherSourceFiles.content_hash(WeatherDataSourceEnum.DWD_TRY, str(tmp_path / "nothing_here"))


@pytest.mark.base
def test_the_component_reads_the_same_series_from_the_cache_as_from_the_producer(
    tmp_path: pathlib.Path,
) -> None:
    """A warm run and a cold run are the same run, value for value.

    The component's twelve series are what the simulation consumes, and they must not depend on whether
    the frame came out of the producer or off the disk -- otherwise the golden references would only
    describe whichever of the two CI happened to take.

    Catches: a column read on one path and not the other, and a CSV round trip that loses precision.
    """
    cold = _prepared_weather(tmp_path)
    warm = _prepared_weather(tmp_path)

    for attribute in (
        "temperature_list",
        "daily_average_outside_temperature_list_in_celsius",
        "dhi_list",
        "dni_list",
        "dniextra_list",
        "ghi_list",
        "altitude_list",
        "azimuth_list",
        "apparent_zenith_list",
        "wind_speed_list",
        "pressure_list",
    ):
        assert getattr(cold, attribute) == getattr(warm, attribute), attribute


@pytest.mark.base
def test_the_produced_series_are_the_ones_they_have_always_been(tmp_path: pathlib.Path) -> None:
    """The Aachen hourly year, pinned by value: five timesteps and the annual sum of five series.

    Everything else here tests the key; this tests the numbers. A regression in the calculation --
    a resampling that shifts by one step, an aggregation that takes the last value instead of the mean,
    a reader that renames a column -- produces a series that is wrong on both the cold and the warm
    side, so the cache-parity test cannot see it, and one that is still far above the annual-DNI floor
    in ``tests/test_weather.py``. Until now only the golden pairs of ``system_setups`` would have
    caught it, hours later and outside the ``base`` set.

    The five series are the ones #628 does not touch. The direct normal irradiance and the apparent
    zenith are deliberately not pinned here: the clamp fix changes them by design, and the branch that
    makes it adds their pin.

    Catches: a producer regression, in ``base``, without a cache and without a full simulation.
    """
    prepared = _prepared_weather(tmp_path, SimulationParameters.full_year(year=2021, seconds_per_timestep=3600))

    steps = (0, 2000, 4380, 6570, 8759)
    pinned = {
        "temperature_list": (
            [0.7225, 11.691666666666666, 20.290416666666665, 16.57208333333333, 3.9325],
            96320.14416666667,
        ),
        "ghi_list": ([0.0, 199.725, 400.4375, 7.491666666666666, 0.0], 1066931.0),
        "dhi_list": ([0.0, 154.8, 356.9583333333333, 3.745833333333333, 0.0], 584942.0),
        "wind_speed_list": (
            [1.1141666666666667, 2.2620833333333334, 1.3620833333333335, 0.4370833333333334, 2.3774999999999995],
            21090.380833333333,
        ),
        "azimuth_list": (
            [235.35231754861118, 110.37457252488502, 175.0235816373964, 268.65727736347003, 327.9098383055702],
            1576813.7202642623,
        ),
        "daily_average_outside_temperature_list_in_celsius": (
            [-0.6139409722222222, 11.741267361111113, 18.16220486111111, 15.309670138888889, 5.226111111111111],
            96314.30411458333,
        ),
    }

    for attribute, (values_at_steps, annual_sum) in pinned.items():
        series = getattr(prepared, attribute)
        assert len(series) == 8760, attribute
        assert [series[step] for step in steps] == pytest.approx(values_at_steps, rel=1e-12), attribute
        assert sum(series) == pytest.approx(annual_sum, rel=1e-12), attribute


@pytest.mark.base
def test_the_daily_average_is_one_block_mean_per_day_with_the_boundaries_it_has_always_had() -> None:
    """A block's mean, broadcast over its timesteps, and the boundary a timestep late.

    The average is taken over blocks of one day, but the loop that computed it advanced its start
    index one timestep after the boundary, so the timestep at ``k * timesteps_per_day`` belongs to the
    block before it and the last block averages whatever is left of a short series. That is what every
    golden reference in the repository was computed with, so it is what the vectorised version has to
    reproduce -- here on a hand-checked series of ten six-hour timesteps rather than on a year.

    Catches: a rewrite that starts each block one timestep early (every golden KPI that depends on the
    outside temperature moves), that drops the partial last block, or that re-centres the window.
    """
    six_hourly = [0.0, 0.0, 0.0, 4.0, 10.0, 10.0, 10.0, 10.0, 20.0, 20.0]

    averages = calculation.calculate_daily_average_outside_temperature(six_hourly, 6 * 3600)

    assert averages == [1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 10.0, 10.0, 10.0, 20.0]
    assert calculation.calculate_daily_average_outside_temperature([], 3600) == []


@pytest.mark.base
def test_a_timestep_longer_than_a_day_is_refused() -> None:
    """A timestep of two days leaves no block to average, and says so instead of returning NaN.

    ``int(24 * 3600 / seconds_per_timestep)`` is zero for any timestep longer than a day, and the loop
    this replaces then averaged empty slices: a series of NaN, which the building would have simulated
    with and reported.
    """
    with pytest.raises(ValueError, match="86400"):
        calculation.calculate_daily_average_outside_temperature([1.0, 2.0], 2 * 86400)


def _prepared_weather(
    cache_directory: pathlib.Path, parameters: Optional[SimulationParameters] = None
) -> weather.Weather:
    """Build an Aachen weather component and run its preparation against the given cache directory.

    Args:
        cache_directory: the directory the entry is looked up in and written to.
        parameters: the run's simulation parameters; a one-day hourly run by default.

    Returns:
        weather.Weather: the prepared component.
    """
    parameters = parameters or SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(cache_directory)
    component: weather.Weather = weather.Weather(
        config=weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN),
        my_simulation_parameters=parameters,
    )
    component.set_sim_repo(sim_repository.SimRepository())
    component.i_prepare_simulation()
    return component
