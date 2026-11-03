"""The rules of spec section 3.2 give the values of Appendix B, and F_w behaves as its definition says."""

from typing import Dict

import numpy as np
import pandas as pd
import pytest

from hisim.verification import derivation
from hisim.verification.schema import Case, Construction, Glazing, Layer, Window

pytestmark = pytest.mark.base


class AppendixB:
    """The worked derivation of the spec's Appendix B, whose inputs the human confirmed on 2026-11-03."""

    KAPPA_IN_JOULE_PER_M2_PER_KELVIN = {
        ("600", "wall"): 9576.0,
        ("600", "roof"): 7980.0,
        ("600", "floor"): 19500.0,
        ("900", "wall"): 119350.0,
        ("900", "floor"): 112000.0,
    }
    DECISIVE_CONDITION = {("600", "wall"): "b", ("600", "roof"): "b", ("600", "floor"): "b", ("900", "wall"): "a", ("900", "floor"): "b"}
    THERMAL_CAPACITY_IN_MEGAJOULE_PER_KELVIN = {"600": 1.928, "900": 13.350}
    EFFECTIVE_MASS_AREA_IN_M2 = {"600": 137.0, "900": 117.9}
    TOTAL_INTERNAL_SURFACE_AREA_IN_M2 = 171.6
    #: Table 7-4: 0.5 ach becomes 0.414 ach for a program with standard air density.
    ADJUSTED_INFILTRATION_PER_HOUR = 0.414


@pytest.mark.parametrize("case_id", ["600", "900"])
def test_kappa_and_the_decisive_condition_match_appendix_b(cases: Dict[str, Case], case_id: str) -> None:
    """Each element's kappa and the condition that fixed its effective thickness."""
    mass = derivation.mass_parameters(cases[case_id])
    by_kind = {entry.kind: entry for entry in mass.surfaces}

    for (expected_case, kind), kappa in AppendixB.KAPPA_IN_JOULE_PER_M2_PER_KELVIN.items():
        if expected_case == case_id:
            assert by_kind[kind].kappa_in_joule_per_m2_per_kelvin == pytest.approx(kappa)
            assert by_kind[kind].thickness.decisive_condition == AppendixB.DECISIVE_CONDITION[(expected_case, kind)]


@pytest.mark.parametrize("case_id", ["600", "900"])
def test_the_mass_and_surface_quantities_match_appendix_b(cases: Dict[str, Case], case_id: str) -> None:
    """C_m, A_m and A_tot to the precision Appendix B prints them with."""
    mass = derivation.mass_parameters(cases[case_id])

    assert mass.thermal_capacity_in_joule_per_kelvin / 1e6 == pytest.approx(AppendixB.THERMAL_CAPACITY_IN_MEGAJOULE_PER_KELVIN[case_id], abs=0.0005)
    assert mass.effective_mass_area_in_m2 == pytest.approx(AppendixB.EFFECTIVE_MASS_AREA_IN_M2[case_id], abs=0.05)
    assert mass.total_internal_surface_area_in_m2 == pytest.approx(AppendixB.TOTAL_INTERNAL_SURFACE_AREA_IN_M2)


def test_the_effective_thickness_rule_takes_the_smallest_condition() -> None:
    """Half the thickness, the depth of the insulation, or the cap, whichever is smallest."""
    thick_uninsulated = Construction("slab", 1.0, (Layer("concrete", 0.30, 1.0, 2000.0, 1000.0, False),), "test")
    thin_uninsulated = Construction("board", 1.0, (Layer("board", 0.05, 1.0, 1000.0, 1000.0, False),), "test")
    insulated_inside = Construction("insulated", 1.0, (Layer("insulation", 0.10, 0.04, 20.0, 1000.0, True), Layer("concrete", 0.20, 1.0, 2000.0, 1000.0, False)), "test")

    assert derivation.effective_thickness(thick_uninsulated).decisive_condition == "c"
    assert derivation.effective_thickness(thick_uninsulated).effective_in_m == derivation.EFFECTIVE_THICKNESS_CAP_IN_M
    assert derivation.effective_thickness(thin_uninsulated).decisive_condition == "a"
    assert derivation.effective_thickness(thin_uninsulated).effective_in_m == 0.025
    assert derivation.effective_thickness(insulated_inside).decisive_condition == "b"
    assert derivation.effective_thickness(insulated_inside).effective_in_m == 0.0
    assert derivation.areal_heat_capacity_in_joule_per_m2_per_kelvin(insulated_inside, 0.0) == 0.0
    assert derivation.areal_heat_capacity_in_joule_per_m2_per_kelvin(thick_uninsulated, 0.10) == 0.10 * 2000.0 * 1000.0


