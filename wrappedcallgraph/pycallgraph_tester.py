import os
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from wrappedcallgraph.callgraphwrap import method_pattern
import cProfile

#pr = cProfile.Profile()


class PyCallGraph_Obj:
    
    def __init__(self) -> None:
        #pr.enable()
        self.execute()
        #pr.disable()
        method_pattern.make_graphviz_chart(with_labels=True, time_resolution=10, filename='wrappedcallgraph/HISIM_Method_Pattern.png')
        
    def execute(self):
        path = "examples/basic_household.py"
        func = "basic_household_explicit"
        mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60 * 60)
        hisim_main.main(path, func,mysimpar)
        log.information(os.getcwd())

run=PyCallGraph_Obj()



# # this profile can be visualized by typing the command "snakeviz wrappedcallgraph/pycallgraph_tester.prof" in the terminal
# pr.dump_stats("wrappedcallgraph/pycallgraph_tester.prof")


