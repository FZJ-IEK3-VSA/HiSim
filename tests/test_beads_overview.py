"""Tests for ``scripts/beads_overview.py``: the one-page overview sorts beads by the schema in AGENTS.md.

The script's only side effect is calling ``br``; everything after ``fetch`` is a pure function of
the fetched data, so these tests feed a canned fixture and never call ``br``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

import pytest

pytestmark = pytest.mark.base


def _load() -> ModuleType:
    """Import the script by path; ``scripts/`` is not a package."""
    name = "beads_overview"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


overview_script = _load()

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
FRESH = "2026-09-27T09:24:55.802531400Z"
OLD = "2026-09-01T10:00:00Z"


def _bead(bead_id: str, kind: str, priority: int, labels: Any, parent: Any = "hisim-e1", updated: str = FRESH) -> Dict:
    return {
        "id": bead_id,
        "title": f"title of {bead_id}",
        "description": "",
        "issue_type": kind,
        "priority": priority,
        "labels": labels,
        "parent": parent,
        "updated_at": updated,
    }


FIXTURE: Dict[str, Any] = {
    "issues": [
        _bead("hisim-e1", "epic", 2, ["renovisor"], parent=None),
        _bead("hisim-a", "bug", 0, ["renovisor"]),
        _bead("hisim-b", "task", 1, ["economics", "difficulty:hard"]),
        _bead("hisim-c", "question", 2, ["economics", "owner-decision"]),
        dict(
            _bead("hisim-d", "task", 3, ["renovisor", "external"]),
            description="Waits on the contract owner, then the frontend.",
        ),
        dict(_bead("hisim-e", "feature", 3, ["renovisor", "external"]), description="Asked the backend (#40)."),
        _bead("hisim-f", "chore", 3, ["cleanup"], parent=None),
        _bead("hisim-g", "question", 4, ["rust"], updated=OLD),
        _bead("hisim-h", "task", 3, ["components", "ci", "needs-triage"]),
        _bead("hisim-i", "improvement", 3, ["docs"]),
        dict(
            _bead("hisim-j", "task", 2, ["ci", "external"]),
            description="Needs the frontend too.",
            notes="2026-09-28: asked.\nWaits on: org admin (FZJ-IEK3-VSA)",
        ),
    ],
    "epics": [
        {
            "epic": {"id": "hisim-e1", "title": "The epic", "status": "open", "priority": 2},
            "total_children": 12,
            "closed_children": 3,
        },
        {"epic": {"id": "hisim-e0", "title": "Done", "status": "closed", "priority": 2}, "total_children": 1},
    ],
    "closed_total": 7,
}


@pytest.fixture(name="overview")
def fixture_overview() -> Dict[str, Any]:
    """The fixture, built as of NOW."""
    built: Dict[str, Any] = overview_script.build(json.loads(json.dumps(FIXTURE)), NOW)
    return built


def _ids(beads: Any) -> list:
    return [bead.id for bead in beads]


def test_urgent_and_queues(overview: Dict[str, Any]) -> None:
    """P0/P1 come first in priority order; each queue lists exactly its labelled beads."""
    assert _ids(overview["urgent"]) == ["hisim-a", "hisim-b"]
    assert _ids(overview["owner_decision"]) == ["hisim-c"]
    assert _ids(overview["needs_triage"]) == ["hisim-h"]
    # A stated "Waits on:" line wins; otherwise the party is the first one the text names, so hisim-d
    # waits on the contract owner, not the frontend.
    assert {party: _ids(group) for party, group in overview["external"].items()} == {
        "backend": ["hisim-e"],
        "contract owner": ["hisim-d"],
        "org admin (FZJ-IEK3-VSA)": ["hisim-j"],
    }


def test_kind_area_table(overview: Dict[str, Any]) -> None:
    """Beads with several or no area labels are counted apart instead of inventing a column per combination."""
    table = overview["table"]
    assert table[("task", "(several)")] == 1
    assert table[("chore", "(none)")] == 1
    assert table[("bug", "renovisor")] == 1
    assert sum(table.values()) == overview["open_total"] == 11


def test_epics_skip_closed(overview: Dict[str, Any]) -> None:
    """Only open epics are listed, with open = total - closed."""
    epics = overview["epics"]
    assert [(e.id, e.open_children, e.closed_children) for e in epics] == [("hisim-e1", 9, 3)]


def test_gaps(overview: Dict[str, Any]) -> None:
    """Every schema break the guide names is found, and nothing else."""
    gaps = {name: _ids(beads) for name, beads in overview["gaps"].items()}
    assert gaps["not exactly one area label"] == ["hisim-f", "hisim-h"]
    assert gaps["no parent epic"] == ["hisim-f"]
    # hisim-g: a question nobody is asked; hisim-i: not a schema kind. hisim-c waits on the owner, so it fits.
    assert gaps["kind does not fit"] == ["hisim-i", "hisim-g"]
    assert gaps["retired label (human, cleanup)"] == ["hisim-f"]
    assert gaps["not updated for 14 days"] == ["hisim-g"]


def test_render_and_json(overview: Dict[str, Any]) -> None:
    """The page carries every section, and --json is serialisable and agrees with it."""
    page = overview_script.render(overview)
    headings = ("P0 and P1 (2)", "owner-decision) (1)", "external) (3)", "needs-triage) (1)", "kind x area", "Epics")
    for heading in headings:
        assert heading in page
    assert "Beads: 11 open, 7 closed" in page
    machine = json.loads(json.dumps(overview_script.to_json(overview)))
    assert machine["urgent"] == ["hisim-a", "hisim-b"]
    assert machine["epics"] == [{"id": "hisim-e1", "title": "The epic", "open": 9, "closed": 3}]
