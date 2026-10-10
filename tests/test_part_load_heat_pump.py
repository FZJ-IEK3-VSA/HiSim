"""Part load of the heat pump's and the electric heater's hot-water charge: the controller commands, the device runs.

* ``MoreAdvancedHeatPumpHPLibControllerDHW`` and ``ElectricHeatingController`` publish the part-load ratio and their
  target: 0 while the charge does not run, exactly 1 at and below the threshold, and above it the ratio
  :class:`hisim.part_load.PartLoadRule` gives for the ratio their device ran and the tank's start and end temperatures.
  The heat pump's target follows the energy manager's raise. Neither keeps part-load memory, and a forced pass
  publishes the ratio the device ran.
* ``MoreAdvancedHeatPumpHPLib`` books the fraction of its full-load charge at the step's COP, the brine pump included;
  ``ElectricHeating`` books the heat its averaged flow carries. Both report the ratio they ran.
"""

from typing import Dict, List

import pytest

from hisim import hydronics
from hisim.components import generic_electric_heating
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibControllerDHW,
)
from hisim.components.more_advanced_heat_pump_hplib_model import HeatPumpOperation
from hisim.part_load import PartLoadRule
from tests.part_load_rigs import (
    AllInputsRig,
    ElectricHeaterRig,
    ElectricHeatingControllerRig,
    HeatPumpControllerDhwRig,
    HeatPumpRig,
)


class Passes:
    """Run a controller over several passes of one step, as the simulator does."""

    #: A step well after the start, beyond any minimum resting time.
    TIMESTEP: int = 1000

    @classmethod
    def ratios(cls, controller: object, stsv: object, fakes: Dict, values: Dict[str, float], passes: int) -> List[float]:
        """Return the ratio each pass publishes, starting a fresh step and restoring the controller before each pass."""
        controller.i_save_state()  # type: ignore[attr-defined]  # a component on its rig
        published = []
        for _ in range(passes):
            controller.i_restore_state()  # type: ignore[attr-defined]  # a component on its rig
            outputs = AllInputsRig.step(controller, stsv, fakes, values, timestep=cls.TIMESTEP)
            published.append(next(value for name, value in outputs.items() if name.startswith("PartLoadRatio")))
        return published


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep, extra", [(60, {}), (600, {}), (900, {"part_load_above_seconds": 900})])
def test_at_and_below_the_threshold_the_heat_pumps_charge_gets_exactly_one(seconds_per_timestep: int, extra: Dict) -> None:
    """The hot-water controller publishes 1.0 in every pass while it charges; 60 s results rest on that."""
    controller, stsv, fakes = HeatPumpControllerDhwRig.build(seconds_per_timestep, **extra)
    values = HeatPumpControllerDhwRig.values(start_c=35.0, end_c=70.0, ratio_run=0.3)
    assert Passes.ratios(controller, stsv, fakes, values, 6) == [1.0] * 6


@pytest.mark.base
@pytest.mark.parametrize("raise_k, target_c", [(0.0, 59.5), (10.0, 69.5)])
def test_the_heat_pumps_target_is_its_set_temperature_less_the_switch_off_tolerance(raise_k: float, target_c: float) -> None:
    """The target follows the energy manager's raise: 59.5 °C without it, 69.5 °C with a 10 K raise."""
    controller, stsv, fakes = HeatPumpControllerDhwRig.build(900)
    outputs = AllInputsRig.step(
        controller, stsv, fakes, HeatPumpControllerDhwRig.values(start_c=35.0, end_c=50.0, raise_k=raise_k)
    )
    assert outputs[MoreAdvancedHeatPumpHPLibControllerDHW.TargetTemperatureDHWInCelsius] == target_c
    assert MoreAdvancedHeatPumpHPLibControllerDHW.charge_end_temperature_in_celsius(60.0 + raise_k) == target_c


@pytest.mark.base
def test_above_the_threshold_the_heat_pumps_controller_applies_the_rule_in_every_pass() -> None:
    """At 900 s the ratio is the rule's for the ratio the heat pump ran, the same in every pass and every step.

    A controller that kept part-load memory would answer the same inputs differently in a later pass.
    """
    controller, stsv, fakes = HeatPumpControllerDhwRig.build(900)
    values = HeatPumpControllerDhwRig.values(start_c=35.0, end_c=62.0, ratio_run=0.8)
    expected = 0.8 * (PartLoadRule.aim_temperature_in_celsius(59.5) - 35.0) / (62.0 - 35.0)
    ratios = Passes.ratios(controller, stsv, fakes, values, 4) + Passes.ratios(controller, stsv, fakes, values, 1)
    assert ratios == pytest.approx([expected] * 5, rel=1e-12)


