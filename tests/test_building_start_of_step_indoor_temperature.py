"""The building publishes its indoor air temperature at the start of the step, the value the previous step ended at.

The value is meant for a component that decides on the room temperature inside the step, such as the heat
distribution system's clamp, which keeps its return from falling below the room. Such a component must read a value
the step's iteration does not move. The building's ``TemperatureIndoorAir`` is computed from the heat delivered in the
same pass, so a decision on it changes the heat, the heat changes the room temperature, and the two can chase each
other from pass to pass. ``TemperatureIndoorAirAtStartOfStepInCelsius`` is the indoor air temperature the previous
step converged to, and the configured initial temperature on the first step.

The tests drive the ``Building`` component itself through the synthetic day of ``test_building_one_day_snapshot.py``,
in both of its configuration variants, with the simulator's per-step protocol: save the state once, restore it before
every pass. Each step runs two passes: first one with more heat delivered, as an earlier pass of an iteration can bring,
then the pass with the day's own inputs, which the next step starts from.
"""

import dataclasses
from typing import Dict, List

import pytest

from hisim import component as cp
from hisim.caching import CacheSettings
from hisim.components.building.building import Building
from tests import functions_for_testing as fft
from tests.test_building_one_day_snapshot import OneDaySnapshot, SyntheticDayProfiles


@dataclasses.dataclass(frozen=True)
class StartOfStepDayResult:
    """The indoor air temperatures one variant publishes over the synthetic day, one entry per step.

    Attributes:
        initial_temperature_in_celsius: The configured initial indoor temperature of the variant, in °C.
        indoor_air_temperatures_in_celsius: ``TemperatureIndoorAir`` of the pass with the day's own inputs, in °C.
        start_of_step_temperatures_in_celsius: ``TemperatureIndoorAirAtStartOfStepInCelsius`` of that pass, in °C.
        indoor_air_temperatures_with_extra_heat_in_celsius: ``TemperatureIndoorAir`` of the pass with more heat
            delivered, in °C.
        start_of_step_temperatures_with_extra_heat_in_celsius: ``TemperatureIndoorAirAtStartOfStepInCelsius`` of the
            pass with more heat delivered, in °C.
    """

    initial_temperature_in_celsius: float
    indoor_air_temperatures_in_celsius: List[float]
    start_of_step_temperatures_in_celsius: List[float]
    indoor_air_temperatures_with_extra_heat_in_celsius: List[float]
    start_of_step_temperatures_with_extra_heat_in_celsius: List[float]


class StartOfStepDay:
    """Runs the ``Building`` through the synthetic day with two passes per step and records its indoor temperatures.

    For example, at a step where the day delivers 6000 W, the first pass gets 11000 W and the second 6000 W. The
    building's ``TemperatureIndoorAir`` differs between the two passes; its start-of-step value must not. The pass
    with the day's own inputs comes last, so every step starts from the state those inputs produce.
    """

    #: The heat the first pass of every step gets on top of the day's delivered heat, in W. It moves the room by a
    #: visible amount within one 15-minute step of either variant.
    EXTRA_THERMAL_POWER_DELIVERED_IN_WATT: float = 5000.0
    #: The input channel the extra heat goes to.
    DELIVERED_HEAT_CHANNEL_NAME: str = "thermal_power_delivered_channel"

    @classmethod
    def run(cls, variant_name: str) -> StartOfStepDayResult:
        """Run one configuration variant of the snapshot through the synthetic day.

        Args:
            variant_name: A variant of ``OneDaySnapshot.variant_names()``.

        Returns:
            The indoor air temperatures both passes of every step published.
        """
        config = OneDaySnapshot.config_for(variant_name)
        building = Building(config=config, my_simulation_parameters=OneDaySnapshot.simulation_parameters())
        building.set_sim_repo(OneDaySnapshot.repository_with_synthetic_weather())
        source_outputs = OneDaySnapshot.wire_synthetic_inputs(building)
        input_vectors = SyntheticDayProfiles.input_vectors()

        fft.add_global_index_of_components([*source_outputs.values(), building])
        delivered_heat_index = source_outputs[cls.DELIVERED_HEAT_CHANNEL_NAME].global_index
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*source_outputs.values(), building]))
        building.i_prepare_simulation()

        temperatures: Dict[str, List[float]] = {
            name: [] for name in ("indoor", "start", "indoor_with_extra_heat", "start_with_extra_heat")
        }
        for timestep in range(SyntheticDayProfiles.TIMESTEPS):
            for channel_name, source_output in source_outputs.items():
                stsv.values[source_output.global_index] = input_vectors[channel_name][timestep]
            delivered_heat_in_watt = input_vectors[cls.DELIVERED_HEAT_CHANNEL_NAME][timestep]
            building.i_save_state()

            stsv.values[delivered_heat_index] = delivered_heat_in_watt + cls.EXTRA_THERMAL_POWER_DELIVERED_IN_WATT
            building.i_restore_state()
            building.i_simulate(timestep, stsv, False)
            outputs = OneDaySnapshot.read_outputs(building, stsv)
            temperatures["indoor_with_extra_heat"].append(outputs[Building.TemperatureIndoorAir])
            temperatures["start_with_extra_heat"].append(outputs[Building.TemperatureIndoorAirAtStartOfStepInCelsius])

            stsv.values[delivered_heat_index] = delivered_heat_in_watt
            building.i_restore_state()
            building.i_simulate(timestep, stsv, False)
            outputs = OneDaySnapshot.read_outputs(building, stsv)
            temperatures["indoor"].append(outputs[Building.TemperatureIndoorAir])
            temperatures["start"].append(outputs[Building.TemperatureIndoorAirAtStartOfStepInCelsius])

        return StartOfStepDayResult(
            initial_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
            indoor_air_temperatures_in_celsius=temperatures["indoor"],
            start_of_step_temperatures_in_celsius=temperatures["start"],
            indoor_air_temperatures_with_extra_heat_in_celsius=temperatures["indoor_with_extra_heat"],
            start_of_step_temperatures_with_extra_heat_in_celsius=temperatures["start_with_extra_heat"],
        )


