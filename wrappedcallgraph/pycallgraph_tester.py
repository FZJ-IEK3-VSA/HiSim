"""Pycallgraph tester module."""
import os
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from wrappedcallgraph.callgraphwrap import MethodChartGraph, MethodChartCallGraphFactory, SingletonDataClassNode #import METHOD_PATTERN


# class PyCallGraphObject:

#     """PyCallGraph Object Class."""

#     def __init__(self) -> None:
#         """Initializes the class."""

#         self.execute()
#         METHOD_PATTERN.make_graphviz_chart(
#             time_resolution=10, filename="wrappedcallgraph/HISIM_Method_Pattern.png"
#         )

#     def execute(self):
#         """Executes a hisim example."""
#         path = "examples/basic_household.py"
#         func = "basic_household_explicit"
#         mysimpar = SimulationParameters.one_day_only(
#             year=2019, seconds_per_timestep=60 * 60
#         )
#         hisim_main.main(path, func, mysimpar)
#         log.information(os.getcwd())


# run = PyCallGraphObject()

######################################################################
# Test Noah's Ideas
class PyCallGraphObject:

    """PyCallGraph Object Class."""

    def __init__(self) -> None:
        """Initializes the class."""

        self.execute()
        # METHOD_PATTERN.make_graphviz_chart(
        #     time_resolution=10, filename="wrappedcallgraph/HISIM_Method_Pattern.png"
        # )
        method_node_container = SingletonDataClassNode()
        method_graph = MethodChartGraph(singleton_node_container=method_node_container)


    def execute(self):
        """Executes a hisim example."""
        path = "examples/basic_household.py"
        func = "basic_household_explicit"
        mysimpar = SimulationParameters.one_day_only(
            year=2019, seconds_per_timestep=60 * 60
        )
        hisim_main.main(path, func, mysimpar)
        log.information(os.getcwd())


run = PyCallGraphObject()
