"""Hard errors raised while reading and checking an energy-system file.

An energy-system file describes a whole simulated household declaratively, and the
rule this package follows everywhere is that an inconsistency aborts the load instead
of being repaired, defaulted or skipped: a file that is wrong in any way must fail
loudly and early, before a single component object exists. Every such failure is
raised as an exception derived from :class:`EnergySystemError`, so an application that
wants to present file problems to a user can catch one class, while individual call
sites can still tell the failure modes apart by the error identifier each exception
carries.

Each failure mode has a stable identifier of the form ``EF-nn`` (collected in
:class:`EnergySystemErrorId`) so that documentation, tests and user reports can refer
to a condition by name rather than by a fragile message substring. Two rules apply to
every message built here: it names the offending element — the component name, the
group name or the dotted key path inside the document — and, whenever the set of
acceptable values is closed and known, it lists that set. The second rule is what
turns a rejection into a repair instruction, and it is the reason
:class:`EnergySystemFormatError` takes the alternatives as structured data rather than
leaving each call site to format them.
"""

# clean

from __future__ import annotations

import difflib
import enum
from typing import ClassVar, Iterable, Optional, Sequence


@enum.unique
class EnergySystemErrorId(enum.Enum):
    """The stable identifiers of every way an energy-system file can be rejected.

    The identifiers are grouped by the lifecycle stage that can first decide the
    condition: ``EF-0x`` for reading the document, ``EF-1x`` and ``EF-2x`` for the
    shape and the cross-references of component entries, ``EF-4x`` for sizing sources
    and ``EF-5x`` for groups. A stage never re-checks what an earlier stage already
    decided, and each identifier is raised from exactly one place in the code so that
    a message cannot drift between two implementations of the same rule.

    Not every member is reachable from the loader and the structural validator alone:
    the identifiers covering presets, config fields, ports and channels can only be
    decided once the component classes are imported, which happens in a later stage
    of the lifecycle. They are listed here nonetheless so that the catalogue lives in
    one place and no later stage has to invent an identifier that collides.
    """

    UNSUPPORTED_FORMAT = "EF-00"
    SCHEMA_VERSION = "EF-01"
    DUPLICATE_KEY = "EF-02"
    TOP_LEVEL_SHAPE = "EF-03"
    UNRESOLVABLE_PATH_VARIABLE = "EF-04"
    ABSOLUTE_PATH = "EF-05"
    WILDCARD_OR_RELATIVE_REFERENCE = "EF-06"
    MALFORMED_BLOCK = "EF-07"
    INVALID_NAME = "EF-08"
    PRESET_AND_CONSTRUCTOR = "EF-11"
    NO_CONFIGURATION_SOURCE = "EF-12"
    MALFORMED_CONSTRUCTOR = "EF-14"
    UNKNOWN_ENTRY_KEY = "EF-18"
    UNCLASSIFIABLE_INPUT_ITEM = "EF-19"
    UNKNOWN_SOURCE = "EF-20"
    MIXED_INPUT_SPELLING = "EF-24"
    DUPLICATE_FEED = "EF-25"
    DUPLICATE_WIRE = "EF-26"
    UNKNOWN_SIZING_SOURCE = "EF-40"
    SIZING_FACT_MISMATCH = "EF-41"
    NESTED_GROUP = "EF-50"
    COMPONENT_IN_TWO_GROUPS = "EF-51"
    DUPLICATE_NAME = "EF-52"
    GROUP_ENABLED_FLAG = "EF-53"
    EMPTY_GROUP = "EF-54"


class EnergySystemError(Exception):
    """Base class for every hard error raised by the energy-system machinery.

    Catching this one class catches every problem an energy-system file can cause,
    regardless of the stage that found it — reading the YAML document, checking its
    structure, importing the component classes, resolving sizing or wiring the
    simulator. It derives from plain :class:`Exception` rather than from
    :class:`ValueError` so that a broad ``except ValueError`` elsewhere in the
    application cannot swallow it and continue with a half-built energy system,
    which is exactly the silent-failure mode this format sets out to remove.
    """


