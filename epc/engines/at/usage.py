"""The norm usage of a residential building: its profile by dwelling units and the time basis of a month.

OENORM B 8110-5:2024-03 Section 8 assigns every building a usage profile; a residential building takes
the row of its dwelling-unit class from Tab. 7 (set temperature θ_i,h, hygienic air change n_L,hyg) and
Tab. 9 (internal gains q_i,h). The classes are read from the data table, never from code.

The time basis of a residential building is the calendar month: B 8110-6-1:2024-03 Eq. (29a) and (37)
take ``t`` as the duration of the month in hours, February with 28 days.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from typing import Tuple

from epc.engines.at.data_tables import AustrianTables, usage_profile_for
from epc.model.building import BuildingDescription

#: A year without a leap day: the reference year of the monthly balance.
_REFERENCE_YEAR = 2023

#: The duration t of each month in h, January to December (B 8110-6-1:2024-03 Eq. (29a), (37)).
MONTH_DURATION_IN_H: Tuple[float, ...] = tuple(
    24.0 * calendar.monthrange(_REFERENCE_YEAR, month)[1] for month in range(1, 13)
)


@dataclass(frozen=True)
class Usage:
    """The usage profile a building computes with.

    Attributes:
        dwelling_unit_class: The class as the table prints it.
        set_temperature_heating_in_c: θ_i,h, B 8110-5 Tab. 7.
        hygienic_air_change_per_h: n_L,hyg, B 8110-5 Tab. 7.
        internal_gains_heating_in_w_per_m2: q_i,h, B 8110-5 Tab. 9.
    """

    dwelling_unit_class: str
    set_temperature_heating_in_c: float
    hygienic_air_change_per_h: float
    internal_gains_heating_in_w_per_m2: float


def usage(tables: AustrianTables, building: BuildingDescription) -> Usage:
    """The usage profile of a residential building by its dwelling units (B 8110-5:2024-03 Section 8).

    Args:
        tables: The Austrian tables.
        building: The description; its dwelling units are at least one.

    Returns:
        The profile of the building's dwelling-unit class.
    """
    row = usage_profile_for(tables, building.dwelling_units)
    return Usage(row.label, row.theta_i_h_c, row.n_l_hyg_per_h, row.q_i_h_w_per_m2)
