"""Helper classes for dual-circuit system."""

from dataclasses import dataclass
import enum
import math
from typing import ClassVar, Optional, Tuple

from hisim.component import ComponentInput, SingleTimeStepValues
from hisim.subcomponents.base import Subcomponent


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


class DiverterValve(Subcomponent):
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

        assert set_temperatures.hysteresis_water_temperature_offset_in_celsius is not None
        needs_space_heating = DiverterValve.space_heating_needed(
            is_heating_season=is_heating_season,
            water_temperature_in_celsius=water_temperature_input_sh_in_celsius,
            set_temperature_in_celsius=set_temperatures.set_temperature_space_heating_in_celsius,
            hysteresis_in_kelvin=set_temperatures.hysteresis_water_temperature_offset_in_celsius,
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

    @staticmethod
    def space_heating_needed(
        *,
        is_heating_season: bool,
        water_temperature_in_celsius: float,
        set_temperature_in_celsius: float,
        hysteresis_in_kelvin: float,
    ) -> bool:
        """Return whether the space-heating circuit asks for heat: in the heating season, below set plus hysteresis.

        For example, with a 35 °C set temperature and a 10 K hysteresis, a buffer at 40 °C on a winter day asks for
        heat, one at 45 °C does not, and no buffer asks for heat outside the heating season.

        Args:
            is_heating_season: Whether the day is in the heating season.
            water_temperature_in_celsius: The space-heating water temperature, in °C.
            set_temperature_in_celsius: The space-heating set temperature, in °C.
            hysteresis_in_kelvin: The controller's hysteresis, in K.

        Returns:
            Whether space heating is needed.
        """
        return is_heating_season and water_temperature_in_celsius < set_temperature_in_celsius + hysteresis_in_kelvin

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


@dataclass(frozen=True)
class HotWaterPriorityTimer:

    """Where a hot-water charge stands against its maximum priority time, at the start or the end of a step.

    ``charge_time_in_seconds`` is how long the charge has had priority since it started or resumed;
    ``space_heating_interval_left_in_seconds`` how much of the space-heating interval after an interruption is left;
    ``charge_interrupted`` whether a charge waits for its interval to end, so a controller that latches its charge on
    its own mode (the boiler's diverter valve) can keep the charge pending while it heats the space. The idle timer,
    all zero and not interrupted, is the state of a generator with no charge running.
    """

    charge_time_in_seconds: float = 0.0
    space_heating_interval_left_in_seconds: float = 0.0
    charge_interrupted: bool = False


class HotWaterPriorityLimit(Subcomponent):

    """The maximum time a hot-water charge has priority over space heating, and the space-heating interval after it.

    A charge that has had priority for the maximum time while space heating asks for heat is interrupted for the
    space-heating interval; it then resumes, and repeats this until the tank reaches its target. A charge is never
    interrupted while space heating does not ask for heat (outside the heating season, or a space-heating circuit at
    its set temperature), and an interval ends early when space heating stops asking. Both times count whole steps,
    rounded up: with 15 / 15 minutes at 900 s the charge and space heating take turns step by step, at 60 s 15 steps
    each. A maximum time of 0 s switches the limit off, and hot water has absolute priority.

    Sources: heat-pump system controllers interrupt a hot-water charge after an installer-set maximum time and then
    block it for a while, so space heating gets the generator: Vaillant multiMATIC VRC 700 ("Max. cylinder charge
    time", off or 20 to 120 min, default 45 min; "DHW req. anti-cycling time", 0 to 120 min, default 30 min),
    Mitsubishi Ecodan FTC3 PAC-IF041B-E ("DHW max. operation time", 30 to 120 min, default 60 min; "DHW mode
    restriction", 30 to 120 min, default 30 min). Siemens Albatros2 boiler and heat-pump controllers (operating
    line 5030 "Ladezeitbegrenzung", 10 to 600 min) interrupt the charge after the set time for as long again, give
    the generator's power to the heating circuits in the pause, and do not interrupt it while the heating circuits are
    off (summer, eco). The defaults of the boiler's and the heat pump's controllers are shorter than those factory
    settings, for the reason given at their fields.
    """

    @staticmethod
    def decide(
        *,
        timer: HotWaterPriorityTimer,
        charge_requested: bool,
        space_heating_requested: bool,
        maximum_priority_time_in_seconds: float,
        space_heating_interval_in_seconds: float,
        seconds_per_timestep: float,
    ) -> Tuple[bool, HotWaterPriorityTimer]:
        """Return whether the charge runs in this step, and the timer at the step's end.

        The rules, the first that applies winning:

        | condition | the charge runs | timer at the end of the step |
        |---|---|---|
        | the limit is off (maximum time 0) or no charge is requested | as requested | idle |
        | space heating asks for heat and an interval is running | no | interval less one step |
        | space heating asks for heat and the charge has had its maximum time | no | full interval less one step |
        | otherwise (an interval ends early when space heating stops asking) | yes | charge time plus one step |

        A step the charge yields marks the timer ``charge_interrupted``; a step it runs clears the mark.

        For example, at 900 s with 15 / 15 minutes and space heating asking throughout, a charge requested on five
        steps runs on the first, third and fifth and yields the second and fourth to space heating.

        Args:
            timer: The timer at the start of the step.
            charge_requested: Whether the controller's own charge rule asks for a charge in this step.
            space_heating_requested: Whether the space-heating circuit asks for heat in this step.
            maximum_priority_time_in_seconds: How long a charge has priority before it is interrupted, in s; 0
                switches the limit off.
            space_heating_interval_in_seconds: How long space heating has the generator after an interruption, in s.
            seconds_per_timestep: The step length, in s.

        Returns:
            Whether the charge runs in this step, and the timer at the step's end.
        """
        if maximum_priority_time_in_seconds <= 0.0 or not charge_requested:
            return charge_requested, HotWaterPriorityTimer()
        if space_heating_requested and (
            timer.space_heating_interval_left_in_seconds > 0.0
            or timer.charge_time_in_seconds >= maximum_priority_time_in_seconds
        ):
            interval_left_in_seconds = (
                timer.space_heating_interval_left_in_seconds
                if timer.space_heating_interval_left_in_seconds > 0.0
                else space_heating_interval_in_seconds
            )
            return False, HotWaterPriorityTimer(
                charge_time_in_seconds=0.0,
                space_heating_interval_left_in_seconds=max(interval_left_in_seconds - seconds_per_timestep, 0.0),
                charge_interrupted=True,
            )
        return True, HotWaterPriorityTimer(
            charge_time_in_seconds=timer.charge_time_in_seconds + seconds_per_timestep,
            space_heating_interval_left_in_seconds=0.0,
            charge_interrupted=False,
        )

    @staticmethod
    def require_valid_times(
        *,
        maximum_priority_time_in_seconds: float,
        space_heating_interval_in_seconds: float,
        controller_description: str,
    ) -> None:
        """Refuse a maximum priority time below 0 s, or a space-heating interval not above 0 s, naming both values.

        For example, 900 s and 900 s are accepted, as is a maximum time of 0 s (no limit) with any interval; a
        negative maximum time and an interval of 0 s beside a maximum time are refused, since an interruption without
        an interval would resume the charge on the step it was interrupted.

        Args:
            maximum_priority_time_in_seconds: The configured maximum priority time, in s.
            space_heating_interval_in_seconds: The configured space-heating interval, in s.
            controller_description: Which controller's configuration, for the message.

        Raises:
            ValueError: If a time is not finite, the maximum time is negative, or the maximum time is above 0 s and
                the interval is not.
        """
        maximum, interval = maximum_priority_time_in_seconds, space_heating_interval_in_seconds
        finite = math.isfinite(maximum) and math.isfinite(interval)
        limit_without_interval = maximum > 0.0 and not interval > 0.0
        if not finite or maximum < 0.0 or limit_without_interval:
            raise ValueError(
                f"{controller_description} needs maximum_hot_water_priority_time_in_seconds of at least 0 s (0 means "
                "no limit) and, with a limit, space_heating_interval_after_hot_water_priority_in_seconds above 0 s, "
                f"got {maximum!r} s and {interval!r} s."
            )


class DualCircuitHotWater(Subcomponent):

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
