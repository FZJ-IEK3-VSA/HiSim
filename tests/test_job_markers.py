"""The collection fails when a test carries no job marker of the testing guide's table, or more than one.

``tests/job_markers.py`` checks the rule of ``system_docs/testing.md`` §2 for every collected test, and
``tests/conftest.py`` registers the check. Most tests drive the check with stand-in tests -- a node id and
a list of markers, which is all the check reads of a ``pytest.Item`` -- and a stand-in session. One runs a
real pytest on a throwaway repository, so pytest's own deselection and hook order are covered too. The
rest pin that the conftest registers the check, that its list of job markers is the guide's table, that
``pytest.ini`` declares no marker that is neither a job nor a group marker, and that the jobs of
``.github/workflows/tests.yml`` run the markers the table gives them.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Dict, Iterator, List, Set, Tuple

import pytest
import yaml

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
    """Reads the rows of the marker table of ``system_docs/testing.md`` §2: their markers and their CI job."""

    #: The testing guide.
    PATH: ClassVar[Path] = Path(__file__).resolve().parent.parent / "system_docs" / "testing.md"
    #: The header line of the marker table.
    TABLE_HEADER: ClassVar[str] = "| Marker | What belongs there | CI job |"
    #: How the CI job column of a row run by the catch-all job begins.
    OTHERS_JOB: ClassVar[str] = "`pytest (others)`"

    @classmethod
    def rows(cls) -> List[Tuple[Set[str], str]]:
        """Return each row of the table as the backticked markers of its first column and its CI job column.

        Example: ``[({"base"}, "`pytest (base)`, 4 workers"), ...]``.
        """
        lines = cls.PATH.read_text(encoding="utf-8").splitlines()
        start = lines.index(cls.TABLE_HEADER) + 2
        rows: List[Tuple[Set[str], str]] = []
        for line in lines[start:]:
            if not line.startswith("|"):
                break
            cells = line.split("|")
            rows.append((set(re.findall(r"`([a-z_0-9]+)`", cells[1])), cells[3].strip()))
        return rows

    @classmethod
    def table_markers(cls) -> Set[str]:
        """Return every marker of the table, ``{"base", "extendedbase", ...}``."""
        return {name for markers, _ in cls.rows() for name in markers}

    @classmethod
    def markers_of_others(cls) -> Set[str]:
        """Return the markers whose CI job is the catch-all ``pytest (others)``, ``{"mpc", "hpcharness", ...}``."""
        return {name for markers, job in cls.rows() if job.startswith(cls.OTHERS_JOB) for name in markers}


class Workflow:
    """Reads the ``marker:`` of every job in the matrix of ``.github/workflows/tests.yml``."""

    #: The workflow of the CI of every pull request.
    PATH: ClassVar[Path] = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"
    #: The ``name:`` of the catch-all job, whose marker is a ``not ... and not ...`` expression.
    OTHERS: ClassVar[str] = "others"

    @classmethod
    def markers_by_job(cls) -> Dict[str, str]:
        """Return the marker expression of each matrix entry by its name, ``{"base": "base", ...}``.

        A split marker has several entries of one name, all with the same marker.
        """
        workflow = yaml.safe_load(cls.PATH.read_text(encoding="utf-8"))
        return {entry["name"]: entry["marker"] for entry in workflow["jobs"]["pytest"]["strategy"]["matrix"]["include"]}

    @classmethod
    def dedicated_markers(cls) -> Set[str]:
        """Return the marker of every job but the catch-all, ``{"base", "extendedbase", ...}``."""
        return {marker for name, marker in cls.markers_by_job().items() if name != cls.OTHERS}

    @classmethod
    def excluded_by_others(cls) -> Set[str]:
        """Return the markers the catch-all job's ``not X and not Y`` expression leaves out, ``{"base", ...}``."""
        return set(re.findall(r"\bnot ([a-z_0-9]+)", cls.markers_by_job()[cls.OTHERS]))


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
    """The check's job markers are the guide's table, and pytest.ini declares them and the group markers only."""

    def test_the_list_is_the_guides_table(self) -> None:
        """The check's list of job markers equals the guide's table.

        A marker in the table but not here would refuse every test that carries it as unmarked; one here but not in
        the table would be accepted by the check while the guide does not name it. Either fails here.
        """
        assert JobMarkers.NAMES == Guide.table_markers()

    def test_every_declared_marker_is_a_job_or_a_group_marker(self, request: pytest.FixtureRequest) -> None:
        """Every marker pytest.ini declares is a job marker or a group marker, and every job marker is declared.

        A job marker pytest.ini does not declare would be refused by --strict-markers on its first use; a
        declared marker in neither list would pass beside a job marker uncounted, as if it were a group marker.

        pytest.ini is read itself: ``config.getini("markers")`` also holds the markers of pytest and its plugins.
        """
        ini = configparser.ConfigParser(interpolation=None)
        assert request.config.inipath is not None, "this run reads no pytest.ini"
        ini.read(request.config.inipath, encoding="utf-8")
        lines = ini["pytest"]["markers"].strip().splitlines()
        declared = {line.split(":", 1)[0].split("(", 1)[0].strip() for line in lines}

        assert declared == JobMarkers.NAMES | JobMarkers.GROUP_NAMES


