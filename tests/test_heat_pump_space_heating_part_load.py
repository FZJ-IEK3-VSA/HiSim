"""The heat pump's space-heating charge of a start-of-step buffer runs a fraction of a coarse step.

The space-heating controller sets the fraction by :meth:`hisim.part_load.StartOfStepPartLoadRule.raising_ratio` from
the buffer's end temperature and its end temperature without the heat pump; the heat pump runs that fraction at full
load and reports it back. The tests check the heat pump's scaling of one step, the ratio it reports in each mode, and
the controller's ratio and target in a free pass, a forced pass, without a buffer and while it does not heat.
"""

from typing import ClassVar, Dict, List

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.components.more_advanced_heat_pump_hplib_model import HeatPumpOperation
from hisim.config import ComponentID, SizingContext
from hisim.part_load import StartOfStepPartLoadRule
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft
from tests.test_heat_pump_flow_booking import SingleStep

pytestmark = pytest.mark.base


def full_load_space_heating() -> HeatPumpOperation:
    """A full-load space-heating step: 0.4 kg/s from 30 to 35 °C at a COP of 4, with a 100 W brine pump."""
    return HeatPumpOperation(
        thermal_power_space_heating_in_watt=0.4 * 4180.0 * 5.0,
        thermal_power_hot_water_in_watt=0.0,
        electrical_power_space_heating_in_watt=0.4 * 4180.0 * 5.0 / 4.0,
        electrical_power_hot_water_in_watt=0.0,
        electrical_power_cooling_in_watt=0.0,
        electrical_power_brine_pump_in_watt=100.0,
        cop=4.0,
        eer=0.0,
        outlet_temperature_space_heating_in_celsius=35.0,
        outlet_temperature_hot_water_in_celsius=45.0,
        mass_flow_space_heating_in_kg_per_second=0.4,
        mass_flow_hot_water_in_kg_per_second=0.0,
    )


def test_a_part_loaded_step_carries_the_averaged_flow_at_the_full_load_outlet() -> None:
    """At a ratio of 0.25: 0.1 kg/s at 35 °C, 2090 W of heat, 522.5 W of electricity and 25 W for the brine pump."""
    step = MoreAdvancedHeatPumpHPLib.part_loaded_space_heating(
        full_load_space_heating(), part_load_ratio=0.25, return_temperature_in_celsius=30.0
    )
    assert step.mass_flow_space_heating_in_kg_per_second == pytest.approx(0.1)
    assert step.outlet_temperature_space_heating_in_celsius == 35.0
    assert step.thermal_power_space_heating_in_watt == pytest.approx(2090.0)
    assert step.electrical_power_space_heating_in_watt == pytest.approx(522.5)
    assert step.electrical_power_brine_pump_in_watt == pytest.approx(25.0)


def test_a_whole_step_is_unchanged_and_a_ratio_of_zero_books_nothing() -> None:
    """Ratio 1 returns the full-load step itself; ratio 0 has no flow, no heat and no electricity."""
    full_load = full_load_space_heating()
    assert MoreAdvancedHeatPumpHPLib.part_loaded_space_heating(
        full_load, part_load_ratio=1.0, return_temperature_in_celsius=30.0
    ) is full_load
    nothing = MoreAdvancedHeatPumpHPLib.part_loaded_space_heating(
        full_load, part_load_ratio=0.0, return_temperature_in_celsius=30.0
    )
    assert nothing.mass_flow_space_heating_in_kg_per_second == 0.0
    assert nothing.thermal_power_space_heating_in_watt == 0.0
    assert nothing.total_electrical_power_in_watt == 0.0


class HeatPumpStep:

    """One step of the parallel-buffer heat pump of :class:`tests.test_heat_pump_flow_booking.SingleStep`."""

    #: The inputs of a space-heating step: 2 °C outside, the buffer's return at 30.04 °C.
    SPACE_HEATING: ClassVar[Dict[str, float]] = {
        MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 30.04,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.03,
        MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1,
    }

    @staticmethod
    def outputs(**values: float) -> Dict[str, float]:
        """Every output of one step with the space-heating inputs, overridden by field name."""
        return SingleStep.step(
            SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
            {**HeatPumpStep.SPACE_HEATING, **values},
        )


