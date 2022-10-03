# # Generic/Built-in
# from typing import Any
# # Owned
# import copy
# import numpy as np
#
# from hisim import dynamic_component
# from hisim import component as cp
# from hisim import loadtypes as lt
# from hisim.simulationparameters import SimulationParameters
# from hisim import log
# from hisim import utils
# from typing import List
# from dataclasses import dataclass
# from dataclasses_json import dataclass_json
#
#
# class ControllerHeatGeneric(dynamic_component.DynamicComponent):
#     """
#     Controlls energy flows for heat demand.
#     Heat Demand can be simulated by a sinking storage temperature.
#     For this storage provides heat for a load profile or for the
#     building. As well Heat Demand can be simulated without storage.
#     Heating component can be added generically.
#     """
#     # Inputs
#
#     StorageTemperatureHeatingWater = "StorageTemperatureHeatingWater"
#     StorageTemperatureWarmWater = "StorageTemperatureWarmWater"
#     ResidenceTemperature = "ResidenceTemperature"
#     my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
#
#     # Outputs
#     my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
#
#     ControlSignalGasHeater = "ControlSignalGasHeater"
#     ControlSignalChp = "ControlSignalChp"
#     ControlSignalHeatPump = "ControlSignalHeatPump"
#     ControlSignalChooseStorage = "ControlSignalChooseStorage"
#
#     CheckPeakShaving = "CheckPeakShaving"
#
#     @utils.measure_execution_time
#     def __init__(self,
#                  my_simulation_parameters: SimulationParameters,
#                  temperature_storage_target_warm_water: float = 50,
#                  temperature_storage_target_heating_water: float = 35,
#                  temperature_storage_target_hysteresis_ww: float = 45,
#                  temperature_storage_target_hysteresis_hw: float = 30,
#                  max_comfortable_temperature_residence: float = 23,
#                  min_comfortable_temperature_residence: float = 19):
#         super().__init__(my_component_inputs=self.my_component_inputs,
#                          my_component_outputs=self.my_component_outputs,
#                          name="Controller",
#                          my_simulation_parameters=my_simulation_parameters)
#
#         self.temperature_storage_target_warm_water = temperature_storage_target_warm_water
#         self.temperature_storage_target_heating_water = temperature_storage_target_heating_water
#         self.temperature_storage_target_hysteresis_hw = temperature_storage_target_hysteresis_hw
#         self.temperature_storage_target_hysteresis_ww = temperature_storage_target_hysteresis_ww
#         self.max_comfortable_temperature_residence = max_comfortable_temperature_residence
#         self.min_comfortable_temperature_residence = min_comfortable_temperature_residence
#         self.state = ControllerState(control_signal_heat_pump=0,
#                                      control_signal_gas_heater=0,
#                                      control_signal_chp=0,
#                                      temperature_storage_target_ww_C=self.temperature_storage_target_warm_water,
#                                      temperature_storage_target_hw_C=self.temperature_storage_target_heating_water,
#                                      timestep_of_hysteresis_ww=0,
#                                      timestep_of_hysteresis_hw=0)
#         self.previous_state = self.state.clone()
#         ###Inputs
#         self.temperature_storage_warm_water: cp.ComponentInput = self.add_input(self.component_name,
#                                                                                 self.StorageTemperatureWarmWater,
#                                                                                 lt.LoadTypes.WATER,
#                                                                                 lt.Units.CELSIUS,
#                                                                                 False)
#         self.temperature_storage_heating_water: cp.ComponentInput = self.add_input(self.component_name,
#                                                                                    self.StorageTemperatureHeatingWater,
#                                                                                    lt.LoadTypes.WATER,
#                                                                                    lt.Units.CELSIUS,
#                                                                                    False)
#         self.temperature_residence: cp.ComponentInput = self.add_input(self.component_name,
#                                                                        self.ResidenceTemperature,
#                                                                        lt.LoadTypes.TEMPERATURE,
#                                                                        lt.Units.CELSIUS,
#                                                                        False)
#
#         # Outputs
#         self.control_signal_gas_heater: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                              field_name=self.ControlSignalGasHeater,
#                                                                              load_type=lt.LoadTypes.ANY,
#                                                                              unit=lt.Units.PERCENT,
#                                                                              sankey_flow_direction=False)
#         self.control_signal_chp: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                       field_name=self.ControlSignalChp,
#                                                                       load_type=lt.LoadTypes.ANY,
#                                                                       unit=lt.Units.PERCENT,
#                                                                       sankey_flow_direction=False)
#         self.control_signal_heat_pump: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                             field_name=self.ControlSignalHeatPump,
#                                                                             load_type=lt.LoadTypes.ANY,
#                                                                             unit=lt.Units.PERCENT,
#                                                                             sankey_flow_direction=False)
#         self.control_signal_choose_storage: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                                  field_name=self.ControlSignalChooseStorage,
#                                                                                  load_type=lt.LoadTypes.ANY,
#                                                                                  unit=lt.Units.ANY,
#                                                                                  sankey_flow_direction=False)
#
#     def build(self, mode: Any) -> None:
#         self.mode = mode
#
#     def write_to_report(self) -> None :
#         pass
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
#     '''
#     def control_electricity_component(self,demand:float,
#                                    stsv:cp.SingleTimeStepValues,
#                                    MyComponentInputs: list,
#                                    MyComponentOutputs:list,
#                                    weight_counter:int,
#                                    component_type: list,
#                                    input_type: lt.InandOutputType,
#                                    output_type:lt.InandOutputType):
#         # to do: add that to much chp-electricty is charged in Battery and doesnt go in to grid
#         for index, element in enumerate(MyComponentOutputs):
#             for tags in element.SourceTags:
#                 if tags.__class__ == lt.ComponentType and tags in component_type:
#                     if element.SourceWeight == weight_counter:
#                         # more electricity than needed
#                         if tags ==lt.ComponentType.Battery:
#                             stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), demand)
#                             break
#                         elif tags ==lt.ComponentType.FuelCell:
#                             if demand < 0:
#                                 stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), -demand)
#                             else:
#                                 stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), 0)
#                             break
#             else:
#                 continue
#             break
#         for index, element in enumerate(MyComponentInputs):
#             for tags in element.SourceTags:
#                 if tags.__class__ == lt.ComponentType and tags in component_type:
#                     if element.SourceWeight == weight_counter:
#                         if tags == lt.ComponentType.Battery:
#                             demand = demand - stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
#                             break
#                         elif tags == lt.ComponentType.FuelCell:
#                             demand = demand + stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
#                             break
#             else:
#                 continue
#             break
#         return demand
#     '''
#
#     # Simulates waterstorages and defines the control signals to heat up storages
#     # work as a 2-point Ruler with Hysteresis
#     def simulate_storage(self, delta_temperature: float,
#                          stsv: cp.SingleTimeStepValues,
#                          timestep: int, temperature_storage: float,
#                          temperature_storage_target: float, temperature_storage_target_hysteresis: float,
#                          temperature_storage_target_C: float, timestep_of_hysteresis: int) -> Any:
#         control_signal_chp: float = 0
#         control_signal_gas_heater: float = 0
#         control_signal_heat_pump: float = 0
#         temperature_storage_target_C = temperature_storage_target_C
#         timestep_of_hysteresis = timestep_of_hysteresis
#
#         # WaterStorage
#         # Heating Components get turned on when storage is underneath target temperature
#         if temperature_storage > 0:
#             if delta_temperature >= 10:
#                 control_signal_heat_pump = 1
#                 control_signal_chp = 1
#                 control_signal_gas_heater = 1
#                 temperature_storage_target_C = temperature_storage_target
#
#             elif delta_temperature > 5 and delta_temperature < 10:
#                 control_signal_heat_pump = 1
#                 if self.state.control_signal_chp < 1:
#                     control_signal_chp = 1
#                     control_signal_gas_heater = 1
#                 elif self.state.control_signal_chp == 1:
#                     control_signal_gas_heater = 1
#                 temperature_storage_target_C = temperature_storage_target
#
#             elif delta_temperature > 0 and delta_temperature <= 5:
#                 control_signal_heat_pump = 1
#                 if self.state.control_signal_chp < 1:
#                     control_signal_chp = 1
#                 elif self.state.control_signal_chp == 1:
#                     control_signal_gas_heater = 0.5
#                 temperature_storage_target_C = temperature_storage_target
#
#                 # Storage warm enough. Try to turn off Heaters
#             elif delta_temperature <= 0:
#                 if temperature_storage_target_C == temperature_storage_target and timestep_of_hysteresis != timestep:
#                     temperature_storage_target_C = temperature_storage_target_hysteresis
#                     timestep_of_hysteresis = timestep
#                 elif temperature_storage_target_C != temperature_storage_target and timestep_of_hysteresis != timestep:
#                     control_signal_heat_pump = 0
#                     control_signal_gas_heater = 0
#                     control_signal_chp = 0
#
#         self.state.control_signal_gas_heater = control_signal_gas_heater
#         self.state.control_signal_chp = control_signal_chp
#         self.state.control_signal_heat_pump = control_signal_heat_pump
#         stsv.set_output_value(self.control_signal_heat_pump, control_signal_heat_pump)
#         stsv.set_output_value(self.control_signal_gas_heater, control_signal_gas_heater)
#         stsv.set_output_value(self.control_signal_chp, control_signal_chp)
#
#         return temperature_storage_target_C, \
#                timestep_of_hysteresis
#
#     def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
#         if force_convergence:
#             return
#         #######HEAT########
#         # If comftortable temperature of building is to low heat with WarmWaterStorage the building
#         # Solution with Control Signal Residence
#         # not perfect solution!
#         '''
#         if self.temperature_residence<self.min_comfortable_temperature_residence:
#
#
#             #heat
#             #here has to be added how "strong" HeatingWater Storage can be discharged
#             #Working with upper boarder?
#
#         elif self.temperature_residence > self.max_comfortable_temperature_residence:
#             #cool
#         elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
#         '''
#
#         # Logic of regulating HeatDemand:
#         # First heat up WarmWaterStorage->more important, than heat up HeatingWater
#         # But only one Storage can be heated up in a TimeStep!
#         # Simulate WarmWater
#
#         delta_temperature_ww = self.state.temperature_storage_target_ww_C - stsv.get_input_value(
#             self.temperature_storage_warm_water)
#         delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
#             self.temperature_storage_heating_water)
#
#         # Choose which Storage should be heated up
#         if delta_temperature_ww >= 0 and delta_temperature_hw >= 0:
#             if delta_temperature_hw <= delta_temperature_ww:
#                 control_signal_choose_storage = 1
#             else:
#                 control_signal_choose_storage = 2
#         elif delta_temperature_ww < 0 and delta_temperature_hw < 0:
#             if delta_temperature_hw <= delta_temperature_ww:
#                 control_signal_choose_storage = 1
#             else:
#                 control_signal_choose_storage = 2
#         elif delta_temperature_ww <= 0 and delta_temperature_hw >= 0:
#             control_signal_choose_storage = 2
#         elif delta_temperature_ww >= 0 and delta_temperature_hw <= 0:
#             control_signal_choose_storage = 1
#
#         # Heats up storage
#         if self.temperature_storage_heating_water.source_output is None:
#             control_signal_choose_storage = 0
#         if control_signal_choose_storage == 1:
#             self.state.temperature_storage_target_ww_C, self.state.timestep_of_hysteresis_ww = self.simulate_storage(
#                 stsv=stsv,
#                 delta_temperature=delta_temperature_ww,
#                 timestep=timestep,
#                 temperature_storage=stsv.get_input_value(self.temperature_storage_warm_water),
#                 temperature_storage_target=self.temperature_storage_target_warm_water,
#                 temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_ww,
#                 temperature_storage_target_C=self.state.temperature_storage_target_ww_C,
#                 timestep_of_hysteresis=self.state.timestep_of_hysteresis_ww)
#         elif control_signal_choose_storage == 2:
#             delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
#                 self.temperature_storage_heating_water)
#             self.state.temperature_storage_target_hw_C, self.state.timestep_of_hysteresis_hw = self.simulate_storage(
#                 stsv=stsv,
#                 delta_temperature=delta_temperature_hw,
#                 timestep=timestep,
#                 temperature_storage=stsv.get_input_value(self.temperature_storage_heating_water),
#                 temperature_storage_target=self.temperature_storage_target_heating_water,
#                 temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_hw,
#                 temperature_storage_target_C=self.state.temperature_storage_target_hw_C,
#                 timestep_of_hysteresis=self.state.timestep_of_hysteresis_hw)
#
#         stsv.set_output_value(self.control_signal_choose_storage, control_signal_choose_storage)
