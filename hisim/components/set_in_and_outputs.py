# Generic/Built-in
from dataclasses import dataclass
import hisim.component as cp
from hisim.components.controller_l2_energy_management_system import Controller as Controller_l2
from typing import List
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"

"""

"""

@dataclass
class DynamicConnectionEntry:
    SourceComponent: str
    SourceTags: list[str]
    SourceWeight: int

class DynamicComponent(cp.Component):
    MyComponentInputs :List[DynamicConnectionEntry] = []
    MyComponentOutputs :List[DynamicConnectionEntry] = []

    def __init__(self, name: str,my_simulation_parameters: SimulationParameters):
        super().__init__(name=name, my_simulation_parameters=my_simulation_parameters)
        self.MyComponentInputs = self.add_output(self.ComponentName,
                                       DynamicComponent.MyComponentOutputs,
                                       lt.LoadTypes.Any,
                                       lt.Units.Any)
    def add_component_input(self, source_component, tags):
        self.MyComponentInputs.append(DynamicConnectionEntry(source_component, tags))
        # init normally ...


class TestSystem:
    def make_ems():
        con_l2 = Controller_l2()
        con_l2.add_component_input("HeatPump", ["Heating", "Gas"], 5)

