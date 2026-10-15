"""Helper classes for dual-circuit system."""

from dataclasses import dataclass
import enum
from typing import ClassVar, Optional, Tuple

from hisim.component import ComponentInput, SingleTimeStepValues


class HeatingMode(enum.Enum):
    """Heating mode of the district heating component."""

    OFF = 0
    SPACE_HEATING = 1
    DOMESTIC_HOT_WATER = 2
    SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL = 3


@dataclass
class SetTemperatureConfig:
    """Configuration of set temperatures.

    All temperatures and the hysteresis offset are expressed in degrees Celsius
    (for an offset, degrees Celsius and kelvin are interchangeable).
    """

    set_temperature_space_heating_in_celsius: float
    set_temperature_dhw_in_celsius: Optional[float]
    hysteresis_water_temperature_offset_in_celsius: Optional[float]


class DiverterValve:
    """Diverter valve for dual-circuit system.

    Diverter valve to switch between space heating and
    domestic hot water mode in a dual-circuit system.
    """

    @staticmethod
    def determine_operating_mode(
        with_domestic_hot_water_preparation: bool,
        current_controller_mode: HeatingMode,
        is_heating_season: bool,
        water_temperature_input_sh_in_celsius: float,
        water_temperature_input_dhw_in_celsius: Optional[float],
        set_temperatures: SetTemperatureConfig,
        parallel_space_heating_and_dhw_option: bool = False,
    ) -> HeatingMode:
        """Return the mode a dual-circuit generator's diverter valve sets for this step, from the tank and buffer temperatures.

        A dual-circuit generator (a boiler, an electric heater, a district-heating substation) heats either its
        space-heating circuit or the hot-water tank, or both at once if ``parallel_space_heating_and_dhw_option`` is
        set. Without the parallel option a hot-water charge has priority over space heating. Whether the tank needs a
        charge is :meth:`hot_water_charge_needed`; space heating is needed in the heating season while the water is
        below its set temperature plus the hysteresis. The heating season comes from the heating curve, or, for a
        generator without one, from :meth:`is_heating_season`. For example, with the parallel option, a tank at 44 °C
        (60 °C set, 15 K hysteresis) on a winter day gives the parallel mode, and the parallel charge goes on up to
        60 °C.

        Args:
            with_domestic_hot_water_preparation: Whether the generator has a hot-water tank at all.
            current_controller_mode: The controller's mode at the start of the step.
            is_heating_season: Whether the day is in the heating season, in which space heating runs at all.
            water_temperature_input_sh_in_celsius: The space-heating water temperature, in °C.
            water_temperature_input_dhw_in_celsius: The hot-water tank's temperature, in °C, or None without a tank.
            set_temperatures: The set temperatures and the hysteresis.
            parallel_space_heating_and_dhw_option: Whether both circuits may be heated at once.

        Returns:
            The mode for this step.
        """

        def space_heating_needed(
            current_water_temperature_in_celsius: float,
            target_water_temperature_in_celsius: float,
        ) -> bool:
            assert set_temperatures.hysteresis_water_temperature_offset_in_celsius is not None
            if not is_heating_season:
                return False
            if (
                current_water_temperature_in_celsius
                >= target_water_temperature_in_celsius
                + set_temperatures.hysteresis_water_temperature_offset_in_celsius
            ):
                return False
            return True

        needs_space_heating = space_heating_needed(
            water_temperature_input_sh_in_celsius,
            set_temperatures.set_temperature_space_heating_in_celsius,
        )
        needs_dhw_heating = False
        if with_domestic_hot_water_preparation:
            assert water_temperature_input_dhw_in_celsius is not None
            assert set_temperatures.set_temperature_dhw_in_celsius is not None
            assert set_temperatures.hysteresis_water_temperature_offset_in_celsius is not None
            needs_dhw_heating = DiverterValve.hot_water_charge_needed(
                controller_mode=current_controller_mode,
                storage_temperature_in_celsius=water_temperature_input_dhw_in_celsius,
                set_temperature_in_celsius=set_temperatures.set_temperature_dhw_in_celsius,
                hysteresis_in_kelvin=set_temperatures.hysteresis_water_temperature_offset_in_celsius,
            )
        mode = HeatingMode.OFF

        if not parallel_space_heating_and_dhw_option:
            if needs_dhw_heating:
                mode = HeatingMode.DOMESTIC_HOT_WATER  # DHW has priority
            elif needs_space_heating:
                mode = HeatingMode.SPACE_HEATING
        else:
            if needs_dhw_heating and needs_space_heating:
                mode = HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL
            elif needs_dhw_heating:
                mode = HeatingMode.DOMESTIC_HOT_WATER
            elif needs_space_heating:
                mode = HeatingMode.SPACE_HEATING

        return mode

    #: The controller modes in which a hot-water charge is running, so the latch keeps it going up to the set temperature.
    CHARGING_MODES: ClassVar[Tuple[HeatingMode, ...]] = (
        HeatingMode.DOMESTIC_HOT_WATER,
        HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL,
    )

    @staticmethod
    def hot_water_charge_needed(
        *,
        controller_mode: HeatingMode,
        storage_temperature_in_celsius: float,
        set_temperature_in_celsius: float,
        hysteresis_in_kelvin: float,
    ) -> bool:
        """Return whether the hot-water tank is to be charged in this step, from its temperature and the current mode.

        The rule is a two-point control with a latch. A charge starts when the tank is below the switch-on point, the
        set temperature less the hysteresis. A charge that is running, in the hot-water mode alone or in parallel
        with space heating (:attr:`CHARGING_MODES`), goes on until the tank reaches the set temperature. For example,
        with a 60 °C set temperature and a 15 K hysteresis, a tank at 44 °C starts a charge, a tank at 50 °C goes on
        charging if a charge is running and stays idle otherwise, and a tank at 60 °C ends the charge.

        Why the parallel mode latches too: a charge beside space heating would otherwise end at the switch-on point
        and top the tank up to 45 °C instead of charging it to 60 °C.

        Args:
            controller_mode: The controller's mode at the start of the step.
            storage_temperature_in_celsius: The tank's temperature, in °C.
            set_temperature_in_celsius: The warm-water temperature a charge ends at, in °C.
            hysteresis_in_kelvin: How far below the set temperature a charge starts, in K.

        Returns:
            Whether the tank is charged in this step.
        """
        if storage_temperature_in_celsius < set_temperature_in_celsius - hysteresis_in_kelvin:
            return True
        return controller_mode in DiverterValve.CHARGING_MODES and storage_temperature_in_celsius < set_temperature_in_celsius

    @staticmethod
    def is_heating_season(
        *, daily_average_outside_temperature_in_celsius: float, heating_threshold_in_celsius: float
    ) -> bool:
        """Return whether space heating runs on a day, for a generator without a heating curve, from the day's mean.

        The electric heater heats the rooms directly and has no heating curve, so its controller decides the heating
        season itself: a day whose mean outside temperature is at or below the threshold is in the heating season.
        For example, with a 16 °C threshold a day at 16 °C is in it and a day at 16.5 °C is not.

        Args:
            daily_average_outside_temperature_in_celsius: The day's mean outside temperature, in °C.
            heating_threshold_in_celsius: The heating threshold, in °C.

        Returns:
            Whether the day is in the heating season.
        """
        return daily_average_outside_temperature_in_celsius <= heating_threshold_in_celsius


