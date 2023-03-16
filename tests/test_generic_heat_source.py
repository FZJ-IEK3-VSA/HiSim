import pytest
from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_source
from hisim.components import controller_l1_heatpump
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_heat_source():

    #simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)
    
    #default config
    my_heat_source_config = generic_heat_source.HeatSourceConfig.get_default_config_heating()
    l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller(name='HeatSource')

    #definition of outputs
    number_of_outputs = 4
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_heat_source = generic_heat_source.HeatSource(config=my_heat_source_config,
                                                    my_simulation_parameters=my_simulation_parameters)

    # Set L1 Heat Pump Controller
    my_heat_source_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        config=l1_config, my_simulation_parameters=my_simulation_parameters)
    
    #definition of building output
    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.CELSIUS)
    
    #connection of in- and outputs
    my_heat_source_controller_l1.storage_temperature_channel.source_output = t_mC
    my_heat_source.l1_heatsource_taget_percentage.source_output = my_heat_source_controller_l1.heat_pump_target_percentage_channel

    # indexing of in- and outputs
    t_mC.global_index = 0
    my_heat_source_controller_l1.heat_pump_target_percentage_channel.global_index = 1  
    my_heat_source.fuel_delivered_channel.global_index = 2
    my_heat_source.thermal_power_delivered_channel.global_index = 3
    
    #test: after five hour temperature in building is 10 °C 
    stsv.values[0] = 10
    j = 60 * 5 
    
    # Simulate
    my_heat_source_controller_l1.i_restore_state()
    my_heat_source_controller_l1.i_simulate(j, stsv,  False)

    my_heat_source.i_restore_state()
    my_heat_source.i_simulate(j, stsv, False)

    #-> Did heat pump turn on?
    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[1]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert my_heat_source_config.power_th / 60 == stsv.values[2]
    assert my_heat_source_config.power_th == stsv.values[3]
