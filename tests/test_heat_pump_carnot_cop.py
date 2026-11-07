"""The heat pump's Carnot-efficiency COP (hisim-heat-pump-model-rework-fpqz.2, step (a) of the heat pump rework).

The COP of heating is ``eta * T_out[K] / (max(T_out - T_source, 12 K) + d)``, fitted per source type to the
Keymark-certified population (spec ``roadmap/heat_pump_model_rework_spec.md``, §3.1). The tests show that the law
reproduces the fit at the certified rating points, that the minimum-lift guard holds the law at small lifts, that the
machine keeps hplib's thermal power and drops hplib's heating rod, that a calibrated generic air/water unit meets a
real certificate at A-7/W52 and A7/W55 where hplib's COP plane missed by -34 % and +31 %, and that hot water runs on
the same curve.
"""

from typing import Any, Optional

import pytest
from hplib import hplib as hpl

from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib, MoreAdvancedHeatPumpHPLibConfig
from hisim.components.more_advanced_heat_pump_hplib_model import (
    CarnotCopHeatPump,
    CarnotEfficiencyCop,
    HeatSourceType,
    HplibResult,
)
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


def machine(group_id: int = 1, thermal_power_in_watt: float = 8000.0) -> CarnotCopHeatPump:
    """Return the machine on hplib's generic fit of a group, sized at A-7/W52 (air) or B0/W52 (brine).

    Args:
        group_id: hplib's group, 1 air/water or 2 brine/water.
        thermal_power_in_watt: The thermal power hplib's fit is sized to, in W.

    Returns:
        The machine, at hplib's 5 K lift.
    """
    heat_pump = CarnotCopHeatPump(
        hpl.HeatPump(hpl.get_parameters("Generic", group_id, -7 if group_id == 1 else 0, 52, thermal_power_in_watt))
    )
    heat_pump.delta_t = 5
    return heat_pump


