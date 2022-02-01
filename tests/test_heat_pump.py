from hisim import component as cp
#import components as cps
#import components
from hisim.components import heat_pump
from hisim import loadtypes as lt

def test_heat_pump():

    seconds_per_timestep = 60

    # Heat Pump
    manufacturer = "Viessmann Werke GmbH & Co KG"
    name = "Vitocal 300-A AWO-AC 301.B07"
    minimum_idle_time = 30
    minimum_operation_time = 15
    heat_pump_power = 7420.0

    # Heat Pump Controller
    t_air_heating = 18.0
    t_air_cooling = 28.0
    offset = 1
    hp_mode = 1

    number_of_outputs = 7
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = heat_pump.HeatPump(manufacturer=manufacturer,
                                          name=name,
                                          min_operation_time=minimum_idle_time,
                                          min_idle_time=minimum_operation_time)

    # Set Heat Pump Controller
    my_heat_pump_controller = heat_pump.HeatPumpController(t_air_heating=t_air_heating,
                                                               t_air_cooling=t_air_cooling,
                                                               offset=offset,
                                                               mode=hp_mode)

    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.Temperature,
                                        lt.Units.Watt)

    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.Temperature,
                              lt.Units.Watt)

    my_heat_pump_controller.t_mC.SourceOutput = t_mC

    my_heat_pump.t_outC.SourceOutput = t_air_outdoorC
    my_heat_pump.stateC.SourceOutput = my_heat_pump_controller.stateC

    # Link inputs and outputs
    t_mC.GlobalIndex = 0
    stsv.values[0] = 10

    my_heat_pump_controller.stateC.GlobalIndex = 1

    my_heat_pump.thermal_energy_deliveredC.GlobalIndex = 2
    my_heat_pump.heatingC.GlobalIndex = 3
    my_heat_pump.coolingC.GlobalIndex = 4
    my_heat_pump.electricity_outputC.GlobalIndex = 5
    my_heat_pump.number_of_cyclesC.GlobalIndex = 6

    j = 60
    # Simulate
    my_heat_pump_controller.i_restore_state()
    my_heat_pump_controller.i_simulate(j, stsv, seconds_per_timestep, False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(j, stsv, seconds_per_timestep, False)

    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[1]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[2]
