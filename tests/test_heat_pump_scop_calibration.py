"""The heat pump's calibration to a stated EN 14825 SCOP (hisim-4g9.15).

A datasheet rates a heat pump's seasonal COP by the bin method of EN 14825. The tests pin that
method on hplib's generic fit (the numbers of the 2026-09-21 check), then show that the fit
calibrated to one or two stated ratings is rated exactly at them, that a calibrated call keeps its
thermal output and only changes the electricity the compressor draws, and that the component
refuses a configuration no datasheet could state.
"""

import re
from typing import Any, Dict

import pytest
from hplib import hplib as hpl

from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    ScopApplication,
    ScopCalibration,
    StandardizedSeasonalCop,
)
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


def generic(group_id: int = 1) -> Any:
    """Return hplib's generic fit for air/water (group 1) or brine/water (group 2), 10 kW at A-7/W52."""
    heatpump = hpl.HeatPump(hpl.get_parameters("Generic", group_id, -7 if group_id == 1 else 0, 52, 10000))
    heatpump.delta_t = 5
    return heatpump


def heating_call(heatpump: Any, outlet: float = 35.0, outdoor: float = 2.0, p_th_min: float = 0.0) -> Dict[str, Any]:
    """One heating call of hplib at an outlet temperature, as the component makes it."""
    result: Dict[str, Any] = heatpump.simulate(
        t_in_primary=outdoor, t_in_secondary=outlet - 5.0, t_amb=outdoor, mode=1, p_th_min=p_th_min
    )
    return result


class TestTheBinMethod:
    """The EN 14825 rating of hplib's generic fits, as the 2026-09-21 check computed it."""

    def test_the_generic_air_water_fit_rates_3_40_at_w55_and_4_74_at_w35(self) -> None:
        """The two numbers the calibration factors are made of for an air/water machine."""
        heatpump = generic(1)
        assert StandardizedSeasonalCop.of(heatpump, ScopApplication.W55) == pytest.approx(3.40, abs=0.05)
        assert StandardizedSeasonalCop.of(heatpump, ScopApplication.W35) == pytest.approx(4.74, abs=0.05)

    def test_a_brine_machine_rates_better_at_the_lower_outlet(self) -> None:
        """Brine enters at 0 °C in every bin; the lower outlet still wins."""
        heatpump = generic(2)
        w35 = StandardizedSeasonalCop.of(heatpump, ScopApplication.W35)
        w55 = StandardizedSeasonalCop.of(heatpump, ScopApplication.W55)
        assert w35 > w55 > 1

    def test_the_anchors_are_the_heat_weighted_mean_outlets(self) -> None:
        """28.9 °C for the W35 line and 40.7 °C for the W55 line (decision of 2026-09-23)."""
        assert StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35) == pytest.approx(28.9, abs=0.05)
        assert StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55) == pytest.approx(40.7, abs=0.05)
        assert ScopCalibration.ANCHORS == (
            StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35),
            StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55),
        )

    def test_a_machine_of_another_group_is_refused_by_name(self) -> None:
        """The bins' source temperature is defined for air/water and brine/water only."""
        heatpump = hpl.HeatPump(hpl.get_parameters("Generic", 3, 10, 52, 10000))
        with pytest.raises(ValueError, match="this one is hplib group 3"):
            StandardizedSeasonalCop.of(heatpump, ScopApplication.W35)