class TestTheLaw:
    """The law itself: its fit, its guard and the source type of each hplib group."""

    #: Per source type and rating point: the outdoor and outlet temperature in °C, the median certified COP of the
    #: Keymark units and the fit's mean absolute relative error at that point. Air: 1,743 units, brine: 176 units
    #: (brine at 0 °C). Reproduced by /home/noah/hisim/agent-tmp/hp-rework/fit_carnot.py on the hisim-x2mu data.
    KEYMARK_POINTS = [
        (HeatSourceType.AIR, -7, 34, 2.970, 0.082),
        (HeatSourceType.AIR, 2, 30, 4.500, 0.082),
        (HeatSourceType.AIR, 7, 27, 6.160, 0.110),
        (HeatSourceType.AIR, 12, 24, 7.920, 0.129),
        (HeatSourceType.AIR, -7, 52, 2.120, 0.116),
        (HeatSourceType.AIR, 2, 42, 3.320, 0.062),
        (HeatSourceType.AIR, 7, 36, 4.560, 0.096),
        (HeatSourceType.AIR, 12, 30, 6.230, 0.092),
        (HeatSourceType.AIR, 7, 35, 4.740, 0.098),
        (HeatSourceType.AIR, 7, 55, 2.890, 0.074),
        (HeatSourceType.BRINE, -7, 34, 4.700, 0.108),
        (HeatSourceType.BRINE, 2, 30, 5.225, 0.131),
        (HeatSourceType.BRINE, 7, 27, 5.550, 0.123),
        (HeatSourceType.BRINE, 12, 24, 5.755, 0.136),
        (HeatSourceType.BRINE, -7, 52, 3.220, 0.094),
        (HeatSourceType.BRINE, 2, 42, 3.900, 0.121),
        (HeatSourceType.BRINE, 7, 36, 4.410, 0.135),
        (HeatSourceType.BRINE, 12, 30, 4.660, 0.153),
        (HeatSourceType.BRINE, 7, 35, 4.660, 0.096),
        (HeatSourceType.BRINE, 7, 55, 3.015, 0.103),
    ]

    @pytest.mark.parametrize(("source_type", "outdoor", "outlet", "certified", "scatter"), KEYMARK_POINTS)
    def test_the_law_meets_the_keymark_median_within_the_fits_scatter(
        self, source_type: HeatSourceType, outdoor: float, outlet: float, certified: float, scatter: float
    ) -> None:
        """Catches a wrong eta, d or temperature scale: the law would leave the population's middle at some point.

        A brine unit is rated with brine at 0 °C at every outdoor temperature, so its source is 0 °C.
        """
        source = outdoor if source_type is HeatSourceType.AIR else 0.0
        cop = CarnotEfficiencyCop.cop(source_type, source, outlet)
        assert abs(cop / certified - 1.0) <= scatter

    def test_the_documented_examples(self) -> None:
        """A-7/W52 air 2.31, A7/W35 air 4.28, B0/W35 brine 4.43, as the docstrings state."""
        assert CarnotEfficiencyCop.cop(HeatSourceType.AIR, -7.0, 52.0) == pytest.approx(2.311, abs=1e-3)
        assert CarnotEfficiencyCop.cop(HeatSourceType.AIR, 7.0, 35.0) == pytest.approx(4.283, abs=1e-3)
        assert CarnotEfficiencyCop.cop(HeatSourceType.BRINE, 0.0, 35.0) == pytest.approx(4.431, abs=1e-3)

    @pytest.mark.parametrize("source_in_celsius", [13.0, 20.0, 25.0, 30.0, 40.0])
    def test_below_the_minimal_lift_the_law_holds_its_value_at_12_kelvin(self, source_in_celsius: float) -> None:
        """Catches the guard missing: at a 5 K lift the law would give 15.4, at a source above the outlet a negative COP.

        For a 25 °C outlet every source from 13 °C up gives the law at a 12 K lift, 0.451 * 298.15 / 16.45 = 8.17.
        """
        cop = CarnotEfficiencyCop.cop(HeatSourceType.AIR, source_in_celsius, 25.0)
        assert cop == pytest.approx(0.451 * 298.15 / (12.0 + 4.45))

    def test_the_guard_is_continuous_and_leaves_larger_lifts_alone(self) -> None:
        """Catches a guard that cuts in above 12 K or jumps at it."""
        at_floor = CarnotEfficiencyCop.cop(HeatSourceType.AIR, 13.0, 25.0)
        just_above = CarnotEfficiencyCop.cop(HeatSourceType.AIR, 12.99, 25.0)
        assert just_above == pytest.approx(0.451 * 298.15 / (12.01 + 4.45))
        assert 0.0 < at_floor - just_above < 0.01

    @pytest.mark.parametrize(
        ("group", "source_type"),
        [
            (1, HeatSourceType.AIR),
            (4, HeatSourceType.AIR),
            (2, HeatSourceType.BRINE),
            (5, HeatSourceType.BRINE),
            (3, HeatSourceType.BRINE),
            (6, HeatSourceType.BRINE),
        ],
    )
    def test_each_hplib_group_takes_its_source_types_parameters(self, group: int, source_type: HeatSourceType) -> None:
        """Air groups take the air fit; brine and water groups the brine fit (no water/water data exist)."""
        assert CarnotEfficiencyCop.source_type_of(group) is source_type

    def test_a_group_hplib_does_not_define_is_refused(self) -> None:
        """Catches an unknown group silently getting some parameters."""
        with pytest.raises(ValueError, match="hplib group 7"):
            CarnotEfficiencyCop.source_type_of(7)


