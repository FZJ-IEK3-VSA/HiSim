"""The Building warns about an unconnected internal-heat-gain input only when the finished wiring left it so.

``HeatingByResidents`` and ``HeatingByDevices`` are optional inputs of the ``Building``; when nothing
feeds them the gains are 0 W, which changes the thermal balance, so the component says so. The check
used to run in ``i_prepare_simulation``, but ``Simulator.run_all_timesteps`` resolves every input's
``source_output`` in ``connect_all_components`` *after* preparing the components, so it warned on
every run, the wired ones included (renovisorissues #35). These tests drive one ``Building`` through
the same order -- declare the links, prepare, resolve the links, simulate -- through the
``ComponentWrapper`` the ``Simulator`` uses, with the synthetic day of the one-day snapshot harness.
"""

from typing import Dict, List, Set

import pytest

from hisim import component as cp
from hisim import log
from hisim.caching import CacheSettings
from hisim.component_wrapper import ComponentWrapper
from hisim.components.building import Building
from hisim.config import ComponentID
from tests import functions_for_testing as fft
from tests.test_building_one_day_snapshot import OneDaySnapshot, SyntheticDayProfiles

#: The channels whose connection the warning is about.
INTERNAL_HEAT_GAIN_CHANNELS: Set[str] = {"occupancy_heat_gain_channel", "device_heat_gain_channel"}
#: How many timesteps each run simulates; more than one, so a repeated warning would show.
SIMULATED_TIMESTEPS: int = 3


@pytest.fixture(name="recorded_warnings")
def fixture_recorded_warnings(monkeypatch, tmp_path) -> List[str]:
    """Record every ``log.warning`` message and keep the solar-gain cache out of the working tree."""
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))
    messages: List[str] = []

    def record(message: str, logging_message_path=None) -> None:
        del logging_message_path
        messages.append(message)

    monkeypatch.setattr(log, "warning", record)
    return messages


def internal_heat_gain_warnings(messages: List[str]) -> List[str]:
    """Return the Building's unconnected-internal-heat-gain warnings among ``messages``."""
    return [message for message in messages if "Internal heat gains from" in message]


def run_building(wire_internal_heat_gains: bool, recorded_warnings: List[str]) -> List[float]:
    """Wire, prepare, connect and simulate one Building the way ``Simulator.run_all_timesteps`` does.

    Each channel of the synthetic day gets a source output of the snapshot harness's synthetic
    source, and the link to it is declared with ``connect_input`` as a system setup or the
    energy-system loader declares it. The internal-heat-gain channels are left out when
    ``wire_internal_heat_gains`` is false. The warnings recorded while preparing are checked
    here: whatever the wiring, preparing the component must not report on inputs that are not
    resolved yet.

    Returns:
        List[float]: the component's ``InternalHeatGainsFromOccupancy`` output (residents plus devices),
        per timestep.
    """
    building = Building(
        config=OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME),
        my_simulation_parameters=OneDaySnapshot.simulation_parameters(),
    )
    building.set_sim_repo(OneDaySnapshot.repository_with_synthetic_weather())
    input_vectors = {
        channel_name: vector
        for channel_name, vector in SyntheticDayProfiles.input_vectors().items()
        if wire_internal_heat_gains or channel_name not in INTERNAL_HEAT_GAIN_CHANNELS
    }
    source_outputs: Dict[str, cp.ComponentOutput] = {}
    for channel_name in input_vectors:
        channel: cp.ComponentInput = getattr(building, channel_name)
        source_outputs[channel_name] = cp.ComponentOutput(
            OneDaySnapshot.SYNTHETIC_SOURCE_NAME,
            channel.field_name,
            channel.loadtype,
            channel.unit,
            component_id=ComponentID(name=OneDaySnapshot.SYNTHETIC_SOURCE_NAME),
        )
        building.connect_input(channel.field_name, OneDaySnapshot.SYNTHETIC_SOURCE_NAME, channel.field_name)
    fft.add_global_index_of_components([*source_outputs.values(), building])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*source_outputs.values(), building]))

    wrapper = ComponentWrapper(building, is_cachable=False, connect_automatically=False)
    wrapper.prepare_calculation()
    assert not internal_heat_gain_warnings(recorded_warnings), (
        "The Building reported on its internal-heat-gain inputs before the wiring was resolved: "
        f"{recorded_warnings}"
    )
    wrapper.connect_inputs(list(source_outputs.values()))

    internal_gains: List[float] = []
    for timestep in range(SIMULATED_TIMESTEPS):
        for channel_name, source_output in source_outputs.items():
            stsv.values[source_output.global_index] = input_vectors[channel_name][timestep]
        building.i_save_state()
        # Two convergence iterations per timestep, as the simulator may run them.
        for _ in range(2):
            building.i_restore_state()
            building.i_simulate(timestep, stsv, False)
        internal_gains.append(stsv.values[building.internal_heat_gains_from_residents_and_devices_channel.global_index])
    return internal_gains


@pytest.mark.base
def test_wired_internal_heat_gains_do_not_warn(recorded_warnings) -> None:
    """A Building whose gains inputs are wired reads the gains and logs no unconnected-input warning."""
    internal_gains = run_building(wire_internal_heat_gains=True, recorded_warnings=recorded_warnings)

    assert not internal_heat_gain_warnings(recorded_warnings)
    expected = SyntheticDayProfiles.OCCUPANCY_HEAT_GAIN_IN_WATT + SyntheticDayProfiles.DEVICE_HEAT_GAIN_IN_WATT
    assert expected > 0.0
    assert internal_gains == [expected] * SIMULATED_TIMESTEPS


@pytest.mark.base
def test_unwired_internal_heat_gains_warn_once_and_simulate_with_zero_watt(recorded_warnings) -> None:
    """A Building whose gains inputs are not wired warns once per input and simulates with 0 W gains."""
    internal_gains = run_building(wire_internal_heat_gains=False, recorded_warnings=recorded_warnings)

    assert internal_heat_gain_warnings(recorded_warnings) == [
        "Building 'Building': the 'HeatingByResidents' input is not connected. "
        "Internal heat gains from occupants default to 0 W.",
        "Building 'Building': the 'HeatingByDevices' input is not connected. "
        "Internal heat gains from devices default to 0 W.",
    ]
    assert internal_gains == [0.0] * SIMULATED_TIMESTEPS
