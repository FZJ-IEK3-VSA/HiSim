"""The space-heating controller estimates the heat pump's starts above the part-load threshold from the hysteresis cycle.

Above 600 s a step is longer than the heat pump's cycles, so its on/off state cannot show them. The controller computes
``t_on = Q / (P_min - P)`` and ``t_off = Q / P`` from the delivered mean power ``P``, the minimum output ``P_min`` and
the heat ``Q`` the buffer takes across its hysteresis band, each at least the heat pump's minimum time, and counts
``dt / (t_on + t_off)`` starts per step through an accumulator in its state. The heat pump adds the whole starts to its
counters, and its KPIs read the counter. The buffer's heat capacity and the heat pump's minimum times reach the
controller as sizing facts.
"""

import math
from typing import ClassVar, Dict, List, Optional

import numpy as np
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump.controller_space_heating import (
    AccumulatedStarts,
    HeatPumpSpaceHeatingMode,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.components.more_advanced_heat_pump.heat_pump import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    MoreAdvancedHeatPumpHPLibState,
)
from hisim.components.simple_water_storage import HotWaterStorageSizingEnum, SimpleHotWaterStorageConfig
from hisim.config import ComponentID, SizingContext, resolve_all
from hisim.config.laws import ConfigSizingError, SizingError
from hisim.loadtypes import InstallationLocation
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft
from tests.test_energy_system_explicit_sizes import PinnedTwin

pytestmark = pytest.mark.base

Controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating


class Cycle:

    """The numbers of the twin's 389.04 l buffer, its 1945 W minimum output and the 600 s minimum times."""

    #: The heat the buffer takes across the default 10 K band, in J.
    BAND_HEAT_IN_JOULE: ClassVar[float] = SimpleHotWaterStorageConfig.heat_capacity_in_joule_per_kelvin_of(389.04) * 10.0
    #: The heat pump's minimum output, in W.
    MINIMUM_THERMAL_POWER_IN_WATT: ClassVar[float] = 1945.0

    @staticmethod
    def starts(
        delivered_thermal_power_in_watt: float,
        *,
        band_heat_in_joule: float = BAND_HEAT_IN_JOULE,
        minimum_time_in_seconds: float = 600.0,
        seconds_per_timestep: float = 900.0,
    ) -> float:
        """The starts of one step of the hysteresis cycle at a delivered power."""
        return Controller.starts_per_step_of_hysteresis_cycle(
            delivered_thermal_power_in_watt=delivered_thermal_power_in_watt,
            minimum_thermal_power_in_watt=Cycle.MINIMUM_THERMAL_POWER_IN_WATT,
            band_heat_in_joule=band_heat_in_joule,
            minimum_running_time_in_seconds=minimum_time_in_seconds,
            minimum_idle_time_in_seconds=minimum_time_in_seconds,
            seconds_per_timestep=seconds_per_timestep,
        )


def test_the_cycle_estimate_is_the_hysteresis_cycle_below_the_minimum_output() -> None:
    """At 900 W of 1945 W, 16.13 MJ of band heat: 15435 s on, 17922 s off, 0.0270 starts in 900 s.

    Catches an estimate that uses the minimum times instead of the band, or swaps the on and the off phase.
    """
    band_heat_in_joule = Cycle.BAND_HEAT_IN_JOULE
    expected = 900.0 / (band_heat_in_joule / (1945.0 - 900.0) + band_heat_in_joule / 900.0)
    assert Cycle.starts(900.0) == pytest.approx(expected)
    assert Cycle.starts(900.0) == pytest.approx(0.02698, rel=1e-3)


@pytest.mark.parametrize("delivered_thermal_power_in_watt", [1945.0, 3000.0, 0.0, -500.0])
def test_a_step_at_or_above_the_minimum_output_or_without_load_has_no_start(delivered_thermal_power_in_watt: float) -> None:
    """At or above the minimum output the machine runs continuously; without load, or cooling, it does not cycle.

    Catches an estimate that counts starts for continuous running or divides by a zero load.
    """
    assert Cycle.starts(delivered_thermal_power_in_watt) == 0.0


