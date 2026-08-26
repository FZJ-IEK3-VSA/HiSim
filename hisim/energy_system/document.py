"""Low-level access to the YAML text of an energy-system file.

Everything in this module works on plain Python values — dicts, lists, strings — and
knows nothing about components, groups or presets. It is the layer between the bytes on
disk and the model: it decides which files may be read at all, parses them under two
restrictions the plain safe loader does not impose, and offers the handful of typed
accessors the model builder needs so that "this block must be a mapping" is spelled once
rather than at every call site.

The two restrictions are deliberate. Duplicate keys are rejected across the whole
document instead of following YAML's last-one-wins rule, because a pasted-twice block
would otherwise change the simulated system silently. And the YAML 1.1 boolean spellings
``yes``, ``no``, ``on``, ``off``, ``y`` and ``n`` are demoted to plain strings, because
configuration values in this codebase are upper-case enum member names and a member
called ``ON`` must not arrive as ``True``.

Every rejection raised here carries the dotted key path of the offending value, which the
model builder extends as it descends, so a message points at one line of a long file
rather than at the file as a whole.
"""

# clean

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Pattern, Tuple, Union

import yaml

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError


def _strict_boolean_resolvers() -> Dict[Any, List[Tuple[str, Pattern[str]]]]:
    """Builds an implicit-resolver table in which only ``true`` and ``false`` are boolean.

    PyYAML implements YAML 1.1, which resolves six more spellings to booleans. Dropping
    the boolean resolver from every starting character and putting a strict one back for
    the spellings of ``true`` and ``false`` keeps ordinary flags working while leaving
    every other bare word a string.

    The table is rebuilt from the safe loader's own table on each call rather than shared,
    so the loader class below owns a private copy that nothing else can mutate.

    Returns:
        A resolver table shaped like PyYAML's, with the boolean entries restricted.
    """
    boolean_tag = "tag:yaml.org,2002:bool"
    strict_pattern = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
    table: Dict[Any, List[Tuple[str, Pattern[str]]]] = {}
    for first_character, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items():
        kept = [(tag, pattern) for tag, pattern in resolvers if tag != boolean_tag]
        if first_character in "tTfF":
            kept.append((boolean_tag, strict_pattern))
        table[first_character] = kept
    return table


class StrictYamlLoader(yaml.SafeLoader):
    """The safe YAML loader with YAML 1.1's surprising boolean spellings removed.

    Everything else about :class:`yaml.SafeLoader` is kept: no arbitrary Python object is
    ever constructed and no tag outside the core schema is honoured. The only change is
    the implicit-resolver table, which no longer turns ``yes``, ``no``, ``on``, ``off``,
    ``y`` or ``n`` into booleans.

    Duplicate keys are not handled here but by :class:`RawDocument`, which walks the
    composed node tree before values are constructed. PyYAML builds mappings bottom-up and
    would not know the key path of a duplicate at the moment it meets one, while the node
    walk can name it exactly.
    """

    yaml_implicit_resolvers = _strict_boolean_resolvers()


