"""The maximum time a hot-water charge has priority over space heating, in the boiler's and the heat pump's controllers.

Owner decision of 2026-10-21 (hisim-room-air-dhw-dip-a5by): a hot-water charge that has had priority for its maximum
time while space heating asks for heat is interrupted for a space-heating interval, then resumes, and still ends at
its target. The rule itself (:class:`HotWaterPriorityLimit`) is tested on its own, then on the two controllers that
use it, each stepped on fake inputs with a toy tank that warms on a charging step and cools a little otherwise.
"""

from typing import Any, Dict, List, Tuple

import pytest

from hisim.components.dual_circuit_system import HeatingMode, HotWaterPriorityLimit, HotWaterPriorityTimer
from hisim.components.generic_boiler import GenericBoilerController, GenericBoilerControllerConfig
from hisim.components.more_advanced_heat_pump_hplib import (
    HeatPumpDhwState,
    MoreAdvancedHeatPumpHPLibControllerDHW,
    MoreAdvancedHeatPumpHPLibControllerDHWConfig,
)
from hisim.config import ComponentID, SizingContext
from tests.part_load_rigs import AllInputsRig, Parameters


class PrioritySequence:

    """Steps the rule alone over a sequence of steps, a namespace."""

    @staticmethod
    def runs(
        *,
        steps: int,
        seconds_per_timestep: float,
        space_heating_requested: List[bool],
        maximum_priority_time_in_seconds: float = 900.0,
        space_heating_interval_in_seconds: float = 900.0,
    ) -> List[bool]:
        """Return, per step, whether a charge requested on every step runs.

        Args:
            steps: The number of steps.
            seconds_per_timestep: The step length, s.
            space_heating_requested: Per step, whether space heating asks for heat.
            maximum_priority_time_in_seconds: The maximum priority time, s.
            space_heating_interval_in_seconds: The space-heating interval, s.

        Returns:
            Per step, whether the charge runs.
        """
        timer = HotWaterPriorityTimer()
        runs = []
        for step in range(steps):
            charges, timer = HotWaterPriorityLimit.decide(
                timer=timer,
                charge_requested=True,
                space_heating_requested=space_heating_requested[step],
                maximum_priority_time_in_seconds=maximum_priority_time_in_seconds,
                space_heating_interval_in_seconds=space_heating_interval_in_seconds,
                seconds_per_timestep=seconds_per_timestep,
            )
            runs.append(charges)
        return runs


@pytest.mark.base
def test_at_900_s_a_charge_and_space_heating_take_turns_step_by_step() -> None:
    """With 15 / 15 minutes at 900 s, a charge that space heating keeps asking for runs every other step."""
    assert PrioritySequence.runs(steps=6, seconds_per_timestep=900, space_heating_requested=[True] * 6) == [
        True,
        False,
        True,
        False,
        True,
        False,
    ]


@pytest.mark.base
def test_at_60_s_a_charge_runs_15_steps_then_space_heating_15_then_the_charge_resumes() -> None:
    """At 60 s the 15-minute maximum is 15 charging steps, the interval 15 space-heating steps."""
    runs = PrioritySequence.runs(steps=45, seconds_per_timestep=60, space_heating_requested=[True] * 45)
    assert runs == [True] * 15 + [False] * 15 + [True] * 15


@pytest.mark.base
def test_times_that_are_no_whole_number_of_steps_round_up() -> None:
    """20 min of priority at 900 s runs two steps, a 20-minute interval yields two."""
    runs = PrioritySequence.runs(
        steps=6,
        seconds_per_timestep=900,
        space_heating_requested=[True] * 6,
        maximum_priority_time_in_seconds=1200.0,
        space_heating_interval_in_seconds=1200.0,
    )
    assert runs == [True, True, False, False, True, True]


@pytest.mark.base
def test_a_charge_is_never_interrupted_while_space_heating_does_not_ask() -> None:
    """Outside the heating season, or with the space-heating water at its set temperature, hot water keeps priority."""
    assert PrioritySequence.runs(steps=8, seconds_per_timestep=900, space_heating_requested=[False] * 8) == [True] * 8


