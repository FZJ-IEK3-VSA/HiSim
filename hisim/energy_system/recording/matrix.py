"""The probe matrix: what one setup's configurations really differ in, before anybody judges it.

Recording a setup under several module configurations produces several flat files, and the whole
question of the grouping pass is what the differences between them *mean*. This module answers the
half of that question observation can answer, and stops exactly there. For every component in the
union of the probe runs it says, per column, one of three things: the component is not there, it
is there and says the same as in the baseline, or it is there and says something else.

The third state is the one the pass exists for. A presence matrix — the obvious first design —
cannot describe an electricity meter that is present in every configuration and wired through an
energy manager in one of them and straight to the participants in another, and that rewiring is
precisely the case a group cannot express and a variant can. Reducing the comparison to "is it
there" would therefore hide the only difference that forces the more expressive construct.

One refinement is applied to the comparison and it is worth stating plainly, because it decides
which rows a person is asked about. When a probe does not have a component at all, every reference
to that component elsewhere is dropped from that probe's recording — a building that lists the
energy manager among its sources simply does not list it in a configuration that has none. Turning
the format's own switches off does exactly the same thing to those references, so a difference that
consists only of such dropped references is not a difference between the two configurations: it is
the same entry seen in two worlds. The comparison therefore removes references to components the
column does not have from the baseline side before comparing, and a row whose only difference was
that reads ``=``. Nothing is inferred by this — the byte-for-byte check of the finished grouped
file is what would catch it if the normalisation were ever too generous.
"""

# clean

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple

from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.energy_system.model import ComponentEntry, EnergySystemFile
from hisim.energy_system.recording.probes import ProbeList


class CellState(enum.Enum):
    """The three things one probe column can say about one component.

    The values are the glyphs the workbook shows, because the sheet is read by a person and three
    single characters make a wide table scannable in a way that three words do not. They are an
    enumeration rather than bare strings so that no part of the pass can invent a fourth state and
    so that the importer can reject a cell somebody typed over.
    """

    ABSENT = "—"
    IDENTICAL = "="
    DIFFERENT = "≠"

    @classmethod
    def of(cls, glyph: str) -> Optional["CellState"]:
        """Reads one written cell back into a state.

        Args:
            glyph: The cell's text.

        Returns:
            The state, or ``None`` when the text is not one of the three glyphs.
        """
        for state in cls:
            if state.value == glyph:
                return state
        return None


