# from hisim import component as cp
# from hisim import loadtypes as lt
# from hisim.simulationparameters import SimulationParameters
# from typing import Any
# class GenericSurplusController(cp.Component):
#     ElectricityInput = "ElectricityInput"
#     State = "State"
#
#     def __init__(self,my_simulation_parameters: SimulationParameters , mode:Any=1)-> None:
#         super().__init__("FlexibleController", my_simulation_parameters=my_simulation_parameters)
#
#         self.build(mode)
#
#         # Retrieves Electricity SUM
#         self.electricity_inputC: cp.ComponentInput = self.add_input(self.component_name,
#                                                                     self.ElectricityInput,
#                                                                     lt.LoadTypes.ELECTRICITY,
#                                                                     lt.Units.WATT,
#                                                                     True)
#         # Returns boolean based on control condition
#         self.stateC: cp.ComponentOutput = self.add_output(self.component_name,
#                                                           self.State,
#                                                           lt.LoadTypes.ANY,
#                                                           lt.Units.ANY)
#
#     def build(self, mode: Any)-> None:
#         self.mode = mode
#
#     def i_save_state(self)-> None:
#         pass
#         #self.previous_state = self.state
#
#     def i_restore_state(self)-> None:
#         pass
#         #self.state = self.previous_state
#
#     def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues)-> None:
#         pass
#
#     def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool)-> None:
#         if force_convergence:
#             return
#         val1 = stsv.get_input_value(self.electricity_inputC)
#         if self.mode == 1:
#             state = 1
#         elif self.mode == 2:
#             if (val1 < 0.0):
#                 state = 1
#             else:
#                 state = 0
#         else:
#             raise Exception("Mode not defined!")
#         stsv.set_output_value(self.stateC, state)
