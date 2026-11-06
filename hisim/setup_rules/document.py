"""The structural rules of an energy-system file: everything decidable from the document without its classes.

Declarative mode only, stage ``LOAD``, subject ``LoadedDocument``. The rules check that names are unique and each
component belongs to at most one group and at most one variant, that every reference resolves to a component the
file declares, that each entry says where its configuration comes from, that the input items of one consumer do not
contradict each other, that no value smuggles in an absolute filesystem path, and that a run record is only re-run.
Whether a preset exists, whether a config field is real and whether two ports can be wired needs the component
classes and is decided later, by the rules of the build; nothing here imports a component class, so an editor, a
schema exporter or a batch-authoring tool can check a file whose classes have not been written yet.

The rules check the *declared* set rather than the selected one. A component in a group nobody enabled and a
component in a variant option nobody selected are both written down, so both have to be well formed -- tomorrow's
file enables the group and selects the option -- and a reference into either is legal, because the expansion drops
such a reference rather than failing on it.

Which document the rules see: :func:`~hisim.energy_system.loader.load_energy_system` hands them the file as the
reader built it; a run (:meth:`~hisim.energy_system.executor.EnergySystemExecutor.construct`) hands them the file
after its imports and groups were expanded, because a file that imports assemblies names their members only once
they are expanded. Those are the two documents the structural validator saw before it moved here, so no rule accepts
or refuses anything it did not. The checks the reader makes while it builds the model -- a name that is no
identifier, an unknown key, an input item of no shape, a malformed reference or constructor block, a group without
its flag or members, a variant selecting no option -- stay in the reader (``hisim/setup_rules/inventory.py`` says
why for each).

Every fault keeps the catalogue message the validator raised, ``EF-nn at <location>: <problem>``, with the source of a
component an import produced, and the registry raises them together as ``EnergySystemStructureError``, a format
error. The validator stopped at the first fault; every rule here reports every fault, and no rule requires another:
each judges its own part of the document, and an unknown name leaves the others' statements intact.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Pattern, Sequence, Set, Tuple

from hisim.config import NameSyntax
from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError, EnergySystemStructureError
from hisim.energy_system.model import AggregatorFeed, DefaultInputs, EnergySystemFile, ExplicitWire
from hisim.setup_rules.rule import Fault, KeyPathStep, RuleStage, SetupRule
from hisim.setup_rules.subjects import LoadedDocument


class DocumentRule(SetupRule[LoadedDocument]):
    """The shared base of the structural rules: the subject they read, the error they raise and how a refusal becomes a fault.

    It declares no ``CODE``, so it is no rule itself. A rule builds the catalogue error the validator raised, without
    raising it, and :meth:`refuse` turns it into a fault with the same message, the source of an imported component and
    the location as a key path.
    """

    VIOLATION = EnergySystemStructureError

    #: The owner recorded for a component written outside every group and every variant. It is the ``components``
    #: block's own key, so a message can name where the collision it reports came from.
    TOP_LEVEL_OWNER: ClassVar[str] = "components"

    #: One step of a location: a key, followed by any number of list indices, ``inputs[0]``.
    LOCATION_STEP: ClassVar[Pattern[str]] = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")

    @classmethod
    def refuse(
        cls, subject: LoadedDocument, refusal: EnergySystemFormatError, component: Optional[str], key_path: Optional[Tuple[KeyPathStep, ...]] = None
    ) -> Fault:
        """Return the fault of one refusal: its catalogue message, the source of an imported component and its location.

        Example: the refusal ``EF-20 at components.boiler.inputs[0]: ...`` gives a fault of ``component="boiler"`` and
        ``key_path=("components", "boiler", "inputs", 0)`` whose message is the refusal's, followed by ``; see: hisim
        setup-rules show EF-20``.

        Args:
            subject: The document, for its path and its source map.
            refusal: The catalogue error the validator raised, built and not raised.
            component: The name of the component the fault is at, ``None`` for a group or a variant.
            key_path: The key path, when the refusal's location is not one (a file path); read from the location otherwise.

        Returns:
            The fault.
        """
        message = str(refusal)
        if subject.source_map is not None:
            message += subject.source_map.suffix(refusal.location)
        located = cls.key_path_of(refusal.location) if key_path is None else key_path
        return cls.fault(message, component=component, key_path=located, file=subject.file)

    @classmethod
    def key_path_of(cls, location: str) -> Tuple[KeyPathStep, ...]:
        """Return the keys and indices of a dotted location, as a fault holds them.

        Example: ``components.boiler.inputs[0].from`` gives ``("components", "boiler", "inputs", 0, "from")``, and
        ``groups.solar.components.pv`` gives ``("groups", "solar", "components", "pv")``.

        Args:
            location: The location a catalogue message prints.

        Returns:
            The key path.
        """
        path: List[KeyPathStep] = []
        for step in location.split("."):
            match = cls.LOCATION_STEP.match(step)
            if match is None:
                path.append(step)
                continue
            path.append(match.group(1))
            path.extend(int(index) for index in re.findall(r"\[(\d+)\]", match.group(2)))
        return tuple(path)

    @classmethod
    def group_memberships(cls, model: EnergySystemFile) -> List[Tuple[str, str, Optional[str]]]:
        """Return every group member with the block that claimed its name before: the walk of the group rules.

        The walk starts from the ungrouped components, owned by ``components``, and visits the groups in file order;
        each member is reported with its previous owner and then claimed by its group. Example: a file with an
        ungrouped ``pv`` and a group ``solar`` listing ``pv`` and ``battery`` gives ``[("solar", "pv", "components"),
        ("solar", "battery", None)]``.

        Args:
            model: The file.

        Returns:
            One ``(group, member, previous owner)`` per member, in file order.
        """
        owner: Dict[str, str] = {name: cls.TOP_LEVEL_OWNER for name in model.components}
        found: List[Tuple[str, str, Optional[str]]] = []
        for group_name, group in model.groups.items():
            for member in group.components:
                found.append((group_name, member, owner.get(member)))
                owner[member] = group_name
        return found

    @classmethod
    def option_memberships(cls, model: EnergySystemFile) -> List[Tuple[str, str, str, Optional[str]]]:
        """Return every option member with the block that claimed its name before: the walk of the variant rules.

        The walk starts from the ungrouped components and the groups' members and visits the variants in file order;
        each option member is reported with its previous owner and then claimed by its variant, so a name repeated
        across the options of one variant is owned by that variant and is no collision. Example: a variant ``heating``
        whose options ``gas`` and ``heat_pump`` both list ``controller`` gives ``[("heating", "gas", "controller",
        None), ("heating", "heat_pump", "controller", "heating")]``.

        Args:
            model: The file.

        Returns:
            One ``(variant, option, member, previous owner)`` per option member, in file order.
        """
        owner: Dict[str, str] = {name: cls.TOP_LEVEL_OWNER for name in model.components}
        for group_name, group in model.groups.items():
            for member in group.components:
                owner[member] = group_name
        found: List[Tuple[str, str, str, Optional[str]]] = []
        for variant_name, variant in model.variants.items():
            for option_name, option in variant.options.items():
                for member in option.components:
                    found.append((variant_name, option_name, member, owner.get(member)))
                    owner[member] = variant_name
        return found


class NoAbsolutePath(DocumentRule):
    """What it checks: no config or constructor value under a path-valued key is an absolute filesystem path.

    A key is path-valued when it is one of ``path``, ``paths``, ``directory``, ``file`` and ``filename`` or ends in
    one after an underscore; nested mappings and lists are followed. Both POSIX and Windows spellings count.

    Why: a file has to resolve on a colleague's machine and inside a container, so a path it contains is written
    symbolically and expanded against the local directory layout when the component is built. An absolute path makes
    the file work on exactly one machine, a portability bug that is cheap to catch here and expensive to diagnose later.

    Example of a fault: ``EF-05 at components.weather.config.source_path: '/home/me/weather.csv' is an absolute
    filesystem path.``

    How to fix: write the path symbolically, for example ``${inputs}/weather/berlin``.
    """

    CODE = "EF-05"
    NAME = "no-absolute-path"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    #: Nouns that make a config key path-valued: the key is one of them or ends in one after an underscore. A path
    #: written in an energy-system file is symbolic -- ``${inputs}/weather/...`` -- so that the file resolves on
    #: another machine.
    PATH_KEY_NOUNS: ClassVar[Tuple[str, ...]] = ("path", "paths", "directory", "file", "filename")

    #: Matches a Windows drive prefix, which is absolute even though it starts with a letter rather than a separator.
    WINDOWS_DRIVE_PATTERN: ClassVar[Pattern[str]] = re.compile(r"^[A-Za-z]:[\\/]")

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per absolute path in the config and constructor blocks of every declared entry."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            refusals = self.absolute_paths(entry.config, f"components.{entry.name}.config")
            if entry.constructor is not None:
                refusals += self.absolute_paths(entry.constructor.arguments, f"components.{entry.name}.constructor.{entry.constructor.name}")
            faults.extend(self.refuse(subject, refusal, entry.name) for refusal in refusals)
        return faults

    @classmethod
    def absolute_paths(cls, block: Mapping[str, Any], location: str) -> List[EnergySystemFormatError]:
        """Walk a config block and return the refusal of every absolute path under a path-valued key, in block order.

        Only keys that name a location are inspected, because an arbitrary string field may legitimately start with a
        slash. Nested mappings and lists are followed, so a path buried in a sub-block is found as well. The recorder's
        :class:`~hisim.energy_system.recording.builder.PortablePathGuard` walks a block it is about to write with the
        same method, so the two cannot disagree about what counts as an absolute path. Example: ``{"source_path":
        "/data/x.csv", "description": "/not/a/path"}`` at ``components.w.config`` gives one refusal, at
        ``components.w.config.source_path``.

        Args:
            block: The config or constructor-argument block to walk.
            location: Dotted key path of that block, grown as the walk descends.

        Returns:
            The refusals, built and not raised; empty for a portable block.
        """
        refusals: List[EnergySystemFormatError] = []
        for key, value in block.items():
            child = f"{location}.{key}"
            if isinstance(value, dict):
                refusals += cls.absolute_paths(value, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        refusals += cls.absolute_paths(item, f"{child}[{index}]")
                    elif cls.is_absolute_path(key, item):
                        refusals.append(cls.absolute_path_error(f"{child}[{index}]", item))
            elif cls.is_absolute_path(key, value):
                refusals.append(cls.absolute_path_error(child, value))
        return refusals

    @classmethod
    def is_absolute_path(cls, key: str, value: Any) -> bool:
        """Decide whether one config value is an absolute path under a path-valued key.

        The key name is what makes a string a path: a field called ``source_path`` holds a location, a field called
        ``description`` does not, and guessing from the value alone would reject legitimate strings. Example:
        ``("source_path", "/x")`` and ``("file", "C:/data")`` are absolute paths, ``("description", "/x")`` and
        ``("file", "${inputs}/x")`` are not.

        Returns:
            ``True`` when the key names a location and the value is an absolute path.
        """
        if not isinstance(value, str) or not value:
            return False
        if not any(key == noun or key.endswith("_" + noun) for noun in cls.PATH_KEY_NOUNS):
            return False
        return value.startswith("/") or value.startswith("\\") or cls.WINDOWS_DRIVE_PATTERN.match(value) is not None

    @staticmethod
    def absolute_path_error(location: str, value: str) -> EnergySystemFormatError:
        """Return the refusal of an absolute path, showing the symbolic form instead.

        Example: ``("components.w.config.file", "/x")`` gives ``EF-05 at components.w.config.file: '/x' is an absolute
        filesystem path. Write paths symbolically, ...``.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.ABSOLUTE_PATH,
            location,
            f"'{value}' is an absolute filesystem path.",
            remedy="Write paths symbolically, e.g. '${inputs}/weather/berlin', so the file stays portable.",
        )


