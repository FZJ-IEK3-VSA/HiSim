
""" For calling a model with profiler and creating a log file. """
import cProfile
import pstats
import hisim.hisim_main as hsm


def maincall():
    """ For calling the Hisim main. """
    # change call here as needed
    hsm.main("..\\examples\\basic_household.py", "basic_household_with_default_connections")


if __name__ == "__main__":
    """ Called from the command line.
    This function calls HiSim main and performs a profiling with cprofile.
    The results are dumped to various text files in the result directory
    and the .prof file can be visualized with for example snakeviz.
    """

    profiler = cProfile.Profile()
    profiler.enable()
    maincall()
    profiler.disable()

    with open("..\\examples\\results\\profilingStatsAsTextSortedCumulative.txt", "w", encoding="utf-8") as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats('cumulative')
        stats.print_stats()
    with open("..\\examples\\results\\profilingStatsAsTextSortedcalls.txt", "w", encoding="utf-8") as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats('ncalls')
        stats.print_stats()
    with open("..\\examples\\results\\profilingStatsAsTextSortedTotalTime.txt", "w", encoding="utf-8") as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats('tottime')
        stats.print_stats()
    stats.dump_stats('..\\examples\\results\\profile-export-data.prof')
