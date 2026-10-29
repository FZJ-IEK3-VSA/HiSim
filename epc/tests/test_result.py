"""T-2: the result dictionary keeps its keys once, its series at twelve values and its common core (I-7, I-8)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from epc.inputs import record
from epc.model.errors import EpcError
from epc.model.result import COMMON_CORE, SITE_CLIMATE_KEY, EpcResult, RunInfo, building_to_dict
from epc.tests import support

pytestmark = pytest.mark.base


def _result(climate_basis: str = "national") -> EpcResult:
    """An empty result for a fixture building, its run stated."""
    loaded = record.load(support.FIXTURES / support.CONFIGURED)
    result = EpcResult(record.describe(loaded.buildings[0]))
    result.run = RunInfo("record", "building", "ZZ", "zz", "edition", climate_basis, "epc", "2026-10-12T00:00:00")
    return result


def _with_core(result: EpcResult) -> EpcResult:
    """The result with the common core filled with placeholders."""
    for key in COMMON_CORE:
        result.add_indicator(key, 1.0, "unit")
    return result


def test_a_key_is_added_once_per_section() -> None:
    """A second indicator, annual or monthly term of the same key is refused; sections are separate."""
    result = _result()
    result.add_indicator("key", 1.0, "unit")
    result.add_term("key", 1.0, "unit")
    result.add_monthly("key", [1.0] * 12, "unit")
    for add in (
        lambda: result.add_indicator("key", 2.0, "unit"),
        lambda: result.add_term("key", 2.0, "unit"),
        lambda: result.add_monthly("key", [2.0] * 12, "unit"),
    ):
        with pytest.raises(ValueError, match="added twice"):
            add()


@pytest.mark.parametrize("length", [11, 13])
def test_a_monthly_series_has_twelve_values(length: int) -> None:
    """Eleven or thirteen values are refused."""
    with pytest.raises(ValueError, match="12"):
        _result().add_monthly("key", [1.0] * length, "unit")


@pytest.mark.parametrize("missing", COMMON_CORE)
def test_a_missing_core_key_is_refused_on_writing(missing: str) -> None:
    """Without a key of the common core, to_dict refuses with E-17 naming it."""
    result = _result()
    for key in COMMON_CORE:
        if key != missing:
            result.add_indicator(key, 1.0, "unit")
    with pytest.raises(EpcError) as refusal:
        result.to_dict()
    assert refusal.value.error_id == "E-17"
    assert f"'{missing}'" in refusal.value.message


def test_the_site_climate_key_is_present_exactly_with_a_site_climate() -> None:
    """A reference+site engine must return it (E-17), a national engine must not."""
    with pytest.raises(EpcError) as refusal:
        _with_core(_result("reference+site")).to_dict()
    assert refusal.value.error_id == "E-17"
    assert SITE_CLIMATE_KEY in refusal.value.message
    national = _with_core(_result("national"))
    national.add_indicator(SITE_CLIMATE_KEY, 1.0, "unit")
    with pytest.raises(ValueError, match="present exactly"):
        national.to_dict()
    with_site = _with_core(_result("reference+site"))
    with_site.add_indicator(SITE_CLIMATE_KEY, 1.0, "unit")
    assert SITE_CLIMATE_KEY in with_site.to_dict()["indicators"]


def test_a_result_without_its_run_is_not_written() -> None:
    """The shell states the run before writing."""
    result = _with_core(_result())
    result.run = None
    with pytest.raises(ValueError, match="states no run"):
        result.to_dict()


def test_a_stated_correction_factor_is_written_only_where_stated() -> None:
    """D43: HiSim's elements write no stated factor; an element that states one writes it."""
    building = record.describe(record.load(support.FIXTURES / support.CONFIGURED).buildings[0])
    floor = dataclasses.replace(building.elements[0], stated_correction_factor=0.6)
    stated = dataclasses.replace(building, elements=(floor,) + building.elements[1:])
    assert all("stated_correction_factor" not in element for element in building_to_dict(building)["elements"])
    assert building_to_dict(stated)["elements"][0]["stated_correction_factor"] == 0.6


def test_to_dict_has_the_shape_of_mockup_a() -> None:
    """Every section of R4.2, entries as value/unit/cites(/label/note), assumptions and provenance carried."""
    result = _with_core(_result())
    result.add_indicator("labelled", "text", "unit", cites="clause", label="L", note="remark")
    result.add_term("annual", (1.0, 2.0), "unit", cites="clause")
    result.add_monthly("monthly", [float(month) for month in range(12)], "unit")
    result.assume("default", "value", "source")
    result.tables["table.csv"] = "sha256:digest"
    written = result.to_dict()
    assert list(written) == ["schema", "run", "indicators", "terms", "building", "site", "assumptions", "provenance"]
    assert written["schema"] == "epc-result/1"
    assert written["indicators"][COMMON_CORE[0]] == {"value": 1.0, "unit": "unit", "cites": ""}
    assert written["indicators"]["labelled"] == {
        "value": "text",
        "unit": "unit",
        "cites": "clause",
        "label": "L",
        "note": "remark",
    }
    assert written["terms"]["annual"]["annual"] == {"value": [1.0, 2.0], "unit": "unit", "cites": "clause"}
    assert written["terms"]["monthly"]["monthly"] == {"unit": "unit", "values": [float(month) for month in range(12)]}
    assert written["assumptions"] == [{"key": "default", "value": "value", "source": "source"}]
    assert written["provenance"]["tables"] == {"table.csv": "sha256:digest"}
    assert written["run"]["building"] == "building"
    json.dumps(written, allow_nan=False)
