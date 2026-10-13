"""T-5: the terms of the Austrian engine, each against a hand computation with hand-chosen values.

DISCLAIMER - SYNTHETIC TEST DATA. Every table value in this module is invented to test the code,
and any building in it is a placeholder or a HiSim test record: no value comes from an OENORM
table, and no number computed here is the heating demand of any real building. Never use them for
a study or a real calculation. The standards themselves (OENORM B 8110-5, B 8110-6-1, B 8110-6-2,
H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards International:
https://www.austrian-standards.at

The tables come from :mod:`epc.tests.at.synthetic.at_tables`; no value of a licensed table appears
here (R8.5).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from epc.engines.at import climate, defaults, usage
from epc.engines.at.data_tables import AustrianTables, load_tables
from epc.inputs import record
from epc.model.building import BuildingDescription, Element, Situation, Window
from epc.model.errors import EpcError
from epc.tests import support
from epc.tests.at.synthetic.at_tables import f_t, synthetic_tables, write

pytestmark = pytest.mark.base


def _building(**changes: Any) -> BuildingDescription:
    """The configured fixture building, changed by keyword."""
    loaded = record.load(support.FIXTURES / support.CONFIGURED)
    return dataclasses.replace(record.describe(loaded.buildings[0]), **changes)


def test_the_gross_floor_area_is_the_conditioned_floor_area_over_0_8() -> None:
    """BGF = BF / 0.8 with HiSim's conditioned floor area as BF, always an assumption (Section 4)."""
    result = defaults.gross_floor_area(_building(conditioned_floor_area_in_m2=100.0))
    assert result.value == 125.0
    assert result.assumption is not None and result.assumption.key == "gross_floor_area"
    assert "Section 4" in result.assumption.source


def test_the_gross_volume_is_the_records_when_it_states_one() -> None:
    """A stated volume is taken as it is, without an assumption."""
    result = defaults.gross_volume(_building(gross_volume_in_m3=400.0), 125.0)
    assert result == defaults.Defaulted(400.0, None)


def test_without_a_gross_volume_the_storey_height_is_3_m_and_said_so() -> None:
    """V = BGF x 3.00 m, recorded as the engine's own assumption that borrows Eq. (65)."""
    result = defaults.gross_volume(_building(gross_volume_in_m3=None), 125.0)
    assert result.value == 375.0
    assert result.assumption is not None and result.assumption.key == "gross_volume"
    assert "Eq. (65)" in result.assumption.source and "non-residential" in result.assumption.source


def test_thermal_bridges_and_ventilation_are_stated_defaults() -> None:
    """Eq. (11a) and natural ventilation are recorded with their clauses."""
    assert "Eq. (11a)" in defaults.thermal_bridges().value
    assert "5.3.2 c)" in defaults.thermal_bridges().source
    assert "n_L,hyg" in defaults.ventilation().value
    assert "6.2.2" in defaults.ventilation().source


@pytest.mark.parametrize(
    "name, factor, situation, row, assumed",
    [
        ("wall", 1.0, Situation.OUTSIDE_AIR, "OUTSIDE_AIR", False),
        ("window", 1.0, Situation.OUTSIDE_AIR, "OUTSIDE_AIR", False),
        ("wall", 0.0, Situation.CONDITIONED_NEIGHBOUR, None, False),
        ("roof", 0.5, Situation.UNCONDITIONED_ATTIC, "CEILING_TO_UNCONDITIONED_ATTIC", True),
        ("floor", 0.5, Situation.UNCONDITIONED_CELLAR, "CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED", True),
        ("wall", 0.5, Situation.UNCONDITIONED_SPACE, "WALL_TO_BUFFER_SPACE", True),
    ],
)
def test_each_situation_takes_its_row_of_tab_3_to_5(
    name: str, factor: float, situation: Situation, row: str, assumed: bool
) -> None:
    """Outside air is Tab. 3, an unconditioned space a Tab. 4 row as an assumption, a neighbour no row (D29)."""
    element = Element(name, 10.0, 1.0, factor, situation, "placeholder")  # type: ignore[arg-type]
    taken, assumption = defaults.correction_factor_row(element)
    assert taken == row
    assert (assumption is not None) == assumed
    if assumption is not None:
        assert assumption.key == f"correction_factor_{name}" and "Tab. 4" in assumption.source


def test_the_cellar_is_uninsulated_because_its_walls_and_floor_are_unknown() -> None:
    """Tab. 4 footnote a judges the cellar's walls and floor, which the description does not state."""
    element = Element("floor", 10.0, 0.1, 0.5, Situation.UNCONDITIONED_CELLAR, "placeholder")
    taken, assumption = defaults.correction_factor_row(element)
    assert taken == "CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED"
    assert assumption is not None and "footnote a" in assumption.value


