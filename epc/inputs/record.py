"""Step 1: ``building.expanded.yaml`` read into building descriptions, computing nothing about the building.

HiSim writes its building out beside every run's realized record, read off the object it computes
with (``roadmap/epc/input_contract.md``). This module is the only place that knows that file. It turns
one entry of it into a :class:`~epc.model.building.BuildingDescription` by four rules and nothing
else (design §3.3):

- **Everything is taken as written.** Every field of contract §2.3 is copied; a missing or
  non-finite one refuses by name (``E-05``). Nothing is averaged, split, scaled, rounded, defaulted
  or looked up, and the reader has no notion of where HiSim got a value.
- **Situation** (D25, design §7.2): factor 1 is outside air, 0 a conditioned neighbour, 0.5 the
  unconditioned space the element class names (attic for a roof, cellar for a floor, buffer space
  for a wall); any other pair refuses (``E-06``).
- **Windows**: one surface per direction with its azimuth from south and its tilt; a direction of
  area 0 is dropped; a window element with area but no direction with area refuses (``E-08``).
- **Dwelling units**: the record's count; 0 refuses naming HiSim's ``number_of_apartments`` (``E-07``).

The file's schema is checked before any field (``E-04``). The site fields may be absent or ``null``:
which of them a run needs is the engine's question (``E-09``), so a record of a country that reads no
site may carry an empty block. Unknown fields are ignored (contract §2.4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from epc.model.building import (
    ELEMENT_NAMES,
    BuildingDescription,
    Element,
    ElementName,
    Provenance,
    Site,
    Situation,
    Window,
)
from epc.model.errors import EpcError, EpcErrorKind

#: The file HiSim writes beside ``realized.energy_system.yaml``.
RECORD_FILENAME = "building.expanded.yaml"

#: The only format this reader understands.
RECORD_SCHEMA = "building-expanded/1"

#: Direction of ``windows_by_direction`` → (azimuth from south, east negative; tilt), in HiSim's order.
WINDOW_DIRECTIONS: Tuple[Tuple[str, float, float], ...] = (
    ("south", 0.0, 90.0),
    ("east", -90.0, 90.0),
    ("north", 180.0, 90.0),
    ("west", 90.0, 90.0),
    ("horizontal", 0.0, 0.0),
)

#: The fields of the ``glazing`` block, the same for every window surface and named as on :class:`Window`.
#: All four are required numbers, as the contract types them (§2.3), although :class:`Window` allows the
#: three reduction factors to be ``None`` for descriptions written by hand.
GLAZING_FIELDS: Tuple[str, ...] = ("g_value", "frame_factor", "non_perpendicular_factor", "external_shading_factor")

#: The unconditioned space behind an element of factor 0.5, by element class (D25).
UNCONDITIONED_SITUATIONS: Dict[str, Situation] = {
    "roof": Situation.UNCONDITIONED_ATTIC,
    "floor": Situation.UNCONDITIONED_CELLAR,
    "wall": Situation.UNCONDITIONED_SPACE,
}


@dataclass(frozen=True)
class RecordEntry:
    """One entry of the record's ``buildings`` list, not yet read.

    Attributes:
        name: The building's name in the record.
        values: The entry as the file holds it.
        schema: The file's schema.
        producer: The file's producer as the file holds it, checked when the entry is read.
    """

    name: str
    values: Mapping[str, Any]
    schema: str
    producer: Any


@dataclass(frozen=True)
class ExpandedRecord:
    """An expanded record whose schema is checked; its entries are read by :func:`describe`.

    Attributes:
        path: Path of the file.
        buildings: One entry per ``Building`` of the run, in the file's order.
    """

    path: Path
    buildings: Tuple[RecordEntry, ...]


def load(results_dir: Path) -> ExpandedRecord:
    """Opens the expanded record of one results directory and checks its schema.

    Args:
        results_dir: The run's results directory.

    Returns:
        The record with its building entries.

    Raises:
        EpcError: ``E-02`` without the file, ``E-04`` for another schema or a file that is not a
            YAML mapping, ``E-03`` without a building, ``E-05`` for an entry without a name.
    """
    path = results_dir / RECORD_FILENAME
    if not path.is_file():
        raise EpcError(EpcErrorKind.NO_EXPANDED_RECORD, dir=results_dir)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise EpcError(EpcErrorKind.SCHEMA_VERSION, path=path, found="not readable as YAML") from error
    if not isinstance(document, dict):
        raise EpcError(EpcErrorKind.SCHEMA_VERSION, path=path, found="not a YAML mapping")
    schema = document.get("schema")
    if schema != RECORD_SCHEMA:
        found = schema if isinstance(schema, str) else "a file without a 'schema'"
        raise EpcError(EpcErrorKind.SCHEMA_VERSION, path=path, found=found)
    buildings = document.get("buildings")
    if not isinstance(buildings, list) or not buildings:
        raise EpcError(EpcErrorKind.NO_BUILDING, path=path)
    entries: List[RecordEntry] = []
    for index, values in enumerate(buildings):
        name = values.get("name") if isinstance(values, dict) else None
        if not isinstance(name, str):
            raise EpcError(EpcErrorKind.MISSING_FIELD, name=f"buildings[{index}]", field="name")
        entries.append(RecordEntry(name=name, values=values, schema=schema, producer=document.get("producer")))
    return ExpandedRecord(path=path, buildings=tuple(entries))


def describe(entry: RecordEntry) -> BuildingDescription:
    """Reads one building entry into its description by the four rules of the module.

    Args:
        entry: One entry of a loaded record.

    Returns:
        The building as HiSim computed with it.

    Raises:
        EpcError: ``E-05`` for a missing or non-finite field, ``E-06`` for a factor without a
            situation, ``E-07`` for 0 dwelling units, ``E-08`` for windows without a direction,
            ``E-18`` for a value no building can have.
    """
    fields = _Fields(entry.name, entry.values)
    elements = tuple(_element(fields, element_name) for element_name in ELEMENT_NAMES)
    glazing = {key: fields.number(f"glazing.{key}") for key in GLAZING_FIELDS}
    windows: List[Window] = []
    for direction, azimuth, tilt in WINDOW_DIRECTIONS:
        area = fields.number(f"windows_by_direction.{direction}")
        if area != 0:
            windows.append(Window(direction, azimuth, tilt, area, **glazing))
    window_area = next(element.area_in_m2 for element in elements if element.name == "window")
    if window_area > 0 and not any(window.area_in_m2 > 0 for window in windows):
        raise EpcError(EpcErrorKind.WINDOWS_WITHOUT_DIRECTION, name=entry.name, area=window_area)
    dwelling_units = fields.integer("dwelling_units.value")
    if dwelling_units == 0:
        raise EpcError(EpcErrorKind.NO_DWELLING_UNITS, name=entry.name)
    return BuildingDescription(
        name=entry.name,
        building_type=fields.present("building_type"),
        conditioned_floor_area_in_m2=fields.number("conditioned_floor_area_in_m2"),
        gross_volume_in_m3=fields.optional_number("gross_volume_in_m3"),
        dwelling_units=dwelling_units,
        dwelling_units_configured=fields.boolean("dwelling_units.configured"),
        heat_capacity_class=fields.string("heat_capacity_class"),
        elements=elements,
        windows=tuple(windows),
        thermal_bridging_surcharge_used_by_hisim=fields.number("thermal_bridging_surcharge_in_watt_per_m2_per_kelvin"),
        site=_site(fields),
        provenance=Provenance(
            record_schema=entry.schema,
            producer=fields.checked("producer", entry.producer, isinstance(entry.producer, str)),
            hisim_building_code=fields.string("hisim_building_code"),
            configured_fields=fields.strings("configured_fields"),
        ),
    )


def situation(building: str, element: str, factor: float) -> Situation:
    """The situation of an element from HiSim's adjustment factor and the element class (D25, design §7.2).

    Args:
        building: The building's name, for the message.
        element: The element class.
        factor: HiSim's adjustment factor of the element.

    Returns:
        What the element borders on.

    Raises:
        EpcError: ``E-06`` for a pair the rule does not name.
    """
    if factor == 1:
        return Situation.OUTSIDE_AIR
    if factor == 0:
        return Situation.CONDITIONED_NEIGHBOUR
    if factor == 0.5 and element in UNCONDITIONED_SITUATIONS:
        return UNCONDITIONED_SITUATIONS[element]
    raise EpcError(EpcErrorKind.UNKNOWN_ADJUSTMENT_FACTOR, name=building, element=element, b=factor)


def _element(fields: _Fields, name: ElementName) -> Element:
    """Reads one element of ``elements`` with its situation."""
    factor = fields.number(f"elements.{name}.adjustment_factor")
    return Element(
        name=name,
        area_in_m2=fields.number(f"elements.{name}.area_in_m2"),
        u_value_in_watt_per_m2_per_kelvin=fields.number(f"elements.{name}.u_value_in_watt_per_m2_per_kelvin"),
        adjustment_factor_used_by_hisim=factor,
        situation=situation(fields.building, name, factor),
        u_value_origin=fields.string(f"elements.{name}.u_value_origin"),
    )


def _site(fields: _Fields) -> Site:
    """Reads the site block, whose fields may be absent or ``null``; the engine decides what it needs."""
    block = fields.present("site")
    fields.checked("site", block, isinstance(block, Mapping))
    climate_region = block.get("climate_region")
    fields.checked("site.climate_region", climate_region, climate_region is None or isinstance(climate_region, str))
    return Site(climate_region=climate_region, elevation_in_m=fields.optional_number("site.elevation_in_m", True))


class _Fields:
    """Reads the fields of one building entry by their dotted path, refusing a missing one with ``E-05``."""

    _MISSING = object()

    def __init__(self, building: str, values: Mapping[str, Any]) -> None:
        """Wraps one entry; ``building`` names it in the messages."""
        self.building = building
        self._values = values

    def present(self, path: str, absent: bool = False) -> Any:
        """The value at a dotted path; a missing one refuses, naming the first missing level, unless ``absent``."""
        value: Any = self._values
        keys = path.split(".")
        for depth, key in enumerate(keys, start=1):
            value = value.get(key, self._MISSING) if isinstance(value, Mapping) else self._MISSING
            if value is self._MISSING:
                if absent:
                    return None
                raise EpcError(EpcErrorKind.MISSING_FIELD, name=self.building, field=".".join(keys[:depth]))
        return value

    def checked(self, path: str, value: Any, admissible: bool) -> Any:
        """The value when it has the field's type; otherwise the field refuses with ``E-05``."""
        if not admissible:
            raise EpcError(EpcErrorKind.MISSING_FIELD, name=self.building, field=path)
        return value

    def number(self, path: str) -> float:
        """A finite number."""
        value = self.optional_number(path)
        return float(self.checked(path, value, value is not None))

    def optional_number(self, path: str, absent: bool = False) -> Optional[float]:
        """A finite number or ``null``; a bool is no number."""
        value = self.present(path, absent)
        if value is None:
            return None
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return float(self.checked(path, value, number and math.isfinite(value)))

    def integer(self, path: str) -> int:
        """An integer; a bool is no integer."""
        value = self.present(path)
        return int(self.checked(path, value, isinstance(value, int) and not isinstance(value, bool)))

    def boolean(self, path: str) -> bool:
        """A boolean."""
        value = self.present(path)
        return bool(self.checked(path, value, isinstance(value, bool)))

    def string(self, path: str) -> str:
        """A string."""
        value = self.present(path)
        return str(self.checked(path, value, isinstance(value, str)))

    def strings(self, path: str) -> Tuple[str, ...]:
        """A list of strings, as a tuple."""
        value = self.present(path)
        return tuple(self.checked(path, value, isinstance(value, list) and all(isinstance(v, str) for v in value)))
