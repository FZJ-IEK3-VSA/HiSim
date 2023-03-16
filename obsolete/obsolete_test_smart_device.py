# from hisim import component as cp
# from hisim.components import generic_heat_pump
# from obsolete import obsolete_generic_smart_device, obsolete_generic_smart_device_controller
# from hisim import loadtypes as lt
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from tests import functions_for_testing as fft
# def test_smart_device_library():
#     """
#     Test if it can load the smart device library
#     """
#     # Heat Pump
#     manufacturer = "Viessmann Werke GmbH & Co KG"
#     name = "Vitocal 300-A AWO-AC 301.B07"
#     minimum_idle_time = 30
#     minimum_operation_time = 15
#     mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
#                                                                  seconds_per_timestep=60)
#     # Set Heat Pump
#     generic_heat_pump.GenericHeatPump(manufacturer=manufacturer,
#                                       name=name,
#                                       min_operation_time=minimum_idle_time,
#                                       min_idle_time=minimum_operation_time, my_simulation_parameters=mysim)
#
# def test_smart_device():
#     """
#     Test time shifting for smart devices
#     """
#     seconds_per_timestep = 60
#
#     available_electricity = 0
#
#     available_electricity_outputC = cp.ComponentOutput("ElectricityHomeGrid",
#                                                        "ElectricityOutput",
#                                                        lt.LoadTypes.ELECTRICITY,
#                                                        lt.Units.WATT)
#     mysim: SimulationParameters = SimulationParameters.full_year(year=2021,
#                                                                  seconds_per_timestep=60)
#     # Create Controller
#     my_flexible_controller = obsolete_generic_smart_device_controller.GenericSurplusController(my_simulation_parameters=mysim, mode=1)
#     # Create Controllable
#     my_controllable = obsolete_generic_smart_device.Controllable("Washing", my_simulation_parameters=mysim)
#
#     number_of_outputs = fft.get_number_of_outputs([available_electricity_outputC,my_flexible_controller,my_controllable])
#     stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)
#
#     # Connect inputs and outputs
#     my_flexible_controller.electricity_inputC.source_output = available_electricity_outputC
#     my_controllable.ApplianceRun.source_output = my_flexible_controller.stateC
#
#     # Add Global Index and set values for fake Inputs
#     fft.add_global_index_of_components([available_electricity_outputC,my_flexible_controller,my_controllable])
#     stsv.values[available_electricity_outputC.global_index] = available_electricity
#
#     #Simulate
#     timestep = 2149
#     my_controllable.i_save_state()
#     my_controllable.i_restore_state()
#     my_flexible_controller.i_simulate(timestep, stsv,  False)
#     my_controllable.i_simulate(timestep, stsv, False)
#     log.information("Signal: {}, Electricity: {}, Task: {}".format(stsv.values[my_flexible_controller.stateC.global_index],
#                                                                    stsv.values[my_controllable.electricity_outputC.global_index],
#                                                                    stsv.values[my_controllable.taskC.global_index]))
#
#     # Signal
#     assert 1.0 == stsv.values[my_flexible_controller.stateC.global_index]
#     # Electricity Load for flexibility
#     assert 0.20805582786885163 == stsv.values[my_controllable.electricity_outputC.global_index]
