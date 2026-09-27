"""Unit tests for the pure stray-detection helpers extracted from the guard fixture.

``guard_against_stray_files`` (in ``tests/conftest.py``) used to inline the only logic
worth testing - the ``after - before`` set difference, the ``_is_in_result_dir``
filtering, and the stray/allowed decision - behind two live ``_git_status()`` subprocess
calls, so it could only be exercised by running real ``git`` and mutating the working
tree. That logic now lives in the pure helpers ``_compute_stray`` and ``_should_fail``;
these tests cover them with canned status sets, no git, no filesystem mutation. The last test
runs the real module-scoped guard end to end, on a throwaway git repository under ``tmp_path``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Set

import pytest

from tests.conftest import REPO_ROOT, _compute_stray, _overlapping_modules, _should_fail

RESULTS_DIR: Path = (REPO_ROOT / "results").resolve()


@pytest.mark.base
def test_compute_stray_no_changes_returns_empty() -> None:
    """An unchanged working tree produces no stray lines."""
    before: Set[str] = {" M src/a.py", "?? some/untracked.txt"}
    after: Set[str] = set(before)
    assert _compute_stray(before, after, RESULTS_DIR) == []


@pytest.mark.base
def test_compute_stray_new_file_outside_result_dir_is_stray() -> None:
    """A newly-appeared untracked file outside the result dir is flagged."""
    before: Set[str] = set()
    after: Set[str] = {"?? junk/litter.txt"}
    assert _compute_stray(before, after, RESULTS_DIR) == ["?? junk/litter.txt"]


@pytest.mark.base
def test_compute_stray_modified_file_outside_result_dir_is_stray() -> None:
    """A modification (not just creation) introduced during the test is flagged."""
    before: Set[str] = set()
    after: Set[str] = {" M src/changed.py"}
    assert _compute_stray(before, after, RESULTS_DIR) == [" M src/changed.py"]


@pytest.mark.base
def test_compute_stray_new_file_inside_result_dir_is_allowed() -> None:
    """Files written beneath the allowed result dir are not stray."""
    before: Set[str] = set()
    after: Set[str] = {"?? results/run/output.csv"}
    assert _compute_stray(before, after, RESULTS_DIR) == []


@pytest.mark.base
def test_compute_stray_result_dir_path_itself_is_allowed() -> None:
    """A path that resolves exactly to the result dir is allowed (not a parent match)."""
    before: Set[str] = set()
    after: Set[str] = {"?? results"}
    assert _compute_stray(before, after, RESULTS_DIR) == []


@pytest.mark.base
def test_compute_stray_filters_pre_existing_lines() -> None:
    """Lines already dirty before the test are not reported as new strays."""
    before: Set[str] = {" M src/existing.py"}
    after: Set[str] = {" M src/existing.py", "?? junk/new.txt"}
    assert _compute_stray(before, after, RESULTS_DIR) == ["?? junk/new.txt"]


@pytest.mark.base
def test_compute_stray_lost_lines_are_not_stray() -> None:
    """A line that disappears between before/after is not a stray (set difference is one-sided)."""
    before: Set[str] = {" M src/fixed.py"}
    after: Set[str] = set()
    assert _compute_stray(before, after, RESULTS_DIR) == []


@pytest.mark.base
def test_compute_stray_sorts_output() -> None:
    """Stray lines are returned sorted so error messages are deterministic."""
    before: Set[str] = set()
    after: Set[str] = {"?? z.txt", "?? a.txt", "?? results/ok.txt"}
    assert _compute_stray(before, after, RESULTS_DIR) == ["?? a.txt", "?? z.txt"]


@pytest.mark.base
def test_compute_stray_rename_outside_result_dir_is_stray() -> None:
    """A rename whose target lives outside the result dir is flagged (target path is used)."""
    before: Set[str] = set()
    after: Set[str] = {"R  old.py -> relocated/new.py"}
    assert _compute_stray(before, after, RESULTS_DIR) == ["R  old.py -> relocated/new.py"]


@pytest.mark.base
def test_compute_stray_rename_into_result_dir_is_allowed() -> None:
    """A rename whose target lands inside the result dir is not flagged."""
    before: Set[str] = set()
    after: Set[str] = {"R  old.py -> results/run/new.py"}
    assert _compute_stray(before, after, RESULTS_DIR) == []


@pytest.mark.base
def test_compute_stray_mixed_before_after() -> None:
    """Pre-existing, allowed-new and stray-new lines are partitioned correctly together."""
    before: Set[str] = {" M src/pre.py", "?? results/pre_existing.csv"}
    after: Set[str] = {
        " M src/pre.py",
        "?? results/pre_existing.csv",
        "?? results/new.csv",
        "?? junk/stray.txt",
        "A  junk/staged.txt",
    }
    # Sorted lexicographically on the full porcelain line: '?' (0x3F) < 'A' (0x41).
    assert _compute_stray(before, after, RESULTS_DIR) == [
        "?? junk/stray.txt",
        "A  junk/staged.txt",
    ]


@pytest.mark.base
def test_should_fail_empty_is_false() -> None:
    """An empty stray list must not fail the guard."""
    assert _should_fail([]) is False


@pytest.mark.base
def test_should_fail_non_empty_is_true() -> None:
    """Any non-empty stray list must fail the guard."""
    assert _should_fail(["?? junk.txt"]) is True
    assert _should_fail(["?? a.txt", "?? b.txt"]) is True


@pytest.mark.base
def test_guard_decision_logic_matches_fixture_behaviour() -> None:
    """End-to-end check of the extracted decision: allowed paths => no failure."""
    before: Set[str] = set()
    after: Set[str] = {"?? results/anything/output.csv"}
    stray = _compute_stray(before=before, after=after, result_dir=RESULTS_DIR)
    assert _should_fail(stray) is False


@pytest.mark.base
def test_overlapping_modules_names_only_other_workers_that_overlap() -> None:
    """Under xdist a failure lists the modules other workers ran during this module's window."""
    own = {"module": "tests/test_a.py", "worker": "gw0", "start": 10.0, "end": 20.0}
    others = [
        {"module": "tests/test_a.py", "worker": "gw0", "start": 10.0, "end": 20.0},
        {"module": "tests/test_b.py", "worker": "gw1", "start": 15.0, "end": 25.0},
        {"module": "tests/test_c.py", "worker": "gw2", "start": 1.0, "end": 9.0},
        {"module": "tests/test_d.py", "worker": "gw3", "start": 19.0, "end": None},
    ]
    assert _overlapping_modules(own, others) == ["tests/test_b.py (worker gw1)", "tests/test_d.py (worker gw3)"]


