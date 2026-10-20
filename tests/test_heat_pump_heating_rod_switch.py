"""The heat pump evaluates hplib at the return itself where its grid interpolation would straddle the heating-rod switch.

hplib runs its heating rod alone once its fitted coefficient of performance falls to 1, and its thermal power jumps
there. The heat pump caches hplib on a 0.1 K grid of the return temperature and interpolates between grid points; across
the switch that interpolation turned the jump into a ramp of about 25 kW/K, which with the hot-water tank's step mean
formed a loop contracting by only about 0.9 per pass.
"""

from typing import Tuple

import pytest

from hisim.components import more_advanced_heat_pump_hplib
from hisim.components.more_advanced_heat_pump_hplib_model import HplibResult
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


def hplib_result(cop: float, thermal_power_in_watt: float) -> HplibResult:
    """Return an hplib result with this coefficient of performance and thermal power, and plain other fields.

    Args:
        cop: The coefficient of performance, dimensionless.
        thermal_power_in_watt: The thermal power, in W.

    Returns:
        The result.
    """
    return HplibResult(
        outlet_temperature_in_celsius=55.0,
        mass_flow_in_kg_per_second=0.3,
        thermal_power_in_watt=thermal_power_in_watt,
        electrical_power_in_watt=thermal_power_in_watt / cop,
        cop=cop,
        eer=0.0,
    )


class TestTheSwitchBetweenTwoGridPoints:
    """The pure check whether two neighbouring operating points lie on either side of the heating-rod switch."""

    #: The lower and the upper point's coefficient of performance, and whether they straddle the switch.
    TRANSITIONS = [
        (1.0033, 1.0, True),
        (1.0, 1.0033, True),
        (1.0, 1.0, False),
        (2.5, 2.4, False),
        (1.0000001, 1.0, True),
        (0.9, 1.0, False),
    ]

    @pytest.mark.parametrize(("lower_cop", "upper_cop", "expected"), TRANSITIONS)
    def test_every_row_of_the_table(self, lower_cop: float, upper_cop: float, expected: bool) -> None:
        """Catches a switch the check misses, which would interpolate across hplib's jump, or one it invents."""
        assert (
            HplibResult.straddle_the_heating_rod_switch(
                lower=hplib_result(lower_cop, 5000.0), upper=hplib_result(upper_cop, 8000.0)
            )
            is expected
        )


def air_water_heat_pump() -> more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib:
    """Build an 8 kW air-water heat pump with hot-water preparation, at 60 s steps.

    At 0 °C outside its hplib model switches to the heating rod alone between a return of 64.0 and 64.1 °C: 4.8 kW
    at a COP of 1.0033 below, the rod's 8.1 kW above.

    Returns:
        The heat pump.
    """
    config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_reference_temperature_in_celsius = -7.0
    config.with_domestic_hot_water_preparation = True
    return more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60), config=config
    )


def answers_at(
    heat_pump: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib, return_temperature_in_celsius: float
) -> Tuple[HplibResult, HplibResult]:
    """Return the heat pump's answer at a hot-water return at 0 °C outside, and hplib's own answer there.

    Args:
        heat_pump: The heat pump.
        return_temperature_in_celsius: The return temperature, in °C.

    Returns:
        The heat pump's answer and hplib's answer computed at the return itself.
    """
    answer = heat_pump.get_cached_results_or_run_hplib_simulation(
        source_temperature_in_celsius=0.0,
        return_temperature_in_celsius=return_temperature_in_celsius,
        ambient_temperature_in_celsius=0.0,
        mode=1,
        operation_mode="heating_dhw",
        minimal_thermal_power_in_watt=heat_pump.HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT,
    )
    exact = heat_pump.hplib_result_computed(
        source_on_grid_in_celsius=0.0,
        return_temperature_in_celsius=return_temperature_in_celsius,
        ambient_on_grid_in_celsius=0.0,
        mode=1,
        minimal_thermal_power_in_watt=heat_pump.HOT_WATER_MINIMAL_THERMAL_POWER_IN_WATT,
    )
    return answer, exact


@pytest.mark.parametrize("return_temperature_in_celsius", [64.01, 64.05, 64.09])
def test_between_grid_points_across_the_switch_the_answer_is_hplibs_own(return_temperature_in_celsius: float) -> None:
    """Catches an interpolation across the switch: its thermal power would lie between the compressor's and the rod's."""
    answer, exact = answers_at(air_water_heat_pump(), return_temperature_in_celsius)
    assert answer == exact
    assert answer.thermal_power_in_watt < 5000.0 or answer.thermal_power_in_watt == pytest.approx(8135.03, abs=0.01)


def test_away_from_the_switch_the_answer_is_still_interpolated() -> None:
    """Catches the exact evaluation leaking to every return, which would change the heat pump's answer everywhere."""
    heat_pump = air_water_heat_pump()
    answer, _ = answers_at(heat_pump, 50.04)
    lower, _ = answers_at(heat_pump, 50.0)
    upper, _ = answers_at(heat_pump, 50.1)
    assert answer == HplibResult.interpolated(lower=lower, upper=upper, share_of_upper=answer_share(50.04))


def answer_share(return_temperature_in_celsius: float) -> float:
    """Return the upper grid point's weight of a return temperature on hplib's 0.1 K grid, dimensionless.

    Args:
        return_temperature_in_celsius: The return temperature, in °C.

    Returns:
        The weight, from 0 to 1.
    """
    cells = return_temperature_in_celsius * 10.0
    return cells - int(cells)
