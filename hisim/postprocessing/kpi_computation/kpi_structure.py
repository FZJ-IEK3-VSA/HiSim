# clean

"""Classes to provide the structure for the KPI generation."""
from typing import TYPE_CHECKING, Any, Dict, Optional, Union, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json, LetterCase, config as dataclasses_json_config
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
    CHP = "CHP"
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
    FUEL_METER = "Fuel Meter"
    ELECTRICITY_METER = "Electricity Meter"
    CAR = "Car"
    CAR_BATTERY = "Car Battery"
    ROOFTOP_PV = "Rooftop PV"
    SOLAR_THERMAL = "Solar Thermal"
    STORAGE_DOMESTIC_HOT_WATER = "Storage For Domestic Hot Water"
    STORAGE_HOT_WATER_SPACE_HEATING = "Storage For Space Heating Hot Water"
    WINDTURBINE = "Wind Turbine"
    SMART_DEVICE = "Smart Device"
    # EMS = "Energy Management System"
    ELECTRICITY_GRID = "Electricity Grid"
    THERMAL_GRID = "Thermal Grid"
    COSTS_DISTRICT_GRID = "Costs Of District Grid"
    EMISSIONS_DISTRICT_GRID = "Emissions Of District Grid"
    CONTRACTING = "Contracting"
    GENERIC_HEAT_SOURCE = "Generic Heat Source"  # used in simple_heat_source.py
    GROUND_PROBE = "Ground Probe"
    ELECTRIC_HEATING = "Electric Heating"
    ENERGY_MANAGEMENT_SYSTEM = "Energy Management System"
    DISTRICT_ENERGY_MANAGEMENT_SYSTEM = " District Energy Management System"


@dataclass_json(letter_case=LetterCase.CAMEL)
@dataclass
class KpiEntry:
    """Class for storing one KPI entry.

    Serialized with camelCase keys (``nameOfSourceComponent``): the dicts produced by
    ``to_dict`` are the wire format of the webtool KPI JSON, which predates the repo-wide
    dataclasses_json convention, so the historical spelling is pinned here explicitly.

    Attributes:
        name: Human-readable name of the KPI.
        unit: Unit of the KPI value (e.g. "kWh", "EUR", "kg CO2eq").
        value: Numeric KPI value as a float, or a string for descriptive
            or non-numeric KPIs. May be ``None`` if not yet computed.
        description: Optional human-readable description of the KPI.
        tag: Optional category tag from :class:`KpiTagEnumClass` used to
            group KPIs by component or domain.
        name_of_source_component: Optional name of the component that
            produced this KPI.
    """

    name: str
    unit: str
    value: Optional[Union[float, str]]
    description: Optional[str] = None
    # The tag is written as its enum *value* ("Battery", "General", ...) directly in
    # to_dict(), because the resulting dicts are json.dump'ed as-is by the webtool export.
    tag: Optional[KpiTagEnumClass] = field(
        default=None,
        metadata=dataclasses_json_config(
            encoder=lambda tag: tag.value if tag is not None else None,
            decoder=lambda raw: KpiTagEnumClass(raw) if raw is not None else None,
        ),
    )
    name_of_source_component: Optional[str] = None
    # Optional uncertainty band (cost_spec.md §7.3): `value` is the AVERAGE slot. Additive —
    # legacy KPIs leave these unset during the parallel phase of the lifecycle cost engine.
    value_min: Optional[float] = None
    value_max: Optional[float] = None

    if TYPE_CHECKING:
        # The serialization API is injected at runtime by the @dataclass_json decorator,
        # which mypy deliberately does not resolve (see the mypy.ini note on
        # dataclasses_json); these stubs mirror it for the type checker.
        def to_dict(self) -> Dict[str, Any]:
            """Stub for the dict dump that @dataclass_json injects at runtime."""
            raise NotImplementedError

        @classmethod
        def from_dict(cls, kvs: Any, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=unused-argument
            """Stub for the dict decoder that @dataclass_json injects at runtime."""
            raise NotImplementedError


class KpiHelperClass:
    """Class for providing some helper functions for calculating KPIs."""

    @staticmethod
    def compute_total_energy_from_power_timeseries(power_timeseries_in_watt: pd.Series, time_resolution_in_seconds: float) -> float:
        """Compute total energy in kWh from a power time series in watts.

        Args:
            power_timeseries_in_watt: Power values in watts sampled at a fixed
                time resolution. An empty series yields 0.0.
            time_resolution_in_seconds: Constant time step between samples, in
                seconds.

        Returns:
            The total energy in kilowatt-hours as a float.
        """
        if power_timeseries_in_watt.empty:
            return 0.0

        energy_in_kilowatt_hour = float(power_timeseries_in_watt.sum() * time_resolution_in_seconds / 3.6e6)
        return energy_in_kilowatt_hour

    @staticmethod
    def compute_mean_max_min_values(list_or_pandas_series: Union[List[float], pd.Series]) -> Tuple[float, float, float]:
        """Calculate mean, maximum, and minimum values of a numeric sequence.

        Args:
            list_or_pandas_series: A list or pandas Series of numeric values.

        Returns:
            A tuple ``(mean_value, max_value, min_value)`` of floats.
        """

        # Convert the input to an ndarray once and reuse it. Passing a plain
        # ``list`` to ``np.mean``/``np.max``/``np.min`` would internally call
        # ``np.asarray`` three times, building the same array each time.
        arr = np.asarray(list_or_pandas_series)
        mean_value = float(arr.mean())
        max_value = float(arr.max())
        min_value = float(arr.min())

        return mean_value, max_value, min_value
