"""Test for scaling of the energy system.

Depending on the building floor area and the rooftop area the energy system components,
such as pv system, battery, heat pumps, water storage, etc. need to be scaled up.
"""
# clean
import math
import numpy as np
import pytest
from typing import Tuple, List, Any
from hisim import component
from hisim.components import weather, building, loadprofilegenerator_connector, generic_pv_system, advanced_heat_pump_hplib, advanced_battery_bslib, simple_hot_water_storage
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft

@pytest.mark.buildingtest
@utils.measure_execution_time
def test_energy_system_scalability():
    """Test function for the scability of the whole energy system."""

    (
        original_pv_electricity_output
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=1,
        scaling_factor_for_rooftop_area=1
    )

    # calculate pv electricity output and respective scaling factor when
    # the rooftop floor area is scaled with factor=5
    (
        scaled_pv_electricity_output
    ) = simulation_for_one_timestep(
        scaling_factor_for_absolute_conditioned_floor_area=5,
        scaling_factor_for_rooftop_area=5
    )

    # now compare the two results and test if pv output is upscaled correctly
    np.testing.assert_allclose(scaled_pv_electricity_output, original_pv_electricity_output * 5, rtol=0.01)

def simulation_for_one_timestep(
    scaling_factor_for_absolute_conditioned_floor_area: int, scaling_factor_for_rooftop_area: int,
) -> Any: #Tuple[List[float], Any]:
    """Test function for the example house for one timestep."""

    # Set simu params
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )
    repo = component.SimRepository()
    # Set building inputs
    absolute_conditioned_floor_area_in_m2 = 121.2 * scaling_factor_for_absolute_conditioned_floor_area
    rooftop_area_in_m2 = 168.9 * scaling_factor_for_rooftop_area
    
    # Set Residence
    my_residence_config = (
        building.BuildingConfig.get_default_german_single_family_home()
    )
    my_residence_config.absolute_conditioned_floor_area_in_m2 = (
        absolute_conditioned_floor_area_in_m2
    )
    my_residence = building.Building(
        config=my_residence_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    my_residence.set_sim_repo(repo)
    my_residence.i_prepare_simulation()

    log.information(my_residence_config.building_code)
    log.information("Rooftop area" + str(rooftop_area_in_m2))

    # Set Occupancy
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
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
    
    # Set PV
    my_pv_config = generic_pv_system.PVSystemConfig.get_scaled_PV_system(rooftop_area_in_m2=rooftop_area_in_m2)
    my_pv = generic_pv_system.PVSystem(my_simulation_parameters=my_simulation_parameters, config=my_pv_config)
    my_pv.set_sim_repo(repo)
    my_pv.i_prepare_simulation()

    number_of_outputs = fft.get_number_of_outputs(
        [my_occupancy, my_weather, my_residence, my_pv]
    )
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )
    my_residence.temperature_outside_channel.source_output = (
        my_weather.air_temperature_output
    )
    my_residence.altitude_channel.source_output = my_weather.altitude_output
    my_residence.azimuth_channel.source_output = my_weather.azimuth_output
    my_residence.direct_normal_irradiance_channel.source_output = my_weather.DNI_output
    my_residence.direct_horizontal_irradiance_channel.source_output = (
        my_weather.DHI_output
    )
    my_residence.occupancy_heat_gain_channel.source_output = (
        my_occupancy.heating_by_residentsC
    )
    
    my_pv.t_outC.source_output = my_weather.air_temperature_output
    my_pv.azimuthC.source_output = my_weather.azimuth_output
    my_pv.DNIC.source_output = my_weather.DNI_output
    my_pv.DNIextraC.source_output = my_weather.DNI_extra_output
    my_pv.DHIC.source_output = my_weather.DHI_output
    my_pv.GHIC.source_output = my_weather.GHI_output
    my_pv.apparent_zenithC.source_output = my_weather.apparent_zenith_output
    my_pv.wind_speedC.source_output = my_weather.wind_speed_output

    fft.add_global_index_of_components([my_occupancy, my_weather, my_residence, my_pv])

    my_residence.seconds_per_timestep = seconds_per_timestep

    # Simulates
    my_occupancy.i_simulate(60000, stsv, False)
    my_weather.i_simulate(60000, stsv, False)
    my_residence.i_simulate(60000, stsv, False)
    my_pv.i_simulate(60000,stsv,False)
    
    
    pv_electricity_output = stsv.values[-1]
    log.information(f"Occupancy Outputs: {stsv.values[0:5]}")
    log.information(f"Weather Outputs: {stsv.values[5:15]}")
    log.information(f"Residence Outputs: {stsv.values[15:24]}")
    log.information(f"PV Outputs: {stsv.values[24:]}")
    
    return pv_electricity_output


