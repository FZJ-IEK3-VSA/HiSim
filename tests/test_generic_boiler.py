"""Test for generic pv system."""

from typing import Any, Dict, Optional
import pytest
from hisim import component
from hisim import simulator as sim
from hisim.components.dual_circuit_system import HeatingMode
from hisim.components.generic_boiler import (
    GenericBoilerController,
    GenericBoilerControllerConfig,
)


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "operating_mode",
        "min_state_time",
        "water_temp_sh",
        "water_temp_dhw",
        "expected_mode",
    ],
    [
        (HeatingMode.OFF, 0, 60, 60, HeatingMode.OFF),
        (HeatingMode.OFF, 0, 40, 60, HeatingMode.SPACE_HEATING),
        (HeatingMode.OFF, 0, 40, 40, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.OFF, 0, 40, 54, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.OFF, 0, 0, 0, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.SPACE_HEATING, 0, 60, 60, HeatingMode.OFF),
        (HeatingMode.DOMESTIC_HOT_WATER, 0, 60, 60, HeatingMode.OFF),
        (HeatingMode.SPACE_HEATING, 0, 60, 40, HeatingMode.DOMESTIC_HOT_WATER),
        (HeatingMode.DOMESTIC_HOT_WATER, 0, 40, 60, HeatingMode.SPACE_HEATING),
        (HeatingMode.OFF, 10, 0, 0, HeatingMode.DOMESTIC_HOT_WATER),
    ],
)
def test_determine_mode_returns_correct_operation_mode_for_temperature_and_time(
    operating_mode: HeatingMode,
    min_state_time: int,
    water_temp_sh: float,
    water_temp_dhw: float,
    expected_mode: str,
):
    """Test determine_mode."""
    """ GIVEN """
    testee = given_default_testee(
        {
            "minimum_runtime_in_seconds": min_state_time,
            "minimum_resting_time_in_seconds": min_state_time,
            "with_domestic_hot_water_preparation": True,
            "set_heating_threshold_outside_temperature_in_celsius": 15,
        }
    )
    testee.controller_mode = operating_mode
    testee.warm_water_temperature_aim_in_celsius = 60
    testee.config.dhw_hysteresis_offset = 5

    daily_avg_outside_temperature = 10
    heating_flow_temperature = 55
    timestep = 5

    """ WHEN """
    _, _ = testee.determine_operating_mode(
        daily_avg_outside_temperature,
        water_temp_sh,
        water_temp_dhw,
        heating_flow_temperature,
        timestep,
    )

    """ THEN """
    assert testee.controller_mode == expected_mode


def given_default_testee(
    config_overwrite: Optional[Dict[str, Any]] = None,
) -> GenericBoilerController:
    """Create and configure default testee."""
    if config_overwrite is None:
        config_overwrite = {}
    simulationparameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=60
    )
    config = GenericBoilerControllerConfig.get_default_on_off_generic_boiler_controller_config(
        maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000, with_domestic_hot_water_preparation=True
    )
    config.minimum_runtime_in_seconds = config_overwrite.get(
        "minimum_runtime_in_seconds", 0
    )
    config.minimum_resting_time_in_seconds = config_overwrite.get(
        "minimum_resting_time_in_seconds", 0
    )
    config.offset = 0
    testee = GenericBoilerController(
        simulationparameters,
        config,
        component.DisplayConfig(),
    )
    return testee
