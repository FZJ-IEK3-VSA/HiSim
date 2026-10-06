"""Unit tests for ``scripts/golden_kpis.py`` — golden leaves, their reader, and KPI comparison.

Every helper is pure and depends only on its arguments (and the module tolerance
constants), so these tests pin behaviour without touching stored goldens or
running HiSim.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiSource, KpiTagEnumClass
from scripts.golden_kpis import (
    ABS_TOL,
    ADDRESS,
    MISSING,
    NEW,
    REL_TOL,
    UNIT,
    VALUE,
    GoldenFormatError,
    compare,
    golden_leaves,
    load_golden,
    read_golden,
)
from tests.golden_leaf_factory import component, derived, general, key_of, leaf_map

pytestmark = pytest.mark.base


def _entry(
    name: str,
    value: Any,
    unit: str = "kWh",
    source: KpiSource | None = None,
    tag: KpiTagEnumClass = KpiTagEnumClass.GENERAL,
) -> Dict[str, Any]:
    """One serialized KPI entry, as ``all_kpis.json`` holds it."""
    legacy_name = None if source is None else source.name
    return KpiEntry(
        name=name, unit=unit, value=value, tag=tag, source=source, name_of_source_component=legacy_name
    ).to_dict()


# --------------------------------------------------------------------------- #
# golden_leaves()
# --------------------------------------------------------------------------- #
def test_golden_leaves_keys_every_kpi_by_its_dotted_address_and_stores_its_fields() -> None:
    """The ``{building:{tag:{key:entry}}}`` tree becomes one leaf per KPI, with unit and source fields."""
    building = KpiSource(member="Building", name="Building", display_name="The house", label="mine")
    raw = {
        "BUI1": {
            "General": {
                "Self-sufficiency rate of electricity": _entry("Self-sufficiency rate of electricity", 41.2, "%")
            },
            "Building": {
                "Conditioned floor area (Building)": _entry(
                    "Conditioned floor area", 121, "m2", building, tag=KpiTagEnumClass.BUILDING
                ),
            },
        }
    }

    leaves = golden_leaves(raw)

    assert leaves == {
        "BUI1.General.Self-sufficiency rate of electricity": {
            "value": 41.2,
            "unit": "%",
            "building": "BUI1",
            "tag": "General",
            "name": "Self-sufficiency rate of electricity",
            "source": None,
        },
        "BUI1.Building.Conditioned floor area (Building)": {
            "value": 121.0,
            "unit": "m2",
            "building": "BUI1",
            "tag": "Building",
            "name": "Conditioned floor area",
            "source": {
                "import": None,
                "instance": None,
                "path": [],
                "member": "Building",
                "assembly": None,
                "name": "Building",
            },
        },
    }
    assert isinstance(leaves["BUI1.Building.Conditioned floor area (Building)"]["value"], float)


def test_golden_leaves_keep_non_numeric_values_as_they_are() -> None:
    """A string or null KPI value is stored verbatim and compared exactly downstream."""
    raw = {"BUI1": {"General": {"code": _entry("code", "DE.N.SFH.05", "-"), "none": _entry("none", None, "-")}}}
    leaves = golden_leaves(raw)
    assert leaves[key_of("code")]["value"] == "DE.N.SFH.05"
    assert leaves[key_of("none")]["value"] is None


def test_golden_leaves_of_an_empty_collection_is_empty() -> None:
    """An empty KPI tree has no leaves."""
    assert not golden_leaves({})


def test_golden_leaves_refuse_an_entry_written_before_the_source_existed() -> None:
    """Catches a golden being written with a guessed source for an old ``all_kpis.json``."""
    old_entry = {"name": "x", "unit": "kWh", "value": 1.0, "tag": "Battery", "nameOfSourceComponent": "Battery"}
    with pytest.raises(GoldenFormatError, match="carries no 'source'"):
        golden_leaves({"BUI1": {"Battery": {"x": old_entry}}})


def test_golden_leaves_refuse_a_key_that_disagrees_with_its_entry() -> None:
    """Catches a collection whose key was built by hand drifting from its entry (the finder's check)."""
    entry = _entry("x", 1.0, source=KpiSource(member="Battery", name="Battery"), tag=KpiTagEnumClass.BATTERY)
    with pytest.raises(ValueError, match="addresses itself as 'BUI1.Battery.x \\(Battery\\)'"):
        golden_leaves({"BUI1": {"Battery": {"x": entry}}})


def test_golden_leaves_refuse_an_entry_without_a_unit() -> None:
    """Catches a golden storing a KPI whose unit nobody can compare."""
    entry = {"name": "x", "value": 1.0, "tag": "General", "source": None}
    with pytest.raises(GoldenFormatError, match="no unit"):
        golden_leaves({"BUI1": {"General": {"x": entry}}})


# --------------------------------------------------------------------------- #
# read_golden() / load_golden(): every reader of today's goldens refuses anything else
# --------------------------------------------------------------------------- #
def test_read_golden_accepts_the_leaves_it_wrote() -> None:
    """A round trip through JSON reads back the same leaves."""
    leaves = leaf_map(derived("x", 1.0), component("Floor area", "Building", 121.0, unit="m2"))
    assert read_golden(json.loads(json.dumps(leaves)), "test") == leaves


def test_read_golden_refuses_the_old_flat_form_with_the_re_bless_message() -> None:
    """Catches a flat ``dotted key -> value`` golden being read as if it carried a unit and a source."""
    with pytest.raises(GoldenFormatError, match="old flat form.*golden-update workflow with force_rewrite"):
        read_golden({key_of("x"): 1.0}, "test")


def test_read_golden_refuses_a_key_that_disagrees_with_its_fields() -> None:
    """Catches a hand-edited key, or a hand-edited field, keeping its old key."""
    _, leaf = component("Floor area", "Building", 121.0)
    with pytest.raises(GoldenFormatError, match="which address it as 'BUI1.Building.Floor area \\(Building\\)'"):
        read_golden({"BUI1.Building.Floor area": leaf}, "test")


@pytest.mark.parametrize("dropped", ["value", "unit", "building", "tag", "name", "source"])
def test_read_golden_refuses_a_leaf_that_lacks_a_field(dropped: str) -> None:
    """Catches a leaf missing a field passing as a complete one."""
    key, leaf = derived("x", 1.0)
    del leaf[dropped]
    with pytest.raises(GoldenFormatError, match=f"missing fields: \\['{dropped}'\\]"):
        read_golden({key: leaf}, "test")


def test_read_golden_refuses_a_source_with_presentation_fields() -> None:
    """Catches ``display_name`` or ``label`` creeping into the goldens, where they would churn."""
    key, leaf = component("Floor area", "Building", 121.0)
    leaf["source"]["display_name"] = "Building"
    with pytest.raises(GoldenFormatError, match="exactly the fields import, instance, path, member, assembly, name"):
        read_golden({key: leaf}, "test")


def test_a_leaf_source_carries_its_whole_address_path() -> None:
    """Catches a golden dropping the inner steps of a component's path, or reading them back reordered."""
    key, leaf = component("Floor area", "heating-sys-1-Building", 121.0, path=(("heating", "sys-1"), ("hp", None)))

    assert leaf["source"]["path"] == [{"import": "heating", "instance": "sys-1"}, {"import": "hp", "instance": None}]
    assert leaf["source"]["import"] == "heating" and leaf["source"]["instance"] == "sys-1"
    assert read_golden(json.loads(json.dumps({key: leaf})), "test") == {key: leaf}


def test_read_golden_refuses_a_source_without_its_path_as_stale() -> None:
    """Catches a golden written before ``source.path`` existed being read as a site component."""
    key, leaf = component("Floor area", "Building", 121.0)
    del leaf["source"]["path"]
    with pytest.raises(
        GoldenFormatError, match="'source' has no 'path'.*stale.*golden-update workflow with force_rewrite"
    ):
        read_golden({key: leaf}, "test")


@pytest.mark.parametrize(
    "path, message",
    [
        ("heating", "source.path is not a list of address steps"),
        ([{"import": "heating"}], r"source.path\[0\]: an address step must be an object with exactly the keys"),
        ([{"import": "", "instance": None}], r"source.path\[0\]: the address step's import is not a non-empty"),
        ([{"import": "heating", "instance": None}], "are not the outermost step of its path"),
    ],
)
def test_read_golden_refuses_a_malformed_path(path: Any, message: str) -> None:
    """Catches a hand-edited path, or one that contradicts the source's import, passing as an address."""
    key, leaf = component("Floor area", "Building", 121.0)
    leaf["source"]["path"] = path
    with pytest.raises(GoldenFormatError, match=message):
        read_golden({key: leaf}, "test")


def test_read_golden_refuses_a_json_list() -> None:
    """A golden is an object of leaves."""
    with pytest.raises(GoldenFormatError, match="not an object of golden leaves"):
        read_golden([1, 2], "test")


def test_load_golden_names_the_file_it_refuses(tmp_path: Path) -> None:
    """The message names the path, so a CI log points at the file to re-bless."""
    path = tmp_path / "pair.json"
    path.write_text('{"BUI1.General.x": 1.0}')
    with pytest.raises(GoldenFormatError, match=str(path)):
        load_golden(path)
    path.write_text("{")
    with pytest.raises(GoldenFormatError, match="cannot be read as JSON"):
        load_golden(path)


# --------------------------------------------------------------------------- #
# compare()
# --------------------------------------------------------------------------- #
def _messages(deviations: list) -> list[str]:
    return [str(deviation) for deviation in deviations]


def test_compare_exact_match_no_errors() -> None:
    """Identical KPI maps compare without errors."""
    assert not compare("x", general({"a": 1.0}), general({"a": 1.0}))


def test_compare_within_tolerance_no_errors() -> None:
    """A difference within tolerance is not reported."""
    assert not compare("x", general({"a": 1.0 + 1e-12}), general({"a": 1.0}))


def test_compare_outside_tolerance_reports_change() -> None:
    """A difference beyond tolerance is reported with its absolute and relative magnitude."""
    found = compare("x", general({"a": 2.0}), general({"a": 1.0}))
    assert [deviation.kind for deviation in found] == [VALUE]
    msg = str(found[0])
    assert msg.startswith("x: KPI 'BUI1.General.a' changed: ref=1.0 got=2.0")
    assert "abs diff=1" in msg
    assert "rel diff=100.000%" in msg
    assert "tolerance rel=1e-09" in msg


def test_compare_a_changed_unit_is_a_deviation_of_its_own_kind() -> None:
    """Catches a unit change passing the gate because the number stayed the same."""
    found = compare("x", leaf_map(derived("a", 1.0, unit="MWh")), leaf_map(derived("a", 1.0, unit="kWh")))
    assert [(deviation.kind, deviation.key) for deviation in found] == [(UNIT, key_of("a"))]
    assert _messages(found) == ["x: KPI 'BUI1.General.a' changed its unit: ref='kWh' got='MWh'"]


def test_compare_a_changed_value_and_unit_are_two_deviations() -> None:
    """A value change does not hide the unit change of the same KPI, nor the other way round."""
    found = compare("x", leaf_map(derived("a", 2.0, unit="MWh")), leaf_map(derived("a", 1.0, unit="kWh")))
    assert [deviation.kind for deviation in found] == [VALUE, UNIT]


def test_compare_a_changed_source_member_under_the_same_key_is_an_address_deviation() -> None:
    """Catches a component swapped under an unchanged runtime name passing the gate."""
    got = leaf_map(component("Floor area", "Building", 1.0, member="OtherBuilding"))
    ref = leaf_map(component("Floor area", "Building", 1.0))
    found = compare("x", got, ref)
    assert [deviation.kind for deviation in found] == [ADDRESS]
    assert "changed its address fields" in str(found[0])


def test_compare_missing_kpi() -> None:
    """A KPI present in the reference but absent from the run is reported missing."""
    found = compare("x", {}, general({"a": 1.0}))
    assert [deviation.kind for deviation in found] == [MISSING]
    assert _messages(found) == ["x: missing KPI 'BUI1.General.a' in current run"]


def test_compare_new_kpi() -> None:
    """A KPI present in the run but absent from the reference is reported new."""
    found = compare("x", general({"a": 1.0}), {})
    assert [deviation.kind for deviation in found] == [NEW]
    assert _messages(found) == ["x: new KPI 'BUI1.General.a' not in reference (regenerate if intended)"]


def test_compare_missing_precede_new_and_new_sorted() -> None:
    """Missing-KPI messages precede new-KPI ones, and new KPIs are sorted."""
    errs = compare("x", general({"z": 1.0, "b": 2.0}), general({"a": 1.0}))
    assert _messages(errs) == [
        "x: missing KPI 'BUI1.General.a' in current run",
        "x: new KPI 'BUI1.General.b' not in reference (regenerate if intended)",
        "x: new KPI 'BUI1.General.z' not in reference (regenerate if intended)",
    ]


def test_compare_non_numeric_exact() -> None:
    """Non-numeric KPI values are compared for exact equality."""
    assert not compare("x", general({"a": "PEM"}), general({"a": "PEM"}))
    assert _messages(compare("x", general({"a": "AEM"}), general({"a": "PEM"}))) == [
        "x: KPI 'BUI1.General.a' changed: ref='PEM' got='AEM'"
    ]


def test_compare_zero_values_equal() -> None:
    """Two zero KPI values compare equal without a false relative-tolerance hit."""
    assert not compare("x", general({"a": 0.0}), general({"a": 0.0}))


def test_compare_uses_module_tolerance_defaults() -> None:
    """The default tolerance is the tight 1e-9 policy value."""
    assert REL_TOL == 1e-9
    assert ABS_TOL == 0.0
    # A difference just above rel_tol is flagged.
    assert compare("x", general({"a": 1.0 + 1e-6}), general({"a": 1.0}))
