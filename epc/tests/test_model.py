"""T-1: the building description is immutable and refuses on construction what no building can have (I-9)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from epc.model.building import BuildingDescription, Element, Provenance, Site, Situation, Window
from epc.model.errors import EpcError

pytestmark = pytest.mark.base


def _window(**changes: Any) -> Window:
    """A window surface with placeholder values."""
    window = Window("south", 0.0, 90.0, 4.0, 0.5, None, None, None)
    return dataclasses.replace(window, **changes)


def _element(name: Any, **changes: Any) -> Element:
    """An element against outside air with placeholder values."""
    element = Element(name, 10.0, 1.0, 1.0, Situation.OUTSIDE_AIR, "placeholder")
    return dataclasses.replace(element, **changes)


def _description(**changes: Any) -> BuildingDescription:
    """A valid description with placeholder values, changed by keyword."""
    values: dict = {
        "name": "building",
        "building_type": None,
        "conditioned_floor_area_in_m2": 100.0,
        "gross_volume_in_m3": None,
        "dwelling_units": 1,
        "dwelling_units_configured": False,
        "heat_capacity_class": "medium",
        "elements": tuple(_element(name) for name in ("floor", "wall", "roof", "window", "door")),
        "windows": (_window(),),
        "thermal_bridging_surcharge_used_by_hisim": 0.0,
        "site": Site(None, None),
        "provenance": Provenance("schema", "producer", "code", ()),
    }
    values.update(changes)
    return BuildingDescription(**values)


def test_a_valid_description_is_accepted_and_immutable() -> None:
    """Placeholders pass; no field can be reassigned."""
    description = _description()
    with pytest.raises(dataclasses.FrozenInstanceError):
        description.dwelling_units = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"building_type": "detached"},
        {"gross_volume_in_m3": 300.0},
        {"elements": (_element("door", area_in_m2=0.0, u_value_in_watt_per_m2_per_kelvin=0.0),)},
        {"elements": (_element("window", area_in_m2=0.0),), "windows": ()},
        {"windows": (_window(g_value=1.0),)},
    ],
    ids=["building type", "gross volume", "door without area", "no windows at all", "g of 1"],
)
def test_admissible_values_are_accepted(changes: dict) -> None:
    """A stated building type, a volume, an element of area 0 and a g-value of 1 are values a building can have."""
    _description(**changes)


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"building_type": "castle"}, "building_type"),
        ({"conditioned_floor_area_in_m2": 0.0}, "conditioned_floor_area_in_m2"),
        ({"conditioned_floor_area_in_m2": float("nan")}, "conditioned_floor_area_in_m2"),
        ({"gross_volume_in_m3": 0.0}, "gross_volume_in_m3"),
        ({"dwelling_units": 0}, "dwelling_units"),
        ({"elements": (_element("chimney"),)}, "elements.name"),
        ({"elements": (_element("wall", area_in_m2=-1.0),)}, "elements.wall.area_in_m2"),
        ({"elements": (_element("wall", area_in_m2=float("inf")),)}, "elements.wall.area_in_m2"),
        (
            {"elements": (_element("roof", u_value_in_watt_per_m2_per_kelvin=-0.1),)},
            "elements.roof.u_value_in_watt_per_m2_per_kelvin",
        ),
        ({"windows": ()}, "windows"),
        ({"windows": (_window(), _window())}, "windows"),
        ({"windows": (_window(area_in_m2=0.0),)}, "windows.south.area_in_m2"),
        ({"windows": (_window(g_value=0.0),)}, "windows.south.g_value"),
        ({"windows": (_window(g_value=1.01),)}, "windows.south.g_value"),
    ],
)
def test_inadmissible_values_are_refused(changes: dict, field: str) -> None:
    """Each value no building can have refuses with E-18 naming the field."""
    with pytest.raises(EpcError) as refusal:
        _description(**changes)
    assert refusal.value.error_id == "E-18"
    assert f"'{field}'" in refusal.value.message
