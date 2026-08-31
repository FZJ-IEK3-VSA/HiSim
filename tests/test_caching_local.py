"""Tests for the local tier of the HiSim cache service, :mod:`hisim.caching.local`.

The one behaviour this module has to pin is atomicity, because it is the whole reason the local tier
of ``roadmap/cache_service_spec.md`` §4 ships ahead of the rest of the cache service. A cache entry
that lands in place by rename can never be read half written; one that is written straight to its
final path can, and used to be, which is what broke a parallel scenario regeneration in CI.

The first test is the control: it pins the old behaviour so the second test cannot quietly stop
proving anything if the harness ever loses its grip on the timing.

The second half of the module covers the companion metadata written beside every entry. It is what
makes an entry validatable at all: recomputing the hash of the recorded inputs and comparing it with
the name the entry is filed under proves the contents belong to the key, and any disagreement -- or
any missing metadata -- deletes the entry rather than serving it.
"""

# clean

import pathlib
import threading
from typing import Any, Callable, ClassVar, List, Optional, Tuple

import pytest

from hisim import utils
from hisim.caching import CacheEntryMetadata, atomic_cache_write
from hisim.simulationparameters import SimulationParameters


class CacheRaceHarness:
    """Drives two writers and one reader at a single cache path with no sleeps.

    The point of the harness is to make the half-written window observable on demand rather than by
    luck. Both writers emit their payload in chunks and then block on an event until the reader has
    probed the target path at least once, so the reader is guaranteed to look while the writers are
    mid-payload. Whether it actually *sees* something half written is then purely a property of the
    write strategy under test, which is what the tests below assert.

    The two writers emit byte-identical payloads because that is what really happens in HiSim: two
    processes that miss the same cache key compute the same entry, the contents being a pure function
    of the key. So any difference the reader observes is truncation, never disagreement.
    """

    CHUNK_COUNT: ClassVar[int] = 8
    LINES_PER_CHUNK: ClassVar[int] = 512
    HANDSHAKE_TIMEOUT_SECONDS: ClassVar[float] = 10.0
    # Both writers describe the same inputs, because they are computing the same key.
    CACHE_KEY_STRING: ClassVar[str] = '{"location": "Aachen"}2021-01-01|2021-12-31|60'

    def __init__(self, target_path: str) -> None:
        """Builds the payload and the events that sequence the writers against the reader."""
        self.target_path = target_path
        self.chunks = [
            "".join(f"chunk={chunk_index},line={line_index},{'p' * 64}\n" for line_index in range(self.LINES_PER_CHUNK))
            for chunk_index in range(self.CHUNK_COUNT)
        ]
        self.expected_content = "".join(self.chunks)
        self.first_chunks_written = [threading.Event() for _ in range(2)]
        self.reader_has_probed = threading.Event()
        self.writers_finished = threading.Event()
        self.observed_contents: List[str] = []

    def _emit(self, handle: Any, writer_index: int) -> None:
        """Writes the payload chunk by chunk, pausing after the first chunk for the reader."""
        handle.write(self.chunks[0])
        handle.flush()
        self.first_chunks_written[writer_index].set()
        self.reader_has_probed.wait(self.HANDSHAKE_TIMEOUT_SECONDS)
        for chunk in self.chunks[1:]:
            handle.write(chunk)
            handle.flush()

    def write_directly(self, writer_index: int) -> None:
        """Writes straight to the final path -- the pattern every cache writer used before the fix."""
        with open(self.target_path, "w", encoding="utf-8") as handle:
            self._emit(handle, writer_index)

    def write_atomically(self, writer_index: int) -> None:
        """Writes through :func:`hisim.caching.atomic_cache_write`, the pattern the fix introduces."""
        with atomic_cache_write(self.target_path, self.CACHE_KEY_STRING) as temporary_path:
            with open(temporary_path, "w", encoding="utf-8") as handle:
                self._emit(handle, writer_index)

    def read_repeatedly(self) -> None:
        """Polls the target path until both writers are done, recording every state it can read."""
        for event in self.first_chunks_written:
            event.wait(self.HANDSHAKE_TIMEOUT_SECONDS)
        while True:
            already_finished = self.writers_finished.is_set()
            try:
                with open(self.target_path, "r", encoding="utf-8") as handle:
                    self.observed_contents.append(handle.read())
            except FileNotFoundError:
                # An absent entry is a legitimate observation: the reader simply missed the cache.
                pass
            self.reader_has_probed.set()
            if already_finished:
                return

    def run(self, write: Callable[[int], None]) -> List[str]:
        """Runs both writers and the reader to completion and returns everything the reader saw."""
        threads = [threading.Thread(target=write, args=(index,)) for index in range(2)]
        reader = threading.Thread(target=self.read_repeatedly)
        reader.start()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=self.HANDSHAKE_TIMEOUT_SECONDS)
        self.writers_finished.set()
        reader.join(timeout=self.HANDSHAKE_TIMEOUT_SECONDS)
        assert not reader.is_alive(), "the reader thread did not finish"
        return self.observed_contents


