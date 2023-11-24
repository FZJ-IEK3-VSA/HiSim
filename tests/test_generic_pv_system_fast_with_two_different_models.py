"""Test for generic pv system with different modules from different module databases (test works only with simphotovoltaicfast method)."""
import pytest
from tests import functions_for_testing as fft
from hisim import sim_repository
from hisim import component
from hisim.components import weather, building
from hisim.components import generic_pv_system
from hisim import simulator as sim
from hisim import log


@pytest.mark.base
def test_pv_system_for_two_different_modules_from_two_different_databases_with_simphotovoltaicfast() -> None:
    """Test the pv system for two modules."""

    module_name_one = "Hanwha HSL60P6-PA-4-250T [2013]"
    module_database_one = (
        generic_pv_system.PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
    )

    pv_electricity_output_in_watt = (
        simulate_pv_system_for_one_timestep_and_one_specific_pv_module(
            module_name=module_name_one, module_database=module_database_one
        )
    )
    log.information(
        "pv module: "
        + str(module_name_one)
        + " with pv electricity output [W]: "
        + str(pv_electricity_output_in_watt)
        + "\n"
    )

    module_name_two = "Trina Solar TSM-410DE09"
    module_database_two = (
        generic_pv_system.PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
    )

    pv_electricity_output_in_watt = (
        simulate_pv_system_for_one_timestep_and_one_specific_pv_module(
            module_name=module_name_two, module_database=module_database_two
        )
    )
    log.information(
        "pv module: "
        + str(module_name_two)
        + " with pv electricity output [W]: "
        + str(pv_electricity_output_in_watt)
    )


def simulate_pv_system_for_one_timestep_and_one_specific_pv_module(
    module_name: str, module_database: generic_pv_system.PVLibModuleAndInverterEnum
) -> float:
    """Test generic pv system."""

    seconds_per_timestep = 60

    repo = sim_repository.SimRepository()

    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Set Building
    my_building_information = building.BuildingInformation(
        config=building.BuildingConfig.get_default_german_single_family_home()
    )
    log.information(
        "rooftop size in m2 " + str(my_building_information.scaled_rooftop_area_in_m2)
    )

    # Sets Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Set PV System
    my_pvs_config = generic_pv_system.PVSystemConfig.get_scaled_pv_system(
        rooftop_area_in_m2=my_building_information.scaled_rooftop_area_in_m2,
        share_of_maximum_pv_power=1.0,
        module_name=module_name,
        module_database=module_database,
        load_module_data=False,
    )
    log.information("pv power in watt " + str(my_pvs_config.power_in_watt))

    my_pvs = generic_pv_system.PVSystem(
        config=my_pvs_config, my_simulation_parameters=mysim
    )
    my_pvs.set_sim_repo(repo)
    my_pvs.i_prepare_simulation()
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_pvs])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    my_pvs.t_out_channel.source_output = my_weather.air_temperature_output
    my_pvs.azimuth_channel.source_output = my_weather.azimuth_output
    my_pvs.dni_channel.source_output = my_weather.dni_output
    my_pvs.dni_extra_channel.source_output = my_weather.dni_extra_output
    my_pvs.dhi_channel.source_output = my_weather.dhi_output
    my_pvs.ghi_channel.source_output = my_weather.ghi_output
    my_pvs.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
    my_pvs.wind_speed_channel.source_output = my_weather.wind_speed_output

    fft.add_global_index_of_components([my_weather, my_pvs])

    timestep = 655
    my_weather.i_simulate(timestep, stsv, False)
    my_pvs.i_simulate(timestep, stsv, False)

    pv_electricity_output_in_watt = float(stsv.values[
        my_pvs.electricity_output_channel.global_index
    ])

    return pv_electricity_output_in_watt
