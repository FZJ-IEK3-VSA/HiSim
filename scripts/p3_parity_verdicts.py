#!/usr/bin/env python3
"""What the parity rig concluded about a triple, and how it says so.

TEMPORARY — this module belongs to the P3 migration parity rig (requirements R11) and is deleted
with it in phase P6 (R11.8 amended and AC-P3.20 deferred to P6, 2026-08-31).

The comparison lives next door in ``p3_parity_check.py``; what lives here is the vocabulary it
answers in and everything that turns an answer into output. The two are separated because they
are read by different people at different moments: the comparison is read by someone asking what
the rig checks, and this module by someone reading a table or an uploaded artifact and asking what
a cell means.

Three of these classes carry a rule rather than a format. :class:`Verdict` has four states rather
than two, because "the two runs agree" and "there was nothing to compare" are different answers
(R11.4). :class:`Tolerance` defaults to exact equality and marks any run that needed slack, because
needing slack is a finding rather than a setting (R11.2). And :class:`ArtifactWriter` writes only
for a failing triple, because forty passing triples' result frames are evidence for something
nobody is going to look at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, List, Optional, Sequence, Tuple

import numpy as np

try:  # importable both as ``scripts.p3_parity_verdicts`` (tests) and as a sibling of the checker
    from p3_parity_runs import (  # type: ignore[import-not-found]
        ColumnDifference,
        Differences,
        RunOutcome,
        TripleInputs,
    )
except ModuleNotFoundError:  # pragma: no cover - depends on how scripts/ is on the path
    from scripts.p3_parity_runs import ColumnDifference, Differences, RunOutcome, TripleInputs


class Verdict(Enum):
    """The four states one comparison of one triple can be in, and how a table shows them.

    An enum rather than string constants so that a state outside the four is unrepresentable — a
    misspelled verdict must be an error at the point of assignment, not a row that quietly renders
    as FAIL. Four states rather than two, because "the two runs agree" and "there was nothing to
    compare" are different answers and a table must show which one it is. ``TOLERATED`` is loud on
    purpose — a triple that only agrees once a tolerance is allowed is a finding, so it can never
    look like a plain pass. ``UNAVAILABLE`` names its stage's state precisely but fails its triple
    (R11.4, amended 2026-09-05): every setup's KPI layer is expected to work now that the golden
    gate covers the whole fleet, so a stage that could not run is something to investigate, not a
    shrug — the note beside it says which side and why.
    """

    OK = "OK"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    TOLERATED = "TOLERATED"

    @property
    def passes(self) -> bool:
        """Whether a stage in this state lets its triple count as parity.

        Returns:
            ``True`` for ``OK`` and for ``TOLERATED`` (which is marked loudly elsewhere); ``False``
            for ``FAILED`` and for ``UNAVAILABLE``.
        """
        return self in (Verdict.OK, Verdict.TOLERATED)


@dataclass
class TripleVerdict:
    """What comparing one triple concluded, in the form the summary table prints.

    One object per triple rather than a stream of assertions, because the rig's output is a table
    covering every triple of a dispatch (R11.7): a caller has to be able to run forty of these and
    print one row each, and the workflow's summary job reads exactly these fields back out of the
    JSON the checker writes.
    """

    stem: str
    window: str
    wiring: Verdict = Verdict.UNAVAILABLE
    results: Verdict = Verdict.UNAVAILABLE
    kpis: Verdict = Verdict.UNAVAILABLE
    notes: List[str] = field(default_factory=list)
    wire_diff: str = ""
    differences: Differences = field(default_factory=Differences)
    tolerated: bool = False

    @property
    def passed(self) -> bool:
        """Whether this triple counts as parity.

        Returns:
            ``True`` when every stage passes; an unavailable stage fails the triple (R11.4,
            amended 2026-09-05).
        """
        return all(state.passes for state in (self.wiring, self.results, self.kpis))

    def row(self) -> str:
        """Renders this triple as one row of the summary table.

        Returns:
            A pipe-separated row without the surrounding table.
        """
        note = "; ".join(self.notes)
        return (
            f"| {self.stem} | {self.window} | {self.wiring.value} | {self.results.value} | "
            f"{self.kpis.value} | {'PASS' if self.passed else 'FAIL'} | {note} |"
        )

    def to_json(self) -> dict:
        """The machine-readable form the workflow's summary job reads back.

        Returns:
            A plain dictionary of the verdict's states and notes.
        """
        return {
            "setup": self.stem,
            "window": self.window,
            "wiring": self.wiring.value,
            "results": self.results.value,
            "kpis": self.kpis.value,
            "passed": self.passed,
            "tolerated": self.tolerated,
            "notes": list(self.notes),
        }


@dataclass
class CheckedTriple:
    """One checked triple: its verdict and the two runs that produced it.

    The verdict alone is what a table prints, but a failure has to be written out of the runs
    themselves — both KPI sets, both result frames — so the two travel together rather than the
    checker keeping the last run around as state. That also keeps a checker reusable across a
    whole dispatch without one triple's artifacts being able to leak into the next one's.
    """

    verdict: "TripleVerdict"
    sides: Tuple[Tuple[str, RunOutcome], ...]

class Tolerance:
    """The relative and absolute slack a run allows, and the fact that allowing any is a finding.

    Exact equality is the default and the only setting that proves what the rig exists to prove
    (R11.2). A tolerance is offered because a triple that needs one has to be *measured* — knowing
    that a setup agrees to 1e-14 and not to 1e-16 is the beginning of an investigation — but a run
    that used one is marked as such in every line of output it produces, so the finding cannot be
    mistaken for a pass.
    """

    def __init__(self, relative: float = 0.0, absolute: float = 0.0) -> None:
        """Records the slack of one run.

        Args:
            relative: Largest relative deviation still counted as equal; ``0.0`` is exact.
            absolute: Largest absolute deviation still counted as equal; ``0.0`` is exact.

        Raises:
            ValueError: If either slack is negative — a typo like ``--rel-tol -1`` would otherwise
                silently behave like exact equality while the report prints the nonsense value as
                if it had been measured against.
        """
        self.relative = float(relative)
        self.absolute = float(absolute)
        if self.relative < 0.0 or self.absolute < 0.0:
            raise ValueError(
                f"A tolerance cannot be negative (rel_tol={self.relative:g}, "
                f"abs_tol={self.absolute:g})."
            )

    @property
    def exact(self) -> bool:
        """Whether this run demands byte equality.

        Returns:
            ``True`` when neither slack was raised above zero.
        """
        return self.relative == 0.0 and self.absolute == 0.0

    @staticmethod
    def equal(expected: Any, actual: Any) -> bool:
        """Exact equality with one adjustment: two NaN values count as equal.

        A value both runs failed to produce is the same value, and ``nan != nan`` would otherwise
        make a byte-identical pair of runs fail its comparison. This is the rig's single notion of
        "exactly equal", so exact mode and tolerant mode cannot disagree about identical NaNs.

        Args:
            expected: The Python run's value.
            actual: The declarative run's value.

        Returns:
            ``True`` when the two are equal, NaN==NaN included.
        """
        if expected == actual:
            return True
        if not isinstance(expected, float) or not isinstance(actual, float):
            return False
        return bool(np.isnan(expected) and np.isnan(actual))

    def accepts(self, expected: Any, actual: Any) -> bool:
        """Whether two values count as equal under this tolerance.

        Non-numeric values — the flattened KPI tree carries strings and nulls — are always compared
        exactly, because a tolerance has no meaning for them and silently passing an unequal string
        would hide the very thing the comparison is for.

        Args:
            expected: The Python run's value.
            actual: The declarative run's value.

        Returns:
            ``True`` when the two are equal, or close enough under a non-zero tolerance.
        """
        if self.equal(expected, actual):
            return True
        if not isinstance(expected, (int, float)) or not isinstance(actual, (int, float)):
            return False
        if self.exact:
            return False
        return bool(np.isclose(expected, actual, rtol=self.relative, atol=self.absolute, equal_nan=True))

    def accepts_column(self, expected: np.ndarray, actual: np.ndarray) -> np.ndarray:
        """Row-wise :meth:`accepts` over two whole result columns.

        A column has to be judged at every row, not at its single worst-absolute row: under a
        relative tolerance the out-of-tolerance row can be a low-magnitude one whose absolute
        deviation is unremarkable, and probing only the worst-absolute row would silently skip it.

        Args:
            expected: The Python run's column as floats.
            actual: The declarative run's column as floats.

        Returns:
            A boolean mask, ``True`` where the two rows count as equal under this tolerance.
        """
        equal = np.asarray((expected == actual) | (np.isnan(expected) & np.isnan(actual)), dtype=bool)
        if self.exact:
            return equal
        close = np.isclose(expected, actual, rtol=self.relative, atol=self.absolute, equal_nan=True)
        return np.asarray(equal | close, dtype=bool)

    def describe(self) -> str:
        """Renders the tolerance for the report header.

        Returns:
            A short phrase naming the two slacks.
        """
        if self.exact:
            return "exact equality"
        return f"rel_tol={self.relative:g}, abs_tol={self.absolute:g}"

def last_line(text: Optional[str]) -> str:
    """Reduces a traceback to the one line worth putting in a table cell.

    Args:
        text: The traceback, or ``None``.

    Returns:
        The last non-empty line, or a note that there was no message.
    """
    if not text:
        return "no message"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no message"

class ArtifactWriter:
    """Writes everything a failing triple has to hand to whoever investigates it.

    A parity failure is never diagnosed from a verdict line, so a failing triple leaves behind
    both KPI sets, both result frames and the wire diff (R11.7) in one directory the workflow can
    upload whole. A passing triple leaves none of it, because forty passing triples' result frames
    are hundreds of megabytes of evidence for something nobody is going to look at.
    """

    #: File names written into a failing triple's artifact directory.
    WIRE_DIFF: ClassVar[str] = "wire_diff.txt"
    REPORT: ClassVar[str] = "report.txt"
    VERDICT: ClassVar[str] = "verdict.json"
    KPI_TEMPLATE: ClassVar[str] = "{side}_all_kpis.json"
    RESULTS_TEMPLATE: ClassVar[str] = "{side}_all_results.csv"

    @classmethod
    def write(
        cls,
        triple: TripleInputs,
        verdict: TripleVerdict,
        sides: Sequence[Tuple[str, RunOutcome]],
        report: str,
    ) -> Path:
        """Writes the artifacts of one failing triple.

        Args:
            triple: The triple that failed.
            verdict: Its verdict.
            sides: The two named run outcomes.
            report: The rendered failure report.

        Returns:
            The directory everything was written to.
        """
        directory: Path = triple.work_directory / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / cls.WIRE_DIFF).write_text(verdict.wire_diff or "no wiring diff", encoding="utf-8")
        (directory / cls.REPORT).write_text(report, encoding="utf-8")
        (directory / cls.VERDICT).write_text(json.dumps(verdict.to_json(), indent=2), encoding="utf-8")
        for name, side in sides:
            if side.kpis is not None:
                (directory / cls.KPI_TEMPLATE.format(side=name)).write_text(
                    json.dumps(side.kpis, indent=2, sort_keys=True), encoding="utf-8"
                )
            if side.frame is not None:
                side.frame.to_csv(directory / cls.RESULTS_TEMPLATE.format(side=name))
        return directory

class Report:
    """Everything one invocation prints, so the run and its rendering stay separable.

    Kept apart from the checker for the usual reason — a test drives the check and inspects the
    verdict without parsing text — and because the workflow prints the same rows from the JSON
    the checker writes, so the row format has to exist independently of any one run.
    """

    #: The header of the summary table, printed once above the rows.
    HEADER: ClassVar[Tuple[str, str]] = (
        "| setup | window | wiring | results | KPIs | verdict | notes |",
        "|---|---|---|---|---|---|---|",
    )

    @classmethod
    def failure(cls, verdict: TripleVerdict, tolerance: Tolerance) -> str:
        """Renders the full failure report of one triple.

        Args:
            verdict: The failing verdict.
            tolerance: The slack the run allowed, quoted so the reader knows what was demanded.

        Returns:
            A multi-line report naming the wire diff, the differing columns and the differing KPIs.
        """
        lines = [
            f"PARITY FAILURE: {verdict.stem} / {verdict.window} (compared at {tolerance.describe()})",
            "",
            f"wiring: {verdict.wiring.value}   results: {verdict.results.value}   "
            f"KPIs: {verdict.kpis.value}",
        ]
        lines.extend(f"  note: {note}" for note in verdict.notes)
        if verdict.wiring == Verdict.FAILED:
            lines += ["", "wire diff (python -> declarative, through the declared renamings):"]
            lines.extend(f"  {line}" for line in verdict.wire_diff.splitlines())
        lines += cls.differences("result columns that differ", verdict.differences.columns)
        lines += cls.differences("KPIs that differ", verdict.differences.kpis)
        return "\n".join(lines)

    @classmethod
    def differences(cls, title: str, entries: Sequence[Any]) -> List[str]:
        """Renders one block of differences, quoting the first few and counting the rest.

        Args:
            title: What the block is about.
            entries: The differences, each carrying a ``describe`` method.

        Returns:
            The block's lines, empty when there is nothing to show.
        """
        if not entries:
            return []
        lines = ["", f"{title} ({len(entries)}):"]
        lines.extend(f"  {entry.describe()}" for entry in entries[: Differences.QUOTED])
        if len(entries) > Differences.QUOTED:
            lines.append(f"  ... and {len(entries) - Differences.QUOTED} more")
        return lines

    @classmethod
    def summarize(cls, directory: Path) -> Tuple[str, int, int]:
        """Renders one table from the verdict files a whole dispatch left behind.

        The workflow runs each triple in its own container, so no single job sees more than one
        verdict; the summary job collects the JSON every job wrote and prints them as the one
        table R11.7 asks for. Rendering it here rather than in the workflow keeps the row format
        in a single place, so a column added to the table appears in both outputs at once.

        Args:
            directory: A directory tree holding the ``verdict.json`` files of one dispatch.

        Returns:
            The rendered table, the number of triples it covers, and the number that did not
            reach parity. The count of covered triples is returned so the caller can refuse a
            dispatch that covered nothing — an empty verdict directory must not read as a pass.
        """
        rows: List[TripleVerdict] = []
        for path in sorted(directory.rglob("*.json")):
            for entry in cls.entries(json.loads(path.read_text(encoding="utf-8"))):
                rows.append(
                    TripleVerdict(
                        stem=entry["setup"],
                        window=entry["window"],
                        wiring=Verdict(entry["wiring"]),
                        results=Verdict(entry["results"]),
                        kpis=Verdict(entry["kpis"]),
                        notes=list(entry.get("notes", ())),
                        tolerated=bool(entry.get("tolerated", False)),
                    )
                )
        rows.sort(key=lambda verdict: (verdict.stem, verdict.window))
        return cls.table(rows), len(rows), len([row for row in rows if not row.passed])

    @classmethod
    def entries(cls, payload: Any) -> List[dict]:
        """Normalises one verdict file, which holds either a list of verdicts or a single one.

        Args:
            payload: The parsed file.

        Returns:
            The verdicts it carries.
        """
        if isinstance(payload, list):
            return [entry for entry in payload if isinstance(entry, dict) and "setup" in entry]
        if isinstance(payload, dict) and "setup" in payload:
            return [payload]
        return []

    @classmethod
    def table(cls, verdicts: Sequence[TripleVerdict]) -> str:
        """Renders the summary table of a whole dispatch.

        Args:
            verdicts: One verdict per triple, in the order they ran.

        Returns:
            The table, header included.
        """
        return "\n".join([*cls.HEADER, *(verdict.row() for verdict in verdicts)])
