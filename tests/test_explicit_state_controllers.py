"""Every controller on :class:`hisim.explicit_state_controller.ExplicitStateController` keeps its whole memory in its state.

The base class owns the lifecycle: an unforced pass decides a new state from the state at the start of the step, a
forced pass republishes the state of the last unforced pass, and that state is what the step commits. This holds only
if the controller keeps no memory outside its state and does not replace the lifecycle methods. This module checks
both for every subclass in ``hisim.components``, found as subclasses of the base, not from a list:

* **No hidden state.** Each controller runs for one winter day at 900 s in a recorded energy system that names its
  class, with the predefined occupancy profile in place of the local LoadProfileGenerator. After every step, no
  instance attribute other than the base's two state references may differ from its value after
  ``i_prepare_simulation``. Channels, configuration, simulation parameters and the simulation repository are not
  memory and are left out; everything else is compared through deep copies.
* **The committed state belongs to the published outputs.** In the same run, after every step, :meth:`publish` (or
  :meth:`publish_forced` on a forced step) of the committed state writes exactly the outputs the step converged to.
* **The lifecycle is the base's.** No subclass defines ``i_simulate``, ``i_save_state`` or ``i_restore_state``.

A loop that never settles without forcing (a flip controller and a plant that echoes its command) checks that a forced
step commits the state of the last unforced pass, through the real simulator.
"""

import copy
import datetime
import enum
import importlib
import math
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, NamedTuple, Set, Tuple

import numpy as np
import pytest
from dataclasses_json import dataclass_json

import hisim.components
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.calculation_scope import CalculationScope
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.energy_system.executor import build_energy_system
from hisim.explicit_state_controller import ExplicitStateController
from hisim.sim_repository import SimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


class Memory:
    """A controller's memory outside its state: deep copies of its instance attributes, and their comparison.

    The memory is every instance attribute except the base's two state references, the input and output channels
    (they describe the wiring; a pass writes its values to the step's value vector), the configuration and the
    simulation parameters (fixed for the run) and the simulation repository (shared by all components). Channels held
    inside other attributes, such as a part-load helper, are kept by reference, so they compare as the same object.
    """

    #: Attribute values that are not the controller's memory.
    NOT_MEMORY: ClassVar[Tuple[type, ...]] = (
        cp.ComponentInput,
        cp.ComponentOutput,
        ConfigBase,
        SimulationParameters,
        SimRepository,
    )
    #: The base's state references, the only attributes that may change.
    STATE_REFERENCES: ClassVar[Tuple[str, ...]] = ("state_at_step_start", "state_of_last_free_pass")

    @classmethod
    def of(cls, controller: cp.Component) -> Dict[str, Any]:
        """Return a deep copy of the controller's memory outside its state, by attribute name.

        Args:
            controller: The controller.

        Returns:
            Every instance attribute that is neither a state reference, nor of :attr:`NOT_MEMORY`, nor a list of them.
        """
        attributes = vars(controller)
        shared: Dict[int, Any] = {
            id(value): value for value in [*controller.inputs, *controller.outputs, *attributes.values()]
            if isinstance(value, cls.NOT_MEMORY)
        }
        return {
            name: copy.deepcopy(value, dict(shared))
            for name, value in attributes.items()
            if name not in cls.STATE_REFERENCES
            and not isinstance(value, cls.NOT_MEMORY)
            and not (isinstance(value, list) and value and all(isinstance(item, cls.NOT_MEMORY) for item in value))
        }

    @classmethod
    def equal(cls, first: Any, second: Any) -> bool:
        """Return whether two values are equal, descending into containers and objects; NaN equals NaN.

        Args:
            first: One value.
            second: The other value.

        Returns:
            True if the two are equal.
        """
        if isinstance(first, np.ndarray) or isinstance(second, np.ndarray):
            return bool(np.array_equal(first, second, equal_nan=True))
        if isinstance(first, dict) and isinstance(second, dict):
            return first.keys() == second.keys() and all(cls.equal(first[key], second[key]) for key in first)
        if isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)):
            return len(first) == len(second) and all(cls.equal(one, other) for one, other in zip(first, second))
        if type(first) is type(second) and hasattr(first, "__dict__") and not isinstance(first, enum.Enum):
            return first is second or cls.equal(vars(first), vars(second))
        if isinstance(first, float) and isinstance(second, float) and math.isnan(first) and math.isnan(second):
            return True
        return bool(first == second)


