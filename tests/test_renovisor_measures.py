"""Unit tests for the RenoVisor measure-application semantics (spec section 3)."""

import pytest

from hisim.renovisor.measures import apply_measures

pytestmark = pytest.mark.base


def _minimal_home() -> dict:
    """Return a minimal home inventory for measure tests."""
    return {
        "heating": {"primary": "gas"},
        "pv": {"kWp": 0},
        "battery": {"kWh": 0},
        "solarThermal": {"mode": "none"},
    }


def test_heat_pump_measure_replaces_primary_heating() -> None:
    """The heat_pump measure switches heating.primary and notes the ignored kW sizing."""
    result = apply_measures(_minimal_home(), [{"type": "heat_pump", "params": {"kW": 8}}])
    assert result.home_inputs["heating"]["primary"] == "heat_pump"
    path, status, note = result.report_notes[0]
    assert (path, status) == ("measures[0]", "used")
    assert "kW=8" in note


def test_pv_and_battery_measures_set_new_totals() -> None:
    """PV/battery measures overwrite the inventory capacities (new total, not additive)."""
    home = _minimal_home()
    home["pv"]["kWp"] = 3
    home["battery"]["kWh"] = 4
    result = apply_measures(home, [{"type": "pv", "params": {"kWp": 8}}, {"type": "battery", "params": {"kWh": 10}}])
    assert result.home_inputs["pv"]["kWp"] == 8
    assert result.home_inputs["battery"]["kWh"] == 10


def test_solar_thermal_measure_enables_mode_but_keeps_existing() -> None:
    """The solar_thermal measure turns mode 'none' into 'hot_water' but keeps a set mode."""
    enabled = apply_measures(_minimal_home(), [{"type": "solar_thermal"}])
    assert enabled.home_inputs["solarThermal"]["mode"] == "hot_water"

    home = _minimal_home()
    home["solarThermal"]["mode"] = "hot_water_and_heating"
    kept = apply_measures(home, [{"type": "solar_thermal"}])
    assert kept.home_inputs["solarThermal"]["mode"] == "hot_water_and_heating"


def test_envelope_measures_are_counted_by_distinct_type() -> None:
    """Envelope measures collect as distinct types for the TABULA variant bump."""
    measures = [
        {"type": "roof_insulation", "params": {"material": "wood_fibre", "mm": 200}},
        {"type": "roof_insulation", "params": {"material": "stone_wool", "mm": 300}},
        {"type": "windows", "params": {"glazing": "triple"}},
        {"type": "air_sealing", "params": {"targetN50": 3.0}},
    ]
    result = apply_measures(_minimal_home(), measures)
    assert result.envelope_measure_types == {"roof_insulation", "windows", "air_sealing"}
    assert all(status == "approximated" for _, status, _ in result.report_notes)


def test_unknown_and_malformed_measures_are_ignored_with_note() -> None:
    """Unknown measure types and entries without a type produce ignored notes."""
    result = apply_measures(_minimal_home(), [{"type": "fusion_reactor"}, {"params": {"mm": 100}}])
    assert result.home_inputs == _minimal_home()
    statuses = [status for _, status, _ in result.report_notes]
    assert statuses == ["ignored", "ignored"]


def test_original_inventory_is_not_mutated() -> None:
    """Measure application works on a deep copy of the inventory."""
    home = _minimal_home()
    apply_measures(home, [{"type": "heat_pump"}, {"type": "pv", "params": {"kWp": 5}}])
    assert home["heating"]["primary"] == "gas"
    assert home["pv"]["kWp"] == 0
