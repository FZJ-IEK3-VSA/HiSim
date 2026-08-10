"""Backward-compatibility tests for renamed ``ArcheTypeConfigModular`` fields.

The fields ``mobility_set``, ``mobility_distance`` and
``absolute_conditioned_floor_area`` were renamed to
``transportation_device_set``, ``commuting_travel_route_set`` and
``absolute_conditioned_floor_area_in_m2`` respectively.  These tests verify
that JSON configs using the old field names are still deserialized correctly
(with a deprecation warning) instead of silently dropping the values.
"""

# clean

import json
import warnings
from pathlib import Path

import pytest

from hisim.modular_household.interface_configs.archetype_config import (
    ArcheTypeConfigModular,
)
from hisim.modular_household.interface_configs.modular_household_config import (
    read_config,
)


# A JsonReference serializes to a dict with ``Name`` and ``Guid`` keys.
_DEVICE_REF: dict[str, str | dict[str, str]] = {
    "Name": "Bus and one 30 km/h Car",
    "Guid": {"StrVal": "device-guid-123"},
}
_ROUTE_REF: dict[str, str | dict[str, str]] = {
    "Name": "Travel Route Set for 15km Commuting Distance",
    "Guid": {"StrVal": "route-guid-456"},
}


# --------------------------------------------------------------------------- #
# ArcheTypeConfigModular.from_dict / from_json  (dataclasses_json path)
# --------------------------------------------------------------------------- #
def test_from_dict_old_keys_map_to_new_fields() -> None:
    """``from_dict`` maps legacy ``mobility_set``/``mobility_distance`` keys."""
    old_dict = {
        "mobility_set": _DEVICE_REF,
        "mobility_distance": _ROUTE_REF,
        "building_code": "TEST",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_dict(old_dict)
    assert cfg.transportation_device_set is not None
    assert cfg.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.commuting_travel_route_set is not None
    assert cfg.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert cfg.building_code == "TEST"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_from_json_old_keys_map_to_new_fields() -> None:
    """``from_json`` maps legacy ``mobility_set``/``mobility_distance`` keys."""
    old_json = json.dumps(
        {
            "mobility_set": _DEVICE_REF,
            "mobility_distance": _ROUTE_REF,
            "building_code": "TEST2",
        }
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_json(old_json)
    assert cfg.transportation_device_set is not None
    assert cfg.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.commuting_travel_route_set is not None
    assert cfg.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert cfg.building_code == "TEST2"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_from_dict_new_keys_no_warning() -> None:
    """``from_dict`` with new keys works without deprecation warnings."""
    new_dict = {
        "transportation_device_set": _DEVICE_REF,
        "commuting_travel_route_set": _ROUTE_REF,
        "building_code": "TEST3",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_dict(new_dict)
    assert cfg.transportation_device_set is not None
    assert cfg.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.commuting_travel_route_set is not None
    assert cfg.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert cfg.building_code == "TEST3"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_from_dict_both_keys_new_wins() -> None:
    """When both old and new keys are present, the new key value wins."""
    both_dict = {
        "mobility_set": {"Name": "OLD_DEVICE", "Guid": {"StrVal": "old"}},
        "transportation_device_set": _DEVICE_REF,
        "mobility_distance": {"Name": "OLD_ROUTE", "Guid": {"StrVal": "old"}},
        "commuting_travel_route_set": _ROUTE_REF,
        "building_code": "TEST4",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_dict(both_dict)
    assert cfg.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_to_json_uses_new_field_names() -> None:
    """``to_json`` serializes with the new field names, not the old ones."""
    cfg = ArcheTypeConfigModular()
    j = json.loads(cfg.to_json())
    assert "transportation_device_set" in j
    assert "commuting_travel_route_set" in j
    assert "mobility_set" not in j
    assert "mobility_distance" not in j


def test_round_trip_no_warning() -> None:
    """Round-trip ``to_json`` → ``from_json`` produces no deprecation warning."""
    cfg = ArcheTypeConfigModular(building_code="RoundTrip")
    j = cfg.to_json()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        restored = ArcheTypeConfigModular.from_json(j)
    assert restored.building_code == "RoundTrip"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


# --------------------------------------------------------------------------- #
# read_config  (dataclass_wizard path via ModularHouseholdConfig.from_dict)
# --------------------------------------------------------------------------- #
def test_read_config_old_keys_migrated(tmp_path: Path) -> None:
    """``read_config`` migrates legacy keys in the nested archetype config."""
    config_with_old_keys = {
        "system_setup_config": {
            "archetype_config_": {
                "mobility_set": _DEVICE_REF,
                "mobility_distance": _ROUTE_REF,
                "building_code": "TEST_OLD",
            }
        }
    }
    config_path = tmp_path / "config_old.json"
    config_path.write_text(json.dumps(config_with_old_keys), encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = read_config(str(config_path))
    assert cfg.archetype_config_ is not None
    assert cfg.archetype_config_.transportation_device_set is not None
    assert cfg.archetype_config_.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.archetype_config_.commuting_travel_route_set is not None
    assert cfg.archetype_config_.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert cfg.archetype_config_.building_code == "TEST_OLD"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_read_config_new_keys_no_warning(tmp_path: Path) -> None:
    """``read_config`` with new keys works without deprecation warnings."""
    config_with_new_keys = {
        "system_setup_config": {
            "archetype_config_": {
                "transportation_device_set": _DEVICE_REF,
                "commuting_travel_route_set": _ROUTE_REF,
                "building_code": "TEST_NEW",
            }
        }
    }
    config_path = tmp_path / "config_new.json"
    config_path.write_text(json.dumps(config_with_new_keys), encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = read_config(str(config_path))
    assert cfg.archetype_config_ is not None
    assert cfg.archetype_config_.transportation_device_set is not None
    assert cfg.archetype_config_.transportation_device_set.Name == _DEVICE_REF["Name"]
    assert cfg.archetype_config_.commuting_travel_route_set is not None
    assert cfg.archetype_config_.commuting_travel_route_set.Name == _ROUTE_REF["Name"]
    assert cfg.archetype_config_.building_code == "TEST_NEW"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


# --------------------------------------------------------------------------- #
# absolute_conditioned_floor_area -> absolute_conditioned_floor_area_in_m2
#
# The ``absolute_conditioned_floor_area`` field was renamed to
# ``absolute_conditioned_floor_area_in_m2`` so that the physical unit (square
# metres) is explicit in the field name.  JSON configs that still use the old
# key must be migrated transparently with a ``DeprecationWarning``.
# --------------------------------------------------------------------------- #
def test_from_dict_old_floor_area_key_maps_to_new_field() -> None:
    """``from_dict`` maps the legacy ``absolute_conditioned_floor_area`` key."""
    old_dict = {
        "absolute_conditioned_floor_area": 121.2,
        "building_code": "TEST_AREA",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_dict(old_dict)
    assert cfg.absolute_conditioned_floor_area_in_m2 == 121.2
    assert cfg.building_code == "TEST_AREA"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_from_json_old_floor_area_key_maps_to_new_field() -> None:
    """``from_json`` maps the legacy ``absolute_conditioned_floor_area`` key."""
    old_json = json.dumps(
        {
            "absolute_conditioned_floor_area": 150.0,
            "building_code": "TEST_AREA2",
        }
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_json(old_json)
    assert cfg.absolute_conditioned_floor_area_in_m2 == 150.0
    assert cfg.building_code == "TEST_AREA2"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_from_dict_both_floor_area_keys_new_wins() -> None:
    """When both old and new floor-area keys are present, the new value wins."""
    both_dict = {
        "absolute_conditioned_floor_area": 999.0,  # legacy value must be ignored
        "absolute_conditioned_floor_area_in_m2": 121.2,
        "building_code": "TEST_AREA3",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ArcheTypeConfigModular.from_dict(both_dict)
    assert cfg.absolute_conditioned_floor_area_in_m2 == 121.2
    assert cfg.building_code == "TEST_AREA3"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_to_json_uses_new_floor_area_field_name() -> None:
    """``to_json`` serializes with the new field name, not the old one."""
    cfg = ArcheTypeConfigModular(absolute_conditioned_floor_area_in_m2=121.2)
    j = json.loads(cfg.to_json())
    assert j["absolute_conditioned_floor_area_in_m2"] == 121.2
    assert "absolute_conditioned_floor_area" not in j


def test_read_config_old_floor_area_key_migrated(tmp_path: Path) -> None:
    """``read_config`` migrates the legacy floor-area key in the nested config."""
    config_with_old_key = {
        "system_setup_config": {
            "archetype_config_": {
                "absolute_conditioned_floor_area": 121.2,
                "building_code": "TEST_AREA_OLD",
            }
        }
    }
    config_path = tmp_path / "config_area_old.json"
    config_path.write_text(json.dumps(config_with_old_key), encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = read_config(str(config_path))
    assert cfg.archetype_config_ is not None
    assert cfg.archetype_config_.absolute_conditioned_floor_area_in_m2 == 121.2
    assert cfg.archetype_config_.building_code == "TEST_AREA_OLD"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
