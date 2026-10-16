"""The collection fails when a test carries no job marker of the testing guide's table, or more than one.

``tests/job_markers.py`` checks the rule of ``system_docs/testing.md`` §2 for every collected test, and
``tests/conftest.py`` registers the check. These tests drive the check with stand-in tests -- a node id and
a list of markers, which is all the check reads of a ``pytest.Item`` -- and a stand-in session, so they
need no second pytest run. They also pin that the conftest registers the check, and that its list of job
markers is the guide's table and is declared in ``pytest.ini``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List

import pytest

from tests.job_markers import JobMarkerCheck, JobMarkers

pytestmark = pytest.mark.base


@dataclass
class StandInTest:
    """A collected test as the check sees it: its node id and the names of its markers."""

    nodeid: str
    marker_names: List[str] = field(default_factory=list)

    def iter_markers(self) -> Iterator[pytest.Mark]:
        """Return one ``pytest.Mark`` per marker name, as ``pytest.Item.iter_markers`` does."""
        return iter(pytest.Mark(name, (), {}) for name in self.marker_names)


@dataclass
class StandInSession:
    """A session as the check's hook reads it: the tests the run selected."""

    items: List[StandInTest]


class Guide:
    """Reads the job markers the table of ``system_docs/testing.md`` §2 lists."""

    #: The testing guide.
    PATH: Path = Path(__file__).resolve().parent.parent / "system_docs" / "testing.md"
    #: The header line of the marker table.
    TABLE_HEADER: str = "| Marker | What belongs there | CI job |"

    @classmethod
    def table_markers(cls) -> set:
        """Return every backticked marker in the first column of the table's rows."""
        lines = cls.PATH.read_text(encoding="utf-8").splitlines()
        start = lines.index(cls.TABLE_HEADER) + 2
        names = set()
        for line in lines[start:]:
            if not line.startswith("|"):
                break
            names.update(re.findall(r"`([a-z_0-9]+)`", line.split("|")[1]))
        return names


class TestTheRule:
    """Exactly one job marker passes; none, or two, is a fault that names the test."""

    def test_one_job_marker_beside_a_group_marker_passes(self) -> None:
        """A group marker such as worked_examples is not a second job marker; counting it would refuse such tests."""
        assert not JobMarkers.faults([StandInTest("t.py::a", ["base", "worked_examples", "xdist_group"])])

    def test_a_test_without_a_job_marker_is_named(self) -> None:
        """A test with only a group marker, or none, would run only in 'pytest (others)'."""
        found = JobMarkers.faults([StandInTest("t.py::a", ["worked_examples"]), StandInTest("t.py::b")])

        assert found == ["t.py::a carries no job marker", "t.py::b carries no job marker"]

    def test_a_test_with_two_job_markers_is_named_with_both(self) -> None:
        """A test with two job markers would run in two CI jobs."""
        found = JobMarkers.faults([StandInTest("t.py::a", ["extendedbase", "base", "base"])])

        assert found == ["t.py::a carries 2 job markers: 'base', 'extendedbase'"]

    def test_the_refusal_names_every_offending_test(self) -> None:
        """The error lists all faults at once, not only the first, and leaves the correct test out."""
        tests = [
            StandInTest("t.py::none"),
            StandInTest("t.py::fine", ["base"]),
            StandInTest("t.py::two", ["mpc", "utsp"]),
        ]

        with pytest.raises(pytest.UsageError) as refusal:
            JobMarkers.check(tests)

        message = str(refusal.value)
        assert message.startswith("2 test(s) do not carry exactly one job marker")
        assert "t.py::none carries no job marker" in message
        assert "t.py::two carries 2 job markers: 'mpc', 'utsp'" in message
        assert "t.py::fine" not in message


class TestTheHook:
    """The hook checks the selected and the deselected tests, and the conftest registers it."""

    def test_a_deselected_test_without_a_job_marker_still_fails_the_collection(self) -> None:
        """'pytest -m base' drops an unmarked test; the check must see it anyway, or the rule holds only in 'others'."""
        check = JobMarkerCheck()
        check.pytest_deselected([StandInTest("t.py::dropped")])
        session = StandInSession([StandInTest("t.py::kept", ["base"])])

        with pytest.raises(pytest.UsageError, match=r"t\.py::dropped carries no job marker"):
            check.pytest_collection_finish(session)  # type: ignore[arg-type]  # a stand-in for pytest.Session

    def test_a_correct_collection_passes(self) -> None:
        """A collection in which every test carries one job marker goes through."""
        check = JobMarkerCheck()
        check.pytest_deselected([StandInTest("t.py::dropped", ["extendedbase"])])
        session = StandInSession([StandInTest("t.py::kept", ["base"])])

        check.pytest_collection_finish(session)  # type: ignore[arg-type]  # a stand-in for pytest.Session

    def test_the_conftest_registers_the_check(self, request: pytest.FixtureRequest) -> None:
        """Without the registration in tests/conftest.py nothing would check the rule in a real run."""
        plugin = request.config.pluginmanager.get_plugin(JobMarkerCheck.PLUGIN_NAME)

        assert isinstance(plugin, JobMarkerCheck)


class TestTheList:
    """The check's job markers are the guide's table, and each is declared in pytest.ini."""

    def test_the_list_is_the_guides_table(self) -> None:
        """A marker added to the table but not here would be refused on every test that carries it, and vice versa."""
        assert JobMarkers.NAMES == Guide.table_markers()

    def test_every_job_marker_is_declared(self, request: pytest.FixtureRequest) -> None:
        """A name here that pytest.ini does not declare would be refused by --strict-markers on its first use."""
        declared = {line.split(":", 1)[0].split("(", 1)[0].strip() for line in request.config.getini("markers")}

        assert JobMarkers.NAMES <= declared
