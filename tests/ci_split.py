"""Splits one CI test shard over several jobs, the same way in every job, by a committed table of durations.

A shard of ``tests.yml`` whose tests take longer than one job should runs as ``n`` jobs, each passing
``--ci-group k/n`` (``k`` from 1 to ``n``) beside the shard's ``-m <marker>``. Every job collects the
same tests, cuts them into the same ``n`` groups and keeps group ``k``, so every selected test runs in
exactly one of the ``n`` jobs. Example::

    pytest -m extendedbase -n 2 --dist loadgroup --no-loadscope-reorder --ci-group 2/3

**Units.** The groups are made of units, never of single tests: a test's unit is its ``xdist_group``
if it has one, else its module (``tests/test_p3_parity.py``). A module's tests therefore stay in one
job, so its module-scoped fixtures and its stray-file guard window stay whole; a module whose tests
are independent enough to run apart declares that by putting them in ``xdist_group``s of their own
(``tests/test_dhw_chain_time_resolution.py`` does, one group per twin, the two resolutions of a twin
sharing its 60 s reference run). Under ``--dist loadgroup`` the same units decide which tests share
an xdist worker: :meth:`CiSplit.mark_module_groups` gives every test without an ``xdist_group`` its
module's, so loadgroup keeps a module on one worker as loadfile does, and a declared group may go to
another worker than the rest of its module.

**Balance.** The units go heaviest first, each to the group with the least seconds so far (ties to
the lowest group number), weighed by ``tests/ci_durations.json``: ``seconds`` per unit, and
``default_seconds`` for a unit the table does not name yet, so a new test lands in some group
without a table change. Within the kept group the units run heaviest first as well (the job passes
``--no-loadscope-reorder`` so xdist keeps that order instead of sorting by test count), so the
longest unit never starts last. A stale weight only makes one job finish a little later; it never
makes a test run twice or not at all.

**Refreshing the table.** Every split job runs with ``--ci-durations-out``, which writes the seconds
each unit took in that job (setup, call and teardown of its tests) to
``results/ci_durations/<job>[-<worker>].json``; the job uploads them as the artifact
``ci-durations-<job>``. To refresh, download those artifacts of a recent run on main and merge them::

    gh run download <run id> -R FZJ-IEK3-VSA/HiSim -p 'ci-durations-*' -D durations
    python tests/ci_split.py refresh durations/*/*.json

``refresh`` replaces the seconds of every unit the files name, keeps the other entries, and drops
none: an entry no collected test uses any more is reported by ``tests/test_ci_split.py``. Refresh
when a split job's wall time drifts well past the others', not on every change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import pytest


@dataclass(frozen=True)
class CiGroup:
    """One group of a split: the ``k`` of ``--ci-group k/n`` and the ``n``.

    Example: ``CiGroup.parse("2/3")`` is ``CiGroup(index=2, count=3)``, the second of three groups.
    """

    index: int
    count: int

    @classmethod
    def parse(cls, text: str) -> "CiGroup":
        """Reads ``k/n`` with ``1 <= k <= n``.

        Raises:
            ValueError: When the text is not two positive integers ``k/n`` with ``k`` at most ``n``.
        """
        parts = text.split("/")
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            raise ValueError(f"--ci-group takes k/n, such as 2/3, not {text!r}")
        index, count = int(parts[0]), int(parts[1])
        if not 1 <= index <= count:
            raise ValueError(f"--ci-group {text}: k must be between 1 and n")
        return cls(index, count)


class CiSplit:
    """The split of a shard into groups, the units it is made of, and the durations table that weighs them."""

    #: The committed durations table.
    TABLE_PATH: ClassVar[Path] = Path(__file__).with_name("ci_durations.json")
    #: The option that selects one group.
    GROUP_OPTION: ClassVar[str] = "--ci-group"
    #: The option that records the seconds per unit of this run.
    DURATIONS_OPTION: ClassVar[str] = "--ci-durations-out"
    #: The option that writes every selected test's unit and markers instead of splitting (tests/test_ci_split.py).
    PLAN_OPTION: ClassVar[str] = "--ci-split-plan"
    #: The marker whose name is a test's declared unit.
    GROUP_MARKER: ClassVar[str] = "xdist_group"

    @classmethod
    def declared_group(cls, item: pytest.Item) -> Optional[str]:
        """The test's ``xdist_group`` name, or ``None`` when it has none.

        Several ``xdist_group`` markers are joined as pytest-xdist joins them (sorted, with ``_``), so
        the unit is the scope xdist schedules the test in.
        """
        names = set()
        for mark in item.iter_markers(cls.GROUP_MARKER):
            names.add(str(mark.args[0] if mark.args else mark.kwargs.get("name", "default")))
        return "_".join(sorted(names)) if names else None

    @staticmethod
    def module_of(item: pytest.Item) -> str:
        """The test's module as a path relative to the root directory, such as ``tests/test_p3_parity.py``."""
        return Path(str(item.path)).resolve().relative_to(item.config.rootpath.resolve()).as_posix()

    @classmethod
    def unit_of(cls, item: pytest.Item) -> str:
        """The unit a test is split with: its ``xdist_group``, else its module."""
        return cls.declared_group(item) or cls.module_of(item)

    @classmethod
    def load_table(cls, path: Optional[Path] = None) -> Tuple[Dict[str, float], float]:
        """The seconds per unit and the default seconds of the durations table.

        Raises:
            ValueError: When a weight is not a positive number.
        """
        table = json.loads((path or cls.TABLE_PATH).read_text(encoding="utf-8"))
        seconds = {str(unit): float(value) for unit, value in table["seconds"].items()}
        default = float(table["default_seconds"])
        if default <= 0 or any(value <= 0 for value in seconds.values()):
            raise ValueError("tests/ci_durations.json: every weight must be a positive number of seconds")
        return seconds, default

    @staticmethod
    def plan(units: Sequence[str], seconds: Mapping[str, float], default_seconds: float, count: int) -> Dict[str, int]:
        """Assigns every unit to one of ``count`` groups, numbered from 1, heaviest unit first onto the lightest group.

        Example: units ``a`` (100 s), ``b`` (60 s) and ``c`` (50 s) in two groups give ``a`` group 1 and
        ``b`` and ``c`` group 2. Ties of weight keep the order of ``units``, ties of load go to the lower
        group, so the plan depends on nothing but its arguments.

        Args:
            units: The distinct units, in collection order.
            seconds: The weight of each unit the table names.
            default_seconds: The weight of a unit the table does not name.
            count: How many groups.

        Returns:
            The group number of every unit.
        """
        order = sorted(
            range(len(units)), key=lambda position: (-seconds.get(units[position], default_seconds), position)
        )
        loads = [0.0] * count
        assignment: Dict[str, int] = {}
        for position in order:
            unit = units[position]
            lightest = min(range(count), key=lambda group: (loads[group], group))
            loads[lightest] += seconds.get(unit, default_seconds)
            assignment[unit] = lightest + 1
        return assignment

    @classmethod
    def select(
        cls, items: List[pytest.Item], group: CiGroup, seconds: Mapping[str, float], default_seconds: float
    ) -> Tuple[List[pytest.Item], List[pytest.Item]]:
        """Splits the selected tests into the kept group, heaviest unit first, and the deselected rest.

        Within the kept group the units are ordered by weight, heaviest first, and the tests of one unit
        keep their collection order.
        """
        units = [cls.unit_of(item) for item in items]
        distinct = list(dict.fromkeys(units))
        assignment = cls.plan(distinct, seconds, default_seconds, group.count)
        kept = [(item, unit) for item, unit in zip(items, units) if assignment[unit] == group.index]
        deselected = [item for item, unit in zip(items, units) if assignment[unit] != group.index]
        first_seen = {unit: position for position, unit in enumerate(distinct)}
        kept.sort(key=lambda pair: (-seconds.get(pair[1], default_seconds), first_seen[pair[1]]))
        return [item for item, _ in kept], deselected

    @staticmethod
    def loadgroup_active(option: Any) -> bool:
        """Whether this process collects for ``--dist loadgroup``: on an xdist worker, or in the controller.

        Example: a worker of ``pytest -n 2 --dist loadgroup`` sees ``dist == "no"`` and ``loadgroup is True``
        (xdist resets ``dist`` on its workers), the controller ``dist == "loadgroup"``; ``--dist loadfile``
        gives neither.
        """
        return bool(getattr(option, "loadgroup", False)) or getattr(option, "dist", None) == "loadgroup"

    @classmethod
    def mark_module_groups(cls, items: List[pytest.Item]) -> None:
        """Gives every test without an ``xdist_group`` its module's, so ``--dist loadgroup`` keeps modules whole.

        loadgroup schedules a test without a group by its class rather than its module; with this, its
        scheduling unit is the module, as under loadfile, except for the tests that declare a group.
        """
        for item in items:
            if cls.declared_group(item) is None:
                item.add_marker(pytest.mark.xdist_group(cls.module_of(item)))


