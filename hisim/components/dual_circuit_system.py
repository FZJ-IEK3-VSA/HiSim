"""Helper classes for dual-circuit system."""

from dataclasses import dataclass
import enum
import math
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
        """Return the mode a dual-circuit generator's diverter valve sets for this step, from two water temperatures.

        The two temperatures are the space-heating water temperature (a buffer's, the distribution water's, or 0 for
        the electric heater, which has neither) and the hot-water tank's.

        A dual-circuit generator (a boiler, an electric heater, a district-heating substation) heats either its
        space-heating circuit or the hot-water tank, or both at once if ``parallel_space_heating_and_dhw_option`` is
        set. Without the parallel option a hot-water charge has priority over space heating. Whether the tank needs a
        charge is :meth:`hot_water_charge_needed`; space heating is needed in the heating season while the water is
        below its set temperature plus the hysteresis. The heating season comes from the heating curve. For example,
        with the parallel option, a tank at 44 °C (60 °C set, 15 K hysteresis) on a winter day gives the parallel mode,
        and the parallel charge goes on up to 60 °C.

        Args:
            with_domestic_hot_water_preparation: Whether the generator has a hot-water tank at all.
            current_controller_mode: The controller's mode at the start of the step.
            is_heating_season: Whether the day is in the heating season, in which space heating runs at all.
            water_temperature_input_sh_in_celsius: The space-heating water temperature, in °C: a buffer's, the
                distribution water's, or 0 for the electric heater.
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

    #: The controller modes in which a hot-water charge is running: the latch keeps it going up to the set temperature,
    #: and a generator runs its hot-water circuit in exactly these modes.
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
    def require_valid_hysteresis(
        *,
        hysteresis_in_kelvin: float,
        set_temperature_in_celsius: float,
        field_name: str,
        controller_description: str,
    ) -> None:
        """Refuse a hysteresis outside ``0 < hysteresis < set temperature``, naming the field and the value.

        The switch-on point of a hot-water charge is the set temperature less the hysteresis
        (:meth:`hot_water_charge_needed`). A negative hysteresis moves it above the set temperature, so a 60 °C tank
        with -5 K charges to 65 °C; zero makes the latch a one-point control that starts and ends a charge at the same
        temperature; a hysteresis at or above the set temperature puts the switch-on point at or below 0 °C, so no
        charge ever starts. For example, the shipped 10 K and 15 K at a 60 °C set temperature are accepted, 0 K and
        60 K are refused. A dual-circuit controller calls this when it is built, where it knows its set temperature.

        Args:
            hysteresis_in_kelvin: The configured hysteresis, in K.
            set_temperature_in_celsius: The warm-water temperature a charge ends at, in °C.
            field_name: The configuration field the hysteresis comes from, for the message.
            controller_description: Which controller is built, for the message, e.g. "The boiler's controller".

        Raises:
            ValueError: If the hysteresis is not a finite number above 0 K and below the set temperature.
        """
        if not math.isfinite(hysteresis_in_kelvin) or not 0.0 < hysteresis_in_kelvin < set_temperature_in_celsius:
            raise ValueError(
                f"{controller_description} needs {field_name} above 0 K and below its {set_temperature_in_celsius} °C "
                f"warm-water set temperature, got {hysteresis_in_kelvin!r} K: a hot-water charge starts at the set "
                "temperature less this hysteresis, so a negative value charges above the set temperature, 0 starts "
                "and ends a charge at one point, and a value at or above the set temperature never starts one."
            )


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