def test_the_altitude_adjustment_gives_the_table_value() -> None:
    """0.5 ach times 1.0156/1.2255 is the 0.414 ach of Table 7-4."""
    adjusted = derivation.altitude_adjusted_air_change_rate_per_hour(0.5)

    assert adjusted == 0.5 * 1.0156 / 1.2255
    assert adjusted == pytest.approx(AppendixB.ADJUSTED_INFILTRATION_PER_HOUR, abs=0.0005)


class SyntheticGlazing:
    """A glazing with a two-point table, so the interpolation is checkable by hand."""

    GLAZING = Glazing("two_point", 3.0, 0.8, 0.6, {0: 0.8, 60: 0.4, 90: 0.0}, "test")
    WINDOW = Window("south", 12.0, 180.0, 90.0, "two_point")
    LATITUDE = 39.833
    LONGITUDE = -104.65

    @classmethod
    def june_day(cls, dni: float, dhi: float) -> pd.DataFrame:
        """One day of hourly rows at the station's half hours, with the given beam and diffuse irradiance."""
        index = pd.date_range("2021-06-21 00:30:00", periods=24, freq="h", tz="Etc/GMT+7")
        return pd.DataFrame({"DNI": dni, "DHI": dhi, "GHI": dhi}, index=index)


def test_the_coefficient_is_interpolated_in_the_cosine_and_clipped_at_ninety_degrees() -> None:
    """Table angles give the table, 95 degrees gives the value at 90, 30 degrees sits on the cosine line."""
    glazing = SyntheticGlazing.GLAZING
    at = derivation.shgc_at_incidence(glazing, np.array([0.0, 60.0, 90.0, 95.0, 30.0]))

    assert list(at[:4]) == [0.8, 0.4, 0.0, 0.0]
    cosine_fraction = (np.cos(np.radians(30.0)) - np.cos(np.radians(60.0))) / (1.0 - np.cos(np.radians(60.0)))
    assert at[4] == pytest.approx(0.4 + cosine_fraction * 0.4)


def test_pure_diffuse_irradiance_gives_the_hemispherical_ratio() -> None:
    """Without beam, every watt is weighted with the hemispherical coefficient: F_w = SHGC_hemis / SHGC_n."""
    factor = derivation.nonperpendicular_reduction_factor(
        SyntheticGlazing.WINDOW, SyntheticGlazing.GLAZING, SyntheticGlazing.june_day(0.0, 100.0), SyntheticGlazing.LATITUDE, SyntheticGlazing.LONGITUDE, 0.2
    )

    assert factor.beam_on_plane_in_watt_hour_per_m2 == 0.0
    assert factor.diffuse_on_plane_in_watt_hour_per_m2 > 0.0
    assert factor.nonperpendicular_reduction_factor == pytest.approx(0.6 / 0.8)
    assert factor.hemispherical_ratio == pytest.approx(0.6 / 0.8)


def test_beam_irradiance_is_weighted_with_the_angular_coefficient() -> None:
    """With beam only, F_w lies strictly between the ratio at the steepest table angle and one."""
    factor = derivation.nonperpendicular_reduction_factor(
        SyntheticGlazing.WINDOW, SyntheticGlazing.GLAZING, SyntheticGlazing.june_day(800.0, 0.0), SyntheticGlazing.LATITUDE, SyntheticGlazing.LONGITUDE, 0.2
    )

    assert factor.beam_on_plane_in_watt_hour_per_m2 > 0.0
    assert factor.diffuse_on_plane_in_watt_hour_per_m2 == 0.0
    assert 0.0 < factor.nonperpendicular_reduction_factor < 1.0
