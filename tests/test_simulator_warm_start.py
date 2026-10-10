"""Every time step's convergence iteration starts from the values the previous step converged to.

The simulator iterates each time step until no output changes any more. On the first pass of a
step, a component that reads an output computed later in the component order has no value of
this step yet; it reads whatever the step's start vector holds. That start vector is the previous
step's converged vector (the warm start). Only step 0 starts from all zeros, because there is no
previous step. Until 2026-10-09 every step started from all zeros (bead hisim-4g9.23), so such a
reader saw 0 on every first pass.

The reproduction has two components. ``Reader`` is added before ``Source`` and reads its output;
``Source`` publishes ``100 + timestep``, or a constant 100. The reader records the input it sees on
every pass, and the tests read that record.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import pytest
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


@dataclass_json
@dataclass
class WarmStartStubConfig(ConfigBase):
    """Configuration of the two stub components of the warm-start reproduction.

    It carries the identity and one switch: whether the source publishes a constant or a value
    that changes with every time step. The reader ignores the switch.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the source stub; the config belongs to a test double."""
        return WarmStartSource.get_full_classname()

    component_id: ComponentID
    constant_source: bool = False


class WarmStartSource(Component):
    """Publishes one value per time step: ``100 + timestep``, or a constant 100.

    It has no inputs and no state, so its output on a step is the same on every pass. Added after
    the reader, it is computed after the reader on every pass.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The one output the reader reads.
    Value: ClassVar[str] = "Value"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: WarmStartStubConfig) -> None:
        """Builds the source and declares its output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity and whether the published value is constant.
        """
        self.config: WarmStartStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.value_output: ComponentOutput = self.add_output(
            self.component_name, self.Value, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The value."
        )

    def i_save_state(self) -> None:
        """Saves the state; the source has none."""

    def i_restore_state(self) -> None:
        """Restores the state; the source has none."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Publishes 100, plus the time step unless the config asks for a constant."""
        value = 100.0 if self.config.constant_source else 100.0 + timestep
        stsv.set_output_value(self.value_output, value)


class WarmStartReader(Component):
    """Reads the source's value on every pass and records it with its time step and pass number.

    It publishes twice the value it reads, so that its own output changes with its input and the
    convergence check sees the effect of a stale first-pass read.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input connected to the source's output.
    ValueIn: ClassVar[str] = "ValueIn"
    #: Twice the value read.
    Doubled: ClassVar[str] = "Doubled"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: WarmStartStubConfig) -> None:
        """Builds the reader, declares its input and output, and starts an empty record.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity of the reader.
        """
        self.config: WarmStartStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.value_input: ComponentInput = self.add_input(
            self.component_name, self.ValueIn, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.doubled_output: ComponentOutput = self.add_output(
            self.component_name, self.Doubled, lt.LoadTypes.ANY, lt.Units.ANY, output_description="2 x input."
        )
        #: One entry per pass: (time step, pass number counted from 1, value read).
        self.record: List[Tuple[int, int, float]] = []

    def i_save_state(self) -> None:
        """Saves the state; the reader has none that affects its output."""

    def i_restore_state(self) -> None:
        """Restores the state; the reader has none that affects its output."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Records the value read on this pass and publishes twice that value."""
        previous_pass = self.record[-1][1] if self.record and self.record[-1][0] == timestep else 0
        value = stsv.get_input_value(self.value_input)
        self.record.append((timestep, previous_pass + 1, value))
        stsv.set_output_value(self.doubled_output, 2.0 * value)

    def first_pass_values(self) -> Dict[int, float]:
        """Returns the value the reader read on the first pass of each time step."""
        return {timestep: value for timestep, pass_number, value in self.record if pass_number == 1}

    def passes_per_step(self) -> Dict[int, int]:
        """Returns the number of passes the simulator needed on each time step."""
        passes: Dict[int, int] = {}
        for timestep, pass_number, _ in self.record:
            passes[timestep] = max(passes.get(timestep, 0), pass_number)
        return passes


def run_reader_before_source(tmp_path: Path, constant_source: bool) -> WarmStartReader:
    """Runs one hourly day with the reader added before the source, and returns the reader.

    Args:
        tmp_path: Directory for the simulator's module directory and results.
        constant_source: Whether the source publishes a constant 100 instead of ``100 + timestep``.

    Returns:
        The reader, whose record holds every value it read.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.result_directory = str(tmp_path / "results")
    simulator = Simulator(
        module_directory=str(tmp_path), module_filename="warm_start", my_simulation_parameters=parameters
    )
    simulator.set_simulation_parameters(parameters)
    reader = WarmStartReader(parameters, WarmStartStubConfig(component_id=ComponentID(name="Reader")))
    source = WarmStartSource(
        parameters, WarmStartStubConfig(component_id=ComponentID(name="Source"), constant_source=constant_source)
    )
    reader.connect_input(WarmStartReader.ValueIn, source.component_name, WarmStartSource.Value)
    simulator.add_component(reader)
    simulator.add_component(source)
    simulator.run_all_timesteps()
    return reader


class TestTheFirstPassReadsThePreviousStep:
    """The first pass of a step reads the previous step's converged values, not zeros."""

    def test_a_reader_before_its_source_sees_the_previous_steps_value(self, tmp_path: Path) -> None:
        """Step 0 reads 0, as there is no previous step; every later step reads ``100 + (t - 1)``.

        Before the warm start, every step's first pass read 0 here.
        """
        reader = run_reader_before_source(tmp_path, constant_source=False)

        first_pass = reader.first_pass_values()

        assert len(first_pass) == 24
        assert first_pass[0] == 0.0
        assert {timestep: first_pass[timestep] for timestep in range(1, 24)} == {
            timestep: 100.0 + timestep - 1 for timestep in range(1, 24)
        }

    def test_a_step_whose_values_do_not_change_converges_after_one_pass(self, tmp_path: Path) -> None:
        """With a constant source, step 0 needs three passes and every later step one.

        Step 0 starts from zeros: pass 1 reads 0, pass 2 reads 100, and pass 3 confirms it. Every
        later step starts from those converged values, so its first pass already reproduces them.
        Before the warm start, every step needed three passes.
        """
        reader = run_reader_before_source(tmp_path, constant_source=True)

        passes = reader.passes_per_step()

        assert passes[0] == 3
        assert {timestep: passes[timestep] for timestep in range(1, 24)} == {timestep: 1 for timestep in range(1, 24)}
