"""Tests for ``dataclasses_json`` serialization round-trips on ``KPIConfigModular``.

These tests verify that ``KPIConfigModular`` (defined in
``hisim.modular_household.interface_configs.kpi_config``) survives a
``to_dict`` / ``from_dict`` and ``to_json`` / ``from_json`` round-trip with
all fields intact. They are independent of the ``get_kpi`` arithmetic pinned
in ``test_kpi_config_modular.py`` and only exercise the serialization
helpers provided by ``dataclasses_json``. No simulation, no I/O, no mocking.

Unit convention
---------------
``self_consumption_rate`` and ``autarky_rate`` are stored in **percent** in
[0, 100], not as fractions in [0, 1] (see ``KPIConfigModular``'s docstring
and ``hisim/postprocessing/compute_kpis.py``). The values used below
(e.g. ``50.0 + 30.0 == 80.0``) are percent values.
"""

# clean

import pytest

from hisim.modular_household.interface_configs.kpi_config import KPIConfigModular


@pytest.mark.base
def test_to_dict_from_dict_round_trip() -> None:
    """``to_dict`` / ``from_dict`` round-trip reconstructs an equal instance."""
    original = KPIConfigModular(
        self_consumption_rate=50.0,
        autarky_rate=30.0,
        injection=100.0,
        economic_investment_costs_in_euro=2000.0,
        co2_investment_costs_in_kg=500.0,
    )
    reconstructed = KPIConfigModular.from_dict(original.to_dict())
    assert isinstance(reconstructed, KPIConfigModular)
    assert reconstructed == original


@pytest.mark.base
def test_to_json_from_json_round_trip() -> None:
    """``to_json`` / ``from_json`` round-trip reconstructs an equal instance."""
    original = KPIConfigModular(
        self_consumption_rate=50.0,
        autarky_rate=30.0,
        injection=100.0,
        economic_investment_costs_in_euro=2000.0,
        co2_investment_costs_in_kg=500.0,
    )
    serialized = original.to_json()
    assert isinstance(serialized, str)
    reconstructed = KPIConfigModular.from_json(serialized)
    assert isinstance(reconstructed, KPIConfigModular)
    assert reconstructed == original


@pytest.mark.base
def test_all_zero_round_trip_via_dict() -> None:
    """All-zero instance round-trips through ``to_dict`` / ``from_dict``."""
    original = KPIConfigModular(
        self_consumption_rate=0.0,
        autarky_rate=0.0,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    reconstructed = KPIConfigModular.from_dict(original.to_dict())
    assert reconstructed == original
    assert reconstructed.get_kpi() == 0.0


@pytest.mark.base
def test_all_zero_round_trip_via_json() -> None:
    """All-zero instance round-trips through ``to_json`` / ``from_json``."""
    original = KPIConfigModular(
        self_consumption_rate=0.0,
        autarky_rate=0.0,
        injection=0.0,
        economic_investment_costs_in_euro=0.0,
        co2_investment_costs_in_kg=0.0,
    )
    reconstructed = KPIConfigModular.from_json(original.to_json())
    assert reconstructed == original
    assert reconstructed.get_kpi() == 0.0
