"""The grouping decision: what a person said each observed difference means.

The matrix next door says what differs. Nothing in it says whether a difference is structure. A
photovoltaic array that is half the size in one configuration and a battery that is absent in
another look alike to any diff, and only one of them is a part of the household that can be
switched off; the other is a number somebody chose. That judgement is made once per setup by a
person, in a table, and this module is the shape it has afterwards.

What lives here is only the decision as a value: the four things a person can say about a row, the
switch positions a probe column stands for, and the whole of one setup's table. Reading and writing
the committed file is next door in :mod:`hisim.energy_system.recording.grouping_io`, and holding a
decision against what was actually observed is in
:mod:`hisim.energy_system.recording.grouping_checks`. The split is what lets a test build a decision
in three lines without touching YAML, and it keeps the model free of any opinion about files.

The workbook is the editing surface and ``<stem>.grouping.yaml`` is the artefact. A spreadsheet is
the right place to fill four columns in against a hundred rows and the wrong place to review a
change in, so the decisions are normalised into that file, which is what is committed, what a
reviewer reads as a diff and what the second recording pass is handed. The workbook is regenerated
from the probe runs whenever it is wanted and is never committed.
"""

# clean

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import ClassVar, Dict, Optional, Mapping, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError


class GroupingKeys:
    """The key names of the committed grouping document, written down once.

    A reader, a writer and a workbook importer all have to agree on these strings, and three
    copies of them is how a key comes to be spelled one way in one place and another way in the
    next. Holding them as class-scope constants also lets a rejection list the valid keys from the
    same source the reader checks against, so the message cannot go stale.
    """

    #: The top-level keys of the document, in the order it writes them.
    DOCUMENT: ClassVar[Tuple[str, ...]] = ("setup", "probes", "assignments", "configurations")

    #: The keys one assignment entry may carry.
    ASSIGNMENT_KEYS: ClassVar[Tuple[str, ...]] = ("assign", "note")

    #: The keys one configuration entry may carry.
    CONFIGURATION_KEYS: ClassVar[Tuple[str, ...]] = ("groups", "variants")

    SETUP: ClassVar[str] = "setup"
    PROBES: ClassVar[str] = "probes"
    ASSIGNMENTS: ClassVar[str] = "assignments"
    CONFIGURATIONS: ClassVar[str] = "configurations"
    ASSIGN: ClassVar[str] = "assign"
    NOTE: ClassVar[str] = "note"
    GROUPS: ClassVar[str] = "groups"
    VARIANTS: ClassVar[str] = "variants"


class AssignmentKind(enum.Enum):
    """The four things a person can say about a component that is not the same everywhere.

    They are exhaustive by construction: a difference either is not about membership at all
    (``ORDINARY`` for a component that stays put, ``OVERRIDE`` for a value a consumer sets), or it
    is, and then the component is either switched on and off on its own (``GROUP``) or it is one
    face of a choice between alternative worlds (``VARIANT``).

    ``OVERRIDE`` is the member that keeps the tool honest. Without it every knob that happens to
    change a number would be pushed into a group, and the file would grow structure for things that
    have none — which is exactly why nothing here is ever decided automatically.
    """

    ORDINARY = ""
    GROUP = "group"
    VARIANT = "variant"
    OVERRIDE = "override"


