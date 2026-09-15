"""Reading one component entry: its class, its configuration, its inputs and its sizing.

An entry is where almost all of an energy-system file's text sits, and it is the same block
wherever it is written — at the top level, inside a group or inside a variant option — so the
code that reads one lives apart from the code that reads the document around it. That split is
what lets a new container of components be added to the format without the entry reader
learning about it, and it keeps both modules small enough to read in one sitting.

Everything decided here is decidable from the document alone. The reader classifies each input
item by the keys it carries, parses every reference through the format's one grammar and rejects
the first shape problem it meets, naming the offending key and listing the ones the shape does
accept. No component class is imported, so nothing is said yet about whether a preset, a field
or a port exists — that is the class-bound stage's business, much later in the lifecycle.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import (
    AggregatorFeed,
    AnyInputItem,
    ComponentEntry,
    ConstructorCall,
    DefaultInputs,
    DispatchSpec,
    ExplicitWire,
    Group,
    SourceReference,
)
from hisim.energy_system.names import NameRules


class EntryReader:
    """Builds component entries and the mappings that hold them, one block at a time.

    The reader is a namespace of classmethods rather than an object: it carries no state
    between two entries, because an entry is complete in itself and nothing about the one
    before it changes how the next is read. Callers hand it a raw block and the dotted key
    path that block sits at, and get back the frozen models or an exception naming the path.

    Which keys classify an input item as which of the three shapes is the one piece of
    knowledge that would otherwise be spread over several call sites, so it is pinned here as
    class constants and used by both the classification and the per-shape key check.
    """

    #: The keys that classify an input item as an aggregator feed. Any one of them is
    #: enough, so a feed missing its tags is reported as an incomplete feed rather than
    #: as an item of no recognisable kind.
    FEED_KEYS: ClassVar[Tuple[str, ...]] = ("tags", "weight", "dispatch", "component_type")

    #: The keys an explicit wire may carry, in canonical order.
    WIRE_KEYS: ClassVar[Tuple[str, ...]] = ("input", "from")

    #: The keys an aggregator feed may carry, in canonical order.
    FEED_ITEM_KEYS: ClassVar[Tuple[str, ...]] = ("from", "component_type", "tags", "weight", "dispatch")

    #: The keys a dispatch back-channel may carry.
    DISPATCH_KEYS: ClassVar[Tuple[str, ...]] = ("target_input", "tags")

    @classmethod
    def components(cls, raw: Any, location: str) -> Dict[str, ComponentEntry]:
        """Builds the entries of one components mapping, at the top level or in a group.

        Both places hold the same kind of block, because a group is a set of components
        and not a different way of declaring one. The key of each entry is the component's
        name and its whole identity, so it is checked against the identifier rule first.

        Raises:
            EnergySystemFormatError: ``EF-07`` for a non-mapping block, ``EF-08`` for an
                unusable component name, plus whatever an entry raises.
        """
        block = RawDocument.mapping(raw, location)
        entries: Dict[str, ComponentEntry] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "component")
            entries[name] = cls.entry(name, value, f"{location}.{name}")
        return entries

    @classmethod
    def entry(cls, name: str, raw: Any, location: str) -> ComponentEntry:
        """Builds one component entry from its mapping.

        An entry carrying a group's keys is reported as an attempted nested group rather
        than as two unknown keys, because that is what the author meant and groups do not
        nest.

        Raises:
            EnergySystemFormatError: ``EF-50`` if the entry looks like a group, ``EF-18``
                for a key that is not an entry key, ``EF-07`` for a bad ``class``.
        """
        entry = RawDocument.mapping(raw, location)
        if set(entry) & set(Group.GROUP_KEYS):
            raise EnergySystemFormatError(
                EnergySystemErrorId.NESTED_GROUP,
                location,
                f"'{name}' carries group keys, but groups do not nest and a component is not a group.",
                alternatives=ComponentEntry.ENTRY_KEYS,
                alternatives_label="entry keys",
            )
        for key in entry:
            if key not in ComponentEntry.ENTRY_KEYS:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNKNOWN_ENTRY_KEY,
                    f"{location}.{key}",
                    f"'{key}' is not a key of a component entry.",
                    alternatives=ComponentEntry.ENTRY_KEYS,
                    alternatives_label="entry keys",
                    offending_value=str(key),
                )
        class_path = RawDocument.string(entry.get(ComponentEntry.CLASS_KEY), f"{location}.class", required=True)
        return ComponentEntry(
            name=name,
            class_path=class_path or "",
            preset=RawDocument.string(entry.get("preset"), f"{location}.preset", required=False),
            constructor=cls._constructor(entry.get("constructor"), f"{location}.constructor"),
            config=RawDocument.mapping(entry.get("config"), f"{location}.config"),
            inputs=cls._inputs(entry.get("inputs"), f"{location}.inputs"),
            sizing_sources=cls._sizing_sources(entry.get("sizing_sources"), f"{location}.sizing_sources"),
        )

    @classmethod
    def _constructor(cls, raw: Any, location: str) -> Optional[ConstructorCall]:
        """Builds the named-constructor call of an entry, if it has one.

        The block maps exactly one constructor name to its arguments. Two names leave it
        open which one runs and zero says nothing, so both are hard errors rather than a
        choice the executor makes for the author.

        Raises:
            EnergySystemFormatError: ``EF-14`` if the block does not name exactly one
                constructor, ``EF-07`` if its arguments are not a mapping.
        """
        if raw is None:
            return None
        block = RawDocument.mapping(raw, location)
        if len(block) != 1:
            raise EnergySystemFormatError(
                EnergySystemErrorId.MALFORMED_CONSTRUCTOR,
                location,
                f"a constructor block names exactly one constructor, but {len(block)} are written.",
                remedy="Write 'constructor: {<name>: {<argument>: <value>}}'.",
            )
        constructor_name = next(iter(block))
        NameRules.check_identifier(constructor_name, location, "constructor")
        return ConstructorCall(
            name=constructor_name,
            arguments=RawDocument.mapping(block[constructor_name], f"{location}.{constructor_name}"),
        )

    @classmethod
    def _inputs(cls, raw: Any, location: str) -> Tuple[AnyInputItem, ...]:
        """Builds the input list of an entry, classifying each item by the keys it carries.

        The list keeps the order the file gives it, because that is what an author reads
        and what a re-emitted file must reproduce; no meaning depends on it.

        Raises:
            EnergySystemFormatError: ``EF-07`` if the value is not a list, and whatever an
                individual item raises.
        """
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise RawDocument.malformed(location, raw, "a list of input items")
        return tuple(cls._input_item(item, f"{location}[{index}]") for index, item in enumerate(raw))

    @classmethod
    def _input_item(cls, raw: Any, location: str) -> AnyInputItem:
        """Classifies and builds one input item.

        Classification is total and depends only on which keys are present: an ``input``
        key makes it an explicit wire, any aggregator-feed key makes it a feed, and a bare
        string asks for the target's declared defaults. Anything else, a bare string naming
        a port included, is rejected rather than guessed at.

        Raises:
            EnergySystemFormatError: ``EF-19`` for an item of no recognisable shape or
                with a foreign key, ``EF-06`` for a bad reference, ``EF-07`` for a bad value.
        """
        if isinstance(raw, str):
            source, member = NameRules.split_reference(raw, location, require_member=False)
            if member is not None:
                raise cls._unclassifiable(location, "a bare input item names a component, not one of its outputs")
            return DefaultInputs(source=source)
        if not isinstance(raw, dict):
            raise cls._unclassifiable(location, "an input item is a component name or a mapping")
        if "input" in raw:
            cls._check_item_keys(raw, location, cls.WIRE_KEYS)
            source, member = NameRules.split_reference(raw.get("from"), f"{location}.from", require_member=True)
            return ExplicitWire(
                source=source,
                input=RawDocument.string(raw.get("input"), f"{location}.input", required=True) or "",
                output=member or "",
            )
        if set(raw) & set(cls.FEED_KEYS):
            cls._check_item_keys(raw, location, cls.FEED_ITEM_KEYS)
            source, member = NameRules.split_reference(raw.get("from"), f"{location}.from", require_member=False)
            return AggregatorFeed(
                source=source,
                output=member,
                component_type=RawDocument.string(
                    raw.get("component_type"), f"{location}.component_type", required=False
                ),
                tags=RawDocument.string_tuple(raw.get("tags"), f"{location}.tags", required=True),
                weight=RawDocument.integer(raw.get("weight"), f"{location}.weight"),
                dispatch=cls._dispatch(raw.get("dispatch"), f"{location}.dispatch", "dispatch" in raw),
            )
        raise cls._unclassifiable(location, "the item has neither 'input' nor any aggregator-feed key")

    @classmethod
    def _dispatch(cls, raw: Any, location: str, present: bool) -> Optional[DispatchSpec]:
        """Builds the optional dispatch back-channel of an aggregator feed.

        Writing ``dispatch: {}`` and leaving the key out mean different things — the first
        asks the aggregator to publish a control signal for this participant, the second
        does not — so the caller passes whether the key was written at all.

        Raises:
            EnergySystemFormatError: ``EF-19`` for a key the block does not know, ``EF-07``
                for a value of the wrong kind.
        """
        if not present:
            return None
        block = RawDocument.mapping(raw, location)
        cls._check_item_keys(block, location, cls.DISPATCH_KEYS)
        return DispatchSpec(
            target_input=RawDocument.string(block.get("target_input"), f"{location}.target_input", required=False),
            tags=RawDocument.string_tuple(block.get("tags"), f"{location}.tags", required=False),
        )

    @classmethod
    def _sizing_sources(cls, raw: Any, location: str) -> Dict[str, Any]:
        """Builds the ``sizing_sources`` block, parsing every reference it contains.

        A value is either one reference or a list of them; the list form is how a field
        whose law sums over many providers names all of them, and an empty list says
        explicitly that no component feeds that fact. The two forms stay apart in the model
        because a re-emitted file has to spell them the way they were written.

        Raises:
            EnergySystemFormatError: ``EF-07`` for a value that is neither a string nor a
                list, ``EF-08`` for an unusable fact name, ``EF-06`` for a malformed
                reference.
        """
        block = RawDocument.mapping(raw, location)
        sources: Dict[str, Any] = {}
        for fact, value in block.items():
            NameRules.check_identifier(fact, location, "fact")
            if isinstance(value, list):
                sources[fact] = tuple(
                    SourceReference.parse(item, f"{location}.{fact}[{index}]") for index, item in enumerate(value)
                )
            elif isinstance(value, str):
                sources[fact] = SourceReference.parse(value, f"{location}.{fact}")
            else:
                raise RawDocument.malformed(f"{location}.{fact}", value, "a reference or a list of references")
        return sources

    @classmethod
    def _check_item_keys(cls, raw: Mapping[str, Any], location: str, allowed: Sequence[str]) -> None:
        """Rejects a key that the classified input-item shape does not know.

        The check runs after classification, so the message lists the keys of the shape the
        item was recognised as rather than the union of all three, which is what tells an
        author that ``weight`` on an explicit wire is a shape mix-up and not a typo.

        Raises:
            EnergySystemFormatError: ``EF-19`` naming the unknown key and listing the keys
                the shape does accept.
        """
        for key in raw:
            if key not in allowed:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNCLASSIFIABLE_INPUT_ITEM,
                    f"{location}.{key}",
                    f"'{key}' does not belong to this kind of input item.",
                    alternatives=allowed,
                    alternatives_label="keys",
                    offending_value=str(key),
                )

    @classmethod
    def _unclassifiable(cls, location: str, problem: str) -> EnergySystemFormatError:
        """Builds the rejection for an input item that matches none of the three shapes.

        The three spellings are repeated in the message because an item of no recognisable
        kind usually means the author reached for one of them and mis-remembered it.

        Returns:
            The exception, which the caller raises so the traceback starts at the check.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.UNCLASSIFIABLE_INPUT_ITEM,
            location,
            f"{problem}.",
            remedy=(
                "An input item is 'name', or '{input: Port, from: source.Output}', "
                "or '{from: source, tags: [...], weight: N}'."
            ),
        )