class TestTheCalibration:
    """The calibrated fit is rated at the stated SCOPs, and only the compressor's electricity moves."""

    @pytest.mark.parametrize(
        "group_id, w35, w55",
        [(1, None, 3.0), (1, 4.6, None), (1, 4.6, 3.4), (1, 5.5, 3.0), (2, 5.0, 3.8), (2, None, 3.4)],
    )
    def test_the_calibrated_fit_is_rated_at_the_stated_scops(self, group_id: int, w35: Any, w55: Any) -> None:
        """One rating, two ratings, a wide pair, a brine machine: each rates back exactly."""
        heatpump = generic(group_id)
        calibration = ScopCalibration.of(heatpump, w35, w55)
        for application, stated in ((ScopApplication.W35, w35), (ScopApplication.W55, w55)):
            if stated is not None:
                assert StandardizedSeasonalCop.of(heatpump, application, calibration) == pytest.approx(
                    stated, abs=ScopCalibration.TOLERANCE
                )

    @pytest.mark.parametrize(
        "group_id, w35, w55",
        [(1, 8.0, 1.5), (1, 3.0, 1.2), (2, 10.0, 1.5), (1, None, 1.1), (1, 10.0, 10.0), (2, None, 1.1)],
    )
    def test_the_pairs_the_fixed_point_iteration_missed_are_rated_exactly(
        self, group_id: int, w35: Any, w55: Any
    ) -> None:
        """8.0 / 1.5 was slow, 3.0 / 1.2 and the brine 10 / 1.5 stalled; they and the extremes are met exactly."""
        heatpump = generic(group_id)
        calibration = ScopCalibration.of(heatpump, w35, w55)
        for application, stated in ((ScopApplication.W35, w35), (ScopApplication.W55, w55)):
            if stated is not None:
                assert StandardizedSeasonalCop.of(heatpump, application, calibration) == pytest.approx(
                    stated, abs=ScopCalibration.TOLERANCE
                )

    def test_a_pair_beyond_the_fits_reach_is_refused_with_the_limit(self) -> None:
        """With W35 at 6.0 the air/water fit cannot rate W55 below 1.31, whatever the factors."""
        with pytest.raises(ValueError, match=re.escape("with W35 at 6.0, hplib's fit cannot rate below W55 1.31")):
            ScopCalibration.of(generic(1), 6.0, 1.1)

    def test_the_factors_stay_moderate_for_a_wide_pair(self) -> None:
        """The reason for the mean-outlet anchors: 5.5 / 3.0 no longer drives a factor to 0.46."""
        factors = ScopCalibration.of(generic(1), 5.5, 3.0).factors
        assert all(0.6 < factor < 1.3 for factor in factors.values())

    def test_a_calibrated_call_keeps_its_heat_and_scales_the_electricity(self) -> None:
        """P_th is unchanged; P_el is divided and the COP multiplied by the factor."""
        heatpump = generic(1)
        calibration = ScopCalibration.of(heatpump, None, 3.0)
        factor = calibration.factors[ScopApplication.W55]
        raw = heating_call(heatpump)
        calibrated = calibration.apply(heatpump, raw, mode=1)
        assert float(calibrated["P_th"]) == pytest.approx(float(raw["P_th"]))
        assert float(calibrated["P_el"]) == pytest.approx(float(raw["P_el"]) / factor)
        assert float(calibrated["COP"]) == pytest.approx(float(raw["COP"]) * factor)

    def test_the_factor_is_linear_between_the_anchors_and_constant_outside(self) -> None:
        """Two ratings: the W35 factor below 28.9 °C, the W55 factor above 40.7 °C, a line between."""
        calibration = ScopCalibration({ScopApplication.W35: 0.9, ScopApplication.W55: 1.2})
        low = StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35)
        high = StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55)
        assert calibration.factor(20.0) == pytest.approx(0.9)
        assert calibration.factor(60.0) == pytest.approx(1.2)
        assert calibration.factor((low + high) / 2) == pytest.approx(1.05)

    def test_a_rod_only_call_and_a_cooling_call_are_left_alone(self) -> None:
        """The heating rod is not the unit the SCOP rates, and cooling is out of scope."""
        heatpump = generic(1)
        calibration = ScopCalibration({ScopApplication.W55: 0.8})
        rod_only = {"COP": 1.0, "P_th": 5000.0, "P_el": 5000.0, "T_out": 55.0}
        assert calibration.apply(heatpump, rod_only, mode=1) == rod_only
        cooling = {"COP": 0.0, "EER": 3.0, "P_th": -4000.0, "P_el": 1300.0, "T_out": 18.0}
        assert calibration.apply(heatpump, cooling, mode=2) == cooling

    def test_in_the_compressor_plus_rod_branch_only_the_compressor_is_calibrated(self) -> None:
        """The mixed branch draws p_el_ref + p_th_ref in hplib; the rod's share keeps its COP of 1."""
        heatpump = generic(1)
        calibration = ScopCalibration({ScopApplication.W55: 0.8})
        compressor, rod = float(heatpump.p_el_ref), float(heatpump.p_th_ref)
        mixed = {"COP": 1.5, "P_th": 1.5 * (compressor + rod), "P_el": compressor + rod, "T_out": 55.0}
        calibrated = calibration.apply(heatpump, mixed, mode=1)
        assert float(calibrated["P_el"]) == pytest.approx(compressor / 0.8 + rod)
        assert float(calibrated["P_th"]) == pytest.approx(mixed["P_th"])