@pytest.mark.parametrize("units, label", [(1, "1-1"), (2, "2-5"), (5, "2-5"), (6, "6+"), (60, "6+")])
def test_the_usage_profile_follows_the_dwelling_units(tmp_path: Path, units: int, label: str) -> None:
    """The class of the dwelling units gives θ_i,h, n_L,hyg and q_i,h of its row (B 8110-5 Section 8)."""
    tables = load_tables(write(tmp_path, synthetic_tables()))
    profile = usage.usage(tables, _building(dwelling_units=units))
    row = next(row for row in tables.usage_profiles if row.label == label)
    assert profile == usage.Usage(label, row.theta_i_h_c, row.n_l_hyg_per_h, row.q_i_h_w_per_m2)


def test_the_month_durations_are_the_calendar_months() -> None:
    """The duration t of Eq. (29a) and (37) is the hours of each month, February with 28 days."""
    assert usage.MONTH_DURATION_IN_H[0] == 744.0
    assert usage.MONTH_DURATION_IN_H[1] == 672.0
    assert len(usage.MONTH_DURATION_IN_H) == 12
    assert sum(usage.MONTH_DURATION_IN_H) == 8760.0


#: The window surfaces of every direction, as the reader makes them (azimuth from south, east negative).
WINDOWS = tuple(
    Window(name, azimuth, tilt, 1.0, 0.5, None, None, None)
    for name, azimuth, tilt in (
        ("south", 0.0, 90.0), ("east", -90.0, 90.0), ("north", 180.0, 90.0), ("west", 90.0, 90.0),
        ("horizontal", 0.0, 0.0),
    )
)


@pytest.fixture(name="tables")
def fixture_tables(tmp_path: Path) -> AustrianTables:
    """The synthetic tables, loaded."""
    return load_tables(write(tmp_path, synthetic_tables()))


def test_the_reference_climate_is_annex_a_as_printed(tables: AustrianTables) -> None:
    """θ_e,j of Tab. A.1; a vertical surface by its signed azimuth, the horizontal one from Tab. A.1."""
    result = climate.reference_climate(tables, WINDOWS)
    assert result.outdoor_temperatures_in_c == tuple(float(month - 0.5) for month in range(1, 13))
    for window in WINDOWS[:4]:
        expected = tuple(float(90 + window.azimuth_in_degree / 100 + month) for month in range(1, 13))
        assert result.radiation_in_kwh_per_m2[window.name] == expected, window.name
    assert result.radiation_in_kwh_per_m2["horizontal"] == tuple(10.0 * month for month in range(1, 13))


@pytest.mark.parametrize("elevation, band_limit", [(150.0, "E7"), (15.0, "E2")])
def test_the_site_climate_follows_eq_1_to_3(tables: AustrianTables, elevation: float, band_limit: str) -> None:
    """θ_e,j = a + b h/100, I_S = a2 h² + a1 h + a0, I_S,ON,j = I_S F_T with |azimuth| and tilt of the surface."""
    assert band_limit in [band.band for band in tables.transposition_bands]
    result = climate.site_climate(tables, WINDOWS, "W", elevation)
    months = range(1, 13)
    assert result.outdoor_temperatures_in_c == tuple(month + (-0.01 * month) * elevation / 100 for month in months)
    horizontal = tuple(1e-06 * elevation**2 + (0.01 * month) * elevation + (20 + month) for month in months)
    assert climate.global_radiation(tables, "W", elevation) == horizontal
    steps = {"south": (0, 6), "east": (4, 6), "west": (4, 6), "north": (8, 6), "horizontal": (0, 0)}
    for name, (azimuth_step, tilt_step) in steps.items():
        expected = tuple(value * f_t(azimuth_step, tilt_step) for value in horizontal)
        assert result.radiation_in_kwh_per_m2[name] == expected, name


def test_an_azimuth_of_minus_180_is_north(tables: AustrianTables) -> None:
    """−180° and 180° are the same direction."""
    north = Window("north", -180.0, 90.0, 1.0, 0.5, None, None, None)
    assert climate.reference_climate(tables, (north,)) == climate.reference_climate(tables, WINDOWS[2:3])
    assert climate.site_climate(tables, (north,), "W", 150.0) == climate.site_climate(tables, WINDOWS[2:3], "W", 150.0)


@pytest.mark.parametrize("azimuth, tilt", [(10.0, 90.0), (0.0, 50.0), (float("nan"), 90.0), (0.0, float("inf"))])
def test_an_angle_off_the_grid_is_refused_not_interpolated(tables: AustrianTables, azimuth: float, tilt: float) -> None:
    """E-13 under both climates (D21); interpolation is deferred (P10)."""
    window = Window("odd", azimuth, tilt, 1.0, 0.5, None, None, None)
    for compute in (lambda: climate.reference_climate(tables, (window,)),
                    lambda: climate.site_climate(tables, (window,), "W", 150.0)):
        with pytest.raises(EpcError) as refusal:
            compute()
        assert refusal.value.error_id == "E-13"
        assert "Window 'odd'" in refusal.value.message
