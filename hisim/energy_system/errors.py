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
:class:`EnergySystemCatalogueError` takes the alternatives as structured data rather than
leaving each call site to format them.

Four concrete classes derive from it, one per lifecycle stage that can reject a file:
:class:`EnergySystemFormatError` for everything decidable from the document alone,
:class:`EnergySystemBindingError` for what only the component classes can decide,
:class:`EnergySystemSizingError` for what only the sizing kernel can, and
:class:`EnergySystemWiringError` for what only the built components can. The split lets a
caller tell "this file is wrong" from "these classes are not ready yet" without reading the
message.
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

    Three bands run past ten members and continue with letters rather than renumbering the
    ones already in use: ``EF-1A``/``EF-1B`` close the entry band with the two conditions
    a value in a ``config`` block can hit, ``EF-2A`` closes the connection band with a tag
    name no enum knows, and ``EF-4A`` … ``EF-4H`` wrap the eight failure modes of the sizing
    kernel one-to-one, with ``EF-4X`` for a kernel failure that matches none of them. The
    ``EF-6x`` band closes the list with the two ways writing a run record can fail, neither
    of which an author can cause: a record that is not fully concrete, and a re-execution
    that did not reproduce the record it was handed.

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
    METADATA_ON_A_PLAIN_RUN = "EF-09"
    CLASS_NOT_IMPORTABLE = "EF-10"
    PRESET_AND_CONSTRUCTOR = "EF-11"
    NO_CONFIGURATION_SOURCE = "EF-12"
    UNKNOWN_PRESET = "EF-13"
    MALFORMED_CONSTRUCTOR = "EF-14"
    UNKNOWN_CONSTRUCTOR = "EF-15"
    CONSTRUCTOR_ARGUMENT = "EF-16"
    UNKNOWN_CONFIG_FIELD = "EF-17"
    UNKNOWN_ENTRY_KEY = "EF-18"
    UNDECODABLE_VALUE = "EF-1A"
    AUTO_ON_CONCRETE_FIELD = "EF-1B"
    UNCLASSIFIABLE_INPUT_ITEM = "EF-19"
    UNKNOWN_SOURCE = "EF-20"
    UNKNOWN_OUTPUT_PORT = "EF-21"
    UNKNOWN_INPUT_PORT = "EF-22"
    NO_DECLARED_DEFAULTS = "EF-23"
    MIXED_INPUT_SPELLING = "EF-24"
    DUPLICATE_FEED = "EF-25"
    DUPLICATE_WIRE = "EF-26"
    NOT_AN_AGGREGATOR = "EF-27"
    NO_CHANNEL_MATCH = "EF-28"
    DISPATCH_RULE_VIOLATED = "EF-29"
    UNKNOWN_TAG = "EF-2A"
    PORT_TYPE_MISMATCH = "EF-30"
    UNCONNECTED_MANDATORY_INPUT = "EF-31"
    PORT_NAME_COLLISION = "EF-32"
    COMPONENT_CONSTRUCTION_FAILED = "EF-33"
    UNKNOWN_SIZING_SOURCE = "EF-40"
    SIZING_FACT_MISMATCH = "EF-41"
    DISABLED_SIZING_SOURCE = "EF-42"
    FACT_NOT_READ = "EF-43"
    SIZING_UNPROVIDED = "EF-4A"
    SIZING_AMBIGUOUS = "EF-4B"
    SIZING_NOT_A_PROVIDER = "EF-4C"
    SIZING_NULL_VALUE = "EF-4D"
    SIZING_SHAPE_MISMATCH = "EF-4E"
    SIZING_FIELD_CYCLE = "EF-4F"
    SIZING_MANY_UNSUPPORTED = "EF-4G"
    SIZING_DUPLICATE_NAME = "EF-4H"
    SIZING_FAILED = "EF-4X"
    NESTED_GROUP = "EF-50"
    COMPONENT_IN_TWO_GROUPS = "EF-51"
    DUPLICATE_NAME = "EF-52"
    GROUP_ENABLED_FLAG = "EF-53"
    EMPTY_GROUP = "EF-54"
    RECORD_NOT_CONCRETE = "EF-60"
    RERUN_NOT_REPRODUCED = "EF-61"
    RECORDED_NAME_INVALID = "EF-R1"
    RECORDED_QUALIFIED_IDENTITY = "EF-R2"
    RECORDED_ABSOLUTE_PATH = "EF-R3"
    RECORDED_PRESET_GONE = "EF-R4"
    RECORDED_FILE_REJECTED = "EF-R5"


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