class CiSplitPlugin:
    """The pytest hooks of the split, registered by ``tests/conftest.py``."""

    def __init__(self, config: pytest.Config) -> None:
        """Reads the split's options; does nothing for a run that passes none of them."""
        self.config = config
        text = config.getoption(CiSplit.GROUP_OPTION)
        self.group = CiGroup.parse(text) if text else None
        self.durations_out: Optional[str] = config.getoption(CiSplit.DURATIONS_OPTION)
        self.plan_out: Optional[str] = config.getoption(CiSplit.PLAN_OPTION)
        self.seconds_per_unit: Dict[str, float] = defaultdict(float)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: List[pytest.Item]) -> None:
        """Under ``--dist loadgroup``, gives every test without a group its module's, before pytest-xdist reads them.

        On an xdist worker, where the tests are collected, xdist has set ``dist`` to ``no`` and kept the
        mode as ``loadgroup``; in a run without workers the mode is ``dist`` itself.
        """
        if CiSplit.loadgroup_active(config.option):
            CiSplit.mark_module_groups(items)

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Writes the plan file of ``--ci-split-plan``: every selected test's node id, unit and markers."""
        if self.plan_out:
            rows = [
                {
                    "nodeid": item.nodeid,
                    "unit": CiSplit.unit_of(item),
                    "markers": sorted({mark.name for mark in item.iter_markers()}),
                }
                for item in session.items
            ]
            Path(self.plan_out).write_text(json.dumps(rows, indent=1), encoding="utf-8")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
        """Adds the wall time of the test's setup, call and teardown to its unit's, for ``--ci-durations-out``."""
        start = time.perf_counter()
        yield
        if self.durations_out:
            self.seconds_per_unit[CiSplit.unit_of(item)] += time.perf_counter() - start

    def pytest_sessionfinish(self) -> None:
        """Writes the seconds per unit of ``--ci-durations-out``, one file per xdist worker."""
        if not self.durations_out or not self.seconds_per_unit:
            return
        path = Path(self.durations_out)
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        if worker:
            path = path.with_name(f"{path.stem}-{worker}{path.suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        rounded = {unit: round(value, 1) for unit, value in sorted(self.seconds_per_unit.items())}
        path.write_text(json.dumps(rounded, indent=1) + "\n", encoding="utf-8")


def add_options(parser: pytest.Parser) -> None:
    """Declares the split's options."""
    group = parser.getgroup("ci-split", "the split of a CI shard over several jobs (tests/ci_split.py)")
    group.addoption(
        CiSplit.GROUP_OPTION,
        metavar="K/N",
        default=None,
        help="keep only group K of N of the selected tests, cut by tests/ci_durations.json",
    )
    group.addoption(
        CiSplit.DURATIONS_OPTION,
        metavar="PATH",
        default=None,
        help="write the seconds each unit (xdist_group, else module) took to PATH, one file per xdist worker",
    )
    group.addoption(
        CiSplit.PLAN_OPTION,
        metavar="PATH",
        default=None,
        help="write every selected test's node id, unit and markers to PATH (for tests/test_ci_split.py)",
    )


def select_group(config: pytest.Config, items: List[pytest.Item]) -> None:
    """Keeps the ``--ci-group`` of the selected tests and reports the rest as deselected; a no-op without it.

    Runs after pytest's ``-m`` selection, so the groups are cut from the shard's own tests.
    """
    text = config.getoption(CiSplit.GROUP_OPTION)
    if not text:
        return
    seconds, default_seconds = CiSplit.load_table()
    kept, deselected = CiSplit.select(items, CiGroup.parse(text), seconds, default_seconds)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = kept


def refresh(files: Sequence[Path], table_path: Path = CiSplit.TABLE_PATH) -> Dict[str, float]:
    """Replaces the table's seconds of every unit the ``--ci-durations-out`` files name, keeping the other entries.

    A unit runs in one process of one job, so a unit named by two files was measured twice (two runs
    downloaded together, say); the larger figure is kept.

    Returns:
        The refreshed seconds per unit, as written.
    """
    table = json.loads(table_path.read_text(encoding="utf-8"))
    measured: Dict[str, float] = {}
    for file in files:
        for unit, value in json.loads(Path(file).read_text(encoding="utf-8")).items():
            measured[unit] = max(measured.get(unit, 0.0), float(value))
    seconds = dict(table["seconds"])
    seconds.update({unit: max(1, round(value)) for unit, value in measured.items()})
    refreshed: Dict[str, float] = dict(sorted(seconds.items()))
    table["seconds"] = refreshed
    table_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    return refreshed


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python tests/ci_split.py refresh FILE...``: merges measured seconds into tests/ci_durations.json."""
    parser = argparse.ArgumentParser(description="Maintains tests/ci_durations.json, the weights of the CI split.")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh_parser = commands.add_parser("refresh", help="merge --ci-durations-out files into the table")
    refresh_parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    written = refresh(args.files)
    print(f"{CiSplit.TABLE_PATH}: {len(written)} units")
    return 0


if __name__ == "__main__":
    sys.exit(main())
