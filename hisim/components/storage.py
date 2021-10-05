# Generic/Built-in
import copy
import json
from math import pi

# Owned
import globals
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt

from globals import load_smart_appliance
from components.extended_storage import WaterSlice

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class StorageSubstance:

    def __init__(self,
                 specific_heat,
                 boiling_temperature,
                 freezing_temperature,
                 density):

        self.specific_heat = specific_heat
        self.boiling_temperature = boiling_temperature
        self.freezing_temperature = freezing_temperature
        self.density = density

class Water(StorageSubstance):

    def __init__(self):
        super().__init__(specific_heat=4180/3600,
                         boiling_temperature=95,
                         freezing_temperature=0,
                         density=1)

class ThermalEnergyStorageState:

    def __init__(self,
                 initial_temperature=70,
                 volume=100,
                 substance=Water()):
        self.mass = volume * substance.density
        self.thermal_capacity = self.mass * substance.specific_heat
        self.t = initial_temperature

    def update_tes_t(self, heat_loss, energy_demand, energy_heater_t_plus):
        energy_exchange = heat_loss + energy_demand + energy_heater_t_plus    # Wh
        t_final = energy_exchange / self.thermal_capacity + self.t     # C
        self.energy_exchange = energy_exchange
        self.t = t_final
        return t_final

class ThermalEnergyStorage(Component):
    # Inputs
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    WaterConsumption="WaterConsumption"
    TemperatureAir = "TemperatureAir"

    # Outputs
    HeatLoss="HeatLoss"
    EnergyExchange="EnergyExchange"
    ThermalEnergyStorageTemperature="ThermalEnergyStorageTemperature"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump or ...
    # 3. Occupancy

    def __init__(self,
                 manufacturer="Viessman",
                 model="Vitocell 300-B, 500L, EVBA-A",
                 material=Water(),
                 initial_temperature=70,
                 base=False,
                 tapwater_temperature=10,
                 warmwater_temperature=45,
                 ):
        super().__init__(name="TES")

        self.warmwater_temperature = warmwater_temperature
        self.tapwater_temperature = tapwater_temperature
        self.base = base

        self.build(manufacturer=manufacturer,model=model,material=material)

        self.state = ThermalEnergyStorageState(initial_temperature=initial_temperature,
                                               volume=self.volume,
                                               substance=self.storage_substance)
        self.previous_state = copy.copy(self.state)

        self.thermal_energy_deliveredC: ComponentInput = self.add_input(self.ComponentName,
                                                       self.ThermalEnergyDelivered,
                                                       lt.LoadTypes.Any,
                                                       lt.Units.Any,
                                                       True)

        self.water_loadC: ComponentInput = self.add_input(self.ComponentName,
                                                              self.WaterConsumption,
                                                              lt.LoadTypes.Volume,
                                                              lt.Units.Liter,
                                                              True)
        self.t_airC: ComponentInput = self.add_input(self.ComponentName,
                                                     self.TemperatureAir,
                                                     lt.LoadTypes.Temperature,
                                                     lt.Units.Degrees,
                                                     True)

        self.heat_lossC: ComponentOutput = self.add_output(self.ComponentName,
                                                           self.HeatLoss,
                                                           lt.LoadTypes.Heating,
                                                           lt.Units.W)
        self.energy_exchangeC: ComponentOutput = self.add_output(self.ComponentName,
                                                                 self.EnergyExchange,
                                                                 lt.LoadTypes.Electricity,
                                                                 lt.Units.W)
        self.tes_tC: ComponentOutput = self.add_output(self.ComponentName,
                                                   self.ThermalEnergyStorageTemperature,
                                                   lt.LoadTypes.Temperature,
                                                   lt.Units.Celsius)

    def build(self, manufacturer, model, material):
        self.manufacturer = manufacturer
        self.model = model
        self.volume = 0

        # Imports database
        database = globals.load_smart_appliance("Thermal Energy Storage")
        # Finds the requested TES
        for tes in database:
            if tes["Manufacturer"] == manufacturer and tes["Model"] == model:
                self.volume = tes["Volume"]
                self.standby_heat_consumption = (tes["Standby Heat Consumption"] * 1E3 ) / 24
                self.h_A = self.standby_heat_consumption / 45    # 45 Degrees temperature difference
                self.price = tes["Price"]
                break

        # Checks if TES was loaded correctly
        if self.volume == 0:
            raise Exception("TES model has not been found.")

        # Saves properties
        self.maximum_temperature = material.boiling_temperature
        self.minimum_temperature = material.freezing_temperature
        self.total_mass = material.density * self.volume
        self.capacitance = self.total_mass * material.density / 3600    # Wk / K
        self.storage_substance = material

    def __str__(self):
        properties = "Manufacturer: {}\n".format(self.manufacturer)
        properties += "Model: {}\n".format(self.model)
        properties += "Volume: {}\n".format(self.volume)
        return properties

    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        heating_provided = stsv.get_input_value(self.thermal_energy_deliveredC)
        warm_water_mass_demand = stsv.get_input_value(self.water_loadC)
        if self.base:
            t_air = stsv.get_input_value(self.t_airC)
        else:
            t_air = 20

        warm_water_heating_demand = - self.storage_substance.specific_heat * \
                                           warm_water_mass_demand * \
                                           (self.warmwater_temperature - self.tapwater_temperature)
        # Heat loss from TES to air
        tes_heat_loss = - self.h_A * (self.state.t - t_air)


        self.state.update_tes_t(heat_loss=tes_heat_loss,                               # Heat loss due the difference between TES and environment
                                energy_demand=warm_water_heating_demand,               # Heating demand required to heat up the wall
                                energy_heater_t_plus=heating_provided)            # Heating energy provided by the heater

        stsv.set_output_value(self.heat_lossC, tes_heat_loss)
        stsv.set_output_value(self.energy_exchangeC, self.state.energy_exchange)
        stsv.set_output_value(self.tes_tC, self.state.t)