def test_the_minimum_times_floor_the_on_and_the_off_phase() -> None:
    """With 10 kJ of band heat both phases last seconds; the minimum times stretch them to 600 s each.

    At 1000 W of 1945 W the band gives 10.6 s on and 10 s off; floored, a 900 s step holds 900 / 1200 = 0.75 starts.
    Without the floors it would hold 43.7. Catches an estimate that ignores the heat pump's minimum times.
    """
    assert Cycle.starts(1000.0, band_heat_in_joule=1e4) == pytest.approx(0.75)
    assert Cycle.starts(1000.0, band_heat_in_joule=1e4, minimum_time_in_seconds=0.0) == pytest.approx(
        900.0 / (1e4 / 945.0 + 1e4 / 1000.0)
    )


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"band_heat_in_joule": 0.0}, "band heat"),
        ({"band_heat_in_joule": math.inf}, "band heat"),
        ({"minimum_running_time_in_seconds": -1.0}, "0 or more"),
        ({"minimum_thermal_power_in_watt": -1.0}, "0 or more"),
        ({"delivered_thermal_power_in_watt": math.nan}, "finite"),
    ],
)
def test_the_cycle_estimate_refuses_impossible_inputs(arguments: Dict[str, float], match: str) -> None:
    """A band of no heat, a negative minimum time or output, or a non-finite power is refused, not estimated."""
    values = {
        "delivered_thermal_power_in_watt": 900.0,
        "minimum_thermal_power_in_watt": 1945.0,
        "band_heat_in_joule": Cycle.BAND_HEAT_IN_JOULE,
        "minimum_running_time_in_seconds": 600.0,
        "minimum_idle_time_in_seconds": 600.0,
        "seconds_per_timestep": 900.0,
        **arguments,
    }
    with pytest.raises(ValueError, match=match):
        Controller.starts_per_step_of_hysteresis_cycle(**values)


def test_whole_starts_carry_the_fraction_on() -> None:
    """0.95 and 0.1 give one start and 0.05 carried; 0.2 and 2.9 give three and 0.1; 0.3 and 0.2 none and 0.5."""
    one = Controller.whole_starts_of(accumulated_starts=0.95, starts_in_step=0.1)
    assert one.whole_starts == 1 and one.remainder == pytest.approx(0.05)
    three = Controller.whole_starts_of(accumulated_starts=0.2, starts_in_step=2.9)
    assert three.whole_starts == 3 and three.remainder == pytest.approx(0.1)
    assert Controller.whole_starts_of(accumulated_starts=0.3, starts_in_step=0.2) == AccumulatedStarts(0, 0.5)


def test_a_six_hour_cycle_at_900_s_gives_one_start_every_24_steps() -> None:
    """1/24 start per step crosses a whole start at the 24th, 48th and 72nd step, not one step late.

    Catches a rounding loss of the accumulator: 24 times 1/24 adds up to 0.9999999999999999.
    """
    accumulated = 0.0
    steps_with_a_start: List[int] = []
    for step in range(1, 73):
        result = Controller.whole_starts_of(accumulated_starts=accumulated, starts_in_step=900.0 / 21600.0)
        accumulated = result.remainder
        if result.whole_starts:
            steps_with_a_start.append(step)
    assert steps_with_a_start == [24, 48, 72]


@pytest.mark.parametrize(("accumulated", "in_step"), [(-0.1, 0.1), (0.1, -0.1), (math.nan, 0.1), (0.1, math.inf)])
def test_the_accumulator_refuses_a_negative_or_non_finite_count(accumulated: float, in_step: float) -> None:
    """A negative or non-finite count would publish a wrong whole start; it is refused."""
    with pytest.raises(ValueError, match="Carried and estimated starts"):
        Controller.whole_starts_of(accumulated_starts=accumulated, starts_in_step=in_step)


