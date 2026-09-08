#!/usr/bin/env python3
"""Emit the GitHub Actions matrix of every (setup, window) triple the parity rig can run.

TEMPORARY — this script belongs to the P3 migration parity rig (requirements R11) and is deleted
with it in phase P6 (R11.8 amended and AC-P3.20 deferred to P6, 2026-08-31).

The matrix is derived from the files that exist rather than from a configuration list, because the
rig's coverage is exactly "every setup that has been recorded" and a second list would be one more
thing to forget. A setup with no recorded twin is not silently skipped by this: the recording
driver already fails and names it, so there is nothing for the rig to add.

Deliberately standard-library only, like ``golden_matrix.py``: this runs in the lightweight
discover job before any dependency is installed, so it cannot import HiSim. The price is that the
window names appear here as well as in ``p3_parity_runs.py``, and a test asserts the two agree.
Being the one module of the rig everything else may import, this is also where the window fence
lives: :class:`MatrixPaths` knows which windows are runnable and refuses the rest with a reason,
and the runner and the checker ask it rather than keeping a second answer.

Examples
--------
    python scripts/p3_parity_matrix.py
    python scripts/p3_parity_matrix.py --setup basic_household --window january
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


class MatrixPaths:
    """Where this script looks for the setups and their recorded twins.

    Stated as class constants rather than as module-level names so that a reader sees the whole of
    what the discover job depends on at once, and so the removal PR can find it.
    """

    #: Root of the repository, found from this file rather than from the working directory.
    REPO_ROOT = Path(__file__).resolve().parent.parent

    #: Where the Python setups live.
    SETUPS = REPO_ROOT / "system_setups"

    #: Where the recorded twins live.
    ENERGY_SYSTEMS = REPO_ROOT / "energy_systems"

    #: Suffix of a recorded twin.
    RECORDED_SUFFIX = ".energy_system.yaml"

    #: The module of ``system_setups/`` that is not a setup.
    NOT_A_SETUP = "__init__.py"

    #: Every window the rig has a definition for, runnable or not. Mirrors
    #: ``ParityWindows.FACTORIES``; a test asserts the two stay equal, because this module may not
    #: import HiSim to ask.
    WINDOWS = ("january", "july")

    #: The windows whose definition is kept but which the rig refuses to run. Mid-year start dates
    #: do not work anywhere in HiSim: every profile-driven component — weather, PV, building, the
    #: LPG occupancy, cars, smart devices, the CSV loader — indexes its year-long profile from
    #: timestep 0 as if that were the 1st of January, whatever the start date says. A July run
    #: therefore either reproduces January's numbers or, where a component does honour the date
    #: (a solar-thermal collector's sun position), pairs July sun with January irradiance. The
    #: fence is a fence rather than a deletion because the mid-year-start epic removes it again.
    FENCED_WINDOWS = ("july",)

    #: Where the fence's refusal sends the reader.
    FENCE_ROADMAP = "roadmap/midyear_start_epic.md"

    @classmethod
    def runnable_windows(cls) -> Tuple[str, ...]:
        """The windows a dispatch may actually run, in the order of :attr:`WINDOWS`.

        Returns:
            Every defined window that is not fenced.
        """
        return tuple(window for window in cls.WINDOWS if window not in cls.FENCED_WINDOWS)

    @classmethod
    def refuse_fenced(cls, windows: Sequence[str]) -> None:
        """Refuses a request for a fenced window, saying what is broken and where it is tracked.

        A fenced window is refused rather than quietly dropped, because a dispatch that silently
        ran fewer triples than it was asked for would report a green table over coverage nobody
        checked. The refusal is one sentence about the defect and one pointer, so whoever asked
        for July learns why the answer would have been January's numbers.

        Args:
            windows: The windows a caller asked for.

        Raises:
            ValueError: If any of them is fenced.
        """
        fenced = sorted({window for window in windows if window in cls.FENCED_WINDOWS})
        if not fenced:
            return
        raise ValueError(
            f"The window(s) {fenced} are fenced out of the parity rig: a mid-year window reads "
            "January's profiles today, because every profile-driven component indexes its "
            "year-long profile from timestep 0 as if that were the 1st of January, whatever the "
            "start date says. The window definitions are kept for the mid-year-start epic, which "
            f"unfences them; see {cls.FENCE_ROADMAP}. Runnable window(s): "
            f"{list(cls.runnable_windows())}."
        )


def covered_setups(root: Optional[Path] = None) -> List[str]:
    """The setups the rig covers: those with a Python module and a recorded twin beside it.

    Args:
        root: Repository root to look in; the real one when omitted.

    Returns:
        The setup stems, sorted.
    """
    base = MatrixPaths.REPO_ROOT if root is None else Path(root)
    setups = base / MatrixPaths.SETUPS.name
    twins = base / MatrixPaths.ENERGY_SYSTEMS.name
    return sorted(
        path.stem
        for path in setups.glob("*.py")
        if path.name != MatrixPaths.NOT_A_SETUP
        and (twins / f"{path.stem}{MatrixPaths.RECORDED_SUFFIX}").exists()
    )


def build_matrix(
    setups: Optional[Sequence[str]] = None,
    windows: Optional[Sequence[str]] = None,
    root: Optional[Path] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Builds the matrix a workflow consumes with ``fromJSON``.

    Args:
        setups: Restrict to these setup stems; every covered setup when omitted or empty.
        windows: Restrict to these windows; every runnable window when omitted or empty.
        root: Repository root to look in; the real one when omitted.

    Returns:
        ``{"include": [{"setup": ..., "window": ...}, ...]}``.

    Raises:
        ValueError: If a named setup is not covered, if a named window does not exist — a typo in
            a hand-dispatched run has to fail loudly rather than quietly run nothing — or if a
            named window is fenced, which :meth:`MatrixPaths.refuse_fenced` explains.
    """
    available = covered_setups(root)
    chosen = list(available) if not setups else [stem for stem in available if stem in set(setups)]
    if setups:
        unknown = sorted(set(setups) - set(available))
        if unknown:
            raise ValueError(f"No recorded twin for {unknown}; covered setups are {available}.")
    chosen_windows = list(MatrixPaths.runnable_windows()) if not windows else list(windows)
    unknown_windows = sorted(set(chosen_windows) - set(MatrixPaths.WINDOWS))
    if unknown_windows:
        raise ValueError(f"Unknown window(s) {unknown_windows}; choose from {list(MatrixPaths.WINDOWS)}.")
    MatrixPaths.refuse_fenced(chosen_windows)
    return {
        "include": [
            {"setup": stem, "window": window} for stem in chosen for window in chosen_windows
        ]
    }


