"""Test that the lpg utsp connector scales its profiles with the number of households.

The profiles come from the **local pylpg install**, not from the remote UTSP. This test used
to run against the live service, which meant a gateway timeout at the far end failed the build
for reasons that had nothing to do with HiSim -- and, before the fallback chain was deleted
(roadmap/pylpg_flakiness.md F1), meant something worse: an unreachable service quietly
substituted a shipped profile for a *different* household, so the ratios below were computed
from two unrelated profiles and then asserted to be exactly 2x. Generating locally makes the
test depend on nothing but this machine.

The connector class is still UtspLpgConnector and the utsp marker still selects it: both name
the component, not the remote service.
"""

from typing import List, Tuple
import pytest
import numpy as np
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
    EnergyIntensityType,
)
from utspclient.helpers.lpgpythonbindings import JsonReference
from tests import functions_for_testing as fft
from hisim import component
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.config import ComponentID


@pytest.mark.utsp
def test_occupancy_scaling_with_local_lpg():
    """Test that the connector scales its profiles with the household count, using local pylpg."""

    # run occupancy for one household only
    household = Households.CHR02_Couple_30_64_age_with_work
    my_occupancy, data_acquisition_mode_after_initialization = build_lpg_utsp_connector(households=household)
    # Assign global indices explicitly here so the process-wide component-index
    # registry mutation stays visible at the call site (see KB-4368 / KB-5627).
    fft.add_global_index_of_components([my_occupancy])
    (
        number_of_residents_one,
        heating_by_residents_one,
        heating_by_devices_one,
        electricity_consumption_one,
        water_consumption_one,
    ) = simulate_and_read_occupancy_outputs(my_occupancy)

    # The connector no longer rewrites its own acquisition mode when a source is unreachable
    # (roadmap/pylpg_flakiness.md F1), so reaching this line at all means local pylpg delivered.
    # The assertion pins that: a mode other than the configured one would mean the profiles above
    # came from a different household and every scaling ratio below would be meaningless.
    assert (
        data_acquisition_mode_after_initialization
        == loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG
    ), "the connector must run the configured mode, never substitute another profile source"

    # Guard against a vacuously-satisfied scaling test: if any
    # single-household baseline were zero, every assert_allclose(N * 0, 0)
    # below would pass even if the connector ignored the household count
    # entirely, giving false confidence that scaling works (issue #1788).
    # The baselines are summed over the first 24 hours (see
    # simulate_and_read_occupancy_outputs) to avoid timestep-0 zeros, but
    # these guards provide an explicit fail-loud check. They run before the
    # second profile generation so a zero baseline short-circuits without
    # spending minutes on an LPG run whose result cannot mean anything.
    assert number_of_residents_one > 0, "baseline residents must be non-zero for scaling test to be meaningful"
    assert heating_by_residents_one > 0, "baseline heating-by-residents must be non-zero"
    assert heating_by_devices_one > 0, "baseline heating-by-devices must be non-zero"
    assert electricity_consumption_one > 0, "baseline electricity must be non-zero"
    assert water_consumption_one > 0, "baseline water must be non-zero"

    log.information(f"number of residents in 1 household {number_of_residents_one}")

    # run occupancy for two identical households
    household_list = [
        Households.CHR02_Couple_30_64_age_with_work,
        Households.CHR02_Couple_30_64_age_with_work,
        # Households.CHR02_Couple_30_64_age_with_work,
        # Households.CHR02_Couple_30_64_age_with_work,
    ]
    my_occupancy, _ = build_lpg_utsp_connector(households=household_list)
    fft.add_global_index_of_components([my_occupancy])
    (
        number_of_residents_two,
        heating_by_residents_two,
        heating_by_devices_two,
        electricity_consumption_two,
        water_consumption_two,
    ) = simulate_and_read_occupancy_outputs(my_occupancy)

    log.information(f"number of residents in {len(household_list)} households {number_of_residents_two}")

    # now test if results are doubled when occupancy is initialzed with 2 households
    np.testing.assert_allclose(number_of_residents_two, len(household_list) * number_of_residents_one, rtol=0.01)
    np.testing.assert_allclose(heating_by_residents_two, len(household_list) * heating_by_residents_one, rtol=0.01)
    np.testing.assert_allclose(heating_by_devices_two, len(household_list) * heating_by_devices_one, rtol=0.01)
    np.testing.assert_allclose(
        electricity_consumption_two, len(household_list) * electricity_consumption_one, rtol=0.01
    )
    np.testing.assert_allclose(water_consumption_two, len(household_list) * water_consumption_one, rtol=0.01)