class ValidName(DocumentRule):
    """What it checks: a component name with the address separator ``-`` is one the expansion of imports produced.

    The authored grammar never admits ``-``; the expansion of imports names its members by their serialized addresses
    (``pv-east-PVSystem``) and lists each in the file's address table (``assemblies_spec.md`` §2.4). The reader checks
    every other name against the identifier grammar as it reads it.

    Why: a hyphenated name not listed in the address table did not come from an expansion, so it is an authored name
    the grammar refuses.

    Example of a fault: ``EF-08 at components.pv-east: 'pv-east' is not a usable component name: only the expansion of
    imports names a component with the address separator '-', and this name is not one it produced.``

    How to fix: rename the component to an identifier, ``pv_east``.
    """

    CODE = "EF-08"
    NAME = "valid-name"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per declared component name that is no identifier and no address the expansion produced."""
        faults: List[Fault] = []
        for name in subject.model.declared_components():
            identity = subject.model.addresses.get(name)
            if not NameSyntax.is_identifier(name) and (identity is None or identity.address != name):
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.INVALID_NAME,
                    f"components.{name}",
                    f"'{name}' is not a usable component name: only the expansion of imports names a component "
                    "with the address separator '-', and this name is not one it produced.",
                )
                faults.append(self.refuse(subject, refusal, name))
        return faults


class NoMetadataOnAPlainRun(DocumentRule):
    """What it checks: a file with a ``metadata`` block is run only when the run declares itself a re-run of a record.

    A file that is only loaded, not run, is not checked.

    Why: a ``metadata`` block is written by a generator, never by hand, so a file that has one is a record of a
    previous run rather than an authored energy system. Re-running a record reproduces a run exactly, but it is a
    different intent from running an authored file, and silently accepting both would make it impossible to tell
    which one happened.

    Example of a fault: ``EF-09 at results/realized.energy_system.yaml: this file carries a 'metadata' block, which
    only a generated run record has.``

    How to fix: re-run it explicitly (``rerun=True``) if that is what you mean, or strip the metadata block to treat it
    as an authored energy system.
    """

    CODE = "EF-09"
    NAME = "no-metadata-on-a-plain-run"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return a fault when the run is not a re-run and the file carries ``metadata``; nothing for a file only loaded."""
        if subject.rerun is not False or subject.model.metadata is None:
            return []
        refusal = EnergySystemFormatError(
            EnergySystemErrorId.METADATA_ON_A_PLAIN_RUN,
            subject.file or RawDocument.TEXT_ORIGIN,
            "this file carries a 'metadata' block, which only a generated run record has.",
            remedy=(
                "Re-run it explicitly if that is what you mean, or strip the metadata block to "
                "treat it as an authored energy system."
            ),
        )
        return [self.refuse(subject, refusal, None, key_path=("metadata",))]


