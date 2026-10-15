"""The RenoVisor builds every test module reads, made once per test session, and the CI job of the tests that read them.

A capability document runs the whole probe set through ``validate`` + ``apply`` + ``translate``,
about a hundred seconds in CI, and the translation map is rendered from one. The tests that read
them share :func:`fixture_capability_document` and :func:`fixture_translation_map_page`; the
tests that claim two builds are identical compare the shared build with the one deliberately
independent second build, :func:`fixture_independent_capability_document`, which is made only
when such a test runs. A test that needs a build under a patch makes its own.

**The marker.** Every test that reads one of these three fixtures, directly or through another
fixture, carries the marker ``renovisor_probes`` and not ``base``. The ``renovisor-probes`` job of
``tests.yml`` runs them in one process, so the document is built once per CI run (twice with the
determinism build); in the ``base`` job each of its four xdist workers that picked up one of these
modules built it again. :class:`ProbeTier` checks the marking at collection time in every run.
"""

from typing import ClassVar, FrozenSet, Iterable, List

import pytest

from hisim.renovisor.capabilities import CapabilityDocument
from hisim.renovisor.map import TranslationMap


@pytest.fixture(scope="session", name="capability_document")
def fixture_capability_document() -> CapabilityDocument:
    """Build the capability document once; every test in the session reads the same probe run."""
    return CapabilityDocument.build()


@pytest.fixture(scope="session", name="independent_capability_document")
def fixture_independent_capability_document() -> CapabilityDocument:
    """A second build of the same state, for the determinism tests and for nothing else."""
    return CapabilityDocument.build()


@pytest.fixture(scope="session", name="translation_map_page")
def fixture_translation_map_page(capability_document: CapabilityDocument) -> str:
    """Render the translation map once, from the shared capability document."""
    return TranslationMap.render(document=capability_document)


class ProbeTier:
    """Keeps the tests that read a probe-set build in the ``renovisor_probes`` job and the others out of it.

    A test reads a build when one of :attr:`FIXTURES` is among its fixtures, counted transitively
    (pytest's ``item.fixturenames``): the module fixture ``document`` of ``test_capabilities.py``
    and ``page`` of ``test_map.py`` read the session builds, so their tests count too.
    """

    #: The marker of the tests that read a build, and the CI job that runs them.
    MARKER: ClassVar[str] = "renovisor_probes"
    #: The marker such a test must not carry: its job runs on four workers and would build the document on each.
    FORBIDDEN: ClassVar[str] = "base"
    #: The session fixtures of this file that build the probe set, or render from such a build.
    FIXTURES: ClassVar[FrozenSet[str]] = frozenset(
        {"capability_document", "independent_capability_document", "translation_map_page"}
    )

    @classmethod
    def reads_a_build(cls, item: pytest.Item) -> bool:
        """Whether a collected test uses one of :attr:`FIXTURES`, directly or through another fixture."""
        return bool(cls.FIXTURES & set(getattr(item, "fixturenames", ())))

    @classmethod
    def faults(cls, items: Iterable[pytest.Item]) -> List[str]:
        """One sentence per test whose markers do not match whether it reads a build.

        Example: a test that asks for ``capability_document`` and is marked ``base`` yields
        ``tests/renovisor/test_x.py::test_y reads a probe-set build but carries 'base'``.

        Args:
            items: The collected tests of the session.

        Returns:
            The faults, empty when every test is marked as it should be.
        """
        found: List[str] = []
        for item in items:
            names = {mark.name for mark in item.iter_markers()}
            if cls.reads_a_build(item):
                if cls.FORBIDDEN in names:
                    found.append(f"{item.nodeid} reads a probe-set build but carries '{cls.FORBIDDEN}'")
                if cls.MARKER not in names:
                    found.append(f"{item.nodeid} reads a probe-set build but lacks '{cls.MARKER}'")
            elif cls.MARKER in names:
                found.append(f"{item.nodeid} carries '{cls.MARKER}' but reads no probe-set build")
        return found

    @classmethod
    def check(cls, items: Iterable[pytest.Item]) -> None:
        """Stops the collection when a test is marked against what it reads.

        Raises:
            pytest.UsageError: When :meth:`faults` finds any, naming every one.
        """
        found = cls.faults(items)
        if found:
            raise pytest.UsageError(
                "A test that reads RenoVisor's capability document or translation map must carry "
                f"'{cls.MARKER}' instead of '{cls.FORBIDDEN}', and only such a test may carry it "
                "(tests/renovisor/conftest.py): " + "; ".join(found)
            )


def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    """Checks the ``renovisor_probes`` marking of the whole session.

    It runs before pytest's own ``-m`` selection, so every shard sees every test and a wrongly
    marked one fails the collection in all of them, not only in the shard that would have run it.
    """
    ProbeTier.check(items)
