"""Test for heat demand calculation in the building module.

The aim is to compare the calculated heat demand in the building module with the heat demand given by TABULA.
"""

import datetime
import time
from hisim import component
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft
import numpy as np

seconds_per_timestep = 60
my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )
my_occupancy_profile = "CH01"

initial_internal_temperature_in_celsius = 20.0
heating_reference_temperature_in_celsius = 4.4
absolute_conditioned_floor_area_in_m2 = 121.2

@utils.measure_execution_time
def test_building_heat_demand():
    """Test function for heating demand of the building module."""

    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
        )
    my_residence_config.initial_internal_temperature_in_celsius = initial_internal_temperature_in_celsius
    my_residence_config.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
    my_residence_config.absolute_conditioned_floor_area_in_m2 = absolute_conditioned_floor_area_in_m2
    my_residence = building.Building(
            config=my_residence_config, my_simulation_parameters=my_simulation_parameters)


    repo = component.SimRepository()

    my_residence.set_sim_repo(repo)
    my_residence.i_prepare_simulation()

    tabula_conditioned_floor_area_in_m2 = my_residence.buildingdata["A_C_Ref"].values[0]
    # in tabula given as q_h_nd
    energy_need_for_heating_given_directly_from_tabula = my_residence.buildingdata["q_h_nd"].values[0]
    log.information("energy need Q_H_nd for heating from tabula related to conditioned floor area [kWh/(m2*year)] " +str(energy_need_for_heating_given_directly_from_tabula))
    log.information("energy need Q_H_nd for heating from tabula related to conditioned floor area [kWh/(m2*day] " +str(energy_need_for_heating_given_directly_from_tabula/(12*30*24)))
    log.information("energy need Q_H_nd for heating from tabula related to conditioned floor area [kWh/(m2*hour] " +str(energy_need_for_heating_given_directly_from_tabula/(12*30*24*60)))
    log.information("energy need Q_H_nd for heating from tabula [kWh/hour] " +str(energy_need_for_heating_given_directly_from_tabula * tabula_conditioned_floor_area_in_m2/(12*30*24*60))+ "\n")

    # Tabula formular for energy need for heating is given by q_h_nd = q_ht - eta_h_gn * (q_sol + q_int)
    # where q_ht is the total_heat_transfer, eta_h_gn is the gain utilization factor for heating, q_sol is the solar heat load (or gain) during heating seasons
    # and q_int are the internal heat sources (units kilowatthour per m2 per year). 
    # The variables are all related to the conditioned floor area A_C_ref.

    # total_heat_transfer_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_ht"].values[0]
    gain_utilization_factor_for_heating = my_residence.buildingdata["eta_h_gn"].values[0]
    # solar_heat_gain_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_sol"].values[0]
    # internal_heat_gains_in_kilowatthour_per_m2_per_year = my_residence.buildingdata["q_int"].values[0]
    # energy_need_for_heating_calculated_from_other_tabula_data = total_heat_transfer_in_kilowatthour_per_m2_per_year - gain_utilization_factor_for_heating * (solar_heat_gain_in_kilowatthour_per_m2_per_year + internal_heat_gains_in_kilowatthour_per_m2_per_year)
    # log.information(str(energy_need_for_heating_calculated_from_other_tabula_data))

    # # check whether the tabula data and tabula calulation are equivalent (with 1% tolerance)
    # np.testing.assert_allclose(energy_need_for_heating_given_directly_from_tabula, energy_need_for_heating_calculated_from_other_tabula_data, rtol=0.01)

    # Set Occupancy
    my_occupancy_config = loadprofilegenerator_connector.OccupancyConfig(
        profile_name=my_occupancy_profile, name="Occupancy-1"
    )
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_occupancy.set_sim_repo(repo)
    my_occupancy.i_prepare_simulation()

  

    # Set Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.Aachen
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Fake power delivered
    thermal_power_delivered_output = component.ComponentOutput(
        "FakeThermalDeliveryMachine",
        "ThermalDelivery",
        LoadTypes.HEATING,
        Units.WATT,
    )

    # Fake outside temperature from weather
    air_outside_temperature_output = component.ComponentOutput(
        "FakeOutsideAirTemp",
        "AirTemp",
        LoadTypes.TEMPERATURE,
        Units.CELSIUS,
    )
    number_of_outputs = fft.get_number_of_outputs(
        [my_occupancy, my_weather, my_residence, thermal_power_delivered_output, air_outside_temperature_output]
    )
    

    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    # my_residence.temperature_outside_channel.source_output = (
    #     my_weather.air_temperature_output
    # )
    my_residence.temperature_outside_channel.source_output = (
        air_outside_temperature_output
    )
    my_residence.altitude_channel.source_output = my_weather.altitude_output
    my_residence.azimuth_channel.source_output = my_weather.azimuth_output
    my_residence.direct_normal_irradiance_channel.source_output = (
        my_weather.DNI_output
    )
    my_residence.direct_horizontal_irradiance_channel.source_output = (
        my_weather.DHI_output
    )
    my_residence.occupancy_heat_gain_channel.source_output = (
        my_occupancy.heating_by_residentsC
    )

    my_residence.thermal_power_delivered_channel.source_output = (
        thermal_power_delivered_output
    )

    fft.add_global_index_of_components(
        [my_occupancy, my_weather, my_residence, thermal_power_delivered_output, air_outside_temperature_output]
    )
    log.information("-----------------------------------------------------------------------------------------------------------------------------------------")
    log.information("before simulation run:")

    log.information("internal (occupancy) heat gains [W] " + str(my_residence.internal_heat_gains_through_occupancy_in_watt))
    # log.information("outside temp (weather) [°C] " + str(stsv.values[my_weather.air_temperature_output.global_index])+ "\n")
    log.information("outside temp (set fake) [°C] " + str(stsv.values[air_outside_temperature_output.global_index])+ "\n")
    log.information("thermal mass bulk temperature [°C] " + str(stsv.values[my_residence.thermal_mass_temperature_channel.global_index]))
    log.information("heat loss [W] " + str(stsv.values[my_residence.total_power_to_residence_channel.global_index]))
    log.information("solar gain Q_sol [W] " + str(stsv.values[my_residence.solar_gain_through_windows_channel.global_index]))
    log.information("max heat demand [W] " + str(stsv.values[my_residence.var_max_thermal_building_demand_channel.global_index])+ "\n")
    log.information("fake thermal power delivered Q_H_nd [W]  " + str(stsv.values[thermal_power_delivered_output.global_index])+ "\n")

    # stsv.values[my_weather.air_temperature_output.global_index] = 4.4
    stsv.values[air_outside_temperature_output.global_index] = 4.4
    stsv.values[thermal_power_delivered_output.global_index] = 0

    my_occupancy.i_simulate(0, stsv, False)
    my_weather.i_simulate(0, stsv, False)
    my_residence.i_simulate(0, stsv, False)
    log.information("-----------------------------------------------------------------------------------------------------------------------------------------")
    log.information("after simulation run:")
    log.information("all outputs " + str(stsv.values))
    log.information("occupancy outputs " + str(stsv.values[2:6]))
    log.information("weather outputs " + str(stsv.values[6:15]))
    log.information("residence outputs " + str(stsv.values[15:]) + "\n")
    log.information("internal (occupancy) heat gains [W] " + str(my_residence.internal_heat_gains_through_occupancy_in_watt))
    # log.information("outside temp (weather) [°C] " + str(stsv.values[my_weather.air_temperature_output.global_index])+ "\n")
    log.information("outside temp (set fake) [°C] " + str(stsv.values[air_outside_temperature_output.global_index])+ "\n")
    log.information("thermal mass bulk temperature [°C]  " + str(stsv.values[my_residence.thermal_mass_temperature_channel.global_index]))
    log.information("heat loss [W] " + str(stsv.values[my_residence.total_power_to_residence_channel.global_index]))
    log.information("solar gain Q_sol [W] " + str(stsv.values[my_residence.solar_gain_through_windows_channel.global_index]))
    log.information("max heat demand [W] " + str(stsv.values[my_residence.var_max_thermal_building_demand_channel.global_index])+ "\n")
    log.information("fake thermal power delivered Q_H_nd [W] " + str(stsv.values[thermal_power_delivered_output.global_index])+ "\n")

    log.information("-----------------------------------------------------------------------------------------------------------------------------------------")
    # Tabula formular for energy need for heating is given by q_h_nd = q_ht - eta_h_gn * (q_sol + q_int)
    q_h_nd_calculated_from_building_data = stsv.values[my_residence.var_max_thermal_building_demand_channel.global_index] - gain_utilization_factor_for_heating * (stsv.values[my_residence.solar_gain_through_windows_channel.global_index] + my_residence.internal_heat_gains_through_occupancy_in_watt)

    log.information("energy need Q_H_nd calculated from building data with max heat demand as Q_ht [kW] " + str(q_h_nd_calculated_from_building_data / 1000))
    log.information("energy need Q_H_nd calculated from building data with max heat demand as Q_ht divided by conditioned floor area [kW/m2] " + str(q_h_nd_calculated_from_building_data / (1000 * tabula_conditioned_floor_area_in_m2)))
