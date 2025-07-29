# clean

"""Classes to provide the structure for the KPi generation."""
from typing import Optional, Union, List, Tuple
from enum import Enum
from dataclasses import dataclass
from dataclass_wizard import JSONWizard
import pandas as pd
import numpy as np


class KpiTagEnumClass(Enum):
    """Determine KPI tags as enums."""

    GENERAL = "General"
    COSTS = "Costs"
    EMISSIONS = "Emissions"
    BUILDING = "Building"
    AIR_CONDITIONER = "Air Conditioner"
    BATTERY = "Battery"
    HEAT_DISTRIBUTION_SYSTEM = "Heat Distribution System"
    HEATPUMP_SPACE_HEATING = "Heat Pump For Space Heating"
    HEATPUMP_DOMESTIC_HOT_WATER = "Heat Pump For Domestic Hot Water"
    HEATPUMP_SPACE_HEATING_AND_DOMESTIC_HOT_WATER = "Heat Pump For SH and DHW"
    RESIDENTS = "Residents"
    GAS_BOILER = "Gas Boiler"
    OIL_BOILER = "Oil Boiler"
    PELLET_BOILER = "Pellet Boiler"
    WOOD_CHIP_BOILER = "Wood Chip Boiler"
    HYDROGEN_BOILER = "Hydrogen Boiler"
    DISTRICT_HEATING = "District Heating"
    GAS_METER = "Gas Meter"
    HEATING_METER = "Heating Meter"
    ELECTRICITY_METER = "Electricity Meter"
    CAR = "Car"
    CAR_BATTERY = "Car Battery"
    ROOFTOP_PV = "Rooftop PV"
    SOLAR_THERMAL = "Solar Thermal"
    STORAGE_DOMESTIC_HOT_WATER = "Storage For Domestic Hot Water"
    STORAGE_HOT_WATER_SPACE_HEATING = "Storage For Space Heating Hot Water"
    WINDTURBINE = "Wind Turbine"
    SMART_DEVICE = "Smart Device"
    EMS = "Energy Management System"
    ELECTRICITY_GRID = "Electricity Grid"
    THERMAL_GRID = "Thermal Grid"
    COSTS_DISTRICT_GRID = "Costs Of District Grid"
    EMISSIONS_DISTRICT_GRID = "Emissions Of District Grid"
    CONTRACTING = "Contracting"
    GENERIC_HEAT_SOURCE = "Generic Heat Source"  # used in simple_heat_source.py
    GROUND_PROBE = "Ground Probe"
    ELECTRIC_HEATING = "Electric Heating"
    ENERGY_MANAGEMENT_SYSTEM = "Energy Management System"


@dataclass
class KpiEntry(JSONWizard):
    """Class for storing one kpi entry."""

    name: str
    unit: str
    value: Optional[float]
    description: Optional[str] = None
    tag: Optional[KpiTagEnumClass] = None
    name_of_source_component: Optional[str] = None


class KpiHelperClass:
    """Class for providing some helper fucntions for calculating KPIs."""

    @staticmethod
    def compute_total_energy_from_power_timeseries(power_timeseries_in_watt: pd.Series, timeresolution: int) -> float:
        """Computes the energy in kWh from a power timeseries in W."""
        if power_timeseries_in_watt.empty:
            return 0.0

        energy_in_kilowatt_hour = float(power_timeseries_in_watt.sum() * timeresolution / 3.6e6)
        return energy_in_kilowatt_hour

    @staticmethod
    def calc_mean_max_min_value(list_or_pandas_series: Union[List, pd.Series]) -> Tuple[float, float, float]:
        """Calc mean, max and min values from List or pd.Series with numpy."""

        mean_value = float(np.mean(list_or_pandas_series))
        max_value = float(np.max(list_or_pandas_series))
        min_value = float(np.min(list_or_pandas_series))

        return mean_value, max_value, min_value
