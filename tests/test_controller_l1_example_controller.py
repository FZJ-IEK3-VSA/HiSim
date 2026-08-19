"""Tests for the SimpleControllerConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``SimpleControllerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O. The remaining methods (``__init__``, ``i_save_state``,
``i_restore_state``, ``i_simulate``) mutate instance/``stsv`` state or require
a constructed ``SimpleController`` with ``SimulationParameters`` and channel
wiring, so they are out of scope here.
"""

from typing import Optional

import pytest

from hisim.components.controller_l1_example_controller import (
    SimpleController,
    SimpleControllerConfig,
)
from hisim.component import ComponentID


def _assert_defaults(config: SimpleControllerConfig, expected_building: Optional[str]) -> None:
    """Assert ``config`` carries the documented default values.

    Only the component identity is allowed to vary; everything else must match the
    hardcoded defaults in ``get_default_config``.
    """
    assert isinstance(config, SimpleControllerConfig)
    assert config.component_id.name == "SimpleController"
    assert config.component_id.building == expected_building


@pytest.mark.base
def test_get_default_config_defaults() -> None:
    """``get_default_config()`` returns the documented defaults."""
    config = SimpleControllerConfig.get_default_config()
    _assert_defaults(config, None)


@pytest.mark.base
def test_get_default_config_custom_building() -> None:
    """Only the building changes when a component_id is passed explicitly."""
    config = SimpleControllerConfig.get_default_config(ComponentID(name="SimpleController", building="BUI2"))
    _assert_defaults(config, "BUI2")


@pytest.mark.base
def test_get_default_config_empty_building() -> None:
    """An empty building is accepted unchanged (no coercion/rejection)."""
    config = SimpleControllerConfig.get_default_config(component_id=ComponentID(name="SimpleController", building=""))
    _assert_defaults(config, "")


@pytest.mark.base
def test_get_default_config_arbitrary_building() -> None:
    """``get_default_config`` forwards any string as the building."""
    config = SimpleControllerConfig.get_default_config(
        component_id=ComponentID(name="SimpleController", building="haus42")
    )
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
    """Two calls with different buildings produce independent configs."""
    first = SimpleControllerConfig.get_default_config()
    second = SimpleControllerConfig.get_default_config(
        component_id=ComponentID(name="SimpleController", building="BUI2")
    )
    assert first.component_id.building is None
    assert second.component_id.building == "BUI2"
