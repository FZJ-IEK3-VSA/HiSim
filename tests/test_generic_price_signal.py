"""Tests for the PriceSignalConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``PriceSignalConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import pytest

from hisim.components.generic_price_signal import PriceSignal, PriceSignalConfig


def _assert_defaults(config: PriceSignalConfig, building_name: str) -> None:
    """Assert ``config`` carries the documented default values.

    Only ``building_name`` is allowed to vary; everything else must match
    the hardcoded defaults in ``get_default_price_signal_config``.
    """
    assert config.building_name == building_name
    assert config.name == "PriceSignal"
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
    _assert_defaults(config, "BUI1")


@pytest.mark.base
def test_get_default_price_signal_config_custom_building_name() -> None:
    """Only ``building_name`` changes when it is passed explicitly."""
    config = PriceSignalConfig.get_default_price_signal_config(building_name="BUI2")
    assert isinstance(config, PriceSignalConfig)
    _assert_defaults(config, "BUI2")


@pytest.mark.base
def test_get_default_price_signal_config_empty_building_name() -> None:
    """An empty ``building_name`` is accepted unchanged (no coercion/rejection)."""
    config = PriceSignalConfig.get_default_price_signal_config(building_name="")
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
    """Two calls with different ``building_name`` produce independent configs."""
    first = PriceSignalConfig.get_default_price_signal_config(building_name="BUI1")
    second = PriceSignalConfig.get_default_price_signal_config(building_name="BUI2")
    assert first.building_name == "BUI1"
    assert second.building_name == "BUI2"
    # The list defaults are fresh instances, not shared mutable state.
    first.fixed_price.append(1.0)
    assert second.fixed_price == []