@dataclass(frozen=True)
class Assignment:
    """One person's decision about one row of the components sheet.

    An assignment is the whole of what the tool is told: which component, what kind of thing its
    difference is, under which name, and — for a variant — whether the component belongs to one
    named option or to all of them. The note travels with it because the reasoning is the part a
    reviewer of the committed file most needs and the part a spreadsheet cell most easily loses.

    The two variant spellings answer the two shapes a variant row has. A component that only exists
    in one world names its option; a component that exists in every world and is *wired* differently
    in each names no option, because it is written out in full in each of them.
    """

    #: Separator between the kind and the name a group or variant assignment carries.
    KIND_SEPARATOR: ClassVar[str] = ":"

    #: Separator between a variant's name and the one option a component belongs to.
    OPTION_SEPARATOR: ClassVar[str] = "/"

    component: str
    kind: AssignmentKind = AssignmentKind.ORDINARY
    name: str = ""
    option: Optional[str] = None
    note: str = ""

    def __post_init__(self) -> None:
        """Refuses a combination :meth:`parse` would never produce.

        The written form uses ``:`` and ``/`` as separators with no escaping, so a name or an
        option carrying one would not round-trip through :attr:`text` and :meth:`parse` — it would
        come back as a different assignment. Constructing such a value directly is a programming
        error rather than a malformed file, so the refusal is a plain :class:`ValueError`.

        Raises:
            ValueError: For a separator inside a name or option, a switch assignment without a
                name, an ordinary or override assignment carrying one, or a group with an option.
        """
        named = self.kind in (AssignmentKind.GROUP, AssignmentKind.VARIANT)
        if named and not self.name:
            raise ValueError(f"a {self.kind.value} assignment needs a name.")
        if not named and (self.name or self.option):
            raise ValueError(f"an assignment of kind {self.kind!r} carries no name or option.")
        if self.kind is AssignmentKind.GROUP and self.option:
            raise ValueError("a group assignment carries no option; only a variant does.")
        for part in (self.name, self.option or ""):
            if self.KIND_SEPARATOR in part or self.OPTION_SEPARATOR in part:
                raise ValueError(
                    f"'{part}' cannot be a switch name or option: ':' and '/' are the separators "
                    "of the written form and would not survive the round trip."
                )

    @property
    def text(self) -> str:
        """The canonical spelling of this assignment, as the sheet cell and the file both write it.

        Returns:
            The empty string for an ordinary component, ``override``, ``group:<name>``,
            ``variant:<name>`` or ``variant:<name>/<option>``.
        """
        if self.kind in (AssignmentKind.ORDINARY, AssignmentKind.OVERRIDE):
            return str(self.kind.value)
        suffix = f"{self.OPTION_SEPARATOR}{self.option}" if self.option else ""
        return f"{self.kind.value}{self.KIND_SEPARATOR}{self.name}{suffix}"

    @classmethod
    def parse(cls, component: str, text: str, note: str, origin: str) -> "Assignment":
        """Reads one written assignment.

        Args:
            component: The component the assignment is about.
            text: The cell's text, which may be empty.
            note: The reasoning written beside it.
            origin: The file or workbook it came from, for the message.

        Returns:
            The assignment.

        Raises:
            EnergySystemRecordingError: ``EF-R6`` when the text is none of the four forms, or names
                a group or a variant with an empty name.
        """
        written = (text or "").strip()
        if not written:
            return cls(component=component, note=note)
        if written == AssignmentKind.OVERRIDE.value:
            return cls(component=component, kind=AssignmentKind.OVERRIDE, note=note)
        head, separator, tail = written.partition(cls.KIND_SEPARATOR)
        kinds = {AssignmentKind.GROUP.value: AssignmentKind.GROUP, AssignmentKind.VARIANT.value: AssignmentKind.VARIANT}
        if not separator or head not in kinds or not tail.strip():
            raise cls._error(component, written, origin)
        name, _, option = tail.strip().partition(cls.OPTION_SEPARATOR)
        if not name.strip() or (kinds[head] is AssignmentKind.GROUP and option):
            raise cls._error(component, written, origin)
        return cls(
            component=component,
            kind=kinds[head],
            name=name.strip(),
            option=option.strip() or None,
            note=note,
        )

    @classmethod
    def _error(cls, component: str, written: str, origin: str) -> EnergySystemRecordingError:
        """Builds the rejection of an unreadable assignment cell; the caller raises it.

        Args:
            component: The row's component.
            written: What the cell said.
            origin: Where it was written.

        Returns:
            The exception.
        """
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_UNASSIGNED_DIFFERENCE,
            f"{origin}:assignments.{component}",
            f"'{written}' is not an assignment.",
            alternatives=("", "override", "group:<name>", "variant:<name>", "variant:<name>/<option>"),
            alternatives_label="forms",
            offending_value=written,
        )


