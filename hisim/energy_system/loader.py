"""Turning an energy-system YAML document into the model, and writing it back out.

This module owns both directions of the file boundary. :func:`load_energy_system` turns a
path or a piece of YAML text into a fully checked :class:`EnergySystemFile`, and
:func:`dump_energy_system` writes such a file back in the one canonical style the format
has, so a program that loads a file, edits it and rewrites it changes only what it edited.

The reader raises the shape errors: an unusable suffix, a wrong schema version, an unknown
key, an input item matching none of the three accepted forms. Rules that span more than
one entry — unique names, group membership, a closed reference graph — belong to the
structural validator, which :func:`load_energy_system` runs afterwards. Neither step
imports a component class, so nothing is decided yet about presets, fields or ports.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple, Union

from hisim.energy_system.document import RawDocument
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import (
    AggregatorFeed,
    AnyInputItem,
    ComponentEntry,
    ConstructorCall,
    DefaultInputs,
    DispatchSpec,
    EnergySystemFile,
    ExplicitWire,
    Group,
    NameRules,
    SourceReference,
)
from hisim.energy_system.validation import validate_structure


class EnergySystemReader:
    """Builds the model from a parsed energy-system document.

    The reader descends the document once, checking the schema version before anything
    else is interpreted — a file written for another version may use the same keys for
    different things — and then walking the components and the groups, raising on the
    first shape problem it meets.

    Everything the reader decides is decidable from the document alone. It never imports a
    component class and never repairs, defaults or skips anything: a file is taken as
    written or rejected with a message naming the offending element and the valid values.
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
    def read(cls, source: Union[str, Path]) -> EnergySystemFile:
        """Reads a path or a piece of YAML text and builds the model, without validating.

        Args:
            source: A path to a ``.yaml`` or ``.yml`` file, or YAML text itself.

        Returns:
            The parsed file, before the structural validator has seen it.

        Raises:
            EnergySystemFormatError: For any problem in the text, the top level or the
                shape of a component entry or a group.
        """
        document, origin = RawDocument.read(source)
        return cls.build(document, origin)

    @classmethod
    def build(cls, document: Mapping[str, Any], origin: str) -> EnergySystemFile:
        """Builds the model from an already parsed document.

        Args:
            document: The top-level mapping.
            origin: Label of the document, prefixing every key path in a message.

        Returns:
            The parsed file, before structural validation.

        Raises:
            EnergySystemFormatError: ``EF-01`` for a missing or unsupported schema version,
                ``EF-03`` for an unknown top-level key or a document with no components.
        """
        cls._check_schema_version(document, origin)
        unknown = [key for key in document if key not in EnergySystemFile.TOP_LEVEL_KEYS]
        if unknown:
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                f"{origin}.{unknown[0]}",
                f"'{unknown[0]}' is not a top-level key of an energy-system file.",
                alternatives=EnergySystemFile.TOP_LEVEL_KEYS,
                alternatives_label="top-level keys",
                offending_value=str(unknown[0]),
            )
        if "components" not in document and "groups" not in document:
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                origin,
                "the document declares neither 'components' nor 'groups'.",
            )
        metadata = RawDocument.mapping(document["metadata"], f"{origin}.metadata") if "metadata" in document else None
        return EnergySystemFile(
            schema_version=EnergySystemFile.SUPPORTED_SCHEMA_VERSION,
            name=RawDocument.string(document.get("name"), f"{origin}.name", required=True) or "",
            description=RawDocument.string(document.get("description"), f"{origin}.description", required=False),
            components=cls._build_components(document.get("components"), f"{origin}.components"),
            groups=cls._build_groups(document.get("groups"), f"{origin}.groups"),
            metadata=metadata,
        )

    @classmethod
    def _check_schema_version(cls, document: Mapping[str, Any], origin: str) -> None:
        """Rejects a document that does not declare exactly the supported schema version.

        The version is mandatory and checked before any other key: a file written against
        another version of the format cannot be interpreted safely, and the author needs to
        be told that rather than shown a list of key problems.

        Raises:
            EnergySystemFormatError: ``EF-01`` if the key is missing or holds any other
                value, naming the version that is supported.
        """
        version = document.get("schema_version")
        if version != EnergySystemFile.SUPPORTED_SCHEMA_VERSION:
            written = "no schema_version" if "schema_version" not in document else f"schema_version {version!r}"
            raise EnergySystemFormatError(
                EnergySystemErrorId.SCHEMA_VERSION,
                f"{origin}.schema_version",
                f"the document declares {written}.",
                alternatives=[str(EnergySystemFile.SUPPORTED_SCHEMA_VERSION)],
                alternatives_label="schema versions",
            )

    @classmethod
    def _build_components(cls, raw: Any, location: str) -> Dict[str, ComponentEntry]:
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
            entries[name] = cls._build_entry(name, value, f"{location}.{name}")
        return entries

    @classmethod
    def _build_entry(cls, name: str, raw: Any, location: str) -> ComponentEntry:
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
            constructor=cls._build_constructor(entry.get("constructor"), f"{location}.constructor"),
            config=RawDocument.mapping(entry.get("config"), f"{location}.config"),
            inputs=cls._build_inputs(entry.get("inputs"), f"{location}.inputs"),
            sizing_sources=cls._build_sizing_sources(entry.get("sizing_sources"), f"{location}.sizing_sources"),
        )

    @classmethod
    def _build_constructor(cls, raw: Any, location: str) -> Optional[ConstructorCall]:
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
    def _build_inputs(cls, raw: Any, location: str) -> Tuple[AnyInputItem, ...]:
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
        return tuple(cls._build_input_item(item, f"{location}[{index}]") for index, item in enumerate(raw))

    @classmethod
    def _build_input_item(cls, raw: Any, location: str) -> AnyInputItem:
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
                dispatch=cls._build_dispatch(raw.get("dispatch"), f"{location}.dispatch", "dispatch" in raw),
            )
        raise cls._unclassifiable(location, "the item has neither 'input' nor any aggregator-feed key")

    @classmethod
    def _build_dispatch(cls, raw: Any, location: str, present: bool) -> Optional[DispatchSpec]:
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
    def _build_sizing_sources(cls, raw: Any, location: str) -> Dict[str, Any]:
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
    def _build_groups(cls, raw: Any, location: str) -> Dict[str, Group]:
        """Builds the groups block: named sets of components, each with an on/off flag.

        Both keys of a group are required: a group without a flag cannot be switched and a
        group without components cannot switch anything.

        Raises:
            EnergySystemFormatError: ``EF-08`` for an unusable group name, ``EF-18`` for a
                key a group does not have, ``EF-53`` for a missing or non-boolean
                ``enabled``, ``EF-54`` for a group with no components.
        """
        block = RawDocument.mapping(raw, location)
        groups: Dict[str, Group] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "group")
            group_location = f"{location}.{name}"
            body = RawDocument.mapping(value, group_location)
            for key in body:
                if key not in Group.GROUP_KEYS:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.UNKNOWN_ENTRY_KEY,
                        f"{group_location}.{key}",
                        f"'{key}' is not a key of a group.",
                        alternatives=Group.GROUP_KEYS,
                        alternatives_label="group keys",
                        offending_value=str(key),
                    )
            if not isinstance(body.get("enabled"), bool):
                raise EnergySystemFormatError(
                    EnergySystemErrorId.GROUP_ENABLED_FLAG,
                    f"{group_location}.enabled",
                    f"group '{name}' has no boolean 'enabled' flag.",
                    alternatives=["false", "true"],
                    alternatives_label="flags",
                )
            members = cls._build_components(body.get("components"), f"{group_location}.components")
            if not members:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.EMPTY_GROUP,
                    f"{group_location}.components",
                    f"group '{name}' lists no components.",
                    remedy="A group with nothing in it cannot switch anything on or off; delete it.",
                )
            groups[name] = Group(name=name, enabled=bool(body["enabled"]), components=members)
        return groups

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


