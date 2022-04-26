# Owned
from hisim.component import Component
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
import hisim.component as cp
__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


class Test_InandOutputs(Component):
    """
    Gets Control Signal and calculates on base of it Massflow and Temperature of Massflow
    """
    #Input

    #Output
    MassflowOutput= "Hot Water Energy Output"

    def __init__(self,my_simulation_parameters: SimulationParameters ):
        super().__init__(name="Test_InandOutputs", my_simulation_parameters=my_simulation_parameters)

        self.mass_out: cp.ComponentOutput = self.add_output(self.ComponentName, Test_InandOutputs.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int):
        pass

    def i_simulate(self, timestep: int, force_convergence: bool):

        print(2)