class TestTheComponent:
    """The configuration's two fields reach the component, and an impossible one is refused."""

    @staticmethod
    def config(**fields: Any) -> MoreAdvancedHeatPumpHPLibConfig:
        """An 8 kW air/water machine with the given SCOP fields."""
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        config.set_thermal_output_power_in_watt = 8000.0
        config.heating_reference_temperature_in_celsius = -7.0
        for name, value in fields.items():
            setattr(config, name, value)
        return config

    @staticmethod
    def build(config: MoreAdvancedHeatPumpHPLibConfig) -> MoreAdvancedHeatPumpHPLib:
        """The component on a one-day simulation."""
        return MoreAdvancedHeatPumpHPLib(SimulationParameters.one_day_only(2021, 900), config)

    def test_without_a_scop_nothing_is_calibrated(self) -> None:
        """The fit as hplib ships it: no factor, and the cached call is hplib's own."""
        component = self.build(self.config())
        assert not component.scop_calibration.factors
        cached = component.get_cached_results_or_run_hplib_simulation(2.0, 30.0, 2.0, 1, "heating_building", 1800.0)
        raw = component.heatpump.simulate(t_in_primary=2.0, t_in_secondary=30.0, t_amb=2.0, mode=1, p_th_min=1800.0)
        assert float(cached["P_el"]) == pytest.approx(float(raw["P_el"]))

    def test_a_stated_pair_calibrates_every_heating_call(self) -> None:
        """The cached call carries the calibrated electricity, and the report names the factors."""
        component = self.build(
            self.config(standardized_scop_en14825_w35=4.6, standardized_scop_en14825_w55=3.4)
        )
        assert set(component.scop_calibration.factors) == {ScopApplication.W35, ScopApplication.W55}
        cached = component.get_cached_results_or_run_hplib_simulation(2.0, 30.0, 2.0, 1, "heating_building", 1800.0)
        raw = component.heatpump.simulate(t_in_primary=2.0, t_in_secondary=30.0, t_amb=2.0, mode=1, p_th_min=1800.0)
        factor = component.scop_calibration.factor(float(raw["T_out"]))
        assert float(cached["P_el"]) == pytest.approx(float(raw["P_el"]) / factor)
        assert any("SCOP calibration factor W55" in line for line in component.write_to_report())

    @pytest.mark.parametrize(
        "fields, message",
        [
            ({"standardized_scop_en14825_w55": 1.0}, "above 1"),
            ({"standardized_scop_en14825_w35": 11.0}, "at most"),
            ({"standardized_scop_en14825_w35": 3.4, "standardized_scop_en14825_w55": 4.6}, "cannot outperform"),
            (
                {"standardized_scop_en14825_w35": 6.0, "standardized_scop_en14825_w55": 1.1},
                "HeatPump.*: "
                + re.escape(
                    "the stated SCOPs W35 6.0 and W55 1.1 cannot be met: with W35 at 6.0, hplib's "
                    "Generic air/water fit cannot rate below W55 1.31; a lower W55 rating would need a factor "
                    "below 0.001 at the W55 anchor (40.7 °C)."
                ),
            ),
        ],
    )
    def test_an_impossible_scop_is_refused_by_name(self, fields: Dict[str, float], message: str) -> None:
        """A SCOP of 1 or less, above 10, a W55 rating above W35, or a pair the fit cannot reach fails the build."""
        with pytest.raises(ValueError, match=message):
            self.build(self.config(**fields))
