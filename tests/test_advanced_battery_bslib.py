from hisim import component as cp
#import components as cps
#import components
from hisim.components import advanced_battery_bslib
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft

import os
from hisim import utils
import numpy as np

def test_advanced_battery_bslib():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)


    #===================================================================================================================
    # Set Advanced Battery
    system_id = 'SG1'   # Generic ac coupled battery storage system
    p_inv_custom = 5    # kW
    e_bat_custom = 10   # kWh
    name = "Battery"
    my_advanced_battery_config=advanced_battery_bslib.BatteryConfig (system_id=system_id,
                                                                     p_inv_custom=p_inv_custom,
                                                                     e_bat_custom=e_bat_custom,
                                                                     name=name)
    my_advanced_battery = advanced_battery_bslib.Battery(config=my_advanced_battery_config,
                                                         my_simulation_parameters=my_simulation_parameters)

    # Set Fake Input
    loading_power_input = cp.ComponentOutput("FakeLoadingPowerInput",
                             "LoadingPowerInput",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    number_of_outputs = fft.get_number_of_outputs([my_advanced_battery,loading_power_input])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_advanced_battery.p_set.SourceOutput = loading_power_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_advanced_battery, loading_power_input])

    stsv.values[loading_power_input.GlobalIndex] = 4000

    timestep = 1000

    # Simulate
    my_advanced_battery.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity indeed that corresponded to the battery model
    # Not all Elect could be charged
    assert stsv.values[my_advanced_battery.p_bs.GlobalIndex] == 3998.0
    assert stsv.values[my_advanced_battery.p_bat.GlobalIndex] == 3959.5306911168
    assert stsv.values[my_advanced_battery.soc.GlobalIndex] == 0.006432121891379126

