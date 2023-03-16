import pytest
from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_heat_pump():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
    # Heat Pump
    name = "HeatPump"
    manufacturer = "Viessmann Werke GmbH & Co KG"
    heat_pump_name = "Vitocal 300-A AWO-AC 301.B07"
    minimum_idle_time = 30
    minimum_operation_time = 15
    heat_pump_power = 7420.0

    # Heat Pump Controller
    temperature_air_heating_in_celsius = 18.0
    temperature_air_cooling_in_celsius = 28.0
    offset = 1
    hp_mode = 1



    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump.GenericHeatPump(config=generic_heat_pump.GenericHeatPumpConfig(
                                                     manufacturer=manufacturer,
                                                     name=name,
                                                     heat_pump_name=heat_pump_name,
                                                     min_operation_time=minimum_idle_time,
                                                     min_idle_time=minimum_operation_time), 
                                                     my_simulation_parameters=my_simulation_parameters)

    # Set Heat Pump Controller
    my_heat_pump_controller = generic_heat_pump.GenericHeatPumpController(config=generic_heat_pump.GenericHeatPumpControllerConfig(
                            	                               name="GenericHeatPumpCotroller",
                                                               temperature_air_heating_in_celsius=temperature_air_heating_in_celsius,
                                                               temperature_air_cooling_in_celsius=temperature_air_cooling_in_celsius,
                                                               offset=offset,
                                                               mode=hp_mode),
                                                           my_simulation_parameters=my_simulation_parameters)

    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.TEMPERATURE,
                                        lt.Units.WATT)

    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.WATT)

    my_heat_pump_controller.temperature_mean_channel.source_output = t_mC

    my_heat_pump.temperature_outside_channel.source_output = t_air_outdoorC
    my_heat_pump.state_channel.source_output = my_heat_pump_controller.state_channel

    number_of_outputs = fft.get_number_of_outputs([t_air_outdoorC,t_mC,my_heat_pump,my_heat_pump_controller])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([t_air_outdoorC,t_mC,my_heat_pump,my_heat_pump_controller])
    # Link inputs and outputs

    stsv.values[t_mC.global_index] = 10

    timestep = 60
    # Simulate
    my_heat_pump_controller.i_restore_state()
    my_heat_pump_controller.i_simulate(timestep, stsv,  False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(timestep, stsv, False)

    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[my_heat_pump_controller.state_channel.global_index]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[my_heat_pump.thermal_power_delivered_channel.global_index]
