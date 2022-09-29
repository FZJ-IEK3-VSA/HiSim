""" Class for the simulation repository. """
# clean
from typing import Any

from hisim import loadtypes as lt


class SimRepository:

    """ Class for exchanging information across all components. """

    def __init__(self) -> None:
        """ Initializes the SimRepository. """
        self.my_dict: dict[str, Any] = {}
        self.my_dynamic_dict: dict[lt.ComponentType, dict[int, Any]] = {elem: {} for elem in lt.ComponentType}

    def set_entry(self, key: str, entry: Any) -> None:
        """ Sets an entry in the SimRepository. """
        self.my_dict[key] = entry

    def get_entry(self, key: str) -> Any:
        """ Gets an entry from the SimRepository. """
        return self.my_dict[key]

    def exist_entry(self, key: str) -> bool:
        """ Checks if an entry exists. """
        if key in self.my_dict:
            return True
        return False

    def delete_entry(self, key: str) -> None:
        """ Deletes an existing entry. """
        self.my_dict.pop(key)

    def set_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int, entry: Any) -> None:
        """ Sets a dynamic entry. """
        self.my_dynamic_dict[component_type][source_weight] = entry

    def get_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """ Gets a dynmaic entry. """
        component = self.my_dynamic_dict.get(component_type, None)
        if component is None:
            return None
        value = component.get(source_weight, None)
        return value

    def get_dynamic_component_weights(self, component_type: lt.ComponentType) -> list:
        """ Gets weights for dynamic components. """
        return list(self.my_dynamic_dict[component_type].keys())

    def delete_dynamic_entry(self, component_type: lt.ComponentType, source_weight: int) -> Any:
        """ Deletes a dynamic component entry. """
        self.my_dynamic_dict[component_type].pop(source_weight)

    def clear(self):
        """ Clears all dictionaries at the end of the simulation to enable garbage collection and reduce memory consumption. """
        self.my_dict.clear()
        del self.my_dict
        self.my_dynamic_dict.clear()
        del self.my_dynamic_dict
