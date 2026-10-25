"""No controller in HiSim advances its state in a forced pass, which the simulator's forced-pass rule needs.

The simulator does not restore a controller (kind L1 or L2) in a forced pass (owner decision of 2026-10-23, bead
hisim-component-physics-4g9.28), so a forced step commits the state the controller has after its last forced pass. That
is safe only if a forced pass does not advance the state: a second forced pass on the same values must leave it as the
first one did. This module checks that for every L1 and L2 controller class in ``hisim.components``, found as
subclasses of the two kind base classes, not from a list.

How a controller runs: in the first recorded energy system (``energy_systems/*.energy_system.yaml`` in name order,
without the grouped and composed twins) that names its class, for one spring day at 900 s, with the predefined
occupancy profile in place of the local LoadProfileGenerator. A class that no recorded system names is built from its
canonical preset alone, each input fed a slow wave between 0 and 1. A class with neither is skipped, and so is a system
that needs the LoadProfileGenerator's driving profiles, which the predefined profile does not carry.

The check, after every step: on the step's own converged values the controller runs one forced pass, then a second,
and the second must change none of its state. A forced pass may compute its state from the state saved at the start
of the step and the current inputs, as the air conditioner's and the solar thermal system's controllers do; repeated,
it computes the same state again, so not restoring it is safe. What breaks the rule is a state that advances with each
forced pass, a counter, a ramp or a ``+=``: a forced step can run some 90 forced passes at 60 s steps and more at longer
ones (``Simulator.maximum_passes_for_step_length``), and each would advance it once more.
"""

import copy
import datetime
import enum
import importlib
import math
import pkgutil
import typing
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Tuple

import numpy as np
import pytest

