"""The solar gain through a window is the whole irradiance on its plane, not the beam share alone.

A window takes the beam, the sky-diffuse and the ground-reflected irradiance on its plane and passes the
TABULA reduction factor's share of them into the zone. Two cases pin that sum: a north window, which the
beam misses entirely, and a south window facing the sun, where the beam is most of it. Under a uniformly
bright sky a vertical plane sees one half of the sky dome and one half of the ground, so it receives half
the diffuse horizontal irradiance and half the reflected global horizontal irradiance (Duffie and Beckman,
Solar Engineering of Thermal Processes, 4th ed. 2013, eq. 2.15.1; the sky-view and ground-view factors
``(1 ± cos tilt) / 2`` evaluate to 1/2 at 90°).

The sky is the same in both: the sun in the south at a zenith of 60°, a direct normal irradiance of
800 W/m², a diffuse horizontal irradiance of 100 W/m² and a global horizontal irradiance of 500 W/m².
"""

import math

import pytest

from hisim.components.building.window import Window
from hisim.components.generic_pv_system import calculation as pv_calculation

DNI, DHI, GHI = 800.0, 100.0, 500.0
SUN_AZIMUTH, APPARENT_ZENITH = 180.0, 60.0


def _window(window_azimuth_angle: float) -> Window:
    """A vertical window of one square metre facing the given azimuth, 180 being south."""
    return Window(
        window_tilt_angle=90.0,
        window_azimuth_angle=window_azimuth_angle,
        area=1.0,
        glass_solar_transmittance=0.6,
        frame_area_fraction_reduction_factor=0.3,
        external_shading_vertical_reduction_factor=0.6,
        nonperpendicular_reduction_factor=0.9,
    )


def _gain(window: Window, **weather) -> float:
    """The window's solar gain under the module's sky, with any weather value overridden."""
    return float(
        window.calc_solar_heat_gains(
            sun_azimuth=weather.get("sun_azimuth", SUN_AZIMUTH),
            direct_normal_irradiance=weather.get("direct_normal_irradiance", DNI),
            diffuse_horizontal_irradiance=weather.get("diffuse_horizontal_irradiance", DHI),
            global_horizontal_irradiance=weather.get("global_horizontal_irradiance", GHI),
            direct_normal_irradiance_extra=weather.get("direct_normal_irradiance_extra", 1361.0),
            apparent_zenith=weather.get("apparent_zenith", APPARENT_ZENITH),
            window_tilt_angle=window.window_tilt_angle,
            window_azimuth_angle=window.window_azimuth_angle,
            reduction_factor_with_area=window.reduction_factor_with_area,
        )
    )


#: Half the sky dome and half the ground, which a vertical plane sees whichever way it faces.
DIFFUSE_ON_A_VERTICAL_PLANE = (DHI + Window.ALBEDO * GHI) / 2


@pytest.mark.base
def test_the_window_and_the_pv_system_assume_the_same_ground_albedo() -> None:
    """One ground in front of the house: the window's reflected share and the PV system's agree on it."""
    assert Window.ALBEDO == pv_calculation.ALBEDO


@pytest.mark.base
def test_a_north_window_with_the_sun_in_the_south_receives_the_diffuse_light() -> None:
    """Beam excluded by geometry (angle of incidence above 90°), the gain is the diffuse share alone."""
    north_window = _window(window_azimuth_angle=0.0)

    gain = _gain(north_window)

    assert gain == pytest.approx(
        DIFFUSE_ON_A_VERTICAL_PLANE * north_window.reduction_factor_with_area, rel=1e-9
    )


@pytest.mark.base
def test_a_south_window_facing_the_sun_receives_the_beam_on_top_of_the_diffuse_light() -> None:
    """The beam is the larger share here, so a gain that carried the diffuse light alone would fail."""
    south_window = _window(window_azimuth_angle=180.0)
    # The sun is in the plane's own azimuth, so the angle of incidence is measured from the vertical
    # normal: cos(aoi) = sin(zenith), and the beam on the plane is DNI times it.
    beam_on_the_wall = DNI * math.sin(math.radians(APPARENT_ZENITH))

    gain = _gain(south_window)

    assert gain == pytest.approx(
        (beam_on_the_wall + DIFFUSE_ON_A_VERTICAL_PLANE) * south_window.reduction_factor_with_area,
        rel=1e-9,
    )
    assert beam_on_the_wall > DIFFUSE_ON_A_VERTICAL_PLANE


@pytest.mark.base
@pytest.mark.parametrize("corrupt_value", ["diffuse_horizontal_irradiance", "apparent_zenith"])
def test_a_window_refuses_a_weather_value_that_is_not_a_finite_number(corrupt_value: str) -> None:
    """A gap in the weather file is corrupt input, not a timestep without sun, so the run fails.

    One irradiance and one sun position, because the guard is on the irradiance pvlib computes rather
    than on any single value it is given: both reach it by the same path.
    """
    south_window = _window(window_azimuth_angle=180.0)

    with pytest.raises(ValueError, match="not a finite number"):
        _gain(south_window, **{corrupt_value: float("nan")})
