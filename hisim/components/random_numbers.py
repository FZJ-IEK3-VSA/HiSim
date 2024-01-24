"""Module for generating random numbers."""

# clean

# Generic/Built-in
import random
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import Component, SingleTimeStepValues, ConfigBase, DisplayConfig
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

    """Random number class."""

    RandomOutput: str = "Random Numbers"

    def __init__(
        self,
        config: RandomNumbersConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
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
        """Restores the state."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""

        val1: float = self.values[timestep]
        stsv.set_output_value(self.output1, float(val1))

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write to report."""
        lines = []
        lines.append(f"Random number Generator: {self.component_name}")
        lines.append(f"Minimum number: {self.minimum}")
        lines.append(f"Maximum number: {self.maximum}")
        return lines
