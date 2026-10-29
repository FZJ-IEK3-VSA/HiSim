"""T-3, T-4: the expanded record read into descriptions that equal it, its refusals, and the situation rule.

Every expected value is read from the fixture file itself, never from the function under test
(I-5); every refusal case is a fixture changed in memory and written to a temporary directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from epc.inputs import record
from epc.model.building import BuildingDescription, Situation
from epc.model.errors import EpcError
from epc.tests import support

pytestmark = pytest.mark.base


def _read(results_dir: Path) -> BuildingDescription:
    """The description of the only building of a results directory."""
    loaded = record.load(results_dir)
    assert len(loaded.buildings) == 1
    return record.describe(loaded.buildings[0])


def _changed(tmp_path: Path, change: Callable[[Dict[str, Any]], None], name: str = support.CONFIGURED) -> Path:
    """A fixture record with its first building changed in memory, written to a temporary results directory."""
    document = support.fixture_document(name)
    change(support.first_building(document))
    return support.write_record(tmp_path / "run", document)


def _refusal(results_dir: Path) -> EpcError:
    """The refusal of reading a results directory."""
    with pytest.raises(EpcError) as refusal:
        _read(results_dir)
    return refusal.value


@pytest.mark.parametrize("name", support.FIXTURE_NAMES)
def test_a_fixture_reads_into_a_description_that_equals_it(name: str) -> None:
    """Every field of the description is the record's value, nothing computed (I-5, AC10)."""
    document = support.fixture_document(name)
    expected = support.first_building(document)
    description = _read(support.FIXTURES / name)
    assert description.name == expected["name"]
    assert description.building_type == expected["building_type"]
    assert description.conditioned_floor_area_in_m2 == expected["conditioned_floor_area_in_m2"]
    assert description.gross_volume_in_m3 == expected["gross_volume_in_m3"]
    assert description.heat_capacity_class == expected["heat_capacity_class"]
    assert description.dwelling_units == expected["dwelling_units"]["value"]
    assert description.dwelling_units_configured == expected["dwelling_units"]["configured"]
    assert description.thermal_bridging_surcharge_used_by_hisim == (
        expected["thermal_bridging_surcharge_in_watt_per_m2_per_kelvin"]
    )
    assert [element.name for element in description.elements] == list(expected["elements"])
    for element in description.elements:
        stated = expected["elements"][element.name]
        assert element.area_in_m2 == stated["area_in_m2"]
        assert element.u_value_in_watt_per_m2_per_kelvin == stated["u_value_in_watt_per_m2_per_kelvin"]
        assert element.adjustment_factor_used_by_hisim == stated["adjustment_factor"]
        assert element.u_value_origin == stated["u_value_origin"]
    stated_windows = {direction: area for direction, area in expected["windows_by_direction"].items() if area != 0}
    assert {window.name: window.area_in_m2 for window in description.windows} == stated_windows
    for window in description.windows:
        assert window.g_value == expected["glazing"]["g_value"]
        assert window.frame_factor == expected["glazing"]["frame_factor"]
        assert window.non_perpendicular_factor == expected["glazing"]["non_perpendicular_factor"]
        assert window.external_shading_factor == expected["glazing"]["external_shading_factor"]
    assert description.site.climate_region == expected["site"]["climate_region"]
    assert description.site.elevation_in_m == expected["site"]["elevation_in_m"]
    assert description.provenance.record_schema == document["schema"]
    assert description.provenance.producer == document["producer"]
    assert description.provenance.hisim_building_code == expected["hisim_building_code"]
    assert list(description.provenance.configured_fields) == expected["configured_fields"]


def test_windows_are_one_surface_per_direction_with_an_area() -> None:
    """Directions of area 0 are dropped; azimuth from south, east negative; vertical except horizontal."""
    description = _read(support.FIXTURES / support.CONFIGURED)
    angles = {window.name: (window.azimuth_in_degree, window.tilt_in_degree) for window in description.windows}
    assert angles == {"south": (0.0, 90.0), "east": (-90.0, 90.0), "north": (180.0, 90.0), "west": (90.0, 90.0)}


def test_a_horizontal_window_lies_flat(tmp_path: Path) -> None:
    """A horizontal window area becomes a surface of tilt 0."""
    description = _read(_changed(tmp_path, lambda building: building["windows_by_direction"].update(horizontal=1.0)))
    horizontal = [window for window in description.windows if window.name == "horizontal"]
    assert [(window.azimuth_in_degree, window.tilt_in_degree) for window in horizontal] == [(0.0, 0.0)]


def test_situations_follow_from_the_factor_and_the_class() -> None:
    """The fixture's floor of factor 0.5 borders on an unconditioned cellar, the rest on outside air."""
    description = _read(support.FIXTURES / support.FLOOR_FACTOR_HALF)
    situations = {element.name: element.situation for element in description.elements}
    assert situations.pop("floor") == Situation.UNCONDITIONED_CELLAR
    assert set(situations.values()) == {Situation.OUTSIDE_AIR}


