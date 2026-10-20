"""Tests for the car-profile producer: its layering, its key, and that an edit to its code misses the cache.

The car's driving profile at the run's resolution used to be cached under the car's configuration and the
simulation parameters alone, so an edit to the calculation -- the LoadProfileGenerator's time conversion
above all -- kept serving the profiles the old code had written. It is now a producer like the weather:
its key fingerprints the producer module and the conversion it imports. These tests pin that: the
closure is exactly those modules, an edit to either moves the key, and a moved key is a miss in a real
cache directory. They also pin what the key says about the profile: it is identified by its hash, so
the same profile is one entry and a different one is another.
"""

import dataclasses
import importlib
import pathlib
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd
import pytest

from hisim.caching import CacheClient, CacheKey, Fingerprints, ImportClosure, ProducerLayering
from hisim.caching.keys import CanonicalJson
from hisim.components import generic_car_profile, lpg_local_time
from hisim.components.generic_car_profile import CarProfileInputs, produce_car_profile

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ProducerCopy:
    """A copy of the real car-profile producer and the real LPG time conversion, in a package a test may edit.

    The copy is measured instead of a toy module so that the fingerprint is taken on the code that really
    computes the profile. It lives under its own root package, which is also the root its closure is
    computed against, and the producer's import of the conversion is rewritten to the copy, so an edit
    to the copied conversion is an edit inside the closure, exactly as in the package.
    """

    #: The package the copy lives in.
    ROOT: ClassVar[str] = "car_profile_probe"

    #: The module name the producer is copied to.
    PRODUCER: ClassVar[str] = "producer"

    #: The module name the conversion is copied to.
    CONVERSION: ClassVar[str] = "lpg_local_time"

    #: The import the copied producer must reach the copied conversion through.
    REAL_CONVERSION_IMPORT: ClassVar[str] = "from hisim.components import lpg_local_time"

    def __init__(self, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the package and put it on the import path for the test's lifetime.

        Args:
            directory: the directory to write the package into.
            monkeypatch: used to extend ``sys.path``.
        """
        self.directory = directory / self.ROOT
        self.directory.mkdir()
        (self.directory / "__init__.py").write_text("", encoding="utf-8")
        self.write(self.CONVERSION, pathlib.Path(lpg_local_time.__file__ or "").read_text(encoding="utf-8"))
        producer_source = pathlib.Path(generic_car_profile.__file__ or "").read_text(encoding="utf-8")
        assert self.REAL_CONVERSION_IMPORT in producer_source, (
            "the producer no longer imports the LPG time conversion the way this probe rewrites; point it at "
            "the new import, or the copy would measure the real module instead of the copied one"
        )
        self.write(
            self.PRODUCER,
            producer_source.replace(self.REAL_CONVERSION_IMPORT, f"from {self.ROOT} import {self.CONVERSION}"),
        )
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

    def cache_key(self, inputs: CarProfileInputs) -> CacheKey:
        """Return the key the copied producer files ``inputs`` under.

        The third-party half is a constant: the copy's closure names ``hisim`` as an outside package,
        and its version is not what these tests are about.

        Args:
            inputs: the calculation's inputs.

        Returns:
            CacheKey: the key.
        """
        return CacheKey(
            artifact_kind=CarProfileInputs.ARTIFACT_KIND,
            code_fingerprint=self.code_fingerprint(),
            third_party_fingerprint="pinned by the test",
            dto_json=CanonicalJson.dumps(inputs),
        )


def calculation_inputs(**overrides: Any) -> CarProfileInputs:
    """Build the inputs of a two-day, hourly run over a two-day minute profile.

    The car is at home except for a drive to the workplace on the morning of the first day.

    Args:
        **overrides: fields to replace.

    Returns:
        CarProfileInputs: the inputs.
    """
    minutes = 2 * 24 * 60
    car_location: List[Optional[str]] = ["Home"] * minutes
    distances = [0.0] * minutes
    for minute in range(8 * 60, 8 * 60 + 30):
        car_location[minute] = None
        distances[minute] = 500.0
    for minute in range(8 * 60 + 30, 17 * 60):
        car_location[minute] = "Workplace"
    fields: Dict[str, Any] = {
        "lpg_time_resolution": "00:01:00",
        "car_location": tuple(car_location),
        "driven_distance_per_minute_in_meters": tuple(distances),
    }
    fields.update(overrides)
    content_hash = CarProfileInputs.content_hash(
        fields["lpg_time_resolution"], fields["car_location"], fields["driven_distance_per_minute_in_meters"]
    )
    arguments: Dict[str, Any] = {
        "profile_content_hash": content_hash,
        "profile_start": "2021-01-01T00:00:00",
        "seconds_per_timestep": 3600,
        "timesteps": 48,
        "window_start": "2021-01-01T00:00:00",
        **fields,
    }
    return CarProfileInputs(**arguments)


@pytest.mark.base
def test_the_producer_obeys_the_layering_rule() -> None:
    """The closure is the producer, the LPG time conversion, its window offset and the key module, and nothing else.

    Catches: the producer reaching for ``hisim.utils``, ``Component`` or the simulation parameters,
    which would make it uncallable without HiSim and its key hostage to every edit of those modules.
    """
    closure = ImportClosure.of(generic_car_profile)

    ProducerLayering.check(closure)
    assert set(closure.package_modules) == {
        "hisim.components.generic_car_profile",
        "hisim.components.lpg_local_time",
        "hisim.caching.keys",
        "hisim.window_offset",
    }
    assert set(closure.third_party_top_levels) == {"numpy", "pandas"}
    assert not closure.dynamic_import_sites


@pytest.mark.base
@pytest.mark.parametrize(
    ("module", "old", "new"),
    [
        (ProducerCopy.PRODUCER, "if minutes_per_timestep > 1:", "if minutes_per_timestep > 2:"),
        (ProducerCopy.CONVERSION, "# Each row is localized twice", "# Every row is localized twice"),
    ],
)
def test_an_edit_to_the_producer_or_the_conversion_misses_the_cache(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, module: str, old: str, new: str
) -> None:
    """A profile filed before an edit to the producer or to the conversion is not found after it.

    The entry is written into a real cache directory and found again under the unchanged code; then
    one line of the copied module changes and the same inputs are looked up again.

    Catches: a key that does not follow the producer's code, which is the defect the legacy key had --
    a changed calculation kept serving the profiles the old one wrote.
    """
    for variable in ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR"):
        monkeypatch.delenv(variable, raising=False)
    (tmp_path / "code").mkdir()
    probe = ProducerCopy(tmp_path / "code", monkeypatch)
    cache_directory = tmp_path / "cache"
    cache_directory.mkdir()
    client = CacheClient.from_environment()
    inputs = calculation_inputs()

    entry = client.lookup_producer(probe.cache_key(inputs), str(cache_directory))
    assert not entry.exists
    with entry.writing() as temporary_path:
        produce_car_profile(inputs).to_csv(temporary_path, index=False)
    assert client.lookup_producer(probe.cache_key(inputs), str(cache_directory)).exists

    source = probe.source(module)
    edited = source.replace(old, new)
    assert edited != source, "the line the edit targets has moved; point this test at another line"
    probe.write(module, edited)

    assert not client.lookup_producer(probe.cache_key(inputs), str(cache_directory)).exists


@pytest.mark.base
def test_the_profile_enters_the_key_by_its_hash() -> None:
    """The same profile is one key; a profile differing in one minute, another span or another window is another.

    Catches: the profile left out of the key (two households sharing an entry) or the payload itself in
    the key (a key of half a million numbers).
    """
    base = calculation_inputs()
    same = calculation_inputs()
    distances = list(base.driven_distance_per_minute_in_meters)
    distances[600] = 1.0
    other_profile = calculation_inputs(driven_distance_per_minute_in_meters=tuple(distances))

    assert CanonicalJson.dumps(base) == CanonicalJson.dumps(same)
    assert CanonicalJson.dumps(base) != CanonicalJson.dumps(other_profile)
    locations = list(base.car_location)
    locations[600] = "Shopping"
    other_place = calculation_inputs(car_location=tuple(locations))
    assert CanonicalJson.dumps(base) != CanonicalJson.dumps(other_place)
    assert "driven_distance_per_minute_in_meters" not in CanonicalJson.dumps(base)
    assert "car_location" not in CanonicalJson.dumps(base)
    one_day = dataclasses.replace(base, timesteps=24)
    assert CanonicalJson.dumps(one_day) != CanonicalJson.dumps(base)
    july = dataclasses.replace(base, window_start="2021-07-01T00:00:00")
    assert CanonicalJson.dumps(july) != CanonicalJson.dumps(base)


@pytest.mark.base
def test_the_produced_profile_is_hourly_and_in_standard_time() -> None:
    """The minute profile, put onto the run's standard-time clock, is summed and voted per hour.

    In January the wall clock is standard time, so the drive from 08:00 to 08:30 is in the hour starting
    at 08:00, which carries its 15 km, and the car is at the workplace for all of the hours 09:00 to 16:00.
    Catches the profile being read in UTC, an hour earlier, as it was before hisim-midyear-windows-9g2.5.
    """
    produced = produce_car_profile(calculation_inputs())

    assert list(produced.columns) == [CarProfileInputs.LOCATION_COLUMN, CarProfileInputs.DISTANCE_COLUMN]
    assert len(produced) == 48
    distances = produced[CarProfileInputs.DISTANCE_COLUMN]
    assert float(distances.sum()) == pytest.approx(15000.0)
    assert float(distances.iloc[8]) == pytest.approx(15000.0)
    locations = produced[CarProfileInputs.LOCATION_COLUMN]
    assert list(locations.iloc[9:17]) == [2] * 8
    assert int(locations.iloc[17]) == 1
    assert int(locations.iloc[0]) == 1
    assert isinstance(produced, pd.DataFrame)


@pytest.mark.base
@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"seconds_per_timestep": 30, "timesteps": 5760}, "whole multiple"),
        ({"seconds_per_timestep": 90, "timesteps": 1920}, "whole multiple"),
        ({"lpg_time_resolution": "00:05:00", "seconds_per_timestep": 900, "timesteps": 192}, "one value per 60 s"),
    ],
    ids=[
        "sub-minute-timestep",
        "timestep-not-a-whole-number-of-minutes",
        "profile-not-minutely",
    ],
)
def test_a_run_the_producer_cannot_cut_a_profile_for_is_refused_when_the_inputs_are_built(
    overrides: Dict[str, Any], refusal: str
) -> None:
    """A timestep shorter than or not a multiple of the profile's minute, or a coarser profile, is refused.

    Catches: such a run reaching the producer, where it fails as a pandas length error that names
    neither the car nor the cause.
    """
    with pytest.raises(ValueError, match=refusal):
        calculation_inputs(**overrides)


@pytest.mark.base
def test_a_missing_distance_is_hashed_instead_of_stopping_the_key() -> None:
    """A minute without a distance hashes as such, as the producer's own numeric conversion reads it.

    Catches: ``float(None)`` raising a ``TypeError`` while the key is built, for a profile the producer
    itself would turn into a number.
    """
    distances: List[Optional[float]] = [0.0, None, 2.0]
    with_gap = CarProfileInputs.content_hash("00:01:00", ["Home"] * 3, distances)  # type: ignore[arg-type]
    without_gap = CarProfileInputs.content_hash("00:01:00", ["Home"] * 3, [0.0, 0.0, 2.0])

    assert with_gap != without_gap


@pytest.mark.base
def test_the_location_of_a_timestep_is_its_most_frequent_minute_the_first_on_a_tie() -> None:
    """The vote over a timestep's minutes picks the most frequent location, and the earliest one on a tie.

    Catches: a rewrite of the vote that breaks ties differently, which would move the car's location in
    every timestep it changes place in the middle of.
    """
    assert generic_car_profile.most_frequent([0, 1, 1, 2]) == 1
    assert generic_car_profile.most_frequent([2, 1, 1, 2]) == 2
    assert generic_car_profile.most_frequent([1, 0, 0, 1, 2]) == 1
    assert generic_car_profile.most_frequent([0]) == 0


@pytest.mark.base
def test_a_july_window_reads_the_profile_from_its_stated_start() -> None:
    """Catches a car whose July window reads the profile as if it started on 1 January, or at its 00:00.

    A profile that states 1 July 00:00 on the wall clock as its start begins an hour before the window in
    standard time: the drive at 08:00 summer time falls into the hour starting at 07:00 of the run's clock.
    """
    inputs = calculation_inputs(profile_start="2021-07-01T00:00:00", window_start="2021-07-01T00:00:00", timesteps=24)

    produced = produce_car_profile(inputs)

    assert len(produced) == 24
    assert float(produced[CarProfileInputs.DISTANCE_COLUMN].iloc[7]) == pytest.approx(15000.0)
    assert list(produced[CarProfileInputs.LOCATION_COLUMN].iloc[8:16]) == [2] * 8
