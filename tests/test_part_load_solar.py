"""Part load of the solar pump: the controller commands the ratio, the collector runs it, nothing ranks it.

* ``SolarThermalSystemController`` publishes the part-load ratio and its target, the 80 °C maximum storage temperature
  above which it stops the pump: 0 while the pump stands, exactly 1 at and below the threshold, and above it the ratio
  :class:`hisim.part_load.PartLoadRule` gives for the ratio the pump ran and the storage's start and end temperatures.
  It keeps no part-load memory, and a forced pass publishes the ratio the pump ran.
* ``SolarThermalSystem`` books the commanded fraction: the averaged flow at the full-load outlet, the heat it carries
  and the fraction of the pump's electricity, and reports the ratio its pump ran.

How a collector and a backup on one storage settle together is tested on the rule itself
(``tests/test_part_load_rule.py``).
"""

from typing import Any, Dict, List

import pytest

from hisim.components import solar_thermal_system
from hisim.components.solar_thermal_system import SolarThermalSystem, SolarThermalSystemController
from hisim.part_load import PartLoadRule
from tests.part_load_rigs import AllInputsRig, CollectorRig, SolarControllerRig


class ControllerPasses:
    """Run the solar controller over the passes of one step, as the simulator does."""

    #: A step well into the run.
    TIMESTEP: int = 100

    @classmethod
    def ratios(cls, seconds_per_timestep: int, values: Dict[str, float], passes: int, **extra: int) -> List[float]:
        """Return the ratio the controller publishes in each pass of a fresh step with the given inputs."""
        controller, stsv, fakes = SolarControllerRig.build(seconds_per_timestep, **extra)
        controller.i_save_state()
        published = []
        for _ in range(passes):
            controller.i_restore_state()
            outputs = AllInputsRig.step(controller, stsv, fakes, values, timestep=cls.TIMESTEP)
            published.append(outputs[SolarThermalSystemController.PartLoadRatio])
        return published


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep, extra", [(60, {}), (600, {}), (900, {"part_load_above_seconds": 900})])
def test_at_and_below_the_threshold_a_running_pump_gets_exactly_one(seconds_per_timestep: int, extra: Dict) -> None:
    """The pump that runs gets 1.0 in every pass however hot the storage ends; 60 s results rest on that."""
    values = SolarControllerRig.values(start_c=45.0, end_c=70.0, ratio_run=0.3)
    assert ControllerPasses.ratios(seconds_per_timestep, values, 6, **extra) == [1.0] * 6


@pytest.mark.base
def test_above_the_threshold_the_controller_applies_the_rule_towards_its_maximum_storage_temperature() -> None:
    """At 900 s the ratio lies on the line from the start temperature through the ratio run to the 80 °C maximum.

    A controller that still aimed at the backups' 60 °C warm-water aim would publish 0 for a storage that starts at
    75 °C and stop the sun's charge there.
    """
    values = SolarControllerRig.values(start_c=75.0, end_c=82.0)
    expected = (PartLoadRule.aim_temperature_in_celsius(80.0) - 75.0) / (82.0 - 75.0)
    assert ControllerPasses.ratios(900, values, 3) == pytest.approx([expected] * 3, rel=1e-12)
    controller, stsv, fakes = SolarControllerRig.build(900)
    target = AllInputsRig.step(controller, stsv, fakes, values, timestep=ControllerPasses.TIMESTEP)[
        SolarThermalSystemController.TargetTemperatureInCelsius
    ]
    assert target == SolarThermalSystemController.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS == 80.0


@pytest.mark.base
def test_a_storage_above_the_backups_aim_but_below_the_maximum_keeps_the_sunny_pump_running_the_whole_step() -> None:
    """A storage that starts at 61 °C, above the backups' 60 °C aim, gets the whole step of a sunny collector.

    A controller that stopped at the backups' aim would publish 0 here and leave the heat on the roof.
    """
    assert ControllerPasses.ratios(900, SolarControllerRig.values(start_c=61.0, end_c=66.0), 2) == [1.0, 1.0]


