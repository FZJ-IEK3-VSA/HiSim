""" Enum classes to help against string constants.

Guidelines for enum classes:
    1. Write members names extensively, with no abbreviation, i.e., 'Watt' instead of 'W'.
    2. Attributes should follow the International System of Units (SI)
       [https://en.wikipedia.org/wiki/International_System_of_Units], i.e., for power the attribute is 'W'.
    3. Do not use multipliers such as 'Kilowatt'.

    Exceptions to this rule are: 'Kilometer', 'Kilogram'.
    In case of an exception, the simple form should be avoided altogether, e.g., given the 'Kilometer'
    is a Unit, then 'Meter' should not be used.

"""
from __future__ import annotations

import enum
import typing


@enum.unique
class Locations(str, enum.Enum):

    """To parse locations for climate data."""

    AACHEN = "Aachen"
    MADRID = "Madrid"
    FR = "FR"


@enum.unique
class ChargingLocations(str, enum.Enum):

    """To parse locations for EV Charging."""

    AT_HOME = "AtHome"
    AT_WORK = "AtWork"


@enum.unique
class OccupancyProfiles(str, enum.Enum):

    """To parse LPG predefined household strings."""

    CHR01 = "CHR01 Couple both at Work"


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

    """Load type named constants so that they are the same everywhere and no typos happen.

    The members mix three orthogonal ideas - physical quantities (TEMPERATURE, PRESSURE),
    media/substances (WARM_WATER, GAS, GREEN_HYDROGEN) and energy domains (ELECTRICITY,
    HEATING) - so without a rule, two authors will label the same port differently and the
    load-type check in ``component_wrapper.connect_inputs`` will reject their wiring.
    Choose the load type of a port by the first matching rule:

    1. **Temperature signals** (unit CELSIUS or KELVIN): always ``TEMPERATURE``, regardless
       of the medium. Consumers of a temperature care about the quantity; the medium is
       evident from the port name, and the dimension lives in the unit.
    2. **Flow rates** (KG_PER_SEC, LITER, ...): the **medium** being transported -
       ``WARM_WATER`` for heating and DHW water loops, ``WATER`` for cold, feed or brine
       water, ``GAS``/``OIL``/``GREEN_HYDROGEN``/``OXYGEN``/``AIR`` for the respective
       fluids. There is deliberately no generic mass-flow quantity type; the dimension is
       already expressed by the unit. Every loop that attaches to one of the water storages
       or to the heat distribution system - including the solar-thermal collector loop - is
       ``WARM_WATER``, while ``WATER`` is reserved for heat-pump source-side circuits
       (brine, ground, air) and for cold or feed water.
    3. **Power and energy** (WATT, WATT_HOUR, ...): the **energy domain** - ``ELECTRICITY``,
       ``HEATING`` for space heat, ``WARM_WATER`` for DHW heat - or the **fuel carrier**
       when the value represents fuel consumption (the generic boiler's energy-demand
       outputs carry ``config.energy_carrier``, so a gas boiler reports GAS and an oil
       boiler OIL).
    4. **Control and state signals** without a physical flow: ``ANY`` for counters,
       accumulators and continuous internal states (cycle counts, states of charge,
       modulation degrees), which connects to any counterpart without triggering the
       load-type check; ``ON_OFF`` where the signal genuinely is a binary on/off flag and
       ``ACTIVATION`` where it is an activation or mode command. For the latter two the
       resulting constraint on the wiring is intended: only another on/off respectively
       activation port may be connected.
    """

    ANY = "Any"

    ELECTRICITY = "Electricity"
    IRRADIANCE = "Irradiance"
    SPEED = "Speed"
    HEATING = "Heating"
    COOLING = "Cooling"

    TEMPERATURE = "Temperature"
    PRESSURE = "Pressure"
    TIME = "Time"
    PRICE = "Price"
    # Substance
    GAS = "Gas"
    AIR = "Air"
    GREEN_HYDROGEN = "Green Hydrogen"
    OXYGEN = "Oxygen"
    WATER = "Water"
    WARM_WATER = "WarmWater"
    DIESEL = "Diesel"
    OIL = "Oil"
    PELLETS = "Pellets"
    WOOD_CHIPS = "WoodChips"
    DISTRICTHEATING = "DistrictHeating"

    # Controllers:
    ON_OFF = "OnOff"  # encoding: 0 means off and 1 means on
    ACTIVATION = "Activation"