@pytest.mark.base
def test_under_force_convergence_the_heat_pumps_controller_publishes_the_ratio_run() -> None:
    """A forced pass publishes the ratio the heat pump ran, whatever the tank reads, so the charge stops changing."""
    controller, stsv, fakes = HeatPumpControllerDhwRig.build(900)
    Passes.ratios(controller, stsv, fakes, HeatPumpControllerDhwRig.values(start_c=35.0, end_c=62.0), 2)
    outputs = AllInputsRig.step(
        controller,
        stsv,
        fakes,
        HeatPumpControllerDhwRig.values(start_c=35.0, end_c=50.0, ratio_run=0.42),
        timestep=Passes.TIMESTEP,
        force_convergence=True,
    )
    assert outputs[MoreAdvancedHeatPumpHPLibControllerDHW.PartLoadRatioDHW] == 0.42


@pytest.mark.base
def test_the_heat_pump_books_the_commanded_fraction_at_its_cop_and_reports_it() -> None:
    """At a ratio below 1 the flow, heat and electricity scale; outlet and COP stay the full-load ones."""
    pump = MoreAdvancedHeatPumpHPLib
    full = HeatPumpRig.charge(900, ratio=1.0)
    part = HeatPumpRig.charge(900, ratio=0.4)
    assert part[pump.TemperatureOutputDHW] == full[pump.TemperatureOutputDHW]
    assert part[pump.COP] == full[pump.COP]
    assert part[pump.MassFlowOutputDHW] == pytest.approx(0.4 * full[pump.MassFlowOutputDHW], rel=1e-12)
    assert part[pump.ThermalOutputPowerDHW] == hydronics.circuit_power_w(
        mass_flow_kg_per_s=part[pump.MassFlowOutputDHW], t_supply_c=part[pump.TemperatureOutputDHW], t_return_c=55.0
    )
    assert part[pump.ThermalOutputPowerDHW] == pytest.approx(0.4 * full[pump.ThermalOutputPowerDHW], rel=1e-12)
    assert part[pump.ElectricalInputPowerDHW] == part[pump.ThermalOutputPowerDHW] / part[pump.COP]
    assert (part[pump.PartLoadRatioRunDHW], full[pump.PartLoadRatioRunDHW]) == (0.4, 1.0)


@pytest.mark.base
def test_a_charge_the_heat_pump_keeps_running_against_its_controller_runs_the_whole_step() -> None:
    """A charge held by the minimum running time ignores the commanded ratio, runs whole and reports 1.

    The controller commanded no fraction for a charge it switched off, so a heat pump that ran the commanded ratio there
    would under-book the run its own protection enforces.
    """
    pump = MoreAdvancedHeatPumpHPLib
    held = HeatPumpRig.charge(900, ratio=0.3, state_dhw=0.0, minimum_running_time_in_seconds=3600)
    whole = HeatPumpRig.charge(900, ratio=1.0, minimum_running_time_in_seconds=3600)
    assert held[pump.ThermalOutputPowerDHW] == whole[pump.ThermalOutputPowerDHW] > 0.0
    assert held[pump.PartLoadRatioRunDHW] == 1.0
    off = HeatPumpRig.charge(900, ratio=0.3, state_dhw=0.0)
    assert (off[pump.ThermalOutputPowerDHW], off[pump.PartLoadRatioRunDHW]) == (0.0, 0.0)


@pytest.mark.base
def test_the_part_loaded_hot_water_step_scales_the_brine_pump_with_the_fraction() -> None:
    """The documented example: a quarter of a 0.4 kg/s charge from 52 to 57 °C at COP 2.5, with a 100 W brine pump.

    A brine pump drawing its full power for a fraction of a charge would overbook a ground-source heat pump's
    electricity above the part-load threshold.
    """
    full_load = HeatPumpOperation(
        thermal_power_space_heating_in_watt=0.0,
        thermal_power_hot_water_in_watt=8360.0,
        electrical_power_space_heating_in_watt=0.0,
        electrical_power_hot_water_in_watt=3344.0,
        electrical_power_cooling_in_watt=0.0,
        electrical_power_brine_pump_in_watt=100.0,
        cop=2.5,
        eer=0.0,
        outlet_temperature_space_heating_in_celsius=35.0,
        outlet_temperature_hot_water_in_celsius=57.0,
        mass_flow_space_heating_in_kg_per_second=0.0,
        mass_flow_hot_water_in_kg_per_second=0.4,
    )
    part = MoreAdvancedHeatPumpHPLib.part_loaded_hot_water(full_load, part_load_ratio=0.25, return_temperature_in_celsius=52.0)
    assert part.mass_flow_hot_water_in_kg_per_second == pytest.approx(0.1, rel=1e-12)
    assert part.thermal_power_hot_water_in_watt == pytest.approx(2090.0, rel=1e-12)
    assert part.electrical_power_hot_water_in_watt == pytest.approx(836.0, rel=1e-12)
    assert part.electrical_power_brine_pump_in_watt == pytest.approx(25.0, rel=1e-12)
    whole = MoreAdvancedHeatPumpHPLib.part_loaded_hot_water(full_load, part_load_ratio=1.0, return_temperature_in_celsius=52.0)
    assert whole is full_load


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_the_electric_heaters_controller_publishes_one_at_the_threshold_and_its_warm_water_aim(seconds_per_timestep: int) -> None:
    """At 60 s the charge gets 1.0 whatever the heater ran; the target published is the controller's 60 °C aim."""
    controller, stsv, fakes = ElectricHeatingControllerRig.build(seconds_per_timestep)
    values = ElectricHeatingControllerRig.values(start_c=40.0, end_c=45.0, ratio_run=0.3)
    ratios = Passes.ratios(controller, stsv, fakes, values, 4)
    if seconds_per_timestep == 60:
        assert ratios == [1.0] * 4
    target = AllInputsRig.step(controller, stsv, fakes, values, timestep=Passes.TIMESTEP)[
        generic_electric_heating.ElectricHeatingController.TargetTemperatureDhwInCelsius
    ]
    assert target == controller.warm_water_temperature_aim_in_celsius == 60.0


