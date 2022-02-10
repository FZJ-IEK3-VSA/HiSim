# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

class Transformer(Component):
    TransformerInput = "Input1"
    TransformerInput2 = "Optional Input1"
    TransformerOutput = "MyTransformerOutput"
    TransformerOutput2 = "MyTransformerOutput2"

    def __init__(self, name: str, my_simulation_parameters: SimulationParameters ):
        super().__init__(name=name, my_simulation_parameters=my_simulation_parameters)
        self.input1: ComponentInput = self.add_input(self.ComponentName, Transformer.TransformerInput, lt.LoadTypes.Any, lt.Units.Any, True)
        self.input2: ComponentInput = self.add_input(self.ComponentName, Transformer.TransformerInput2, lt.LoadTypes.Any, lt.Units.Any, False)
        self.output1: ComponentOutput = self.add_output(self.ComponentName, Transformer.TransformerOutput, lt.LoadTypes.Any, lt.Units.Any)
        self.output2: ComponentOutput = self.add_output(self.ComponentName, Transformer.TransformerOutput2, lt.LoadTypes.Any, lt.Units.Any)

    def i_save_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        startval_1 = stsv.get_input_value(self.input1)
        startval_2 = stsv.get_input_value(self.input2)
        stsv.set_output_value(self.output1, startval_1 * 5)
        stsv.set_output_value(self.output2, startval_2 * 1000)
