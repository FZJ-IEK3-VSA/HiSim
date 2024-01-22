"""Test for loadprofile generator connector."""

import pytest
from tests import functions_for_testing as fft

from hisim import component

from hisim.simulationparameters import SimulationParameters
from obsolete import loadprofilegenerator_connector


@pytest.mark.base
def test_occupancy():
    """Tests Occupancy profile for profile CHR01.

    Year heating generated: 1719 kWh
    """

    my_occupancy_profile = "CHR01 Couple both at Work"
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.full_year(
        2021, seconds_per_timestep
    )
    my_simulation_parameters.predictive_control = False
    my_occupancy_config = (
        loadprofilegenerator_connector.OccupancyConfig.get_default_chr01_couple_both_at_work()
    )
    my_occupancy_config.profile_name = my_occupancy_profile
    my_occupancy = loadprofilegenerator_connector.Occupancy(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    number_of_outputs = fft.get_number_of_outputs([my_occupancy])
    stsv = component.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_occupancy])

    my_occupancy.i_simulate(0, stsv, False)
    number_of_residents = []
    heating_by_residents = []
    heating_by_devices = []
    electricity_consumption = []
    water_consumption = []
    for i in range(24 * 60 * 365):
        my_occupancy.i_simulate(i, stsv, False)
        number_of_residents.append(
            stsv.values[my_occupancy.number_of_residents_channel.global_index]
        )
        heating_by_residents.append(
            stsv.values[my_occupancy.heating_by_residents_channel.global_index]
        )
        heating_by_devices.append(
            stsv.values[my_occupancy.heating_by_devices_channel.global_index]
        )
        electricity_consumption.append(
            stsv.values[my_occupancy.electricity_output_channel.global_index]
        )
        water_consumption.append(
            stsv.values[my_occupancy.water_consumption_channel.global_index]
        )

    year_heating_by_occupancy = sum(heating_by_residents) / (seconds_per_timestep * 1e3)
    assert year_heating_by_occupancy == 1443.2325
    # pdb.set_trace()


# def test_profile():
#    cProfile.run('test_occupancy()')


# def test_occupancy_dummy():
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
#        log.information("Electricity: {}".format(stsv.values[2]))
#
#    #assert False
#    # year_heating_by_occupancy = sum(heating_by_residents)/(60*1E3)
#    assert year_heating_by_occupancy == 1443.2325