class ControllerRun:
    """What one recorded system's day showed for one explicit-state controller class.

    ``memory_changes`` lists (step, attribute names) where the memory outside the state differed from its value
    after preparation; ``foreign_outputs`` lists (step, output names) where publishing the committed state wrote other
    values than the step converged to; ``forced_steps`` counts the forced steps.
    """

    def __init__(self) -> None:
        """Start with no step seen."""
        self.memory_changes: List[Tuple[int, List[str]]] = []
        self.foreign_outputs: List[Tuple[int, List[str]]] = []
        self.forced_steps: int = 0


class ExplicitStateRuns:
    """Finds the explicit-state controller classes and runs each in a recorded system, as the module docstring says."""

    #: The repository's recorded energy systems.
    ENERGY_SYSTEMS: ClassVar[Path] = Path(__file__).resolve().parents[1] / "energy_systems"
    #: The system tried first: the reference system of the directory, the smallest that names the boiler controller.
    FIRST_SYSTEM: ClassVar[str] = "gas_boiler_household.energy_system.yaml"
    #: The day: a winter day, on which the heating runs in every hour.
    START: ClassVar[datetime.datetime] = datetime.datetime(2021, 1, 15)
    #: The step length, in s.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    def __init__(self, work: Path) -> None:
        """Start with no system run.

        Args:
            work: The directory the runs write to.
        """
        self.work = work
        self.runs_of_system: Dict[Path, Dict[type, ControllerRun]] = {}

    @staticmethod
    def controller_classes() -> List[type]:
        """Import every module of ``hisim.components`` and return each explicit-state class, sorted by full name."""
        for module in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
            importlib.import_module(module.name)
        found: Set[type] = set()
        unvisited: List[type] = [ExplicitStateController]
        while unvisited:
            subclasses: List[type] = unvisited.pop().__subclasses__()
            for subclass in subclasses:
                unvisited.append(subclass)
                if subclass.__module__.startswith("hisim.components."):
                    found.add(subclass)
        return sorted(found, key=lambda found_class: f"{found_class.__module__}.{found_class.__name__}")

    def systems(self) -> List[Path]:
        """Return the recorded systems in the order they are tried: :attr:`FIRST_SYSTEM`, then the rest by name."""
        paths = sorted(
            path
            for path in self.ENERGY_SYSTEMS.glob("*.energy_system.yaml")
            if ".grouped." not in path.name and ".composed." not in path.name
        )
        return sorted(paths, key=lambda path: path.name != self.FIRST_SYSTEM)

    def run(self, controller_class: type) -> ControllerRun:
        """Return the run of the controller class, in the first system of :meth:`systems` that names it.

        Args:
            controller_class: The controller class.

        Returns:
            What the day showed for the class.
        """
        full_name = f"{controller_class.__module__}.{controller_class.__name__}"
        naming = [path for path in self.systems() if full_name in path.read_text("utf-8")]
        if not naming:
            pytest.fail(f"no recorded energy system names {full_name}; an explicit-state controller needs one to be checked")
        if naming[0] not in self.runs_of_system:
            self.runs_of_system[naming[0]] = self.run_system(naming[0])
        return self.runs_of_system[naming[0]][controller_class]

    def run_system(self, path: Path) -> Dict[type, ControllerRun]:
        """Run a recorded system for the day and check every explicit-state controller in it after every step.

        Args:
            path: The recorded energy-system file.

        Returns:
            The run of each explicit-state controller class in the system.
        """
        energy_system = self.work / path.name
        energy_system.write_text(path.read_text("utf-8").replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), "utf-8")
        parameters = SimulationParameters(
            self.START, self.START + datetime.timedelta(days=1), self.SECONDS_PER_TIMESTEP
        )
        parameters.result_directory = str(self.work / f"{path.stem}_results")
        with CalculationScope.open(label=str(energy_system), run_directory=parameters.result_directory):
            simulator = build_energy_system(energy_system, parameters).simulator
            simulator.prepare_calculation()
            simulator.connect_all_components()
            controllers = [
                wrapped.my_component
                for wrapped in simulator.wrapped_components
                if isinstance(wrapped.my_component, ExplicitStateController)
            ]
            prepared = {id(controller): Memory.of(controller) for controller in controllers}
            runs: Dict[type, ControllerRun] = {type(controller): ControllerRun() for controller in controllers}
            values = cp.SingleTimeStepValues(len(simulator.all_outputs))
            for timestep in range(parameters.timesteps):
                values, _, forced = simulator.process_one_timestep(timestep, values)
                for controller in controllers:
                    run = runs[type(controller)]
                    run.forced_steps += int(forced)
                    now = Memory.of(controller)
                    before = prepared[id(controller)]
                    changed = sorted(name for name in before.keys() | now.keys() if not Memory.equal(before.get(name), now.get(name)))
                    if changed:
                        run.memory_changes.append((timestep, changed))
                    foreign = self.outputs_not_of_the_committed_state(controller, values, forced)
                    if foreign:
                        run.foreign_outputs.append((timestep, foreign))
        return runs

    @staticmethod
    def outputs_not_of_the_committed_state(
        controller: ExplicitStateController, values: cp.SingleTimeStepValues, forced: bool
    ) -> List[str]:
        """Return the outputs whose converged values differ from what publishing the committed state writes.

        The controller's outputs are set to NaN before the state is published, so an output that :meth:`publish` does
        not write, because :meth:`decide` wrote it, shows up as NaN.

        Args:
            controller: The controller, after the step.
            values: The values the step converged to.
            forced: Whether the step was forced, so the committed state is published as a forced pass publishes it.

        Returns:
            The names of the differing outputs; empty if the committed state belongs to the published outputs.
        """
        republished = values.clone()
        for output in controller.outputs:
            republished.values[output.global_index] = math.nan
        if forced:
            controller.publish_forced(stsv=republished, state=controller.state_of_last_free_pass)
        else:
            controller.publish(stsv=republished, state=controller.state_of_last_free_pass)
        return [
            output.field_name
            for output in controller.outputs
            if not Memory.equal(
                float(values.values[output.global_index]), float(republished.values[output.global_index])
            )
        ]