@pytest.fixture(name="start_of_step_days", scope="module")
def fixture_start_of_step_days(tmp_path_factory) -> Dict[str, StartOfStepDayResult]:
    """Run both variants once, with the building's solar-gain cache in a temporary directory.

    The building files its solar-gain series in the cache directory; redirecting it keeps the working tree clean.
    """
    patcher = pytest.MonkeyPatch()
    patcher.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path_factory.mktemp("start_of_step_solar_cache")))
    try:
        return {variant_name: StartOfStepDay.run(variant_name) for variant_name in OneDaySnapshot.variant_names()}
    finally:
        patcher.undo()


@pytest.mark.base
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_the_start_of_step_indoor_temperature_is_the_previous_steps_indoor_temperature(
    variant_name: str, start_of_step_days: Dict[str, StartOfStepDayResult]
) -> None:
    """Every step's start-of-step value equals the previous step's indoor air temperature; the first is the initial one.

    The first step publishes the variant's configured initial temperature (22 °C by default, 19 °C scaled).

    Catches a value read after this step's update (it would equal the same step's indoor temperature) or one that is
    not saved with the state (it would stay at the initial value). The day has to move the room for either to show,
    which the first assertion checks.
    """
    day = start_of_step_days[variant_name]
    indoor = day.indoor_air_temperatures_in_celsius
    at_start = day.start_of_step_temperatures_in_celsius
    assert any(later != earlier for earlier, later in zip(indoor, indoor[1:])), "the synthetic day never moves the room"
    assert at_start[0] == day.initial_temperature_in_celsius
    assert at_start[1:] == indoor[:-1]


@pytest.mark.base
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_the_start_of_step_indoor_temperature_is_the_same_in_every_pass_of_a_step(
    variant_name: str, start_of_step_days: Dict[str, StartOfStepDayResult]
) -> None:
    """A pass with more heat delivered publishes another indoor temperature but the same start-of-step value.

    For example, 5000 W more in the first pass warms the room of that pass, while both passes publish the indoor
    temperature the previous step ended at. Catches a start-of-step value computed from the pass's own heat.
    """
    day = start_of_step_days[variant_name]
    moved_steps = [
        timestep
        for timestep, (with_extra_heat, without) in enumerate(
            zip(day.indoor_air_temperatures_with_extra_heat_in_celsius, day.indoor_air_temperatures_in_celsius)
        )
        if with_extra_heat != without
    ]
    assert moved_steps, "the extra heat never moves the room, so the test cannot tell the two values apart"
    assert day.start_of_step_temperatures_with_extra_heat_in_celsius == day.start_of_step_temperatures_in_celsius
