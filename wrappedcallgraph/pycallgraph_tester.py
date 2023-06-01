"""Pycallgraph tester module."""
import os
import cProfile
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from wrappedcallgraph.callgraphwrap_test import MethodChart

profiler = cProfile.Profile()
profiler.enable()


class PyCallGraphObject:

    """PyCallGraph Object Class."""

    def __init__(self) -> None:
        """Initializes the class."""

        self.execute()
        methodchart = MethodChart()
        methodchart.make_graphviz_chart(
            time_resolution=10, filename="HISIM_Method_Pattern.png"
        )
        profiler.disable()

        profiler.dump_stats("profile.prof")

    def execute(self):
        """Executes a hisim example."""
        path = "../examples/basic_household.py"
        func = "basic_household_explicit"
        mysimpar = SimulationParameters.one_day_only(
            year=2019, seconds_per_timestep=60 * 60
        )
        hisim_main.main(path, func, mysimpar)
        log.information(os.getcwd())


run = PyCallGraphObject()
