from hisim import component as cp
#import components as cps
#import components
from hisim.components import advanced_fuel_cell
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft

def test_chp_system():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # CHP-System
    min_operation_time=60
    min_idle_time = 15
    gas_type = "Methan"
    operating_mode = "electricity"
    p_el_max=3_000

    #===================================================================================================================
    # Set Gas Heater
    my_chp_system_config= advanced_fuel_cell.CHP.get_default_config()
    my_chp_system_config.min_operation_time=60
    my_chp_system_config.min_idle_time = 15
    my_chp_system_config.gas_type = "Methan"
    my_chp_system_config.operating_mode = "electricity"
    my_chp_system_config.p_el_max=3_000

    my_chp_system = advanced_fuel_cell.CHP(config=my_chp_system_config,
                                   my_simulation_parameters=my_simulation_parameters)
    # Set Fake Outputs for Gas Heater
    control_signal = cp.ComponentOutput("FakeControlSignal",
                             "ControlSignal",
                             lt.LoadTypes.Any,
                             lt.Units.Percent)

    massflow_input_temperature= cp.ComponentOutput("FakeMassflowInputTemperature",
                             "MassflowInputTemperature",
                             lt.LoadTypes.Water,
                             lt.Units.Celsius)

    electricity_from_CHP_target = cp.ComponentOutput("FakeElectricityFromCHPTarget",
                             "ElectricityFromCHPTarget",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)

    my_chp_system.control_signal.SourceOutput = control_signal
    my_chp_system.mass_inp_temp.SourceOutput = massflow_input_temperature
    my_chp_system.electricity_target.SourceOutput = electricity_from_CHP_target

    number_of_outputs = fft.get_number_of_outputs([control_signal,
                                                   massflow_input_temperature,
                                                   electricity_from_CHP_target,
                                                   my_chp_system])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([control_signal,
                                        massflow_input_temperature,
                                        electricity_from_CHP_target,
                                        my_chp_system])

    stsv.values[control_signal.GlobalIndex] = 0
    stsv.values[massflow_input_temperature.GlobalIndex ] = 50
    stsv.values[electricity_from_CHP_target.GlobalIndex] = 300



    timestep = 100

    # Simulate
    my_chp_system.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity demand got produced by chp
    #
    assert stsv.values[my_chp_system.mass_out.GlobalIndex ] == 0.011
    assert stsv.values[my_chp_system.mass_out_temp.GlobalIndex] == 82.6072779444372
    assert stsv.values[my_chp_system.gas_demand_target.GlobalIndex] == 9.99470663620661e-05
    assert stsv.values[my_chp_system.el_power.GlobalIndex] ==  400.0
    assert stsv.values[my_chp_system.number_of_cyclesC.GlobalIndex] == 1
    assert stsv.values[my_chp_system.th_power.GlobalIndex] == 1500.0
    assert stsv.values[my_chp_system.gas_demand_real_used.GlobalIndex] == 9.99470663620661e-05
