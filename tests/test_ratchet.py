"""Tests of the generic ratchet core, ``scripts/ratchet.py``: comparison, pruning, reports and the baseline file.

The core knows nothing about imports; these tests use made-up keys. The import-graph side is tested in
``tests/test_module_graph.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest

from scripts.ratchet import BaselineEntry, BaselineError, BaselineFile, BaselineLayout, BeadIds, EntryKey, Finding, FindingKind, Ratchet

pytestmark = pytest.mark.base


class Keys:
    """Made-up keys and a layout shared by the tests below."""

    LAYOUT = BaselineLayout(sections=("lines", "hatches"), key_fields=("file", "symbol"), header=("A test baseline.",))
    A = EntryKey("lines", ("a.py", "f"))
    B = EntryKey("lines", ("b.py", "g"))
    C = EntryKey("hatches", ("a.py", "*"))

    @staticmethod
    def baseline(**counts: int) -> Dict[EntryKey, BaselineEntry]:
        """Return a baseline of the keys A, B and C with the given counts, each naming the bead ``bead-<key>``."""
        keys = {"a": Keys.A, "b": Keys.B, "c": Keys.C}
        return {keys[name]: BaselineEntry(keys[name], count, f"bead-{name}") for name, count in counts.items()}


class TestCompare:
    """``Ratchet.compare`` finds every difference between measurement and baseline, and nothing else."""

    def test_agreement_gives_no_finding(self) -> None:
        """A comparison that reports a matching baseline as different would fail every clean run."""
        assert not Ratchet.compare({Keys.A: 2, Keys.B: 1}, Keys.baseline(a=2, b=1))

    def test_a_violation_not_in_the_baseline_is_new(self) -> None:
        """Without this, a new violation would slip in unnoticed: the ratchet's main purpose."""
        assert Ratchet.compare({Keys.A: 1}, {}) == [Finding(FindingKind.NEW, Keys.A, 1, 0, "")]

    def test_more_occurrences_than_the_count_is_a_regression(self) -> None:
        """A second occurrence under an existing entry would otherwise hide behind the first one's entry."""
        findings = Ratchet.compare({Keys.A: 3}, Keys.baseline(a=2))
        assert findings == [Finding(FindingKind.GREW, Keys.A, 3, 2, "bead-a")]
        assert findings[0].kind.is_regression

    def test_a_gone_violation_is_a_stale_entry(self) -> None:
        """A fixed violation left in the baseline would make room for the same violation to come back silently."""
        findings = Ratchet.compare({}, Keys.baseline(a=2))
        assert findings == [Finding(FindingKind.GONE, Keys.A, 0, 2, "bead-a")]
        assert not findings[0].kind.is_regression

    def test_a_measured_zero_counts_as_gone(self) -> None:
        """A measurement that lists a key with zero occurrences must not count as a match."""
        assert [finding.kind for finding in Ratchet.compare({Keys.A: 0}, Keys.baseline(a=1))] == [FindingKind.GONE]

    def test_a_lowered_count_is_a_stale_entry(self) -> None:
        """A partly fixed violation must lower its count, or the freed occurrence could be reused."""
        assert Ratchet.compare({Keys.A: 1}, Keys.baseline(a=3)) == [Finding(FindingKind.LOWER, Keys.A, 1, 3, "bead-a")]

    def test_findings_are_sorted_by_key(self) -> None:
        """An unsorted result would make the failure messages change order from run to run."""
        findings = Ratchet.compare({Keys.B: 1, Keys.C: 1, Keys.A: 1}, {})
        assert [finding.key for finding in findings] == sorted([Keys.A, Keys.B, Keys.C])


class TestPruned:
    """``Ratchet.pruned`` only deletes or lowers entries."""

    def test_gone_entries_are_deleted_and_lowered_ones_lowered(self) -> None:
        """Prune must remove what is fixed and lower what is partly fixed, keeping the beads."""
        pruned = Ratchet.pruned(Keys.baseline(a=3, b=1, c=2), {Keys.A: 1, Keys.C: 2})
        assert pruned == {Keys.A: BaselineEntry(Keys.A, 1, "bead-a"), Keys.C: BaselineEntry(Keys.C, 2, "bead-c")}

    def test_prune_never_adds_or_raises(self) -> None:
        """A prune that added new violations or raised a count would let the tool approve what a review must approve."""
        baseline = Keys.baseline(a=1)
        pruned = Ratchet.pruned(baseline, {Keys.A: 5, Keys.B: 2})
        assert pruned == baseline


