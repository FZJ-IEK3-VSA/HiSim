
from math import pi

from components.extended_storage import WaterSlice
from components.configuration import WarmWaterStorageConfig, GasHeaterConfig
from components.configuration import PhysicsConfig
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt

from components.configuration import GasControllerConfig
from math import floor

class GasController(Component):

    # inputs
    # temperatures
    Temperature0Percent = "Temperature 0 Percent"       # °C
    Temperature20Percent = "Temperature 20 Percent"     # °C
    Temperature40Percent = "Temperature 40 Percent"     # °C
    Temperature60Percent = "Temperature 60 Percent"     # °C
    Temperature80Percent = "Temperature 80 Percent"     # °C
    Temperature100Percent = "Temperature 100 Percent"   # °C

    # outputs
    GasHeaterPowerPercent = "Gas Heater Power Percent"  # %
    OnOffCycles = "On Off Cycles"

    def __init__(self, component_name, seconds_per_timestep):
        super().__init__(component_name)
        self.temperature_0_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature0Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_20_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature20Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_40_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature40Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_60_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature60Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_80_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature80Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_100_percent: ComponentInput = self.add_input(self.ComponentName, GasController.Temperature100Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)

        self.gasheater_power_percent: ComponentOutput = self.add_output(self.ComponentName, GasController.GasHeaterPowerPercent, lt.LoadTypes.Any, lt.Units.Percent)

        # self.on_off_cycles: ComponentOutput = self.add_output(self.ComponentName, ExtendedController.OnOffCycles, LoadTypes.Any, lt.Units.Any)
        # self.on_off_cycles_counter = 0

        self.seconds_per_timestep = seconds_per_timestep
        self.runtime_counter = 0
        self.previous_runtime = self.runtime_counter
        self.state = 0
        self.previous_state = self.state

    def i_save_state(self):
        self.previous_state = self.state
        self.previous_runtime = self.runtime_counter

    def i_restore_state(self):
        self.state = self.previous_state
        self.runtime_counter = self.previous_runtime

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        if force_convergence:
            return

        temperature_0_percent = stsv.get_input_value(self.temperature_0_percent)
        temperature_20_percent = stsv.get_input_value(self.temperature_20_percent)
        temperature_40_percent = stsv.get_input_value(self.temperature_40_percent)
        temperature_60_percent = stsv.get_input_value(self.temperature_60_percent)
        temperature_80_percent = stsv.get_input_value(self.temperature_80_percent)
        temperature_100_percent = stsv.get_input_value(self.temperature_100_percent)

        temperatures_in_tank = [temperature_0_percent, temperature_20_percent, temperature_40_percent,
                                temperature_60_percent, temperature_80_percent, temperature_100_percent]
        heights_in_tank = [0, 20, 40, 60, 80, 100]

        if (GasControllerConfig.height_upper_sensor or GasControllerConfig.height_lower_sensor) not in heights_in_tank:
            print("Wrong sensor setting. Only 0, 20, 40, 60, 80, 100% are allowed.\n"
                  "You tried " + str(GasControllerConfig.height_upper_sensor) + " and " + str(GasControllerConfig.height_lower_sensor))
            raise ValueError

        for i in range(len(heights_in_tank)):
            if GasControllerConfig.height_upper_sensor == heights_in_tank[i]:
                temperature_upper_sensor = temperatures_in_tank[i]
            if GasControllerConfig.height_lower_sensor == heights_in_tank[i]:
                temperature_lower_sensor = temperatures_in_tank[i]

        # upper sensor
        if temperature_upper_sensor < GasControllerConfig.temperature_switch_on:
            if self.state == 0:
                # reset timer because chp is switched on again
                self.runtime_counter = 0
            # switch on
            self.state = 1
        # ToDo: modulating if the waste energy is to high?

        # lower sensor
        if temperature_lower_sensor > GasControllerConfig.temperature_switch_off:
            minimum_timesteps_decimal = (GasControllerConfig.minimum_runtime_minutes * 60) / self.seconds_per_timestep
            minimum_timesteps = floor(minimum_timesteps_decimal)
            if self.runtime_counter > minimum_timesteps:
                # chp has to run at least xx min
                # switch off
                self.state = 0

        # minimum runtime gas heater
        if self.state == 1:
            self.runtime_counter += 1
        # can be added if there is a solution for the <'iteration problem'>
        # if self.previous_state is not self.state:
        #    self.on_off_cycles_counter += 0.5

        # print("runtime_conunter")
        # print(self.runtime_counter)

        stsv.set_output_value(self.gasheater_power_percent, self.state)
        # stsv.set_output_value(self.on_off_cycles, self.on_off_cycles_counter)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass


class GasHeaterSimulation:

