"""Tests for the generic L1 runtime controller config defaults and state transitions.

These are pure, side-effect-free unit tests for the small data holders in
``hisim.components.controller_l1_generic_runtime``: the ``L1Config`` static
factory methods and the ``L1GenericRuntimeControllerState`` transition methods.
They need no ``SimulationParameters`` and no I/O.
"""

import pytest

from hisim.components.controller_l1_generic_runtime import (
    L1Config,
    L1GenericRuntimeControllerState,
)


# --------------------------------------------------------------------------- #
# L1Config.get_default_config / get_default_config_heatpump
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_get_default_config_returns_expected_fields() -> None:
    """get_default_config builds an L1Config with the documented default values."""
    config = L1Config.get_default_config("HP")
    assert config.building_name == "BUI1"
    assert config.name == "RuntimeController_HP"
    assert config.source_weight == 1
    assert config.min_operation_time_in_seconds == 3600
    assert config.min_idle_time_in_seconds == 900


@pytest.mark.base
def test_get_default_config_accepts_custom_building_name() -> None:
    """Passing building_name overrides the default while leaving other fields unchanged."""
    config = L1Config.get_default_config("HP", building_name="BUI2")
    assert config.building_name == "BUI2"
    assert config.name == "RuntimeController_HP"
    assert config.source_weight == 1
    assert config.min_operation_time_in_seconds == 3600
    assert config.min_idle_time_in_seconds == 900


@pytest.mark.base
def test_get_default_config_heatpump_returns_expected_fields() -> None:
    """get_default_config_heatpump builds an L1Config with the heat-pump defaults."""
    config = L1Config.get_default_config_heatpump("HP")
    assert config.name == "L1RuntimeControllerHP"
    assert config.building_name == "BUI1"
    assert config.source_weight == 1
    assert config.min_operation_time_in_seconds == 3600 * 3
    assert config.min_idle_time_in_seconds == 3600


@pytest.mark.base
def test_get_default_config_heatpump_accepts_custom_building_name() -> None:
    """get_default_config_heatpump honors a custom building_name."""
    config = L1Config.get_default_config_heatpump("HP", building_name="BUI2")
    assert config.building_name == "BUI2"
    assert config.name == "L1RuntimeControllerHP"
    assert config.source_weight == 1
    assert config.min_operation_time_in_seconds == 10800
    assert config.min_idle_time_in_seconds == 3600


@pytest.mark.base
def test_get_default_config_empty_name_concatenation() -> None:
    """An empty name still produces a valid (if degenerate) controller name."""
    config = L1Config.get_default_config("")
    assert config.name == "RuntimeController_"
    assert config.building_name == "BUI1"
    assert config.source_weight == 1


# --------------------------------------------------------------------------- #
# L1GenericRuntimeControllerState transitions
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_activate_sets_on_off_and_activation_time_step() -> None:
    """activate flips the state on and records the activation timestep."""
    state = L1GenericRuntimeControllerState(
        on_off=0, activation_time_step=0, deactivation_time_step=0
    )
    state.activate(5)
    assert state.on_off == 1
    assert state.activation_time_step == 5
    # deactivation_time_step must be untouched by activate
    assert state.deactivation_time_step == 0


@pytest.mark.base
def test_deactivate_sets_on_off_and_deactivation_time_step() -> None:
    """deactivate flips the state off and records the deactivation timestep."""
    state = L1GenericRuntimeControllerState(
        on_off=1, activation_time_step=3, deactivation_time_step=0
    )
    state.deactivate(10)
    assert state.on_off == 0
    assert state.deactivation_time_step == 10
    # activation_time_step must be untouched by deactivate
    assert state.activation_time_step == 3


@pytest.mark.base
def test_clone_returns_distinct_instance_with_equal_fields() -> None:
    """clone produces an independent copy; mutating it does not affect the original."""
    original = L1GenericRuntimeControllerState(
        on_off=1, activation_time_step=7, deactivation_time_step=4
    )
    cloned = original.clone()
    assert cloned is not original
    assert cloned.on_off == original.on_off
    assert cloned.activation_time_step == original.activation_time_step
    assert cloned.deactivation_time_step == original.deactivation_time_step

    # mutate the clone, original must be unaffected
    cloned.activate(42)
    assert original.on_off == 1
    assert original.activation_time_step == 7
    assert original.deactivation_time_step == 4


@pytest.mark.base
def test_activate_and_deactivate_at_timestep_zero() -> None:
    """Activating/deactivating at timestep 0 sets the fields to 0 without error."""
    state = L1GenericRuntimeControllerState(
        on_off=0, activation_time_step=99, deactivation_time_step=99
    )
    state.activate(0)
    assert state.on_off == 1
    assert state.activation_time_step == 0
    assert state.deactivation_time_step == 99

    state.deactivate(0)
    assert state.on_off == 0
    assert state.deactivation_time_step == 0
    assert state.activation_time_step == 0


@pytest.mark.base
def test_default_constructed_state_honors_defaults() -> None:
    """Omitting the timestep args yields the documented default of 0."""
    state = L1GenericRuntimeControllerState(on_off=1)
    assert state.on_off == 1
    assert state.activation_time_step == 0
    assert state.deactivation_time_step == 0


@pytest.mark.base
def test_i_prepare_simulation_is_a_noop() -> None:
    """i_prepare_simulation does nothing and mutates no state."""
    state = L1GenericRuntimeControllerState(
        on_off=1, activation_time_step=2, deactivation_time_step=3
    )
    # Should not raise and should leave the state untouched.
    state.i_prepare_simulation()
    assert state.on_off == 1
    assert state.activation_time_step == 2
    assert state.deactivation_time_step == 3