class TestTheMachine:
    """hplib's thermal power, outlet and flow with the law's COP, and no heating rod."""

    def test_hplibs_thermal_power_is_kept_and_the_electricity_follows_the_law(self) -> None:
        """At A2 and a 25 °C return (outlet 30 °C): hplib's 7.87 kW, the law's COP 4.21 instead of hplib's 4.73."""
        heat_pump = machine()
        answer = heat_pump.simulate(t_in_primary=2.0, t_in_secondary=25.0, t_amb=2.0, mode=1)
        hplib_answer = heat_pump.hplib_heat_pump.simulate(t_in_primary=2.0, t_in_secondary=25.0, t_amb=2.0, mode=1)
        assert answer["P_th"] == pytest.approx(hplib_answer["P_th"])
        assert answer["T_out"] == pytest.approx(30.0)
        assert answer["COP"] == pytest.approx(CarnotEfficiencyCop.cop(HeatSourceType.AIR, 2.0, 30.0))
        assert answer["COP"] == pytest.approx(4.21, abs=0.01)
        assert answer["P_el"] == pytest.approx(answer["P_th"] / answer["COP"])
        assert answer["m_dot"] == pytest.approx(answer["P_th"] / (5.0 * 4200.0))

    @pytest.mark.parametrize(("outdoor", "outlet"), [(-7.0, 65.0), (0.0, 70.0), (-15.0, 75.0)])
    def test_where_hplib_runs_its_rod_alone_the_compressor_runs_on(self, outdoor: float, outlet: float) -> None:
        """Catches hplib's rod answer surviving: 8.1 kW at a COP of 1, where real units reach about 2.

        The thermal power is the compressor's at hplib's switch, its fitted electricity (hplib's plane at a COP of 1).
        """
        heat_pump = machine()
        hplib_answer = heat_pump.hplib_heat_pump.simulate(
            t_in_primary=outdoor, t_in_secondary=outlet - 5.0, t_amb=outdoor, mode=1
        )
        assert hplib_answer["COP"] == pytest.approx(1.0)
        answer = heat_pump.simulate(t_in_primary=outdoor, t_in_secondary=outlet - 5.0, t_amb=outdoor, mode=1)
        assert answer["COP"] == pytest.approx(CarnotEfficiencyCop.cop(HeatSourceType.AIR, outdoor, outlet))
        assert answer["COP"] > 1.6
        assert answer["P_th"] == pytest.approx(heat_pump.fitted_electrical_power_in_watt(outdoor, outlet, outdoor))
        assert answer["P_el"] < answer["P_th"]

    def test_the_compressor_power_is_continuous_at_hplibs_switch(self) -> None:
        """Catches the 3.3 kW jump of hplib's switch (0 °C, returns 64.0 to 64.1 °C) coming back."""
        heat_pump = machine()
        below = heat_pump.simulate(t_in_primary=0.0, t_in_secondary=64.0, t_amb=0.0, mode=1)
        above = heat_pump.simulate(t_in_primary=0.0, t_in_secondary=64.1, t_amb=0.0, mode=1)
        assert below["P_th"] == pytest.approx(4799.0, abs=5.0)
        assert abs(above["P_th"] - below["P_th"]) < 50.0

    def test_the_compressor_is_raised_to_the_minimal_power_as_hplib_raises_it(self) -> None:
        """A 2 kW machine at A7/W27 delivers 1.45 kW by its plane and is raised to the 1.8 kW minimum, as in hplib."""
        heat_pump = machine(thermal_power_in_watt=2000.0)
        answer = heat_pump.simulate(t_in_primary=7.0, t_in_secondary=22.0, t_amb=7.0, mode=1, p_th_min=1800.0)
        hplib_answer = heat_pump.hplib_heat_pump.simulate(
            t_in_primary=7.0, t_in_secondary=22.0, t_amb=7.0, mode=1, p_th_min=1800.0
        )
        assert answer["P_th"] == pytest.approx(1800.0)
        assert hplib_answer["P_th"] == pytest.approx(1800.0)

    def test_where_hplib_adds_its_rod_to_the_compressor_only_the_compressor_remains(self) -> None:
        """Catches the rod's share surviving in hplib's compressor-plus-rod branch.

        The 2 kW machine at 0 °C and a 64 °C return cannot reach 1.8 kW with its compressor (at most p_el_ref times
        its COP, 1.44 kW); hplib adds its 2.03 kW rod, the machine delivers the compressor's 1.44 kW.
        """
        heat_pump = machine(thermal_power_in_watt=2000.0)
        answer = heat_pump.simulate(t_in_primary=0.0, t_in_secondary=64.0, t_amb=0.0, mode=1, p_th_min=1800.0)
        hplib_answer = heat_pump.hplib_heat_pump.simulate(
            t_in_primary=0.0, t_in_secondary=64.0, t_amb=0.0, mode=1, p_th_min=1800.0
        )
        assert answer["P_th"] == pytest.approx(hplib_answer["P_th"] - heat_pump.p_th_ref)
        assert answer["P_th"] < 1800.0

    def test_cooling_is_hplibs_own_answer(self) -> None:
        """Catches the heating law leaking into cooling, whose EER stays hplib's."""
        heat_pump = machine()
        answer = heat_pump.simulate(t_in_primary=30.0, t_in_secondary=22.0, t_amb=30.0, mode=2)
        hplib_answer = heat_pump.hplib_heat_pump.simulate(t_in_primary=30.0, t_in_secondary=22.0, t_amb=30.0, mode=2)
        assert answer == hplib_answer


