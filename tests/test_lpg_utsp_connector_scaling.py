"""Test for running multiple requests with lpg utsp connector and scaling up the results."""
import os
import pytest

from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    TransportationDeviceSets,
    TravelRouteSets,
)
from tests import functions_for_testing as fft

from hisim import utils
from hisim import component
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_occupancy_scaling_with_utsp():
    """Test for testing if the scaling with the lpg utsp connector works when calculating several households."""

    # Set Simu Params
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    url = "http://134.94.131.167:443/api/v1/profilerequest"
    api_key = "OrjpZY93BcNWw8lKaMp0BEchbCc"
    household = [
        Households.CHR01_Couple_both_at_Work,
        Households.CHR02_Couple_30_64_age_with_work,
        Households.CHR03_Family_1_child_both_at_work,
    ]
    result_path = "results1"
    travel_route_set = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
    transportation_device_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW

    # Build Simu Params
    my_simulation_parameters = SimulationParameters.full_year(
        year=year, seconds_per_timestep=seconds_per_timestep
    )
    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        name="UTSPConnector",
        url=url,
        api_key=api_key,
        household=household,
        result_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        consumption=0,
        profile_with_washing_machine_and_dishwasher=True,
        predictive_control=False,
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    print("my occupancy initilaized.")

    number_of_outputs = fft.get_number_of_outputs([my_occupancy])
    stsv = component.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_occupancy])

    my_occupancy.i_simulate(0, stsv, False)
    number_of_residents = []
    heating_by_residents = []
    heating_by_devices = []
    electricity_consumption = []
    water_consumption = []
    for i in range(24 * 60 * 365):
        my_occupancy.i_simulate(i, stsv, False)
        number_of_residents.append(
            stsv.values[my_occupancy.number_of_residentsC.global_index]
        )
        heating_by_residents.append(
            stsv.values[my_occupancy.heating_by_residentsC.global_index]
        )
        heating_by_devices.append(
            stsv.values[my_occupancy.heating_by_devices_channel.global_index]
        )
        electricity_consumption.append(
            stsv.values[my_occupancy.electricity_outputC.global_index]
        )
        water_consumption.append(
            stsv.values[my_occupancy.water_consumptionC.global_index]
        )

    year_heating_by_occupancy = sum(heating_by_residents) / (seconds_per_timestep * 1e3)
    print("year heating by occupancy ", year_heating_by_occupancy)
    assert year_heating_by_occupancy == 1443.2325
