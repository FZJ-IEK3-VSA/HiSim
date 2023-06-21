# Generic/Built-in
import random
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
)
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class RandomNumbersConfig(ConfigBase):

    """Configuration of the Random Numbers."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return RandomNumbers.get_full_classname()

    name: str
    timesteps: int
    minimum: float
    maximum: float

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return RandomNumbersConfig(
            name="RandomNumbers",
            timesteps=100,
            minimum=1,
            maximum=20,
        )


class RandomNumbers(Component):
    RandomOutput: str = "Random Numbers"

    def __init__(
        self,
        config: RandomNumbersConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.values: List[float] = []
        self.minimum = config.minimum
        self.maximum = config.maximum
        number_range = config.maximum - config.minimum
        for _ in range(config.timesteps):
            number = config.minimum + random.random() * number_range
            self.values.append(number)
        self.output1 = self.add_output(
            self.component_name,
            RandomNumbers.RandomOutput,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Random Number Output",
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