class OneConfigurationOrigin(DocumentRule):
    """What it checks: no entry names both a ``preset`` and a ``constructor``.

    Why: a preset and a named constructor are two ways of producing the same thing, the component's configuration
    before overrides, so naming both leaves it open which one runs.

    Example of a fault: ``EF-11 at components.boiler: 'boiler' names both preset 'condensing_gas' and constructor
    'sized_to'.``

    How to fix: keep exactly one of ``preset`` and ``constructor``.
    """

    CODE = "EF-11"
    NAME = "one-configuration-origin"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per declared entry that names both a preset and a constructor."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            if entry.preset is not None and entry.constructor is not None:
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.PRESET_AND_CONSTRUCTOR,
                    f"components.{entry.name}",
                    f"'{entry.name}' names both preset '{entry.preset}' and constructor '{entry.constructor.name}'.",
                    remedy="An entry carries exactly one of 'preset' and 'constructor'.",
                )
                faults.append(self.refuse(subject, refusal, entry.name))
        return faults


class ConfigurationStated(DocumentRule):
    """What it checks: every entry names a ``preset``, a ``constructor`` or a non-empty ``config`` block.

    Whether a ``config`` block alone is complete depends on the class and is decided by the build; this rule makes
    the weaker check that something is written at all.

    Why: an entry that names none of the three says nothing about how its component is configured.

    Example of a fault: ``EF-12 at components.boiler: 'boiler' says nothing about how it is configured.``

    How to fix: write ``preset``, or ``constructor``, or a complete ``config`` block.
    """

    CODE = "EF-12"
    NAME = "configuration-stated"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per declared entry with no preset, no constructor and an empty config."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            if entry.preset is None and entry.constructor is None and not entry.config:
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.NO_CONFIGURATION_SOURCE,
                    f"components.{entry.name}",
                    f"'{entry.name}' says nothing about how it is configured.",
                    remedy="Write 'preset', or 'constructor', or a complete 'config' block.",
                )
                faults.append(self.refuse(subject, refusal, entry.name))
        return faults


