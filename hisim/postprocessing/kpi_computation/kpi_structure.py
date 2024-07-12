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
    HEATDISTRIBUTIONSYSTEM = "Heat Distribution System"
    HEATPUMP_SPACE_HEATING = "Heat Pump For Space Heating"
    HEATPUMP_DOMESTIC_HOT_WATER = "Heat Pump For Domestic Hot Water"
    RESIDENTS = "Residents"


@dataclass
class KpiEntry(JSONWizard):

    """Class for storing one kpi entry."""

    name: str
    unit: str
    value: Optional[float]
    description: Optional[str] = None
    tag: Optional[KpiTagEnumClass] = None