@pytest.mark.base
def test_an_interval_ends_early_when_space_heating_stops_asking() -> None:
    """A space-heating interval nobody needs any more gives the step back to the charge."""
    runs = PrioritySequence.runs(
        steps=5,
        seconds_per_timestep=60,
        space_heating_requested=[True, True, True, False, True],
        maximum_priority_time_in_seconds=120.0,
        space_heating_interval_in_seconds=600.0,
    )
    assert runs == [True, True, False, True, True]


@pytest.mark.base
def test_a_maximum_time_of_0_s_keeps_absolute_priority() -> None:
    """The limit switched off runs the charge on every requested step."""
    runs = PrioritySequence.runs(
        steps=6, seconds_per_timestep=900, space_heating_requested=[True] * 6, maximum_priority_time_in_seconds=0.0
    )
    assert runs == [True] * 6


@pytest.mark.base
def test_a_step_without_a_requested_charge_resets_the_timer() -> None:
    """A charge that ended starts the next one with a full priority time."""
    charges, timer = HotWaterPriorityLimit.decide(
        timer=HotWaterPriorityTimer(charge_time_in_seconds=900.0),
        charge_requested=False,
        space_heating_requested=True,
        maximum_priority_time_in_seconds=900.0,
        space_heating_interval_in_seconds=900.0,
        seconds_per_timestep=900.0,
    )
    assert not charges
    assert timer == HotWaterPriorityTimer()


@pytest.mark.base
@pytest.mark.parametrize(
    ("maximum_in_seconds", "interval_in_seconds"),
    [(-1.0, 900.0), (900.0, 0.0), (900.0, -60.0), (float("nan"), 900.0), (900.0, float("inf"))],
)
def test_invalid_priority_times_are_refused_by_both_controllers(
    maximum_in_seconds: float, interval_in_seconds: float
) -> None:
    """A negative maximum time, or a limit without an interval, would silently mean something else."""
    with pytest.raises(ValueError, match="maximum_hot_water_priority_time_in_seconds"):
        GenericBoilerControllerConfig(
            component_id=ComponentID(name="BoilerController"),
            maximum_hot_water_priority_time_in_seconds=maximum_in_seconds,
            space_heating_interval_after_hot_water_priority_in_seconds=interval_in_seconds,
        )
    with pytest.raises(ValueError, match="maximum_hot_water_priority_time_in_seconds"):
        MoreAdvancedHeatPumpHPLibControllerDHWConfig(
            component_id=ComponentID(name="HeatPumpControllerDHW"),
            maximum_hot_water_priority_time_in_seconds=maximum_in_seconds,
            space_heating_interval_after_hot_water_priority_in_seconds=interval_in_seconds,
        )


@pytest.mark.base
def test_both_controllers_default_to_15_minutes_of_priority_and_15_of_space_heating() -> None:
    """The owner's decision of 2026-10-21: 15 minutes each, in the boiler's and the heat pump's controller."""
    boiler = GenericBoilerControllerConfig.preset_modulating("BoilerController")
    heat_pump = MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW")
    for config in (boiler, heat_pump):
        assert config.maximum_hot_water_priority_time_in_seconds == 900.0
        assert config.space_heating_interval_after_hot_water_priority_in_seconds == 900.0


class ToyTank:

    """A hot-water tank that warms by a fixed lift on a charging step and cools by a fixed draw otherwise, a namespace.

    The controllers decide on the tank's start-of-step temperature only, so a tank this simple is enough to step them
    through a whole charge: from below the switch-on point, through its interruptions, to its end at the target.
    """

    #: The tank's start, below the boiler's 50 °C and the heat pump's 40 °C switch-on points, °C.
    START_TEMPERATURE_IN_CELSIUS = 38.0
    #: The cooling of a step without a charge, K; a binary fraction, so the toy tank's sums are exact.
    DRAW_PER_STEP_IN_KELVIN = 0.25

    @classmethod
    def step(cls, temperature_in_celsius: float, *, charges: bool, lift_per_step_in_kelvin: float) -> float:
        """Return the tank's temperature after one step."""
        if charges:
            return temperature_in_celsius + lift_per_step_in_kelvin
        return temperature_in_celsius - cls.DRAW_PER_STEP_IN_KELVIN


