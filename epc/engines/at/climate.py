"""The monthly climate of the Austrian engine: outdoor temperature and radiation on every window surface.

The heating demand is computed twice, under the reference climate and under the site climate (R4.1,
R5.1, R5.2), and both give the same two things: the monthly mean outdoor temperature θ_e,j and, per
window surface, the monthly global radiation I_S,ON,j on that surface, which B 8110-6-1:2024-03
Eq. (43) multiplies with the surface's collector area.

- **Reference climate** (B 8110-5:2024-03 5.1, Annex A): temperature and radiation as printed, the
  horizontal surface from Tab. A.1, a tilted surface from Tab. A.2–A.7 by its tilt and its azimuth
  from south.
- **Site climate** (B 8110-5:2024-03 5.2): θ_e,j by Eq. (1) with Annex B by region and elevation band,
  I_S on the horizontal by Eq. (2) with Annex D by region, and I_S,ON,j on the surface by Eq. (3) with
  Annex E by elevation band, azimuth and tilt, h the elevation in m.

Every angle must be one the tables print; an angle between two grid values is refused (``E-13``), not
interpolated (D21, P10). The site fields are checked by the engine before (``E-09``, ``E-10``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence, Tuple

from epc.engines.at.climate_tables import (
    REFERENCE_AZIMUTHS,
    REFERENCE_TILTS,
    TRANSPOSITION_AZIMUTHS,
    TRANSPOSITION_TILTS,
)
from epc.engines.at.data_tables import AustrianTables, temperature_coefficients_for, transposition_band_for
from epc.engines.at.table_reading import Monthly
from epc.model.building import Window
from epc.model.errors import EpcError, EpcErrorKind


@dataclass(frozen=True)
class Climate:
    """The monthly climate a building is computed under.

    Attributes:
        outdoor_temperatures_in_c: θ_e,j, January to December.
        radiation_in_kwh_per_m2: I_S,ON,j per window surface, by the surface's name, January to December.
    """

    outdoor_temperatures_in_c: Monthly
    radiation_in_kwh_per_m2: Mapping[str, Monthly]


def reference_climate(tables: AustrianTables, windows: Sequence[Window]) -> Climate:
    """The reference climate of B 8110-5:2024-03 Annex A for the window surfaces.

    Args:
        tables: The Austrian tables.
        windows: The window surfaces of the description.

    Returns:
        θ_e,j of Tab. A.1 and I_S,ON,j of Tab. A.1–A.7 per surface.

    Raises:
        EpcError: ``E-13`` for a surface whose tilt or azimuth is not printed in Annex A.
    """
    table = tables.reference_climate
    radiation = {}
    for window in windows:
        tilt, azimuth = _angles(window)
        if tilt == 0:
            radiation[window.name] = table.horizontal_radiation_kwh_per_m2
            continue
        if tilt not in REFERENCE_TILTS or azimuth not in REFERENCE_AZIMUTHS:
            raise _off_grid(window)
        radiation[window.name] = table.radiation_kwh_per_m2[(tilt, azimuth)]
    return Climate(table.temperatures_c, MappingProxyType(radiation))


def site_climate(tables: AustrianTables, windows: Sequence[Window], region: str, elevation_in_m: float) -> Climate:
    """The site climate of B 8110-5:2024-03 5.2 for a climate region and an elevation.

    Args:
        tables: The Austrian tables.
        windows: The window surfaces of the description.
        region: The climate region of Bild 1, checked by the engine.
        elevation_in_m: The elevation h in m, checked by the engine.

    Returns:
        θ_e,j by Eq. (1) and I_S,ON,j by Eq. (2) and (3) per surface.

    Raises:
        EpcError: ``E-13`` for a surface whose tilt or azimuth is not printed in Annex E.
    """
    coefficients = temperature_coefficients_for(tables, region, elevation_in_m)
    temperatures = tuple(a + b * elevation_in_m / 100 for a, b in zip(coefficients.a, coefficients.b))
    horizontal = global_radiation(tables, region, elevation_in_m)
    band = transposition_band_for(tables, elevation_in_m)
    radiation = {}
    for window in windows:
        tilt, azimuth = _angles(window)
        if tilt not in TRANSPOSITION_TILTS or abs(azimuth) not in TRANSPOSITION_AZIMUTHS:
            raise _off_grid(window)
        factors = band.factors[(abs(azimuth), tilt)]
        radiation[window.name] = tuple(value * factor for value, factor in zip(horizontal, factors))
    return Climate(temperatures, MappingProxyType(radiation))


def global_radiation(tables: AustrianTables, region: str, elevation_in_m: float) -> Monthly:
    """I_S on the horizontal surface by B 8110-5:2024-03 Eq. (2), in kWh/(m² · M), January to December."""
    coefficients = tables.radiation_coefficients[region]
    return tuple(
        a2 * elevation_in_m**2 + a1 * elevation_in_m + a0
        for a2, a1, a0 in zip(coefficients.a2, coefficients.a1, coefficients.a0)
    )


def _angles(window: Window) -> Tuple[float, float]:
    """A surface's tilt and its azimuth from south in (−180°, 180°]; ``E-13`` when either is not finite."""
    if not (math.isfinite(window.tilt_in_degree) and math.isfinite(window.azimuth_in_degree)):
        raise _off_grid(window)
    azimuth = window.azimuth_in_degree % 360
    return window.tilt_in_degree, azimuth - 360 if azimuth > 180 else azimuth


def _off_grid(window: Window) -> EpcError:
    """The ``E-13`` refusal of a surface whose angles the tables do not print."""
    return EpcError(
        EpcErrorKind.WINDOW_ANGLE_OFF_GRID, name=window.name, a=window.azimuth_in_degree, t=window.tilt_in_degree
    )
