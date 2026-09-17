"""Tests for the advanced EV battery (bslib) config and state helpers.

Covers three pure functions of ``hisim.components.advanced_ev_battery_bslib``
that previously had no unit tests:

* ``CarBatteryConfig.preset_standard``
* ``CarBatteryConfig.get_main_classname``
* ``EVBatteryState.clone``

These tests need no simulation parameters, no bslib calls and no I/O.
"""

import pytest

from hisim.components.advanced_ev_battery_bslib import (
    CarBattery,
    CarBatteryConfig,
    EVBatteryState,
)
from hisim.config import ComponentID


@pytest.mark.base
def test_preset_standard_returns_expected_defaults() -> None:
    """``preset_standard`` returns a deterministic config with the fleet's battery."""
    cfg = CarBatteryConfig.preset_standard("CarBattery")

    assert cfg.component_id.building is None
    assert cfg.component_id.name == "CarBattery"
    assert cfg.system_id == "SG1"
    assert cfg.p_inv_custom == 1e4
    assert cfg.e_bat_custom == 30
    assert cfg.source_weight == 1
    assert cfg.total_charged_energy_in_kilowatthour == 0
    assert cfg.total_discharged_energy_in_kilowatthour == 0


@pytest.mark.base
def test_an_assigned_component_id_overrides_building_and_name() -> None:
    """A ``component_id`` assigned after the preset overrides the identity, the rest stay."""
    cfg = CarBatteryConfig.preset_standard("CarBattery")
    cfg.component_id = ComponentID(name="Y", building="X")

    assert cfg.component_id.building == "X"
    assert cfg.component_id.name == "Y"
    # All other fields keep their defaults.
    assert cfg.system_id == "SG1"
    assert cfg.p_inv_custom == 1e4
    assert cfg.e_bat_custom == 30
    assert cfg.source_weight == 1
    assert cfg.total_charged_energy_in_kilowatthour == 0
    assert cfg.total_discharged_energy_in_kilowatthour == 0


@pytest.mark.base
def test_get_main_classname_matches_car_battery_full_classname() -> None:
    """``get_main_classname`` returns ``CarBattery.get_full_classname()``.

    The expected string is compared both literally and against the runtime value of
    ``CarBattery.get_full_classname()`` to avoid hardcoding mistakes.
    """
    expected = "hisim.components.advanced_ev_battery_bslib.CarBattery"

    assert CarBatteryConfig.get_main_classname() == expected
    assert CarBatteryConfig.get_main_classname() == CarBattery.get_full_classname()


@pytest.mark.base
def test_ev_battery_state_clone_copies_soc() -> None:
    """``clone`` returns a new ``EVBatteryState`` with the same ``soc``."""
    state = EVBatteryState(soc=0.5)
    cloned = state.clone()

    assert cloned.soc == 0.5
    assert cloned is not state


@pytest.mark.base
def test_ev_battery_state_clone_is_independent() -> None:
    """Mutating the original state does not affect the cloned state."""
    state = EVBatteryState(soc=0.5)
    cloned = state.clone()

    state.soc = 0.9

    assert cloned.soc == 0.5


@pytest.mark.base
def test_ev_battery_state_clone_edge_cases() -> None:
    """``clone`` preserves the boundary state-of-charge values 0.0 and 1.0."""
    assert EVBatteryState(soc=0.0).clone().soc == 0.0
    assert EVBatteryState(soc=1.0).clone().soc == 1.0
