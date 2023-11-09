""" For calling a model with profiler and creating a log file. """
# clean
import cProfile
import pstats
import hisim.hisim_main as hsm


def maincall() -> None:
    """For calling the Hisim main."""
    # change call here as needed
    # hsm.main("..\\system_setups\\modular_example.py")
    hsm.main(
        "..\\system_setups\\household_with_advanced_hp_hws_hds_pv.py",
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

    with open(
        "..\\system_setups\\results\\profilingStatsAsTextSortedCumulative.txt",
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("cumulative")
        stats.print_stats()
    with open(
        "..\\system_setups\\results\\profilingStatsAsTextSortedcalls.txt",
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("ncalls")
        stats.print_stats()
    with open(
        "..\\system_setups\\results\\profilingStatsAsTextSortedTotalTime.txt",
        "w",
        encoding="utf-8",
    ) as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats("tottime")
        stats.print_stats()
    stats.dump_stats("..\\system_setups\\results\\profile-export-data.prof")
