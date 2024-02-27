"""Obsolete buildng module.

Here are functions added that were used in the buildig module before.

"""
#  clean
# Generic/Built-in

import math
import pvlib
import numpy as np

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


# def get_thermal_conductance_ventilation_in_watt_per_kelvin(
#     self,
# ):
#     """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
# Long from for H_ve_adj: Ventilation
# Determine the ventilation conductance
# Building volume (TABULA: Conditioned building volume)
# room_volume_in_m3 = float(self.buildingdata["V_C"].values[0])
# ven_method = "RC_BuildingSimulator"
# if ven_method == "RC_BuildingSimulator":
#     """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
#     air_changes_per_hour_through_ventilation = 1.5
#     air_changes_per_hour_through_infiltration = 0.5
#     # The efficiency of the heat recovery system for ventilation. Set to 0 if there is no heat recovery
#     # ventilation_efficiency_of_the_heat_recovery_system = 0.6
#     # heat_capacity_of_air_per_volume_in_joule_per_m3_per_kelvin = 1200

#     total_air_changes_per_hour = (
#         air_changes_per_hour_through_infiltration
#         + air_changes_per_hour_through_ventilation
#     )

# Fraction of the considered air flow that goes through the heat recovery system
# fraction_of_air_flow_through_heat_recovery_system = (
#     air_changes_per_hour_through_ventilation / total_air_changes_per_hour
# )

# Temperature adjustment factor taking ventilation and infiltration
# [ISO: 9.3.2.8]
# temperature_adjustment_factor_for_air_flow_from_a_heat_recovery_system = (
#     1
#     - fraction_of_air_flow_through_heat_recovery_system
#     * ventilation_efficiency_of_the_heat_recovery_system
# )
# # [ISO: 9.3.1]
# thermal_conductance_by_ventilation_in_watt_per_kelvin = float(
#     heat_capacity_of_air_per_volume_in_joule_per_m3_per_kelvin
#     * temperature_adjustment_factor_for_air_flow_from_a_heat_recovery_system
#     * room_volume_in_m3
#     * (total_air_changes_per_hour / 3600)
# )


class WindowObsolete:

    """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""

    def __init__(
        self,
        window_azimuth_angle=None,
        window_tilt_angle=90,
        area=None,
        glass_solar_transmittance=0.6,
        frame_area_fraction_reduction_factor=0.3,
        external_shading_vertical_reduction_factor=1.0,
        nonperpendicular_reduction_factor=0.9,
    ):
        """Constructs all the neccessary attributes."""
        # Angles
        self.window_tilt_angle = window_tilt_angle
        self.window_azimuth_angle = window_azimuth_angle
        self.window_tilt_angle_rad = math.radians(window_tilt_angle)
        self.window_azimuth_angle_rad = math.radians(window_azimuth_angle)

        # Area
        self.area = area

        # Transmittance
        self.glass_solar_transmittance = glass_solar_transmittance

        # Incident Solar Radiation
        self.incident_solar: int

        # Reduction factors
        self.nonperpendicular_reduction_factor = nonperpendicular_reduction_factor
        self.external_shading_vertical_reduction_factor = (
            external_shading_vertical_reduction_factor
        )
        self.frame_area_fraction_reduction_factor = frame_area_fraction_reduction_factor

        self.reduction_factor = (
            glass_solar_transmittance
            * nonperpendicular_reduction_factor
            * external_shading_vertical_reduction_factor
            * (1 - frame_area_fraction_reduction_factor)
        )

        self.reduction_factor_with_area = self.reduction_factor * area

    def calc_solar_gains(
        self,
        sun_azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
    ):
        """Calculates the Solar Gains in the building zone through the set Window.

        :param sun_altitude: Altitude Angle of the Sun in Degrees
        :type sun_altitude: float
        :param sun_azimuth: Azimuth angle of the sun in degrees
        :type sun_azimuth: float
        :param normal_direct_radiation: Normal Direct Radiation from weather file
        :type normal_direct_radiation: float
        :param horizontal_diffuse_radiation: Horizontal Diffuse Radiation from weather file
        :type horizontal_diffuse_radiation: float
        :return: self.incident_solar, Incident Solar Radiation on window
        :return: self.solar_gains - Solar gains in building after transmitting through the window
        :rtype: float
        """
        albedo = 0.4
        # automatic pd time series in future pvlib version
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
        # use perez model to calculate the plane of array diffuse sky radiation
        poa_sky_diffuse = pvlib.irradiance.perez(
            self.window_tilt_angle,
            self.window_azimuth_angle,
            direct_horizontal_irradiance,
            np.float64(direct_normal_irradiance),
            direct_normal_irradiance_extra,
            apparent_zenith,
            sun_azimuth,
            airmass,
        )
        # calculate ground diffuse with specified albedo
        poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(
            self.window_tilt_angle,
            global_horizontal_irradiance,
            albedo=albedo,
        )
        # calculate angle of incidence
        aoi = pvlib.irradiance.aoi(
            self.window_tilt_angle,
            self.window_azimuth_angle,
            apparent_zenith,
            sun_azimuth,
        )
        # calculate plane of array irradiance
        poa_irrad = pvlib.irradiance.poa_components(
            aoi,
            np.float64(direct_normal_irradiance),
            poa_sky_diffuse,
            poa_ground_diffuse,
        )

        if math.isnan(poa_irrad["poa_direct"]):
            self.incident_solar = 0
        else:
            self.incident_solar = (poa_irrad["poa_direct"]) * self.area

        solar_gains = (
            self.incident_solar
            * self.glass_solar_transmittance
            * self.nonperpendicular_reduction_factor
            * self.external_shading_vertical_reduction_factor
            * (1 - self.frame_area_fraction_reduction_factor)
        )
        return solar_gains
