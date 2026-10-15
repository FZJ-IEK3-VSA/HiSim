"""The CI split of a test shard over several jobs (``tests/ci_split.py``): every test in exactly one job.

The pure tests check the plan on made-up units; the two tests at the end read the real workflow and
the real collection, so a split shard that drops a group from ``tests.yml``, or a durations table that
names a unit no test has any more, fails here rather than as a test that silently never runs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple

import pytest
import yaml

from tests.ci_split import CiGroup, CiSplit, refresh

pytestmark = pytest.mark.base


class Repository:
    """Where the workflow, the table and the tests live."""

    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]
    WORKFLOW: ClassVar[Path] = ROOT / ".github" / "workflows" / "tests.yml"

    @classmethod
    def split_shards(cls) -> Dict[str, List[CiGroup]]:
        """The groups of every split marker of ``tests.yml``'s matrix, by marker.

        Example: ``{"extendedbase": [CiGroup(1, 3), CiGroup(2, 3), CiGroup(3, 3)], ...}``.
        """
        workflow = yaml.safe_load(cls.WORKFLOW.read_text(encoding="utf-8"))
        shards: Dict[str, List[CiGroup]] = {}
        for entry in workflow["jobs"]["pytest"]["strategy"]["matrix"]["include"]:
            if "group" in entry:
                shards.setdefault(entry["marker"], []).append(CiGroup.parse(entry["group"]))
        return shards


@dataclass
class FakeConfig:
    """The part of a pytest config :class:`CiSplit` reads."""

    rootpath: Path


@dataclass
class FakeItem:
    """A collected test as :class:`CiSplit` sees it: its file, its ``xdist_group`` names and its config."""

    path: Path
    config: FakeConfig
    groups: Tuple[str, ...] = ()
    nodeid: str = ""
    added: List[Any] = field(default_factory=list)

    def iter_markers(self, name: str) -> List[Any]:
        """The item's ``xdist_group`` markers, declared or added."""
        declared = [pytest.mark.xdist_group(group).mark for group in self.groups]
        return [mark for mark in declared + [marker.mark for marker in self.added] if mark.name == name]

    def add_marker(self, marker: Any) -> None:
        """Adds a marker, as ``pytest.Item.add_marker`` does."""
        self.added.append(marker)


def _items(root: Path) -> List[FakeItem]:
    """Seven tests in four modules, one of which declares a group per twin."""
    config = FakeConfig(root)
    return [
        FakeItem(root / "tests" / "test_a.py", config, nodeid="a1"),
        FakeItem(root / "tests" / "test_a.py", config, nodeid="a2"),
        FakeItem(root / "tests" / "test_b.py", config, nodeid="b1"),
        FakeItem(root / "tests" / "test_t.py", config, ("t::one",), nodeid="t1-900"),
        FakeItem(root / "tests" / "test_t.py", config, ("t::one",), nodeid="t1-3600"),
        FakeItem(root / "tests" / "test_t.py", config, ("t::two",), nodeid="t2-900"),
        FakeItem(root / "tests" / "test_c.py", config, nodeid="c1"),
    ]


SECONDS = {"tests/test_a.py": 100.0, "tests/test_b.py": 60.0, "t::one": 50.0, "t::two": 45.0}


def test_the_plan_puts_the_heaviest_unit_first_onto_the_lightest_group() -> None:
    """Catches a plan that ignores the weights: with two groups the 100 s unit stays alone in group 1."""
    plan = CiSplit.plan(["a", "b", "c"], {"a": 100.0, "b": 60.0, "c": 50.0}, 20.0, 2)
    assert plan == {"a": 1, "b": 2, "c": 2}
    assert CiSplit.plan(["new", "a"], {"a": 100.0}, 20.0, 2) == {"a": 1, "new": 2}


@pytest.mark.parametrize("count", [1, 2, 3, 5, 9])
def test_every_test_lands_in_exactly_one_group(tmp_path: Path, count: int) -> None:
    """The jobs of a split together run every selected test once, whatever the number of groups."""
    items = _items(tmp_path)
    kept_per_group = [
        CiSplit.select(list(items), CiGroup(index, count), SECONDS, 20.0)[0]  # type: ignore[arg-type]
        for index in range(1, count + 1)
    ]
    ran = [item.nodeid for kept in kept_per_group for item in kept]
    assert sorted(ran) == sorted(item.nodeid for item in items)


