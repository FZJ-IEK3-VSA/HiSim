from hisim.components import generic_dhw_boiler
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_dhw_boiler

from hisim import loadtypes as lt
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters

def test_simple_bucket_boiler_state():
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only( 2017, seconds_per_timestep )
    
    # Boiler
    volume = 200
    surface : float = 1.2
    u_value : float = 0.36
    T_warmwater : float = 50
    T_drainwater : float = 10
    power : float = 2400
    efficiency : float = 1
    fuel : str = 'electricity'
    
    # Boiler Controller L1
    minimum_idle_time = 30 * 60
    minimum_operation_time = 15 * 60

    # Boiler Controller L2
    T_min = 45.0
    T_max = 60.0
    T_tolerance = 10.0
    
    #definition of outputs
    number_of_outputs = 5
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues( number_of_outputs )

    #===================================================================================================================
    # Set Boiler
    my_boiler = generic_dhw_boiler.Boiler( my_simulation_parameters=my_simulation_parameters,
                                           volume = volume, 
                                           surface = surface,
                                           u_value = u_value,
                                           T_warmwater = T_warmwater,
                                           T_drainwater = T_drainwater,
                                           power = power,
                                           efficiency = efficiency,
                                           fuel = fuel )
    # Set L1 Boiler Controller
    my_boiler_controller_l1 = controller_l1_generic_runtime.L1_Controller( min_operation_time = minimum_operation_time,
                                                                           min_idle_time = minimum_idle_time, 
                                                                           my_simulation_parameters = my_simulation_parameters )
    
    # Set L2 Heat Pump Controller
    my_boiler_controller_l2 = controller_l2_generic_dhw_boiler.L2_Controller(  my_simulation_parameters = my_simulation_parameters,
                                                                               T_min = T_min,
                                                                               T_max = T_max )
    
    #definition of hot water use 
    WW_use = cp.ComponentOutput( "FakeWarmwaterUse",
                                 "WaterConsumption",
                                 lt.LoadTypes.WarmWater,
                                 lt.Units.Liter )
    
    
    #connection of in- and outputs
    my_boiler_controller_l2.ReferenceTemperatureC.SourceOutput = my_boiler.TemperatureMeanC
    my_boiler.WaterConsumptionC.SourceOutput = WW_use
    my_boiler.l1_DeviceSignalC.SourceOutput = my_boiler_controller_l1.l1_DeviceSignalC
    my_boiler_controller_l1.l2_DeviceSignalC.SourceOutput = my_boiler_controller_l2.l2_DeviceSignalC
    
    # indexing of in- and outputs
    WW_use.GlobalIndex = 0
    my_boiler.TemperatureMeanC.GlobalIndex = 1
    my_boiler_controller_l1.l1_DeviceSignalC.GlobalIndex = 2  
    my_boiler_controller_l2.l2_DeviceSignalC.GlobalIndex = 3
    my_boiler.ElectricityOutputC.GlobalIndex = 4
    
    
    stsv.values[ 0 ] = 1
    j = 60
    
    # Simulate
    my_boiler.i_restore_state()
    my_boiler.i_simulate( j, stsv, False )
    
    #check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    assert stsv.values[ 1 ] >= 49.6 and stsv.values[ 1 ] < 49.7