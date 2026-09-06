"""The human-readable overview of every grouping decision this repository has committed.

The grouping pass leaves four files per setup behind — a probe list, a decision table, a grouped
energy system and the flat twin it is checked against — and none of them reads as an answer to the
question a person actually arrives with: *which setups have structure yet, what is that structure,
and why was each difference called what it was called*. Answering it means holding three files side
by side and counting, which is exactly the kind of work that is done once, written down, and then
quietly goes stale. So it is generated instead, from the committed files, by one command.

The page is therefore a rendering and never a source. Every number on it is counted, every name is
read out of a file and every judgement note is the note the grouping table carries, unfolded. What
is fixed prose is only what is true of the format rather than of a setup: what the ``Overrides``
column counts, what a shared ``components:`` section is, what makes the baseline column the
committed twin. That split is what lets the page be regenerated on every change and compared byte
for byte in a test, the same way the recorded twins are.

Three orderings carry the whole determinism of the output, and all three are file order rather than
sorted order or set order. The setups come in the order their ``*.grouping.yaml`` files sort by
name; the variant groups, their options and the members of each come in the order the grouped file
writes them; and the overrides come in the order the shared ``components:`` section writes them.
Nothing here iterates a set into the page.
"""

# clean

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

from hisim.energy_system.loader import load_energy_system
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.recording.grouping import Assignment, AssignmentKind, Grouping
from hisim.energy_system.recording.grouping_io import read_grouping
from hisim.energy_system.recording.probe_session import GroupingPass
from hisim.energy_system.recording.probes import ProbeList
from hisim.energy_system.recording.session import RecordedFileWriter, RecordingSession
from hisim.energy_system.repository import RepositoryLayout


class Prose:
    """The small English the page needs: number words, joined lists, first sentences.

    A generated page that says "4 of the shared components are overrides" reads as a machine's
    output, and a page nobody reads is not worth generating, so the few places where a count
    belongs in running text spell it out. Everything here is deliberately tiny and deliberately
    English-only: the page is one document in one language, and a general pluralisation library
    would be more code than the page it serves.
    """

    #: The counts that have a word. Beyond the last of them a digit reads better than a word
    #: anyway, so the fallback is the digit rather than a longer table.
    WORDS: ClassVar[Tuple[str, ...]] = (
        "no",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
    )

    @classmethod
    def word(cls, count: int) -> str:
        """Spells one small count as a word.

        Args:
            count: The number to spell.

        Returns:
            The English word for it, or its digits when it is larger than the table.
        """
        return cls.WORDS[count] if 0 <= count < len(cls.WORDS) else str(count)

    @classmethod
    def capitalised(cls, count: int) -> str:
        """Spells one small count as a word that opens a sentence.

        Args:
            count: The number to spell.

        Returns:
            The word from :meth:`word` with its first letter capitalised.
        """
        spelled = cls.word(count)
        return spelled[:1].upper() + spelled[1:]

    @classmethod
    def listed(cls, items: Sequence[str], conjunction: str = "and") -> str:
        """Joins names the way a sentence joins them rather than the way a table does.

        Args:
            items: The already-rendered names, in the order they should appear.
            conjunction: The word before the last of them.

        Returns:
            One name, two joined by the conjunction, or a comma list ending in it; the empty
            string for no names at all.
        """
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"

    @classmethod
    def first_sentence(cls, text: str) -> str:
        """The opening sentence of a description, with its line wrapping undone.

        Args:
            text: The whole description, as the probe list wrote it.

        Returns:
            Everything up to and including the first sentence-ending period, or the whole
            collapsed text when it holds only one sentence.
        """
        collapsed = cls.unfold(text)
        head, separator, _ = collapsed.partition(". ")
        return f"{head}." if separator else collapsed

    @classmethod
    def unfold(cls, text: str) -> str:
        """Undoes the line wrapping a YAML scalar carries.

        A note or a description is written across several lines in the committed file so that its
        diff stays readable, and the page wants the sentence rather than the wrapping.

        Args:
            text: The scalar as it was read.

        Returns:
            The same text with every run of whitespace collapsed to one space.
        """
        return " ".join(text.split())

    @classmethod
    def sentence(cls, text: str) -> str:
        """Ends a piece of authored text in a full stop when it does not already end in one.

        Args:
            text: The text, normally a grouped file's description.

        Returns:
            The unfolded text with a period appended when it needs one.
        """
        unfolded = cls.unfold(text)
        return unfolded if not unfolded or unfolded[-1] in ".!?" else f"{unfolded}."


