"""Tests for the air conditioner's controller.

The controller samples the indoor air temperature once per time step and decides the next
step on that sample and on the state it committed (see :class:`AirConditionerController`).
These tests pin what that decision promises: the mode switches with a hysteresis band, the
minimum run and idle times hold, the power ramps by the distance from the set point it holds,
and no pass of a step remembers anything from an earlier pass of the same step. A closed loop
against a small building model checks that the set points are actually held.
"""

from typing import Any, Dict, List, Optional, Tuple

import pytest

from hisim import component
from hisim import loadtypes
from hisim import simulator as sim
from hisim.components.air_conditioner import (
    AirConditionerController,
    AirConditionerControllerConfig,
    AirConditionerControllerMode as Mode,
    AirConditionerControllerState,
)
from hisim.config import ComponentID, DisplayConfig


def given_default_testee(
    config_overwrite: Optional[Dict[str, Any]] = None,
) -> AirConditionerController:
    """Builds a controller from the standard preset at 60 s steps, with optional field overrides.

    The preset holds 20 °C while heating and 24 °C while cooling, with a 1 K hysteresis band
    and 1800 s from zero to full power per kelvin of deviation. The minimum run and idle times
    default to 0 here so that a test sees the temperature rules alone; tests that need them
    pass them in ``config_overwrite``.

    Args:
        config_overwrite: Configuration fields to set after the preset, by name.

    Returns:
        The controller.
    """
    overwrite: Dict[str, Any] = {"minimum_runtime_s": 0, "minimum_idle_time_s": 0}
    overwrite.update(config_overwrite or {})
    simulation_parameters = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = AirConditionerControllerConfig.preset_standard("AirConditionerController")
    for field_name, value in overwrite.items():
        setattr(config, field_name, value)
    testee: AirConditionerController = AirConditionerController(simulation_parameters, config, DisplayConfig())
    return testee


def committed(
    mode: Mode,
    indoor_air_temperature_deg_c: Optional[float],
    power_fraction: float = 0.5,
    activation_time_step: int = 0,
    deactivation_time_step: int = 0,
) -> AirConditionerControllerState:
    """Builds a committed controller state to hand to :meth:`AirConditionerController.decide`.

    Args:
        mode: The committed mode.
        indoor_air_temperature_deg_c: The sampled indoor air temperature, in °C.
        power_fraction: The committed power fraction; replaced by 0 when the mode is off.
        activation_time_step: The step the unit last started.
        deactivation_time_step: The step the unit last stopped.

    Returns:
        The state.
    """
    return AirConditionerControllerState(
        mode,
        activation_time_step,
        deactivation_time_step,
        0.0 if mode == Mode.OFF else power_fraction,
        indoor_air_temperature_deg_c,
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ["committed_mode", "indoor_air_temperature_deg_c", "expected_mode"],
    [
        (Mode.OFF, 40.0, Mode.COOLING),
        (Mode.OFF, 24.1, Mode.COOLING),
        (Mode.OFF, 24.0, Mode.OFF),
        (Mode.OFF, 22.0, Mode.OFF),
        (Mode.OFF, 20.0, Mode.OFF),
        (Mode.OFF, 19.9, Mode.HEATING),
        (Mode.OFF, -4.0, Mode.HEATING),
        # A heating unit holds 20 °C and stops only above 20 + 1 = 21 °C.
        (Mode.HEATING, 15.0, Mode.HEATING),
        (Mode.HEATING, 20.5, Mode.HEATING),
        (Mode.HEATING, 21.0, Mode.HEATING),
        (Mode.HEATING, 21.1, Mode.OFF),
        (Mode.HEATING, 30.0, Mode.OFF),
        # A cooling unit holds 24 °C and stops only below 24 - 1 = 23 °C.
        (Mode.COOLING, 30.0, Mode.COOLING),
        (Mode.COOLING, 23.5, Mode.COOLING),
        (Mode.COOLING, 23.0, Mode.COOLING),
        (Mode.COOLING, 22.9, Mode.OFF),
        (Mode.COOLING, 10.0, Mode.OFF),
    ],
)
def test_mode_switches_at_the_set_points_and_stops_one_kelvin_inside_the_band(
    committed_mode: Mode,
    indoor_air_temperature_deg_c: float,
    expected_mode: Mode,
) -> None:
    """An idle unit starts outside the 20-24 °C band, and a running one at minimum power stops 1 K back inside it."""
    testee = given_default_testee()

    decided = testee.decide(
        committed(committed_mode, indoor_air_temperature_deg_c, AirConditionerController.MINIMUM_POWER_FRACTION), 100
    )

    assert decided.mode == expected_mode


