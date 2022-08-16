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
from tests import functions_for_testing as fft

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


    #repo = component.SimRepository()
    t2 = time.perf_counter()
    log.profile("T2: " + str(t2-t1))
    # Set Occupancy
    my_occupancy_config= loadprofilegenerator_connector.OccupancyConfig(profile_name=my_occupancy_profile)
    my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters)
    #my_occupancy.set_sim_repo( repo )

    t3 = time.perf_counter()
    log.profile("T2: " + str(t3 - t2))

    # Set Weather
    my_weather_config=weather.WeatherConfig(location=weather_location)
    my_weather = weather.Weather(config=my_weather_config,my_simulation_parameters=my_simulation_parameters)
    #my_weather.set_sim_repo(repo)
    t4 = time.perf_counter()
    log.profile("T2: " + str(t4 - t3))

    # Set Residence
    my_residence_config=building.Building.get_default_config()
    my_residence_config.building_code=building_code
    my_residence_config.bClass=bClass

    my_residence = building.Building(config=my_residence_config, my_simulation_parameters=my_simulation_parameters)

    # Fake energy delivered
    thermal_energy_delivered_output = component.ComponentOutput("FakeThermalDeliveryMachine",
                                                                "ThermalDelivery",
                                                                LoadTypes.HEATING,
                                                                Units.WATT)
    t5 = time.perf_counter()
    log.profile("T2: " + str(t4 - t5))

    number_of_outputs = fft.get_number_of_outputs([my_occupancy,my_weather,my_residence,thermal_energy_delivered_output])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(number_of_outputs)


    assert 1 == 1
    my_residence.t_outC.source_output = my_weather.t_outC
    my_residence.altitudeC.source_output = my_weather.altitudeC
    my_residence.azimuthC.source_output = my_weather.azimuthC
    my_residence.DNIC.source_output = my_weather.DNIC
    my_residence.DHIC.source_output = my_weather.DHIC
    my_residence.occupancy_heat_gainC.source_output = my_occupancy.heating_by_residentsC
    my_residence.thermal_energy_deliveredC.source_output = thermal_energy_delivered_output

    fft.add_global_index_of_components([my_occupancy,my_weather,my_residence,thermal_energy_delivered_output])

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
        stsv.values[my_residence.t_mC.global_index] = 23
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
        assert (stsv.values[my_residence.t_mC.global_index] - 23.0) < - 0.01 * (seconds_per_timestep / 60)
    t7 = time.perf_counter()
    log.profile("T2: " + str(t7 - t6))
    log.profile("T2: " + str(t7 - t6))
    starttime = datetime.datetime.now()
    d4 = starttime.strftime("%d-%b-%Y %H:%M:%S")
    log.profile("Finished @ " + d4)