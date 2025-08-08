""" Helper classes for dual-circuit system. """

from dataclasses import dataclass
import enum
from typing import Optional


class HeatingMode(enum.Enum):
    """Heating mode of the district heating component."""

    OFF = 0
    SPACE_HEATING = 1
    DOMESTIC_HOT_WATER = 2


@dataclass
class SetTemperatureConfig:
    """Configuration of set temperatures."""

    set_temperature_space_heating: float
    set_temperature_dhw: Optional[float]
    hysteresis_dhw_offset: Optional[float]
    outside_temperature_threshold: Optional[float]


class DiverterValve:
    """Diverter valve for dual-circuit system.

    Diverter valve to switch between space heating and
    domestic hot water mode in a dual-circuit system.
    """

    @staticmethod
    def determine_operating_mode(
        with_domestic_hot_water_preparation: bool,
        current_controller_mode: HeatingMode,
        daily_average_outside_temperature: float,
        water_temperature_input_sh_in_celsius: float,
        water_temperature_input_dhw_in_celsius: Optional[float],
        set_temperatures: SetTemperatureConfig,
    ) -> HeatingMode:
        """Set conditions for the district heating controller mode."""

        def dhw_heating_needed(
            controller_mode,
            actual_water_temperature,
            set_water_temperature,
        ):
            if not with_domestic_hot_water_preparation:
                return False

            assert water_temperature_input_dhw_in_celsius is not None
            assert set_temperatures.set_temperature_dhw is not None

            if actual_water_temperature < (
                set_water_temperature - set_temperatures.hysteresis_dhw_offset
            ):
                return True

            if (
                controller_mode == HeatingMode.DOMESTIC_HOT_WATER
                and actual_water_temperature < set_water_temperature
            ):
                return True

            return False

        def space_heating_needed(
            current_water_temperature, target_water_temperature
        ):
            if (
                DiverterValve.determine_summer_heating_mode(
                    daily_average_outside_temperature,
                    set_temperatures.outside_temperature_threshold,
                )
                == "off"
            ):
                return False
            if current_water_temperature >= target_water_temperature:
                return False
            return True

        needs_space_heating = space_heating_needed(
            water_temperature_input_sh_in_celsius,
            set_temperatures.set_temperature_space_heating,
        )
        needs_dhw_heating = dhw_heating_needed(
            current_controller_mode,
            water_temperature_input_dhw_in_celsius,
            set_temperatures.set_temperature_dhw,
        )

        if needs_dhw_heating:
            # DHW has higher priority
            return HeatingMode.DOMESTIC_HOT_WATER
        if needs_space_heating:
            return HeatingMode.SPACE_HEATING
        return HeatingMode.OFF

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
        elif (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "off"

        # it is cold enough for heating
        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "on"

        else:
            raise ValueError(
                f"""Daily average temperature {daily_average_outside_temperature_in_celsius}°C
                or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."""
            )
        return heating_mode
