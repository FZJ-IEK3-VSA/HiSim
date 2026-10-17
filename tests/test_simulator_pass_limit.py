"""The simulator's limit on passes per time step grows with the step length.

The simulator iterates each time step until no output changes any more and aborts a step that has
not converged after its pass limit. The limit is 100 passes per 60 s of step length, and never less
than 100: 100 at 60 s and below, 1500 at 900 s, 6000 at 3600 s. A loop whose error shrinks by a
fixed factor per pass needs more passes the longer the step, so a fixed limit of 100 aborted coarse
steps that would have converged.

The reproduction has two components coupled in a loop. ``SlowLeader`` publishes
``target + 0.97 * (follower value - target)`` and ``SlowFollower`` copies the leader's value, so the
gap to the target shrinks by a factor of 0.97 per pass. A jump of 1000 in the target then takes a
few hundred passes: more than 100, fewer than 1500.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import pytest
from dataclasses_json import dataclass_json

from hisim import log
from hisim import loadtypes as lt
from hisim.component import ComponentInput, ComponentOutput, PhysicsComponent, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


@dataclass_json
@dataclass
class SlowLoopStubConfig(ConfigBase):
    """Configuration of the two stub components of the slowly contracting loop.

    It carries the identity, the size of each jump of the leader's target and how many time steps
    the target stays on one level. The follower ignores the last two.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the leader stub; the config belongs to a test double."""
        return SlowLeader.get_full_classname()

    component_id: ComponentID
    jump_size: float = 1000.0
    timesteps_per_level: int = 32


class SlowLeader(PhysicsComponent):
    """Pulls the follower's value towards a target by 3 % per pass, and records its passes.

    The target is ``jump_size * (1 + timestep // timesteps_per_level)``: it jumps by ``jump_size``
    at step 0 (from the all-zero start) and at every multiple of ``timesteps_per_level``. Added
    before the follower, the leader reads the follower's value of the previous pass.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input connected to the follower's output.
    FollowerValue: ClassVar[str] = "FollowerValue"
    #: The value the leader publishes.
    Value: ClassVar[str] = "Value"
    #: The share of the gap to the target that is left after one pass.
    REMAINING_GAP_PER_PASS: ClassVar[float] = 0.97

    def __init__(self, my_simulation_parameters: SimulationParameters, config: SlowLoopStubConfig) -> None:
        """Builds the leader, declares its input and output, and starts an empty record.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity, jump size and how long the target stays on one level.
        """
        self.config: SlowLoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.follower_input: ComponentInput = self.add_input(
            self.component_name, self.FollowerValue, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.value_output: ComponentOutput = self.add_output(
            self.component_name, self.Value, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The value."
        )
        #: One entry per pass: (time step, pass number counted from 1).
        self.record: List[Tuple[int, int]] = []

    def i_save_state(self) -> None:
        """Saves the state; the leader has none that affects its output."""

    def i_restore_state(self) -> None:
        """Restores the state; the leader has none that affects its output."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Records the pass and publishes the follower's value moved 3 % of the way to the target."""
        previous_pass = self.record[-1][1] if self.record and self.record[-1][0] == timestep else 0
        self.record.append((timestep, previous_pass + 1))
        target = self.config.jump_size * (1 + timestep // self.config.timesteps_per_level)
        follower_value = stsv.get_input_value(self.follower_input)
        stsv.set_output_value(self.value_output, target + self.REMAINING_GAP_PER_PASS * (follower_value - target))

    def passes_per_step(self) -> Dict[int, int]:
        """Returns the number of passes the simulator needed on each time step."""
        passes: Dict[int, int] = {}
        for timestep, pass_number in self.record:
            passes[timestep] = max(passes.get(timestep, 0), pass_number)
        return passes


class SlowFollower(PhysicsComponent):
    """Publishes the value it reads from the leader, closing the loop."""

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input connected to the leader's output.
    LeaderValue: ClassVar[str] = "LeaderValue"
    #: The value the follower publishes.
    Value: ClassVar[str] = "Value"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: SlowLoopStubConfig) -> None:
        """Builds the follower and declares its input and output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity of the follower.
        """
        self.config: SlowLoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.leader_input: ComponentInput = self.add_input(
            self.component_name, self.LeaderValue, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.value_output: ComponentOutput = self.add_output(
            self.component_name, self.Value, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The value."
        )

    def i_save_state(self) -> None:
        """Saves the state; the follower has none."""

    def i_restore_state(self) -> None:
        """Restores the state; the follower has none."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Publishes the leader's value of this pass."""
        stsv.set_output_value(self.value_output, stsv.get_input_value(self.leader_input))


def slow_loop_simulator(tmp_path: Path, seconds_per_timestep: int) -> Tuple[Simulator, SlowLeader]:
    """Builds a simulator of one day with the slowly contracting loop, and returns it with the leader.

    Args:
        tmp_path: Directory for the simulator's module directory and results.
        seconds_per_timestep: The step length of the run.

    Returns:
        The simulator, with both components added, and the leader, whose record holds every pass.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=seconds_per_timestep)
    parameters.result_directory = str(tmp_path / "results")
    simulator = Simulator(
        module_directory=str(tmp_path), module_filename="pass_limit", my_simulation_parameters=parameters
    )
    simulator.set_simulation_parameters(parameters)
    leader = SlowLeader(parameters, SlowLoopStubConfig(component_id=ComponentID(name="Leader")))
    follower = SlowFollower(parameters, SlowLoopStubConfig(component_id=ComponentID(name="Follower")))
    leader.connect_input(SlowLeader.FollowerValue, follower.component_name, SlowFollower.Value)
    follower.connect_input(SlowFollower.LeaderValue, leader.component_name, SlowLeader.Value)
    simulator.add_component(leader)
    simulator.add_component(follower)
    return simulator, leader


def run_first_step(simulator: Simulator) -> int:
    """Prepares the simulator, runs time step 0 from all zeros and returns the number of passes it took.

    Args:
        simulator: A simulator with its components added.

    Returns:
        The number of passes of step 0.
    """
    simulator.prepare_calculation()
    simulator.connect_all_components()
    _, passes, _ = simulator.process_one_timestep(0, SingleTimeStepValues(len(simulator.all_outputs)))
    return passes


class TestThePassLimitScalesWithTheStepLength:
    """The limit is 100 passes per 60 s of step length, rounded up, and at least 100."""

    @pytest.mark.parametrize(
        "seconds_per_timestep, expected_limit",
        [(1, 100), (60, 100), (61, 102), (900, 1500), (3600, 6000)],
    )
    def test_the_limit_for_a_step_length(self, seconds_per_timestep: int, expected_limit: int) -> None:
        """Catches a fixed limit, a limit that rounds down at 61 s or one that falls below 100 for short steps."""
        assert Simulator.maximum_passes_for_step_length(seconds_per_timestep) == expected_limit

    def test_the_simulator_sets_its_limit_from_its_step_length(self, tmp_path: Path) -> None:
        """Catches a simulator that keeps the limit of 100 after it is given 3600 s steps."""
        simulator, _ = slow_loop_simulator(tmp_path, seconds_per_timestep=3600)

        assert simulator.maximum_passes_per_timestep == 6000


class TestASlowlyContractingLoop:
    """A loop that needs a few hundred passes converges at 900 s and aborts at 60 s."""

    def test_it_converges_at_900_s_steps(self, tmp_path: Path) -> None:
        """Catches the fixed limit of 100: the step needs more than 100 passes, fewer than its limit of 1500."""
        simulator, leader = slow_loop_simulator(tmp_path, seconds_per_timestep=900)

        passes = run_first_step(simulator)

        assert 100 < passes < 1500
        assert leader.passes_per_step()[0] == passes

    def test_it_aborts_at_60_s_steps_naming_the_step_the_length_and_the_limit(self, tmp_path: Path) -> None:
        """Catches an abort message without the step length and the limit, or one that drops the changed values."""
        simulator, _ = slow_loop_simulator(tmp_path, seconds_per_timestep=60)

        with pytest.raises(ValueError) as raised:
            run_first_step(simulator)

        message = str(raised.value)
        assert message.startswith(
            "More than 100 passes in time step 0 (60 s steps; the limit is 100 passes per 60 s of step length)\n"
        )
        assert "Leader" in message and "Follower" in message


class TestTheRunLogsOneSummaryOfThePassCounts:
    """At the end of the time loop the run logs how many steps needed more than 100 passes, and the most passes."""

    def test_the_summary_counts_the_slow_steps_and_the_largest_pass_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Catches a run without the summary, or one that miscounts the steps above 100 passes or the largest count.

        One day at 900 s has 96 steps; the target jumps at steps 0, 32 and 64, so three steps need more than
        100 passes and every other step one pass.
        """
        warnings: List[str] = []
        informations: List[str] = []
        monkeypatch.setattr(log, "warning", lambda message, *_args, **_kwargs: warnings.append(message))
        monkeypatch.setattr(log, "information", lambda message, *_args, **_kwargs: informations.append(message))
        simulator, leader = slow_loop_simulator(tmp_path, seconds_per_timestep=900)

        simulator.run_all_timesteps()

        passes = leader.passes_per_step()
        slow_steps = sorted(timestep for timestep, count in passes.items() if count > 100)
        assert slow_steps == [0, 32, 64]
        summaries = [message for message in warnings if message.startswith("Pass counts:")]
        assert summaries == [
            f"Pass counts: 3 of 96 time steps needed more than 100 passes; the largest pass count was "
            f"{max(passes.values())} (the limit is 1500 passes at 900 s steps)."
        ]
        assert not [message for message in informations if message.startswith("Pass counts:")]

    def test_a_run_within_100_passes_logs_the_summary_as_information(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Catches a summary that warns although no step needed more than 100 passes."""
        warnings: List[str] = []
        informations: List[str] = []
        monkeypatch.setattr(log, "warning", lambda message, *_args, **_kwargs: warnings.append(message))
        monkeypatch.setattr(log, "information", lambda message, *_args, **_kwargs: informations.append(message))
        simulator, leader = slow_loop_simulator(tmp_path, seconds_per_timestep=3600)
        leader.config.jump_size = 0.0

        simulator.run_all_timesteps()

        assert not [message for message in warnings if message.startswith("Pass counts:")]
        assert [message for message in informations if message.startswith("Pass counts:")] == [
            f"Pass counts: 0 of 24 time steps needed more than 100 passes; the largest pass count was "
            f"{max(leader.passes_per_step().values())} (the limit is 6000 passes at 3600 s steps)."
        ]
