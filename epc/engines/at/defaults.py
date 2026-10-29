"""What the Austrian engine assumes where HiSim's building does not state what the norm needs.

The building description is HiSim's building as it is (D24); the norm needs more: a gross floor area
and a gross volume, a thermal-bridge allowance, a ventilation case, and for every element the row of
B 8110-6-1 Tab. 3–5 that gives its temperature-correction factor. This module is the one place that
decides those: each function returns the value or the row together with the :class:`Assumption` that
states it, and the engine records every assumption in the result (R4.4). No stage file applies a
default of its own.

The values of the norm's single constants are local data like its tables (D7, R8.1): the functions
read them by the keys below from the tables' ``constants``. The storey height of the gross volume is
the one value that is the engine's own choice, and its assumption says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from epc.model.building import BuildingDescription, Element, Situation
from epc.model.result import Assumption

#: B 8110-6-1:2024-03 Section 4, p. 8: the reference area BF of a residential building as a share of BGF.
REFERENCE_AREA_SHARE = "reference_area_share"

#: The gross storey height taken for the gross volume when the record states none: the one with which
#: B 8110-6-1:2024-03 10.3.2, Eq. (65), normalises non-residential buildings. The norm states no
#: substitute for the gross volume of a residential building (Section 4); this is the engine's choice.
GROSS_STOREY_HEIGHT = "gross_storey_height"

#: B 8110-6-1:2024-03 8.3, Eq. (40), simplified approach b): the glass share f_g of a window's area.
GLASS_SHARE = "glass_share"

#: B 8110-6-1:2024-03 8.3.1.2.2: the shading factor F_S,h of the smallest class of dwelling units (with the
#: terraced houses), of the middle and of the largest class, and the upper limits of the first two classes.
SHADING_FACTOR_SMALL = "shading_factor_small"
SHADING_FACTOR_MEDIUM = "shading_factor_medium"
SHADING_FACTOR_LARGE = "shading_factor_large"
SHADING_SMALL_UP_TO_UNITS = "shading_small_up_to_units"
SHADING_MEDIUM_UP_TO_UNITS = "shading_medium_up_to_units"

#: The row of Tab. 3–5 per situation, and whether taking it is an assumption. A conditioned neighbour is
#: outside the thermal envelope and takes no row (Section 4, p. 8).
_ROWS: Dict[Situation, Tuple[Optional[str], Optional[str]]] = {
    Situation.OUTSIDE_AIR: ("OUTSIDE_AIR", None),
    Situation.CONDITIONED_NEIGHBOUR: (None, None),
    Situation.UNCONDITIONED_ATTIC: (
        "CEILING_TO_UNCONDITIONED_ATTIC",
        "ceiling to an unconditioned, closed attic (Tab. 4)",
    ),
    Situation.UNCONDITIONED_CELLAR: (
        "CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED",
        "ceiling to an unconditioned, uninsulated cellar (Tab. 4); a cellar counts as insulated only by "
        "its walls and floor (Tab. 4 footnote a), which the building description does not state",
    ),
    Situation.UNCONDITIONED_SPACE: ("WALL_TO_BUFFER_SPACE", "wall to another buffer space (Tab. 4)"),
}


@dataclass(frozen=True)
class Defaulted:
    """A value the engine computes with, and the assumption behind it when the description does not state it.

    Attributes:
        value: The value.
        assumption: The assumption to record, or ``None`` when the value is the description's own.
    """

    value: float
    assumption: Optional[Assumption]


def gross_floor_area(constants: Mapping[str, float], building: BuildingDescription) -> Defaulted:
    """The conditioned gross floor area BGF: HiSim's conditioned floor area taken as the reference area BF.

    Args:
        constants: The prose constants of the tables.
        building: The description.

    Returns:
        BGF in m², with its assumption (D26).
    """
    share = constants[REFERENCE_AREA_SHARE]
    return Defaulted(
        building.conditioned_floor_area_in_m2 / share,
        Assumption(
            "gross_floor_area",
            f"BGF = conditioned floor area / {share}; HiSim's conditioned floor area taken as the reference area BF",
            "B 8110-6-1:2024-03 Section 4: BF as a share of BGF for residential buildings",
        ),
    )


def gross_volume(constants: Mapping[str, float], building: BuildingDescription,
                 gross_floor_area_in_m2: float) -> Defaulted:
    """The conditioned gross volume V: the record's when it states one, else BGF times a storey height.

    Args:
        constants: The prose constants of the tables.
        building: The description.
        gross_floor_area_in_m2: BGF.

    Returns:
        V in m³, with an assumption only when the record states no volume (D26, P11).
    """
    if building.gross_volume_in_m3 is not None:
        return Defaulted(building.gross_volume_in_m3, None)
    height = constants[GROSS_STOREY_HEIGHT]
    return Defaulted(
        gross_floor_area_in_m2 * height,
        Assumption(
            "gross_volume",
            f"V = BGF x {height} m",
            "the record states no gross volume and B 8110-6-1:2024-03 Section 4 none for residential "
            "buildings; the storey height of Eq. (65), which normalises non-residential buildings",
        ),
    )


def thermal_bridges() -> Assumption:
    """The thermal-bridge allowance: the default of B 8110-6-1 5.3.2 c), Eq. (11a); the record states none."""
    return Assumption(
        "thermal_bridges",
        "L_psi + L_chi by the default of B 8110-6-1 5.3.2 c), Eq. (11a)",
        "B 8110-6-1:2024-03 5.3.2 c): until the planned thermal-bridge catalogue exists; the record states no "
        "thermal bridges",
    )


def ventilation() -> Assumption:
    """The ventilation case: natural ventilation with the hygienic air change; the record states no system."""
    return Assumption(
        "ventilation",
        "natural ventilation, n_nL = n_L,hyg",
        "B 8110-6-1:2024-03 6.2.2, Eq. (15a), (15b); the record states no ventilation system",
    )


def glass_share(constants: Mapping[str, float]) -> Defaulted:
    """The glass share f_g of a window: the description states the window areas, not their glass areas."""
    share = constants[GLASS_SHARE]
    return Defaulted(
        share,
        Assumption(
            "glass_share",
            f"A_g = {share} A_W",
            "B 8110-6-1:2024-03 8.3, Eq. (40), simplified approach b); the record states no glass areas",
        ),
    )


def shading_factor(constants: Mapping[str, float], building: BuildingDescription) -> Defaulted:
    """The shading factor F_S,h of every glazed element: the lump value of B 8110-6-1:2024-03 8.3.1.2.2.

    The smallest class of dwelling units and terraced houses take the first value, the middle class the
    second, larger buildings the third. Without a building type the dwelling units alone decide, and the
    assumption says so (R5.9, D30).

    Args:
        constants: The prose constants of the tables.
        building: The description.

    Returns:
        F_S,h with its assumption.
    """
    units = building.dwelling_units
    if building.building_type == "terraced" or units <= constants[SHADING_SMALL_UP_TO_UNITS]:
        value = constants[SHADING_FACTOR_SMALL]
    elif units <= constants[SHADING_MEDIUM_UP_TO_UNITS]:
        value = constants[SHADING_FACTOR_MEDIUM]
    else:
        value = constants[SHADING_FACTOR_LARGE]
    basis = f"{units} dwelling units" + (
        ", building type not stated" if building.building_type is None else f", {building.building_type}"
    )
    return Defaulted(
        value,
        Assumption(
            "shading_factor",
            f"F_S,h = {value} for {basis}",
            "B 8110-6-1:2024-03 8.3.1.2.2, the lump value for all glazed elements in the heating case",
        ),
    )


def correction_factor_row(element: Element) -> Tuple[Optional[str], Optional[Assumption]]:
    """The row of B 8110-6-1 Tab. 3–5 whose temperature-correction factor an element takes (D29).

    Outside air is the Tab. 3 row; an unconditioned space is a Tab. 4 row by element class, which is the
    engine's default and an assumption; a conditioned neighbour takes no row, as it lies outside the
    thermal envelope.

    Args:
        element: One element of the description.

    Returns:
        The row key of the data contract, or ``None`` for a conditioned neighbour, and the assumption
        when the row is a default.
    """
    row, taken_as = _ROWS[element.situation]
    if taken_as is None:
        return row, None
    return row, Assumption(
        f"correction_factor_{element.name}",
        f"{element.name} with HiSim's adjustment factor {element.adjustment_factor_used_by_hisim} taken as {taken_as}",
        "B 8110-6-1:2024-03 Tab. 4; the row per situation is the engine's default (D29)",
    )
