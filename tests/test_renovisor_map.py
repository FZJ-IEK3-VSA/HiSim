"""The translation map is collected correctly and the committed page is current.

Two kinds of claim are checked here. The first is about the data: all 33 catalogue measures are
classified, every status the page can show is actually reachable, and the joins onward — inventory
path, bindings target, base file — are present where an effect has them.

The second is the freshness claim (decision V1). ``roadmap/renovisor/translation_map.html`` is a
committed file, and a registry, catalogue or bindings change that is not reflected in it would
leave a page that quietly lies. The test therefore renders the page and compares it byte for byte
with what is committed — the same discipline the energy-system JSON schema is kept under — and
also asserts that the page loads nothing from outside itself.
"""

from pathlib import Path
from typing import Dict

import pytest

from hisim.renovisor.catalogue import Catalogue
from hisim.renovisor.map import (
    BaseFileDescriber,
    EffectDescriber,
    MapCommand,
    MapData,
    MapRenderer,
    MapStatus,
    ReferenceBuilding,
    StatusRules,
)
from hisim.renovisor.effects import NoEffect, Refusal, SelectBaseFile
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.vocabulary import HeatGenerator, SolarThermalSupplies, ThermalElement

pytestmark = pytest.mark.base

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_MAP = REPOSITORY_ROOT / MapCommand.DEFAULT_OUTPUT


@pytest.fixture(name="data", scope="module")
def fixture_data() -> MapData:
    """Collect the map once for the whole module; it runs every measure many times over."""
    return MapData.collect()


@pytest.fixture(name="document", scope="module")
def fixture_document(data: MapData) -> str:
    """Render the page once for the whole module."""
    return MapRenderer(data).render()


def test_every_catalogue_measure_is_on_the_map(data: MapData) -> None:
    """All 33 measures are collected, in catalogue order, each with at least one outcome."""
    catalogue = Catalogue.load()
    assert tuple(measure.measure_id for measure in data.measures) == catalogue.measure_ids()
    assert len(data.measures) == 33
    for measure in data.measures:
        assert measure.outcomes, f"{measure.measure_id} produced no outcome at all"


def test_every_measure_has_exactly_one_headline_status(data: MapData) -> None:
    """The per-status counts partition the catalogue, which is what makes them worth reading."""
    statuses = data.statuses()
    assert set(statuses) == set(MapStatus.in_preference_order())
    counted = sum(len(ids) for ids in statuses.values())
    assert counted == len(data.measures)
    for status, ids in statuses.items():
        assert len(set(ids)) == len(ids), f"{status} lists a measure twice"


def test_every_status_is_reachable(data: MapData) -> None:
    """A status nothing reaches would be a legend entry that means nothing."""
    reached = {outcome.status for measure in data.measures for outcome in measure.outcomes}
    assert reached == set(MapStatus.in_preference_order())


def test_every_measure_names_its_decisions(data: MapData) -> None:
    """Decision V2: the decision filter is built from the registry docstrings."""
    for measure in data.measures:
        assert measure.decisions, f"{measure.measure_id} contributes no decision id"
    assert "Q1" in data.decision_ids()


def test_display_names_travel_beside_the_hisim_spelling(data: MapData) -> None:
    """Decision V3: the page is meant to be readable by the frontend team as well."""
    heating = next(measure for measure in data.measures if measure.measure_id == "HEATING_INSTALLATION")
    values = {
        (choice.value, choice.display_value)
        for outcome in heating.outcomes
        for choices in outcome.choice_sets
        for choice in choices
    }
    assert ("FLOORHEATING", "surface_heating") in values


def test_an_effect_is_joined_through_to_a_hisim_field(data: MapData) -> None:
    """The point of the page: an inventory path resolves to a component and a field."""
    temperature = next(
        measure for measure in data.measures if measure.measure_id == "CHANGE_ROOM_TEMPERATURE"
    )
    rows = [row for outcome in temperature.outcomes for row in outcome.effects]
    assert rows
    assert rows[0].inventory_path == "building_config.general.set_heating_temperature_in_celsius"
    assert "Building.set_heating_temperature_in_celsius" in rows[0].hisim_target
    assert "HeatDistributionController.set_heating_temperature_for_building_in_celsius" in rows[0].hisim_target


def test_a_base_file_switch_names_the_recorded_files(data: MapData) -> None:
    """A switch is only informative if the page says which recorded files it can still reach."""
    heating = next(measure for measure in data.measures if measure.measure_id == "HEATING_SYSTEM")
    targets = {row.hisim_target for outcome in heating.outcomes for row in outcome.effects}
    assert "household_heatpump_building_sizer.grouped.energy_system.yaml" in " ".join(targets)