class ControllerRun:

    """The space-heating controller of a floor-heated house over several steps, its inputs fake outputs."""

    #: A heating step at 900 s: the buffer at its 37.5 °C target, the heat pump delivering 900 W of 1945 W.
    VALUES: ClassVar[Dict[str, float]] = {
        Controller.WaterTemperatureInput: 37.0,
        Controller.SpaceHeatingSupplyTemperatureSetInCelsius: 35.0,
        Controller.HeatingSeason: 1.0,
        Controller.DailyAverageOutsideTemperature: 0.0,
        Controller.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 38.0,
        Controller.WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius: 36.0,
        Controller.ActualPartLoadRatioSh: 0.5,
        Controller.DeliveredThermalPowerSpaceHeatingInWatt: 900.0,
        Controller.ThermalPowerAtMinimumLoadSpaceHeatingInWatt: 1945.0,
    }

    def __init__(self, *, seconds_per_timestep: int = 900, storage_heat_capacity_in_joule_per_kelvin: Optional[float] = 1.0e6):
        """Build the controller, starting in heating, with every input wired to a fake output."""
        config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
            SizingContext(
                set_heating_temperature_in_celsius=21.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
                minimum_running_time_in_seconds=600.0,
                minimum_idle_time_in_seconds=600.0,
            )
        )
        config.storage_heat_capacity_in_joule_per_kelvin = storage_heat_capacity_in_joule_per_kelvin
        self.controller = Controller(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
        )
        self.controller.state_at_step_start = self.controller.state_at_step_start._replace(
            mode=HeatPumpSpaceHeatingMode.HEATING
        )
        fakes: List[cp.ComponentOutput] = []
        for component_input in self.controller.inputs:
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            fakes.append(fake)
        self.fakes = fakes
        fft.add_global_index_of_components([*fakes, self.controller])
        self.stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, self.controller]))
        for fake in fakes:
            self.stsv.values[fake.global_index] = ControllerRun.VALUES.get(fake.field_name, 0.0)

    def starts(self) -> float:
        """The starts the controller publishes in the pass just run."""
        return self.stsv.values[self.controller.compressor_starts_channel.global_index]

    def step(self) -> float:
        """Run one converged step, one pass and the save, and return its published starts."""
        self.controller.i_simulate(timestep=0, stsv=self.stsv, force_convergence=False)
        starts = self.starts()
        self.controller.i_save_state()
        return starts


def test_the_controller_publishes_a_whole_start_once_the_accumulated_cycles_reach_one() -> None:
    """At 900 W of 1945 W on a 1 MJ/K buffer over its 10 K band, a step holds 0.0435 starts: one start in step 23.

    Catches a controller that publishes the fraction, that loses its accumulator between steps or never resets it.
    """
    run = ControllerRun()
    per_step = Cycle.starts(900.0, band_heat_in_joule=1.0e7)
    published = [run.step() for _ in range(46)]
    expected_first = math.ceil(1.0 / per_step)
    assert published[expected_first - 1] == 1
    assert sum(published) == math.floor(46 * per_step)
    assert all(starts in (0, 1) for starts in published)
    assert run.controller.state_at_step_start.cycle_starts_accumulated == pytest.approx(46 * per_step - sum(published))


def test_the_accumulator_survives_the_passes_of_a_step_and_only_the_save_advances_it() -> None:
    """Three passes of one step decide from the same saved fraction; only i_save_state commits the new one.

    Catches an accumulator that advances in every pass of a step, which would count one cycle per pass.
    """
    run = ControllerRun()
    run.controller.state_at_step_start = run.controller.state_at_step_start._replace(cycle_starts_accumulated=0.98)
    for _ in range(3):
        run.controller.i_simulate(timestep=0, stsv=run.stsv, force_convergence=False)
        run.controller.i_restore_state()
        assert run.starts() == 1
    assert run.controller.state_at_step_start.cycle_starts_accumulated == 0.98
    run.controller.i_save_state()
    assert run.controller.state_at_step_start.cycle_starts_accumulated == pytest.approx(
        0.98 + Cycle.starts(900.0, band_heat_in_joule=1.0e7) - 1.0
    )


