"""T-8: the result as JSON, results as one table, refusals as one summary (R7)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from epc.inputs import record
from epc.model.result import EpcResult, RunInfo
from epc.reports import json_report, summary, table
from epc.tests import support

pytestmark = pytest.mark.base


def _document(building: str, extra: Dict[str, float]) -> Dict[str, Any]:
    """The dummy engine's result for a fixture building, with extra indicators of unit ``u``."""
    loaded = record.load(support.FIXTURES / support.CONFIGURED)
    result: EpcResult = support.DummyEngine().compute(record.describe(loaded.buildings[0]))
    for key, value in extra.items():
        result.add_indicator(key, value, "u")
    result.run = RunInfo("record", building, "ZZ", "zz", "edition", "national", "epc", "2026-10-12T00:00:00")
    return result.to_dict()


def test_the_json_file_is_the_result_dictionary(tmp_path: Path) -> None:
    """Every section of R4.2 is written, every indicator with its unit; folders are created."""
    document = _document("building", {})
    path = json_report.write(document, tmp_path / "run" / "building.epc.json")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == document
    assert set(written) == {"schema", "run", "indicators", "terms", "building", "site", "assumptions", "provenance"}
    assert set(written["terms"]) == {"annual", "monthly"}
    assert all(entry["unit"] for entry in written["indicators"].values())


def test_a_non_finite_number_is_not_written(tmp_path: Path) -> None:
    """JSON has no spelling for NaN."""
    with pytest.raises(ValueError):
        json_report.write({"value": float("nan")}, tmp_path / "x.epc.json")
    assert not (tmp_path / "x.epc.json").exists()


def test_the_table_has_every_key_seen_in_first_seen_order(tmp_path: Path) -> None:
    """Leading columns, then each indicator key with its unit; empty where a result lacks the key."""
    first = _document("first", {"only_first": 3.0})
    second = _document("second", {"only_second": 4.0})
    path = table.write([("runs/a", first), ("runs/b", second)], tmp_path / table.FILENAME)
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    keys = list(first["indicators"]) + ["only_second"]
    units = {**{key: entry["unit"] for key, entry in first["indicators"].items()}, "only_second": "u"}
    assert rows[0] == list(table.LEADING_COLUMNS) + [f"{key} [{units[key]}]" for key in keys]
    assert rows[1][:5] == ["runs/a", "first", "ZZ", "zz", "edition"]
    assert rows[1][-2:] == ["3.0", ""]
    assert rows[2][:2] == ["runs/b", "second"]
    assert rows[2][-2:] == ["", "4.0"]
    assert len(rows) == 3


def test_the_summary_states_counts_and_every_refusal(tmp_path: Path) -> None:
    """Date, found/computed/refused, one row per refusal with its id; pipes escaped."""
    refusals = [summary.Refusal("runs/c", "building", "E-06", "factor | 0.7")]
    text = summary.write("2026-10-12", 3, 2, refusals, tmp_path / summary.FILENAME).read_text(encoding="utf-8")
    assert "2026-10-12" in text
    assert "runs found: 3 · computed: 2 · refused: 1" in text
    assert "| runs/c | building | E-06 | factor \\| 0.7 |" in text


def test_a_summary_whose_counts_do_not_add_up_is_refused(tmp_path: Path) -> None:
    """The counts must add up: found = computed + refused (I-11)."""
    with pytest.raises(ValueError, match="I-11"):
        summary.write("2026-10-12", 3, 3, [summary.Refusal("r", "b", "E-06", "m")], tmp_path / summary.FILENAME)
