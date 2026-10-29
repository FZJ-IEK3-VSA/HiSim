"""The monthly heat gains of the Austrian engine: internal gains and solar gains through the windows.

- **Internal gains** of a residential building, B 8110-6-1:2024-03 Eq. (37), with q_i,h of the usage
  profile and the duration of the month.
- **Solar gains** of a residential building, Eq. (43), over the window surfaces, with the collector area
  of Eq. (44), the glass area of Eq. (40), the window surface's area and HiSim's g-value. The glass share
  and the shading factor are the norm's lump values (:mod:`epc.engines.at.defaults`); F_g is part of
  Eq. (44). The constants are read by key from the tables (D7).

Both are computed per climate from the same building; only I_S,ON,j differs between them (I-3).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Sequence

from epc.engines.at import defaults
from epc.engines.at.climate import Climate
from epc.engines.at.table_reading import Monthly
from epc.engines.at.usage import MONTH_DURATION_IN_H, Usage
from epc.model.building import Window

#: B 8110-6-1:2024-03 8.3.2, Eq. (44): the two factors of F_g, the glazing's reduction of the solar yield.
GLAZING_NON_PERPENDICULAR_FACTOR = "glazing_non_perpendicular_factor"
GLAZING_SOILING_FACTOR = "glazing_soiling_factor"


def internal_gains(constants: Mapping[str, float], usage: Usage, gross_floor_area_in_m2: float) -> Monthly:
    """Q_i by B 8110-6-1:2024-03 Eq. (37), in kWh per month, January to December."""
    share = constants[defaults.REFERENCE_AREA_SHARE]
    return tuple(
        usage.internal_gains_heating_in_w_per_m2 * gross_floor_area_in_m2 * share * hours / 1000
        for hours in MONTH_DURATION_IN_H
    )


def collector_areas(
    constants: Mapping[str, float], windows: Sequence[Window], glass_share: float, shading_factor: float
) -> Mapping[str, float]:
    """A_trans,h of every window surface by B 8110-6-1:2024-03 Eq. (40) and (44), in m², by surface name."""
    reduction = constants[GLAZING_NON_PERPENDICULAR_FACTOR] * constants[GLAZING_SOILING_FACTOR]
    return MappingProxyType(
        {
            window.name: glass_share * window.area_in_m2 * shading_factor * window.g_value * reduction
            for window in windows
        }
    )


def solar_gains(areas: Mapping[str, float], climate: Climate) -> Monthly:
    """Q_s,h by B 8110-6-1:2024-03 Eq. (43), in kWh per month, January to December."""
    return tuple(
        sum(climate.radiation_in_kwh_per_m2[name][month] * area for name, area in areas.items())
        for month in range(len(MONTH_DURATION_IN_H))
    )
