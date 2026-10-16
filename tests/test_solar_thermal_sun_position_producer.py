"""Tests for the solar-thermal sun-position producer: its layering, its key, and that an edit misses the cache.

The collector's sun position used to be cached under the collector's configuration and the simulation
parameters, a key that saw neither the code nor pvlib's version. It is now a producer like the weather,
and these tests pin what that buys: the closure is the producer alone, an edit to it is a miss in a real
cache directory, and the key is the coordinates and the run's instants -- not the collector's area or
costs, which do not move the sun.
"""

import dataclasses
import importlib
import pathlib
import sys
from typing import Any, ClassVar, Dict

import pytest

from hisim.caching import CacheClient, CacheKey, Fingerprints, ImportClosure, ProducerLayering
from hisim.caching.keys import CanonicalJson
from hisim.components import solar_thermal_sun_position
from hisim.components.solar_thermal_sun_position import SunPositionInputs, produce_sun_position

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class ProducerCopy:
    """A copy of the real sun-position producer in a package a test may edit.

    The copy is measured instead of a toy module so that the fingerprint is taken on the code that
    really computes the sun's position.
    """

    #: The package the copy lives in.
    ROOT: ClassVar[str] = "sun_position_probe"

    #: The module name the producer is copied to.
    PRODUCER: ClassVar[str] = "producer"

    def __init__(self, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Write the package and put it on the import path for the test's lifetime.

        Args:
            directory: the directory to write the package into.
            monkeypatch: used to extend ``sys.path``.
        """
        self.directory = directory / self.ROOT
        self.directory.mkdir(parents=True)
        (self.directory / "__init__.py").write_text("", encoding="utf-8")
        self.write(pathlib.Path(solar_thermal_sun_position.__file__ or "").read_text(encoding="utf-8"))
        monkeypatch.syspath_prepend(str(directory))

    def write(self, source: str) -> None:
        """Write or overwrite the copied producer.

        Args:
            source: its source.
        """
        (self.directory / f"{self.PRODUCER}.py").write_text(source, encoding="utf-8")

    def source(self) -> str:
        """Return the copied producer's current source.

        Returns:
            str: the source.
        """
        return (self.directory / f"{self.PRODUCER}.py").read_text(encoding="utf-8")

    def cache_key(self, inputs: SunPositionInputs) -> CacheKey:
        """Return the key the copied producer, as it stands on disk, files ``inputs`` under.

        Args:
            inputs: the calculation's inputs.

        Returns:
            CacheKey: the key; its third-party half is a constant, since pvlib's version is not what
                these tests change.
        """
        for name in list(sys.modules):
            if name == self.ROOT or name.startswith(self.ROOT + "."):
                del sys.modules[name]
        importlib.invalidate_caches()
        module = importlib.import_module(f"{self.ROOT}.{self.PRODUCER}")
        return CacheKey(
            artifact_kind=SunPositionInputs.ARTIFACT_KIND,
            code_fingerprint=Fingerprints.code(ImportClosure.of(module, root_package=self.ROOT)),
            third_party_fingerprint="pinned by the test",
            dto_json=CanonicalJson.dumps(inputs),
        )


def calculation_inputs(**overrides: Any) -> SunPositionInputs:
    """Build the inputs of one hourly day in Aachen in June.

    Args:
        **overrides: fields to replace.

    Returns:
        SunPositionInputs: the inputs.
    """
    fields: Dict[str, Any] = {
        "latitude_in_degrees": 50.78,
        "longitude_in_degrees": 6.08,
        "window_start": "2021-06-21T00:00:00",
        "seconds_per_timestep": 3600,
        "timesteps": 24,
    }
    fields.update(overrides)
    return SunPositionInputs(**fields)


@pytest.mark.base
def test_the_producer_obeys_the_layering_rule() -> None:
    """The closure is the producer alone, with pandas and pvlib.

    Catches: the producer importing the collector's component module, which would put the component
    machinery into the key and make every edit of the collector a cache miss.
    """
    closure = ImportClosure.of(solar_thermal_sun_position)

    ProducerLayering.check(closure)
    assert set(closure.package_modules) == {"hisim.components.solar_thermal_sun_position"}
    assert set(closure.third_party_top_levels) == {"pandas", "pvlib"}
    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_an_edit_to_the_producer_misses_the_cache(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sun position filed before an edit to the producer is not found after it.

    Catches: a key that does not follow the producer's code, the defect of the legacy key.
    """
    for variable in ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR"):
        monkeypatch.delenv(variable, raising=False)
    probe = ProducerCopy(tmp_path / "code", monkeypatch)
    cache_directory = tmp_path / "cache"
    cache_directory.mkdir()
    client = CacheClient.from_environment()
    inputs = calculation_inputs()

    entry = client.lookup_producer(probe.cache_key(inputs), str(cache_directory))
    assert not entry.exists
    with entry.writing() as temporary_path:
        produce_sun_position(inputs).to_csv(temporary_path, index=False)
    assert client.lookup_producer(probe.cache_key(inputs), str(cache_directory)).exists

    source = probe.source()
    edited = source.replace("latitude=inputs.latitude_in_degrees,", "latitude=inputs.latitude_in_degrees,  # edited")
    assert edited != source, "the line the edit targets has moved; point this test at another line"
    probe.write(edited)

    assert not client.lookup_producer(probe.cache_key(inputs), str(cache_directory)).exists


@pytest.mark.base
@pytest.mark.parametrize(
    "field_and_value",
    [
        ("latitude_in_degrees", 40.0),
        ("longitude_in_degrees", 10.0),
        ("window_start", "2021-12-21T00:00:00"),
        ("seconds_per_timestep", 900),
        ("timesteps", 48),
    ],
)
def test_each_field_moves_the_key(field_and_value: Any) -> None:
    """Every field of the inputs is key material: changing any one gives another key.

    Catches: a run of another window or at another place reading this run's sun.
    """
    name, value = field_and_value
    base = calculation_inputs()

    assert CanonicalJson.dumps(dataclasses.replace(base, **{name: value})) != CanonicalJson.dumps(base)


@pytest.mark.base
def test_the_sun_is_highest_around_noon_utc_in_june() -> None:
    """The produced frame has one row per timestep, and the June sun in Aachen culminates near 11:30 UTC.

    The run's instants are read as UTC, so the smallest apparent zenith is at the 11:00 or 12:00 UTC
    timestep and lies near 90 - (90 - 50.8 + 23.4) = 27 degrees.
    """
    produced = produce_sun_position(calculation_inputs())

    assert list(produced.columns) == list(SunPositionInputs.COLUMNS)
    assert len(produced) == 24
    noon = int(produced["apparent_zenith"].to_numpy().argmin())
    assert noon in (11, 12)
    assert float(produced["apparent_zenith"].min()) == pytest.approx(27.4, abs=1.0)