@pytest.mark.base
def test_a_full_storage_stops_the_pump_and_its_ratio_is_zero() -> None:
    """A storage that started above the 80 °C maximum stops the pump; the ratio published is 0."""
    assert ControllerPasses.ratios(900, SolarControllerRig.values(start_c=81.0, end_c=81.0), 2) == [0.0, 0.0]


@pytest.mark.base
def test_under_force_convergence_the_solar_controller_publishes_the_ratio_run() -> None:
    """A forced pass publishes the ratio the pump ran, whatever the storage reads, so the pump stops changing."""
    controller, stsv, fakes = SolarControllerRig.build(900)
    controller.i_save_state()
    AllInputsRig.step(
        controller, stsv, fakes, SolarControllerRig.values(start_c=55.0, end_c=62.0), timestep=ControllerPasses.TIMESTEP
    )
    forced = AllInputsRig.step(
        controller,
        stsv,
        fakes,
        SolarControllerRig.values(start_c=55.0, end_c=50.0, ratio_run=0.42),
        timestep=ControllerPasses.TIMESTEP,
        force_convergence=True,
    )
    assert forced[SolarThermalSystemController.PartLoadRatio] == 0.42


class PumpSteps:
    """Drive the solar controller through passes and steps as the simulator does, and read its on/off signal."""

    #: A flow between the switch-off and the switch-on flow, kg/s.
    BETWEEN_THE_FLOWS_IN_KG_PER_S: float = 0.004

    #: A flow above the switch-on flow, kg/s.
    ABOVE_THE_ON_FLOW_IN_KG_PER_S: float = 0.006

    #: A flow below the switch-off flow, kg/s.
    BELOW_THE_OFF_FLOW_IN_KG_PER_S: float = 0.002

    @staticmethod
    def values(mass_flow_in_kg_per_second: float, ratio_run: float = 1.0) -> Dict[str, float]:
        """Return the controller's inputs for a tank at 45 °C and the given collector flow and ratio run."""
        values = SolarControllerRig.values(start_c=45.0, end_c=50.0, ratio_run=ratio_run)
        flow_input = SolarThermalSystemController.RequiredWaterMassFlowAtStartOfStepInKgPerSecond
        values[flow_input] = mass_flow_in_kg_per_second
        return values

    @staticmethod
    def signal(
        controller: Any, stsv: Any, fakes: Dict[str, Any], values: Dict[str, float], force_convergence: bool = False
    ) -> float:
        """Run one pass from the saved state, as the simulator does, and return the on/off signal it publishes."""
        controller.i_restore_state()
        outputs = AllInputsRig.step(
            controller, stsv, fakes, values, timestep=ControllerPasses.TIMESTEP, force_convergence=force_convergence
        )
        return outputs[SolarThermalSystemController.ControlSignalToSolarThermalSystem]


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_the_pump_state_of_the_previous_step_chooses_the_flow_and_no_pass_remembers_another(
    seconds_per_timestep: int,
) -> None:
    """Within a step the decision reads only the pass's inputs and the saved state; the state moves only between steps.

    A controller that kept a pass's decision would hold a pump on in the next pass of the same step, which is the pass
    memory that makes a converged step depend on the passes before it; one that saved nothing between steps would have
    no hysteresis at all.
    """
    controller, stsv, fakes = SolarControllerRig.build(seconds_per_timestep)
    controller.i_save_state()  # the first step starts with the pump standing
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S)) == 1
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)) == 0
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S)) == 1

    controller.i_save_state()  # the step ended with the pump running
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)) == 1
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BELOW_THE_OFF_FLOW_IN_KG_PER_S)) == 0
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)) == 1

    controller.i_save_state()  # the step ended with the pump running again
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BELOW_THE_OFF_FLOW_IN_KG_PER_S)) == 0
    controller.i_save_state()  # the step ended with the pump standing
    assert PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)) == 0


