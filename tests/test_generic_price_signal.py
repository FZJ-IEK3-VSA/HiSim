"""Tests for the PriceSignalConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``PriceSignalConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import pytest
from typing import Optional

from hisim.components.generic_price_signal import PriceSignal, PriceSignalConfig
from hisim.component import ComponentID


def _assert_defaults(config: PriceSignalConfig, expected_building: Optional[str]) -> None:
    """Assert ``config`` carries the documented default values.

    Only the component identity is allowed to vary; everything else must match
    the hardcoded defaults in ``get_default_price_signal_config``.
    """
    assert config.component_id.building == expected_building
    assert config.component_id.name == "PriceSignal"
    assert config.country == "Germany"
    assert config.pricing_scheme == "fixed"
    assert config.installed_capacity == 10e3
    assert config.price_signal_type == "dummy"
    assert config.fixed_price == []
    assert config.static_tou_price == []
    assert config.price_injection == 0.0
    assert config.predictive_control is False
    assert config.prediction_horizon is None


@pytest.mark.base
def test_get_default_price_signal_config_defaults() -> None:
    """``get_default_price_signal_config()`` returns the documented defaults."""
    config = PriceSignalConfig.get_default_price_signal_config()
    assert isinstance(config, PriceSignalConfig)
    _assert_defaults(config, None)


@pytest.mark.base
def test_get_default_price_signal_config_custom_building() -> None:
    """Only the building changes when a component_id is passed explicitly."""
    config = PriceSignalConfig.get_default_price_signal_config(
        component_id=ComponentID(name="PriceSignal", building="BUI2")
    )
    assert isinstance(config, PriceSignalConfig)
    _assert_defaults(config, "BUI2")


@pytest.mark.base
def test_get_default_price_signal_config_empty_building() -> None:
    """An empty building is accepted unchanged (no coercion/rejection)."""
    config = PriceSignalConfig.get_default_price_signal_config(
        component_id=ComponentID(name="PriceSignal", building="")
    )
    assert isinstance(config, PriceSignalConfig)
    _assert_defaults(config, "")


@pytest.mark.base
def test_get_main_classname() -> None:
    """``get_main_classname`` returns the fully-qualified ``PriceSignal`` path."""
    classname = PriceSignalConfig.get_main_classname()
    assert isinstance(classname, str)
    assert classname == PriceSignal.get_full_classname()
    assert classname == "hisim.components.generic_price_signal.PriceSignal"


@pytest.mark.base
def test_config_is_pure_and_does_not_mutate() -> None:
    """Two calls with different buildings produce independent configs."""
    first = PriceSignalConfig.get_default_price_signal_config()
    second = PriceSignalConfig.get_default_price_signal_config(
        component_id=ComponentID(name="PriceSignal", building="BUI2")
    )
    assert first.component_id.building is None
    assert second.component_id.building == "BUI2"
    # The list defaults are fresh instances, not shared mutable state.
    first.fixed_price.append(1.0)
    assert not second.fixed_price
