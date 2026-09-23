"""The heat pump's space-heating controller never waits below the room setpoint (hisim-q1rm).

The controller switches on when the buffer water falls ``lower_temperature_offset`` (5 K) below
the flow temperature the emitter circuit asks for. A floor-heating curve asks for barely more
than the room setpoint in mild weather, which put that point below room temperature: the buffer
never reached it, and the full-year RenoVisor mockup kept the heat pump off from spring to autumn
while the room sat at 18-19 °C against 21 °C. The switch-on point is now never below the
building's heating setpoint; where the band already worked, nothing changes.
"""

import pytest

from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.config import SizingContext
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base

ROOM_SETPOINT = 21.0
OFFSET = 5.0


def controller() -> MoreAdvancedHeatPumpHPLibControllerSpaceHeating:
    """An idle floor-heating controller of a house heated to 21 °C."""
    config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
        SizingContext(
            set_heating_temperature_in_celsius=ROOM_SETPOINT,
            heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            set_heating_threshold_outside_temperature_in_celsius=20.0,
        )
    )
    component = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900)
    )
    component.controller_heatpumpmode = "off"
    return component


def after(water: float, flow: float, modifier: float = 0.0) -> str:
    """The controller's mode after one decision on an idle machine."""
    component = controller()
    component.conditions_on_off(
        water_temperature_input_in_celsius=water,
        set_heating_flow_temperature_in_celsius=flow,
        summer_heating_mode="on",
        storage_temperature_modifier=modifier,
        upper_temperature_offset_for_state_conditions_in_celsius=OFFSET,
        lower_temperature_offset_for_state_conditions_in_celsius=OFFSET,
    )
    return component.controller_heatpumpmode


def test_a_mild_day_s_floor_heating_starts_when_the_buffer_is_below_the_room_setpoint() -> None:
    """The mockup's July case: flow 23.4 °C, buffer 18.6 °C. It used to wait for 18.4 °C."""
    assert after(water=18.6, flow=23.4) == "heating"


def test_the_band_still_decides_where_it_lies_above_the_setpoint() -> None:
    """A winter flow of 35 °C switches on below 30 °C, as before."""
    assert after(water=31.0, flow=35.0) == "off"
    assert after(water=29.0, flow=35.0) == "heating"


def test_water_at_the_room_setpoint_does_not_start_a_mild_day() -> None:
    """At or above the setpoint and above flow - 5 K, the idle machine stays idle."""
    assert after(water=21.5, flow=23.4) == "off"


def test_the_energy_manager_s_raise_moves_the_floor_too() -> None:
    """The storage modifier shifts the whole switch-on point, the floor included."""
    component = controller()
    assert component.switch_on_temperature_in_celsius(23.4, OFFSET, 2.0) == pytest.approx(ROOM_SETPOINT + 2.0)
    assert component.switch_on_temperature_in_celsius(35.0, OFFSET, 2.0) == pytest.approx(32.0)


def test_the_setpoint_is_the_building_s_own() -> None:
    """Left AUTO, the field is sized from the building's heating setpoint."""
    assert controller().heatpump_controller_config.set_heating_temperature_for_building_in_celsius == ROOM_SETPOINT
