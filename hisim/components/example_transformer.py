# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from typing import List
class Transformer(Component):
    TransformerInput = "Input1"
    TransformerInput2 = "Optional Input1"
    TransformerOutput = "MyTransformerOutput"
    TransformerOutput2 = "MyTransformerOutput2"

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters ) -> None:
        super().__init__(name=name, my_simulation_parameters=my_simulation_parameters)
        self.input1: ComponentInput = self.add_input(self.component_name, Transformer.TransformerInput, lt.LoadTypes.ANY, lt.Units.ANY, True)
        self.input2: ComponentInput = self.add_input(self.component_name, Transformer.TransformerInput2, lt.LoadTypes.ANY, lt.Units.ANY, False)
        self.output1: ComponentOutput = self.add_output(self.component_name, Transformer.TransformerOutput, lt.LoadTypes.ANY, lt.Units.ANY)
        self.output2: ComponentOutput = self.add_output(self.component_name, Transformer.TransformerOutput2, lt.LoadTypes.ANY, lt.Units.ANY)

    def i_save_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_restore_state(self) -> None:
        pass
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool) -> None:
        startval_1 = stsv.get_input_value(self.input1)
        startval_2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, startval_1 * 5)
        stsv.set_output_value(self.output2, startval_2 * 1000)
    def write_to_report(self) -> List[str]:
        lines = []
        lines.append("Transformer: " + self.component_name)
        return lines

