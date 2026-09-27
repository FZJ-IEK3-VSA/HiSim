"""Shared pytest fixtures for the whole suite.

The autouse ``guard_against_stray_files`` fixture fails any test module that leaves the git
working tree dirtier than it found it - i.e. creates, modifies or deletes a file that
git would track, outside the dedicated result directory. This keeps accidental
artifacts out of merge requests. It takes one ``git status`` before and one after each
module (not each test), so a failure names the module; re-running that module alone
finds the test.

Files git already ignores (__pycache__, *.pyc, caches, logs, the various results/ and
tests/test/ dirs - see .gitignore) are intentionally not flagged: they can never reach
an MR. The default result dir <repo>/results is NOT gitignored, so it is excluded
explicitly via the ResultPathProviderSingleton.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator
from typing import Optional, Set

import pytest

from hisim.caching import CacheSettings
from hisim.result_path_provider import ResultPathProviderSingleton

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_cache_environment() -> Iterator[None]:
    """Clears every ``HISIM_CACHE_*`` variable, so the suite runs the same on every machine.

    ``hisim.utils.get_cache_file`` reads these variables on every call; a developer with
    ``HISIM_CACHE_DIR`` in a ``.env`` or exported shell would otherwise have cache paths land outside
    pytest's ``tmp_path`` and see spurious failures. Tests that exercise an override set the variable
    themselves with ``monkeypatch.setenv``, which applies after this fixture.

    The fixture builds its own ``MonkeyPatch`` instead of requesting the ``monkeypatch`` fixture.
    Requesting it would instantiate the shared function-scoped instance before every later fixture,
    and teardown runs in reverse, so later fixtures would tear down while a test's own patches --
    some of which replace ``subprocess.run`` globally -- are still active. (The module-scoped
    stray-file guard runs its ``git status`` outside every function-scoped fixture anyway, through a
    ``subprocess.run`` it bound at import.)
    """
    patcher = pytest.MonkeyPatch()
    for attribute in vars(CacheSettings.Variables).values():
        if isinstance(attribute, str) and attribute.startswith("HISIM_CACHE"):
            patcher.delenv(attribute, raising=False)
    yield
    patcher.undo()


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


# Bound at import, so a test that monkeypatches ``subprocess.run`` cannot reach the guard's call.
_run_subprocess = subprocess.run


def _git_status() -> Set[str]:
    """Return the set of porcelain status lines for the working tree (untracked files included)."""
    output = _run_subprocess(
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


@dataclass
class _ModuleWindow:
    """What the stray-file guard knows about the module it watches."""

    module: str
    start: float
    worker: Optional[str]
    result_dirs: Set[Path] = field(default_factory=set)
    last_test: Optional[str] = None


def _overlapping_modules(own: dict, others: list[dict]) -> list[str]:
    """Modules on other xdist workers whose run window overlapped ``own``.

    Each window is ``{"module", "worker", "start", "end"}``; ``end`` is ``None`` while a module still
    runs. Pure, so the attribution rule is testable without xdist.
    """
    own_end = own["end"] if own["end"] is not None else float("inf")
    suspects = []
    for other in others:
        if other["worker"] == own["worker"]:
            continue
        other_end = other["end"] if other["end"] is not None else float("inf")
        if other["start"] <= own_end and own["start"] <= other_end:
            suspects.append(f"{other['module']} (worker {other['worker']})")
    return sorted(suspects)


def _ledger_file(ledger: Path, worker: str, module: str) -> Path:
    """The ledger file that holds one worker's window for one module."""
    return ledger / f"{worker}__{module.replace('/', '__').replace(':', '_')}.json"


def _write_window(ledger: Path, window: _ModuleWindow, end: Optional[float]) -> None:
    """Record a module's run window (``end`` None while it runs) for the other xdist workers to read."""
    ledger.mkdir(parents=True, exist_ok=True)
    record = {"module": window.module, "worker": window.worker, "start": window.start, "end": end}
    _ledger_file(ledger, str(window.worker), window.module).write_text(json.dumps(record), encoding="utf-8")


