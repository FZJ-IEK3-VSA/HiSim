from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heat_clever_simple
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

def test_heat_pump_modular():

    #simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only( 2017, seconds_per_timestep )
    
    #default config
    my_hp_config = generic_heat_pump_modular.HeatPump.get_default_config_heating( )
    l2_config = controller_l2_generic_heat_clever_simple.L2_Controller.get_default_config_heating( )
    l1_config = controller_l1_generic_runtime.L1_Controller.get_default_config_heatpump( )

    #definition of outputs
    number_of_outputs = 7
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues( number_of_outputs )

    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump_modular.HeatPump( config = my_hp_config,
                                                       my_simulation_parameters = my_simulation_parameters )

    # Set L1 Heat Pump Controller
    my_heat_pump_controller_l1 = controller_l1_generic_runtime.L1_Controller( config = l1_config,
                                                                              my_simulation_parameters = my_simulation_parameters )
    
    # Set L2 Heat Pump Controller
    my_heat_pump_controller_l2 = controller_l2_generic_heat_clever_simple.L2_Controller(  config = l2_config, 
                                                                                        my_simulation_parameters = my_simulation_parameters )

    #definition of weather output
    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.TEMPERATURE,
                                        lt.Units.WATT)
    
    #definition of building output
    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.WATT)
    
    #definition of electricity surplus
    ElectricityTargetC = cp.ComponentOutput( 'FakeSurplusSignal',
                                             'ElectricityTarget',
                                             lt.LoadTypes.ELECTRICITY,
                                             lt.Units.WATT )
    
    #connection of in- and outputs
    my_heat_pump_controller_l2.ReferenceTemperatureC.source_output = t_mC
    my_heat_pump_controller_l2.ElectricityTargetC.source_output = ElectricityTargetC
    my_heat_pump.TemperatureOutsideC.source_output = t_air_outdoorC
    my_heat_pump.L1DeviceSignalC.source_output = my_heat_pump_controller_l1.L1DeviceSignalC
    my_heat_pump_controller_l1.l2_DeviceSignalC.source_output = my_heat_pump_controller_l2.l2_DeviceSignalC

    # indexing of in- and outputs
    t_mC.global_index = 0
    ElectricityTargetC.global_index = 1
    t_air_outdoorC.global_index = 2
    my_heat_pump_controller_l1.L1DeviceSignalC.global_index = 3  
    my_heat_pump_controller_l2.l2_DeviceSignalC.global_index = 4
    my_heat_pump.ThermalPowerDeliveredC.global_index = 5
    my_heat_pump.ElectricityOutputC.global_index = 6
    
    #test: after five hour temperature in building is 10 °C 
    stsv.values[ 0 ] = 10
    stsv.values[ 1 ] = 0
    j = 60 * 5 
    
    # Simulate
    my_heat_pump_controller_l2.i_restore_state()
    my_heat_pump_controller_l2.i_simulate(j, stsv,  False)

    my_heat_pump_controller_l1.i_restore_state()
    my_heat_pump_controller_l1.i_simulate(j, stsv,  False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(j, stsv, False)

    #-> Did heat pump turn on?
    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[ 3 ]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert my_hp_config.power_th == stsv.values[ 5 ]