@pytest.mark.base
def test_a_pump_the_full_storage_stopped_runs_again_only_three_kelvin_below_the_maximum() -> None:
    """The full-storage stop holds across steps until the storage starts a step 3 K below the maximum, at 77 °C.

    A controller without the band would restart the pump as soon as the storage cools below 80 °C and switch it every
    second step; one that kept the stop in a pass instead of the saved state would hold or release it within a step.
    """
    controller, stsv, fakes = SolarControllerRig.build(900)
    sunny = PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S

    def signal(start_c: float) -> float:
        """Run one pass of a sunny step whose storage started at ``start_c`` and return the pump signal."""
        values = PumpSteps.values(sunny)
        values[SolarThermalSystemController.StorageTemperatureAtStartOfStepInCelsius] = start_c
        return PumpSteps.signal(controller, stsv, fakes, values)

    controller.i_save_state()  # the first step starts with the pump standing and the stop released
    assert signal(60.5) == 1  # above the backups' 60 °C aim, below the maximum, no stop: the pump switches on
    controller.i_save_state()
    assert signal(79.5) == 1  # just below the maximum: the pump keeps running
    controller.i_save_state()
    assert signal(80.025) == 0  # the part-load rule ended the storage just above the maximum: the stop sets
    controller.i_save_state()
    assert signal(79.5) == 0  # the storage cooled below the maximum, but not 3 K below it: the stop holds
    controller.i_save_state()
    assert signal(77.5) == 0
    controller.i_save_state()
    assert signal(77.0) == 1  # exactly 3 K below the maximum: the stop releases and the sunny collector starts the pump
    controller.i_save_state()
    assert signal(78.5) == 1  # inside the band, the released stop stays released


@pytest.mark.base
def test_the_collector_publishes_the_flow_its_heat_at_the_start_of_step_inlet_needs_whatever_the_pump_does() -> None:
    """The flow the controller switches on comes from the start-of-step inlet, not from the step mean or the pump.

    A collector that published its step-mean flow here would let the pump's own heat switch the pump off in the step
    it switches on; one whose answer depended on the pump signal would switch on its own output.
    """
    at_the_mean = CollectorRig.charge(900, ratio=1.0, inlet_at_start_of_step_c=45.0)
    flow_name = SolarThermalSystem.RequiredWaterMassFlowAtStartOfStepInKgPerSecond
    assert at_the_mean[flow_name] == at_the_mean[SolarThermalSystem.RequiredWaterMassFlowOutput] > 0.0
    cooler_start = CollectorRig.charge(900, ratio=1.0, inlet_at_start_of_step_c=40.0)
    assert cooler_start[flow_name] > at_the_mean[flow_name]
    mean_flow_name = SolarThermalSystem.RequiredWaterMassFlowOutput
    assert cooler_start[mean_flow_name] == at_the_mean[mean_flow_name]
    standing = CollectorRig.charge(900, ratio=0.0, control_signal=0.0, inlet_at_start_of_step_c=40.0)
    assert standing[flow_name] == cooler_start[flow_name]


@pytest.mark.base
@pytest.mark.parametrize("pump_ran_in_previous_step", [False, True])
def test_a_pass_after_other_passes_equals_the_first_pass_of_the_step(pump_ran_in_previous_step: bool) -> None:
    """Passes with other inputs before a pass change nothing it publishes: the generic iteration-safety check.

    A controller whose restore restored nothing, or that wrote its state in a pass, would publish a different signal
    after the other passes than in a fresh step.
    """
    fresh, fresh_stsv, fresh_fakes = SolarControllerRig.build(900)
    iterated, iterated_stsv, iterated_fakes = SolarControllerRig.build(900)
    for controller, stsv, fakes in ((fresh, fresh_stsv, fresh_fakes), (iterated, iterated_stsv, iterated_fakes)):
        controller.i_save_state()
        flow = PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S if pump_ran_in_previous_step else 0.0
        PumpSteps.signal(controller, stsv, fakes, PumpSteps.values(flow))
        controller.i_save_state()
    for flow in (PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S, 0.0, PumpSteps.BELOW_THE_OFF_FLOW_IN_KG_PER_S):
        PumpSteps.signal(iterated, iterated_stsv, iterated_fakes, PumpSteps.values(flow))
    full_storage = PumpSteps.values(PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S)
    full_storage[SolarThermalSystemController.StorageTemperatureAtStartOfStepInCelsius] = 81.0
    PumpSteps.signal(iterated, iterated_stsv, iterated_fakes, full_storage)
    final = PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)
    expected = PumpSteps.signal(fresh, fresh_stsv, fresh_fakes, final)
    assert PumpSteps.signal(iterated, iterated_stsv, iterated_fakes, final) == expected
    assert expected == (1 if pump_ran_in_previous_step else 0)


