"""Direct unit tests for the ``_migrate_legacy_field_names`` helper.

``_migrate_legacy_field_names`` is a pure, self-contained helper used by
``ArcheTypeConfigModular.from_dict`` to rename the deprecated JSON field names
``mobility_set`` and ``mobility_distance`` to their current names
(``transportation_device_set`` and ``commuting_travel_route_set``).  The
existing tests in ``test_archetype_config_backward_compat.py`` only exercise it
indirectly through ``from_dict`` / ``from_json``.  This module pins down the
helper's own behavior directly.
"""

# clean

import warnings

import pytest

from hisim.modular_household.interface_configs.archetype_config import (
    migrate_legacy_field_names as _migrate_legacy_field_names,
)


# --------------------------------------------------------------------------- #
# 1. Non-dict passthrough -- returned unchanged, no warning emitted.
# --------------------------------------------------------------------------- #
@pytest.mark.base
@pytest.mark.parametrize(
    "value",
    [None, [1, 2, 3], "hello", 42, 3.14, True],
)
def test_non_dict_passthrough(value) -> None:
    """Non-dict inputs are returned unchanged and emit no warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _migrate_legacy_field_names(value)
    assert result == value
    assert result is value or result == value
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


# --------------------------------------------------------------------------- #
# 2. Empty dict -- returned unchanged, no warning.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_empty_dict_passthrough() -> None:
    """An empty dict is returned as a new empty dict with no warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _migrate_legacy_field_names({})
    assert isinstance(result, dict)
    assert not result
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


# --------------------------------------------------------------------------- #
# 3. Single legacy key renamed, with a DeprecationWarning.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_single_legacy_key_mobility_set_renamed() -> None:
    """``mobility_set`` is renamed to ``transportation_device_set``."""
    with pytest.warns(DeprecationWarning) as record:
        result = _migrate_legacy_field_names({"mobility_set": "X"})
    assert result == {"transportation_device_set": "X"}
    message = str(record[0].message)
    assert "mobility_set" in message
    assert "transportation_device_set" in message


@pytest.mark.base
def test_single_legacy_key_mobility_distance_renamed() -> None:
    """``mobility_distance`` is renamed to ``commuting_travel_route_set``."""
    with pytest.warns(DeprecationWarning) as record:
        result = _migrate_legacy_field_names({"mobility_distance": "Y"})
    assert result == {"commuting_travel_route_set": "Y"}
    message = str(record[0].message)
    assert "mobility_distance" in message
    assert "commuting_travel_route_set" in message


# --------------------------------------------------------------------------- #
# 4. Both legacy keys present -- both renamed, single warning listing both.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_both_legacy_keys_renamed() -> None:
    """Both legacy keys are renamed and a single warning lists both old names."""
    with pytest.warns(DeprecationWarning) as record:
        result = _migrate_legacy_field_names(
            {"mobility_set": "A", "mobility_distance": "B"}
        )
    assert result == {
        "transportation_device_set": "A",
        "commuting_travel_route_set": "B",
    }
    # Exactly one DeprecationWarning should be emitted.
    dep_warnings = [w for w in record if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) == 1
    message = str(dep_warnings[0].message)
    assert "mobility_set" in message
    assert "mobility_distance" in message
    assert "transportation_device_set" in message
    assert "commuting_travel_route_set" in message


# --------------------------------------------------------------------------- #
# 5. New key only -- no migration, no warning.
# --------------------------------------------------------------------------- #
@pytest.mark.base
@pytest.mark.parametrize(
    "payload",
    [
        {"transportation_device_set": "X"},
        {"commuting_travel_route_set": "Y"},
    ],
)
def test_new_key_only_no_migration(payload) -> None:
    """Dicts that only use the new field names are returned unchanged, no warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _migrate_legacy_field_names(payload)
    assert result == payload
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


# --------------------------------------------------------------------------- #
# 6. Legacy + new key collision -- the new value wins, warning still emitted.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_legacy_and_new_collision_new_wins() -> None:
    """When both legacy and new keys are present, the new value is kept."""
    with pytest.warns(DeprecationWarning):
        result = _migrate_legacy_field_names(
            {"mobility_set": "old_val", "transportation_device_set": "new_val"}
        )
    assert result == {"transportation_device_set": "new_val"}


# --------------------------------------------------------------------------- #
# 7. Input is not mutated.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_input_not_mutated() -> None:
    """The original dict passed in must not be modified."""
    original = {"mobility_set": "X"}
    with pytest.warns(DeprecationWarning):
        _migrate_legacy_field_names(original)
    assert original == {"mobility_set": "X"}


@pytest.mark.base
def test_collision_input_not_mutated() -> None:
    """Even when a legacy/new collision occurs, the input dict is untouched."""
    original = {"mobility_set": "old_val", "transportation_device_set": "new_val"}
    with pytest.warns(DeprecationWarning):
        _migrate_legacy_field_names(original)
    assert original == {
        "mobility_set": "old_val",
        "transportation_device_set": "new_val",
    }


# --------------------------------------------------------------------------- #
# 8. Unrelated keys are preserved alongside migrated ones.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_unrelated_keys_preserved() -> None:
    """Keys that are not legacy field names are preserved untouched."""
    with pytest.warns(DeprecationWarning):
        result = _migrate_legacy_field_names(
            {"mobility_set": "X", "building_code": "DE.N.SFH.05"}
        )
    assert result == {
        "transportation_device_set": "X",
        "building_code": "DE.N.SFH.05",
    }