def _concurrent_modules_text(ledger: Optional[Path], window: _ModuleWindow, end: float) -> str:
    """The failure message's paragraph on other xdist workers; empty when not under xdist."""
    if ledger is None:
        return ""
    others = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(ledger.glob("*.json"))]
    own = {"module": window.module, "worker": window.worker, "start": window.start, "end": end}
    suspects = _overlapping_modules(own, others)
    if not suspects:
        return f"Running under pytest-xdist (worker {window.worker}); no other worker ran a module meanwhile.\n\n"
    listing = "\n".join(f"  {suspect}" for suspect in suspects)
    return (
        f"Running under pytest-xdist (worker {window.worker}). These modules ran on other workers at the same "
        f"time; the file may be theirs, and they fail the same way if it is:\n{listing}\n\n"
    )


@pytest.fixture(scope="module", autouse=True)
def guard_against_stray_files(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[_ModuleWindow]:
    """Fail the module if its tests leave stray files outside the result directory.

    One ``git status --porcelain --untracked-files=all`` before the module's first test and one after
    its last: two per module instead of two per test. A path that is new in the after snapshot, not
    inside a result directory any of the module's tests used (``_record_result_dir`` collects them
    per test), is stray. A module-scoped fixture's teardown error is reported by pytest as an error
    of the module's last test, so the message names the module, says that the last test is only
    where the error surfaced, and gives the command that re-runs the module alone.

    Under pytest-xdist (``--dist loadfile``, a module's tests all on one worker) the guard compares
    only this module's own before and after snapshots, never a session-wide start state, so files
    other workers leave behind before or after this module's window are not counted. Attribution
    rule inside the window: git cannot say which process wrote a file, so a stray path that appears
    while this module runs is counted against it even when a concurrent module on another worker
    wrote it. The guarantee therefore holds (the writer's own window always contains the write, so
    it always fails), but a concurrent innocent module can fail alongside it. To make that visible,
    each worker records its module windows in a ledger next to the workers' base temp directories,
    and a failure under xdist lists every module that overlapped this one on another worker. A fully
    safe rule (never blaming an innocent module) is not possible with ``git status`` alone; it would
    need per-process write tracing, which subprocesses spawned by tests escape. One case goes only
    against the innocent: a module that writes a tracked-looking file into the checkout and removes it
    again passes itself, while a concurrent module whose after snapshot catches the file in flight
    fails; a serial re-run of the named module settles it, and the cure is the same as for a real
    stray file (write into ``tmp_path`` or the result directory).
    """
    module_path = request.node.nodeid or str(request.path)
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    ledger = tmp_path_factory.getbasetemp().parent / "stray_guard_windows" if worker else None
    window = _ModuleWindow(module=module_path, start=time.time(), worker=worker)
    if ledger is not None:
        _write_window(ledger, window, end=None)
    before = _git_status()
    yield window
    after = _git_status()
    end = time.time()
    if ledger is not None:
        _write_window(ledger, window, end=end)
    result_dir = _result_dir()
    other_result_dirs = window.result_dirs - {result_dir}
    stray = [
        line
        for line in _compute_stray(before=before, after=after, result_dir=result_dir)
        if not any(_is_in_result_dir(_path_of(line), allowed) for allowed in other_result_dirs)
    ]
    if _should_fail(stray):
        stray_text = "\n".join(stray)
        pytest.fail(
            f"Stray-file guard: module {module_path} left files outside the result directory "
            f"(would pollute a merge request):\n{stray_text}\n\n"
            f"The guard checks once per module, so pytest reports this as an error in the teardown of the "
            f"module's last test ({window.last_test}), which is not necessarily the test that wrote the file. "
            f"Re-run the module alone to find it:\n  pytest {module_path}\n\n"
            f"{_concurrent_modules_text(ledger, window, end)}"
            f"{_stray_file_diagnostics(stray_status_lines=stray, result_dir=result_dir)}\n\n"
            f"Write into the ResultPathProviderSingleton result directory (or tmp_path), "
            f"or add a legitimate output location to .gitignore.",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _record_result_dir(request: pytest.FixtureRequest) -> Iterator[None]:
    """Remember, per test and without a subprocess, the result directory it was allowed to write to.

    The module guard excludes every one of them, as the per-test guard did, even when a later test
    of the module points the ``ResultPathProviderSingleton`` elsewhere.
    """
    window: _ModuleWindow = request.getfixturevalue("guard_against_stray_files")
    yield
    window.result_dirs.add(_result_dir())
    window.last_test = request.node.nodeid