# ToDo: create config Class that reads from Excel
    def __init__(self, config: GasHeaterConfig):
        """
        Be careful: eff_th_min is the efficiency at lowest possible power. This doesn't mean that the efficiency is
        lower than eff_th_max.
        The system configuration is done in the config class.
        The gas heater converts fuel into thermal energy which can heat up the warm water storage.
        Its the backup for the CHP system and will cover peak demands and support the CHP in periods with high thermal demand.
        Systems can module
        """

        if config.is_modulating:
            self.is_modulating = True
            self.P_th_min = config.P_th_min
            self.eff_th_min = config.eff_th_min
        else:
            self.is_modulating = False

        self.P_th_max = config.P_th_max # not necessary
        self.P_total_max = config.P_th_max
        self.eff_th_max = config.eff_th_max
        # self.volume_flow_max = config.volume_flow_max / 1000 / 60    # Liters/min --> m^3/s
        self.mass_flow_max = config.mass_flow_max         # kg/s
        self.temperature_max = config.temperature_max

    def read_efficiency_curves(self, power_percentage: float): # ,ws_in_temperature: float, target_temperature: float ):
        """
        :param power_percentage:    At which percentage is the system operating atm [0-1] -> inside power range!!
        :param ws_in_temperature:   Temperature of inflowing water slice [°C]
        :param target_temperature:  Temperature which should flow to the storage [°C]
        :return:
        ToDo: The water temperature storage --> chp is influencing the efficiency, furthermore the target temperature will aswell
        """
        # Interpolation
        if self.is_modulating:
            d_eff_th = (self.eff_th_max - self.eff_th_min)
            eff_th_real = self.eff_th_min + d_eff_th * power_percentage
        else:
            eff_th_real = self.eff_th_max
        return eff_th_real

    def thermal_efficency_changes_by_backflowing_temperature(self, ws_in_temperature: float):
        """
        Interpolation like in: 'def read_efficiency_curves'
        So far only Vitovalor PA2 is providing data regarding this
        :param is_modulating:
        :param ws_in_temperature:
        :return:
        """
        pass

    def process_thermal(self, seconds_per_timestep, eff_th_real, ws_in):
        """
        The thermal efficiency at a certain operation/power level allows to calculate the added energy in this timestep.
        Adding this amount to the incoming waterslice gives a new waterslice with a higher temperature
        # :param energy_input:    Enthalpy of the incoming gas  ToDo Brennwert / Heizwert definieren -> ersetzt self.P_total
        :param eff_th_real:     Thermal efficiency atm
        :param ws_in:           Incoming waterslice from storage (of interest: mass & temperature)
        :return:                Heated waterslice which flows to the tank
        """
        heat_capacity = PhysicsConfig.water_specific_heat_capacity
        thermal_energy_to_add = self.P_total_max * eff_th_real * seconds_per_timestep       # Ws
        ws_out_mass = ws_in.mass
        try:
            ws_out_temperature = ws_in.temperature + thermal_energy_to_add / (heat_capacity * ws_out_mass)
        except ZeroDivisionError:
            print(heat_capacity)
            print(ws_out_mass)
            print(ws_in.mass)
            raise ValueError
        wasted_energy = 0
        if ws_out_temperature > self.temperature_max:
            # wasted energy due to maximum heat by gasheater caused by to hot water flowing into gasheater
            delta_T = ws_out_temperature - self.temperature_max
            wasted_energy = (delta_T * ws_out_mass * PhysicsConfig.water_specific_heat_capacity) / seconds_per_timestep  # W
            ws_out_temperature = self.temperature_max
            # print("The gasheater is not working efficient. Water from Tank to gasheater too hot")
        ws_out_enthalpy = ws_in.enthalpy + thermal_energy_to_add
        ws_in.change_slice_parameters(new_temperature=ws_out_temperature, new_enthalpy=ws_out_enthalpy, new_mass=ws_out_mass)

        return ws_in, wasted_energy, thermal_energy_to_add / seconds_per_timestep

