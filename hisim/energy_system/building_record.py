"""The building as the run computes with it: ``building.expanded.yaml``, written beside the record.

The realized record states a Building's configuration only; the envelope the run actually computes
with — every area, U-value and adjustment factor, and where each U-value came from — lives only
inside :class:`~hisim.components.building.information.BuildingInformation`. A downstream tool that
needs exactly that building would otherwise have to rebuild it, and would get a different one the
moment its rebuild drifted from HiSim's. So HiSim writes it out itself.

Every number is read off the ``BuildingInformation`` as it is: nothing is averaged, scaled, rounded
or re-derived, and a value is only cast to a plain Python number so that no pandas or numpy scalar
reaches the YAML. A missing or non-finite value is refused rather than written.

This module only builds the text; :func:`~hisim.energy_system.executor.write_records` writes it.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, List, Optional

import yaml

from hisim.components.building.building import Building
from hisim.components.building.config import BuildingConfig
from hisim.components.building.information import BuildingInformation
from hisim.energy_system.metadata import RunMetadata

#: The file the expanded buildings are written to, beside ``realized.energy_system.yaml``.
BUILDING_RECORD_FILENAME = "building.expanded.yaml"

#: The format of the file, so a reader can refuse one it does not understand.
BUILDING_RECORD_SCHEMA = "building-expanded/1"

#: The typology segment of a TABULA code -> the building type the file states. A code whose segment
#: is not listed (``SFH-TH``, ``MFH-AB``, the district and national-variant typologies) still runs
#: and gets ``building_type: null``; refusing it is the reader's decision.
BUILDING_TYPES: Dict[str, str] = {
    "SFH": "detached",
    "TH": "terraced",
    "MFH": "multi_family",
    "AB": "apartment_block",
}

#: File key of each envelope element -> the ``BuildingInformation`` attribute holding its area.
ELEMENT_AREA_ATTRIBUTES: Dict[str, str] = {
    "floor": "floor_area_in_m2",
    "wall": "facade_area_in_m2",
    "roof": "roof_area_in_m2",
    "window": "window_area_in_m2",
    "door": "door_area_in_m2",
}


def _number(name: str, field_name: str, value: Any, cast: type = float) -> Any:
    """Casts one value read off the building to a plain Python number, refusing a bad one.

    Args:
        name: The Building component's name, for the message.
        field_name: The field of the file the value goes to, for the message.
        value: The value as the building holds it.
        cast: ``float`` or ``int``.

    Returns:
        The value as a plain Python number.

    Raises:
        ValueError: When the value is missing or not finite.
    """
    if value is None or not math.isfinite(float(value)):
        raise ValueError(f"Building '{name}': {field_name} is {value!r}, not a finite number.")
    return cast(value)


def _building_type(building_code: str) -> Optional[str]:
    """The building type of a TABULA code's typology segment, or ``None`` when it is not listed."""
    segments = building_code.split(".")
    return BUILDING_TYPES.get(segments[2]) if len(segments) > 2 else None


def _read(name: str, building_information: Any, attribute: str) -> Any:
    """Reads one attribute off the building, refusing a missing one with the building's name."""
    if not hasattr(building_information, attribute):
        raise ValueError(f"Building '{name}': BuildingInformation has no attribute '{attribute}'.")
    return getattr(building_information, attribute)


def expanded_building_block(name: str, building_information: Any, building_config: BuildingConfig) -> dict:
    """Builds the entry of one Building in ``building.expanded.yaml``.

    Pure: it reads the building and its configuration and touches nothing else.

    Args:
        name: The Building component's name in the record.
        building_information: The ``BuildingInformation`` the run computes with.
        building_config: The Building's configuration.

    Returns:
        The entry, in the file's key order.

    Raises:
        ValueError: When a value is missing or not finite.
    """
    code = building_config.building_code
    element_values = _read(name, building_information, "element_values")()
    elements: Dict[str, Dict[str, Any]] = {}
    for key, area_attribute in ELEMENT_AREA_ATTRIBUTES.items():
        values = element_values[key]
        area = _read(name, building_information, area_attribute)
        elements[key] = {
            "area_in_m2": _number(name, f"elements.{key}.area_in_m2", area),
            "u_value_in_watt_per_m2_per_kelvin": _number(
                name, f"elements.{key}.u_value_in_watt_per_m2_per_kelvin", values.u_value_in_watt_per_m2_per_kelvin
            ),
            "u_value_origin": str(values.u_value_origin),
            "adjustment_factor": _number(name, f"elements.{key}.adjustment_factor", values.adjustment_factor),
        }
    window_areas = _read(name, building_information, "scaled_window_areas_in_m2")
    glazing_attributes = {
        "g_value": "total_solar_energy_transmittance_for_perpedicular_radiation",
        "frame_factor": "reduction_factor_for_frame_area_fraction_of_window",
        "non_perpendicular_factor": "reduction_factor_for_non_perpedicular_radiation",
        "external_shading_factor": "reduction_factor_for_external_vertical_shading",
    }
    return {
        "name": name,
        "hisim_building_code": code,
        "building_type": _building_type(code),
        "conditioned_floor_area_in_m2": _number(
            name,
            "conditioned_floor_area_in_m2",
            _read(name, building_information, "scaled_conditioned_floor_area_in_m2"),
        ),
        "gross_volume_in_m3": None,
        "heat_capacity_class": str(_read(name, building_information, "building_heat_capacity_class")),
        "dwelling_units": {
            "value": _number(
                name, "dwelling_units.value", _read(name, building_information, "number_of_apartments"), int
            ),
            "configured": building_config.number_of_apartments is not None,
        },
        "elements": elements,
        "windows_by_direction": {
            direction.lower(): _number(name, f"windows_by_direction.{direction.lower()}", area)
            for direction, area in zip(BuildingInformation.WINDOWS_DIRECTIONS, window_areas)
        },
        "glazing": {
            key: _number(name, f"glazing.{key}", _read(name, building_information, attribute))
            for key, attribute in glazing_attributes.items()
        },
        "thermal_bridging_surcharge_in_watt_per_m2_per_kelvin": _number(
            name,
            "thermal_bridging_surcharge_in_watt_per_m2_per_kelvin",
            _read(name, building_information, "thermal_bridging_surcharge_in_watt_per_m2_per_kelvin"),
        ),
        "site": {"climate_region": None, "elevation_in_m": None},
        "configured_fields": [
            field.name
            for field in dataclasses.fields(building_config)
            if field.default is None and getattr(building_config, field.name) is not None
        ],
    }


def building_record_text(built: Any) -> str:
    """Returns the text of ``building.expanded.yaml``: one entry per Building of the built system.

    Every entry is built before the text is, so a building that cannot be stated yields no
    partial file. A system without a Building still gets the text, with no entries.

    Args:
        built: The finished build.

    Returns:
        The file's text, header comment included.

    Raises:
        ValueError: When a Building cannot be stated completely.
    """
    buildings: List[dict] = [
        expanded_building_block(
            wrapped.my_component.component_name,
            wrapped.my_component.my_building_information,
            wrapped.my_component.buildingconfig,
        )
        for wrapped in built.simulator.wrapped_components
        if isinstance(wrapped.my_component, Building)
    ]
    version = RunMetadata.hisim_version()
    document = {"schema": BUILDING_RECORD_SCHEMA, "producer": f"HiSim {version}", "buildings": buildings}
    body: str = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    return f"# {BUILDING_RECORD_FILENAME} — Generated by HiSim {version} — do not hand-edit\n" + body