class TestTheWorkflow:
    """The jobs of tests.yml run the job markers the table gives them, and the catch-all job none of the others."""

    def test_pytest_others_excludes_each_job_marker_that_the_table_gives_another_job(self) -> None:
        """The catch-all job excludes exactly the job markers that have a job of their own.

        A job marker that 'pytest (others)' does not exclude runs its tests twice, in its own job and there;
        excluding a marker the table gives to 'pytest (others)' would leave its tests out of CI.
        """
        assert Workflow.excluded_by_others() == JobMarkers.NAMES - Guide.markers_of_others()

    def test_every_other_job_runs_a_job_marker_that_pytest_others_excludes(self) -> None:
        """Every dedicated job runs one of the markers the catch-all job excludes.

        A job running a marker outside the list would run tests the check counts as unmarked, and tests that
        'pytest (others)' runs as well.
        """
        assert Workflow.dedicated_markers() <= Workflow.excluded_by_others()


class ThrowawayRepository:
    """A repository under ``tmp_path`` with the check's module, a conftest registering only the check, and tests."""

    #: The conftest: the check alone, without the HiSim conftest's fixtures and guards.
    CONFTEST: ClassVar[str] = (
        "from tests.job_markers import JobMarkerCheck\n\n\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(JobMarkerCheck(), JobMarkerCheck.PLUGIN_NAME)\n"
    )
    #: The two job markers the tests use.
    PYTEST_INI: ClassVar[str] = "[pytest]\ntestpaths = tests\nmarkers =\n    base: b\n    extendedbase: e\n"
    #: One test with one job marker, one with none and one with two.
    MODULE: ClassVar[str] = (
        "import pytest\n\n\n"
        "@pytest.mark.base\ndef test_kept():\n    pass\n\n\n"
        "def test_unmarked():\n    pass\n\n\n"
        "@pytest.mark.base\n@pytest.mark.extendedbase\ndef test_two():\n    pass\n"
    )

    @classmethod
    def collect(cls, root: Path, *arguments: str) -> subprocess.CompletedProcess:
        """Build the repository under ``root`` and run ``pytest --collect-only`` with ``arguments`` there.

        Args:
            root: An empty directory, ``tmp_path``.
            arguments: Further pytest arguments, for example ``-m base``.

        Returns:
            The finished pytest process, its output captured as text.
        """
        (root / "tests").mkdir()
        shutil.copy(Path(__file__).resolve().parent / "job_markers.py", root / "tests" / "job_markers.py")
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests" / "conftest.py").write_text(cls.CONFTEST, encoding="utf-8")
        (root / "tests" / "test_probe.py").write_text(cls.MODULE, encoding="utf-8")
        (root / "pytest.ini").write_text(cls.PYTEST_INI, encoding="utf-8")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
        environment.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *arguments],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


class TestARealRun:
    """A real pytest run on a throwaway repository fails its collection, naming the offending tests."""

    def test_pytest_minus_m_base_fails_on_the_tests_it_deselects(self, tmp_path: Path) -> None:
        """A real collection with -m base refuses the unmarked and the double-marked test and names both.

        The stand-in tests above call the hooks by hand; this covers pytest's own -m deselection reaching the
        check, so a 'pytest -m base' that dropped an unmarked test without a word would fail here.
        """
        completed = ThrowawayRepository.collect(tmp_path, "-m", "base")

        output = completed.stdout + completed.stderr
        assert completed.returncode == pytest.ExitCode.USAGE_ERROR, output
        assert "tests/test_probe.py::test_unmarked carries no job marker" in output, output
        assert "tests/test_probe.py::test_two carries 2 job markers: 'base', 'extendedbase'" in output, output
        assert "test_kept" not in output, output
