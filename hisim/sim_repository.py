""" Class for the simulation repository. """
# clean
from typing import Any, Dict

from hisim import loadtypes as lt


class SimRepository:

    """Class for exchanging information across all components."""

    def __init__(self) -> None:
        """Initializes the SimRepository."""
        self.entries: Dict[str, Any] = {}
        self.dynamic_entries: Dict[lt.ComponentType, Dict[int, Any]] = {component_type: {} for component_type in lt.ComponentType}

    def set_entry(self, key: str, entry: Any) -> None:
        """Sets an entry in the SimRepository."""
        self.entries[key] = entry

    def get_entry(self, key: str) -> Any:
        """Gets an entry from the SimRepository."""
        return self.entries[key]

    def entry_exists(self, key: str) -> bool:
        """Checks if an entry exists."""
        return key in self.entries

    def delete_entry(self, key: str) -> None:
        """Deletes an existing entry."""
        self.entries.pop(key)

    def set_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int, entry: Any) -> None:
        """Sets a dynamic entry."""
        self.dynamic_entries[component_type][source_weight] = entry

    def get_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """Gets a dynamic entry."""
        entries_by_weight = self.dynamic_entries.get(component_type, None)
        if entries_by_weight is None:
            return None
        value = entries_by_weight.get(source_weight, None)
        return value

    def get_dynamic_component_weights(self, component_type: lt.ComponentType) -> list[int]:
        """Gets weights for dynamic components."""
        return list(self.dynamic_entries[component_type].keys())

    def delete_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """Deletes a dynamic component entry."""
        self.dynamic_entries[component_type].pop(source_weight)

    def clear(self) -> None:
        """Clears all dictionaries at the end of the simulation to enable garbage collection and reduce memory consumption."""
        self.entries.clear()
        del self.entries
        self.dynamic_entries.clear()
        del self.dynamic_entries
