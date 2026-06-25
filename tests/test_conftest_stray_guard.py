"""Unit tests for the pure stray-detection helpers extracted from the guard fixture.

``guard_against_stray_files`` (in ``tests/conftest.py``) used to inline the only logic
worth testing - the ``after - before`` set difference, the ``_is_in_result_dir``
filtering, and the stray/allowed decision - behind two live ``_git_status()`` subprocess
calls, so it could only be exercised by running real ``git`` and mutating the working
tree. That logic now lives in the pure helpers ``_compute_stray`` and ``_should_fail``;
these tests cover them with canned status sets, no git, no filesystem mutation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Set

import pytest

from tests.conftest import REPO_ROOT, _compute_stray, _should_fail

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
