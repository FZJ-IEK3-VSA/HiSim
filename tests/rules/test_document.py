"""Direct tests of the structural rules (``hisim/setup_rules/document.py``): each finds its fault and accepts a good document.

Each rule is run on the smallest document that breaks it and on the skeleton that does not; the helpers of the
family (the walks, the path scan, the spelling of an input item) are tested on their own. The tests of the hooks show
which document the rules see: the file as read when it is loaded, the expanded file in a run.
"""

from typing import ClassVar, List, Optional, Type

import pytest

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError, EnergySystemStructureError
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.energy_system.loader import load_energy_system, parse_energy_system
from hisim.setup_rules.document import (
    ConfigurationStated,
    DocumentRule,
    KnownSizingSource,
    KnownSource,
    NoAbsolutePath,
    NoDuplicateFeed,
    NoDuplicateWire,
    NoMetadataOnAPlainRun,
    NoNameCollision,
    OneConfigurationOrigin,
    OneGroupPerComponent,
    OneInputSpelling,
    OneVariantPerComponent,
    SizingSourceNamesItsFact,
    ValidName,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import Fault, RuleStage, SetupRuleViolation
from hisim.setup_rules.subjects import LoadedDocument
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.helpers import Mocks, both_libraries
from tests.functions_for_testing import DocumentRules

pytestmark = pytest.mark.base


class Texts:
    """The documents the tests break: a valid skeleton, and the blocks a test appends to it or swaps into it."""

    #: Two components, a source with nothing to say and a consumer taking its defaults; every rule accepts it.
    SKELETON: ClassVar[str] = """\
schema_version: 3
name: rule under test
setup_kind: demo
components:
  weather:
    class: hisim.components.weather.Weather
    preset: aachen
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    inputs:
      - weather
"""

    #: The consumer's input item in the skeleton, for a test to replace with its own items.
    INPUTS: ClassVar[str] = "    inputs:\n      - weather\n"

    #: A component class for added entries; the structural rules never import it.
    CLASS: ClassVar[str] = "hisim.components.generic_pv_system.PVSystem"

    @classmethod
    def with_inputs(cls, *items: str) -> str:
        """The skeleton with the consumer's input items replaced, each one YAML flow item."""
        return cls.SKELETON.replace(cls.INPUTS, "    inputs:\n" + "".join(f"      - {item}\n" for item in items))

    @classmethod
    def with_extra(cls, extra: str) -> str:
        """The skeleton with more top-level YAML appended."""
        return cls.SKELETON + extra

    @classmethod
    def group(cls, name: str, *members: str, enabled: bool = True) -> str:
        """A group block of preset entries, to append under a ``groups:`` key."""
        body = "".join(f"      {member}: {{class: {cls.CLASS}, preset: rooftop}}\n" for member in members)
        return f"  {name}:\n    enabled: {str(enabled).lower()}\n    components:\n{body}"

    @classmethod
    def variant(cls, name: str, options: str, selected: str) -> str:
        """A variant block whose options each list the comma-separated members, to append under a ``variants:`` key.

        Example: ``variant("heating", "gas=controller;pump=controller", "gas")``.
        """
        body = ""
        for option in options.split(";"):
            option_name, members = option.split("=")
            entries = "".join(f"          {member}: {{class: {cls.CLASS}, preset: rooftop}}\n" for member in members.split(",") if member)
            body += f"      {option_name}:\n        components:\n{entries or '          {}' + chr(10)}"
        return f"  {name}:\n    selected: {selected}\n    options:\n{body}"


def check(rule: Type[DocumentRule], text: str, rerun: Optional[bool] = None) -> List[Fault]:
    """The faults one rule finds in a document written as YAML text."""
    return list(rule().check(DocumentRules.subject(text, rerun)))


class TestNoAbsolutePath:
    """``EF-05`` and its walk, which the recorder shares."""

    def test_an_absolute_path_under_a_path_key_is_a_fault(self) -> None:
        """Catches a file that resolves on one machine only, in a config and in a constructor block."""
        text = Texts.SKELETON.replace(
            "    preset: aachen\n", "    config:\n      source_path: /home/me/weather\n      nested: {paths: ['C:\\\\x', '${inputs}/y']}\n"
        )
        faults = check(NoAbsolutePath, text)

        assert [fault.key_path for fault in faults] == [
            ("components", "weather", "config", "source_path"),
            ("components", "weather", "config", "nested", "paths", 0),
        ]
        assert faults[0].message.startswith("EF-05 at components.weather.config.source_path: '/home/me/weather' is an absolute filesystem path.")
        assert faults[0].component == "weather"

    def test_the_skeleton_has_no_absolute_path(self) -> None:
        """Catches the scan refusing a portable file."""
        assert not check(NoAbsolutePath, Texts.SKELETON)

    def test_the_walk_finds_every_absolute_path_and_ignores_other_keys(self) -> None:
        """Catches the walk stopping at the first path, or reading a key that names no location."""
        block = {"source_path": "/a", "description": "/not/a/path", "inner": [{"file": "\\\\server\\x"}], "paths": ["/b", "${inputs}/c"]}

        refusals = NoAbsolutePath.absolute_paths(block, "components.w.config")

        assert [refusal.location for refusal in refusals] == [
            "components.w.config.source_path",
            "components.w.config.inner[0].file",
            "components.w.config.paths[0]",
        ]

    def test_a_path_is_absolute_only_under_a_path_valued_key(self) -> None:
        """Catches the key rule or one of the three absolute spellings being lost."""
        assert NoAbsolutePath.is_absolute_path("source_path", "/x")
        assert NoAbsolutePath.is_absolute_path("file", "C:\\data")
        assert NoAbsolutePath.is_absolute_path("directory", "\\\\share")
        assert not NoAbsolutePath.is_absolute_path("description", "/x")
        assert not NoAbsolutePath.is_absolute_path("file", "${inputs}/x")
        assert not NoAbsolutePath.is_absolute_path("file", "")

    def test_the_refusal_shows_the_symbolic_form(self) -> None:
        """Catches the remedy, the part that says what to write instead, being dropped."""
        refusal = NoAbsolutePath.absolute_path_error("components.w.config.file", "/x")

        assert refusal.error_id is EnergySystemErrorId.ABSOLUTE_PATH
        assert "${inputs}/weather/berlin" in str(refusal)


class TestValidName:
    """``EF-08``: a hyphenated name only from the expansion of imports."""

    def test_a_hyphenated_name_no_expansion_produced_is_a_fault(self) -> None:
        """Catches an authored name with the address separator, which the reader admits only from an address table."""
        subject = DocumentRules.subject(Texts.SKELETON)
        components = dict(subject.model.components)
        components["pv-east"] = components.pop("weather").model_copy(update={"name": "pv-east"})
        faults = ValidName().check(LoadedDocument(model=subject.model.model_copy(update={"components": components}), document={}))

        assert [fault.component for fault in faults] == ["pv-east"]
        assert "only the expansion of imports names a component" in faults[0].message

    def test_the_names_an_expansion_produced_are_valid(self) -> None:
        """Catches the rule refusing the members an import produced, which the address table lists."""
        expanded, _expansion, imports = Executors.mock_house().expand()

        assert any("-" in name for name in expanded.components)
        assert not ValidName().check(LoadedDocument(model=expanded, document={}, source_map=imports.source_map))


class TestNoMetadataOnAPlainRun:
    """``EF-09``: a run record is only re-run."""

    METADATA: ClassVar[str] = "metadata:\n  generator: a test\n"

    def test_a_plain_run_of_a_record_is_a_fault(self) -> None:
        """Catches a generated record run as if it were authored."""
        faults = check(NoMetadataOnAPlainRun, Texts.with_extra(self.METADATA), rerun=False)

        assert len(faults) == 1 and faults[0].key_path == ("metadata",)
        assert faults[0].message.startswith("EF-09 at <text>: this file carries a 'metadata' block")

    def test_a_rerun_and_a_load_are_no_fault(self) -> None:
        """Catches the rule refusing a declared re-run, a file only loaded, or a file without metadata."""
        assert not check(NoMetadataOnAPlainRun, Texts.with_extra(self.METADATA), rerun=True)
        assert not check(NoMetadataOnAPlainRun, Texts.with_extra(self.METADATA), rerun=None)
        assert not check(NoMetadataOnAPlainRun, Texts.SKELETON, rerun=False)


class TestConfigurationOrigin:
    """``EF-11`` and ``EF-12``: one origin per entry, and some origin."""

    def test_a_preset_and_a_constructor_is_a_fault(self) -> None:
        """Catches an entry that leaves open which of two configurations runs."""
        text = Texts.SKELETON.replace("    preset: aachen\n", "    preset: aachen\n    constructor:\n      for_location: {location: AACHEN}\n")
        faults = check(OneConfigurationOrigin, text)

        assert [fault.component for fault in faults] == ["weather"]
        assert "names both preset 'aachen' and constructor 'for_location'" in faults[0].message
        assert not check(OneConfigurationOrigin, Texts.SKELETON)

    def test_an_entry_with_no_origin_is_a_fault(self) -> None:
        """Catches an entry that says nothing about how it is configured."""
        faults = check(ConfigurationStated, Texts.SKELETON.replace("    preset: aachen\n", ""))

        assert [fault.component for fault in faults] == ["weather"]
        assert faults[0].message.startswith("EF-12 at components.weather: 'weather' says nothing about how it is configured.")
        assert not check(ConfigurationStated, Texts.SKELETON)


class TestReferences:
    """``EF-20``, ``EF-40`` and ``EF-41``: every reference resolves, and a sizing line names its own fact."""

    def test_an_input_from_an_undeclared_component_is_a_fault(self) -> None:
        """Catches a misspelled source, listing the declared components."""
        faults = check(KnownSource, Texts.with_inputs("weathr", "wether"))

        assert [fault.key_path for fault in faults] == [("components", "boiler", "inputs", 0), ("components", "boiler", "inputs", 1)]
        assert "Did you mean: weather?" in faults[0].message
        assert not check(KnownSource, Texts.SKELETON)

    def test_a_sizing_source_naming_no_component_is_a_fault(self) -> None:
        """Catches a dangling provider."""
        text = Texts.SKELETON + "    sizing_sources:\n      pv_peak_power_in_watt: pv.pv_peak_power_in_watt\n"
        faults = check(KnownSizingSource, text)

        assert [fault.key_path for fault in faults] == [("components", "boiler", "sizing_sources", "pv_peak_power_in_watt")]
        assert not check(KnownSizingSource, text.replace("pv.", "weather."))

    def test_a_sizing_source_renaming_its_fact_is_a_fault(self) -> None:
        """Catches a source line that answers for another fact than its key."""
        text = Texts.SKELETON + "    sizing_sources:\n      pv_peak_power_in_watt: weather.roof_area_in_m2\n"
        faults = check(SizingSourceNamesItsFact, text)

        assert "which names the fact 'roof_area_in_m2'" in faults[0].message
        assert not check(SizingSourceNamesItsFact, text.replace("roof_area_in_m2", "pv_peak_power_in_watt"))


class TestInputShapes:
    """``EF-24``, ``EF-2C`` and ``EF-2D``: the input items of one consumer agree."""

    WIRE: ClassVar[str] = "{input: TemperatureOutside, from: weather.TemperatureOutside}"
    FEED: ClassVar[str] = "{from: weather.TemperatureOutside, tags: [electricity], weight: 1}"

    def test_a_wire_and_a_feed_from_one_source_is_a_fault(self) -> None:
        """Catches one relationship written twice in two spellings, and a repeated defaults item."""
        faults = check(OneInputSpelling, Texts.with_inputs(self.WIRE, self.FEED, "weather", "weather"))

        assert [fault.key_path[-1] for fault in faults] == [1, 3]
        assert "'boiler' already declares wires from 'weather'." in faults[0].message
        assert not check(OneInputSpelling, Texts.with_inputs("weather", self.WIRE))

    def test_a_feed_written_twice_is_a_fault_with_the_catalogue_code(self) -> None:
        """Catches a source output fed twice, its message under the rule's own code."""
        faults = check(NoDuplicateFeed, Texts.with_inputs(self.FEED, self.FEED))

        assert faults[0].code == "EF-2C" and faults[0].message.startswith("EF-2C at components.boiler.inputs[1]: 'boiler' is fed twice")
        assert not check(NoDuplicateFeed, Texts.with_inputs(self.FEED))

    def test_a_wire_written_twice_is_a_fault_with_the_catalogue_code(self) -> None:
        """Catches an input wired twice, which would leave the winner to list order."""
        faults = check(NoDuplicateWire, Texts.with_inputs(self.WIRE, self.WIRE))

        assert faults[0].code == "EF-2D" and faults[0].message.startswith("EF-2D at components.boiler.inputs[1]: 'boiler' wires its input 'TemperatureOutside' twice")
        assert not check(NoDuplicateWire, Texts.with_inputs(self.WIRE))

    def test_the_spelling_of_an_item_and_the_clashes_between_spellings(self) -> None:
        """Catches the exclusion of wires and feeds, or the single defaults item, being lost."""
        items = parse_energy_system(Texts.with_inputs("weather", self.WIRE)).components["boiler"].inputs

        assert [OneInputSpelling.spelling_of(item) for item in items] == ["defaults", "wires"]
        assert OneInputSpelling.spelling_of("not an item") is None
        assert OneInputSpelling.clashes({"wires"}, "feeds") and OneInputSpelling.clashes({"feeds"}, "wires")
        assert OneInputSpelling.clashes({"defaults"}, "defaults")
        assert not OneInputSpelling.clashes({"defaults"}, "wires") and not OneInputSpelling.clashes({"wires"}, "wires")


class TestNames:
    """``EF-51``, ``EF-52`` and ``EF-57``: names, groups and variants."""

    def test_a_component_in_two_groups_is_a_fault(self) -> None:
        """Catches a component switched by two flags."""
        text = Texts.with_extra("groups:\n" + Texts.group("solar", "pv") + Texts.group("roof", "pv"))
        faults = check(OneGroupPerComponent, text)

        assert [fault.key_path for fault in faults] == [("groups", "roof", "components", "pv")]
        assert "already listed in group 'solar'" in faults[0].message
        assert not check(OneGroupPerComponent, Texts.with_extra("groups:\n" + Texts.group("solar", "pv")))

    def test_every_name_collision_is_a_fault(self) -> None:
        """Catches a grouped name equal to an ungrouped one, a group or variant named as a component, an option overriding one."""
        text = Texts.with_extra(
            "groups:\n" + Texts.group("weather", "boiler") + "variants:\n" + Texts.variant("boiler", "a=weather", "a")
        )
        messages = [fault.message for fault in check(NoNameCollision, text)]

        assert any("component 'boiler' has the same name as an ungrouped component" in message for message in messages)
        assert any("group 'weather' has the same name as a component" in message for message in messages)
        assert any("variant 'boiler' has the same name as a component" in message for message in messages)
        assert any("component 'weather' has the same name as an ungrouped component" in message for message in messages)

    def test_a_name_repeated_across_the_options_of_one_variant_is_no_collision(self) -> None:
        """Catches the rule refusing the point of a variant: two worlds wiring one component differently."""
        text = Texts.with_extra("variants:\n" + Texts.variant("heating", "gas=controller;pump=controller", "gas"))

        assert not check(NoNameCollision, text)
        assert not check(OneVariantPerComponent, text)

    def test_a_component_in_two_variants_is_a_fault(self) -> None:
        """Catches two exclusive choices both deciding one component."""
        text = Texts.with_extra("variants:\n" + Texts.variant("heating", "gas=controller", "gas") + Texts.variant("storage", "big=controller", "big"))
        faults = check(OneVariantPerComponent, text)

        assert [fault.component for fault in faults] == ["controller"]
        assert "already declared by the variant 'heating'" in faults[0].message
        assert not check(NoNameCollision, text)

    def test_the_walks_report_each_member_with_its_previous_owner(self) -> None:
        """Catches the walks the three rules share losing the order of the blocks or the claim of a name."""
        model = parse_energy_system(
            Texts.with_extra(
                "groups:\n" + Texts.group("solar", "pv", "weather") + "variants:\n" + Texts.variant("heating", "gas=controller;pump=controller,pv", "gas")
            )
        )

        assert DocumentRule.group_memberships(model) == [("solar", "pv", None), ("solar", "weather", "components")]
        assert DocumentRule.option_memberships(model) == [
            ("heating", "gas", "controller", None),
            ("heating", "pump", "controller", "heating"),
            ("heating", "pump", "pv", "solar"),
        ]

    def test_a_location_becomes_a_key_path(self) -> None:
        """Catches an index or a key lost between the message's location and the fault's key path."""
        assert DocumentRule.key_path_of("components.boiler.inputs[0].from") == ("components", "boiler", "inputs", 0, "from")
        assert DocumentRule.key_path_of("groups.solar.components.pv") == ("groups", "solar", "components", "pv")
        assert DocumentRule.key_path_of("components.w.config.grid[1][2]") == ("components", "w", "config", "grid", 1, 2)


class Executors:
    """Executors of the files the tests of the hooks build, against the mock and the real libraries of assemblies."""

    @staticmethod
    def mock_house() -> EnergySystemExecutor:
        """The executor of the mock house, a version-4 file that imports assemblies; expanded, never built."""
        return EnergySystemExecutor(
            parse_energy_system(Mocks.HOUSE),
            SimulationParameters.one_day_only(2021),
            assembly_resolver=both_libraries(),
            source_energy_system=str(Mocks.HOUSE),
        )


class TestTheRefusal:
    """The violation the registry raises for the structural rules, and how a fault names its source."""

    def test_every_fault_of_a_document_is_raised_at_once_as_a_format_error(self) -> None:
        """Catches the structural rules stopping at the first fault, as the validator did, or losing the format error."""
        text = Texts.with_inputs("weathr").replace("    preset: aachen\n", "")
        with pytest.raises(EnergySystemFormatError) as caught:
            load_energy_system(text)

        error = caught.value
        assert isinstance(error, EnergySystemStructureError) and isinstance(error, SetupRuleViolation)
        assert [fault.code for fault in error.faults] == ["EF-12", "EF-20"]
        assert error.error_id is EnergySystemErrorId.NO_CONFIGURATION_SOURCE and error.location == "components.weather"
        assert str(error).count("see: hisim setup-rules show") == 2

    def test_the_catalogue_parts_of_a_fault_message(self) -> None:
        """Catches the identifier, location or problem of a refusal read wrongly from its fault."""
        parts = EnergySystemStructureError.catalogue_parts("EF-20 at components.b.inputs[0]: it is wrong. Valid x: a.; see: hisim setup-rules show EF-20")

        assert parts == (EnergySystemErrorId.UNKNOWN_SOURCE, "components.b.inputs[0]", "it is wrong. Valid x: a.")
        with pytest.raises(ValueError, match="not a message of the energy-system catalogue"):
            EnergySystemStructureError.catalogue_parts("no catalogue message")

    def test_a_fault_at_an_imported_component_names_its_source(self) -> None:
        """Catches the source of a component an import produced missing from a refusal, as the validator's annotation gave it."""
        executor = Executors.mock_house()
        expanded, _expansion, imports = executor.expand()
        subject = executor.loaded_document(expanded, imports)
        refusal = EnergySystemFormatError(EnergySystemErrorId.UNKNOWN_SOURCE, "components.pv-west-PVSystem.inputs[0]", "it is wrong.")

        fault = KnownSource.refuse(subject, refusal, "pv-west-PVSystem")

        assert "[source: pv-west-PVSystem (import pv, instance west" in fault.message
        assert fault.file == str(Mocks.HOUSE) and fault.key_path == ("components", "pv-west-PVSystem", "inputs", 0)
        assert imports.source_map.suffix("components.weather") == "" and imports.source_map.suffix("groups.x") == ""


class TestWhichDocument:
    """The hooks: a loaded file is checked as read, a run checks the file it builds."""

    DISABLED: ClassVar[str] = (
        "groups:\n  solar:\n    enabled: false\n    components:\n"
        f"      pv: {{class: {Texts.CLASS}, preset: rooftop, constructor: {{sized_to: {{power: 1}}}}}}\n"
    )

    def test_a_loaded_file_is_checked_over_its_declared_world(self) -> None:
        """Catches the loader skipping a switched-off group: tomorrow's file enables it."""
        with pytest.raises(EnergySystemStructureError, match="EF-11 at components.pv"):
            load_energy_system(Texts.with_extra(self.DISABLED))

    def test_a_run_checks_the_expanded_file_it_builds(self) -> None:
        """Catches a run refusing what its expansion dropped, which the validator after the expansion never saw."""
        model = parse_energy_system(Texts.with_extra(self.DISABLED))
        executor = EnergySystemExecutor(model, SimulationParameters.one_day_only(2021), rerun=False)
        expanded, _expansion, imports = executor.expand()
        subject = executor.loaded_document(expanded, imports)

        assert "pv" not in subject.model.declared_components() and subject.rerun is False
        assert not DocumentRules.registry().findings(RuleStage.LOAD, subject, SetupKind.DEMO).faults

    def test_a_run_checks_the_members_its_imports_produced(self) -> None:
        """Catches a run checking a file that imports assemblies before its members exist."""
        executor = Executors.mock_house()
        expanded, _expansion, imports = executor.expand()
        subject = executor.loaded_document(expanded, imports)

        assert subject.model is expanded and subject.source_map is imports.source_map and subject.file == str(Mocks.HOUSE)
        assert not DocumentRules.registry().findings(RuleStage.LOAD, subject, expanded.setup_kind).faults

    def test_the_family_finds_nothing_in_the_skeleton(self) -> None:
        """Catches a rule of the family refusing the smallest valid file."""
        assert not DocumentRules.findings(Texts.SKELETON).faults
