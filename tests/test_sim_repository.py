"""Unit tests for the pure in-memory CRUD methods of :class:`SimRepository`.

``SimRepository`` is a small, fully deterministic key/value store used to exchange
data across components during a simulation. Every method only mutates or reads the
two internal dicts (``entries`` and ``dynamic_entries``); none of them touch the
filesystem, the network, or any global state. These tests therefore exercise the
CRUD contract directly and hermetically, with no simulation setup required.

The dynamic-entry dict is pre-seeded in ``__init__`` with an empty sub-dict for
every member of :class:`lt.ComponentType`, so any real enum member (here
``ComponentType.PV``) is a valid key without extra setup.
"""

# clean

from typing import Any

import pytest

from hisim import loadtypes as lt
from hisim.sim_repository import SimRepository

pytestmark = pytest.mark.base

# A representative ComponentType member. ``__init__`` pre-populates an empty
# sub-dict for every ComponentType member, so PV needs no extra setup.
_CT: lt.ComponentType = lt.ComponentType.PV


# --------------------------------------------------------------------------- #
# Plain entries: set_entry / get_entry / entry_exists / delete_entry
# --------------------------------------------------------------------------- #
def test_set_then_get_entry_roundtrips_value() -> None:
    """``set_entry`` stores a value that ``get_entry`` returns unchanged."""
    repo = SimRepository()
    repo.set_entry("foo", 42)
    assert repo.get_entry("foo") == 42


def test_entry_exists_reflects_set_and_delete() -> None:
    """``entry_exists`` is False on a fresh repo, True after set, False after delete."""
    repo = SimRepository()
    assert repo.entry_exists("foo") is False
    repo.set_entry("foo", 1)
    assert repo.entry_exists("foo") is True
    repo.delete_entry("foo")
    assert repo.entry_exists("foo") is False


def test_get_entry_missing_key_raises_keyerror() -> None:
    """``get_entry`` on an absent key raises ``KeyError`` (matches ``dict[key]``)."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.get_entry("missing")


def test_delete_entry_missing_key_raises_keyerror() -> None:
    """``delete_entry`` uses ``dict.pop`` without a default, so a missing key raises."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.delete_entry("missing")


def test_stored_none_is_distinct_from_absent() -> None:
    """Storing ``None`` is a present entry, distinguishable from a missing key."""
    repo = SimRepository()
    repo.set_entry("k", None)
    assert repo.entry_exists("k") is True
    assert repo.get_entry("k") is None


def test_set_entry_overwrites_existing_value() -> None:
    """A second ``set_entry`` for the same key replaces the previous value."""
    repo = SimRepository()
    repo.set_entry("k", 1)
    repo.set_entry("k", 2)
    assert repo.get_entry("k") == 2


# --------------------------------------------------------------------------- #
# Dynamic entries: set_dynamic_entry / get_dynamic_entry /
#                  get_dynamic_component_weights / delete_dynamic_entry
# --------------------------------------------------------------------------- #
def test_set_then_get_dynamic_entry_roundtrips_value() -> None:
    """``set_dynamic_entry`` stores a value that ``get_dynamic_entry`` returns."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 10, "v")
    assert repo.get_dynamic_entry(_CT, 10) == "v"


def test_get_dynamic_entry_unset_weight_returns_none() -> None:
    """An unset ``(component_type, weight)`` pair resolves to ``None``."""
    repo = SimRepository()
    assert repo.get_dynamic_entry(_CT, 999) is None


def test_get_dynamic_entry_unknown_component_type_returns_none() -> None:
    """A component type that is not a pre-seeded key resolves to ``None``.

    ``get_dynamic_entry`` uses ``dict.get(component_type, None)`` and therefore
    returns ``None`` (rather than raising) for an unknown component type.
    """
    repo = SimRepository()
    # ``get_dynamic_entry`` is typed to take a ComponentType; pass a value that is
    # not a pre-seeded key to exercise the ``.get(...) is None`` branch.
    not_a_component_type: Any = "not-a-component-type"
    assert repo.get_dynamic_entry(not_a_component_type, 1) is None


def test_get_dynamic_component_weights_empty_on_fresh_repo() -> None:
    """A freshly constructed repo has no weights for any component type."""
    repo = SimRepository()
    assert not repo.get_dynamic_component_weights(_CT)


def test_get_dynamic_component_weights_preserves_insertion_order() -> None:
    """Weights are returned in insertion order (Python dict ordering)."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 1, "a")
    repo.set_dynamic_entry(_CT, 2, "b")
    assert repo.get_dynamic_component_weights(_CT) == [1, 2]


def test_delete_dynamic_entry_removes_the_entry() -> None:
    """``delete_dynamic_entry`` removes the entry; it is no longer retrievable."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 1, "a")
    repo.set_dynamic_entry(_CT, 2, "b")

    # The current implementation discards the ``dict.pop`` return value, so the
    # method returns ``None`` rather than the stored value. Pin that contract so a
    # future change to the return value is a deliberate, reviewed decision.
    assert repo.delete_dynamic_entry(_CT, 1) is None

    assert repo.get_dynamic_entry(_CT, 1) is None
    assert repo.get_dynamic_component_weights(_CT) == [2]


def test_delete_dynamic_entry_missing_weight_raises_keyerror() -> None:
    """``delete_dynamic_entry`` uses ``dict.pop`` without a default."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.delete_dynamic_entry(_CT, 999)


def test_dynamic_entries_are_independent_per_component_type() -> None:
    """Entries stored under one component type do not leak into another."""
    repo = SimRepository()
    other: lt.ComponentType = lt.ComponentType.BATTERY
    repo.set_dynamic_entry(_CT, 1, "pv-value")
    repo.set_dynamic_entry(other, 1, "battery-value")
    assert repo.get_dynamic_entry(_CT, 1) == "pv-value"
    assert repo.get_dynamic_entry(other, 1) == "battery-value"
    assert repo.get_dynamic_component_weights(_CT) == [1]
    assert repo.get_dynamic_component_weights(other) == [1]


# --------------------------------------------------------------------------- #
# clear()
# --------------------------------------------------------------------------- #
def test_clear_deletes_both_internal_dicts() -> None:
    """``clear`` deletes the ``entries`` and ``dynamic_entries`` attributes."""
    repo = SimRepository()
    repo.set_entry("a", 1)
    repo.set_dynamic_entry(_CT, 1, "x")
    repo.clear()
    assert hasattr(repo, "entries") is False
    assert hasattr(repo, "dynamic_entries") is False


def test_clear_on_fresh_empty_repo_does_not_raise() -> None:
    """``clear`` on a freshly constructed, empty repo is a no-op (no error)."""
    repo = SimRepository()
    repo.clear()
    assert hasattr(repo, "entries") is False
    assert hasattr(repo, "dynamic_entries") is False
