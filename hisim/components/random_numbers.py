"""Module for generating random numbers."""

# clean

# Generic/Built-in
import random
from typing import List, ClassVar, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import Component, SingleTimeStepValues
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class RandomNumbersConfig(ConfigBase):
    """Configuration of the Random Numbers.

    Holds the range to draw from and the seed that fixes which numbers are drawn. The seed is part
    of the configuration rather than an argument because a system description has to be able to say
    what a run will do: two runs of one configuration produce the same series, which is what lets a
    recorded energy-system file reproduce the setup it was recorded from.

    How many values are drawn is deliberately *not* configured. That count is the length of the
    simulation, which belongs to the simulation parameters, and writing it here would pin a
    component to the horizon it happened to be built under.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return RandomNumbers.get_full_classname()

    component_id: ComponentID
    minimum: float
    maximum: float
    seed: int

    @classmethod
    def get_default_config(cls) -> "RandomNumbersConfig":
        """Gets a default config."""
        return RandomNumbersConfig(
            component_id=ComponentID(name="RandomNumbers"),
            minimum=1,
            maximum=20,
            seed=1,
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

    # A generator of random values is not a device: it has nothing to buy and nothing to run.
    # Declaring that is what lets a setup built from it ask for costs at all;
    # see Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    RandomOutput: ClassVar[str] = "RandomNumbers"

    def __init__(
        self,
        config: RandomNumbersConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: Optional[DisplayConfig] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        """Initialize the class.

        Args:
            config: Configuration holding the ``[minimum, maximum]`` range to draw from and the
                seed that fixes the series.
            my_simulation_parameters: Simulation parameters of the run; their timestep count is how
                many values are drawn.
            my_display_config: Display configuration for the component.
            rng: Optional :class:`random.Random` instance used instead of the one the seed builds.
                Only a test that wants to inject a specific generator needs this; leaving it
                ``None`` draws from ``random.Random(config.seed)``, which never touches the global
                ``random`` module state and gives the same series on every run.
        """
        if my_display_config is None:
            my_display_config = DisplayConfig()
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
            rng = random.Random(config.seed)
        self.values: List[float] = self._generate_values(
            minimum=config.minimum,
            maximum=config.maximum,
            timesteps=my_simulation_parameters.timesteps,
            rng=rng,
        )
        self.random_output = self.add_output(
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

        random_value: float = self.values[timestep]
        stsv.set_output_value(self.random_output, float(random_value))

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
