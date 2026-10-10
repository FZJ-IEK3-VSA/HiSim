"""Every component declares its kind, and the simulator evaluates the components of a pass kind by kind.

The kinds (:class:`hisim.component.ComponentKind`) are data sources, L2 controllers, L1 controllers, physics and
meters. The simulator evaluates a pass in that order, keeping the order the components were added in within one kind;
the outputs, their indices and the result columns keep the order the components were added in.
"""

import dataclasses
import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import ClassVar, List, Tuple, Type

import pytest
from dataclasses_json import dataclass_json

import hisim.components
from hisim import loadtypes as lt
from hisim.component import Component, ComponentKind, ComponentKindNotDeclaredError, ComponentOutput, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


def concrete_component_classes() -> List[Type[Component]]:
    """Return every concrete :class:`Component` subclass defined in a module of ``hisim.components``.

    A class counts where it is defined, so a class imported into another module is listed once.
    """
    classes = []
    for module_info in pkgutil.walk_packages(hisim.components.__path__, "hisim.components."):
        module = importlib.import_module(module_info.name)
        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, Component) and member.__module__ == module.__name__ and not inspect.isabstract(member):
                classes.append(member)
    return classes


def test_the_component_library_is_found() -> None:
    """Catches the enumeration below finding nothing, which would let every class pass without a kind."""
    names = {component_class.__name__ for component_class in concrete_component_classes()}
    assert {"Weather", "L2GenericEnergyManagementSystem", "GenericBoilerController", "SimpleDHWStorage", "ElectricityMeter"} <= names


@pytest.mark.parametrize("component_class", concrete_component_classes(), ids=lambda component_class: component_class.__name__)
def test_every_component_class_declares_its_kind(component_class: Type[Component]) -> None:
    """Catches a component class without a kind, which the simulator would refuse when a system adds it."""
    assert isinstance(component_class.get_kind(), ComponentKind)


@pytest.mark.parametrize(
    ("kinds", "expected_positions"),
    [
        ((), []),
        ((ComponentKind.PHYSICS,), [0]),
        ((ComponentKind.PHYSICS, ComponentKind.L1_CONTROLLER, ComponentKind.DATA_SOURCE, ComponentKind.PHYSICS), [2, 1, 0, 3]),
        ((ComponentKind.METER, ComponentKind.PHYSICS, ComponentKind.L1_CONTROLLER, ComponentKind.L2_CONTROLLER, ComponentKind.DATA_SOURCE), [4, 3, 2, 1, 0]),
        ((ComponentKind.L1_CONTROLLER, ComponentKind.L1_CONTROLLER, ComponentKind.L2_CONTROLLER), [2, 0, 1]),
        ((ComponentKind.DATA_SOURCE, ComponentKind.L2_CONTROLLER, ComponentKind.L1_CONTROLLER, ComponentKind.PHYSICS, ComponentKind.METER), [0, 1, 2, 3, 4]),
    ],
)
def test_the_evaluation_order_is_by_kind_and_then_by_the_order_added(kinds: Tuple[ComponentKind, ...], expected_positions: List[int]) -> None:
    """Catches a kind evaluated out of its place, or two components of one kind swapped against the order they were added in."""
    assert Simulator.evaluation_positions(kinds) == expected_positions


@dataclass_json
@dataclasses.dataclass
class KindProbeConfig(ConfigBase):
    """The configuration of the probes below: their identity only."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Name no component class; the probes are test doubles."""
        return "tests.test_component_kinds.KindProbe"


class KindProbe(Component):
    """A component that publishes one value and records the pass order it was evaluated in."""

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The output every probe publishes, a counter.
    Value: ClassVar[str] = "Value"

    def __init__(self, my_simulation_parameters: SimulationParameters, name: str, evaluated: List[str]) -> None:
        """Build the probe with its one output; ``evaluated`` collects the names of the probes in the order they run."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=KindProbeConfig(component_id=ComponentID(name=name)),
            my_display_config=DisplayConfig(),
        )
        self.evaluated = evaluated
        self.value_output: ComponentOutput = self.add_output(
            self.component_name, self.Value, lt.LoadTypes.ANY, lt.Units.ANY, output_description="A constant."
        )

    def i_save_state(self) -> None:
        """Save nothing; the probe has no state."""

    def i_restore_state(self) -> None:
        """Restore nothing; the probe has no state."""

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Record that the probe ran and publish a constant."""
        self.evaluated.append(self.component_name)
        stsv.set_output_value(self.value_output, 1.0)


class DataSourceProbe(KindProbe):
    """A probe of the data-source kind."""

    KIND = ComponentKind.DATA_SOURCE


class ControllerProbe(KindProbe):
    """A probe of the L1-controller kind."""

    KIND = ComponentKind.L1_CONTROLLER


class DeviceProbe(KindProbe):
    """A probe of the physics kind."""

    KIND = ComponentKind.PHYSICS


def probe_simulator(tmp_path: Path) -> Simulator:
    """Return a simulator of one 900 s day that writes only below ``tmp_path``."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    parameters.result_directory = str(tmp_path / "results")
    simulator: Simulator = Simulator(module_directory=str(tmp_path), module_filename="kinds", my_simulation_parameters=parameters)
    simulator.set_simulation_parameters(parameters)
    return simulator


def test_a_pass_runs_kind_by_kind_while_the_outputs_keep_the_order_added(tmp_path: Path) -> None:
    """Catches a pass that runs the components in the order added, or outputs reordered along with the evaluation.

    The device is added before its controller and the data source last; the pass runs the data source, the
    controller and then the device, while the output indices follow the order the components were added in.
    """
    simulator = probe_simulator(tmp_path)
    parameters = simulator.get_simulation_parameters()
    evaluated: List[str] = []
    for probe in (
        DeviceProbe(parameters, "Device", evaluated),
        ControllerProbe(parameters, "Controller", evaluated),
        DataSourceProbe(parameters, "Weather", evaluated),
    ):
        simulator.add_component(probe)
    simulator.prepare_calculation()
    simulator.connect_all_components()
    stsv = SingleTimeStepValues(len(simulator.all_outputs))
    simulator.process_one_timestep(0, stsv)
    assert evaluated[:3] == ["Weather", "Controller", "Device"]
    assert [output.component_name for output in simulator.all_outputs] == ["Device", "Controller", "Weather"]
    assert [output.global_index for output in simulator.all_outputs] == [0, 1, 2]


def test_a_component_without_a_kind_is_refused_when_it_is_added(tmp_path: Path) -> None:
    """Catches a class without a kind reaching the time loop, where the simulator could not place it in a pass."""
    simulator = probe_simulator(tmp_path)
    with pytest.raises(ComponentKindNotDeclaredError, match="declares no KIND"):
        simulator.add_component(KindProbe(simulator.get_simulation_parameters(), "Probe", []))
