"""Tests for the ElectricityMeterConfig preset/classname classmethods and ElectricityMeterState.self_copy.

These tests pin down the pure, side-effect-free helpers on
``ElectricityMeterConfig`` and ``ElectricityMeterState`` that are otherwise
only exercised indirectly through full system setups. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import dataclasses

import pytest

from hisim.components.electricity_meter import (
    ElectricityMeter,
    ElectricityMeterConfig,
    ElectricityMeterState,
)
from hisim.config import ComponentID


_OPTIONAL_FIELDS: tuple[str, ...] = (
    "device_co2_footprint_in_kg",
    "investment_costs_in_euro",
    "lifetime_in_years",
    "maintenance_costs_in_euro_per_year",
    "subsidy_as_percentage_of_investment_costs",
)


def _assert_all_optional_fields_are_none(config: ElectricityMeterConfig) -> None:
    """Assert every Optional cost/emission field on ``config`` is ``None``.

    These are deliberately left unset by the preset because capex and device
    emissions are computed later in ``get_cost_capex``.
    """
    for field_name in _OPTIONAL_FIELDS:
        assert getattr(config, field_name) is None, (
            f"Expected {field_name} to be None, got {getattr(config, field_name)!r}"
        )


@pytest.mark.base
def test_preset_standard_defaults() -> None:
    """``preset_standard("ElectricityMeter")`` returns the documented defaults."""
    config: ElectricityMeterConfig = ElectricityMeterConfig.preset_standard("ElectricityMeter")
    assert isinstance(config, ElectricityMeterConfig)
    assert config.component_id.name == "ElectricityMeter"
    assert config.component_id.building is None
    _assert_all_optional_fields_are_none(config)


@pytest.mark.base
def test_preset_standard_names_the_instance() -> None:
    """The preset names the meter it builds; the name is the caller's, not a default."""
    config: ElectricityMeterConfig = ElectricityMeterConfig.preset_standard("X")
    assert isinstance(config, ElectricityMeterConfig)
    assert config.component_id.name == "X"
    assert config.component_id.building is None
    _assert_all_optional_fields_are_none(config)


@pytest.mark.base
def test_a_building_is_an_explicit_override_on_the_preset() -> None:
    """A meter inside a named building is the preset plus an explicit identity override."""
    config: ElectricityMeterConfig = dataclasses.replace(
        ElectricityMeterConfig.preset_standard("MyMeter"),
        component_id=ComponentID(name="MyMeter", building="HouseA"),
    )
    assert isinstance(config, ElectricityMeterConfig)
    assert config.component_id.name == "MyMeter"
    assert config.component_id.building == "HouseA"
    _assert_all_optional_fields_are_none(config)


@pytest.mark.base
def test_get_main_classname_returns_full_electricity_meter_path() -> None:
    """``get_main_classname`` returns the fully-qualified ``ElectricityMeter`` path."""
    classname: str = ElectricityMeterConfig.get_main_classname()
    assert isinstance(classname, str)
    assert classname == ElectricityMeter.get_full_classname()
    assert classname == "hisim.components.electricity_meter.ElectricityMeter"


@pytest.mark.base
def test_self_copy_returns_distinct_equal_object_for_zeros() -> None:
    """``self_copy`` of a zero state is equal but a distinct object."""
    state: ElectricityMeterState = ElectricityMeterState(
        cumulative_production_in_watt_hour=0,
        cumulative_consumption_in_watt_hour=0,
    )
    copy: ElectricityMeterState = state.self_copy()
    assert copy is not state
    assert copy.cumulative_production_in_watt_hour == state.cumulative_production_in_watt_hour == 0
    assert copy.cumulative_consumption_in_watt_hour == state.cumulative_consumption_in_watt_hour == 0


@pytest.mark.base
def test_self_copy_preserves_mixed_sign_values() -> None:
    """``self_copy`` preserves positive and negative field values exactly."""
    state: ElectricityMeterState = ElectricityMeterState(
        cumulative_production_in_watt_hour=100.5,
        cumulative_consumption_in_watt_hour=-200.0,
    )
    copy: ElectricityMeterState = state.self_copy()
    assert copy is not state
    assert copy.cumulative_production_in_watt_hour == 100.5
    assert copy.cumulative_consumption_in_watt_hour == -200.0


@pytest.mark.base
def test_self_copy_is_independent_of_original_mutation() -> None:
    """Mutating the original after copying does not affect the copy."""
    state: ElectricityMeterState = ElectricityMeterState(
        cumulative_production_in_watt_hour=10.0,
        cumulative_consumption_in_watt_hour=20.0,
    )
    copy: ElectricityMeterState = state.self_copy()
    state.cumulative_production_in_watt_hour = 999.0
    state.cumulative_consumption_in_watt_hour = -999.0
    assert copy.cumulative_production_in_watt_hour == 10.0
    assert copy.cumulative_consumption_in_watt_hour == 20.0


@pytest.mark.base
def test_self_copy_preserves_large_values_exactly() -> None:
    """``self_copy`` preserves large magnitude values without loss."""
    state: ElectricityMeterState = ElectricityMeterState(
        cumulative_production_in_watt_hour=1e12,
        cumulative_consumption_in_watt_hour=-1e12,
    )
    copy: ElectricityMeterState = state.self_copy()
    assert copy is not state
    assert copy.cumulative_production_in_watt_hour == 1e12
    assert copy.cumulative_consumption_in_watt_hour == -1e12