class ThermalEnergyStorageController(Component):
    WarmWaterConsumption = "WarmWaterConsumption"
    ElectricityConsumption = "ElectricityConsumption"
    HeatLoss="HeatLoss"
    TemperatureOutput = "TemperatureOutput"
    EnergyExchange = "EnergyExchange"
    ThermalEnergyStorageTemperature = "ThermalEnergyStorageTemperature"

    def __init__(self,
                 name="TESController",
                 initial_storage_temperature=30,
                 tapwater_temperature=10,
                 warmwater_temperature=45,
                 #tes=ThermalEnergyStorage(),
                 storage_substance=Water(),
                 t_air=25,
                 time=None,
                 mode=1):
        tes = 0

        # Modes
        # Mode [1]: Constant heat pump power uninterrupted
        # Mode [2]: Constant heat pump power with interruption when the tes temperature is over 95
        # Mode [3]: heat pump working from 6 a.m. to 10 p.m.
        # Mode [4]: heat pump working from 6 a.m. to 10 p.m. with interruption when the tes temperature is over 95
        super().__init__(name)
        heater = 0

        # Initialize components
        self.storage_substance = storage_substance
        #self.heater = heater
        self.tes = tes

        # Corrects timesteps
        time_cor_factor = 1 / 60
        #time_cor_factor = ( time.seconds_per_timestep / 3600 )
        #self.heater.profile_power = heater.max_heating_power * time_cor_factor * 0.1
        #self.heater.max_heating_power_var = heater.max_heating_power * time_cor_factor * (self.heater.seconds_to_max_heating_power / 3600 )
        self.tes.standby_heat_consumption = tes.standby_heat_consumption * time_cor_factor
        self.tes.h_A = tes.h_A * time_cor_factor
        #self.storage_substance.specific_heat = self.storage_substance.specific_heat * time_cor_factor
        self.t_air = t_air


        self.warmwater_temperature = warmwater_temperature
        self.tapwater_temperature = tapwater_temperature

        # Energy Managment Mode
        self.mode = mode
        if self.mode == 1:
            self.heater_profile_power = 0
        #if self.mode == 1 or self.mode == 2:
        #    self.heater_profile_power = [0] * time.timesteps
        #    for i in range(time.timesteps):
        #        self.heater_profile_power[i] = self.heater.profile_power
        #elif self.mode == 3 or self.mode == 4:
        #    self.heater_profile_power = [0] * time.timesteps
        #    timestep_6 = (6 * 60 * 60) / time.seconds_per_timestep
        #    timestep_22 = (22 * 60 * 60) / time.seconds_per_timestep
        #    for timestep in range(time.timesteps):
        #        timestep_res = timestep % ( (24*60*60) / time.seconds_per_timestep )
        #        if timestep_6 <= timestep_res <= timestep_22:
        #            self.heater_profile_power[timestep] = self.heater.profile_power
        #elif self.mode == 5:
        #    self.heater_profile_power = [0] * time.timesteps
        #    timestep_first = (10 * 60 * 60) / time.seconds_per_timestep
        #    timestep_last = (17 * 60 * 60) / time.seconds_per_timestep
        #    for timestep in range(time.timesteps):
        #        timestep_res = timestep % ((24*60*60) / time.seconds_per_timestep)
        #        if timestep_first <= timestep_res <= timestep_last:
        #            self.heater_profile_power[timestep] = self.heater.profile_power



        self.state = ThermalEnergyStorageState(initial_temperature=70,
                                               tes=self.tes,
                                               substance=self.storage_substance)
                                               #heater=self.heater)
        self.previous_state = copy.copy(self.state)

        # Retrieves Electricity SUM
        self.water_loadC: ComponentInput = self.add_input(self.ComponentName,
                                                          ThermalEnergyStorageController.WarmWaterConsumption,
                                                          lt.LoadTypes.Volume,
                                                          lt.Units.Liter,
                                                          True)


        self.heat_lossC: ComponentOutput = self.add_output(self.ComponentName,
                                                           ThermalEnergyStorageController.HeatLoss,
                                                           lt.LoadTypes.Heating,
                                                           lt.Units.W)
        self.energy_exchangeC: ComponentOutput = self.add_output(self.ComponentName,
                                                                      ThermalEnergyStorageController.EnergyExchange,
                                                                      lt.LoadTypes.Electricity,
                                                                      lt.Units.W)
        self.tes_tC : ComponentOutput = self.add_output(self.ComponentName,
                                                        ThermalEnergyStorageController.ThermalEnergyStorageTemperature,
                                                        lt.LoadTypes.Temperature,
                                                        lt.Units.Celsius)


    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):

        # According to norm DIN 1988, warm water should be delivered under 30 seconds

        if self.mode == 1:
                state = 1
        else:
                state = 0

        stsv.set_output_value(self.stateC, state)

        #warm_water_heating_demand = - self.storage_substance.specific_heat * \
        #                                   warm_water_mass_demand * \
        #                                   (self.warmwater_temperature - self.tapwater_temperature)
        ## Heat loss from TES to air
        #tes_heat_loss = - self.tes.h_A * (self.state.t - self.t_air)
        #heating_energy_heater = self.heater_profile_power[timestep]

        #if self.mode == 1 or self.mode == 3:
        #    pass
        #elif self.mode == 2 or self.mode == 4 or self.mode == 5:
        #    if self.state.t >= 95:
        #        energy_heater = 0


        #self.state.update_tes_t(heat_loss=tes_heat_loss,                               # Heat loss due the difference between TES and environment
        #                        energy_demand=warm_water_heating_demand,               # Heating demand required to heat up the wall
        #                        energy_heater_t_plus=heating_energy_heater)            # Heating energy provided by the heater

        #stsv.set_output_value(self.heat_lossC, tes_heat_loss)
        #stsv.set_output_value(self.energy_exchangeC, self.state.energy_exchange)
        #stsv.set_output_value(self.tes_tC, self.state.t)