@pytest.mark.base
def test_writing_a_cache_entry_directly_exposes_a_partial_file(tmp_path: pathlib.Path) -> None:
    """Pins the defect the atomic write exists to prevent, so the next test means something.

    This is the control arm. Writing straight to the final cache path -- what all six cache writers
    (``weather.py``, ``building/building.py``, ``generic_pv_system.py``, ``solar_thermal_system.py``,
    ``generic_car.py`` and the UTSP connector) used to do -- lets a concurrent reader open the entry
    while it is still being written and come away with a truncated prefix of it. In HiSim that prefix
    is a weather year cut in half, which either fails to parse or, worse, parses cleanly and silently
    shortens the simulation.

    If this test ever stops observing a partial read, the harness has lost its grip on the timing and
    the companion test below is no longer proving anything.
    """
    harness = CacheRaceHarness(str(tmp_path / "Weather_deadbeef.cache"))
    observations = harness.run(harness.write_directly)

    partial_observations = [seen for seen in observations if seen != harness.expected_content]
    assert partial_observations, "expected the direct write to expose at least one half-written state"
    assert all(harness.expected_content.startswith(seen) for seen in partial_observations)


@pytest.mark.base
def test_atomic_cache_write_never_exposes_a_partial_file(tmp_path: pathlib.Path) -> None:
    """Two concurrent writers plus a reader: the reader sees the whole entry or no entry at all.

    This is the regression guard for the CI failure in which one scenario setup read the weather
    cache entry that its near-twin was still writing beside it. With the write landed by
    ``os.replace`` the final path only ever holds a complete entry, so every read the harness manages
    is byte-identical to the payload; reads that find nothing are fine, since a miss just means the
    caller computes the entry itself.

    The temporary file must also be gone afterwards, both because leftovers accumulate in a directory
    nobody prunes and because a stray name there would eventually be mistaken for an entry.
    """
    harness = CacheRaceHarness(str(tmp_path / "Weather_deadbeef.cache"))
    observations = harness.run(harness.write_atomically)

    assert observations, "the reader never managed to read the entry at all"
    assert all(seen == harness.expected_content for seen in observations)
    assert pathlib.Path(harness.target_path).read_text(encoding="utf-8") == harness.expected_content
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "Weather_deadbeef.cache",
        "Weather_deadbeef.cache" + CacheEntryMetadata.SUFFIX,
    ]


@pytest.mark.base
def test_atomic_cache_write_leaves_no_debris_when_the_write_fails(tmp_path: pathlib.Path) -> None:
    """A write that raises must leave the previous entry intact and no temporary file behind.

    Cache computations are long and interruptible, so failures are not hypothetical. The helper has
    to unwind cleanly: the exception reaches the caller unchanged, whatever was cached before is
    still there for the next run, and the half-written temporary file is removed rather than left to
    accumulate in ``hisim/inputs/cache``.
    """
    target_path = tmp_path / "Weather_deadbeef.cache"
    target_path.write_text("the previous entry\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="computation blew up"):
        with atomic_cache_write(str(target_path), "the inputs of the new entry") as temporary_path:
            pathlib.Path(temporary_path).write_text("half of the new entry", encoding="utf-8")
            raise RuntimeError("computation blew up")

    assert target_path.read_text(encoding="utf-8") == "the previous entry\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Weather_deadbeef.cache"]


class CacheKeyFixture:
    """A minimal configuration plus the simulation parameters a cache key is built from.

    ``get_cache_file`` needs something with ``to_json`` and a ``SimulationParameters``; building a
    real component configuration would drag half the component layer into a test about file naming.
    Keeping the pair in one object lets each test derive the entry path, write an entry, tamper with
    its metadata and look it up again without repeating six lines of setup.
    """

    def __init__(self, cache_directory: pathlib.Path) -> None:
        """Points the simulation parameters at a scratch cache directory.

        Args:
            cache_directory: the temporary directory the entry is written into.
        """
        self.simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
        self.simulation_parameters.cache_dir_path = str(cache_directory)

    class Configuration:
        """Stands in for a component configuration; only ``to_json`` matters to the key."""

        @staticmethod
        def to_json() -> str:
            """Returns the configuration JSON that the cache key is built from.

            Returns:
                str: a fixed JSON document, so the key is stable across the test's two lookups.
            """
            return '{"name": "metadata-demo"}'

    def lookup(self) -> Tuple[bool, str]:
        """Asks ``get_cache_file`` whether this key has an entry, and where it would live.

        Returns:
            Tuple[bool, str]: the advisory ``exists`` flag and the absolute entry path.
        """
        return utils.get_cache_file(
            component_key="MetadataDemo",
            parameter_class=self.Configuration(),
            my_simulation_parameters=self.simulation_parameters,
        )

    def write_entry(self, contents: str, effective_cache_key_string: Optional[str] = None) -> str:
        """Writes an entry for this key, optionally lying about the inputs that produced it.

        Args:
            contents: the payload to store.
            effective_cache_key_string: what to record as the effective inputs; defaults to the
                honest value, so that passing something else simulates a poisoned entry.

        Returns:
            str: the path the entry was written to.
        """
        _, cache_filepath = self.lookup()
        honest = utils.build_cache_key_string(self.Configuration(), self.simulation_parameters)
        with atomic_cache_write(cache_filepath, effective_cache_key_string or honest) as temporary_path:
            pathlib.Path(temporary_path).write_text(contents, encoding="utf-8")
        return cache_filepath


