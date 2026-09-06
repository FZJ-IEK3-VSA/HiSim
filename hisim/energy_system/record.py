"""The realized record: what a run actually built, written back as an energy-system file.

An authored energy-system file is a statement of intent. It names presets instead of values,
leaves the numbers a law owns unwritten, keeps the switched-off parts of the household around
under a flag, and says nothing about where an unambiguous sizing fact came from. That is what
makes it pleasant to write and useless as evidence: two runs of the same file a year apart can
differ in every number without a single character of the file changing.

The realized record is the other half of the pair. It is the same format and loads through the
same reader, but it states facts: every preset and every named constructor expanded into a
complete ``config`` block, every value a law computed written as the number it computed, every
switched-off group and its components absent, and one ``sizing_sources`` line for every fact any
component read — including the ones the author could leave out because only one component
provided them. Written that way, re-running the record sizes nothing at all, which is exactly
what makes it a reproduction: there is no decision left for a second run to make differently.

Three rules keep the record honest and are enforced here rather than trusted. Paths go back to
their portable ``${var}`` spelling, so a record written on one machine reproduces on another.
Enums are written by member name, the one spelling this format uses, rather than by whatever
internal value the class happens to store. And nothing may still say ``AUTO``: a record that
carried the sentinel would look re-executable and quietly size itself again, so the writer refuses
to produce one. The reproduction block a record carries on top of all this — the version, the
commit, the files the run started from — is assembled in :mod:`hisim.energy_system.metadata`.

The promise is also checked, not only made. Re-executing a record runs through this module a
second time, and the two things that would break the promise are refused: a field the second run
had to size, and a record that came out saying something other than the record it was given.
"""

# clean

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.config.sizing import _AutoSize
from hisim.energy_system.codec import ConfigValueCodec
from hisim.energy_system.configure import EntryConfigurator
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordError
from hisim.energy_system.metadata import RunMetadata
from hisim.energy_system.model import (
    AnySizingSource,
    ComponentEntry,
    EnergySystemFile,
    Group,
    SourceReference,
)
from hisim.energy_system.path_resolver import PathFieldCodec, PathResolver

if TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from hisim.energy_system.executor import BuiltEnergySystem


class ConfigBlockWriter:
    """Turns a resolved configuration object into the ``config`` block a record writes.

    The block has to satisfy three audiences at once. A person reads it, so enums appear as the
    member names the rest of the format uses rather than as the internal values a class happens
    to store. The reader of the record loads it, so every value is plain YAML data — no objects,
    no sentinels. And a second machine reproduces it, so filesystem locations go back to their
    portable ``${var}`` spelling before they are written.

    What is *not* written is the identity: the entry's key is the component's whole name in this
    format, and a second spelling of it inside the block would make the record contradict itself.
    """

    #: The one field of every configuration that a record never writes, because the entry's key
    #: already carries it.
    IDENTITY_FIELD: ClassVar[str] = "component_id"

    def __init__(self, resolver: PathResolver) -> None:
        """Prepares the writer for one run.

        Args:
            resolver: The registry that turns absolute filesystem locations back into the
                ``${var}`` references a portable record carries.
        """
        self.resolver = resolver

    def block(self, name: str, config: Any) -> Dict[str, Any]:
        """Renders one configuration as the complete mapping a record entry carries.

        Args:
            name: The component's name, used in the message when a value cannot be written.
            config: The resolved configuration object.

        Returns:
            Every field of the configuration except its identity, in declaration order.

        Raises:
            EnergySystemRecordError: ``EF-60`` when a value is neither plain data nor anything
                the writer can reduce to plain data.
        """
        codec = ConfigValueCodec(type(config))
        dumped = config.to_dict()
        block = {
            key: codec.wire_value(key, self.plain(value, name, key))
            for key, value in dumped.items()
            if key != self.IDENTITY_FIELD
        }
        path_fields = [key for key in block if EntryConfigurator.is_path_field(key)]
        if path_fields:
            block = PathFieldCodec.symbolize(block, path_fields, self.resolver)
        return block

    @classmethod
    def plain(cls, value: Any, name: str, path: str) -> Any:
        """Reduces one configuration value to the plain data a YAML document holds.

        Enums become their member name, nested configuration objects become mappings of their
        own fields, and sequences become lists. A set is written in sorted order, because a
        record that changed the order of its own values between two runs of the same system
        would break the one promise it makes.

        Args:
            value: The value to reduce.
            name: The component's name, for the message.
            path: The dotted path of the value inside the block, for the message.

        Returns:
            The plain equivalent.

        Raises:
            EnergySystemRecordError: ``EF-60`` when the value is not plain data and cannot be
                reduced to it.
        """
        if isinstance(value, enum.Enum):
            return value.name
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: cls.plain(getattr(value, field.name), name, f"{path}.{field.name}")
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): cls.plain(item, name, f"{path}.{key}") for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls.plain(item, name, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, (set, frozenset)):
            return sorted(cls.plain(item, name, path) for item in value)
        raise EnergySystemRecordError(
            EnergySystemErrorId.RECORD_NOT_CONCRETE,
            f"components.{name}.config.{path}",
            f"the configuration of '{name}' holds {value!r} at '{path}', which is not plain "
            "data and cannot be written into a record.",
        )