@pytest.mark.base
@pytest.mark.parametrize(
    ["committed_mode", "indoor_air_temperature_deg_c", "timestep", "expected_mode"],
    [
        # Started at step 0 with a 15-step minimum runtime: held until step 15 whatever the air.
        (Mode.HEATING, 30.0, 14, Mode.HEATING),
        (Mode.HEATING, 30.0, 15, Mode.OFF),
        (Mode.COOLING, 10.0, 14, Mode.COOLING),
        (Mode.COOLING, 10.0, 15, Mode.OFF),
        # Stopped at step 0 with a 10-step minimum idle time: held off until step 10.
        (Mode.OFF, 0.0, 9, Mode.OFF),
        (Mode.OFF, 0.0, 10, Mode.HEATING),
        (Mode.OFF, 40.0, 9, Mode.OFF),
        (Mode.OFF, 40.0, 10, Mode.COOLING),
    ],
)
def test_minimum_runtime_and_idle_time_hold_the_mode(
    committed_mode: Mode,
    indoor_air_temperature_deg_c: float,
    timestep: int,
    expected_mode: Mode,
) -> None:
    """The unit keeps running for the minimum runtime and stays off for the minimum idle time."""
    testee = given_default_testee({"minimum_runtime_s": 60 * 15, "minimum_idle_time_s": 60 * 10})

    decided = testee.decide(
        committed(committed_mode, indoor_air_temperature_deg_c, AirConditionerController.MINIMUM_POWER_FRACTION),
        timestep,
    )

    assert decided.mode == expected_mode


@pytest.mark.base
@pytest.mark.parametrize(
    ["committed_mode", "indoor_air_temperature_deg_c", "committed_power_fraction", "expected_power_fraction"],
    [
        # 60 s steps and 1800 s to full power at 1 K: 1/30 of full power per kelvin and step.
        (Mode.HEATING, 19.0, 0.5, 0.5 + 1.0 / 30.0),
        (Mode.HEATING, 17.0, 0.5, 0.5 + 3.0 / 30.0),
        (Mode.HEATING, 20.0, 0.5, 0.5),
        (Mode.HEATING, 20.6, 0.5, 0.5 - 0.6 / 30.0),
        (Mode.HEATING, 10.0, 0.95, 1.0),
        (Mode.HEATING, 20.9, 0.11, AirConditionerController.MINIMUM_POWER_FRACTION),
        (Mode.COOLING, 25.0, 0.5, 0.5 + 1.0 / 30.0),
        (Mode.COOLING, 24.0, 0.5, 0.5),
        (Mode.COOLING, 23.4, 0.5, 0.5 - 0.6 / 30.0),
        (Mode.COOLING, 35.0, 0.95, 1.0),
        # A unit that starts begins at the minimum power and ramps from there.
        (Mode.OFF, 19.0, 0.0, AirConditionerController.MINIMUM_POWER_FRACTION),
        (Mode.OFF, 22.0, 0.0, 0.0),
    ],
)
def test_power_ramps_by_the_distance_from_the_set_point_it_holds(
    committed_mode: Mode,
    indoor_air_temperature_deg_c: float,
    committed_power_fraction: float,
    expected_power_fraction: float,
) -> None:
    """Below the held set point the power rises, above it the power falls, between the minimum and 1."""
    testee = given_default_testee()

    decided = testee.decide(
        committed(committed_mode, indoor_air_temperature_deg_c, committed_power_fraction), 100
    )

    assert decided.power_fraction == pytest.approx(expected_power_fraction)


@pytest.mark.base
@pytest.mark.parametrize(
    ["committed_mode", "indoor_air_temperature_deg_c", "expected_power_fraction"],
    [
        # Back inside the band at 70 % power: no stop, the power ramps down by the deviation instead.
        (Mode.HEATING, 21.5, 0.7 - 1.5 / 30.0),
        (Mode.HEATING, 30.0, 0.7 - 10.0 / 30.0),
        (Mode.COOLING, 22.5, 0.7 - 1.5 / 30.0),
        (Mode.COOLING, 10.0, 0.7 - 14.0 / 30.0),
    ],
)
def test_a_unit_back_inside_the_band_ramps_down_before_it_stops(
    committed_mode: Mode, indoor_air_temperature_deg_c: float, expected_power_fraction: float
) -> None:
    """Soft stop: above the minimum power the unit keeps running and ramps its power down."""
    testee = given_default_testee()

    decided = testee.decide(committed(committed_mode, indoor_air_temperature_deg_c, 0.7), 100)

    assert decided.mode == committed_mode
    assert decided.power_fraction == pytest.approx(expected_power_fraction)


