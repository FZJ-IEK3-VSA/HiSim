import pytest
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim import simulator as sim
from tests import functions_for_testing as fft


@pytest.mark.base
def test_photovoltaic():
    # Sets inputs
    # weather_location = "Aachen"
    seconds_per_timestep = 60
    power = 10

    repo = sim_repository.SimRepository()

    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(year=2021,
                                                                         seconds_per_timestep=seconds_per_timestep)

    # Weather: 6 outputs
    # PVS:  1 output

    # Sets Occupancy
    my_weather_config=weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.Aachen)
    my_weather = weather.Weather( config = my_weather_config, my_simulation_parameters = mysim)
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    my_pvs_config= generic_pv_system.PVSystem.get_default_config()
    my_pvs_config.power=power
    my_pvs = generic_pv_system.PVSystem(config=my_pvs_config,my_simulation_parameters=mysim)
    my_pvs.set_sim_repo(repo)
    my_pvs.i_prepare_simulation()
    number_of_outputs = fft.get_number_of_outputs([my_weather,my_pvs])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(number_of_outputs)

    my_pvs.t_outC.source_output = my_weather.air_temperature_output
    my_pvs.azimuthC.source_output = my_weather.azimuth_output
    my_pvs.DNIC.source_output = my_weather.DNI_output
    my_pvs.DNIextraC.source_output = my_weather.DNI_extra_output
    my_pvs.DHIC.source_output = my_weather.DHI_output
    my_pvs.GHIC.source_output = my_weather.GHI_output
    my_pvs.apparent_zenithC.source_output = my_weather.apparent_zenith_output
    my_pvs.wind_speedC.source_output = my_weather.wind_speed_output

    fft.add_global_index_of_components([my_weather,my_pvs])

    timestep = 655
    my_weather.i_simulate(timestep, stsv,  False)
    my_pvs.i_simulate(timestep, stsv,  False)
    assert abs(0.4532226665022684 - stsv.values[ my_pvs.electricity_outputC.global_index]) < 0.05