class SizingSourceWriter:
    """Writes the ``sizing_sources`` block that makes a record size nothing when re-run.

    An authored file writes a sizing source only where the fact has more than one provider; the
    rest is left to the binding rule, which finds the unique provider by itself. A record cannot
    afford that. Its whole purpose is that a second run makes no decisions, and "the unique
    provider" is a decision — one that would come out differently the moment a component is
    added to the file. So the record writes every fact every component read, whether the author
    had to or not.

    The block is built from what actually happened rather than from what could have happened:
    the resolution report lists one lookup per fact a consumer really read, naming the provider
    that answered it, and a fact nothing read produces no line.
    """

    def __init__(self, lookups: Any) -> None:
        """Collects the reads of one run, grouped by consumer and fact.

        Args:
            lookups: The fact lookups of the run's resolution report, in resolution order.
        """
        self.reads: Dict[str, Dict[str, List[str]]] = {}
        for lookup in lookups:
            providers = self.reads.setdefault(lookup.consumer, {}).setdefault(lookup.fact, [])
            if lookup.source not in providers:
                providers.append(lookup.source)

    def block(self, entry: ComponentEntry) -> Dict[str, AnySizingSource]:
        """Builds one entry's complete sizing block: what it wrote, plus what it read.

        The author's own lines come first and in their written shape, because a list and a
        scalar mean different things and a record must not turn one into the other. The reads
        the author did not have to write follow, one line per fact.

        Args:
            entry: The component entry as the expanded file carries it.

        Returns:
            The block, empty when the component neither wrote nor read a sizing source.
        """
        block: Dict[str, AnySizingSource] = dict(entry.sizing_sources)
        for fact, providers in self.reads.get(entry.name, {}).items():
            if fact in block:
                continue
            references = tuple(SourceReference(component=provider, fact=fact) for provider in providers)
            block[fact] = references[0] if len(references) == 1 else references
        return block

    def added_facts(self, entry: ComponentEntry) -> Tuple[str, ...]:
        """Names the facts the record wrote for a component that the author did not.

        Args:
            entry: The component entry as the expanded file carries it.

        Returns:
            The facts present in the record's block and absent from the authored one, in the
            order the record writes them.
        """
        return tuple(
            fact for fact in self.reads.get(entry.name, {}) if fact not in entry.sizing_sources
        )


