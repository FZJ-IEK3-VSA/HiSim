""" Enum classes to help against string constants.

Guidelines for enum classes:
    1. Write members names extensively, with no abbreviation, i.e., 'Watt' instead of 'W'.
    2. Attributes should follow the International System of Units (SI)
    [https://en.wikipedia.org/wiki/International_System_of_Units], i.e., for power the attribute is 'W'.
    3. Do not use multipliers such as 'Kilowatt'.
        3.1 Exceptions to this rule are: 'Kilometer', 'Kilogram'.
        3.2 In case of an exception, the simple form should be avoided altogether, e.g., given the 'Kilometer'
        is an Unit, then 'Meter' should not be used.

"""
# clean
import enum


@enum.unique
class Locations(str, enum.Enum):

    """To parse locations for climate data."""

    AACHEN = "Aachen"


@enum.unique
class ChargingLocations(str, enum.Enum):

    """To parse locations for EV Charging."""

    AT_HOME = "AtHome"
    AT_WORK = "AtWork"


@enum.unique
class OccupancyProfiles(str, enum.Enum):

    """To parse LPG predefined household strings."""

    CH01 = "CH01"


class BuildingCodes(str, enum.Enum):
    # TODO: Discussed DN & JG: don't use enums for building types. Raise exception in building component if code doesn't exist.

    """To parse predefined house types from tabula."""

    DE_N_SFH_05_GEN_REEX_001_002 = "DE.N.SFH.05.Gen.ReEx.001.002"


@enum.unique
class DisplayNames(str, enum.Enum):

    """For the sankey plotting."""

    ELECTRICITY_OUTPUT = "ElectricityOutput"
    ELECTRICITY_INPUT = "ElectricityInput"


@enum.unique
class Termination(str, enum.Enum):

    """For the simulation status of modular household."""

    SUCCESSFUL = "Sucessful"
    INVESTMENT_EXCEEDED = "InvestmentExceeded"


@enum.unique
class LoadTypes(str, enum.Enum):

    """Load type named constants so that they are the same everywhere and no typos happen."""

    ANY = "Any"

    ELECTRICITY = "Electricity"
    IRRADIANCE = "Irradiance"
    SPEED = "Speed"
    HEATING = "Heating"
    COOLING = "Cooling"

    VOLUME = "Volume"
    TEMPERATURE = "Temperature"
    TIME = "Time"

    # Substance
    GAS = "Gas"
    HYDROGEN = "Hydrogen"
    OXYGEN = "Oxygen"
    WATER = "Water"
    WARM_WATER = "WarmWater"
    DIESEL = "Diesel"
    OIL = "Oil"
    DISTRICTHEATING = "DistrictHeating"

    PRICE = "Price"

    # Controllers:
    ON_OFF = "OnOff"  # encoding: 0 means off and 1 means on
    ACTIVATION = "Activation"


@enum.unique
class Units(str, enum.Enum):

    """Unit Constants."""

    # Unphysical
    ANY = "-"
    PERCENT = "%"

    # Power
    WATT = "W"
    KILOWATT = "kW"
    KWH_PER_TIMESTEP = "kWh per timestep"

    # Power per area
    WATT_PER_SQUARE_METER = "W per square meter"
    WATT_HOUR_PER_SQUARE_METER = "Wh per square meter"

    # Speed
    METER_PER_SECOND = "m/s"

    # Energy
    WATT_HOUR = "Wh"
    KWH = "kWh"

    # Volume
    LITER = "L"

    # Volume per time
    LITER_PER_TIMESTEP = "Liter per timestep"

    # Mass
    KG = "kg"

    # Mass flow
    KG_PER_SEC = "kg/s"

    # Degrees
    CELSIUS = "°C"
    KELVIN = "K"

    # Degrees
    DEGREES = "Degrees"

    # Time
    SECONDS = "s"
    TIMESTEPS = "timesteps"
    YEARS = "years"

    # Cost
    EUR_PER_KWH = "Euros per kWh"
    EURO = "Euro"

    # Binary for controllers
    BINARY = "binary"


@enum.unique
class ComponentType(str, enum.Enum):

    """Component types for use in dynamic controllers."""

    PV = "PV"
    SMART_DEVICE = "SmartDevice"
    SURPLUS_CONTROLLER = "SurplusController"
    PREDICTIVE_CONTROLLER = "PredictiveControllerforSmartDevices"
    HEAT_PUMP = "HeatPump"
    GAS_HEATER = "GasHeater"
    BATTERY = "Battery"
    CAR_BATTERY = "CarBattery"
    FUEL_CELL = "FuelCell"
    ELECTROLYZER = "Electrolyzer"
    CHP = "CHP"
    H2_STORAGE = "H2Storage"
    ELECTRIC_VEHICLE = "ElectricVehicle"
    ELECTRIC_BOILER = "ElectricBoiler"
    BOILER = "Boiler"
    BUFFER = "Buffer"
    HEATERS = [HEAT_PUMP, GAS_HEATER]


@enum.unique
class HeatingSystems(str, enum.Enum):

    """To parse heating systems in simulation inputs."""

    HEAT_PUMP = "HeatPump"
    ELECTRIC_HEATING = "ELectricHeating"
    OIL_HEATING = "OilHeating"
    GAS_HEATING = "GasHeating"
    DISTRICT_HEATING = "DistrictHeating"


@enum.unique
class InandOutputType(str, enum.Enum):

    """For dynamic controllers."""

    MASS_FLOW = "Massflow"
    CONTROL_SIGNAL = "ControlSignal"
    ELECTRICITY_TARGET = "ElectricityTarget"
    ELECTRICITY_REAL = "ElectricityReal"

    # L3
    LAST_ACTIVATION = "LastActivation"
    LATEST_ACTIVATION = "LatestActivation"
    EARLIEST_ACTIVATION = "EarliestActivation"
    RECOMMENDED_ACTIVATION = "RecommendedActivation"

    # Energy Management System / Postprocessing Options
    ELECTRICITY_PRODUCTION = "ElectricityProduction"
    ELECTRICITY_INJECTION = "ElectricityInjection"
    ELECTRICITY_CONSUMPTION = "ElectricityConsumption"
    ELECTRICITY_CONSUMPTION_EMS_CONTROLLED = "Consumption with EMS control"
    ELECTRICITY_CONSUMPTION_UNCONTROLLED = "Consumption without any EMS control"
    STORAGE_CONTENT = "StorageContent"
    CHARGE_DISCHARGE = "ChargeDischarge"

    # Heating
    HEAT_TO_BUILDING = "HeatToBuilding"
    HEAT_TO_BUFFER = "HeatToBuffer"
    THERMAL_PRODUCTION = "ThermalProduction"
    FUEL_CONSUMPTION = "FuelConsumption"
