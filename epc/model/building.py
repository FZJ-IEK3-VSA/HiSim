"""The input dictionary: one building as HiSim computed with it, in no country's terms.

A :class:`BuildingDescription` is what every engine computes from. It holds HiSim's building as it
is: per element class one :class:`Element` with HiSim's area, U-value and adjustment factor, the
windows per direction as :class:`Window` surfaces, the glazing, the conditioned floor area, the
dwelling units, the thermal mass class and the :class:`Site`. Nothing here knows a norm or a file;
the reader of step 1 fills it from the expanded record, and a validation example may state it by
hand with its situations directly (design §3.3, §5.2).

The description is immutable and checks its values on construction (I-9). The checks refuse only
what no building can have: a negative area or U-value, a window surface without area, a g-value
outside (0, 1], fewer than one dwelling unit, a window element with an area but no window surface
to carry its gains. An element with area 0 is a building without that element (HiSim writes a door
of area 0 for an archetype that has none) and is accepted.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Tuple

from epc.model.errors import EpcError, EpcErrorKind

ElementName = Literal["floor", "wall", "roof", "window", "door"]
BuildingType = Literal["detached", "terraced", "multi_family", "apartment_block"]

#: The element classes HiSim computes with, one element each, in the order of the expanded record.
ELEMENT_NAMES: Tuple[ElementName, ...] = ("floor", "wall", "roof", "window", "door")

#: The building types the expanded record can state; ``None`` where HiSim's code states none.
BUILDING_TYPES: Tuple[BuildingType, ...] = ("detached", "terraced", "multi_family", "apartment_block")


@enum.unique
class Situation(enum.Enum):
    """What an element borders on, from HiSim's adjustment factor and the element class (design §7.2).

    ``UNCONDITIONED_GARAGE`` and ``GROUND`` are never derived from a record: a description written by
    hand, such as a norm's worked example or a real certificate, states them for a floor above a closed
    garage or a floor on the ground.
    """

    OUTSIDE_AIR = "OUTSIDE_AIR"
    CONDITIONED_NEIGHBOUR = "CONDITIONED_NEIGHBOUR"
    UNCONDITIONED_ATTIC = "UNCONDITIONED_ATTIC"
    UNCONDITIONED_CELLAR = "UNCONDITIONED_CELLAR"
    UNCONDITIONED_SPACE = "UNCONDITIONED_SPACE"
    UNCONDITIONED_GARAGE = "UNCONDITIONED_GARAGE"
    GROUND = "GROUND"


@dataclass(frozen=True)
class Element:
    """One element class of HiSim's envelope, as HiSim computed with it.

    Attributes:
        name: The element class.
        area_in_m2: HiSim's area of the element.
        u_value_in_watt_per_m2_per_kelvin: HiSim's U-value of the element.
        adjustment_factor_used_by_hisim: HiSim's transmission adjustment factor, from which the
            situation follows.
        situation: What the element borders on.
        u_value_origin: HiSim's origin string of the U-value, verbatim.
        stated_correction_factor: The heating-case temperature-correction factor, if stated (a
            certificate prints one per element); the engine then takes it instead of the row of the
            situation (D43). HiSim's record never states it.
    """

    name: ElementName
    area_in_m2: float
    u_value_in_watt_per_m2_per_kelvin: float
    adjustment_factor_used_by_hisim: float
    situation: Situation
    u_value_origin: str
    stated_correction_factor: Optional[float] = None


@dataclass(frozen=True)
class Window:
    """One window surface for the solar gains: a direction of HiSim's window areas.

    Attributes:
        name: The direction (``south``, ``east``, ``north``, ``west``, ``horizontal``).
        azimuth_in_degree: Azimuth from south, east negative.
        tilt_in_degree: Tilt from the horizontal.
        area_in_m2: HiSim's window area in that direction.
        g_value: Total solar energy transmittance at perpendicular radiation.
        frame_factor: HiSim's reduction factor for the frame area fraction, if stated.
        non_perpendicular_factor: HiSim's reduction factor for non-perpendicular radiation, if stated.
        external_shading_factor: HiSim's reduction factor for external vertical shading, if stated.
        glass_area_in_m2: The glass area of the surface, if stated (a certificate or a norm example states
            it; HiSim's record does not). Without it an engine takes its norm's default share.

    The three reduction factors may be ``None`` here although the expanded record always states them
    (input_contract §2.3): a description written by hand, such as a norm's worked example, has none, and
    an engine that uses them decides what it does without. The reader of the record stays strict.
    """

    name: str
    azimuth_in_degree: float
    tilt_in_degree: float
    area_in_m2: float
    g_value: float
    frame_factor: Optional[float]
    non_perpendicular_factor: Optional[float]
    external_shading_factor: Optional[float]
    glass_area_in_m2: Optional[float] = None


@dataclass(frozen=True)
class Site:
    """Where the building stands, as HiSim's weather configuration states it; read by the engines that need it.

    Attributes:
        climate_region: The climate region, if stated.
        elevation_in_m: The elevation above sea level, if stated.
    """

    climate_region: Optional[str]
    elevation_in_m: Optional[float]


@dataclass(frozen=True)
class Provenance:
    """Where the description came from, passed into the result unchanged and uninterpreted.

    Attributes:
        record_schema: The schema of the expanded record.
        producer: The program that wrote the record.
        hisim_building_code: HiSim's building code, opaque.
        configured_fields: The override fields of HiSim's building configuration that were set.
    """

    record_schema: str
    producer: str
    hisim_building_code: str
    configured_fields: Tuple[str, ...]


@dataclass(frozen=True)
class BuildingDescription:
    """One building as HiSim computed with it, checked on construction.

    Attributes:
        name: The name of the building in the record.
        building_type: The building type, or ``None`` where HiSim's code states none.
        conditioned_floor_area_in_m2: HiSim's reference area; each engine derives its own from it.
        gross_volume_in_m3: The gross volume, when the record states one.
        dwelling_units: HiSim's count of dwelling units.
        dwelling_units_configured: Whether the count was configured rather than estimated by HiSim.
        heat_capacity_class: HiSim's thermal mass class, mapped by each engine.
        elements: HiSim's envelope elements; the window element carries the transmission area.
        windows: The window surfaces per direction, which carry the solar gains.
        thermal_bridging_surcharge_used_by_hisim: HiSim's thermal-bridging surcharge, information only.
        site: Where the building stands.
        provenance: Where the description came from.

    Raises:
        EpcError: ``E-18`` for a value no building can have.
    """

    name: str
    building_type: Optional[BuildingType]
    conditioned_floor_area_in_m2: float
    gross_volume_in_m3: Optional[float]
    dwelling_units: int
    dwelling_units_configured: bool
    heat_capacity_class: str
    elements: Tuple[Element, ...]
    windows: Tuple[Window, ...]
    thermal_bridging_surcharge_used_by_hisim: float
    site: Site
    provenance: Provenance

    def __post_init__(self) -> None:
        """Refuses the values no building can have (I-9)."""
        checks: List[Tuple[str, Any, bool, str]] = [
            (
                "building_type",
                self.building_type,
                self.building_type in BUILDING_TYPES + (None,),
                f"one of {', '.join(BUILDING_TYPES)} or null",
            ),
            (
                "conditioned_floor_area_in_m2",
                self.conditioned_floor_area_in_m2,
                _at_least(self.conditioned_floor_area_in_m2, 0, inclusive=False),
                "a finite number > 0",
            ),
            (
                "gross_volume_in_m3",
                self.gross_volume_in_m3,
                self.gross_volume_in_m3 is None or _at_least(self.gross_volume_in_m3, 0, inclusive=False),
                "a finite number > 0 or null",
            ),
            ("dwelling_units", self.dwelling_units, self.dwelling_units >= 1, "at least 1"),
            (
                "windows",
                tuple(window.name for window in self.windows),
                len({window.name for window in self.windows}) == len(self.windows),
                "one window surface per name",
            ),
        ]
        for element in self.elements:
            checks += [
                ("elements.name", element.name, element.name in ELEMENT_NAMES, f"one of {', '.join(ELEMENT_NAMES)}"),
                (
                    f"elements.{element.name}.area_in_m2",
                    element.area_in_m2,
                    _at_least(element.area_in_m2, 0, inclusive=True),
                    "a finite number >= 0",
                ),
                (
                    f"elements.{element.name}.u_value_in_watt_per_m2_per_kelvin",
                    element.u_value_in_watt_per_m2_per_kelvin,
                    _at_least(element.u_value_in_watt_per_m2_per_kelvin, 0, inclusive=True),
                    "a finite number >= 0",
                ),
                (
                    "windows",
                    self.windows,
                    element.name != "window" or not element.area_in_m2 > 0 or bool(self.windows),
                    f"a window element of {element.area_in_m2} m² needs at least one window surface",
                ),
                (
                    f"elements.{element.name}.stated_correction_factor",
                    element.stated_correction_factor,
                    element.stated_correction_factor is None
                    or (element.situation is not Situation.CONDITIONED_NEIGHBOUR
                        and _at_least(element.stated_correction_factor, 0, inclusive=False)
                        and element.stated_correction_factor <= 1),
                    "null, or > 0 and <= 1 for an element of the envelope (not a conditioned neighbour)",
                ),
            ]
        for window in self.windows:
            checks += [
                (
                    f"windows.{window.name}.area_in_m2",
                    window.area_in_m2,
                    _at_least(window.area_in_m2, 0, inclusive=False),
                    "a finite number > 0",
                ),
                (
                    f"windows.{window.name}.g_value",
                    window.g_value,
                    _at_least(window.g_value, 0, inclusive=False) and window.g_value <= 1,
                    "a number in (0, 1]",
                ),
                (
                    f"windows.{window.name}.glass_area_in_m2",
                    window.glass_area_in_m2,
                    window.glass_area_in_m2 is None
                    or (_at_least(window.glass_area_in_m2, 0, inclusive=False)
                        and window.glass_area_in_m2 <= window.area_in_m2),
                    "a finite number > 0 and at most the window's area, or null",
                ),
            ]
        for field, value, admissible, rule in checks:
            if not admissible:
                raise EpcError(EpcErrorKind.VALUE_NOT_ADMISSIBLE, name=self.name, field=field, value=value, rule=rule)


def _at_least(value: float, bound: float, inclusive: bool) -> bool:
    """Whether a value is a finite number above the bound, or equal to it when ``inclusive``."""
    return math.isfinite(value) and (value >= bound if inclusive else value > bound)