class Markdown:
    """The two escapes and the one table shape the page uses, in one place.

    Nothing here is a Markdown library. The page writes exactly one kind of table and exactly one
    kind of code span, and the only character that can break either is the pipe inside a cell, so
    the whole of the formatting knowledge is these few methods rather than a dependency.
    """

    #: What a cell holds when there is nothing to say in it.
    EMPTY: ClassVar[str] = "—"

    #: How a table separates one line of a cell from the next; a Markdown cell is one line.
    LINE_BREAK: ClassVar[str] = "<br/>"

    @classmethod
    def code(cls, text: str) -> str:
        """Renders one name as a code span.

        Args:
            text: The name.

        Returns:
            The name in backticks.
        """
        return f"`{text}`"

    @classmethod
    def cell(cls, text: str) -> str:
        """Makes one piece of text safe to put in a table cell.

        Args:
            text: The cell's content.

        Returns:
            The text with every pipe escaped, or the empty marker when there is none.
        """
        return text.replace("|", r"\|") if text else cls.EMPTY

    @classmethod
    def table(cls, headings: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
        """Renders one table, heading rule included.

        Args:
            headings: The column headings, already rendered.
            rows: One sequence of already-rendered cells per row.

        Returns:
            The table's lines, without a trailing blank line.
        """
        lines = [
            "| " + " | ".join(headings) + " |",
            "| " + " | ".join("---" for _ in headings) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        return lines


class FleetCensus:
    """How many setups this repository has recorded, and how many of them are grouped yet.

    The denominator of the page's one fleet-wide sentence, and the one number on it that is not
    read out of a grouping file. It has to be counted rather than configured, because "the setups
    that have a twin" is exactly the set the recorder writes and any written-down copy of it goes
    stale the first time a setup is added.

    Three kinds of file live in ``energy_systems/`` and only one of them is a flat twin. The
    hand-written exemplar carries no recorder marker in its header and is not a twin of anything; a
    grouped file carries the extra header line naming the decision that shaped it and is the second
    file of a setup already counted; and a probe recording carries the line naming its column and is
    one configuration rather than one setup. All three are told apart by the header, because the
    header is the only part of those files their writers control.
    """

    #: How a grouped file's header opens, taken from the line the grouping pass writes so that the
    #: two spellings cannot drift apart.
    GROUPED_MARKER: ClassVar[str] = GroupingPass.GROUPING_LINE.partition("{")[0]

    #: How a probe recording's header line opens, taken from the writer for the same reason.
    PROBE_MARKER: ClassVar[str] = RecordedFileWriter.PROBE_LINE.partition("{")[0]

    @classmethod
    def flat_twins(cls, directory: Path) -> Tuple[Path, ...]:
        """The recorded flat twins of one directory, in sorted file-name order.

        Args:
            directory: The directory the recorded files live in, normally ``energy_systems/``.

        Returns:
            One path per setup that has a twin; empty when the directory does not exist.
        """
        if not directory.is_dir():
            return ()
        found = []
        for path in sorted(directory.glob(f"*{RecordedFileWriter.SUFFIX}")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if cls._is_flat_twin(text):
                found.append(path)
        return tuple(found)

    @classmethod
    def _is_flat_twin(cls, text: str) -> bool:
        """Whether one file's header says it is the plain recording of one setup.

        Args:
            text: The whole file.

        Returns:
            ``True`` when the recorder wrote it and neither a grouping nor a probe line follows.
        """
        if not RecordedFileWriter.was_recorded(text):
            return False
        header, _ = RecordedFileWriter.split(text)
        lines = header.splitlines()
        return not any(line.startswith((cls.GROUPED_MARKER, cls.PROBE_MARKER)) for line in lines)


@dataclass(frozen=True)
class GroupedSetup:
    """One setup's three committed files, read, plus the views the page takes of them.

    The decision says what each difference means, the grouped file says what that came to, and the
    probe list says which configurations the claim covers; no one of the three answers a question
    the page asks on its own. Holding them together as one value is what lets every renderer below
    be a pure function of a setup rather than a walk over directories.

    Nothing here decides anything. The grouped file is the authority on order and on membership,
    the grouping table is the authority on judgement, and where the two overlap — which shared
    component is an override — the page reads the membership from the file and the reason from the
    table, so the two cannot be made to disagree by this module.
    """

    #: The suffix of a committed grouping decision, swept for by the command that renders the page.
    GROUPING_SUFFIX: ClassVar[str] = Grouping.SUFFIX

    stem: str
    grouping: Grouping
    probes: ProbeList
    system: EnergySystemFile

    @classmethod
    def read(cls, grouping_path: Path, root: Path) -> "GroupedSetup":
        """Reads the three files of one grouped setup.

        Args:
            grouping_path: The committed ``*.grouping.yaml``.
            root: The repository root the decision's own paths are relative to.

        Returns:
            The setup, with its probe list and its grouped energy system loaded.

        Raises:
            EnergySystemRecordingError: When the decision or the probe list is malformed.
            EnergySystemFormatError: When the grouped energy-system file is.
        """
        decision = read_grouping(grouping_path)
        stem = Path(decision.setup).stem
        grouped = grouping_path.parent / f"{stem}{GroupingPass.GROUPED_SUFFIX}"
        return cls(
            stem=stem,
            grouping=decision,
            probes=ProbeList.read(root / decision.probes),
            system=load_energy_system(grouped),
        )

    @property
    def shared(self) -> Tuple[str, ...]:
        """The components every configuration has, in the order the shared section writes them.

        Returns:
            One name per component of the grouped file's top-level ``components:`` block.
        """
        return tuple(self.system.components)

    @property
    def overrides(self) -> Tuple[str, ...]:
        """The shared components a consumer sets the value of, in shared-section order.

        Returns:
            One name per shared component the grouping table assigns ``override``.
        """
        return tuple(
            name for name in self.shared if self.grouping.assignment(name).kind is AssignmentKind.OVERRIDE
        )

    @property
    def fixed(self) -> Tuple[str, ...]:
        """The shared components nothing about is knobbed, in shared-section order.

        Returns:
            One name per shared component that is not an override.
        """
        overrides = set(self.overrides)
        return tuple(name for name in self.shared if name not in overrides)

    @property
    def group_names(self) -> Tuple[str, ...]:
        """The variant groups the grouped file declares, in file order.

        Returns:
            One name per group of the ``variants:`` section.
        """
        return tuple(self.system.variants)

    def options(self, group: str) -> Tuple[str, ...]:
        """The options one variant group offers, in the order the file writes them.

        Args:
            group: The group's name.

        Returns:
            One name per option.
        """
        return tuple(self.system.variants[group].options)

    def selected(self, group: str) -> str:
        """The option one variant group is resolved to in the committed file.

        Args:
            group: The group's name.

        Returns:
            The selected option's name.
        """
        return self.system.variants[group].selected

    def option_members(self, group: str, option: str) -> Tuple[str, ...]:
        """The components one option writes out, in the order its ``components:`` block does.

        Args:
            group: The group's name.
            option: The option's name.

        Returns:
            One name per component of that option.
        """
        return tuple(self.system.variants[group].options[option].components)

    def group_scoped(self, group: str) -> Tuple[str, ...]:
        """The components assigned to a variant group as a whole rather than to one option.

        These are the components that survive every option and are wired differently in each, so
        every option writes them out in full. They are what forces a variant rather than a group.

        Args:
            group: The group's name.

        Returns:
            One name per such component, in the order the grouping table lists them.
        """
        return tuple(
            assignment.component
            for assignment in self.grouping.assignments
            if assignment.kind is AssignmentKind.VARIANT
            and assignment.name == group
            and assignment.option is None
        )

    def assignment_rows(self) -> Tuple[Assignment, ...]:
        """Every judgement the table carries, in the one order the page states as its rule.

        Variant assignments come first, group by group in the order the grouped file's ``variants:``
        section lists them; inside a group the members assigned to the group as a whole come before
        the members assigned to a single option, and those follow the options and each option's own
        ``components:`` block. On/off groups follow, then the overrides in shared-section order.
        Anything the table says that none of those reached — a note left on a component with no
        assignment — is appended in table order rather than dropped.

        Returns:
            One assignment per row of the assignments table.
        """
        rows: List[Assignment] = []
        seen: Dict[str, None] = {}

        def take(component: str) -> None:
            """Adds one component's judgement to the rows, once."""
            if component in seen:
                return
            assignment = self.grouping.assignment(component)
            if assignment.kind is AssignmentKind.ORDINARY and not assignment.note:
                return
            seen[component] = None
            rows.append(assignment)

        for group in self.group_names:
            for component in self.group_scoped(group):
                take(component)
            for option in self.options(group):
                for component in self.option_members(group, option):
                    if self.grouping.assignment(component).option == option:
                        take(component)
        for name, group_block in self.system.groups.items():
            for component in group_block.components:
                if self.grouping.assignment(component).name == name:
                    take(component)
        for component in self.overrides:
            take(component)
        for assignment in self.grouping.assignments:
            take(assignment.component)
        return tuple(rows)


class MermaidShape:
    """The one picture the page draws per setup: where the grouped file puts each component.

    The diagram is a picture of the *file*, not of the wiring: one box per role a component can
    have — shared and fixed, shared and knobbed, one per option of each variant group — with the
    members listed inside the box. That is deliberate and is the whole reason the block is
    generated rather than drawn. A node per component would be unreadable at eleven components and
    unopenable at fifty, and it would say nothing the assignments table below does not already say
    better; a node per role says the one thing only a picture says, which is how few places there
    are for a component to be.
    """

    #: The fence the block is written in; artifact and repository renderers both know it.
    FENCE: ClassVar[str] = "```mermaid"

    #: The direction, which is top-down because a setup contains roles and a group contains options.
    HEADER: ClassVar[str] = "flowchart TD"

    #: The node ids of the two shared boxes, which are the same in every setup's diagram.
    SETUP_NODE: ClassVar[str] = "setup"
    FIXED_NODE: ClassVar[str] = "fixed"
    KNOBS_NODE: ClassVar[str] = "knobs"

    #: How the two shared boxes are titled, the count being what the reader wants first.
    FIXED_TITLE: ClassVar[str] = "fixed in every configuration ({count})"
    KNOBS_TITLE: ClassVar[str] = "knobs, stated at the baseline value ({count})"

    #: How a variant group's own box is titled.
    GROUP_TITLE: ClassVar[str] = "variant group: {name}"

    #: What marks the option the grouped file selects, both in its label and by its outline. The
    #: style is a stroke and not a fill, so the box stays readable in a dark theme as well.
    SELECTED_SUFFIX: ClassVar[str] = " (selected)"
    SELECTED_CLASS: ClassVar[str] = "selected"
    SELECTED_STYLE: ClassVar[str] = "classDef selected stroke:#2e7d32,stroke-width:3px;"

    #: How the members of a role are separated inside its label. A Markdown-safe break, because
    #: the same page renders in viewers that pass the label through a Markdown pass.
    MEMBER_SEPARATOR: ClassVar[str] = "<br/>"

    @classmethod
    def render(cls, setup: GroupedSetup) -> List[str]:
        """Renders the whole fenced block for one setup.

        Args:
            setup: The setup to draw.

        Returns:
            The block's lines, opening and closing fence included.
        """
        declarations: List[str] = [cls._node(cls.SETUP_NODE, [setup.stem])]
        edges: List[str] = []
        styling: List[str] = []
        if setup.fixed:
            declarations.append(
                cls._node(cls.FIXED_NODE, [cls.FIXED_TITLE.format(count=len(setup.fixed)), *setup.fixed])
            )
            edges.append(cls._edge(cls.SETUP_NODE, cls.FIXED_NODE))
        if setup.overrides:
            declarations.append(
                cls._node(cls.KNOBS_NODE, [cls.KNOBS_TITLE.format(count=len(setup.overrides)), *setup.overrides])
            )
            edges.append(cls._edge(cls.SETUP_NODE, cls.KNOBS_NODE))
        option_index = 0
        for number, group in enumerate(setup.group_names, start=1):
            group_node = f"group{number}"
            declarations.append(cls._node(group_node, [cls.GROUP_TITLE.format(name=group)]))
            edges.append(cls._edge(cls.SETUP_NODE, group_node))
            for option in setup.options(group):
                node = f"opt{cls._letters(option_index)}"
                option_index += 1
                chosen = option == setup.selected(group)
                title = f"{option}{cls.SELECTED_SUFFIX}" if chosen else option
                declarations.append(cls._node(node, [title, *setup.option_members(group, option)]))
                edges.append(cls._edge(group_node, node))
                if chosen:
                    styling.append(f"class {node} {cls.SELECTED_CLASS};")
        if styling:
            styling.insert(0, cls.SELECTED_STYLE)
        blocks = [block for block in (declarations, edges, styling) if block]
        lines = [cls.FENCE, cls.HEADER]
        for position, block in enumerate(blocks):
            if position:
                lines.append("")
            lines.extend(f"    {line}" for line in block)
        lines.append("```")
        return lines

    @classmethod
    def _node(cls, node: str, label_lines: Sequence[str]) -> str:
        """Declares one box, its members joined into a single label.

        Args:
            node: The node's id.
            label_lines: The title and then one member name per line.

        Returns:
            The declaration.
        """
        return f'{node}["{cls.MEMBER_SEPARATOR.join(label_lines)}"]'

    @classmethod
    def _edge(cls, source: str, target: str) -> str:
        """Draws one containment arrow.

        Args:
            source: The containing node.
            target: The contained node.

        Returns:
            The edge.
        """
        return f"{source} --> {target}"

    @classmethod
    def _letters(cls, index: int) -> str:
        """Names one option box by its position, so the ids read as a list rather than as numbers.

        Args:
            index: The option's zero-based position among every option of the diagram.

        Returns:
            ``A`` … ``Z``, then ``AA`` and onwards.
        """
        letters = ""
        position = index
        while True:
            letters = chr(ord("A") + position % 26) + letters
            position = position // 26 - 1
            if position < 0:
                return letters


class OverviewPage:
    """The whole page: a fleet table, then one section per grouped setup.

    Everything is assembled as a list of lines and joined once, with Unix line endings and a single
    trailing newline, because the page is compared byte for byte against its committed copy the way
    a recorded twin is. That comparison is the only thing that keeps the page honest, so nothing
    here may depend on the machine it runs on: no timestamp, no path outside the repository, no
    ordering that a filesystem decides.
    """

    #: Where the generated page is committed. Beside the rest of the pass's own documentation
    #: rather than in ``energy_systems/``, which holds files the tools read rather than pages.
    DEFAULT_OUTPUT: ClassVar[str] = "roadmap/declarative_energy_systems/grouping_overview.md"

    #: The comment above the title, which says the same thing to a person editing the raw file
    #: that the italic line below says to a person reading the rendered page.
    HEADER_COMMENT: ClassVar[Tuple[str, ...]] = (
        "<!--",
        "Generated by `hisim energy-system grouping overview` from the committed `*.grouping.yaml`",
        "files and the grouped energy-system files they produced. Do not edit by hand: every number,",
        "name and note on this page is read out of those files, and an edit here is lost the next time",
        "the command runs. To change what this page says, change the grouping table or re-record.",
        "-->",
    )

    #: The page's title and the line under it.
    TITLE: ClassVar[str] = "# Grouping overview"
    SUBTITLE: ClassVar[str] = (
        "*Generated from the committed grouping tables and grouped energy-system files by "
        "`hisim energy-system grouping overview`. Do not edit by hand.*"
    )

    #: The fleet table's columns, and the footnote that says what the bold option means.
    FLEET_HEADINGS: ClassVar[Tuple[str, ...]] = (
        "Setup",
        "Variant groups",
        "Options",
        "Components per option",
        "Overrides",
    )
    BASELINE_FOOTNOTE_MARK: ClassVar[str] = "[^baseline]"
    BASELINE_FOOTNOTE: ClassVar[str] = (
        "[^baseline]: The option in bold is the one the grouped file selects, which is the option the "
        "baseline probe column realizes. A grouped file's `variants:` section names it as `selected:`."
    )
    OVERRIDES_NOTE: ClassVar[str] = (
        "The `Overrides` column counts the components the grouping table assigns `override`, not the "
        "individual values the pass reports as consumer knobs; one override component can carry several "
        "knobbed values, and a component that belongs to a variant option can carry knobbed values too."
    )

    #: How one group's options and per-option counts are separated from the next group's.
    GROUP_SEPARATOR: ClassVar[str] = "; "

    #: The two per-setup tables' columns. The probe table grows one column per variant group.
    ASSIGNMENT_HEADINGS: ClassVar[Tuple[str, ...]] = ("Component", "Assignment", "Judgement")
    PROBE_HEADINGS: ClassVar[Tuple[str, str]] = ("Probe column", "What it varies")
    PROBE_GIST_HEADING: ClassVar[str] = "Gist"

    #: What the probe table writes for a column whose overlay is empty.
    BASELINE_OVERLAY: ClassVar[str] = "class defaults"

    #: The rule the assignments table is ordered by, stated on the page so that a reader can check
    #: it, and the clause that is only true of a page carrying an on/off group.
    ORDERING_RULE: ClassVar[str] = (
        "Ordering rule: variant assignments first, then overrides. Within the variant assignments, "
        "groups in the order the grouped file's `variants:` section lists them; within a group, the "
        "members assigned to the group as a whole before the members assigned to a single option, then "
        "the options in the order that section lists them, and within each option the order that "
        "option's `components:` block lists them. Within the overrides, the order the shared "
        "`components:` section lists them."
    )
    ORDERING_RULE_GROUPS: ClassVar[str] = (
        "An on/off group's members come between the two, the groups in the order the grouped file's "
        "`groups:` section lists them and within each the order its `components:` block lists them."
    )
    ORDERING_RULE_NOTES: ClassVar[str] = (
        "The judgement text is the note from the grouping table in full, with the YAML line wrapping "
        "undone."
    )

    #: What the probe table's own columns are, said once under it.
    PROBE_NOTE: ClassVar[str] = (
        "The `What it varies` column is the probe's `module_config` overlay, one `field = value` per "
        'line; the baseline column has no overlay at all, which is what "class defaults" means. The '
        "`Gist` column is the first sentence of the probe's description; the full description stays in "
        "the probe list. Columns are in the order the probe list declares them."
    )

    def __init__(self, setups: Sequence[GroupedSetup], recorded: int) -> None:
        """Prepares one rendering.

        Args:
            setups: The grouped setups, in the order their sections should appear.
            recorded: How many flat twins the repository holds, which is the fleet denominator.
        """
        self.setups = tuple(setups)
        self.recorded = recorded

    def render(self) -> str:
        """Renders the whole page.

        Returns:
            The page's text, with Unix line endings and one trailing newline.
        """
        lines: List[str] = [*self.HEADER_COMMENT, "", self.TITLE, "", self.SUBTITLE, ""]
        lines.extend(self._fleet())
        for setup in self.setups:
            lines.append("")
            lines.extend(self._setup(setup))
        return "\n".join(lines) + "\n"

    def _fleet(self) -> List[str]:
        """Renders the fleet table, its footnotes and the sentence about the ungrouped setups.

        Returns:
            The section's lines.
        """
        rows = [self._fleet_row(setup) for setup in self.setups]
        lines = ["## Fleet", ""]
        lines.extend(Markdown.table(self.FLEET_HEADINGS, rows))
        if any(setup.group_names for setup in self.setups):
            lines.extend(["", self.BASELINE_FOOTNOTE])
        lines.extend(["", self.OVERRIDES_NOTE, "", self._census()])
        return lines

    def _fleet_row(self, setup: GroupedSetup) -> List[str]:
        """Renders one setup's row of the fleet table.

        Args:
            setup: The setup the row is about.

        Returns:
            The row's five cells.
        """
        groups = [Markdown.code(group) for group in setup.group_names]
        options = []
        counts = []
        for group in setup.group_names:
            rendered = []
            for option in setup.options(group):
                code = Markdown.code(option)
                selected = option == setup.selected(group)
                rendered.append(f"**{code}** {self.BASELINE_FOOTNOTE_MARK}" if selected else code)
            options.append(", ".join(rendered))
            counts.append(", ".join(str(len(setup.option_members(group, option))) for option in setup.options(group)))
        return [
            Markdown.code(setup.stem),
            Markdown.cell(", ".join(groups)),
            Markdown.cell(self.GROUP_SEPARATOR.join(options)),
            Markdown.cell(self.GROUP_SEPARATOR.join(counts)),
            str(len(setup.overrides)),
        ]

    def _census(self) -> str:
        """The one sentence pair about how much of the fleet this page can speak for.

        Returns:
            How many recorded setups are grouped, and what the rest are.
        """
        grouped = len(self.setups)
        rest = max(self.recorded - grouped, 0)
        opening = (
            f"One of the {self.recorded} recorded setups is grouped so far."
            if grouped == 1
            else f"{grouped} of the {self.recorded} recorded setups are grouped so far."
        )
        if not rest:
            return f"{opening} Every recorded setup this repository holds has a grouping table."
        tail = (
            "The other one has a flat twin only: a single recorded energy-system file, with no probe "
            "list, no grouping table and no `variants:` section, so nothing on this page can be said "
            "about it yet."
            if rest == 1
            else f"The other {rest} have flat twins only: a single recorded energy-system file per "
            "setup, with no probe list, no grouping table and no `variants:` section, so nothing on "
            "this page can be said about them yet."
        )
        return f"{opening} {tail}"

    def _setup(self, setup: GroupedSetup) -> List[str]:
        """Renders one setup's whole section.

        Args:
            setup: The setup to render.

        Returns:
            The section's lines, opening with its heading.
        """
        lines = [f"## {Markdown.code(setup.stem)}", "", self._introduction(setup), "", "### Shape", ""]
        lines.extend(MermaidShape.render(setup))
        lines.extend(["", self._caption(setup), "", "### Assignments", ""])
        lines.extend(self._assignments(setup))
        lines.extend(["", self._ordering_rule(setup), "", "### Probe configurations", ""])
        lines.extend(self._probes(setup))
        lines.extend(["", self.PROBE_NOTE])
        return lines

    @classmethod
    def _introduction(cls, setup: GroupedSetup) -> str:
        """The paragraph that says where the setup came from and what its shape came to.

        Args:
            setup: The setup being introduced.

        Returns:
            The paragraph.
        """
        described = Prose.sentence(setup.system.description or "")
        opening = f"Recorded from {Markdown.code(setup.grouping.setup)}"
        opening += f', described in the grouped file as "{described}"' if described else ", with no description"
        shape = f"{len(setup.shared)} components are shared by every configuration"
        if not setup.group_names:
            shape += ", and the grouped file declares no variant group."
        elif len(setup.group_names) == 1:
            group = setup.group_names[0]
            shape += f", and one variant group, {Markdown.code(group)}, {cls._chooses(setup, group)}."
        else:
            clauses = [f"{Markdown.code(group)} {cls._chooses(setup, group)}" for group in setup.group_names]
            shape += (
                f", and {Prose.word(len(setup.group_names))} variant groups decide the rest of its "
                f"shape: {'; '.join(clauses)}."
            )
        paragraph = f"{opening}. {shape}" if not described else f"{opening} {shape}"
        if setup.overrides:
            knobs = (
                "One of the shared components is an override, which means the grouped file states the "
                "baseline's value for it"
                if len(setup.overrides) == 1
                else f"{Prose.capitalised(len(setup.overrides))} of the shared components are overrides, "
                "which means the grouped file states the baseline's value for them"
            )
            paragraph += f" {knobs} and a consumer sets that value to something else."
        return paragraph

    @classmethod
    def _chooses(cls, setup: GroupedSetup, group: str) -> str:
        """How one variant group's choice is put in running text.

        Args:
            setup: The setup the group belongs to.
            group: The group's name.

        Returns:
            The clause following the group's name.
        """
        options = [Markdown.code(option) for option in setup.options(group)]
        if len(options) == 1:
            return f"offers {options[0]} alone"
        return f"chooses between {Prose.listed(options)}"

    @classmethod
    def _caption(cls, setup: GroupedSetup) -> str:
        """The paragraph under the diagram, saying how the boxes map onto the grouped file.

        Args:
            setup: The setup the diagram is of.

        Returns:
            The paragraph.
        """
        sentences = []
        shared_by_group = [
            (group, members) for group in setup.group_names for members in (setup.group_scoped(group),) if members
        ]
        if shared_by_group:
            names = [Markdown.code(name) for group, members in shared_by_group for name in members]
            single = len(names) == 1
            where = (
                "both options"
                if len(shared_by_group) == 1 and len(setup.options(shared_by_group[0][0])) == 2
                else "every option of its group"
            )
            sentences.append(
                f"{Prose.listed(names)} {'appears' if single else 'appear'} under {where} because "
                f"{'it is' if single else 'they are'} assigned to the group rather than to one option: "
                f"{'it is' if single else 'they are'} present in every configuration, and each option "
                f"writes {'it' if single else 'them'} out in full with its own wiring."
            )
        sentences.append(cls._boxes_sentence(setup))
        return " ".join(sentences)

    @classmethod
    def _boxes_sentence(cls, setup: GroupedSetup) -> str:
        """The sentence naming which boxes are the shared section and which are the options.

        Args:
            setup: The setup the diagram is of.

        Returns:
            The sentence.
        """
        shared = Prose.word(len(setup.shared))
        if setup.fixed and setup.overrides:
            opening = (
                f"The first two boxes together are the grouped file's shared `components:` section — "
                f"{shared} components, of which the {Prose.word(len(setup.overrides))} knobs are the "
                "ones whose committed value a consumer replaces —"
            )
        elif setup.overrides:
            opening = (
                f"The first box is the grouped file's shared `components:` section — {shared} components, "
                "every one of them a knob whose committed value a consumer replaces —"
            )
        else:
            opening = (
                f"The first box is the grouped file's shared `components:` section — {shared} components, "
                "none of them a knob —"
            )
        if not setup.group_names:
            return f"{opening} and the setup has no variant group, so that is every component it has."
        return (
            f"{opening} and each option box is that option's own `components:` block, so every component "
            "of the setup is on this picture exactly where the grouped file puts it."
        )

    @classmethod
    def _assignments(cls, setup: GroupedSetup) -> List[str]:
        """Renders the assignments table.

        Args:
            setup: The setup whose judgements are tabulated.

        Returns:
            The table's lines.
        """
        rows = [
            [
                Markdown.code(assignment.component),
                Markdown.code(assignment.text) if assignment.text else Markdown.EMPTY,
                Markdown.cell(Prose.unfold(assignment.note)),
            ]
            for assignment in setup.assignment_rows()
        ]
        return Markdown.table(cls.ASSIGNMENT_HEADINGS, rows)

    @classmethod
    def _ordering_rule(cls, setup: GroupedSetup) -> str:
        """The paragraph stating the order the assignments table is in.

        Args:
            setup: The setup the table belongs to.

        Returns:
            The paragraph, carrying the on/off-group clause only when the setup has such a group.
        """
        parts = [cls.ORDERING_RULE]
        if setup.system.groups:
            parts.append(cls.ORDERING_RULE_GROUPS)
        parts.append(cls.ORDERING_RULE_NOTES)
        return " ".join(parts)

    @classmethod
    def _probes(cls, setup: GroupedSetup) -> List[str]:
        """Renders the probe-configurations table, one column per variant group.

        Args:
            setup: The setup whose probe list is tabulated.

        Returns:
            The table's lines.
        """
        headings = [
            *cls.PROBE_HEADINGS,
            *(Markdown.code(group) for group in setup.group_names),
            cls.PROBE_GIST_HEADING,
        ]
        rows = []
        for probe in setup.probes.probes:
            selection = setup.grouping.selection(probe.column)
            positions = [
                Markdown.code(selection.variants[group]) if group in selection.variants else Markdown.EMPTY
                for group in setup.group_names
            ]
            rows.append(
                [
                    Markdown.code(probe.column),
                    cls._overlay(probe.module_config),
                    *positions,
                    Markdown.cell(Prose.first_sentence(probe.description or "")),
                ]
            )
        return Markdown.table(headings, rows)

    @classmethod
    def _overlay(cls, module_config: Any) -> str:
        """Renders one probe's module-configuration overlay for a table cell.

        A string value is written as the author wrote it and every other value the way JSON writes
        it, because the two questions a reader has of this cell are "which field" and "which of the
        values it can take", and quotes around a display name answer neither.

        Args:
            module_config: The probe's overlay, as the probe list holds it.

        Returns:
            One ``field = value`` per line, or the words that mean an empty overlay.
        """
        fields = dict(module_config)
        if not fields:
            return cls.BASELINE_OVERLAY
        rendered = [
            f"{name} = {value if isinstance(value, str) else json.dumps(value)}" for name, value in fields.items()
        ]
        return Markdown.code(Markdown.LINE_BREAK.join(rendered))


class OverviewSweep:
    """Finding every committed grouping decision and rendering the page from all of them.

    The sweep is what makes the page a fleet document rather than a per-setup one: a second setup
    gains a grouping table and the page grows a section without anybody editing it. It sorts the
    decisions by file name so that the order of the sections is a property of the repository rather
    than of the filesystem that listed it.
    """

    #: Where the committed decisions and the recorded twins both live.
    DIRECTORY: ClassVar[str] = RecordingSession.DEFAULT_OUTPUT_DIRECTORY

    @classmethod
    def root(cls) -> Path:
        """The checkout this installation of HiSim belongs to.

        Returns:
            The repository root, found by walking up from this module the way the recorder finds it
            from the setup it is recording.
        """
        return RepositoryLayout.root(Path(__file__))

    @classmethod
    def decisions(cls, directory: Path) -> Tuple[Path, ...]:
        """The committed grouping decisions of one directory, in sorted file-name order.

        Args:
            directory: The directory to sweep, normally ``energy_systems/``.

        Returns:
            One path per decision.
        """
        return tuple(sorted(directory.glob(f"*{Grouping.SUFFIX}"))) if directory.is_dir() else ()

    @classmethod
    def page(cls, directory: Path, root: Optional[Path] = None) -> OverviewPage:
        """Reads every grouped setup of one directory and prepares the page for them.

        Args:
            directory: The directory holding the decisions and the recorded twins.
            root: The repository root the decisions' own paths are relative to; the checkout this
                module lies in when omitted.

        Returns:
            The page, ready to render.
        """
        anchor = root if root is not None else cls.root()
        setups = [GroupedSetup.read(path, anchor) for path in cls.decisions(directory)]
        return OverviewPage(setups, len(FleetCensus.flat_twins(directory)))


def render_overview(directory: Path, root: Optional[Path] = None) -> str:
    """Renders the grouping overview page for one directory of committed files.

    Args:
        directory: The directory holding the ``*.grouping.yaml`` decisions, their grouped energy
            systems and the recorded flat twins that are the fleet denominator.
        root: The repository root the decisions' probe-list paths are relative to; the checkout
            this module lies in when omitted.

    Returns:
        The page's text, ending in one newline.

    Raises:
        EnergySystemRecordingError: When a decision or a probe list is malformed.
        EnergySystemFormatError: When a grouped energy-system file is.
    """
    return OverviewSweep.page(directory, root).render()


def write_overview(directory: Path, path: Path, root: Optional[Path] = None) -> Tuple[Path, int, int]:
    """Renders the overview page and writes it where it is committed.

    Args:
        directory: The directory holding the committed grouping files.
        path: Where the page goes; its parent is created when it does not exist.
        root: The repository root, as for :func:`render_overview`.

    Returns:
        The path written, how many grouped setups it describes and how many flat twins were counted.

    Raises:
        EnergySystemRecordingError: When a decision or a probe list is malformed.
        EnergySystemFormatError: When a grouped energy-system file is.
    """
    page = OverviewSweep.page(directory, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page.render(), encoding="utf-8", newline="\n")
    return path, len(page.setups), page.recorded
