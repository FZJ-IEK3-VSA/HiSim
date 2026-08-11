""" Class for the simulation repository. """
# clean
from __future__ import annotations
from typing import Any
import enum

from hisim import loadtypes as lt
from hisim import log

class SimRepository:

    """Class for exchanging information across all components."""

    def __init__(self) -> None:
        """Initializes the SimRepository."""
        self.entries: dict[SimRepositoryKeyEnum, Any] = {}
        self.dynamic_entries: dict[lt.ComponentType, dict[int, Any]] = {component_type: {} for component_type in lt.ComponentType}

    def set_entry(self, key: SimRepositoryKeyEnum, entry: Any) -> None:
        """Stores a value in the repository under the given key.

        Args:
            key: The lookup key for the entry.
            entry: The value to store.
        """
        self.entries[key] = entry

    def get_entry(self, key: SimRepositoryKeyEnum) -> Any:
        """Retrieves the value stored under the given key.

        Args:
            key: The lookup key for the entry.

        Returns:
            The stored value.

        Raises:
            KeyError: If no entry exists for the given key.
        """
        return self.entries[key]

    def entry_exists(self, key: SimRepositoryKeyEnum) -> bool:
        """Checks whether an entry exists for the given key.

        Args:
            key: The lookup key to check.

        Returns:
            True if an entry exists, False otherwise.
        """
        return key in self.entries

    def delete_entry(self, key: SimRepositoryKeyEnum) -> None:
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
        """
        if self.entries:
            self.entries.clear()
        else:
            log.warning("Sim repo entries were already empty. Your clear() function was unnecessary. " + str(self.entries))
        if self.dynamic_entries:
            self.dynamic_entries.clear()
        else:
            log.warning("Sim repo dynamic entries were already empty. Your clear() function was unnecessary. " + str(self.dynamic_entries))


class SimRepositoryKeyEnum(enum.Enum):
    """Class for setting dictionary keys in the simulation repository."""

    NUMBEROFAPARTMENTS = 1
    WATERMASSFLOWRATEOFHEATGENERATOR = 2
    MAXTHERMALBUILDINGDEMAND = 3
    SETHEATINGTEMPERATUREFORWATERSTORAGE = 4
    SETCOOLINGTEMPERATUREFORWATERSTORAGE = 5
    LOCATION = 6
    RESULT_SCENARIO_NAME = 7
    THERMALTRANSMISSIONCOEFFICIENTGLAZING = 8
    THERMALTRANSMISSIONSURFACEINDOORAIR = 9
    THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM = 10
    THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS = 11
    THERMALTRANSMISSIONCOEFFICIENTVENTILLATION = 12
    THERMALCAPACITYENVELOPE = 13
    PREDICTIVE = 14
    PREDICTIONHORIZON = 15
    PVINCLUDED = 16
    PVPEAKPOWER = 17
    SMARTDEVICESINCLUDED = 18
    BATTERYINCLUDED = 19
    MPCBATTERYCAPACITY = 20
    COEFFICIENT_OF_PERFORMANCE_HEATING = 21
    ENERGY_EFFICIENY_RATIO_COOLING = 22
    WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST = 23
    HEATFLUXTHERMALMASSNODEFORECAST = 24
    HEATFLUXSURFACENODEFORECAST = 25
    HEATFLUXINDOORAIRNODEFORECAST = 26
    PVFORECASTYEARLY = 28
    MAXIMUMBATTERYCAPACITY = 29
    MINIMUMBATTERYCAPACITY = 30
    MAXIMALCHARGINGPOWER = 31
    MAXIMALDISCHARGINGPOWER = 32
    BATTERYEFFICIENCY = 33
    INVERTEREFFICIENCY = 34
    PRICEPURCHASEFORECAST24H = 35
    PRICEINJECTIONFORECAST24H = 36
    WEATHERALTITUDEYEARLYFORECAST = 37
    WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST = 38
    WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST = 39
    WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST = 40
    WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST = 41
    WEATHERAZIMUTHYEARLYFORECAST = 42
    WEATHERAPPARENTZENITHYEARLYFORECAST = 43
    HEATINGBYRESIDENTSYEARLYFORECAST = 44
    WEATHERWINDSPEEDYEARLYFORECAST = 45
    WEATHERPRESSUREYEARLYFORECAST = 46
    WEATHERTEMPERATUREOUTSIDE24HFORECAST = 47
    WEATHERLOCATIONDICT = 48
    DESCRIPTION = 49
    OCCUPANCYELECTRICITYDEMAND24HFORECAST = 50
    TESTENTRY = 51