def test_at_60_s_and_without_a_buffer_the_controller_estimates_nothing() -> None:
    """At 60 s the heat pump counts its own starts; without a buffer's heat capacity there is no cycle to estimate."""
    fine = ControllerRun(seconds_per_timestep=60)
    no_buffer = ControllerRun(storage_heat_capacity_in_joule_per_kelvin=None)
    no_buffer.controller.state_at_step_start = no_buffer.controller.state_at_step_start._replace(
        cycle_starts_accumulated=0.99
    )
    assert [fine.step() for _ in range(5)] == [0] * 5
    assert [no_buffer.step() for _ in range(5)] == [0] * 5


class HeatPumpCounters:

    """The heat pump's switch counters after one step, from a state before it."""

    @staticmethod
    def state(*, control_before: float, published_before: float) -> MoreAdvancedHeatPumpHPLibState:
        """A state with counters at 0 and the two modes of the step before."""
        return MoreAdvancedHeatPumpHPLibState(
            time_on_heating=0, time_off=0, time_on_cooling=0, on_off_previous=control_before,
            published_time_on_heating=0, published_time_off=0, published_on_off_previous=published_before,
            cumulative_thermal_energy_tot_in_watt_hour=0.0, cumulative_thermal_energy_space_heating_in_watt_hour=0.0,
            cumulative_thermal_energy_dhw_in_watt_hour=0.0, cumulative_electrical_energy_tot_in_watt_hour=0.0,
            cumulative_electrical_energy_space_heating_in_watt_hour=0.0,
            cumulative_electrical_energy_dhw_in_watt_hour=0.0, counter_switch_space_heating=0, counter_switch_dhw=0,
            counter_onoff=0, delta_t_secondary_side=5.0, delta_t_primary_side=0.0,
        )


@pytest.mark.parametrize(
    ("control_before", "published_before", "control_now", "published_now", "commanded", "on_off", "space_heating"),
    [
        (0, 0, 1, 1, 0, 1, 1),  # a start from off into space heating
        (1, 0, 1, 1, 0, 0, 0),  # space heating resumed after a ratio-0 step: the estimate's
        (1, 0, 1, 1, 2, 2, 2),  # the same with two commanded starts
        (1, 1, 1, 1, 1, 1, 1),  # continuous running with one commanded start
        (1, 0, 2, 2, 0, 1, 0),  # a hot-water start after a ratio-0 space-heating step
        (2, 2, 1, 1, 0, 0, 1),  # hot water to space heating: a switch, no start
    ],
)
def test_the_heat_pump_adds_the_commanded_starts_and_does_not_count_a_resumed_charge(
    control_before: float, published_before: float, control_now: float, published_now: float, commanded: int,
    on_off: int, space_heating: int,
) -> None:
    """The on/off and space-heating counters grow by the commanded starts; resumed space heating is no start of its own.

    Catches a heat pump that counts a ratio-0 step's end as a start beside the controller's estimate (twice the starts)
    or that drops the estimate.
    """
    counters = MoreAdvancedHeatPumpHPLib.switch_counters(
        on_off=published_now,
        control_on_off=control_now,
        state=HeatPumpCounters.state(control_before=control_before, published_before=published_before),
        commanded_starts=commanded,
    )
    assert (counters.on_off, counters.space_heating) == (on_off, space_heating)


def test_the_heat_pump_refuses_negative_commanded_starts() -> None:
    """A negative start would shrink a counter; it is refused."""
    with pytest.raises(ValueError, match="0 or more compressor starts"):
        MoreAdvancedHeatPumpHPLib.switch_counters(
            on_off=1, control_on_off=1, state=HeatPumpCounters.state(control_before=1, published_before=1), commanded_starts=-1
        )


def test_the_start_kpis_read_the_counter() -> None:
    """The counter 0, 1, 1, 3, 3, 4 over the states 0, 1, 1, 1, 0, 2 holds four starts, three of them in space heating.

    Catches a starts KPI that still counts the ``TimeOff`` transitions and so misses the commanded starts of a step in
    which the heat pump heated throughout.
    """
    counter = np.array([0.0, 1.0, 1.0, 3.0, 3.0, 4.0])
    state = np.array([0.0, 1.0, 1.0, 1.0, 0.0, 2.0])
    assert MoreAdvancedHeatPumpHPLib.heat_pump_starts(counter) == 4
    assert MoreAdvancedHeatPumpHPLib.heat_pump_starts(np.array([])) == 0
    assert MoreAdvancedHeatPumpHPLib.space_heating_starts(counter_on_off=counter, on_off_state=state) == 3
    with pytest.raises(ValueError, match="same steps"):
        MoreAdvancedHeatPumpHPLib.space_heating_starts(counter_on_off=counter, on_off_state=state[:-1])