class GasHeater(Component):
    ControlSignal = "ControlSignal"                                 # 0 or 1
    WaterInput_mass = "WaterInput_mass"                             # kg/s
    WaterInput_temperature = "WaterInput_temperature"               # °C

    GasDemand = "GasDemand"                                         # m^3
    WaterOutput_mass = "WaterOutput_mass"                           # kg/s
    WaterOutput_temperature = "WaterOutput_temperature"             # °C
    WastedEnergyMaxTemperature = "Wasted Energy Max Temperature"    # W
    ThermalOutput = "ThermalOutput"                                 # W

    def __init__(self, component_name, config: GasHeaterConfig, seconds_per_timestep):
        super().__init__(component_name)

        self.control_signal: ComponentInput = self.add_input(self.ComponentName, GasHeater.ControlSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.water_input_mass: ComponentInput = self.add_input(self.ComponentName, GasHeater.WaterInput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec, True)
        self.water_input_temperature: ComponentInput = self.add_input(self.ComponentName, GasHeater.WaterInput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)

        # the following will be calculated
        self.water_output_mass: ComponentOutput = self.add_output(self.ComponentName, GasHeater.WaterOutput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        self.water_output_temperature: ComponentOutput = self.add_output(self.ComponentName, GasHeater.WaterOutput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.wasted_energy_max_temperature: ComponentOutput = self.add_output(self.ComponentName, GasHeater.WastedEnergyMaxTemperature, lt.LoadTypes.WarmWater, lt.Units.Watt)
        self.thermal_output: ComponentOutput = self.add_output(self.ComponentName, GasHeater.ThermalOutput, lt.LoadTypes.WarmWater, lt.Units.Watt)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, GasHeater.GasDemand, lt.LoadTypes.Gas, lt.Units.kg_per_sec)

        self.gasheater = GasHeaterSimulation(config)
        self.seconds_per_timestep = seconds_per_timestep

        self.state = []
        self.previous_state = self.state

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):

        # Inputs
        control_signal = stsv.get_input_value(self.control_signal)
        water_input_mass_sec = stsv.get_input_value(self.water_input_mass)
        water_input_mass = water_input_mass_sec * self.seconds_per_timestep
        water_input_temperature = stsv.get_input_value(self.water_input_temperature)

        if control_signal == 1 and (water_input_mass == 0 and water_input_temperature == 0):
            """first iteration"""
            water_input_temperature = 40
            water_input_mass = self.gasheater.mass_flow_max * self.seconds_per_timestep     # kg

        # efficiencies regarding gasheater percentage
        eff_th_real = self.gasheater.read_efficiency_curves(control_signal)

        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")
        # so far non modulating
        if control_signal == 1:
            # volume_flow_gasheater = GasHeaterConfig.volume_flow_max * self.seconds_per_timestep
            # m^3 = kg / kg/m^3
            volume_flow_gasheater = water_input_mass / PhysicsConfig.water_density
            # print(water_input_mass)
            # print(control_signal)
            # print(water_input_temperature)
            ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, (4 * volume_flow_gasheater) / (pi * WarmWaterStorageConfig.tank_diameter ** 2), water_input_temperature)
            ws_output, wasted_energy_max_temperature, thermal_output = self.gasheater.process_thermal(self.seconds_per_timestep, eff_th_real, ws)
        elif control_signal == 0:
            # the gas heater is  not operating, slices have 0 volume and temperature (-> faster calculation to give height directly)
            height_flow_gasheater = 0
            volume_flow_gasheater = 0
            water_input_temperature = 0
            ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, height_flow_gasheater, water_input_temperature)
            # ws = WaterSlice(WarmWaterStorageConfig.tank_diameter,(4 * volume_flow_gasheater) / (pi * WarmWaterStorageConfig.tank_diameter ** 2), water_input_temperature)
            ws_output = ws
            wasted_energy_max_temperature = 0
            thermal_output = 0
        else:
            print("Wrong controller settings")
            raise ValueError

        ws_output_mass = ws_output.mass / self.seconds_per_timestep
        ws_output_temperature = ws_output.temperature

        # Mass is consistent
        stsv.set_output_value(self.water_output_mass, ws_output_mass)
        stsv.set_output_value(self.water_output_temperature, ws_output_temperature)
        stsv.set_output_value(self.wasted_energy_max_temperature, wasted_energy_max_temperature)
        stsv.set_output_value(self.thermal_output, thermal_output)

        # W = J/s
        # kg/s = J/s  /  J/kg
        gas_demand = (self.gasheater.P_total_max * control_signal) / PhysicsConfig.natural_gas_specific_fuel_value_per_kg
        stsv.set_output_value(self.gas_demand, gas_demand)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

class GasHeaterForEnergyBalance(Component):
    ControlSignal = "ControlSignal"
    HotWaterOutput = "Hot Water Energy Output"
    GasDemand = "GasDemand"

    def __init__(self, name: str, maximum_power):
        super().__init__(name)
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, GasHeaterForEnergyBalance.ControlSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.hot_water_output: ComponentOutput = self.add_output(self.ComponentName, GasHeaterForEnergyBalance.HotWaterOutput, lt.LoadTypes.WarmWater, lt.Units.kWh)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, GasHeaterForEnergyBalance.GasDemand, lt.LoadTypes.Gas, lt.Units.kWh)
        self.maximum_power = maximum_power

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        control_signal = stsv.get_input_value(self.control_signal)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")
        gas_power = self.maximum_power * control_signal
        stsv.set_output_value(self.hot_water_output, gas_power * 0.85)  # efficiency
        stsv.set_output_value(self.gas_demand, gas_power)  # gas consumption
