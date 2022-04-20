from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_gas_heater
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft

def test_gas_heater():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
    # GasHeater
    temperaturedelta=10
    power_max = 12_000
    #===================================================================================================================
    # Set Gas Heater
    my_gas_heater = generic_gas_heater.GasHeater(temperaturedelta=temperaturedelta,
                                       power_max=power_max,
                                       my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    I_0 = cp.ComponentOutput("FakeControlSignal",
                             "ControlSignal",
                             lt.LoadTypes.Any,
                             lt.Units.Percent)

    I_1 = cp.ComponentOutput( "FakeMassflowInputTemperature",
                              "MassflowInputTemperature",
                              lt.LoadTypes.Water,
                              lt.Units.Celsius)

    number_of_outputs = fft.get_number_of_outputs([I_0,I_1,my_gas_heater])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_gas_heater.control_signal.SourceOutput = I_0
    my_gas_heater.mass_inp_temp.SourceOutput = I_1

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([I_0,I_1,my_gas_heater])
    stsv.values[I_0.GlobalIndex] = 1
    stsv.values[I_1.GlobalIndex] = 30


    j = 30

    # Simulate
    my_gas_heater.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered massflow and power is indeed that corresponded to the gas heater model
    assert stsv.values[my_gas_heater.mass_out.GlobalIndex] == 0.2582496413199426
    assert stsv.values[my_gas_heater.mass_out_temp.GlobalIndex] == 40
    assert stsv.values[my_gas_heater.p_th.GlobalIndex] == 10_800

test_gas_heater()