class RawDocument:
    """Reads energy-system YAML into plain values and types the pieces of it.

    The class has two halves. :meth:`read` covers the way in — accepting a path or a
    piece of text, refusing any file suffix but the two YAML ones, parsing under the
    strict loader and rejecting duplicate keys — and returns the top-level mapping
    together with a short label naming where it came from. The remaining classmethods are
    the typed accessors the model builder uses while descending that mapping.

    The accessors exist so that the same wording is used for every wrong-kind value in the
    format, and so that the difference between "absent" and "present but wrong" is decided
    in one place: an absent block is an empty one, an absent optional string is ``None``,
    and anything present with the wrong kind is a hard error naming the key path.
    """

    #: The file suffixes an energy-system file may carry. JSON is a YAML subset and would
    #: parse, but a second accepted spelling means a second canonical style, a second
    #: duplicate-key rule and a second fixture format for no gain.
    SUPPORTED_SUFFIXES: ClassVar[Tuple[str, ...]] = (".yaml", ".yml")

    #: Origin label used in messages when the document came from a string, not a file.
    TEXT_ORIGIN: ClassVar[str] = "<text>"

    @classmethod
    def read(cls, source: Union[str, Path]) -> Tuple[Dict[str, Any], str]:
        """Reads a path or a piece of YAML text into the document's top-level mapping.

        A :class:`~pathlib.Path` is always a file. A string is taken as a path when it has
        no line break and carries a file suffix, and as YAML text otherwise, which makes
        the common calls — a file name, an inline document in a test — both work without
        a second entry point.

        Args:
            source: The path or the YAML text.

        Returns:
            A pair of the top-level mapping and the origin label for messages.

        Raises:
            EnergySystemFormatError: ``EF-00`` for an unsupported file suffix, ``EF-02``
                for a duplicate key, ``EF-03`` for an unreadable file, invalid YAML or a
                document that is not a mapping.
        """
        text, origin = cls._read_text(source)
        return cls._parse(text, origin), origin

    @classmethod
    def _read_text(cls, source: Union[str, Path]) -> Tuple[str, str]:
        """Resolves the ambiguous source argument into YAML text and an origin label.

        The suffix check happens here rather than after parsing so that a JSON file, which
        a YAML parser would happily accept, is refused as a format rather than reported
        for whatever it does differently. Reading errors are reported with the path, since
        a missing or unreadable file is a caller's problem, not the document's.

        Raises:
            EnergySystemFormatError: ``EF-00`` for an unsupported suffix, ``EF-03`` if the
                file cannot be read.
        """
        if isinstance(source, str) and ("\n" in source or Path(source).suffix == ""):
            return source, cls.TEXT_ORIGIN
        path = Path(source)
        if path.suffix.lower() not in cls.SUPPORTED_SUFFIXES:
            raise EnergySystemFormatError(
                EnergySystemErrorId.UNSUPPORTED_FORMAT,
                str(path),
                f"'{path.suffix or path.name}' is not an energy-system file format.",
                alternatives=cls.SUPPORTED_SUFFIXES,
                alternatives_label="suffixes",
                remedy="Energy-system files are YAML; JSON is not an accepted input format.",
            )
        try:
            return path.read_text(encoding="utf-8"), path.name
        except OSError as problem:
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                str(path),
                f"the file cannot be read ({problem}).",
            ) from problem

    @classmethod
    def _parse(cls, text: str, origin: str) -> Dict[str, Any]:
        """Parses the text into plain values, rejecting duplicate keys before constructing.

        The document is composed into a node tree first so that a duplicate key can be
        reported with its full path, and only then constructed into values. A document
        that is not a mapping — an empty file, a bare list — is refused with the list of
        keys that were expected instead.

        Raises:
            EnergySystemFormatError: ``EF-02`` for a duplicate key, ``EF-03`` for invalid
                YAML or a document that is not a mapping.
        """
        try:
            node = yaml.compose(text, Loader=StrictYamlLoader)
            cls._reject_duplicate_keys(node, origin)
            document = yaml.load(text, Loader=StrictYamlLoader)
        except yaml.YAMLError as problem:
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                origin,
                f"the document is not valid YAML ({problem}).",
            ) from problem
        if not isinstance(document, dict):
            raise EnergySystemFormatError(
                EnergySystemErrorId.TOP_LEVEL_SHAPE,
                origin,
                "the document is not a mapping of top-level keys.",
            )
        return document

    @classmethod
    def _reject_duplicate_keys(cls, node: Optional[yaml.Node], location: str) -> None:
        """Walks the composed node tree and rejects a key written twice in one mapping.

        YAML's own rule is last-one-wins, which turns a copy-paste slip — two ``config``
        blocks in one entry, the same component name twice — into a silently different
        energy system. The whole document is walked, not only its top level, and the
        message carries the full key path so the second occurrence can be found.

        Raises:
            EnergySystemFormatError: ``EF-02`` naming the key path of the duplicate.
        """
        if isinstance(node, yaml.MappingNode):
            seen: List[str] = []
            for key_node, value_node in node.value:
                key = str(key_node.value)
                if key in seen:
                    raise EnergySystemFormatError(
                        EnergySystemErrorId.DUPLICATE_KEY,
                        f"{location}.{key}",
                        f"the key '{key}' is written twice in the same block.",
                        remedy="YAML would keep only the last one; merge them into a single block.",
                    )
                seen.append(key)
                cls._reject_duplicate_keys(value_node, f"{location}.{key}")
        elif isinstance(node, yaml.SequenceNode):
            for index, child in enumerate(node.value):
                cls._reject_duplicate_keys(child, f"{location}[{index}]")

    @classmethod
    def malformed(cls, location: str, value: Any, expected: str) -> EnergySystemFormatError:
        """Builds the rejection used for every value whose YAML kind is wrong.

        All wrong-kind problems share one identifier because they share one cause and one
        fix: the author wrote a scalar where a block belongs, or the other way round. The
        message names the key path, what was expected there and what was found, which is
        everything needed to correct the line.

        Returns:
            The exception, which the caller raises so the traceback starts at the check.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.MALFORMED_BLOCK,
            location,
            f"expected {expected}, but found {type(value).__name__} {value!r}.",
        )

    @classmethod
    def mapping(cls, value: Any, location: str) -> Dict[str, Any]:
        """Returns a block as a plain dict, treating an absent block as an empty one.

        Writing ``config:`` with nothing under it and leaving ``config`` out entirely mean
        the same thing, so both produce an empty dict rather than one of them being an
        error. A value that is present but is not a mapping is rejected.

        Raises:
            EnergySystemFormatError: ``EF-07`` if the value is present but not a mapping.
        """
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise cls.malformed(location, value, "a mapping")
        return dict(value)

    @classmethod
    def string(cls, value: Any, location: str, *, required: bool) -> Optional[str]:
        """Returns a non-empty string value, or ``None`` for an absent optional one.

        Blank strings are refused along with absent required ones: a preset named ``" "``
        is a typo in every case, and accepting it would push the failure to the stage that
        looks the preset up, far from the line that caused it.

        Raises:
            EnergySystemFormatError: ``EF-07`` if the value is missing while required, or
                present but not a non-empty string.
        """
        if value is None:
            if required:
                raise cls.malformed(location, value, "a non-empty string")
            return None
        if not isinstance(value, str) or not value.strip():
            raise cls.malformed(location, value, "a non-empty string")
        return value

    @classmethod
    def string_tuple(cls, value: Any, location: str, *, required: bool) -> Tuple[str, ...]:
        """Returns a list of names as a tuple of strings.

        Tag lists are the only place the format uses a list of bare names, and they are
        required on an aggregator feed and optional on its dispatch block, which is why the
        caller says which of the two applies. An absent optional list is an empty tuple.

        Raises:
            EnergySystemFormatError: ``EF-07`` if the value is missing while required, or
                is not a list of non-empty strings.
        """
        if value is None:
            if required:
                raise cls.malformed(location, value, "a list of names")
            return ()
        if not isinstance(value, list):
            raise cls.malformed(location, value, "a list of names")
        return tuple(cls.string(item, location, required=True) or "" for item in value)

    @classmethod
    def integer(cls, value: Any, location: str) -> int:
        """Returns an integer value, rejecting booleans along with everything else.

        Booleans are integers in Python, so ``weight: true`` would otherwise pass as the
        weight one. The format has exactly one integer field, the dispatch weight of an
        aggregator feed, and it is never optional.

        Raises:
            EnergySystemFormatError: ``EF-07`` if the value is absent, a boolean, or of
                any other non-integer kind.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            raise cls.malformed(location, value, "an integer")
        return value