class KnownSource(DocumentRule):
    """What it checks: every input item names a source component the file declares.

    The declared components include those of groups nobody enabled and of options nobody selected; the expansion
    drops a reference into them rather than failing on it.

    Why: wiring is written on the consumer, so a misspelled source would be wired to nothing at all and no later stage
    would notice.

    Example of a fault: ``EF-20 at components.boiler.inputs[0]: 'boiler' declares an input from 'weathr', which is not
    a component of this energy system. Did you mean: weather? Valid components: boiler, weather.``

    How to fix: name a component of the file, as the message lists them.
    """

    CODE = "EF-20"
    NAME = "known-source"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per input item whose source is no declared component."""
        components = subject.model.declared_components()
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            for index, item in enumerate(entry.inputs):
                if item.source not in components:
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.UNKNOWN_SOURCE,
                        f"components.{entry.name}.inputs[{index}]",
                        f"'{entry.name}' declares an input from '{item.source}', which is not a component of this energy system.",
                        alternatives=tuple(components),
                        alternatives_label="components",
                        offending_value=item.source,
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
        return faults


class OneInputSpelling(DocumentRule):
    """What it checks: one consumer takes from one source either explicit wires or aggregator feeds, never both, and at most one defaults item.

    Why: wires and feeds from the same source would describe the same relationship twice and in two different ways,
    and a repeated defaults item would apply the source's declared connections twice.

    Example of a fault: ``EF-24 at components.meter.inputs[1]: 'meter' already declares wires from 'pv'.``

    How to fix: keep one spelling per pair: a defaults item plus either explicit wires or aggregator feeds.
    """

    CODE = "EF-24"
    NAME = "one-input-spelling"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per input item that mixes or repeats the spelling its consumer already uses for its source."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            spellings: Dict[str, Set[str]] = {}
            for index, item in enumerate(entry.inputs):
                kinds = spellings.setdefault(item.source, set())
                kind = self.spelling_of(item)
                if kind is None:
                    continue
                if self.clashes(kinds, kind):
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.MIXED_INPUT_SPELLING,
                        f"components.{entry.name}.inputs[{index}]",
                        f"'{entry.name}' already declares {' and '.join(sorted(kinds))} from '{item.source}'.",
                        remedy=(
                            "A (consumer, source) pair carries at most one defaults item plus either "
                            "explicit wires or aggregator feeds, never both."
                        ),
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
                kinds.add(kind)
        return faults

    @staticmethod
    def spelling_of(item: Any) -> Optional[str]:
        """Return the spelling of an input item: ``defaults``, ``wires`` or ``feeds``; ``None`` for anything else.

        Example: ``DefaultInputs(source="weather")`` gives ``"defaults"``, an ``ExplicitWire`` ``"wires"``.
        """
        if isinstance(item, DefaultInputs):
            return "defaults"
        if isinstance(item, ExplicitWire):
            return "wires"
        if isinstance(item, AggregatorFeed):
            return "feeds"
        return None

    @staticmethod
    def clashes(kinds: Set[str], kind: str) -> bool:
        """Return whether an item of this spelling mixes or repeats the spellings already used for its pair.

        Explicit wires and aggregator feeds exclude each other, and a defaults item may appear once; wires and feeds
        themselves may repeat, since a consumer takes several ports or several flows from one source. Example:
        ``({"wires"}, "feeds")`` and ``({"defaults"}, "defaults")`` clash, ``({"defaults"}, "wires")`` does not.
        """
        if kind == "defaults":
            return "defaults" in kinds
        return (kind == "wires" and "feeds" in kinds) or (kind == "feeds" and "wires" in kinds)


class NoDuplicateFeed(DocumentRule):
    """What it checks: one consumer is fed each source output once, as the file writes its feeds.

    The wiring rule ``EF-25`` checks the same of the participant outputs once the feeds are resolved; this rule
    checks the feeds as written, before any class is imported, unselected options included. Its faults carry the
    catalogue code ``EF-25`` the validator raised.

    Why: an aggregator accepts each source output once; a second feed would leave the winner to list order.

    Example of a fault: ``EF-25 at components.meter.inputs[1]: 'meter' is fed twice from 'pv.ElectricityOutput'.``

    How to fix: delete the second feed.
    """

    CODE = "EF-2C"
    NAME = "no-duplicate-feed"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per aggregator feed whose source output the consumer was already fed."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            fed_outputs: Set[Tuple[str, str]] = set()
            for index, item in enumerate(entry.inputs):
                if not isinstance(item, AggregatorFeed):
                    continue
                feed_key = (item.source, item.output or "")
                if feed_key in fed_outputs:
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.DUPLICATE_FEED,
                        f"components.{entry.name}.inputs[{index}]",
                        f"'{entry.name}' is fed twice from '{item.source}{'.' + item.output if item.output else ''}'.",
                        remedy="An aggregator accepts each source output once.",
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
                fed_outputs.add(feed_key)
        return faults


class NoDuplicateWire(DocumentRule):
    """What it checks: one consumer wires each of its inputs once, as the file writes its wires.

    The wiring rule ``EF-26`` checks the same once the defaults items are expanded; this rule checks the wires as
    written, before any class is imported, unselected options included. Its faults carry the catalogue code ``EF-26``
    the validator raised.

    Why: an input takes exactly one source; a second wire would leave the winner to list order.

    Example of a fault: ``EF-26 at components.boiler.inputs[2]: 'boiler' wires its input 'TemperatureOutside' twice.``

    How to fix: delete one of the two wires.
    """

    CODE = "EF-2D"
    NAME = "no-duplicate-wire"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per explicit wire into an input the consumer already wired."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            wired_inputs: Set[str] = set()
            for index, item in enumerate(entry.inputs):
                if not isinstance(item, ExplicitWire):
                    continue
                if item.input in wired_inputs:
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.DUPLICATE_WIRE,
                        f"components.{entry.name}.inputs[{index}]",
                        f"'{entry.name}' wires its input '{item.input}' twice.",
                        remedy="An input takes exactly one source.",
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
                wired_inputs.add(item.input)
        return faults


class KnownSizingSource(DocumentRule):
    """What it checks: every reference of a ``sizing_sources`` block names a component the file declares.

    Why: a dangling provider would leave the consuming field unsized with no explanation.

    Example of a fault: ``EF-40 at components.battery.sizing_sources.pv_peak_power_in_watt: 'battery' takes
    'pv_peak_power_in_watt' from 'pv_sout', which is not a component of this energy system. Did you mean: pv_south?``

    How to fix: name a component of the file, as the message lists them.
    """

    CODE = "EF-40"
    NAME = "known-sizing-source"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per sizing reference whose component is no declared component."""
        components = subject.model.declared_components()
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            for fact, reference in entry.sizing_references():
                if reference.component not in components:
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.UNKNOWN_SIZING_SOURCE,
                        f"components.{entry.name}.sizing_sources.{fact}",
                        f"'{entry.name}' takes '{fact}' from '{reference.component}', which is not a component of this energy system.",
                        alternatives=tuple(components),
                        alternatives_label="components",
                        offending_value=reference.component,
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
        return faults


