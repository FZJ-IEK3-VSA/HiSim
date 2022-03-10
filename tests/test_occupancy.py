from hisim import component
from hisim.components import occupancy
from hisim.simulationparameters import SimulationParameters

def test_occupancy():
    """
    Tests Occupancy profile for profile CH01
    Year heating generated: 1719 kWh
    """

    repo = component.SimRepository()
    my_occupancy_profile = "CH01"
    seconds_per_timestep = 60
    number_of_outputs = 4
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)
    stsv = component.SingleTimeStepValues(number_of_outputs)
    my_occupancy = occupancy.Occupancy(profile=my_occupancy_profile, my_simulation_parameters=my_simulation_parameters)
    my_occupancy.set_sim_repo( repo )

    # Needed to number globalindex to activate return of component outputs
    my_occupancy.number_of_residentsC.GlobalIndex = 0
    my_occupancy.heating_by_residentsC.GlobalIndex = 1
    my_occupancy.electricity_outputC.GlobalIndex = 2
    my_occupancy.water_consumptionC.GlobalIndex = 3

    my_occupancy.i_simulate(0, stsv, False)
    number_of_residents = []
    heating_by_residents = []
    electricity_consumption = []
    water_consumption = []
    for i in range(24 * 60 * 365):
        my_occupancy.i_simulate(i, stsv,  False)
        number_of_residents.append(stsv.values[0])
        heating_by_residents.append(stsv.values[1])
        electricity_consumption.append(stsv.values[2])
        water_consumption.append(stsv.values[3])

    year_heating_by_occupancy = sum(heating_by_residents) / (seconds_per_timestep * 1E3)
    assert year_heating_by_occupancy == 1719.355
    # pdb.set_trace()

#def test_profile():
#    cProfile.run('test_occupancy()')


#def test_occupancy_dummy():
#    my_occupancy_profile = "DummyElectricity01"
#    number_of_outputs = 4
#    stsv = component.SingleTimeStepValues(number_of_outputs)
#    my_occupancy = occupancy.Occupancy(profile=my_occupancy_profile)
#
#    # Needed to number globalindex to activate return of component outputs
#    my_occupancy.number_of_residentsC.GlobalIndex = 0
#    my_occupancy.heating_by_residentsC.GlobalIndex = 1
#    my_occupancy.electricity_consumptionC.GlobalIndex = 2
#    my_occupancy.water_consumptionC.GlobalIndex = 3
#
#    my_occupancy.i_simulate(0, stsv, False)
#    number_of_residents = []
#    heating_by_residents = []
#    electricity_consumption = []
#    water_consumption = []
#    for i in range(4):
#        # for i in range(24 * 60 * 365):
#        my_occupancy.i_simulate(i, stsv, False)
#        number_of_residents.append(stsv.values[0])
#        heating_by_residents.append(stsv.values[1])
#        electricity_consumption.append(stsv.values[2])
#        water_consumption.append(stsv.values[3])
#        print("Electricity: {}".format(stsv.values[2]))
#
#    #assert False
#    # year_heating_by_occupancy = sum(heating_by_residents)/(60*1E3)
#    assert year_heating_by_occupancy == 1719.355
