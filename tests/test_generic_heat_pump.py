from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

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



    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump.HeatPump(manufacturer=manufacturer,
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
                                        lt.LoadTypes.Temperature,
                                        lt.Units.Watt)

    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.Temperature,
                              lt.Units.Watt)

    my_heat_pump_controller.t_mC.SourceOutput = t_mC

    my_heat_pump.t_outC.SourceOutput = t_air_outdoorC
    my_heat_pump.stateC.SourceOutput = my_heat_pump_controller.stateC

    number_of_outputs = fft.get_number_of_outputs([t_air_outdoorC,t_mC,my_heat_pump,my_heat_pump_controller])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([t_air_outdoorC,t_mC,my_heat_pump,my_heat_pump_controller])
    # Link inputs and outputs

    stsv.values[t_mC.GlobalIndex] = 10

    timestep = 60
    # Simulate
    my_heat_pump_controller.i_restore_state()
    my_heat_pump_controller.i_simulate(timestep, stsv,  False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(timestep, stsv, False)

    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[my_heat_pump_controller.stateC.GlobalIndex]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[my_heat_pump.thermal_energy_deliveredC.GlobalIndex]