class SizingSourceNamesItsFact(DocumentRule):
    """What it checks: every reference of a ``sizing_sources`` block names the very fact it is written under.

    Why: the line answers where *that* fact comes from, and a source cannot rename a fact on the way.

    Example of a fault: ``EF-41 at components.battery.sizing_sources.pv_peak_power_in_watt: 'battery' maps
    'pv_peak_power_in_watt' onto 'pv_south.roof_area_in_m2', which names the fact 'roof_area_in_m2'.``

    How to fix: write ``<component>.<the fact of the key>``, ``pv_south.pv_peak_power_in_watt``.
    """

    CODE = "EF-41"
    NAME = "sizing-source-names-its-fact"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per sizing reference whose fact differs from the key it is written under."""
        faults: List[Fault] = []
        for entry in subject.model.declared_entries():
            for fact, reference in entry.sizing_references():
                if reference.fact != fact:
                    refusal = EnergySystemFormatError(
                        EnergySystemErrorId.SIZING_FACT_MISMATCH,
                        f"components.{entry.name}.sizing_sources.{fact}",
                        f"'{entry.name}' maps '{fact}' onto '{reference.text}', which names the fact '{reference.fact}'.",
                        remedy=f"Write '{reference.component}.{fact}'; a source line cannot rename a fact.",
                    )
                    faults.append(self.refuse(subject, refusal, entry.name))
        return faults


class OneGroupPerComponent(DocumentRule):
    """What it checks: no component is listed in two groups.

    Why: a group is a set with an on/off flag, not a namespace; a component in two groups would be switched by two
    flags at once.

    Example of a fault: ``EF-51 at groups.storage.components.battery: component 'battery' is already listed in group
    'solar'.``

    How to fix: list the component in one group only.
    """

    CODE = "EF-51"
    NAME = "one-group-per-component"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per group member that an earlier group already lists."""
        faults: List[Fault] = []
        for group_name, member, previous in self.group_memberships(subject.model):
            if previous is not None and previous != self.TOP_LEVEL_OWNER:
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.COMPONENT_IN_TWO_GROUPS,
                    f"groups.{group_name}.components.{member}",
                    f"component '{member}' is already listed in group '{previous}'.",
                    remedy="A component belongs to at most one group; groups are sets, not namespaces.",
                )
                faults.append(self.refuse(subject, refusal, member))
        return faults


