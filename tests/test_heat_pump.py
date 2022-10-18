from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

def test_heat_pump():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
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

    number_of_outputs = 8
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(manufacturer=manufacturer,
                                                     name=name,
                                                     min_operation_time=minimum_idle_time,
                                                     min_idle_time=minimum_operation_time, my_simulation_parameters=my_simulation_parameters)

    # Set Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.HeatPumpController(t_air_heating=t_air_heating,
                                                               t_air_cooling=t_air_cooling,
                                                               offset=offset,
                                                               mode=hp_mode,
                                                           my_simulation_parameters=my_simulation_parameters)

    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.TEMPERATURE,
                                        lt.Units.WATT)

    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.WATT)

    my_heat_pump_controller.t_mC.source_output = t_mC

    my_heat_pump.t_outC.source_output = t_air_outdoorC
    my_heat_pump.stateC.source_output = my_heat_pump_controller.stateC

    # Link inputs and outputs
    t_mC.global_index = 0
    stsv.values[0] = 10

    my_heat_pump_controller.stateC.global_index = 1

    my_heat_pump.thermal_energy_deliveredC.global_index = 2
    my_heat_pump.heatingC.global_index = 3
    my_heat_pump.coolingC.global_index = 4
    my_heat_pump.electricity_outputC.global_index = 5
    my_heat_pump.number_of_cyclesC.global_index = 6
    t_air_outdoorC.global_index = 7
    j = 60
    # Simulate
    my_heat_pump_controller.i_restore_state()
    my_heat_pump_controller.i_simulate(j, stsv,  False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(j, stsv, False)

    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[1]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[2]