@pytest.mark.base
def test_a_written_entry_is_found_again_with_its_metadata(tmp_path: pathlib.Path) -> None:
    """The ordinary path: an entry written honestly validates and is served on the next lookup.

    This is the control for the two tests below. Without it a bug that rejected every entry would
    still pass them, and the cache would have quietly stopped being a cache.
    """
    fixture = CacheKeyFixture(tmp_path)
    cache_filepath = fixture.write_entry("the profile\n")

    exists, found_path = fixture.lookup()

    assert exists is True
    assert found_path == cache_filepath
    metadata_path = pathlib.Path(CacheEntryMetadata.metadata_filepath(cache_filepath))
    assert metadata_path.is_file()
    assert CacheEntryMetadata.hash_of(metadata_path.read_text(encoding="utf-8")) in cache_filepath


@pytest.mark.base
def test_an_entry_without_metadata_is_discarded_and_recomputed(tmp_path: pathlib.Path) -> None:
    """An entry nothing describes is deleted on sight and reported as a miss.

    Every entry written before the metadata scheme existed is in exactly this state, which is what
    makes the migration self-executing: there is no purge to run and no directory to wipe, because
    the first lookup of an undescribed entry removes it. The same rule covers a metadata file lost to
    a partial copy or a botched manual edit.
    """
    fixture = CacheKeyFixture(tmp_path)
    cache_filepath = fixture.write_entry("the profile\n")
    pathlib.Path(CacheEntryMetadata.metadata_filepath(cache_filepath)).unlink()

    exists, _ = fixture.lookup()

    assert exists is False
    assert not pathlib.Path(cache_filepath).exists()


@pytest.mark.base
def test_an_entry_whose_metadata_disagrees_with_its_name_is_discarded(tmp_path: pathlib.Path) -> None:
    """A poisoned entry -- contents from inputs the key does not describe -- is found and deleted.

    This is the case the metadata exists for. Recording what was *requested* would make such an entry
    look sound, because the request is what the filename was hashed from; recording what actually
    produced the bytes turns validation into a comparison, and the comparison fails exactly when the
    two diverged. It is also tamper-evident: the metadata cannot be edited into agreement without
    recomputing the digest in the name.
    """
    fixture = CacheKeyFixture(tmp_path)
    cache_filepath = fixture.write_entry(
        "a different household's profile\n", effective_cache_key_string='{"name": "something-else"}'
    )

    exists, _ = fixture.lookup()

    assert exists is False
    assert not pathlib.Path(cache_filepath).exists()
    assert not pathlib.Path(CacheEntryMetadata.metadata_filepath(cache_filepath)).exists()


@pytest.mark.base
def test_the_metadata_lands_before_the_data(tmp_path: pathlib.Path) -> None:
    """The data file is the last thing to appear, so no observer can see an unvalidatable entry.

    ``get_cache_file`` decides an entry exists by looking at the data file. If the data landed first,
    a crash or a concurrent reader in the gap would find an entry with no metadata -- which the rules
    above then delete, throwing away a perfectly good computation. The reverse order can only leave
    an orphan metadata file, which nothing looks at.
    """
    fixture = CacheKeyFixture(tmp_path)
    _, cache_filepath = fixture.lookup()
    metadata_filepath = CacheEntryMetadata.metadata_filepath(cache_filepath)
    honest = utils.build_cache_key_string(fixture.Configuration(), fixture.simulation_parameters)

    with atomic_cache_write(cache_filepath, honest) as temporary_path:
        pathlib.Path(temporary_path).write_text("the profile\n", encoding="utf-8")
        assert not pathlib.Path(cache_filepath).exists()
        assert not pathlib.Path(metadata_filepath).exists()

    assert pathlib.Path(metadata_filepath).is_file()
    assert pathlib.Path(cache_filepath).is_file()
