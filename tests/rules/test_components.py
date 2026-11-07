"""Direct tests of the component-build rules: each rule on a hand-built system with its fault and on one without.

The systems are the probes of ``tests/test_component_kinds.py``: components of each kind that publish one value and
read what they are wired to. Beside the rules, the tests cover what the family shares (when the cost rules apply,
which classes the cost model refuses, the evaluation order the cycle rules read), the sentence of a class without one
kind, the simulator's two subjects and its two hooks, and the build reporting every fault at once where it used to
stop at the first (D14 of ``roadmap/system_kinds_spec.md``). The existing tests of the simulator, the wrapper, the
kinds and the cost declarations prove that the moved checks kept their messages.
"""

from pathlib import Path
from typing import Any, List, Sequence

import pytest

from hisim import loadtypes as lt
from hisim.component import ComponentConnection, ComponentKinds, MeterComponent, PhysicsComponent
from hisim.components.advanced_battery_bslib import Battery
from hisim.components.generic_pv_system import PVSystem
from hisim.config import ComponentID, DisplayConfig
from hisim.dynamic_component import DynamicComponent
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.setup_rules.components import (
    AutoConnectPossible,
    ConnectionRules,
    CostDeclarationRule,
    CostRelevanceDeclared,
    EvaluationOrderRule,
    KindInEvaluationOrder,
    LoadTypeAgrees,
    MandatoryInputConnected,
    NoL2Cycle,
    NoPhysicsCycle,
    NotEmpty,
    OneKindBase,
    PricedHasCostFacts,
    SourceOutputRegistered,
    UndeclaredCostRelevanceError,
    UniqueName,
    UniqueOutputKey,
    UnitAgrees,
    UnpriceableComponentError,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, SetupRuleViolation
from hisim.setup_rules.subjects import AddedSystem, BuiltSystem
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from tests import functions_for_testing as fft
from tests.test_component_kinds import (
    ControllerProbe,
    DataSourceProbe,
    DeviceProbe,
    KindProbe,
    KindProbeConfig,
    probe_simulator,
    wired_probes,
)
from tests.test_economics_declaration_gate import PricedWithoutFacts, UndeclaredDevice

pytestmark = pytest.mark.base


class DynamicProbe(DynamicComponent, MeterComponent):
    """A dynamic component that reads, through its default connections, the components whose names it is given."""

    def __init__(self, my_simulation_parameters: SimulationParameters, name: str, reads: Sequence[str]) -> None:
        """Build the probe with one output; ``reads`` names the components its default connections read, empty for none."""
        super().__init__(
            my_component_inputs=[],
            my_component_outputs=[],
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=KindProbeConfig(component_id=ComponentID(name=name)),
            my_display_config=DisplayConfig(),
        )
        self.reads = tuple(reads)
        self.add_output(self.component_name, KindProbe.Value, lt.LoadTypes.ANY, lt.Units.ANY, output_description="A constant.")

    def declares_default_connections(self) -> bool:
        """Declare default connections when the probe reads anything."""
        return bool(self.reads)

    def default_connections_for(self, source_component: Any) -> List[Any]:
        """Return one stand-in connection for a component the probe reads, none for another."""
        return [source_component.component_name] if source_component.component_name in self.reads else []


class Rig:
    """Hand-built subjects of probes for the rules of the family."""

    PARAMETERS = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)

    @classmethod
    def device(cls, name: str) -> DeviceProbe:
        """A physics probe."""
        return DeviceProbe(cls.PARAMETERS, name, [])

    @staticmethod
    def added(components: Sequence[Any], *options: PostProcessingOptions) -> AddedSystem:
        """The components as added, with the outputs they own and the options given; a cost stand-in owns none."""
        outputs = tuple(output for component in components for output in getattr(component, "outputs", ()))
        return AddedSystem(components=tuple(components), outputs=outputs, post_processing_options=options)

    @staticmethod
    def built(components: Sequence[Any], connects_automatically: Sequence[str] = ()) -> BuiltSystem:
        """The components as connected, with the outputs they own."""
        outputs = tuple(output for component in components for output in component.outputs)
        return BuiltSystem(components=tuple(components), outputs=outputs, connects_automatically=frozenset(connects_automatically))

    @classmethod
    def reader_of(cls, source: DeviceProbe, load_type: lt.LoadTypes, unit: lt.Units, mandatory: bool = True) -> DeviceProbe:
        """A device with one input ``In`` of the given load type and unit, connected to the source's value."""
        reader = cls.device("Reader")
        component_input = reader.add_input(reader.component_name, "In", load_type, unit, mandatory)
        component_input.src_object_name, component_input.src_field_name = source.component_name, KindProbe.Value
        component_input.source_output = source.value_output
        return reader

    @classmethod
    def typed_source(cls, load_type: lt.LoadTypes, unit: lt.Units) -> DeviceProbe:
        """A device whose value output has the given load type and unit."""
        source = cls.device("Source")
        source.value_output.load_type, source.value_output.unit = load_type, unit
        return source