@pytest.mark.base
@pytest.mark.parametrize(("ratio_run", "expected_signal"), [(0.42, 1), (1.0, 1), (0.0, 0)])
def test_under_force_convergence_the_pump_stays_as_the_collector_reports_it_ran(
    ratio_run: float, expected_signal: int
) -> None:
    """A forced pass keeps the pump as it ran, whatever the flow, and the next step starts from that state.

    A forced pass that decided on the flow again would keep switching a pump at the edge of its switch-on flow until
    the simulator gives up; one that kept an earlier pass's decision would remember a pass.
    """
    controller, stsv, fakes = SolarControllerRig.build(900)
    controller.i_save_state()
    flow = PumpSteps.BELOW_THE_OFF_FLOW_IN_KG_PER_S if expected_signal else PumpSteps.ABOVE_THE_ON_FLOW_IN_KG_PER_S
    forced_values = PumpSteps.values(flow, ratio_run=ratio_run)
    assert PumpSteps.signal(controller, stsv, fakes, forced_values, force_convergence=True) == expected_signal
    controller.i_save_state()
    next_values = PumpSteps.values(PumpSteps.BETWEEN_THE_FLOWS_IN_KG_PER_S)
    assert PumpSteps.signal(controller, stsv, fakes, next_values) == expected_signal


@pytest.mark.base
def test_the_collector_books_the_commanded_fraction_and_the_pump_runs_that_fraction() -> None:
    """At half the step the flow, heat and pump electricity halve; the outlet stays the full-load outlet."""
    full = CollectorRig.charge(900, ratio=1.0)
    part = CollectorRig.charge(900, ratio=0.5)
    assert full[SolarThermalSystem.WaterMassFlowOutput] > 0.0
    assert part[SolarThermalSystem.WaterTemperatureOutput] == full[SolarThermalSystem.WaterTemperatureOutput]
    for field in (
        SolarThermalSystem.WaterMassFlowOutput,
        SolarThermalSystem.ThermalPowerOutput,
        SolarThermalSystem.ThermalEnergyOutput,
        SolarThermalSystem.ElectricityConsumptionOutput,
    ):
        assert part[field] == pytest.approx(0.5 * full[field], rel=1e-12)
    assert part[SolarThermalSystem.SolarPumpHeatLoss] == part[SolarThermalSystem.ElectricityConsumptionOutput]
    assert (part[SolarThermalSystem.PartLoadRatioRun], full[SolarThermalSystem.PartLoadRatioRun]) == (0.5, 1.0)
    standing = CollectorRig.charge(900, ratio=0.0, control_signal=0.0)
    assert (standing[SolarThermalSystem.ElectricityConsumptionOutput], standing[SolarThermalSystem.PartLoadRatioRun]) == (0.0, 0.0)


@pytest.mark.base
def test_the_pump_powers_are_named_constants() -> None:
    """The pump draws 10 W, or 35 W with ``old_solar_pump``, while the controller runs it at full load."""
    assert SolarThermalSystem.PUMP_POWER_IN_WATT == 10
    assert SolarThermalSystem.OLD_PUMP_POWER_IN_WATT == 35
    assert CollectorRig.charge(900, ratio=1.0)[SolarThermalSystem.ElectricityConsumptionOutput] == 10.0
    assert solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("x").old_solar_pump is False
