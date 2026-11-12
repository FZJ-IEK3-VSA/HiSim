"""The case files load, inherit as the loader promises, and agree with the suite file."""

import pathlib
from typing import Dict

import pytest

from hisim.verification.case_loader import load_cases, merge, read_case_files, resolve
from hisim.verification.schema import Case, CaseFileError
from hisim.verification.suite import Suite

pytestmark = pytest.mark.base

#: The cases of phase 1 (spec section 2.1).
CASE_IDS = frozenset({"600", "620", "640", "660", "670", "680", "685", "695", "900", "920", "940", "980", "985", "995", "600FF", "680FF", "900FF", "980FF"})


def test_every_case_of_phase_one_loads(cases: Dict[str, Case]) -> None:
    """Eighteen files, eighteen resolved cases, every one naming the standard."""
    assert set(cases) == CASE_IDS
    assert all(case.standard.startswith("ANSI/ASHRAE Standard 140") for case in cases.values())


def test_620_moves_the_window_to_east_and_west(cases: Dict[str, Case]) -> None:
    """Six square metres east and west, no south window, the walls' opaque areas follow."""
    case = cases["620"]
    assert {(window.name, window.area_in_m2, window.azimuth_in_degrees) for window in case.windows} == {("east", 6.0, 90.0), ("west", 6.0, 270.0)}
    areas = {surface.name: surface.area_in_m2 for surface in case.surfaces}
    assert areas["wall_south"] == 21.6
    assert areas["wall_east"] == areas["wall_west"] == pytest.approx(10.2)
    assert case.constructions == cases["600"].constructions


def test_640_adds_the_setback_and_keeps_everything_else(cases: Dict[str, Case]) -> None:
    """The setback is the only difference to 600: 10 degC from 23:00, back at 07:00, ramp to 08:00."""
    case = cases["640"]
    assert case.thermostat is not None and case.thermostat.setback is not None
    setback = case.thermostat.setback
    assert (setback.setpoint_in_celsius, setback.from_hour, setback.to_hour, setback.ramp_to_hour) == (10.0, 23, 7, 8)
    assert case.thermostat.heating_setpoint_in_celsius == 20.0
    assert case.surfaces == cases["600"].surfaces
    assert case.windows == cases["600"].windows


def test_900_replaces_the_walls_and_the_floor_and_keeps_the_roof(cases: Dict[str, Case]) -> None:
    """The high-mass case has the roof of the low-mass case (7.2.2.2.1)."""
    case = cases["900"]
    by_kind = {surface.kind: surface.construction for surface in case.surfaces}
    assert by_kind == {"wall": "high_mass_wall", "roof": "low_mass_roof", "floor": "high_mass_floor"}
    assert case.constructions["low_mass_roof"] == cases["600"].constructions["low_mass_roof"]
    assert case.constructions["high_mass_wall"].layers[0].name == "concrete block"


def test_the_free_float_cases_have_no_thermostat(cases: Dict[str, Case]) -> None:
    """A free-float case is its base case without a thermostat."""
    for base in ("600", "680", "900", "980"):
        free_float = cases[f"{base}FF"]
        assert free_float.free_float and free_float.thermostat is None
        assert free_float.surfaces == cases[base].surfaces
        assert free_float.windows == cases[base].windows


def test_the_twenty_twenty_cases_cool_at_the_heating_set_point(cases: Dict[str, Case]) -> None:
    """685, 695, 985 and 995 heat below and cool above 20 degC."""
    for case_id in ("685", "695", "985", "995"):
        thermostat = cases[case_id].thermostat
        assert thermostat is not None
        assert (thermostat.heating_setpoint_in_celsius, thermostat.cooling_setpoint_in_celsius) == (20.0, 20.0)


def test_merge_lays_mappings_over_each_other_and_replaces_everything_else() -> None:
    """A nested key is overridden in place; a list, a scalar or null replaces the parent's value."""
    parent = {"a": {"x": 1, "y": {"z": 2}}, "items": [1, 2], "t": {"k": 1}}
    child = {"a": {"y": {"z": 3}, "w": 4}, "items": [9], "t": None}

    assert merge(parent, child) == {"a": {"x": 1, "y": {"z": 3}, "w": 4}, "items": [9], "t": None}
    assert parent == {"a": {"x": 1, "y": {"z": 2}}, "items": [1, 2], "t": {"k": 1}}


def test_a_cycle_and_an_unknown_parent_are_refused_by_name(tmp_path: pathlib.Path) -> None:
    """The loader names the cycle and the missing parent."""
    (tmp_path / "a.yaml").write_text("case: 'A'\nderives_from: 'B'\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("case: 'B'\nderives_from: 'A'\n", encoding="utf-8")
    (tmp_path / "c.yaml").write_text("case: 'C'\nderives_from: 'nowhere'\n", encoding="utf-8")
    raw = read_case_files(tmp_path)

    with pytest.raises(CaseFileError, match="cycle"):
        resolve(raw, "A")
    with pytest.raises(CaseFileError, match="no such case file"):
        resolve(raw, "C")


def test_a_resolved_case_names_no_parent(case_directory: pathlib.Path) -> None:
    """The resolved mapping is complete in itself."""
    resolved = resolve(read_case_files(case_directory), "995")

    assert "derives_from" not in resolved
    assert resolved["case"] == "995"
    assert resolved["constructions"]["high_mass_insulated_wall"]["layers"][1]["thickness_in_m"] == 0.2452


def test_a_file_that_is_not_a_case_is_refused(tmp_path: pathlib.Path) -> None:
    """A YAML without a 'case' key is not a case file."""
    (tmp_path / "x.yaml").write_text("title: no case here\n", encoding="utf-8")

    with pytest.raises(CaseFileError, match="not a case file"):
        load_cases(tmp_path)


def test_the_suite_file_names_the_cases_and_the_ranges(cases: Dict[str, Case]) -> None:
    """Every case of the suite file is a case file; a range's cases are case files unless it is not modelled."""
    suite = Suite.load("ashrae140")

    assert set(suite.cases) == set(cases)
    for range_case in suite.range_cases:
        needed = {range_case.case} if range_case.parent is None else {range_case.case, range_case.parent}
        assert needed <= set(cases) or range_case.not_modelled, range_case
