from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_hot_water_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

def test_storage():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
    # Storage
    V_SP_heating_water = 1000
    V_SP_warm_water = 200
    temperature_of_warm_water_extratcion = 32
    ambient_temperature = 15




    number_of_outputs = 8
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_storage = generic_hot_water_storage.HeatStorage(V_SP_heating_water=V_SP_heating_water,
                                     V_SP_warm_water=V_SP_warm_water,
                                     temperature_of_warm_water_extratcion=temperature_of_warm_water_extratcion,
                                     ambient_temperature=ambient_temperature,
                                     my_simulation_parameters=my_simulation_parameters)

    I_0 = cp.ComponentOutput("FakeThermalDemandHeatingWater",
                             "ThermalDemandHeatingWater",
                             lt.LoadTypes.Any,
                             lt.Units.Percent)

    I_1 = cp.ComponentOutput("FakeThermalDemandWarmWater",
                              "ThermalDemandWarmWater",
                              lt.LoadTypes.Water,
                              lt.Units.Celsius)

    I_2 = cp.ComponentOutput("FakeControlSignalChooseStorage",
                              "ControlSignalChooseStorage",
                              lt.LoadTypes.Water,
                              lt.Units.Celsius)

    I_3 = cp.ComponentOutput("FakeThermalInputPower1",
                              "ThermalInputPower1",
                              lt.LoadTypes.Water,
                              lt.Units.Celsius)

    my_storage.thermal_demand_heating_water.SourceOutput = I_0
    my_storage.thermal_demand_warm_water.SourceOutput = I_1
    my_storage.control_signal_choose_storage.SourceOutput = I_2
    my_storage.thermal_input_power1.SourceOutput = I_3


    # Link inputs and outputs
    I_0.GlobalIndex = 0
    I_1.GlobalIndex = 1
    I_2.GlobalIndex = 2
    I_3.GlobalIndex = 3
    stsv.values[0] = 2000
    stsv.values[1] = 400
    stsv.values[2] = 1
    stsv.values[3] = 800

    my_storage.T_sp_C_hw.GlobalIndex = 4
    my_storage.T_sp_C_ww.GlobalIndex = 5
    my_storage.UA_SP_C.GlobalIndex = 6
    my_storage.T_sp_C.GlobalIndex = 7

    j = 300
    # Simulate


    my_storage.i_restore_state()
    my_storage.i_simulate(j, stsv, False)

    assert  39.97334630595229 == stsv.values[4]
    assert 40.02265485276707 == stsv.values[5]
    assert 6.26 == stsv.values[6]
    assert 40.02265485276707 == stsv.values[7]