class RecordRealizer:
    """Rewrites one built energy system into the record of what it built.

    Constructed over a finished build — the expanded file, the resolved configurations and the
    report that says where every number came from — and asked once for the record. The rewrite
    is entry by entry and changes exactly three things per entry: the configuration origin
    becomes a complete block, the sizing sources become complete, and nothing else moves, so a
    record diffs against the file it came from line for line.

    Groups survive as groups. A group that was on is part of the system's structure and deleting
    it would make the record impossible to compare with the file; a group that was off is gone
    already, because expansion removed it before anything was built.
    """

    def __init__(self, built: "BuiltEnergySystem", resolver: PathResolver) -> None:
        """Prepares the realizer for one built system.

        Args:
            built: The finished build, carrying the expanded file, the configurations, the
                resolution report and the names of the files the run started from.
            resolver: The registry that symbolizes filesystem locations.
        """
        self.built = built
        self.configs = ConfigBlockWriter(resolver)
        self.sources = SizingSourceWriter(built.configured.report.lookups)

    def realize(self) -> EnergySystemFile:
        """Builds the record of the run.

        Returns:
            The same energy system with every entry stated in full and a metadata block added.

        Raises:
            EnergySystemRecordError: ``EF-60`` when a value cannot be written as plain data or
                the finished record is not fully concrete.
        """
        components = {
            name: self.entry(entry) for name, entry in self.built.model.components.items()
        }
        groups = {
            name: self._group(group) for name, group in self.built.model.groups.items()
        }
        record: EnergySystemFile = self.built.model.model_copy(
            update={
                "components": components,
                "groups": groups,
                "metadata": RunMetadata.collect(
                    RunMetadata.describe_source(self.built.source_energy_system),
                    RunMetadata.describe_source(self.built.source_simulation_parameters),
                ),
            }
        )
        assert_fully_concrete(record)
        return record

    def entry(self, entry: ComponentEntry) -> ComponentEntry:
        """Rewrites one entry into the statement of what was actually configured.

        Args:
            entry: The entry as the expanded file carries it.

        Returns:
            The same entry with ``preset`` and ``constructor`` gone, a complete ``config`` block
            in their place and a complete ``sizing_sources`` block.
        """
        config = self.built.configured.config_of(entry.name)
        return entry.model_copy(
            update={
                "preset": None,
                "constructor": None,
                "config": self.configs.block(entry.name, config),
                "sizing_sources": self.sources.block(entry),
            }
        )

    def _group(self, group: Group) -> Group:
        """Rewrites the entries of one surviving group.

        Args:
            group: The group as the expanded file carries it; it is enabled by construction.

        Returns:
            The group with every entry realized.
        """
        return group.model_copy(
            update={
                "components": {name: self.entry(entry) for name, entry in group.components.items()}
            }
        )


def assert_fully_concrete(record: EnergySystemFile) -> None:
    """Verifies that a record states facts only, and raises when it does not.

    Three things would make a record look re-executable while quietly deciding something on the
    next run: an entry still naming a preset or a constructor, whose expansion could change; a
    value still spelled ``AUTO``, which would be sized again; and, indirectly, both together.
    The check runs on every record that is written, not only in tests, because a breach means
    the machinery lied about what it did rather than that a file was wrong.

    Args:
        record: The record about to be written.

    Raises:
        EnergySystemRecordError: ``EF-60`` naming the entry and, for a sentinel, the path of the
            value inside its configuration block.
    """
    for name, entry in record.all_components().items():
        if entry.preset is not None or entry.constructor is not None:
            raise EnergySystemRecordError(
                EnergySystemErrorId.RECORD_NOT_CONCRETE,
                f"components.{name}",
                f"the record of '{name}' still names a preset or a constructor instead of the "
                "configuration it produced.",
            )
    assert_no_sentinels(record)


def assert_no_sentinels(record: EnergySystemFile) -> None:
    """Verifies that no value of a generated file still asks to be sized, and raises when one does.

    The weaker half of :func:`assert_fully_concrete`, separated because two kinds of generated file
    need it and only one of them may also be checked for presets. A realized record must state its
    configurations in full, so a preset in it is a breach. A file recorded from a Python setup must
    do the opposite and name the preset a configuration was built from, and it is exactly that
    naming which lets a later conversion shrink the file. What both must satisfy is this: a value
    still spelled ``AUTO`` would be computed again on the next run instead of reproduced.

    Args:
        record: The generated file about to be written.

    Raises:
        EnergySystemRecordError: ``EF-60`` naming the entry and the path of the value inside its
            configuration block.
    """
    for name, entry in record.all_components().items():
        for path in _sentinel_paths(entry.config, ()):
            raise EnergySystemRecordError(
                EnergySystemErrorId.RECORD_NOT_CONCRETE,
                f"components.{name}.config.{'.'.join(path)}",
                f"the generated file for '{name}' still says '{_AutoSize.WIRE_SPELLING}', so "
                "re-running it would size that field again instead of reproducing this run.",
            )


