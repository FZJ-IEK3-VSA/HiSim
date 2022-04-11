from hisim import component as cp
#import components as cps
#import components
from hisim.components import advanced_battery
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log

import pandas as pd
import os
from hisim import utils

def test_advanced_battery():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # Advanced-Battery
    number_of_outputs = 3
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Advanced Battery
    parameter= pd.read_excel(os.path.join(utils.HISIMPATH["advanced_battery"], 'inputs'), index_col=0)
    my_advanced_battery = advanced_battery.AdvancedBattery(parameter=parameter,
                                   my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    I_0 = cp.ComponentOutput("FakeLoadingPowerInput",
                             "LoadingPowerInput",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    my_advanced_battery.control_signal.SourceOutput = I_0

    # Link inputs and outputs
    I_0.GlobalIndex = 0
    stsv.values[0] = 100

    my_advanced_battery.P_bs_C.GlobalIndex = 1
    my_advanced_battery.soc_C.GlobalIndex = 2

    j = 100

    # Simulate
    my_advanced_battery.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity indeed that corresponded to the battery model
    assert stsv.values[1] == 0.011
    assert stsv.values[2] == 82.6072779444372