@pytest.mark.parametrize(
    "change",
    [
        lambda building: building.update(building_type=None),
        lambda building: building["site"].clear(),
        lambda building: building["site"].update(climate_region="S/SO", elevation_in_m=356),
        lambda building: building["elements"]["door"].update(area_in_m2=0.0, u_value_in_watt_per_m2_per_kelvin=0.0),
        lambda building: building.update(an_unknown_field=1),
    ],
    ids=["building type null", "empty site block", "site stated", "door without area", "unknown field"],
)
def test_what_the_contract_allows_is_read(tmp_path: Path, change: Callable[[Dict[str, Any]], None]) -> None:
    """A null building type, an empty or a stated site, an element of area 0 and an unknown field are read."""
    _read(_changed(tmp_path, change))


def test_a_factor_without_a_situation_is_refused(tmp_path: Path) -> None:
    """Factor 0.7 refuses with E-06 naming the element and the factor."""
    error = _refusal(_changed(tmp_path, lambda building: building["elements"]["wall"].update(adjustment_factor=0.7)))
    assert error.error_id == "E-06"
    assert "'wall'" in error.message and "0.7" in error.message


def test_zero_dwelling_units_are_refused(tmp_path: Path) -> None:
    """0 dwelling units refuse with E-07 naming HiSim's number_of_apartments."""
    error = _refusal(_changed(tmp_path, lambda building: building["dwelling_units"].update(value=0)))
    assert error.error_id == "E-07"
    assert "number_of_apartments" in error.message


def test_a_window_area_without_direction_is_refused(tmp_path: Path) -> None:
    """A window element with area and every direction 0 refuses with E-08."""

    def no_direction(building: Dict[str, Any]) -> None:
        building["windows_by_direction"] = {direction: 0.0 for direction in building["windows_by_direction"]}

    window = support.first_building(support.fixture_document(support.CONFIGURED))["elements"]["window"]
    window_area = window["area_in_m2"]
    error = _refusal(_changed(tmp_path, no_direction))
    assert error.error_id == "E-08"
    assert f"{window_area} m²" in error.message


@pytest.mark.parametrize(
    "field",
    [
        "conditioned_floor_area_in_m2",
        "heat_capacity_class",
        "elements.wall.u_value_in_watt_per_m2_per_kelvin",
        "elements.door",
        "windows_by_direction.west",
        "glazing.g_value",
        "dwelling_units.configured",
        "configured_fields",
        "site",
    ],
)
def test_a_missing_field_is_refused_by_name(tmp_path: Path, field: str) -> None:
    """Removing a field of contract §2.3 refuses with E-05 naming it."""

    def remove(building: Dict[str, Any]) -> None:
        *parents, last = field.split(".")
        for key in parents:
            building = building[key]
        del building[last]

    error = _refusal(_changed(tmp_path, remove))
    assert error.error_id == "E-05"
    assert f"'{field}'" in error.message


def test_a_non_finite_value_is_refused(tmp_path: Path) -> None:
    """A NaN area refuses with E-05 naming the field."""
    error = _refusal(_changed(tmp_path, lambda building: building["elements"]["roof"].update(area_in_m2=float("nan"))))
    assert error.error_id == "E-05"
    assert "'elements.roof.area_in_m2'" in error.message


def test_another_schema_is_refused_before_any_field(tmp_path: Path) -> None:
    """building-expanded/2 refuses with E-04 naming the file and the version found."""
    document = support.fixture_document(support.CONFIGURED)
    document["schema"] = "building-expanded/2"
    del support.first_building(document)["elements"]
    error = _refusal(support.write_record(tmp_path / "run", document))
    assert error.error_id == "E-04"
    assert "building-expanded/2" in error.message and record.RECORD_FILENAME in error.message


def test_a_directory_without_the_record_is_refused(tmp_path: Path) -> None:
    """No building.expanded.yaml refuses with E-02 naming the directory."""
    error = _refusal(tmp_path)
    assert error.error_id == "E-02"
    assert str(tmp_path) in error.message


def test_a_record_without_a_building_is_refused(tmp_path: Path) -> None:
    """An empty buildings list refuses with E-03."""
    document = support.fixture_document(support.CONFIGURED)
    document["buildings"] = []
    with pytest.raises(EpcError) as refusal:
        record.load(support.write_record(tmp_path / "run", document))
    assert refusal.value.error_id == "E-03"


@pytest.mark.parametrize("element", ["floor", "wall", "roof", "window", "door"])
@pytest.mark.parametrize(
    "factor, situations",
    [
        (1.0, {}),
        (0.0, {}),
        (
            0.5,
            {
                "roof": Situation.UNCONDITIONED_ATTIC,
                "floor": Situation.UNCONDITIONED_CELLAR,
                "wall": Situation.UNCONDITIONED_SPACE,
            },
        ),
        (0.7, {}),
    ],
)
def test_the_situation_rule(element: str, factor: float, situations: Dict[str, Situation]) -> None:
    """T-4: 1 outside air and 0 conditioned neighbour for every class, 0.5 by class, anything else E-06 (D25)."""
    expected = {1.0: Situation.OUTSIDE_AIR, 0.0: Situation.CONDITIONED_NEIGHBOUR}.get(factor, situations.get(element))
    if expected is None:
        with pytest.raises(EpcError) as refusal:
            record.situation("building", element, factor)
        assert refusal.value.error_id == "E-06"
    else:
        assert record.situation("building", element, factor) == expected
