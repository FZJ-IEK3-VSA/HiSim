# clean

"""Classes to provide the structure for the KPi generation."""
from typing import Optional
from enum import Enum
from dataclasses import dataclass
from dataclass_wizard import JSONWizard


class KpiTagEnumClass(Enum):

    """Determine KPI tags as enums."""

    GENERAL = "General"
    COSTS_AND_EMISSIONS = "Costs and Emissions"
    BUILDING = "Building"
    BATTERY = "Battery"
    HEAT_DISTRIBUTION_SYSTEM = "Heat Distribution System"
    HEATPUMP_SPACE_HEATING = "Heat Pump For Space Heating"
    HEATPUMP_DOMESTIC_HOT_WATER = "Heat Pump For Domestic Hot Water"
    RESIDENTS = "Residents"
    GAS_HEATER_SPACE_HEATING = "Gas Heater For Space Heating"
    GAS_HEATER_DOMESTIC_HOT_WATER = "Gas Heater For Domestic Hot Water"


@dataclass
class KpiEntry(JSONWizard):

    """Class for storing one kpi entry."""

    name: str
    unit: str
    value: Optional[float]
    description: Optional[str] = None
    tag: Optional[KpiTagEnumClass] = None
