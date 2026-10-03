"""Window solar-gain model used by the Building component.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout and the RC_BuildingSimulator reference). Holds ``Window``, moved
verbatim from the former single-module ``building.py``.
"""

import math
from functools import lru_cache
from typing import ClassVar

import pvlib

from hisim import log


# =====================================================================================================================================
class Window:
    """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""

    #: Solar reflectivity of the ground in front of the window, which decides the ground-reflected share of
    #: the irradiance on its plane. The default of EN ISO 52010-1:2017, and the value the PV system computes
    #: with (``hisim.components.generic_pv_system.calculation.ALBEDO``, kept equal by
    #: ``tests/test_building_window.py``); stated here because pvlib would otherwise fall back to its own
    #: default of 0.25.
    ALBEDO: ClassVar[float] = 0.2

    def __init__(
        self,
        window_azimuth_angle=None,
        window_tilt_angle=None,
        area=None,
        glass_solar_transmittance=None,
        frame_area_fraction_reduction_factor=None,
        external_shading_vertical_reduction_factor=None,
        nonperpendicular_reduction_factor=None,
    ):
        """Construct all the neccessary attributes."""
        self.warning_message_already_shown = False
        # Angles
        self.window_tilt_angle = window_tilt_angle
        self.window_azimuth_angle = window_azimuth_angle
        self.window_tilt_angle_rad: float = 0

        # Area
        self.area = area

        # Transmittance
        self.glass_solar_transmittance = glass_solar_transmittance
        # Incident Solar Radiation
        self.incident_solar: int

        # Reduction factors
        self.nonperpendicular_reduction_factor = nonperpendicular_reduction_factor
        self.external_shading_vertical_reduction_factor = external_shading_vertical_reduction_factor
        self.frame_area_fraction_reduction_factor = frame_area_fraction_reduction_factor

        self.reduction_factor = (
            glass_solar_transmittance
            * nonperpendicular_reduction_factor
            * external_shading_vertical_reduction_factor
            * (1 - frame_area_fraction_reduction_factor)
        )

        self.reduction_factor_with_area = self.reduction_factor * self.area

    def calc_direct_solar_factor(
        self,
        sun_altitude,
        sun_azimuth,
        apparent_zenith,
    ):
        """Calculate the cosine of the angle of incidence on the window.

        Commented equations, that provide a direct calculation, were derived in:

        Proportion of the radiation incident on the window (cos of the incident ray)
        ref:Quaschning, Volker, and Rolf Hanitsch. "Shade calculations in photovoltaic systems."
        ISES Solar World Conference, Harare. 1995.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        sun_altitude_rad = math.radians(sun_altitude)

        aoi = pvlib.irradiance.aoi(
            self.window_tilt_angle,
            self.window_azimuth_angle,
            apparent_zenith,
            sun_azimuth,
        )

        direct_factor = math.cos(aoi) / (math.sin(sun_altitude_rad))

        return direct_factor

    def calc_diffuse_solar_factor(
        self,
    ):
        """Calculate the proportion of diffuse radiation.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        self.window_tilt_angle_rad = math.radians(self.window_tilt_angle)
        # Proportion of incident light on the window surface
        return (1 + math.cos(self.window_tilt_angle_rad)) / 2

    # Calculate solar heat gain through windows.
    # (** Check header)
    @lru_cache(maxsize=16)
    def calc_solar_heat_gains(
        self,
        sun_azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
        window_tilt_angle,
        window_azimuth_angle,
        reduction_factor_with_area,
    ):
        """Calculate the solar gain in the building zone through this window, for one timestep.

        The gain is the total irradiance on the window plane -- beam, sky-diffuse and ground-reflected
        share, as ``pvlib.irradiance.get_total_irradiance`` sums them under an isotropic sky -- times the
        window's reduction factor and area (ISO 13790:2008, 11.3.2 to 11.4.2; the TABULA calculation
        method, Loga et al. 2013).

        :param sun_azimuth: Azimuth angle of the sun in degrees, 180 being south
        :type sun_azimuth: float
        :param direct_normal_irradiance: Direct normal irradiance from the weather in W/m²
        :type direct_normal_irradiance: float
        :param direct_horizontal_irradiance: Diffuse horizontal irradiance from the weather in W/m²
        :type direct_horizontal_irradiance: float
        :param global_horizontal_irradiance: Global horizontal irradiance from the weather in W/m²
        :type global_horizontal_irradiance: float
        :param direct_normal_irradiance_extra: Extraterrestrial normal irradiance in W/m²
        :type direct_normal_irradiance_extra: float
        :param apparent_zenith: Apparent zenith angle of the sun in degrees
        :type apparent_zenith: float
        :return: Solar gain entering the building through the window in W; 0 when the irradiance on the
            plane is undefined
        :rtype: float
        """
        if window_azimuth_angle is None:
            window_azimuth_angle = 0
            if self.warning_message_already_shown is False:
                log.warning("window azimuth angle was set to 0 south because no value was set.")
                self.warning_message_already_shown = True

        poa_irrad = pvlib.irradiance.get_total_irradiance(
            window_tilt_angle,
            window_azimuth_angle,
            apparent_zenith,
            sun_azimuth,
            direct_normal_irradiance,
            global_horizontal_irradiance,
            direct_horizontal_irradiance,
            direct_normal_irradiance_extra,
            albedo=self.ALBEDO,
        )

        if math.isnan(poa_irrad["poa_global"]):
            return 0.0

        return poa_irrad["poa_global"] * reduction_factor_with_area