class EnergySystemFormatError(EnergySystemError):
    """A file was rejected because its text, shape or internal references are wrong.

    This covers everything decidable from the document alone: an unsupported file
    suffix, a wrong schema version, a duplicate key, an unknown top-level or entry
    key, an input item matching none of the accepted shapes, a reference naming a
    component that the file does not declare, a group rule violation and an absolute
    filesystem path. None of these needs a component class to be imported, so they
    are all reported before the first import happens.

    The exception carries the error identifier, the location of the offending element
    and the problem text as separate attributes, and assembles them into one message
    of the form ``EF-nn at <location>: <problem>``. When a closed set of acceptable
    values exists it is appended as ``Valid <what>: a, b, c.``, optionally preceded by
    a "did you mean" hint computed from the offending value, so that the message tells
    the author both what is wrong and what may be written instead.
    """

    #: How many close matches a "did you mean" hint offers at most. One suggestion is
    #: the useful case (a typo); a long list of near-misses is noise next to the full
    #: list of valid values that the message already carries.
    MAXIMUM_SUGGESTIONS: ClassVar[int] = 3

    #: Similarity below which a candidate is not offered as a suggestion at all. The
    #: value is difflib's own default and errs towards silence: a wrong suggestion is
    #: worse than none, because the full set of valid values follows anyway.
    SUGGESTION_CUTOFF: ClassVar[float] = 0.6

    def __init__(
        self,
        error_id: EnergySystemErrorId,
        location: str,
        problem: str,
        *,
        alternatives: Optional[Sequence[str]] = None,
        alternatives_label: str = "values",
        offending_value: Optional[str] = None,
        remedy: Optional[str] = None,
    ) -> None:
        """Builds the exception and formats its single-line message.

        Args:
            error_id: The catalogue identifier of the condition; it opens the message
                so that a report can be looked up without parsing prose.
            location: Where the problem sits, spelled as a dotted key path into the
                document (``components.battery.sizing_sources``) or as a file name for
                whole-document problems.
            problem: One sentence naming what is wrong with that element.
            alternatives: The closed set of acceptable values, when one exists. It is
                sorted and listed in the message, and it is also the pool the "did you
                mean" hint is drawn from.
            alternatives_label: The noun describing the alternatives, used to write
                ``Valid components: …`` rather than a generic ``Valid values: …``.
            offending_value: The value that was written, used only to compute the
                suggestion; omit it when the problem is a missing rather than a wrong
                value.
            remedy: An optional closing sentence telling the author what to do.
        """
        self.error_id = error_id
        self.location = location
        self.problem = problem
        self.alternatives = tuple(alternatives) if alternatives is not None else ()
        super().__init__(
            self.build_message(
                error_id,
                location,
                problem,
                alternatives=alternatives,
                alternatives_label=alternatives_label,
                offending_value=offending_value,
                remedy=remedy,
            )
        )

    @classmethod
    def build_message(
        cls,
        error_id: EnergySystemErrorId,
        location: str,
        problem: str,
        *,
        alternatives: Optional[Sequence[str]] = None,
        alternatives_label: str = "values",
        offending_value: Optional[str] = None,
        remedy: Optional[str] = None,
    ) -> str:
        """Assembles the message text from its parts, without raising anything.

        Keeping the formatting in a classmethod means the exact wording can be reused
        by a caller that wants to report a problem without raising — a batch validator
        collecting several findings, for instance — and it keeps the constructor free
        of formatting logic. The parts are always emitted in the same order so that
        messages of different conditions read alike.

        Args:
            error_id: The catalogue identifier opening the message.
            location: The dotted key path or file name of the offending element.
            problem: One sentence naming what is wrong.
            alternatives: The closed set of acceptable values, if any.
            alternatives_label: The noun used to introduce that set.
            offending_value: The written value, used for the "did you mean" hint.
            remedy: An optional closing instruction.

        Returns:
            The complete single-line message.
        """
        parts = [f"{error_id.value} at {location}: {problem}"]
        if alternatives:
            ordered = sorted(set(alternatives))
            hint = cls.suggest(offending_value, ordered)
            if hint:
                parts.append(f"Did you mean: {hint}?")
            parts.append(f"Valid {alternatives_label}: {', '.join(ordered)}.")
        if remedy:
            parts.append(remedy)
        return " ".join(parts)

    @classmethod
    def suggest(cls, offending_value: Optional[str], candidates: Iterable[str]) -> str:
        """Returns a comma-separated "did you mean" hint, or an empty string.

        The hint exists for the common failure — a misspelled component, preset or
        field name — where showing the one near match next to the full list saves the
        author from scanning it. Candidates that are not similar enough to the written
        value are dropped entirely rather than filled up to a fixed count, so a
        genuinely unrelated name produces no hint at all.

        Args:
            offending_value: The value the author wrote, or ``None`` when the problem
                is a missing value and no comparison is possible.
            candidates: The acceptable values to compare against.

        Returns:
            The matching candidates joined by ``", "``, or ``""`` when there is no
            value to compare or nothing is close enough.
        """
        if not offending_value:
            return ""
        matches = difflib.get_close_matches(
            offending_value,
            list(candidates),
            n=cls.MAXIMUM_SUGGESTIONS,
            cutoff=cls.SUGGESTION_CUTOFF,
        )
        return ", ".join(matches)
