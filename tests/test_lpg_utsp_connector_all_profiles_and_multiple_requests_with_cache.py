"""Test for running multiple requests with lpg utsp connector from a cache directory path.

This test can only be executed locally when you have a local directory where you want to store
all lpg household profiles and do calculation with it.
"""

# from typing import Union, List, Tuple, Any, Dict
# import pytest
# from utspclient.helpers.lpgdata import Households
# from utspclient.helpers.lpgpythonbindings import JsonReference
# from dotenv import load_dotenv

# from tests import functions_for_testing as fft
# from hisim import component
# from hisim.components import loadprofilegenerator_utsp_connector
# from hisim.simulationparameters import SimulationParameters

# load_dotenv()


# def initialize_lpg_utsp_connector_and_cache_results_if_not_already_done(
#     household: Union[JsonReference, List[JsonReference]],
#     cache_dir_path: str,
#     my_simulation_parameters: SimulationParameters,
# ) -> Any:
#     """Initialize the lpg utsp connector for one household."""

#     # Build occupancy
#     my_occupancy_config = (
#         loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
#     )

#     my_occupancy_config.cache_dir_path = cache_dir_path
#     my_occupancy_config.household = household

#     my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
#         config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
#     )

#     return my_occupancy


# def initialize_lpg_utsp_connector_and_return_results(
#     households: Union[JsonReference, List[JsonReference]],
#     cache_dir_path: str,
#     my_simulation_params: SimulationParameters,
# ) -> Tuple[
#     Union[float, Any],
#     Union[float, Any],
#     Union[float, Any],
#     Union[float, Any],
#     Union[float, Any],
# ]:
#     """Initialize the lpg utsp connector and simulate for one timestep."""

#     # Build occupancy
#     my_occupancy = initialize_lpg_utsp_connector_and_cache_results_if_not_already_done(
#         household=households,
#         cache_dir_path=cache_dir_path,
#         my_simulation_parameters=my_simulation_params,
#     )

#     number_of_outputs = fft.get_number_of_outputs([my_occupancy])
#     stsv = component.SingleTimeStepValues(number_of_outputs)

#     # Add Global Index and set values for fake Inputs
#     fft.add_global_index_of_components([my_occupancy])

#     my_occupancy.i_simulate(0, stsv, False)

#     timestep = 10
#     my_occupancy.i_simulate(timestep, stsv, False)
#     number_of_residents = stsv.values[
#         my_occupancy.number_of_residents_channel.global_index
#     ]
#     heating_by_residents = stsv.values[
#         my_occupancy.heating_by_residents_channel.global_index
#     ]
#     heating_by_devices = stsv.values[
#         my_occupancy.heating_by_devices_channel.global_index
#     ]
#     electricity_consumption = stsv.values[
#         my_occupancy.electricity_output_channel.global_index
#     ]
#     water_consumption = stsv.values[my_occupancy.water_consumption_channel.global_index]

#     return (
#         number_of_residents,
#         heating_by_residents,
#         heating_by_devices,
#         electricity_consumption,
#         water_consumption,
#     )


# @pytest.mark.utsp
# def test_utsp_calculation_for_multiple_households_using_caches():
#     """Test if utsp can handle multiple households using local caches."""

#     # local cache dir path where all lpg household profiles are cached
#     cache_dir_path = "/fast/home/k-rieck/lpg-utsp-data"

#     # Build Simu Params
#     my_simulation_params = SimulationParameters.full_year(
#         year=2021, seconds_per_timestep=60
#     )

#     household_list = [
#         Households.CHR02_Couple_30_64_age_with_work,
#         Households.CHR60_Family_1_toddler_one_at_work_one_at_home,
#         Households.CHR43_Single_man_with_1_child_with_work,
#     ]
#     # run occupancy for each household alone
#     value_dict: Dict = {
#         "electricity_consumption": [],
#         "water_consumption": [],
#         "heating_by_devices": [],
#         "heating_by_residents": [],
#         "number_of_residents": [],
#     }
#     for household in household_list:
#         (
#             number_of_residents,
#             heating_by_residents,
#             heating_by_devices,
#             electricity_consumption,
#             water_consumption,
#         ) = initialize_lpg_utsp_connector_and_return_results(
#             households=household,
#             cache_dir_path=cache_dir_path,
#             my_simulation_params=my_simulation_params,
#         )

