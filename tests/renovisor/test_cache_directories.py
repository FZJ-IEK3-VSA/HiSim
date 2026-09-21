"""hisim-epc.22: the ordered cache directories a calculation reads and writes.

Two directories, a seed and a volume: a file present only in the second is read from there, a
new entry is written to the first, and the environment variable a container sets fills the
parameter list. Unset, everything behaves exactly as it did before the list existed.
"""

import datetime
import os
import pathlib

import pytest

from hisim.caching import CacheClient, CacheKey, CacheSettings
from hisim.caching.locations import CacheLocations
from hisim.renovisor.simulation import SimulationSetup
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class Given:
    """The one key, payload and client every test here varies on."""

    KEY_MATERIAL: str = '{"location": "Aachen"}2019-01-01###2020-01-01###3600'
    COMPONENT_KEY: str = "Tabula"
    PAYLOAD: str = "index,value\n0,1.5\n"

    @staticmethod
    def client(**environment: str) -> CacheClient:
        """A client over settings read from the given environment; empty means no overrides."""
        return CacheClient(CacheSettings.from_environment(environment))

    @staticmethod
    def producer_key() -> CacheKey:
        """A producer key with fixed parts, so a test's lookups all name the same entry."""
        return CacheKey(
            artifact_kind="probe", code_fingerprint="fp", third_party_fingerprint="tp", dto_json="{}"
        )

    @staticmethod
    def parameters(cache_directories: list[str]) -> SimulationParameters:
        """Parameters pointing at the given cache directories."""
        return SimulationParameters(
            start_date=datetime.datetime(2019, 1, 1),
            end_date=datetime.datetime(2019, 1, 2),
            seconds_per_timestep=3600,
            cache_directories=cache_directories,
        )


def test_a_file_only_in_the_second_directory_is_read_from_there(tmp_path: pathlib.Path) -> None:
    """The seed lacks the entry, the volume has it: the read walks on and finds it."""
    seed, volume = tmp_path / "seed", tmp_path / "volume"
    volume.mkdir()
    (volume / "entry.cache").write_text(Given.PAYLOAD)
    locations = CacheLocations([str(seed), str(volume)])

    assert locations.read("entry.cache") == str(volume / "entry.cache")
    assert locations.read("missing.cache") is None
    assert locations.write_directory() == str(seed)


def test_empty_directories_are_refused() -> None:
    """A location list without a directory has nowhere to write, so it is refused."""
    with pytest.raises(ValueError, match="at least one cache directory"):
        CacheLocations([])


def test_a_new_producer_entry_is_written_to_the_first_directory(tmp_path: pathlib.Path) -> None:
    """A producer miss writes into the seed, never into the volume behind it."""
    seed, volume = tmp_path / "seed", tmp_path / "volume"
    seed.mkdir()
    client = Given.client()
    key = Given.producer_key()

    entry = client.lookup_producer(key, CacheLocations([str(seed), str(volume)]))

    assert entry.exists is False
    assert entry.path.startswith(str(seed))
    assert not (volume / pathlib.Path(entry.path).name).exists()


def test_a_producer_hit_in_a_later_directory_is_returned_at_its_own_path(tmp_path: pathlib.Path) -> None:
    """The client returns the volume's path for a validated hit the seed does not hold."""
    seed, volume = tmp_path / "seed", tmp_path / "volume"
    volume.mkdir()
    client = Given.client()
    key = Given.producer_key()
    volume_entry = client.lookup(key.artifact_kind, key.material, str(volume))
    with volume_entry.writing() as temporary_path:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            handle.write(Given.PAYLOAD)

    hit = client.lookup_producer(key, CacheLocations([str(seed), str(volume)]))

    assert hit.exists is True
    assert hit.path == volume_entry.path


def test_a_bare_directory_keeps_its_meaning(tmp_path: pathlib.Path) -> None:
    """Callers that pass one directory, as every component did before, behave as before."""
    only = tmp_path / "only"
    only.mkdir()
    client = Given.client()

    entry = client.lookup_producer(Given.producer_key(), str(only))

    assert entry.path.startswith(str(only))


