"""The hplib heat pump and its two controllers tell the energy manager what PV mode may use, and follow its target.

The heat pump publishes its space-heating electricity at full and at minimum load, from the step's start temperatures.
The space-heating controller publishes its own need, the electricity it would run without the energy manager's raise,
from the state at the step's start, and in PV mode turns the energy manager's power target into a part-load ratio. The
hot-water controller publishes whether its charge would run without the raise. The tests check the pure rules, a
hand-stepped heat pump and hand-stepped controllers.
"""

from typing import Dict, List

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump.controller_dhw import (
    MoreAdvancedHeatPumpHPLibControllerDHW,
    MoreAdvancedHeatPumpHPLibControllerDHWConfig,
)
from hisim.components.more_advanced_heat_pump.controller_space_heating import (
    HeatPumpSpaceHeatingMode,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.components.more_advanced_heat_pump.heat_pump import (
    MoreAdvancedHeatPumpHPLib,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.config import ComponentID, SizingContext
from hisim.part_load import StartOfStepPartLoadRule
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft
from tests.test_heat_pump_flow_booking import SingleStep

pytestmark = pytest.mark.base


@pytest.mark.parametrize(
    ("full_electrical_in_watt", "full_thermal_in_watt", "minimum_thermal_in_watt", "expected_in_watt"),
    [
        (1500.0, 6000.0, 1800.0, 450.0),
        (1500.0, 6000.0, 8000.0, 1500.0),
        (1500.0, 6000.0, 0.0, 0.0),
        (0.0, 0.0, 1800.0, 0.0),
        (1500.0, 0.0, 1800.0, 1500.0),
    ],
)
def test_the_minimum_load_is_the_minimum_heat_at_the_full_load_cop(
    full_electrical_in_watt: float, full_thermal_in_watt: float, minimum_thermal_in_watt: float, expected_in_watt: float
) -> None:
    """Catches a minimum load above the full load, or one taken at another COP than the full load's.

    The heat pump modulates by running a fraction of the step at full load, so its minimum load is the minimum heat
    over the full-load COP, at most the full load.
    """
    assert MoreAdvancedHeatPumpHPLib.minimum_load_electrical_power_in_watt(
        full_load_electrical_power_in_watt=full_electrical_in_watt,
        full_load_thermal_power_in_watt=full_thermal_in_watt,
        minimum_thermal_power_in_watt=minimum_thermal_in_watt,
    ) == pytest.approx(expected_in_watt)


@pytest.mark.parametrize(("full_electrical_in_watt", "minimum_thermal_in_watt"), [(-1.0, 1800.0), (1500.0, -1.0)])
def test_a_negative_load_is_refused(full_electrical_in_watt: float, minimum_thermal_in_watt: float) -> None:
    """Catches a negative full load or minimum heat turned into a minimum load the energy manager decides on."""
    with pytest.raises(ValueError, match="must not be negative"):
        MoreAdvancedHeatPumpHPLib.minimum_load_electrical_power_in_watt(
            full_load_electrical_power_in_watt=full_electrical_in_watt,
            full_load_thermal_power_in_watt=6000.0,
            minimum_thermal_power_in_watt=minimum_thermal_in_watt,
        )


def power_range_in_watt(start_temperature_in_celsius: float, return_temperature_in_celsius: float) -> Dict[str, float]:
    """Step a fresh 10 kW parallel-buffer heat pump once and return its full-load and minimum-load electricity, in W.

    Args:
        start_temperature_in_celsius: The buffer's start-of-step temperature, in °C.
        return_temperature_in_celsius: The return temperature the heat pump reads in this pass, in °C.
    """
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    outputs = SingleStep.step(
        heat_pump,
        {
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: return_temperature_in_celsius,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.0,
            MoreAdvancedHeatPumpHPLib.SpaceHeatingStorageTemperatureAtStartOfStepInCelsius: start_temperature_in_celsius,
        },
    )
    return {
        "full": outputs[MoreAdvancedHeatPumpHPLib.ElectricalPowerAtFullLoadSpaceHeatingInWatt],
        "minimum": outputs[MoreAdvancedHeatPumpHPLib.ElectricalPowerAtMinimumLoadSpaceHeatingInWatt],
    }


def test_the_power_range_follows_the_buffer_s_start_temperature_not_the_return_of_the_pass() -> None:
    """Catches a power range taken at the return of each pass, which moves the energy manager's thresholds in a step.

    Two heat pumps reading different returns but the same buffer start temperature publish the same range; a warmer
    start, at which the heat pump's COP is lower, raises it. The minimum load is the 1,500 W minimum heat at the
    full-load COP, so it lies below the full load.
    """
    at_30 = power_range_in_watt(35.0, 30.0)
    at_40 = power_range_in_watt(35.0, 40.0)
    warmer = power_range_in_watt(45.0, 30.0)
    assert at_30 == at_40
    assert warmer["full"] > at_30["full"]
    assert 0.0 < at_30["minimum"] < at_30["full"]


@pytest.mark.parametrize(("return_temperature_in_celsius", "rod_runs"), [(35.0, False), (66.0, True)])
def test_the_full_load_is_the_compressor_s_draw_of_a_full_load_step_without_the_rod(
    return_temperature_in_celsius: float, rod_runs: bool
) -> None:
    """Catches a full load that counts the backup heating rod, or one that differs from what a full-load step books.

    A parallel-buffer heat pump runs a whole space-heating step with the buffer's start temperature as its return. At
    0 °C outside its compressor stops at about 69 °C, so a 66 °C return asking for 71 °C needs the rod; the published
    full load is then still the compressor's ``ElectricalInputPowerSH`` alone, the part the controller's ratio scales.
    """
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    outputs = SingleStep.step(
        heat_pump,
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 0.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 0.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: return_temperature_in_celsius,
            MoreAdvancedHeatPumpHPLib.SpaceHeatingStorageTemperatureAtStartOfStepInCelsius: return_temperature_in_celsius,
        },
    )
    assert (outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] > 0.0) == rod_runs
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalPowerAtFullLoadSpaceHeatingInWatt] == pytest.approx(
        outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH]
    )