@pytest.fixture(name="explicit_state_runs", scope="module")
def fixture_explicit_state_runs(tmp_path_factory: pytest.TempPathFactory) -> ExplicitStateRuns:
    """The runs of this module, shared so that a system with several such controllers runs once."""
    return ExplicitStateRuns(tmp_path_factory.mktemp("explicit_state_controllers"))


CONTROLLER_CLASSES = ExplicitStateRuns.controller_classes()


def test_at_least_one_controller_uses_the_explicit_state_base() -> None:
    """Catches a discovery that finds nothing, which would let every test below pass without checking anything."""
    assert CONTROLLER_CLASSES


@pytest.mark.parametrize("controller_class", CONTROLLER_CLASSES, ids=lambda controller_class: controller_class.__name__)
def test_the_controller_keeps_no_memory_outside_its_state(
    explicit_state_runs: ExplicitStateRuns, controller_class: type
) -> None:
    """Catches an attribute a pass changes outside the state, which the base neither saves nor restores."""
    run = explicit_state_runs.run(controller_class)

    assert not run.memory_changes, f"{controller_class.__name__} changed attributes outside its state: {run.memory_changes[:3]}"


@pytest.mark.parametrize("controller_class", CONTROLLER_CLASSES, ids=lambda controller_class: controller_class.__name__)
def test_the_committed_state_publishes_the_outputs_of_the_step(
    explicit_state_runs: ExplicitStateRuns, controller_class: type
) -> None:
    """Catches an output written in decide, or computed in publish from something other than the state."""
    run = explicit_state_runs.run(controller_class)

    assert not run.foreign_outputs, f"{controller_class.__name__}: outputs not of the committed state: {run.foreign_outputs[:3]}"


@pytest.mark.parametrize("controller_class", CONTROLLER_CLASSES, ids=lambda controller_class: controller_class.__name__)
def test_the_controller_leaves_the_lifecycle_to_the_base(controller_class: type) -> None:
    """Catches a subclass that brings back its own save, restore or simulate, past the guard of the base class."""
    for klass in controller_class.__mro__[: controller_class.__mro__.index(ExplicitStateController)]:
        assert not set(ExplicitStateController.OWNED_LIFECYCLE_METHODS) & set(vars(klass)), klass.__name__


def test_a_subclass_that_defines_a_lifecycle_method_is_refused() -> None:
    """Catches a guard that lets a subclass replace the save, restore or simulate of the base class."""
    with pytest.raises(TypeError, match="i_restore_state"):

        class Restoring(ExplicitStateController, cp.L1ControllerComponent):  # pylint: disable=unused-variable
            """A controller that restores by hand."""

            def i_restore_state(self) -> None:  # type: ignore[misc]  # pylint: disable=overridden-final-method
                """Restore by hand."""


@dataclass_json
@dataclass
class FlipLoopConfig(ConfigBase):
    """Configuration of the flip loop's two test doubles: only their identity."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the flip controller; the configuration belongs to a test double."""
        return FlipController.get_full_classname()

    component_id: ComponentID