class DocumentDifference:
    """The dotted-path difference between two entry documents, and how to apply it again.

    An ``override`` is a difference a person has declared to be a value a consumer sets rather
    than a question of membership, and the grouped file states the baseline's value for it. That
    only stays honest if the difference itself is written down somewhere and can be re-applied, so
    that the claim "this file plus these knobs reproduces that configuration" is checkable rather
    than asserted. This class is both halves of that: the diff and its inverse.

    The walk descends into mappings and treats everything else — scalars, and lists such as an
    ``inputs`` block — as one value. A list whose second item changed is reported as a new list
    rather than as an index, because an index into a list that other columns write differently is
    not a stable name for anything.
    """

    #: Marks a path the other document does not have at all, so that applying a difference can
    #: remove a key as well as change one.
    ABSENT: ClassVar[str] = "\x00absent"

    #: Separator of a dotted path into an entry document.
    SEPARATOR: ClassVar[str] = "."

    @classmethod
    def between(cls, before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
        """Computes the paths at which two entry documents disagree, with the second one's values.

        Args:
            before: The baseline entry document.
            after: The column's entry document.

        Returns:
            One entry per differing path, holding the value ``after`` has there or
            :attr:`ABSENT` when it has none.
        """
        differences: Dict[str, Any] = {}
        cls._walk(before, after, "", differences)
        return differences

    @classmethod
    def _walk(cls, before: Mapping[str, Any], after: Mapping[str, Any], prefix: str, into: Dict[str, Any]) -> None:
        """Descends both documents in parallel, collecting the paths that differ.

        Args:
            before: The baseline side of this level.
            after: The column side of this level.
            prefix: The dotted path of this level, empty at the top.
            into: The mapping the differences are collected in.
        """
        for key in list(before) + [key for key in after if key not in before]:
            path = f"{prefix}{cls.SEPARATOR}{key}" if prefix else str(key)
            left, right = before.get(key, cls.ABSENT), after.get(key, cls.ABSENT)
            if isinstance(left, Mapping) and isinstance(right, Mapping):
                cls._walk(left, right, path, into)
            elif left != right:
                into[path] = right

    @classmethod
    def apply(cls, document: Mapping[str, Any], differences: Mapping[str, Any]) -> Dict[str, Any]:
        """Re-applies a difference to an entry document, returning a new one.

        Args:
            document: The entry document to change.
            differences: The paths and values a previous :meth:`between` produced.

        Returns:
            A fresh document carrying the differences.
        """
        result = cls.copy(document)
        for path, value in differences.items():
            keys = path.split(cls.SEPARATOR)
            block = result
            for key in keys[:-1]:
                block = block.setdefault(key, {})
            if value == cls.ABSENT:
                block.pop(keys[-1], None)
            else:
                block[keys[-1]] = value
        return result

    @classmethod
    def copy(cls, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Copies an entry document deeply enough that applying a difference cannot reach the original.

        Args:
            document: The document to copy.

        Returns:
            A fresh mapping whose nested mappings are fresh too.
        """
        return {
            key: cls.copy(value) if isinstance(value, Mapping) else value for key, value in document.items()
        }


class EntryComparison:
    """Compares one component's entry between the baseline and one probe column.

    The comparison works on the canonical document each entry emits rather than on the model
    objects, because that is the form the files are written in and therefore the form in which
    "the same" means the same thing to a reader of a diff. It is also the form the difference of an
    ``override`` is expressed in, so the whole pass has one notion of what an entry is.

    The normalisation of references into components a column does not have lives here, in one
    place, so that the matrix, the override differences and the report all see the same entry.
    """

    #: The entry key holding the wiring, which is the one place a reference to another component
    #: is written; the format writes nothing at the source of a connection.
    INPUTS_KEY: ClassVar[str] = "inputs"

    #: The entry key holding the sizing references, absent from every recorded file but read here
    #: so that the comparison does not silently depend on a recording never carrying one.
    SIZING_KEY: ClassVar[str] = "sizing_sources"

    #: The key naming the other end of one input item, in every one of the three item shapes.
    SOURCE_KEY: ClassVar[str] = "from"

    @classmethod
    def document(cls, entry: ComponentEntry) -> Dict[str, Any]:
        """Renders one entry as the canonical mapping the file writes for it.

        Args:
            entry: The entry to render.

        Returns:
            The mapping, with the entry's keys in the format's own order.
        """
        return EnergySystemEmitter.entry(entry)

    @classmethod
    def restricted(cls, document: Mapping[str, Any], present: Mapping[str, Any]) -> Dict[str, Any]:
        """Drops every reference the entry makes to a component the other world does not have.

        Args:
            document: The entry document, as :meth:`document` produced it.
            present: The components that exist in the world being compared against; only the keys
                are read.

        Returns:
            A fresh document whose ``inputs`` and ``sizing_sources`` mention only those components.
        """
        result = DocumentDifference.copy(document)
        items = result.get(cls.INPUTS_KEY)
        if isinstance(items, list):
            kept = [item for item in items if cls._source_of(item) in present]
            if kept:
                result[cls.INPUTS_KEY] = kept
            else:
                result.pop(cls.INPUTS_KEY, None)
        sizing = result.get(cls.SIZING_KEY)
        if isinstance(sizing, Mapping):
            result[cls.SIZING_KEY] = {
                fact: cls._kept_sources(value, present) for fact, value in sizing.items()
            }
        return result

    @classmethod
    def _kept_sources(cls, value: Any, present: Mapping[str, Any]) -> Any:
        """Keeps only the sizing references whose provider exists in the other world.

        Args:
            value: One ``sizing_sources`` value, a dotted reference or a list of them.
            present: The components that exist there.

        Returns:
            The value with the vanished providers removed.
        """
        if isinstance(value, list):
            return [item for item in value if str(item).split(".", 1)[0] in present]
        return value

    @classmethod
    def _source_of(cls, item: Any) -> str:
        """The component one input item draws from, whichever of the three shapes it has.

        Args:
            item: The written item: a bare source name, or a mapping carrying ``from``.

        Returns:
            The source component's name, or the empty string for an item with no source at all.
        """
        if isinstance(item, str):
            return item
        if isinstance(item, Mapping):
            return str(item.get(cls.SOURCE_KEY, "")).split(".", 1)[0]
        return ""


@dataclass(frozen=True)
class ProbeRecording:
    """One probe's flat recording: the column it fills, the file it wrote and what that file says.

    Both halves are kept because both are used and neither can be recomputed cheaply from the
    other. The text is what the byte-for-byte proof compares against, header comments included; the
    model is what the matrix diffs and what the grouped file's entries are taken from.
    """

    column: str
    path: Path
    text: str
    model: EnergySystemFile

    def entries(self) -> Dict[str, ComponentEntry]:
        """The components this recording holds, by name, in file order.

        Returns:
            A fresh mapping; a flat recording has neither groups nor variants, so this is all of
            them however the file is read.
        """
        return dict(self.model.all_components())


@dataclass(frozen=True)
class ComponentRow:
    """One row of the ``components`` sheet: one component, seen from every probe column.

    A row is the unit a person makes a decision about, which is why it carries the states of all
    columns at once rather than the cells carrying a back-reference to it. The two questions the
    decision turns on — is this component missing anywhere, does it say something different
    anywhere — are answered by the row itself so that neither the workbook writer nor the importer
    has to re-derive them from the glyphs.
    """

    component: str
    class_path: str
    states: Mapping[str, CellState]

    @property
    def absent_in(self) -> Tuple[str, ...]:
        """The columns that do not have this component at all.

        Returns:
            The column names, in matrix order.
        """
        return tuple(column for column, state in self.states.items() if state is CellState.ABSENT)

    @property
    def differs_in(self) -> Tuple[str, ...]:
        """The columns that have this component but say something else about it.

        Returns:
            The column names, in matrix order.
        """
        return tuple(column for column, state in self.states.items() if state is CellState.DIFFERENT)

    @property
    def present_in(self) -> Tuple[str, ...]:
        """The columns that have this component at all, identically or not.

        Returns:
            The column names, in matrix order.
        """
        return tuple(column for column, state in self.states.items() if state is not CellState.ABSENT)

    @property
    def is_uniform(self) -> bool:
        """Whether every column says exactly the same thing about this component.

        A uniform row needs no decision: the component is an ordinary part of the system in every
        configuration probed, and the person is asked about the others.

        Returns:
            ``True`` when every cell is ``=``.
        """
        return all(state is CellState.IDENTICAL for state in self.states.values())


@dataclass(frozen=True)
class ProbeMatrix:
    """The whole three-state table, plus the recordings it was computed from.

    The matrix is the observation half of the grouping pass and is complete on its own: given the
    probe recordings it is a pure function of them, so a test builds one from two hand-written
    files and a fleet run builds one from four subprocesses, and both exercise the same code.

    Rows are in the order the components were registered — the baseline's order first, then any
    component a later column introduced, in that column's own order — because that is the order
    both the recorded files and the workbook read in, and a table sorted by name would stop
    matching the file it describes.
    """

    columns: Tuple[str, ...]
    rows: Tuple[ComponentRow, ...]
    recordings: Mapping[str, ProbeRecording]

    @property
    def baseline(self) -> str:
        """The column every other column is compared against.

        Returns:
            The first column's name.
        """
        return self.columns[0]

    def row(self, component: str) -> Optional[ComponentRow]:
        """Finds one row by component name.

        Args:
            component: The component to look for.

        Returns:
            The row, or ``None`` when no probe has that component.
        """
        for row in self.rows:
            if row.component == component:
                return row
        return None

    def decided_rows(self) -> Tuple[ComponentRow, ...]:
        """The rows a person has to say something about: every row that is not ``=`` everywhere.

        Returns:
            The non-uniform rows, in matrix order.
        """
        return tuple(row for row in self.rows if not row.is_uniform)

    def differences(self, component: str, column: str) -> Dict[str, Any]:
        """The dotted differences of one component's entry between the baseline and one column.

        This is what an ``override`` row's knob values are read from, and what the realization of
        that column re-applies to the grouped file's entry.

        Args:
            component: The component to diff.
            column: The column to diff against the baseline.

        Returns:
            The differing paths with the column's values; empty when the two agree.
        """
        baseline_entry = self.recordings[self.baseline].entries().get(component)
        column_recording = self.recordings[column]
        column_entry = column_recording.entries().get(component)
        if baseline_entry is None or column_entry is None:
            return {}
        present = column_recording.entries()
        before = EntryComparison.restricted(EntryComparison.document(baseline_entry), present)
        after = EntryComparison.restricted(EntryComparison.document(column_entry), present)
        return DocumentDifference.between(before, after)

    @classmethod
    def of(cls, probe_list: ProbeList, recordings: Mapping[str, ProbeRecording]) -> "ProbeMatrix":
        """Builds the matrix from one recording per probe column.

        Args:
            probe_list: The list the columns come from; its order is the table's column order.
            recordings: One recording per column, keyed by column name.

        Returns:
            The matrix.
        """
        columns = tuple(column for column in probe_list.columns if column in recordings)
        baseline = recordings[columns[0]].entries()
        names: Dict[str, str] = {name: entry.class_path for name, entry in baseline.items()}
        for column in columns[1:]:
            for name, entry in recordings[column].entries().items():
                names.setdefault(name, entry.class_path)
        rows = tuple(
            ComponentRow(
                component=name,
                class_path=class_path,
                states={column: cls._state(name, baseline, recordings[column]) for column in columns},
            )
            for name, class_path in names.items()
        )
        return cls(columns=columns, rows=rows, recordings=dict(recordings))

    @classmethod
    def _state(
        cls, component: str, baseline: Mapping[str, ComponentEntry], recording: ProbeRecording
    ) -> CellState:
        """Decides what one column says about one component.

        Args:
            component: The component in question.
            baseline: The baseline column's entries.
            recording: The column's recording.

        Returns:
            ``ABSENT`` when the column has no such component, ``IDENTICAL`` when its entry agrees
            with the baseline's once references into components the column does not have are
            removed, and ``DIFFERENT`` otherwise — including when the baseline is the side that
            does not have the component, since there is then nothing for it to agree with.
        """
        present = recording.entries()
        entry = present.get(component)
        if entry is None:
            return CellState.ABSENT
        reference = baseline.get(component)
        if reference is None:
            return CellState.DIFFERENT
        before = EntryComparison.restricted(EntryComparison.document(reference), present)
        after = EntryComparison.restricted(EntryComparison.document(entry), present)
        return CellState.IDENTICAL if before == after else CellState.DIFFERENT
