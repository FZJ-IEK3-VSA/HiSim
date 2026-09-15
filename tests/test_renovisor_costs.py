"""The cost block: the engine's figures, the envelope materials, and what is still missing.

Four things are worth asserting here. The envelope arithmetic, because it is the one number in
the payload HiSim computes itself and decisions Q8/Q9 are strict about where it may come from --
the material table's euro per cubic metre, times thickness, times element area, and nothing else.
The translation of the engine's ``min``/``best_estimate``/``max`` band into the contract's
``low``/``best_estimate``/``high`` one. The subsidy wiring of step 6 §5: the moment a country
catalogue file exists, the grant field starts being published, which a synthetic ``IE.json`` in a
temporary directory proves without anyone having written the real one. And the three fields that
are absent by decision -- the grant, the payback period and the property-value increase -- each
of which has to be in ``missing`` with a reason a caller can act on.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from hisim.renovisor.costs import (
    CostBuilder,
    CostDocuments,
    CostField,
    CostSchema,
    CostSources,
    EnvelopeMaterialCost,
    SubsidyCatalogue,
)
from hisim.renovisor.effects import AddThermalResistance
from hisim.renovisor.layers import ElementAreas, EnvelopeLayers
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.provenance import Range
from hisim.renovisor.vocabulary import Provenance, ThermalElement

pytestmark = pytest.mark.base


def layers_of(*additions: AddThermalResistance, area_in_m2: float = 100.0) -> EnvelopeLayers:
    """Return the layers of a package with one fixed area per element.

    Args:
        *additions: The insulation effects.
        area_in_m2: The area every element is given, so the arithmetic stays checkable by eye.

    Returns:
        The resolved layers.
    """
    areas = ElementAreas(
        areas={element: area_in_m2 for element in ThermalElement},
        sources={element: "a test" for element in ThermalElement},
    )
    return EnvelopeLayers.of(additions, areas)


def one_layer() -> EnvelopeLayers:
    """Return a single 100 mm EPS layer on a 100 m2 facade."""
    return layers_of(
        AddThermalResistance(
            element=ThermalElement.FACADE,
            material_asp_id="polystyrene_eps_rigid_board",
            thickness_in_mm=100,
            measure_id="EXTERNAL_INSULATION",
        )
    )


def engine_exports(
    tmp_path: Path,
    perspective: str = CostSources.PERSPECTIVE_ID,
    subsidy_npv: Optional[float] = None,
) -> Path:
    """Write a minimal pair of cost-engine exports into a result directory.

    The shapes are the ones ``hisim/economics/exports.py`` writes: a perspective map in
    ``lifecycle_costs.json`` and a semicolon-separated long-format timeline.

    Args:
        tmp_path: The directory to write into.
        perspective: The perspective id the rows are written under.
        subsidy_npv: When given, a ``SUBSIDY`` entry in ``npv_by_category`` of the *net*
            perspective, negative-signed as the engine books support.

    Returns:
        The result directory.
    """
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    categories: Dict[str, Any] = {}
    document: Dict[str, Any] = {
        perspective: {
            "perspective": perspective,
            "parameters": {CostSources.HORIZON_FIELD: 20},
            "total_npv_in_euro": {"min": 100.0, "best_estimate": 200.0, "max": 300.0},
            "equivalent_annual_cost_in_euro": {"min": 12.0, "best_estimate": 24.0, "max": 36.0},
            "npv_by_category": categories,
        }
    }
    if subsidy_npv is not None:
        document[CostSources.SUBSIDY_PERSPECTIVE_ID] = {
            "perspective": CostSources.SUBSIDY_PERSPECTIVE_ID,
            "parameters": {CostSources.HORIZON_FIELD: 20},
            "npv_by_category": {CostSources.SUBSIDY_CATEGORY: subsidy_npv},
        }
    (results / CostDocuments.COSTS_FILE_NAME).write_text(json.dumps(document), encoding="utf-8")
    rows = [
        (0, CostSources.INVESTMENT_CATEGORY, 1000.0, 2000.0, 3000.0),
        (1, CostSources.ENERGY_CATEGORIES[0], 10.0, 20.0, 30.0),
        (1, CostSources.ENERGY_CATEGORIES[1], 1.0, 2.0, 3.0),
        (1, CostSources.MAINTENANCE_CATEGORY, 5.0, 6.0, 7.0),
    ]
    header = ";".join(
        [
            CostDocuments.PERSPECTIVE_COLUMN,
            CostDocuments.YEAR_COLUMN,
            CostDocuments.CATEGORY_COLUMN,
            CostDocuments.NOMINAL_LOW_COLUMN,
            CostDocuments.NOMINAL_BEST_COLUMN,
            CostDocuments.NOMINAL_HIGH_COLUMN,
        ]
    )
    body = "\n".join(
        f"{perspective};{year};{category};{low};{best};{high}" for year, category, low, best, high in rows
    )
    (results / CostDocuments.TIMELINE_FILE_NAME).write_text(f"{header}\n{body}\n", encoding="utf-8")
    return results


def build(
    results: Optional[Path],
    layers: EnvelopeLayers,
    catalogue: Optional[Path] = None,
    country: str = "IE",
) -> Any:
    """Return the cost block for one set of inputs, with the committed material table.

    The ten-year re-evaluation is deliberately left out by passing no result directory to the
    builder: it runs the real cost engine, which belongs in the end-to-end test rather than in a
    base-marked one.
    """
    return CostBuilder(
        documents=CostDocuments.load(results) if results is not None else None,
        layers=layers,
        materials=InsulationMaterials.load(),
        country=country,
        results_directory=None,
        subsidy_catalogue_path=catalogue,
    ).build()


def test_the_envelope_cost_is_the_material_price_times_the_installed_volume() -> None:
    """Decision Q8/Q9: euro per cubic metre from the database, times thickness, times area."""
    prices = InsulationMaterials.load().by_asp_id("polystyrene_eps_rigid_board")
    band = prices.investment_cost_in_euro_per_m3["IE"]
    assert band.low is not None and band.high is not None

    total, note = EnvelopeMaterialCost(one_layer(), InsulationMaterials.load(), "IE").total()

    assert total is not None
    volume = 0.1 * 100.0
    assert total.low == pytest.approx(band.low * volume)
    assert total.high == pytest.approx(band.high * volume)
    assert total.best_estimate == pytest.approx(0.5 * (band.low + band.high) * volume)
    assert "material only" in note


def test_the_envelope_cost_needs_a_price_column_for_the_country() -> None:
    """A country the database has no price column for makes the whole envelope figure absent."""
    total, note = EnvelopeMaterialCost(one_layer(), InsulationMaterials.load(), "XX").total()

    assert total is None
    assert "XX" in note


def test_the_engine_band_becomes_the_contracts_own_slot_names(tmp_path: Path) -> None:
    """C3: ``min``/``max`` on the engine's side, ``low``/``high`` on the contract's."""
    block = build(engine_exports(tmp_path), layers_of())

    value = block.values[CostField.NET_PRESENT_VALUE.value]
    assert value["value"] == {"low": 100.0, "best_estimate": 200.0, "high": 300.0}
    assert value["provenance"] == Provenance.PARTIAL.value


def test_the_monthly_cost_is_the_equivalent_annual_cost_over_twelve(tmp_path: Path) -> None:
    """The engine's headline KPI, divided by twelve and by nothing else."""
    block = build(engine_exports(tmp_path), layers_of())

    value = block.values[CostField.MONTHLY_TWENTY_YEARS.value]["value"]
    assert value["best_estimate"] == pytest.approx(24.0 / 12.0)