@enum.unique
class Units(str, enum.Enum):

    """Unit Constants."""

    # Unphysical
    ANY = "-"
    PERCENT = "%"
    # A dimensionless fraction from 0 to 1, such as a part-load ratio; aggregated as a mean, never summed
    FRACTION = "fraction"

    # Power
    WATT = "W"
    KILOWATT = "kW"
    KWH_PER_TIMESTEP = "kWh per timestep"

    # Power per area
    WATT_PER_METER = "W per meter"
    WATT_PER_SQUARE_METER = "W per square meter"
    WATT_HOUR_PER_SQUARE_METER = "Wh per square meter"

    # Speed
    METER_PER_SECOND = "m/s"

    # Distance
    METER = "m"

    # Area
    SQUARE_METER = "m2"

    # Energy
    WATT_HOUR = "Wh"
    KWH = "kWh"
    JOULE = "J"
    KILOJOULE = "kJ"

    # Volume
    LITER = "L"
    CUBICMETERS = "m3"

    # Volume per time
    LITER_PER_TIMESTEP = "Liter per timestep"
    CUBICMETERS_PER_SECOND = "Cubic meters per second"

    # Mass
    KG = "kg"

    # Mass flow
    KG_PER_SEC = "kg/s"

    # Degrees
    CELSIUS = "°C"
    KELVIN = "K"

    # Degrees
    DEGREES = "Degrees"

    # Pressure
    BAR = "bar"
    PASCAL = "Pa"

    # Time
    SECONDS = "s"
    HOURS = "h"
    TIMESTEPS = "timesteps"
    YEARS = "years"

    # Cost
    EUR_PER_KWH = "Euros per kWh"
    EURO = "Euro"

    # Binary for controllers
    BINARY = "binary"


@enum.unique
class OutputAggregation(str, enum.Enum):
    """How the time steps of one output are combined into a monthly, daily or hourly value and a run total.

    Every component output has one of these (``ComponentOutput.aggregation``). Post-processing
    reads it to form the monthly bar charts, the monthly, daily and hourly result tables, the run
    totals and the values on the network chart. An output that declares nothing gets the rule of
    its unit (:meth:`of_unit`): a rate or a state such as W, °C or a fraction is averaged, an
    amount such as Wh is summed.

    The unit is not enough for every output, so a component declares the aggregation where the
    unit's rule is wrong:

    - a running total or a counter (``CumulativeConsumption`` in Wh, ``CounterOnOff``) is ``LAST``:
      its monthly value is its value at the end of the month, its run total the final value;
    - a stored energy (``ThermalEnergyInStorage`` in Wh) is ``MEAN``: the energy held on average;
    - an on/off flag (``HeatingSeason``, 0 or 1) is ``MEAN``: the share of the time it was on;
    - a state with more than two values (a controller mode such as -1 cooling, 0 off, 1 heating),
      a timer that restarts at every switch, or an angle of the sun is ``NONE``: no monthly
      value or run total of it means anything. Such an output gets no charts, and its cells in
      the aggregated tables are empty (NaN); the per-time-step exports still contain it.

    The KPIs do not read these aggregations; they are computed from the time series.
    """

    #: The sum over the period, for amounts per time step such as an energy in Wh.
    SUM = "sum"
    #: The mean over the period, for rates and states such as a power in W or a temperature.
    MEAN = "mean"
    #: The value of the period's last time step, for running totals and counters.
    LAST = "last"
    #: No aggregate: a monthly value or run total of this output means nothing.
    NONE = "none"

    @classmethod
    def units_averaged_by_default(cls) -> frozenset[Units]:
        """Return the units whose outputs are averaged when they declare no aggregation.

        These are rates and states, which keep their unit when averaged: a power, a temperature,
        a pressure, a fraction. Every other unit (an energy, a volume, a mass, a cost) is summed
        to a total. Example: ``Units.WATT`` is in the set, ``Units.WATT_HOUR`` is not.

        Returns:
            The units averaged by default.
        """
        return frozenset(
            {
                Units.CELSIUS,
                Units.KELVIN,
                Units.ANY,
                Units.METER_PER_SECOND,
                Units.DEGREES,
                Units.WATT,
                Units.KILOWATT,
                Units.WATT_PER_SQUARE_METER,
                Units.KG_PER_SEC,
                Units.PERCENT,
                Units.FRACTION,
                Units.PASCAL,
            }
        )

    @classmethod
    def of_unit(cls, unit: typing.Any) -> "OutputAggregation":
        """Return the aggregation an output of this unit gets when it declares none.

        This is the rule post-processing applied to every output before outputs declared their
        aggregation, so an output that declares nothing is aggregated as before. Example:
        ``of_unit(Units.WATT)`` is ``MEAN``, ``of_unit(Units.WATT_HOUR)`` is ``SUM``.

        Args:
            unit: The output's unit, a :class:`Units` member or its string value.

        Returns:
            ``MEAN`` for the units in :meth:`units_averaged_by_default`, ``SUM`` for all others.
        """
        unit_value = unit.value if isinstance(unit, Units) else unit
        mean_unit_values = {mean_unit.value for mean_unit in cls.units_averaged_by_default()}
        return cls.MEAN if unit_value in mean_unit_values else cls.SUM


