from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt

from math import pi

from components.extended_storage import WaterSlice
from components.configuration import WarmWaterStorageConfig
from components.configuration import PhysicsConfig
from components.extended_controller import ExtendedControllerConfig
from components.configuration import CHPControllerConfig


from math import floor

import pandas as pd
import os
import globals

class CHPConfigSimple:
    is_modulating = True
    P_el_min = 2_000        # [W]
    P_th_min = 3_000        # [W]
    eff_el_min = 0.2        # [-]
    eff_th_min = 0.5        # [-]

    P_el_max = 3_000        # [W]
    P_th_max = 4_000        # [W]
    eff_el_max = 0.4        # [-]
    eff_th_max = 0.55       # [-]


class CHPConfig:
    """
    Be careful: eff_xx_min is the efficiency at lowest possible power. This doesn't mean that the efficiency is
    lower than eff_xx_max.

    P_total = fuel consumption
    system_name: which chp system is investigated :string input
    """
    # system_name = "BlueGEN15"
    # system_name = "Dachs 0.8"
    # system_name = "Test_KWK"
    # system_name = "Dachs G2.9"
    # system_name = "HOMER"
    system_name = "BlueGen BG15"

    #df = pd.read_excel(os.path.join(globals.HISIMPATH["inputs"], 'mock_up_efficiencies.xlsx'), index_col=0)

    #df_specific = df.loc[str(system_name)]

    #if str(df_specific['is_modulating']) == 'Yes':
    #    is_modulating = True
    #    P_el_min = df_specific['P_el_min']
    #    P_th_min = df_specific['P_th_min']
    #    P_total_min = df_specific['P_total_min']
    #    eff_el_min = df_specific['eff_el_min']
    #    eff_th_min = df_specific['eff_th_min']

    #elif str(df_specific['is_modulating']) == 'No':
    #    is_modulating = False
    #else:
    #    print("Modulation is not defined. Modulation must be 'Yes' or 'No'")
    #    raise ValueError

    #P_el_max = df_specific['P_el_max']
    #P_th_max = df_specific['P_th_max']
    #P_total_max = df_specific['P_total_max']        # maximum fuel consumption
    #eff_el_max = df_specific['eff_el_max']
    #eff_th_max = df_specific['eff_th_max']
    #mass_flow_max = df_specific['mass_flow (dT=20°C)']
    #temperature_max = df_specific['temperature_max']


"""
Input aus Excel vs import aus JSON überprüfen
"""

