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

import enum


@enum.unique
class DisplayNames(str, enum.Enum):

    """ For the sankey plotting. """

    ELECTRICITY_OUTPUT = "ElectricityOutput"
    ELECTRICITY_INPUT = "ElectricityInput"


@enum.unique
class LoadTypes(str, enum.Enum):

    """ Load type named constants so that they are the same everywhere and no typos happen. """

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

    OIL = 'Oil'
    DISTRICTHEATING = 'DistrictHeating'

    PRICE = "Price"

    # Controllers:
    ON_OFF = "OnOff"  # encoding: 0 means off and 1 means on
    ACTIVATION = 'Activation'


@enum.unique
class Units(str, enum.Enum):

    """ Unit Constants. """

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
    KELVIN = 'K'

    # Degrees
    DEGREES = "Degrees"

    # Time
    SECONDS = "s"
    TIMESTEPS = 'timesteps'

    # Cost
    CENTS_PER_KWH = "Cents per kWh"

    # Binary for controllers
    BINARY = 'binary'


@enum.unique
class ComponentType(str, enum.Enum):

    """ Component types for use in dynamic controllers. """

    PV = "PV"
    SMART_DEVICE = "SmartDevice"
    HEAT_PUMP = "HeatPump"
    GAS_HEATER = "GasHeater"
    BATTERY = "Battery"
    FUEL_CELL = "FuelCell"
    ELECTROLYZER = "Electrolyzer"
    BOILER = "Boiler"
    BUFFER = "Buffer"
    HEATERS = [HEAT_PUMP, GAS_HEATER]


@enum.unique
class InandOutputType(str, enum.Enum):

    """ For dynamic controllers. """

    MASS_FLOW = "Massflow"
    CONTROL_SIGNAL = "ControlSignal"
    ELECTRICITY_TARGET = "ElectricityTarget"
    ELECTRICITY_REAL = "ElectricityReal"

    # L3
    LAST_ACTIVATION = "LastActivation"
    LATEST_ACTIVATION = "LatestActivation"
    EARLIEST_ACTIVATION = "EarliestActivation"
    RECOMMENDED_ACTIVATION = "RecommendedActivation"

    # Energy Management System
    PRODUCTION = "Production"
    CONSUMPTION = "Consumption"

    # Heating
    HEAT_TO_BUILDING = "HeatToBuilding"
    HEAT_TO_BUFFER = "HeatToBuffer"
