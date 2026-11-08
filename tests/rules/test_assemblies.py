"""Direct tests of the assembly rules: each rule on what an expansion of imports recorded, with its fault and without.

The rules that refuse a recorded refusal are tested with a refusal of their code and with one of another code, on the
subject they read: the assemblies found (``ImportedAssemblies``), the expanded imports (``ExpandedImports``) or the
wired file (``BuiltDocument``). The library check is tested on an assembly of the mock library and on a broken one.
Further tests expand small inline files against the mock library and show that the expansion records every fault of
a step and the rules refuse them together, and pin the helpers the expansion gained when its checks moved here: the
resolver's lookup, the parameter selection, the sequence, the recorded sequence of a record, the binder's attempt,
the selection's refusals, the refusal record and the error.
"""

from pathlib import Path
from typing import ClassVar, Optional, Sequence, Tuple, Type

import pytest

from hisim import loadtypes as lt
from hisim.energy_system.assemblies.addresses import Block, sequence
from hisim.energy_system.assemblies.binding import PortBinder, PortRefused
from hisim.energy_system.assemblies.expansion import ImportExpander, expand_imports, recorded_sequence_refusal
from hisim.energy_system.assemblies.parameters import resolve_selection, select
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.selection import Observer, SelectionPlan
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import (
    CatalogueRefusal,
    EnergySystemAssemblyError,
    EnergySystemErrorId,
    EnergySystemImportsError,
    ImportRefusal,
    WiringRefusal,
)
from hisim.energy_system.imports_model import Selection
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.wiring import WiredSystem
from hisim.setup_rules.assemblies import (
    ActuationSelection,
    AssemblyFound,
    AssemblyFoundOnce,
    BoundOutputIsRead,
    BoundPartnerExists,
    CircuitEndsMatch,
    ConstraintHolds,
    ImportRule,
    KnownPort,
    LibraryChecker,
    LibraryValid,
    ObserverSelection,
    OneProviderPerCarrier,
    OptionalPortDecided,
    OrderValid,
    ParameterValid,
    PortContract,
    PortHasAPartner,
    PortUnambiguous,
    RequiredPortNotDeclined,
    VerbOnAnActivePort,
    WeightFree,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, SetupRule, Skip
from hisim.setup_rules.subjects import BuiltDocument, ExpandedImports, ImportedAssemblies
from tests import functions_for_testing as fft
from tests.assemblies.helpers import Library, Mocks, read_system, site

pytestmark = pytest.mark.base


class Imports:
    """Recorded refusals and the subjects that hold them, of a file ``house.energy_system.yaml`` importing ``tank``."""

    #: The path of the file the subjects belong to.
    FILE: ClassVar[str] = "house.energy_system.yaml"

    #: The rules that refuse what the lookup of the assemblies recorded, with the identifier of their refusals.
    LOOKUP: ClassVar[Tuple[Tuple[Type[SetupRule], EnergySystemErrorId], ...]] = (
        (AssemblyFound, EnergySystemErrorId.ASSEMBLY_NOT_FOUND),
        (AssemblyFoundOnce, EnergySystemErrorId.ASSEMBLY_FOUND_TWICE),
    )

    #: The rules that refuse what the selection, the binding and the sequence recorded, with their identifiers.
    EXPANSION: ClassVar[Tuple[Tuple[Type[SetupRule], EnergySystemErrorId], ...]] = (
        (ParameterValid, EnergySystemErrorId.PARAMETER_INVALID),
        (ConstraintHolds, EnergySystemErrorId.CONSTRAINT_VIOLATED),
        (PortHasAPartner, EnergySystemErrorId.PORT_WITHOUT_PARTNER),
        (PortUnambiguous, EnergySystemErrorId.PORT_AMBIGUOUS),
        (RequiredPortNotDeclined, EnergySystemErrorId.REQUIRED_PORT_DECLINED),
        (BoundPartnerExists, EnergySystemErrorId.BOUND_PARTNER_ABSENT),
        (OptionalPortDecided, EnergySystemErrorId.OPTIONAL_PORT_UNDECIDED),
        (VerbOnAnActivePort, EnergySystemErrorId.VERB_ON_INACTIVE_PORT),
        (KnownPort, EnergySystemErrorId.UNKNOWN_PORT),
        (BoundOutputIsRead, EnergySystemErrorId.BOUND_OUTPUT_NOT_READ),
        (PortContract, EnergySystemErrorId.PORT_CONTRACT),
        (CircuitEndsMatch, EnergySystemErrorId.CIRCUIT_MISMATCH),
        (OneProviderPerCarrier, EnergySystemErrorId.CARRIER_PROVIDER),
        (OrderValid, EnergySystemErrorId.ORDER_INVALID),
    )

    #: The rules that refuse what the selection of the observers' feeds recorded during the wiring, with their identifiers.
    SELECTION: ClassVar[Tuple[Tuple[Type[SetupRule], EnergySystemErrorId], ...]] = (
        (ObserverSelection, EnergySystemErrorId.OBSERVER_SELECTION),
        (ActuationSelection, EnergySystemErrorId.ACTUATION),
        (WeightFree, EnergySystemErrorId.WEIGHT_COLLISION),
    )

    @staticmethod
    def refusal(error_id: EnergySystemErrorId) -> ImportRefusal:
        """A refusal of this identifier at the import ``tank``."""
        return ImportRefusal(error_id, "import 'tank', house.energy_system.yaml:9", "the problem.", key_path=("imports", "tank"), owner="tank")

    @staticmethod
    def other_than(error_id: EnergySystemErrorId) -> EnergySystemErrorId:
        """An identifier of the band that is not this one, for the refusal a rule must leave alone."""
        return EnergySystemErrorId.PARAMETER_INVALID if error_id is not EnergySystemErrorId.PARAMETER_INVALID else EnergySystemErrorId.PORT_AMBIGUOUS

    @classmethod
    def expanded(cls, *refusals: ImportRefusal) -> ExpandedImports:
        """The expanded imports of the file, holding these refusals."""
        return ExpandedImports(fft.AssemblyRules.model(), tuple(refusals), cls.FILE)

    @classmethod
    def found(cls, *refusals: ImportRefusal, assemblies: Optional[dict] = None) -> ImportedAssemblies:
        """The assemblies of the file's imports, none found unless given, and these refusals of the lookup."""
        return ImportedAssemblies(fft.AssemblyRules.model(), assemblies or {}, tuple(refusals), cls.FILE)

    @staticmethod
    def built(*refusals: WiringRefusal) -> BuiltDocument:
        """The wired file of no component, holding the refusals of the selection."""
        return fft.WiringRules.subject(WiredSystem(components=(), wires=(), resolved_feeds=(), refusals=tuple(refusals)), name="house")


class Assembly:
    """A valid assembly of a mock tank, and the library a test writes it into, with or without a fault."""

    #: A valid assembly; a test breaks it by replacing a part.
    VALID: ClassVar[str] = f"""\
schema_version: 4
kind: assembly
name: test/base
parameters:
  volume_in_liter: {{type: float, unit: LITER, default: 150, range: {{min: 50, max: 500}}, description: Volume.}}
components:
  Tank:
    class: {Mocks.CLASSES}.MockTank
    preset: standard
    config:
      volume_in_liter: {{$param: volume_in_liter}}
    inputs:
      - {{$port: demand}}
interface:
  needs:
    demand: {{into: [Tank], partner: MockOccupancy}}
tests:
  bounds: [{{output: Tank.HeatLoss, unit: WATT, min: 0}}]
  monotone: [{{parameter: volume_in_liter, kpi: Standby heat losses, member: Tank, direction: increasing}}]
"""

    #: The base assembly without a description and with a unit its field does not have: two problems, one of EF-78.
    BROKEN: ClassVar[str] = VALID.replace(" description: Volume.}", "}").replace("unit: LITER", "unit: WATT")

    @staticmethod
    def resolved(directory: Path, text: str) -> ResolvedAssembly:
        """The assembly ``test/base`` written with this text into a library in ``directory``, resolved."""
        library = Library(directory)
        library.add("test/base", text)
        return AssemblyResolver([library.directory]).resolve("test/base", "test")


class TestRecordedRefusals:
    """Each rule that refuses a recorded refusal: a fault for a refusal of its code, none for one of another code."""

    @pytest.mark.parametrize(("rule", "error_id"), Imports.LOOKUP, ids=[rule.CODE for rule, _ in Imports.LOOKUP])
    def test_a_lookup_rule_refuses_its_refusal_and_leaves_another(self, rule: Type[SetupRule], error_id: EnergySystemErrorId) -> None:
        """Catches a lookup rule refusing nothing, or another rule's refusal."""
        (fault,) = rule().check(Imports.found(Imports.refusal(error_id), Imports.refusal(Imports.other_than(error_id))))

        assert fault.message == f"{error_id.value} at import 'tank', house.energy_system.yaml:9: the problem.; see: hisim setup-rules show {rule.CODE}"
        assert (fault.component, fault.key_path, fault.file) == ("tank", ("imports", "tank"), Imports.FILE)
        assert not rule().check(Imports.found(Imports.refusal(Imports.other_than(error_id))))

    @pytest.mark.parametrize(("rule", "error_id"), Imports.EXPANSION, ids=[rule.CODE for rule, _ in Imports.EXPANSION])
    def test_an_expansion_rule_refuses_its_refusal_and_leaves_another(self, rule: Type[SetupRule], error_id: EnergySystemErrorId) -> None:
        """Catches an expansion rule refusing nothing, or another rule's refusal."""
        (fault,) = rule().check(Imports.expanded(Imports.refusal(error_id), Imports.refusal(Imports.other_than(error_id))))

        assert fault.message.startswith(f"{error_id.value} at import 'tank'") and fault.message.endswith(f"see: hisim setup-rules show {rule.CODE}")
        assert (fault.component, fault.key_path, fault.file) == ("tank", ("imports", "tank"), Imports.FILE)
        assert not rule().check(Imports.expanded(Imports.refusal(Imports.other_than(error_id))))

    @pytest.mark.parametrize(("rule", "error_id"), Imports.SELECTION, ids=[rule.CODE for rule, _ in Imports.SELECTION])
    def test_a_selection_rule_refuses_its_refusal_and_leaves_another(self, rule: Type[SetupRule], error_id: EnergySystemErrorId) -> None:
        """Catches a selection rule refusing nothing, or another rule's refusal."""
        other = EnergySystemErrorId.OBSERVER_SELECTION if error_id is not EnergySystemErrorId.OBSERVER_SELECTION else EnergySystemErrorId.ACTUATION
        (fault,) = rule().check(Imports.built(SelectionPlan.refusal(error_id, "Ems", "the problem."), SelectionPlan.refusal(other, "Ems", "other.")))

        assert fault.message == f"{error_id.value} at components.Ems: the problem.; see: hisim setup-rules show {rule.CODE}"
        assert (fault.component, fault.key_path) == ("Ems", ("components", "Ems"))
        assert not rule().check(Imports.built(SelectionPlan.refusal(other, "Ems", "other.")))

    def test_faults_of_takes_the_refusals_of_the_rules_code_in_recorded_order(self) -> None:
        """Catches ``ImportRule.faults_of`` dropping a refusal's place, or reordering two refusals of one code."""
        first, second = (ImportRefusal(EnergySystemErrorId.PORT_AMBIGUOUS, f"import '{key}'", "two.", key_path=("imports", key), owner=key) for key in ("b", "a"))
        faults = ImportRule.faults_of(PortUnambiguous, [first, Imports.refusal(EnergySystemErrorId.PORT_CONTRACT), second], "f.yaml")

        assert [(fault.component, fault.key_path, fault.file) for fault in faults] == [("b", ("imports", "b"), "f.yaml"), ("a", ("imports", "a"), "f.yaml")]
        assert all(fault.code == "EF-7B" and fault.name == "port-unambiguous" for fault in faults)


class TestLibraryValid:
    """The library check as the rule ``EF-75``: an assembly with problems, one without, and the message."""

    def test_an_assembly_of_the_mock_library_passes(self) -> None:
        """Catches the library check refusing a valid assembly, which would refuse every import of it."""
        assembly = AssemblyResolver([Mocks.LIBRARY]).resolve("mock/wired_tank", "test")

        assert not LibraryValid().check(Imports.found(assemblies={"tank": assembly, "second": assembly}))

    def test_an_assembly_with_problems_is_one_fault_listing_every_problem_once_per_assembly(self, tmp_path: Path) -> None:
        """Catches a problem missing from the fault, the unit problem left out, or an assembly imported twice refused twice."""
        assembly = Assembly.resolved(tmp_path, Assembly.BROKEN)
        faults = LibraryValid().check(Imports.found(assemblies={"tank": assembly, "spare": assembly}))

        assert len(faults) == 1
        fault = faults[0]
        assert fault.message.startswith("EF-75 at test/base.assembly.yaml: the assembly 'test/base' has 2 problems: ")
        assert "no description" in fault.message and "EF-78: the parameter 'volume_in_liter' is in WATT" in fault.message
        assert (fault.component, fault.key_path, fault.file) == ("tank", ("imports", "tank"), Imports.FILE)

    def test_the_message_counts_the_problems(self, tmp_path: Path) -> None:
        """Catches ``LibraryValid.message`` writing "1 problems", or a message other than the expansion raised."""
        assembly = Assembly.resolved(tmp_path, Assembly.VALID)

        assert LibraryValid.message(assembly, ["a: one."]) == "EF-75 at test/base.assembly.yaml: the assembly 'test/base' has 1 problem: a: one."
        assert LibraryValid.message(assembly, ["a: one.", "b: two."]).endswith("has 2 problems: a: one. | b: two.")

    def test_the_checker_lists_nothing_for_a_valid_file_and_every_problem_of_a_broken_one(self, tmp_path: Path) -> None:
        """Catches ``LibraryChecker.check`` stopping at the first problem."""
        assert not LibraryChecker(Assembly.resolved(tmp_path / "valid", Assembly.VALID)).check()
        assert len(LibraryChecker(Assembly.resolved(tmp_path / "broken", Assembly.BROKEN)).check()) == 2


class TestTheExpansionRecordsEveryFault:
    """Inline files expanded against the mock library: every fault of a step at once, as ``EnergySystemImportsError``."""

    TANK: ClassVar[str] = "tank: {assembly: mock/wired_tank"
    HEATER: ClassVar[str] = "heater: {assembly: mock/variant_heater"

    @staticmethod
    def refused(text: str, resolver: Optional[AssemblyResolver] = None) -> EnergySystemImportsError:
        """The error the expansion of an inline file raises, its faults carrying the file name ``inline.energy_system.yaml``."""
        model, lines = read_system(text)
        with pytest.raises(EnergySystemImportsError) as raised:
            expand_imports(model, resolver or AssemblyResolver([Mocks.LIBRARY]), lines=lines, file="inline.energy_system.yaml")
        return raised.value

    def test_every_assembly_an_import_cannot_find_is_refused_at_once(self) -> None:
        """Catches the lookup stopping at the first import whose assembly it cannot find."""
        error = self.refused(site(Mocks.OCCUPANCY, imports="one: {assembly: mock/nowhere}\ntwo: {assembly: mock/elsewhere}"))

        assert [(fault.code, fault.component, fault.key_path, fault.file) for fault in error.faults] == [
            ("EF-71", "one", ("imports", "one"), "inline.energy_system.yaml"),
            ("EF-71", "two", ("imports", "two"), "inline.energy_system.yaml"),
        ]
        assert isinstance(error, EnergySystemAssemblyError) and error.error_id is EnergySystemErrorId.ASSEMBLY_NOT_FOUND
        assert error.location == "imports.one" and "'mock/nowhere' is in none of the library directories" in error.problem

    def test_an_assembly_the_library_check_refuses_is_refused_before_anything_is_selected(self, tmp_path: Path) -> None:
        """Catches an import of a broken assembly selected and bound as if it were valid."""
        library = Library(tmp_path)
        library.add("test/base", Assembly.BROKEN)
        error = self.refused(site(Mocks.OCCUPANCY, imports="tank: {assembly: test/base}"), AssemblyResolver([library.directory]))

        assert [fault.code for fault in error.faults] == ["EF-75"] and "2 problems" in error.faults[0].message

    def test_every_parameter_an_import_breaks_is_refused_and_the_binding_is_not_run(self) -> None:
        """Catches the selection stopping at the first parameter, or binding an import whose parameters it refused."""
        text = site(Mocks.OCCUPANCY, imports=f"{self.TANK}}}\n{self.HEATER}, parameters: {{power_in_watt: 9000, colour: red}}}}")
        error = self.refused(text)

        assert [fault.code for fault in error.faults] == ["EF-76", "EF-76"]
        assert "9000 lies outside the range" in error.faults[0].message and "'colour', which is not a parameter" in error.faults[1].message
        assert all(fault.key_path == ("imports", "heater") and fault.component == "heater" for fault in error.faults)
        assert Skip("EF-7A", ("EF-76",)) in error.skipped

    def test_every_port_the_binder_refuses_is_refused_at_once(self) -> None:
        """Catches the binder stopping at the first port it cannot bind."""
        text = site(Mocks.OCCUPANCY, imports=f"{self.TANK}, none: [hot_water_demand]}}\n{self.HEATER}, bind: {{nonsense: Occupancy}}}}")
        error = self.refused(text)

        assert [(fault.code, fault.component) for fault in error.faults] == [("EF-7C", "tank"), ("EF-7G", "heater")]

    def test_every_repeated_order_number_is_refused(self) -> None:
        """Catches the sequence stopping at the first two blocks that claim one position."""
        text = site(
            Mocks.WEATHER.replace("}", ", order: 1}"),
            Mocks.OCCUPANCY.replace("}", ", order: 1}"),
            imports=f"{self.TANK}, order: 1}}\n{self.HEATER}}}",
        )
        error = self.refused(text)

        assert [(fault.code, fault.component) for fault in error.faults] == [("EF-7P", "Occupancy"), ("EF-7P", "tank")]

    def test_a_file_the_rules_find_nothing_in_expands(self) -> None:
        """Catches a rule refusing a file with nothing wrong in its imports."""
        model, lines = read_system(site(Mocks.OCCUPANCY, imports=f"{self.TANK}}}\n{self.HEATER}}}"))
        flat, _record = expand_imports(model, AssemblyResolver([Mocks.LIBRARY]), lines=lines)

        assert list(flat.components) == ["Occupancy", "tank-Tank", "heater-Heater", "heater-Controller"]


class TestTheSelectionRefusesAlone:
    """A refused selection is reported alone: the wiring rules require the selection rules."""

    def test_an_actuation_fault_is_an_imports_error_and_the_wiring_rules_are_not_run(self) -> None:
        """Catches a refused selection reported again by the wiring rules as an open or doubly fed input."""
        refusal = SelectionPlan.refusal(EnergySystemErrorId.ACTUATION, "battery", "ranked by no controller.")
        findings = SetupRuleRegistry.collect().findings(RuleStage.BUILD, Imports.built(refusal), SetupKind.DEMO)

        assert [fault.code for fault in findings.faults] == ["EF-7U"]
        assert isinstance(findings.violation(), EnergySystemImportsError)
        assert Skip("EF-26", ("EF-7U",)) in findings.skipped and "EF-31" in {skip.code for skip in findings.skipped}

    def test_the_actuation_rule_is_not_run_when_an_observer_was_refused(self) -> None:
        """Catches the controllable outputs judged when a refused observer ranked nothing."""
        refusal = SelectionPlan.refusal(EnergySystemErrorId.OBSERVER_SELECTION, "Ems", "observes nothing.")
        findings = fft.AssemblyRules.findings(Imports.built(refusal))

        assert [fault.code for fault in findings.faults] == ["EF-7S"] and Skip("EF-7U", ("EF-7S",)) in findings.skipped


class TestHelpers:
    """The helpers the expansion gained when its checks moved: each records what it refused, without raising."""

    def test_the_resolver_finds_an_assembly_or_returns_why_not(self, tmp_path: Path) -> None:
        """Catches ``AssemblyResolver.find`` raising, or ``resolve`` no longer raising what ``find`` returns."""
        first, second = Library(tmp_path / "a"), Library(tmp_path / "b")
        first.add("test/base", Assembly.VALID)
        second.add("test/base", Assembly.VALID)
        resolver = AssemblyResolver([first.directory])

        assert isinstance(resolver.find("test/base", "imports.tank"), ResolvedAssembly)
        missing = resolver.find("test/bse", "imports.tank")
        assert isinstance(missing, ImportRefusal) and missing.error_id is EnergySystemErrorId.ASSEMBLY_NOT_FOUND and "Did you mean: test/base?" in missing.message
        malformed = resolver.find("base.assembly.yaml", "imports.tank")
        assert isinstance(malformed, ImportRefusal) and "is not an assembly path" in malformed.message
        doubled = AssemblyResolver([first.directory, second.directory]).find("test/base", "imports.tank")
        assert isinstance(doubled, ImportRefusal) and doubled.error_id is EnergySystemErrorId.ASSEMBLY_FOUND_TWICE
        with pytest.raises(EnergySystemAssemblyError, match="EF-71 at imports.tank"):
            resolver.resolve("test/bse", "imports.tank")

    def test_the_parameter_selection_records_every_value_then_every_constraint(self) -> None:
        """Catches ``resolve_selection`` stopping at the first value, or checking constraints on values it refused."""
        heater = AssemblyResolver([Mocks.LIBRARY]).resolve("mock/variant_heater", "test")
        selection, refusals = resolve_selection(heater.model, heater.label, {"power_in_watt": 9000, "colour": "red"}, "import 'heater'")

        assert selection is None and [refusal.error_id for refusal in refusals] == [EnergySystemErrorId.PARAMETER_INVALID] * 2
        selection, refusals = resolve_selection(heater.model, heater.label, {"power_in_watt": 3000}, "import 'heater'")
        assert selection is not None and not refusals and selection.resolved["power_in_watt"] == 3000
        with pytest.raises(EnergySystemAssemblyError, match="EF-76 at import 'heater': the import sets 'colour'"):
            select(heater.model, heater.label, {"colour": "red"}, "import 'heater'")

    def test_the_sequence_records_every_block_that_repeats_a_number(self) -> None:
        """Catches ``sequence`` raising, or keeping the second of two blocks with one number at that place."""
        written = (("A", 1), ("B", 1), ("C", None), ("D", 1))
        blocks = [Block(f"component '{name}'", f"f.yaml:{line}", order, [], ("components", name)) for line, (name, order) in enumerate(written)]
        units, refusals = sequence(blocks)

        assert units == [] and [refusal.component for refusal in refusals] == ["B", "D"]
        assert refusals[0].message == "EF-7P at f.yaml:1: component 'B' and component 'A' both carry order: 1."
        assert sequence(blocks[:1] + blocks[2:3]) == ([], ())

    def test_a_record_out_of_its_sequence_is_refused_and_one_in_it_is_not(self) -> None:
        """Catches ``recorded_sequence_refusal`` missing a record whose components moved, or refusing a file without a record."""
        text = (
            "schema_version: 3\nname: r\nsetup_kind: demo\ncomponents:\n  A: {{class: a.A, preset: p}}\n  B: {{class: a.B, preset: p}}\n"
            "metadata: {{imports: {{sequence: [{0}], addresses: {{}}}}}}\n"
        )
        refused = recorded_sequence_refusal(parse_energy_system(text.format("B, A")))

        assert refused is not None and refused.error_id is EnergySystemErrorId.ORDER_INVALID and refused.key_path == ("metadata", "imports", "sequence")
        assert recorded_sequence_refusal(parse_energy_system(text.format("A, B"))) is None
        with pytest.raises(EnergySystemImportsError, match="EF-7P at metadata.imports.sequence"):
            expand_imports(parse_energy_system(text.format("B, A")))

    def test_the_judge_refuses_a_refusal_no_rule_reads(self) -> None:
        """Catches an expansion going on from a step whose refusal no assembly rule refused."""
        ImportExpander.judge(Imports.expanded())
        with pytest.raises(RuntimeError, match="no assembly rule refused"):
            ImportExpander.judge(Imports.expanded(Imports.refusal(EnergySystemErrorId.DUPLICATE_NAME)))

    def test_the_binder_records_a_refused_decision_and_returns_the_others(self) -> None:
        """Catches ``PortBinder.attempt`` letting a refusal escape, or dropping a decision's result."""
        binder = PortBinder({}, {}, ImportRecord())

        def refuse() -> int:
            raise PortRefused(Imports.refusal(EnergySystemErrorId.PORT_AMBIGUOUS))

        assert binder.attempt(lambda: 3) == 3 and binder.attempt(refuse) is None
        assert [refusal.error_id for refusal in binder.refusals] == [EnergySystemErrorId.PORT_AMBIGUOUS]

    def test_a_selection_refusal_is_located_at_its_observer(self) -> None:
        """Catches ``SelectionPlan.refusal`` locating a refusal where the source map cannot name its import."""
        refusal = SelectionPlan.refusal(EnergySystemErrorId.OBSERVER_SELECTION, "grid-Meter", "observes nothing.")

        assert refusal.message == "EF-7S at components.grid-Meter: observes nothing."

    @pytest.mark.parametrize(
        ("others", "reached"),
        [(["HEAT_PUMP"], "the base weight of HEAT_PUMP (HeatPump): the two would tie"), ([], "at or above the monitored-only weight 999")],
        ids=["another-type", "monitored-only"],
    )
    def test_a_weight_collision_names_what_the_weight_reached(self, others: Sequence[str], reached: str) -> None:
        """Catches ``SelectionPlan.collision`` naming no participant, or no remedy."""
        feed = FeedRequest(consumer="Ems", source="Battery2", output="AcPower", component_type=lt.ComponentType.BATTERY, flow_tags=(), weight=6)
        pump = FeedRequest(consumer="Ems", source="HeatPump", output="Power", component_type=lt.ComponentType.HEAT_PUMP, flow_tags=(), weight=7)
        refusal = SelectionPlan.collision(Observer("Ems", Selection(), "Ems"), feed, 1, 7, list(others), [feed, pump])

        assert refusal.error_id is EnergySystemErrorId.WEIGHT_COLLISION and refusal.location == "components.Ems"
        assert "participant 2 of BATTERY, at 6 + 1 = 7" in refusal.message and reached in refusal.message and "Pin the weight on the feed" in refusal.message

    def test_a_refusal_becomes_the_error_with_its_message(self) -> None:
        """Catches ``CatalogueRefusal.error`` building an error whose message differs from the refusal's."""
        refusal = CatalogueRefusal(EnergySystemErrorId.ASSEMBLY_NOT_FOUND, "imports.x", "missing.", alternatives=("a/b",), offending_value="a/c")
        error = refusal.error(EnergySystemAssemblyError)

        assert str(error) == refusal.message and error.error_id is EnergySystemErrorId.ASSEMBLY_NOT_FOUND and error.alternatives == ("a/b",)

    def test_the_imports_error_reads_its_identifier_from_the_first_fault(self) -> None:
        """Catches ``EnergySystemImportsError`` without the catalogue attributes a caller of an assembly error reads."""
        faults = [PortUnambiguous.fault("EF-7B at import 'a', f.yaml:3: two candidates."), PortContract.fault("EF-7J at import 'b': no member.")]
        error = EnergySystemImportsError(RuleStage.BUILD, SetupKind.DEMO, faults, ())

        assert (error.error_id, error.location, error.problem) == (EnergySystemErrorId.PORT_AMBIGUOUS, "import 'a', f.yaml:3", "two candidates.")
        assert not error.alternatives and "2 faults" in str(error)
