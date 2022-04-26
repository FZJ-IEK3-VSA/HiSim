from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump_modular
from hisim.components import controller_l1_generic_heatpump_modular
from hisim.components import controller_l2_generic_heatpump_modular
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

def test_heat_pump_modular():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)
    
    # Heat Pump
    manufacturer = "Viessmann Werke GmbH & Co KG"
    name = "Vitocal 300-A AWO-AC 301.B07"
    heat_pump_power = 7420.0

    # L1 Heat Pump Controller 
    minimum_idle_time = 30 * 60
    minimum_operation_time = 15 * 60
    
    # L2 Heat Pump Controller 
    T_min_heating = 17
    T_max_heating = 19

    #definition of outputs
    number_of_outputs = 9
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues( number_of_outputs )

    #===================================================================================================================
    # Set Heat Pump
    my_heat_pump = generic_heat_pump_modular.HeatPump( manufacturer=manufacturer,
                                                       name=name,
                                                       my_simulation_parameters = my_simulation_parameters )

    # Set L1 Heat Pump Controller
    my_heat_pump_controller_l1 = controller_l1_generic_heatpump_modular.L1_Controller( min_operation_time = minimum_operation_time,
                                                                                       min_idle_time = minimum_idle_time, 
                                                                                       my_simulation_parameters = my_simulation_parameters )
    
    # Set L2 Heat Pump Controller
    my_heat_pump_controller_l2 = controller_l2_generic_heatpump_modular.L2_Controller(  T_min_heating = T_min_heating,
                                                                                        T_max_heating = T_max_heating, 
                                                                                        my_simulation_parameters = my_simulation_parameters )

    #definition of weather output
    t_air_outdoorC = cp.ComponentOutput("FakeTemperatureOutside",
                                        "TemperatureAir",
                                        lt.LoadTypes.Temperature,
                                        lt.Units.Watt)
    
    #definition of building output
    t_mC = cp.ComponentOutput("FakeHouse",
                              "TemperatureMean",
                              lt.LoadTypes.Temperature,
                              lt.Units.Watt)
    
    #connection of in- and outputs
    my_heat_pump_controller_l2.ResidenceTemperatureC.SourceOutput = t_mC
    my_heat_pump.TemperatureOutsideC.SourceOutput = t_air_outdoorC
    my_heat_pump.l1_HeatPumpSignalC.SourceOutput = my_heat_pump_controller_l1.l1_HeatPumpSignalC
    my_heat_pump.l1_HeatPumpCompulsoryC.SourceOutput = my_heat_pump_controller_l1.l1_HeatPumpCompulsoryC
    my_heat_pump.l2_HeatPumpSignalC.SourceOutput = my_heat_pump_controller_l2.l2_HeatPumpSignalC
    my_heat_pump.l2_HeatPumpCompulsoryC.SourceOutput = my_heat_pump_controller_l2.l2_HeatPumpCompulsoryC
    my_heat_pump_controller_l1.HeatPumpSignalC.SourceOutput = my_heat_pump.HeatPumpSignalC

    # indexing of in- and outputs
    t_mC.GlobalIndex = 0
    t_air_outdoorC.GlobalIndex = 1
    my_heat_pump_controller_l1.l1_HeatPumpSignalC.GlobalIndex = 2  
    my_heat_pump_controller_l1.l1_HeatPumpCompulsoryC.GlobalIndex = 3
    my_heat_pump_controller_l2.l2_HeatPumpSignalC.GlobalIndex = 4
    my_heat_pump_controller_l2.l2_HeatPumpCompulsoryC.GlobalIndex = 5
    my_heat_pump.HeatPumpSignalC.GlobalIndex = 6
    my_heat_pump.ThermalEnergyDeliveredC.GlobalIndex = 7
    my_heat_pump.ElectricityOutputC.GlobalIndex = 8
    
    #test: after one hour temperature in building is 10 °C 
    stsv.values[ 0 ] = 10
    j = 60
    
    # Simulate
    my_heat_pump_controller_l2.i_restore_state()
    my_heat_pump_controller_l2.i_simulate(j, stsv,  False)
    
    my_heat_pump_controller_l1.i_restore_state()
    my_heat_pump_controller_l1.i_simulate(j, stsv,  False)
   
    my_heat_pump.i_restore_state()
    my_heat_pump.i_simulate(j, stsv, False)

    #-> Did heat pump turn on?
    # Check if there is a signal to heat up the house
    assert 1 == stsv.values[ 6 ]
    # Check if the delivered heat is indeed that corresponded to the heat pump model
    assert heat_pump_power == stsv.values[ 7 ]