def test_a_cycling_step_runs_its_share_of_the_minimum_output() -> None:
    """3000 W of 1945 W run the whole step, 972.5 W half of it, 0 W none; without a minimum output every step runs whole."""
    share = MoreAdvancedHeatPumpHPLib.cycling_running_share(
        delivered_thermal_power_in_watt=np.array([3000.0, 972.5, 0.0]), minimum_thermal_power_in_watt=1945.0
    )
    assert share.tolist() == pytest.approx([1.0, 0.5, 0.0])
    assert MoreAdvancedHeatPumpHPLib.cycling_running_share(
        delivered_thermal_power_in_watt=np.array([10.0]), minimum_thermal_power_in_watt=0.0
    ).tolist() == [1.0]
    with pytest.raises(ValueError, match="0 W or more"):
        MoreAdvancedHeatPumpHPLib.cycling_running_share(
            delivered_thermal_power_in_watt=np.array([10.0]), minimum_thermal_power_in_watt=-1.0
        )


@pytest.mark.parametrize(("seconds_per_timestep", "expected_hours"), [(60, 3 * 60 / 3600), (900, (1.0 + 0.5 + 1.0) * 900 / 3600)])
def test_the_heating_hours_count_a_cycling_step_at_its_share_above_the_threshold(
    seconds_per_timestep: int, expected_hours: float
) -> None:
    """Steps in space heating at 3000 W and 972.5 W and one hot-water step: 3 whole steps at 60 s, 2.5 at 900 s.

    Catches a heating-time KPI that counts a cycling 900 s step whole, or that applies the share at 60 s.
    """
    heat_pump = MoreAdvancedHeatPumpHPLib(
        config=PinnedTwin.configure().config_of(PinnedTwin.HEAT_PUMP),
        my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep),
    )
    hours = heat_pump.heating_running_time_in_hours(
        on_off_state=np.array([1.0, 1.0, 2.0, 0.0]),
        space_heating_thermal_power_in_watt=np.array([3000.0, 0.5 * heat_pump.minimum_thermal_output_power_in_watt, 0.0, 0.0]),
    )
    assert hours == pytest.approx(expected_hours)


class Sizing:

    """The heat pump, its buffer and its space-heating controller, resolved together by the sizing engine."""

    #: The facts of the building and the site the three read.
    SEED: ClassVar[SizingContext] = SizingContext(
        heating_load_in_watt=7780.75,
        heating_reference_temperature_in_celsius=-7.0,
        technical_room_location=InstallationLocation.HEATED_ZONE,
        heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
        set_heating_temperature_in_celsius=20.0,
    )

    @staticmethod
    def buffer(name: str) -> SimpleHotWaterStorageConfig:
        """A heat pump's buffer, sized at 50 l/kW."""
        config = SimpleHotWaterStorageConfig.preset_buffer(name)
        config.sizing_option = HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP
        return config

    @staticmethod
    def controller() -> MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig:
        """The preset space-heating controller."""
        return MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("ControllerSH")