class TestComponentsAsAdded:
    """The rules of the components as added: one kind, one name, a non-empty run."""

    def test_one_kind_base_finds_a_class_without_a_kind_once(self) -> None:
        """Catches a class without a kind that reaches the run, or one fault per instance of the class."""
        probes = [KindProbe(Rig.PARAMETERS, "A", []), KindProbe(Rig.PARAMETERS, "B", []), Rig.device("P")]

        faults = OneKindBase().check(Rig.added(probes))

        assert [fault.component for fault in faults] == ["A"]
        assert "must inherit exactly one kind base class" in faults[0].message and "it inherits none" in faults[0].message

    def test_one_kind_base_accepts_a_class_of_one_kind(self) -> None:
        """Catches a rule that refuses a kind it should accept."""
        assert not OneKindBase().check(Rig.added([Rig.device("P"), ControllerProbe(Rig.PARAMETERS, "C", [])]))

    def test_unique_name_finds_every_name_added_twice(self) -> None:
        """Catches a second component of one name, which the simulator used to refuse when it was added."""
        faults = UniqueName().check(Rig.added([Rig.device("P"), Rig.device("P"), Rig.device("Q"), Rig.device("Q")]))

        assert [fault.message.split(";")[0] for fault in faults] == ["duplicate component name : P", "duplicate component name : Q"]
        assert [fault.component for fault in faults] == ["P", "Q"]

    def test_unique_name_accepts_distinct_names(self) -> None:
        """Catches a rule that refuses distinct names."""
        assert not UniqueName().check(Rig.added([Rig.device("P"), Rig.device("Q")]))

    def test_not_empty_finds_a_run_without_a_component_or_without_an_output(self) -> None:
        """Catches an empty run that reaches the time loop."""
        no_component = NotEmpty().check(AddedSystem(components=(), outputs=()))
        no_output = NotEmpty().check(AddedSystem(components=(Rig.device("P"),), outputs=()))

        assert [fault.message.split(";")[0] for fault in [*no_component, *no_output]] == [
            "Not a single component was defined. Quitting.",
            "Not a single column was defined.",
        ]

    def test_not_empty_accepts_a_run_with_a_component_and_an_output(self) -> None:
        """Catches a rule that refuses a run with something to compute."""
        assert not NotEmpty().check(Rig.added([Rig.device("P")]))


