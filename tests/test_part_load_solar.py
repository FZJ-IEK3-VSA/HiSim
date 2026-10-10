"""Part load of the solar pump: the controller commands the ratio, the collector runs it, nothing ranks it.

* ``SolarThermalSystemController`` publishes the part-load ratio and its target, the 60 °C warm-water aim above which
  it stops the pump: 0 while the pump stands, exactly 1 at and below the threshold, and above it the ratio
  :class:`hisim.part_load.PartLoadRule` gives for the ratio the pump ran and the storage's start and end temperatures.
  It keeps no part-load memory, and a forced pass publishes the ratio the pump ran.
* ``SolarThermalSystem`` books the commanded fraction: the averaged flow at the full-load outlet, the heat it carries
  and the fraction of the pump's electricity, and reports the ratio its pump ran.

How a collector and a backup on one storage settle together is tested on the rule itself
(``tests/test_part_load_rule.py``).
"""

from typing import Dict, List

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
def test_above_the_threshold_the_controller_applies_the_rule_towards_its_warm_water_aim() -> None:
    """At 900 s the ratio lies on the line from the start temperature through the ratio run, in every pass."""
    values = SolarControllerRig.values(start_c=55.0, end_c=62.0)
    expected = (PartLoadRule.aim_temperature_in_celsius(60.0) - 55.0) / (62.0 - 55.0)
    assert ControllerPasses.ratios(900, values, 3) == pytest.approx([expected] * 3, rel=1e-12)
    controller, stsv, fakes = SolarControllerRig.build(900)
    target = AllInputsRig.step(controller, stsv, fakes, values, timestep=ControllerPasses.TIMESTEP)[
        SolarThermalSystemController.TargetTemperatureInCelsius
    ]
    assert target == SolarThermalSystemController.WARM_WATER_AIM_IN_CELSIUS == 60.0


@pytest.mark.base
def test_a_full_storage_stops_the_pump_and_its_ratio_is_zero() -> None:
    """A storage that started above 60 °C stops the pump; the ratio published is 0."""
    assert ControllerPasses.ratios(900, SolarControllerRig.values(start_c=61.0, end_c=61.0), 2) == [0.0, 0.0]


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
