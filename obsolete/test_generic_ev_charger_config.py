"""Tests for the EVChargerMode enum and EVChargerControllerConfig.mode.

These tests pin down the string-valued contract of :class:`EVChargerMode`: every
member carries its own name as its value, so a serialized controller
configuration names the charging strategy explicitly instead of encoding it as
an integer. The historic integer codes (1-6) and the ``__post_init__`` coercion
that accepted them were removed in a clean cutover, so the tests also assert
that plain integers are no longer valid modes. They only construct dataclass
instances and assert field values - no simulation, no I/O.
"""

# clean

import json

import pytest

from projects.HiSim.obsolete.generic_ev_charger import (
    EVChargerController,
    EVChargerControllerConfig,
    EVChargerMode,
)
from hisim.component import ComponentID


@pytest.mark.base
def test_enum_members_have_their_own_name_as_value() -> None:
    """Every EVChargerMode member serializes as its own member name.

    This is the property that makes a config JSON readable: the value written
    to disk is the member name, not an opaque ordinal.
    """
    for member in EVChargerMode:
        assert member.value == member.name
        assert isinstance(member.value, str)


@pytest.mark.base
def test_enum_lookup_by_name_string_returns_member() -> None:
    """Constructing the enum from its member-name string yields the member.

    This is exactly the path dataclasses_json takes when decoding a config, so
    it proves that a JSON file holding ``"VEHICLE_TO_GRID"`` loads back into the
    matching member.
    """
    assert EVChargerMode("VEHICLE_TO_GRID") is EVChargerMode.VEHICLE_TO_GRID
    assert EVChargerMode("STRAIGHT_CHARGING") is EVChargerMode.STRAIGHT_CHARGING


@pytest.mark.base
def test_integer_codes_are_no_longer_valid_modes() -> None:
    """The historic integer codes are gone; looking them up raises ValueError.

    The old ``__post_init__`` coercion accepted the codes 1-6; it was deleted
    without a replacement shim, so an integer no longer maps to any member.
    """
    for code in (1, 2, 3, 4, 5, 6):
        with pytest.raises(ValueError):
            EVChargerMode(code)


@pytest.mark.base
def test_config_with_enum_member_keeps_member() -> None:
    """Passing an EVChargerMode member stores it unchanged.

    Nothing rewrites or normalizes the field any more, so the exact member
    handed to the constructor is the member the component later reads.
    """
    config = EVChargerControllerConfig(
        component_id=ComponentID(name="Controller"),
        mode=EVChargerMode.STRAIGHT_CHARGING,
    )
    assert config.mode is EVChargerMode.STRAIGHT_CHARGING
    assert isinstance(config.mode, EVChargerMode)


@pytest.mark.base
def test_config_round_trips_through_json_as_member_name() -> None:
    """The config serializes ``mode`` as the member name and decodes back to it.

    This walks the full dataclasses_json path used by the JSON scenario
    executor: ``to_dict`` then ``json.dumps`` must produce the member-name
    string, and ``from_dict`` must return an equal config.
    """
    config = EVChargerControllerConfig(
        component_id=ComponentID(name="Controller"),
        mode=EVChargerMode.TIGHT_STEPPED_PRIORITIZED_CHARGING,
    )
    encoded = json.loads(json.dumps(config.to_dict()))
    assert encoded["mode"] == "TIGHT_STEPPED_PRIORITIZED_CHARGING"
    decoded = EVChargerControllerConfig.from_dict(encoded)
    assert decoded.mode is EVChargerMode.TIGHT_STEPPED_PRIORITIZED_CHARGING
    assert decoded == config


@pytest.mark.base
def test_get_default_config_uses_straight_charging() -> None:
    """``get_default_config`` returns the STRAIGHT_CHARGING member.

    The default has to be an enum member rather than a raw value so that
    downstream member comparisons in the controller keep working.
    """
    config = EVChargerControllerConfig.get_default_config()
    assert isinstance(config, EVChargerControllerConfig)
    assert config.mode is EVChargerMode.STRAIGHT_CHARGING


@pytest.mark.base
def test_get_main_classname_returns_full_controller_path() -> None:
    """``get_main_classname`` returns the fully-qualified EVChargerController path.

    The JSON executor uses this string to find the component class belonging to
    a configuration, so it must stay exact.
    """
    classname = EVChargerControllerConfig.get_main_classname()
    assert classname == EVChargerController.get_full_classname()
    assert classname == "hisim.components.generic_ev_charger.EVChargerController"
