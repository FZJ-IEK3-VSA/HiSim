"""Turning an energy-system YAML document into the model, and writing it back out.

This module owns both directions of the file boundary. :func:`load_energy_system` turns a
path or a piece of YAML text into a fully checked :class:`EnergySystemFile`, and
:func:`dump_energy_system` writes such a file back in the one canonical style the format
has, so a program that loads a file, edits it and rewrites it changes only what it edited.

The reader raises the document's own shape errors: an unusable suffix, a wrong schema
version, an unknown top-level key, a group without a flag, a variant whose selection names
no option. What a single component entry looks like is read one module below, in
:mod:`hisim.energy_system.entries`, so that this module is about the document and that one
about the block every container of components repeats. Rules that span more than one entry —
unique names, group and variant membership, a closed reference graph — belong to the
structural validator, which :func:`load_energy_system` runs afterwards. No step here imports
a component class, so nothing is decided yet about presets, fields or ports.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Tuple, Union

from hisim.energy_system.document import RawDocument
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.entries import EntryReader
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import EnergySystemFile, Group, Variant, VariantOption
from hisim.energy_system.names import NameRules
from hisim.energy_system.validation import validate_structure


class EnergySystemReader:
    """Builds the model from a parsed energy-system document.

    The reader descends the document once, checking the schema version before anything
    else is interpreted — a file written for another version may use the same keys for
    different things — and then walking the three places components are written: the top
    level, the groups and the options of the variants. Every entry it meets is handed to
    :class:`hisim.energy_system.entries.EntryReader`, and the first shape problem raises.

    Everything the reader decides is decidable from the document alone. It never imports a
    component class and never repairs, defaults or skips anything: a file is taken as
    written or rejected with a message naming the offending element and the valid values.
    """

    #: The top-level blocks a component may be written in. A document naming none of them
    #: describes nothing, which is reported as such rather than as an empty run.
    COMPONENT_BLOCKS: ClassVar[Tuple[str, ...]] = ("components", "groups", "variants")

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
        if not any(key in document for key in cls.COMPONENT_BLOCKS):
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                origin,
                "the document declares no components at all.",
                alternatives=cls.COMPONENT_BLOCKS,
                alternatives_label="blocks that hold components",
            )
        metadata = RawDocument.mapping(document["metadata"], f"{origin}.metadata") if "metadata" in document else None
        return EnergySystemFile(
            schema_version=EnergySystemFile.SUPPORTED_SCHEMA_VERSION,
            name=RawDocument.string(document.get("name"), f"{origin}.name", required=True) or "",
            description=RawDocument.string(document.get("description"), f"{origin}.description", required=False),
            components=EntryReader.components(document.get("components"), f"{origin}.components"),
            groups=cls._build_groups(document.get("groups"), f"{origin}.groups"),
            variants=cls._build_variants(document.get("variants"), f"{origin}.variants"),
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
    def _build_groups(cls, raw: Any, location: str) -> Dict[str, Group]:
        """Builds the groups block: named sets of components, each with an on/off flag.

        Both keys of a group are required: a group without a flag cannot be switched and a
        group without components cannot switch anything. An option of a variant is the one
        components block that may legally be empty, which is why that check lives there.

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
            cls._check_block_keys(body, group_location, Group.GROUP_KEYS, "group")
            if not isinstance(body.get("enabled"), bool):
                raise EnergySystemFormatError(
                    EnergySystemErrorId.GROUP_ENABLED_FLAG,
                    f"{group_location}.enabled",
                    f"group '{name}' has no boolean 'enabled' flag.",
                    alternatives=["false", "true"],
                    alternatives_label="flags",
                )
            members = EntryReader.components(body.get("components"), f"{group_location}.components")
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
    def _build_variants(cls, raw: Any, location: str) -> Dict[str, Variant]:
        """Builds the variants block: named exclusive choices, each resolved to one option.

        Both keys of a variant are required, and the selection is checked against the options
        right here rather than left to the validator, because the message that makes the
        mistake trivial to fix is the one that lists the options the variant actually has.

        Raises:
            EnergySystemFormatError: ``EF-08`` for an unusable variant name, ``EF-18`` for a
                key a variant does not have, ``EF-55`` for a selection naming no option of
                this variant, ``EF-56`` for a variant offering no option at all.
        """
        block = RawDocument.mapping(raw, location)
        variants: Dict[str, Variant] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "variant")
            variant_location = f"{location}.{name}"
            body = RawDocument.mapping(value, variant_location)
            cls._check_block_keys(body, variant_location, Variant.VARIANT_KEYS, "variant")
            options = cls._build_options(body.get("options"), f"{variant_location}.options", name)
            selected = RawDocument.string(body.get("selected"), f"{variant_location}.selected", required=True)
            if selected not in options:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNKNOWN_VARIANT_OPTION,
                    f"{variant_location}.selected",
                    f"variant '{name}' selects '{selected}', which is none of its options.",
                    alternatives=tuple(options),
                    alternatives_label=f"options of '{name}'",
                    offending_value=str(selected),
                )
            variants[name] = Variant(name=name, selected=selected or "", options=options)
        return variants

    @classmethod
    def _build_options(cls, raw: Any, location: str, variant_name: str) -> Dict[str, VariantOption]:
        """Builds the options of one variant, each a complete alternative world of its own.

        An option with an empty ``components`` block is accepted, unlike an empty group: it is
        how a file spells the world in which the variant contributes nothing, which is a real
        alternative and not a mistake. A variant with no option at all is a mistake, since
        there is then nothing for its selection to name.

        Raises:
            EnergySystemFormatError: ``EF-56`` for an empty options mapping, ``EF-08`` for an
                unusable option name, ``EF-18`` for a key an option does not have.
        """
        block = RawDocument.mapping(raw, location)
        if not block:
            raise EnergySystemFormatError(
                EnergySystemErrorId.EMPTY_VARIANT,
                location,
                f"variant '{variant_name}' offers no options.",
                remedy="A variant is a choice between at least two alternative worlds; write them under 'options'.",
            )
        options: Dict[str, VariantOption] = {}
        for name, value in block.items():
            NameRules.check_identifier(name, location, "variant option")
            option_location = f"{location}.{name}"
            body = RawDocument.mapping(value, option_location)
            cls._check_block_keys(body, option_location, VariantOption.OPTION_KEYS, "variant option")
            options[name] = VariantOption(
                name=name,
                components=EntryReader.components(body.get("components"), f"{option_location}.components"),
            )
        return options

    @classmethod
    def _check_block_keys(cls, body: Mapping[str, Any], location: str, allowed: Tuple[str, ...], role: str) -> None:
        """Rejects a key one of the document's named blocks does not know.

        Groups, variants and options all have a small closed set of keys, and an author who
        writes a fifth one has almost always reached for another block's vocabulary — an
        ``enabled`` flag on a variant, an ``options`` mapping on a group. Naming the role in
        the message is what turns that into one obvious edit.

        Raises:
            EnergySystemFormatError: ``EF-18`` naming the key and listing the ones the block
                does accept.
        """
        for key in body:
            if key not in allowed:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNKNOWN_ENTRY_KEY,
                    f"{location}.{key}",
                    f"'{key}' is not a key of a {role}.",
                    alternatives=allowed,
                    alternatives_label=f"{role} keys",
                    offending_value=str(key),
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