@enum.unique
class OutputPostprocessingRules(str, enum.Enum):

    """Rules for postprocessing."""

    DISPLAY_IN_WEBTOOL = "DISPLAY_IN_WEBTOOL"


@enum.unique
class EnergyRole(str, enum.Enum):

    """What an energy port of a component is in that component's energy balance (hisim-energy-sankeys-9uoo.1).

    The balance a component closes is ``sum(IN) - sum(OUT) - sum(LOSS) - sum(STORED_CHANGE) = 0``
    over all its ports, per timestep. A port's value is read with its own sign: a negative ``IN``
    is energy leaving through that port, a negative ``STORED_CHANGE`` is energy the component
    gave up from its store.
    """

    #: Energy entering the component (a fuel burnt, heat received, ambient heat drawn).
    IN = "in"
    #: Useful energy leaving the component to another component or to a consumer.
    OUT = "out"
    #: Energy leaving the component unused, to the environment (a standby loss, a flue loss).
    LOSS = "loss"
    #: Increase of the energy the component holds during the step (negative when it discharges).
    STORED_CHANGE = "stored_change"


@enum.unique
class EnergyBalanceCarrier(str, enum.Enum):

    """The energy carrier an energy port carries, for the balance check and the Sankey (hisim-energy-sankeys-9uoo.1).

    Distinct from :class:`hisim.economics.carriers.EnergyCarrier`, which names what is *priced* at the
    system boundary: this one also names the heat inside the house. Heat is split by what it is used
    for, space heating, domestic hot water and cooling, and a water flow counts by the heat it
    carries, ``m c dT``, never by its mass. ``AMBIENT_HEAT`` and ``SOLAR`` are what the environment
    gives a heat pump or a collector; a port of either carrier is linked to the environment node of
    the same name.
    """

    ELECTRICITY = "electricity"
    NATURAL_GAS = "natural_gas"
    HEATING_OIL = "heating_oil"
    PELLETS = "pellets"
    WOOD_CHIPS = "wood_chips"
    HYDROGEN = "hydrogen"
    DIESEL = "diesel"
    DISTRICT_HEAT = "district_heat"
    SPACE_HEATING_HEAT = "space_heating_heat"
    DOMESTIC_HOT_WATER_HEAT = "domestic_hot_water_heat"
    COOLING = "cooling"
    AMBIENT_HEAT = "ambient_heat"
    SOLAR = "solar"


@enum.unique
class EnvironmentNode(str, enum.Enum):

    """The environment as the balance and the Sankey see it: where energy comes from or goes to outside the house.

    An ``IN`` port of carrier ``AMBIENT_HEAT`` or ``SOLAR`` comes from the node of that name and a
    ``LOSS`` port goes ``OUTDOORS``, unless the port names its node itself.
    """

    AMBIENT_HEAT = "ambient heat"
    SOLAR = "solar"
    OUTDOORS = "outdoors"


