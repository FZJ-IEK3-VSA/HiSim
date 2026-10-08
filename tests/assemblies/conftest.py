"""The test tier of every test in this directory, and the collection order of the assembly test contracts.

**The tier.** Every test under ``tests/assemblies/`` runs in exactly one of two tiers: the
``nightly`` tier (the Latin hypercube samples of ``test_library_contracts.py``) or the
``assemblies`` tier, its own CI job (``pytest (assemblies)`` in ``tests.yml``). The hook below adds
the ``assemblies`` marker to every test here that is not ``nightly``, so a test written without a
marker lands in the right job. A test here that carries ``base``, ``extendedbase`` or
``extendedbase2`` stops the collection with an error naming it: those markers select other CI jobs,
and the assembly tests need the local LoadProfileGenerator, which only the assemblies job is meant
to provide.

**The order.** Every check of one sample reads the same isolation run, which the contract module's
run cache makes once and releases when the next sample's run is asked for. pytest collects test
function by test function, so the hook moves the tests of one sample next to each other, keeping
every other order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, FrozenSet, List

import pytest


class AssemblyTier:
    """The tier markers of the tests in this directory."""

    #: The marker every test here carries unless it is nightly.
    MARKER: ClassVar[str] = "assemblies"
    #: The other tier a test here may carry instead.
    NIGHTLY: ClassVar[str] = "nightly"
    #: The markers of other CI jobs, which a test here must not carry.
    FORBIDDEN: ClassVar[FrozenSet[str]] = frozenset({"base", "extendedbase", "extendedbase2"})
    #: This directory; the hook sees the items of the whole session and acts on the ones in here.
    DIRECTORY: ClassVar[Path] = Path(__file__).resolve().parent

    @classmethod
    def owns(cls, item: pytest.Item) -> bool:
        """Whether a collected test lives in this directory."""
        return cls.DIRECTORY in Path(str(item.path)).resolve().parents

    @classmethod
    def apply(cls, items: List[pytest.Item]) -> None:
        """Adds the ``assemblies`` marker to every non-nightly test here; refuses a test here with another job's marker.

        Example: a test written with ``@pytest.mark.base`` in ``tests/assemblies/test_x.py`` stops
        the collection with ``tests/assemblies/test_x.py::test_y carries the marker 'base'``.

        Raises:
            pytest.UsageError: When a test here carries ``base``, ``extendedbase`` or ``extendedbase2``.
        """
        wrong: List[str] = []
        for item in items:
            if not cls.owns(item):
                continue
            names = {mark.name for mark in item.iter_markers()}
            for name in sorted(names & cls.FORBIDDEN):
                wrong.append(f"{item.nodeid} carries the marker '{name}'")
            if cls.NIGHTLY not in names and cls.MARKER not in names:
                item.add_marker(cls.MARKER)
        if wrong:
            raise pytest.UsageError(
                "The tests under tests/assemblies/ run in the 'assemblies' tier (or 'nightly'), never in another "
                "CI job's: remove the marker, the conftest adds 'assemblies' by itself. "
                + "; ".join(wrong)
            )


def case_of(item: pytest.Item) -> Any:
    """The ``case`` parameter of a contract test (an assembly's sample), or ``None``."""
    case = getattr(getattr(item, "callspec", None), "params", {}).get("case")
    return case if hasattr(case, "order") else None


def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    """Gives every test here its tier, then groups the tests parametrized by one ``case`` in the order of the cases.

    It runs before pytest's own ``-m`` selection, so ``-m assemblies`` selects the markers it adds.
    """
    AssemblyTier.apply(items)
    slots = [index for index, item in enumerate(items) if case_of(item) is not None]
    grouped = sorted((items[index] for index in slots), key=lambda item: case_of(item).order)
    for index, item in zip(slots, grouped):
        items[index] = item
