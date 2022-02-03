import component
from components import weather
from components import pvs
import simulator as sim

def test_photovoltaic():
    # Sets inputs
    weather_location = "Aachen"
    seconds_per_timestep = 60
    power = 10
    year = 2019

    stsv : component.SingleTimeStepValues = component.SingleTimeStepValues(10)
    # Weather: 6 outputs
    # PVS:  1 output


    my_sim_params: sim.SimulationParameters = sim.SimulationParameters.full_year(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)

    my_weather = weather.Weather(location="Aachen", my_simulation_parameters=my_sim_params)



    my_pvs = pvs.PVSystem(power=power,location="Aachen")

    my_pvs.t_outC.SourceOutput = my_weather.t_outC
    my_pvs.azimuthC.SourceOutput = my_weather.azimuthC
    my_pvs.DNIC.SourceOutput = my_weather.DNIC
    my_pvs.DNIextraC.SourceOutput = my_weather.DNIextraC
    my_pvs.DHIC.SourceOutput = my_weather.DHIC
    my_pvs.GHIC.SourceOutput = my_weather.GHIC
    my_pvs.apparent_zenithC.SourceOutput = my_weather.apparent_zenithC
    my_pvs.wind_speedC.SourceOutput = my_weather.wind_speedC

    my_weather.t_outC.GlobalIndex = 0
    my_weather.azimuthC.GlobalIndex = 1
    my_weather.DNIC.GlobalIndex = 2
    my_weather.DHIC.GlobalIndex = 3
    my_weather.GHIC.GlobalIndex = 4
    my_weather.altitudeC.GlobalIndex = 5
    my_weather.azimuthC.GlobalIndex = 6
    my_weather.apparent_zenithC.GlobalIndex = 7
    my_weather.wind_speedC.GlobalIndex = 8

    my_pvs.electricity_outputC.GlobalIndex = 9

    my_weather.i_simulate(655, stsv, seconds_per_timestep, False)
    my_pvs.i_simulate(655, stsv, seconds_per_timestep, False)
    assert 0.4235107699732713 == stsv.values[9]
