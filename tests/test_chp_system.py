from hisim import component as cp
#import components as cps
#import components
from hisim.components import chp_system
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log

def test_chp_system():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # CHP-System
    min_operation_time=60
    min_idle_time = 15
    gas_type = "Methan"
    operating_mode = "electricity"
    p_el_max=3_000
    number_of_outputs = 11
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Gas Heater
    my_chp_system = chp_system.CHP(min_operation_time=min_operation_time,
                                   min_idle_time=min_idle_time,
                                   gas_type=gas_type,
                                   operating_mode=operating_mode,
                                   p_el_max=p_el_max,
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

    I_2 = cp.ComponentOutput("FakeElectricityFromCHPTarget",
                             "ElectricityFromCHPTarget",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    my_chp_system.control_signal.SourceOutput = I_0
    my_chp_system.mass_inp_temp.SourceOutput = I_1
    my_chp_system.electricity_target.SourceOutput = I_2


    # Link inputs and outputs
    I_0.GlobalIndex = 0
    I_1.GlobalIndex = 1
    I_2.GlobalIndex = 2
    stsv.values[0] = 0
    stsv.values[1] = 50
    stsv.values[2] = 300

    my_chp_system.mass_out.GlobalIndex = 3
    my_chp_system.mass_out_temp.GlobalIndex = 4
    my_chp_system.gas_demand_target.GlobalIndex = 5
    my_chp_system.el_power.GlobalIndex = 6
    my_chp_system.number_of_cyclesC.GlobalIndex = 7
    my_chp_system.th_power.GlobalIndex = 8
    my_chp_system.gas_demand_real_used.GlobalIndex = 9


    j = 100

    # Simulate
    my_chp_system.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity demand got produced by chp
    assert stsv.values[3] == 0.011
    assert stsv.values[4] == 82.6072779444372
    assert stsv.values[5] == 9.99470663620661e-05
    assert stsv.values[6] ==  400.0
    assert stsv.values[7] == 1
    assert stsv.values[8] == 1500.0
    assert stsv.values[9] == 9.99470663620661e-05