class TestCostDeclarations:
    """The two cost-declaration rules and what they share."""

    def test_cost_relevance_declared_lists_every_undeclared_class_once(self) -> None:
        """Catches a lifecycle-cost run that starts with a class no cost model describes."""
        faults = CostRelevanceDeclared().check(
            Rig.added([UndeclaredDevice(), UndeclaredDevice("Second"), PricedWithoutFacts()], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        )

        assert len(faults) == 1 and faults[0].message.count("UndeclaredDevice (module") == 1
        assert faults[0].message.startswith("Lifecycle cost computation was requested, but 1 component class(es)")

    def test_cost_relevance_declared_accepts_a_declared_fleet_and_a_run_without_costs(self) -> None:
        """Catches the rule firing on a declared class, or in a run that never asks what anything costs."""
        assert not CostRelevanceDeclared().check(Rig.added([Battery.__new__(Battery)], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS))
        assert not CostRelevanceDeclared().check(Rig.added([UndeclaredDevice()], PostProcessingOptions.PLOT_LINE))

    def test_priced_has_cost_facts_lists_every_priced_class_without_a_source(self) -> None:
        """Catches a PRICED class nothing can describe that reaches the run."""
        faults = PricedHasCostFacts().check(Rig.added([PricedWithoutFacts(), UndeclaredDevice()], PostProcessingOptions.LIFECYCLE_COST_REPORT))

        assert len(faults) == 1 and "PricedWithoutFacts" in faults[0].message and "UndeclaredDevice" not in faults[0].message

    def test_priced_has_cost_facts_accepts_a_hook_or_an_adapter_entry(self) -> None:
        """Catches the rule refusing a class with its own get_cost_facts or with an adapter entry."""
        assert not PricedHasCostFacts().check(Rig.added([PVSystem.__new__(PVSystem), Battery.__new__(Battery)], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS))

    def test_an_undeclared_class_is_reported_alone_with_its_own_error(self) -> None:
        """Catches the two defects reported together, or under the base error: the undeclared one is fixed first."""
        registry = fft.ComponentRules.registry([CostRelevanceDeclared, PricedHasCostFacts])
        both = Rig.added([UndeclaredDevice(), PricedWithoutFacts()], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)

        with pytest.raises(UndeclaredCostRelevanceError) as raised:
            registry.run(RuleStage.BUILD, both, SetupKind.RESIDENTIAL)
        with pytest.raises(UnpriceableComponentError):
            registry.run(RuleStage.BUILD, Rig.added([PricedWithoutFacts()], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in raised.value.faults] == ["CB-08"] and [skip.code for skip in raised.value.skipped] == ["CB-09"]
        assert issubclass(UndeclaredCostRelevanceError, ValueError) and issubclass(UnpriceableComponentError, ValueError)

    def test_requested(self) -> None:
        """Catches a report run (LIFECYCLE_COST_REPORT) that the rules let through to fail after the simulation."""
        assert CostDeclarationRule.requested(Rig.added([], PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS))
        assert CostDeclarationRule.requested(Rig.added([], PostProcessingOptions.LIFECYCLE_COST_REPORT))
        assert not CostDeclarationRule.requested(Rig.added([], PostProcessingOptions.COMPUTE_KPIS))

    def test_has_cost_facts_source(self) -> None:
        """Catches a check that disagrees with the adapter's precedence: hook first, table second."""
        assert CostDeclarationRule.has_cost_facts_source(PVSystem)
        assert CostDeclarationRule.has_cost_facts_source(Battery)
        assert not CostDeclarationRule.has_cost_facts_source(PricedWithoutFacts)

    def test_refusal(self) -> None:
        """Catches the one statement of the rule naming the wrong error for a class."""
        assert CostDeclarationRule.refusal(UndeclaredDevice) is UndeclaredCostRelevanceError
        assert CostDeclarationRule.refusal(PricedWithoutFacts) is UnpriceableComponentError
        assert CostDeclarationRule.refusal(Battery) is None
        assert CostDeclarationRule.refusal(object) is UndeclaredCostRelevanceError

    def test_classes_refused_as(self) -> None:
        """Catches a class listed once per instance, or out of the order added."""
        subject = Rig.added([PricedWithoutFacts(), UndeclaredDevice(), PricedWithoutFacts("Again")])

        assert CostDeclarationRule.classes_refused_as(subject, UnpriceableComponentError) == [PricedWithoutFacts]
        assert CostDeclarationRule.classes_refused_as(subject, UndeclaredCostRelevanceError) == [UndeclaredDevice]


class TestConnections:
    """The rules of the connected inputs: one output per name, matching load types and units, mandatory inputs connected."""

    def test_unique_output_key_finds_an_output_registered_twice(self) -> None:
        """Catches two result columns of one name, which the wrapper used to refuse when the second was registered."""
        first, second = Rig.device("P"), Rig.device("P")

        faults = UniqueOutputKey().check(Rig.built([first, second]))

        assert [(fault.component, fault.port) for fault in faults] == [("P", KindProbe.Value)]
        assert faults[0].message.startswith("Trying to register the same key twice: P # Value. Check if more than one building is being modeled.")

    def test_unique_output_key_accepts_distinct_outputs(self) -> None:
        """Catches a rule that refuses outputs of distinct names."""
        assert not UniqueOutputKey().check(Rig.built([Rig.device("P"), Rig.device("Q")]))

    def test_load_type_agrees_finds_two_concrete_load_types(self) -> None:
        """Catches an input that reads a quantity of another load type."""
        reader = Rig.reader_of(Rig.typed_source(lt.LoadTypes.HEATING, lt.Units.WATT), lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

        faults = LoadTypeAgrees().check(Rig.built([reader]))

        assert [(fault.component, fault.port) for fault in faults] == [("Reader", "In")]
        assert "do not have the same load type!" in faults[0].message

    def test_load_type_agrees_accepts_the_same_load_type_or_any(self) -> None:
        """Catches the wildcard ANY refused, or a matching load type."""
        same = Rig.reader_of(Rig.typed_source(lt.LoadTypes.HEATING, lt.Units.WATT), lt.LoadTypes.HEATING, lt.Units.WATT)
        wildcard = Rig.reader_of(Rig.typed_source(lt.LoadTypes.ANY, lt.Units.WATT), lt.LoadTypes.HEATING, lt.Units.WATT)

        assert not LoadTypeAgrees().check(Rig.built([same, wildcard]))

    def test_unit_agrees_finds_two_concrete_units(self) -> None:
        """Catches a value read in another unit than it was written in."""
        reader = Rig.reader_of(Rig.typed_source(lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT), lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

        faults = UnitAgrees().check(Rig.built([reader]))

        assert len(faults) == 1 and "do not have the same unit!" in faults[0].message

    def test_unit_agrees_accepts_the_same_unit_or_any_on_one_side(self) -> None:
        """Catches the Units.ANY case refused: it connects with a warning of the connection, not a refusal."""
        same = Rig.reader_of(Rig.typed_source(lt.LoadTypes.ELECTRICITY, lt.Units.WATT), lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        wildcard = Rig.reader_of(Rig.typed_source(lt.LoadTypes.ELECTRICITY, lt.Units.ANY), lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

        assert not UnitAgrees().check(Rig.built([same, wildcard]))

    def test_mandatory_input_connected_finds_an_unconnected_mandatory_input(self) -> None:
        """Catches a mandatory input that reads zero in every step."""
        reader = Rig.device("Reader")
        reader.add_input(reader.component_name, "In", lt.LoadTypes.ANY, lt.Units.ANY, True)
        source = Rig.device("Source")

        faults = MandatoryInputConnected().check(Rig.built([reader, source]))

        assert [(fault.component, fault.port) for fault in faults] == [("Reader", "In")]
        assert "is not connected to any ComponentOutput" in faults[0].message and "['Reader', 'Source']" in faults[0].message

    def test_mandatory_input_connected_accepts_a_connected_an_optional_and_an_allowed_input(self) -> None:
        """Catches an optional input, or one allowed to stay unconnected, refused."""
        reader = Rig.reader_of(Rig.device("Source"), lt.LoadTypes.ANY, lt.Units.ANY)
        reader.add_input(reader.component_name, "Optional", lt.LoadTypes.ANY, lt.Units.ANY, False)
        reader.add_input(reader.component_name, "Allowed", lt.LoadTypes.ANY, lt.Units.ANY, True).allow_unconnected_mandatory = True

        assert not MandatoryInputConnected().check(Rig.built([reader]))

    def test_source_output_registered_finds_an_output_no_component_owns(self) -> None:
        """Catches an input whose ordering constraint would vanish from the evaluation order."""
        reader = Rig.reader_of(Rig.device("Source"), lt.LoadTypes.ANY, lt.Units.ANY)

        faults = SourceOutputRegistered().check(Rig.built([reader]))

        assert len(faults) == 1 and "The inputs ['Reader # In'] resolved to outputs that no added component registered" in faults[0].message

    def test_source_output_registered_accepts_an_owned_output(self) -> None:
        """Catches a rule that refuses an input reading an output of the run."""
        source = Rig.device("Source")
        assert not SourceOutputRegistered().check(Rig.built([source, Rig.reader_of(source, lt.LoadTypes.ANY, lt.Units.ANY)]))


class TestAutomaticConnection:
    """The rule of the components added with connect_automatically=True."""

    def test_auto_connect_possible_finds_a_component_without_default_connections_and_one_without_a_match(self) -> None:
        """Catches a component asked to connect automatically whose inputs then stay silently unconnected."""
        lonely = Rig.device("Lonely")
        unmatched = Rig.device("Unmatched")
        unmatched.add_default_connections([ComponentConnection("In", "NoSuchClass", KindProbe.Value)])
        dynamic = DynamicProbe(Rig.PARAMETERS, "Meter", reads=())
        dynamic_unmatched = DynamicProbe(Rig.PARAMETERS, "Meter2", reads=("Meter2", "Absent"))

        faults = AutoConnectPossible().check(Rig.built([lonely, unmatched, dynamic, dynamic_unmatched], ["Lonely", "Unmatched", "Meter", "Meter2"]))

        assert [fault.component for fault in faults] == ["Lonely", "Unmatched", "Meter", "Meter2"]
        assert faults[0].message.startswith("Automatic connection does not work for Lonely because no default connections were found.")
        assert faults[1].message.startswith("No component in the system setup matches the default connections of Unmatched.")
        assert faults[3].message.startswith("No component in the system setup matches the default connections of Meter2.")

    def test_auto_connect_possible_accepts_a_match_and_a_component_connected_by_hand(self) -> None:
        """Catches a rule that refuses a component whose default connections match, or one never asked to connect automatically."""
        source = Rig.device("Source")
        target = Rig.device("Target")
        target.add_default_connections([ComponentConnection("In", "DeviceProbe", KindProbe.Value)])
        meter = DynamicProbe(Rig.PARAMETERS, "Meter", reads=("Source",))

        assert not AutoConnectPossible().check(Rig.built([source, target, meter, Rig.device("ByHand")], ["Target", "Meter"]))


class TestEvaluationOrder:
    """The rules of the evaluation order: every kind placed, and the cycles it breaks logged."""

    def test_kind_in_evaluation_order_finds_a_kind_the_order_lacks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a kind base class added to the component layer and not to the order of a pass."""
        monkeypatch.setattr(Simulator, "EVALUATION_ORDER_OF_KINDS", tuple(kind for kind in Simulator.EVALUATION_ORDER_OF_KINDS if kind is not PhysicsComponent))

        faults = KindInEvaluationOrder().check(Rig.built([Rig.device("P"), DataSourceProbe(Rig.PARAMETERS, "D", [])]))

        assert len(faults) == 1 and "Not kind base classes, so not placeable in a pass: ['PhysicsComponent']." in faults[0].message

    def test_kind_in_evaluation_order_accepts_every_kind_and_leaves_a_class_without_one_to_cb_01(self) -> None:
        """Catches the rule refusing a placed kind, or reporting a class without a kind, which is another rule's fault."""
        assert not KindInEvaluationOrder().check(Rig.built([Rig.device("P"), KindProbe(Rig.PARAMETERS, "K", [])]))

    def test_no_physics_cycle_finds_a_cycle_of_constraints(self, tmp_path: Path) -> None:
        """Catches a cycle of physics constraints passing without a warning."""
        simulator = probe_simulator(tmp_path)
        wired_probes(simulator, "PPCC", (frozenset({2}), frozenset({3}), frozenset({1}), frozenset({0})))

        faults = NoPhysicsCycle().check(simulator.built_system())

        assert len(faults) == 1 and faults[0].message.startswith("The physics components P0, P1 require one another to be evaluated first")
        assert not NoPhysicsCycle.ENFORCED

    def test_no_physics_cycle_accepts_an_order_without_a_cycle(self, tmp_path: Path) -> None:
        """Catches a warning about a system whose controller commands one device and reads another."""
        simulator = probe_simulator(tmp_path)
        wired_probes(simulator, "PCP", (frozenset(), frozenset({0, 2}), frozenset({1})))

        assert not NoPhysicsCycle().check(simulator.built_system())

    def test_no_l2_cycle_finds_l2_controllers_that_read_one_another(self, tmp_path: Path) -> None:
        """Catches an L2 cycle passing without a warning."""
        simulator = probe_simulator(tmp_path)
        wired_probes(simulator, "LLL", (frozenset({1}), frozenset({2}), frozenset({1})))

        faults = NoL2Cycle().check(simulator.built_system())

        assert len(faults) == 1 and faults[0].message.startswith("The L2 controllers L1, L2 read one another in a cycle.")
        assert not NoL2Cycle.ENFORCED

    def test_no_l2_cycle_accepts_l2_controllers_reading_one_way(self, tmp_path: Path) -> None:
        """Catches a warning about an L2 controller that only reads another."""
        simulator = probe_simulator(tmp_path)
        wired_probes(simulator, "LL", (frozenset({1}), frozenset()))

        assert not NoL2Cycle().check(simulator.built_system())

    def test_order_of(self, tmp_path: Path) -> None:
        """Catches the rules reading another order than the run's, or failing on a class without a kind."""
        simulator = probe_simulator(tmp_path)
        wired_probes(simulator, "PCP", (frozenset(), frozenset({0, 2}), frozenset({1})))

        order = EvaluationOrderRule.order_of(simulator.built_system())

        assert order is not None and [simulator.wrapped_components[position] for position in order.positions] == simulator.evaluation_plan()
        assert EvaluationOrderRule.order_of(Rig.built([KindProbe(Rig.PARAMETERS, "K", [])])) is None

    def test_names(self) -> None:
        """Catches the names of a cycle out of the order of its positions."""
        components = [Rig.device("P0"), Rig.device("P1"), Rig.device("P2")]

        assert EvaluationOrderRule.names(components, (2, 0)) == "P2, P0"


class TestTheSimulator:
    """The simulator's sentence of a kind, its two subjects, its order and its two hooks."""

    def test_component_kinds_problem(self) -> None:
        """Catches the lookup and the rule disagreeing about which classes have one kind."""
        problem = ComponentKinds.problem(KindProbe)

        assert problem is not None and problem.endswith("it inherits none.")
        assert ComponentKinds.problem(DeviceProbe) is None
        assert ComponentKinds.problem(object) is not None

    def test_evaluation_order_of(self, tmp_path: Path) -> None:
        """Catches the order of the components differing from the order of the positions of their kinds and wiring."""
        simulator = probe_simulator(tmp_path)
        probes = wired_probes(simulator, "PCD", (frozenset({1}), frozenset(), frozenset()))

        assert Simulator.evaluation_order_of(probes).positions == (2, 1, 0)

    def test_added_system_and_built_system(self, tmp_path: Path) -> None:
        """Catches a subject that loses the outputs, the options or the components connected automatically."""
        simulator = probe_simulator(tmp_path)
        simulator.get_simulation_parameters().post_processing_options = [PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS]
        first, second = Rig.device("P"), Rig.device("Q")
        simulator.add_component(first)
        simulator.add_component(second, connect_automatically=True)

        added, built = simulator.added_system(), simulator.built_system()

        assert added.components == built.components == (first, second)
        assert added.outputs == built.outputs == (first.value_output, second.value_output)
        assert added.post_processing_options == (PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS,)
        assert built.connects_automatically == frozenset({"Q"})

    def test_a_run_refuses_every_fault_of_the_components_as_added_before_it_prepares_anything(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches the simulator stopping at the first fault when a component is added, or preparing a run it refuses."""

        def explode(*_args: Any, **_kwargs: Any) -> None:
            """Fail the test if the run got this far."""
            raise AssertionError("the run prepared its directory before the rules of the components as added refused it")

        simulator = probe_simulator(tmp_path)
        simulator.add_component(Rig.device("P"))
        simulator.add_component(Rig.device("P2"))
        simulator.add_component(KindProbe(Rig.PARAMETERS, "P2", []))
        monkeypatch.setattr(Simulator, "prepare_simulation_directory", explode)

        with pytest.raises(SetupRuleViolation) as raised:
            simulator.run_all_timesteps()

        assert [fault.code for fault in raised.value.faults] == ["CB-01", "CB-02"]

    def test_a_run_refuses_every_connection_fault_before_the_first_step(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches the connection stopping at its first fault: a load type, a unit and a mandatory input, all in one refusal."""

        def explode(*_args: Any, **_kwargs: Any) -> None:
            """Fail the test if the run got this far."""
            raise AssertionError("the run reached its first step")

        simulator = probe_simulator(tmp_path)
        heat, power = Rig.typed_source(lt.LoadTypes.HEATING, lt.Units.WATT), Rig.device("Power")
        power.value_output.load_type, power.value_output.unit = lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT
        reader = Rig.device("Reader")
        for field_name, source in (("FromHeat", heat), ("FromPower", power), ("FromNobody", None)):
            reader.add_input(reader.component_name, field_name, lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
            reader.connect_input(field_name, source.component_name if source else "Nobody", KindProbe.Value)
        for component in (heat, power, reader):
            simulator.add_component(component)
        monkeypatch.setattr(Simulator, "process_one_timestep", explode)

        with pytest.raises(SetupRuleViolation) as raised:
            simulator.run_all_timesteps()

        assert [(fault.code, fault.port) for fault in raised.value.faults] == [("CB-04", "FromHeat"), ("CB-05", "FromPower"), ("CB-06", "FromNobody")]


class TestTheRulesThatReadTheWiring:
    """The fluid-loop and energy-port rules wait for the connection rules (``ConnectionRules``)."""

    def test_a_broken_connection_is_refused_alone_and_the_rules_of_the_wiring_are_skipped(self) -> None:
        """Catches a broken connection reported together with what the loop and port rules make of it.

        Before the connection rules moved here, the connection raised first and nothing else was judged; the fluid-loop
        rules that walk the wiring and every energy-port rule now require ``CB-04`` to ``CB-06`` and are skipped.
        """
        reader = Rig.reader_of(Rig.typed_source(lt.LoadTypes.HEATING, lt.Units.WATT), lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

        findings = SetupRuleRegistry.collect().findings(RuleStage.BUILD, Rig.built([reader]), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in findings.faults] == ["CB-04", "CB-10"]
        skipped = {skip.code for skip in findings.skipped}
        assert {"FL-02", "FL-08", "EP-01", "EP-05", "EP-06"} <= skipped and "FL-01" not in skipped

    def test_the_rules_of_the_wiring_require_the_connection_rules(self) -> None:
        """Catches a rule that reads the wiring and would judge a system whose connection is broken."""
        wiring_rules = [
            rule for rule in SetupRuleRegistry.collect().rules if rule.CODE.startswith(("EP-", "FL-")) and rule.CODE != "FL-01"
        ]

        assert wiring_rules and all(ConnectionRules.CODES <= rule.REQUIRES for rule in wiring_rules)
