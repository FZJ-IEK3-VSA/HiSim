""" For calling a model with profiler and creating a log file. """

# clean
import cProfile
import pstats
from pathlib import Path

import hisim.hisim_main as hsm


def maincall() -> None:
    """For calling the Hisim main."""
    # change call here as needed
    # hsm.main("..\\system_setups\\modular_example.py")
    hsm.main(
        str(Path("../system_setups/household_heat_pump.py")),
    )


if __name__ == "__main__":
    """Called from the command line.
    This function calls HiSim main and performs a profiling with cprofile.
    The results are dumped to various text files in the result directory
    and the .prof file can be visualized with for example snakeviz.
    """

    profiler = cProfile.Profile()
    profiler.enable()
    maincall()
    profiler.disable()

    results_path = Path("../system_setups/results/")

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
