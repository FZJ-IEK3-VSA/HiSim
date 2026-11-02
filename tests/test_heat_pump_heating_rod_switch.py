"""The heat pump evaluates hplib at the return itself where its grid interpolation would straddle the heating-rod switch.

hplib runs its heating rod alone once its fitted coefficient of performance falls to 1, and its thermal power jumps
there. The heat pump caches hplib on a 0.1 K grid of the return temperature and interpolates between grid points; across
the switch that interpolation turned the jump into a ramp of about 25 kW/K, which with the hot-water tank's step mean
formed a loop contracting by only about 0.9 per pass.
"""

from typing import Optional, Tuple

import pytest

from hisim.components import more_advanced_heat_pump_hplib
from hisim.components.more_advanced_heat_pump_hplib_model import HplibResult
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


def hplib_result(cop: float, thermal_power_in_watt: float, heating_rod_alone: bool) -> HplibResult:
    """Return an hplib result with this coefficient of performance, thermal power and rod verdict, and plain others.

    Args:
        cop: The coefficient of performance, dimensionless, calibrated or not.
        thermal_power_in_watt: The thermal power, in W.
        heating_rod_alone: Whether hplib itself runs its heating rod alone there.

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
        heating_rod_alone=heating_rod_alone,
    )


class TestTheSwitchBetweenTwoGridPoints:
    """The pure check whether two neighbouring operating points lie on either side of the heating-rod switch."""

    #: The lower and the upper point's coefficient of performance and hplib's rod verdict, and whether they straddle
    #: the switch. The last two rows are calibrated compressor results with a COP below 1 (hisim-g0dp).
    TRANSITIONS = [
        (1.0033, False, 1.0, True, True),
        (1.0, True, 1.0033, False, True),
        (1.0, True, 1.0, True, False),
        (2.5, False, 2.4, False, False),
        (1.0000001, False, 1.0, True, True),
        (0.879, False, 1.0, True, True),
        (0.879, False, 0.9, False, False),
    ]

    @pytest.mark.parametrize(("lower_cop", "lower_rod", "upper_cop", "upper_rod", "expected"), TRANSITIONS)
    def test_every_row_of_the_table(
        self, lower_cop: float, lower_rod: bool, upper_cop: float, upper_rod: bool, expected: bool
    ) -> None:
        """Catches a switch the check misses, which would interpolate across hplib's jump, or one it invents."""
        assert (
            HplibResult.straddle_the_heating_rod_switch(
                lower=hplib_result(lower_cop, 5000.0, lower_rod), upper=hplib_result(upper_cop, 8000.0, upper_rod)
            )
            is expected
        )


class TestHplibsOwnVerdict:
    """hplib's verdict on its heating rod, read from its own result before any calibration."""

    #: hplib's own COP, its mode (1 heating, 2 cooling), and whether it runs its heating rod alone.
    VERDICTS = [
        (1.0, 1, True),
        (1.0033, 1, False),
        (3.2, 1, False),
        (0.0, 2, False),
    ]

    @pytest.mark.parametrize(("cop", "mode", "expected"), VERDICTS)
    def test_every_row_of_the_table(self, cop: float, mode: int, expected: bool) -> None:
        """Catches a verdict on the wrong side of hplib's threshold, or a heating rod invented in cooling."""
        assert HplibResult.runs_the_heating_rod_alone({"COP": cop}, mode) is expected


def air_water_heat_pump(
    scop_w35: Optional[float] = None, scop_w55: Optional[float] = None
) -> more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib:
    """Build an 8 kW air-water heat pump with hot-water preparation, at 60 s steps, with an optional stated SCOP.

    At 0 °C outside its hplib model switches to the heating rod alone between a return of 64.0 and 64.1 °C: 4.8 kW
    at a COP of 1.0033 below, the rod's 8.1 kW above. A stated SCOP calibrates the compressor's results
    (:class:`ScopCalibration`), not the rod's. The generic fit's own W55 SCOP is about 3.4, so a stated W55 of 3.0
    scales the compressor's COP by about 0.88: next to the switch to about 0.88, below 1, although hplib itself still
    runs the compressor there.

    Args:
        scop_w35: The stated SCOP at W35 (EN 14825), dimensionless; None leaves hplib's generic fit.
        scop_w55: The stated SCOP at W55 (EN 14825), dimensionless; None leaves hplib's generic fit.

    Returns:
        The heat pump.
    """
    config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_reference_temperature_in_celsius = -7.0
    config.with_domestic_hot_water_preparation = True
    config.standardized_scop_en14825_w35 = scop_w35
    config.standardized_scop_en14825_w55 = scop_w55
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


class TestAcrossTheSwitchBetweenGridPoints:
    """Between two grid points on either side of the switch every machine answers hplib's own result at the return."""

    #: The stated SCOPs at W35 and W55 the machine is built with, dimensionless: none, both with a W55 factor below 1,
    #: W55 alone below the generic fit's, and both with a W55 factor above 1 (the table of hisim-g0dp).
    STATED_SCOPS = [(None, None), (4.0, 3.0), (None, 3.0), (4.5, 3.5)]

    @pytest.mark.parametrize(("scop_w35", "scop_w55"), STATED_SCOPS)
    @pytest.mark.parametrize("return_temperature_in_celsius", [64.01, 64.05, 64.09])
    def test_the_answer_is_hplibs_own(
        self, return_temperature_in_celsius: float, scop_w35: Optional[float], scop_w55: Optional[float]
    ) -> None:
        """Catches an interpolation across the switch: its power would lie between the compressor's and the rod's.

        With a stated W55 SCOP below the generic fit's the calibrated compressor's COP next to the switch is below 1;
        the switch must still be found, on hplib's own verdict (hisim-g0dp).
        """
        answer, exact = answers_at(air_water_heat_pump(scop_w35, scop_w55), return_temperature_in_celsius)
        assert answer == exact
        assert answer.thermal_power_in_watt < 5000.0 or answer.thermal_power_in_watt == pytest.approx(8135.03, abs=0.01)

    @pytest.mark.parametrize(("scop_w35", "scop_w55"), STATED_SCOPS)
    def test_a_stated_scop_keeps_the_rods_power(
        self, scop_w35: Optional[float], scop_w55: Optional[float]
    ) -> None:
        """Catches the ramp of hisim-g0dp: with a W55 SCOP of 3.0 a return of 64.05 °C gave 6467.1 W, not 8135.0 W.

        At 64.05 °C hplib runs the heating rod alone, whose power the calibration does not scale, so every machine
        answers hplib's 8135.0 W.
        """
        answer, _ = answers_at(air_water_heat_pump(scop_w35, scop_w55), 64.05)
        assert answer.thermal_power_in_watt == pytest.approx(8135.0, abs=0.1)
        assert answer.heating_rod_alone


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
