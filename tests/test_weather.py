import component
from components import weather
import simulator as sim
def test_weather():
    stsv : component.SingleTimeStepValues = component.SingleTimeStepValues(8)
    year=2019
    seconds_per_timestep=60
    my_sim_params: sim.SimulationParameters = sim.SimulationParameters.full_year(year=year,
                                                                                 seconds_per_timestep=seconds_per_timestep)

    my_weather = weather.Weather(location="Aachen", my_simulation_parameters=my_sim_params)

    my_weather.t_outC.GlobalIndex = 0
    my_weather.DNIC.GlobalIndex = 1
    my_weather.DHIC.GlobalIndex = 2
    my_weather.GHIC.GlobalIndex = 3
    my_weather.altitudeC.GlobalIndex = 4
    my_weather.azimuthC.GlobalIndex = 5
    my_weather.apparent_zenithC.GlobalIndex = 6
    my_weather.wind_speedC.GlobalIndex = 7

    DNI = []
    for i in range(60*24*365):
        my_weather.i_simulate(i, stsv, 60, False)
        DNI.append(stsv.values[1])

    assert sum(DNI) > 950