def test_the_heat_pump_asks_hplib_once_per_step_for_its_power_range() -> None:
    """Catches a power range recomputed in every pass, which costs an hplib lookup per pass for a value that cannot move.

    A second pass at the same temperatures returns the memo the first pass stored.
    """
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    values = {
        MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
        MoreAdvancedHeatPumpHPLib.SpaceHeatingStorageTemperatureAtStartOfStepInCelsius: 35.0,
    }
    SingleStep.step(heat_pump, values)
    memo = heat_pump.space_heating_power_range_memo
    assert memo is not None
    SingleStep.step(heat_pump, values)
    assert heat_pump.space_heating_power_range_memo is memo


@pytest.mark.parametrize(
    ("own_ratio", "target_in_watt", "full_in_watt", "raised_ratio", "expected_ratio"),
    [
        (0.2, 900.0, 1500.0, 1.0, 0.6),
        (0.2, 900.0, 1500.0, 0.4, 0.4),
        (0.7, 900.0, 1500.0, 1.0, 0.7),
        (0.0, 1500.0, 1500.0, 1.0, 1.0),
        (0.0, 0.0, 1500.0, 0.8, 0.8),
        (0.3, 0.0, 0.0, 0.0, 0.0),
    ],
)
def test_every_pv_mode_ratio(
    own_ratio: float, target_in_watt: float, full_in_watt: float, raised_ratio: float, expected_ratio: float
) -> None:
    """Catches a PV-mode ratio below the own need, above the buffer's raised limit, or one applied outside PV mode.

    In PV mode (a target above 0) the ratio is ``max(own, min(target / full load, raised))``; outside it the ratio the
    controller decided stays.
    """
    assert MoreAdvancedHeatPumpHPLibControllerSpaceHeating.pv_mode_part_load_ratio(
        own_part_load_ratio=own_ratio,
        electrical_power_target_in_watt=target_in_watt,
        full_load_electrical_power_in_watt=full_in_watt,
        raised_part_load_ratio=raised_ratio,
    ) == pytest.approx(expected_ratio)


@pytest.mark.parametrize(
    ("own_ratio", "target_in_watt", "full_in_watt", "raised_ratio", "message"),
    [
        (1.2, 900.0, 1500.0, 1.0, "own need"),
        (0.2, 900.0, 1500.0, -0.1, "raised"),
        (0.2, -1.0, 1500.0, 1.0, "must not be negative"),
        (0.2, 900.0, 0.0, 1.0, "full-load electricity above 0 W"),
    ],
)
def test_an_impossible_pv_mode_input_is_refused(
    own_ratio: float, target_in_watt: float, full_in_watt: float, raised_ratio: float, message: str
) -> None:
    """Catches a ratio outside [0, 1], a negative target, or a target the heat pump has no full load to follow."""
    with pytest.raises(ValueError, match=message):
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.pv_mode_part_load_ratio(
            own_part_load_ratio=own_ratio,
            electrical_power_target_in_watt=target_in_watt,
            full_load_electrical_power_in_watt=full_in_watt,
            raised_part_load_ratio=raised_ratio,
        )


