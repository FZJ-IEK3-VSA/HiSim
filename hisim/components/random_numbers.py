# Generic/Built-in
import random
from typing import List

# Owned
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


class RandomNumbers(Component):
    RandomOutput: str = "Random Numbers"

    def __init__(
        self,
        name: str,
        timesteps: int,
        minimum: float,
        maximum: float,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        super().__init__(name, my_simulation_parameters=my_simulation_parameters)
        self.values: List[float] = []
        self.minimum = minimum
        self.maximum = maximum
        number_range = maximum - minimum
        for _ in range(timesteps):
            number = minimum + random.random() * number_range
            self.values.append(number)
        self.output1 = self.add_output(
            self.component_name,
            RandomNumbers.RandomOutput,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )

    def i_restore_state(self) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        val1: float = self.values[timestep]
        stsv.set_output_value(self.output1, float(val1))

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_save_state(self) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        lines = []
        lines.append("Random number Generator: {}".format(self.component_name))
        lines.append("Minimum number: {}".format(self.minimum))
        lines.append("Maximum number: {}".format(self.maximum))
        return lines