def test_the_investment_is_the_devices_plus_the_envelope_with_the_split_visible(
    tmp_path: Path,
) -> None:
    """Decision Q23: one total, and a breakdown that says which half came from where."""
    block = build(engine_exports(tmp_path), one_layer())

    breakdown = block.values[CostField.INVESTMENT_BREAKDOWN.value]
    devices = Range(**breakdown[CostBuilder.DEVICES_KEY]["value"])
    envelope = Range(**breakdown[CostBuilder.ENVELOPE_KEY]["value"])
    total = Range(**block.values[CostField.INVESTMENT.value]["value"])

    assert devices == Range(low=1000.0, best_estimate=2000.0, high=3000.0)
    assert total.low == pytest.approx(devices.low + envelope.low)
    assert total.high == pytest.approx(devices.high + envelope.high)


def test_the_year_one_bills_come_from_the_timeline(tmp_path: Path) -> None:
    """Energy is the sum of the energy categories of year 1; maintenance is its own category."""
    block = build(engine_exports(tmp_path), layers_of())

    energy = block.values[CostField.ENERGY.value]["value"]
    maintenance = block.values[CostField.MAINTENANCE.value]["value"]
    assert energy == {"low": 11.0, "best_estimate": 22.0, "high": 33.0}
    assert maintenance == {"low": 5.0, "best_estimate": 6.0, "high": 7.0}


