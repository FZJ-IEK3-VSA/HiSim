# # type: ignore
# # Owned
# from hisim.component import Component, ComponentOutput, ComponentInput, SingleTimeStepValues
# from hisim import loadtypes as lt
#
# from hisim.components.configuration import LoadConfig
# from hisim.components.configuration import AdvElectrolyzerConfig
# from hisim.components.configuration import HouseholdWarmWaterDemandConfig
# from hisim.components.configuration import PhysicsConfig
# from hisim.simulationparameters import SimulationParameters
# from typing import List
# import hisim.log as log
#
# class HouseholdHeatDemand(Component):
#     """
#     Component class the checks if space head and warm water
#     demand is covered.
#
#     Parameters
#     ----------
#     componant_name: str
#         Component name
#     seconds_per_timestep : int
#         Number of seconds per simulation time step
#     """
#
#     HeatDemand = "Heat demand"                  # W
#     MassInput = "Mass Input"                    # kg/s
#     TemperatureInput = "Temperature Input"      # °C
#
#     MassOutput = "Mass Output"                  # kg/s
#     TemperatureOutput = "Temperature Output"    # °C
#
#     DemandSatisfied = "Demand Satisfied"        # 0 or 1
#
#     def __init__(self, component_name, my_simulation_parameters: SimulationParameters ):
#         super().__init__(name=component_name, my_simulation_parameters=my_simulation_parameters)
#         self.heat_demand: ComponentInput = self.add_input(self.component_name, HouseholdHeatDemand.HeatDemand, lt.LoadTypes.WARM_WATER, lt.Units.WATT, True)
#         self.mass_input: ComponentInput = self.add_input(self.component_name, HouseholdHeatDemand.MassInput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True)
#         self.temperature_input: ComponentInput = self.add_input(self.component_name, HouseholdHeatDemand.TemperatureInput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS, True)
#
#         self.mass_output: ComponentOutput = self.add_output(self.component_name, HouseholdHeatDemand.MassOutput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
#         self.temperature_output: ComponentOutput = self.add_output(self.component_name, HouseholdHeatDemand.TemperatureOutput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS)
#
#         self.demand_satisfied: ComponentOutput = self.add_output(self.component_name, HouseholdHeatDemand.DemandSatisfied, lt.LoadTypes.WARM_WATER, lt.Units.ANY)
#
#         self.state:List = []
#         self.previous_state = self.state
#         self.check_temperature = 50
#
#     def i_save_state(self):
#         pass
#
#     def i_restore_state(self):
#         pass
#
#     def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
#         if force_convergence:
#             return
#         """
#         The heat demand is given in kWh/timestep by LPG.
#         The input mass is given in kg/s and is converted to kg/timestep.
#         """
#         heat_demand = stsv.get_input_value(self.heat_demand)        # W
#         mass_input_sec = stsv.get_input_value(self.mass_input)      # kg/s
#         mass_input = mass_input_sec * self.my_simulation_parameters.seconds_per_timestep     # kg
#         # massflow is generated in this component. Actually no conversion for the inflow needed.
#
#         temperature_input = stsv.get_input_value(self.temperature_input)    # °C
#
#         if heat_demand > 0 and (mass_input == 0 and temperature_input == 0):
#             """first iteration --> random numbers"""
#             temperature_input = 40.456
#             mass_input = 0.0123 * self.my_simulation_parameters.seconds_per_timestep
#
#         if heat_demand > 0:
#             # massflow by configuration class is given in kg/s
#
#             # J = W * s
#             massflows_possible = LoadConfig.possible_massflows_load  # kg/s
#             mass_flow_level = 0
#             # K = W / (W/kgK * kg/s)
#             temperature_delta_heat = heat_demand / (PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin * massflows_possible[mass_flow_level])
#             while temperature_delta_heat > LoadConfig.delta_T:
#                 mass_flow_level += 1
#                 temperature_delta_heat = heat_demand / (PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin * massflows_possible[mass_flow_level])
#
#             # kg/timestep = kg/s * seconds_per_timestep
#             mass_input_load = massflows_possible[mass_flow_level] * self.my_simulation_parameters.seconds_per_timestep
#
#             # mass_input_load = LoadConfig.massflow_load * self.seconds_per_timestep
#             energy_demand = heat_demand * self.my_simulation_parameters.seconds_per_timestep
#             enthalpy_slice = mass_input_load * temperature_input * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
#             enthalpy_new = enthalpy_slice - energy_demand
#             temperature_new = enthalpy_new / (mass_input_load * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin)
#
#
#         else:
#             # no water is flowing
#             temperature_new = temperature_input
#             mass_input_load = 0
#
#         if temperature_new < LoadConfig.temperature_returnflow_minimum and heat_demand > 0:
#             demand_satisfied = 0
#             # log.information("The backfolwing temperature from the load is too small. The chosen system can't provide the requirements.")
#             # log.information(temperature_new)
#             # ToDo: Switch to level 2 of the pump ?
#
#         else:
#             demand_satisfied = 1
#
#         mass_output_load = mass_input_load / self.my_simulation_parameters.seconds_per_timestep  # kg/timestep --> kg/s
#         self.test_new_temperature = temperature_new
#
#         stsv.set_output_value(self.mass_output, mass_output_load)
#         stsv.set_output_value(self.temperature_output, temperature_new)
#         stsv.set_output_value(self.demand_satisfied, demand_satisfied)
#
#     def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
#         # all values the can be checked, can be tested and checked if this code
#         # raise ValueError
#         # ToDo: Get the output temperature and check if high enough --> is there another way to get it??
#         if stsv.get_input_value(self.heat_demand) > 0:
#             if self.test_new_temperature < LoadConfig.temperature_returnflow_minimum:
#                 log.error("The backfolwing temperature from the load is too small. The chosen system can't provide the requirements.")
#                 log.error("T_back = " + str(self.test_new_temperature))
#                 raise ValueError
#
# class ElectricityDistributor(Component):
#     """
#     Electricity Distributor component hardcoded to work with
#     photovoltaic system, CHP, electrolyzer and household appliances demand.
#
#
#     Parameters
#     ----------
#     component_name : str
#         Component name
#     """
#     PowerPV = "PowerPV"
#     PowerCHP = "PowerCHP"
#     DemandHousehold = "DemandHousehold"
#
#     PowerToElectrolyzer = "PowerToElectrolyzer"
#     PowerToFromGrid = "PowerToFromGrid"
#
#     def __init__(self, component_name:str, my_simulation_parameters: SimulationParameters ):
#         super().__init__(name=component_name, my_simulation_parameters=my_simulation_parameters)
#         # input
#         self.power_PV: ComponentInput = self.add_input(self.component_name, ElectricityDistributor.PowerPV, lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
#         self.power_CHP: ComponentInput = self.add_input(self.component_name, ElectricityDistributor.PowerCHP, lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
#         self.demand_household: ComponentInput = self.add_input(self.component_name, ElectricityDistributor.DemandHousehold, lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
#
#         # output
#         self.power_to_electrolyzer: ComponentOutput = self.add_output(self.component_name, ElectricityDistributor.PowerToElectrolyzer, lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
#         self.power_from_to_grid: ComponentOutput = self.add_output(self.component_name, ElectricityDistributor.PowerToFromGrid, lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
#
#
#     def i_save_state(self):
#         pass
#
#     def i_restore_state(self):
#         pass
#
#     def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
#         """
#         Distributor to control the electricity flow in the system.
#         Energy is generated by PV and CHP
#         Energy goes to the Electrolyzer if its in its range, otherwise into the grid.
#
#         Only one unit (W) so no influence of the timestep
#         """
#         power_pv = stsv.get_input_value(self.power_PV)
#         power_chp = stsv.get_input_value(self.power_CHP)
#         demand_household = stsv.get_input_value(self.demand_household)
#
#         power_supply = power_pv + power_chp
#         power_after_own_consumtion = power_supply - demand_household
#         # new calculation in each timestep
#         power_from_to_grid = 0
#         power_to_electrolyzer = 0
#
#         if power_after_own_consumtion < 0:
#             # adding a negative value gives a negative result --> electricity from grid
#             power_from_to_grid += power_after_own_consumtion
#         elif power_after_own_consumtion < AdvElectrolyzerConfig.min_power:
#             # Electrolyzer cant operate at this low power
#             power_from_to_grid = power_after_own_consumtion
#         elif AdvElectrolyzerConfig.min_power <= power_after_own_consumtion <= AdvElectrolyzerConfig.max_power:
#             # power in range of Electrolyzer so all the power goes into it
#             power_to_electrolyzer = power_after_own_consumtion
#
#         elif power_after_own_consumtion > AdvElectrolyzerConfig.max_power:
#             # Power goes to Electrolyzer and to grid
#             power_to_electrolyzer = AdvElectrolyzerConfig.max_power
#             power_after_own_consumtion -= AdvElectrolyzerConfig.max_power
#             power_from_to_grid = power_after_own_consumtion
#
#         stsv.set_output_value(self.power_to_electrolyzer, power_to_electrolyzer)
#         stsv.set_output_value(self.power_from_to_grid, power_from_to_grid)
#
#     def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
#         # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
#         pass
#
# class HouseholdWarmWaterDemandWatt(Component):
#     """
#     Component class the checks if space head and warm water
#     demand is covered.
#
#     Parameters
#     ----------
#     componant_name: str
#         Component name
#     seconds_per_timestep : int
#         Number of seconds per simulation time step
#     """
#     # input
#     WW_EnergyDemand = "Warm Water Volume Demand"            # W
#     WW_MassInput = "Warm Water Mass Input"                  # kg/s
#     WW_TemperatureInput = "Warm Water Temperature Input"    # °C
#
#     # output
#     WW_MassOutput = "Mass Output"                           # kg/s
#     WW_TemperatureOutput = "Temperature Output"             # °C
#     EnergyDischarged = "Energy Discharged"                          # W
#     DemandSatisfied = "Demand Satisfied"                    # 0 or 1
#
#     def __init__(self, component_name:str, my_simulation_parameters: SimulationParameters):
#         super().__init__(component_name, my_simulation_parameters)
#         # input
#         self.ww_energy_demand: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemandWatt.WW_EnergyDemand, lt.LoadTypes.WARM_WATER, lt.Units.WATT, True)
#         self.ww_mass_input: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemandWatt.WW_MassInput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True)
#         self.ww_temperature_input: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemandWatt.WW_TemperatureInput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS, True)
#
#         # output
#         self.ww_mass_output: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemandWatt.WW_MassOutput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
#         self.ww_temperature_output: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemandWatt.WW_TemperatureOutput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS)
#
#         self.energy_discharged: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemandWatt.EnergyDischarged, lt.LoadTypes.WARM_WATER, lt.Units.WATT)
#         self.demand_satisfied: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemandWatt.DemandSatisfied, lt.LoadTypes.WARM_WATER, lt.Units.ANY)
#
#
#     def i_save_state(self):
#         pass
#
#     def i_restore_state(self):
#         pass
#
#     def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
#         if force_convergence:
#             return
#         """
#         The warmwater demand is given in Watt by the VDI 4655 profiles.
#         The temperature levels are given by the conig file.
#         The input mass from the warmwater storage is given in kg/s and is converted to kg/timestep.
#         """
#         # ww demand
#         ww_energy_demand_watt = stsv.get_input_value(self.ww_energy_demand)         # W
#         ww_energy_demand = ww_energy_demand_watt * self.my_simulation_parameters.seconds_per_timestep        # J
#         ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand
#         # from wws
#         ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input)            # kg/s
#         ww_mass_input = ww_mass_input_per_sec * self.my_simulation_parameters.seconds_per_timestep           # kg
#         ww_temperature_input = stsv.get_input_value(self.ww_temperature_input)      # °C
#
#         freshwater_temperature = HouseholdWarmWaterDemandConfig.freshwater_temperature
#         temperature_difference_hot = HouseholdWarmWaterDemandConfig.temperature_difference_hot      # Grädigkeit
#         temperature_difference_cold = HouseholdWarmWaterDemandConfig.temperature_difference_cold
#         energy_losses_watt = HouseholdWarmWaterDemandConfig.heat_exchanger_losses
#         energy_losses = energy_losses_watt * self.my_simulation_parameters.seconds_per_timestep
#
#         if ww_temperature_input > (ww_temperature_demand + temperature_difference_hot) or ww_energy_demand == 0:
#             demand_satisfied = 1
#         else:
#             demand_satisfied = 0
#
#         if ww_energy_demand > 0 and (ww_mass_input == 0 and ww_temperature_input == 0):
#             """first iteration --> random numbers"""
#             ww_temperature_input = 40.45
#             ww_mass_input = 9.3
#
#         """
#         Warm water is provided by the warmwater stoage.
#         The household needs water at a certain temperature. To get the correct temperature the amount of water from
#         the wws is regulated and is depending on the temperature provided by the wws. The backflowing water to wws
#         is cooled down to the temperature of (freshwater+temperature_difference_cold) --> ww_temperature_output.
#         """
#         if ww_energy_demand > 0:
#             # heating up the freshwater. The mass is consistent
#             energy_discharged = ww_energy_demand + energy_losses
#             ww_temperature_output = freshwater_temperature + temperature_difference_cold
#             ww_mass_input = energy_discharged / (PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin * (ww_temperature_input - ww_temperature_output))
#         else:
#             ww_temperature_output = ww_temperature_input
#             ww_mass_input = 0
#             energy_discharged = 0
#
#         ww_mass_output = ww_mass_input / self.my_simulation_parameters.seconds_per_timestep  # kg/timestep --> kg/s
#
#         stsv.set_output_value(self.ww_mass_output, ww_mass_output)
#         stsv.set_output_value(self.ww_temperature_output, ww_temperature_output)
#         stsv.set_output_value(self.demand_satisfied, demand_satisfied)
#         stsv.set_output_value(self.energy_discharged, energy_discharged)
#
#     def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
#         # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
#         pass
#
# class HouseholdWarmWaterDemand(Component):
#     """
#     Component class the checks if space head and warm water
#     demand is covered.
#
#     Parameters
#     ----------
#     componant_name: str
#         Component name
#     seconds_per_timestep : int
#         Number of seconds per simulation time step
#     """
#
#     # input
#     # input
#     WW_VolumeDemand = "Warm Water Volume Demand"            # Liter/timestep
#     # WW_TemperatureDemand = "Warm Water Temperature Demand"  # °C --> config
#     WW_MassInput = "Warm Water Mass Input"                  # kg/s
#     WW_TemperatureInput = "Warm Water Temperature Input"    # °C
#
#     # output
#     WW_MassOutput = "Mass Output"                           # kg/s
#     WW_TemperatureOutput = "Temperature Output"             # °C
#     # WW_MassSupply = "Mass Supply"                         # kg/s
#     # WW_TemperatureSupply = "Temperature Supply"           # °C
#     EnergyDemand = "Energy Demand"                          # kW
#     DemandSatisfied = "Demand Satisfied"                    # 0 or 1
#
#     def __init__(self, component_name:str,my_simulation_parameters: SimulationParameters ):
#         super().__init__(name=component_name,my_simulation_parameters=my_simulation_parameters )
#         # input
#         self.ww_volume_demand: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemand.WW_VolumeDemand, lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP, True)
#         # self.ww_temperature_demand: ComponentInput = self.add_input(self.ComponentName, HouseholdWarmWaterDemand.WW_TemperatureDemand, LoadTypes.WarmWater, lt.Units.Celsius, True)
#         self.ww_mass_input: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemand.WW_MassInput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True)
#         self.ww_temperature_input: ComponentInput = self.add_input(self.component_name, HouseholdWarmWaterDemand.WW_TemperatureInput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS, True)
#
#         # output
#         self.ww_mass_output: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemand.WW_MassOutput, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
#         self.ww_temperature_output: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemand.WW_TemperatureOutput, lt.LoadTypes.WARM_WATER, lt.Units.CELSIUS)
#
#         # these are exactly the demand values  --> kick out?!
#         # self.ww_mass_supply: ComponentOutput = self.add_output(self.ComponentName, HouseholdWarmWaterDemand.WW_MassSupply, LoadTypes.WarmWater, lt.Units.kg_per_sec)
#         # self.ww_temperature_supply: ComponentOutput = self.add_output(self.ComponentName, HouseholdWarmWaterDemand.WW_TemperatureSupply, LoadTypes.WarmWater, lt.Units.Celsius)
#
#         self.energy_demand: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemand.EnergyDemand, lt.LoadTypes.WARM_WATER, lt.Units.WATT)
#         self.demand_satisfied: ComponentOutput = self.add_output(self.component_name, HouseholdWarmWaterDemand.DemandSatisfied, lt.LoadTypes.WARM_WATER, lt.Units.ANY)
#
#     def i_save_state(self):
#         pass
#
#     def i_restore_state(self):
#         pass
#
#     def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
#         if force_convergence:
#             return
#         """
#         The warmwater demand is given in liter/timestep by LPG.
#         The warmwater temperature is constant.
#         The input mass from the warmwater stoage is given in kg/s and is transfered to kg/timestep, which can be treated
#         like kg because only one timstep is considered at the same time.
#         """
#         ww_volume_demand = stsv.get_input_value(self.ww_volume_demand)              # liter/timestep
#         ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand
#         # ww_temperature_demand = stsv.get_input_value(self.ww_temperature_demand)    # °C            # could also be config value?!
#         # from wws
#         ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input)            # kg/s
#         ww_mass_input = ww_mass_input_per_sec * self.my_simulation_parameters.seconds_per_timestep           # kg
#         ww_temperature_input = stsv.get_input_value(self.ww_temperature_input)      # °C
#
#         freshwater_temperature = HouseholdWarmWaterDemandConfig.freshwater_temperature
#         temperature_difference_hot = HouseholdWarmWaterDemandConfig.temperature_difference_hot      # Grädigkeit
#         temperature_difference_cold = HouseholdWarmWaterDemandConfig.temperature_difference_cold
#         ww_mass_demand = ww_volume_demand / 1000 * PhysicsConfig.water_density            # kg/timestep
#         energy_losses_watt = 0                                          # [W]
#         energy_losses = energy_losses_watt * self.my_simulation_parameters.seconds_per_timestep  # [J]
#
#         if ww_temperature_input > ww_temperature_demand + temperature_difference_hot or ww_volume_demand == 0:
#             demand_satisfied = 1
#             # log.error("Can satisfy warmwater demand")
#             # log.error(ww_temperature_input)
#         else:
#             demand_satisfied = 0
#             # log.error("Can't satisfy warmwater demand")
#             # log.error(ww_temperature_input)
#
#
#         if ww_volume_demand > 0 and (ww_mass_input == 0 and ww_temperature_input == 0):
#             """first iteration --> random numbers"""
#             ww_temperature_input = 40.45
#             ww_mass_input = 89.3
#
#         """
#         Warm water is provided by the warmwater stoage.
#         The houshold needs water at a certain temperature. To get the correct temperature the amount of water from
#         the wws is regulated and is depending on the temperature provided by the wws. The backflowing water to wws
#         is cooled down to the temperature of (freshwater+temperature_difference_cold).
#
#         """
#         if ww_volume_demand > 0:
#             # heating up the freshwater. The mass is consistent
#             energy_demand = (ww_mass_demand * ww_temperature_demand - ww_mass_demand * freshwater_temperature) * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
#             energy_discharged = energy_demand + energy_losses   # Joule
#             ww_temperature_output = freshwater_temperature + temperature_difference_cold
#             ww_mass_input = energy_discharged / (PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin * (ww_temperature_input - ww_temperature_output))
#         else:
#             energy_demand = 0
#             ww_temperature_output = ww_temperature_input
#             ww_mass_input = 0
#
#         ww_mass_output = ww_mass_input / self.my_simulation_parameters.seconds_per_timestep  # kg/timestep --> kg/s
#
#         stsv.set_output_value(self.ww_mass_output, ww_mass_output)
#         stsv.set_output_value(self.ww_temperature_output, ww_temperature_output)
#         stsv.set_output_value(self.energy_demand, energy_demand)
#         stsv.set_output_value(self.demand_satisfied, demand_satisfied)
#
#     def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
#         # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
#         pass
