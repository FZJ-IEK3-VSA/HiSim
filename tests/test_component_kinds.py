"""Every component inherits one kind base class, and the simulator evaluates the components of a pass kind by kind.

The kinds are the five kind base classes of :mod:`hisim.component` (:class:`hisim.component.ComponentKinds`): data
sources, L2 controllers, L1 controllers, physics and meters. The simulator evaluates a pass in that order, keeping the
order the components were added in within one kind, except that an L2 controller reading another L2 controller follows
it and that within the physics every device an L1 controller commands runs before the other physics the controller
reads; the outputs, their indices and the result columns keep the order the components were added in.
"""

import dataclasses
import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import ClassVar, Dict, FrozenSet, List, Tuple, Type

import pytest
from dataclasses_json import dataclass_json

import hisim.components
from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentKindNotDeclaredError,
    ComponentKinds,
    ComponentOutput,
    DataSourceComponent,
    L1ControllerComponent,
    L2ControllerComponent,
    MeterComponent,
    PhysicsComponent,
    SingleTimeStepValues,
    StatelessComponent,
)
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.transformer_rectifier import Transformer
from hisim.components.weather import Weather
from hisim.dynamic_component import DynamicComponent
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


def concrete_component_classes() -> List[Type[Component]]:
    """Return every concrete :class:`Component` subclass defined in a module of ``hisim.components``.

    A class counts where it is defined, so a class imported into another module is listed once. For example, the list
    holds ``Weather`` and ``ElectricityMeter``. The test below needs every such class, because the simulator refuses a
    component whose class inherits no kind base class.

    Returns:
        The classes, in the order the modules and their members are found.
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
def test_every_component_class_inherits_one_kind(component_class: Type[Component]) -> None:
    """Catches a component class without a kind base class, which the simulator would refuse when a system adds it."""
    assert ComponentKinds.of(component_class) in ComponentKinds.BASES


@pytest.mark.parametrize(
    ("component_class", "expected_kind"),
    [
        (Weather, DataSourceComponent),
        (L2GenericEnergyManagementSystem, L2ControllerComponent),
        (ElectricityMeter, MeterComponent),
        (Transformer, PhysicsComponent),
        (PhysicsComponent, PhysicsComponent),
    ],
)
def test_the_kind_is_found_through_the_bases_a_class_combines_it_with(
    component_class: Type[Component], expected_kind: Type[Component]
) -> None:
    """Catches the lookup missing a kind that sits behind ``DynamicComponent`` or ``StatelessComponent`` in the MRO."""
    assert ComponentKinds.of(component_class) is expected_kind


@pytest.mark.parametrize("component_class", [Component, StatelessComponent, DynamicComponent])
def test_a_base_without_a_kind_is_refused_by_the_lookup(component_class: Type[Component]) -> None:
    """Catches the lookup inventing a kind for a base that is legitimately kind-less, and a refusal that is not a TypeError."""
    with pytest.raises(ComponentKindNotDeclaredError, match="inherits none"):
        ComponentKinds.of(component_class)
    assert issubclass(ComponentKindNotDeclaredError, TypeError)


def test_a_class_with_two_kinds_is_refused_by_the_lookup() -> None:
    """Catches a class that inherits two kind base classes and would be placed in a pass by whichever comes first."""

    class ControllerAndDevice(L1ControllerComponent, PhysicsComponent):
        """A class that claims two kinds at once."""

    with pytest.raises(ComponentKindNotDeclaredError, match="L1ControllerComponent, PhysicsComponent"):
        ComponentKinds.of(ControllerAndDevice)


def test_the_simulator_orders_exactly_the_kinds_the_lookup_knows() -> None:
    """Catches a kind base class the simulator cannot place, or a kind listed twice in the evaluation order."""
    assert len(Simulator.EVALUATION_ORDER_OF_KINDS) == len(ComponentKinds.BASES)
    assert set(Simulator.EVALUATION_ORDER_OF_KINDS) == set(ComponentKinds.BASES)


@pytest.mark.parametrize(
    ("kinds", "expected_positions"),
    [
        ((), ()),
        ((PhysicsComponent,), (0,)),
        ((PhysicsComponent, L1ControllerComponent, DataSourceComponent, PhysicsComponent), (2, 1, 0, 3)),
        ((MeterComponent, PhysicsComponent, L1ControllerComponent, L2ControllerComponent, DataSourceComponent), (4, 3, 2, 1, 0)),
        ((L1ControllerComponent, L1ControllerComponent, L2ControllerComponent), (2, 0, 1)),
        ((DataSourceComponent, L2ControllerComponent, L1ControllerComponent, PhysicsComponent, MeterComponent), (0, 1, 2, 3, 4)),
    ],
)
def test_the_evaluation_order_is_by_kind_and_then_by_the_order_added(
    kinds: Tuple[Type[Component], ...], expected_positions: Tuple[int, ...]
) -> None:
    """Catches a kind evaluated out of its place, or two components of one kind swapped against the order they were added in."""
    order = Simulator.evaluation_positions(kinds, [frozenset()] * len(kinds))
    assert order.positions == expected_positions
    assert not order.cycles


class Wiring:
    """Small systems for the physics order, as kinds and the positions each component reads from."""

    #: The kinds by letter: D data source, C L1 controller, P physics, M meter.
    KIND_OF_LETTER: ClassVar[Dict[str, Type[Component]]] = {
        "D": DataSourceComponent,
        "C": L1ControllerComponent,
        "P": PhysicsComponent,
        "M": MeterComponent,
    }

    #: The gas + solar arrangement as household_gas_solar_thermal adds it: the hot-water tank (0) before the boiler
    #: (1) and the collector (2), then the boiler's controller (3), the collector's controller (4) and the weather (5).
    #: Each controller commands its device and reads the tank and its device; the devices read their controller, the
    #: tank and the weather; the tank reads both devices.
    GAS_AND_SOLAR: ClassVar[Tuple[str, Tuple[FrozenSet[int], ...]]] = (
        "PPPCCD",
        (
            frozenset({1, 2}),
            frozenset({3, 0, 5}),
            frozenset({4, 0, 5}),
            frozenset({0, 1}),
            frozenset({0, 2}),
            frozenset(),
        ),
    )

    @classmethod
    def order(
        cls, letters: str, sources: Tuple[FrozenSet[int], ...]
    ) -> Tuple[Tuple[int, ...], Tuple[Tuple[int, ...], ...]]:
        """Return the evaluation positions and the cycles of a system given as kind letters and source sets."""
        result = Simulator.evaluation_positions([cls.KIND_OF_LETTER[letter] for letter in letters], sources)
        return result.positions, result.cycles


@pytest.mark.parametrize(
    ("letters", "sources", "expected_positions"),
    [
        # A controller that commands the device added after the store it reads: the device moves before the store.
        ("PCP", (frozenset({2}), frozenset({0, 2}), frozenset({1, 0})), (1, 2, 0)),
        # The store is added after the device already: nothing moves.
        ("PCP", (frozenset({1, 2}), frozenset({2}), frozenset({0})), (1, 0, 2)),
        # Physics no controller reads keep the order added, around a pair the rule orders.
        ("PPCP", (frozenset(), frozenset({3}), frozenset({1}), frozenset({2})), (2, 0, 3, 1)),
        # A controller that commands two devices and reads both: its own devices are not ordered against each other.
        ("PPC", (frozenset({2}), frozenset({2}), frozenset({0, 1})), (2, 0, 1)),
        # A store with a controller of its own, added as radiator (0), tank (1), boiler (2): the boiler goes before the
        # tank its controller (3) reads, and the tank, commanded by the second controller (4), before the radiator
        # that controller reads.
        ("PPPCC", (frozenset({1}), frozenset({4, 2}), frozenset({3}), frozenset({1}), frozenset({0})), (3, 4, 2, 1, 0)),
        # Meters and data sources are not physics; a controller reading a meter orders nothing.
        ("MCP", (frozenset({2}), frozenset({0}), frozenset({1})), (1, 2, 0)),
    ],
)
def test_every_device_of_an_l1_controller_runs_before_the_other_physics_the_controller_reads(
    letters: str, sources: Tuple[FrozenSet[int], ...], expected_positions: Tuple[int, ...]
) -> None:
    """Catches a store evaluated between a controller and its device, which pairs the device's report with an old answer.

    Also catches the rule moving physics no controller constrains, or ordering a controller's own devices.
    """
    assert Wiring.order(letters, sources) == (expected_positions, ())


def test_the_gas_and_solar_tank_runs_after_the_boiler_and_the_collector() -> None:
    """Catches the household_gas_solar_thermal order (controller, tank, device), whose part-load iteration spirals.

    The file adds the tank before the boiler and the collector; the pass must run the weather, both controllers, the
    boiler, the collector and then the tank.
    """
    assert Wiring.order(*Wiring.GAS_AND_SOLAR) == ((5, 3, 4, 1, 2, 0), ())


def test_physics_whose_constraints_form_a_cycle_keep_the_order_added() -> None:
    """Catches a cycle of constraints that hangs the order or reorders the components involved.

    Controller 2 commands device 0 and reads device 1; controller 3 commands device 1 and reads device 0. Each device
    must then run before the other, so both keep the order they were added in and are reported as one cycle.
    """
    sources = (frozenset({2}), frozenset({3}), frozenset({1}), frozenset({0}))
    assert Wiring.order("PPCC", sources) == ((2, 3, 0, 1), ((0, 1),))


def test_a_cycle_keeps_its_place_among_the_ordered_physics() -> None:
    """Catches a cycle that loses the constraints it has to the other physics, or drags them out of their order.

    Physics 0 and 1 form a cycle through controllers 3 and 4; controller 5 commands device 2 and reads device 0, so
    device 2 runs before the cycle, which keeps its order added.
    """
    sources = (frozenset({3}), frozenset({4}), frozenset({5}), frozenset({1}), frozenset({0}), frozenset({0}))
    assert Wiring.order("PPPCCC", sources) == ((3, 4, 5, 2, 0, 1), ((0, 1),))


@pytest.mark.parametrize(
    ("kinds", "sources", "expected_positions"),
    [
        # the heating curve added before the energy manager it reads
        ((L2ControllerComponent, L2ControllerComponent), [{1}, set()], [1, 0]),
        # independent L2 controllers keep the order added
        ((L2ControllerComponent, L2ControllerComponent), [set(), set()], [0, 1]),
        # a cycle keeps the order added
        ((L2ControllerComponent, L2ControllerComponent), [{1}, {0}], [0, 1]),
        # two readers of one source keep their order behind it
        ((L2ControllerComponent, L2ControllerComponent, L2ControllerComponent), [{2}, {2}, set()], [2, 0, 1]),
        # reads of other kinds do not reorder
        (
            (PhysicsComponent, L2ControllerComponent, L1ControllerComponent, L2ControllerComponent),
            [set(), {3}, {1}, {0}],
            [3, 1, 2, 0],
        ),
        # only L2 controllers are reordered by their reads
        ((L1ControllerComponent, L1ControllerComponent), [{1}, set()], [0, 1]),
    ],
)
def test_an_l2_controller_is_evaluated_after_the_l2_controllers_it_reads(
    kinds: Tuple[Type[Component], ...], sources: List[set], expected_positions: List[int]
) -> None:
    """Catches the heating curve evaluated before the energy manager whose raise it applies: it lags it a pass.

    It also catches a reordering of anything but L2 controllers, and a cycle that loses a controller.
    """
    assert Simulator.evaluation_positions(kinds, sources).positions == tuple(expected_positions)


def test_l2_controllers_that_read_one_another_are_reported_as_a_cycle() -> None:
    """Catches an L2 cycle that is kept in the order added without being reported, while a physics cycle is.

    L2 controllers 1 and 2 read each other and 0 reads nothing; the three keep the order added, the cycle (1, 2) is
    reported, and the physics cycles stay empty.
    """
    kinds = (L2ControllerComponent, L2ControllerComponent, L2ControllerComponent)
    order = Simulator.evaluation_positions(kinds, [set(), {2}, {1}])
    assert order.positions == (0, 1, 2)
    assert order.l2_cycles == ((1, 2),)
    assert not order.cycles


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


class DataSourceProbe(KindProbe, DataSourceComponent):
    """A probe of the data-source kind."""


class ControllerProbe(KindProbe, L1ControllerComponent):
    """A probe of the L1-controller kind."""


class DeviceProbe(KindProbe, PhysicsComponent):
    """A probe of the physics kind."""


class CoordinatorProbe(KindProbe, L2ControllerComponent):
    """A probe of the L2-controller kind."""


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
    with pytest.raises(ComponentKindNotDeclaredError, match="must inherit exactly one kind base class"):
        simulator.add_component(KindProbe(simulator.get_simulation_parameters(), "Probe", []))


def wired_probes(simulator: Simulator, letters: str, sources: Tuple[FrozenSet[int], ...]) -> List[KindProbe]:
    """Add one probe per kind letter to ``simulator``, each reading the outputs of the probes its source set names.

    The letters are D data source, L L2 controller, C L1 controller and P physics. The probes are named after their
    kind letter and position (``C3``), added in the order given, and each source
    becomes an input ``From<name>`` wired to that probe's output, so the simulator sees the wiring of the system.
    """
    probe_class_of_letter: Dict[str, Type[KindProbe]] = {
        "D": DataSourceProbe,
        "L": CoordinatorProbe,
        "C": ControllerProbe,
        "P": DeviceProbe,
    }
    parameters = simulator.get_simulation_parameters()
    evaluated: List[str] = []
    probes = [
        probe_class_of_letter[letter](parameters, f"{letter}{position}", evaluated)
        for position, letter in enumerate(letters)
    ]
    for probe, probe_sources in zip(probes, sources):
        for source in sorted(probe_sources):
            field_name = f"From{probes[source].component_name}"
            probe.add_input(probe.component_name, field_name, lt.LoadTypes.ANY, lt.Units.ANY, True)
            probe.connect_input(field_name, probes[source].component_name, KindProbe.Value)
    for probe in probes:
        simulator.add_component(probe)
    simulator.prepare_calculation()
    simulator.connect_all_components()
    return probes


def test_the_simulator_runs_the_gas_and_solar_devices_before_their_tank(tmp_path: Path) -> None:
    """Catches the simulator ignoring the wiring when it plans a pass, which leaves the tank before the devices.

    The probes are wired as household_gas_solar_thermal is: the tank added first, read by both controllers.
    """
    simulator = probe_simulator(tmp_path)
    wired_probes(simulator, *Wiring.GAS_AND_SOLAR)
    planned = [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()]
    assert planned == ["D5", "C3", "C4", "P1", "P2", "P0"]


def test_a_cycle_of_constraints_is_warned_about_and_keeps_the_order_added(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catches a cycle of physics constraints that fails the build, reorders its components or passes silently.

    Two controllers each command one device and read the other's, so each device must run first; the simulator keeps
    them in the order added and names both in a warning.
    """
    simulator = probe_simulator(tmp_path)
    wired_probes(simulator, "PPCC", (frozenset({2}), frozenset({3}), frozenset({1}), frozenset({0})))
    capsys.readouterr()
    planned = [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()]
    assert planned == ["C2", "C3", "P0", "P1"]
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("WRN:")]
    assert len(warnings) == 1
    assert "P0, P1" in warnings[0]