def _sentinel_paths(value: Any, prefix: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    """Collects the paths at which a value tree still carries the ``AUTO`` wire spelling.

    Args:
        value: A value of a record's configuration block.
        prefix: The key path leading to it.

    Returns:
        One path per occurrence, empty when the tree is concrete.
    """
    if isinstance(value, str):
        return [prefix] if value == _AutoSize.WIRE_SPELLING else []
    found: List[Tuple[str, ...]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.extend(_sentinel_paths(item, prefix + (str(key),)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_sentinel_paths(item, prefix + (str(index),)))
    return found


def realize(
    built: "BuiltEnergySystem", *, path_resolver: Optional[PathResolver] = None
) -> EnergySystemFile:
    """Builds the realized record of one built energy system.

    The record is the file the run would have had to be written as for nothing to be decided:
    presets and constructors expanded, every computed number written, every read sizing fact
    named, disabled groups absent and a metadata block naming the version that produced it.
    Nothing has to have been simulated — a system that was only built records just as well,
    which is what lets a tool show what a file would produce without producing it.

    Args:
        built: The finished build, as :func:`hisim.energy_system.executor.build_energy_system`
            returns it.
        path_resolver: The registry that symbolizes filesystem locations; the one the build used
            when omitted, and this machine's default when the build carries none.

    Returns:
        The record, ready to be written and to be loaded again.

    Raises:
        EnergySystemRecordError: ``EF-60`` when the record would not be fully concrete.
    """
    resolver = path_resolver or built.path_resolver or PathResolver.default()
    return RecordRealizer(built, resolver).realize()


def verify_rerun(built: "BuiltEnergySystem", record: EnergySystemFile) -> None:
    """Checks that re-executing a record really did reproduce it, and raises when it did not.

    Re-running a record is the format's reproduction guarantee, and a guarantee nobody checks is
    a hope. Two properties make it true and both are verified here. Nothing was sized: a record
    states every number, so a second run that computed one has been handed something other than a
    record, or a law has started firing on a value that used to be pinned. And the record the
    second run writes says the same as the one it read, apart from the two metadata keys naming
    the files it came from, which legitimately differ.

    Comments are not part of the comparison. The first run of an authored file sizes fields and
    says so in the margin; a re-run of its record sizes nothing and therefore has nothing to say,
    which is the correct rendering of what happened rather than a difference in the record.

    Args:
        built: The finished build of the re-run.
        record: The record that re-run has just realized.

    Raises:
        EnergySystemRecordError: ``EF-61`` when a field was sized or the two records disagree.
    """
    from hisim.energy_system.loader import dump_energy_system  # noqa: PLC0415

    for name, config in built.configured.configs:
        sized = getattr(config, "sizing_record", ()) or ()
        if sized:
            raise EnergySystemRecordError(
                EnergySystemErrorId.RERUN_NOT_REPRODUCED,
                f"components.{name}.config",
                f"re-running a record sized '{sized[0].field}' of '{name}' instead of taking the "
                "value the record states.",
            )
    before = dump_energy_system(_without_source_metadata(built.model))
    after = dump_energy_system(_without_source_metadata(record))
    if before != after:
        raise EnergySystemRecordError(
            EnergySystemErrorId.RERUN_NOT_REPRODUCED,
            built.model.name,
            "the record this re-run produced differs from the record it was given; the first "
            f"differing line is {_first_difference(before, after)}.",
        )


def _without_source_metadata(model: EnergySystemFile) -> EnergySystemFile:
    """Returns the same model with the two metadata keys naming its input files removed.

    Args:
        model: A record, or the model a record was realized from.

    Returns:
        A copy whose metadata block carries everything but the ``source_`` keys.
    """
    return model.model_copy(update={"metadata": RunMetadata.without_sources(model.metadata)})


def _first_difference(before: str, after: str) -> str:
    """Renders the first line at which two records disagree, for the message.

    Args:
        before: The record that was read.
        after: The record that was written.

    Returns:
        A one-line rendering naming the line number and both sides.
    """
    given, produced = before.splitlines(), after.splitlines()
    for number, (left, right) in enumerate(zip(given, produced), start=1):
        if left != right:
            return f"{number}: given {left!r}, produced {right!r}"
    longer = produced if len(produced) > len(given) else given
    return f"{min(len(given), len(produced)) + 1}: only one side has {longer[min(len(given), len(produced))]!r}"
