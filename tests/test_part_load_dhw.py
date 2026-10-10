"""Part load of the boiler's hot-water charge: the controller commands the ratio, the boiler runs it.

* ``GenericBoilerController`` publishes the part-load ratio and its target: 0 while the charge does not run, exactly 1
  at and below the threshold, and above it the ratio :class:`hisim.part_load.PartLoadRule` gives for the ratio the
  boiler ran and the tank's start and end temperatures. It keeps no part-load memory, and under ``force_convergence``
  it publishes the ratio the boiler ran.
* ``GenericBoiler`` books the commanded fraction of its full-load charge: the averaged flow at the full-load supply,
  the heat that flow carries and the fraction of the fuel, and reports the ratio it ran. Space heating ignores the
  ratio.
"""

from typing import Dict

import pytest

from hisim.components import generic_boiler
from hisim.components.dual_circuit_system import HeatingMode
from hisim import hydronics
from hisim.part_load import PartLoadRule
from tests.part_load_rigs import BoilerControllerRig, BoilerRig, Parameters, Rig


class ControllerPass:
    """One pass of the boiler controller on its rig."""

    #: A step well after the controller's minimum resting time from the simulation start.
    TIMESTEP: int = 1000

    @classmethod
    def run(cls, controller: generic_boiler.GenericBoilerController, stsv: object, force_convergence: bool = False) -> Dict:
        """Simulate one pass and return the controller's mode, ratio and target."""
        controller.i_simulate(cls.TIMESTEP, stsv, force_convergence)  # type: ignore[arg-type]  # the rig's step values
        return {
            "mode": Rig.output(stsv, controller.operating_mode_channel),
            "ratio": Rig.output(stsv, controller.dhw_part_load.ratio_channel),
            "target_c": Rig.output(stsv, controller.dhw_part_load.target_channel),
        }


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep, extra", [(60, {}), (600, {}), (900, {"part_load_above_seconds": 900})])
def test_at_and_below_the_threshold_a_running_charge_gets_exactly_one(seconds_per_timestep: int, extra: Dict) -> None:
    """A charge that runs gets the ratio 1.0 in every pass, however hot the tank ends; 60 s results rest on that."""
    controller, stsv, fakes = BoilerControllerRig.build(seconds_per_timestep, **extra)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=75.0)
    controller.i_save_state()
    for _ in range(6):
        controller.i_restore_state()
        published = ControllerPass.run(controller, stsv)
        assert published["mode"] == HeatingMode.DOMESTIC_HOT_WATER.value
        assert published["ratio"] == 1.0


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_a_charge_that_does_not_run_gets_zero_and_the_target_is_the_warm_water_aim(seconds_per_timestep: int) -> None:
    """With the tank above its switch-on point the ratio is 0; the target published is the 60 °C warm-water aim."""
    controller, stsv, fakes = BoilerControllerRig.build(seconds_per_timestep)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=58.0, tank_end_c=57.0)
    published = ControllerPass.run(controller, stsv)
    assert published["mode"] != HeatingMode.DOMESTIC_HOT_WATER.value
    assert published["ratio"] == 0.0
    assert published["target_c"] == controller.warm_water_temperature_aim_in_celsius == 60.0


@pytest.mark.base
def test_above_the_threshold_the_controller_applies_the_rule_to_the_ratio_run_and_the_tank() -> None:
    """At 900 s the ratio is the rule's: the line from the tank's start through the ratio the boiler ran and the end."""
    controller, stsv, fakes = BoilerControllerRig.build(900)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=64.0, ratio_run=0.8)
    expected = 0.8 * (60.0 + PartLoadRule.TARGET_BAND_IN_KELVIN / 2.0 - 45.0) / (64.0 - 45.0)
    assert ControllerPass.run(controller, stsv)["ratio"] == pytest.approx(expected, rel=1e-12)


@pytest.mark.base
def test_the_controller_keeps_no_part_load_memory_between_passes_or_steps() -> None:
    """The same inputs give the same ratio in every pass and after every save and restore.

    A controller that kept part-load memory, a search or a pass count, would answer the same inputs differently in a
    later pass or a later step.
    """
    controller, stsv, fakes = BoilerControllerRig.build(900)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=64.0, ratio_run=1.0)
    controller.i_save_state()
    first = ControllerPass.run(controller, stsv)["ratio"]
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=50.0, ratio_run=0.3)
    for _ in range(3):
        controller.i_restore_state()
        ControllerPass.run(controller, stsv)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=64.0, ratio_run=1.0)
    controller.i_restore_state()
    assert ControllerPass.run(controller, stsv)["ratio"] == first
    controller.i_save_state()
    controller.i_restore_state()
    assert ControllerPass.run(controller, stsv)["ratio"] == first


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep, held_ratio", [(900, 0.42), (60, 1.0)])
def test_under_force_convergence_the_controller_publishes_the_ratio_run(seconds_per_timestep: int, held_ratio: float) -> None:
    """A forced pass publishes the ratio the boiler ran, whatever the tank reads, so the charge stops changing.

    At and below the threshold the ratio keeps its last unforced value, exactly 1 for a running charge, as without
    part load.
    """
    controller, stsv, fakes = BoilerControllerRig.build(seconds_per_timestep)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=64.0, ratio_run=1.0)
    controller.i_save_state()
    ControllerPass.run(controller, stsv)
    BoilerControllerRig.set_inputs(stsv, fakes, tank_start_c=45.0, tank_end_c=50.0, ratio_run=0.42)
    for _ in range(3):
        controller.i_restore_state()
        assert ControllerPass.run(controller, stsv, force_convergence=True)["ratio"] == held_ratio