def test_get_cache_file_reads_a_later_directory_and_writes_the_first(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The solar-thermal/car/UTSP path: a hit in the volume is read, a miss lands in the seed."""
    from hisim.utils import get_cache_file

    seed, volume = tmp_path / "seed", tmp_path / "volume"
    monkeypatch.delenv("HISIM_CACHE_DIR", raising=False)

    class ParameterClass:
        """A minimal stand-in for a config: its JSON is the key material's first half."""

        def to_json(self) -> str:
            """The JSON the key is hashed from."""
            return '{"probe": 1}'

    parameters = Given.parameters([str(seed), str(volume)])
    exists, path = get_cache_file(Given.COMPONENT_KEY, ParameterClass(), parameters)
    assert exists is False
    assert path.startswith(str(seed))

    # Land a valid entry in the volume under the same key, through the client.
    client = Given.client()
    landed = client.lookup(
        Given.COMPONENT_KEY,
        _key_material_of(ParameterClass(), parameters),
        str(volume),
    )
    with landed.writing() as temporary_path:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            handle.write(Given.PAYLOAD)

    exists_again, path_again = get_cache_file(Given.COMPONENT_KEY, ParameterClass(), parameters)
    assert exists_again is True
    assert path_again.startswith(str(volume))


def _key_material_of(parameter_class: object, parameters: SimulationParameters) -> str:
    """The key material get_cache_file builds, spelled through its own builder."""
    from hisim.utils import build_cache_key_string

    return build_cache_key_string(parameter_class, parameters)


class TestTheEnvironmentVariable:
    """The container-facing half: HISIM_CACHE_DIRECTORIES fills the parameter list."""

    def test_unset_is_todays_behaviour(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without the variable, the parameters use the single cache path, as always."""
        monkeypatch.delenv(SimulationSetup.CACHE_DIRECTORIES_VARIABLE, raising=False)
        parameters = self._parameters(tmp_path)

        assert parameters.cache_directories == []
        assert parameters.cache_locations().directories == (parameters.cache_dir_path,)

    def test_it_fills_the_directory_list_in_order(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A container passes both directories on the variable; the run reads in order."""
        seed, volume = tmp_path / "seed", tmp_path / "volume"
        monkeypatch.setenv(
            SimulationSetup.CACHE_DIRECTORIES_VARIABLE, os.pathsep.join([str(seed), str(volume)])
        )
        parameters = self._parameters(tmp_path)

        assert parameters.cache_directories == [str(seed), str(volume)]
        assert parameters.cache_dir_path == str(seed), "the recorded single path stays consistent"
        assert parameters.cache_locations().write_directory() == str(seed)

    def test_a_relative_entry_is_refused(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A relative path would depend on the worker's working directory; it is refused."""
        monkeypatch.setenv(
            SimulationSetup.CACHE_DIRECTORIES_VARIABLE, f"/abs/first{os.pathsep}relative/second"
        )

        with pytest.raises(ValueError, match="absolute"):
            self._parameters(tmp_path)

    def test_the_manifest_states_the_directories_used(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """calculation.json names the directories, in priority order.

        The realized parameter record deliberately omits them -- its own header says machine
        settings are absent -- so the run manifest is the one place a result directory says
        where its cache entries came from.
        """
        seed = tmp_path / "seed"
        monkeypatch.setenv(SimulationSetup.CACHE_DIRECTORIES_VARIABLE, str(seed))
        parameters = self._parameters(tmp_path)

        assert list(parameters.cache_locations().directories) == [str(seed)]

    @staticmethod
    def _parameters(tmp_path: pathlib.Path) -> SimulationParameters:
        """SimulationSetup.parameters over its fixed calendar, one day long."""
        return SimulationSetup.parameters(
            period=type("OneDay", (), {"days": 1})(),
            output_directory=tmp_path,
            cache_directory=None,
            country="IE",
        )
