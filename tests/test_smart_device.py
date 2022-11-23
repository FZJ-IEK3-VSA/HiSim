from os import path, listdir, makedirs
import shutil
import json

from hisim import component as cp
from hisim.components import generic_heat_pump
from hisim.components import generic_smart_device
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests import functions_for_testing as fft

import csv

def test_smart_device():
    """
    Test time shifting for smart devices
    """
    #initialize simulation parameters
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
                                                                 seconds_per_timestep=60)

    # copy flexibilty device activation file from LPG to right location if nothing is there
    # Check whether the results/Results folder exists or not
    isExist = path.exists(utils.HISIMPATH['utsp_reports'])
    if not isExist:
       # Create a new directory because it does not exist
       makedirs(utils.HISIMPATH['utsp_reports'])

    files_in_reports = listdir(utils.HISIMPATH['utsp_reports'])
    if not files_in_reports:
        files_to_copy = listdir(utils.HISIMPATH['utsp_example_reports'])
        for file in files_to_copy:
            shutil.copyfile(path.join(utils.HISIMPATH['utsp_example_reports'], file),
                            path.join(utils.HISIMPATH['utsp_reports'], file))

    filepath = path.join(utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json")
    jsonfile = open(filepath)
    strfile = json.load(jsonfile)
    device = strfile[0]['Device']['Name']

    #create smart_device
    my_smart_device = generic_smart_device.SmartDevice(identifier=device, source_weight=0, my_simulation_parameters=mysim,
                                                       smart_devices_included=False)

    #get first activation and corrisponding profile from data (SmartDevice Class reads in data )
    activation = my_smart_device.latest_start[0]
    profile = my_smart_device.electricity_profile[0]

    #assign outputs correctly
    number_of_outputs = 1
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
    my_smart_device.ElectricityOutputC.global_index = 0

    # Simulate and check that (a) device is activated at latest possible starting point, (b) device runs with the defined power profile
    my_smart_device.i_restore_state()
    for j in range(activation + len(profile)):
        my_smart_device.i_simulate(j, stsv, False)
        if j >= activation:
            assert stsv.values[0] == profile[j - activation]
