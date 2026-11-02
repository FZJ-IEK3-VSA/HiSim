"""The collection guard that keeps the capability-document tests out of the ``base`` job.

:class:`tests.renovisor.conftest.ProbeTier` runs on every collection; these cases feed it stand-in
items, so they check its rule without building anything: a test that reads a probe-set build
carries ``renovisor_probes`` and not ``base``, and only such a test carries ``renovisor_probes``.
"""

from types import SimpleNamespace
from typing import Any, List

import pytest

from tests.renovisor.conftest import ProbeTier


class StandInItem:
    """A collected test reduced to what the guard reads: its id, its fixtures and its markers."""

    @staticmethod
    def make(nodeid: str, fixtures: List[str], markers: List[str]) -> Any:
        """Return an object shaped like a ``pytest.Item`` for :class:`ProbeTier`.

        Args:
            nodeid: The id the guard names in its faults.
            fixtures: The transitive fixture names, as ``item.fixturenames`` lists them.
            markers: The names of the markers the test carries.
        """
        marks = [SimpleNamespace(name=name) for name in markers]
        return SimpleNamespace(nodeid=nodeid, fixturenames=fixtures, iter_markers=lambda: iter(marks))


@pytest.mark.base
class TestTheProbeTier:
    """Both directions of the rule, and the quiet case."""

    def test_a_correctly_marked_session_has_no_faults(self) -> None:
        """A reader marked ``renovisor_probes`` and a non-reader marked ``base`` are both right."""
        items = [
            StandInItem.make("t.py::reads", ["document", "capability_document"], ["renovisor_probes"]),
            StandInItem.make("t.py::plain", ["tmp_path"], ["base"]),
        ]
        assert not ProbeTier.faults(items)
        ProbeTier.check(items)

    def test_a_reader_in_base_is_refused_twice(self) -> None:
        """Carrying ``base`` and lacking the new marker are two faults, and both are named."""
        items = [StandInItem.make("t.py::reads", ["translation_map_page", "capability_document"], ["base"])]

        assert ProbeTier.faults(items) == [
            "t.py::reads reads a probe-set build but carries 'base'",
            "t.py::reads reads a probe-set build but lacks 'renovisor_probes'",
        ]
        with pytest.raises(pytest.UsageError, match="t.py::reads"):
            ProbeTier.check(items)

    def test_the_determinism_build_counts_as_a_read(self) -> None:
        """The second build costs as much as the first, so a test asking for it alone moves too."""
        items = [StandInItem.make("t.py::twice", ["independent_capability_document"], [])]

        assert ProbeTier.faults(items) == ["t.py::twice reads a probe-set build but lacks 'renovisor_probes'"]

    def test_the_marker_on_a_test_that_reads_no_build_is_refused(self) -> None:
        """The job runs in one process; a test that needs no build belongs in ``base``."""
        items = [StandInItem.make("t.py::plain", ["tmp_path"], ["renovisor_probes"])]

        assert ProbeTier.faults(items) == ["t.py::plain carries 'renovisor_probes' but reads no probe-set build"]