class TestBeadsAndSummary:
    """Bead checks and the report rows."""

    def test_unknown_beads_are_listed_once_and_sorted(self) -> None:
        """An entry pointing at a bead that does not exist would leave a violation without an owner."""
        baseline = Keys.baseline(a=1, b=1, c=1)
        assert Ratchet.unknown_beads(baseline, frozenset({"bead-b"})) == ["bead-a", "bead-c"]
        assert not Ratchet.unknown_beads(baseline, frozenset({"bead-a", "bead-b", "bead-c"}))

    def test_summary_counts_entries_and_occurrences_per_label(self) -> None:
        """The report per bead must add up entries and occurrences, largest first, ties by label."""
        entries = list(Keys.baseline(a=3, b=1, c=1).values())
        rows = Ratchet.summary(entries, lambda entry: entry.key.section)
        assert [(row.label, row.entries, row.occurrences) for row in rows] == [("lines", 2, 4), ("hatches", 1, 1)]
        rows = Ratchet.summary(entries, lambda entry: entry.bead)
        assert [row.label for row in rows] == ["bead-a", "bead-b", "bead-c"]

    def test_markdown_table_has_a_total(self) -> None:
        """A job summary without a total would hide how much is left."""
        table = Ratchet.markdown_table("Per bead", "bead", Ratchet.summary(Keys.baseline(a=3, b=1).values(), lambda entry: entry.bead))
        assert table.splitlines()[0] == "### Per bead"
        assert "| bead-a | 1 | 3 |" in table
        assert table.splitlines()[-1] == "| **total** | **2** | **4** |"


class TestBaselineFile:
    """The baseline file has one canonical form and refuses malformed entries."""

    def test_render_is_sorted_one_entry_per_line_and_parses_back(self) -> None:
        """Without a canonical sorted form, two branches that each fixed a violation would conflict on merge."""
        entries = Keys.baseline(b=1, a=2, c=1)
        text = BaselineFile.render(entries, Keys.LAYOUT)
        assert text.splitlines() == [
            "# A test baseline.",
            "lines:",
            '- {file: "a.py", symbol: "f", count: 2, bead: "bead-a"}',
            '- {file: "b.py", symbol: "g", count: 1, bead: "bead-b"}',
            "hatches:",
            '- {file: "a.py", symbol: "*", count: 1, bead: "bead-c"}',
        ]
        assert BaselineFile.parse(text, Keys.LAYOUT) == entries
        assert BaselineFile.render(dict(reversed(list(entries.items()))), Keys.LAYOUT) == text

    def test_an_empty_section_is_written_as_an_empty_list(self) -> None:
        """An empty section written as a bare key would read back as None and look like a different file."""
        text = BaselineFile.render({}, Keys.LAYOUT)
        assert text.splitlines()[1:] == ["lines: []", "hatches: []"]
        assert not BaselineFile.parse(text, Keys.LAYOUT)

    @pytest.mark.parametrize(
        "text, message",
        [
            ("other: []\n", "unknown sections"),
            ("lines: {}\n", "must be a list"),
            ('lines:\n- {file: "a", count: 1, bead: "x"}\n', "exactly the fields"),
            ('lines:\n- {file: "a", symbol: "f", count: 0, bead: "x"}\n', "positive integer"),
            ('lines:\n- {file: "a", symbol: "f", count: 1, bead: ""}\n', "names no bead"),
            ('lines:\n- {file: "a", symbol: "f", count: 1, bead: "x"}\n- {file: "a", symbol: "f", count: 2, bead: "y"}\n', "occurs twice"),
            ("- 1\n", "must be a mapping"),
        ],
    )
    def test_malformed_baselines_are_refused(self, text: str, message: str) -> None:
        """A malformed entry read leniently could tolerate a violation nobody listed."""
        with pytest.raises(BaselineError, match=message):
            BaselineFile.parse(text, Keys.LAYOUT)

    def test_render_refuses_an_unknown_section(self) -> None:
        """An entry of an unknown section would be dropped from the file without a word."""
        with pytest.raises(BaselineError, match="unknown sections"):
            BaselineFile.render({EntryKey("other", ("a", "b")): BaselineEntry(EntryKey("other", ("a", "b")), 1, "x")}, Keys.LAYOUT)

    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        """The file on disk must hold exactly what was written."""
        path = tmp_path / "baseline.yaml"
        BaselineFile.write(path, Keys.baseline(a=1), Keys.LAYOUT)
        assert BaselineFile.read(path, Keys.LAYOUT) == Keys.baseline(a=1)


class TestBeadIds:
    """``BeadIds.read`` reads the ids of a beads file."""

    def test_reads_every_id_and_skips_blank_lines(self, tmp_path: Path) -> None:
        """A missed id would fail a baseline entry that names an existing bead."""
        path = tmp_path / "issues.jsonl"
        path.write_text('{"id": "hisim-a"}\n\n{"id": "hisim-b", "title": "t"}\n', encoding="utf-8")
        assert BeadIds.read(path) == frozenset({"hisim-a", "hisim-b"})

    def test_a_line_without_id_is_refused(self, tmp_path: Path) -> None:
        """A corrupt beads file must fail loudly, not shrink the set of known ids."""
        path = tmp_path / "issues.jsonl"
        path.write_text('{"title": "no id"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="is not a bead"):
            BeadIds.read(path)
