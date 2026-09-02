"""Tests for :mod:`hisim.caching.client` and for ``hisim.utils.get_cache_file`` delegating to it.

Two things are pinned. First, the client's local tier does exactly what ``get_cache_file`` used to do
inline -- the same filename, the same directory handling, the same validation and discarding -- because
the delegation is only safe if nothing moved but the code. Second, the layering rule of spec §4:
importing the cache package pulls in neither a component, nor the simulator, nor the simulation
parameters, which is checked in a fresh interpreter so that nothing imported earlier by the test
session can hide a violation.

Each test states the failure mode it catches.
"""

# clean

import dataclasses
import os
import pathlib
import subprocess
import sys
from typing import ClassVar

import pytest

from hisim import utils
from hisim.caching import CacheClient, CacheEntryMetadata, CacheSettings, atomic_cache_write, default_client
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class Given:
    """The one key and the one payload every test here uses, so the tests read as variations on a theme."""

    KEY_MATERIAL: ClassVar[str] = '{"location": "Aachen"}2021-01-01###2021-12-31###60'
    COMPONENT_KEY: ClassVar[str] = "Weather"
    PAYLOAD: ClassVar[str] = "index,value\n0,1.5\n"

    @classmethod
    def client(cls, tmp_path: pathlib.Path, **environment: str) -> CacheClient:
        """A client over a settings object read from the given environment mapping.

        Args:
            tmp_path: unused except to make the signature symmetric with the tests.
            **environment: the variables to set.

        Returns:
            CacheClient: the client.
        """
        del tmp_path
        return CacheClient(CacheSettings.from_environment(environment))

    @classmethod
    def landed_entry(cls, client: CacheClient, directory: pathlib.Path) -> str:
        """Writes one valid entry through the client and returns its path.

        Args:
            client: the client to look the entry up through.
            directory: the cache directory.

        Returns:
            str: the entry's path.
        """
        entry = client.lookup(cls.COMPONENT_KEY, cls.KEY_MATERIAL, str(directory))
        with entry.writing() as temporary_path:
            pathlib.Path(temporary_path).write_text(cls.PAYLOAD, encoding="utf-8")
        return entry.path


@pytest.mark.base
def test_a_miss_names_the_path_and_creates_the_directory(tmp_path: pathlib.Path) -> None:
    """Looking up an entry that does not exist returns where it would go, in a directory that now exists.

    Catches: a writer having to create the directory itself, which is how a first run on a clean
    machine used to fail.
    """
    directory = tmp_path / "fresh" / "cache"
    client = Given.client(tmp_path)

    entry = client.lookup(Given.COMPONENT_KEY, Given.KEY_MATERIAL, str(directory))

    assert not entry.exists
    assert directory.is_dir()
    expected_name = CacheClient.entry_filename(Given.COMPONENT_KEY, CacheEntryMetadata.hash_of(Given.KEY_MATERIAL))
    assert entry.path == str(directory / expected_name)
    assert entry.key_material == Given.KEY_MATERIAL


@pytest.mark.base
def test_an_entry_written_through_the_client_is_a_hit_next_time(tmp_path: pathlib.Path) -> None:
    """The write path and the read path agree on name and metadata.

    Catches: ``writing()`` landing the entry under a name ``lookup()`` does not look for, or without
    the metadata that makes it count.
    """
    client = Given.client(tmp_path)
    path = Given.landed_entry(client, tmp_path)

    entry = client.lookup(Given.COMPONENT_KEY, Given.KEY_MATERIAL, str(tmp_path))

    assert entry.exists
    assert entry.path == path
    assert pathlib.Path(path).read_text(encoding="utf-8") == Given.PAYLOAD
    assert CacheEntryMetadata.describes(path)


@pytest.mark.base
def test_an_entry_without_metadata_is_discarded_and_reported_as_a_miss(tmp_path: pathlib.Path) -> None:
    """A data file that cannot be shown to belong to its key is deleted, not served.

    Catches: the migration rule -- every pre-metadata entry clears itself on first lookup -- being
    lost in the move from ``get_cache_file`` to the client.
    """
    client = Given.client(tmp_path)
    name = CacheClient.entry_filename(Given.COMPONENT_KEY, CacheEntryMetadata.hash_of(Given.KEY_MATERIAL))
    stale = tmp_path / name
    stale.write_text("written before the metadata scheme existed", encoding="utf-8")

    entry = client.lookup(Given.COMPONENT_KEY, Given.KEY_MATERIAL, str(tmp_path))

    assert not entry.exists
    assert not stale.exists(), "the unvalidatable entry must be deleted, not left to be served later"


