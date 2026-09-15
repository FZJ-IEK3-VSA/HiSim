"""Tests of the envelope physics: U-value sources, the thickness rule, composition, and the checks.

The numbers here are the ones a wrong result would hide behind, so each is pinned against a worked
example rather than against whatever the code currently returns.
"""

import json
from pathlib import Path

import pytest

from hisim.renovisor.envelope import (
    EnvelopePaths,
    ExclusivityTable,
    FitToBuilding,
    InventoryThenTabula,
    RegulatoryTargets,
    TabulaUValues,
    ThicknessDefault,
    UValueComposer,
)
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.vocabulary import ThermalElement

pytestmark = pytest.mark.base

EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"


@pytest.fixture(name="inventory")
def fixture_inventory() -> Inventory:
    """The example Irish 1988 detached house, fresh for each test."""
    return Inventory.from_dict(json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8")))


@pytest.fixture(name="targets", scope="module")
def fixture_targets() -> RegulatoryTargets:
    """The committed Irish target table."""
    return RegulatoryTargets.load()


def test_u_value_paths_follow_the_contract_s_naming() -> None:
    """Each element's U-value sits under envelope_details, named after the element."""
    assert EnvelopePaths.u_value_path(ThermalElement.ROOF) == (
        "building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin"
    )
    assert EnvelopePaths.u_value_path(ThermalElement.FACADE).endswith("facade_u_value_in_watt_per_m2_per_kelvin")


def test_tabula_supplies_five_u_values_for_one_code() -> None:
    """The archetype row carries one U-value per element, read from the U_Actual columns."""
    values = TabulaUValues.for_code("IE.N.SFH.04.Gen.ReEx.001.001")

    assert values[ThermalElement.FACADE] == pytest.approx(2.4)
    assert values[ThermalElement.WINDOW] == pytest.approx(5.7)
    assert set(values) == set(ThermalElement)


def test_the_inventory_wins_per_element(inventory: Inventory) -> None:
    """Decision Q10: a surveyed element beats the archetype, one element at a time."""
    source = InventoryThenTabula(inventory)

    assert source.u_value(ThermalElement.WINDOW) == pytest.approx(4.8)
    assert source.source_of(ThermalElement.WINDOW) == "the inventory's envelope_details"
    assert source.u_value(ThermalElement.FACADE) == pytest.approx(0.6)
    assert "TABULA" in source.source_of(ThermalElement.FACADE)
    assert source.building_code == "IE.N.SFH.07.Gen.ReEx.001.001"


def test_a_null_envelope_value_falls_back_to_tabula(inventory: Inventory) -> None:
    """A field that exists and holds null means 'not stated', not 'zero'."""
    inventory.set(EnvelopePaths.u_value_path(ThermalElement.WINDOW), None)

    assert InventoryThenTabula(inventory).u_value(ThermalElement.WINDOW) == pytest.approx(3.1)


def test_every_regulatory_row_carries_a_source_and_a_status(targets: RegulatoryTargets) -> None:
    """A regulatory number without provenance is a wrong result that looks right."""
    assert targets.target_ids() == (
        "pitched_roof_insulated_at_ceiling",
        "pitched_roof_insulated_on_slope",
        "flat_roof",
        "wall",
        "cavity_fill_wall",
        "ground_floor",
        "window",
        "door",
    )
    for target_id in targets.target_ids():
        row = targets.target(target_id)
        assert row.source.startswith("Building Regulations TGD L")
        assert row.status.startswith("PROVISIONAL")
        assert row.u_value_in_watt_per_m2_per_kelvin > 0


def test_the_regulatory_values(targets: RegulatoryTargets) -> None:
    """The eight target values, pinned so a silent edit of the data file fails the build."""
    assert targets.u_value("pitched_roof_insulated_at_ceiling") == 0.16
    assert targets.u_value("pitched_roof_insulated_on_slope") == 0.25
    assert targets.u_value("flat_roof") == 0.25
    assert targets.u_value("wall") == 0.35
    assert targets.u_value("cavity_fill_wall") == 0.55
    assert targets.u_value("ground_floor") == 0.45
    assert targets.u_value("window") == 1.6
    assert targets.u_value("door") == 1.6


def test_a_missing_target_row_raises(targets: RegulatoryTargets) -> None:
    """A measure needing a target the table lacks refuses; it does not invent one."""
    with pytest.raises(KeyError):
        targets.u_value("conservatory")


def test_the_worked_thickness_example() -> None:
    """A 1.5 W/m2K wall insulated with EPS to the 0.35 target needs 77.8 mm, so 80 mm."""
    assert ThicknessDefault.for_target(1.5, 0.0355, 0.35) == 80


def test_a_thicker_layer_is_needed_for_a_harder_target() -> None:
    """The 0.16 attic target asks much more of the same material than the 0.35 wall target."""
    assert ThicknessDefault.for_target(1.5, 0.041, 0.16) > ThicknessDefault.for_target(1.5, 0.041, 0.35)


def test_an_element_already_at_the_target_needs_no_layer() -> None:
    """The rule is honest rather than useful here: the request should then state a thickness."""
    assert ThicknessDefault.for_target(0.2, 0.04, 0.35) == 0


def test_thickness_rejects_impossible_inputs() -> None:
    """A zero conductivity or U-value would make the formula meaningless."""
    with pytest.raises(ValueError):
        ThicknessDefault.for_target(1.5, 0.0, 0.35)
    with pytest.raises(ValueError):
        ThicknessDefault.for_target(0.0, 0.04, 0.35)


def test_two_layers_on_one_element_add_up() -> None:
    """Requirement M2: the second layer improves on the first rather than replacing it."""
    first = UValueComposer.resistance(80, 0.0355)
    second = UValueComposer.resistance(60, 0.04)

    one_layer = UValueComposer.compose(2.4, [first])
    two_layers = UValueComposer.compose(2.4, [first, second])

    assert two_layers < one_layer < 2.4
    assert two_layers == pytest.approx(1.0 / (1.0 / 2.4 + first + second))


def test_a_replacement_sets_the_baseline() -> None:
    """A replaced window starts from the new unit's U-value, not the old one's."""
    assert UValueComposer.compose(1.4, []) == pytest.approx(1.4)


def test_composition_rejects_impossible_inputs() -> None:
    """A non-positive baseline or a negative resistance is a programming error, not a result."""
    with pytest.raises(ValueError):
        UValueComposer.compose(0.0, [])
    with pytest.raises(ValueError):
        UValueComposer.compose(1.0, [-0.5])
    with pytest.raises(ValueError):
        UValueComposer.resistance(-10, 0.04)


def test_exclusivity_refuses_two_floor_constructions() -> None:
    """A basement ceiling and a suspended floor describe two different buildings."""
    refusals = ExclusivityTable.check(
        ["BASEMENT_CEILING_INSULATION", "SUSPENDED_GROUND_FLOOR_INSULATION"]
    )

    assert len(refusals) == 1
    assert refusals[0].reason is ReasonCode.CONTRADICTORY_MEASURES
    assert "floor" in refusals[0].detail


def test_exclusivity_allows_two_layers_within_one_group() -> None:
    """Rafter and rolled-out attic insulation are two layers of one loft, not two roofs."""
    assert ExclusivityTable.check(["RAFTER_INSULATION", "ROLLED_OUT_ATTIC_INSULATION"]) == ()


def test_exclusivity_allows_external_plus_internal_wall_insulation() -> None:
    """The facade has no groups on purpose: both measures are real layers on one wall."""
    assert ExclusivityTable.check(["EXTERNAL_INSULATION", "INTERNAL_DRY_LINING_INSULATION"]) == ()


def test_fit_is_not_checked_when_the_inventory_is_silent(inventory: Inventory) -> None:
    """The construction facts are not in the contract yet, so a silent inventory is trusted."""
    assert FitToBuilding.check(["CAVITY_WALL_INSULATION"], inventory) == ()


def test_fit_refuses_a_measure_the_building_cannot_take(inventory: Inventory) -> None:
    """A cavity measure on a solid wall is a refusal once the inventory states the construction."""
    inventory.set(EnvelopePaths.WALL_CONSTRUCTION, "SOLID")

    refusals = FitToBuilding.check(["CAVITY_WALL_INSULATION"], inventory)

    assert len(refusals) == 1
    assert refusals[0].reason is ReasonCode.MEASURE_DOES_NOT_FIT_BUILDING
    assert refusals[0].measure_id == "CAVITY_WALL_INSULATION"


def test_fit_accepts_a_measure_the_building_can_take(inventory: Inventory) -> None:
    """A cavity measure on a cavity wall passes; so does a basement measure over a basement."""
    inventory.set(EnvelopePaths.WALL_CONSTRUCTION, "CAVITY")
    inventory.set(EnvelopePaths.FLOOR_CONSTRUCTION, "OVER_UNHEATED_BASEMENT")

    assert FitToBuilding.check(["CAVITY_WALL_INSULATION", "BASEMENT_CEILING_INSULATION"], inventory) == ()
