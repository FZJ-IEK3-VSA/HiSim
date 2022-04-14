from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_gas_heater
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log

def test_gas_heater():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
    # Heat Pump
    temperaturedelta=10
    power_max = 12_000

    number_of_outputs = 6
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

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

    I_1 = cp.ComponentOutput("FakeMassflowInputTemperature",
                                                      "MassflowInputTemperature",
                                                      lt.LoadTypes.Water,
                                                      lt.Units.Celsius)

    my_gas_heater.control_signal.SourceOutput = I_0
    my_gas_heater.mass_inp_temp.SourceOutput = I_1

    # Link inputs and outputs
    I_0.GlobalIndex = 0
    I_1.GlobalIndex = 1

    stsv.values[0] = 1
    stsv.values[1] = 30

    my_gas_heater.mass_out.GlobalIndex = 2
    my_gas_heater.mass_out_temp.GlobalIndex = 3
    my_gas_heater.gas_demand.GlobalIndex = 4
    my_gas_heater.p_th.GlobalIndex = 5
    j = 30

    # Simulate
    my_gas_heater.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered massflow and power is indeed that corresponded to the gas heater model
    assert stsv.values[2] == 0.2582496413199426
    assert stsv.values[3] == 40
    assert stsv.values[5] == 10_800