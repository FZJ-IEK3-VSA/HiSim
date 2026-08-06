"""Tests for the prepare_row_for_writing_to_table helper.

This module contains unit tests for the module-level function
``prepare_row_for_writing_to_table`` in
``hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation``.
The helper is a pure, side-effect-free function used by both
``opex_calculation`` and ``capex_calculation`` to build summary rows.
"""

# clean

import pytest

from hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation import (
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
