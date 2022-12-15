# # -*- coding: utf-8 -*-
#
# # Generic/Built-in
# import numpy as np
# from typing import Any, List
# # Owned
# import hisim.utils as utils
# from hisim import component as cp
# from hisim.loadtypes import LoadTypes, Units
# from hisim.simulationparameters import SimulationParameters
# from hisim.components.building import Building
# from hisim import log
#
# from dataclasses import dataclass
# from dataclasses_json import dataclass_json
#
# __authors__ = "edited Johanna Ganglbauer"
# __copyright__ = "Copyright 2021, the House Infrastructure Project"
# __credits__ = ["Noah Pflugradt"]
# __license__ = "MIT"
# __version__ = "0.1"
# __maintainer__ = "Vitor Hugo Bellotto Zago"
# __email__ = "vitor.zago@rwth-aachen.de"
# __status__ = "development"
#
#
# @dataclass_json
# @dataclass
# class L2CHPConfig:
#     """
#     GCHP Config
#     """
#     name: str
#     source_weight: int
#     T_min: float
#     T_max: float
#
#     def __init__(self,
#                  name: str,
#                  source_weight: int,
#                  T_min: float,
#                  T_max: float):
#         self.name = name
#         self.source_weight = source_weight
#         self.T_min = T_min
#         self.T_max = T_max
#
#
# class L2_ControllerState:
#     """
#     This data class saves the state of the CHP.
#     """
#
#     def __init__(self, timestep_actual: int = -1, state: int = 0, compulsory: int = 0, count: int = 0):
#         self.timestep_actual = timestep_actual
#         self.state = state
#         self.compulsory = compulsory
#         self.count = count
#
#     def clone(self) -> Any:
#         return L2_ControllerState(timestep_actual=self.timestep_actual, state=self.state, compulsory=self.compulsory,
#                                   count=self.count)
#
#     def is_first_iteration(self, timestep: int) -> bool:
#         if self.timestep_actual + 1 == timestep:
#             self.timestep_actual += 1
#             self.compulsory = 0
#             self.count = 0
#             return True
#         else:
#             return False
#
#     def is_compulsory(self) -> None:
#         if self.count <= 1:
#             self.compulsory = 0
#         else:
#             self.compulsory = 1
#
#     def activate(self) -> None:
#         self.state = 1
#         self.compulsory = 1
#         self.count += 1
#
#     def deactivate(self) -> None:
#         self.state = 0
#         self.compulsory = 1
#         self.count += 1
#
#
# class L2_Controller(cp.Component):
#     """ L2 heat pump controller. Processes signals ensuring comfort temperature of building
#
#     Parameters
#     --------------
#     T_min: float, optional
#         Minimum temperature of water in boiler, in °C. The default is 45 °C.
#     T_max: float, optional
#         Maximum temperature of water in boiler, in °C. The default is 60 °C.
#     T_tolerance : float, optional
#         Temperature difference the boiler may go below or exceed the hysteresis, because of recommendations from L3. The default is 10 °C.
#     source_weight : int, optional
#         Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
#     """
#     # Inputs
#     ReferenceTemperature = "ReferenceTemperature"
#
#     # Outputs
#     l2_DeviceSignal = "l2_DeviceSignal"
#
#     @utils.measure_execution_time
#     def __init__(self,
#                  config: L2CHPConfig,
#                  my_simulation_parameters: SimulationParameters):
#         super().__init__(config.name + str(config.source_weight), my_simulation_parameters=my_simulation_parameters)
#         self.build(config)
#
#         # Component Inputs
#         self.ReferenceTemperatureC: cp.ComponentInput = self.add_input(self.component_name,
#                                                                        self.ReferenceTemperature,
#                                                                        LoadTypes.TEMPERATURE,
#                                                                        Units.CELSIUS,
#                                                                        mandatory=True)
#         self.add_default_connections(Building, self.get_building_default_connections())
#
#         # Component outputs
#         self.l2_DeviceSignalC: cp.ComponentOutput = self.add_output(self.component_name,
#                                                                     self.l2_DeviceSignal,
#                                                                     LoadTypes.ON_OFF,
#                                                                     Units.BINARY)
#
#     @staticmethod
#     def get_default_config() -> L2CHPConfig:
#         config = L2CHPConfig(name='CHPTemperatureHydrogenAndElectricityController',
#                              source_weight=1,
#                              T_min=20,
#                              T_max=22)
#         return config
#
#     def get_building_default_connections(self) -> List[cp.ComponentConnection]:
#         log.information("setting building default connections in L2 CHP Controller")
#         connections: List[cp.ComponentConnection] = []
#         building_classname = Building.get_classname()
#         connections.append(
#             cp.ComponentConnection(L2_Controller.ReferenceTemperature, building_classname, Building.TemperatureMean))
#         return connections
#
#     def i_prepare_simulation(self) -> None:
#         """ Prepares the simulation. """
#         pass
#
#     def build(self, config: L2CHPConfig) -> None:
#
#         self.name = config.name
#         self.source_weight = config.source_weight
#         self.T_min = config.T_min
#         self.T_max = config.T_max
#         self.state = L2_ControllerState()
#         self.previous_state = L2_ControllerState()
#
#     def i_save_state(self) -> None:
#         self.previous_state = self.state.clone()
#
#     def i_restore_state(self) -> None:
#         self.state = self.previous_state.clone()
#
#     def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
#         pass
#
#     def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
#         # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
#         if force_convergence:
#             T_control = stsv.get_input_value(self.ReferenceTemperatureC)
#             if T_control < (self.T_max + self.T_min) / 2:
#                 stsv.set_output_value(self.l2_DeviceSignalC, 1)
#             else:
#                 stsv.set_output_value(self.l2_DeviceSignalC, 0)
#
#         # get temperature of building
#         T_control = stsv.get_input_value(self.ReferenceTemperatureC)
#
#         # check if it is the first iteration and reset compulsory and timestep_of_last_activation in state and previous_state
#         if self.state.is_first_iteration(timestep):
#             self.previous_state.is_first_iteration(timestep)
#
#         # check out
#         if T_control > self.T_max:
#             # stop heating if temperature exceeds upper limit
#             self.state.deactivate()
#             self.previous_state.deactivate()
#
#         elif T_control < self.T_min:
#             # start heating if temperature goes below lower limit
#             self.state.activate()
#             self.previous_state.activate()
#         else:
#             if self.state.compulsory == 1:
#                 # use previous state if it compulsory
#                 pass
#             else:
#                 # use revious state if l3 was not available
#                 self.state = self.previous_state.clone()
#
#         stsv.set_output_value(self.l2_DeviceSignalC, self.state.state)
#
#     def prin1t_outpu1t(self, t_m: float, state: L2_ControllerState) -> None:
#         log.information("==========================================")
#         log.information("T m: {}".format(t_m))
#         log.information("State: {}".format(state))
#
#     def write_to_report(self) -> List[str]:
#         lines = []
#         lines.append("Generic Controller L2: " + self.component_name)
#         return lines
#
