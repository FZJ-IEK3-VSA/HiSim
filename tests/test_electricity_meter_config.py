"""Tests for the ElectricityMeterConfig factory/classname classmethods and ElectricityMeterState.self_copy.

These tests pin down the pure, side-effect-free helpers on
``ElectricityMeterConfig`` and ``ElectricityMeterState`` that are otherwise
only exercised indirectly through full system setups. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import pytest

from hisim.components.electricity_meter import (
    ElectricityMeter,
    ElectricityMeterConfig,
    ElectricityMeterState,
)


_OPTIONAL_FIELDS = (
    "device_co2_footprint_in_kg",
    "investment_costs_in_euro",
    "lifetime_in_years",
    "maintenance_costs_in_euro_per_year",
    "subsidy_as_percentage_of_investment_costs",
)


def _assert_all_optional_fields_are_none(config: ElectricityMeterConfig) -> None:
    """Assert every Optional cost/emission field on ``config`` is ``None``.

    These are deliberately left unset by the default factory because capex and
    device emissions are computed later in ``get_cost_capex``.
    """
    for field_name in _OPTIONAL_FIELDS:
        assert getattr(config, field_name) is None, (
            f"Expected {field_name} to be None, got {getattr(config, field_name)!r}"
        )


@pytest.mark.base
def test_get_electricity_meter_default_config_defaults() -> None:
    """``get_electricity_meter_default_config()`` returns the documented defaults."""
    config: ElectricityMeterConfig = ElectricityMeterConfig.get_electricity_meter_default_config()
    assert isinstance(config, ElectricityMeterConfig)
    assert config.name == "ElectricityMeter"
    assert config.building_name == "BUI1"
    _assert_all_optional_fields_are_none(config)


@pytest.mark.base
def test_get_electricity_meter_default_config_custom_name_and_building() -> None:
    """Passing ``name`` and ``building_name`` sets both while keeping Optional fields ``None``."""
    config: ElectricityMeterConfig = ElectricityMeterConfig.get_electricity_meter_default_config(
        name="MyMeter", building_name="HouseA"
    )
    assert isinstance(config, ElectricityMeterConfig)
    assert config.name == "MyMeter"
    assert config.building_name == "HouseA"
    _assert_all_optional_fields_are_none(config)


@pytest.mark.base
def test_get_electricity_meter_default_config_name_only_defaults_building() -> None:
    """Passing only ``name`` leaves ``building_name`` at its ``BUI1`` default."""
    config: ElectricityMeterConfig = ElectricityMeterConfig.get_electricity_meter_default_config(name="X")
    assert isinstance(config, ElectricityMeterConfig)
    assert config.name == "X"
    assert config.building_name == "BUI1"
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
