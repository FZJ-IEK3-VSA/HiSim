"""Helper classes for dual-circuit system."""

from dataclasses import dataclass
import enum
from typing import ClassVar, Optional

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
    outside_temperature_threshold_in_celsius: Optional[float]


class DiverterValve:
    """Diverter valve for dual-circuit system.

    Diverter valve to switch between space heating and
    domestic hot water mode in a dual-circuit system.
    """

    @staticmethod
    def determine_operating_mode(
        with_domestic_hot_water_preparation: bool,
        current_controller_mode: HeatingMode,
        daily_average_outside_temperature_in_celsius: float,
        water_temperature_input_sh_in_celsius: float,
        water_temperature_input_dhw_in_celsius: Optional[float],
        set_temperatures: SetTemperatureConfig,
        parallel_space_heating_and_dhw_option: bool = False,
    ) -> HeatingMode:
        """Set conditions for the district heating controller mode."""

        def dhw_heating_needed(
            controller_mode: HeatingMode,
            actual_water_temperature_in_celsius: Optional[float],
            set_water_temperature_in_celsius: Optional[float],
        ) -> bool:
            if not with_domestic_hot_water_preparation:
                return False

            assert actual_water_temperature_in_celsius is not None
            assert set_water_temperature_in_celsius is not None
            assert set_temperatures.hysteresis_water_temperature_offset_in_celsius is not None

            if actual_water_temperature_in_celsius < (
                set_water_temperature_in_celsius
                - set_temperatures.hysteresis_water_temperature_offset_in_celsius
            ):
                return True

            if (
                controller_mode == HeatingMode.DOMESTIC_HOT_WATER
                and actual_water_temperature_in_celsius < set_water_temperature_in_celsius
            ):
                return True

            return False

        def space_heating_needed(
            current_water_temperature_in_celsius: float,
            target_water_temperature_in_celsius: float,
        ) -> bool:
            assert set_temperatures.hysteresis_water_temperature_offset_in_celsius is not None
            if (
                DiverterValve.determine_summer_heating_mode(
                    daily_average_outside_temperature_in_celsius,
                    set_temperatures.outside_temperature_threshold_in_celsius,
                )
                == "off"
            ):
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
        needs_dhw_heating = dhw_heating_needed(
            current_controller_mode,
            water_temperature_input_dhw_in_celsius,
            set_temperatures.set_temperature_dhw_in_celsius,
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

    @staticmethod
    def determine_summer_heating_mode(
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Determine summer heating mode.

        Determines whether heating should be switched off entirely,
        based on the average daily outside temperature.
        """

        # if no heating threshold is set, space heating is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif daily_average_outside_temperature_in_celsius > set_heating_threshold_temperature_in_celsius:
            heating_mode = "off"

        # it is cold enough for heating (at or below threshold)
        elif daily_average_outside_temperature_in_celsius <= set_heating_threshold_temperature_in_celsius:
            heating_mode = "on"
        # (equality case is handled as "on" above)

        return heating_mode


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
