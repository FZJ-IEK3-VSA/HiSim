"""Tests for ``KPIConfigModular.get_kpi``.

These tests pin down the pure, side-effect-free ``get_kpi`` helper on
``KPIConfigModular`` (defined in
``hisim.modular_household.interface_configs.kpi_config``), which is otherwise
only exercised indirectly through the obsolete KPI computation module. They
only construct dataclass instances and call ``get_kpi`` - no simulation, no
I/O, no mocking.

The ``dataclasses_json`` serialization round-trips for the same dataclass
live in ``test_kpi_config_serialization.py``; the two concerns (KPI
arithmetic vs. serialization) are tested separately so that a change in one
does not require editing the other.

Unit convention
---------------
``self_consumption_rate_in_percent`` and ``autarky_rate_in_percent`` are
stored in **percent** in [0, 100], not as fractions in [0, 1] (per
``KPIConfigModular``'s docstring and the postprocessing KPI computation in
``hisim/postprocessing/compute_kpis.py``, where the producer multiplies by
100 and tags the entries with ``unit="%"``). Accordingly ``get_kpi`` returns
the sum in percent as well. The values used below (e.g. 50.0 + 30.0 == 80.0)
are percent values; a caller assuming fractions would expect 0.5 + 0.3 == 0.8.
"""

# clean

import pytest

from hisim.modular_household.interface_configs.kpi_config import KPIConfigModular


def test_get_kpi_sums_self_consumption_and_autarky() -> None:
    """``get_kpi`` returns ``self_consumption_rate_in_percent + autarky_rate_in_percent`` for typical inputs.

    Values are in percent (50.0 % + 30.0 % == 80.0 %), matching the unit
    documented on the ``KPIConfigModular`` fields; not fractions.
    """
    config = KPIConfigModular(
        self_consumption_rate_in_percent=50.0,
        autarky_rate_in_percent=30.0,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    assert config.get_kpi() == 80.0


def test_get_kpi_zero_boundary() -> None:
    """All-zero inputs yield ``get_kpi() == 0.0``."""
    config = KPIConfigModular(
        self_consumption_rate_in_percent=0.0,
        autarky_rate_in_percent=0.0,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    assert config.get_kpi() == 0.0


def test_get_kpi_negative_self_consumption_no_clamping() -> None:
    """``get_kpi`` performs a plain sum with no clamping or ``abs`` applied."""
    config = KPIConfigModular(
        self_consumption_rate_in_percent=-10.0,
        autarky_rate_in_percent=20.0,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    assert config.get_kpi() == 10.0


def test_get_kpi_large_value_boundary() -> None:
    """Large magnitudes are summed without overflow or special-casing."""
    config = KPIConfigModular(
        self_consumption_rate_in_percent=1e9,
        autarky_rate_in_percent=1e9,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    assert config.get_kpi() == 2e9


def test_get_kpi_ignores_remaining_fields() -> None:
    """``get_kpi`` only depends on the first two fields; the rest are irrelevant."""
    config = KPIConfigModular(
        self_consumption_rate_in_percent=50.0,
        autarky_rate_in_percent=30.0,
        injection=123.0,
        economic_investment_costs_in_euro=456.0,
        co2_investment_costs_in_kg=789.0,
    )
    assert config.get_kpi() == 80.0
