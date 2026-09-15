"""Tests of the material import and its typed table.

Three things must hold. The range parser reads the dump's prose cells the way the import assumes.
The import fails loudly rather than defaulting a conductivity. And the committed table is exactly
what re-running the import produces, so a contract refresh without a re-import is a failing build
rather than a stale physics constant.
"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.materials_import import MaterialRowError, MaterialsImport, RangeParser

pytestmark = pytest.mark.base


@pytest.fixture(name="materials", scope="module")
def fixture_materials() -> InsulationMaterials:
    """The committed material table, loaded once for the module."""
    return InsulationMaterials.load()


@pytest.mark.parametrize(
    "raw,low,high",
    [
        ("110 - 270", 110.0, 270.0),
        ("28-45", 28.0, 45.0),
        ("40-75", 40.0, 75.0),
        ("≥ 50", 50.0, None),
        ("50", 50.0, 50.0),
        (50, 50.0, 50.0),
        (0.041, 0.041, 0.041),
        ("120-1401-0", None, None),
        ("not relevant", None, None),
        ("n", None, None),
        (None, None, None),
    ],
)
def test_range_parser(raw: Any, low: Optional[float], high: Optional[float]) -> None:
    """Numbers, hyphenated ranges and 'at least' cells parse; anything else keeps only its text."""
    parsed = RangeParser.parse(raw)
    assert parsed.low == low
    assert parsed.high == high
    assert parsed.raw == (None if raw is None else str(raw))


def test_cost_cells_must_be_plain_numbers() -> None:
    """A per-square-metre price in a per-cubic-metre column is no value, not a range to read."""
    assert RangeParser.number_or_none("41-80€/m2") is None
    assert RangeParser.number_or_none(107) == 107.0
    assert RangeParser.number_or_none(True) is None


def test_a_row_without_a_numeric_conductivity_fails_the_import() -> None:
    """Decision Q5: an unparseable row fails loudly; nothing is skipped and nothing defaults."""
    with pytest.raises(MaterialRowError) as error:
        MaterialsImport._read_row(0, {"asp_id": "broken", "Material properties and characteristics": {}})
    assert error.value.asp_id == "broken"
    assert "thermal conductivity" in error.value.detail


def test_a_row_without_an_asp_id_fails_the_import() -> None:
    """A material with no id could not be addressed by a request and is not silently numbered."""
    with pytest.raises(MaterialRowError):
        MaterialsImport._read_row(7, {"name": "nameless"})


def test_the_committed_table_is_what_the_import_produces(tmp_path: Path) -> None:
    """Regenerating into a temporary directory must reproduce the committed file byte for byte."""
    regenerated = MaterialsImport.write(tmp_path / "insulation_materials.json")

    assert regenerated.read_text(encoding="utf-8") == MaterialsImport.OUTPUT_PATH.read_text(encoding="utf-8")


def test_the_table_records_which_contract_revision_it_came_from(materials: InsulationMaterials) -> None:
    """Provenance is part of the table, so a number can be traced back to a dump revision."""
    source = materials.source()
    assert source["file"] == "materials.yaml"
    assert len(str(source["contract_commit"])) == 40
    assert len(str(source["sha256"])) == 64


def test_every_material_has_a_positive_conductivity(materials: InsulationMaterials) -> None:
    """The one number the U-value derivation needs is present and usable for all 26 rows."""
    assert len(materials.asp_ids()) == 26
    for material in materials.materials():
        assert material.thermal_conductivity_in_watt_per_meter_per_kelvin > 0


def test_one_row_in_full(materials: InsulationMaterials) -> None:
    """A worked row: the fields the translation layer reads all arrive typed."""
    wood_fibre = materials.by_asp_id("wood_fiber_rigid_board")

    assert wood_fibre.name == "Wood fiber - Rigid board"
    assert wood_fibre.thermal_conductivity_in_watt_per_meter_per_kelvin == 0.041
    assert wood_fibre.lifespan_in_years.low == 50.0
    assert wood_fibre.lifespan_in_years.high is None
    assert wood_fibre.investment_cost_in_euro_per_m3["IE"].low == 260.0
    assert wood_fibre.investment_cost_in_euro_per_m3["IE"].high == 660.0
    assert wood_fibre.co2_footprint_in_kg_per_m2 == 11.421507
    assert wood_fibre.co2_storage_in_kg_per_m2 == -36.981043
    assert "Recycling" in wood_fibre.end_of_life
    assert wood_fibre.comparison_baseline is False
    assert wood_fibre.sources


def test_a_non_numeric_cost_keeps_its_raw_text(materials: InsulationMaterials) -> None:
    """The Spanish EPS price is a per-square-metre range; it becomes no number plus its text."""
    spanish = materials.by_asp_id("polystyrene_eps_rigid_board").investment_cost_in_euro_per_m3["ES"]
    assert spanish.low is None
    assert spanish.raw_low == "41-80"


def test_an_unknown_material_raises(materials: InsulationMaterials) -> None:
    """Decision Q6: an absent material is a KeyError the registry refuses on, never a substitute."""
    assert not materials.contains("pir")
    with pytest.raises(KeyError):
        materials.by_asp_id("pir")


def test_heat_capacity_and_density_are_not_imported() -> None:
    """Challenge C10: the dump's heat capacity and density are unusable and stay out of the table."""
    table = json.loads(MaterialsImport.OUTPUT_PATH.read_text(encoding="utf-8"))
    for row in table["materials"]:
        for field_name in row:
            assert "heat_capacity" not in field_name
            assert "density" not in field_name
