"""Profiling harness that runs a HiSim setup under cProfile and writes sorted statistics to disk."""

# clean
from __future__ import annotations

import cProfile
import pstats
from collections.abc import Callable
from pathlib import Path

from hisim import hisim_main


def run_default_simulation() -> None:
    """Run the HiSim simulation by calling hisim_main.main()."""
    # change call here as needed
    # hisim_main.main("..\\system_setups\\modular_example.py")
    hisim_main.main("../system_setups/household_heat_pump.py")


def profile_and_write_stats(
    simulation_fn: Callable[[], None], results_path: Path
) -> None:
    """Profile ``simulation_fn`` with :mod:`cProfile` and write the stats to ``results_path``.

    This wraps the profiling orchestration that used to live inline in the
    ``__main__`` block so it can be exercised from tests without running a full
    HiSim simulation. ``simulation_fn`` is called once under the profiler; three
    text files with the stats sorted by cumulative time, call count and total
    time are written into ``results_path`` (which is created if missing), plus a
    binary ``.prof`` dump that can be visualised with tools such as snakeviz.

    Args:
        simulation_fn: zero-argument callable to profile (e.g. :func:`run_default_simulation`).
        results_path: directory to write the profiling artefacts into.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    simulation_fn()
    profiler.disable()

    results_path.mkdir(parents=True, exist_ok=True)

    sort_specs = [
        ("profilingStatsAsTextSortedCumulative.txt", "cumulative"),
        ("profilingStatsAsTextSortedcalls.txt", "ncalls"),
        ("profilingStatsAsTextSortedTotalTime.txt", "tottime"),
    ]
    for filename, sort_key in sort_specs:
        with open(results_path / filename, "w", encoding="utf-8") as f:
            pstats.Stats(profiler, stream=f).sort_stats(sort_key).print_stats()
    pstats.Stats(profiler).dump_stats(results_path / "profile-export-data.prof")


if __name__ == "__main__":
    # Called from the command line: runs HiSim main under cProfile and dumps
    # sorted stats to text files plus a .prof file (visualizable with snakeviz).
    profile_and_write_stats(run_default_simulation, Path("../system_setups/results/"))
