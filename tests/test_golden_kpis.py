"""Unit tests for ``scripts/golden_kpis.py`` — flattening and KPI comparison.

Both helpers are pure and depend only on their arguments (and the module tolerance
constants), so these tests pin behaviour without touching stored goldens or
running HiSim.
"""
from __future__ import annotations

import pytest

from scripts.golden_kpis import ABS_TOL, REL_TOL, compare, flatten

pytestmark = pytest.mark.base


# --------------------------------------------------------------------------- #
# flatten()
# --------------------------------------------------------------------------- #
def test_flatten_nested_all_kpis_structure() -> None:
    """The HiSim ``{building:{category:{kpi:{'value':...}}}}`` tree flattens to dotted keys."""
    raw = {
        "BUI1": {
            "General": {"Total electricity consumption": {"value": 123.4, "unit": "kWh"}},
            "Battery": {"Losses": {"value": 5, "unit": "kWh"}},
        }
    }
    flat = flatten(raw)
    assert flat == {
        "BUI1.General.Total electricity consumption": 123.4,
        "BUI1.Battery.Losses": 5.0,
    }


def test_flatten_coerces_numbers_to_float() -> None:
    """Integer KPI values are coerced to float; non-numerics are preserved."""
    flat = flatten({"B": {"C": {"k_int": {"value": 7}, "k_str": {"value": "PEM"}}}})
    assert flat["B.C.k_int"] == 7.0
    assert isinstance(flat["B.C.k_int"], float)
    assert flat["B.C.k_str"] == "PEM"


def test_flatten_bool_kept_as_bool() -> None:
    """Booleans are not treated as numbers (compared exactly downstream)."""
    flat = flatten({"B": {"C": {"flag": {"value": True}}}})
    assert flat["B.C.flag"] is True


def test_flatten_value_that_is_dict_is_not_a_leaf() -> None:
    """A node whose ``value`` is itself a dict is recursed into, not taken as a leaf."""
    flat = flatten({"B": {"value": {"nested": {"value": 1.0}}}})
    assert flat == {"B.value.nested": 1.0}


def test_flatten_empty() -> None:
    """An empty KPI tree flattens to an empty mapping."""
    assert not flatten({})


# --------------------------------------------------------------------------- #
# compare()
# --------------------------------------------------------------------------- #
def test_compare_exact_match_no_errors() -> None:
    """Identical KPI maps compare without errors."""
    assert not compare("x", {"a": 1.0}, {"a": 1.0})


def test_compare_within_tolerance_no_errors() -> None:
    """A difference within tolerance is not reported."""
    assert not compare("x", {"a": 1.0 + 1e-12}, {"a": 1.0})


def test_compare_outside_tolerance_reports_change() -> None:
    """A difference beyond tolerance is reported with its absolute and relative magnitude."""
    msgs = compare("x", {"a": 2.0}, {"a": 1.0})
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.startswith("x: KPI 'a' changed: ref=1.0 got=2.0")
    assert "abs diff=1" in msg
    assert "rel diff=100.000%" in msg
    assert "tolerance rel=1e-09" in msg


def test_compare_missing_kpi() -> None:
    """A KPI present in the reference but absent from the run is reported missing."""
    assert compare("x", {}, {"a": 1.0}) == ["x: missing KPI 'a' in current run"]


def test_compare_new_kpi() -> None:
    """A KPI present in the run but absent from the reference is reported new."""
    assert compare("x", {"a": 1.0}, {}) == [
        "x: new KPI 'a' not in reference (regenerate if intended)"
    ]


def test_compare_missing_precede_new_and_new_sorted() -> None:
    """Missing-KPI messages precede new-KPI ones, and new KPIs are sorted."""
    errs = compare("x", {"z": 1.0, "b": 2.0}, {"a": 1.0})
    assert errs == [
        "x: missing KPI 'a' in current run",
        "x: new KPI 'b' not in reference (regenerate if intended)",
        "x: new KPI 'z' not in reference (regenerate if intended)",
    ]


def test_compare_non_numeric_exact() -> None:
    """Non-numeric KPI values are compared for exact equality."""
    assert not compare("x", {"a": "PEM"}, {"a": "PEM"})
    assert compare("x", {"a": "AEM"}, {"a": "PEM"}) == ["x: KPI 'a' changed: ref='PEM' got='AEM'"]


def test_compare_zero_values_equal() -> None:
    """Two zero KPI values compare equal without a false relative-tolerance hit."""
    assert not compare("x", {"a": 0.0}, {"a": 0.0})


def test_compare_uses_module_tolerance_defaults() -> None:
    """The default tolerance is the tight 1e-9 policy value."""
    assert REL_TOL == 1e-9
    assert ABS_TOL == 0.0
    # A difference just above rel_tol is flagged.
    assert compare("x", {"a": 1.0 + 1e-6}, {"a": 1.0})
