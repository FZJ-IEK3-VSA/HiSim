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
    my_gas_heater_config=generic_gas_heater.GasHeater.get_default_config()
    my_gas_heater_config.temperaturedelta=temperaturedelta
    my_gas_heater_config.power_max = power_max

    my_gas_heater = generic_gas_heater.GasHeater(config=my_gas_heater_config,
                                                 my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    control_signal = cp.ComponentOutput("FakeControlSignal",
                             "ControlSignal",
                                        lt.LoadTypes.ANY,
                                        lt.Units.PERCENT)

    mass_flow_input_temperature = cp.ComponentOutput( "FakeMassflowInputTemperature",
                              "MassflowInputTemperature",
                                                      lt.LoadTypes.WATER,
                                                      lt.Units.CELSIUS)

    number_of_outputs = fft.get_number_of_outputs([control_signal,mass_flow_input_temperature,my_gas_heater])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_gas_heater.control_signal_channel.source_output = control_signal
    my_gas_heater.mass_inp_temp_channel.source_output = mass_flow_input_temperature

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([control_signal,mass_flow_input_temperature,my_gas_heater])
    stsv.values[control_signal.global_index] = 1
    stsv.values[mass_flow_input_temperature.global_index] = 30


    timestep = 30

    # Simulate
    my_gas_heater.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Mass-Flow out of Gas-Heater to heat up Storages or House
    assert stsv.values[my_gas_heater.mass_out_channel.global_index] == 0.2582496413199426
    # Temperature of Water out of GasHeater
    assert stsv.values[my_gas_heater.mass_out_temp_channel.global_index] == temperaturedelta + stsv.values[mass_flow_input_temperature.global_index]
    # Real Power of GasHeater
    assert stsv.values[my_gas_heater.p_th_channel.global_index] == 10_800