class NoNameCollision(DocumentRule):
    """What it checks: component, group and variant names do not collide.

    Component names are global: a grouped component may not reuse the name of an ungrouped one, a group or a variant
    may not share a name with a component, and a variant option may not declare a component written outside the
    variant. A name repeated across the options of one variant is no collision: only one of those options exists.

    Why: a reference is a bare name, so two things of one name would make it ambiguous; and an option states its
    components in full, it never overrides one written elsewhere.

    Example of a fault: ``EF-52 at groups.solar.components.pv: component 'pv' has the same name as an ungrouped
    component.``

    How to fix: rename one of the two, or move the component into every option of the variant, or out of it.
    """

    CODE = "EF-52"
    NAME = "no-name-collision"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per name that collides with a component, a group or a variant."""
        model = subject.model
        components = model.declared_components()
        faults: List[Fault] = []
        for group_name, member, previous in self.group_memberships(model):
            if previous == self.TOP_LEVEL_OWNER:
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"groups.{group_name}.components.{member}",
                    f"component '{member}' has the same name as an ungrouped component.",
                    remedy="Component names are global across the whole file, groups included.",
                )
                faults.append(self.refuse(subject, refusal, member))
        for group_name in model.groups:
            if group_name in components:
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"groups.{group_name}",
                    f"group '{group_name}' has the same name as a component.",
                    remedy="A reference is a bare name, so a group and a component may not share one.",
                )
                faults.append(self.refuse(subject, refusal, None))
        for variant_name in model.variants:
            if variant_name in components or variant_name in model.groups:
                collided = "component" if variant_name in components else "group"
                refusal = EnergySystemFormatError(
                    EnergySystemErrorId.DUPLICATE_NAME,
                    f"variants.{variant_name}",
                    f"variant '{variant_name}' has the same name as a {collided}.",
                    remedy="A reference is a bare name, so a variant, a group and a component may not share one.",
                )
                faults.append(self.refuse(subject, refusal, None))
        for variant_name, option_name, member, previous in self.option_memberships(model):
            if previous is None or previous == variant_name or previous in model.variants:
                continue
            where = "an ungrouped component" if previous == self.TOP_LEVEL_OWNER else f"the group '{previous}'"
            refusal = EnergySystemFormatError(
                EnergySystemErrorId.DUPLICATE_NAME,
                f"variants.{variant_name}.options.{option_name}.components.{member}",
                f"component '{member}' has the same name as {where}.",
                remedy=(
                    "An option states its components in full and never overrides one written "
                    "elsewhere; move the component into every option, or out of the variant."
                ),
            )
            faults.append(self.refuse(subject, refusal, member))
        return faults


class OneVariantPerComponent(DocumentRule):
    """What it checks: no component is declared by two variants.

    Why: a variant is an exclusive choice; two choices cannot both decide the same component, and no selection could
    resolve which one does.

    Example of a fault: ``EF-57 at variants.storage.options.large.components.controller: component 'controller' is
    already declared by the variant 'heating'.``

    How to fix: declare the component in one variant only, or outside every variant.
    """

    CODE = "EF-57"
    NAME = "one-variant-per-component"
    STAGE = RuleStage.LOAD
    ENFORCED = True

    def check(self, subject: LoadedDocument) -> Sequence[Fault]:
        """Return one fault per option member that another variant already declares."""
        faults: List[Fault] = []
        for variant_name, option_name, member, previous in self.option_memberships(subject.model):
            if previous is None or previous == variant_name or previous not in subject.model.variants:
                continue
            refusal = EnergySystemFormatError(
                EnergySystemErrorId.COMPONENT_IN_TWO_VARIANTS,
                f"variants.{variant_name}.options.{option_name}.components.{member}",
                f"component '{member}' is already declared by the variant '{previous}'.",
                remedy=(
                    "A component belongs to at most one variant; two exclusive choices cannot "
                    "both decide the same component."
                ),
            )
            faults.append(self.refuse(subject, refusal, member))
        return faults