@pytest.mark.base
@pytest.mark.parametrize(
    ["committed_mode", "indoor_air_temperature_deg_c"], [(Mode.HEATING, 21.001), (Mode.COOLING, 22.999)]
)
def test_the_soft_stop_ends_within_the_bound(committed_mode: Mode, indoor_air_temperature_deg_c: float) -> None:
    """From full power, with the air held just past the hysteresis edge, the unit stops within the soft-stop bound.

    Each step lowers the power by at least hysteresis × min(dt, 450 s) / t_ramp = 1/30, so 27 steps bring
    1.0 down to the minimum of 0.1 and the 28th stops the unit, within 0.9 × t_ramp × 1 K / hysteresis = 27 minutes;
    the power never rises on the way.
    """
    testee = given_default_testee()
    state = committed(committed_mode, indoor_air_temperature_deg_c, 1.0)
    powers: List[float] = []
    for timestep in range(100, 200):
        state = testee.decide(state, timestep)
        if state.mode == Mode.OFF:
            break
        powers.append(state.power_fraction)

    assert state.mode == Mode.OFF
    assert len(powers) <= 27
    assert powers == sorted(powers, reverse=True)


@pytest.mark.base
@pytest.mark.parametrize(
    ["seconds_per_timestep", "expected_change_per_kelvin_and_timestep"],
    [(60, 60.0 / 1800.0), (450, 450.0 / 1800.0), (900, 450.0 / 1800.0), (3600, 450.0 / 1800.0)],
)
def test_the_ramp_per_step_is_capped_at_450_seconds(
    seconds_per_timestep: int, expected_change_per_kelvin_and_timestep: float
) -> None:
    """Steps longer than 450 s ramp only as far as a 450 s step, which keeps 15-minute and hourly runs stable."""
    simulation_parameters = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=seconds_per_timestep)
    config = AirConditionerControllerConfig.preset_standard("AirConditionerController")

    testee = AirConditionerController(simulation_parameters, config, DisplayConfig())

    assert testee.power_fraction_change_per_kelvin_and_timestep == pytest.approx(
        expected_change_per_kelvin_and_timestep
    )


@pytest.mark.base
@pytest.mark.parametrize("hysteresis_in_kelvin", [0.0, float("nan")])
def test_a_hysteresis_of_zero_or_nan_is_refused(hysteresis_in_kelvin: float) -> None:
    """Without a finite positive hysteresis the soft stop has no bound, so the controller refuses to be built."""
    with pytest.raises(ValueError, match="finite positive hysteresis"):
        given_default_testee({"hysteresis_in_kelvin": hysteresis_in_kelvin})


@pytest.mark.base
@pytest.mark.parametrize("field_name", ["heating_set_temperature_deg_c", "cooling_set_temperature_deg_c"])
def test_a_set_point_that_is_not_finite_is_refused(field_name: str) -> None:
    """Catches a NaN set point, which passes the order check because every comparison with NaN is false."""
    with pytest.raises(ValueError, match=rf"finite {field_name}, got nan"):
        given_default_testee({field_name: float("nan")})


@pytest.mark.base
@pytest.mark.parametrize("field_name", ["minimum_runtime_s", "minimum_idle_time_s"])
@pytest.mark.parametrize("seconds", [-900.0, float("nan"), float("inf")])
def test_a_minimum_time_that_is_negative_or_not_finite_is_refused(field_name: str, seconds: float) -> None:
    """Catches a negative minimum time that would round to no minimum without a word."""
    with pytest.raises(ValueError, match=rf"{field_name} to be a finite time of at least 0 s, got"):
        given_default_testee({field_name: seconds})


@pytest.mark.base
@pytest.mark.parametrize("ramp_time_s", [0.0, -1800.0, float("nan"), float("inf")])
def test_a_ramp_time_that_is_not_a_finite_positive_time_is_refused(ramp_time_s: float) -> None:
    """A ramp time of 0 divides by zero and a negative one inverts the ramp, so the controller refuses to be built.

    The message names the field and the value, so the author sees which number to change.
    """
    with pytest.raises(ValueError, match=r"ramp_time_to_full_power_at_one_kelvin_s above 0 s, got"):
        given_default_testee({"ramp_time_to_full_power_at_one_kelvin_s": ramp_time_s})