class BoilerControllerSteps:

    """The boiler's controller stepped over a charge on fake inputs, a namespace."""

    #: The first step number: the controller keeps the burner off for its minimum resting time from step 0.
    FIRST_TIMESTEP = 100

    @staticmethod
    def run(
        seconds_per_timestep: int, *, steps: int, heating_season: bool, lift_per_step_in_kelvin: float
    ) -> Tuple[List[HeatingMode], List[float]]:
        """Return the controller's mode and the toy tank's start temperature on every step.

        The buffer stays at 30 °C below its 40 °C flow set temperature, so space heating asks for heat throughout the
        heating season.

        Args:
            seconds_per_timestep: The step length, s.
            steps: The number of steps.
            heating_season: Whether the steps lie in the heating season.
            lift_per_step_in_kelvin: How much a charging step warms the tank, K.

        Returns:
            The modes and the tank temperatures, one per step.
        """
        config = GenericBoilerControllerConfig.preset_modulating("BoilerController").resolve(
            SizingContext(maximal_thermal_power_in_watt=20000, minimal_thermal_power_in_watt=2000)
        )
        config.with_domestic_hot_water_preparation = True
        controller = GenericBoilerController(
            my_simulation_parameters=Parameters.one_day(seconds_per_timestep), config=config
        )
        stsv, fakes = AllInputsRig.build(controller)
        tank_in_celsius = ToyTank.START_TEMPERATURE_IN_CELSIUS
        modes, tanks = [], []
        for timestep in range(steps):
            controller.i_save_state()
            controller.i_restore_state()
            outputs = AllInputsRig.step(
                controller,
                stsv,
                fakes,
                {
                    GenericBoilerController.WaterTemperatureInputFromWaterStorage: 30.0,
                    GenericBoilerController.WaterTemperatureInputFromDHWStorage: tank_in_celsius,
                    GenericBoilerController.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius: tank_in_celsius,
                    GenericBoilerController.ActualPartLoadRatioDhw: 1.0,
                    GenericBoilerController.SpaceHeatingSupplyTemperatureSetInCelsius: 40.0,
                    GenericBoilerController.HeatingSeason: 1.0 if heating_season else 0.0,
                },
                # past the controller's resting time from the start of the run
                timestep=timestep + BoilerControllerSteps.FIRST_TIMESTEP,
            )
            mode = HeatingMode(int(outputs[GenericBoilerController.OperatingMode]))
            modes.append(mode)
            tanks.append(tank_in_celsius)
            tank_in_celsius = ToyTank.step(
                tank_in_celsius,
                charges=mode == HeatingMode.DOMESTIC_HOT_WATER,
                lift_per_step_in_kelvin=lift_per_step_in_kelvin,
            )
        return modes, tanks


@pytest.mark.base
def test_the_boiler_interrupts_a_charge_every_other_step_at_900_s_and_the_tank_still_reaches_60_c() -> None:
    """Catches a charge that never yields to space heating, or one that does not resume after its interval.

    The toy tank needs six charging steps of 4 K from 38 °C to 60 °C; the charge passes the 50 °C switch-on point on
    the way, so only the controller's latch through its interruptions brings it to 60 °C.
    """
    modes, tanks = BoilerControllerSteps.run(900, steps=14, heating_season=True, lift_per_step_in_kelvin=4.0)
    hot_water = HeatingMode.DOMESTIC_HOT_WATER
    space_heating = HeatingMode.SPACE_HEATING
    assert modes[:11] == [hot_water, space_heating] * 5 + [hot_water]
    assert tanks[11] >= 60.0
    assert hot_water not in modes[11:]


@pytest.mark.base
def test_the_boiler_at_60_s_charges_15_steps_heats_15_and_resumes() -> None:
    """At 60 s the 15-minute maximum and interval are 15 steps each, and the charge resumes after them.

    The toy tank warms 1 K a step: 38 to 53 °C in the first 15 steps, back to 49.25 °C in the interval, and above
    60 °C after eleven more.
    """
    modes, tanks = BoilerControllerSteps.run(60, steps=45, heating_season=True, lift_per_step_in_kelvin=1.0)
    hot_water = HeatingMode.DOMESTIC_HOT_WATER
    space_heating = HeatingMode.SPACE_HEATING
    assert modes[:41] == [hot_water] * 15 + [space_heating] * 15 + [hot_water] * 11
    assert tanks[41] >= 60.0
    assert hot_water not in modes[41:]