class WarmWaterStorageConfig:
    tank_diameter = 1 # 0.9534        # [m]
    tank_height = 2 # 3.15              # [m]
    tank_start_temperature: float = 65  # [°C]
    temperature_difference = 0.3        # [°C]
    tank_u_value =  0 # 0.35                 # [W/m^2*K]
    slice_height_minimum = 0.05         # [m]

class WarmWaterStorageSimulation:
    """
    The most important function is simulate_one_timestep. It calls all other functions needed.

    The storage is defined by its diameter, height and temperature. Inside there are water slices represented by my_slices
    """
    def __init__(self, config: WarmWaterStorageConfig):
        # Initialises a starting tank and adds one slice with the tanks height and the starting temperature
        self.diameter = config.tank_diameter
        self.height_storage = config.tank_height
        self.start_temperature = config.tank_start_temperature

        self.check_units_wws()

        self.area = (pi / 4) * (self.diameter ** 2)
        self.volume = self.area * self.height_storage     # [m^3]
        self.my_slices = []
        self.my_slices.append(WaterSlice(self.diameter, self.height_storage, self.start_temperature))

    def check_units_wws(self):
        """
        Checking the storage values for a correct setup of the tank
        """
        for value in (self.diameter, self.height_storage, self.start_temperature):
            if not isinstance(value, (int, float)):
                raise TypeError
            if isinstance(value, bool):
                # Otherwise bool results into 0 or 1
                raise TypeError
            if value <= 0:
                # The tank must always have positive values. Otherwise it wouldn't be a tank
                raise ValueError
        if self.start_temperature >= 100:
            raise ValueError  # -> Boiling Water

    def begin_new_timestep_alternative(self):
        """ try to write an alternative to deepcopy"""
        slices_to_save = len(self.my_slices)
        save_slices_step = []
        for i in range(slices_to_save):
            save_slices_step.append(WaterSlice.init_from_another_slice(self.my_slices[i]))
            # Todo: auf restore anwenden

    def reset_to_last_timestep_alternative(self, save_values_step_1):
        """ try to write an alternative to deepcopy"""
        amount_of_saved_slices = len(save_values_step_1)
        for i in range(amount_of_saved_slices):
            self.my_slices[i] = save_values_step_1[i]

    def begin_new_timestep(self):
        """
        Deep copy of my_slices
        -> relevant for framework. This allows to reset to the previous state
        """
        save_values_step_1 = copy.deepcopy(self.my_slices)
        return save_values_step_1

    def reset_to_last_timestep(self, save_values_step_1):
        """
        Get back the step before
        Use together with def begin_new_timestep
        """
        self.my_slices = copy.deepcopy(save_values_step_1)
        return

    def create_water_slice(self, slice_temperature_input: float, slice_mass_input: float):
        """
        Creation of one water slice.
        The height has to be calculated fom the input mass.
        :param slice_temperature_input [°C]     Temperature for the new water slice
        :param slice_mass_input [kg]            Mass for the new water slice
        :return ws                              New water slice
        """

        height = slice_mass_input / (self.area * 1000)
        temperature = slice_temperature_input
        ws = WaterSlice(self.diameter, height, temperature)
        return ws

    def insert_slice(self, ws, is_from_top):
        """
        Insert the created water slice into the tank. The variable is_from_top decides about where to input the slice.
        If True then its added on top, otherwise ist added at the bottom.
        True -> From CHP / probably hot water / input on top
        False -> From Load / probably cold water is coming back // input at the bottom
        What happens with the slice?
        a) is_from_top = True
        b) is_from_top = False
        Option 1:
        a) New slice is added above the original top slice if T_new >= T_0 + 0,5°C
        b) New slice is added below the original bottom slice if T_new <= T_0 - 0,5°C
        Option 2:
        a) New slice is mixed with the top slice if T_new < T_0 + 0,5°C
        b) New slice is mixed with the bottom slice if T_new > T_0 - 0,5°C

        Checking the temperature gradient (--> T_top < T_bottom)
        Slice will be mixed with the next one until the temperature gradient is correct
        - Its enough to only check the first and second slide. The temperature gradient of the other layers should be intact due to the previous steps.
        - this is not relevant if there is only one slice -> len() > 1
        - after mixing slice a) [1] or b) [-1] will be removed

        :param ws:          water slice which should be inserted
        :param is_from_top: where the new slice should be added
        :return:            not return, changes are made with self.___
        """
        temperature_difference = WarmWaterStorageConfig.temperature_difference
        if ws.height == 0:
            # no empty slices will be inserted into the tank
            pass
        else:
            if is_from_top:
                outer_slice = self.my_slices[0]
                if ws.temperature >= (outer_slice.temperature + temperature_difference):
                    self.my_slices.insert(0, ws)
                else:
                    outer_slice.add_another_slice(ws)
                    # Check temperature profile
                    # Only the first two slices are compared. The temperature profile of the rest should be ok due to the previous timestep
                    # The while loop will check the two upper slices until the temperature profile is correct. This can tanke more loops because the secound slice is always replaced by the third after it is combined with the  first
                    while len(self.my_slices) > 1 and outer_slice.temperature < self.my_slices[1].temperature:
                        outer_slice.add_another_slice(self.my_slices[1])
                        self.my_slices.remove(self.my_slices[1])

            elif not is_from_top:
                outer_slice = self.my_slices[-1]
                if ws.temperature <= (outer_slice.temperature - temperature_difference):
                    self.my_slices.append(ws)
                else:
                    outer_slice.add_another_slice(ws)
                    # connect the bottom slices till the temperature profile is correct
                    while len(self.my_slices) > 1 and self.my_slices[-1].temperature > self.my_slices[-2].temperature:
                        self.my_slices[-2].add_another_slice(self.my_slices[-1])
                        self.my_slices.remove(self.my_slices[-1])

    # def push_slices(self, ws_height, is_from_top: bool):
    def push_slices(self, ws_height, is_from_top: bool):
        """
        While the current top or bottom slice has a height of zero it is not existing and has to be deleted.

        Initially an empty pushed_out_slice is created.
        When the system is not in operation, slices must still be created. Their size is zero and they are deleted in the next timestep.
        If the system is operating the same amount of water, that came into the tank (ws), has to be pushed out of the tank.
        Therefore the slices on the opposite side are collected and mixed with the empty slice.
        This way a new output slice is created and the collected slices are deleted.
        Its also possible to collect only a part of a slice. The rest will stay in the tank a the outer layer.

        Caution:
        slice_to_push =     the previously existing slice in the tank
        pushed_out_slice =  the new created slice which will leave the tank

        :param ws_height:   Height of the incoming and already inserted water slice. The same slice height has to be pushed out
        :param is_from_top: Where was the slice inserted --> water will be pushed on the opposite side
        :return:            The slice which is pushed out in this timestep on the opposite side of bool: is_from_top
        """
        # maybe redundant lines?
        while self.my_slices[0].height == 0:
            self.my_slices.remove(self.my_slices[0])
            raise ValueError
        while self.my_slices[-1].height == 0:
            self.my_slices.remove(self.my_slices[-1])
            raise ValueError

        pushed_out_slice = WaterSlice(self.diameter, 0, 0)
        if ws_height > 0:
            collected_height_so_far = 0
            while collected_height_so_far < ws_height:
                height_still_needed = ws_height - collected_height_so_far
                if is_from_top:
                    slice_to_push = self.my_slices[-1]  # The slice which will added to the pushed_out_slice next
                else:  # is_fom_top == False
                    slice_to_push = self.my_slices[0]
                if slice_to_push.height <= height_still_needed:         # the whole slice will be pushed
                    self.my_slices.remove(slice_to_push)                # removing whole slice
                    pushed_out_slice.add_another_slice(slice_to_push)   # add the whole slice to pushed_out_slice
                    collected_height_so_far += slice_to_push.height
                else:   # Only a part of the outermost layer has to be removed. This part will be added to pushed_out_slice and removed from the remaining slice
                    pushed_out_slice.add_another_slice(WaterSlice(self.diameter, height_still_needed, slice_to_push.temperature))
                    # Height, mass & enthalpy have to be changed in the remaining slice
                    slice_to_push.height -= height_still_needed
                    slice_to_push.mass -= (height_still_needed * self.area * pushed_out_slice.density)
                    slice_to_push.enthalpy -= (height_still_needed * self.area * pushed_out_slice.density) * slice_to_push.specific_heat_capacity * slice_to_push.temperature
                    collected_height_so_far += height_still_needed  # -> this should be zero
                    if collected_height_so_far != ws_height:
                        a = collected_height_so_far
                        b = ws_height


                    # The remaining slice (slice_to_push) must have a minimum size
                    if slice_to_push.height < WarmWaterStorageConfig.slice_height_minimum:
                        self.my_slices.remove(slice_to_push)
                        if is_from_top:
                            self.my_slices[-1].add_another_slice(slice_to_push)
                        else:
                            self.my_slices[0].add_another_slice(slice_to_push)
        return pushed_out_slice

    def check_slice_temperature_order(self):
        """
        Heat losses on top and bottom lead to a decrease of these slices.
        Especially if there are no changes in the tank (in-/output) this can lead to higher changes in the temperature of the top/bottom slice
        The temperature profile of the two highest and the two lowest slices are checked.
        The slices in between are not influenced by this effect.
        """

        # check from top
        if len(self.my_slices) >= 2:
            while self.my_slices[0].temperature <= self.my_slices[1].temperature:
                self.my_slices[0].add_another_slice(self.my_slices[1])
                self.my_slices.remove(self.my_slices[1])
                # no more mixing if there is only one slice left
                if len(self.my_slices) == 1:
                    break

        # check from bottom
        if len(self.my_slices) >= 2:
            while self.my_slices[-1].temperature >= self.my_slices[-2].temperature:
                self.my_slices[-2].add_another_slice(self.my_slices[-1])
                self.my_slices.remove(self.my_slices[-1])
                # no more mixing if there is only one slice left
                if len(self.my_slices) == 1:
                    break

    def simulate_one_timestep(self, seconds_per_timestep, slice_temperature_input_upper: float, slice_mass_input_upper: float, is_from_top_upper: bool, slice_temperature_input_bottom: float, slice_mass_input_bottom: float, is_from_top_bottom: bool):
        """
        This is the combination of the previous functions.
        One timestep will be simulated. This includes 5 steps:
        1. Creation of the two new input slices according to the input values
        2. Insert the slices into the tank. This can include the mixing of slices inside the tank.
            -> It also leads to a 'larger' height of the tank for the moment.
        3. Create the output slices and delete the corresponding slices in the tank
            --> The tank has its initial size again (tank size == sum of all slices)
        4. Heat losses for all slices in the tank (horizontal). (Vertical losses (top/bottom) could be added later on)

        :param seconds_per_timestep:            seconds per timestep
        :param slice_temperature_input_upper:   temperature of water chp --> storage
        :param slice_mass_input_upper:          mass of water chp --> storage
        :param is_from_top_upper:               True value
        :param slice_temperature_input_bottom:  temperature of water load --> storage
        :param slice_mass_input_bottom:         mass of water load --> storage
        :param is_from_top_bottom:              False value

        :return:    output_top      water slices which is pushed out at the top
                    output_bottom   water slices which is pushed out at the bottom
                    heat_losses [W] heat losses in this timestep
        """
        # in and outflow
        ws_upper = self.create_water_slice(slice_temperature_input_upper, slice_mass_input_upper)
        ws_bottom = self.create_water_slice(slice_temperature_input_bottom, slice_mass_input_bottom)

        ws_upper_height = copy.deepcopy(ws_upper.height)
        ws_bottom_height = copy.deepcopy(ws_bottom.height)

        if ws_upper.height != slice_mass_input_upper * 4 / (pi * (self.diameter ** 2) * PhysicsConfig.water_density):
            print("huhuu")
            print(ws_upper.height)
            print(slice_mass_input_upper * 4 / (pi * (self.diameter ** 2) * PhysicsConfig.water_density))
            raise ValueError

        if ws_bottom.height != slice_mass_input_bottom * 4 / (pi * (self.diameter ** 2) * PhysicsConfig.water_density):
            print("huhuu")
            print(ws_bottom.height)
            print(slice_mass_input_bottom * 4 / (pi * (self.diameter ** 2) * PhysicsConfig.water_density))
            raise ValueError

        previous_mass = self.calculate_tanks_mass()

        # insert slices
        # CHP
        self.insert_slice(ws_upper, is_from_top_upper)
        # Load
        self.insert_slice(ws_bottom, is_from_top_bottom)

        # push slices
        # to Load
        output_top = self.push_slices(ws_bottom_height, is_from_top_bottom)
        # to CHP
        output_bottom = self.push_slices(ws_upper_height, is_from_top_upper)

        mass_now = self.calculate_tanks_mass()

        if abs(mass_now - previous_mass) > 0.001:
            raise ValueError

        # heat losses to environment and energy exchange between the segments
        if len(self.my_slices) > 1:
            self.energy_exchange_between_slices(lambda_water_water=0.6, seconds_per_timestep=seconds_per_timestep)

        # calculate heat losses
        heat_losses = self.energy_losses_in_one_timestep(WarmWaterStorageConfig.tank_u_value, seconds_per_timestep)

        return output_top, output_bottom, heat_losses

    def get_temperature_level_at_specific_height(self, height_of_interest):
        """
        Get the temperature at a specific height in the tank. Height is measured from top to bottom.
        The height of the slices in the tank will be added to collected_height if larger than height_of_interest.
        The slice counter must be lower than the slices in the tank.
        The slice counter tells how many slices have been skipped to get to the right height in the tank.
        To set temperature_of_interest correctly the slice counter has to be reduced by one before the temperature is assigned.
        Except the height_of_interest is zero. Then the top slice [0] is taken.

        :param height_of_interest:          The height of the slice whose temperature is to be returned
        :return: temperature_of_interest    Temperature of interest [°C]
        """
        collected_height = 0
        slice_counter = 0
        slices_in_tank = len(self.my_slices)
        while slice_counter < slices_in_tank and height_of_interest > collected_height:
            collected_height += self.my_slices[slice_counter].height
            slice_counter += 1
        if height_of_interest == 0:
            temperature_of_interest = self.my_slices[0].temperature
        else:
            # numbers of my_slice start at 0, so 1 has to be subtracted
            temperature_of_interest = self.my_slices[slice_counter - 1].temperature

        return temperature_of_interest

    def get_load_percentage(self, minimum_temperature):
        """
        --> not used
        How big is the part of the tank which is above a certain temperature.
        :param minimum_temperature: Only slices with this temperature or above this temperature-level can be used properly
        :return: usable_percentage_of_tank: % which are above minimum temperature
        """
        usable_height = 0
        slice_counter = 0
        slices_in_tank = len(self.my_slices)
        while slice_counter < slices_in_tank and self.my_slices[slice_counter].temperature >= minimum_temperature:
            usable_height += self.my_slices[slice_counter].height
            slice_counter += 1
        usable_percentage_of_tank = usable_height / self.height_storage * 100

        return usable_percentage_of_tank, usable_height

    def calculate_tanks_enthalpy(self):
        """
        To get the enthalpy of all slices combined
        No changes of values
        Calculation from Ws to kWh; 3600s = 1h, 1000W = 1kW
        :return enthalpy_tank   Enthalpy of the tank [kWh]
        """
        enthalpy_tank = 0
        for i in range(len(self.my_slices)):
            enthalpy_tank += self.my_slices[i].enthalpy

        enthalpy_tank = enthalpy_tank / 3600 / 1000
        return enthalpy_tank

    def calculate_tanks_mean_temperature(self):
        """
        Get the average temperature of tank
        specific_heat_capacity can be neglected because its constant for all slices
        For each slice:
        temperature(i) * mass(i) / total_mass
        :return average_temperature     Average tank temperature [°C]
        """
        total_energy = 0
        average_density = PhysicsConfig.water_density
        for i in range(len(self.my_slices)):
            total_energy += self.my_slices[i].mass * self.my_slices[i].temperature
        average_temperature = total_energy / (self.volume * average_density)
        return average_temperature

    def calculate_tanks_mass(self):
        total_mass = 0
        for i in range(len(self.my_slices)):
            total_mass += self.my_slices[i].mass
        return total_mass

    def energy_losses_in_one_timestep(self, u_value_tank, seconds_per_timestep: int = 1, ambient_temperature: float = 20):
        """
        Energy losses for all the slices in the tank
        The called function 'heat_losses_to_ambient' & 'heat_losses_top_bottom' sets a new enthalpy and temperature for slices
        :return losses_this_timestep [Ws]    Sum of all heat losses to the ambient in this timestep
        """
        losses_this_timestep = 0
        # Losses at top and bottom
        losses_this_timestep += self.my_slices[0].heat_losses_vertical_top_or_bottom(u_value_tank, seconds_per_timestep, ambient_temperature)
        losses_this_timestep += self.my_slices[-1].heat_losses_vertical_top_or_bottom(u_value_tank, seconds_per_timestep, ambient_temperature)

        # Losses to the sides
        for i in range(len(self.my_slices)):
            losses_this_timestep += self.my_slices[i].heat_losses_horizontal(u_value_tank, seconds_per_timestep, ambient_temperature)
        return losses_this_timestep

    def energy_exchange_between_slices(self, lambda_water_water: float = 0.6, seconds_per_timestep: int = 1):
        """
        Internal heat exchange between the slices
        The transferred heat is defined by the temperature difference and the distance between the segments middle.
        slice_height saves all the distances between the slices.
        energy_transfer saves the energy which is transferred to the slice below.
        The transferred energy ( = Enthalpy) is subtracted from the slices and a new temperature is calculated.
        The storing of transferred energy is necessary because a direct subtraction would influence the iterative process.

        u_value Water-Water = ~0.6 ?!
        Upper slice (warmer):
            energy is transferred to the lower slice
        Upper slice (colder):
            energy is received from the upper slice

        --> top slice just looses energy
        --> bottom slice just gains energy
        """

        energy_transfer = []
        slice_height = []
        # for i in range(len(self.my_slices)):
        # print("Schicht: " + str(i) + " Höhe: " + str(self.my_slices[i].height) + " Temperatur: " + str(self.my_slices[i].temperature))

        for i in range(len(self.my_slices) - 1):
            slice_height.append((self.my_slices[i].height + self.my_slices[i+1].height) / 2)

        for i in range(len(self.my_slices) - 1):
            energy_transfer.append((lambda_water_water * self.area / slice_height[i] * (self.my_slices[i].temperature - self.my_slices[i+1].temperature)) * seconds_per_timestep)

        assert len(energy_transfer) == len(slice_height)

        for i in range(len(self.my_slices) - 1):
            self.my_slices[i].enthalpy -= energy_transfer[i]
            self.my_slices[i+1].enthalpy += energy_transfer[i]

        for i in range(len(self.my_slices)):
            # print("slice number " + str(i))
            previous = copy.deepcopy(self.my_slices[i].temperature)
            self.my_slices[i].calculate_new_temperature()
            # print(previous)
            # print(self.my_slices[i].temperature)
            assert abs(previous - self.my_slices[i].temperature) > 0

    def energy_exchange_between_slices_differential_equation(self, lambda_water_water: float = 0.06, seconds_per_timestep: int = 1):
        """
        A differential equation can be used to solve this.
        Most complex part of the whole simulation.. is this even useful?
        ToDo: This code is not working so far.. Function above should be good enough

        ToDo: Add heat transfer to the environment

        Using odeint function from scipy.integrate
        :param lambda_water_water:
        :param seconds_per_timestep:
        :return:
        """
        slice_height = []
        for i in range(len(self.my_slices)):
            print("Schicht: " + str(i) + " Höhe: " + str(self.my_slices[i].height) + " Temperatur: " + str(self.my_slices[i].temperature))

        for i in range(len(self.my_slices) - 1):
            slice_height.append((self.my_slices[i].height + self.my_slices[i+1].height) / 2)
        slice_height.append(0)

        def ode_sd():
            # ab 2. Schicht
            division_factor = (self.my_slices[i].specific_heat_capacity * self.my_slices[i].mass)
            coefficient_T_slice = ((lambda_water_water * self.my_slices[i].area) / slice_height[i] + (lambda_water_water * self.my_slices[i].area)/ slice_height[i+1]) / division_factor

            coefficient_constant_T_slice = ((lambda_water_water * self.my_slices[i].area * self.my_slices[i-1].temperature) / slice_height[i] + (lambda_water_water * self.my_slices[i].area * self.my_slices[i+1].temperature)/ slice_height[i+1]) / division_factor

            dT_waterslice_dt = (-1) * self.my_slices[i].temperature * coefficient_T_slice + coefficient_constant_T_slice

            return dT_waterslice_dt
        new_temperatures = []
        for i in range(len(self.my_slices)):
            # y = odeint(ode_sd, y0, ts)
            # new_temperatures.append(y)
            pass
        for i in range(len(self.my_slices)):
            self.my_slices[i].temperature = new_temperatures[i]
        pass