class CHPController(Component):
    # inputs
    ElectricityDemand = "Electricity Demand"            # W
    PV_Production = "PV Production"                      # W
    Temperature0Percent = "Temperature 0 Percent"       # °C
    Temperature20Percent = "Temperature 20 Percent"     # °C
    Temperature40Percent = "Temperature 40 Percent"     # °C
    Temperature60Percent = "Temperature 60 Percent"     # °C
    Temperature80Percent = "Temperature 80 Percent"     # °C
    Temperature100Percent = "Temperature 100 Percent"   # °C

    # Outputs
    CHPPowerPercent = "CHP Power Level"                 # % [0-1]
    OnOffCycles = "On Off Cycles"
    CHPMassflow = "CHP Massflow"                        # kg/s
    RuntimeCounter = "Runtime Counter"                  # timesteps

    def __init__(self, component_name, seconds_per_timestep):
        super().__init__(component_name)
        # input
        self.electricity_demand: ComponentInput = self.add_input(self.ComponentName, CHPController.ElectricityDemand, lt.LoadTypes.Electricity, lt.Units.Watt, True)
        self.pv_production: ComponentInput = self.add_input(self.ComponentName, CHPController.PV_Production, lt.LoadTypes.Electricity, lt.Units.Watt, True)

        self.temperature_0_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature0Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_20_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature20Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_40_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature40Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_60_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature60Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_80_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature80Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.temperature_100_percent: ComponentInput = self.add_input(self.ComponentName, CHPController.Temperature100Percent, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)

        self.chp_power_percent: ComponentOutput = self.add_output(self.ComponentName, CHPController.CHPPowerPercent, lt.LoadTypes.Any, lt.Units.Percent)
        self.chp_massflow: ComponentOutput = self.add_output(self.ComponentName, CHPController.CHPMassflow, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        self.runtime_chp: ComponentOutput = self.add_output(self.ComponentName, CHPController.RuntimeCounter, lt.LoadTypes.Any, lt.Units.Percent)

        # self.on_off_cycles: ComponentOutput = self.add_output(self.ComponentName, CHPController.OnOffCycles, LoadTypes.Any, lt.Units.Any)
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

        electricity_demand_hh = stsv.get_input_value(self.electricity_demand)
        pv_production = stsv.get_input_value(self.pv_production)
        electricity_demand = electricity_demand_hh - pv_production

        temperatures_in_tank = [temperature_0_percent, temperature_20_percent, temperature_40_percent,
                                temperature_60_percent, temperature_80_percent, temperature_100_percent]
        heights_in_tank = [0, 20, 40, 60, 80, 100]

        if (CHPControllerConfig.height_upper_sensor or CHPControllerConfig.height_lower_sensor) not in heights_in_tank:
            print("Wrong sensor setting. Only 0, 20, 40, 60, 80, 100% are allowed.\n"
                  "You tried " + str(CHPControllerConfig.height_upper_sensor) + " and " + str(CHPControllerConfig.height_lower_sensor))
            raise ValueError

        # get temperatures at the chosen sensors
        for i in range(len(heights_in_tank)):
            if CHPControllerConfig.height_upper_sensor == heights_in_tank[i]:
                temperature_upper_sensor = temperatures_in_tank[i]
            if CHPControllerConfig.height_lower_sensor == heights_in_tank[i]:
                temperature_lower_sensor = temperatures_in_tank[i]

        if CHPControllerConfig.method_of_operation == "heat":
            # upper sensor
            if temperature_upper_sensor < CHPControllerConfig.temperature_switch_on:
                if self.state == 0:
                    # reset timer because chp is switched on again
                    self.runtime_counter = 0
                # switch on
                self.state = 1

            # lower sensor
            if temperature_lower_sensor > CHPControllerConfig.temperature_switch_off:
                minimum_timesteps_decimal = (CHPControllerConfig.minimum_runtime_minutes * 60) / self.seconds_per_timestep
                minimum_timesteps = floor(minimum_timesteps_decimal)
                if self.runtime_counter > minimum_timesteps:
                    # chp has to run at least xx min
                    # switch off
                    self.state = 0

            # minimum runtime chp
            if self.state == 1:
                self.runtime_counter += 1
            # can be added if there is a solution for the <'iteration problem'>
            # if self.previous_state is not self.state:
            #    self.on_off_cycles_counter += 0.5

        elif CHPControllerConfig.method_of_operation == "power":
            if electricity_demand > 0:
                if self.state == 0:
                    # reset timer because chp is switched on again
                    self.runtime_counter = 0
                self.runtime_counter += 1

            min_power = CHPConfig.P_el_min
            max_power = CHPConfig.P_el_max
            if electricity_demand <= 0:
                self.state = 0
            elif 0 < electricity_demand <= min_power:
                self.state = 0.1
            elif min_power < electricity_demand < max_power:
                """
                A moduling component will not run on the minimum power because the demand must be greater than min.Power
                """
                delta = (max_power - min_power) / 10
                self.state = 0
                power = min_power
                while power < electricity_demand:
                    self.state += 0.1
                    power += delta
            elif electricity_demand > max_power:
                self.state = 1

        else:
            print("Selcet a correct method of operation (heat or power)")
            raise ValueError

        # Todo: Geregelte Umwälzpumpe nur bei gewissen anlagen. Daher diesen Abschnitt optional
        if 0 < self.state <= 1:
            """
            Variable massflow in the chp depending on the power level
            """
            # ToDo: Can be misleading because at e.g. state = 0.1 the massflow is going down t0 10 % but the power is a P_min + 10% of delta.
            chp_massflow = CHPConfig.mass_flow_max * self.state
        else:
            chp_massflow = 0

        # print("runtime_conunter")
        # print(self.runtime_counter)

        stsv.set_output_value(self.chp_power_percent, self.state)
        # stsv.set_output_value(self.on_off_cycles, self.on_off_cycles_counter)
        stsv.set_output_value(self.chp_massflow, chp_massflow)
        stsv.set_output_value(self.runtime_chp, self.runtime_counter)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

class CHPSystemSimulation:

    def __init__(self, config: CHPConfig):
        """
        Be careful: eff_xx_min is the efficiency at lowest possible power. This doesn't mean that the efficiency is
        lower than eff_xx_max.
        The name of the used system is defined in the config class.

        The CHP system converts a fuel (natural gas / H2) into thermal and electrical energy.
        Some systems can modulate, some can't. If thy can, they have max and min values. Otherwise just max values.
        The needed values for CHP systems are electrical and thermal Power and Efficiencies, the massflow and the maximum temperature for the thermal energy.
        The values ae red from an excel file/database.
        P_total is including the losses and is equal to the fuel consumption.
        P_total_min is calculated in the input file by =(P_th_min/eff_th_min + P_el_min/eff_el_min) / 2
        The massflow is calculated in the input file for a given temperature difference (standard: 20°C).
        """

        if config.is_modulating:
            self.is_modulating = True
            self.P_el_min = config.P_el_min
            self.P_th_min = config.P_th_min
            self.P_total_min = config.P_total_min
            self.eff_el_min = config.eff_el_min
            self.eff_th_min = config.eff_th_min
        else:
            self.is_modulating = False

        self.P_el_max = config.P_el_max
        self.P_th_max = config.P_th_max
        self.P_total_max = config.P_total_max
        self.eff_el_max = config.eff_el_max
        self.eff_th_max = config.eff_th_max
        self.mass_flow_max = config.mass_flow_max       # kg/s
        self.temperature_max = config.temperature_max

    def read_efficiency_curves(self, power_percentage: float):
        """
        Linear efficiencies are used.
        If the system is modulating, the efficiencies change inside the power range. Otherwise the efficiencies are constant.
        - A linear efficiency between the given datapoints is assumed.
        - Calculate the difference between the datapoints
            d_eff can be negative
        - Calculate the real efficiencies.
        :param      power_percentage:   At which percentage is the system operating atm [0-1] -> inside power range!!
        :return:    eff_el_real         Electric efficiency in this timestep
                    eff_th_real         Thermal efficiency in this timestep
        """
        # Interpolation
        if self.is_modulating:
            d_eff_el = (self.eff_el_max - self.eff_el_min)
            d_eff_th = (self.eff_th_max - self.eff_th_min)
            eff_el_real = self.eff_el_min + d_eff_el * power_percentage
            eff_th_real = self.eff_th_min + d_eff_th * power_percentage
        else:
            eff_el_real = self.eff_el_max
            eff_th_real = self.eff_th_max
        return eff_el_real, eff_th_real

    def thermal_efficency_changes_by_backflowing_temperature(self, ws_in_temperature: float):
        """
        Interpolation like in: 'def read_efficiency_curves'
        Option for further refinement
        Also the target temperature can be relevant
        :param      is_modulating:
        :param      ws_in_temperature:
        :return:
        """
        pass

    def process_thermal(self, seconds_per_timestep, eff_th_real, ws_in, control_signal):
        """
        The thermal efficiency at a certain operation/power level allows to calculate the added energy in this timestep.
        Adding this amount to the incoming waterslice gives a new waterslice with a higher temperature

        - Calculation of the thermal power in this timestep and the power which is added
        - Calculate the new temperature of the waterslice
        - Check if the temperature is above the maximum temperature and eventually limit it.

        TODo: if wasted energy --> reduce the control signal or increase mass flow?!

        :param      seconds_per_timestep:               Seconds per timestep [s/timestep]
        :param      eff_th_real:                        Thermal efficiency atm
        :param      ws_in:                              Incoming waterslice from storage (of interest: mass & temperature)
        :param      control_signal                      Power level of the CHP
        :return:    ws_in                               Heated waterslice which flows to the tank
                    wasted_energy [W]                   Heat which was not used
                    thermal_energy_to_add/timestep [W]  Thermal energy which was really added
        """
        if control_signal == 1:
            thermal_energy_to_add = self.P_th_max * seconds_per_timestep       # Ws
        elif control_signal == 0:
            thermal_energy_to_add = 0
        elif 0 < control_signal < 1:
            thermal_energy_to_add = (self.P_total_min + (control_signal * ExtendedControllerConfig.chp_power_states_possible - 1) * (self.P_total_max - self.P_total_min)) * eff_th_real * seconds_per_timestep  # Ws
        else:
            raise Exception ("Invalid signal: " + str(control_signal))

        heat_capacity = PhysicsConfig.water_specific_heat_capacity
        ws_out_mass = ws_in.mass
        ws_out_temperature = ws_in.temperature + thermal_energy_to_add / (heat_capacity * ws_out_mass)
        if ws_out_temperature > self.temperature_max:
            # wasted energy due to maximum heat by CHP caused by to hot water flowing into CHP
            delta_T = ws_out_temperature - self.temperature_max
            wasted_energy = (ws_out_mass * PhysicsConfig.water_specific_heat_capacity * delta_T) / seconds_per_timestep # W
            ws_out_temperature = self.temperature_max
            ws_out_enthalpy = ws_out_mass * PhysicsConfig.water_specific_heat_capacity * ws_out_temperature
            # print("The CHP is not working efficient. Water from Tank to CHP too hot")
        else:
            wasted_energy = 0
            ws_out_enthalpy = ws_in.enthalpy + thermal_energy_to_add

        ws_in.change_slice_parameters(new_temperature=ws_out_temperature, new_enthalpy=ws_out_enthalpy, new_mass=ws_out_mass)

        return ws_in, wasted_energy, thermal_energy_to_add / seconds_per_timestep

    def process_electric(self, eff_el_real, control_signal):
        """
        calculate the electrical power of the CHP

        :param eff_el:              Real el efficiency in this timestep
        :param control_signal       Power level of the CHP
        :return: electric_power [W] Generated power in this timestep
        """
        if control_signal == 1:
            electric_power = self.P_el_max
        elif control_signal == 0:
            electric_power = 0
        elif 0 < control_signal < 1:
            # electric_power = (self.P_total_min + (control_signal - 1 / ExtendedControllerConfig.chp_power_states_possible) * (self.P_total_max - self.P_total_min)) * eff_el_real # Ws
            electric_power = (self.P_el_min + (control_signal * ExtendedControllerConfig.chp_power_states_possible - 1) * (self.P_el_max - self.P_el_min) / (ExtendedControllerConfig.chp_power_states_possible - 1))
        else:
            raise Exception("Invalid signal: " + str(control_signal))

        return electric_power

    def calculate_gas_demand(self):
        # Todo :
        pass

class CHPSystem(Component):
    ControlSignal = "ControlSignal"                                 # 0 or 1
    WaterInput_mass = "WaterInput_mass"                             # kg/s
    WaterInput_temperature = "WaterInput_temperature"               # °C
    LoadPowerDemand = "Load Power Demand"                           # W

    GasDemand = "GasDemand"                                         # kg/s
    WaterOutput_mass = "WaterOutput_mass"                           # kg/s
    WaterOutput_temperature = "WaterOutput_temperature"             # °C
    WastedEnergyMaxTemperature = "Wasted Energy Max Temperature"    # W
    ThermalOutput = "ThermalOutput"                                 # W
    Electricity_output = "Electricity_output"                       # W


    def __init__(self, component_name, config: CHPConfig, seconds_per_timestep):
        super().__init__(component_name)
        # input
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, CHPSystem.ControlSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.water_input_mass: ComponentInput = self.add_input(self.ComponentName, CHPSystem.WaterInput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec, True)
        self.water_input_temperature: ComponentInput = self.add_input(self.ComponentName, CHPSystem.WaterInput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius, True)
        self.load_power_demand: ComponentInput = self.add_input(self.ComponentName, CHPSystem.LoadPowerDemand, lt.LoadTypes.Electricity, lt.Units.Watt, True)

        # output
        self.water_output_mass: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.WaterOutput_mass, lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        self.water_output_temperature: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.WaterOutput_temperature, lt.LoadTypes.WarmWater, lt.Units.Celsius)
        self.wasted_energy_max_temperature: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.WastedEnergyMaxTemperature, lt.LoadTypes.WarmWater, lt.Units.Watt)
        self.thermal_output: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.ThermalOutput, lt.LoadTypes.WarmWater, lt.Units.Watt)
        self.electricity_output: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.Electricity_output, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, CHPSystem.GasDemand, lt.LoadTypes.Gas, lt.Units.kg_per_sec)

        self.chp = CHPSystemSimulation(config)
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
        water_input_mass_per_sec = stsv.get_input_value(self.water_input_mass)
        water_input_mass = water_input_mass_per_sec * self.seconds_per_timestep
        water_input_temperature = stsv.get_input_value(self.water_input_temperature)
        # Watt
        load_power_demand = stsv.get_input_value(self.load_power_demand)
        # Todo: control signal >0
        if control_signal == 1 and (water_input_mass == 0 and water_input_temperature == 0):
            """first iteration"""
            water_input_temperature = 40
            water_input_mass = self.chp.mass_flow_max * self.seconds_per_timestep  # kg/s * s = kg

        # efficiencies regarding CHP Percentage
        eff_el_real, eff_th_real = self.chp.read_efficiency_curves(control_signal)

        if control_signal > 1:
            print(control_signal)
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            print(control_signal)
            raise Exception("Expected a control signal between 0 and 1")

        # modulating is used in electricity-led
        if 0 < control_signal <= 1:
            # volume_flow_chp = CHPConfig.volume_flow_max * self.seconds_per_timestep
            # m^3 = kg / (kg/m^3)
            volume_flow_chp = water_input_mass / PhysicsConfig.water_density
            ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, (4 * volume_flow_chp) / (pi * WarmWaterStorageConfig.tank_diameter ** 2), water_input_temperature)
            ws_output, wasted_energy_max_temperature, thermal_output = self.chp.process_thermal(self.seconds_per_timestep, eff_th_real, ws, control_signal)
            el_power = self.chp.process_electric(eff_el_real, control_signal)

        elif control_signal == 0:
            # the chp is  not operating, slices have 0 volume and temperature (-> faster calculation to give height directly)
            height_flow_chp = 0
            volume_flow_chp = 0
            water_input_temperature = 0
            ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, height_flow_chp, water_input_temperature)
            # ws = WaterSlice(WarmWaterStorageConfig.tank_diameter,(4 * volume_flow_chp) / (pi * WarmWaterStorageConfig.tank_diameter ** 2), water_input_temperature)
            ws_output = ws
            wasted_energy_max_temperature = 0
            thermal_output = 0
            el_power = 0

            # print("chp off and output T = " + str(ws_output.temperature))
        else:
            print("Wrong controller settings")
            raise ValueError

        ws_output_mass = ws_output.mass / self.seconds_per_timestep

        # W = J/s
        # kg/s = J/s  /  J/kg
        if control_signal == 0:
            gas_demand = 0
        elif control_signal == 1:
            # ToDo Heizwert vs. wärmekapazität
            gas_demand = (self.chp.P_total_max * control_signal) / PhysicsConfig.hydrogen_specific_fuel_value_per_kg
        elif 0 < control_signal < 1:
            gas_demand = (self.chp.P_total_min + (control_signal - 1 / ExtendedControllerConfig.chp_power_states_possible) * (self.chp.P_total_max - self.chp.P_total_min)) / PhysicsConfig.hydrogen_specific_fuel_value_per_kg
        else:
            print("Wrong control signal")
            raise ValueError


        # Defining outputs
        stsv.set_output_value(self.water_output_mass, ws_output_mass)
        stsv.set_output_value(self.water_output_temperature, ws_output.temperature)
        stsv.set_output_value(self.wasted_energy_max_temperature, wasted_energy_max_temperature)
        stsv.set_output_value(self.thermal_output, thermal_output)
        stsv.set_output_value(self.electricity_output, el_power)
        stsv.set_output_value(self.gas_demand, gas_demand)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass
