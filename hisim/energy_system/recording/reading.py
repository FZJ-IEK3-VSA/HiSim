"""Shared low-level document handling of the recording pass: strictness checks and dotted paths.

The probe list and the grouping decision are read by two different readers with two different
error identities, but the rules they enforce are the same three: a required key must hold a
non-empty string, a block must be a mapping, and a key the format does not declare is a typo to
refuse rather than to ignore. Before this module each reader carried its own copy of the three,
byte for byte apart from the error id — two sources of truth for one strictness rule, which is
exactly how one reader comes to accept what the other refuses.

The checks stay parameterised by error id and by the document's own noun ("a probe list", "a
grouping decision"), so a refusal still names the document kind the person actually wrote. The
energy-system loader has its own spelling of the unknown-key rule with its own error family; the
two are deliberately not merged, because a format error and a recording error are different
contracts with different readers.

The dotted-path operations live here for the same one-source-of-truth reason: a probe overlay and
an override difference both address a nested entry document by ``a.b.c`` paths, and the walk that
sets such a path has to mean the same thing in both — separators, missing-block handling, copying —
or a fix to one caller's path handling silently misses the other.
"""

# clean

from __future__ import annotations

import copy
from typing import Any, ClassVar, Dict, Mapping, Sequence

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError


class DocumentPaths:
    """The dotted-path vocabulary over a nested entry document, written down once.

    A path is ``.``-separated keys into nested mappings. Lists are one value — an index into a
    list two columns write differently is not a stable name for anything — so the walk descends
    into mappings only and everything else is set or removed whole.
    """

    #: Separator of a dotted path into an entry document.
    SEPARATOR: ClassVar[str] = "."

    @classmethod
    def copy(cls, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Copies a document deeply, so that writing into the copy cannot reach the original.

        Args:
            document: The document to copy.

        Returns:
            A fresh, fully independent document.
        """
        return copy.deepcopy(dict(document))

    @classmethod
    def set_value(cls, document: Dict[str, Any], path: str, value: Any) -> None:
        """Sets one dotted path of the document, creating the intermediate mappings it needs.

        Args:
            document: The document, modified in place.
            path: The dotted path.
            value: The value to set at its leaf.
        """
        keys = path.split(cls.SEPARATOR)
        block = document
        for key in keys[:-1]:
            block = block.setdefault(key, {})
        block[keys[-1]] = value

    @classmethod
    def remove(cls, document: Dict[str, Any], path: str) -> None:
        """Removes one dotted path from the document, quietly when it is not there.

        Args:
            document: The document, modified in place.
            path: The dotted path.
        """
        keys = path.split(cls.SEPARATOR)
        block: Any = document
        for key in keys[:-1]:
            block = block.get(key) if isinstance(block, dict) else None
            if block is None:
                return
        if isinstance(block, dict):
            block.pop(keys[-1], None)


class StrictMapping:
    """The shared strictness checks over one raw recording-side document.

    All methods are classmethods over explicit arguments; the class holds no state. Every method
    either returns the validated value or raises an :class:`EnergySystemRecordingError` carrying
    the caller's error id, so a call site never branches on a boolean and then invents its own
    message.
    """

    @classmethod
    def required_string(
        cls, document: Mapping[str, Any], key: str, origin: str, error_id: EnergySystemErrorId, noun: str
    ) -> str:
        """Reads one required single-line string from the document.

        Args:
            document: The parsed document.
            key: The key to read.
            origin: The file it came from.
            error_id: The error family of the calling reader.
            noun: What the document is, for the message ("a probe list").

        Returns:
            The string, stripped.

        Raises:
            EnergySystemRecordingError: When the key is missing, blank or not a string.
        """
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise EnergySystemRecordingError(
                error_id, f"{origin}:{key}", f"{noun} needs a non-empty '{key}'."
            )
        return value.strip()

    @classmethod
    def mapping(cls, value: Any, location: str, origin: str, error_id: EnergySystemErrorId) -> Dict[str, Any]:
        """Insists that one block is a mapping.

        Args:
            value: The raw value.
            location: Dotted key path, for the message.
            origin: The file it came from.
            error_id: The error family of the calling reader.

        Returns:
            A fresh dict of the mapping.

        Raises:
            EnergySystemRecordingError: When the value is anything else.
        """
        if not isinstance(value, dict):
            raise EnergySystemRecordingError(
                error_id,
                f"{origin}:{location}",
                f"'{location}' must be a mapping, not {type(value).__name__}.",
            )
        return dict(value)

    @classmethod
    def reject_unknown(
        cls,
        block: Mapping[str, Any],
        allowed: Sequence[str],
        origin: str,
        where: str,
        error_id: EnergySystemErrorId,
        noun: str,
    ) -> None:
        """Refuses the first key the block carries that the format does not declare.

        Args:
            block: The mapping to check.
            allowed: The keys it may carry.
            origin: The file it came from.
            where: How the message names the block.
            error_id: The error family of the calling reader.
            noun: What the document is, for the message.

        Raises:
            EnergySystemRecordingError: Naming the key and the ones that are valid.
        """
        for key in block:
            if key not in allowed:
                raise EnergySystemRecordingError(
                    error_id,
                    f"{origin}:{where}",
                    f"'{key}' is not a key {noun} declares.",
                    alternatives=allowed,
                    alternatives_label="keys",
                    offending_value=str(key),
                )
