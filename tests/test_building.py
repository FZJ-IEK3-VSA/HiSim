from hisim import component
from hisim.components import occupancy
from hisim.components import weather
from hisim.components import building
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

def test_building():
    # Sets inputs
    weather_location = "Aachen"
    my_occupancy_profile = "CH01"
    building_code="DE.N.SFH.05.Gen.ReEx.001.001"
    bClass="medium"
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=seconds_per_timestep)
    stsv : component.SingleTimeStepValues = component.SingleTimeStepValues(13)

    # Set Occupancy
    my_occupancy = occupancy.Occupancy(profile=my_occupancy_profile, my_simulation_parameters=my_simulation_parameters)


    # Set Weather
    my_weather = weather.Weather(location=weather_location,my_simulation_parameters=my_simulation_parameters)

    # Set Residence
    my_residence = building.Building(building_code=building_code, bClass=bClass, my_simulation_parameters=my_simulation_parameters)

    # Fake energy delivered
    thermal_energy_delivered_output = component.ComponentOutput("FakeThermalDeliveryMachine",
                                                                "ThermalDelivery",
                                                                LoadTypes.Heating,
                                                                Units.Watt)


    assert 1 == 1
    my_residence.t_outC.SourceOutput = my_weather.t_outC
    my_residence.altitudeC.SourceOutput = my_weather.altitudeC
    my_residence.azimuthC.SourceOutput = my_weather.azimuthC
    my_residence.DNIC.SourceOutput = my_weather.DNIC
    my_residence.DHIC.SourceOutput = my_weather.DHIC
    my_residence.occupancy_heat_gainC.SourceOutput = my_occupancy.heating_by_residentsC
    my_residence.thermal_energy_deliveredC.SourceOutput = thermal_energy_delivered_output

    my_occupancy.number_of_residentsC.GlobalIndex = 0
    my_occupancy.heating_by_residentsC.GlobalIndex = 1
    my_occupancy.electricity_outputC.GlobalIndex = 2
    my_occupancy.water_consumptionC.GlobalIndex = 3

    my_weather.t_outC.GlobalIndex = 4
    my_weather.DNIC.GlobalIndex = 5
    my_weather.DHIC.GlobalIndex = 6
    my_weather.GHIC.GlobalIndex = 7
    my_weather.altitudeC.GlobalIndex = 8
    my_weather.azimuthC.GlobalIndex = 9
    my_weather.wind_speedC.GlobalIndex = 10

    my_residence.t_mC.GlobalIndex = 11
    thermal_energy_delivered_output.GlobalIndex = 12

    # Simulates
    stsv.values[11] = 23
    print(stsv.values)
    my_weather.i_simulate(0, stsv, seconds_per_timestep, False)
    print(stsv.values)
    my_occupancy.i_simulate(0, stsv, seconds_per_timestep, False)
    print(stsv.values)
    my_residence.i_simulate(0, stsv, seconds_per_timestep, False)
    print(stsv.values)

    print("Occupancy: {}\n".format(stsv.values[:4]))
    print("Weather: {}\n".format(stsv.values[4:10]))
    print("Residence: {}\n".format(stsv.values[10:]))
    # #assert False
    print(stsv.values[11])
    assert (stsv.values[11] - 22.98) <0.01
