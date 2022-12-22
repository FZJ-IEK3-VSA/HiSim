"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""

import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim import utils
from examples import household_with_fake_heater


@utils.measure_execution_time
def test_basic_household():
    """ Single day. """
    path = "../examples/household_with_fake_heater.py"
    func = "household_fake_heating"
    mysimpar = SimulationParameters.full_year_all_options(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
    log.information(os.getcwd())
    log.information("after simulation run:")
    # hn = household_with_fake_heater.household_fake_heating(my_simulation_parameters=mysimpar)
    # hn.my



# # clean
# # needs to be developed still and checked
# from hisim import component
# from hisim.components import loadprofilegenerator_connector
# from hisim.components import weather
# from hisim.components import building
# from hisim.loadtypes import LoadTypes, Units
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from hisim import utils
# from tests import functions_for_testing as fft
# import numpy as np

# seconds_per_timestep = 60
# my_simulation_parameters = SimulationParameters.full_year(
#     year=2021, seconds_per_timestep=seconds_per_timestep
# )
# my_occupancy_profile = "CH01"

# initial_internal_temperature_in_celsius = 20.0
# heating_reference_temperature_in_celsius = 12.5
# absolute_conditioned_floor_area_in_m2 = 121.2


# @utils.measure_execution_time
# def test_building_heat_demand():
#     """Test function for heating demand of the building module."""

#     # Set Residence
#     my_residence_config = (
#         building.BuildingConfig.get_default_german_single_family_home()
#     )
#     my_residence_config.initial_internal_temperature_in_celsius = (
#         initial_internal_temperature_in_celsius
#     )
#     my_residence_config.heating_reference_temperature_in_celsius = (
#         heating_reference_temperature_in_celsius
#     )
#     my_residence_config.absolute_conditioned_floor_area_in_m2 = (
#         absolute_conditioned_floor_area_in_m2
#     )
#     my_residence = building.Building(
#         config=my_residence_config, my_simulation_parameters=my_simulation_parameters
#     )

#     repo = component.SimRepository()

#     my_residence.set_sim_repo(repo)
#     my_residence.i_prepare_simulation()

#     tabula_conditioned_floor_area_in_m2 = my_residence.buildingdata["A_C_Ref"].values[0]
#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Check values from TABULA
#     # in tabula energy need for heating is given as q_h_nd, it is related to the conditioned floor area
#     q_h_nd_given_directly_from_tabula_in_kWh_per_m2_per_year = (
#         my_residence.buildingdata["q_h_nd"].values[0]
#     )
#     q_h_nd_given_directly_from_tabula_in_kWh_per_hour = (
#         q_h_nd_given_directly_from_tabula_in_kWh_per_m2_per_year
#         * tabula_conditioned_floor_area_in_m2
#         / (12 * 30 * 24)
#     )
#     log.information(
#         "energy need q_h_nd for heating from tabula related to conditioned floor area [kWh/(m2*year)] "
#         + str(q_h_nd_given_directly_from_tabula_in_kWh_per_m2_per_year)
#     )
#     log.information(
#         "energy need q_h_nd for heating from tabula related to conditioned floor area [kWh/(m2*hour] "
#         + str(q_h_nd_given_directly_from_tabula_in_kWh_per_m2_per_year / (12 * 30 * 24))
#     )
#     log.information(
#         "energy need q_h_nd for heating from tabula related to conditioned floor area [kWh/(m2*minute] "
#         + str(
#             q_h_nd_given_directly_from_tabula_in_kWh_per_m2_per_year
#             / (12 * 30 * 24 * 60)
#         )
#     )
#     log.information(
#         "energy need q_h_nd for heating from tabula [kWh/hour] "
#         + str(q_h_nd_given_directly_from_tabula_in_kWh_per_hour)
#         + "\n"
#     )

#     # Tabula formular for energy need for heating is given by q_h_nd = q_ht - eta_h_gn * (q_sol + q_int)
#     # where q_ht is the total_heat_transfer, eta_h_gn is the gain utilization factor for heating, q_sol is the solar heat load (or gain) during heating seasons
#     # and q_int are the internal heat sources (units kilowatthour per m2 per year).
#     # The variables are all related to the conditioned floor area A_C_ref.

#     # total_heat_transfer_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_ht"].values[0]
#     gain_utilization_factor_for_heating = my_residence.buildingdata["eta_h_gn"].values[
#         0
#     ]
#     # solar_heat_gain_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_sol"].values[0]
#     # internal_heat_gains_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_int"].values[0]
#     # energy_need_for_heating_calculated_from_other_tabula_data = (
#     #     total_heat_transfer_in_kilowatthour_per_m2_per_year
#     #     - gain_utilization_factor_for_heating
#     #     * (
#     #         solar_heat_gain_in_kilowatthour_per_m2_per_year
#     #         + internal_heat_gains_in_kilowatthour_per_m2_per_year
#     #     )
#     # )
#     # log.information(str(energy_need_for_heating_calculated_from_other_tabula_data))

#     # # check whether the tabula data and tabula calulation are equivalent (with 1% tolerance)
#     # np.testing.assert_allclose(energy_need_for_heating_given_directly_from_tabula, energy_need_for_heating_calculated_from_other_tabula_data, rtol=0.01)
#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Set Occupancy
#     my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
#         profile_name=my_occupancy_profile, name="Occupancy-1"
#     )
#     my_occupancy = loadprofilegenerator_connector.Occupancy(
#         config=my_occupancy_config,
#         my_simulation_parameters=my_simulation_parameters,
#     )
#     my_occupancy.set_sim_repo(repo)
#     my_occupancy.i_prepare_simulation()

#     # Set Weather
#     my_weather_config = weather.WeatherConfig.get_default(
#         location_entry=weather.LocationEnum.Aachen
#     )
#     my_weather = weather.Weather(
#         config=my_weather_config, my_simulation_parameters=my_simulation_parameters
#     )
#     my_weather.set_sim_repo(repo)
#     my_weather.i_prepare_simulation()
#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Fake inputs

#     # Fake power delivered
#     thermal_power_delivered_output = component.ComponentOutput(
#         "FakeThermalDeliveryMachine",
#         "ThermalDelivery",
#         LoadTypes.HEATING,
#         Units.WATT,
#     )
#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Set stsv
#     number_of_outputs = fft.get_number_of_outputs(
#         [
#             my_occupancy,
#             my_weather,
#             my_residence,
#             thermal_power_delivered_output,
#         ]
#     )

#     stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
#         number_of_outputs
#     )
#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Set source outputs for my residence

#     my_residence.temperature_outside_channel.source_output = (
#         my_weather.air_temperature_output
#     )
#     my_residence.altitude_channel.source_output = my_weather.altitude_output
#     my_residence.azimuth_channel.source_output = my_weather.azimuth_output
#     my_residence.direct_normal_irradiance_channel.source_output = my_weather.DNI_output
#     my_residence.direct_horizontal_irradiance_channel.source_output = (
#         my_weather.DHI_output
#     )
#     my_residence.occupancy_heat_gain_channel.source_output = (
#         my_occupancy.heating_by_residentsC
#     )

#     my_residence.thermal_power_delivered_channel.source_output = (
#         thermal_power_delivered_output
#     )

#     fft.add_global_index_of_components(
#         [
#             my_occupancy,
#             my_weather,
#             my_residence,
#             thermal_power_delivered_output,
#         ]
#     )

#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Show logging information of some outputs and run simulation
#     log.information(
#         "-----------------------------------------------------------------------------------------------------------------------------------------"
#     )
#     log.information("before simulation run:")

#     log.information(
#         "internal (occupancy) heat gains [W] "
#         + str(my_residence.internal_heat_gains_through_occupancy_in_watt)
#     )
#     log.information("outside temp (weather) [°C] " + str(stsv.values[my_weather.air_temperature_output.global_index])+ "\n")

#     log.information(
#         "thermal mass bulk temperature [°C] "
#         + str(stsv.values[my_residence.thermal_mass_temperature_channel.global_index])
#     )
#     log.information(
#         "heat loss [W] "
#         + str(stsv.values[my_residence.total_power_to_residence_channel.global_index])
#     )
#     log.information(
#         "solar gain Q_sol [W] "
#         + str(stsv.values[my_residence.solar_gain_through_windows_channel.global_index])
#     )
#     log.information(
#         "max heat demand [W] "
#         + str(
#             stsv.values[
#                 my_residence.var_max_thermal_building_demand_channel.global_index
#             ]
#         )
#         + "\n"
#     )
#     log.information(
#         "fake thermal power delivered Q_H_nd [W]  "
#         + str(stsv.values[thermal_power_delivered_output.global_index])
#         + "\n"
#     )

#     stsv.values[thermal_power_delivered_output.global_index] = 0
#     # Simulation
#     my_occupancy.i_simulate(0, stsv, False)
#     my_weather.i_simulate(0, stsv, False)
#     my_residence.i_simulate(0, stsv, False)

#     log.information(
#         "-----------------------------------------------------------------------------------------------------------------------------------------"
#     )
#     log.information("after simulation run:")
#     log.information("all outputs " + str(stsv.values))
#     log.information("occupancy outputs " + str(stsv.values[2:6]))
#     log.information("weather outputs " + str(stsv.values[6:15]))
#     log.information("residence outputs " + str(stsv.values[15:]) + "\n")
#     log.information(
#         "internal (occupancy) heat gains [W] "
#         + str(my_residence.internal_heat_gains_through_occupancy_in_watt)
#     )

#     log.information(
#         "thermal mass bulk temperature [°C]  "
#         + str(stsv.values[my_residence.thermal_mass_temperature_channel.global_index])
#     )
#     log.information(
#         "heat loss [W] "
#         + str(stsv.values[my_residence.total_power_to_residence_channel.global_index])
#     )
#     log.information(
#         "solar gain Q_sol [W] "
#         + str(stsv.values[my_residence.solar_gain_through_windows_channel.global_index])
#     )
#     log.information(
#         "max heat demand [W] "
#         + str(
#             stsv.values[
#                 my_residence.var_max_thermal_building_demand_channel.global_index
#             ]
#         )
#         + "\n"
#     )
#     log.information(
#         "fake thermal power delivered Q_H_nd [W] "
#         + str(stsv.values[thermal_power_delivered_output.global_index])
#         + "\n"
#     )

#     log.information(
#         "-----------------------------------------------------------------------------------------------------------------------------------------"
#     )

#     # -------------------------------------------------------------------------------------------------------------------------------------------------------------------
#     # Calculate energy need from building module data and compare with tabula data

#     # Tabula formular for energy need for heating is given by q_h_nd = q_ht - eta_h_gn * (q_sol + q_int)
#     q_h_nd_calculated_from_building_data = stsv.values[
#         my_residence.var_max_thermal_building_demand_channel.global_index
#     ] - gain_utilization_factor_for_heating * (
#         stsv.values[my_residence.solar_gain_through_windows_channel.global_index]
#         + my_residence.internal_heat_gains_through_occupancy_in_watt
#     )
#     q_h_nd_calculated_from_building_data_in_kW = (
#         q_h_nd_calculated_from_building_data / 1000
#     )
#     log.information(
#         "energy need Q_H_nd calculated from building data with max heat demand as Q_ht [kW] "
#         + str(q_h_nd_calculated_from_building_data_in_kW)
#     )
#     log.information(
#         "energy need Q_H_nd calculated from building data with max heat demand as Q_ht divided by conditioned floor area [kW/m2] "
#         + str(
#             q_h_nd_calculated_from_building_data_in_kW
#             / tabula_conditioned_floor_area_in_m2
#         )
#     )
#     log.information(
#         "-----------------------------------------------------------------------------------------------------------------------------------------"
#     )

#     # test whether energy need from tabula is equal to energy need from building module with 10% tolerance
#     # (you can maniulate the test result by changing the initial internal temperature or the heating reference temperature (see above))
#     np.testing.assert_allclose(
#         q_h_nd_given_directly_from_tabula_in_kWh_per_hour,
#         q_h_nd_calculated_from_building_data_in_kW,
#         rtol=0.1,
#     )
