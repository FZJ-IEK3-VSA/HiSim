"""Test for generic pv system."""

from typing import Any, Dict, Optional
import pytest
from hisim import component
from hisim import simulator as sim
from hisim.components.generic_boiler import (
    GenericBoilerController,
    GenericBoilerControllerConfig,
)


@pytest.mark.base
@pytest.mark.parametrize(
    [
        "summer_heating_mode",
        "controller_generic_boilermode",
        "min_state_time",
        "current_temperature_deg_c",
        "expected_mode",
    ],
    [
        ("on", "off", 0, 60, "off"),
        ("on", "off", 0, 40, "heating"),
        ("on", "off", 0, 0, "heating"),
        ("off", "off", 0, 60, "off"),
        ("off", "off", 0, 40, "off"),
        ("off", "off", 0, 0, "off"),
        ("on", "heating", 0, 60, "off"),
        ("on", "heating", 0, 40, "heating"),
        ("on", "heating", 0, 0, "heating"),
        ("on", "heating", 60, 60, "heating"),
        ("on", "off", 60, 20, "off"),
    ],
)
def test_determine_mode_returns_correct_operation_mode_for_temperature_and_time(
    summer_heating_mode: str,
    controller_generic_boilermode: str,
    min_state_time: int,
    current_temperature_deg_c: float,
    expected_mode: str,
):
    """Test generic pv system."""
    """ GIVEN """
    testee = given_default_testee(
        {
            "minimum_runtime_in_seconds": min_state_time,
            "minimum_resting_time_in_seconds": min_state_time,
        }
    )
    testee.controller_generic_boilermode = controller_generic_boilermode
    heating_flow_temperature_deg_c = 55
    timestep = 0

    """ WHEN """
    testee.conditions_on_off(
        timestep,
        current_temperature_deg_c,
        heating_flow_temperature_deg_c,
        summer_heating_mode=summer_heating_mode,
    )

    """ THEN """
    assert testee.controller_generic_boilermode == expected_mode


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
        maximal_thermal_power_in_watt=2500, minimal_thermal_power_in_watt=1000
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