@pytest.mark.base
@pytest.mark.parametrize(
    "heating_set_temperature_deg_c, cooling_set_temperature_deg_c", [(26.0, 20.0), (22.0, 22.0)]
)
def test_a_heating_set_point_at_or_above_the_cooling_set_point_is_refused(
    heating_set_temperature_deg_c: float, cooling_set_temperature_deg_c: float
) -> None:
    """With heating 26 °C and cooling 20 °C an idle unit would heat a 21-25 °C room, so the controller refuses it.

    Equal set points leave no comfort band at all and are refused the same way.
    """
    with pytest.raises(
        ValueError,
        match=(
            r"heating_set_temperature_deg_c below cooling_set_temperature_deg_c, "
            rf"got {heating_set_temperature_deg_c} °C and {cooling_set_temperature_deg_c} °C"
        ),
    ):
        given_default_testee(
            {
                "heating_set_temperature_deg_c": heating_set_temperature_deg_c,
                "cooling_set_temperature_deg_c": cooling_set_temperature_deg_c,
            }
        )


@pytest.mark.base
def test_decide_leaves_the_committed_state_untouched_and_waits_for_a_first_sample() -> None:
    """``decide`` leaves the state it is given unchanged, and before the first sample the unit stays as it is."""
    testee = given_default_testee()
    committed_state = committed(Mode.OFF, 10.0)

    testee.decide(committed_state, 100)
    unsampled = testee.decide(committed(Mode.OFF, None), 100)

    assert committed_state.mode == Mode.OFF
    assert committed_state.power_fraction == 0.0
    assert unsampled.mode == Mode.OFF
    assert unsampled.power_fraction == 0.0


@pytest.mark.base
@pytest.mark.parametrize(
    ["mode", "power_fraction", "expected_signal"],
    [(Mode.OFF, 0.0, 0.0), (Mode.COOLING, 0.7, -0.7), (Mode.HEATING, 0.7, 0.7)],
)
def test_signal_is_the_power_fraction_signed_by_mode(mode: Mode, power_fraction: float, expected_signal: float) -> None:
    """Heating sends a positive signal, cooling a negative one, off sends 0."""
    state = AirConditionerControllerState(mode, 0, 0, power_fraction, 22.0)

    assert AirConditionerController.get_signed_power_fraction(state) == expected_signal


def given_wired_testee(
    config_overwrite: Optional[Dict[str, Any]] = None,
) -> Tuple[AirConditionerController, component.SingleTimeStepValues]:
    """Builds a controller whose input reads slot 0 of a two-slot step-values array.

    Slot 0 stands for the building's indoor air temperature, slot 1 is the controller's signal.

    Args:
        config_overwrite: Configuration fields to set after the preset, by name.

    Returns:
        The controller and the step values it reads from and writes to.
    """
    testee = given_default_testee(config_overwrite)
    indoor_air_temperature_output = component.ComponentOutput(
        "FakeBuilding",
        "TemperatureIndoorAir",
        loadtypes.LoadTypes.TEMPERATURE,
        loadtypes.Units.CELSIUS,
        component_id=ComponentID("FakeBuilding"),
    )
    indoor_air_temperature_output.global_index = 0
    testee.indoor_air_temperature_channel.source_output = indoor_air_temperature_output
    testee.operation_modulating_signal_channel.global_index = 1
    return testee, component.SingleTimeStepValues(2)


def run_one_pass(
    testee: AirConditionerController,
    stsv: component.SingleTimeStepValues,
    timestep: int,
    indoor_air_temperature_deg_c: float,
) -> float:
    """Runs one pass of a step the way the simulator does: restore the committed state, then simulate.

    Args:
        testee: The controller, wired by :func:`given_wired_testee`.
        stsv: Its step values.
        timestep: The step.
        indoor_air_temperature_deg_c: The indoor air temperature the pass reads, in °C.

    Returns:
        The signal the pass sent.
    """
    stsv.values[0] = indoor_air_temperature_deg_c
    testee.i_restore_state()
    testee.i_simulate(timestep, stsv, False)
    return stsv.values[1]


