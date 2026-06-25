"""Tests for the ``ModularHouseholdConfig`` defaults.

These tests cover ``ModularHouseholdConfig.get_default`` -- the only deterministic,
side-effect-free function in
``hisim.modular_household.interface_configs.modular_household_config``. The
``write_config`` and ``read_config`` helpers perform file I/O and are
intentionally not exercised here.
"""

# clean

import pytest

from hisim.modular_household.interface_configs import (
    archetype_config,
    system_config,
)
from hisim.modular_household.interface_configs.modular_household_config import (
    ModularHouseholdConfig,
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