def test_the_base_file_describer_narrows_by_every_field_a_wish_states() -> None:
    """A wish that no recorded file satisfies has to come back empty, not with everything."""
    heat_pump = SelectBaseFile(measure_id="X", generator=HeatGenerator.HEAT_PUMP)
    assert BaseFileDescriber.files(heat_pump) == (
        "household_heatpump_building_sizer.grouped.energy_system.yaml",
        "household_heatpump_car_building_sizer.grouped.energy_system.yaml",
        "household_heatpump_solar_thermal_building_sizer.grouped.energy_system.yaml",
    )
    oil_collector = SelectBaseFile(
        measure_id="X",
        generator=HeatGenerator.OIL_HEATING,
        solar_thermal=SolarThermalSupplies.DHW_ONLY,
    )
    assert BaseFileDescriber.files(oil_collector) == ()


def test_status_rules_separate_waiting_for_data_from_waiting_for_a_decision() -> None:
    """The distinction the page's colours are built on."""
    blocked = Refusal(ReasonCode.MATERIAL_NOT_IN_DATABASE, "p", "d", "M")
    refused = Refusal(ReasonCode.UNSUPPORTED_SYSTEM, "p", "d", "M")
    assert StatusRules.of([blocked]) is MapStatus.BLOCKED_ON_DATA
    assert StatusRules.of([refused]) is MapStatus.REFUSED
    assert StatusRules.of([blocked, refused]) is MapStatus.REFUSED
    assert StatusRules.of([NoEffect(ReasonCode.NO_SHADING_MODEL, "M")]) is MapStatus.NO_EFFECT
    assert StatusRules.of([]) is MapStatus.SIMULATED


def test_an_unbound_path_is_named_rather_than_silently_blank() -> None:
    """The map must not hide a gap in the bindings table behind an empty cell."""
    assert EffectDescriber.target_of("nothing.the.contract.has") == "no binding covers this path"


def test_the_reference_building_is_stated_rather_than_looked_up(document: str) -> None:
    """Nothing on the page depends on a TABULA row, which is what keeps it deterministic."""
    reference = ReferenceBuilding()
    values = {element: reference.u_value(element) for element in ThermalElement}
    assert set(values.values()) == {ReferenceBuilding.U_VALUE_IN_WATT_PER_M2_PER_KELVIN}
    assert "reference U-value of 2 W/m2K" in document


def test_rendering_is_deterministic(data: MapData) -> None:
    """Two renderings of one collection, and two collections, produce the same bytes."""
    assert MapRenderer(data).render() == MapRenderer(data).render()
    assert MapRenderer(MapData.collect()).render() == MapRenderer(data).render()


def test_the_page_loads_nothing_from_outside_itself(document: str) -> None:
    """Self-contained means self-contained: no script, style, font or image from a URL."""
    for forbidden in ("http://", "https://", "<link", "<img", "src=", "@import"):
        assert forbidden not in document, f"the page references {forbidden}"


def test_the_page_carries_no_timestamp(document: str) -> None:
    """A timestamp would make every regeneration a diff and the freshness test useless."""
    for forbidden in ("generated at", "20260915", "T00:00", "datetime"):
        assert forbidden.lower() not in document.lower()


def test_the_page_shows_the_pinned_contract_revision(data: MapData, document: str) -> None:
    """The header has to say which contract revision the rules on the page were written against."""
    assert data.contract_commit[:12] in document
    assert data.contract_sha256[:12] in document


def test_the_committed_map_is_current(document: str) -> None:
    """Decision V1: regenerate with ``python -m hisim.renovisor.map`` when this fails."""
    assert COMMITTED_MAP.exists(), f"{COMMITTED_MAP} is missing; run python -m hisim.renovisor.map"
    committed = COMMITTED_MAP.read_text(encoding="utf-8")
    assert committed == document, (
        "roadmap/renovisor/translation_map.html is out of date; "
        "regenerate it with 'python -m hisim.renovisor.map'"
    )


def test_the_command_writes_the_page(tmp_path: Path, document: str) -> None:
    """The freshness test and a person run exactly the same code path."""
    target = tmp_path / "nested" / "map.html"
    assert MapCommand.main(["--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == document


def test_the_status_counts_are_the_ones_the_reviewer_was_told(data: MapData) -> None:
    """A change to what HiSim can simulate has to be looked at, not merely regenerated.

    The numbers are the ones reported for step 4 and differ from
    ``measures_v2_requirements.md`` §4.1 because the material and glazing rows are blocked
    (decisions Q6, Q7, Q13).
    """
    counts: Dict[str, int] = {
        status.value: len(ids) for status, ids in data.statuses().items()
    }
    assert counts == {
        MapStatus.SIMULATED.value: 16,
        MapStatus.LAW_PENDING.value: 1,
        MapStatus.NO_EFFECT.value: 9,
        MapStatus.BLOCKED_ON_DATA.value: 6,
        MapStatus.REFUSED.value: 1,
    }