@pytest.mark.base
def test_the_boiler_books_the_commanded_fraction_of_its_full_load_charge() -> None:
    """At a ratio below 1 flow, heat and fuel scale; the supply stays the full-load supply."""
    full = BoilerRig.charge(900, ratio=1.0)
    part = BoilerRig.charge(900, ratio=0.3)
    assert part["supply_c"] == full["supply_c"] == 70.0
    assert part["flow_kg_per_s"] == pytest.approx(0.3 * full["flow_kg_per_s"], rel=1e-12)
    assert part["heat_w"] == hydronics.circuit_power_w(
        mass_flow_kg_per_s=part["flow_kg_per_s"], t_supply_c=70.0, t_return_c=55.0
    )
    assert part["heat_w"] == pytest.approx(0.3 * full["heat_w"], rel=1e-12)
    assert part["fuel_w"] == pytest.approx(0.3 * full["fuel_w"], rel=1e-12)
    assert part["fuel_wh"] == pytest.approx(part["fuel_w"] * 900.0 / 3600.0, rel=1e-12)
    assert (part["ratio_run"], full["ratio_run"]) == (0.3, 1.0)


@pytest.mark.base
def test_a_whole_step_and_space_heating_are_booked_as_without_part_load() -> None:
    """Ratio 1 leaves the charge untouched, and space heating ignores the hot-water ratio altogether."""
    full = BoilerRig.charge(900, ratio=1.0)
    run = generic_boiler.GenericBoiler.part_loaded_firing(
        generic_boiler.BoilerFiring(
            fuel_power_in_watt=full["fuel_w"],
            thermal_power_in_watt=full["heat_w"],
            mass_flow_in_kg_per_second=full["flow_kg_per_s"],
            supply_temperature_in_celsius=70.0,
        ),
        part_load_ratio=1.0,
        return_temperature_in_celsius=55.0,
    )
    assert (run.mass_flow_in_kg_per_second, run.thermal_power_in_watt, run.fuel_power_in_watt) == (
        full["flow_kg_per_s"],
        full["heat_w"],
        full["fuel_w"],
    )
    assert BoilerRig.charge(900, ratio=0.0, mode=HeatingMode.SPACE_HEATING) == BoilerRig.charge(
        900, ratio=1.0, mode=HeatingMode.SPACE_HEATING
    )
    assert BoilerRig.charge(900, ratio=1.0, mode=HeatingMode.SPACE_HEATING)["ratio_run"] == 0.0
    assert BoilerRig.charge(900, ratio=1.0, mode=HeatingMode.OFF)["ratio_run"] == 0.0


@pytest.mark.base
def test_the_part_loaded_firing_example() -> None:
    """The documented example: 0.2 kg/s from 55 to 70 °C with 14 kW of fuel at a quarter of the step."""
    run = generic_boiler.GenericBoiler.part_loaded_firing(
        generic_boiler.BoilerFiring(
            fuel_power_in_watt=14000.0,
            thermal_power_in_watt=12540.0,
            mass_flow_in_kg_per_second=0.2,
            supply_temperature_in_celsius=70.0,
        ),
        part_load_ratio=0.25,
        return_temperature_in_celsius=55.0,
    )
    assert run.mass_flow_in_kg_per_second == pytest.approx(0.05, rel=1e-12)
    assert run.thermal_power_in_watt == pytest.approx(3135.0, rel=1e-12)
    assert run.fuel_power_in_watt == pytest.approx(3500.0, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize("ratio", [-0.1, 1.5])
def test_a_ratio_outside_zero_to_one_is_refused(ratio: float) -> None:
    """A controller that sent a ratio outside [0, 1] fails the boiler's step instead of scaling by it."""
    with pytest.raises(ValueError, match="a part-load ratio is a fraction from 0 to 1"):
        BoilerRig.charge(900, ratio=ratio)


@pytest.mark.base
def test_the_threshold_parameters_reach_the_controller() -> None:
    """A controller built for a 900 s run with the threshold at 900 s does not run part load."""
    controller, _, _ = BoilerControllerRig.build(900, part_load_above_seconds=900)
    assert not controller.dhw_part_load.runs_part_load
    assert Parameters.one_day(900).runs_part_load()