@pytest.mark.base
def test_every_pass_of_a_step_sends_the_same_signal_and_the_last_pass_is_what_the_next_step_reads() -> None:
    """No pass memory: a pass's signal depends on the committed state only, not on earlier passes.

    Step 0 samples 18 °C and step 1 starts heating on it. In step 2 the passes read wildly
    different temperatures and all send the same signal, ramped once more on the 18 °C that
    step 1 committed. Step 3 then decides on what the last pass of step 2 read (25 °C, so the
    power ramps down by 5/30 to the minimum), not on any earlier pass (5 °C would have raised
    it).
    """
    testee, stsv = given_wired_testee()
    testee.i_save_state()
    run_one_pass(testee, stsv, 0, 18.0)
    testee.i_save_state()
    started = run_one_pass(testee, stsv, 1, 18.0)

    testee.i_save_state()
    signals: List[float] = [run_one_pass(testee, stsv, 2, temperature) for temperature in (18.0, 30.0, 5.0, 25.0)]
    testee.i_save_state()
    next_step = run_one_pass(testee, stsv, 3, 25.0)

    assert started == pytest.approx(AirConditionerController.MINIMUM_POWER_FRACTION)
    assert signals == [signals[0]] * len(signals)
    assert signals[0] == pytest.approx(started + 2.0 / 30.0)
    assert next_step == pytest.approx(AirConditionerController.MINIMUM_POWER_FRACTION)


class SmallBuilding:
    """A two-node building model just detailed enough to test the controller's closed loop.

    The thermal mass warms with the delivered power less the losses to the outside. The
    indoor air sits at the mass temperature plus a rise per watt delivered in the same step,
    the way the ISO 13790 air node of HiSim's building answers within one step (0.6 K per kW
    in the air-conditioned house).

    Attributes:
        mass_temperature_deg_c: Temperature of the thermal mass, in °C.
    """

    AIR_RISE_IN_KELVIN_PER_WATT = 0.6e-3
    LOSS_IN_WATT_PER_KELVIN = 300.0
    HEAT_CAPACITY_IN_JOULE_PER_KELVIN = 2.0e7
    UNIT_CAPACITY_IN_WATT = 6000.0

    def __init__(self, mass_temperature_deg_c: float) -> None:
        """Starts the model at a mass temperature.

        Args:
            mass_temperature_deg_c: The initial mass temperature, in °C.
        """
        self.mass_temperature_deg_c = mass_temperature_deg_c

    def step(self, signal: float, outside_temperature_deg_c: float, step_length_s: float) -> float:
        """Advances the model by one step under the controller's signal.

        Args:
            signal: The controller's signed power fraction.
            outside_temperature_deg_c: The outdoor temperature, in °C.
            step_length_s: The step length, in s.

        Returns:
            The indoor air temperature at the end of the step, in °C.
        """
        power_in_watt = signal * self.UNIT_CAPACITY_IN_WATT
        loss_in_watt = self.LOSS_IN_WATT_PER_KELVIN * (self.mass_temperature_deg_c - outside_temperature_deg_c)
        self.mass_temperature_deg_c += (
            (power_in_watt - loss_in_watt) * step_length_s / self.HEAT_CAPACITY_IN_JOULE_PER_KELVIN
        )
        return self.mass_temperature_deg_c + power_in_watt * self.AIR_RISE_IN_KELVIN_PER_WATT


@pytest.mark.base
@pytest.mark.parametrize(
    ["outside_temperature_deg_c", "start_temperature_deg_c", "held_temperature_deg_c"],
    [(5.0, 21.0, 20.0), (12.0, 19.0, 20.0), (32.0, 23.0, 24.0), (38.0, 26.0, 24.0)],
)
def test_closed_loop_holds_the_set_point(
    outside_temperature_deg_c: float, start_temperature_deg_c: float, held_temperature_deg_c: float
) -> None:
    """Against a steady load the indoor air settles within 0.05 K of the set point it holds.

    The loads range from a third to two thirds of the unit's capacity, heating and cooling. A
    controller whose power followed the deviation directly would settle a load-dependent
    distance away; the ramp leaves no such droop. The preset's minimum times apply, and in
    every step both passes send the same signal.
    """
    testee, stsv = given_wired_testee({"minimum_runtime_s": 1800.0, "minimum_idle_time_s": 900.0})
    building = SmallBuilding(start_temperature_deg_c)
    indoor_air_temperature_deg_c = start_temperature_deg_c
    last_six_hours: List[float] = []
    signal = 0.0
    for timestep in range(48 * 60):
        testee.i_save_state()
        first_pass_signal = run_one_pass(testee, stsv, timestep, indoor_air_temperature_deg_c)
        indoor_air_temperature_deg_c = building.step(first_pass_signal, outside_temperature_deg_c, 60.0)
        signal = run_one_pass(testee, stsv, timestep, indoor_air_temperature_deg_c)
        assert signal == first_pass_signal
        if timestep >= 42 * 60:
            last_six_hours.append(indoor_air_temperature_deg_c)

    assert signal != 0.0
    assert max(last_six_hours) == pytest.approx(held_temperature_deg_c, abs=0.05)
    assert min(last_six_hours) == pytest.approx(held_temperature_deg_c, abs=0.05)
