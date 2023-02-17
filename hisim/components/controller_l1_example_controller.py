# Generic/Built-in
import copy

# Owned
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class SimpleController(Component):
    StorageFillLevel = "Fill Level Percent"
    GasHeaterPowerPercent = "Gas Heater Power Level"

    def __init__(
        self, name: str, my_simulation_parameters: SimulationParameters
    ) -> None:
        super().__init__(name, my_simulation_parameters=my_simulation_parameters)
        self.input1: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.StorageFillLevel,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            True,
        )
        self.output1: ComponentOutput = self.add_output(
            self.component_name,
            SimpleController.GasHeaterPowerPercent,
            lt.LoadTypes.GAS,
            lt.Units.PERCENT,
        )
        self.state = 0
        self.previous_state = self.state

    def i_save_state(self) -> None:
        self.previous_state = self.state

    def i_restore_state(self) -> None:
        self.state = self.previous_state

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return
        percent = stsv.get_input_value(self.input1)
        if percent < 0.4:
            self.state = 1
        if percent > 0.99:
            self.state = 0
        stsv.set_output_value(self.output1, self.state)