def test_the_buffer_contributes_its_heat_capacity_and_the_controller_reads_it() -> None:
    """The buffer of 389.04 l contributes 1.613 MJ/K; the controller resolves to it and to the heat pump's 600 s times.

    Catches a fact that is not declared, computed from the wrong volume, or not bound to the controller's sized field.
    """
    heat_pump, buffer, controller = resolve_all(
        [MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump"), Sizing.buffer("Buffer"), Sizing.controller()],
        seed=Sizing.SEED,
    )
    volume_in_liter = buffer.volume_heating_water_storage_in_liter
    assert volume_in_liter == pytest.approx(389.04)
    assert SimpleHotWaterStorageConfig.sizing_facts(buffer, SizingContext()) == {
        "storage_heat_capacity_in_joule_per_kelvin": volume_in_liter * 0.992 * 4180.0
    }
    assert controller.storage_heat_capacity_in_joule_per_kelvin == pytest.approx(volume_in_liter * 0.992 * 4180.0)
    assert controller.heat_pump_minimum_running_time_in_seconds == heat_pump.minimum_running_time_in_seconds == 600
    assert controller.heat_pump_minimum_idle_time_in_seconds == heat_pump.minimum_idle_time_in_seconds == 600


def test_a_stated_heat_capacity_wins_and_a_machine_that_does_not_cycle_contributes_no_minimum_times() -> None:
    """A stated 2 MJ/K stays; a heat pump with cycling off contributes 0 s for both minimum times."""
    heat_pump = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    heat_pump.cycling_mode = False
    controller = Sizing.controller()
    controller.storage_heat_capacity_in_joule_per_kelvin = 2.0e6
    _, _, resolved = resolve_all([heat_pump, Sizing.buffer("Buffer"), controller], seed=Sizing.SEED)
    assert resolved.storage_heat_capacity_in_joule_per_kelvin == 2.0e6
    assert resolved.heat_pump_minimum_running_time_in_seconds == 0.0
    assert resolved.heat_pump_minimum_idle_time_in_seconds == 0.0


def test_two_buffers_are_refused_with_the_sources_snippet() -> None:
    """Beside two buffers the controller's bare fact is ambiguous; the engine names both and the sources to write."""
    with pytest.raises(SizingError, match="storage_heat_capacity_in_joule_per_kelvin") as refusal:
        resolve_all(
            [
                MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump"),
                Sizing.buffer("BufferA"),
                Sizing.buffer("BufferB"),
                Sizing.controller(),
            ],
            seed=Sizing.SEED,
        )
    assert "BufferA" in str(refusal.value) and "BufferB" in str(refusal.value)


def test_without_a_buffer_the_auto_field_is_refused_and_a_stated_none_resolves() -> None:
    """No buffer provides the heat capacity: AUTO is refused, a stated None resolves, and its controller estimates nothing."""
    with pytest.raises(ConfigSizingError, match="storage_heat_capacity_in_joule_per_kelvin"):
        resolve_all([MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump"), Sizing.controller()], seed=Sizing.SEED)
    controller = Sizing.controller()
    controller.storage_heat_capacity_in_joule_per_kelvin = None
    _, resolved = resolve_all([MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump"), controller], seed=Sizing.SEED)
    assert resolved.storage_heat_capacity_in_joule_per_kelvin is None
    estimate = Controller(config=resolved, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900))
    assert estimate.storage_heat_capacity_in_joule_per_kelvin is None


def test_the_grouped_twin_s_controller_reads_its_buffer_s_heat_capacity() -> None:
    """In the recorded grouped heat-pump twin the controller holds the buffer's heat capacity and the 600 s times.

    Catches a twin in which the fact does not bind, for example because a second store declared it.
    """
    configured = PinnedTwin.configure()
    buffer = configured.config_of(PinnedTwin.BUFFER)
    controller = configured.config_of("MoreAdvancedHeatPumpHPLibControllerSH")
    assert controller.storage_heat_capacity_in_joule_per_kelvin == pytest.approx(
        SimpleHotWaterStorageConfig.heat_capacity_in_joule_per_kelvin_of(buffer.volume_heating_water_storage_in_liter)
    )
    assert controller.heat_pump_minimum_running_time_in_seconds == 600
    assert controller.heat_pump_minimum_idle_time_in_seconds == 600


def test_the_heat_capacity_of_a_vessel_refuses_an_empty_one() -> None:
    """155.6 l take 0.645 MJ/K; a vessel of 0 l or of no finite volume is refused."""
    assert SimpleHotWaterStorageConfig.heat_capacity_in_joule_per_kelvin_of(155.6) == pytest.approx(155.6 * 0.992 * 4180.0)
    for volume_in_liter in (0.0, -1.0, math.nan):
        with pytest.raises(ValueError, match="finite volume above 0 l"):
            SimpleHotWaterStorageConfig.heat_capacity_in_joule_per_kelvin_of(volume_in_liter)
