"""Which recorded energy-system file a heating-system combination runs (decision Q18).

A *base file* is one of the recorded ``energy_systems/household_*_building_sizer.grouped.
energy_system.yaml`` files. Each wires a complete household — building, weather, occupancy, PV,
storages, electricity meter, an ``electricity_management`` variant — around one heat generator,
with the P4 sizing laws in place, so a calculation parametrises a base file rather than building
a system from scratch. Decision Q18: use them as they stand, select by lookup, refuse what has no
file; new combinations arrive through the energy-system golden gate, never as a RenoVisor-owned
second version of a file.

This module is the lookup table and nothing else. No file is opened here, and the parametriser of
step 5 is what turns a selected name into a loaded system::

    key = BaseFileKey(generator=HeatGenerator.HEAT_PUMP, solar_thermal=False, cars=0)
    BaseFiles.select(key)   # 'household_heatpump_building_sizer.grouped.energy_system.yaml'

Everything the table does not have is a refusal with ``NO_BASE_FILE_FOR_COMBINATION``: solar
thermal on anything but gas or a heat pump, a car on anything but a heat pump, two cars, HVO and
hybrid heat pumps.
"""

from dataclasses import dataclass
from typing import ClassVar, Dict, Tuple

from hisim.renovisor.vocabulary import HeatGenerator, SolarThermalSupplies


@dataclass(frozen=True)
class BaseFileKey:
    """The three facts that pick a base file.

    Args:
        generator: The heat generator the dwelling ends up with.
        solar_thermal: Whether a solar thermal collector is part of the system.
        cars: How many electric vehicles are charged at the dwelling.
    """

    generator: HeatGenerator
    solar_thermal: bool
    cars: int


class BaseFiles:
    """The selection table over the recorded grouped energy-system files.

    Eleven files exist, and the table names each of them once. What is missing is as informative
    as what is present: there is no oil-plus-solar-thermal file, no two-car file, and no file for
    HVO or a hybrid heat pump, so those combinations are refusals rather than approximations.

    Two things this table used to leave open were read off the recorded wiring in step 5. The two
    solar-thermal files wire their collector into the ``DHWStorage`` and into nothing else, so
    :attr:`SOLAR_THERMAL_SUPPLIES_SUPPORTED` is domestic hot water only as a fact rather than as
    an assumption. And "no photovoltaics" is expressed the way every other device size is: the
    inventory's ``photovoltaics.power_in_watt`` reaches the array's own sizable ``power_in_watt``,
    and a recorded value on a sizable field pins it, so a zero there is an array that produces
    nothing while the entry itself stays in the file.
    """

    #: The directory the files live in, relative to the repository root.
    DIRECTORY: ClassVar[str] = "energy_systems"

    BY_KEY: ClassVar[Dict[BaseFileKey, str]] = {
        BaseFileKey(HeatGenerator.GAS_HEATING, False, 0): "household_gas_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.GAS_HEATING, True, 0):
            "household_gas_solar_thermal_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.OIL_HEATING, False, 0): "household_oil_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.PELLET_HEATING, False, 0):
            "household_pellets_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.WOODCHIP_HEATING, False, 0):
            "household_wood_chips_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.HYDROGEN_HEATING, False, 0):
            "household_hydrogen_boiler_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.HEAT_PUMP, False, 0):
            "household_heatpump_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.HEAT_PUMP, True, 0):
            "household_heatpump_solar_thermal_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.HEAT_PUMP, False, 1):
            "household_heatpump_car_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.ELECTRIC_HEATING, False, 0):
            "household_electric_heating_building_sizer.grouped.energy_system.yaml",
        BaseFileKey(HeatGenerator.DISTRICT_HEATING, False, 0):
            "household_district_heating_building_sizer.grouped.energy_system.yaml",
    }

    #: Which solar-thermal supplies the recorded files implement, read off their wiring in step 5:
    #: both files feed the domestic hot water storage alone. The other two values stay refusals
    #: until a recorded file wires a collector into the space heating.
    SOLAR_THERMAL_SUPPLIES_SUPPORTED: ClassVar[Tuple[SolarThermalSupplies, ...]] = (SolarThermalSupplies.DHW_ONLY,)

    #: The generator a domestic-hot-water heat pump needs the house to have, because no recorded
    #: file puts one on a boiler house.
    DHW_HEAT_PUMP_GENERATOR: ClassVar[HeatGenerator] = HeatGenerator.HEAT_PUMP

    #: How many electric vehicles the recorded fleet can carry.
    MAXIMUM_CARS: ClassVar[int] = 1

    @classmethod
    def select(cls, key: BaseFileKey) -> str:
        """Return the file name for one combination.

        Args:
            key: The generator, whether solar thermal is present, and the number of cars.

        Returns:
            The file name, without a directory.

        Raises:
            KeyError: When no recorded file carries the combination; the application turns that
                into ``Refusal(NO_BASE_FILE_FOR_COMBINATION)``.
        """
        return cls.BY_KEY[key]

    @classmethod
    def has(cls, key: BaseFileKey) -> bool:
        """Return whether a recorded file exists for the combination."""
        return key in cls.BY_KEY

    @classmethod
    def file_names(cls) -> Tuple[str, ...]:
        """Return every file the table names, sorted, for the test that checks they exist."""
        return tuple(sorted(set(cls.BY_KEY.values())))
