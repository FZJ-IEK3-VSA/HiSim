# # type: ignore
#
# import math
# import unittest
# import copy
#
#
# class WWSConfig:
#     # ToDo: Ein bessere Konfiguration der Paameter in den 'arbeitenden' Klassen kan über eine config-Klasses gegeben werden
#     #   -> Hierzu informieren und die umsetzung erarbeiten!
#     #   Ansatz siehe im folgenen docstring
#     """
#     ### for better configuration
#     class WarmWaterStorage_config:
#         tank_diameter = 4
#         tank_height = 30
#         tank_start_temperature: float = 20"""
#
#
# """class WarmWaterStorage_config:
#     tank_diameter = 0.3
#     tank_height = 30
#     tank_start_temperature: float = 60
#
# class WarmWaterStorage:
#
#
#      # Layer 0 ist die oberste Schicht
#     #def __init__(self, tank_diameter: float, tank_height: float, tank_start_temperature: float = 20):
#     def __init__(self, config:WarmWaterStorage_config):
#         # Initialises a tarting tank and adds one slice with the tanks height and the starting temperature
#         self.diameter = config.tank_diameter
#         self.height_storage = tank_height
#         self.area = (math.pi / 4) * (self.diameter ** 2)
#         self.volume = self.area * self.height_storage
#         self.my_slices = []
#         self.my_slices.append(WaterSlice(self.diameter, self.height_storage, tank_start_temperature))
#         self.check_units_wws()
#
# tank_diameter = 4
# tank_height = 30
# tank_start_temperature: float = 20"""
#
#
# class ObsStratifiedWarmWaterStorage:
#     # Layer 0 is the upmost layer
#     def __init__(self, tank_diameter: float, tank_height: float, tank_start_temperature: float = 20):
#         """ Initialises a tarting tank and adds one slice with the tanks height and the starting temperature"""
#         self.diameter = tank_diameter
#         self.start_temperature = tank_start_temperature
#         self.height_storage = tank_height
#
#         self.check_units_wws()      # Check the input values before using them
#
#         self.area = (math.pi / 4) * (self.diameter ** 2)
#         self.volume = self.area * self.height_storage
#         self.my_slices = []
#         self.my_slices.append(ObsWaterSlice(self.diameter, self.height_storage, self.start_temperature))
#
#     def check_units_wws(self):
#         for value in (self.diameter, self.height_storage, self.start_temperature):
#             if not isinstance(value, (int, float)):
#                 raise TypeError
#             if isinstance(value, bool):
#                 # Otherwise bool results into 0 or 1
#                 raise TypeError
#             if value <= 0:
#                 # The tank must always have positive values. Otherwise it wouldn't be a tank
#                 raise ValueError
#         if self.start_temperature >= 100:
#             raise ValueError  # -> Boiling Water
#
#     def begin_new_timestep(self):
#         """Deep copy of my_slices-> damit nicht rückwirkend übeschrieben wird
#         # whatfor is copy._deepcopy_list() useful?"""
#         save_values_step_1 = copy.deepcopy(self.my_slices)
#         return save_values_step_1
#
#     def reset_to_last_time_step(self, save_values_step_1):
#         """
#         Get back the step before
#         Use together with def begin_new_timestep
#         """
#         self.my_slices = copy.deepcopy(save_values_step_1)
#         return
#
#     def calculate_chp_output_temperatur(self, massflow_chp, power_chp_kw, timestep):
#         """
#         Given: power production & massflow
#         -> calculate: temperature which is flowing to the tank
#         - collect slice
#         - heat up slice
#         - push back to the tank
#
#         """
#         massflow_remaining = massflow_chp
#         a = -1
#         enthalpy_collected:float = 0
#         while massflow_remaining > 0:
#             lowest_slice = self.my_slices[a]
#             a -= 1
#
#             if massflow_remaining > lowest_slice.mass:
#                 enthalpy_collected += lowest_slice.enthalpy
#                 massflow_remaining -= lowest_slice.mass
#             else:
#                 percentage = massflow_remaining / lowest_slice.mass
#                 enthalpy_collected += percentage * lowest_slice.enthalpy
#                 # Rounding error
#                 massflow_remaining = 0
#
#         temperature_chp_hot = (enthalpy_collected + power_chp_kw * 1000 * timestep) / (massflow_chp * 4180)
#         return temperature_chp_hot
#
#     def calculate_chp_massflow(self, temperature_chp_hot, power_chp_kw, timestep):
#         """
#         With a given power provided by the chp and a fixed temperature leaving it.
#         The varying parameter is the mass flowing through the chp.
#         It can be determined by using the enthalpy provided by the chp and calculate how much water from the bottom
#         of the tank can be heated to the desired temperature. This amount of water is the massflow_chp
#         """
#         power_input_remaining:float = power_chp_kw * 1000 * timestep
#         massflow_chp:float = 0
#         # a: vaiable to go through the slices backwards
#         a = -1
#         while power_input_remaining > 0:
#             lowest_slice = self.my_slices[a]
#             a -= 1
#             enthalpy_needed = lowest_slice.mass * lowest_slice.specific_heat_capacity * temperature_chp_hot - lowest_slice.enthalpy
#
#             if power_input_remaining > enthalpy_needed:
#                 power_input_remaining -= enthalpy_needed
#                 massflow_chp += lowest_slice.mass
#             else:
#                 percentage_chp = power_input_remaining / enthalpy_needed
#                 # ToDo : Rounding Error, which leads to another run of the while function
#                 power_input_remaining = 0
#                 # power_input_remaining -= enthalpy_needed * percentage
#                 massflow_chp += lowest_slice.mass * percentage_chp
#         return massflow_chp
#
#     def calculate_load_massflow(self, temperature_load_cold, power_load_kw, timestep):
#
#         power_consumption_remaining:float = power_load_kw * 1000 * timestep
#         massflow_load:float = 0
#         r = 0
#         while power_consumption_remaining > 0:
#             highest_slice = self.my_slices[r]
#             r += 1
#             enthalpy_to_get_from_slice = highest_slice.enthalpy - highest_slice.mass * highest_slice.specific_heat_capacity * temperature_load_cold
#
#             if power_consumption_remaining > enthalpy_to_get_from_slice:
#                 power_consumption_remaining -= enthalpy_to_get_from_slice
#                 massflow_load += highest_slice.mass
#             else:
#                 percentage_load = power_consumption_remaining / enthalpy_to_get_from_slice
#                 massflow_load = highest_slice.mass * percentage_load
#                 power_consumption_remaining = 0
#
#         return massflow_load
#
#     def create_water_slice(self, slice_temperature_input: float, slice_mass_input: float, is_from_top: bool):
#         """ Creation of one water slice and insert it into the tank. The variable is_from_top decides about where to input the slice.
#         If True then its added on top, otherwise ist added at the bottom. Actually this means: is_from_top == is_from_chp:
#         True -> From CHP // this gives a constant temperature which is the hottest in the system (heat source) -> always on top
#         False -> Fom Load // Cold water is coming back. This means that this is the coldest water in the system
#           ToDO: What happens if the system is starting again and the water in the tank is colder than the backflowing water"""
#
#         temperature_difference = 0.1
#         height = slice_mass_input / (self.area * 1000)
#         temperature = slice_temperature_input
#         ws = ObsWaterSlice(self.diameter, height, temperature)
#         if is_from_top:
#             if (ws.temperature + temperature_difference) >= self.my_slices[0].temperature >= (ws.temperature - temperature_difference):
#                 self.my_slices[0].height += ws.height
#                 self.my_slices[0].calculate_mass()
#                 self.my_slices[0].calculate_enthalpy()
#                 # Sind die Parameter so richtig erhöht worden?
#             else:
#                 self.my_slices.insert(0, ws)
#         else:
#             if self.my_slices[-1].temperature <= (ws.temperature + temperature_difference) and self.my_slices[0].temperature >= (ws.temperature - temperature_difference):
#                 self.my_slices[-1].height += ws.height
#                 self.my_slices[-1].calculate_mass()
#                 self.my_slices[-1].calculate_enthalpy()
#             else:
#                 self.my_slices.append(ws)
#         return ws
#
#     def push_slices(self, ws, is_from_top: bool):
#         """
#         If the input slice has a height of zero it is not existing and has to be deleted
#         Else, the amount of input water will be pushed ot of the tank on the other side.
#         """
#         if self.my_slices[0].height == 0:
#             self.my_slices.pop(0)
#         if self.my_slices[-1].height == 0:
#             self.my_slices.pop(-1)
#
#         if ws.height == 0:
#             pass
#         else:
#             collected_height_so_far:float = 0
#             pushed_out_slice = ObsWaterSlice(self.diameter, 0, 0)
#             while collected_height_so_far < ws.height:              # ws.height bezieht sich auf das neu eingebrachte slice
#                 height_still_needed = ws.height - collected_height_so_far
#                 if is_from_top:
#                     slice_to_push = self.my_slices[-1]              # Das slice, das als nächstes ausgeschoben wird
#                 else:  # Einschub von unten  -> Oben wird entfernt
#                     slice_to_push = self.my_slices[0]
#                 if slice_to_push.height <= height_still_needed:     # wird die ganze unterste schicht ausgeschoben?
#                     self.my_slices.remove(slice_to_push)            # -> wenn ja, wird diese komplett entfernt; separate Massen-entfernung nicht notwendig
#                     pushed_out_slice.add_another_slice(slice_to_push)   # das gesamtslice welches ausgegeben wird wird berechnet
#                     collected_height_so_far += slice_to_push.height
#                 else:   # Die unterste Schicht muss nicht komplett entfernt werden, sonder der Differenzbetrag muss von der letzten schicht abgezogen werden
#                     # Des Auszugebenden slice wird der notwendige Anteil es untersten slices übergeben
#                     pushed_out_slice.add_another_slice(ObsWaterSlice(self.diameter, height_still_needed, slice_to_push.temperature))
#                     # Unterstes slice wird verkürzt, sowohl höhe als auch masse & Enthalpie
#                     slice_to_push.height -= height_still_needed
#                     # Wenn andernorts mit der Masse/Enthalpie gearbeitet wird, sollte diese auch entfernt werden
#                     # Masse kann auch in temporäre Variable gepack werden
#                     slice_to_push.mass -= (height_still_needed * (math.pi / 4) * (self.diameter ** 2) * pushed_out_slice.density)
#                     slice_to_push.enthalpy -= (height_still_needed * (math.pi / 4) * (self.diameter ** 2) * pushed_out_slice.density) * slice_to_push.specific_heat_capacity * slice_to_push.temperature
#                     collected_height_so_far += height_still_needed  # -> collected_... sollte 0 ergeben
#                     # TODO: Entstehen hier evtl Rundungsfehler? -> Test ob die Höhe noch stimmt?
#             return pushed_out_slice
#
#     def calculate_tanks_enthalpy(self):
#         """ To get the enthalpy of all slices combined
#             No changes of values"""
#         enthalpy_tank:float = 0
#         for i in range(len(self.my_slices)):
#             enthalpy_tank += self.my_slices[i].enthalpy
#         return enthalpy_tank
#
#     def calculate_tanks_mean_temperature(self):
#         """ Get the average temperature of tank
#             For each slice:
#             temperature(i) * mass(i)  / total_mass """
#         total_energy:float = 0
#         average_density:float = 1000  # kg/m^3
#         for i in range(len(self.my_slices)):
#             total_energy += self.my_slices[i].mass * self.my_slices[i].temperature
#         average_temperature = total_energy / (self.volume * average_density)
#         return average_temperature
#
#     def energy_losses_in_one_timestep(self, u_value_tank, timestep: int = 1, ambient_temperature: float = 20):
#         """ Energy losses for all the slices in the tank
#             the called function 'heat_losses_to_ambient' & 'heat_losses_top_bottom' sets a new enthalpy and temperature for slices"""
#         losses_this_timestep = 0
#         """ Losses at top and bottom"""
#         losses_this_timestep += self.my_slices[0].heat_losses_top_bottom(u_value_tank, timestep, ambient_temperature)
#         losses_this_timestep += self.my_slices[-1].heat_losses_top_bottom(u_value_tank, timestep, ambient_temperature)
#
#         """ Losses to the sides """
#         for i in range(len(self.my_slices)):
#             losses_this_timestep += self.my_slices[i].heat_losses_to_ambient(u_value_tank, timestep, ambient_temperature)
#         return losses_this_timestep
#
#     def energy_exchange_between_slices(self, lambda_water_water: float = 0.6, timestep: int = 1):
#         """
#         Cant be used in this way... (see notes 28.10.2020)
#         u_value Water-Water = ~0.06 ??
#         Upper slice warmer:
#             energy is transfered to the lower slice
#         Upper slice colder:
#             energy is transfered to the upper slice
#         """
#         for i in range(len(self.my_slices)-1):
#             if self.my_slices[i].temperature > self.my_slices[i+1].temperature:
#                 temperature_delta = self.my_slices[i].temperature - self.my_slices[i+1].temperature
#                 energy_transfered = self.area * lambda_water_water * temperature_delta * timestep
#                 self.my_slices[i].enthalpy -= energy_transfered
#                 self.my_slices[i+1].enthalpy += energy_transfered
#                 self.my_slices[i].temperature = self.my_slices[i].calculate_temperature()
#                 self.my_slices[i+1].temperature = self.my_slices[i+1].calculate_temperature()
#             else:
#                 temperature_delta = self.my_slices[i].temperature - self.my_slices[i + 1].temperature
#                 energy_transfered = self.area * lambda_water_water * temperature_delta * timestep
#                 self.my_slices[i].enthalpy -= energy_transfered
#                 self.my_slices[i + 1].enthalpy += energy_transfered
#                 self.my_slices[i].temperature = self.my_slices[i].calculate_temperature()
#                 self.my_slices[i + 1].temperature = self.my_slices[i + 1].calculate_temperature()
#
#
# class ObsWaterSlice:
#
#     def check_units(self):
#         for value in (self.diameter, self.height, self.temperature):
#             if not isinstance(value, (int, float)):
#                 raise TypeError
#             if isinstance(value, bool):   # Otherwise bool results into 0 or 1
#                 raise TypeError
#         if self.diameter <= 0 or self.temperature < 0 or self.height < 0:
#             """
#             Zero for the height & temperature is accepted because this makes it possible to create an "empty" slice
#             Diameter must always be positive
#             'Empty' slice is deleted right after the creation
#             """
#             raise ValueError
#
#         if self.temperature >= 100:
#             raise ValueError    # -> Boiling Water
#
#     def __init__(self, tank_diameter: float, height: float, temperature: float):
#         self.diameter:float = tank_diameter
#         self.height:float = height
#         self.temperature:float = temperature
#         self.density:float = 1000     # kg/(m^3)
#         self.specific_heat_capacity:float = 4180  # J/kg*K
#         self.mass:float = self.height * (math.pi / 4) * (self.diameter ** 2) * self.density
#         # Enthalpy referencing to the temperature of 0 °C.
#         # kg * J/kg*K * K = J = Ws
#         self.enthalpy:float = self.mass * self.specific_heat_capacity * self.temperature
#         self.check_units()
#
#     def calculate_mass(self):
#         self.mass = self.height * (math.pi / 4) * (self.diameter ** 2) * self.density
#         # durch das self. kann man das return [return mass] weglassen (richtig?!)
#         return self.mass
#
#     def calculate_enthalpy(self):
#         self.enthalpy = self.mass * self.specific_heat_capacity * self.temperature
#         return self.enthalpy
#
#     def calculate_temperature(self):
#         self.temperature = self.enthalpy / (self.mass * self.specific_heat_capacity)
#         return self.temperature
#
#     def add_another_slice(self, other_slice):  # this should be -> other_slice: WaterSlice -> but got an error
#         new_height = self.height + other_slice.height
#         new_mass = self.mass + other_slice.mass
#         new_temperature = (self.mass * self.temperature + other_slice.calculate_mass() * other_slice.temperature) / (self.mass + other_slice.calculate_mass())
#         self.height = new_height
#         self.mass = new_mass
#         self.temperature = new_temperature
#         # The new enthalpy is based in the new parameters
#         self.enthalpy = self.mass * self.specific_heat_capacity * self.temperature
#
#     def heat_losses_to_ambient(self, u_value_tank, stepsize: float = 1, ambient_temperature: float = 20) -> float:
#         """
#         All slices in the tank must be considered
#             ->  this function is callable by the WWS class to apply it on all the slices in the tank
#         TODo: When will the loss take part? Before or after the slice is coming in?
#             -> probably not too relevant for long term investigations
#             -> !! Losses on the top and bottom!!
#         The self is regarding to the slice itself!
#         """
#         perimeter_tank = math.pi * self.diameter
#         loss_area = perimeter_tank * self.height
#         # W/m^2*K * m^2 * K * s = Ws
#         energy_losses = u_value_tank * loss_area * (self.temperature - ambient_temperature) * stepsize
#         self.enthalpy -= energy_losses
#         # An enthalpy loss results in an temperature loss
#         self.temperature = self.enthalpy / (self.mass * self.specific_heat_capacity)
#         return energy_losses    # one slice is loosing this amount
#
#     def heat_losses_top_bottom(self, u_value_tank, stepsize: float = 1, ambient_temperature: float = 20) -> float:
#         """
#         Heat losses through top OR bottom of the tank. Function has to be called twice (one each)
#         """
#         area_tank = (math.pi / 4) * self.diameter**2
#         energy_losses = u_value_tank * area_tank * (self.temperature - ambient_temperature) * stepsize
#         self.enthalpy -= energy_losses
#         self.temperature = self.enthalpy / (self.mass * self.specific_heat_capacity)
#         return energy_losses
#
#
# if __name__ == '__main__':
#     unittest.main()
#
#     """
#     Calculation of System setups is can be found in the file 'Run_system_setups'
#     """
