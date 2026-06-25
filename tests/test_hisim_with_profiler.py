"""Tests for the profiling helper extracted from ``hisim/hisim_with_profiler.py``.

The profiling orchestration (cProfile enable/disable, the three pstats text dumps
and the binary ``.prof`` dump) used to live inline in the ``__main__`` block, so it
could only be exercised by launching the script as a process - which runs a full
``hsm.main()`` simulation (heavy I/O, network, file writes). It is now hoisted into
:func:`profile_and_write_stats`, which accepts the callable to profile and the
output directory; these tests drive it with a trivial callable and a ``tmp_path`` so
no HiSim simulation has to run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hisim.hisim_with_profiler import profile_and_write_stats


def _profiled_callable() -> None:
    """A tiny, cheap callable for profiling - recognisable in the stats output."""

    def _inner() -> int:
        return sum(range(1000))

    _inner()


@pytest.mark.base
def test_profile_and_write_stats_creates_all_artefacts(tmp_path: Path) -> None:
    """All four profiling artefacts are written to the requested directory."""
    profile_and_write_stats(_profiled_callable, tmp_path)

    expected = {
        "profilingStatsAsTextSortedCumulative.txt",
        "profilingStatsAsTextSortedcalls.txt",
        "profilingStatsAsTextSortedTotalTime.txt",
        "profile-export-data.prof",
    }
    assert {p.name for p in tmp_path.iterdir()} == expected
    for name in expected:
        assert (tmp_path / name).is_file()
        assert (tmp_path / name).stat().st_size > 0


@pytest.mark.base
def test_profile_and_write_stats_text_files_have_expected_sort_headers(
    tmp_path: Path,
) -> None:
    """Each text dump is sorted by the sort order implied by its filename."""
    profile_and_write_stats(_profiled_callable, tmp_path)

    headers = {
        "profilingStatsAsTextSortedCumulative.txt": "Ordered by: cumulative time",
        "profilingStatsAsTextSortedcalls.txt": "Ordered by: call count",
        "profilingStatsAsTextSortedTotalTime.txt": "Ordered by: internal time",
    }
    for filename, header in headers.items():
        text = (tmp_path / filename).read_text(encoding="utf-8")
        assert header in text, f"{filename} missing header {header!r}"


@pytest.mark.base
def test_profile_and_write_stats_records_the_profiled_callable(tmp_path: Path) -> None:
    """The callable's frame actually shows up in the profile, not just an empty run."""
    profile_and_write_stats(_profiled_callable, tmp_path)

    cumulative = (tmp_path / "profilingStatsAsTextSortedCumulative.txt").read_text(
        encoding="utf-8"
    )
    assert "_profiled_callable" in cumulative or "_inner" in cumulative


@pytest.mark.base
def test_profile_and_write_stats_creates_missing_results_path(tmp_path: Path) -> None:
    """A non-existent ``results_path`` is created on the fly."""
    nested = tmp_path / "nested" / "results"
    assert not nested.exists()

    profile_and_write_stats(_profiled_callable, nested)

    assert nested.is_dir()
    assert (nested / "profile-export-data.prof").is_file()


@pytest.mark.base
def test_profile_and_write_stats_runs_the_callable_once(tmp_path: Path) -> None:
    """The supplied callable is invoked exactly once."""
    calls = {"count": 0}

    def counting() -> None:
        calls["count"] += 1

    profile_and_write_stats(counting, tmp_path)
    assert calls["count"] == 1
