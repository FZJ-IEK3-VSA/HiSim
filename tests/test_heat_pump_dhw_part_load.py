"""Above 600 s the heat pump's hot-water charge runs a fraction of the step, which its hot-water controller sets.

Bead hisim-heat-pump-model-rework-fpqz.9: above ``SimulationParameters.part_load_above_seconds`` the hot-water
controller commands the part-load ratio :class:`hisim.part_load.PartLoadRule` gives on the tank's end-of-step
temperature, aiming at the charge end, and the heat pump runs it through the same law as its space-heating ratio: the
averaged flow ``r m`` at the full-load (capped) outlet and ``r`` times the full-load electricity. The hot-water priority
limit counts the share of each step the charge runs. At and below the threshold every ratio is exactly 1.

The tests step the controller and the heat pump on fake inputs, and a toy tank whose end temperature rises linearly
with the ratio, through the passes of a step, to show the charge stopping inside the step at the charge end.
"""

from typing import ClassVar, Dict, List, Tuple

import pytest

from hisim import hydronics
from hisim.components.dual_circuit_system import HotWaterPriorityLimit, HotWaterPriorityTimer
from hisim.components.more_advanced_heat_pump.controller_dhw import (
    HeatPumpDhwState,
    MoreAdvancedHeatPumpHPLibControllerDHW,
    MoreAdvancedHeatPumpHPLibControllerDHWConfig,
)
from hisim.components.more_advanced_heat_pump.heat_pump import (
    MoreAdvancedHeatPumpHPLib,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.part_load import PartLoadRule
from tests.part_load_rigs import AllInputsRig, Parameters
from tests.test_heat_pump_flow_booking import SingleStep, flow_thermal_power_in_watt

pytestmark = pytest.mark.base


class ControllerPass:

    """The heat pump's hot-water controller on fake inputs, stepped pass by pass, a namespace."""

    #: The controller's field names, shortened.
    START: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage
    END: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius
    ACTUAL: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.ActualPartLoadRatioDhw
    RAISE: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier
    SPACE_HEATING: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.SpaceHeatingStateFromController
    REQUESTED: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.RequestedPartLoadRatioDhw
    TARGET: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.TargetTemperatureDhwInCelsius
    STATE: ClassVar[str] = MoreAdvancedHeatPumpHPLibControllerDHW.State_dhw

    @staticmethod
    def controller(seconds_per_timestep: int) -> Tuple[MoreAdvancedHeatPumpHPLibControllerDHW, object, Dict[str, object]]:
        """Return the standard controller (40/60 °C, charge end 2 K, 15 / 15 min) at a step length, wired to fakes."""
        controller = MoreAdvancedHeatPumpHPLibControllerDHW(
            my_simulation_parameters=Parameters.one_day(seconds_per_timestep),
            config=MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW"),
        )
        stsv, fakes = AllInputsRig.build(controller)
        return controller, stsv, fakes

    @classmethod
    def first_pass(cls, seconds_per_timestep: int, values: Dict[str, float]) -> Dict[str, float]:
        """Return the outputs of one unforced pass of a fresh controller on ``values`` (0 for every other input)."""
        controller, stsv, fakes = cls.controller(seconds_per_timestep)
        return AllInputsRig.step(controller, stsv, fakes, values)


class ToyTank:

    """A tank whose end temperature rises linearly with the charge's ratio, ``T_end = T_start + r * rise``, a namespace."""

    #: The rise a whole step of charging gives, K.
    RISE_PER_WHOLE_STEP_IN_KELVIN: ClassVar[float] = 6.0

    @classmethod
    def end_temperature_in_celsius(cls, start_temperature_in_celsius: float, ratio: float) -> float:
        """Return the tank's end-of-step temperature after a charge at ``ratio`` from its start temperature, °C."""
        return start_temperature_in_celsius + ratio * cls.RISE_PER_WHOLE_STEP_IN_KELVIN


@pytest.mark.parametrize("seconds_per_timestep", [60, 600])
def test_at_and_below_600_s_a_charge_requests_exactly_the_whole_step(seconds_per_timestep: int) -> None:
    """Catches a fine-step run that part-loads its charge: the tank ends past the charge end and the ratio stays 1."""
    outputs = ControllerPass.first_pass(
        seconds_per_timestep,
        {ControllerPass.START: 39.0, ControllerPass.END: 70.0, ControllerPass.ACTUAL: 1.0},
    )
    assert outputs[ControllerPass.STATE] == HeatPumpDhwState.ON.value
    assert outputs[ControllerPass.REQUESTED] == 1.0
    assert outputs[ControllerPass.TARGET] == 58.0


def test_above_600_s_the_controller_requests_the_ratio_that_ends_the_tank_at_the_charge_end() -> None:
    """A tank that starts at 39 °C and ends at 61 °C after a whole step needs (58.025 - 39) / (61 - 39) of it."""
    outputs = ControllerPass.first_pass(900, {ControllerPass.START: 39.0, ControllerPass.END: 61.0, ControllerPass.ACTUAL: 1.0})
    expected = PartLoadRule.next_part_load_ratio(
        actual_ratio=1.0, start_temperature_in_celsius=39.0, end_temperature_in_celsius=61.0, target_temperature_in_celsius=58.0
    )
    assert expected == pytest.approx((58.025 - 39.0) / (61.0 - 39.0))
    assert outputs[ControllerPass.REQUESTED] == expected
    assert outputs[ControllerPass.TARGET] == 58.0


def test_the_energy_manager_s_raise_lifts_the_charge_end_the_ratio_aims_at() -> None:
    """With a 10 K raise the charge ends at 68 °C, and the ratio aims there."""
    outputs = ControllerPass.first_pass(
        900, {ControllerPass.START: 50.0, ControllerPass.END: 74.0, ControllerPass.ACTUAL: 1.0, ControllerPass.RAISE: 10.0}
    )
    assert outputs[ControllerPass.TARGET] == 68.0
    assert outputs[ControllerPass.REQUESTED] == pytest.approx((68.025 - 50.0) / (74.0 - 50.0))


def test_without_a_charge_the_controller_requests_nothing() -> None:
    """A tank at 50 °C with no charge running starts none, and the requested ratio is 0."""
    outputs = ControllerPass.first_pass(900, {ControllerPass.START: 50.0, ControllerPass.END: 49.0, ControllerPass.ACTUAL: 0.0})
    assert outputs[ControllerPass.STATE] == HeatPumpDhwState.OFF.value
    assert outputs[ControllerPass.REQUESTED] == 0.0


def test_the_charge_stops_inside_the_step_at_the_charge_end_and_ends_on_the_next_step() -> None:
    """Catches a charge that runs past its end: the passes settle where the tank ends inside the target band.

    Each pass the controller reads the tank's end temperature at the ratio the heat pump ran in the pass before (the
    heat pump follows its controller, so the actual ratio is the requested one); a tank that starts at 55 °C and
    would rise 6 K in a whole step ends at the charge end after half a step. The next step starts from there, and the
    controller ends the charge.
    """
    controller, stsv, fakes = ControllerPass.controller(900)
    start_in_celsius = 55.0
    controller.i_save_state()
    # a charge already running: the tank lies above the 40 °C switch-on point
    first = AllInputsRig.step(controller, stsv, fakes, {ControllerPass.START: 39.0, ControllerPass.ACTUAL: 1.0})
    assert first[ControllerPass.STATE] == HeatPumpDhwState.ON.value
    controller.i_save_state()
    actual_ratio = 1.0
    ratios: List[float] = []
    for _ in range(20):
        outputs = AllInputsRig.step(
            controller,
            stsv,
            fakes,
            {
                ControllerPass.START: start_in_celsius,
                ControllerPass.END: ToyTank.end_temperature_in_celsius(start_in_celsius, actual_ratio),
                ControllerPass.ACTUAL: actual_ratio,
            },
            timestep=1,
        )
        actual_ratio = outputs[ControllerPass.REQUESTED]
        ratios.append(actual_ratio)
    end_in_celsius = ToyTank.end_temperature_in_celsius(start_in_celsius, actual_ratio)
    assert 0.0 < actual_ratio < 1.0
    assert ratios[-1] == ratios[-2]
    assert 58.0 <= end_in_celsius <= 58.0 + PartLoadRule.TARGET_BAND_IN_KELVIN
    controller.i_save_state()
    following = AllInputsRig.step(
        controller, stsv, fakes, {ControllerPass.START: end_in_celsius, ControllerPass.END: end_in_celsius}, timestep=2
    )
    assert following[ControllerPass.STATE] == HeatPumpDhwState.OFF.value
    assert following[ControllerPass.REQUESTED] == 0.0


def test_a_forced_pass_above_600_s_holds_the_ratio_the_heat_pump_ran() -> None:
    """In a forced pass the requested ratio becomes the actual one the heat pump reports, so the charge stops moving."""
    controller, stsv, fakes = ControllerPass.controller(900)
    values = {ControllerPass.START: 39.0, ControllerPass.END: 61.0, ControllerPass.ACTUAL: 1.0}
    AllInputsRig.step(controller, stsv, fakes, values)
    forced = AllInputsRig.step(controller, stsv, fakes, {**values, ControllerPass.ACTUAL: 0.7}, force_convergence=True)
    assert forced[ControllerPass.REQUESTED] == 0.7


def test_the_priority_limit_counts_the_share_of_the_step_the_charge_runs() -> None:
    """A 900 s charge that runs 0.4 of its first step has had 360 s of priority, so it keeps the second step too."""
    runs, timer = HotWaterPriorityLimit.decide(
        timer=HotWaterPriorityTimer(),
        charge_requested=True,
        space_heating_requested=True,
        maximum_priority_time_in_seconds=900.0,
        space_heating_interval_in_seconds=900.0,
        seconds_per_timestep=900.0,
        charged_share_of_step=0.4,
    )
    assert runs
    assert timer.charge_time_in_seconds == pytest.approx(360.0)
    runs_again, timer = HotWaterPriorityLimit.decide(
        timer=timer,
        charge_requested=True,
        space_heating_requested=True,
        maximum_priority_time_in_seconds=900.0,
        space_heating_interval_in_seconds=900.0,
        seconds_per_timestep=900.0,
        charged_share_of_step=1.0,
    )
    assert runs_again
    assert timer.charge_time_in_seconds == pytest.approx(1260.0)
    yields, _ = HotWaterPriorityLimit.decide(
        timer=timer,
        charge_requested=True,
        space_heating_requested=True,
        maximum_priority_time_in_seconds=900.0,
        space_heating_interval_in_seconds=900.0,
        seconds_per_timestep=900.0,
        charged_share_of_step=1.0,
    )
    assert not yields


@pytest.mark.parametrize("share", [-0.1, 1.5, float("nan")])
def test_a_charged_share_outside_0_to_1_is_refused(share: float) -> None:
    """A share of the step is a fraction; anything else is a wiring or controller error."""
    with pytest.raises(ValueError, match="charged share"):
        HotWaterPriorityLimit.decide(
            timer=HotWaterPriorityTimer(),
            charge_requested=True,
            space_heating_requested=True,
            maximum_priority_time_in_seconds=900.0,
            space_heating_interval_in_seconds=900.0,
            seconds_per_timestep=900.0,
            charged_share_of_step=share,
        )


def test_the_controller_s_priority_timer_counts_the_requested_ratio_of_its_charge() -> None:
    """The heat pump's controller hands the limit the ratio it requests: a charge at 0.5 of a 900 s step counts 450 s."""
    controller, stsv, fakes = ControllerPass.controller(900)
    # 39 °C to an end of 39 + 2 * (58.025 - 39) after a whole step: the rule requests exactly 0.5
    end_in_celsius = 39.0 + 2.0 * (58.025 - 39.0)
    outputs = AllInputsRig.step(
        controller,
        stsv,
        fakes,
        {ControllerPass.START: 39.0, ControllerPass.END: end_in_celsius, ControllerPass.ACTUAL: 1.0, ControllerPass.SPACE_HEATING: 1.0},
    )
    assert outputs[ControllerPass.REQUESTED] == pytest.approx(0.5)
    assert controller.state_of_last_free_pass.hot_water_priority_timer.charge_time_in_seconds == pytest.approx(450.0)


def test_the_charge_end_is_the_set_temperature_less_the_configured_distance() -> None:
    """60 °C less the default 2 K is 58 °C; with a 10 K raise, 68 °C."""
    controller, _, _ = ControllerPass.controller(900)
    assert controller.charge_end_temperature_in_celsius(60.0) == 58.0
    assert controller.charge_end_temperature_in_celsius(70.0) == 68.0


class HotWaterStep:

    """One hot-water step of the heat pump of :class:`tests.test_heat_pump_flow_booking.SingleStep`, a namespace."""

    #: The inputs: 2 °C outside, the cylinder's return at 45.03 °C, a charge commanded.
    HOT_WATER: ClassVar[Dict[str, float]] = {
        MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 30.04,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.03,
        MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW: 2,
    }

    @staticmethod
    def outputs(position: PositionHotWaterStorageInSystemSetup, **values: float) -> Dict[str, float]:
        """Every output of one hot-water step with the inputs above, overridden by field name."""
        return SingleStep.step(SingleStep.heat_pump(position), {**HotWaterStep.HOT_WATER, **values})


@pytest.mark.parametrize("position", list(PositionHotWaterStorageInSystemSetup))
def test_the_heat_pump_runs_the_commanded_hot_water_ratio_at_the_full_load_outlet(
    position: PositionHotWaterStorageInSystemSetup,
) -> None:
    """At 0.4 the flow, the heat and the electricity are 0.4 of the whole step's, at the same outlet; reported 0.4."""
    whole = HotWaterStep.outputs(position, RequestedPartLoadRatioDhw=1.0)
    part = HotWaterStep.outputs(position, RequestedPartLoadRatioDhw=0.4)
    assert whole[MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioDhw] == 1.0
    assert part[MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioDhw] == 0.4
    assert whole[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] > 0.0
    assert part[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == pytest.approx(whole[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW])
    for name in (
        MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
        MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW,
        MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
    ):
        assert part[name] == pytest.approx(0.4 * whole[name]), name
    assert part[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0
    assert part[MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioSh] == 0.0


@pytest.mark.parametrize("ratio", [1.0, 0.4, 0.0])
def test_the_part_loaded_charge_books_the_heat_its_averaged_flow_carries(ratio: float) -> None:
    """The booked hot-water heat is ``r m c (T_out - T_ret)`` from the heat pump's own outputs, at every ratio."""
    outputs = HotWaterStep.outputs(PositionHotWaterStorageInSystemSetup.PARALLEL, RequestedPartLoadRatioDhw=ratio)
    carried = flow_thermal_power_in_watt(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureInputDHW,
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] == pytest.approx(carried, abs=1e-9)
    if ratio == 0.0:
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] == 0.0
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW] == 0.0


def test_a_hot_water_charge_capped_at_its_set_temperature_keeps_the_cap_at_part_load() -> None:
    """A charge capped at 48 °C runs its averaged flow at 48 °C: the cap is the full-load outlet, not lowered by r."""
    whole = HotWaterStep.outputs(
        PositionHotWaterStorageInSystemSetup.PARALLEL, RequestedPartLoadRatioDhw=1.0, SupplyTemperatureSetForDHWInCelsius=48.0
    )
    part = HotWaterStep.outputs(
        PositionHotWaterStorageInSystemSetup.PARALLEL, RequestedPartLoadRatioDhw=0.25, SupplyTemperatureSetForDHWInCelsius=48.0
    )
    assert whole[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == pytest.approx(48.0)
    assert part[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == pytest.approx(48.0)
    expected_heat_in_watt = (
        part[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * (48.0 - 45.03)
    )
    assert part[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] == pytest.approx(expected_heat_in_watt)
    assert part[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW] == pytest.approx(
        0.25 * whole[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW]
    )


def test_a_hot_water_step_the_heat_pump_keeps_running_on_its_own_runs_whole() -> None:
    """Its minimum running time holds hot water after the controller stopped: the actual ratio is 1, not the request."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL, cycling_mode=True)
    heat_pump.state.on_off_previous = 2
    outputs = SingleStep.step(heat_pump, {**HotWaterStep.HOT_WATER, "OnOffSwitchDHW": 0, "RequestedPartLoadRatioDhw": 0.0})
    assert outputs[MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioDhw] == 1.0
    assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] > 0.0


def test_a_space_heating_step_reports_no_hot_water_ratio() -> None:
    """Outside hot water the actual hot-water ratio is 0, whatever the hot-water controller requests."""
    outputs = HotWaterStep.outputs(
        PositionHotWaterStorageInSystemSetup.PARALLEL, OnOffSwitchDHW=0, OnOffSwitchSH=1, RequestedPartLoadRatioDhw=1.0
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioDhw] == 0.0
    assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW] == 0.0
