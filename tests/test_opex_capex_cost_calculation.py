"""Tests for the prepare_row_for_writing_to_table helper.

This module contains unit tests for the module-level function
``prepare_row_for_writing_to_table`` in
``hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation``.
The helper is a pure, side-effect-free function used by both
``opex_calculation`` and ``capex_calculation`` to build summary rows.
"""

# clean

from typing import Dict, Optional

import pytest

from hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation import (
    _accumulate_capex,
    _capex_only_heatpump,
    prepare_row_for_writing_to_table,
)


@pytest.mark.base
def test_prepare_row_for_writing_to_table_typical_multi_key_dict() -> None:
    """A typical multi-key dict yields the name followed by values in insertion order."""
    row = prepare_row_for_writing_to_table(
        "Total",
        {"consumption": 1.5, "co2_emissions": 2.0, "energy_cost": 3.0, "maintenance": 4.0},
    )
    assert row == ["Total", 1.5, 2.0, 3.0, 4.0]
    # Leading element is always the row name.
    assert row[0] == "Total"
    # Value order matches insertion order of the dict.
    assert row[1:] == [1.5, 2.0, 3.0, 4.0]


@pytest.mark.base
def test_prepare_row_for_writing_to_table_empty_dict() -> None:
    """An empty dict yields a single-element list containing only the row name."""
    row = prepare_row_for_writing_to_table("Empty", {})
    assert row == ["Empty"]
    assert len(row) == 1


@pytest.mark.base
def test_prepare_row_for_writing_to_table_single_key_dict() -> None:
    """A single-key dict yields the name followed by the single value."""
    row = prepare_row_for_writing_to_table("Row", {"x": 42})
    assert row == ["Row", 42]


@pytest.mark.base
def test_prepare_row_for_writing_to_table_dict_with_none_values() -> None:
    """None values are preserved (mirrors real usage with subsidy/lifetime)."""
    row = prepare_row_for_writing_to_table(
        "Total",
        {"investment": 0.0, "subsidy": None, "lifetime": None},
    )
    assert row == ["Total", 0.0, None, None]


@pytest.mark.base
def test_prepare_row_for_writing_to_table_mixed_types() -> None:
    """Mixed-type values are preserved in insertion order."""
    row = prepare_row_for_writing_to_table("R", {"a": "text", "b": 3.14})
    assert row == ["R", "text", 3.14]


# --- Tests for the capex None-guard helpers ----------------------------------
# The capex summary dictionaries mix always-float keys (investment, co2, ...)
# with always-None keys (subsidy, lifetime) under a shared Optional[float]
# annotation. These helpers perform the arithmetic with explicit None guards so
# that a broken invariant raises loudly instead of producing a confusing
# TypeError (see issue #1866).


def _sample_capex_dict(investment: Optional[float] = 0.0, subsidy: Optional[float] = None) -> Dict[str, Optional[float]]:
    """Build a small capex-style dict mirroring the real float/None key pattern."""
    return {"investment": investment, "subsidy": subsidy}


@pytest.mark.base
def test_accumulate_capex_adds_to_existing_float() -> None:
    """A float target is increased by the given amount."""
    target = _sample_capex_dict(investment=100.0)
    _accumulate_capex(target, "investment", 42.5)
    assert target["investment"] == pytest.approx(142.5)


@pytest.mark.base
def test_accumulate_capex_raises_loudly_on_none_target() -> None:
    """Accumulating into a None-valued key raises instead of a silent TypeError."""
    target = _sample_capex_dict(investment=None)
    with pytest.raises(ValueError, match="investment"):
        _accumulate_capex(target, "investment", 42.5)


@pytest.mark.base
def test_capex_only_heatpump_subtracts_float_keys() -> None:
    """Float keys are subtracted (all_components minus without_hp) and rounded."""
    all_components = _sample_capex_dict(investment=100.0, subsidy=None)
    without_hp = _sample_capex_dict(investment=30.0, subsidy=None)
    result = _capex_only_heatpump(all_components, without_hp)
    assert result["investment"] == pytest.approx(70.0)
    assert result["subsidy"] is None


@pytest.mark.base
def test_capex_only_heatpump_preserves_key_order() -> None:
    """Result keys keep the all_components insertion order (needed for CSV rows)."""
    all_components = {"investment": 5.0, "co2": 6.0, "subsidy": None, "lifetime": None}
    without_hp = {"investment": 1.0, "co2": 2.0, "subsidy": None, "lifetime": None}
    result = _capex_only_heatpump(all_components, without_hp)
    assert list(result.keys()) == ["investment", "co2", "subsidy", "lifetime"]
    assert result["investment"] == pytest.approx(4.0)
    assert result["co2"] == pytest.approx(4.0)
    assert result["subsidy"] is None
    assert result["lifetime"] is None


@pytest.mark.base
def test_capex_only_heatpump_rounds_to_two_decimals() -> None:
    """Differences are rounded to two decimals, matching the previous behavior."""
    all_components = {"investment": 10.005}
    without_hp = {"investment": 0.004}
    result = _capex_only_heatpump(all_components, without_hp)
    assert result["investment"] == pytest.approx(round(10.005 - 0.004, 2))


@pytest.mark.base
def test_capex_only_heatpump_raises_loudly_on_mismatch() -> None:
    """A None/float mismatch between the two mappings raises instead of coercing."""
    all_components = _sample_capex_dict(investment=100.0, subsidy=None)
    without_hp = _sample_capex_dict(investment=30.0, subsidy=0.1)
    with pytest.raises(ValueError, match="subsidy"):
        _capex_only_heatpump(all_components, without_hp)
