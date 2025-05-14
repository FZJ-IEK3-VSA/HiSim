"""Test for generic pv system."""

from typing import Any, Dict, Optional
from unittest.mock import patch
import pytest
from hisim.components.air_conditioner import (
    AirConditionerController,
    AirConditionerControllerConfig,
    AirConditionerControllerState,
)
from hisim import component
from hisim import simulator as sim
from tests import functions_for_testing as fft


@pytest.mark.base
@pytest.mark.parametrize(
    ["start_controller_mode", "current_temperature_deg_c", "mode"],
    [
        ("off", 40, "cooling"),
        ("off", 26.1, "cooling"),
        ("off", 26.0, "off"),
        ("off", 25.9, "off"),
        ("off", 21.0, "off"),
        ("off", 18.0, "off"),
        ("off", 17.9, "heating"),
        ("off", 0, "heating"),
        ("off", -4, "heating"),
    ],
)
def test_determine_mode_returns_correct_operation_mode_for_temperature(
    start_controller_mode, current_temperature_deg_c: float, mode: str
):
    """Test generic pv system."""
    """ GIVEN """
    testee = given_default_testee()

    testee.previous_state = AirConditionerControllerState(
        start_controller_mode, 0, 0, 0.0
    )

    """ WHEN """
    returned_mode = testee.determine_operating_mode(
        current_temperature_deg_c, 0
    )

    """ THEN """
    assert returned_mode == mode


@pytest.mark.base
@pytest.mark.parametrize(
    ["start_controller_mode", "current_temperature_deg_c", "expected_mode"],
    [
        ("off", 40, "off"),
        ("off", 26.1, "off"),
        ("off", 26.0, "off"),
        ("off", 21.0, "off"),
        ("off", 18.0, "off"),
        ("off", 17.9, "off"),
        ("off", 0, "off"),
        ("off", -4, "off"),
        ("heating", 40, "heating"),
        ("heating", 26.1, "heating"),
        ("heating", 26.0, "heating"),
        ("heating", 21.0, "heating"),
        ("heating", 18.0, "heating"),
        ("heating", 17.9, "heating"),
        ("heating", 0, "heating"),
        ("heating", -4, "heating"),
        ("cooling", 40, "cooling"),
        ("cooling", 26.1, "cooling"),
        ("cooling", 26.0, "cooling"),
        ("cooling", 21.0, "cooling"),
        ("cooling", 18.0, "cooling"),
        ("cooling", 17.9, "cooling"),
        ("cooling", 0, "cooling"),
        ("cooling", -4, "cooling"),
    ],
)
def test_determine_mode_returns_correct_operation_mode_for_operating_time(
    start_controller_mode, current_temperature_deg_c: float, expected_mode: str
):
    """ Test determine_operation_mode."""
    """ GIVEN """
    testee = given_default_testee(
        {"minimum_runtime_s": 60 * 15, "minimum_idle_time_s": 60 * 10}
    )
    testee.state = AirConditionerControllerState(
        start_controller_mode, 0, 0, 0.0
    )
    testee.previous_state = testee.state.clone()

    """ WHEN """
    returned_mode = testee.determine_operating_mode(
        current_temperature_deg_c, 0
    )

    """ THEN """
    assert returned_mode == expected_mode


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "operating_mode",
        "current_temperature_deg_c",
        "expected_modulation_percentage",
    ],
    [
        ("off", 40, 0),
        ("cooling", 40, 1.0),
        ("cooling", 31, 1.0),
        ("cooling", 26.5, 0.3055555555),
        ("cooling", 26, 0.1),
        ("cooling", 21, 0.1),
        ("heating", 21, 0.1),
        ("heating", 18, 0.1),
        ("heating", 17.5, 0.3055555555),
        ("heating", 13, 1.0),
        ("heating", 0, 1.0),
        ("heating", -5, 1.0),
    ],
)
def test_modulate_returns_correct_modulation_percentage(
    operating_mode, current_temperature_deg_c, expected_modulation_percentage
):
    """ Test modulate_power."""

    """ GIVEN """
    testee = given_default_testee()

    """ WHEN """
    modulating_percentage = testee.modulate_power(
        current_temperature_deg_c, operating_mode
    )

    """ THEN """
    assert modulating_percentage == pytest.approx(
        expected_modulation_percentage
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ["mocked_mode", "mocked_modulation", "expected_output"],
    [
        ("off", 0.0, 0.0),
        ("cooling", 1.0, -1.0),
        ("heating", 1.0, 1.0),
    ],
)
@patch.object(AirConditionerController, "modulate_power")
@patch.object(AirConditionerController, "determine_operating_mode")
def test_simulate_sets_correct_state_for_operation_mode(
    mock_method_mode,
    mock_method_modulate,
    mocked_mode,
    mocked_modulation,
    expected_output,
):
    """ Test i_simulate."""

    """ GIVEN """
    testee = given_default_testee()
    number_of_outputs = fft.get_number_of_outputs([testee])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )
    fft.add_global_index_of_components([testee])
    mock_method_mode.return_value = mocked_mode
    mock_method_modulate.return_value = mocked_modulation

    """ WHEN """
    testee.i_simulate(0, stsv, False)

    """ THEN """
    assert (
        stsv.values[testee.operation_modulating_signal_channel.global_index]
        == expected_output
    )
    mock_method_modulate.assert_called_once()
    mock_method_mode.assert_called_once()


def given_default_testee(
    config_overwrite: Optional[Dict[str, Any]] = None,
) -> AirConditionerController:
    """Create and configure default testee."""
    if config_overwrite is None:
        config_overwrite = {}
    simulationparameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = AirConditionerControllerConfig.get_default_air_conditioner_controller_config()
    config.heating_set_temperature_deg_c = 18.0
    config.cooling_set_temperature_deg_c = 26.0
    config.minimum_runtime_s = config_overwrite.get("minimum_runtime_s", 0)
    config.minimum_idle_time_s = config_overwrite.get("minimum_idle_time_s", 0)
    config.offset = 0
    testee: AirConditionerController = AirConditionerController(
        simulationparameters,
        config,
        component.DisplayConfig(),
    )
    return testee
