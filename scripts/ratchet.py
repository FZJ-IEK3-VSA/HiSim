"""The generic ratchet: a committed baseline of today's violations that may only shrink.

A ratchet check measures violations of some rule (an import across a forbidden edge, a function over the line
limit, a ``# type: ignore``), each under a key, and compares the measured count per key with a committed baseline.
A key the baseline does not hold, or a count above the baseline's, fails: no new violation gets in. A baseline
entry whose count went down, or whose violation is gone, fails too, until the entry is lowered or deleted: a fix
cannot silently make room for a later regression. Every entry names the bead that will empty it.

This module knows nothing about what is measured. ``scripts/module_graph.py`` uses it for the import graph; the
code-size and escape-hatch ratchets can use it the same way, each with its own :class:`BaselineLayout`.

The baseline file is YAML with one entry per line, sorted, so that merges of two branches that each fixed a
violation stay trivial::

    imports:
    - {importer: "hisim.a", imported: "hisim.b", name: "X", count: 2, bead: "hisim-some-fix-1a2b"}
"""
from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, List, Mapping, Tuple

import yaml


@dataclasses.dataclass(frozen=True, order=True)
class EntryKey:
    """The identity of one baseline entry: its section and the values of its key fields.

    For example ``EntryKey("imports", ("hisim.components.generic_boiler", "hisim.postprocessing.x", "Helper"))``.
    The order of ``fields`` is the order of the layout's key fields, so two keys compare and sort field by field.
    """

    section: str
    fields: Tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class BaselineEntry:
    """One tolerated violation: its key, how many times it may occur, and the bead that will remove it."""

    key: EntryKey
    count: int
    bead: str


@dataclasses.dataclass(frozen=True)
class BaselineLayout:
    """The shape of one baseline file: its sections, the key fields of an entry, and its header comment.

    A section is a top-level list of the file (``imports``, ``type_checking_imports``); every entry of every section
    carries the same key fields plus ``count`` and ``bead``. The header is written as ``#`` comment lines above the
    entries, so the file explains itself to a reader who opens it.
    """

    sections: Tuple[str, ...]
    key_fields: Tuple[str, ...]
    header: Tuple[str, ...]

    #: The two value fields every entry carries beside its key fields.
    VALUE_FIELDS = ("count", "bead")


class BaselineError(ValueError):
    """A baseline file that cannot be read as a baseline of its layout: a wrong field, a duplicate, a bad count."""


