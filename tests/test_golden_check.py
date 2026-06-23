"""Unit tests for the pure helpers in ``scripts/golden_check.py``.

``compare`` is a self-contained, side-effect-free dict-diff that depends only on
its arguments and the module constants ``REL_TOL`` / ``ABS_TOL``. These tests pin
its behaviour without touching the stored golden references or running HiSim.
The ``run_setup`` self-test branch (``"_dummy"``) is also exercised because it is
deterministic and side-effect-free for that single input.
"""
from __future__ import annotations

import math

import pytest

from scripts.golden_check import ABS_TOL, REL_TOL, compare, run_setup

pytestmark = pytest.mark.base


# --------------------------------------------------------------------------- #
# compare()
# --------------------------------------------------------------------------- #
def test_compare_empty_inputs_returns_no_errors() -> None:
    """Two empty KPI dicts compare equal."""
    assert compare("x", {}, {}) == []


def test_compare_exact_match_returns_no_errors() -> None:
    """Identical KPI dicts compare equal."""
    assert compare("x", {"a": 1.0}, {"a": 1.0}) == []


def test_compare_within_relative_tolerance_returns_no_errors() -> None:
    """A ``got`` value within ``REL_TOL`` of the reference is accepted."""
    assert compare("x", {"a": 1.0 + 1e-12}, {"a": 1.0}) == []


def test_compare_outside_tolerance_reports_change() -> None:
    """A ``got`` value well outside ``REL_TOL`` reports a changed KPI."""
    assert compare("x", {"a": 2.0}, {"a": 1.0}) == [
        "x: KPI 'a' changed: ref=1.0 got=2.0"
    ]


def test_compare_missing_kpi_in_got() -> None:
    """A KPI present in ``ref`` but absent from ``got`` is reported as missing."""
    assert compare("x", {}, {"a": 1.0}) == [
        "x: missing KPI 'a' in current run"
    ]


def test_compare_new_kpi_only_in_got() -> None:
    """A KPI present only in ``got`` is reported as new/unexpected."""
    assert compare("x", {"a": 1.0}, {}) == [
        "x: new KPI 'a' not in reference (regenerate if intended)"
    ]


def test_compare_missing_precedes_new_with_ordering() -> None:
    """Missing-key errors precede new-key errors, in ``ref`` then ``got`` order."""
    errs = compare("x", {"b": 5.0}, {"a": 1.0})
    assert errs == [
        "x: missing KPI 'a' in current run",
        "x: new KPI 'b' not in reference (regenerate if intended)",
    ]
    # Also assert the ordering invariant explicitly in case the wording changes.
    missing_idx = errs.index("x: missing KPI 'a' in current run")
    new_idx = errs.index("x: new KPI 'b' not in reference (regenerate if intended)")
    assert missing_idx < new_idx


def test_compare_zero_values_are_equal() -> None:
    """Two zero KPIs compare equal even with ``ABS_TOL == 0.0``."""
    assert compare("x", {"a": 0.0}, {"a": 0.0}) == []


def test_compare_abs_tol_zero_rejects_tiny_difference_from_zero() -> None:
    """With ``ABS_TOL == 0.0`` a tiny non-zero ``got`` differs from a zero ref."""
    assert ABS_TOL == 0.0
    # math.isclose(1e-9, 0.0, rel_tol=1e-6, abs_tol=0.0) is False because both
    # the relative and absolute tolerances evaluate against zero here.
    assert not math.isclose(1e-9, 0.0, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    errs = compare("x", {"a": 1e-9}, {"a": 0.0})
    assert errs == ["x: KPI 'a' changed: ref=0.0 got=1e-09"]


def test_compare_multiple_changed_kpis_preserve_ref_iteration_order() -> None:
    """Multiple changed KPIs are reported in ``ref`` insertion order."""
    ref = {"a": 1.0, "b": 2.0, "c": 3.0}
    got = {"a": 10.0, "b": 20.0, "c": 30.0}
    assert compare("x", got, ref) == [
        "x: KPI 'a' changed: ref=1.0 got=10.0",
        "x: KPI 'b' changed: ref=2.0 got=20.0",
        "x: KPI 'c' changed: ref=3.0 got=30.0",
    ]


def test_compare_name_propagated_into_messages() -> None:
    """The ``name`` argument is reproduced verbatim in every error message."""
    errs = compare("my_setup", {"b": 5.0}, {"a": 1.0})
    assert all(err.startswith("my_setup:") for err in errs)


# --------------------------------------------------------------------------- #
# run_setup() self-test branch
# --------------------------------------------------------------------------- #
def test_run_setup_dummy_returns_expected_kpis() -> None:
    """The ``_dummy`` self-test branch returns the documented deterministic KPIs."""
    result = run_setup("_dummy")
    assert set(result.keys()) == {"answer", "ratio"}
    assert result["answer"] == pytest.approx(42.0)
    assert result["ratio"] == pytest.approx(1.0 / 3.0)


def test_run_setup_unknown_name_raises_not_implemented() -> None:
    """Any non-dummy name is not yet wired up and must raise ``NotImplementedError``."""
    with pytest.raises(NotImplementedError):
        run_setup("anything_else")