@pytest.mark.base
def test_the_boiler_keeps_absolute_priority_outside_the_heating_season() -> None:
    """In summer nothing asks for space heating, so a charge runs to its end without interruption."""
    modes, tanks = BoilerControllerSteps.run(900, steps=8, heating_season=False, lift_per_step_in_kelvin=4.0)
    assert modes[:6] == [HeatingMode.DOMESTIC_HOT_WATER] * 6
    assert tanks[6] >= 60.0


class HeatPumpControllerSteps:

    """The heat pump's hot-water controller stepped over a charge on fake inputs, a namespace."""

    @staticmethod
    def run(
        seconds_per_timestep: int, *, steps: int, space_heating_state: float, lift_per_step_in_kelvin: float
    ) -> Tuple[List[int], List[float]]:
        """Return the published hot-water signal and the toy tank's start temperature on every step.

        Args:
            seconds_per_timestep: The step length, s.
            steps: The number of steps.
            space_heating_state: The space-heating controller's signal on every step, 1 heating, 0 off.
            lift_per_step_in_kelvin: How much a charging step warms the tank, K.

        Returns:
            The published signals (2 charge, 0 none) and the tank temperatures, one per step.
        """
        controller = MoreAdvancedHeatPumpHPLibControllerDHW(
            my_simulation_parameters=Parameters.one_day(seconds_per_timestep),
            config=MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW"),
        )
        stsv, fakes = AllInputsRig.build(controller)
        tank_in_celsius = ToyTank.START_TEMPERATURE_IN_CELSIUS
        signals, tanks = [], []
        for timestep in range(steps):
            controller.i_save_state()
            controller.i_restore_state()
            inputs: Dict[str, Any] = {
                MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage: tank_in_celsius,
                MoreAdvancedHeatPumpHPLibControllerDHW.SpaceHeatingStateFromController: space_heating_state,
            }
            outputs = AllInputsRig.step(controller, stsv, fakes, inputs, timestep=timestep)
            signal = int(outputs[MoreAdvancedHeatPumpHPLibControllerDHW.State_dhw])
            signals.append(signal)
            tanks.append(tank_in_celsius)
            tank_in_celsius = ToyTank.step(
                tank_in_celsius,
                charges=signal == HeatPumpDhwState.ON.value,
                lift_per_step_in_kelvin=lift_per_step_in_kelvin,
            )
        return signals, tanks


@pytest.mark.base
def test_the_heat_pump_interrupts_a_charge_every_other_step_at_900_s_and_the_tank_still_reaches_58_c() -> None:
    """Catches a heat-pump charge that never yields to space heating, or one that does not resume.

    The toy tank needs six charging steps of 4 K from 38 °C to the 58 °C charge end (60 °C less 2 K), since it cools
    0.25 K on every step the charge yields.
    """
    signals, tanks = HeatPumpControllerSteps.run(900, steps=14, space_heating_state=1.0, lift_per_step_in_kelvin=4.0)
    on, off = HeatPumpDhwState.ON.value, HeatPumpDhwState.OFF.value
    assert signals[:11] == [on, off] * 5 + [on]
    assert tanks[11] >= 58.0
    assert on not in signals[11:]


@pytest.mark.base
def test_the_heat_pump_at_60_s_charges_15_steps_heats_15_and_resumes() -> None:
    """At 60 s the 15-minute maximum and interval are 15 steps each, and the charge resumes after them."""
    signals, tanks = HeatPumpControllerSteps.run(60, steps=45, space_heating_state=1.0, lift_per_step_in_kelvin=1.0)
    on, off = HeatPumpDhwState.ON.value, HeatPumpDhwState.OFF.value
    assert signals[:39] == [on] * 15 + [off] * 15 + [on] * 9
    assert tanks[39] >= 58.0
    assert on not in signals[39:]


@pytest.mark.base
def test_the_heat_pump_keeps_absolute_priority_while_space_heating_is_off() -> None:
    """With the space-heating controller off (summer) a charge runs to its end without interruption."""
    signals, tanks = HeatPumpControllerSteps.run(900, steps=8, space_heating_state=0.0, lift_per_step_in_kelvin=4.0)
    assert signals[:5] == [HeatPumpDhwState.ON.value] * 5
    assert tanks[5] >= 58.0
