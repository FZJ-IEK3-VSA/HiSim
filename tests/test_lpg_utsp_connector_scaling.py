"""Test for running multiple requests with lpg utsp connector and scaling up the results."""

from typing import Union, List, Tuple, Any
import pytest
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


@pytest.mark.base
def test_occupancy_scaling_with_utsp():
    """Test for testing if the scaling with the lpg utsp connector works when calculating several households."""

    # run occupancy for one household only
    household = Households.CHR02_Couple_30_64_age_with_work
    (
        number_of_residents_one,
        heating_by_residents_one,
        heating_by_devices_one,
        electricity_consumption_one,
        water_consumption_one,
    ) = initialize_lpg_utsp_connector_and_return_results(households=household)

    log.information(
        "number of residents in 1 household " + str(number_of_residents_one)
    )

    # run occupancy for two identical households
    household_list = [
        Households.CHR02_Couple_30_64_age_with_work,
        Households.CHR02_Couple_30_64_age_with_work,
    ]
    (
        number_of_residents_two,
        heating_by_residents_two,
        heating_by_devices_two,
        electricity_consumption_two,
        water_consumption_two,
    ) = initialize_lpg_utsp_connector_and_return_results(households=household_list)

    log.information(
        "number of residents in 2 households " + str(number_of_residents_two)
    )

    # now test if results are doubled when occupancy is initialzed with 2 households
    assert number_of_residents_two == 2 * number_of_residents_one
    assert heating_by_residents_two == 2 * heating_by_residents_one
    assert heating_by_devices_two == 2 * heating_by_devices_one
    assert electricity_consumption_two == 2 * electricity_consumption_one
    assert water_consumption_two == 2 * water_consumption_one


def initialize_lpg_utsp_connector_and_return_results(
    households: Union[JsonReference, List[JsonReference]]
) -> Tuple[
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
]:
    """Initialize the lpg utsp connector and simulate for one timestep."""
    # Set Simu Params
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    url = "http://134.94.131.167:443/api/v1/profilerequest"
    api_key = "OrjpZY93BcNWw8lKaMp0BEchbCc"
    result_path = "lpg_utsp_scaling_test"
    travel_route_set = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
    transportation_device_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
    energy_intensity = EnergyIntensityType.EnergySaving

    # Build Simu Params
    my_simulation_parameters = SimulationParameters.full_year(
        year=year, seconds_per_timestep=seconds_per_timestep
    )
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        name="UTSPConnector",
        url=url,
        api_key=api_key,
        household=households,
        result_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        consumption=0,
        profile_with_washing_machine_and_dishwasher=True,
        predictive_control=False,
        energy_intensity=energy_intensity,
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    number_of_outputs = fft.get_number_of_outputs([my_occupancy])
    stsv = component.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_occupancy])

    my_occupancy.i_simulate(0, stsv, False)

    timestep = 10
    my_occupancy.i_simulate(timestep, stsv, False)
    number_of_residents = stsv.values[
        my_occupancy.number_of_residents_channel.global_index
    ]
    heating_by_residents = stsv.values[
        my_occupancy.heating_by_residents_channel.global_index
    ]
    heating_by_devices = stsv.values[
        my_occupancy.heating_by_devices_channel.global_index
    ]
    electricity_consumption = stsv.values[
        my_occupancy.electricity_output_channel.global_index
    ]
    print(electricity_consumption)
    water_consumption = stsv.values[my_occupancy.water_consumption_channel.global_index]

    return (
        number_of_residents,
        heating_by_residents,
        heating_by_devices,
        electricity_consumption,
        water_consumption,
    )
