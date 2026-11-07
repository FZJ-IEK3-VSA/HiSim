"""The inventory of every check HiSim applies to a setup: where each lives today, what it is, and where it moves.

The inventory is the one list of the checks, the 114 that move into the rules package and the 88 that stay where
they are: a parse fault of the reader, a check the reader makes while it builds the model, a device's own physics
guard, a call of the component API refused when it is made, a recorder check, a CI gate, a policy of the run, and
one retired check that never fired. Each record names
the check, its nature, when it runs, whether it refuses or logs, where it lives today and, for a check that moves,
the module of ``hisim/setup_rules/`` it moves to and its stage. When a family moves, its
pull request sets each moved record's ``lives`` to its place in the package and its ``codes`` to the codes it
allocated; ``tests/rules/test_inventory.py`` holds the registry and the records in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Optional, Tuple

from hisim.setup_rules.rule import RuleStage


class RuleNature(str, Enum):
    """What kind of statement a check makes, which tells a reviewer what a waiver of it would give up.

    The nature is descriptive: any rule may be waived for a kind, and the nature enforces nothing. The values are
    the letters of the inventory's tables.
    """

    #: A law of the model or a consistency the code relies on: energy is conserved, a loop closes, no result is NaN.
    LAW = "L"
    #: A domain rule about the whole system: room heat needs a receiver, a bought carrier passes a meter.
    DOMAIN = "D"
    #: A domain rule about a building, which has no subject in a plant.
    BUILDING = "B"
    #: An assumption that holds only for a household.
    RESIDENTIAL = "R"
    #: A parse fault, met before there is a document to check; it stays in the reader.
    PARSE = "P"
    #: A device's own physics guard or a class's declaration; it stays in the component.
    COMPONENT = "K"
    #: A CI gate or a contract of a test harness.
    GATE = "C"


@dataclass(frozen=True)
class RuleRecord:
    """One check HiSim applies to a setup, as the inventory records it.

    ``rule_id`` is ``<family>.<snake_name>``: for a check that moves, the family is its target module and the
    snake name is its rule's ``NAME`` with underscores (``energy_ports.room_heat_needs_receiver`` for
    ``room-heat-needs-receiver``); a check that stays is filed under ``reader``, ``run``, ``costs``, ``recorder``,
    ``house`` or ``gate``. ``codes`` are the error codes the check carries today, empty where it has none yet.
    ``checks`` says what it checks, ``lives`` where it lives (a path with ``Class.method``; ``hisim/setup_rules/...``
    once moved), ``when`` when it runs and ``enforcement`` whether it refuses (``E``), only logs (``L``) or either.
    ``target`` is the module of the rules package it moves to and ``stage`` the stage it runs at there; both are
    ``None`` for a check that stays. ``note`` holds what the inventory adds: a qualification of the nature, the
    scope a gate gets, or why a check stays.
    """

    rule_id: str
    nature: RuleNature
    when: str
    enforcement: str
    checks: str
    lives: str
    codes: Tuple[str, ...] = ()
    target: Optional[str] = None
    stage: Optional[RuleStage] = None
    note: str = ""

    #: Where a moved check lives: a module of the rules package.
    PACKAGE_PATH: ClassVar[str] = "hisim/setup_rules/"

    @property
    def family(self) -> str:
        """The part of the id before the dot: the target module of a moved check, or the group of one that stays."""
        return self.rule_id.partition(".")[0]

    @property
    def in_package(self) -> bool:
        """Whether the check already lives in the rules package, as a rule the registry collects."""
        return self.lives.startswith(self.PACKAGE_PATH)

    @property
    def moves(self) -> bool:
        """Whether the check moves into the rules package, or has moved; a check with no target stays where it is."""
        return self.target is not None


class Inventory:
    """Every check of a setup HiSim applies, in the order of the reader, the build, the run, the recorder, the house and CI.

    The records of the spec of the setup kinds, section 6, kept here as the one list; the spec cites this file as
    its source.
    """

    RECORDS: ClassVar[Tuple[RuleRecord, ...]] = (
        RuleRecord(
            rule_id="reader.ef_00", codes=("EF-00",), nature=RuleNature.PARSE, when="load", enforcement="E", checks="the file suffix is .yaml/.yml",
            lives="hisim/energy_system/document.py:RawDocument._read_text; hisim/energy_system/executor.py:SimulationParametersReader.read",
        ),
        RuleRecord(
            rule_id="reader.ef_01", codes=("EF-01",), nature=RuleNature.PARSE, when="load", enforcement="E",
            checks="schema_version present and in (3, 4); a v3 file has no imports; an assembly file is v4",
            lives="hisim/energy_system/loader.py:EnergySystemReader._check_schema_version; hisim/energy_system/assemblies/reader.py:AssemblyReader.build",
        ),
        RuleRecord(
            rule_id="reader.ef_02", codes=("EF-02",), nature=RuleNature.PARSE, when="load", enforcement="E", checks="no YAML key written twice",
            lives="hisim/energy_system/document.py:RawDocument._reject_duplicate_keys",
        ),
        RuleRecord(
            rule_id="reader.ef_03", codes=("EF-03",), nature=RuleNature.PARSE, when="load", enforcement="E",
            checks="readable YAML, a mapping, no unknown top-level key, some components",
            lives="hisim/energy_system/document.py:RawDocument._parse; hisim/energy_system/loader.py:EnergySystemReader._build", note="a missing setup_kind joins it",
        ),
        RuleRecord(
            rule_id="reader.ef_07", codes=("EF-07",), nature=RuleNature.PARSE, when="load / build", enforcement="E",
            checks="a block has the YAML type expected; a simulation-parameters file, address table or consuming block is well-formed",
            lives=(
                "hisim/energy_system/document.py:RawDocument.malformed; hisim/energy_system/address_table.py; "
                "hisim/energy_system/executor.py:SimulationParametersReader"
            ),
        ),
        RuleRecord(
            rule_id="document.no_absolute_path", codes=("EF-05",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded, again when recorded",
            enforcement="E", checks="no absolute filesystem path in a value",
            lives="hisim/setup_rules/document.py:NoAbsolutePath",
            note="the recorder runs the same walk, NoAbsolutePath.absolute_paths, in hisim/energy_system/recording/builder.py:PortablePathGuard",
        ),
        RuleRecord(
            rule_id="document.reference_syntax", codes=("EF-06",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="no wildcard or relative reference",
            lives="hisim/energy_system/names.py:NameRules._reference_error",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.valid_name", codes=("EF-08",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="a component, group, variant or option name is an identifier",
            lives="hisim/setup_rules/document.py:ValidName",
            note=(
                "the rule refuses a hyphenated name the expansion did not produce; the identifier check of every name as it is read, "
                "hisim/energy_system/names.py:NameRules.check_identifier, stays in the reader: the reader decides it while it builds the model, which cannot "
                "hold the fault; a rule could only see the file once the reader built it (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.no_metadata_on_a_plain_run", codes=("EF-09",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load, in a run on the expanded file",
            enforcement="E", checks="a file with metadata: (a run record) is only re-run",
            lives="hisim/setup_rules/document.py:NoMetadataOnAPlainRun",
            note=(
                "moved from hisim/energy_system/executor.py:EnergySystemExecutor.check_metadata; a LOAD rule, since it reads the document and whether the run"
                " is a re-run (LoadedDocument.rerun), not the built components"
            ),
        ),
        RuleRecord(
            rule_id="document.one_configuration_origin", codes=("EF-11",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="an entry names a preset or a constructor, never both",
            lives="hisim/setup_rules/document.py:OneConfigurationOrigin",
            note="split in PR 7 from the record of EF-11 and EF-12, one rule per code",
        ),
        RuleRecord(
            rule_id="document.configuration_stated", codes=("EF-12",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="an entry names at least one of preset, constructor, config",
            lives="hisim/setup_rules/document.py:ConfigurationStated",
            note="split in PR 7 from the record of EF-11 and EF-12, one rule per code",
        ),
        RuleRecord(
            rule_id="document.constructor_block", codes=("EF-14",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="a constructor block names exactly one constructor",
            lives="hisim/energy_system/entries.py:EntryReader._constructor",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.known_entry_keys", codes=("EF-18",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="an entry, group, variant or option uses only its closed key set",
            lives="hisim/energy_system/entries.py:EntryReader.entry; hisim/energy_system/loader.py:EnergySystemReader._check_block_keys",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7); the entry reader also reads the members of an assembly file"
            ),
        ),
        RuleRecord(
            rule_id="document.input_item_shape", codes=("EF-19",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="an input item is a defaults, wire or feed item",
            lives="hisim/energy_system/entries.py:EntryReader._check_item_keys",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.known_source", codes=("EF-20",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="an input item names a component the file declares",
            lives="hisim/setup_rules/document.py:KnownSource",
        ),
        RuleRecord(
            rule_id="document.one_input_spelling", codes=("EF-24",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="one consumer-source pair is spelled as wires or as feeds, not both; no defaults item repeated",
            lives="hisim/setup_rules/document.py:OneInputSpelling",
        ),
        RuleRecord(
            rule_id="document.no_duplicate_feed", codes=("EF-2C",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="one source output fed once into an aggregator (file level)",
            lives="hisim/setup_rules/document.py:NoDuplicateFeed",
            note=(
                "the validator raised it as EF-25; PR 7 gave it the catalogue code EF-2C, since the wiring rule wiring.feed_unique (EF-25) "
                "checks something else, the participant outputs an aggregator measures after its feeds are resolved"
            ),
        ),
        RuleRecord(
            rule_id="document.no_duplicate_wire", codes=("EF-2D",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="one input wired once (file level)",
            lives="hisim/setup_rules/document.py:NoDuplicateWire",
            note=(
                "the validator raised it as EF-26; PR 7 gave it the catalogue code EF-2D, since the wiring rule wiring.no_input_fed_twice (EF-26) "
                "checks something else, every wire the build plans, those a bare defaults item adds included"
            ),
        ),
        RuleRecord(
            rule_id="document.known_sizing_source", codes=("EF-40",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="sizing_sources names an existing component",
            lives="hisim/setup_rules/document.py:KnownSizingSource",
            note="split in PR 7 from the record of EF-40 and EF-41, one rule per code",
        ),
        RuleRecord(
            rule_id="document.sizing_source_names_its_fact", codes=("EF-41",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="a sizing_sources reference names the fact it is written under",
            lives="hisim/setup_rules/document.py:SizingSourceNamesItsFact",
            note="split in PR 7 from the record of EF-40 and EF-41, one rule per code",
        ),
        RuleRecord(
            rule_id="document.no_nested_group", codes=("EF-50",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="an entry carries no group keys",
            lives="hisim/energy_system/entries.py:EntryReader.entry",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.one_group_per_component", codes=("EF-51",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="a component is in at most one group",
            lives="hisim/setup_rules/document.py:OneGroupPerComponent",
        ),
        RuleRecord(
            rule_id="document.no_name_collision", codes=("EF-52",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="component, group, variant and expanded import names do not collide",
            lives="hisim/setup_rules/document.py:NoNameCollision",
            note=(
                "named document.unique_name in the design record, renamed in PR 7 since components.unique_name (CB-02) holds the name; "
                "the collision of two expanded import names stays with the expansion, "
                "hisim/energy_system/assemblies/expansion.py:ImportExpander._check_names, which cannot build the expanded file it would hand the rule"
            ),
        ),
        RuleRecord(
            rule_id="document.group_enabled_flag", codes=("EF-53",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="a group's enabled is a bool",
            lives="hisim/energy_system/loader.py:EnergySystemReader._build_groups",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7)"
            ),
        ),
        RuleRecord(
            rule_id="document.group_not_empty", codes=("EF-54",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="a group lists a component",
            lives="hisim/energy_system/loader.py:EnergySystemReader._build_groups",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7); a run checks the expanded file, which keeps no group, so a rule would see the group only"
                " in the raw document"
            ),
        ),
        RuleRecord(
            rule_id="document.known_variant_option", codes=("EF-55",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="selected names an option",
            lives="hisim/energy_system/loader.py:EnergySystemReader._build_variants",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7); the expansion of a run cannot resolve a selection naming no option, so a rule after it "
                "would come too late"
            ),
        ),
        RuleRecord(
            rule_id="document.variant_not_empty", codes=("EF-56",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="a variant offers an option",
            lives="hisim/energy_system/loader.py:EnergySystemReader._build_options",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7); the expansion of a run cannot resolve a variant without options, so a rule after it "
                "would come too late"
            ),
        ),
        RuleRecord(
            rule_id="document.one_variant_per_component", codes=("EF-57",), nature=RuleNature.LAW, target="document", stage=RuleStage.LOAD,
            when="load; in a run on the file after its imports and groups were expanded",
            enforcement="E", checks="a component is declared by at most one variant",
            lives="hisim/setup_rules/document.py:OneVariantPerComponent",
        ),
        RuleRecord(
            rule_id="document.not_cut_from_v1", codes=("EF-73",), nature=RuleNature.LAW, when="load", enforcement="E",
            checks="a construct cut from v1 is refused by name",
            lives="hisim/energy_system/imports_reader.py:CutConstructs.error",
            note=(
                "stays: the reader decides it while it builds the model, which cannot hold the fault; a rule could only see the file once the reader built it"
                " (D9's reason for a parse fault, decided for PR 7); the imports reader and the assembly reader refuse the same constructs"
            ),
        ),
        RuleRecord(
            rule_id="bindings.class_importable", codes=("EF-10",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the dotted class path imports a HiSim component with an annotated config",
            lives="hisim/setup_rules/bindings.py:ClassImportable",
            note="the refusals are recorded by hisim/energy_system/classes.py:ClassBinder.resolve_classes",
        ),
        RuleRecord(
            rule_id="bindings.known_preset", codes=("EF-13",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the preset is declared by the config class", lives="hisim/setup_rules/bindings.py:KnownPreset",
        ),
        RuleRecord(
            rule_id="bindings.known_constructor", codes=("EF-15",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the constructor is declared by the config class", lives="hisim/setup_rules/bindings.py:KnownConstructor",
        ),
        RuleRecord(
            rule_id="bindings.constructor_arguments", codes=("EF-16",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="no unknown and no missing required argument", lives="hisim/setup_rules/bindings.py:ConstructorArguments",
        ),
        RuleRecord(
            rule_id="bindings.known_config_field", codes=("EF-17",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="every config key is a field; component_id is not set in config", lives="hisim/setup_rules/bindings.py:KnownConfigField",
        ),
        RuleRecord(
            rule_id="bindings.decodable_value", codes=("EF-1A",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a value decodes into its field's type",
            lives="hisim/setup_rules/bindings.py:DecodableValue",
            note=(
                "the refusals are raised by hisim/energy_system/codec.py:ConfigValueCodec and hisim/energy_system/configure.py:EntryConfigurator and "
                "recorded per entry by hisim/energy_system/configure.py:ConfigurationOutcome.attempt"
            ),
        ),
        RuleRecord(
            rule_id="bindings.auto_only_on_a_sized_field", codes=("EF-1B",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="AUTO only on a field with a sizing law", lives="hisim/setup_rules/bindings.py:AutoOnlyOnASizedField",
        ),
        RuleRecord(
            rule_id="bindings.sizing_fact_is_read", codes=("EF-43",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="sizing_sources names a fact the class's laws read", lives="hisim/setup_rules/bindings.py:SizingFactIsRead",
        ),
        RuleRecord(
            rule_id="bindings.path_variable_known", codes=("EF-04",), nature=RuleNature.LAW, target="bindings", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a ${name} names a registered path variable", lives="hisim/setup_rules/bindings.py:PathVariableKnown",
            note=(
                "the refusals are raised by hisim/energy_system/path_resolver.py:PathResolver.resolve and recorded per entry by "
                "hisim/energy_system/configure.py:ConfigurationOutcome.attempt; a path resolved outside the configuring of a file still raises there"
            ),
        ),
        RuleRecord(
            rule_id="sizing.fact_provided", codes=("EF-4A",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a fact a law reads is provided by somebody",
            lives="hisim/setup_rules/sizing.py:FactProvided",
            note="the vocabulary is the house's, §4.3; the rule is not",
        ),
        RuleRecord(
            rule_id="sizing.provider_unambiguous", codes=("EF-4B",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="several providers need a source line", lives="hisim/setup_rules/sizing.py:ProviderUnambiguous",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.source_is_a_provider", codes=("EF-4C",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a source line names a component that declares the fact",
            lives="hisim/setup_rules/sizing.py:SourceIsAProvider",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.fact_not_null", codes=("EF-4D",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the provider gives a value", lives="hisim/setup_rules/sizing.py:FactNotNull",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.source_shape", codes=("EF-4E",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the mapping shape fits the law's cardinality",
            lives="hisim/setup_rules/sizing.py:SourceShape",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.no_field_cycle", codes=("EF-4F",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="sibling Self(...) reads form no cycle", lives="hisim/setup_rules/sizing.py:NoFieldCycle",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.one_read_shape", codes=("EF-4G",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a fact is read many-fold or once, not both; no repeated provider",
            lives="hisim/setup_rules/sizing.py:OneReadShape",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.unique_instance", codes=("EF-4H",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="every config has component_id.name, no two share it", lives="hisim/setup_rules/sizing.py:UniqueInstance",
            note=(
                "the kernel's refusal is raised by hisim/config/engine.py:SizingFactEngine.register and recorded by "
                "hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it"
            ),
        ),
        RuleRecord(
            rule_id="sizing.sized_together", codes=("EF-4J",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="a SizedTogether group is stated whole or not at all", lives="hisim/setup_rules/sizing.py:SizedTogetherWhole",
            note=(
                "the kernel's refusal is raised by hisim/config/sizing.py:SizedTogether.check and recorded by "
                "hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it"
            ),
        ),
        RuleRecord(
            rule_id="sizing.resolved", codes=("EF-4X",), nature=RuleNature.LAW, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="E",
            checks="the kernel made progress and no law raised", lives="hisim/setup_rules/sizing.py:Resolved",
            note="the kernel's refusal is raised by hisim/config/engine.py and recorded by hisim/energy_system/sizing_bridge.py:resolve_sizing; the rule classifies it",
        ),
        RuleRecord(
            rule_id="sizing.unconsumed_fact", codes=("EF-4K",), nature=RuleNature.DOMAIN, target="sizing", stage=RuleStage.BUILD, when="build", enforcement="L",
            checks="a provided fact nobody reads", lives="hisim/setup_rules/sizing.py:UnconsumedFact",
            note="the run record keeps the lines as warnings (hisim/energy_system/configure.py:ConfigurationOutcome.configured)",
        ),
        RuleRecord(
            rule_id="sizing.sizing_source_enabled", codes=("EF-42",), nature=RuleNature.LAW, when="build", enforcement="E",
            checks="a scalar sizing source is not in a disabled group or unselected option",
            lives="hisim/energy_system/groups.py:GroupExpander._dangling_scalar_error; hisim/energy_system/assemblies/addresses.py:_rewrite_sizing",
            note=(
                "stays: the expansion of groups and imports decides it while it builds the expanded file, which cannot hold a source "
                "naming a removed component; a rule could only see the file once it was expanded (decided for PR 8)"
            ),
        ),
        RuleRecord(
            rule_id="wiring.output_exists", codes=("EF-21",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a wire, feed or consuming output names an existing output; a written wire never names a derived one; default feeds are unambiguous",
            lives="hisim/setup_rules/wiring.py:OutputExists",
            note=(
                "the refusals are recorded by hisim/energy_system/feed_resolution.py:DynamicConnectionResolver._resolve_feed and "
                "hisim/energy_system/aggregator_ports.py:AggregatorPortChecker.check_ports_were_created"
            ),
        ),
        RuleRecord(
            rule_id="wiring.input_exists", codes=("EF-22",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="the same for inputs", lives="hisim/setup_rules/wiring.py:InputExists",
            note="the refusals are recorded by hisim/energy_system/aggregator_ports.py:AggregatorPortChecker.check_ports_were_created",
        ),
        RuleRecord(
            rule_id="wiring.defaults_declared", codes=("EF-23",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a bare defaults item has default connections declared for the source class", lives="hisim/setup_rules/wiring.py:DefaultsDeclared",
            note="the refusals are recorded by hisim/energy_system/wiring.py:WiringPlanner._expand_default_item",
        ),
        RuleRecord(
            rule_id="wiring.no_input_fed_twice", codes=("EF-26",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="after default expansion, one input has one wire", lives="hisim/setup_rules/wiring.py:NoInputFedTwice",
        ),
        RuleRecord(
            rule_id="wiring.feed_unique", codes=("EF-25",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="one participant output enters an aggregator once", lives="hisim/setup_rules/wiring.py:FeedUnique",
            note="the refusals are recorded by hisim/energy_system/aggregator_ports.py:AggregatorPortChecker.check_participant_ports_are_unique",
        ),
        RuleRecord(
            rule_id="wiring.target_is_an_aggregator", codes=("EF-27",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="feeds go to a component with dynamic channels", lives="hisim/setup_rules/wiring.py:TargetIsAnAggregator",
            note="the refusals are recorded by hisim/energy_system/feed_resolution.py:DynamicConnectionResolver.resolve_target",
        ),
        RuleRecord(
            rule_id="wiring.channel_matches", codes=("EF-28",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a feed's tags match exactly one channel", lives="hisim/setup_rules/wiring.py:ChannelMatches",
            note="the refusals are recorded by hisim/energy_system/channel_matching.py:ChannelMatcher.match",
        ),
        RuleRecord(
            rule_id="wiring.dispatch_rule", codes=("EF-29",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a dispatch block where and only where the channel dispatches; no rank on a forbidding channel; allowed dispatch tags",
            lives="hisim/setup_rules/wiring.py:DispatchRuleHolds",
            note="the refusals are recorded by hisim/energy_system/channel_matching.py:ChannelMatcher._check_dispatch_rule, _check_dispatch_tags",
        ),
        RuleRecord(
            rule_id="wiring.known_tag", codes=("EF-2A",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a component-type or flow tag is a known enum member", lives="hisim/setup_rules/wiring.py:KnownTag",
            note=(
                "the refusals are recorded by hisim/energy_system/channels.py:TagDecoder.component_type, flow_tags; refused as EnergySystemWiringError, where"
                " EnergySystemBindingError was raised before"
            ),
        ),
        RuleRecord(
            rule_id="wiring.dispatch_signal_unique", codes=("EF-2B",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="two feeds do not claim one control signal", lives="hisim/setup_rules/wiring.py:DispatchSignalUnique",
            note="the refusals are recorded by hisim/energy_system/dispatch_signals.py:DispatchSignalPlanner.check",
        ),
        RuleRecord(
            rule_id="wiring.port_types_agree", codes=("EF-30",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="load type and unit agree across a wire or feed (ANY matches)", lives="hisim/setup_rules/wiring.py:PortTypesAgree",
            note=(
                "the refusals are recorded by hisim/energy_system/channel_matching.py:ChannelMatcher._check_port_types and, for an aggregator's own refusal, "
                "DynamicConnectionResolver.resolve_target"
            ),
        ),
        RuleRecord(
            rule_id="wiring.mandatory_input_fed", codes=("EF-31",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E (L when allowed)", checks="a mandatory input is fed; allow_unconnected_mandatory only logs",
            lives="hisim/setup_rules/wiring.py:MandatoryInputFed",
            note="the inputs that make a device need a building are listed in §6.8; an allowed open input is logged by hisim/energy_system/wiring.py:WiredSystem.apply",
        ),
        RuleRecord(
            rule_id="wiring.port_name_free", codes=("EF-32",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="feed resolution creates no port name that exists", lives="hisim/setup_rules/wiring.py:PortNameFree",
            note="the refusals are recorded by hisim/energy_system/aggregator_ports.py:AggregatorPortChecker.check_port_names_are_free",
        ),
        RuleRecord(
            rule_id="wiring.constructed", codes=("EF-33",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a component constructor did not raise", lives="hisim/setup_rules/wiring.py:Constructed",
            note="the refusals are recorded by hisim/energy_system/wiring.py:ComponentBuilder._construct; the refusal is raised from the constructor's exception",
        ),
        RuleRecord(
            rule_id="wiring.consuming_output_carrier", codes=("EF-7M",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a consuming output's energy port carries the need's carrier", lives="hisim/setup_rules/wiring.py:ConsumingOutputCarrier",
        ),
        RuleRecord(
            rule_id="wiring.meter_feed_count", codes=("EF-7N",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="the meter's class declares exactly one feed per consuming output", lives="hisim/setup_rules/wiring.py:MeterFeedCount",
            note="the feed a consuming output lands as is read by hisim/energy_system/feed_resolution.py:DynamicConnectionResolver.declared_feeds_for",
        ),
        RuleRecord(
            rule_id="wiring.no_double_count", codes=("EF-7T",), nature=RuleNature.LAW, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="an aggregator does not read another aggregator's output while both observe one flow",
            lives="hisim/setup_rules/wiring.py:NoDoubleCount",
        ),
        RuleRecord(
            rule_id="wiring.unconsumed_source", codes=("EF-34",), nature=RuleNature.DOMAIN, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="L", checks="a component feeds nobody", lives="hisim/setup_rules/wiring.py:UnconsumedSource",
            note="the run record keeps the lines as warnings (hisim/energy_system/executor.py:EnergySystemExecutor.construct)",
        ),
        RuleRecord(
            rule_id="wiring.unmetered_output_of_a_need", codes=("EF-35",), nature=RuleNature.DOMAIN, target="wiring", stage=RuleStage.BUILD, when="build",
            enforcement="L", checks="a metered consumer's other outputs of the same carrier are named in the need",
            lives="hisim/setup_rules/wiring.py:UnmeteredOutputOfANeed",
        ),
        RuleRecord(
            rule_id="reader.ef_70", codes=("EF-70",), nature=RuleNature.PARSE, when="load / build", enforcement="E",
            checks="an assembly or imports block is well-formed; the assembly's kind is assembly",
            lives="hisim/energy_system/imports_reader.py:ImportsReader.shape_error; hisim/energy_system/assemblies/reader.py:AssemblyReader.build",
        ),
        RuleRecord(
            rule_id="assemblies.found", codes=("EF-71",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="the assembly is in one library directory",
            lives="hisim/setup_rules/assemblies.py:AssemblyFound",
            note=(
                "the lookups are recorded by hisim/energy_system/assemblies/resolver.py:AssemblyResolver.find, every import's; the refusal of a library "
                "directory that does not exist stays in AssemblyResolver.__init__, a configuration of the machine refused when the resolver is made, before "
                "any file"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.found_once", codes=("EF-72",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="not in two",
            lives="hisim/setup_rules/assemblies.py:AssemblyFoundOnce",
            note="the lookups are recorded by hisim/energy_system/assemblies/resolver.py:AssemblyResolver.find",
        ),
        RuleRecord(
            rule_id="assemblies.library_valid", codes=("EF-75", "EF-78", "EF-79"), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build; CI tests/assemblies/test_assembly_library.py", enforcement="E",
            checks=(
                "the assembly file passes the library check (the 45 problems LibraryChecker.check lists: name equals path, members load, parameters described"
                " with defaults, units and ranges, constraints consistent, variants cover the selector, ports and placeholders name members, a tests: "
                "contract with bounds, monotone and expect entries); a parameter's unit equals its field's (EF-78), a fed field has a unit (EF-79)"
            ),
            lives="hisim/setup_rules/assemblies.py:LibraryValid",
            note=(
                "the checker moved whole into the package (hisim/setup_rules/assemblies.py:LibraryChecker); its problems are one rule with 45 messages; the "
                "CI test tests/assemblies/test_assembly_library.py runs the checker on every library file. PR 9 merged the record assemblies.parameter_unit "
                "(EF-78, EF-79): the unit problems are problems of the library check, listed in the fault of EF-75 under their own codes as they always were,"
                " and a rule of their own would take them out of that list"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.parameter_valid", codes=("EF-76",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="an import parameter has the type, value and range its declaration states",
            lives="hisim/setup_rules/assemblies.py:ParameterValid",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/parameters.py:resolve_selection and, for a member's preset parameter and display"
                " template, by hisim/energy_system/assemblies/addresses.py:member_units"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.constraint_holds", codes=("EF-77",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="exactly_one_of / all_or_none_of hold",
            lives="hisim/setup_rules/assemblies.py:ConstraintHolds",
            note="the refusals are recorded by hisim/energy_system/assemblies/parameters.py:resolve_selection",
        ),
        RuleRecord(
            rule_id="assemblies.port_has_a_partner", codes=("EF-7A",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a required port finds a candidate; a many-fact port a provider",
            lives="hisim/setup_rules/assemblies.py:PortHasAPartner",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide, _many_fact",
        ),
        RuleRecord(
            rule_id="assemblies.port_unambiguous", codes=("EF-7B",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="several candidates need a verb",
            lives="hisim/setup_rules/assemblies.py:PortUnambiguous",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide, resolve",
        ),
        RuleRecord(
            rule_id="assemblies.required_port_not_declined", codes=("EF-7C",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="no none: on a required port",
            lives="hisim/setup_rules/assemblies.py:RequiredPortNotDeclined",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide",
        ),
        RuleRecord(
            rule_id="assemblies.bound_partner_exists", codes=("EF-7D",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="the bind target exists",
            lives="hisim/setup_rules/assemblies.py:BoundPartnerExists",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide, check_declared, resolve, provided",
        ),
        RuleRecord(
            rule_id="assemblies.optional_port_decided", codes=("EF-7E",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="an optional port with candidates carries a verb",
            lives="hisim/setup_rules/assemblies.py:OptionalPortDecided",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide",
        ),
        RuleRecord(
            rule_id="assemblies.verb_on_an_active_port", codes=("EF-7F",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="no verb on a switched-off port",
            lives="hisim/setup_rules/assemblies.py:VerbOnAnActivePort",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.decide",
        ),
        RuleRecord(
            rule_id="assemblies.known_port", codes=("EF-7G",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a verb names an existing port of the right kind",
            lives="hisim/setup_rules/assemblies.py:KnownPort",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.bind",
        ),
        RuleRecord(
            rule_id="assemblies.bound_output_is_read", codes=("EF-7H",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a bound provided output is one the port's wires name",
            lives="hisim/setup_rules/assemblies.py:BoundOutputIsRead",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.provided",
        ),
        RuleRecord(
            rule_id="assemblies.port_contract", codes=("EF-7J",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a port fits its members (carrier, fact, circuit lowering, observer, controllables); placeholders have counterparts",
            lives="hisim/setup_rules/assemblies.py:PortContract",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder.lower, _carrier, _land_fact, _lower_circuit, _observe, "
                "_plan_controllables and hisim/energy_system/assemblies/expansion.py:ImportExpander.expand, _site_owner"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.circuit_ends_match", codes=("EF-7K",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="both ends of a circuit binding name one circuit, written consistently",
            lives="hisim/setup_rules/assemblies.py:CircuitEndsMatch",
            note="the refusals are recorded by hisim/energy_system/assemblies/binding.py:PortBinder._joined, resolve",
        ),
        RuleRecord(
            rule_id="assemblies.one_provider_per_carrier", codes=("EF-7L",), nature=RuleNature.DOMAIN, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a carrier has exactly one provider; a provider has a bound consumer; a required need a provider",
            lives="hisim/setup_rules/assemblies.py:OneProviderPerCarrier",
            note=(
                "one site with one supply per carrier: a district or a plant with two grid connections is refused; today no setup has two; the refusals are "
                "recorded by hisim/energy_system/assemblies/binding.py:PortBinder.bind, _carrier"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.order_valid", codes=("EF-7P",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="an evaluation order names each number once; recorded members follow the record's sequence",
            lives="hisim/setup_rules/assemblies.py:OrderValid",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/addresses.py:sequence and "
                "hisim/energy_system/assemblies/expansion.py:recorded_sequence_refusal"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.observer_selection", codes=("EF-7S",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="an observes: selector resolves",
            lives="hisim/setup_rules/assemblies.py:ObserverSelection",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/selection.py:SelectionPlan._select, which the wiring runs; the rule reads the "
                "built file"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.actuation_selection", codes=("EF-7U",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a controllable selection resolves",
            lives="hisim/setup_rules/assemblies.py:ActuationSelection",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/selection.py:SelectionPlan.__call__, which the wiring runs; the rule reads the "
                "built file"
            ),
        ),
        RuleRecord(
            rule_id="assemblies.weight_free", codes=("EF-7V",), nature=RuleNature.LAW, target="assemblies", stage=RuleStage.BUILD,
            when="build", enforcement="E",
            checks="a derived weight reaches no other kind's base weight",
            lives="hisim/setup_rules/assemblies.py:WeightFree",
            note=(
                "the refusals are recorded by hisim/energy_system/assemblies/selection.py:SelectionPlan._rank, collision, which the wiring runs; the rule "
                "reads the built file"
            ),
        ),
        RuleRecord(
            rule_id="components.one_kind_base", codes=("CB-01",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build, on the components as added, before the run prepares anything", enforcement="E",
            checks="the class inherits exactly one of the five kind bases", lives="hisim/setup_rules/components.py:OneKindBase",
            note="the sentence is hisim/component.py:ComponentKinds.problem, which ComponentKinds.of raises for a lookup of such a class",
        ),
        RuleRecord(
            rule_id="components.unique_name", codes=("CB-02",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build, on the components as added, before the run prepares anything", enforcement="E",
            checks="no second component with one component_id.key", lives="hisim/setup_rules/components.py:UniqueName",
        ),
        RuleRecord(
            rule_id="components.unique_output_key", codes=("CB-03",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="no two outputs with one full name", lives="hisim/setup_rules/components.py:UniqueOutputKey",
        ),
        RuleRecord(
            rule_id="components.has_outputs", nature=RuleNature.LAW, when="never (retired in PR 5)", enforcement="E",
            checks="a component registered at least one output", lives="removed from ComponentWrapper.register_component_outputs in PR 5",
            note="retired: never fired, the raise sat after a registration inside the loop; no code was allocated",
        ),
        RuleRecord(
            rule_id="components.name_is_identity", nature=RuleNature.LAW, when="build (construction)", enforcement="E",
            checks="the constructor name equals config.component_id.key; the name is an identifier; the config is a ConfigBase; no AUTO left; parameters set",
            lives="hisim/component.py:Component.__init__", note="a constructor contract, D10",
        ),
        RuleRecord(
            rule_id="components.connect_input_target", nature=RuleNature.LAW, when="build (the call)", enforcement="E",
            checks="connect_input names an existing input, once", lives="hisim/component.py:Component.connect_input",
            note="stays: fires at the call that would leave a broken component; a rule could only see the broken state afterwards (decided for PR 5)",
        ),
        RuleRecord(
            rule_id="components.default_connections_wellformed", nature=RuleNature.LAW, when="build (the call)", enforcement="E",
            checks="a default-connection list is non-empty, from one source class, and the connections asked for exist",
            lives="Component.add_default_connections, get_default_connections; hisim/dynamic_component.py",
            note="stays: fires at the call that would leave a broken component; a rule could only see the broken state afterwards (decided for PR 5)",
        ),
        RuleRecord(
            rule_id="components.load_type_agrees", nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD, when="build", enforcement="E",
            codes=("CB-04",), checks="input and output share a load type (ANY matches)", lives="hisim/setup_rules/components.py:LoadTypeAgrees",
            note="one rule with EF-30",
        ),
        RuleRecord(
            rule_id="components.unit_agrees", nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD, when="build", enforcement="E / L",
            codes=("CB-05",), checks="concrete units agree; Units.ANY warns", lives="hisim/setup_rules/components.py:UnitAgrees",
            note="the warning for Units.ANY on one side stays with the connection, hisim/component_wrapper.py:ComponentWrapper.connect_inputs",
        ),
        RuleRecord(
            rule_id="components.mandatory_input_connected", nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD, when="build", enforcement="E / L",
            codes=("CB-06",), checks="a mandatory input has a source (allow_unconnected_mandatory warns)",
            lives="hisim/setup_rules/components.py:MandatoryInputConnected",
            note=(
                "see §6.8; one rule with EF-31; the warning for allow_unconnected_mandatory stays with the connection, "
                "hisim/component_wrapper.py:ComponentWrapper.connect_inputs"
            ),
        ),
        RuleRecord(
            rule_id="components.auto_connect_possible", nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD, when="build", enforcement="E",
            codes=("CB-07",), checks="automatic connection finds default connections and one matching source",
            lives="hisim/setup_rules/components.py:AutoConnectPossible",
        ),
        RuleRecord(
            rule_id="components.no_duplicate_dynamic_feed", nature=RuleNature.LAW, when="build (the call)", enforcement="E",
            checks="one source output enters a dynamic component once", lives="hisim/dynamic_component.py:DynamicComponent.add_component_input_and_connect",
            note=(
                "the imperative twin of EF-25; stays: fires at the call that would leave a broken component; a rule could only see the broken state "
                "afterwards (decided for PR 5)"
            ),
        ),
        RuleRecord(
            rule_id="components.dispatch_port_unique", nature=RuleNature.LAW, when="build (the call)", enforcement="E",
            checks="an aggregator has no two dispatch ports of one name or one type on one weight",
            lives="DynamicComponent._refuse_a_dispatch_port_this_aggregator_already_has",
            note="stays: fires at the call that would leave a broken component; a rule could only see the broken state afterwards (decided for PR 5)",
        ),
        RuleRecord(
            rule_id="components.default_connection_tag", nature=RuleNature.LAW, when="build (the call)", enforcement="E",
            checks="a default connection's tag is carried by its target output", lives="DynamicComponent.connect_with_dynamic_connections_list",
            note="stays: fires at the call that would leave a broken component; a rule could only see the broken state afterwards (decided for PR 5)",
        ),
        RuleRecord(
            rule_id="components.channel_declared", nature=RuleNature.LAW, when="build / run", enforcement="E",
            checks="an aggregator asks only for a channel it declares", lives="DynamicComponent.get_channel", note="an internal contract",
        ),
        RuleRecord(
            rule_id="components.cost_relevance_declared", codes=("CB-08",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build, on the components as added, before the run prepares anything", enforcement="E",
            checks="with COMPUTE_LIFECYCLE_COSTS or LIFECYCLE_COST_REPORT: no class with cost_relevance UNDECLARED",
            lives="hisim/setup_rules/components.py:CostRelevanceDeclared",
            note="the first half of Simulator.check_cost_declarations, split from components.priced_has_cost_facts in PR 5",
        ),
        RuleRecord(
            rule_id="components.priced_has_cost_facts", codes=("CB-09",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build, on the components as added, before the run prepares anything", enforcement="E",
            checks="with COMPUTE_LIFECYCLE_COSTS or LIFECYCLE_COST_REPORT: a PRICED class has get_cost_facts or an adapter entry",
            lives="hisim/setup_rules/components.py:PricedHasCostFacts",
            note=(
                "the second half of Simulator.check_cost_declarations, split from components.cost_relevance_declared in PR 5: two refusals, "
                "two errors (UnpriceableComponentError)"
            ),
        ),
        RuleRecord(
            rule_id="components.source_output_registered", codes=("CB-10",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build", enforcement="E", checks="every connected input's source output belongs to an added component",
            lives="hisim/setup_rules/components.py:SourceOutputRegistered",
        ),
        RuleRecord(
            rule_id="components.kind_in_evaluation_order", codes=("CB-11",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build", enforcement="E", checks="every kind is in EVALUATION_ORDER_OF_KINDS", lives="hisim/setup_rules/components.py:KindInEvaluationOrder",
        ),
        RuleRecord(
            rule_id="components.no_physics_cycle", codes=("CB-12",), nature=RuleNature.DOMAIN, target="components", stage=RuleStage.BUILD, when="build",
            enforcement="L", checks="the L1 device-before-store constraints form no cycle", lives="hisim/setup_rules/components.py:NoPhysicsCycle",
            note="the cycles are found by hisim/evaluation_order.py:PhysicsOrder.ordered, through hisim/simulator.py:Simulator.evaluation_order_of",
        ),
        RuleRecord(
            rule_id="components.no_l2_cycle", codes=("CB-13",), nature=RuleNature.DOMAIN, target="components", stage=RuleStage.BUILD, when="build",
            enforcement="L", checks="L2 controllers read one another in no cycle", lives="hisim/setup_rules/components.py:NoL2Cycle",
            note="the cycles are found by hisim/evaluation_order.py:L2Order.ordered, through hisim/simulator.py:Simulator.evaluation_order_of",
        ),
        RuleRecord(
            rule_id="components.not_empty", codes=("CB-14",), nature=RuleNature.LAW, target="components", stage=RuleStage.BUILD,
            when="build, on the components as added, before the run prepares anything", enforcement="E",
            checks="the run has at least one component and one column", lives="hisim/setup_rules/components.py:NotEmpty",
        ),
        RuleRecord(
            rule_id="energy_ports.loop_pairing", codes=("EP-01",), nature=RuleNature.LAW, target="energy_ports", stage=RuleStage.BUILD,
            when="build; again after the run", enforcement="E", checks="an end's other end declares the loop; a loop has exactly one supplier",
            lives="hisim/setup_rules/energy_ports.py:LoopPairing",
            note=(
                "the loop ends are resolved by hisim/postprocessing/energy_balance/ports.py:Wiring.loops, "
                "which the balance after the run reads again (results.loop_faults)"
            ),
        ),
        RuleRecord(
            rule_id="energy_ports.one_reader_per_flow", codes=("EP-02",), nature=RuleNature.LAW, target="energy_ports", stage=RuleStage.BUILD,
            when="build; after the run", enforcement="E", checks="a loop's mass flow is read by one loop-declaring component",
            lives="hisim/setup_rules/energy_ports.py:OneReaderPerFlow",
            note=(
                "the loop ends are resolved by hisim/postprocessing/energy_balance/ports.py:Wiring.loops, "
                "which the balance after the run reads again (results.loop_faults)"
            ),
        ),
        RuleRecord(
            rule_id="energy_ports.no_loop_on_a_node", codes=("EP-03",), nature=RuleNature.LAW, target="energy_ports", stage=RuleStage.BUILD,
            when="build; after the run", enforcement="E", checks="an exempt component or a meter declares no loop end",
            lives="hisim/setup_rules/energy_ports.py:NoLoopOnANode",
            note=(
                "the loop ends are resolved by hisim/postprocessing/energy_balance/ports.py:Wiring.loops, "
                "which the balance after the run reads again (results.loop_faults, which refuses an end on a node after the run as well)"
            ),
        ),
        RuleRecord(
            rule_id="energy_ports.receiver_unambiguous", codes=("EP-04",), nature=RuleNature.LAW, target="energy_ports", stage=RuleStage.BUILD, when="build",
            enforcement="E",
            checks="an output port read through paired inputs by one component; a non-metered PRODUCTION port read by one declared component unless an input is tagged",
            lives="hisim/setup_rules/energy_ports.py:ReceiverUnambiguous",
        ),
        RuleRecord(
            rule_id="energy_ports.input_port_pairs", codes=("EP-05",), nature=RuleNature.LAW, target="energy_ports", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="an input port pairs with the output port it reads (same carrier, opposite role)",
            lives="hisim/setup_rules/energy_ports.py:InputPortPairs",
        ),
        RuleRecord(
            rule_id="energy_ports.room_heat_needs_receiver", codes=("EP-06",), nature=RuleNature.DOMAIN, target="energy_ports", stage=RuleStage.BUILD,
            when="build", enforcement="E", checks="a PRODUCTION port of ROOM_HEAT is read by a component",
            lives="hisim/setup_rules/energy_ports.py:RoomHeatNeedsReceiver",
            note=", waived for demo and plant",
        ),
        RuleRecord(
            rule_id="fluid_loops.loop_tagged_whole", codes=("FL-01",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a port set tags all four loop quantities", lives="hisim/setup_rules/fluid_loops.py:LoopTaggedWhole",
            note=(
                "the loops tagged by half are found by hisim/component.py:FluidLoopPortSet.half_tagged, which FluidLoopPortSet.group "
                "refuses again (FluidLoopError) when port sets are asked for outside a build"
            ),
        ),
        RuleRecord(
            rule_id="fluid_loops.far_end_tagged", codes=("FL-02",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a tagged flow is read by, or fed from, a tagged channel", lives="hisim/setup_rules/fluid_loops.py:FarEndTagged",
        ),
        RuleRecord(
            rule_id="fluid_loops.flow_read_once", codes=("FL-03",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="one flow out is read by one flow in (a split needs a valve)", lives="hisim/setup_rules/fluid_loops.py:FlowReadOnce",
        ),
        RuleRecord(
            rule_id="fluid_loops.closes", codes=("FL-04",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="a wired port set's flow out is read and its flow in fed", lives="hisim/setup_rules/fluid_loops.py:LoopCloses",
        ),
        RuleRecord(
            rule_id="fluid_loops.wired_whole", codes=("FL-05",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="flows and temperatures are connected together, or neither", lives="hisim/setup_rules/fluid_loops.py:WiredWhole",
        ),
        RuleRecord(
            rule_id="fluid_loops.temperature_follows_flow", codes=("FL-06",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD,
            when="build", enforcement="E", checks="a member's temperature in reads the member whose flow it reads",
            lives="hisim/setup_rules/fluid_loops.py:TemperatureFollowsFlow",
        ),
        RuleRecord(
            rule_id="fluid_loops.one_carrier_one_fluid", codes=("FL-07",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD,
            when="build", enforcement="E", checks="all members state one carrier and one fluid", lives="hisim/setup_rules/fluid_loops.py:OneCarrierOneFluid",
        ),
        RuleRecord(
            rule_id="fluid_loops.one_pump_owner", codes=("FL-08",), nature=RuleNature.LAW, target="fluid_loops", stage=RuleStage.BUILD, when="build",
            enforcement="E", checks="exactly one member pumps", lives="hisim/setup_rules/fluid_loops.py:OnePumpOwner",
        ),
        RuleRecord(
            rule_id="results.finite", codes=("AR-01",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run, first",
            enforcement="E", checks="no result column holds NaN or inf unless its output declares may_be_non_finite; one column per output",
            lives="hisim/setup_rules/results.py:Finite",
        ),
        RuleRecord(
            rule_id="results.all_rows", codes=("AR-02",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E", checks="the table has one row per timestep", lives="hisim/setup_rules/results.py:AllRows",
            note="moved from hisim/simulator.py:Simulator.prepare_post_processing, which now indexes whatever rows it has",
        ),
        RuleRecord(
            rule_id="results.port_finite", codes=("AR-03",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E", checks="a declared port is finite in every step", lives="hisim/setup_rules/results.py:PortFinite",
            note=(
                "a rule of its own beside results.finite, requiring it: a port of an output that declares may_be_non_finite is checked only "
                "here, and its fault raises EnergyBalanceError as before"
            ),
        ),
        RuleRecord(
            rule_id="results.component_balance_closes", codes=("AR-04",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN,
            when="after the run", enforcement="E",
            checks="per component, consumption − production − loss − stored change ≤ 0.1 % of throughput over the run and per step (Tolerance)",
            lives="hisim/setup_rules/results.py:ComponentBalanceCloses",
            note="the balance is computed by hisim/postprocessing/energy_balance/check.py:BalanceCheck.balance, run",
        ),
        RuleRecord(
            rule_id="results.link_closes", codes=("AR-05",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E", checks="every PRODUCTION→CONSUMPTION transfer between declared components is booked by both sides within 0.1 %",
            lives="hisim/setup_rules/results.py:LinkCloses",
            note=(
                "the links are computed by hisim/postprocessing/energy_balance/check.py:BalanceCheck.links; "
                "hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.declared_links"
            ),
        ),
        RuleRecord(
            rule_id="results.loop_faults", codes=("AR-06",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E", checks="the loop rules of §6.5 on the run's columns, plus: an open end pumps no water",
            lives="hisim/setup_rules/results.py:LoopFaults",
            note=(
                "the breaches are found by hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.add_loops, Wiring.loops; "
                "an end on an exempt component or a meter is one of them since this rule moved, no ValueError"
            ),
        ),
        RuleRecord(
            rule_id="results.loop_columns", codes=("AR-07",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E", checks="a loop's mass flow is never negative, the timestep positive; the columns a port, loop or exchange names exist",
            lives="hisim/setup_rules/results.py:ColumnsReadable",
            note=(
                "the faults are recorded by hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.collect, add_input_ports, add_loops "
                "(LoopColumns.refusal), read_meters, which raised a ValueError for each before"
            ),
        ),
        RuleRecord(
            rule_id="results.no_port_on_a_node", nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run", enforcement="E",
            checks="an exempt component or a meter declares no port",
            codes=("AR-08",), lives="hisim/setup_rules/results.py:NoPortOnANode",
            note="the faults are recorded by hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.node_port_fault, which raised a ValueError before",
        ),
        RuleRecord(
            rule_id="meters.meter_balance", codes=("MT-01",), nature=RuleNature.LAW, target="meters", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="E (conditional)",
            checks="per meter and carrier, import − export equals the ports it reads, within tolerance; enforced only while nothing is undeclared (Q-H)",
            lives="hisim/setup_rules/meters.py:MeterBalanceAgrees",
            note="the meter balance and its Q-H switch are computed by hisim/postprocessing/energy_balance/meters.py:MeterBalance.of, MeterCheck.failing",
        ),
        RuleRecord(
            rule_id="meters.metered_port_has_a_meter", codes=("MT-02",), nature=RuleNature.DOMAIN, target="meters", stage=RuleStage.AFTER_RUN,
            when="after the run", enforcement="E (conditional)",
            checks="a metered port's peer is a meter or a component; enforced only while nothing is undeclared",
            lives="hisim/setup_rules/meters.py:MeteredPortHasAMeter",
            note=(
                ", waived for demo; the unmetered ports and their Q-H switch are computed by hisim/postprocessing/energy_balance/ports.py:"
                "DeclaredPorts.unmetered and hisim/postprocessing/energy_balance/meters.py:MeterCheck.failing_unmetered"
            ),
        ),
        RuleRecord(
            rule_id="results.port_linked", codes=("AR-09",), nature=RuleNature.DOMAIN, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="L (the acceptance PR makes it E)", checks="a non-idle port has a peer",
            lives="hisim/setup_rules/results.py:PortLinked",
            note=(
                "the spec's row names a demo waiver, its sections 4.2 and 4.6 none (a demo's unlinked heat is reported); "
                "waived for no kind; the unlinked ports are computed by hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.unlinked"
            ),
        ),
        RuleRecord(
            rule_id="results.component_declared", codes=("AR-10",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN, when="after the run",
            enforcement="L (CI makes it E, §6.9)", checks="a component declares ports or is exempt with a reason",
            lives="hisim/setup_rules/results.py:ComponentDeclared",
            note="the undeclared components are found by hisim/postprocessing/energy_balance/ports.py:DeclaredPorts.collect",
        ),
        RuleRecord(
            rule_id="results.heating_only_loop_never_cools", codes=("AR-11",), nature=RuleNature.LAW, target="results", stage=RuleStage.AFTER_RUN,
            when="after the run", enforcement="E",
            checks="no supplier of a fluid loop that may not cool (a member says may_cool=False) removes more than 1 W from its water in a step",
            lives="hisim/setup_rules/results.py:HeatingOnlyLoopNeverCools",
            note=(
                "added after the inventory (bead hisim-false-cooling-edge-7130), the heating-only check of §3.4 and §3.5 of "
                "roadmap/fluid_loops_spec.md; the steps are found by hisim/postprocessing/energy_balance/ports.py:"
                "DeclaredPorts.cooling_beyond_the_noise, which books a negative heat within the noise as the loop's heat"
            ),
        ),
        RuleRecord(
            rule_id="costs.lifecycle_engine", nature=RuleNature.LAW, when="post-processing", enforcement="E / L",
            checks="the cost engine finished; CostDataError always propagates, other errors fail only with require_lifecycle_costs",
            lives="hisim/postprocessing/postprocessing_main.py:PostProcessor.compute_lifecycle_costs", note="the engine's own policy",
        ),
        RuleRecord(
            rule_id="run.write_guard", nature=RuleNature.LAW, when="every write", enforcement="E",
            checks="no write outside the calculation's directories; one result directory per calculation; not under hisim/inputs",
            lives="hisim/write_guard.py:WriteGuard._hook, CalculationDirectories", note="not a rule on a system",
        ),
        RuleRecord(
            rule_id="run.simulation_window", nature=RuleNature.LAW, when="load", enforcement="E",
            checks="the step divides a day, the span is whole days in one year, the start is midnight; weather_year, part_load_above_seconds, cache_directories valid",
            lives="hisim/simulationparameters.py:SimulationParameters.validated_window and siblings", note="parameters, not the system",
        ),
        RuleRecord(
            rule_id="run.convergence", nature=RuleNature.LAW, when="per step", enforcement="E",
            checks="a step converges within max(100, 100·s/60) passes; after 10 it is forced", lives="Simulator.process_one_timestep", note="the loop",
        ),
        RuleRecord(
            rule_id="run.post_processing_options", nature=RuleNature.LAW, when="post-processing", enforcement="E",
            checks="report writers need GENERATE_PDF_REPORT; KPI JSON needs COMPUTE_KPIS; network charts need MAKE_NETWORK_CHARTS and dot",
            lives="PostProcessor.run and siblings",
        ),
        RuleRecord(
            rule_id="run.kpi_consistency", nature=RuleNature.LAW, when="post-processing", enforcement="E",
            checks=(
                "self-sufficiency, relative demand, self-consumption ≤ 100 %; batteries publish their stored-energy increase; KPI entries have a source, a tag, a "
                "unique address"
            ),
            lives="hisim/postprocessing/kpi_computation/kpi_preparation.py, kpi_structure.py, kpi_address.py",
            note="the HTW Berlin definitions are a prosumer household's, noted",
        ),
        RuleRecord(
            rule_id="run.building_sizer_kpi_json", nature=RuleNature.BUILDING, when="post-processing", enforcement="E",
            checks="a building object has a positive conditioned floor area", lives="PostProcessor.write_kpis_to_json_for_building_sizer",
        ),
        RuleRecord(
            rule_id="run.scenario_kpis_read_bui1", nature=RuleNature.RESIDENTIAL, when="post-processing", enforcement="E (KeyError)",
            checks="write_kpis_in_dict reads the default building label", lives="PostProcessor.write_kpis_in_dict", note="R-shaped (one house); a district bead",
        ),
        RuleRecord(
            rule_id="run.economics", nature=RuleNature.RESIDENTIAL, when="post-processing", enforcement="E",
            checks=(
                "DeviceEnergySpecs rows for every electricity publisher; useful-heat sources are Building.TheoreticalHeatingEnergyDemand and "
                "SimpleDHWStorage.ThermalEnergyConsumptionDHW; EconomicContext (tenancy, envelope), perspectives (owner-occupier, landlord, tenant), the subsidy "
                "context (dwelling units, household size)"
            ),
            lives="hisim/economics/bridge.py, adapter.py, perspectives.py, subsidies/context.py",
            note="the cost engine is a household's; scoped when the engine runs for a commercial case",
        ),
        RuleRecord(
            rule_id="recorder.recorded_name_invalid", codes=("EF-R1",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a component's runtime name is an identifier", lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_qualified_identity", codes=("EF-R2",), nature=RuleNature.RESIDENTIAL, when="recording", enforcement="E",
            checks="no component carries a building or a unit in its identity (blocks a district twin)", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_absolute_path", codes=("EF-R3",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="no absolute path survives re-symbolisation", lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_preset_gone", codes=("EF-R4",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a preset provenance names a builder the class still has", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_file_rejected", codes=("EF-R5",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="the written file loads and builds again", lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.grouping_unassigned_difference", codes=("EF-R6",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a grouping assigns every difference between its probes", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.grouping_unknown_option", codes=("EF-R7",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a grouping names only options it declares", lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.equal_parameter_files", codes=("EF-R8",), nature=RuleNature.LAW, when="recording", enforcement="L",
            checks="no two simulation-parameter files normalise equal (the fleet driver checks it without raising)", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.probe_list_malformed", codes=("EF-R9",), nature=RuleNature.LAW, when="recording", enforcement="E", checks="a probe list is well-formed",
            lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.grouping_not_reproduced", codes=("EF-R10",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="the grouped file reproduces every probe", lives="hisim/energy_system/recording/", note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_dispatch_ambiguous", codes=("EF-R11",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="an aggregator's control outputs for one participant can be told apart", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_would_overwrite", codes=("EF-R12",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a recording never overwrites a file the recorder did not produce", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_auto_differs", codes=("EF-R13",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="a field left to its preset re-resolves to the number the run produced", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="recorder.recorded_field_missing", codes=("EF-R14",), nature=RuleNature.LAW, when="recording", enforcement="E",
            checks="the rebuilt file holds every component and field the run had", lives="hisim/energy_system/recording/",
            note="checks a recording against its run, not a system",
        ),
        RuleRecord(
            rule_id="house.heat_distribution_system_needs_building", nature=RuleNature.BUILDING, when="build (EF-31)", enforcement="E",
            checks="a heat distribution system needs a building: mandatory inputs TheoreticalThermalBuildingDemand, ResidenceTemperatureIndoorAir only a Building feeds",
            lives="hisim/components/heat_distribution_system.py:HeatDistribution.__init__, HeatDistributionController.__init__", note="right for any heated building",
        ),
        RuleRecord(
            rule_id="house.room_temperature_controllers_need_building", nature=RuleNature.BUILDING, when="build (EF-31)", enforcement="E",
            checks=(
                "room-temperature controllers need a building: mandatory indoor temperature: AirConditionerController, SimpleAirConditioner, L1CHPController, "
                "IdealizedElectricHeaterController"
            ),
            lives="hisim/components/air_conditioner.py, simple_air_conditioner.py, generic_chp/controller.py, idealized_electric_heater.py",
            note="right for any heated building",
        ),
        RuleRecord(
            rule_id="house.heating_season_input", nature=RuleNature.BUILDING, when="build", enforcement="E",
            checks=(
                "heating-season input: boiler, district heating, electric heating, heat-pump space heating and HDS controllers read HeatingSeason from the heating "
                "curve, whose laws read the building's load per m²"
            ),
            lives=(
                "hisim/components/generic_boiler.py, generic_district_heating.py, generic_electric_heating.py, more_advanced_heat_pump_hplib.py, "
                "heating_curve.py:HEATING_THRESHOLD_LAW"
            ),
            note="a process-heat boiler has no season; a commercial building has one",
        ),
        RuleRecord(
            rule_id="house.building_without_residents_warns", nature=RuleNature.RESIDENTIAL, when="first step", enforcement="L",
            checks="a building without residents warns: HeatingByResidents and HeatingByDevices optional; 0 W when unconnected",
            lives="hisim/components/building/building.py:Building.warn_about_unconnected_internal_heat_gains",
            note="D11 adds residents to the one setup that lacks them",
        ),
        RuleRecord(
            rule_id="house.tabula_windows_scaling_apartments", nature=RuleNature.RESIDENTIAL, when="build", enforcement="E",
            checks=(
                "TABULA windows, scaling, apartments: a row with no reference window area is refused; floor area and base area exclusive; apartments ≥ 0 (else floor "
                "area / 92.1 m²)"
            ),
            lives="hisim/components/building/information.py", note="TABULA is residential",
        ),
        RuleRecord(
            rule_id="house.design_heating_load", nature=RuleNature.BUILDING, when="build (sizing)", enforcement="E",
            checks="the design heating load: (H_tr + H_ve)·(T_in − T_ref), DIN EN 12831 steady state",
            lives="hisim/components/building/information.py:get_max_thermal_building_demand", note="the same law for both uses",
        ),
        RuleRecord(
            rule_id="house.building_provides_facts", nature=RuleNature.BUILDING, when="build", enforcement="E",
            checks="the building provides the facts: heating load, apartments, floor area, roof area, set points; a consumer without a building fails EF-4A",
            lives="hisim/components/building/config.py:SIZING_CONTRIBUTIONS", note="(apartments: R)",
        ),
        RuleRecord(
            rule_id="house.per_apartment_sizing_laws", nature=RuleNature.RESIDENTIAL, when="build", enforcement="E",
            checks="per-apartment sizing laws: DHW tank 250 l per apartment; collector 4 m² per apartment; boiler floor 2,500 W per apartment",
            lives="hisim/components/simple_water_storage.py:SimpleDHWStorageConfig.VOLUME_LAW; solar_thermal_system.py; generic_boiler.py",
            note="commercial laws per use",
        ),
        RuleRecord(
            rule_id="house.rooftop_pv_needs_roof_area_buffer", nature=RuleNature.BUILDING, when="build", enforcement="E",
            checks="rooftop PV needs a roof area; a buffer needs the generator's power",
            lives="hisim/components/generic_pv_system/config.py:_rooftop_power_in_watt; simple_water_storage.py:_buffer_volume_in_liter", note="/ L",
        ),
        RuleRecord(
            rule_id="house.domestic_hot_water", nature=RuleNature.RESIDENTIAL, when="per step", enforcement="E",
            checks="domestic hot water: tap 45 °C, fresh water 10 °C, 2,000 kWh/a per household",
            lives="hisim/components/configuration.py:HouseholdWarmWaterDemandConfig", note="per use for commercial",
        ),
        RuleRecord(
            rule_id="house.heating_curve_design_day", nature=RuleNature.BUILDING, when="build / step", enforcement="E",
            checks="heating-curve design day: the reference temperature is below the set point; the load fraction ≥ 0",
            lives="hisim/components/heating_curve.py:HeatingCurveController.__init__",
        ),
        RuleRecord(
            rule_id="house.comfort_bands", nature=RuleNature.BUILDING, when="build", enforcement="E",
            checks="comfort bands: heating set < cooling set, finite, positive hysteresis",
            lives="hisim/components/air_conditioner.py:AirConditionerController.__init__", note="the band's values by use",
        ),
        RuleRecord(
            rule_id="house.cooling_through_emitter_needs_floor_heating", nature=RuleNature.BUILDING, when="build / step", enforcement="E",
            checks="cooling through the emitter needs floor heating; HDS cooling needs cooling_enabled",
            lives="hisim/components/more_advanced_heat_pump_hplib.py; heat_distribution_system.py",
        ),
        RuleRecord(
            rule_id="house.household_occupancy", nature=RuleNature.RESIDENTIAL, when="build", enforcement="E",
            checks=(
                "a household occupancy: one household per connector (more through the LPG); a predefined profile exists; a car's profile is published by exactly one "
                "occupancy; a car runs on electricity or diesel"
            ),
            lives="hisim/components/loadprofilegenerator_utsp_connector.py; lpg_car_information.py; generic_car.py:CarConfig.for_household",
        ),
        RuleRecord(
            rule_id="house.substation_dhw_share", nature=RuleNature.BUILDING, when="per step", enforcement="E",
            checks="substation DHW share: in parallel mode the DHW load is below the connected load",
            lives="hisim/components/generic_district_heating.py; generic_electric_heating.py",
        ),
        RuleRecord(
            rule_id="house.storage_s_converged_temperature", nature=RuleNature.COMPONENT, when="per step", enforcement="E",
            checks="the storage's converged temperature: the mean water temperature in [0, 90] °C after convergence",
            lives="hisim/components/simple_water_storage.py:SimpleWaterStorage.check_converged_mean_water_temperature (the one non-empty i_doublecheck)",
            note="a domestic tank's cap; a process tank would raise it",
        ),
        RuleRecord(
            rule_id="house.loop_carriers", nature=RuleNature.COMPONENT, when="declaration", enforcement="E",
            checks="the loop carriers: SPACE_HEATING_HEAT, DOMESTIC_HOT_WATER_HEAT, AMBIENT_HEAT", lives="hisim/energy_port.py:FluidLoop.LOOP_CARRIERS",
            note="a process-heat loop needs a carrier",
        ),
        RuleRecord(
            rule_id="gate.golden_check", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every (setup, parameters) pair reproduces its KPI leaves in Python, twin and composed mode",
            lives=(
                "scripts/golden_check.py, golden_kpis.py, golden_config.json; .github/workflows/golden-check.yml, golden-year.yml; tests/test_golden_matrix.py (every"
                " setup is in the config)"
            ),
            note="every kind (D8)",
        ),
        RuleRecord(
            rule_id="gate.twin_freshness", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every setup re-records byte-identically; no skip list; no two parameter files describe one run",
            lives="scripts/record_all_setups.py --check; .github/workflows/energy-system-freshness.yml; tests/test_energy_system_recording_fleet.py",
            note="every kind; checks the setup_kind key",
        ),
        RuleRecord(
            rule_id="gate.parity", nature=RuleNature.GATE, when="CI", enforcement="E", checks="a Python setup equals its twin, wiring and results",
            lives="scripts/p3_parity_check.py; tests/test_p3_parity.py", note="every kind",
        ),
        RuleRecord(
            rule_id="gate.schema", nature=RuleNature.GATE, when="CI", enforcement="E", checks="the committed schema equals a fresh export; every twin validates",
            lives="tests/test_energy_system_schema.py", note="every kind",
        ),
        RuleRecord(
            rule_id="gate.port_coverage", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every component class declares ports or is exempt with a reason stated on its own body; an exempt class declares nothing",
            lives="tests/test_energy_port_coverage.py, tests/component_sweep.py (sizes every preset against ComponentSweep.REFERENCE_HOUSE, a single-family archetype)",
            note="L; the reference house is R-shaped (a commercial sweep when a commercial archetype exists)",
        ),
        RuleRecord(
            rule_id="gate.flag_agreement", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="an electricity postprocessing flag agrees with its energy port; the one-sided list only shrinks", lives="tests/test_energy_port_flag_agreement.py",
            note="L",
        ),
        RuleRecord(
            rule_id="gate.kind_base", nature=RuleNature.GATE, when="CI", enforcement="E", checks="every component class has one kind; the evaluation order",
            lives="tests/test_component_kinds.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.cost_declarations", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every class declares cost_relevance; adapter keys resolve; AUTO_SUBSTITUTES are the single-family archetype",
            lives="tests/test_economics_adapter_contract.py, test_economics_declaration_gate.py; tests/renovisor/test_twin_cost_declarations.py",
            note="L; substitutes R-shaped",
        ),
        RuleRecord(
            rule_id="gate.no_per_step_finite_checks", nature=RuleNature.GATE, when="CI", enforcement="E", checks="no isfinite in hot paths",
            lives="tests/test_no_per_step_finite_checks.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.explicit_state_controllers", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="no hidden state; the committed state reproduces the outputs", lives="tests/test_explicit_state_controllers.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.fluid_loop_fleet", nature=RuleNature.GATE, when="CI", enforcement="E", checks="every recorded file builds and gets its loop ids",
            lives="tests/test_fluid_loops.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.dhw_and_buffer_twins", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every file with a tank closes it; both loop ends agree; the forced-step share is bounded",
            lives="tests/test_dhw_chain_twins.py, test_space_heating_buffer_twins.py, test_part_load_twins.py", note="B (files with a tank)",
        ),
        RuleRecord(
            rule_id="gate.sizing_scalability", nature=RuleNature.GATE, when="CI", enforcement="E", checks="components scale ×5 with the floor area",
            lives="tests/test_sizing_energy_systems.py", note="R",
        ),
        RuleRecord(
            rule_id="gate.assembly_library", nature=RuleNature.GATE, when="CI", enforcement="E", checks="EF-75 on every library file",
            lives="tests/assemblies/test_assembly_library.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.assembly_contracts_run_balance_finite_bounds", nature=RuleNature.GATE, when="CI", enforcement="E", checks="per sample",
            lives="tests/assemblies/test_library_contracts.py; hisim/energy_system/assemblies/testing/checks.py", note="L",
        ),
        RuleRecord(
            rule_id="gate.assembly_contract_heat_delivered", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="no HEATING input left unconnected when a default connection serves it", lives="checks.py:HeatDelivery.lost, check_heat_delivered", note="B",
        ),
        RuleRecord(
            rule_id="gate.assembly_contract_room_heated", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every heated building's day-mean air within 1 K (Building.UNDERHEATING_TOLERANCE_IN_KELVIN) of its set point", lives="checks.py:check_room_heated",
            note="B (a heated building)",
        ),
        RuleRecord(
            rule_id="gate.isolation_harness", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every carrier moved through a metered port gets a meter partner; a port with no partner refuses the build",
            lives="hisim/energy_system/assemblies/testing/isolation.py, partners.py", note="the isolation system is demo",
        ),
        RuleRecord(
            rule_id="gate.composed_twin_gates", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every composed file equals its twin outside the neutral swaps", lives="tests/assemblies/test_twin_gates.py, test_composed_twins.py",
            note="every kind",
        ),
        RuleRecord(
            rule_id="gate.layering_lints", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="nothing outside hisim/renovisor imports it; subcomponents import only the kernel; a component imports no file format",
            lives="tests/test_renovisor_import_lint.py, test_subcomponent_import_lint.py, test_energy_system_layering.py",
            note="L; the rules package joins the layering test (imports no component)",
        ),
        RuleRecord(
            rule_id="gate.semgrep", nature=RuleNature.GATE, when="CI", enforcement="E", checks="no file write outside the allowlist; no print",
            lives=".semgrep/file_writes.yml, no_print.yml; quality.yml", note="L",
        ),
        RuleRecord(
            rule_id="gate.stray_files", nature=RuleNature.GATE, when="CI", enforcement="E", checks="a test dirties nothing outside its result directory",
            lives="tests/conftest.py:guard_against_stray_files", note="L",
        ),
        RuleRecord(
            rule_id="gate.renovisor_path_verification", nature=RuleNature.GATE, when="CI", enforcement="E",
            checks="every probe path translates and diffs against its base", lives="python -m hisim.renovisor verify; .github/workflows/path-verification.yml",
            note="R (RenoVisor)",
        ),
    )

    @classmethod
    def record(cls, rule_id: str) -> Optional[RuleRecord]:
        """Return the record with this id, for example ``energy_ports.room_heat_needs_receiver``, or ``None``."""
        return next((record for record in cls.RECORDS if record.rule_id == rule_id), None)
