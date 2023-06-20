import pytest
from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_heatpump
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_heat_pump_modular():

    #simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)
    
    #default config
    my_hp_config = generic_heat_pump_modular.HeatPumpConfig.get_default_config_heating()
    l1_config = controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller("HP1")

    #definition of outputs
    number_of_outputs = 6
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump_modular.ModularHeatPump(config=my_hp_config,
                                                             my_simulation_parameters=my_simulation_parameters)

    # Set L1 Heat Pump Controller
    my_heat_pump_controller_l1 = controller_l1_heatpump.L1HeatPumpController(config=l1_config,
                                                                             my_simulation_parameters=my_simulation_parameters)

    #definition of weather output
    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.TEMPERATURE,
                                        lt.Units.WATT)
    
    #definition of building output
    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.TEMPERATURE,
                              lt.Units.CELSIUS)

    
    #definition of electricity surplus
    ElectricityTargetC = cp.ComponentOutput('FakeSurplusSignal',
                                            'ElectricityTarget',
                                            lt.LoadTypes.ELECTRICITY,
                                            lt.Units.WATT)
    
    #connection of in- and outputs
    my_heat_pump.temperature_outside_channel.source_output = t_air_outdoorC
    my_heat_pump.heat_controller_power_modifier_channel.source_output = my_heat_pump_controller_l1.heat_pump_target_percentage_channel
    my_heat_pump_controller_l1.storage_temperature_channel.source_output = t_mC

    # indexing of in- and outputs
    t_mC.global_index = 0
    ElectricityTargetC.global_index = 1
    t_air_outdoorC.global_index = 2
    my_heat_pump_controller_l1.heat_pump_target_percentage_channel.global_index = 3
    my_heat_pump.thermal_power_delicered_channel.global_index = 4
    my_heat_pump.electricity_output_channel.global_index = 5
    
    #test: after five hour temperature in building is 10 °C 
    stsv.values[0] = 10
    stsv.values[1] = 0
    stsv.values[2] = 0
    j = 60 * 60
    
    # Simulate
    my_heat_pump_controller_l1.i_restore_state()
    my_heat_pump_controller_l1.i_simulate(j, stsv,  False)

    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(j, stsv, False)

    #-> Did heat pump turn on?
    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[3]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert my_hp_config.power_th == stsv.values[4]
