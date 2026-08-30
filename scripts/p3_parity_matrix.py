#!/usr/bin/env python3
"""Emit the GitHub Actions matrix of every (setup, window) triple the parity rig can run.

TEMPORARY — this script belongs to the P3 migration parity rig (requirements R11) and is deleted
with it in P3's last PR (R11.8, AC-P3.20).

The matrix is derived from the files that exist rather than from a configuration list, because the
rig's coverage is exactly "every setup that has been recorded" and a second list would be one more
thing to forget. A setup with no recorded twin is not silently skipped by this: the recording
driver already fails and names it, so there is nothing for the rig to add.

Deliberately standard-library only, like ``golden_matrix.py``: this runs in the lightweight
discover job before any dependency is installed, so it cannot import HiSim. The price is that the
window names appear here as well as in ``p3_parity_runs.py``, and a test asserts the two agree.

Examples
--------
    python scripts/p3_parity_matrix.py
    python scripts/p3_parity_matrix.py --setup basic_household --window july
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence


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

    #: The windows every triple is measured over. Mirrors ``ParityWindows.FACTORIES``; a test
    #: asserts the two stay equal, because this module may not import HiSim to ask.
    WINDOWS = ("january", "july")


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
        windows: Restrict to these windows; both when omitted or empty.
        root: Repository root to look in; the real one when omitted.

    Returns:
        ``{"include": [{"setup": ..., "window": ...}, ...]}``.

    Raises:
        ValueError: If a named setup is not covered, or a named window does not exist — a typo in
            a hand-dispatched run has to fail loudly rather than quietly run nothing.
    """
    available = covered_setups(root)
    chosen = list(available) if not setups else [stem for stem in available if stem in set(setups)]
    if setups:
        unknown = sorted(set(setups) - set(available))
        if unknown:
            raise ValueError(f"No recorded twin for {unknown}; covered setups are {available}.")
    chosen_windows = list(MatrixPaths.WINDOWS) if not windows else list(windows)
    unknown_windows = sorted(set(chosen_windows) - set(MatrixPaths.WINDOWS))
    if unknown_windows:
        raise ValueError(f"Unknown window(s) {unknown_windows}; choose from {list(MatrixPaths.WINDOWS)}.")
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
    parser.add_argument(
        "--window", nargs="+", choices=list(MatrixPaths.WINDOWS), help="restrict to these windows"
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Prints the matrix as one compact JSON line, ready for ``$GITHUB_OUTPUT``.

    Args:
        argv: The command line, defaulting to the process's own.

    Returns:
        ``0``.
    """
    arguments = parse_arguments(argv)
    matrix = build_matrix(arguments.setup, arguments.window)
    print(json.dumps(matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
