"""Collection order of the assembly test contracts (``tests/assemblies/test_library_contracts.py``).

Every check of one sample reads the same isolation run, which the module's run cache makes once and
releases when the next sample's run is asked for. pytest collects test function by test function,
so this hook moves the tests of one sample next to each other, keeping every other order.
"""

from __future__ import annotations

from typing import Any, List

import pytest


def case_of(item: pytest.Item) -> Any:
    """The ``case`` parameter of a contract test (an assembly's sample), or ``None``."""
    case = getattr(getattr(item, "callspec", None), "params", {}).get("case")
    return case if hasattr(case, "order") else None


def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    """Groups the tests parametrized by one ``case`` in the order of the cases, keeping every other order."""
    slots = [index for index, item in enumerate(items) if case_of(item) is not None]
    grouped = sorted((items[index] for index in slots), key=lambda item: case_of(item).order)
    for index, item in zip(slots, grouped):
        items[index] = item
