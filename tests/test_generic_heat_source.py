from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_source
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heat_simple
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

def test_heat_source():

    #simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)
    
    #default config
    my_heat_source_config = generic_heat_source.HeatSource.get_default_config_heating()
    l2_config = controller_l2_generic_heat_simple.L2_Controller.get_default_config_heating()
    l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config()

    #definition of outputs
    number_of_outputs = 5
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_heat_source = generic_heat_source.HeatSource(config=my_heat_source_config,
                                                    my_simulation_parameters=my_simulation_parameters)

    # Set L1 Heat Pump Controller
    my_heat_source_controller_l1 = controller_l1_generic_runtime.L1_Controller(
        config=l1_config, my_simulation_parameters=my_simulation_parameters)
    
    # Set L2 Heat Pump Controller
    my_heat_source_controller_l2 = controller_l2_generic_heat_simple.L2_Controller(
        config = l2_config, my_simulation_parameters=my_simulation_parameters)
    
    #definition of building output
    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.WATT)
    
    #connection of in- and outputs
    my_heat_source_controller_l2.ReferenceTemperatureC.source_output = t_mC
    my_heat_source.L1DeviceSignalC.source_output = my_heat_source_controller_l1.L1DeviceSignalC
    my_heat_source_controller_l1.l2_DeviceSignalC.source_output = my_heat_source_controller_l2.l2_DeviceSignalC

    # indexing of in- and outputs
    t_mC.global_index = 0
    my_heat_source_controller_l1.L1DeviceSignalC.global_index = 1  
    my_heat_source_controller_l2.l2_DeviceSignalC.global_index = 2
    my_heat_source.FuelDeliveredC.global_index = 3
    my_heat_source.ThermalPowerDeliveredC.global_index = 4
    
    #test: after five hour temperature in building is 10 °C 
    stsv.values[0] = 10
    j = 60 * 5 
    
    # Simulate
    my_heat_source_controller_l2.i_restore_state()
    my_heat_source_controller_l2.i_simulate(j, stsv,  False)

    my_heat_source_controller_l1.i_restore_state()
    my_heat_source_controller_l1.i_simulate(j, stsv,  False)

    my_heat_source.i_restore_state()
    my_heat_source.i_simulate(j, stsv, False)

    #-> Did heat pump turn on?
    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[1]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert my_heat_source_config.power_th / 60 == stsv.values[3]
    assert my_heat_source_config.power_th == stsv.values[4]