def heat_pump_component(
    scop_w35: Optional[float] = None, scop_w55: Optional[float] = None, with_hot_water: bool = False
) -> MoreAdvancedHeatPumpHPLib:
    """Return an 8 kW air/water heat pump on a one-day simulation, with an optional SCOP pair and hot water.

    Args:
        scop_w35: The stated W35 SCOP, or None.
        scop_w55: The stated W55 SCOP, or None.
        with_hot_water: Whether the heat pump also heats hot water.

    Returns:
        The heat pump.
    """
    config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    config.set_thermal_output_power_in_watt = 8000.0
    config.heating_reference_temperature_in_celsius = -7.0
    config.standardized_scop_en14825_w35 = scop_w35
    config.standardized_scop_en14825_w55 = scop_w55
    config.with_domestic_hot_water_preparation = with_hot_water
    return MoreAdvancedHeatPumpHPLib(SimulationParameters.one_day_only(2021, 900), config)


def heating_answer(
    heat_pump: MoreAdvancedHeatPumpHPLib, outdoor: float, outlet: float, operation_mode: str = "heating_building"
) -> HplibResult:
    """Return the heat pump's cached heating answer at an outdoor temperature and an outlet (return plus 5 K).

    Args:
        heat_pump: The heat pump.
        outdoor: The outdoor (and source) temperature, in °C.
        outlet: The outlet temperature, in °C.
        operation_mode: The circuit, ``heating_building`` or ``heating_dhw``.

    Returns:
        The answer.
    """
    heat_pump.heatpump.delta_t = 5.0
    return heat_pump.get_cached_results_or_run_hplib_simulation(
        source_temperature_in_celsius=outdoor,
        return_temperature_in_celsius=outlet - 5.0,
        ambient_temperature_in_celsius=outdoor,
        mode=1,
        operation_mode=operation_mode,
        minimal_thermal_power_in_watt=0.0,
    )


class TestACertifiedUnit:
    """A real air/water unit's certificate against the calibrated generic machine.

    Viessmann Vitocal 250-A AWO-E-AC 251.A10, Keymark certificate data as exported in hplib's raw Keymark files
    (``_Viessmann_Climate_Solutions_SEVitocal_x5x-A_z4.csv``): SCOP W35 4.99, W55 3.91; EN 14825 COP at A-7/W52 2.5,
    EN 14511 COP at A7/W55 2.97. hplib's generic COP plane calibrated to the same SCOPs gave 1.66 (-34 %) and 3.89
    (+31 %); the Carnot-efficiency law gives 2.76 (+10 %) and 3.37 (+13 %).
    """

    #: The unit's stated SCOPs, W35 and W55.
    STATED_SCOPS = (4.99, 3.91)

    @pytest.mark.parametrize(("outdoor", "outlet", "certified"), [(-7.0, 52.0, 2.5), (7.0, 55.0, 2.97)])
    def test_the_calibrated_machine_meets_the_certificate_within_15_percent(
        self, outdoor: float, outlet: float, certified: float
    ) -> None:
        """Catches the outdoor-dependent error of hplib's plane coming back: -34 % at A-7/W52, +31 % at A7/W55."""
        heat_pump = heat_pump_component(*self.STATED_SCOPS)
        answer = heating_answer(heat_pump, outdoor, outlet)
        assert abs(answer.cop / certified - 1.0) <= 0.15


class TestHotWater:
    """Hot water runs on the same curve as space heating (owner decision (6) of 2026-10-21)."""

    @pytest.mark.parametrize("scops", [(None, None), (4.99, 3.91)])
    def test_hot_water_and_space_heating_share_the_cop_at_one_outlet(self, scops: Any) -> None:
        """Catches a separate hot-water model: at A7/W55 both circuits get the same (calibrated) COP."""
        heat_pump = heat_pump_component(*scops, with_hot_water=True)
        hot_water = heating_answer(heat_pump, 7.0, 55.0, operation_mode="heating_dhw")
        space_heating = heating_answer(heat_pump, 7.0, 55.0)
        assert hot_water.cop == pytest.approx(space_heating.cop)
        factor = heat_pump.scop_calibration.factor(55.0)
        assert hot_water.cop == pytest.approx(CarnotEfficiencyCop.cop(HeatSourceType.AIR, 7.0, 55.0) * factor)
