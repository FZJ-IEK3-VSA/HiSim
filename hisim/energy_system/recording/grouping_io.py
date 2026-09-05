"""Reading and writing the committed form of one setup's grouping decision.

A decision made in a spreadsheet has to leave the spreadsheet to be reviewable. This module is that
boundary: it reads ``<stem>.grouping.yaml`` back into the plain model next door and writes the model
out again in the one style the committed file has. Nothing else in the pass touches the document,
so the two directions cannot drift and the round-trip that the workbook depends on — regenerate,
re-import, get the same bytes — is a property of one pair of functions rather than of the whole
tool.

The reader is strict in the same way the energy-system reader is. An unknown key is a typo and is
refused rather than ignored, because a decision that was silently dropped is worse than one that
would not load, and a grouping file is small enough that every key in it was written on purpose.

The writer omits everything empty. A component the table says nothing about is simply not in the
file, so the committed artefact is the judgement and not the inventory, and a diff of it shows the
decisions that changed rather than the components that did not.
"""

# clean

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Sequence, Tuple, Union

import yaml

from hisim.energy_system.document import RawDocument
from hisim.energy_system.emitter import CanonicalDumper
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.recording.reading import StrictMapping
from hisim.energy_system.repository import RepositoryLayout
from hisim.energy_system.recording.grouping import (
    Assignment,
    AssignmentKind,
    ConfigurationSelection,
    Grouping,
    GroupingKeys,
)


class GroupingReader:
    """Reads the committed grouping decision back into a :class:`Grouping`.

    Kept apart from the model so that the model stays a plain value a test can build in three
    lines. The reader is strict in the same way the energy-system reader is: an unknown key is a
    typo and is refused rather than ignored, because a decision nobody notices was dropped is worse
    than a decision that would not load.
    """

    #: What this document is, spelled the way its refusals name it.
    NOUN: ClassVar[str] = "a grouping decision"

    @classmethod
    def read(cls, source: Union[str, Path]) -> Grouping:
        """Reads one grouping file.

        Args:
            source: The ``*.grouping.yaml`` file, or the YAML text itself.

        Returns:
            The decision.

        Raises:
            EnergySystemRecordingError: ``EF-R6`` for an unreadable assignment and ``EF-R7`` for
                any other way the document is not a grouping decision.
        """
        document, origin = RawDocument.read(source)
        if origin != RawDocument.TEXT_ORIGIN:
            origin = RepositoryLayout.relative(Path(source))
        cls._reject_unknown(document, GroupingKeys.DOCUMENT, origin, "the document")
        setup = cls._required(document, GroupingKeys.SETUP, origin)
        probes = cls._required(document, GroupingKeys.PROBES, origin)
        assignments = cls._assignments(document.get(GroupingKeys.ASSIGNMENTS) or {}, origin)
        configurations = cls._configurations(document.get(GroupingKeys.CONFIGURATIONS) or {}, origin)
        return Grouping(
            setup=setup, probes=probes, assignments=assignments, configurations=configurations, origin=origin
        )

    @classmethod
    def _assignments(cls, block: Any, origin: str) -> Tuple[Assignment, ...]:
        """Reads the ``assignments`` block.

        Args:
            block: The raw mapping of component name to decision.
            origin: The file it came from.

        Returns:
            The assignments in document order.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` when the block or one of its entries is not a
                mapping, or carries a key the format does not declare.
        """
        mapping = cls._mapping(block, GroupingKeys.ASSIGNMENTS, origin)
        assignments = []
        for component, value in mapping.items():
            entry = cls._mapping(value, f"{GroupingKeys.ASSIGNMENTS}.{component}", origin)
            cls._reject_unknown(entry, GroupingKeys.ASSIGNMENT_KEYS, origin, f"assignments.{component}")
            assignments.append(
                Assignment.parse(
                    str(component),
                    str(entry.get(GroupingKeys.ASSIGN) or ""),
                    str(entry.get(GroupingKeys.NOTE) or ""),
                    origin,
                )
            )
        return tuple(assignments)

    @classmethod
    def _configurations(cls, block: Any, origin: str) -> Tuple[ConfigurationSelection, ...]:
        """Reads the ``configurations`` block.

        Args:
            block: The raw mapping of column name to switch positions.
            origin: The file it came from.

        Returns:
            The selections in document order.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` for a malformed block or an unknown key.
        """
        mapping = cls._mapping(block, GroupingKeys.CONFIGURATIONS, origin)
        selections = []
        for column, value in mapping.items():
            entry = cls._mapping(value, f"{GroupingKeys.CONFIGURATIONS}.{column}", origin)
            cls._reject_unknown(entry, GroupingKeys.CONFIGURATION_KEYS, origin, f"configurations.{column}")
            groups = cls._mapping(
                entry.get(GroupingKeys.GROUPS) or {}, f"{GroupingKeys.CONFIGURATIONS}.{column}.groups", origin
            )
            variants = cls._mapping(
                entry.get(GroupingKeys.VARIANTS) or {}, f"{GroupingKeys.CONFIGURATIONS}.{column}.variants", origin
            )
            for name, flag in groups.items():
                # bool() would make every non-empty string True, so a hand-written "false" or
                # "off" — which the strict YAML loader keeps as a string — would silently flip a
                # switch on. Only a real YAML boolean is a group position.
                if not isinstance(flag, bool):
                    raise EnergySystemRecordingError(
                        EnergySystemErrorId.GROUPING_UNKNOWN_OPTION,
                        f"{origin}:{GroupingKeys.CONFIGURATIONS}.{column}.groups.{name}",
                        f"{flag!r} is not a position a group flag can be in; write true or false.",
                        alternatives=("true", "false"),
                        alternatives_label="positions",
                        offending_value=str(flag),
                    )
            selections.append(
                ConfigurationSelection(
                    column=str(column),
                    groups={str(name): bool(flag) for name, flag in groups.items()},
                    variants={str(name): str(option) for name, option in variants.items()},
                )
            )
        return tuple(selections)

    @classmethod
    def _required(cls, document: Mapping[str, Any], key: str, origin: str) -> str:
        """Reads one required single-line string; the shared check with this reader's identity.

        Args:
            document: The parsed document.
            key: The key to read.
            origin: The file it came from.

        Returns:
            The string.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` when the key is missing or is not a string.
        """
        return StrictMapping.required_string(
            document, key, origin, EnergySystemErrorId.GROUPING_UNKNOWN_OPTION, cls.NOUN
        )

    @classmethod
    def _mapping(cls, value: Any, location: str, origin: str) -> Dict[str, Any]:
        """Insists that one block is a mapping; the shared check with this reader's identity.

        Args:
            value: The raw value.
            location: Dotted key path, for the message.
            origin: The file it came from.

        Returns:
            The mapping.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` when it is anything else.
        """
        return StrictMapping.mapping(value, location, origin, EnergySystemErrorId.GROUPING_UNKNOWN_OPTION)

    @classmethod
    def _reject_unknown(cls, block: Mapping[str, Any], allowed: Sequence[str], origin: str, where: str) -> None:
        """Refuses an undeclared key; the shared check with this reader's identity.

        Args:
            block: The mapping to check.
            allowed: The keys it may carry.
            origin: The file it came from.
            where: How the message names the block.

        Raises:
            EnergySystemRecordingError: ``EF-R7`` naming the key and the valid ones.
        """
        StrictMapping.reject_unknown(
            block, allowed, origin, where, EnergySystemErrorId.GROUPING_UNKNOWN_OPTION, cls.NOUN
        )


