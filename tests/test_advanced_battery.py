from hisim import component as cp
#import components as cps
#import components
from hisim.components import advanced_battery
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft

import os
from hisim import utils
import numpy as np

def test_advanced_battery():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)


    #===================================================================================================================
    # Set Advanced Battery
    parameter=np.load(os.path.join(utils.HISIMPATH["advanced_battery"]["siemens_junelight"]))
    my_advanced_battery = advanced_battery.AdvancedBattery(parameter=parameter,
                                   my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    loading_power_input = cp.ComponentOutput("FakeLoadingPowerInput",
                             "LoadingPowerInput",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    number_of_outputs = fft.get_number_of_outputs([my_advanced_battery,loading_power_input])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_advanced_battery.Pr_C.SourceOutput = loading_power_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_advanced_battery, loading_power_input])

    stsv.values[loading_power_input.GlobalIndex] = 4000

    timestep = 1000

    # Simulate
    my_advanced_battery.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity indeed that corresponded to the battery model
    # Not all Elect could be charged
    assert stsv.values[my_advanced_battery.P_bs_C.GlobalIndex] == 3572.0
    assert stsv.values[my_advanced_battery.soc_C.GlobalIndex] == 0.00619212585059115

