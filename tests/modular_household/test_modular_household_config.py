"""Tests for the ``ModularHouseholdConfig`` defaults and ``read_config`` fallbacks.

These tests cover ``ModularHouseholdConfig.get_default`` and the ``read_config``
helper's fallback behavior when the config file is missing, unparseable, or
contains no usable sub-configurations.
"""

# clean

import warnings
from pathlib import Path

import pytest
from dataclass_wizard.errors import ParseError

from hisim.modular_household.interface_configs import (
    archetype_config,
    system_config,
)
from hisim.modular_household.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
    _config_from_setup_dict,
    read_config,
)


@pytest.mark.base
def test_get_default_returns_modular_household_config() -> None:
    """``get_default`` returns a fully populated ``ModularHouseholdConfig`` instance."""
    cfg: ModularHouseholdConfig = ModularHouseholdConfig.get_default()
    assert isinstance(cfg, ModularHouseholdConfig)


@pytest.mark.base
def test_get_default_populates_system_config() -> None:
    """``get_default`` populates ``system_config_`` with a ``SystemConfig`` instance."""
    cfg: ModularHouseholdConfig = ModularHouseholdConfig.get_default()
    assert cfg.system_config_ is not None
    assert isinstance(cfg.system_config_, system_config.SystemConfig)


@pytest.mark.base
def test_get_default_populates_archetype_config() -> None:
    """``get_default`` populates ``archetype_config_`` with an ``ArcheTypeConfigModular``."""
    cfg: ModularHouseholdConfig = ModularHouseholdConfig.get_default()
    assert cfg.archetype_config_ is not None
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfigModular)


@pytest.mark.base
def test_get_default_yields_distinct_instances() -> None:
    """Repeated ``get_default`` calls return distinct instances with no shared mutable state."""
    first: ModularHouseholdConfig = ModularHouseholdConfig.get_default()
    second: ModularHouseholdConfig = ModularHouseholdConfig.get_default()
    # The two configs must be separate objects...
    assert first is not second
    # ...and their sub-config fields must not be shared mutable state either.
    assert first.system_config_ is not second.system_config_
    assert first.archetype_config_ is not second.archetype_config_
    # The sub-config field types must still match between the two instances.
    assert type(first.system_config_) is type(second.system_config_)
    assert type(first.archetype_config_) is type(second.archetype_config_)


@pytest.mark.base
def test_direct_construction_leaves_sub_configs_none() -> None:
    """Constructing ``ModularHouseholdConfig`` directly leaves the sub-configs as ``None``.

    This confirms that the defaults are populated by ``get_default`` and are not the
    dataclass field defaults themselves.
    """
    cfg: ModularHouseholdConfig = ModularHouseholdConfig()
    assert cfg.system_config_ is None
    assert cfg.archetype_config_ is None


# --------------------------------------------------------------------------- #
# read_config fallback tests
# --------------------------------------------------------------------------- #


def _assert_default_config(cfg: ModularHouseholdConfig) -> None:
    """Assert that *cfg* is a ``ModularHouseholdConfig`` with both sub-configs populated."""
    assert isinstance(cfg, ModularHouseholdConfig)
    assert cfg.system_config_ is not None
    assert isinstance(cfg.system_config_, system_config.SystemConfig)
    assert cfg.archetype_config_ is not None
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfigModular)


@pytest.mark.base
def test_read_config_with_none_pathname_returns_default() -> None:
    """``read_config(None)`` falls back to a default config with both sub-configs."""
    cfg = read_config(None)
    _assert_default_config(cfg)


@pytest.mark.base
def test_read_config_with_missing_file_returns_default(tmp_path: Path) -> None:
    """A non-existent path triggers the ``FileNotFoundError`` fallback to defaults."""
    missing = tmp_path / "does_not_exist.json"
    cfg = read_config(str(missing))
    _assert_default_config(cfg)


@pytest.mark.base
def test_read_config_with_empty_object_returns_default(tmp_path: Path) -> None:
    """An empty JSON object (no ``system_setup_config`` key) falls back to defaults.

    ``from_dict(None)`` raises a ``MissingData`` error (a ``ParseError`` subclass),
    which is caught by the ``except`` clause.
    """
    config_path = tmp_path / "empty.json"
    config_path.write_text("{}", encoding="utf-8")
    cfg = read_config(str(config_path))
    _assert_default_config(cfg)