class ThermalEnergyStorage2(Component):
    # Input
    HeatPump_ChargingSideInput_mass = "ChargingSideInput_mass"                       # kg/s
    HeatPump_ChargingSideInput_temperature = "ChargingSideInput_temperature"         # °C
    #Heating_DischargingSideInput_mass = "Heating DischargingSideInput_mass"                 # kg/s
    #Heating_DischargingSideInput_temperature = "Heating DischargingSideInput_temperature"   # °C
    # ww
    WW_DischargingSideInput_mass = "WW DischargingSideInput_mass"                 # kg/s
    WW_DischargingSideInput_temperature = "WW DischargingSideInput_temperature"   # °C

    # Output
    HeatPump_ChargingSideOutput_mass = "ChargingSideOutput_mass"                       # kg/s
    HeatPump_ChargingSideOutput_temperature = "ChargingSideOutput_temperature"         # °C
    #Heating_DischargingSideOutput_mass = "Heating DischargingSideOutput_mass"               # kg/s
    #Heating_DischargingSideOutput_temperature = "Heating DischargingSideOutput_temperature" # °C
    # ww
    WW_DischargingSideOutput_mass = "WW DischargingSideOutput_mass"  # kg/s
    WW_DischargingSideOutput_temperature = "WW DischargingSideOutput_temperature"  # °C

    # temperatures
    Temperature0Percent = "Temperature 0 Percent"                           # °C
    Temperature20Percent = "Temperature 20 Percent"                         # °C
    Temperature40Percent = "Temperature 40 Percent"                         # °C
    Temperature60Percent = "Temperature 60 Percent"                         # °C
    Temperature80Percent = "Temperature 80 Percent"                         # °C
    Temperature100Percent = "Temperature 100 Percent"                       # °C

    # For information
    TankEnthalpy = "Tank_Enthalpy"                                          # kWh
    TankMeanTemperature = "Tank_Mean_Temperature"                           # °C
    AmountOfSlices = "Amount Of Slices"                                     # -
    HeatLosses = "Heat Losses"                                              # W
    TankMass = "Tank Mass"                                                  # kg

    def __init__(self, component_name, config: WarmWaterStorageConfig, sim_params):
        super().__init__(component_name)

        # Input
        self.charging_side_input_mass: ComponentInput = self.add_input(self.ComponentName, self.HeatPump_ChargingSideInput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec, True)
        self.charging_side_input_temperature: ComponentInput = self.add_input(self.ComponentName, self.HeatPump_ChargingSideInput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)

        #self.heating_discharging_side_input_mass: ComponentInput = self.add_input(self.ComponentName, self.Heating_DischargingSideInput_mass, LoadTypes.WarmWater, lt.Units.kg_per_sec, True)
        #self.heating_discharging_side_input_temperature: ComponentInput = self.add_input(self.ComponentName, self.Heating_DischargingSideInput_temperature, LoadTypes.WarmWater, lt.Units.Celsius, True)
        # warm water
        self.ww_discharging_side_input_mass: ComponentInput = self.add_input(self.ComponentName, self.WW_DischargingSideInput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec, True)
        self.ww_discharging_side_input_temperature: ComponentInput = self.add_input(self.ComponentName, self.WW_DischargingSideInput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)

        # Output
        self.charging_side_output_mass: ComponentOutput = self.add_output(self.ComponentName, self.HeatPump_ChargingSideOutput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        self.charging_side_output_temperature: ComponentOutput = self.add_output(self.ComponentName, self.HeatPump_ChargingSideOutput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        #self.heating_discharging_side_output_mass: ComponentOutput = self.add_output(self.ComponentName, self.Heating_DischargingSideOutput_mass, LoadTypes.WarmWater, lt.Units.kg_per_sec)
        #self.heating_discharging_side_output_temperature: ComponentOutput = self.add_output(self.ComponentName, self.Heating_DischargingSideOutput_temperature, LoadTypes.WarmWater, lt.Units.Celsius)
        # warm water
        self.ww_discharging_side_output_mass: ComponentOutput = self.add_output(self.ComponentName, self.WW_DischargingSideOutput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        self.ww_discharging_side_output_temperature: ComponentOutput = self.add_output(self.ComponentName, self.WW_DischargingSideOutput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius)

        # temperatures
        self.temperature_0_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature0Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.temperature_20_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature20Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.temperature_40_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature40Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.temperature_60_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature60Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.temperature_80_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature80Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.temperature_100_percent: ComponentOutput = self.add_output(self.ComponentName, self.Temperature100Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius)

        # Outputs for information
        self.tank_enthalpy: ComponentOutput = self.add_output(self.ComponentName, self.TankEnthalpy, lt.LoadTypes.WarmWater, lt.Units.kWh)
        self.tank_mean_temperature: ComponentOutput = self.add_output(self.ComponentName, self.TankMeanTemperature, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.amount_of_slices: ComponentOutput = self.add_output(self.ComponentName, self.AmountOfSlices, lt.LoadTypes.WarmWater, lt.Units.Any)
        self.heat_losses: ComponentOutput = self.add_output(self.ComponentName, self.HeatLosses, lt.LoadTypes.WarmWater, lt.Units.Watt)
        self.tank_mass: ComponentOutput = self.add_output(self.ComponentName, self.TankMass, lt.LoadTypes.WarmWater, lt.Units.kg)

        self.wws = WarmWaterStorageSimulation(config)
        self.seconds_per_timestep = sim_params.seconds_per_timestep
        self.previous_state = 0

    def i_save_state(self):
        self.previous_state = self.wws.begin_new_timestep()

    def i_restore_state(self):
        self.wws.reset_to_last_timestep(self.previous_state)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):

        # temperature of the tank at different heights
        # 0 = top, 100 = bottom
        height_one_percent = WarmWaterStorageConfig.tank_height / 100
        temperature_0_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 0)
        temperature_20_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 20)
        temperature_40_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 40)
        temperature_60_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 60)
        temperature_80_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 80)
        temperature_100_percent = self.wws.get_temperature_level_at_specific_height(height_one_percent * 100)

        tank_enthalpy = self.wws.calculate_tanks_enthalpy()
        tank_mean_temperature = self.wws.calculate_tanks_mean_temperature()
        # sometimes there occur rounding errors at the ~10th decimal digit. they can be neglected
        tank_mass = self.wws.calculate_tanks_mass()

        # amount of slices in the tank
        amount_of_slices = len(self.wws.my_slices)

        # water slice input values
        # mass is in kg/s --> converted to kg/timestep

        # charging side
        charging_side_input_mass_sec = stsv.get_input_value(self.charging_side_input_mass)
        total_charging_side_input_mass = charging_side_input_mass_sec * self.seconds_per_timestep
        total_charging_side_input_temperature = stsv.get_input_value(self.charging_side_input_temperature)
        is_from_top_upper = True

        if total_charging_side_input_mass <= 0:
            total_charging_side_input_temperature = 99.123

        # discharging side
        # from heating
        #heating_discharging_side_input_mass_sec = stsv.get_input_value(self.heating_discharging_side_input_mass)
        #heating_discharging_side_input_mass = heating_discharging_side_input_mass_sec * self.seconds_per_timestep
        #heating_discharging_side_input_temperature = stsv.get_input_value(self.heating_discharging_side_input_temperature)
        # for warm water
        ww_discharging_side_input_mass_sec = stsv.get_input_value(self.ww_discharging_side_input_mass)
        ww_discharging_side_input_mass = ww_discharging_side_input_mass_sec * self.seconds_per_timestep
        ww_discharging_side_input_temperature = stsv.get_input_value(self.ww_discharging_side_input_temperature)

        # total discharging input
        #total_discharging_side_input_mass = heating_discharging_side_input_mass + ww_discharging_side_input_mass
        total_discharging_side_input_mass = ww_discharging_side_input_mass
        is_from_top_bottom = False

        if total_discharging_side_input_mass > 0:
            #total_discharging_side_input_temperature = (heating_discharging_side_input_mass * heating_discharging_side_input_temperature +
            #                                            ww_discharging_side_input_mass * ww_discharging_side_input_temperature) / total_discharging_side_input_mass
            total_discharging_side_input_temperature = (ww_discharging_side_input_mass * ww_discharging_side_input_temperature) / total_discharging_side_input_mass
        else:
            total_discharging_side_input_temperature = 66.123

        # actual simulation of wws
        output_top, output_bottom, heat_losses = self.wws.simulate_one_timestep(self.seconds_per_timestep,
                                                                                total_charging_side_input_temperature,
                                                                                total_charging_side_input_mass,
                                                                                is_from_top_upper,
                                                                                total_discharging_side_input_temperature,
                                                                                total_discharging_side_input_mass,
                                                                                is_from_top_bottom)

        # Check temperature of the two highest and the two lowest slices
        self.wws.check_slice_temperature_order()

        # Ws --> W
        heat_losses_watt = heat_losses / self.seconds_per_timestep

        # set outputs

        # water incompressibility -> what mass comes in from one side, has to go out at the same side
        # massflows are equal to the inflows
        stsv.set_output_value(self.charging_side_output_mass, charging_side_input_mass_sec)
        #stsv.set_output_value(self.heating_discharging_side_output_mass, heating_discharging_side_input_mass_sec)
        stsv.set_output_value(self.ww_discharging_side_output_mass, ww_discharging_side_input_mass_sec)

        # output temperatures
        stsv.set_output_value(self.charging_side_output_temperature, output_bottom.temperature)
        #stsv.set_output_value(self.heating_discharging_side_output_temperature, output_top.temperature)
        stsv.set_output_value(self.ww_discharging_side_output_temperature, output_top.temperature)

        # measured temperatures
        stsv.set_output_value(self.temperature_0_percent, temperature_0_percent)
        stsv.set_output_value(self.temperature_20_percent, temperature_20_percent)
        stsv.set_output_value(self.temperature_40_percent, temperature_40_percent)
        stsv.set_output_value(self.temperature_60_percent, temperature_60_percent)
        stsv.set_output_value(self.temperature_80_percent, temperature_80_percent)
        stsv.set_output_value(self.temperature_100_percent, temperature_100_percent)

        # for information
        stsv.set_output_value(self.tank_enthalpy, tank_enthalpy)
        stsv.set_output_value(self.tank_mean_temperature, tank_mean_temperature)
        stsv.set_output_value(self.amount_of_slices, amount_of_slices)
        stsv.set_output_value(self.heat_losses, heat_losses_watt)
        stsv.set_output_value(self.tank_mass, tank_mass)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

class PhysicsConfig:
    water_density = 1000                                        # [kg/m^3]
    water_specific_heat_capacity = 4.180                         # J/kgK

