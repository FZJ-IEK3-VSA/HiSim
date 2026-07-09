"""Tests for the SimpleControllerConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``SimpleControllerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O. The remaining methods (``__init__``, ``i_save_state``,
``i_restore_state``, ``i_simulate``) mutate instance/``stsv`` state or require
a constructed ``SimpleController`` with ``SimulationParameters`` and channel
wiring, so they are out of scope here.
"""

import pytest

from hisim.components.controller_l1_example_controller import (
    SimpleController,
    SimpleControllerConfig,
)


def _assert_defaults(config: SimpleControllerConfig, building_name: str) -> None:
    """Assert ``config`` carries the documented default values.

    Only ``building_name`` is allowed to vary; everything else must match the
    hardcoded defaults in ``get_default_config``.
    """
    assert isinstance(config, SimpleControllerConfig)
    assert config.name == "SimpleController"
    assert config.building_name == building_name


@pytest.mark.base
def test_get_default_config_defaults() -> None:
    """``get_default_config()`` returns the documented defaults."""
    config = SimpleControllerConfig.get_default_config()
    _assert_defaults(config, "BUI1")


@pytest.mark.base
def test_get_default_config_custom_building_name() -> None:
    """Only ``building_name`` changes when it is passed explicitly."""
    config = SimpleControllerConfig.get_default_config("BUI2")
    _assert_defaults(config, "BUI2")


@pytest.mark.base
def test_get_default_config_empty_building_name() -> None:
    """An empty ``building_name`` is accepted unchanged (no coercion/rejection)."""
    config = SimpleControllerConfig.get_default_config(building_name="")
    _assert_defaults(config, "")


@pytest.mark.base
def test_get_default_config_arbitrary_building_name() -> None:
    """``get_default_config`` forwards any string as ``building_name``."""
    config = SimpleControllerConfig.get_default_config(building_name="haus42")
    _assert_defaults(config, "haus42")


@pytest.mark.base
def test_get_main_classname() -> None:
    """``get_main_classname`` returns the fully-qualified ``SimpleController`` path."""
    classname = SimpleControllerConfig.get_main_classname()
    assert isinstance(classname, str)
    assert "SimpleController" in classname
    assert classname == SimpleController.get_full_classname()


@pytest.mark.base
def test_config_is_pure_and_does_not_mutate() -> None:
    """Two calls with different ``building_name`` produce independent configs."""
    first = SimpleControllerConfig.get_default_config(building_name="BUI1")
    second = SimpleControllerConfig.get_default_config(building_name="BUI2")
    assert first.building_name == "BUI1"
    assert second.building_name == "BUI2"
