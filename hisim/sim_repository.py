""" Class for the simulation repository. """
# clean
from __future__ import annotations
from typing import Any

from hisim import loadtypes as lt


class SimRepository:

    """Class for exchanging information across all components."""

    def __init__(self) -> None:
        """Initializes the SimRepository."""
        self.entries: dict[str, Any] = {}
        self.dynamic_entries: dict[lt.ComponentType, dict[int, Any]] = {component_type: {} for component_type in lt.ComponentType}

    def set_entry(self, key: str, entry: Any) -> None:
        """Stores a value in the repository under the given key.

        Args:
            key: The lookup key for the entry.
            entry: The value to store.
        """
        self.entries[key] = entry

    def get_entry(self, key: str) -> Any:
        """Retrieves the value stored under the given key.

        Args:
            key: The lookup key for the entry.

        Returns:
            The stored value.

        Raises:
            KeyError: If no entry exists for the given key.
        """
        return self.entries[key]

    def entry_exists(self, key: str) -> bool:
        """Checks whether an entry exists for the given key.

        Args:
            key: The lookup key to check.

        Returns:
            True if an entry exists, False otherwise.
        """
        return key in self.entries

    def delete_entry(self, key: str) -> None:
        """Removes the entry stored under the given key.

        Args:
            key: The lookup key for the entry to remove.

        Raises:
            KeyError: If no entry exists for the given key.
        """
        self.entries.pop(key)

    def set_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int, entry: Any) -> None:
        """Stores a value keyed by component type and source weight.

        Args:
            component_type: The component type to store the entry under.
            source_weight: The source weight identifying the specific entry.
            entry: The value to store.
        """
        self.dynamic_entries[component_type][source_weight] = entry

    def get_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """Retrieves a dynamic entry by component type and source weight.

        Args:
            component_type: The component type to look up.
            source_weight: The source weight identifying the specific entry.

        Returns:
            The stored value, or None if no entry exists for the given
            component type or source weight.
        """
        entries_by_weight = self.dynamic_entries.get(component_type, None)
        if entries_by_weight is None:
            return None
        value = entries_by_weight.get(source_weight, None)
        return value

    def get_dynamic_source_weights(self, component_type: lt.ComponentType) -> list[int]:
        """Lists all source weights that have entries for the given component type.

        Args:
            component_type: The component type to look up.

        Returns:
            A list of source weights with stored entries.
        """
        return list(self.dynamic_entries[component_type].keys())

    def delete_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> None:
        """Removes a dynamic entry identified by component type and source weight.

        Args:
            component_type: The component type of the entry to remove.
            source_weight: The source weight identifying the specific entry.

        Raises:
            KeyError: If no entry exists for the given component type or
                source weight.
        """
        self.dynamic_entries[component_type].pop(source_weight)

    def clear(self) -> None:
        """Clears all dictionaries at the end of the simulation to enable garbage collection and reduce memory consumption.

        Using `del` (instead of in-place `.clear()` or reassignment to `{}`) is
        intentional: it removes the attribute reference so Python can reclaim the
        memory for the old dict objects as quickly as possible, which is important
        after a large simulation run.
        """
        self.entries.clear()
        del self.entries
        self.dynamic_entries.clear()
        del self.dynamic_entries
