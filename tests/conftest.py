"""Shared pytest fixtures for the whole suite.

The autouse ``guard_against_stray_files`` fixture fails any test that leaves the git
working tree dirtier than it found it - i.e. creates, modifies or deletes a file that
git would track, outside the dedicated result directory. This keeps accidental
artifacts out of merge requests.

Files git already ignores (__pycache__, *.pyc, caches, logs, the various results/ and
tests/test/ dirs - see .gitignore) are intentionally not flagged: they can never reach
an MR. The default result dir <repo>/results is NOT gitignored, so it is excluded
explicitly via the ResultPathProviderSingleton.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from collections.abc import Iterator
from typing import Set

import pytest

from hisim.result_path_provider import ResultPathProviderSingleton

REPO_ROOT: Path = Path(__file__).resolve().parent.parent

# Make scripts/ importable so tests can ``from hpc_harness import ...`` at the module top.
# The hpc_harness package uses absolute ``hpc_harness.*`` imports internally, so it must be
# reachable as a top-level package rather than as ``scripts.hpc_harness``.
_SCRIPTS_DIR = str(REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

STATUS_DESCRIPTIONS: dict[str, str] = {
    "??": "untracked",
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "unmerged",
}


def _git_status() -> Set[str]:
    """Return the set of porcelain status lines for the working tree (untracked files included)."""
    output = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line for line in output.splitlines() if line.strip()}


def _path_of(status_line: str) -> str:
    """Extract the (last) path from a porcelain status line, handling renames."""
    path = status_line[3:]
    if " -> " in path:  # rename: "old -> new"
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def _status_description(status_line: str) -> str:
    """Return a human-readable description for a porcelain status line."""
    status = status_line[:2]
    if status in STATUS_DESCRIPTIONS:
        return STATUS_DESCRIPTIONS[status]
    descriptions = [STATUS_DESCRIPTIONS.get(char, char) for char in status.strip()]
    return ", ".join(descriptions) if descriptions else status.strip()


def _result_dir() -> Path:
    """The directory tests are allowed to write into (resolved, may not exist yet).

    The provider returns a fully qualified ``base_path`` that defaults to ``<repo>/results``
    even when a test never configured it, and every run directory it builds (e.g.
    ``results/test/<name>``) lives beneath it - so excluding ``base_path`` covers them all.
    """
    base_path = ResultPathProviderSingleton().base_path or str(REPO_ROOT / "results")
    return Path(base_path).resolve()


def _is_in_result_dir(rel_path: str, result_dir: Path) -> bool:
    """Whether a repo-relative path lives inside the allowed result directory."""
    abs_path = (REPO_ROOT / rel_path).resolve()
    return abs_path == result_dir or result_dir in abs_path.parents


def _stray_file_diagnostics(stray_status_lines: list[str], result_dir: Path) -> str:
    """Build detailed diagnostics for files left behind by a test."""
    lines = [
        "Detailed stray-file diagnostics:",
        f"Repository root: {REPO_ROOT}",
        f"Allowed result directory: {result_dir}",
    ]
    for status_line in stray_status_lines:
        rel_path = _path_of(status_line)
        abs_path = (REPO_ROOT / rel_path).resolve()
        exists = abs_path.exists()
        if exists and abs_path.is_file():
            size = f"{abs_path.stat().st_size} bytes"
        elif exists and abs_path.is_dir():
            size = "directory"
        else:
            size = "missing"
        lines.extend(
            [
                "",
                f"git status: {status_line}",
                f"status: {_status_description(status_line)}",
                f"repo-relative path: {rel_path}",
                f"absolute path: {abs_path}",
                f"exists after test: {exists}",
                f"size/type: {size}",
            ]
        )
    return "\n".join(lines)


def _compute_stray(before: Set[str], after: Set[str], result_dir: Path) -> list[str]:
    """Compute the sorted stray status lines introduced between two git snapshots.

    A line is "stray" when it shows up in ``after`` but not in ``before`` and its path
    does not live inside the allowed ``result_dir``. This is the pure, side-effect-free
    core of :func:`guard_against_stray_files`: it takes already-collected porcelain
    status sets and a resolved result directory and returns the offending lines, so the
    diffing/filtering decision can be unit-tested with canned inputs instead of spawning
    real ``git`` and mutating the working tree.

    Args:
        before: porcelain status lines captured before the test ran.
        after: porcelain status lines captured after the test ran.
        result_dir: the allowed result directory (resolved), typically
            ``ResultPathProviderSingleton().base_path``.

    Returns:
        The stray status lines, sorted lexicographically (mirrors the original fixture
        output so error messages stay deterministic).
    """
    new_lines = after - before
    return sorted(
        line for line in new_lines if not _is_in_result_dir(_path_of(line), result_dir)
    )


def _should_fail(stray: list[str]) -> bool:
    """Whether the guard should fail the test for the given stray lines.

    Pure companion to :func:`_compute_stray`: the guard fails iff at least one stray
    line was produced.
    """
    return bool(stray)


@pytest.fixture(autouse=True)
def guard_against_stray_files() -> Iterator[None]:
    """Fail the test if it leaves stray files outside the result directory."""
    before = _git_status()
    yield
    result_dir = _result_dir()
    stray = _compute_stray(before=before, after=_git_status(), result_dir=result_dir)
    if _should_fail(stray):
        raise AssertionError(
            "Test left files outside the result directory (would pollute a merge request):\n"
            + "\n".join(stray)
            + "\n\n"
            + _stray_file_diagnostics(stray_status_lines=stray, result_dir=result_dir)
            + "\n\nWrite into the ResultPathProviderSingleton result directory, "
            "or add a legitimate output location to .gitignore."
        )
    print("No stray files left behind!")
