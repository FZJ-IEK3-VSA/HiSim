#!/usr/bin/env python3
"""Record every Python setup as its declarative twin, and check the twins are current.

Every ``system_setups/*.py`` has a committed ``energy_systems/<stem>.energy_system.yaml`` beside
it, produced by running the setup and writing down what it built. This script is what produces
them, and with ``--check`` it is what proves they are still what the setups build — the freshness
job runs exactly that and fails on any difference, so a setup can never change without its twin.

There is no skip list, deliberately. A setup that cannot be recorded is a defect in the setup or a
gap in the format, not an exception to be carried in a list nobody revisits; the run collects
every failure so one broken setup does not hide the next, and then exits non-zero naming all of
them.

Each setup is recorded in its own subprocess, because setups mutate module state, HiSim
singletons and the local load-profile-generator calculation index, and two setups in one
interpreter would record each other's leftovers. The runs are sequential rather than parallel for
a second reason: two setups needing the same *new* simulation-parameters file have to share one
file, and that only works if the second one can see what the first one wrote.

Examples
--------
    python scripts/record_all_setups.py                    # record every setup
    python scripts/record_all_setups.py --only basic_household
    python scripts/record_all_setups.py --check            # what the freshness job runs
"""
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess  # nosec B404 - the only child is this repository's own CLI
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


class Paths:
    """Where this script finds the setups, the twins and the recorder.

    Held as one class of constants rather than as module-level names so that the layout of the
    repository is stated in a single place, and so that a reader who wants to know what the script
    touches can see all of it at once.
    """

    #: Root of the repository, found from this file rather than from the working directory.
    REPO_ROOT = Path(__file__).resolve().parent.parent

    #: Where the Python setups live.
    SETUPS = REPO_ROOT / "system_setups"

    #: Where the recorded twins and the shared simulation-parameters files live.
    ENERGY_SYSTEMS = REPO_ROOT / "energy_systems"

    #: The parameters a setup is recorded under unless the caller names another file. A setup is
    #: free to replace what it is handed; what the recording follows is what it ended up with.
    DEFAULT_PARAMETERS = ENERGY_SYSTEMS / "one_day_15min.simulation.yaml"

    #: Suffix of a recorded energy-system file.
    RECORDED_SUFFIX = ".energy_system.yaml"

    #: Glob matching the shared simulation-parameters files.
    PARAMETERS_PATTERN = "*.simulation.yaml"

    #: The module of the setup that must never be recorded, because it is not a setup.
    NOT_A_SETUP = "__init__.py"

    #: Prefix of the throwaway directory ``--check`` records into. It is created *inside* the
    #: repository and one level deep, because a recorded file's schema comment is written relative
    #: to the directory holding it: recording into a temporary directory anywhere else would
    #: produce a different first line and report every setup as changed.
    CHECK_PREFIX = ".record-check-"


@dataclass
class SetupOutcome:
    """What recording one setup produced: where it went, or why it could not be recorded.

    A verdict line is printed the moment a setup finishes, but the outcome is also kept, because
    the run's own verdict needs all of them at once: R5.4 makes an unrecordable setup a defect
    rather than an exception, so the summary has to name every one of them together and the exit
    code has to follow from the whole set rather than from the last one.
    """

    stem: str
    ok: bool
    message: str = ""
    log: str = ""


