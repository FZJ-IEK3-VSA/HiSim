"""T-5: the conductances and the gains of the Austrian engine, each against a hand computation.

The building is written by hand with placeholder values; the tables are synthetic
(:mod:`epc.tests.at_tables`); the constants of the expectations are the prose constants of the norm.
No value of a licensed table appears here (R8.5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pytest

from epc.engines.at import climate, defaults, envelope, gains, usage
from epc.engines.at.data_tables import AustrianTables, load_tables
from epc.model.building import BuildingDescription, Element, ElementName, Provenance, Site, Situation, Window
from epc.tests.at_tables import f_h, synthetic_tables, write

pytestmark = pytest.mark.base

#: (name, area, U, factor, situation, Tab. 3–5 row) of the hand-written building.
ELEMENTS: Tuple[Tuple[ElementName, float, float, float, Situation, Optional[str]], ...] = (
    ("floor", 50.0, 0.4, 0.5, Situation.UNCONDITIONED_CELLAR, "CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED"),
    ("wall", 120.0, 0.3, 1.0, Situation.OUTSIDE_AIR, "OUTSIDE_AIR"),
    ("wall", 30.0, 0.9, 0.0, Situation.CONDITIONED_NEIGHBOUR, None),
    ("roof", 50.0, 0.2, 0.5, Situation.UNCONDITIONED_ATTIC, "CEILING_TO_UNCONDITIONED_ATTIC"),
    ("window", 20.0, 1.2, 1.0, Situation.OUTSIDE_AIR, "OUTSIDE_AIR"),
    ("door", 2.0, 1.5, 1.0, Situation.OUTSIDE_AIR, "OUTSIDE_AIR"),
)
WINDOWS = (
    Window("south", 0.0, 90.0, 12.0, 0.6, None, None, None),
    Window("north", 180.0, 90.0, 8.0, 0.5, None, None, None),
)


def _building(dwelling_units: int = 1, building_type: str | None = "detached") -> BuildingDescription:
    """The hand-written building."""
    return BuildingDescription(
        name="hand-written",
        building_type=building_type,  # type: ignore[arg-type]
        conditioned_floor_area_in_m2=100.0,
        gross_volume_in_m3=None,
        dwelling_units=dwelling_units,
        dwelling_units_configured=True,
        heat_capacity_class="medium",
        elements=tuple(Element(name, area, u, b, situation, "hand") for name, area, u, b, situation, _ in ELEMENTS),
        windows=WINDOWS,
        thermal_bridging_surcharge_used_by_hisim=0.0,
        site=Site(None, None),
        provenance=Provenance("hand", "hand", "hand", ()),
    )


@pytest.fixture(name="tables")
def fixture_tables(tmp_path: Path) -> AustrianTables:
    """The synthetic tables, loaded."""
    return load_tables(write(tmp_path, synthetic_tables()))


def test_the_envelope_follows_eq_10a_11a_13_to_15_63a_and_1(tables: AustrianTables) -> None:
    """Σ f A U over the envelope, Eq. (11a), L_T, A, U_m, ℓ_c and L_V, recomputed by hand."""
    profile = usage.usage(tables, _building())
    result = envelope.envelope(tables, _building(), profile, 125.0, 375.0)
    in_envelope = [(area, u, f_h(row)) for _, area, u, _, _, row in ELEMENTS if row is not None]
    element_conductance = sum(factor * area * u for area, u, factor in in_envelope)
    area = sum(area for area, _, _ in in_envelope)
    bridges = max(0.2 * (0.75 - element_conductance / area) * element_conductance, 0.1 * element_conductance)
    assert result.element_conductance_in_w_per_k == element_conductance
    assert result.envelope_area_in_m2 == area == 242.0
    assert result.thermal_bridge_conductance_in_w_per_k == bridges
    assert result.transmission_conductance_in_w_per_k == element_conductance + bridges
    assert result.mean_u_value_in_w_per_m2_per_k == (element_conductance + bridges) / area
    assert result.characteristic_length_in_m == 375.0 / area
    assert result.air_volume_in_m3 == 0.8 * 125.0 * 2.6
    assert result.air_change_per_h == profile.hygienic_air_change_per_h
    assert result.ventilation_conductance_in_w_per_k == 0.34 * profile.hygienic_air_change_per_h * (0.8 * 125.0 * 2.6)
    assert [element.row for element in result.elements] == [row for *_, row in ELEMENTS]


def test_a_conditioned_neighbour_is_in_neither_the_conductance_nor_the_area(tables: AustrianTables) -> None:
    """I-10: the factor-0 wall adds nothing, its area is not part of A (Section 4, p. 8)."""
    profile = usage.usage(tables, _building())
    result = envelope.envelope(tables, _building(), profile, 125.0, 375.0)
    neighbour = result.elements[2]
    assert (neighbour.row, neighbour.factor, neighbour.conductance_in_w_per_k) == (None, None, 0.0)
    assert result.envelope_area_in_m2 == 242.0


def test_the_rows_taken_by_default_are_assumptions(tables: AustrianTables) -> None:
    """D29: the cellar and the attic rows are recorded; outside air and the neighbour are not defaults."""
    result = envelope.envelope(tables, _building(), usage.usage(tables, _building()), 125.0, 375.0)
    keys = [assumption.key for assumption in result.assumptions]
    assert keys == ["correction_factor_floor", "correction_factor_roof"]


@pytest.mark.parametrize(
    "element_conductance, area, expected",
    [(100.0, 500.0, 0.2 * (0.75 - 0.2) * 100.0), (300.0, 500.0, 0.1 * 300.0), (0.0, 500.0, 0.0)],
    ids=["formula above the bound", "the lower bound", "no conductance"],
)
def test_eq_11a_never_falls_below_its_lower_bound(element_conductance: float, area: float, expected: float) -> None:
    """L_ψ + L_χ = 0.2 (0.75 − ΣfAU/ΣA) ΣfAU ≥ 0.1 ΣfAU (I-4)."""
    result = envelope.thermal_bridge_conductance(element_conductance, area)
    assert result == expected
    assert result >= 0.1 * element_conductance


def test_internal_gains_follow_eq_37(tables: AustrianTables) -> None:
    """Q_i = q_i,h · BGF · 0.8 · t / 1000 with t the hours of the month."""
    profile = usage.usage(tables, _building())
    result = gains.internal_gains(profile, 125.0)
    expected = tuple(profile.internal_gains_heating_in_w_per_m2 * 125.0 * 0.8 * hours / 1000
                     for hours in usage.MONTH_DURATION_IN_H)
    assert result == expected
    assert result[0] / result[1] == pytest.approx(31 / 28)


def test_solar_gains_follow_eq_40_43_44(tables: AustrianTables) -> None:
    """A_trans = 0.7 A_W · F_S,h · g · 0.9 · 0.98 per surface; Q_s = Σ I_S,ON,j A_trans."""
    areas = gains.collector_areas(WINDOWS, 0.7, 0.65)
    for window in WINDOWS:
        assert areas[window.name] == 0.7 * window.area_in_m2 * 0.65 * window.g_value * (0.9 * 0.98)
    reference = climate.reference_climate(tables, WINDOWS)
    result = gains.solar_gains(areas, reference)
    for month in range(12):
        expected = sum(reference.radiation_in_kwh_per_m2[name][month] * area for name, area in areas.items())
        assert result[month] == expected


@pytest.mark.parametrize(
    "units, building_type, expected",
    [(1, "detached", 0.65), (2, None, 0.65), (3, "multi_family", 0.50), (10, "apartment_block", 0.50),
     (11, "apartment_block", 0.40), (5, "terraced", 0.65)],
)
def test_the_shading_factor_follows_8_3_1_2_2(units: int, building_type: str | None, expected: float) -> None:
    """0.65 for one or two units or a terraced house, 0.50 for three to ten, 0.40 above (R5.9)."""
    result = defaults.shading_factor(_building(units, building_type))
    assert result.value == expected
    assert result.assumption is not None and "8.3.1.2.2" in result.assumption.source
    assert ("building type not stated" in result.assumption.value) == (building_type is None)


def test_the_glass_share_is_the_simplified_approach_of_eq_40() -> None:
    """A_g = 0.7 A_W, recorded as an assumption (I-4)."""
    result = defaults.glass_share()
    assert result.value == 0.7
    assert result.assumption is not None and "Eq. (40)" in result.assumption.source