def test_a_run_without_cost_exports_leaves_every_engine_field_missing(tmp_path: Path) -> None:
    """No ``lifecycle_costs.json`` means no cost figures at all, with the reason stated."""
    block = build(None, layers_of())

    missing = {entry.field: entry.reason for entry in block.missing}
    for field in (
        CostField.NET_PRESENT_VALUE,
        CostField.ENERGY,
        CostField.MAINTENANCE,
        CostField.MONTHLY_TWENTY_YEARS,
    ):
        assert f"costs.{field.value}" in missing
    assert CostDocuments.COSTS_FILE_NAME in missing[f"costs.{CostField.NET_PRESENT_VALUE.value}"]


def test_the_three_fields_with_no_source_are_missing_with_their_reasons(tmp_path: Path) -> None:
    """The grant, the payback period and the property-value increase, each with its own reason."""
    block = build(engine_exports(tmp_path), one_layer())

    missing = {entry.field: entry.reason for entry in block.missing}
    assert "Q24" in missing[f"costs.{CostField.GRANT.value}"]
    assert "base calculation" in missing[f"costs.{CostField.PAYBACK.value}"]
    assert "A13" in missing[f"costs.{CostField.PROPERTY_VALUE.value}"]


def test_the_grant_appears_the_moment_a_country_catalogue_exists(tmp_path: Path) -> None:
    """Step 6 §5: the payload picks the solver's result up when ``IE.json`` lands, with no edit."""
    catalogue_directory = tmp_path / "subsidy_catalog"
    catalogue_directory.mkdir()
    (catalogue_directory / "IE.json").write_text(
        json.dumps({"country": "IE", "schemes": []}), encoding="utf-8"
    )
    catalogue = SubsidyCatalogue.path_for("IE", catalogue_directory)
    assert catalogue is not None

    results = engine_exports(tmp_path, subsidy_npv=-4000.0)
    block = build(results, one_layer(), catalogue=catalogue)

    value = block.values[CostField.GRANT.value]["value"]
    assert value == {"low": 4000.0, "best_estimate": 4000.0, "high": 4000.0}
    assert f"costs.{CostField.GRANT.value}" not in {entry.field for entry in block.missing}


def test_a_country_without_a_catalogue_has_none(tmp_path: Path) -> None:
    """The lookup is a file check, so no catalogue is ``None`` rather than an empty one."""
    assert SubsidyCatalogue.path_for("IE", tmp_path) is None
    assert SubsidyCatalogue.path_for("DE") is not None


def test_the_shipped_catalogue_directory_is_where_step_six_b_will_write(tmp_path: Path) -> None:
    """A wrong directory would make the grant field stay absent forever without saying so."""
    assert SubsidyCatalogue.directory().name == SubsidyCatalogue.DIRECTORY_NAME
    assert (SubsidyCatalogue.directory() / "DE.json").is_file()


def test_the_map_pane_describes_every_cost_field() -> None:
    """A field added to the payload without a row on the translation map is a failing build."""
    described: Tuple[str, ...] = tuple(row.field for row in CostSchema.rows())
    assert set(described) == {field.value for field in CostField}
