from hisim import component as cp
from hisim.components import heat_pump
from hisim.components import genericsurpluscontroller
from hisim.components import controllable
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
def test_smart_device_library():
    """
    Test if it can load the smart device library
    """
    # Heat Pump
    manufacturer = "Viessmann Werke GmbH & Co KG"
    name = "Vitocal 300-A AWO-AC 301.B07"
    minimum_idle_time = 30
    minimum_operation_time = 15
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
                                                                 seconds_per_timestep=60)
    # Set Heat Pump
    heat_pump.HeatPump(manufacturer=manufacturer,
                           name=name,
                           min_operation_time=minimum_idle_time,
                           min_idle_time=minimum_operation_time, my_simulation_parameters=mysim)

def test_smart_device():
    """
    Test time shifting for smart devices
    """
    seconds_per_timestep = 60
    number_of_outputs = 4
    available_electricity = 0

    stsv : cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    available_electricity_outputC = cp.ComponentOutput("ElectricityHomeGrid",
                                                       "ElectricityOutput",
                                                       lt.LoadTypes.Electricity,
                                                       lt.Units.Watt)
    mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
                                                                 seconds_per_timestep=60)
    # Create Controller
    my_flexible_controller = genericsurpluscontroller.GenericSurplusController(my_simulation_parameters=mysim, mode=1)
    # Create Controllable
    my_controllable = controllable.Controllable("Washing", my_simulation_parameters=mysim)

    # Connect inputs and outputs
    my_flexible_controller.electricity_inputC.SourceOutput = available_electricity_outputC
    my_controllable.ApplianceRun.SourceOutput = my_flexible_controller.stateC

    available_electricity_outputC.GlobalIndex = 0
    stsv.values[0] = available_electricity

    my_flexible_controller.stateC.GlobalIndex = 1
    my_controllable.electricity_outputC.GlobalIndex = 2
    my_controllable.taskC.GlobalIndex = 3

    timestep = 2149
    my_controllable.i_save_state()
    my_controllable.i_restore_state()
    my_flexible_controller.i_simulate(timestep, stsv,  False)
    my_controllable.i_simulate(timestep, stsv, False)
    log.information("Signal: {}, Electricity: {}, Task: {}".format(stsv.values[1],stsv.values[2],stsv.values[3]))

    # Signal
    assert 1.0 == stsv.values[1]
    # Electricity Load for flexibility
    assert 0.20805582786885163 == stsv.values[2]
