"""Test for running multiple requests with lpg utsp connector and scaling up the results."""

from typing import Union, List, Tuple, Any
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
from dotenv import load_dotenv
from tests import functions_for_testing as fft
from hisim import component
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.simulationparameters import SimulationParameters
from hisim import log


load_dotenv()


@pytest.mark.utsp
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
        data_acquisition_mode_after_initialization,
    ) = initialize_lpg_utsp_connector_and_return_results(households=household)

    if (
        data_acquisition_mode_after_initialization
        == loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
    ):
        log.warning(
            "This test makes only sense if the UTSP can be used for data acquisition. Here the use of the UTSP was not possible. Therefore this test will be ignored."
        )

    else:

        log.information("number of residents in 1 household " + str(number_of_residents_one))

        # run occupancy for two identical households
        household_list = [
            Households.CHR02_Couple_30_64_age_with_work,
            Households.CHR02_Couple_30_64_age_with_work,
            # Households.CHR02_Couple_30_64_age_with_work,
            # Households.CHR02_Couple_30_64_age_with_work,
        ]
        (
            number_of_residents_two,
            heating_by_residents_two,
            heating_by_devices_two,
            electricity_consumption_two,
            water_consumption_two,
            data_acquisition_mode_after_initialization,
        ) = initialize_lpg_utsp_connector_and_return_results(households=household_list)

        log.information(f"number of residents in {len(household_list)} households " + str(number_of_residents_two))

        # now test if results are doubled when occupancy is initialzed with 2 households
        np.testing.assert_allclose(number_of_residents_two, len(household_list) * number_of_residents_one, rtol=0.01)
        np.testing.assert_allclose(heating_by_residents_two, len(household_list) * heating_by_residents_one, rtol=0.01)
        np.testing.assert_allclose(heating_by_devices_two, len(household_list) * heating_by_devices_one, rtol=0.01)
        np.testing.assert_allclose(
            electricity_consumption_two, len(household_list) * electricity_consumption_one, rtol=0.01
        )
        np.testing.assert_allclose(water_consumption_two, len(household_list) * water_consumption_one, rtol=0.01)


def initialize_lpg_utsp_connector_and_return_results(
    households: Union[JsonReference, List[JsonReference]]
) -> Tuple[
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
    Union[float, Any],
    loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode,
]:
    """Initialize the lpg utsp connector and simulate for one timestep."""
    # Set Simu Params
    year = 2021
    seconds_per_timestep = 60

    # Set Occupancy
    result_path = "lpg_utsp_scaling_test"
    travel_route_set = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance
    transportation_device_set = TransportationDeviceSets.Bus_and_one_30_km_h_Car
    charging_station_set = ChargingStationSets.Charging_At_Home_with_11_kW
    energy_intensity = EnergyIntensityType.EnergySaving
    guid = "guid should not be varied automatically"
    data_acquisition_mode = loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_UTSP

    # Build Simu Params
    my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

    # Build occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig(
        name="UTSPConnector",
        data_acquisition_mode=data_acquisition_mode,
        household=households,
        result_dir_path=result_path,
        travel_route_set=travel_route_set,
        transportation_device_set=transportation_device_set,
        charging_station_set=charging_station_set,
        consumption=0,
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

    number_of_outputs = fft.get_number_of_outputs([my_occupancy])
    stsv = component.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_occupancy])

    my_occupancy.i_simulate(0, stsv, False)

    timestep = 0
    my_occupancy.i_simulate(timestep, stsv, False)
    number_of_residents = stsv.values[my_occupancy.number_of_residents_channel.global_index]
    heating_by_residents = stsv.values[my_occupancy.heating_by_residents_channel.global_index]
    heating_by_devices = stsv.values[my_occupancy.heating_by_devices_channel.global_index]
    electricity_consumption = stsv.values[my_occupancy.electricity_output_channel.global_index]

    water_consumption = stsv.values[my_occupancy.water_consumption_channel.global_index]

    print(number_of_residents, heating_by_residents, heating_by_devices, electricity_consumption)
    return (
        number_of_residents,
        heating_by_residents,
        heating_by_devices,
        electricity_consumption,
        water_consumption,
        my_occupancy_data_acquisition_mode_after_initialization,
    )