#         # write values to dict
#         value_dict["electricity_consumption"].append(electricity_consumption)
#         value_dict["heating_by_devices"].append(heating_by_devices)
#         value_dict["heating_by_residents"].append(heating_by_residents)
#         value_dict["water_consumption"].append(water_consumption)
#         value_dict["number_of_residents"].append(number_of_residents)

#     # make sums over household results
#     electricity_consumption_sum = sum(value_dict["electricity_consumption"])

#     heating_by_residents_sum = sum(value_dict["heating_by_residents"])

#     water_consumption_sum = sum(value_dict["water_consumption"])

#     heating_by_devices_sum = sum(value_dict["heating_by_devices"])

#     number_of_residents_sum = sum(value_dict["number_of_residents"])

#     # run occupancy for all households together
#     (
#         number_of_residents_two,
#         heating_by_residents_two,
#         heating_by_devices_two,
#         electricity_consumption_two,
#         water_consumption_two,
#     ) = initialize_lpg_utsp_connector_and_return_results(
#         households=household_list,
#         cache_dir_path=cache_dir_path,
#         my_simulation_params=my_simulation_params,
#     )

#     # now test if results are scaled when occupancy is initialzed with multiple households
#     assert number_of_residents_two == number_of_residents_sum
#     assert heating_by_residents_two == heating_by_residents_sum
#     assert heating_by_devices_two == heating_by_devices_sum
#     assert electricity_consumption_two == electricity_consumption_sum
#     assert water_consumption_two == water_consumption_sum


# Recommendation: Run the function and the test below in the comments better on cluster because this can take some time!
# The function and test below is for calculating and caching all lpg household profiles

# def calculate_all_lpg_households(cache_dir_path: str) -> Tuple[List, List]:
#     """Calculate all lpg households."""

#     # Build Simu Params
#     my_simulation_params = SimulationParameters.full_year(
#         year=2021, seconds_per_timestep=60
#     )

#     lpg_household_strings = vars(Households)["__annotations__"]
#     successfully_calculated_lpg_profiles: List = []
#     unsuccessfully_calculated_lpg_profiles: List = []

#     for key in lpg_household_strings:

#         lpg_household_attribute = getattr(Households, key)
#         print(lpg_household_attribute, type(lpg_household_attribute))
#         print("Initializing lpg utsp connector. \n")
#         try:
#             initialize_lpg_utsp_connector_and_cache_results_if_not_already_done(
#                 household=lpg_household_attribute,
#                 cache_dir_path=cache_dir_path,
#                 my_simulation_parameters=my_simulation_params
#             )
#             successfully_calculated_lpg_profiles.append(key)
#         except Exception:
#             print(
#                 "Some error occured during initialization of lpg utsp connector. Maybe the request failed at the UTSP. \n"
#             )
#             unsuccessfully_calculated_lpg_profiles.append(key)

#     return successfully_calculated_lpg_profiles, unsuccessfully_calculated_lpg_profiles

# @pytest.mark.base
# def test_utsp_requests_for_all_profiles_and_cache_them_if_not_cached_already():
#     """Test utsp requests for all household profiles and cache them in cache_dir_path if not already done."""

#     # execution
#     (
#         successfully_calculated_lpg_profiles_b,
#         unsuccessfully_calculated_lpg_profiles_b,
#     ) = calculate_all_lpg_households(cache_dir_path="/fast/home/k-rieck/lpg-utsp-data")

#     print(
#         "successfully calculated profiles ",
#         successfully_calculated_lpg_profiles_b,
#         "\n",
#     )
#     print(
#         "unsuccessfully calculated profiles ",
#         unsuccessfully_calculated_lpg_profiles_b,
#         "\n",
#     )
