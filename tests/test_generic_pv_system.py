from hisim import component
from hisim.components import weather
from hisim.components import generic_pv_system
from hisim import simulator as sim
from tests import functions_for_testing as fft

def test_photovoltaic():
    # Sets inputs
    weather_location = "Aachen"
    seconds_per_timestep = 60
    power = 10

    repo = component.SimRepository()

    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(year=2021,
                                                                           seconds_per_timestep=seconds_per_timestep)
    mysim.reset_system_config( predictive = True )

    # Weather: 6 outputs
    # PVS:  1 output

    # Sets Occupancy
    my_weather_config=weather.WeatherConfig(location = weather_location)
    my_weather = weather.Weather( config = my_weather_config, my_simulation_parameters = mysim, my_simulation_repository = repo )
    my_weather.set_sim_repo(repo)
    my_pvs_config= generic_pv_system.PVSystem.get_default_config()
    my_pvs_config.power=power
    my_pvs = generic_pv_system.PVSystem(config=my_pvs_config,my_simulation_parameters=mysim, my_simulation_repository = repo )
    my_pvs.set_sim_repo(repo)

    number_of_outputs = fft.get_number_of_outputs([my_weather,my_pvs])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(number_of_outputs)

    my_pvs.t_outC.SourceOutput = my_weather.t_outC
    my_pvs.azimuthC.SourceOutput = my_weather.azimuthC
    my_pvs.DNIC.SourceOutput = my_weather.DNIC
    my_pvs.DNIextraC.SourceOutput = my_weather.DNIextraC
    my_pvs.DHIC.SourceOutput = my_weather.DHIC
    my_pvs.GHIC.SourceOutput = my_weather.GHIC
    my_pvs.apparent_zenithC.SourceOutput = my_weather.apparent_zenithC
    my_pvs.wind_speedC.SourceOutput = my_weather.wind_speedC

    fft.add_global_index_of_components([my_weather,my_pvs])

    timestep = 655
    my_weather.i_simulate(timestep, stsv,  False)
    my_pvs.i_simulate(timestep, stsv,  False)
    assert abs(0.4532226665022684- stsv.values[ my_pvs.electricity_outputC.GlobalIndex]) <0.05