def parse_arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parses the command line of one matrix emission.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--setup", nargs="+", metavar="STEM", help="restrict to these setups")
    # Every defined window stays a valid choice, fenced ones included: argparse would otherwise
    # answer a request for July with "invalid choice", and the point of the fence is that whoever
    # asks for July is told what is wrong with it and where that is being fixed.
    parser.add_argument(
        "--window",
        nargs="+",
        choices=list(MatrixPaths.WINDOWS),
        help=f"restrict to these windows (default: {' '.join(MatrixPaths.runnable_windows())})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Prints the matrix as one compact JSON line, ready for ``$GITHUB_OUTPUT``.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        ``0``.

    Raises:
        SystemExit: If the matrix is empty — GitHub reports a matrix job that expanded to zero
            cells as a success, so a dispatch over no recorded twins would otherwise turn the
            whole workflow green while testing nothing — or if the request was refused, which a
            discover job should read as one message rather than as a traceback.
    """
    arguments = parse_arguments(argv)
    try:
        matrix = build_matrix(arguments.setup, arguments.window)
    except ValueError as refusal:
        raise SystemExit(str(refusal)) from refusal
    if not matrix["include"]:
        raise SystemExit("The matrix is empty: no setup has a recorded twin, so the rig would cover nothing.")
    print(json.dumps(matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