_SELFTEST_MODULES = {
    "test_clean.py": "def test_nothing():\n    pass\n",
    "test_dirty.py": (
        "from pathlib import Path\n\n"
        "def test_first():\n    pass\n\n"
        "def test_second():\n"
        "    (Path(__file__).resolve().parent.parent / 'stray_selftest.txt').write_text('x')\n"
    ),
    "test_result_dir.py": (
        "from pathlib import Path\n"
        "from hisim.result_path_provider import ResultPathProviderSingleton\n\n"
        "def test_writes_into_result_dir():\n"
        "    results = Path(__file__).resolve().parent.parent / 'selftest_results'\n"
        "    ResultPathProviderSingleton().base_path = results\n"
        "    results.mkdir()\n"
        "    (results / 'output.csv').write_text('x')\n\n"
        "def test_after_the_provider_moved_on():\n"
        "    ResultPathProviderSingleton().base_path = None\n"
    ),
}


@pytest.mark.base
def test_guard_fails_the_dirty_module_only(tmp_path: Path) -> None:
    """The real guard, run on a throwaway repository: the dirty module fails by name, the others pass.

    The conftest is copied into a fresh git repository under ``tmp_path`` (its ``REPO_ROOT`` follows
    ``__file__``), so the stray file never touches this checkout.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "tests" / "conftest.py", repo / "tests" / "conftest.py")
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    for name, source in _SELFTEST_MODULES.items():
        (repo / "tests" / name).write_text(source)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("PYTEST_XDIST_WORKER", None)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rE"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 1, output
    assert "5 passed, 1 error" in output, output
    assert "ERROR tests/test_dirty.py::test_second" in output, output
    assert "Stray-file guard: module tests/test_dirty.py left files" in output, output
    assert "?? stray_selftest.txt" in output, output
    assert "pytest tests/test_dirty.py" in output, output
    assert "selftest_results" not in output, output
    assert "ERROR tests/test_clean.py" not in output and "ERROR tests/test_result_dir.py" not in output, output
