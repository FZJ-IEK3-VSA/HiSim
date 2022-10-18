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
# @dataclass_json
# @dataclass
# class ControllerElectricityConfig:
#     strategy: str = "optimize_own_consumption"
#     # strategy=["optimize_own_consumption","peak_shaving_from_grid", "peak_shaving_into_grid","seasonal_storage"]
#     limit_to_shave: float = 0
#
#
# class ControllerElectricity(cp.Component):
#     """
#     Controlls energy flows for electricity and heat demand.
#     Electricity storages can be ruled in 4 different strategies.
#     """
#     # Inputs
#     ElectricityConsumptionBuilding = "ElectricityConsumptionBuilding"
#     ElectricityOutputPvs = "ElectricityOutputPvs"
#     ElectricityDemandHeatPump = "ElectricityDemandHeatPump"
#     ElectricityToOrFromBatteryReal = "ElectricityToOrFromBatteryReal"
#     ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
#     ElectricityFromCHPReal = "ElectricityFromCHPReal"
#
#     # Outputs
#     ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"
#     ElectricityToOrFromBatteryTarget = "ElectricityToOrFromBatteryTarget"
#     ElectricityFromCHPTarget = "ElectricityFromCHPTarget"
#     ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
#
#     @utils.measure_execution_time
#     def __init__(self,
#                  my_simulation_parameters: SimulationParameters,
#                  config: ControllerElectricityConfig) -> None:
#         super().__init__(name="EMSElectricityController", my_simulation_parameters=my_simulation_parameters)
#
#         self.strategy = config.strategy
#         self.limit_to_shave = config.limit_to_shave
#
#         ###Inputs
#         self.electricity_consumption_building: cp.ComponentInput = self.add_input(self.component_name,
#                                                                                   self.ElectricityConsumptionBuilding,
#                                                                                   lt.LoadTypes.ELECTRICITY,
#                                                                                   lt.Units.WATT,
#                                                                                   False)
#         self.electricity_output_pvs: cp.ComponentInput = self.add_input(self.component_name,
#                                                                         self.ElectricityOutputPvs,
#                                                                         lt.LoadTypes.ELECTRICITY,
#                                                                         lt.Units.WATT,
#                                                                         False)
#
#         self.electricity_to_or_from_battery_real: cp.ComponentInput = self.add_input(self.component_name,
#                                                                                      self.ElectricityToOrFromBatteryReal,
#                                                                                      lt.LoadTypes.ELECTRICITY,
#                                                                                      lt.Units.WATT,
#                                                                                      False)
#         self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(self.component_name,
#                                                                                     self.ElectricityToElectrolyzerUnused,
#                                                                                     lt.LoadTypes.ELECTRICITY,
#                                                                                     lt.Units.WATT,
#                                                                                     False)
#         self.electricity_from_chp_real: cp.ComponentInput = self.add_input(self.component_name,
#                                                                            self.ElectricityFromCHPReal,
#                                                                            lt.LoadTypes.ELECTRICITY,
#                                                                            lt.Units.WATT,
#                                                                            False)
#         self.electricity_demand_heat_pump: cp.ComponentInput = self.add_input(self.component_name,
#                                                                               self.ElectricityDemandHeatPump,
#                                                                               lt.LoadTypes.ELECTRICITY,
#                                                                               lt.Units.WATT,
#                                                                               False)
#
#         # Outputs
#
#         self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                                field_name=self.ElectricityToOrFromGrid,
#                                                                                load_type=lt.LoadTypes.ELECTRICITY,
#                                                                                unit=lt.Units.WATT,
#                                                                                sankey_flow_direction=False)
#         self.electricity_from_chp_target: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                                field_name=self.ElectricityFromCHPTarget,
#                                                                                load_type=lt.LoadTypes.ELECTRICITY,
#                                                                                unit=lt.Units.WATT,
#                                                                                sankey_flow_direction=False)
#         self.electricity_to_electrolyzer_target: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                                       field_name=self.ElectricityToElectrolyzerTarget,
#                                                                                       load_type=lt.LoadTypes.ELECTRICITY,
#                                                                                       unit=lt.Units.WATT,
#                                                                                       sankey_flow_direction=False)
#         self.electricity_to_or_from_battery_target: cp.ComponentOutput = self.add_output(object_name=self.component_name,
#                                                                                          field_name=self.ElectricityToOrFromBatteryTarget,
#                                                                                          load_type=lt.LoadTypes.ELECTRICITY,
#                                                                                          unit=lt.Units.WATT,
#                                                                                          sankey_flow_direction=False)
#
#     def build(self, mode:Any) -> None:
#         self.mode = mode
#
#     def write_to_report(self) -> None:
#         pass
#
#     def i_save_state(self) -> None:
#         pass
#
#     def i_restore_state(self) -> None:
#         pass
#
#     def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues)  -> None:
#         pass
#
#     def optimize_own_consumption(self, delta_demand: float, stsv: cp.SingleTimeStepValues)  -> None:
#
#         electricity_to_or_from_battery_target: float = 0
#         electricity_from_chp_target: float = 0
#         electricity_to_or_from_grid: float = 0
#
#         # Check if Battery is Component of Simulation
#         if self.electricity_to_or_from_battery_real.source_output is not None:
#             electricity_to_or_from_battery_target = delta_demand
#
#         # electricity_not_used_battery of Charge or Discharge
#         electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
#             self.electricity_to_or_from_battery_real)
#         # more electricity than needed
#         if delta_demand > 0:
#             # Negative sign, because Electricity will flow into grid->Production of Electricity
#             electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(self.electricity_to_or_from_battery_real)
#
#         # less electricity than needed
#         elif delta_demand < 0:
#             if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_from_chp_real.source_output is not None:
#                 electricity_from_chp_target = -delta_demand + stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real)
#
#             # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
#             electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
#                 self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)
#
#         stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
#         stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
#         stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
#
#     # seasonal storaging is almost the same as own_consumption, but a electrolyzer is added
#     # follows strategy to first charge battery than produce H2
#     def seasonal_storage(self, delta_demand: float, stsv: cp.SingleTimeStepValues) -> None:
#
#         electricity_to_or_from_battery_target: float = 0
#         electricity_from_chp_target: float = 0
#         electricity_to_or_from_grid: float = 0
#         electricity_to_electrolyzer_target: float = 0
#
#         # Check if Battery is Component of Simulation
#         if self.electricity_to_or_from_battery_real.source_output is not None:
#             electricity_to_or_from_battery_target = delta_demand
#
#         # electricity_not_used_battery of Charge or Discharge
#         electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
#             self.electricity_to_or_from_battery_real)
#         # more electricity than needed
#         if delta_demand > 0:
#             # Check if enough electricity is there to charge CHP (finds real solution after 2 Iteration-Steps)
#             if self.electricity_to_electrolyzer_unused.source_output is not None:
#                 # possibility to  produce H2
#                 electricity_to_electrolyzer_target = delta_demand - stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real)
#                 if electricity_to_electrolyzer_target < 0:
#                     electricity_to_electrolyzer_target = 0
#
#             # Negative sign, because Electricity will flow into grid->Production of Electricity
#             electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
#                 self.electricity_to_or_from_battery_real) + (electricity_to_electrolyzer_target - stsv.get_input_value(
#                 self.electricity_to_electrolyzer_unused))
#
#         # less electricity than needed
#         elif delta_demand < 0:
#
#             if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_from_chp_real.source_output is not None:
#                 electricity_from_chp_target = -delta_demand + stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real)
#
#             # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
#             electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
#                 self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)
#
#         stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
#         stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
#         stsv.set_output_value(self.electricity_to_electrolyzer_target, electricity_to_electrolyzer_target)
#         stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
#
#     # peak-shaving from grid tries to reduce/shave electricity from grid to an defined boarder
#     # just used for industry, trade and service
#     # so far no chp is added. But produces elect. has to be addded to delta demand
#     def peak_shaving_from_grid(self, delta_demand: float, limit_to_shave: float, stsv: cp.SingleTimeStepValues) -> None:
#         electricity_to_or_from_battery_target: float = 0
#         check_peak_shaving: float = 0
#
#         # more electricity than needed
#         if delta_demand > 0:
#             electricity_to_or_from_battery_target = delta_demand
#         elif -delta_demand > limit_to_shave:
#             check_peak_shaving = 1
#             electricity_to_or_from_battery_target = delta_demand + limit_to_shave
#             if -delta_demand + limit_to_shave + stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real) > 0:
#                 check_peak_shaving = -delta_demand + limit_to_shave + stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real)
#         electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
#             self.electricity_to_or_from_battery_real)
#
#         stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
#         stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
#
#     # peak-shaving from grid tries to reduce/shave electricity into grid to an defined boarder
#     # so far no chp is added. But produces elect. has to be addded to delta demand
#     def peak_shaving_into_grid(self, delta_demand: float, limit_to_shave: float, stsv: cp.SingleTimeStepValues)-> None:
#         # Hier delta Demand noch die Leistung aus CHP hinzufügen
#         electricity_to_or_from_battery_target: float = 0
#         check_peak_shaving: float = 0
#
#         if delta_demand > limit_to_shave:
#             electricity_to_or_from_battery_target = delta_demand - limit_to_shave
#
#             if delta_demand - limit_to_shave - stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real) > 0:
#                 check_peak_shaving = delta_demand - limit_to_shave - stsv.get_input_value(
#                     self.electricity_to_or_from_battery_real)  # Peak Shaving didnt work
#             else:
#                 check_peak_shaving = 1
#         elif delta_demand < 0:
#             electricity_to_or_from_battery_target = delta_demand
#
#         electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
#             self.electricity_to_or_from_battery_real)
#         stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
#         stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
#
#     def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool)-> None:
#         if force_convergence:
#             return
#
#         ###ELECTRICITY#####
#         limit_to_shave = self.limit_to_shave
#         # Production of Electricity positve sign
#         # Consumption of Electricity negative sign
#         delta_demand = stsv.get_input_value(self.electricity_output_pvs) \
#                        - stsv.get_input_value(self.electricity_consumption_building) \
#                        - stsv.get_input_value(self.electricity_demand_heat_pump)
#
#         if self.strategy == "optimize_own_consumption":
#             self.optimize_own_consumption(delta_demand=delta_demand, stsv=stsv)
#         elif self.strategy == "seasonal_storage":
#             self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
#         elif self.strategy == "peak_shaving_into_grid":
#             self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave, stsv=stsv)
#         elif self.strategy == "peak_shaving_from_grid":
#             self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave, stsv=stsv)
#