@dataclass(frozen=True)
class ConfigurationSelection:
    """What one probe column stands for once the decisions are made: switch positions, not fields.

    The ``configurations`` sheet is where the two halves of the decision meet. The components sheet
    says which switches exist; this says, for every column, where each of them stands. The grouped
    file is written with the baseline column's positions, and every other column is a claim the
    pass then proves by realizing the file at those positions and comparing.
    """

    column: str
    groups: Mapping[str, bool] = field(default_factory=dict)
    variants: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Grouping:
    """A whole grouping decision, as committed: which setup, which probes, and every judgement.

    The object is deliberately inert and knows nothing about components or files. It is read from
    the committed YAML, written back to it, and checked against a probe matrix; the second
    recording pass is what turns it into groups and variants. Keeping it that way is what lets the
    round-trip — regenerate the workbook, re-import it, get the same file — be tested without
    recording anything.

    Assignments are held in the order the table lists them, which is the registration order of the
    components, so that the committed file reads down the system the way the recorded twin does and
    a re-export produces the same bytes.
    """

    #: The suffix the committed decision carries, appended to the setup's own stem.
    SUFFIX: ClassVar[str] = ".grouping.yaml"

    #: The one line above the document saying what it is and that it is not hand-maintained alone.
    HEADER: ClassVar[str] = (
        "# The grouping decision for {setup}: what each difference between its probe\n"
        "# configurations means. Edited through the workbook, committed in this form.\n"
    )

    setup: str
    probes: str
    assignments: Tuple[Assignment, ...] = ()
    configurations: Tuple[ConfigurationSelection, ...] = ()
    origin: str = "<text>"

    def assignment(self, component: str) -> Assignment:
        """The decision made about one component, defaulting to "ordinary".

        Args:
            component: The component to look up.

        Returns:
            Its assignment; an ordinary one when the table says nothing about it.
        """
        for assignment in self.assignments:
            if assignment.component == component:
                return assignment
        return Assignment(component=component)

    def selection(self, column: str) -> ConfigurationSelection:
        """The switch positions one probe column stands for.

        Args:
            column: The column to look up.

        Returns:
            Its selection; an empty one when the table says nothing about it.
        """
        for configuration in self.configurations:
            if configuration.column == column:
                return configuration
        return ConfigurationSelection(column=column)

    def group_names(self) -> Tuple[str, ...]:
        """The groups the assignments create, in the order they are first named.

        Returns:
            One name per group.
        """
        names: Dict[str, None] = {}
        for assignment in self.assignments:
            if assignment.kind is AssignmentKind.GROUP:
                names.setdefault(assignment.name, None)
        return tuple(names)

    def variant_names(self) -> Tuple[str, ...]:
        """The variants the assignments create, in the order they are first named.

        Only the assignments can create a variant, exactly as only they can create a group: a
        variant exists because some component belongs to it, so a name that appears in the
        ``configurations`` sheet alone has no members and is a typo to refuse rather than a switch
        to offer. :meth:`variant_options` deliberately answers a wider question — the *options* of
        a variant may legitimately be named by configurations alone — so the check that a selection
        names only real switches must use this method and not that one.

        Returns:
            One name per variant.
        """
        names: Dict[str, None] = {}
        for assignment in self.assignments:
            if assignment.kind is AssignmentKind.VARIANT:
                names.setdefault(assignment.name, None)
        return tuple(names)

    def variant_options(self) -> Dict[str, Tuple[str, ...]]:
        """The variants the assignments create with the options each of them offers.

        An option exists because an assignment named it or because a configuration selected it: a
        variant whose two worlds differ only in how a surviving component is wired has assignments
        that name no option at all, and then the ``configurations`` sheet is the only place the
        option names are written.

        Returns:
            One entry per variant, holding its option names in first-seen order.
        """
        options: Dict[str, Dict[str, None]] = {}
        for assignment in self.assignments:
            if assignment.kind is AssignmentKind.VARIANT:
                options.setdefault(assignment.name, {})
                if assignment.option:
                    options[assignment.name].setdefault(assignment.option, None)
        for configuration in self.configurations:
            for variant, option in configuration.variants.items():
                options.setdefault(variant, {}).setdefault(option, None)
        return {variant: tuple(names) for variant, names in options.items()}

    def members(self, kind: AssignmentKind, name: str, option: Optional[str] = None) -> Tuple[str, ...]:
        """The components one switch owns, in table order.

        Args:
            kind: Whether a group or a variant is meant.
            name: The switch's name.
            option: For a variant, the option whose components are wanted; a component assigned to
                the variant without an option belongs to every one of them.

        Returns:
            The component names.
        """
        return tuple(
            assignment.component
            for assignment in self.assignments
            if assignment.kind is kind
            and assignment.name == name
            and (option is None or assignment.option is None or assignment.option == option)
        )
