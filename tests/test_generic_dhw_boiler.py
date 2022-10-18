from hisim.components import generic_hot_water_storage_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_generic_runtime
from hisim.components import controller_l2_generic_heat_simple

from hisim import loadtypes as lt
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters

def test_simple_bucket_boiler_state():
    
    #simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only( 2017, seconds_per_timestep )
    
    # Boiler defualt config
    l2_config = controller_l2_generic_heat_simple.L2GenericHeatController.get_default_config_waterheating()
    l1_config = controller_l1_generic_runtime.L1Config.get_default_config("runtime controller 1")
    boiler_config = generic_hot_water_storage_modular.HotWaterStorage.get_default_config_boiler( )
    heater_config = generic_heat_source.HeatSource.get_default_config_waterheating( )
    
    #definition of outputs
    number_of_outputs = 6
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues( number_of_outputs )

    #===================================================================================================================
    # Set Boiler
    my_boiler = generic_hot_water_storage_modular.HotWaterStorage( config = boiler_config, my_simulation_parameters = my_simulation_parameters )
    my_heater = generic_heat_source.HeatSource( config = heater_config, my_simulation_parameters = my_simulation_parameters )
    # Set L1 Boiler Controller
    my_boiler_controller_l1 = controller_l1_generic_runtime.L1GenericRuntimeController(config = l1_config, my_simulation_parameters = my_simulation_parameters)
    # Set L2 Heat Pump Controller
    my_boiler_controller_l2 = controller_l2_generic_heat_simple.L2GenericHeatController(config = l2_config, my_simulation_parameters = my_simulation_parameters)
    
    #definition of hot water use 
    WW_use = cp.ComponentOutput( "FakeWarmwaterUse",
                                 "WaterConsumption",
                                 lt.LoadTypes.WARM_WATER,
                                 lt.Units.LITER)
    
    
    #connection of in- and outputs

    my_boiler_controller_l2.reference_temperature_channel.source_output = my_boiler.temperature_mean_c
    my_boiler.water_consumption_c.source_output = WW_use
    my_boiler.thermal_power_delivered_c.source_output = my_heater.ThermalPowerDeliveredC
    my_heater.L1DeviceSignalC.source_output = my_boiler_controller_l1.L1DeviceSignalC
    my_boiler_controller_l1.l2_DeviceSignalC.source_output = my_boiler_controller_l2.l2_device_signal_channel
    
    # indexing of in- and outputs
    WW_use.global_index = 0
    my_boiler.temperature_mean_c.global_index = 1
    my_heater.ThermalPowerDeliveredC.global_index = 2
    my_boiler_controller_l1.L1DeviceSignalC.global_index = 3  
    my_boiler_controller_l2.l2_device_signal_channel.global_index = 4
    my_heater.FuelDeliveredC.global_index = 5
    
    
    j = 60
    stsv.values[ 0 ] = 1
    
    #check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    my_boiler.i_restore_state()
    my_boiler.i_simulate( j, stsv, False )
    
    my_boiler_controller_l2.i_restore_state()
    my_boiler_controller_l2.i_simulate(j, stsv,  False)
    
    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv,  False)
    
    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv,  False)
    
    my_boiler.i_restore_state()
    my_boiler.i_simulate( j, stsv, False )
    
    assert stsv.values[ 1 ] >= 59.6 and stsv.values[ 1 ] < 59.7
    
    #check if heater starts heating when temperature of boiler is too low
    stsv.values[ 0 ] = 0
    stsv.values[ 1 ] = 20
    
    my_boiler_controller_l2.i_restore_state()
    my_boiler_controller_l2.i_simulate(j, stsv,  False)
    
    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv,  False)
    
    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv,  False)
    
    my_boiler.i_restore_state()
    my_boiler.i_simulate( j, stsv, False )
    
    #check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    assert stsv.values[ 2 ] == my_heater.power_th * my_heater.efficiency
    
    #check if heater stops heating when temperature of boiler is too high
    stsv.values[ 0 ] = 0
    stsv.values[ 1 ] = 100
    
    my_boiler_controller_l2.i_restore_state()
    my_boiler_controller_l2.i_simulate(j, stsv,  False)
    
    my_boiler_controller_l1.i_restore_state()
    my_boiler_controller_l1.i_simulate(j, stsv,  False)
    
    my_heater.i_restore_state()
    my_heater.i_simulate(j, stsv,  False)
    
    my_boiler.i_restore_state()
    my_boiler.i_simulate( j, stsv, False )
    
    #check if heat loss in boiler corresponds to heatloss originated from 1 l hot water use and u-value heat loss
    assert stsv.values[ 2 ] == 0