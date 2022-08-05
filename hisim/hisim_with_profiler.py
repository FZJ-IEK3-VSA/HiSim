import hisim.hisim_main as hsm
import cProfile, pstats

def maincall():
    # change call here as needed
    hsm.main( "..\\examples\\basic_household.py", "basic_household_with_default_connections")

if __name__ == "__main__":
    # this function calls hisim main and performs a profiling wiht cprofile.
    # the results are dumped to various text files in the result directory
    # and the .prof file can be visualized with for example snakeviz

    profiler = cProfile.Profile()
    profiler.enable()
    maincall()
    profiler.disable()

    with open("..\\examples\\results\\profilingStatsAsTextSortedCumulative.txt", "w") as f:
        stats = pstats.Stats(profiler,stream=f).sort_stats('cumulative')
        stats.print_stats()
    with open("..\\examples\\results\\profilingStatsAsTextSortedcalls.txt", "w") as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats('ncalls')
        stats.print_stats()
    with open("..\\examples\\results\\profilingStatsAsTextSortedTotalTime.txt", "w") as f:
        stats = pstats.Stats(profiler, stream=f).sort_stats('tottime')
        stats.print_stats()
    stats.dump_stats('..\\examples\\results\\profile-export-data.prof')
