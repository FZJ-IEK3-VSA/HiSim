"""Obsolete pv system functions."""

from typing import Any
import math
import pvlib

def simphotovoltaicfast(
    temperature_model: Any,
    dni_extra: Any,
    dni: Any,
    dhi: Any,
    ghi: Any,
    azimuth: Any,
    apparent_zenith: Any,
    temperature: Any,
    wind_speed: Any,
    surface_azimuth: float,
    surface_tilt: float,
) -> Any:
    """Simulates a defined PV array with the Sandia PV Array Performance Model.

    The implementation is done in accordance with following tutorial:
    https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb

    Parameters
    ----------
    surface_tilt: int or float, optional (default:30)
        Tilt angle of the array in degree.
    surface_azimuth: int or float, optional (default:180)
        Azimuth angle of the array in degree. 180 degree means south,
        90 degree east and 270 west.
    losses: float, optional (default: 0.1)
        Losses due to soiling, mismatch, diode connections, dc wiring etc.
    apparent_zenith: Any
        Apparent zenith.
    azimuth: int, float
        Azimuth.
    dni: Any
        direct normal irradiance.
    ghi: Any
        global horizontal irradiance.
    dhi: Any
        direct horizontal irradiance.
    dni_extra: Any
        direct normal irradiance extra.
    temperature: Any
        tempertaure.
    temperature_model: Any
        temperature model.
    wind_speed: Any
        wind_speed.

    Returns
    -------
    pv_dc: Any
        pv_dc

    """

    poa_irrad = pvlib.irradiance.get_total_irradiance(
        surface_tilt,
        surface_azimuth,
        apparent_zenith,
        azimuth,
        dni,
        ghi,
        dhi,
        dni_extra,
    )

    pvtemps = pvlib.temperature.sapm_cell(
        poa_irrad["poa_global"], temperature, wind_speed, **temperature_model
    )

    pv_dc = pvlib.pvsystem.pvwatts_dc(
        poa_irrad["poa_global"],
        temp_cell=pvtemps,
        pdc0=1,
        gamma_pdc=-0.002,
        temp_ref=25.0,
    )
    if math.isnan(pv_dc):
        pv_dc = 0
    return pv_dc
