"""Backward-compatibility and unit tests for ``ScenarioAnalysisConfig``.

The field ``simulation_duration_to_check`` was renamed to
``simulation_duration_to_check_in_days`` so that the duration unit (days) is
explicit in the field name (GitLab issue #816).  These tests verify that:

* the default config exposes the renamed field with its documented value,
* round-tripping a config through JSON uses the new field name, and
* JSON configs that still use the deprecated ``simulation_duration_to_check``
  key are still deserialized correctly (with a ``DeprecationWarning``) instead
  of silently dropping the value or raising a ``KeyError``.
"""

# clean

import json
import warnings

import pytest

from hisim.postprocessing.scenario_evaluation.scenario_analysis_complete_with_config import (
    ScenarioAnalysisConfig,
    _migrate_legacy_field_names,
)


def _default_dict() -> dict:
    """Return the default config as a plain dict (new field name)."""
    return ScenarioAnalysisConfig.get_default().to_dict()


@pytest.mark.base
def test_default_uses_renamed_field_with_documented_value() -> None:
    """The default config exposes ``simulation_duration_to_check_in_days == '365'``."""
    cfg = ScenarioAnalysisConfig.get_default()
    assert cfg.simulation_duration_to_check_in_days == "365"
    assert not hasattr(cfg, "simulation_duration_to_check")


@pytest.mark.base
def test_to_dict_uses_new_field_name() -> None:
    """``to_dict`` serializes the renamed field and omits the old key."""
    as_dict = _default_dict()
    assert "simulation_duration_to_check_in_days" in as_dict
    assert as_dict["simulation_duration_to_check_in_days"] == "365"
    assert "simulation_duration_to_check" not in as_dict


@pytest.mark.base
def test_roundtrip_new_key_no_warning() -> None:
    """Round-tripping via ``to_json``/``from_json`` keeps the new key and warns nothing."""
    original = ScenarioAnalysisConfig.get_default()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        restored = ScenarioAnalysisConfig.from_json(original.to_json())
    assert restored.simulation_duration_to_check_in_days == "365"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.base
def test_from_json_old_key_maps_to_new_field() -> None:
    """``from_json`` migrates the deprecated key and emits a ``DeprecationWarning``."""
    old_dict = dict(_default_dict())
    old_dict["simulation_duration_to_check"] = old_dict.pop(
        "simulation_duration_to_check_in_days"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ScenarioAnalysisConfig.from_json(json.dumps(old_dict))
    assert cfg.simulation_duration_to_check_in_days == "365"
    assert not hasattr(cfg, "simulation_duration_to_check")
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.base
def test_from_dict_old_key_maps_to_new_field() -> None:
    """``from_dict`` migrates the deprecated key and emits a ``DeprecationWarning``."""
    old_dict = dict(_default_dict())
    old_dict["simulation_duration_to_check"] = old_dict.pop(
        "simulation_duration_to_check_in_days"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ScenarioAnalysisConfig.from_dict(old_dict)
    assert cfg.simulation_duration_to_check_in_days == "365"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.base
def test_from_json_both_keys_new_wins() -> None:
    """When both keys are present the new value wins and a warning is still emitted."""
    both = dict(_default_dict())
    both["simulation_duration_to_check"] = "999"  # legacy value must be ignored
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = ScenarioAnalysisConfig.from_json(json.dumps(both))
    assert cfg.simulation_duration_to_check_in_days == "365"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.base
def test_migrate_does_not_mutate_input() -> None:
    """``_migrate_legacy_field_names`` must not mutate its argument."""
    original = {"simulation_duration_to_check": "365", "building_name": "BUI1"}
    _migrate_legacy_field_names(original)
    assert original == {"simulation_duration_to_check": "365", "building_name": "BUI1"}


@pytest.mark.base
def test_migrate_passthrough_non_dict() -> None:
    """``_migrate_legacy_field_names`` returns non-dict values unchanged."""
    assert _migrate_legacy_field_names("not a dict") == "not a dict"
    assert _migrate_legacy_field_names(None) is None


@pytest.mark.base
def test_keyword_only_construction_accepts_kwargs() -> None:
    """Direct construction with keyword arguments succeeds and preserves values."""
    kwargs = ScenarioAnalysisConfig.get_default().to_dict()
    cfg = ScenarioAnalysisConfig(**kwargs)
    assert cfg.building_name == "BUI1"
    assert cfg.name == "ScenarioAnalysisConfig_0"
    assert cfg.simulation_duration_to_check_in_days == "365"
    assert cfg.data_format_type == "CSV"


@pytest.mark.base
def test_positional_construction_is_rejected() -> None:
    """All fields are keyword-only; positional construction raises ``TypeError``.

    With 14 fields, allowing positional construction is a foot-gun (callers
    must supply every value in the exact declared order with no hint as to
    which slot is which).  ``kw_only=True`` on the dataclass prevents this.
    """
    defaults = ScenarioAnalysisConfig.get_default().to_dict()
    positional_args = list(defaults.values())
    # Assert the TypeError is specifically about *positional* arguments, not a
    # side effect of missing required keyword arguments: with kw_only=True the
    # generated __init__ accepts zero positional arguments, so passing 14
    # values positionally raises "... takes 1 positional argument but 15 were
    # given".  Matching on "positional" distinguishes this from the
    # "missing required keyword-only argument" error an empty to_dict() would
    # produce, making the test's intent precise.
    with pytest.raises(TypeError, match="positional"):
        ScenarioAnalysisConfig(*positional_args)  # type: ignore[call-arg]
