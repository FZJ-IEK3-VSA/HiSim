"""The monthly heat gains of the Austrian engine: internal gains and solar gains through the windows.

- **Internal gains** of a residential building, B 8110-6-1:2024-03 Eq. (37):
  Q_i = q_i,h · BGF · 0.8 · t / 1000 in kWh per month, with q_i,h of the usage profile and t the duration
  of the month.
- **Solar gains** of a residential building, Eq. (43): Q_s,h = Σ I_S,ON,j · A_trans,h over the window
  surfaces, with the collector area of Eq. (44), A_trans,h = A_g · F_S,h · g · F_g, the glass area A_g
  as stated (8.3 a)) or by Eq. (40), A_g = f_g · A_W (8.3 b)), and the surface's g-value. The glass share
  f_g and the shading factor F_S,h are the norm's lump values (:mod:`epc.engines.at.defaults`); F_g is part
  of Eq. (44).

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

#: B 8110-6-1:2024-03 8.3.2, Eq. (44): the reduction factor F_g of the glazing's solar yield, 0.9 × 0.98.
GLAZING_REDUCTION = 0.9 * 0.98


def internal_gains(usage: Usage, gross_floor_area_in_m2: float) -> Monthly:
    """Q_i by B 8110-6-1:2024-03 Eq. (37), in kWh per month, January to December."""
    return tuple(
        usage.internal_gains_heating_in_w_per_m2 * gross_floor_area_in_m2 * defaults.REFERENCE_AREA_SHARE * hours / 1000
        for hours in MONTH_DURATION_IN_H
    )


def collector_areas(windows: Sequence[Window], glass_share: float, shading_factor: float) -> Mapping[str, float]:
    """A_trans,h of every window surface by B 8110-6-1:2024-03 Eq. (44), in m², by surface name.

    The glass area A_g is the surface's stated glass area (8.3 a)) or, without one, f_g · A_W (Eq. (40)).
    """
    return MappingProxyType(
        {
            window.name: glass_area(window, glass_share) * shading_factor * window.g_value * GLAZING_REDUCTION
            for window in windows
        }
    )


def glass_area(window: Window, glass_share: float) -> float:
    """A_g of a window surface: its stated glass area, else f_g · A_W (B 8110-6-1:2024-03 8.3, Eq. (40))."""
    return window.glass_area_in_m2 if window.glass_area_in_m2 is not None else glass_share * window.area_in_m2


def solar_gains(areas: Mapping[str, float], climate: Climate) -> Monthly:
    """Q_s,h by B 8110-6-1:2024-03 Eq. (43), in kWh per month, January to December."""
    return tuple(
        sum(climate.radiation_in_kwh_per_m2[name][month] * area for name, area in areas.items())
        for month in range(len(MONTH_DURATION_IN_H))
    )