import hisim.components
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.calculation_scope import CalculationScope
from hisim.components.lpg_car_information import CarProfileNotPublishedError
from hisim.config import ComponentID, ConfigBase
from hisim.config.presets import canonical_preset
from hisim.energy_system.executor import build_energy_system
from hisim.sim_repository import SimRepository
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class ControllerState:
    """A controller's state, as a deep copy of its instance attributes, and the comparison of two such copies.

    The state is every instance attribute except the input and output channels (they describe the wiring; a pass writes
    its values to the step's value vector, not to them), the configuration and the simulation parameters (fixed for the
    run) and the simulation repository (shared by all components, not the controller's own). Comparing all the rest
    catches a change wherever a controller keeps it, without knowing its fields.
    """

    #: Attribute values that are not the controller's state.
    NOT_STATE: ClassVar[Tuple[type, ...]] = (
        cp.ComponentInput,
        cp.ComponentOutput,
        ConfigBase,
        SimulationParameters,
        SimRepository,
    )

    @classmethod
    def of(cls, controller: cp.Component) -> Dict[str, Any]:
        """Return a deep copy of the controller's state, by attribute name.

        Args:
            controller: The controller.

        Returns:
            Every instance attribute that is neither of :attr:`NOT_STATE` nor a list of them.
        """
        return {
            name: copy.deepcopy(value)
            for name, value in vars(controller).items()
            if not isinstance(value, cls.NOT_STATE)
            and not (isinstance(value, list) and value and all(isinstance(item, cls.NOT_STATE) for item in value))
        }

    @classmethod
    def equal(cls, first: Any, second: Any) -> bool:
        """Return whether two state values are equal, descending into containers and objects.

        Numpy arrays compare element by element, dicts by key, lists and tuples by position, dataclasses and other
        objects of one type by their attributes, and everything else, enum members included, with ``==``. NaN equals
        NaN, since a NaN that stays is no change.

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
            return cls.equal(vars(first), vars(second))
        if isinstance(first, float) and isinstance(second, float) and math.isnan(first) and math.isnan(second):
            return True
        return bool(first == second)

    @classmethod
    def changed_by_a_second_forced_pass(
        cls, controller: cp.Component, timestep: int, values: cp.SingleTimeStepValues
    ) -> List[str]:
        """Run two forced passes of the controller on one copy of the values and return what the second one changed.

        Both passes read the same copy, so the second also sees the outputs the first wrote, as a real second forced
        pass does.

        Args:
            controller: The controller, in the state its last pass left.
            timestep: The step of that pass.
            values: The values the forced passes read.

        Returns:
            The names of the state attributes the second forced pass changed, sorted; empty if it did not advance any.
        """
        forced_values = values.clone()
        controller.i_simulate(timestep, forced_values, force_convergence=True)
        before = cls.of(controller)
        controller.i_simulate(timestep, forced_values, force_convergence=True)
        after = cls.of(controller)
        return sorted(name for name in before.keys() | after.keys() if not cls.equal(before.get(name), after.get(name)))


class ControllerRuns:
    """Runs each controller class for a day as the module docstring describes, and lists its forced state changes.

    A change is a step and the names of the attributes the second forced pass changed in it.
    """

    #: The repository's recorded energy systems.
    ENERGY_SYSTEMS: ClassVar[Path] = Path(__file__).resolve().parents[1] / "energy_systems"
    #: The day. In mid-April heating, hot water, solar heat, district heat and the air conditioner all switch.
    START: ClassVar[datetime.datetime] = datetime.datetime(2021, 4, 15)
    #: The step length, in s.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    def __init__(self, work: Path) -> None:
        """Start with no system run.

        Args:
            work: The directory the runs write to.
        """
        self.work = work
        self.changes_in_system: Dict[Path, Dict[type, List[Tuple[int, List[str]]]]] = {}

    @staticmethod
    def controller_classes() -> List[type]:
        """Import every module of ``hisim.components`` and return each L1 and L2 controller class, sorted by full name."""
        for module in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
            importlib.import_module(module.name)
        found: Set[type] = set()
        unvisited: List[type] = [cp.L1ControllerComponent, cp.L2ControllerComponent]
        while unvisited:
            subclasses: List[type] = unvisited.pop().__subclasses__()
            for subclass in subclasses:
                unvisited.append(subclass)
                if subclass.__module__.startswith("hisim.components."):
                    found.add(subclass)
        return sorted(found, key=lambda found_class: f"{found_class.__module__}.{found_class.__name__}")

    @classmethod
    def parameters(cls) -> SimulationParameters:
        """Return the simulation parameters of the day."""
        return SimulationParameters(cls.START, cls.START + datetime.timedelta(days=1), cls.SECONDS_PER_TIMESTEP)

    def changes(self, controller_class: type) -> List[Tuple[int, List[str]]]:
        """Run the controller class and return the state changes of its second forced passes.

        Args:
            controller_class: The controller class.

        Returns:
            Per step with a change, the step and the attributes the second forced pass changed.
        """
        full_name = f"{controller_class.__module__}.{controller_class.__name__}"
        for path in sorted(self.ENERGY_SYSTEMS.glob("*.energy_system.yaml")):
            if ".grouped." not in path.name and ".composed." not in path.name and full_name in path.read_text("utf-8"):
                if path not in self.changes_in_system:
                    self.changes_in_system[path] = self.run_system(path)
                return self.changes_in_system[path][controller_class]
        return self.run_preset(controller_class)

    def run_system(self, path: Path) -> Dict[type, List[Tuple[int, List[str]]]]:
        """Run a recorded system for the day and return the changes of each of its controllers' second forced passes.

        Args:
            path: The recorded energy-system file.

        Returns:
            The changes of each controller class in the system.
        """
        energy_system = self.work / path.name
        energy_system.write_text(path.read_text("utf-8").replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), "utf-8")
        parameters = self.parameters()
        parameters.result_directory = str(self.work / f"{path.stem}_results")
        with CalculationScope.open(label=str(energy_system), run_directory=parameters.result_directory):
            simulator = build_energy_system(energy_system, parameters).simulator
            try:
                simulator.prepare_calculation()
            except CarProfileNotPublishedError as error:
                pytest.skip(f"{path.name} needs the LoadProfileGenerator's driving profiles: {error}")
            simulator.connect_all_components()
            controllers = [
                wrapped.my_component
                for wrapped in simulator.wrapped_components
                if isinstance(wrapped.my_component, (cp.L1ControllerComponent, cp.L2ControllerComponent))
            ]
            changes: Dict[type, List[Tuple[int, List[str]]]] = {type(controller): [] for controller in controllers}
            values = cp.SingleTimeStepValues(len(simulator.all_outputs))
            for timestep in range(parameters.timesteps):
                values = simulator.process_one_timestep(timestep, values)[0]
                for controller in controllers:
                    changed = ControllerState.changed_by_a_second_forced_pass(controller, timestep, values)
                    if changed:
                        changes[type(controller)].append((timestep, changed))
        return changes

    def run_preset(self, controller_class: type) -> List[Tuple[int, List[str]]]:
        """Run a controller built from its canonical preset alone, each input a slow wave between 0 and 1.

        Input ``j`` reads ``0.5 + 0.5 sin(t / 7 + j)`` in step ``t``: every value lies in a range each port quantity
        accepts (ratios, shares, on/off signals, small temperatures and powers), and the values move from step to step.
        The controller is not prepared (``i_prepare_simulation``): without a system there is nothing to prepare from,
        and the example controller does not implement it.

        Args:
            controller_class: The controller class.

        Returns:
            Per step with a change, the step and the attributes the second forced pass changed.
        """
        preset = canonical_preset(typing.get_type_hints(controller_class.__init__)["config"])
        if preset is None:
            pytest.skip(f"no recorded energy system names {controller_class.__name__}, and its config has no preset")
        parameters = self.parameters()
        controller = controller_class(my_simulation_parameters=parameters, config=preset.build(controller_class.__name__))
        sources = []
        for component_input in controller.inputs:
            component_input.source_output = cp.ComponentOutput(
                "Source", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Source")
            )
            sources.append(component_input.source_output)
        fft.add_global_index_of_components([*sources, controller])
        number_of_values = fft.get_number_of_outputs([*sources, controller])

        def values_of(timestep: int) -> cp.SingleTimeStepValues:
            values = cp.SingleTimeStepValues(number_of_values)
            for index, source in enumerate(sources):
                values.values[source.global_index] = 0.5 + 0.5 * math.sin(timestep / 7 + index)
            return values

        changes: List[Tuple[int, List[str]]] = []
        for timestep in range(parameters.timesteps):
            controller.i_save_state()
            controller.i_restore_state()
            controller.i_simulate(timestep, values_of(timestep), force_convergence=False)
            changed = ControllerState.changed_by_a_second_forced_pass(controller, timestep, values_of(timestep))
            if changed:
                changes.append((timestep, changed))
        return changes


@pytest.fixture(name="controller_runs", scope="module")
def fixture_controller_runs(tmp_path_factory: pytest.TempPathFactory) -> ControllerRuns:
    """The runs of this module, shared so that a recorded system with several controllers runs once."""
    return ControllerRuns(tmp_path_factory.mktemp("forced_controllers"))


@pytest.mark.parametrize("controller_class", ControllerRuns.controller_classes(), ids=lambda controller_class: controller_class.__name__)
def test_a_second_forced_pass_changes_no_state(controller_runs: ControllerRuns, controller_class: type) -> None:
    """Catches a controller whose state advances with every forced pass.

    The simulator does not restore a controller in a forced pass, so such a state would be committed advanced once per
    forced pass.
    """
    changes = controller_runs.changes(controller_class)

    assert not changes, f"{controller_class.__name__} advanced its state in a second forced pass, first steps: {changes[:3]}"