class EnergySystemCatalogueError(EnergySystemError):
    """Shared behaviour of every rejection that carries a catalogue identifier.

    The four concrete error classes below differ only in which lifecycle stage decided
    the condition, and that difference matters to a caller: a tool checking files without
    HiSim's component tree can only ever see the document-level failures, whereas a run
    can see all three. What they share is the message discipline, which lives here: the
    identifier, the location of the offending element and the problem text are kept as
    separate attributes and assembled into one line of the form
    ``EF-nn at <location>: <problem>``.

    When a closed set of acceptable values exists it is appended as ``Valid <what>: a, b,
    c.``, optionally preceded by a "did you mean" hint computed from the offending value,
    so that the message tells the author both what is wrong and what may be written
    instead. Taking the alternatives as structured data rather than as prose is what makes
    that rule enforceable instead of a convention every call site re-implements.
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


class EnergySystemFormatError(EnergySystemCatalogueError):
    """A file was rejected because its text, shape or internal references are wrong.

    This covers everything decidable from the document alone: an unsupported file suffix,
    a wrong schema version, a duplicate key, an unknown top-level or entry key, an input
    item matching none of the accepted shapes, a reference naming a component that the
    file does not declare, a group rule violation, an absolute filesystem path and a
    sizing reference left dangling by a switched-off group. None of these needs a
    component class to be imported, so they are all reported before the first import
    happens.

    Because the rules behind them are pure document rules, an editor plug-in, a schema
    exporter or a batch-authoring tool can provoke and present exactly this class without
    ever pulling in HiSim's component tree.
    """


class EnergySystemBindingError(EnergySystemCatalogueError):
    """A file was rejected against the component classes it names.

    Everything here needs the class in memory: whether the dotted class path imports at
    all and names a component, whether the preset or the named constructor exists, whether
    the constructor's arguments match its parameters, whether a ``config`` key is a field
    of the config class, whether a value decodes into that field's type, and whether a
    ``sizing_sources`` key is a fact the class's laws actually read.

    The split from :class:`EnergySystemFormatError` is not cosmetic: as long as a class has
    not been converted to presets and laws, a perfectly well-formed file will raise this
    class and only this class, so a caller can distinguish "the file is wrong" from "the
    classes are not ready yet".
    """


class EnergySystemWiringError(EnergySystemCatalogueError):
    """A file's connections could not be made between the components it describes.

    Everything here needs the built components rather than only their classes, because HiSim
    creates a component's ports in its constructor: whether a wire's two port names exist,
    whether their load types and units agree, whether a bare item finds declared default
    connections for the source's class, whether an aggregator feed matches one of the target's
    declared channels, whether a dispatch block is allowed on that channel, whether an input is
    fed twice and whether a mandatory input is left open.

    A file raising this class is well formed, names classes that exist and configures them
    correctly — the system it describes simply cannot be wired as written, so the message names
    both ends of the offending connection and, where the set is closed, the ports or channels
    that were available instead.
    """


class EnergySystemSizingError(EnergySystemCatalogueError):
    """Cross-component sizing could not be resolved for this file.

    Raised for the conditions the sizing kernel decides — a fact nobody provides, a fact
    several components provide with no source line to settle it, a source line naming a
    component that does not declare the fact, a null-valued provider, a mapping whose shape
    contradicts the law's cardinality, a cycle between sibling fields, a many-cardinality
    read and a duplicate instance name — each wrapped with the file location that caused it.

    The kernel's own message is kept verbatim inside this one, because it already names the
    candidates and prints the paste-ready ``sizing_sources`` block; the wrapper adds what
    the kernel cannot know, namely which entry of which file the failing config came from.
    """


class EnergySystemRecordError(EnergySystemCatalogueError):
    """A run record could not be written, or a re-execution did not reproduce one.

    The two conditions in this class are the only ones in the whole catalogue that no author
    can provoke: a record that still carries the ``AUTO`` sentinel or a value that cannot be
    written as plain data, and a re-run whose own record differs from the record it was handed.
    Both mean the machinery broke its own promise — a record is a complete, concrete statement
    of what ran, and re-running one reproduces it — so they are raised loudly rather than
    logged, even though nothing about the input file is wrong.

    The message names the component and the field involved, because that is where a breach of
    either promise is diagnosed: a configuration whose dump is not plain data, or a value that
    the record and its re-execution disagree about.
    """


class EnergySystemRecordingError(EnergySystemCatalogueError):
    """A Python setup could not be recorded as an energy-system file.

    The ``EF-Rx`` band is the only one whose subject is a Python setup rather than a file. A
    component whose runtime name is not an identifier, a component carrying a building or a unit
    in its identity, an absolute path that survived re-symbolisation, a preset provenance naming a
    builder the class no longer has, and a recorded file that does not load and build again: each
    of them means the observed system cannot be written down faithfully, and each is a defect in
    the setup or in a component class rather than something an author typed.

    The distinction from :class:`EnergySystemRecordError` matters to a caller. That class means the
    machinery broke its promise about a record it wrote; this one means the input it was asked to
    record cannot be expressed in the format, which is a finding about the setup and is reported
    naming the setup module, the component and the rule.
    """