class SpaceHeatingControllerPasses:
    """A floor-heating space-heating controller stepped by hand, its inputs fake outputs the test sets per pass."""

    #: The heat pump's full-load space-heating electricity the tests publish, in W.
    FULL_LOAD_IN_WATT = 2000.0

    #: The inputs of a pass unless a test names others: a 35 °C supply temperature in the heating season, the buffer
    #: starting at 29 °C, below the 30 °C switch-on point, and a buffer that a whole step of the heat pump would end at
    #: 38 °C and would end at 30 °C without it.
    VALUES: Dict[str, float] = {
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureInput: 29.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SpaceHeatingSupplyTemperatureSetInCelsius: 35.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.HeatingSeason: 1.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 38.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius: 30.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.ActualPartLoadRatioSh: 1.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.ElectricalPowerAtFullLoadSpaceHeatingInWatt: FULL_LOAD_IN_WATT,
    }

    def __init__(self, seconds_per_timestep: int) -> None:
        """Build the controller at this step length and wire every input to a fake output.

        Args:
            seconds_per_timestep: The step length, in s; above 600 s the controller runs part-load ratios.
        """
        config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
            SizingContext(
                set_heating_temperature_in_celsius=21.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            )
        )
        self.controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
        )
        self.fakes: List[cp.ComponentOutput] = []
        for component_input in self.controller.inputs:
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            self.fakes.append(fake)
        fft.add_global_index_of_components([*self.fakes, self.controller])
        self.stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*self.fakes, self.controller]))

    def one_pass(self, **values: float) -> Dict[str, float]:
        """Run one unforced pass with the inputs of :attr:`VALUES`, overridden by field name, and return the outputs."""
        for fake in self.fakes:
            self.stsv.values[fake.global_index] = {**self.VALUES, **values}.get(fake.field_name, 0.0)
        self.controller.i_simulate(timestep=0, stsv=self.stsv, force_convergence=False)
        return {output.field_name: self.stsv.values[output.global_index] for output in self.controller.outputs}


