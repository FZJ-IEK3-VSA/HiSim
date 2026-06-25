"""For calling a model with profiler and creating a log file."""

# clean
from __future__ import annotations

import cProfile
import pstats
from collections.abc import Callable
from pathlib import Path

import hisim.hisim_main as hsm


def run_simulation() -> None:
    """Run the HiSim simulation by calling hsm.main()."""
def run_simulation() -> None:
    """Run the HiSim simulation by calling hsm.main()."""
    # change call here as needed
    # hsm.main("..\\system_setups\\modular_example.py")
    hsm.main(
        str(Path("../system_setups/household_heat_pump.py")),
    )


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
        simulation_fn: zero-argument callable to profile (e.g. :func:`run_simulation`).
        results_path: directory to write the profiling artefacts into.
    """
    profiler = cProfile.Profile()
    profiler.enable()
    simulation_fn()
    profiler.disable()

    results_path = Path(results_path)
    results_path.mkdir(parents=True, exist_ok=True)

    with open(
        results_path.joinpath("profilingStatsAsTextSortedCumulative.txt"),
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("cumulative")
        stats.print_stats()
    with open(
        results_path.joinpath("profilingStatsAsTextSortedcalls.txt"),
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("ncalls")
        stats.print_stats()
    with open(
        results_path.joinpath("profilingStatsAsTextSortedTotalTime.txt"),
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("tottime")
        stats.print_stats()
    stats.dump_stats(results_path.joinpath("profile-export-data.prof"))


if __name__ == "__main__":
    """Called from the command line.
    This function calls HiSim main and performs a profiling with cprofile.
    The results are dumped to various text files in the result directory
    and the .prof file can be visualized with for example snakeviz.
    """
    profile_and_write_stats(run_simulation, Path("../system_setups/results/"))
