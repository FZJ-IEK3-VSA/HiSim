"""Module for generating random numbers."""

# clean

# Generic/Built-in
import random
from typing import List, ClassVar, Optional
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

    building_name: str
    name: str
    timesteps: int
    minimum: float
    maximum: float

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return RandomNumbersConfig(
            building_name="BUI1",
            name="RandomNumbers",
            timesteps=100,
            minimum=1,
            maximum=20,
        )


class RandomNumbers(Component):
    """Component that generates random numbers for simulation.

    This component pre-generates a list of random numbers within a specified range
    (minimum to maximum) for each timestep of the simulation. During simulation, it
    outputs the pre-generated random value for the current timestep.

    Key attributes:
        - values: List of pre-generated random numbers for all timesteps
        - minimum: Minimum value of the random number range (from config)
        - maximum: Maximum value of the random number range (from config)

    Key methods:
        - i_simulate: Outputs the pre-generated random value for the current timestep
    """

    RandomOutput: ClassVar[str] = "Random Numbers"

    def __init__(
        self,
        config: RandomNumbersConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
        rng: Optional[random.Random] = None,
    ) -> None:
        """Initialize the class.

        Args:
            config: Configuration holding the number of timesteps and the
                ``[minimum, maximum]`` range to draw from.
            my_simulation_parameters: Simulation parameters of the run.
            my_display_config: Display configuration for the component.
            rng: Optional :class:`random.Random` instance used to draw the
                values. When ``None`` (the default) a fresh
                ``random.Random()`` is used so the global ``random`` module
                state is never mutated and production output stays
                non-deterministic. Passing a seeded instance (e.g.
                ``random.Random(0)``) makes ``self.values`` reproducible,
                which is what tests want.
        """
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.minimum = config.minimum
        self.maximum = config.maximum
        if rng is None:
            rng = random.Random()
        self.values: List[float] = self._generate_values(
            minimum=config.minimum,
            maximum=config.maximum,
            timesteps=config.timesteps,
            rng=rng,
        )
        self.output1 = self.add_output(
            self.component_name,
            RandomNumbers.RandomOutput,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Random Number Output",
        )

    @staticmethod
    def _generate_values(
        minimum: float,
        maximum: float,
        timesteps: int,
        rng: random.Random,
    ) -> List[float]:
        """Generate ``timesteps`` random values drawn from ``[minimum, maximum]``.

        This is a pure helper: the only state it touches is ``rng``. Passing a
        seeded :class:`random.Random` makes the returned list fully
        reproducible without mutating the module-global ``random`` state, so
        the generation logic (exactly ``timesteps`` values, all within bounds)
        can be asserted on directly and in isolation from the ``Component``
        framework.

        Args:
            minimum: Lower bound (inclusive) of the value range.
            maximum: Upper bound (inclusive in practice, ``random`` draws on
                ``[0, 1)`` so ``maximum`` is approached but never exceeded).
            timesteps: Number of values to generate. Must be non-negative.
            rng: The :class:`random.Random` instance to draw from.

        Returns:
            A list of ``timesteps`` floats, each in ``[minimum, maximum)``.

        Raises:
            ValueError: If ``timesteps`` is negative.
        """
        if timesteps < 0:
            raise ValueError(
                f"timesteps must be non-negative, got {timesteps}."
            )
        number_range = maximum - minimum
        values: List[float] = []
        for _ in range(timesteps):
            values.append(minimum + rng.random() * number_range)
        return values

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
