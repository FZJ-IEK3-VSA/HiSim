""" Disabled due to errors in the pathing. - Noah """

# from os import path, listdir, makedirs
# import json
#
# from hisim import component as cp
# from hisim.components import generic_smart_device
# from hisim.simulationparameters import SimulationParameters
# from hisim import utils
#
# import csv
# import pytest
#
# @pytest.mark.base
# def test_smart_device():
#     """
#     Test time shifting for smart devices
#     """
#     #initialize simulation parameters
#     mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
#                                                                  seconds_per_timestep=60)
#
#     # call LPF to copy flexibilty device activation file from LPG to right location if nothing is there
#     # my_occupancy_profile = "CH01"
#
#     # my_occupancy_config=loadprofilegenerator_connector.OccupancyConfig.get_default_CHS01()
#     # my_occupancy_config.profile_name=my_occupancy_profile
#     # my_occupancy = loadprofilegenerator_connector.Occupancy(config=my_occupancy_config, my_simulation_parameters=mysim)
#
#     filepath = path.join(utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json")
#     with open(filepath, encoding="utf-8") as jsonfile:
#         strfile = json.load(jsonfile)
#     device = strfile[0]['Device']['Name']
#
#     #create smart_device
#     my_smart_device = generic_smart_device.SmartDevice(identifier=device, source_weight=0, my_simulation_parameters=mysim,
#                                                        smart_devices_included=False)
#
#     #get first activation and corrisponding profile from data (SmartDevice Class reads in data )
#     activation = my_smart_device.latest_start[0]
#     profile = my_smart_device.electricity_profile[0]
#
#     #assign outputs correctly
#     number_of_outputs = 1
#     stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
#     my_smart_device.ElectricityOutputC.global_index = 0
#
#     # Simulate and check that (a) device is activated at latest possible starting point, (b) device runs with the defined power profile
#     my_smart_device.i_restore_state()
#     for j in range(activation + len(profile)):
#         my_smart_device.i_simulate(j, stsv, False)
#         if j >= activation:
#             assert stsv.values[0] == profile[j - activation]
