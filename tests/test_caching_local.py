"""Tests for the local tier of the HiSim cache service, :mod:`hisim.caching.local`.

The one behaviour this module has to pin is atomicity, because it is the whole reason the local tier
of ``roadmap/cache_service_spec.md`` §4 ships ahead of the rest of the cache service. A cache entry
that lands in place by rename can never be read half written; one that is written straight to its
final path can, and used to be, which is what broke a parallel scenario regeneration in CI.

The first test is the control: it pins the old behaviour so the second test cannot quietly stop
proving anything if the harness ever loses its grip on the timing.
"""

# clean

import pathlib
import threading
from typing import Any, Callable, ClassVar, List

import pytest

from hisim.caching import atomic_cache_write


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
        with atomic_cache_write(self.target_path) as temporary_path:
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
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Weather_deadbeef.cache"]


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
        with atomic_cache_write(str(target_path)) as temporary_path:
            pathlib.Path(temporary_path).write_text("half of the new entry", encoding="utf-8")
            raise RuntimeError("computation blew up")

    assert target_path.read_text(encoding="utf-8") == "the previous entry\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Weather_deadbeef.cache"]