class TestOwnNeed:
    """The space-heating controller's own need: its decision without the raise, from the state at the step's start."""

    OWN_NEED = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.OwnNeedElectricalPowerInWatt
    RATIO = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.RequestedPartLoadRatioSh

    def test_at_60_s_the_own_need_is_the_full_load_while_the_unraised_hysteresis_heats(self) -> None:
        """Catches an own need that ignores the controller's own hysteresis, or follows the raised one.

        A buffer at 29 °C below the 30 °C switch-on point heats without a raise: the own need is the full load. A
        buffer at 38 °C with a 10 K raise heats only because of the raise (raised switch-on at 40 °C): the own need is
        0, and a 600 W target runs the heat pump at 0.3 of the step.
        """
        assert SpaceHeatingControllerPasses(60).one_pass()[self.OWN_NEED] == SpaceHeatingControllerPasses.FULL_LOAD_IN_WATT
        raised = SpaceHeatingControllerPasses(60).one_pass(
            WaterTemperatureInput=38.0, SimpleHotWaterStorageTemperatureModifier=10.0, ElectricalPowerTargetFromEnergyManagerInWatt=600.0
        )
        assert raised[MoreAdvancedHeatPumpHPLibControllerSpaceHeating.State_SH] == HeatPumpSpaceHeatingMode.HEATING.value
        assert raised[self.OWN_NEED] == 0.0
        assert raised[self.RATIO] == pytest.approx(0.3)

    def test_above_600_s_the_own_need_comes_from_the_step_s_start_and_not_from_the_pass(self) -> None:
        """Catches an own need that follows the buffer readings of each pass, which the raise moves within the step.

        The first step's own need is the full load, the ratio the state starts with; two passes with different buffer
        readings both report it. The step commits the ratio without the raise of its last pass, the raising rule's
        ratio to the unraised 37.5 °C target for 40 °C with and 30 °C without the heat pump, and the next step's own
        need is that ratio times the full load, whatever its own passes read.
        """
        passes = SpaceHeatingControllerPasses(900)
        full_load_in_watt = SpaceHeatingControllerPasses.FULL_LOAD_IN_WATT
        assert passes.one_pass()[self.OWN_NEED] == full_load_in_watt
        end_temperature_field = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius
        assert passes.one_pass(**{end_temperature_field: 40.0})[self.OWN_NEED] == full_load_in_watt
        passes.controller.i_save_state()
        expected_ratio = StartOfStepPartLoadRule.raising_ratio(
            actual_ratio=1.0,
            end_temperature_in_celsius=40.0,
            end_temperature_without_circuit_in_celsius=30.0,
            target_temperature_in_celsius=37.5,
        )
        assert 0.0 < expected_ratio < 1.0
        for end_temperature_in_celsius in (38.0, 45.0):
            own_need = passes.one_pass(**{end_temperature_field: end_temperature_in_celsius})[self.OWN_NEED]
            assert own_need == pytest.approx(expected_ratio * full_load_in_watt)

    def test_in_pv_mode_the_heat_pump_covers_its_own_need_and_adds_the_target_up_to_the_raised_limit(self) -> None:
        """Catches a PV-mode ratio that drops the own need or runs past the buffer's raised limit.

        At 900 s with the own need's ratio 1 (the state's start), a 600 W target cannot lower the ratio below 1;
        after a step that committed a ratio of 0.25, the 1,200 W target asks for 0.6, capped by the ratio to the raised
        target.
        """
        passes = SpaceHeatingControllerPasses(900)
        modifier = {"SimpleHotWaterStorageTemperatureModifier": 10.0}
        assert passes.one_pass(ElectricalPowerTargetFromEnergyManagerInWatt=600.0, **modifier)[self.RATIO] == 1.0
        passes.controller.state_at_step_start = passes.controller.state_at_step_start._replace(part_load_ratio_without_raise_sh=0.25)
        outputs = passes.one_pass(ElectricalPowerTargetFromEnergyManagerInWatt=1200.0, **modifier)
        raised_ratio = StartOfStepPartLoadRule.raising_ratio(
            actual_ratio=1.0,
            end_temperature_in_celsius=38.0,
            end_temperature_without_circuit_in_celsius=30.0,
            target_temperature_in_celsius=47.5,
        )
        assert outputs[self.OWN_NEED] == pytest.approx(0.25 * SpaceHeatingControllerPasses.FULL_LOAD_IN_WATT)
        assert outputs[self.RATIO] == pytest.approx(max(0.25, min(0.6, raised_ratio)))


class TestHotWaterOwnCharge:
    """The hot-water controller says whether its charge is its own need: whether it would charge without the raise."""

    @staticmethod
    def charges_without_raise(storage_temperature_in_celsius: float, raise_in_kelvin: float) -> float:
        """Run one pass of a fresh hot-water controller and return its ``ChargesWithoutRaise``."""
        controller = MoreAdvancedHeatPumpHPLibControllerDHW(
            config=MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("DHW"),
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
        )
        values = {
            MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage: storage_temperature_in_celsius,
            MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier: raise_in_kelvin,
        }
        fakes: List[cp.ComponentOutput] = []
        for component_input in controller.inputs:
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            fakes.append(fake)
        fft.add_global_index_of_components([*fakes, controller])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
        for fake in fakes:
            stsv.values[fake.global_index] = values.get(fake.field_name, 0.0)
        controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
        assert stsv.values[controller.state_dhw_channel.global_index] in (0.0, 2.0)
        return stsv.values[controller.charges_without_raise_channel.global_index]

    @pytest.mark.parametrize(
        ("storage_temperature_in_celsius", "raise_in_kelvin", "expected"),
        [(39.0, 0.0, 1.0), (39.0, 10.0, 1.0), (50.0, 10.0, 0.0), (50.0, 0.0, 0.0)],
    )
    def test_only_a_charge_without_the_raise_is_the_own_need(
        self, storage_temperature_in_celsius: float, raise_in_kelvin: float, expected: float
    ) -> None:
        """Catches a charge only the raise starts reported as own need, or an own charge reported as the raise's.

        A tank at 39 °C, below the 40 °C switch-on point, charges on its own, raise or not. A tank at 50 °C charges only
        with a raise, so its charge is not the own need; without a raise it does not charge at all.
        """
        assert self.charges_without_raise(storage_temperature_in_celsius, raise_in_kelvin) == expected
