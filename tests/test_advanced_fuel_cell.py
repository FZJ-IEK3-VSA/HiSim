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
    # min_operation_time=60
    # min_idle_time = 15
    # gas_type = "Methan"
    # operating_mode = "electricity"
    # p_el_max=3_000

    #===================================================================================================================
    # Set Gas Heater
    my_chp_system_config= advanced_fuel_cell.CHPConfig.get_default_config()
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
                                        lt.LoadTypes.ANY,
                                        lt.Units.PERCENT)

    massflow_input_temperature= cp.ComponentOutput("FakeMassflowInputTemperature",
                             "MassflowInputTemperature",
                                                   lt.LoadTypes.WATER,
                                                   lt.Units.CELSIUS)

    electricity_from_CHP_target = cp.ComponentOutput("FakeElectricityFromCHPTarget",
                             "ElectricityFromCHPTarget",
                                                     lt.LoadTypes.ELECTRICITY,
                                                     lt.Units.WATT)

    my_chp_system.control_signal.source_output = control_signal
    my_chp_system.mass_inp_temp.source_output = massflow_input_temperature
    my_chp_system.electricity_target.source_output = electricity_from_CHP_target

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

    stsv.values[control_signal.global_index] = 0
    stsv.values[massflow_input_temperature.global_index] = 50
    stsv.values[electricity_from_CHP_target.global_index] = 300



    timestep = 100

    # Simulate
    my_chp_system.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Check if the delivered electricity demand got produced by chp
    #
    assert stsv.values[my_chp_system.mass_out.global_index] == 0.011
    assert stsv.values[my_chp_system.mass_out_temp.global_index] == 82.6072779444372
    assert stsv.values[my_chp_system.gas_demand_target.global_index] == 9.99470663620661e-05
    assert stsv.values[my_chp_system.el_power.global_index] == 400.0
    assert stsv.values[my_chp_system.number_of_cyclesC.global_index] == 1
    assert stsv.values[my_chp_system.th_power.global_index] == 1500.0
    assert stsv.values[my_chp_system.gas_demand_real_used.global_index] == 9.99470663620661e-05