@pytest.mark.base
def test_above_the_threshold_the_electric_heaters_controller_applies_the_rule() -> None:
    """At 900 s a tank that ends too hot after the whole step gets the line from its start temperature."""
    controller, stsv, fakes = ElectricHeatingControllerRig.build(900)
    values = ElectricHeatingControllerRig.values(start_c=40.0, end_c=63.0)
    ratios = Passes.ratios(controller, stsv, fakes, values, 3)
    expected = (PartLoadRule.aim_temperature_in_celsius(60.0) - 40.0) / (63.0 - 40.0)
    assert ratios == pytest.approx([expected] * 3, rel=1e-12)


@pytest.mark.base
def test_under_force_convergence_the_electric_heaters_controller_publishes_the_ratio_run() -> None:
    """A forced pass publishes the ratio the heater ran, so the charge stops changing."""
    controller, stsv, fakes = ElectricHeatingControllerRig.build(900)
    Passes.ratios(controller, stsv, fakes, ElectricHeatingControllerRig.values(start_c=40.0, end_c=63.0), 1)
    outputs = AllInputsRig.step(
        controller,
        stsv,
        fakes,
        ElectricHeatingControllerRig.values(start_c=40.0, end_c=50.0, ratio_run=0.42),
        timestep=Passes.TIMESTEP,
        force_convergence=True,
    )
    assert outputs[generic_electric_heating.ElectricHeatingController.PartLoadRatioDhw] == 0.42


@pytest.mark.base
def test_the_electric_heater_books_the_commanded_fraction_and_reports_it() -> None:
    """A 1.5 kW charge (6 kW times 25 K / 100) at half the step books 750 W of heat and electricity."""
    heater = generic_electric_heating.ElectricHeating
    full = ElectricHeaterRig.charge(900, ratio=1.0)
    part = ElectricHeaterRig.charge(900, ratio=0.5)
    assert full[heater.ThermalOutputDhwPower] == pytest.approx(1500.0, rel=1e-12)
    assert part[heater.WaterOutputDhwMassFlowRate] == pytest.approx(0.5 * full[heater.WaterOutputDhwMassFlowRate], rel=1e-12)
    assert part[heater.WaterOutputDhwTemperature] == full[heater.WaterOutputDhwTemperature] == 75.0
    assert part[heater.ThermalOutputDhwPower] == pytest.approx(750.0, rel=1e-12)
    assert part[heater.ElectricOutputDhwPower] == part[heater.ThermalOutputDhwPower]
    assert part[heater.ThermalOutputDhwEnergy] == pytest.approx(750.0 * 900.0 / 3600.0, rel=1e-12)
    assert (part[heater.PartLoadRatioRunDhw], full[heater.PartLoadRatioRunDhw]) == (0.5, 1.0)
    assert ElectricHeaterRig.charge(900, ratio=1.0) == full


@pytest.mark.base
@pytest.mark.parametrize(
    "mode, target_c",
    [
        (generic_electric_heating.HeatingMode.DOMESTIC_HOT_WATER, 60.0),
        (generic_electric_heating.HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL, 45.0),
    ],
)
def test_the_electric_heaters_target_is_where_its_mode_ends_the_charge(
    mode: generic_electric_heating.HeatingMode, target_c: float
) -> None:
    """A charge alone ends at the 60 °C aim; beside space heating the diverter valve ends it at 45 °C.

    A target at 60 °C in the parallel mode would never stop a charge the controller ends at 45 °C.
    """
    assert (
        generic_electric_heating.ElectricHeatingController.charge_end_temperature_in_celsius(
            mode, warm_water_temperature_aim_in_celsius=60.0, hysteresis_in_kelvin=15.0
        )
        == target_c
    )