def test_a_declared_group_is_a_unit_and_the_rest_of_its_module_is_not_tied_to_it(tmp_path: Path) -> None:
    """The two resolutions of a twin stay together, and two twins of one module may go to different groups."""
    items = _items(tmp_path)
    groups = {
        item.nodeid: index
        for index in (1, 2, 3, 4)
        for item in CiSplit.select(list(items), CiGroup(index, 4), SECONDS, 20.0)[0]  # type: ignore[arg-type]
    }
    assert groups["t1-900"] == groups["t1-3600"]
    assert groups["a1"] == groups["a2"]
    assert groups["t1-900"] != groups["t2-900"]


def test_the_kept_units_run_heaviest_first_and_keep_their_own_order(tmp_path: Path) -> None:
    """The longest unit of a job starts first, so it does not start last on a busy worker."""
    kept, _ = CiSplit.select(_items(tmp_path), CiGroup(1, 1), SECONDS, 20.0)  # type: ignore[arg-type]
    assert [item.nodeid for item in kept] == ["a1", "a2", "b1", "t1-900", "t1-3600", "t2-900", "c1"]


def test_loadgroup_gets_a_module_group_for_every_test_without_one(tmp_path: Path) -> None:
    """Under loadgroup a test without a group is scheduled by its module, as loadfile does; a declared group is kept."""
    items = _items(tmp_path)
    CiSplit.mark_module_groups(items)  # type: ignore[arg-type]
    assert [CiSplit.unit_of(item) for item in items] == [  # type: ignore[arg-type]
        "tests/test_a.py",
        "tests/test_a.py",
        "tests/test_b.py",
        "t::one",
        "t::one",
        "t::two",
        "tests/test_c.py",
    ]
    assert items[3].added == []


@pytest.mark.parametrize("text", ["0/3", "4/3", "3", "a/b", "1/0"])
def test_a_malformed_group_is_refused(text: str) -> None:
    """A typo in the workflow fails the job instead of running some other slice of the shard."""
    with pytest.raises(ValueError):
        CiGroup.parse(text)


def test_refresh_replaces_the_measured_units_and_keeps_the_others(tmp_path: Path) -> None:
    """Refreshing from one shard's artifacts keeps the other shard's weights."""
    table = tmp_path / "table.json"
    table.write_text(json.dumps({"default_seconds": 20, "seconds": {"x": 5, "y": 7}}), encoding="utf-8")
    measured = tmp_path / "job-gw0.json"
    measured.write_text(json.dumps({"y": 70.4, "z": 0.2}), encoding="utf-8")
    assert refresh([measured], table) == {"x": 5, "y": 70, "z": 1}


def test_every_group_of_a_split_shard_is_a_job_of_the_workflow() -> None:
    """Catches a split shard whose workflow lists fewer jobs than groups: that group's tests would never run."""
    shards = Repository.split_shards()
    assert {"extendedbase", "extendedbase2"} <= set(shards)
    for marker, groups in shards.items():
        counts = {group.count for group in groups}
        assert len(counts) == 1, f"{marker}: the jobs disagree on the number of groups: {sorted(counts)}"
        assert sorted(group.index for group in groups) == list(range(1, counts.pop() + 1)), marker


def test_the_split_shards_cover_every_collected_test_and_the_table_names_only_units_that_exist(tmp_path: Path) -> None:
    """On the real collection: every test of a split shard lands in exactly one non-empty group of its shard.

    And every unit the durations table weighs is the unit of a test of a split shard, so a weight
    whose test was renamed or removed is noticed.
    """
    shards = Repository.split_shards()
    plan = tmp_path / "plan.json"
    expression = " or ".join(f"({marker})" for marker in shards)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            expression,
            "--ci-split-plan",
            str(plan),
            str(Repository.ROOT / "tests"),
        ],
        cwd=Repository.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    rows = json.loads(plan.read_text(encoding="utf-8"))
    seconds, default_seconds = CiSplit.load_table()
    units_seen = set()
    for marker, groups in shards.items():
        tests = [row for row in rows if marker in row["markers"]]
        assert tests, f"no test carries the split marker {marker!r}"
        units = list(dict.fromkeys(row["unit"] for row in tests))
        units_seen.update(units)
        assignment = CiSplit.plan(units, seconds, default_seconds, groups[0].count)
        assert set(assignment) == set(units)
        assert set(assignment.values()) == set(range(1, groups[0].count + 1)), f"{marker}: an empty group"
    stale = sorted(set(seconds) - units_seen)
    assert not stale, f"tests/ci_durations.json weighs units no test of a split shard has: {stale}"