def build_lpg_utsp_connector(
    households: JsonReference | List[JsonReference]
) -> Tuple[
    loadprofilegenerator_utsp_connector.UtspLpgConnector,
    loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode,
]:
    """Build an ``UtspLpgConnector`` for the given household reference(s).

    Constructs the connector configuration and instance but performs no
    simulation step and does **not** mutate the process-wide component-index
    registry. The caller owns the registry lifecycle and must assign global
    indices (e.g. via :func:`fft.add_global_index_of_components`) before
    simulating the returned connector.

    Args:
        households: A single household reference or a list of household
            references to simulate with the LPG connector, generated locally by pylpg.

    Returns:
        A tuple of:
        - The constructed ``UtspLpgConnector`` instance (outputs not yet
          globally indexed).
        - data_acquisition_mode_after_initialization: The
          ``LpgDataAcquisitionMode`` the connector holds after initialization.
          It is always the configured mode: an unreachable source now fails the
          run instead of substituting another one, so the caller asserts on it
          rather than branching on it.
    """
    # Set Simu Params
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    result_path = "lpg_local_scaling_test"
    travel_route_set = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
    transportation_device_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
    energy_intensity = EnergyIntensityType.EnergySaving
    guid = "guid should not be varied automatically"
    data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG

    # Build Simu Params
    my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        component_id=ComponentID(name="UTSPConnector"),
        data_acquisition_mode=data_acquisition_mode,
        household=households,
        result_dir_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        profile_with_washing_machine_and_dishwasher=True,
        predictive_control=False,
        predictive=False,
        energy_intensity=energy_intensity,
        guid=guid,
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    my_occupancy_data_acquisition_mode_after_initialization = my_occupancy.utsp_config.data_acquisition_mode

    return my_occupancy, my_occupancy_data_acquisition_mode_after_initialization


def simulate_and_read_occupancy_outputs(
    my_occupancy: loadprofilegenerator_utsp_connector.UtspLpgConnector,
) -> Tuple[float, float, float, float, float]:
    """Run simulation steps and read back the occupancy output channels.

    Constructs a :class:`component.SingleTimeStepValues` buffer sized to the
    component's outputs, runs ``i_simulate`` over the first 24 hours, and
    reads the occupancy-related outputs by their global index.

    Energy, water, and heating outputs are **summed** across the 24-hour
    range rather than read at a single timestep. At timestep 0 (midnight)
    these channels may legitimately be zero (no activity), which would make
    ``assert_allclose(N * 0, 0)`` pass vacuously even if the connector
    ignored the household count (issue #1788). Summing over a full day
    guarantees a non-zero baseline so the scaling comparison is meaningful.
    ``number_of_residents`` is a constant count, so it is read once at
    timestep 0.

    The caller must have assigned global indices to ``my_occupancy``'s output
    channels beforehand (e.g. via
    :func:`fft.add_global_index_of_components`). This function deliberately
    does **not** perform that mutation so the process-wide registry lifecycle
    stays explicit and owned by the caller (see KB-4368).

    Args:
        my_occupancy: An ``UtspLpgConnector`` whose output channels already have
            global indices assigned.

    Returns:
        A tuple of:
        - number_of_residents: Total number of residents across the household(s)
          (read at timestep 0; constant across the simulation).
        - heating_by_residents: Sum of heating energy attributable to residents
          over the first 24 hours.
        - heating_by_devices: Sum of heating energy attributable to devices
          over the first 24 hours.
        - electricity_consumption: Sum of electricity consumption over the
          first 24 hours.
        - water_consumption: Sum of water consumption over the first 24 hours.
    """
    stsv = component.SingleTimeStepValues(fft.get_number_of_outputs([my_occupancy]))

    # number_of_residents is a constant count; read it once at timestep 0.
    my_occupancy.i_simulate(0, stsv, False)
    number_of_residents = stsv.values[my_occupancy.number_of_residents_channel.global_index]

    # Sum energy/water/heating outputs over the first 24 hours so the baseline
    # is non-zero by construction. At timestep 0 these channels may legitimately
    # be zero (issue #1788), which would make scaling assertions vacuously pass.
    seconds_per_timestep = my_occupancy.my_simulation_parameters.seconds_per_timestep
    timesteps_per_day = int(24 * 3600 / seconds_per_timestep)

    heating_by_residents = 0.0
    heating_by_devices = 0.0
    electricity_consumption = 0.0
    water_consumption = 0.0

    for timestep in range(timesteps_per_day):
        my_occupancy.i_simulate(timestep, stsv, False)
        heating_by_residents += stsv.values[my_occupancy.heating_by_residents_channel.global_index]
        heating_by_devices += stsv.values[my_occupancy.heating_by_devices_channel.global_index]
        electricity_consumption += stsv.values[my_occupancy.electricity_output_channel.global_index]
        water_consumption += stsv.values[my_occupancy.water_consumption_channel.global_index]

    return (
        number_of_residents,
        heating_by_residents,
        heating_by_devices,
        electricity_consumption,
        water_consumption,
    )