@enum.unique
class ComponentType(str, enum.Enum):

    """Component types for use in dynamic controllers."""

    PV = "PV"
    WINDTURBINE = "Windturbine"
    SMART_DEVICE = "SmartDevice"
    SURPLUS_CONTROLLER = "SurplusController"
    SURPLUS_CONTROLLER_DISTRICT = "SurplusControllerDistrict"
    PREDICTIVE_CONTROLLER = "PredictiveControllerforSmartDevices"
    HEAT_PUMP = "HeatPump"
    GAS_HEATER = "GasHeater"
    OIL_HEATER = "OilHeater"
    PELLET_HEATER = "PelletHeater"
    WOOD_CHIP_HEATER = "WoodChipHeater"
    HYDROGEN_HEATER = "HydrogenHeater"
    DISTRICT_HEATING = "DistrictHeating"
    BATTERY = "Battery"
    CAR_BATTERY = "CarBattery"
    CAR = "Car"
    FUEL_CELL = "FuelCell"
    ELECTROLYZER = "Electrolyzer"
    # The grid-side conversion stage in front of a DC load: the step-down transformer and the
    # rectifier are bought, rated and replaced as one unit, so they are one cost subject here.
    TRANSFORMER_AND_RECTIFIER = "TransformerAndRectifier"
    CHP = "CHP"
    H2_STORAGE = "H2Storage"
    ELECTRIC_VEHICLE = "ElectricVehicle"
    ELECTRIC_HEATER = "ElectricHeater"
    ELECTRIC_HEATING_SH = "ElectricBoilerSH"  # Electric heating for space heating
    ELECTRIC_HEATING_DHW = "ElectricBoilerDHW"  # Electric heating for domnestic hot water
    BOILER = "Boiler"
    BUFFER = "Buffer"
    THERMAL_ENERGY_STORAGE = "ThermalEnergyStorage"
    # The two water vessels of a household, told apart because the lifecycle cost engine's
    # existing-asset register matches by asset class: the space-heating buffer goes with the heat
    # generator, the domestic-hot-water cylinder with the hot-water supply, and a measure that
    # replaces one must not look like it replaced the other. THERMAL_ENERGY_STORAGE stays for
    # the legacy CAPEX path and for any vessel that is neither.
    SPACE_HEATING_STORAGE = "SpaceHeatingStorage"
    DOMESTIC_HOT_WATER_STORAGE = "DomesticHotWaterStorage"
    HEATERS = [HEAT_PUMP, GAS_HEATER]
    RESIDENTS = "Residents"
    BUILDINGS = "Buildings"
    SOLAR_THERMAL_SYSTEM = "SolarThermalSystem"
    ELECTRICITY_METER = "ElectricityMeter"
    GAS_METER = "GasMeter"
    ENERGY_MANAGEMENT_SYSTEM = "EnergyManagementSystem"
    HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING = "Floorheating"
    HEAT_DISTRIBUTION_SYSTEM_RADIATOR = "Conventional Radiator"
    HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR = "Low Temperature Radiator"
    NO_HDS = "No HDS"

    # Building envelope measures (lifecycle cost engine, roadmap/cost_spec.md Q7). These are cost
    # subjects sized in m2 of the respective envelope element, the door in m2 of door
    # (VENTILATION_SYSTEM per unit); the building physics sees them only as changed U-values.
    # The taxonomy follows the renovation measure database, element by element: external wall,
    # roof, top floor ceiling, basement ceiling, basement walls, ground-contacting components,
    # windows (review round 3, PR 556).
    WALL_EXTERNAL_INSULATION = "WallExternalInsulation"
    WALL_INTERNAL_INSULATION = "WallInternalInsulation"
    ROOF_INSULATION_BETWEEN_JOISTS = "RoofInsulationBetweenJoists"
    ROOF_INSULATION_OVER_JOISTS = "RoofInsulationOverJoists"
    WARM_ROOF_INSULATION = "WarmRoofInsulation"
    TOP_CEILING_UPPER_INSULATION = "TopCeilingUpperInsulation"
    BASEMENT_CEILING_BOTTOM_INSULATION = "BasementCeilingBottomInsulation"
    BASEMENT_WALLS_INTERNAL_INSULATION = "BasementWallsInternalInsulation"
    GROUND_EXTERNAL_INSULATION = "GroundExternalInsulation"
    WINDOWS_TRIPLE_GLAZED = "WindowsTripleGlazed"
    EXTERIOR_DOOR = "ExteriorDoor"
    AIR_SEALING = "AirSealing"
    VENTILATION_SYSTEM = "VentilationSystem"

    # different heat_pump types
    HEAT_PUMP_BUILDING = "HeatPumpBuilding"  # Heatpump for heating the house
    HEAT_PUMP_DHW = "HeatPumpDHW"  # heatpump for heating domnestic hot water


