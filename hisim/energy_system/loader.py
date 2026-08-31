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
from typing import Any, Dict, Mapping, Union

from hisim.energy_system.document import RawDocument
from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.entries import EntryReader
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError
from hisim.energy_system.model import EnergySystemFile, Group
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
            components=EntryReader.components(document.get("components"), f"{origin}.components"),
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
