"""Tests for the EVChargerMode enum and EVChargerControllerConfig.mode coercion.

These tests pin down the backward-compatible coercion of ``mode`` from a bare
integer code (1-6) to an :class:`EVChargerMode` member, plus the validation
that rejects invalid values. They only construct dataclass instances and
assert field values - no simulation, no I/O.
"""

# clean

import pytest

from hisim.components.generic_ev_charger import (
    EVChargerController,
    EVChargerControllerConfig,
    EVChargerMode,
)


@pytest.mark.base
def test_enum_members_have_expected_integer_values() -> None:
    """The six EVChargerMode members map 1:1 to the historic integer codes."""
    assert EVChargerMode.STRAIGHT_CHARGING == 1
    assert EVChargerMode.CHARGE_ON_ELECTRICITY_SURPLUS == 2
    assert EVChargerMode.VEHICLE_TO_GRID == 3
    assert EVChargerMode.STEPPED_PRIORITIZED_CHARGING == 4
    assert EVChargerMode.TIGHT_STEPPED_PRIORITIZED_CHARGING == 5
    assert EVChargerMode.SUPER_TIGHT_STEPPED_PRIORITIZED_CHARGING == 6


@pytest.mark.base
def test_config_with_enum_member_keeps_member() -> None:
    """Passing an EVChargerMode member stores it unchanged."""
    config = EVChargerControllerConfig(
        building_name="BUI1",
        name="Controller",
        mode=EVChargerMode.STRAIGHT_CHARGING,
    )
    assert config.mode is EVChargerMode.STRAIGHT_CHARGING
    assert isinstance(config.mode, EVChargerMode)


@pytest.mark.base
def test_config_coerces_int_one_to_straight_charging() -> None:
    """The historic code ``1`` coerces to EVChargerMode.STRAIGHT_CHARGING."""
    config = EVChargerControllerConfig(
        building_name="BUI1",
        name="Controller",
        mode=1,
    )
    assert config.mode is EVChargerMode.STRAIGHT_CHARGING
    assert isinstance(config.mode, EVChargerMode)


@pytest.mark.base
def test_config_coerces_int_six_to_super_tight_stepped() -> None:
    """The historic code ``6`` coerces to the super-tight stepped variant."""
    config = EVChargerControllerConfig(
        building_name="BUI1",
        name="Controller",
        mode=6,
    )
    assert config.mode is EVChargerMode.SUPER_TIGHT_STEPPED_PRIORITIZED_CHARGING


@pytest.mark.base
def test_config_coerces_int_three_to_vehicle_to_grid() -> None:
    """The historic code ``3`` coerces to EVChargerMode.VEHICLE_TO_GRID."""
    config = EVChargerControllerConfig(
        building_name="BUI1",
        name="Controller",
        mode=3,
    )
    assert config.mode is EVChargerMode.VEHICLE_TO_GRID


@pytest.mark.parametrize("invalid_code", [0, 7, -1, 8])
@pytest.mark.base
def test_config_rejects_out_of_range_integer_codes(invalid_code: int) -> None:
    """Integer codes outside the valid 1-6 range raise ValueError."""
    with pytest.raises(ValueError, match="Invalid EVChargerControllerConfig.mode"):
        EVChargerControllerConfig(
            building_name="BUI1",
            name="Controller",
            mode=invalid_code,
        )


@pytest.mark.base
def test_config_rejects_none_mode() -> None:
    """``None`` is not a valid mode and must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid EVChargerControllerConfig.mode"):
        EVChargerControllerConfig(
            building_name="BUI1",
            name="Controller",
            mode=None,
        )


@pytest.mark.base
def test_config_rejects_non_numeric_string() -> None:
    """A non-numeric string cannot be coerced to an int and must raise ValueError."""
    with pytest.raises(ValueError, match="Invalid EVChargerControllerConfig.mode"):
        EVChargerControllerConfig(
            building_name="BUI1",
            name="Controller",
            mode="not-a-mode",
        )


@pytest.mark.base
def test_get_default_config_uses_straight_charging() -> None:
    """``get_default_config`` returns STRAIGHT_CHARGING (not the raw int 1)."""
    config = EVChargerControllerConfig.get_default_config()
    assert isinstance(config, EVChargerControllerConfig)
    assert config.mode is EVChargerMode.STRAIGHT_CHARGING


@pytest.mark.base
def test_get_main_classname_returns_full_controller_path() -> None:
    """``get_main_classname`` returns the fully-qualified EVChargerController path."""
    classname = EVChargerControllerConfig.get_main_classname()
    assert classname == EVChargerController.get_full_classname()
    assert classname == "hisim.components.generic_ev_charger.EVChargerController"
