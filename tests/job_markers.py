"""Fails the collection when a test carries no job marker, or more than one, of the testing guide's table.

Each job of ``.github/workflows/tests.yml`` runs one job marker, except the catch-all
``pytest (others)``, which runs every test that carries none of the other jobs' markers: the tests of
``mpc``, ``hpcharness`` and ``upstream_history``, and an unmarked test. A test's job marker thus decides
where and how often it runs (``system_docs/testing.md`` §2). A test with none runs only in
``pytest (others)`` and is missed by ``pytest -m base``; a test with two can run in two jobs. The rule
"a test carries exactly one job marker of the table" is checked here, for every collected test, in every
run. Example: a test written without a marker stops the collection with
``tests/test_x.py::test_y carries no job marker``.

**When it checks.** Markers are not final when the test functions are collected: ``tests/assemblies/
conftest.py`` adds ``assemblies`` to its tests in ``pytest_collection_modifyitems``, and ``-m``, ``-k``
and ``--ci-group`` (``tests/ci_split.py``) then drop the tests a run does not select. The check therefore
runs in ``pytest_collection_finish``, after every ``pytest_collection_modifyitems`` of every plugin, and
checks the selected tests together with every test reported through ``pytest_deselected``: the whole
collection, with its markers as the run sees them, however the hooks are ordered. A run of
``pytest -m base`` thus fails on an unmarked test too, although it would not run it.

``tests/conftest.py`` registers :class:`JobMarkerCheck` beside the CI split's plugin.
"""

from __future__ import annotations

from typing import ClassVar, FrozenSet, Iterable, List, Protocol, Sequence

import pytest


class MarkedTest(Protocol):
    """What the check reads of a collected test: its node id and its markers, as :class:`pytest.Item` offers."""

    @property
    def nodeid(self) -> str:
        """Return the test's node id, ``tests/test_x.py::TestA::test_b``."""

    def iter_markers(self) -> Iterable[pytest.Mark]:
        """Return the markers of the test, its class and its module (``pytest.Item.iter_markers``)."""


class JobMarkers:
    """The job markers of the table in ``system_docs/testing.md`` §2, and the rule that a test carries one.

    The two group markers carried beside a job marker, ``worked_examples`` and ``xdist_group``
    (:attr:`GROUP_NAMES`), are not job markers and are not counted. Every marker ``pytest.ini`` declares is
    one or the other, which ``tests/test_job_markers.py`` keeps true.
    """

    #: Every job marker of the guide's table. A new CI job's marker goes here, into the table and into pytest.ini.
    NAMES: ClassVar[FrozenSet[str]] = frozenset(
        {
            "base",
            "extendedbase",
            "extendedbase2",
            "system_setups",
            "buildingtest",
            "assemblies",
            "nightly",
            "renovisor_probes",
            "postprocessingoptions",
            "utsp",
            "mpc",
            "hpcharness",
            "upstream_history",
        }
    )
    #: The guide's group markers: they select a group inside a job, are carried beside a job marker, and are
    #: not counted. A marker pytest.ini declares that is in neither set fails ``tests/test_job_markers.py``.
    GROUP_NAMES: ClassVar[FrozenSet[str]] = frozenset({"worked_examples", "xdist_group"})

    @classmethod
    def of(cls, test: MarkedTest) -> List[str]:
        """Return the job markers a test carries, sorted, each once.

        Example: a test marked ``base`` and ``worked_examples`` carries ``["base"]``.
        """
        return sorted({mark.name for mark in test.iter_markers()} & cls.NAMES)

    @classmethod
    def faults(cls, tests: Iterable[MarkedTest]) -> List[str]:
        """Return one sentence per test that carries no job marker or more than one, in the order of the tests.

        Example: ``["tests/test_x.py::test_y carries no job marker",
        "tests/test_x.py::test_z carries 2 job markers: 'base', 'extendedbase'"]``.
        """
        found: List[str] = []
        for test in tests:
            names = cls.of(test)
            if not names:
                found.append(f"{test.nodeid} carries no job marker")
            elif len(names) > 1:
                listed = ", ".join(f"'{name}'" for name in names)
                found.append(f"{test.nodeid} carries {len(names)} job markers: {listed}")
        return found

    @classmethod
    def check(cls, tests: Sequence[MarkedTest]) -> None:
        """Refuse a collection in which a test carries no job marker or more than one.

        Raises:
            pytest.UsageError: naming every such test and its job markers, and the table to choose from.
        """
        found = cls.faults(tests)
        if found:
            raise pytest.UsageError(
                f"{len(found)} test(s) do not carry exactly one job marker of the table in system_docs/testing.md "
                f"§2 ({', '.join(sorted(cls.NAMES))}); a test with none runs only in 'pytest (others)' and is "
                "missed by 'pytest -m base', a test with two can run in two jobs: " + "; ".join(found)
            )


class JobMarkerCheck:
    """The pytest hooks of the check, registered by ``tests/conftest.py``: remember what is deselected, then check."""

    #: The name ``tests/conftest.py`` registers the plugin under.
    PLUGIN_NAME: ClassVar[str] = "hisim_job_markers"

    def __init__(self) -> None:
        """Starts with no deselected tests."""
        self.deselected: List[MarkedTest] = []

    def pytest_deselected(self, items: Sequence[MarkedTest]) -> None:
        """Remembers the tests a run dropped (``-m``, ``-k``, ``--ci-group``), so the check sees them too."""
        self.deselected.extend(items)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Checks every collected test, selected or deselected, once all markers are applied (:class:`JobMarkers`).

        Raises:
            pytest.UsageError: when a test carries no job marker or more than one.
        """
        JobMarkers.check([*session.items, *self.deselected])
