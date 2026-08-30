#!/usr/bin/env python3
"""Run one Python setup and its recorded twin side by side and prove they are the same simulation.

TEMPORARY — this script is the P3 migration parity rig (requirements R11) and is deleted together
with the rest of the rig in P3's last PR (R11.8, AC-P3.20). It is not the permanent golden gate,
it produces no reference and it blesses nothing: its only question is whether the declarative file
recorded from a setup reproduces that setup.

One invocation covers one ``(setup, window)`` triple and makes the three comparisons of R11.3 in
order. First the component set and the wire set, through the declared port-renaming table, because
two systems wired differently have nothing worth comparing numerically. Then **every** column of
the result frame — the content of ``all_results.csv`` — since a difference the KPI layer happens
to average away is still a difference. Then ``all_kpis.json``, where KPI computation succeeds;
seven in-scope setups crash in that layer today, and those still receive a verdict from the first
two comparisons rather than an error (R11.4).

The comparison is **exact by default** (R11.2). Both runs happen in one process on one machine,
where determinism is byte-exact, so the ``rel_tol = 1e-9`` of the permanent gate — which exists to
absorb drift between machines — is not needed here. ``--rel-tol`` and ``--abs-tol`` exist so that a
triple which turns out to need a tolerance can be measured rather than argued about, and the
report says so loudly when a non-zero tolerance is what made a triple pass: that is a finding to
investigate, never a threshold to settle on.

The verdict vocabulary and everything that renders it live in ``p3_parity_verdicts.py``, and the
two runs themselves in ``p3_parity_runs.py``; what is here is the comparison and the command line.

Examples
--------
    python scripts/p3_parity_check.py --setup basic_household --window january
    python scripts/p3_parity_check.py --setup household_gas_building_sizer --window july --rel-tol 1e-12
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import ClassVar, List, Optional, Sequence

import numpy as np
import pandas as pd

from hisim.energy_system.parity import (
    PortRenaming,
    ResultComparison,
    WiringDiff,
    WiringParityHarness,
)

try:  # importable both as ``scripts.p3_parity_check`` (tests) and as a script from ``scripts/``
    from p3_parity_renamings import DeclaredPortRenamings  # type: ignore[import-not-found]
    from p3_parity_runs import (  # type: ignore[import-not-found]
        ColumnDifference,
        KpiDifference,
        ParitySide,
        ParityWindows,
        RunOutcome,
        TripleInputs,
    )
    from p3_parity_verdicts import (  # type: ignore[import-not-found]
        ArtifactWriter,
        CheckedTriple,
        Report,
        Tolerance,
        TripleVerdict,
        Verdict,
        last_line,
    )
except ModuleNotFoundError:  # pragma: no cover - depends on how scripts/ is on the path
    from scripts.p3_parity_renamings import DeclaredPortRenamings
    from scripts.p3_parity_runs import (
        ColumnDifference,
        KpiDifference,
        ParitySide,
        ParityWindows,
        RunOutcome,
        TripleInputs,
    )
    from scripts.p3_parity_verdicts import (
        ArtifactWriter,
        CheckedTriple,
        Report,
        Tolerance,
        TripleVerdict,
        Verdict,
        last_line,
    )


class Paths:
    """Where this script finds the setups, their recorded twins and its own working directory.

    Held as one class of constants rather than as module-level names so that everything the rig
    touches is visible in one place, which matters more than usual here: this whole file is
    scheduled for deletion, and the removal PR has to be able to see what goes with it.
    """

    #: Root of the repository, found from this file rather than from the working directory.
    REPO_ROOT: ClassVar[Path] = Path(__file__).resolve().parent.parent

    #: Where the Python setups live.
    SETUPS: ClassVar[Path] = REPO_ROOT / "system_setups"

    #: Where the recorded twins live.
    ENERGY_SYSTEMS: ClassVar[Path] = REPO_ROOT / "energy_systems"

    #: Suffix of a recorded twin.
    RECORDED_SUFFIX: ClassVar[str] = ".energy_system.yaml"

    #: Where a run writes its two result trees and, on failure, its artifacts.
    DEFAULT_WORK: ClassVar[Path] = REPO_ROOT / "results" / "p3-parity"


class ParityChecker:
    """Runs one triple both ways and makes the three comparisons of R11.3 in order.

    All the state a check has is the tolerance and the renaming table, so the class is a small
    object over those two rather than a bag of classmethods with them threaded through every
    signature. The comparisons themselves are delegated to ``hisim.energy_system.parity``: the rig
    declares what it claims and prints what it found, and the harness there decides whether two
    snapshots or two frames agree, so there is exactly one comparison engine in the repository.
    """

    def __init__(self, tolerance: Tolerance, renaming: PortRenaming) -> None:
        """Prepares a checker for a whole dispatch.

        Args:
            tolerance: The slack this run allows; exact equality unless a caller raised it.
            renaming: The declared translation between the two paths' aggregator port names.
        """
        self.tolerance = tolerance
        self.renaming = renaming

    def check(self, triple: TripleInputs) -> CheckedTriple:
        """Runs one triple both ways and returns its verdict together with the two runs.

        Args:
            triple: The setup, its twin, the window and where to work.

        Returns:
            The verdict, carrying the wire diff and the differing columns and KPIs when it failed,
            and the two run outcomes a failure's artifacts are written from.
        """
        verdict = TripleVerdict(stem=triple.stem, window=triple.window, tolerated=False)
        python_side = self.run_side(triple, "python")
        declarative_side = self.run_side(triple, "declarative")
        sides = (("python", python_side), ("declarative", declarative_side))
        for name, side in sides:
            if side.fatal_error is not None:
                verdict.wiring = Verdict.FAILED
                verdict.results = Verdict.FAILED
                verdict.kpis = Verdict.FAILED
                verdict.notes.append(f"the {name} run did not finish: {last_line(side.fatal_error)}")
                return CheckedTriple(verdict=verdict, sides=sides)
        self.compare_wiring(python_side, declarative_side, verdict)
        self.compare_results(python_side, declarative_side, verdict)
        self.compare_kpis(python_side, declarative_side, verdict)
        return CheckedTriple(verdict=verdict, sides=sides)

    def run_side(self, triple: TripleInputs, side: str) -> RunOutcome:
        """Runs one side of a triple under this rig's shared parameters.

        Args:
            triple: The triple being checked.
            side: One of :attr:`TripleInputs.SIDES`.

        Returns:
            What that side produced.
        """
        results, cache = triple.side_directories(side)
        parameters = ParityWindows.build(triple.window, results, cache)
        if side == "python":
            return ParitySide.python(triple.setup_path, parameters)
        return ParitySide.declarative(triple.energy_system_path, parameters)

    def compare_wiring(self, expected: RunOutcome, actual: RunOutcome, verdict: TripleVerdict) -> None:
        """Makes the first comparison: the component set and the wire set (R11.3).

        The Python snapshot is translated through the declared renaming table before the diff, so
        an aggregator port the two paths name differently compares equal *because someone said the
        two names mean the same wire*, and every name the table does not list still has to match
        literally.

        Args:
            expected: The Python side.
            actual: The declarative side.
            verdict: The verdict being filled in.
        """
        if expected.snapshot is None or actual.snapshot is None:
            verdict.wiring = Verdict.FAILED
            verdict.notes.append("one of the two runs produced no wiring snapshot")
            return
        diff: WiringDiff = WiringParityHarness.compare(
            self.renaming.apply_to(expected.snapshot), actual.snapshot
        )
        verdict.wire_diff = diff.describe()
        if diff.is_identical():
            verdict.wiring = Verdict.OK
            if diff.component_order_differs:
                verdict.notes.append("the components are registered in a different order")
            return
        verdict.wiring = Verdict.FAILED

    def compare_results(self, expected: RunOutcome, actual: RunOutcome, verdict: TripleVerdict) -> None:
        """Makes the second comparison: every column of the result frame (R11.3, amended).

        Args:
            expected: The Python side.
            actual: The declarative side.
            verdict: The verdict being filled in.
        """
        if expected.frame is None or actual.frame is None:
            verdict.results = Verdict.FAILED
            verdict.notes.append("one of the two runs produced no result frame")
            return
        translated = self.renaming.apply_to_results(expected.frame)
        comparison = ResultComparison.between(translated, actual.frame)
        if comparison.structural_problems:
            verdict.results = Verdict.FAILED
            verdict.notes.extend(comparison.structural_problems)
            return
        if comparison.max_absolute_deviation == 0.0:
            verdict.results = Verdict.OK
            return
        verdict.differences.columns = self.differing_columns(translated, actual.frame)
        if not verdict.differences.columns:
            verdict.results = Verdict.TOLERATED
            verdict.tolerated = True
            verdict.notes.append(
                f"the result columns agree only within {self.tolerance.describe()} "
                f"(worst relative {comparison.max_relative_deviation:.6g} in "
                f"'{comparison.worst_column}')"
            )
            return
        verdict.results = Verdict.FAILED
        verdict.notes.append(
            f"{len(verdict.differences.columns)} result column(s) differ; worst relative "
            f"{comparison.max_relative_deviation:.6g} in '{comparison.worst_column}'"
        )

    def differing_columns(self, expected: pd.DataFrame, actual: pd.DataFrame) -> List[ColumnDifference]:
        """Lists the columns whose two runs disagree beyond this run's tolerance.

        Only reached once the shared comparison has already found *some* deviation, because its
        purpose is the failure report rather than the verdict: the report has to name what moved,
        and a per-column sweep is the only way to name more than the single worst one.

        Args:
            expected: The Python result frame, already translated.
            actual: The declarative result frame.

        Returns:
            One entry per differing column, worst row quoted, in frame order.
        """
        found: List[ColumnDifference] = []
        for name in [str(column) for column in expected.columns]:
            left = expected[name].to_numpy(dtype=float)
            right = actual[name].to_numpy(dtype=float)
            if np.array_equal(left, right):
                continue
            absolute = np.abs(left - right)
            position = int(np.argmax(absolute))
            if self.tolerance.accepts(float(left[position]), float(right[position])):
                continue
            scale = max(abs(float(left[position])), abs(float(right[position]))) or 1.0
            found.append(
                ColumnDifference(
                    column=name,
                    timestamp=str(expected.index[position]),
                    expected=float(left[position]),
                    actual=float(right[position]),
                    absolute=float(absolute[position]),
                    relative=float(absolute[position]) / scale,
                )
            )
        return found

    def compare_kpis(self, expected: RunOutcome, actual: RunOutcome, verdict: TripleVerdict) -> None:
        """Makes the third comparison: ``all_kpis.json``, where KPI computation succeeded (R11.4).

        Args:
            expected: The Python side.
            actual: The declarative side.
            verdict: The verdict being filled in.
        """
        unavailable = [
            f"{name}: {last_line(side.kpi_error)}"
            for name, side in (("python", expected), ("declarative", actual))
            if side.kpis is None or side.kpi_error is not None
        ]
        if unavailable:
            verdict.kpis = Verdict.UNAVAILABLE
            verdict.notes.append("KPI stage unavailable (" + "; ".join(unavailable) + ")")
            return
        left, right = expected.kpis or {}, actual.kpis or {}
        only_python = sorted(set(left) - set(right))
        only_declarative = sorted(set(right) - set(left))
        if only_python or only_declarative:
            verdict.kpis = Verdict.FAILED
            verdict.notes.append(
                f"the two KPI sets differ: {len(only_python)} only in the python run, "
                f"{len(only_declarative)} only in the declarative run"
            )
            return
        verdict.differences.kpis = [
            KpiDifference(name=name, expected=left[name], actual=right[name])
            for name in sorted(left)
            if not self.tolerance.accepts(left[name], right[name])
        ]
        exact = [name for name in left if left[name] != right[name]]
        if verdict.differences.kpis:
            verdict.kpis = Verdict.FAILED
            verdict.notes.append(f"{len(verdict.differences.kpis)} KPI(s) differ")
            return
        if exact and not self.tolerance.exact:
            verdict.kpis = Verdict.TOLERATED
            verdict.tolerated = True
            verdict.notes.append(f"{len(exact)} KPI(s) agree only within {self.tolerance.describe()}")
            return
        verdict.kpis = Verdict.OK


def discover(stems: Optional[Sequence[str]]) -> List[str]:
    """The setups this rig can cover: those with both a Python module and a recorded twin.

    A setup without a twin is not silently skipped anywhere else in P3 — the recording driver
    fails and names it — so the rig has nothing to add by failing again; it covers what has been
    recorded and says how many that is.

    Args:
        stems: Setup stems the caller restricted the run to, or ``None`` for all of them.

    Returns:
        The stems to cover, sorted.

    Raises:
        SystemExit: If a named stem has no setup module or no recorded twin.
    """
    available = sorted(
        path.stem
        for path in Paths.SETUPS.glob("*.py")
        if path.name != "__init__.py"
        and (Paths.ENERGY_SYSTEMS / f"{path.stem}{Paths.RECORDED_SUFFIX}").exists()
    )
    if not stems:
        return available
    missing = sorted(set(stems) - set(available))
    if missing:
        raise SystemExit(
            f"--setup names {missing} which have no setup module or no recorded twin in "
            f"{Paths.ENERGY_SYSTEMS.name}/."
        )
    return [stem for stem in available if stem in set(stems)]


def parse_arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parses the command line of one dispatch.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--setup", nargs="+", metavar="STEM", help="cover only these setups")
    parser.add_argument(
        "--window",
        nargs="+",
        choices=list(ParityWindows.names()),
        help="cover only these windows (default: both)",
    )
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=0.0,
        help="relative slack; the default of 0 demands exact equality, and any other value is a finding",
    )
    parser.add_argument(
        "--abs-tol",
        type=float,
        default=0.0,
        help="absolute slack; the default of 0 demands exact equality, and any other value is a finding",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Paths.DEFAULT_WORK,
        help=f"where the two runs write and a failure leaves its artifacts (default: {Paths.DEFAULT_WORK})",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="also write every verdict as JSON to this file, for the workflow's summary job",
    )
    parser.add_argument(
        "--summarize",
        type=Path,
        default=None,
        help="run nothing; print one table from the verdict JSON files under this directory",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs every requested triple and prints one table of what they concluded.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        ``0`` when every triple reached parity, ``1`` otherwise.
    """
    arguments = parse_arguments(argv)
    if arguments.summarize is not None:
        table, without_parity = Report.summarize(arguments.summarize)
        print(table)
        print(f"\n{without_parity} triple(s) did not reach parity.")
        return 1 if without_parity else 0
    tolerance = Tolerance(arguments.rel_tol, arguments.abs_tol)
    checker = ParityChecker(tolerance, DeclaredPortRenamings.port_renaming())
    stems = discover(arguments.setup)
    windows = list(arguments.window or ParityWindows.names())
    print(f"Comparing {len(stems)} setup(s) over {len(windows)} window(s) at {tolerance.describe()}.\n")

    verdicts: List[TripleVerdict] = []
    for stem in stems:
        for window in windows:
            triple = TripleInputs(
                stem=stem,
                window=window,
                setup_path=Paths.SETUPS / f"{stem}.py",
                energy_system_path=Paths.ENERGY_SYSTEMS / f"{stem}{Paths.RECORDED_SUFFIX}",
                work_directory=arguments.work_dir / stem / window,
            )
            verdict = run_triple(checker, triple, tolerance)
            verdicts.append(verdict)
            print(f"[{'PASS' if verdict.passed else 'FAIL'}] {stem} / {window}", flush=True)

    print("\n" + Report.table(verdicts))
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(
            json.dumps([verdict.to_json() for verdict in verdicts], indent=2), encoding="utf-8"
        )
    tolerated = [verdict for verdict in verdicts if verdict.tolerated]
    if tolerated:
        print(
            f"\n!!! {len(tolerated)} triple(s) passed ONLY because a non-zero tolerance "
            f"({tolerance.describe()}) was allowed. Exact equality is what this rig exists to "
            "prove (R11.2); every one of these is a finding to investigate:"
        )
        for verdict in tolerated:
            print(f"    {verdict.stem} / {verdict.window}: {'; '.join(verdict.notes)}")
    failed = [verdict for verdict in verdicts if not verdict.passed]
    print(f"\nDONE: {len(verdicts) - len(failed)} triple(s) reached parity, {len(failed)} did not.")
    return 1 if failed else 0


def run_triple(checker: ParityChecker, triple: TripleInputs, tolerance: Tolerance) -> TripleVerdict:
    """Checks one triple and, when it failed, prints and stores everything about the failure.

    Args:
        checker: The configured checker.
        triple: The triple to run.
        tolerance: The slack allowed, quoted in the failure report.

    Returns:
        The verdict.
    """
    checked = checker.check(triple)
    verdict = checked.verdict
    if verdict.passed:
        return verdict
    report = Report.failure(verdict, tolerance)
    print("\n" + report + "\n")
    directory = ArtifactWriter.write(triple, verdict, checked.sides, report)
    print(f"Artifacts for {triple.stem} / {triple.window} are in {directory}.\n")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