@pytest.mark.base
def test_read_config_with_both_subconfigs_missing_returns_default(
    tmp_path: Path,
) -> None:
    """A config whose ``system_setup_config`` has neither sub-config returns defaults.

    ``from_dict({})`` succeeds but leaves both ``system_config_`` and
    ``archetype_config_`` as ``None``.  ``read_config`` detects this and falls
    back to a default config instead of returning a config with no sub-configs.
    """
    config_path = tmp_path / "both_none.json"
    config_path.write_text('{"system_setup_config": {}}', encoding="utf-8")
    cfg = read_config(str(config_path))
    _assert_default_config(cfg)


@pytest.mark.base
def test_read_config_both_missing_logs_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """When both sub-configs are missing, a warning is logged."""
    config_path = tmp_path / "both_none.json"
    config_path.write_text('{"system_setup_config": {}}', encoding="utf-8")
    read_config(str(config_path))
    captured = capsys.readouterr()
    assert "WRN:" in captured.out
    assert "neither a system nor an archetype config" in captured.out


@pytest.mark.base
def test_read_config_missing_file_logs_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """When the file cannot be read, a warning is logged."""
    missing = tmp_path / "does_not_exist.json"
    read_config(str(missing))
    captured = capsys.readouterr()
    assert "WRN:" in captured.out
    assert "Could not read" in captured.out


@pytest.mark.base
def test_read_config_preserves_present_subconfig(tmp_path: Path) -> None:
    """When only one sub-config is present, it is preserved and the other is defaulted."""
    config_path = tmp_path / "sys_only.json"
    config_path.write_text(
        '{"system_setup_config": {"systemConfig_": {"pvPeakPower": 9999.0}}}',
        encoding="utf-8",
    )
    cfg = read_config(str(config_path))
    # The system_config_ from the file is preserved (not replaced by a default).
    assert isinstance(cfg.system_config_, system_config.SystemConfig)
    assert cfg.system_config_.pv_peak_power == 9999.0
    # The missing archetype_config_ is filled with a default.
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfigModular)


# --------------------------------------------------------------------------- #
# _config_from_setup_dict pure-core tests (no filesystem required)
# --------------------------------------------------------------------------- #


@pytest.mark.base
def test_config_from_setup_dict_migrates_legacy_archetype_keys() -> None:
    """The pure core migrates deprecated archetype keys without a JSON file."""
    setup_dict = {
        "archetype_config_": {
            "mobility_set": {"Name": "Bus and one 30 km/h Car", "Guid": {"StrVal": "dev"}},
            "mobility_distance": {
                "Name": "Travel Route Set for 15km Commuting Distance",
                "Guid": {"StrVal": "route"},
            },
            "building_code": "TEST_PURE",
        }
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = _config_from_setup_dict(setup_dict)
    assert cfg.archetype_config_ is not None
    assert cfg.archetype_config_.transportation_device_set is not None
    assert cfg.archetype_config_.transportation_device_set.Name == "Bus and one 30 km/h Car"
    assert cfg.archetype_config_.commuting_travel_route_set is not None
    assert (
        cfg.archetype_config_.commuting_travel_route_set.Name
        == "Travel Route Set for 15km Commuting Distance"
    )
    assert cfg.archetype_config_.building_code == "TEST_PURE"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.base
def test_config_from_setup_dict_fills_missing_subconfigs() -> None:
    """An empty setup dict yields a config with both sub-configs defaulted."""
    cfg = _config_from_setup_dict({})
    assert isinstance(cfg, ModularHouseholdConfig)
    assert isinstance(cfg.system_config_, system_config.SystemConfig)
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfigModular)


@pytest.mark.base
def test_config_from_setup_dict_preserves_present_subconfig() -> None:
    """A present system config is preserved; the missing archetype is defaulted."""
    cfg = _config_from_setup_dict({"system_config_": {"pv_peak_power": 4242.0}})
    assert isinstance(cfg.system_config_, system_config.SystemConfig)
    assert cfg.system_config_.pv_peak_power == 4242.0
    assert isinstance(cfg.archetype_config_, archetype_config.ArcheTypeConfigModular)


@pytest.mark.base
def test_config_from_setup_dict_none_raises_parse_error() -> None:
    """A ``None`` setup dict is a parse failure that propagates from the pure core."""
    with pytest.raises(ParseError):
        _config_from_setup_dict(None)