class FlipState(NamedTuple):
    """The flip controller's memory: its command, and how many unforced passes decided in the run."""

    command: int
    free_passes: int


class FlipController(ExplicitStateController[FlipState], cp.L1ControllerComponent):
    """Commands 1 when the plant's signal is 0 and 0 otherwise, so the loop with :class:`EchoPlant` never settles.

    The command alternates in every unforced pass, so the simulator forces the step; the controller then republishes
    the command of its last unforced pass, the plant echoes it and the step converges. ``free_passes`` counts the
    unforced passes of the run, so the committed state also shows that it came from the last of them.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True
    #: The input connected to the plant's signal.
    Signal: ClassVar[str] = "Signal"
    #: The command the controller publishes.
    Command: ClassVar[str] = "Command"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FlipLoopConfig) -> None:
        """Build the controller, which starts with command 1, and declare its input and output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: The controller's identity.
        """
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.signal_input: cp.ComponentInput = self.add_input(
            self.component_name, self.Signal, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.command_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.Command, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The command."
        )

    def initial_state(self) -> FlipState:
        """Return command 1 and no pass decided."""
        return FlipState(command=1, free_passes=0)

    def decide(self, *, timestep: int, stsv: cp.SingleTimeStepValues, state: FlipState) -> FlipState:
        """Command the opposite of the plant's signal."""
        return FlipState(command=1 if stsv.get_input_value(self.signal_input) == 0 else 0, free_passes=state.free_passes + 1)

    def publish(self, *, stsv: cp.SingleTimeStepValues, state: FlipState) -> None:
        """Publish the command."""
        stsv.set_output_value(self.command_output, state.command)

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []


class EchoPlant(cp.PhysicsComponent):
    """Publishes the command it receives as its signal; it has no state."""

    MODELS_NO_DEVICE: ClassVar[bool] = True
    #: The input connected to the controller's command.
    Command: ClassVar[str] = "Command"
    #: The signal the plant publishes.
    Signal: ClassVar[str] = "Signal"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FlipLoopConfig) -> None:
        """Build the plant and declare its input and output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: The plant's identity.
        """
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.command_input: cp.ComponentInput = self.add_input(
            self.component_name, self.Command, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.signal_output: cp.ComponentOutput = self.add_output(
            self.component_name, self.Signal, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The signal."
        )

    def i_save_state(self) -> None:
        """Save nothing."""

    def i_restore_state(self) -> None:
        """Restore nothing."""

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Echo the command as the signal."""
        stsv.set_output_value(self.signal_output, stsv.get_input_value(self.command_input))


def flip_loop(tmp_path: Path) -> Tuple[Simulator, FlipController]:
    """Return a prepared and wired simulator of one day at 900 s with the flip controller and the echo plant.

    Args:
        tmp_path: Directory for the simulator's module directory and results.

    Returns:
        The simulator and its controller.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    parameters.result_directory = str(tmp_path / "results")
    simulator = Simulator(module_directory=str(tmp_path), module_filename="flip_loop", my_simulation_parameters=parameters)
    simulator.set_simulation_parameters(parameters)
    controller = FlipController(parameters, FlipLoopConfig(component_id=ComponentID(name="Controller")))
    plant = EchoPlant(parameters, FlipLoopConfig(component_id=ComponentID(name="Plant")))
    controller.connect_input(FlipController.Signal, plant.component_name, EchoPlant.Signal)
    plant.connect_input(EchoPlant.Command, controller.component_name, FlipController.Command)
    simulator.add_component(controller)
    simulator.add_component(plant)
    simulator.prepare_calculation()
    simulator.connect_all_components()
    return simulator, controller


def test_a_forced_step_commits_the_state_of_the_last_free_pass(tmp_path: Path) -> None:
    """Catches a forced step that commits the state of the start of the step, or of a forced pass.

    Every unforced pass of step 0 decides from the initial state (0 passes decided), so the last one has decided one
    pass; its command is the one the step converged to, and step 1 starts from it.
    """
    simulator, controller = flip_loop(tmp_path)
    values = cp.SingleTimeStepValues(len(simulator.all_outputs))

    values, _, forced = simulator.process_one_timestep(0, values)

    assert forced
    committed = controller.state_of_last_free_pass
    assert committed.free_passes == 1
    assert values.values[controller.command_output.global_index] == committed.command
    simulator.process_one_timestep(1, values)
    assert controller.state_at_step_start == committed