def test_l2_controllers_that_read_one_another_are_warned_about_and_keep_the_order_added(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catches an L2 cycle the simulator keeps in the order added without a warning, while it warns of a physics one.

    Two L2 controllers read each other; the plan keeps them in the order added and one warning names both.
    """
    simulator = probe_simulator(tmp_path)
    wired_probes(simulator, "LL", (frozenset({1}), frozenset({0})))
    capsys.readouterr()
    planned = [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()]
    assert planned == ["L0", "L1"]
    warnings = [line for line in capsys.readouterr().out.splitlines() if line.startswith("WRN:")]
    assert len(warnings) == 1
    assert "L0, L1" in warnings[0]


def test_a_component_added_after_the_plan_is_part_of_the_next_plan(tmp_path: Path) -> None:
    """Catches a cached evaluation plan that leaves out a component added after it was computed."""
    simulator = probe_simulator(tmp_path)
    parameters = simulator.get_simulation_parameters()
    evaluated: List[str] = []
    simulator.add_component(DeviceProbe(parameters, "Device", evaluated))
    assert [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()] == ["Device"]

    simulator.add_component(DataSourceProbe(parameters, "Weather", evaluated))

    assert [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()] == ["Weather", "Device"]


def test_an_input_that_stays_unconnected_orders_nothing(tmp_path: Path) -> None:
    """Catches the plan ordering by the source an input names rather than by the output it resolved to.

    Controller C1 commands device P2 and reads it. Its optional input naming an output P0 does not have stays
    unconnected, so C1 does not read P0 and P0 keeps its place before P2. Ordered by the name the input carries, P2
    would move before P0.
    """
    simulator = probe_simulator(tmp_path)
    parameters = simulator.get_simulation_parameters()
    evaluated: List[str] = []
    store = DeviceProbe(parameters, "P0", evaluated)
    controller = ControllerProbe(parameters, "C1", evaluated)
    device = DeviceProbe(parameters, "P2", evaluated)
    device.add_input(device.component_name, "FromC1", lt.LoadTypes.ANY, lt.Units.ANY, True)
    device.connect_input("FromC1", controller.component_name, KindProbe.Value)
    controller.add_input(controller.component_name, "FromP2", lt.LoadTypes.ANY, lt.Units.ANY, True)
    controller.connect_input("FromP2", device.component_name, KindProbe.Value)
    controller.add_input(controller.component_name, "FromP0", lt.LoadTypes.ANY, lt.Units.ANY, False)
    controller.connect_input("FromP0", store.component_name, "NoSuchOutput")
    for probe in (store, controller, device):
        simulator.add_component(probe)
    simulator.prepare_calculation()
    simulator.connect_all_components()

    planned = [wrapped.my_component.component_name for wrapped in simulator.evaluation_plan()]

    assert controller.inputs[-1].source_output is None
    assert planned == ["C1", "P0", "P2"]
