"""Tests for the weather producer: the layering rule it obeys and the key its code is fingerprinted into.

The weather series is the first artifact keyed under ``roadmap/cache_service_spec.md`` §3, and it is
keyed that way because of the #628 cache finding: a fix to the direct normal irradiance changed 33 KPIs
locally and none in CI, where the cache directory is restored from the previous run and the old key --
a hash of the configuration and the simulation parameters -- still matched. These tests pin the three
promises that make that impossible now. The producer's import closure stays small and free of component
machinery, so hashing all of it is affordable; an edit to the producer's source moves the key while an
edit outside its closure does not; and the inputs identify the data file by its contents rather than by
where the checkout happens to be.
"""

# clean

import dataclasses
import importlib
import pathlib
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict

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
    which is only affordable because the closure is tiny. It is asserted as an exact set rather than a
    mere absence of violations, so that an import which quietly drags a frequently edited module into
    the closure -- and would therefore throw every cached weather series away on every edit to it --
    has to be justified here.

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
def test_an_edit_outside_the_closure_leaves_the_fingerprint_alone(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module the producer does not import cannot invalidate its artifacts.

    The other half of the promise: if every edit anywhere invalidated the cache, the scheme would be
    commit-keying under another name and nobody would ever get a hit.

    Catches: a fingerprint widened to the package, the repository or the commit.
    """
    probe = ProducerCopy(tmp_path, monkeypatch)
    probe.write("stranger", '"""A module nothing imports."""\n\nVALUE = 1\n')
    before = probe.code_fingerprint()

    probe.write("stranger", '"""A module nothing imports."""\n\nVALUE = 2\n')

    assert probe.code_fingerprint() == before


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
    assert digest_of(baseline) == digest_of(calculation_inputs(duration_in_days=None))

    quarter_hourly = calculation_inputs(data_source=WeatherDataSourceEnum.DWD_15MIN, duration_in_days=7)
    assert digest_of(quarter_hourly) != digest_of(dataclasses.replace(quarter_hourly, duration_in_days=365))
    assert dataclasses.replace(quarter_hourly, duration_in_days=None).duration_in_days is None
    with pytest.raises(ValueError, match="duration_in_days"):
        dataclasses.replace(quarter_hourly, duration_in_days=None).required_duration_in_days()


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
        "dry_bulb_list",
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


def _prepared_weather(cache_directory: pathlib.Path) -> weather.Weather:
    """Build an Aachen weather component and run its preparation against the given cache directory.

    Args:
        cache_directory: the directory the entry is looked up in and written to.

    Returns:
        weather.Weather: the prepared component.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(cache_directory)
    component: weather.Weather = weather.Weather(
        config=weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN),
        my_simulation_parameters=parameters,
    )
    component.set_sim_repo(sim_repository.SimRepository())
    component.i_prepare_simulation()
    return component