@enum.unique
class HeatingSystems(str, enum.Enum):

    """To parse heating systems in simulation inputs."""

    HEAT_PUMP = "HeatPump"
    ELECTRIC_HEATING = "ElectricHeating"
    OIL_HEATING = "OilHeating"
    GAS_HEATING = "GasHeating"
    PELLET_HEATING = "PelletHeating"
    WOOD_CHIP_HEATING = "WoodChipHeating"
    DISTRICT_HEATING = "DistrictHeating"
    HYDROGEN_HEATING = "HydrogenHeating"
    GAS_SOLAR_THERMAL = "GasSolarThermal"
    HEAT_PUMP_SOLAR_THERMAL = "HeatPumpSolarThermal"


@enum.unique
class InandOutputType(str, enum.Enum):

    """For dynamic controllers."""

    MASS_FLOW = "Massflow"
    CONTROL_SIGNAL = "ControlSignal"
    ELECTRICITY_TARGET = "ElectricityTarget"

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
    #: The increase of the energy a storage holds over the step, in Wh; negative while it gives energy up. Summed
    #: over a run it is the stored energy at the end minus the stored energy at the start, which a storage's losses
    #: subtract from what was charged minus what was discharged.
    STORED_ENERGY_INCREASE = "StoredEnergyIncrease"
    #: The largest power a storage can take in the current step, given its state at the step's start.
    MAXIMUM_CHARGING_POWER = "MaximumChargingPower"
    #: The smallest power a storage charges with; an offer at or below it is not taken.
    MINIMUM_CHARGING_POWER = "MinimumChargingPower"
    CHARGE_DISCHARGE = "ChargeDischarge"
    CHARGE = "Charge"
    DISCHARGE = "Discharge"
    GAS_PRODUCTION = "GasProduction"
    GAS_CONSUMPTION_UNCONTROLLED = "Gas consumption without any control"
    HEAT_DELIVERED = "HEAT_DELIVERED"
    HEAT_CONSUMPTION = "HEAT_CONSUMPTION"

    # Heating
    HEAT_TO_BUILDING = "HeatToBuilding"
    HEAT_TO_BUFFER = "HeatToBuffer"
    THERMAL_PRODUCTION = "ThermalProduction"
    FUEL_CONSUMPTION = "FuelConsumption"

    WATER_HEATING = "WaterHeating"
    HEATING = "Heating"


@enum.unique
class DistrictNames(str, enum.Enum):

    """Names of Districts."""

    QUARTIER = "Quartier"
    BEZIRK = "Bezirk"
    DISTRICT = "District"
    AREA = "Area"
    NEIGHBORHOOD = "Neighborhood"

    @classmethod
    def is_district(cls, building: typing.Optional[str]) -> bool:
        """Tells whether a building object denotes a district rather than a single building.

        District system setups give their aggregating components a building whose value is one
        of the members of this enum, while ordinary buildings use names such as ``"BUI1"``.
        Postprocessing used to decide this by searching every enum value as a substring of the
        component name; now that a component carries its building as a structured field, the
        same question is answered by comparing that field against the enum members. A missing
        building (``None``) is never a district.
        """
        if building is None:
            return False
        return building in {member.value for member in cls}