def load_energy_system(source: Union[str, Path]) -> EnergySystemFile:
    """Loads an energy-system file and checks everything decidable without its classes.

    This is the entry point for every reader of the format. It reads the YAML, rejects
    duplicate and unknown keys, builds the model and runs the structural validator, so a
    file that comes back has a valid shape, unique names, legal groups and a closed
    reference graph. It imports no component class, so nothing is said yet about whether a
    preset, a config field or a port exists.

    Args:
        source: A path to a ``.yaml`` or ``.yml`` file, or a string holding the YAML text.
            A string is a path when it has no line break and carries a file suffix.

    Returns:
        The loaded and structurally valid energy system.

    Raises:
        EnergySystemFormatError: On the first problem found, naming the offending element
            and, where a closed set of valid values exists, listing it.
    """
    model = EnergySystemReader.read(source)
    validate_structure(model)
    return model


def parse_energy_system(source: Union[str, Path]) -> EnergySystemFile:
    """Reads an energy-system file into the model without running structural validation.

    Tools that inspect or repair a file known to be incomplete — an editor showing a
    half-written document, a test exercising the validator on a model it built itself —
    need the parse step alone. Everything the reader itself decides, from the file suffix
    to the shape of an input item, is still enforced.

    Args:
        source: A path to a ``.yaml`` or ``.yml`` file, or a string holding YAML text.

    Returns:
        The parsed energy system, which may still violate structural rules.

    Raises:
        EnergySystemFormatError: On any problem in the text or in the shape of a block.
    """
    return EnergySystemReader.read(source)


def dump_energy_system(model: EnergySystemFile) -> str:
    """Renders an energy system as YAML text in the format's canonical style.

    Loading a canonical file and dumping it again reproduces it character for character,
    which lets a program edit a file in place without rewriting what it did not touch. A
    hand-written file in a different but legal style — flow lists, another key order, blank
    lines — becomes canonical on the first pass and is stable from then on; comments are
    not carried over.

    Args:
        model: The energy system to write.

    Returns:
        The YAML document, ending in a newline.
    """
    return EnergySystemEmitter.dump(model)
