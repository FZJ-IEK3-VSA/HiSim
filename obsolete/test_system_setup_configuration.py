"""Tests for the config-loading behaviour of system setup classes.

This module uses ``HouseholdGasHeaterConfig`` -- a concrete subclass of
``SystemSetupConfigBase`` -- to exercise the ``load_from_dict`` and
``load_from_json`` classmethods. The behaviours covered are:

- empty-dict defaults: an empty dict yields the unscaled default config;
- ``system_setup_config`` overrides: fields overwrite the default in place;
- known-key consumption: ``building_config``, ``options`` and
  ``system_setup_config`` are popped while unknown keys are left untouched;
- the missing-``building_config`` error: ``options`` without a
  ``building_config`` raises ``ValueError``;
- scaled defaults: a ``building_config`` triggers the scaled-default path
  with options applied;
- JSON-file delegation: ``load_from_json`` reads the file and forwards the
  dict to ``load_from_dict``;
- the missing-file error: a missing config path raises ``FileNotFoundError``;
- the carriage-return path workaround: a trailing ``"\r"`` in the path is
  stripped as a fallback so Windows-style paths still resolve.

``load_from_dict`` is the pure, filesystem-free counterpart of
``load_from_json``; the dict-processing logic lives there so the
merge/overwrite behaviour can be unit-tested without writing a JSON file.
"""

import json
from pathlib import Path

import pytest

from hisim.components import building
from system_setups.household_gas_heater import HouseholdGasHeaterConfig


# A concrete subclass with simple defaults is used to exercise the base class.
Config = HouseholdGasHeaterConfig


def test_load_from_dict_empty_returns_default() -> None:
    """An empty dict (no overwrites) yields the unscaled default config."""
    config = Config.load_from_dict({})
    default = Config.get_default()
    assert isinstance(config, Config)
    # weather_location is a simple scalar set by get_default; the empty dict
    # must not change it.
    assert config.weather_location == default.weather_location
    assert config.building_type == default.building_type


def test_load_from_dict_applies_system_setup_config_overwrite() -> None:
    """`system_setup_config` overwrites fields on the default config in place."""
    config = Config.load_from_dict(
        {"system_setup_config": {"weather_location": "HAMBURG"}}
    )
    assert config.weather_location == "HAMBURG"
    # Other fields remain at their defaults.
    assert config.building_type == Config.get_default().building_type


def test_load_from_dict_consumes_known_keys() -> None:
    """`load_from_dict` pops `building_config`, `options`, `system_setup_config`."""
    payload = {
        "building_config": building.BuildingConfig.presets.german_single_family_home.to_dict(),
        "options": {"diesel_car": True},
        "system_setup_config": {"weather_location": "MUNICH"},
        "leftover_key": "ignored",
    }
    Config.load_from_dict(payload)
    # The three recognised keys have been popped, the rest is left untouched.
    assert "building_config" not in payload
    assert "options" not in payload
    assert "system_setup_config" not in payload
    assert "leftover_key" in payload


def test_load_from_dict_options_without_building_config_raises() -> None:
    """Options without a `building_config` is not supported and must raise."""
    with pytest.raises(ValueError, match="Options for default setup not yet implemented"):
        Config.load_from_dict({"options": {"diesel_car": True}})


def test_load_from_dict_with_building_config_uses_scaled_default() -> None:
    """A `building_config` triggers the scaled-default path with options applied."""
    building_config = building.BuildingConfig.presets.german_single_family_home
    config = Config.load_from_dict(
        {
            "building_config": building_config.to_dict(),
            "options": {"diesel_car": True},
        }
    )
    # options.diesel_car == True selects a diesel car config (not None).
    assert config.car_config is not None


def test_load_from_json_delegates_to_load_from_dict(tmp_path: Path) -> None:
    """`load_from_json` reads the file and forwards the dict to `load_from_dict`."""
    payload = {"system_setup_config": {"weather_location": "COLOGNE"}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload), encoding="utf8")

    config = Config.load_from_json(str(config_path))
    assert config.weather_location == "COLOGNE"


def test_load_from_json_missing_file_raises(tmp_path: Path) -> None:
    """A missing config file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        Config.load_from_json(str(tmp_path / "does_not_exist.json"))


def test_load_from_json_matches_load_from_dict(tmp_path: Path) -> None:
    """`load_from_json` and `load_from_dict` agree for identical inputs."""
    building_config = building.BuildingConfig.presets.german_single_family_home
    payload = {
        "building_config": building_config.to_dict(),
        "options": {"diesel_car": True},
        "system_setup_config": {"weather_location": "BREMEN"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload), encoding="utf8")

    from_file = Config.load_from_json(str(config_path))
    from_dict = Config.load_from_dict(json.loads(json.dumps(payload)))
    assert from_file.weather_location == from_dict.weather_location == "BREMEN"
    assert from_file.car_config is not None
    assert from_dict.car_config is not None


def test_load_from_json_windows_cr_workaround(tmp_path: Path) -> None:
    """A trailing carriage return in the path is stripped as a fallback."""
    payload = {"system_setup_config": {"weather_location": "STUTTGART"}}
    config_path = tmp_path / "cr_config.json"
    config_path.write_text(json.dumps(payload), encoding="utf8")

    # The file exists without the CR; passing a CR-suffixed path must still work.
    config = Config.load_from_json(str(config_path) + "\r")
    assert config.weather_location == "STUTTGART"
