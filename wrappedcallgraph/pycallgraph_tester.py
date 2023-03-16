import os
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from wrappedcallgraph.callgraphwrap import method_pattern


class PyCallGraph_Obj:
    def __init__(self) -> None:

        self.execute()
        method_pattern.make_graphviz_chart(
            time_resolution=10, filename="wrappedcallgraph/HISIM_Method_Pattern.png"
        )

    def execute(self):
        path = "examples/basic_household.py"
        func = "basic_household_explicit"
        mysimpar = SimulationParameters.one_day_only(
            year=2019, seconds_per_timestep=60 * 60
        )
        hisim_main.main(path, func, mysimpar)
        log.information(os.getcwd())


run = PyCallGraph_Obj()