class GroupingWriter:
    """Writes a grouping decision in the one style the committed file has.

    The committed form is what a reviewer reads, so it omits everything empty — a component with no
    decision and no note is simply not there — and keeps the order the table has. Writing it
    through the same dumper the energy-system files use means a grouping file and the file it
    produces indent the same way and can be read side by side.
    """

    @classmethod
    def dump(cls, grouping: Grouping) -> str:
        """Renders one decision as the text of its committed file.

        Args:
            grouping: The decision to write.

        Returns:
            The whole file, header comment included.
        """
        document: Dict[str, Any] = {
            GroupingKeys.SETUP: grouping.setup,
            GroupingKeys.PROBES: grouping.probes,
        }
        assignments = {
            assignment.component: cls._assignment(assignment)
            for assignment in grouping.assignments
            if assignment.kind is not AssignmentKind.ORDINARY or assignment.note
        }
        if assignments:
            document[GroupingKeys.ASSIGNMENTS] = assignments
        configurations = {
            configuration.column: cls._configuration(configuration)
            for configuration in grouping.configurations
        }
        if configurations:
            document[GroupingKeys.CONFIGURATIONS] = configurations
        body = str(
            yaml.dump(
                document,
                Dumper=CanonicalDumper,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
        )
        return Grouping.HEADER.format(setup=grouping.setup) + body

    @classmethod
    def _assignment(cls, assignment: Assignment) -> Dict[str, Any]:
        """Renders one assignment, leaving out what it does not say.

        Args:
            assignment: The decision about one component.

        Returns:
            Its mapping.
        """
        written: Dict[str, Any] = {}
        if assignment.kind is not AssignmentKind.ORDINARY:
            written[GroupingKeys.ASSIGN] = assignment.text
        if assignment.note:
            written[GroupingKeys.NOTE] = assignment.note
        return written

    @classmethod
    def _configuration(cls, configuration: ConfigurationSelection) -> Dict[str, Any]:
        """Renders one column's switch positions, leaving out the blocks it does not use.

        Args:
            configuration: The column's selection.

        Returns:
            Its mapping.
        """
        written: Dict[str, Any] = {}
        if configuration.groups:
            written[GroupingKeys.GROUPS] = dict(configuration.groups)
        if configuration.variants:
            written[GroupingKeys.VARIANTS] = dict(configuration.variants)
        return written


def read_grouping(source: Union[str, Path]) -> Grouping:
    """Reads one committed grouping decision.

    Args:
        source: The ``*.grouping.yaml`` file, or the YAML text itself.

    Returns:
        The decision.

    Raises:
        EnergySystemRecordingError: ``EF-R6`` or ``EF-R7`` when the document is not one.
    """
    return GroupingReader.read(source)


def dump_grouping(grouping: Grouping) -> str:
    """Writes one grouping decision in its committed form.

    Args:
        grouping: The decision.

    Returns:
        The file's whole text.
    """
    return GroupingWriter.dump(grouping)
