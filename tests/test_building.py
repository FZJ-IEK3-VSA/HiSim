from hisim import component
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim import log
import os
from hisim import utils
import datetime
import time
@utils.measure_execution_time
def test_building():
    # Sets inputs
    starttime = datetime.datetime.now()
    d4 = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Test Building start @ " + d4)

    t1 = time.perf_counter()
    weather_location = "Aachen"
    my_occupancy_profile = "CH01"
    building_code="DE.N.SFH.05.Gen.ReEx.001.001"
    bClass="medium"
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=seconds_per_timestep)

    stsv : component.SingleTimeStepValues = component.SingleTimeStepValues(20)
    #repo = component.SimRepository()
    t2 = time.perf_counter()
    log.profile("T2: " + str(t2-t1))
    # Set Occupancy
    my_occupancy = loadprofilegenerator_connector.Occupancy(profile_name=my_occupancy_profile, my_simulation_parameters=my_simulation_parameters)
    #my_occupancy.set_sim_repo( repo )

    t3 = time.perf_counter()
    log.profile("T2: " + str(t3 - t2))

    # Set Weather
    my_weather = weather.Weather(location=weather_location,my_simulation_parameters=my_simulation_parameters)
    #my_weather.set_sim_repo(repo)
    t4 = time.perf_counter()
    log.profile("T2: " + str(t4 - t3))

    # Set Residence
    my_residence = building.Building(building_code=building_code, bClass=bClass, my_simulation_parameters=my_simulation_parameters)

    # Fake energy delivered
    thermal_energy_delivered_output = component.ComponentOutput("FakeThermalDeliveryMachine",
                                                                "ThermalDelivery",
                                                                LoadTypes.Heating,
                                                                Units.Watt)
    t5 = time.perf_counter()
    log.profile("T2: " + str(t4 - t5))

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
    my_weather.DNIextraC.GlobalIndex = 11
    my_weather.apparent_zenithC.GlobalIndex = 12

    my_residence.t_mC.GlobalIndex = 13

    thermal_energy_delivered_output.GlobalIndex = 14
    my_residence.total_power_to_residenceC.GlobalIndex = 15
    my_residence.solar_gain_through_windowsC.GlobalIndex = 16
    #test building models for various time resolutions 
    #   -> assume weather and occupancy data from t=0 (time resolution 1 min )
    #   -> calculate temperature of building ( with no heating considered ) for varios time steps
    #   -> check if temperature difference is proportional to time step size ( > 0.1 °C per minute )
    t6 = time.perf_counter()
    log.profile("T2: " + str(t6 - t5))
    for seconds_per_timestep in [ 60, 60 * 15, 60 * 60 ]:
        
        log.trace("Seconds per Timestep: " + str(seconds_per_timestep) )
        my_residence.seconds_per_timestep = seconds_per_timestep
        
        # Simulates
        stsv.values[13] = 23
        #log.information(str(stsv.values))
        my_weather.i_simulate(0, stsv, False)
        log.information(str(stsv.values))
        my_occupancy.i_simulate(0, stsv, False)
        log.information(str(stsv.values))
        my_residence.i_simulate(0, stsv, False)
        log.information(str(stsv.values))
    
        log.information("Occupancy: {}\n".format(stsv.values[:4]))
        log.information("Weather: {}\n".format(stsv.values[4:10]))
        log.information("Residence: {}\n".format(stsv.values[10:]))

        log.information(str(stsv.values[11]))
        # todo: this needs to be corrected
        assert (stsv.values[13] - 23.0 ) < - 0.01 * ( seconds_per_timestep / 60 )
    t7 = time.perf_counter()
    log.profile("T2: " + str(t7 - t6))
    log.profile("T2: " + str(t7 - t6))
    starttime = datetime.datetime.now()
    d4 = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Finished @ " + d4)