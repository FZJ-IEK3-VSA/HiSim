"""

Guidelines for enum classes:
    1. Write members names extensively, with no abbreviation, i.e., 'Watt' instead of 'W'.
    2. Attributes should follow the International System of Units (SI) [https://en.wikipedia.org/wiki/International_System_of_Units], i.e., for power the attribute is 'W'.
    3. Do not use multipliers such as 'Kilowatt'.
        3.1 Exceptions to this rule are: 'Kilometer', 'Kilogram'.
        3.2 In case of an exception, the simple form should be avoided altogether, e.g., given the 'Kilometer' is an Unit, then 'Meter' should not be used.

"""

import enum

@enum.unique
class DisplayNames(str, enum.Enum):
    ElectricityOutput = "ElectricityOutput"
    ElectricityInput = "ElectricityInput"

@enum.unique
class LoadTypes(str, enum.Enum):
    Any = "Any"

    Electricity = "Electricity"
    Irradiance = "Irradiance"
    Speed = "Speed"
    Heating = "Heating"
    Cooling = "Cooling"

    Volume = "Volume"
    Temperature = "Temperature"
    Time = "Time"

    # Substance
    Gas = "Gas"
    Hydrogen = "Hydrogen"
    Oxygen = "Oxygen"
    Water = "Water"
    WarmWater = "WarmWater"
    
    Price = "Price"
    
    #Controllers:
    OnOff = "OnOff" #encoding: 0 means off and 1 means on
    Activation = 'Activation'
    
@enum.unique
class Units(str, enum.Enum):
    # Unphysical
    Any = "-"
    Percent = "%"

    # Power
    Watt = "W"
    kW = "kW"
    kWh_per_timestep = "kWh per timestep"

    # Power per area
    Wm2 = "W per square meter"
    Whm2 = "Wh per square meter"

    # Speed
    MeterPerSecond = "m/s"

    # Energy
    Wh = "Wh"
    kWh = "kWh"

    # Volume
    Liter = "L"

    # Volume per time
    l_per_timestep = "Liter per timestep"

    # Mass
    kg = "kg"

    # Mass flow
    kg_per_sec = "kg/s"

    # Degrees
    Celsius = "°C"
    Kelvin = 'K'

    # Degrees
    Degrees = "Degrees"

    # Time
    Seconds = "s"
    timesteps = 'timesteps'
    
    # Cost
    c_per_kWh = "Cents per kWh"
    
    #binary for controllers
    binary = 'binary'

@enum.unique
class ComponentType(str, enum.Enum):
    # Unphysical
    PV = "PV"
    SmartDevice = "SmartDevice"
    HeatPump = "HeatPump"
    GasHeater = "GasHeater"
    Boiler = "Boiler"
    Battery = "Battery"
    FuelCell = "FuelCell"
    Heaters = [HeatPump, GasHeater]
    
@enum.unique
class InandOutputType(str, enum.Enum):
    Massflow = "Massflow"
    ControlSignal = "ControlSignal"
    ElectricityTarget = "ElectricityTarget"
    ElectricityReal = "ElectricityReal"
    
    #L3
    LastActivation = "LastActivation"
    LatestActivation = "LatestActivation"
    EarliestActivation = "EarliestActivation"
    RecommendedActivation = "RecommendedActivation"
    
    #Energy Management System
    Production = "Production"
    Consumption = "Consumption"
    
    #Heating
    HeatToBuilding = "HeatToBuilding"
    HeatToBuffer = "HeatToBuffer"



