# Generic/Built-in
import random
from typing import List

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
class RandomNumbers(Component):
    RandomOutput: str = "Random Numbers"

    def __init__(self, name: str, timesteps: int, minimum: float, maximum: float, my_simulation_parameters: SimulationParameters ):
        super().__init__(name, my_simulation_parameters=my_simulation_parameters)
        self.values: List[float] = []
        number_range = maximum - minimum
        for x in range(timesteps):
            number = minimum + random.random() * number_range
            self.values.append(number)
        self.output1 = self.add_output(self.ComponentName,
                                       RandomNumbers.RandomOutput,
                                       lt.LoadTypes.Any,
                                       lt.Units.Any)

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        val1: float = self.values[timestep]
        stsv.set_output_value(self.output1, float(val1))

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_save_state(self):
        pass