class FindingKind(str, enum.Enum):
    """What a comparison of the measurement with the baseline found for one key."""

    #: A violation the baseline does not hold: fails.
    NEW = "new"
    #: A violation the baseline holds, but with more occurrences than its count: fails like a new one.
    GREW = "grew"
    #: A baseline entry whose violation is gone: fails until the entry is deleted.
    GONE = "gone"
    #: A baseline entry whose violation occurs fewer times than its count: fails until the count is lowered.
    LOWER = "lower"

    @property
    def is_regression(self) -> bool:
        """Whether the finding is a new or grown violation, as opposed to a stale baseline entry."""
        return self in (FindingKind.NEW, FindingKind.GREW)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One difference between the measurement and the baseline, for one key.

    ``baseline_count`` is 0 for a new violation and ``measured_count`` is 0 for a gone one; ``bead`` is the
    baseline entry's bead, or the empty string for a new violation.
    """

    kind: FindingKind
    key: EntryKey
    measured_count: int
    baseline_count: int
    bead: str


@dataclasses.dataclass(frozen=True)
class SummaryRow:
    """One line of a report: a label (a bead, an edge) with its number of entries and of occurrences."""

    label: str
    entries: int
    occurrences: int


class Ratchet:
    """The comparison, pruning and reporting of a ratchet, as pure functions over keys and counts."""

    @staticmethod
    def compare(measured: Mapping[EntryKey, int], baseline: Mapping[EntryKey, BaselineEntry]) -> List[Finding]:
        """Return every difference between the measured counts and the baseline, sorted by key.

        For example, with the baseline ``{k: 2}`` a measurement ``{k: 3}`` gives one ``GREW`` finding, ``{k: 1}``
        one ``LOWER`` finding, ``{}`` one ``GONE`` finding, and ``{k: 2}`` none. A measured count of zero is the same
        as an absent key.

        Args:
            measured: The number of occurrences of each violation found in the code.
            baseline: The tolerated violations, by key.

        Returns:
            The findings, sorted by key; an empty list when measurement and baseline agree.
        """
        findings: List[Finding] = []
        for key in sorted(set(measured) | set(baseline)):
            measured_count = measured.get(key, 0)
            entry = baseline.get(key)
            if entry is None:
                if measured_count > 0:
                    findings.append(Finding(FindingKind.NEW, key, measured_count, 0, ""))
                continue
            if measured_count > entry.count:
                findings.append(Finding(FindingKind.GREW, key, measured_count, entry.count, entry.bead))
            elif measured_count == 0:
                findings.append(Finding(FindingKind.GONE, key, 0, entry.count, entry.bead))
            elif measured_count < entry.count:
                findings.append(Finding(FindingKind.LOWER, key, measured_count, entry.count, entry.bead))
        return findings

    @staticmethod
    def pruned(baseline: Mapping[EntryKey, BaselineEntry], measured: Mapping[EntryKey, int]) -> Dict[EntryKey, BaselineEntry]:
        """Return the baseline with every stale entry deleted or lowered, and nothing added or raised.

        An entry whose violation is gone is dropped; an entry measured fewer times than its count gets the measured
        count; every other entry stays as it is, also one measured more often than its count, since raising it is a
        decision for a review, not for a tool. A new violation is never added.

        Args:
            baseline: The tolerated violations, by key.
            measured: The number of occurrences of each violation found in the code.

        Returns:
            The pruned baseline; its keys are a subset of the input's and no count is higher than before.
        """
        result: Dict[EntryKey, BaselineEntry] = {}
        for key, entry in baseline.items():
            measured_count = measured.get(key, 0)
            if measured_count <= 0:
                continue
            result[key] = dataclasses.replace(entry, count=min(entry.count, measured_count))
        return result

    @staticmethod
    def unknown_beads(baseline: Mapping[EntryKey, BaselineEntry], known_bead_ids: FrozenSet[str]) -> List[str]:
        """Return the bead ids the baseline names that are not in the beads file, sorted and without repeats.

        Args:
            baseline: The tolerated violations, by key.
            known_bead_ids: Every bead id of ``.beads/issues.jsonl``.

        Returns:
            The unknown ids; an empty list when every entry points at an existing bead.
        """
        return sorted({entry.bead for entry in baseline.values()} - known_bead_ids)

    @staticmethod
    def summary(entries: Iterable[BaselineEntry], label_of: Callable[[BaselineEntry], str]) -> List[SummaryRow]:
        """Return the entries and occurrences per label, the largest number of occurrences first.

        For example, with ``label_of=lambda entry: entry.bead`` the rows say how much each bead has left to remove.
        Rows with the same number of occurrences are sorted by label, so the report is deterministic.

        Args:
            entries: The baseline entries (or measured violations wrapped as entries) to summarise.
            label_of: The label each entry is counted under.

        Returns:
            One row per label.
        """
        entry_counts: Dict[str, int] = {}
        occurrence_counts: Dict[str, int] = {}
        for entry in entries:
            label = label_of(entry)
            entry_counts[label] = entry_counts.get(label, 0) + 1
            occurrence_counts[label] = occurrence_counts.get(label, 0) + entry.count
        rows = [SummaryRow(label, entries_of_label, occurrence_counts[label]) for label, entries_of_label in entry_counts.items()]
        return sorted(rows, key=lambda row: (-row.occurrences, row.label))

    @staticmethod
    def markdown_table(title: str, label_heading: str, rows: List[SummaryRow]) -> str:
        """Return the rows as a Markdown table under a heading, with a total line, for a CI job summary.

        Args:
            title: The heading above the table.
            label_heading: The heading of the label column (``bead``, ``edge``).
            rows: The rows, in the order they are shown.

        Returns:
            The Markdown text, ending with a newline.
        """
        lines = [f"### {title}", "", f"| {label_heading} | entries | occurrences |", "|---|---:|---:|"]
        lines += [f"| {row.label} | {row.entries} | {row.occurrences} |" for row in rows]
        lines.append(f"| **total** | **{sum(row.entries for row in rows)}** | **{sum(row.occurrences for row in rows)}** |")
        return "\n".join(lines) + "\n"


class BaselineFile:
    """Reading and writing a baseline file in its one canonical form."""

    @staticmethod
    def parse(text: str, layout: BaselineLayout) -> Dict[EntryKey, BaselineEntry]:
        """Return the entries of a baseline file's text.

        Args:
            text: The YAML text of the file.
            layout: The sections and key fields the file must have.

        Returns:
            The entries, by key.

        Raises:
            BaselineError: If the text is not a mapping of the layout's sections to lists of entries, an entry lacks
                a field or has one too many, a count is not a positive integer, a bead is empty, or a key occurs
                twice. The message names the entry and the remedy.
        """
        document = yaml.safe_load(text) if text.strip() else {}
        if not isinstance(document, dict):
            raise BaselineError(f"the baseline must be a mapping of the sections {list(layout.sections)} to lists of entries.")
        unknown_sections = sorted(set(document) - set(layout.sections))
        if unknown_sections:
            raise BaselineError(f"the baseline has the unknown sections {unknown_sections}; the sections are {list(layout.sections)}.")
        expected_fields = set(layout.key_fields) | set(BaselineLayout.VALUE_FIELDS)
        entries: Dict[EntryKey, BaselineEntry] = {}
        for section in layout.sections:
            items = document.get(section)
            if items is None:
                items = []
            if not isinstance(items, list):
                raise BaselineError(f"the section '{section}' must be a list of entries, not {type(items).__name__}.")
            for item in items:
                entry = BaselineFile._entry(section, item, layout, expected_fields)
                if entry.key in entries:
                    raise BaselineError(f"the entry {item} occurs twice in '{section}'; merge the two into one with the sum of their counts.")
                entries[entry.key] = entry
        return entries

    @staticmethod
    def _entry(section: str, item: object, layout: BaselineLayout, expected_fields: set) -> BaselineEntry:
        """Return one parsed entry of a section, or raise :class:`BaselineError` naming what is wrong with it."""
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise BaselineError(f"the entry {item} in '{section}' must have exactly the fields {sorted(expected_fields)}.")
        count, bead = item["count"], item["bead"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise BaselineError(f"the entry {item} in '{section}' has the count {count!r}; a count is a positive integer.")
        if not isinstance(bead, str) or not bead.strip():
            raise BaselineError(f"the entry {item} in '{section}' names no bead; every entry names the bead that will remove it.")
        key = EntryKey(section, tuple(str(item[name]) for name in layout.key_fields))
        return BaselineEntry(key, count, bead)

    @staticmethod
    def render(entries: Mapping[EntryKey, BaselineEntry], layout: BaselineLayout) -> str:
        """Return the canonical text of a baseline: the header, then each section's entries one per line, sorted.

        Every value is written as a double-quoted string (a JSON string is a valid YAML string), so that a value such
        as ``*`` is never read as YAML syntax; counts are written as integers.

        Args:
            entries: The entries to write; each key's section must be one of the layout's.
            layout: The sections, key fields and header of the file.

        Returns:
            The text, ending with a newline.

        Raises:
            BaselineError: If an entry's section is not in the layout.
        """
        stray = sorted({key.section for key in entries} - set(layout.sections))
        if stray:
            raise BaselineError(f"entries in the unknown sections {stray}; the sections are {list(layout.sections)}.")
        lines = [f"# {line}".rstrip() for line in layout.header]
        for section in layout.sections:
            keys = sorted(key for key in entries if key.section == section)
            lines.append(f"{section}:" if keys else f"{section}: []")
            for key in keys:
                entry = entries[key]
                fields = [f"{name}: {json.dumps(value)}" for name, value in zip(layout.key_fields, key.fields)]
                fields += [f"count: {entry.count}", f"bead: {json.dumps(entry.bead)}"]
                lines.append("- {" + ", ".join(fields) + "}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def read(path: Path, layout: BaselineLayout) -> Dict[EntryKey, BaselineEntry]:
        """Return the entries of the baseline file at ``path``; see :meth:`parse` for what it refuses."""
        return BaselineFile.parse(path.read_text(encoding="utf-8"), layout)

    @staticmethod
    def write(path: Path, entries: Mapping[EntryKey, BaselineEntry], layout: BaselineLayout) -> None:
        """Write the entries to ``path`` in the canonical form of :meth:`render`."""
        path.write_text(BaselineFile.render(entries, layout), encoding="utf-8")


class BeadIds:
    """The ids of the beads in a beads file, for checking that every baseline entry names an existing bead."""

    @staticmethod
    def read(path: Path) -> FrozenSet[str]:
        """Return every bead id of a ``.beads/issues.jsonl`` file (one JSON object per line).

        Raises:
            ValueError: If a non-empty line is not a JSON object with an ``id``.
        """
        ids = set()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or "id" not in record:
                raise ValueError(f"{path}:{number} is not a bead: a JSON object with an 'id' field.")
            ids.add(str(record["id"]))
        return frozenset(ids)
