# Generic/Built-in
from hisim.simulationparameters import SimulationParameters
from dataclasses import dataclass
import hisim.component as cp
from hisim.components.controller_l2_energy_management_system import Controller as Controller_l2
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib
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



class DynamicComponent(cp.Component):
    cp.ComponentInput: list[DynamicConnectionEntry]
    cp.ComponentOutput: list[DynamicConnectionEntry]

    def add_component_input(self, source_component, tags):
        self.ComponentInput.append(DynamicConnectionEntry(source_component.Name, tags))
        # init normally ...


class TestSystem:
    def make_ems():
        con_l2 = Controller_l2()
        con_l2.add_component_input("HeatPump", ["Heating", "Gas"], 5)

