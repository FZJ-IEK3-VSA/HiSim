"""Tests for ``scripts/rename_bead_ids.py``: old bead ids are renamed as whole ids, children follow, re-runs are no-ops.

Every test works on hand-made temporary files with invented ids (``hisim-zz1``, ``hisim-q9``), so the
real rename of the repository never touches this file.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess  # nosec B404 -- runs the local git binary with fixed arguments
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.base


def _load() -> ModuleType:
    """Import the script by path; ``scripts/`` is not a package."""
    name = "rename_bead_ids"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rename_script = _load()


class Fixture:
    """The invented mapping and bead lines the tests run on."""

    MAPPING = 'renamed_on: "2026-10-20"\nids:\n  hisim-zz1: hisim-alpha-zz1\n  hisim-q9: hisim-beta-q9\n'

    @staticmethod
    def renaming() -> Any:
        """Return the invented renaming: ``hisim-zz1`` -> ``hisim-alpha-zz1``, ``hisim-q9`` -> ``hisim-beta-q9``."""
        return rename_script.IdRenaming(
            renamed_on="2026-10-20", ids={"hisim-zz1": "hisim-alpha-zz1", "hisim-q9": "hisim-beta-q9"}
        )

    @staticmethod
    def beads() -> List[Dict[str, Any]]:
        """Return four beads: the epic, a child, a grandchild-carrying bead and an unrelated bead."""
        child_dependency = {"issue_id": "hisim-zz1.2", "depends_on_id": "hisim-zz1", "type": "parent-child"}
        return [
            {"id": "hisim-a00", "title": "Unrelated, mentions hisim-zz1x only", "status": "open", "issue_type": "task"},
            {
                "id": "hisim-q9",
                "title": "Second epic",
                "description": "Sibling of hisim-zz1.",
                "status": "open",
                "issue_type": "epic",
            },
            {
                "id": "hisim-zz1",
                "title": "Epic",
                "description": "Groups hisim-zz1.2",
                "status": "open",
                "issue_type": "epic",
            },
            {
                "id": "hisim-zz1.2",
                "title": "Child",
                "description": "Line one\nhisim-zz1.2.1 follows (pre-hisim-zz1)",
                "status": "open",
                "issue_type": "task",
                "dependencies": [child_dependency],
                "comments": [{"issue_id": "hisim-zz1.2", "text": "See hisim-q9; not hisim-q9a."}],
            },
        ]

    @staticmethod
    def issues_text() -> str:
        """Return the beads as the issues file, one compact JSON line each except the spaced child line."""
        lines = []
        for bead in Fixture.beads():
            spaced = bead["id"] == "hisim-zz1.2"
            separators = (", ", ": ") if spaced else (",", ":")
            lines.append(json.dumps(bead, ensure_ascii=False, separators=separators))
        return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("hisim-zz1", "hisim-alpha-zz1"),
        ("see hisim-zz1.", "see hisim-alpha-zz1."),
        ("(hisim-zz1.18)", "(hisim-alpha-zz1.18)"),
        ("hisim-zz1.4.2,", "hisim-alpha-zz1.4.2,"),
        ("pre-hisim-zz1.6", "pre-hisim-alpha-zz1.6"),
        ("`hisim-q9`", "`hisim-beta-q9`"),
        ("hisim-zz1x", "hisim-zz1x"),
        ("xhisim-zz1", "xhisim-zz1"),
        ("hisim-zz1-other", "hisim-zz1-other"),
        ("hisim-zz1.4a", "hisim-zz1.4a"),
        ("hisim-alpha-zz1.3", "hisim-alpha-zz1.3"),
        ("Renamed from hisim-zz1 on 2026-10-20.", "Renamed from hisim-zz1 on 2026-10-20."),
    ],
)
def test_ids_are_matched_as_whole_ids(text: str, expected: str) -> None:
    """An old id is renamed with its children; longer ids, new ids and the rename note are left alone."""
    renaming = Fixture.renaming()
    assert renaming.rewrite(text, renaming.pattern())[0] == expected


def test_the_issues_file_is_renamed_noted_and_sorted() -> None:
    """Ids, dependencies and comments are renamed, renamed epics are noted once, and the file is re-sorted."""
    new, count = rename_script.IssuesFile.rewrite(Fixture.issues_text(), Fixture.renaming())
    lines = new.splitlines()
    beads = [json.loads(line) for line in lines]
    ids = [bead["id"] for bead in beads]
    assert ids == ["hisim-a00", "hisim-alpha-zz1", "hisim-alpha-zz1.2", "hisim-beta-q9"]
    assert ids == sorted(ids)
    assert new.endswith("\n")
    unrelated = Fixture.issues_text().splitlines()[0]
    assert lines[0] == unrelated
    epic, child, sibling = beads[1], beads[2], beads[3]
    assert epic["notes"] == "Renamed from hisim-zz1 on 2026-10-20."
    assert list(epic) == ["id", "title", "description", "notes", "status", "issue_type"]
    assert sibling["description"] == "Sibling of hisim-alpha-zz1."
    assert child["description"] == "Line one\nhisim-alpha-zz1.2.1 follows (pre-hisim-alpha-zz1)"
    assert child["dependencies"][0] == {
        "issue_id": "hisim-alpha-zz1.2",
        "depends_on_id": "hisim-alpha-zz1",
        "type": "parent-child",
    }
    assert child["comments"][0]["text"] == "See hisim-beta-q9; not hisim-q9a."
    assert "notes" not in child
    assert lines[2] == json.dumps(child, ensure_ascii=False, separators=(", ", ": "))
    assert count == 11


def test_a_second_run_changes_nothing() -> None:
    """Renaming the renamed file again returns it unchanged, with its notes not duplicated."""
    once, _ = rename_script.IssuesFile.rewrite(Fixture.issues_text(), Fixture.renaming())
    twice, count = rename_script.IssuesFile.rewrite(once, Fixture.renaming())
    assert twice == once
    assert count == 0


def test_a_line_that_does_not_round_trip_stops_the_run() -> None:
    """A changed line the known JSON styles cannot reproduce is refused, not reformatted."""
    text = '{"id": "hisim-zz1",  "issue_type": "epic"}\n'
    with pytest.raises(ValueError, match="round-trip"):
        rename_script.IssuesFile.rewrite(text, Fixture.renaming())


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to list tracked files")
def test_a_checkout_is_renamed_once_and_then_checks_clean(tmp_path: Path) -> None:
    """The command renames tracked text files, skips golden data, contract, binaries, and ``--check`` then passes."""
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "renamed_ids.yaml").write_text(Fixture.MAPPING, encoding="utf-8")
    (tmp_path / ".beads" / "issues.jsonl").write_text(Fixture.issues_text(), encoding="utf-8")
    (tmp_path / "golden_references").mkdir()
    (tmp_path / "golden_references" / "kpis.json").write_text('{"note": "hisim-zz1"}\n', encoding="utf-8")
    contract = tmp_path / "hisim" / "renovisor" / "contract"
    contract.mkdir(parents=True)
    (contract / "mockup.yaml").write_text("# vendored, hash-pinned: hisim-zz1.3\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("Parent hisim-zz1, child hisim-zz1.2.\r\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0hisim-zz1")
    (tmp_path / "untracked.md").write_text("hisim-zz1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec B603 B607
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(tmp_path), "add", ".beads", "golden_references", "hisim", "notes.md", "blob.bin"], check=True
    )

    assert rename_script.main(["--repo", str(tmp_path), "--check"]) == 1
    assert rename_script.main(["--repo", str(tmp_path)]) == 0
    assert (tmp_path / "notes.md").read_bytes() == b"Parent hisim-alpha-zz1, child hisim-alpha-zz1.2.\r\n"
    assert (tmp_path / "golden_references" / "kpis.json").read_text(encoding="utf-8") == '{"note": "hisim-zz1"}\n'
    assert (contract / "mockup.yaml").read_text(encoding="utf-8") == "# vendored, hash-pinned: hisim-zz1.3\n"
    assert (tmp_path / "blob.bin").read_bytes() == b"\0hisim-zz1"
    assert (tmp_path / "untracked.md").read_text(encoding="utf-8") == "hisim-zz1\n"
    assert rename_script.main(["--repo", str(tmp_path), "--check"]) == 0

    renamed = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file() and ".git" not in path.parts}
    assert rename_script.main(["--repo", str(tmp_path)]) == 0
    assert {path: path.read_bytes() for path in renamed} == renamed