def test_the_heat_pump_runs_the_commanded_ratio_of_a_space_heating_step_and_reports_it() -> None:
    """At a commanded ratio of 0.5 the flow and the heat are half the whole step's, and the ratio run is 0.5."""
    whole = HeatPumpStep.outputs(PartLoadRatioSh=1.0)
    half = HeatPumpStep.outputs(PartLoadRatioSh=0.5)
    assert whole[MoreAdvancedHeatPumpHPLib.PartLoadRatioRunSh] == 1.0
    assert half[MoreAdvancedHeatPumpHPLib.PartLoadRatioRunSh] == 0.5
    assert half[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] == pytest.approx(
        0.5 * whole[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH]
    )
    assert half[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == whole[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH]
    assert half[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(
        0.5 * whole[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    )


def test_a_hot_water_step_runs_whole_and_reports_no_space_heating_ratio() -> None:
    """In a hot-water step the space-heating ratio does nothing: the hot-water charge runs whole, the ratio run is 0."""
    whole = HeatPumpStep.outputs(OnOffSwitchDHW=2, PartLoadRatioSh=1.0)
    commanded = HeatPumpStep.outputs(OnOffSwitchDHW=2, PartLoadRatioSh=0.3)
    assert commanded[MoreAdvancedHeatPumpHPLib.PartLoadRatioRunSh] == 0.0
    assert commanded[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] == whole[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW]


class ControllerStep:

    """One pass of the space-heating controller of a floor-heated house at 900 s, its inputs fake outputs."""

    #: The input of the buffer's end temperature without the heat pump, by its field name.
    WITHOUT_HEAT_PUMP: ClassVar[str] = "WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius"

    #: The pass's inputs: a buffer starting at 34 °C below a 35 °C supply temperature in the heating season, which
    #: a whole step of the heat pump would end at 38 °C and which would end at 30 °C without it.
    VALUES: ClassVar[Dict[str, float]] = {
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureInput: 34.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SpaceHeatingSupplyTemperatureSetInCelsius: 35.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.HeatingSeason: 1.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.DailyAverageOutsideTemperature: 0.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 38.0,
        WITHOUT_HEAT_PUMP: 30.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.PartLoadRatioRunSh: 1.0,
    }

    @staticmethod
    def outputs(
        *, state: str = "heating", force_convergence: bool = False, wired: bool = True, **values: float
    ) -> Dict[str, float]:
        """Every output of one pass, the controller starting in ``state``; ``wired=False`` leaves the buffer out."""
        config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
            SizingContext(
                set_heating_temperature_in_celsius=21.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            )
        )
        controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900)
        )
        controller.controller_heatpumpmode = state
        unwired = {
            MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
            ControllerStep.WITHOUT_HEAT_PUMP,
        }
        fakes: List[cp.ComponentOutput] = []
        for component_input in controller.inputs:
            if not wired and component_input.field_name in unwired:
                continue
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            fakes.append(fake)
        fft.add_global_index_of_components([*fakes, controller])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
        for fake in fakes:
            stsv.values[fake.global_index] = {**ControllerStep.VALUES, **values}.get(fake.field_name, 0.0)
        controller.i_simulate(timestep=0, stsv=stsv, force_convergence=force_convergence)
        return {output.field_name: stsv.values[output.global_index] for output in controller.outputs}


def test_the_controller_sets_the_ratio_that_ends_the_buffer_at_the_supply_temperature() -> None:
    """Heating, the ratio is the raising rule's for 38 °C with and 30 °C without the heat pump, the target 35 °C."""
    outputs = ControllerStep.outputs()
    assert outputs[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.State_SH] == 1
    assert outputs[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.TargetTemperatureShInCelsius] == 35.0
    expected = StartOfStepPartLoadRule.raising_ratio(
        ratio_run=1.0,
        end_temperature_in_celsius=38.0,
        end_temperature_without_circuit_in_celsius=30.0,
        target_temperature_in_celsius=35.0,
    )
    assert 0.0 < expected < 1.0
    assert outputs[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.PartLoadRatioSh] == pytest.approx(expected)


def test_the_energy_manager_s_raise_lifts_the_target() -> None:
    """A 5 K raise of the buffer by the energy manager makes the target 40 °C."""
    outputs = ControllerStep.outputs(SimpleHotWaterStorageTemperatureModifier=5.0)
    assert outputs[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.TargetTemperatureShInCelsius] == 40.0


def test_a_buffer_at_its_target_that_its_draw_would_empty_is_charged() -> None:
    """A buffer at 36 °C, above the 35 °C target, that would end at 30 °C without the heat pump gets a charge."""
    outputs = ControllerStep.outputs(WaterTemperatureInput=36.0)
    assert outputs[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.PartLoadRatioSh] > 0.0


def test_without_a_buffer_off_and_in_a_forced_pass_the_ratio_follows_its_other_rules() -> None:
    """No buffer wired: the whole step. Off: nothing. A forced pass: the ratio the heat pump reports it ran."""
    ratio = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.PartLoadRatioSh
    assert ControllerStep.outputs(wired=False)[ratio] == 1.0
    assert ControllerStep.outputs(state="off", WaterTemperatureInput=40.0)[ratio] == 0.0
    assert ControllerStep.outputs(force_convergence=True, PartLoadRatioRunSh=0.4)[ratio] == 0.4
