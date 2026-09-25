"""The heat pump's space-heating band never lies below the room setpoint, in either control mode (hisim-q1rm)."""

import pytest

from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.config import AUTO, ConfigSizingError, SizingContext
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base

ROOM_SETPOINT = 21.0
OFFSET = 5.0


def context(room_setpoint: float = ROOM_SETPOINT) -> SizingContext:
    """The facts a floor-heated house heated to ``room_setpoint`` contributes."""
    return SizingContext(
        set_heating_temperature_in_celsius=room_setpoint,
        heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
        set_heating_threshold_outside_temperature_in_celsius=20.0,
    )


def controller(mode: int = 1, state: str = "off") -> MoreAdvancedHeatPumpHPLibControllerSpaceHeating:
    """A floor-heating controller of a house heated to 21 °C, in the given control mode and state."""
    config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(context())
    config.mode = mode
    component = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900)
    )
    component.controller_heatpumpmode = state
    return component


def after(water: float, flow: float, state: str = "off", modifier: float = 0.0) -> str:
    """The mode-1 controller's mode after one decision."""
    component = controller(mode=1, state=state)
    component.conditions_on_off(
        water_temperature_input_in_celsius=water,
        set_heating_flow_temperature_in_celsius=flow,
        summer_heating_mode="on",
        storage_temperature_modifier=modifier,
        upper_temperature_offset_for_state_conditions_in_celsius=OFFSET,
        lower_temperature_offset_for_state_conditions_in_celsius=OFFSET,
    )
    return str(component.controller_heatpumpmode)


def after_mode_2(water: float, flow: float, state: str = "off") -> str:
    """The mode-2 (heating, cooling, off) controller's mode after one decision, cooling out of season."""
    component = controller(mode=2, state=state)
    component.conditions_heating_cooling_off(
        water_temperature_input_in_celsius=water,
        set_heating_flow_temperature_in_celsius=flow,
        summer_heating_mode="on",
        summer_cooling_mode="off",
        storage_temperature_modifier=0.0,
        upper_temperature_offset_for_state_conditions_in_celsius=OFFSET,
        lower_temperature_offset_for_state_conditions_in_celsius=OFFSET,
    )
    return str(component.controller_heatpumpmode)


def test_a_mild_day_s_floor_heating_starts_when_the_buffer_is_below_the_room_setpoint() -> None:
    """The mockup's July case: flow 23.4 °C, buffer 18.6 °C. It used to wait for 18.4 °C."""
    assert after(water=18.6, flow=23.4) == "heating"


def test_the_band_still_decides_where_it_lies_above_the_setpoint() -> None:
    """A winter flow of 35 °C switches on below 30 °C and off above 40 °C, as before."""
    assert after(water=31.0, flow=35.0) == "off"
    assert after(water=29.0, flow=35.0) == "heating"
    assert after(water=39.0, flow=35.0, state="heating") == "heating"
    assert after(water=41.0, flow=35.0, state="heating") == "off"


def test_water_at_the_room_setpoint_does_not_start_a_mild_day() -> None:
    """At or above the setpoint and above flow - 5 K, the idle machine stays idle."""
    assert after(water=21.5, flow=23.4) == "off"


def test_the_switch_off_point_stays_an_offset_above_the_switch_on_point() -> None:
    """A flow request below room setpoint - 5 K no longer inverts the band.

    At a flow of 14 °C the band alone would switch on below 21 °C (the floor) and off above
    19 °C, toggling every step; the switch-off point is now 26 °C.
    """
    component = controller()
    assert component.heating_switch_temperatures_in_celsius(14.0, OFFSET, OFFSET, 0.0) == pytest.approx((21.0, 26.0))
    assert after(water=20.0, flow=14.0) == "heating"
    assert after(water=20.0, flow=14.0, state="heating") == "heating"
    assert after(water=25.0, flow=14.0, state="heating") == "heating"
    assert after(water=26.5, flow=14.0, state="heating") == "off"


def test_the_switch_off_point_is_unchanged_wherever_the_flow_is_at_least_the_room_setpoint() -> None:
    """The heating curve never asks for less than the room setpoint, so the floor leaves it alone."""
    component = controller()
    for flow in (21.0, 23.4, 35.0):
        _, switch_off = component.heating_switch_temperatures_in_celsius(flow, OFFSET, OFFSET, 1.5)
        assert switch_off == flow + OFFSET + 1.5


def test_the_energy_manager_s_raise_moves_both_points_and_the_floor_too() -> None:
    """The storage modifier shifts the whole band, the floors included."""
    component = controller()
    assert component.heating_switch_temperatures_in_celsius(23.4, OFFSET, OFFSET, 2.0) == pytest.approx(
        (ROOM_SETPOINT + 2.0, 28.4 + 2.0)
    )
    assert component.heating_switch_temperatures_in_celsius(35.0, OFFSET, OFFSET, 2.0) == pytest.approx((32.0, 42.0))
    assert component.heating_switch_temperatures_in_celsius(14.0, OFFSET, OFFSET, 2.0) == pytest.approx((23.0, 28.0))


def test_mode_2_starts_heating_below_the_room_setpoint() -> None:
    """The heating, cooling and off law uses the same floor: 18.6 °C water starts a 23.4 °C flow."""
    assert after_mode_2(water=18.6, flow=23.4) == "heating"
    assert after_mode_2(water=21.5, flow=23.4) == "off"


def test_mode_2_switch_off_point_stays_an_offset_above_the_switch_on_point() -> None:
    """At a 14 °C flow request, mode 2 heats on to 26 °C instead of stopping at 19 °C."""
    assert after_mode_2(water=20.0, flow=14.0) == "heating"
    assert after_mode_2(water=25.0, flow=14.0, state="heating") == "heating"
    assert after_mode_2(water=26.0, flow=14.0, state="heating") == "off"


def test_the_preset_sizes_the_room_setpoint_from_the_building_s_fact() -> None:
    """The preset leaves the setpoint AUTO; resolving copies the building's fact, and the component heats to it."""
    preset = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH")
    assert preset.set_heating_temperature_for_building_in_celsius is AUTO

    resolved = preset.resolve(context(room_setpoint=19.5))
    assert resolved.set_heating_temperature_for_building_in_celsius == 19.5

    component = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=resolved, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900)
    )
    assert component.heating_switch_temperatures_in_celsius(20.0, OFFSET, OFFSET, 0.0)[0] == 19.5


def test_the_preset_cannot_resolve_without_the_room_setpoint() -> None:
    """Without the building's setpoint among the facts there is nothing to floor the band with."""
    with pytest.raises(ConfigSizingError):
        MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
            SizingContext(
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
                set_heating_threshold_outside_temperature_in_celsius=20.0,
            )
        )
