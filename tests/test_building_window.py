"""A window facing away from the sun still receives diffuse light, half from the sky and half from the ground.

A vertical plane sees one half of the sky dome and one half of the ground. Under a uniformly bright sky it
therefore receives half the diffuse horizontal irradiance from the sky and half the reflected global
horizontal irradiance from the ground (Duffie and Beckman, Solar Engineering of Thermal Processes, 4th ed.
2013, eq. 2.15.1; the sky-view and ground-view factors ``(1 ± cos tilt) / 2`` evaluate to 1/2 at 90°). The
window model multiplies the irradiance on its plane by the TABULA reduction factor it carries.
"""

import pytest

from hisim.components.building.window import Window

#: Solar reflectivity of the ground (EN ISO 52010-1:2017 default; the value the PV system uses as well).
GROUND_ALBEDO: float = 0.2


@pytest.mark.base
def test_a_north_window_with_the_sun_in_the_south_receives_the_diffuse_light() -> None:
    """Beam excluded by geometry (angle of incidence above 90°), the gain is the diffuse share alone."""
    north_window = Window(
        window_tilt_angle=90.0,
        window_azimuth_angle=0.0,  # 180 is south
        area=1.0,
        glass_solar_transmittance=0.6,
        frame_area_fraction_reduction_factor=0.3,
        external_shading_vertical_reduction_factor=0.6,
        nonperpendicular_reduction_factor=0.9,
    )
    # A clear sky with the sun in the south at a zenith of 60°: the global horizontal irradiance is the
    # horizontal projection of the beam, 800 * cos 60°, plus the diffuse horizontal irradiance.
    dni, dhi, ghi = 800.0, 100.0, 500.0

    gain = north_window.calc_solar_heat_gains(
        sun_azimuth=180.0,
        direct_normal_irradiance=dni,
        direct_horizontal_irradiance=dhi,  # the model's name for the diffuse horizontal irradiance
        global_horizontal_irradiance=ghi,
        direct_normal_irradiance_extra=1361.0,
        apparent_zenith=60.0,
        window_tilt_angle=north_window.window_tilt_angle,
        window_azimuth_angle=north_window.window_azimuth_angle,
        reduction_factor_with_area=north_window.reduction_factor_with_area,
    )

    diffuse_on_the_wall = (dhi + GROUND_ALBEDO * ghi) / 2
    assert gain == pytest.approx(diffuse_on_the_wall * north_window.reduction_factor_with_area, rel=1e-9)