@dataclass
class CheckReport:
    """The differences a ``--check`` run found between the committed twins and fresh recordings.

    Three kinds, because they call for three different fixes: a twin whose content moved, a setup
    with no committed twin at all, and a simulation-parameters file the recording needed and the
    repository does not have. Collected together so that one run tells a contributor everything
    they have to regenerate rather than one thing at a time.
    """

    changed: List[Tuple[str, str]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    new_parameters: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Whether the committed files are exactly what recording produces.

        Returns:
            ``True`` when nothing changed, nothing is missing and no parameter file was needed.
        """
        return not (self.changed or self.missing or self.new_parameters)


class SetupDiscovery:
    """Which setups a run covers, and the refusal when a caller names one that does not exist.

    Selection is purely additive on purpose. There is no exclusion list and no way to add one, so
    the set of setups this script covers is the set of setups that exist, and the only way to
    leave one out is to delete it.
    """

    @classmethod
    def all_setups(cls) -> List[Path]:
        """Every setup module in the repository, in a stable order.

        Returns:
            The setup paths, sorted by file name.
        """
        return sorted(
            path for path in Paths.SETUPS.glob("*.py") if path.name != Paths.NOT_A_SETUP
        )

    @classmethod
    def select(cls, only: Optional[Sequence[str]]) -> List[Path]:
        """Restricts a run to the named setups, or covers every one of them.

        Args:
            only: Setup stems, with or without the ``.py`` suffix; ``None`` selects all.

        Returns:
            The selected setup paths.

        Raises:
            SystemExit: If a named stem is not a setup.
        """
        candidates = cls.all_setups()
        if not only:
            return candidates
        wanted = {name[:-3] if name.endswith(".py") else name for name in only}
        chosen = [path for path in candidates if path.stem in wanted]
        missing = wanted - {path.stem for path in chosen}
        if missing:
            raise SystemExit(f"--only names not found as setups: {sorted(missing)}")
        return chosen


class Recorder:
    """Runs one setup's recording in its own interpreter and reports what came back.

    The child is this repository's own command line rather than a worker module of this script,
    so that the fleet-wide run and a contributor recording a single setup by hand go through
    exactly the same code and cannot drift apart.
    """

    #: The command that records one setup, completed with the three paths.
    COMMAND = ("-m", "hisim.cli", "energy-system", "record")

    #: Environment variable naming the local load-profile-generator working directory. The runs
    #: are sequential, so one index serves all of them; it is set explicitly rather than left to
    #: the default so that a machine with a stale setting records the same thing as a clean one.
    LPG_INDEX_VARIABLE = "HISIM_LOCAL_LPG_CALC_INDEX"

    #: The index handed to every child.
    LPG_INDEX = "1"

    @classmethod
    def record(cls, setup: Path, parameters: Path, out_dir: Path, python: str) -> SetupOutcome:
        """Records one setup in a subprocess.

        Args:
            setup: The setup module to record.
            parameters: The simulation-parameters file the setup is started from.
            out_dir: Where the recorded file goes.
            python: The interpreter to run the recorder with.

        Returns:
            The outcome, carrying the child's output when it failed.
        """
        environment = dict(os.environ)
        environment[cls.LPG_INDEX_VARIABLE] = cls.LPG_INDEX
        completed = subprocess.run(  # nosec B603 - fixed argument vector, no shell
            [python, *cls.COMMAND, str(setup), str(parameters), "--out", str(out_dir)],
            cwd=str(Paths.REPO_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return SetupOutcome(stem=setup.stem, ok=True)
        return SetupOutcome(
            stem=setup.stem,
            ok=False,
            message=(
                f"the recorder exited with {completed.returncode}; any partial file it left in "
                f"{out_dir} is there for inspection and must not be committed"
            ),
            log=cls.tail(completed.stdout + completed.stderr),
        )

    @classmethod
    def record_all(cls, setups: Sequence[Path], parameters: Path, out_dir: Path, python: str) -> List[SetupOutcome]:
        """Records every setup in turn, printing each verdict as it arrives.

        Sequential, and printing as it goes rather than at the end, because a fleet-wide run takes
        minutes per setup: a caller watching it needs to see progress, and the next setup needs to
        see any simulation-parameters file its predecessor had to write.

        Args:
            setups: The setups to record.
            parameters: The simulation-parameters file each setup is started from.
            out_dir: Where the recorded files go.
            python: The interpreter to run the recorder with.

        Returns:
            One outcome per setup, in the order they were recorded.
        """
        outcomes: List[SetupOutcome] = []
        for index, setup in enumerate(setups, start=1):
            outcome = cls.record(setup, parameters, out_dir, python)
            outcomes.append(outcome)
            status = "OK  " if outcome.ok else "FAIL"
            print(f"[{index}/{len(setups)}] {status} {outcome.stem}", flush=True)
        return outcomes

    @classmethod
    def tail(cls, output: str, lines: int = 20) -> str:
        """Keeps the last lines of a failed child's output for the summary.

        Args:
            output: Everything the child wrote.
            lines: How many trailing lines to keep.

        Returns:
            The tail, or a note when the child wrote nothing.
        """
        kept = [line for line in output.splitlines() if line.strip()][-lines:]
        return "\n".join(kept) if kept else "(the recorder wrote nothing)"


class FreshnessCheck:
    """Re-records every setup into a throwaway directory and compares with what is committed.

    This is the whole of the freshness rule: recording is deterministic, so a committed twin that
    differs from a fresh recording means the setup changed and the twin did not. The comparison is
    on the exact bytes, header comments included, because a header naming a different parameters
    file is as much a change as a moved number.

    The throwaway directory is created inside the repository rather than in the system temporary
    area, and exactly one level deep, so that the schema comment a recorded file opens with — which
    is written relative to the directory holding the file — comes out identical to the committed
    one. It is removed whatever happens.
    """

    @classmethod
    def run(cls, setups: Sequence[Path], parameters: Path, python: str) -> Tuple[List[SetupOutcome], CheckReport]:
        """Records every setup into a temporary directory and diffs the results.

        Args:
            setups: The setups to record.
            parameters: The simulation-parameters file each setup is started from.
            python: The interpreter to run the recorder with.

        Returns:
            The per-setup outcomes and the report of everything that differs.
        """
        scratch = Path(tempfile.mkdtemp(prefix=Paths.CHECK_PREFIX, dir=Paths.REPO_ROOT))
        try:
            outcomes = Recorder.record_all(setups, parameters, scratch, python)
            return outcomes, cls.compare(outcomes, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    @classmethod
    def compare(cls, outcomes: Sequence[SetupOutcome], scratch: Path) -> CheckReport:
        """Compares fresh recordings with the committed twins.

        Only the setups that recorded are compared. One that did not is already a failure of the
        run, and calling its twin missing as well would report one defect as two.

        Args:
            outcomes: What each setup produced.
            scratch: The directory they were recorded into.

        Returns:
            The report.
        """
        report = CheckReport()
        for outcome in outcomes:
            if not outcome.ok:
                continue
            fresh = scratch / f"{outcome.stem}{Paths.RECORDED_SUFFIX}"
            committed = Paths.ENERGY_SYSTEMS / f"{outcome.stem}{Paths.RECORDED_SUFFIX}"
            if not fresh.exists():
                continue
            if not committed.exists():
                report.missing.append(outcome.stem)
                continue
            fresh_text = fresh.read_text(encoding="utf-8")
            committed_text = committed.read_text(encoding="utf-8")
            if fresh_text != committed_text:
                report.changed.append((outcome.stem, cls.diff(committed_text, fresh_text, outcome.stem)))
        report.new_parameters = sorted(
            path.name for path in scratch.glob(Paths.PARAMETERS_PATTERN)
        )
        return report

    @classmethod
    def diff(cls, committed: str, fresh: str, stem: str) -> str:
        """Renders the difference between a committed twin and its fresh recording.

        Args:
            committed: The committed file's text.
            fresh: The freshly recorded text.
            stem: The setup's stem, used to label both sides.

        Returns:
            A unified diff.
        """
        return "".join(
            difflib.unified_diff(
                committed.splitlines(keepends=True),
                fresh.splitlines(keepends=True),
                fromfile=f"committed/{stem}{Paths.RECORDED_SUFFIX}",
                tofile=f"recorded/{stem}{Paths.RECORDED_SUFFIX}",
            )
        )


class DuplicateParameterCheck:
    """Asserts that no two committed simulation-parameters files say the same thing.

    The recorder never writes a duplicate, but a person editing the directory by hand can, and a
    duplicate is worse than untidy: two recordings describing the same run would point at
    different files, and a reader comparing them would look for a difference that is not there.
    The check therefore lives with the freshness job rather than only inside the recorder.
    """

    @classmethod
    def run(cls) -> List[str]:
        """Finds committed parameter files with equal normalised content.

        Returns:
            One message per offending pair; empty when the directory is clean.
        """
        sys.path.insert(0, str(Paths.REPO_ROOT))
        from hisim.energy_system.recording.parameters import (  # noqa: PLC0415
            ParameterFileLibrary,
        )

        return [
            f"{first.name} and {second.name} describe the same run"
            for first, second in ParameterFileLibrary.duplicates(Paths.ENERGY_SYSTEMS)
        ]


class Report:
    """Everything a run prints, gathered so the two modes read the same and differ only in verdict.

    Printing is separated from doing for the usual reason — a test can drive the run and inspect
    the outcomes without parsing text — and because both modes end in the same summary of which
    setups could not be recorded, which is the part R5.4 makes load-bearing.
    """

    @classmethod
    def outcomes(cls, outcomes: Sequence[SetupOutcome]) -> None:
        """Prints the failures of a run in full, naming every setup that could not be recorded.

        Args:
            outcomes: What each setup produced.
        """
        failures = [outcome for outcome in outcomes if not outcome.ok]
        if not failures:
            return
        print(f"\n{len(failures)} setup(s) could not be recorded:")
        for outcome in failures:
            print(f"\n  {outcome.stem}: {outcome.message}")
            for line in outcome.log.splitlines():
                print(f"    {line}")

    @classmethod
    def check(cls, report: CheckReport) -> None:
        """Prints what a ``--check`` run found.

        Args:
            report: The differences found.
        """
        for stem, diff in report.changed:
            print(f"\nThe committed twin of {stem} is out of date:")
            print(diff)
        for stem in report.missing:
            print(f"\n{stem} has no committed twin in energy_systems/.")
        for name in report.new_parameters:
            print(
                f"\nRecording needed a simulation-parameters file that is not committed: {name}."
            )
        if report.clean:
            print("\nEvery committed twin is exactly what recording produces.")
        else:
            print("\nRun 'python scripts/record_all_setups.py' and commit what it writes.")


def main(argv: Optional[List[str]] = None) -> int:
    """Records every setup, or checks that the committed recordings are still current.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        ``0`` when every setup recorded and, in check mode, nothing differed; ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="re-record into a temporary directory and fail on any difference to what is committed",
    )
    parser.add_argument("--only", nargs="+", metavar="SETUP", help="record only these setup stems")
    parser.add_argument(
        "--parameters",
        type=Path,
        default=Paths.DEFAULT_PARAMETERS,
        help=f"the simulation-parameters file each setup is started from (default: {Paths.DEFAULT_PARAMETERS.name})",
    )
    parser.add_argument(
        "--python", default=sys.executable, help="the interpreter to run the recorder with"
    )
    arguments = parser.parse_args(argv)

    setups = SetupDiscovery.select(arguments.only)
    print(f"Setups to record ({len(setups)}):")
    for setup in setups:
        print(f"  - {setup.stem}")
    print()

    if arguments.check:
        outcomes, report = FreshnessCheck.run(setups, arguments.parameters, arguments.python)
        Report.outcomes(outcomes)
        Report.check(report)
        duplicates = DuplicateParameterCheck.run()
        for message in duplicates:
            print(f"\nTwo simulation-parameters files normalise equal: {message}.")
        failed = [outcome for outcome in outcomes if not outcome.ok]
        return 1 if failed or not report.clean or duplicates else 0

    outcomes = Recorder.record_all(setups, arguments.parameters, Paths.ENERGY_SYSTEMS, arguments.python)
    Report.outcomes(outcomes)
    failed = [outcome for outcome in outcomes if not outcome.ok]
    print(f"\nDONE: {len(outcomes) - len(failed)} recorded, {len(failed)} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
