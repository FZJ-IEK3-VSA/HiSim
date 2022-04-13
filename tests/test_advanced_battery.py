from hisim import component as cp
#import components as cps
#import components
from hisim.components import advanced_battery
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log

import os
from hisim import utils
import numpy as np

def test_advanced_battery():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # Advanced-Battery
    number_of_outputs = 3
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Advanced Battery
    parameter=np.load(os.path.join(utils.HISIMPATH["advanced_battery"]["siemens_junelight"]))
    my_advanced_battery = advanced_battery.AdvancedBattery(parameter=parameter,
                                   my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    I_0 = cp.ComponentOutput("FakeLoadingPowerInput",
                             "LoadingPowerInput",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    my_advanced_battery.Pr_C.SourceOutput = I_0

    # Link inputs and outputs
    I_0.GlobalIndex = 0
    stsv.values[0] = 4000

    my_advanced_battery.P_bs_C.GlobalIndex = 1
    my_advanced_battery.soc_C.GlobalIndex = 2

    j = 1000

    # Simulate
    my_advanced_battery.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity indeed that corresponded to the battery model
    assert stsv.values[1] == 3572.0
    assert stsv.values[2] == 0.00619212585059115