class DualCircuitHotWater:

    """The hot-water rules a dual-circuit generator and its controller share: the lift a charge asks for, and the return.

    A dual-circuit generator, the electric heater or the district-heating substation, heats either its space-heating
    circuit or the hot-water tank's coil, switched by a diverter valve. Its controller asks for a hot-water charge as a
    lift above the tank's start-of-step temperature, and the generator reads the tank's step mean as the return of its
    hot-water circuit. Both components use these functions, so the two generators follow the same rules.
    """

    #: The hot-water return temperature a generator without a hot-water tank uses for its idle hot-water circuit, in °C.
    #: Its hot-water circuit never runs, so the value only marks the circuit's published supply temperature.
    RETURN_TEMPERATURE_WITHOUT_TANK_IN_CELSIUS: ClassVar[float] = 0.0

    @staticmethod
    def lift_in_kelvin(
        *, aim_temperature_in_celsius: float, storage_temperature_in_celsius: float, hysteresis_in_kelvin: float
    ) -> float:
        """Return the lift a hot-water charge asks a dual-circuit generator for, from the tank's temperature, in K.

        The lift is the tank's distance below the controller's aim plus the controller's hysteresis. A tank at or above
        the aim still gets the hysteresis as its lift, since the generator cannot cool. For example, with a 60 °C aim
        and a 15 K hysteresis, a tank at 50 °C asks for 25 K, one at 62 °C for 15 K.

        Args:
            aim_temperature_in_celsius: The warm-water temperature the controller aims at, in °C.
            storage_temperature_in_celsius: The tank's start-of-step temperature, in °C.
            hysteresis_in_kelvin: The controller's hysteresis, in K.

        Returns:
            The lift, in K.
        """
        return float(max(aim_temperature_in_celsius - storage_temperature_in_celsius, 0.0) + hysteresis_in_kelvin)

    @staticmethod
    def return_temperature_in_celsius(
        stsv: SingleTimeStepValues, return_temperature_channel: Optional[ComponentInput]
    ) -> float:
        """Return a dual-circuit generator's hot-water return temperature, the tank's step mean, from its input, in °C.

        A generator without a hot-water tank declares no return input; it then gets
        ``RETURN_TEMPERATURE_WITHOUT_TANK_IN_CELSIUS``. For example, a heater whose return input reads 52.3 °C gets
        52.3 °C, and a heater without a tank gets 0 °C.

        Args:
            stsv: The step values the generator reads its inputs from.
            return_temperature_channel: The generator's input of the tank's step mean, or None if it has no tank.

        Returns:
            The return temperature of the hot-water circuit, in °C.
        """
        if return_temperature_channel is None:
            return DualCircuitHotWater.RETURN_TEMPERATURE_WITHOUT_TANK_IN_CELSIUS
        return stsv.get_input_value(return_temperature_channel)
