""" Class for the simulation repository. """
# clean
from typing import Any, Dict
import enum

from hisim import loadtypes as lt


class SimRepository:

    """Class for exchanging information across all components."""

    def __init__(self) -> None:
        """Initializes the SimRepository."""
        self.entries: Dict[str, Any] = {}
        self.dynamic_entries: Dict[lt.ComponentType, Dict[int, Any]] = {component_type: {} for component_type in lt.ComponentType}

    def set_entry(self, key: Any, entry: Any) -> None:
        """Sets an entry in the SimRepository."""
        self.entries[key] = entry

    def get_entry(self, key: Any) -> Any:
        """Gets an entry from the SimRepository."""
        return self.entries[key]

    def entry_exists(self, key: Any) -> bool:
        """Checks if an entry exists."""
        return key in self.entries

    def delete_entry(self, key: Any) -> None:
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
    DESCRIPTION = 47


SingletonDictKeyEnum = SimRepositoryKeyEnum