@pytest.mark.base
def test_an_entry_whose_metadata_disagrees_with_its_name_is_discarded(tmp_path: pathlib.Path) -> None:
    """Metadata that hashes to a different name than the file carries is a poisoned entry.

    Catches: validation that only checks the metadata file *exists*.
    """
    client = Given.client(tmp_path)
    path = Given.landed_entry(client, tmp_path)
    pathlib.Path(CacheEntryMetadata.metadata_filepath(path)).write_text("some other inputs", encoding="utf-8")

    entry = client.lookup(Given.COMPONENT_KEY, Given.KEY_MATERIAL, str(tmp_path))

    assert not entry.exists
    assert not pathlib.Path(path).exists()


@pytest.mark.base
def test_the_environment_override_moves_the_directory(tmp_path: pathlib.Path) -> None:
    """``HISIM_CACHE_DIR`` replaces the default directory the caller passes.

    Catches: the override being parsed but not applied.
    """
    override = tmp_path / "override"
    client = Given.client(tmp_path, HISIM_CACHE_DIR=str(override))

    entry = client.lookup(Given.COMPONENT_KEY, Given.KEY_MATERIAL, str(tmp_path / "default"))

    assert entry.path.startswith(str(override))
    assert override.is_dir()


@pytest.mark.base
def test_get_cache_file_returns_exactly_the_path_it_always_did(tmp_path: pathlib.Path) -> None:
    """The delegation is invisible: same filename, same directory, same validation, from the same inputs.

    The expected path is computed by hand from the published rule -- the digest of the legacy key
    material under the component key -- rather than from the client, so this test would notice the
    client changing the rule.

    Catches: the move to the client changing a single byte of any cache filename, which would orphan
    every existing entry on every machine.
    """

    @dataclasses.dataclass
    class Config:
        """The smallest thing ``get_cache_file`` accepts: something with ``to_json``."""

        name: str = "demo"

        def to_json(self) -> str:
            """The configuration's JSON, as ``ConfigBase`` would produce it."""
            return '{"name": "demo"}'

    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(tmp_path)
    material = utils.build_cache_key_string(Config(), parameters)
    expected = tmp_path / f"demo_{CacheEntryMetadata.hash_of(material)}.cache"

    exists, path = utils.get_cache_file("demo", Config(), parameters)

    assert not exists
    assert path == str(expected)

    with atomic_cache_write(path, material) as temporary_path:
        pathlib.Path(temporary_path).write_text(Given.PAYLOAD, encoding="utf-8")
    exists_after, path_after = utils.get_cache_file("demo", Config(), parameters)
    assert exists_after and path_after == path


@pytest.mark.base
def test_an_explicit_directory_argument_outranks_the_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that names a directory gets that directory, whatever ``HISIM_CACHE_DIR`` says.

    Every test that points ``get_cache_file`` at its own ``tmp_path`` relies on this; without it a
    developer's override would make those tests write into the developer's cache.

    Catches: the environment override silently redirecting an explicit request.
    """

    @dataclasses.dataclass
    class Config:
        """The smallest thing ``get_cache_file`` accepts: something with ``to_json``."""

        name: str = "demo"

        def to_json(self) -> str:
            """The configuration's JSON."""
            return '{"name": "demo"}'

    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path / "env"))
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.cache_dir_path = str(tmp_path / "params")
    explicit = tmp_path / "explicit"

    _, from_argument = utils.get_cache_file("demo", Config(), parameters, cache_dir_path=str(explicit))
    _, from_environment = utils.get_cache_file("demo", Config(), parameters)

    assert from_argument.startswith(str(explicit))
    assert from_environment.startswith(str(tmp_path / "env"))


@pytest.mark.base
def test_default_client_reads_the_environment_or_takes_settings() -> None:
    """The one-liner ``get_cache_file`` calls works both ways.

    Catches: ``default_client`` ignoring the settings it was handed.
    """
    given = CacheSettings.from_environment({CacheSettings.Variables.DIRECTORY: "/given"})

    assert default_client(given).settings is given
    assert isinstance(default_client().settings, CacheSettings)


@pytest.mark.base
def test_importing_the_cache_package_imports_no_component_or_simulation_module() -> None:
    """Spec §4: the package sits at the layer of ``hisim/config/`` and imports nothing above it.

    Run in a fresh interpreter, because in the test process those modules are long since imported and
    ``sys.modules`` would say nothing about who imported them.

    Catches: a convenience import in the package -- ``SimulationParameters`` for a type hint, say --
    that would make every component import the simulation to reach its cache.
    """
    forbidden = (
        "hisim.component",
        "hisim.simulator",
        "hisim.simulationparameters",
        "hisim.sim_repository",
        "hisim.sim_repository_singleton",
        "hisim.utils",
        "hisim.loadtypes",
    )
    program = (
        "import sys\n"
        "import hisim.caching\n"
        f"loaded = [name for name in {forbidden!r} if name in sys.modules]\n"
        "print(','.join(loaded))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parents[1])

    completed = subprocess.run(  # nosec B603 - fixed argument vector, no shell
        [sys.executable, "-c", program], capture_output=True, text=True, check=False, env=environment
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", f"importing hisim.caching also imported: {completed.stdout.strip()}"
