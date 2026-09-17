"""Tests for the SimpleControllerConfig preset and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``SimpleControllerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O. The remaining methods (``__init__``, ``i_save_state``,
``i_restore_state``, ``i_simulate``) mutate instance/``stsv`` state or require
a constructed ``SimpleController`` with ``SimulationParameters`` and channel
wiring, so they are out of scope here.
"""

import dataclasses
from typing import Optional

import pytest

from hisim.components.controller_l1_example_controller import (
    SimpleController,
    SimpleControllerConfig,
)
from hisim.config import ComponentID


def _assert_defaults(config: SimpleControllerConfig, expected_building: Optional[str]) -> None:
    """Assert ``config`` carries the documented default values.

    Only the component identity is allowed to vary; the class has no other field.
    """
    assert isinstance(config, SimpleControllerConfig)
    assert config.component_id.name == "SimpleController"
    assert config.component_id.building == expected_building


@pytest.mark.base
def test_preset_standard_defaults() -> None:
    """``preset_standard`` returns the documented defaults under the name it is given."""
    config = SimpleControllerConfig.preset_standard("SimpleController")
    _assert_defaults(config, None)


@pytest.mark.base
def test_a_preset_config_takes_a_building_by_replacement() -> None:
    """A preset takes only a name, so an identity carrying a building is substituted afterwards."""
    config = dataclasses.replace(
        SimpleControllerConfig.preset_standard("SimpleController"),
        component_id=ComponentID(name="SimpleController", building="BUI2"),
    )
    _assert_defaults(config, "BUI2")


@pytest.mark.base
def test_an_empty_building_label_is_refused() -> None:
    """An empty building label is refused at the identity, naming the field.

    Passed through, it would silently join into a key with a leading underscore — a component
    nobody addressed that way — so the identity layer refuses it where it is written.
    """
    with pytest.raises(ValueError, match="building label"):
        ComponentID(name="SimpleController", building="")


@pytest.mark.base
def test_any_identifier_is_accepted_as_the_building() -> None:
    """Any identifier serves as the building of a preset-built configuration."""
    config = dataclasses.replace(
        SimpleControllerConfig.preset_standard("SimpleController"),
        component_id=ComponentID(name="SimpleController", building="haus42"),
    )
    _assert_defaults(config, "haus42")


@pytest.mark.base
def test_get_main_classname() -> None:
    """``get_main_classname`` returns the fully-qualified ``SimpleController`` path."""
    classname = SimpleControllerConfig.get_main_classname()
    assert isinstance(classname, str)
    assert "SimpleController" in classname
    assert classname == SimpleController.get_full_classname()
    assert SimpleControllerConfig.MAIN_CLASS == classname


@pytest.mark.base
def test_config_is_pure_and_does_not_mutate() -> None:
    """Two calls with different buildings produce independent configs."""
    first = SimpleControllerConfig.preset_standard("SimpleController")
    second = dataclasses.replace(
        SimpleControllerConfig.preset_standard("SimpleController"),
        component_id=ComponentID(name="SimpleController", building="BUI2"),
    )
    assert first.component_id.building is None
    assert second.component_id.building == "BUI2"
