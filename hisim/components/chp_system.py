from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt
import copy
from components.configuration import PhysicsConfig
from math import pi
from math import floor

import pandas as pd
import os
import globals
import math

__authors__ = "Frank Burkrad, Maximilian Hillen,"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"


class CHPConfigSimple:
    """
    CHP Config
    """
    is_modulating = True
    P_el_min = 2_000        # [W]
    P_th_min = 3_000        # [W]
    eff_el_min = 0.2        # [-]
    eff_th_min = 0.5        # [-]
    mass_flow_max=0.011     #kg/s
    P_el_max = 3_000        # [W]
    P_th_max = 4_000        # [W]
    eff_el_max = 0.4        # [-]
    eff_th_max = 0.55       # [-]
    temperature_max=80
    delta_T=10

class CHPConfig:

    # system_name = "BlueGEN15"
    # system_name = "Dachs 0.8"
    # system_name = "Test_KWK"
    # system_name = "Dachs G2.9"
    # system_name = "HOMER"
    system_name = "BlueGen BG15"

    df = pd.read_excel(os.path.join(globals.HISIMPATH["chp_system"], 'mock_up_efficiencies.xlsx'), index_col=0)

    df_specific = df.loc[str(system_name)]

    if str(df_specific['is_modulating']) == 'Yes':
        is_modulating = True
        P_el_min = df_specific['P_el_min']
        P_th_min = df_specific['P_th_min']
        P_total_min = df_specific['P_total_min']
        eff_el_min = df_specific['eff_el_min']
        eff_th_min = df_specific['eff_th_min']

    elif str(df_specific['is_modulating']) == 'No':
        is_modulating = False
    else:
        print("Modulation is not defined. Modulation must be 'Yes' or 'No'")
        raise ValueError

    P_el_max = df_specific['P_el_max']
    P_th_max = df_specific['P_th_max']
    P_total_max = df_specific['P_total_max']        # maximum fuel consumption
    eff_el_max = df_specific['eff_el_max']
    eff_th_max = df_specific['eff_th_max']
    mass_flow_max = df_specific['mass_flow (dT=20°C)']
    temperature_max = df_specific['temperature_max']
    delta_T=10

class CHPState:
    def __init__(self,
                 start_timestep=None,
                 electricity_output = 0.0,
                 cycle_number=None):
        self.start_timestep=start_timestep
        self.electricity_output = electricity_output
        self.cycle_number = cycle_number
        if self.electricity_output == 0.0:
            self.activation = 0
        elif self.electricity_output > 0.0:
            self.activation = 1
        else:
            raise Exception("Impossible CHPState.")
class CHP(Component):
    """
    Simulate chp efficiency (cop) as well as electrical (p_el) &
    thermal power (p_th), massflow (m_dot) and output temperature (t_out).
    """
    # Inputs
    ControlSignal = "ControlSignal" # at which Procentage is the CHP modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"
    ElectricityFromCHPTarget="ElectricityFromCHPTarget"
    HydrogenNotReleased="HydrogenNotReleased"
    #OperatingModelSignal="OperatingModelSignal" #-->Wärme oder Stromgeführt. Nötig?

    #Output
    MassflowOutput= "Hot Water Energy Output"
    MassflowOutputTemperature= "MassflowOutputTemperature"
    ElectricityOutput="ElectricityOutput"
    GasDemandTarget = "GasDemandTarget"
    NumberofCycles = "NumberofCycles"
    ThermalOutputPower = "ThermalOutputPower"
    GasDemandReal="GasDemandReal"
    def __init__(self, name="CHP",min_operation_time=60, min_idle_time=15,gas_type="Hydrogen",operating_mode="both",p_el_max=3600):
        super().__init__(name)
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time
        self. gas_type = gas_type #Gas Type can be "Hydrogen" or "Methan"
        self.operating_mode=operating_mode #operating_mode=["both","heat","electricity"]

        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = CHPState(start_timestep=int(0),cycle_number=0)
        self.previous_state = copy.deepcopy(self.state)

        #the 3600 comes from Normalised chp from p_el_max=3600. Look up chp_system_lib for more information
        self.P_el_max = p_el_max
        usually_P_el_max= CHPConfigSimple.P_el_max
        self.P_th_max = CHPConfigSimple.P_th_max*(self.P_el_max/usually_P_el_max)
        self.P_th_min = CHPConfigSimple.P_th_min
        self.P_el_min = CHPConfigSimple.P_el_min

        if self.P_el_max < self.P_el_min or self.P_th_max < self.P_th_min:
            self.P_el_max=self.P_el_min+100
            self.P_th_max=self.P_th_max+100

        self.mass_flow_max = CHPConfigSimple.mass_flow_max * (self.P_el_max/usually_P_el_max)
        if self.mass_flow_max < CHPConfigSimple.mass_flow_max:
            self.mass_flow_max=CHPConfigSimple.mass_flow_max
        self.eff_th_min = CHPConfigSimple.eff_th_min
        self.eff_th_max = CHPConfigSimple.eff_th_max
        self.eff_el_min = CHPConfigSimple.eff_el_min
        self.eff_el_max = CHPConfigSimple.eff_el_max
        self.temperature_max = CHPConfigSimple.temperature_max

        self.delta_T=CHPConfigSimple.delta_T



        #Inputs
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, CHP.ControlSignal, lt.LoadTypes.Any, lt.Units.Percent, False)
        #self.operating_mode_signal: ComponentInput = self.add_input(self.ComponentName, CHP.OperatingModelSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.mass_inp_temp: ComponentInput = self.add_input(self.ComponentName, CHP.MassflowInputTemperature, lt.LoadTypes.Water, lt.Units.Celsius, False)
        self.electricity_target: ComponentInput = self.add_input(self.ComponentName, CHP.ElectricityFromCHPTarget, lt.LoadTypes.Electricity, lt.Units.Watt, False)
        self.hydrogen_not_released: ComponentInput = self.add_input(self.ComponentName, CHP.HydrogenNotReleased, lt.LoadTypes.Gas, lt.Units.kg,False)


        #Outputs
        self.mass_out: ComponentOutput = self.add_output(self.ComponentName, CHP.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.mass_out_temp: ComponentOutput = self.add_output(self.ComponentName, CHP.MassflowOutputTemperature, lt.LoadTypes.Water, lt.Units.Celsius)
        self.gas_demand_target: ComponentOutput = self.add_output(self.ComponentName, CHP.GasDemandTarget, lt.LoadTypes.Gas, lt.Units.kg_per_sec)
        self.el_power: ComponentOutput = self.add_output(self.ComponentName, CHP.ElectricityOutput, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.number_of_cyclesC: ComponentOutput = self.add_output(self.ComponentName, CHP.NumberofCycles, lt.LoadTypes.Any, lt.Units.Any)
        self.th_power: ComponentOutput = self.add_output(self.ComponentName, CHP.ThermalOutputPower, lt.LoadTypes.Heating, lt.Units.Watt)
        self.gas_demand_real_used: ComponentOutput = self.add_output(self.ComponentName, CHP.GasDemandReal, lt.LoadTypes.Gas, lt.Units.kg_per_sec)







    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)
        self.number_of_cycles_previous = self.number_of_cycles

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
         pass

    def simulate_chp(self,control_signal:float,stsv: SingleTimeStepValues,timestep:int):

        cw = 4182
        ## Calculation.Electric Energy deliverd
        ### CHP is on
        if self.state.activation != 0:
            self.number_of_cycles = self.state.cycle_number
            # Checks if the minimum running time has been reached

            # Minimium running time has been reached and the CHP wants to shut off -->so it shuts off
            if timestep >= self.state.start_timestep + self.min_operation_time and control_signal == 0:

                # all Outputs zero
                mass_out_temp = 0
                mass_out = 0
                el_power = 0
                th_power = 0
                eff_el_real = 0
                eff_th_real = 0

                self.state = CHPState(start_timestep=timestep,
                                      cycle_number=self.number_of_cycles,
                                      electricity_output=el_power)

                stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
                stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
                stsv.set_output_value(self.el_power, el_power)  # mass output flow
                stsv.set_output_value(self.th_power, th_power)  # mass output flow

                # zu ändern, da gas_demand=!gas_power
                stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
                return el_power, th_power, eff_el_real, eff_th_real



            # Minimium running time has not been reached and the CHP wants to shut off -->so it won't shut off
            elif timestep < self.state.start_timestep + self.min_operation_time and control_signal == 0:
                # CHP doesn't want to run but has to, therefore is going to run in minimum power

                maximum_power_th = self.P_th_min
                eff_th_real = self.eff_th_min
                eff_el_real = self.eff_el_min
                maximum_power_el = self.P_el_min

                th_power = maximum_power_th * eff_th_real
                el_power = maximum_power_el * eff_el_real

                mass_out_temp = self.delta_T + stsv.get_input_value(self.mass_inp_temp)
                mass_out = th_power / (cw * self.delta_T)

                if mass_out > self.mass_flow_max:
                    mass_out = self.mass_flow_max
                    mass_out_temp = stsv.get_input_value(self.mass_inp_temp) + th_power / (mass_out * cw)

            # CHP doens't want to shut off and its activated--> so its stays activated
            else:
                # Calculate Eff_th
                d_eff_th = (self.eff_th_max - self.eff_th_min)

                if control_signal * self.P_th_max < self.P_th_min:
                    maximum_power_th = self.P_th_min
                    eff_th_real = self.eff_th_min
                else:
                    maximum_power_th = control_signal * self.P_th_max
                    eff_th_real = self.eff_th_min + d_eff_th * control_signal

                # Calculate Eff_el
                d_eff_el = (self.eff_el_max - self.eff_el_min)

                if control_signal * self.P_el_max < self.P_el_min:
                    maximum_power_el = self.P_el_min
                    eff_el_real = self.eff_el_min
                else:
                    maximum_power_el = control_signal * self.P_el_max
                    eff_el_real = self.eff_el_min + d_eff_el * control_signal

                th_power = maximum_power_th * eff_th_real
                el_power = maximum_power_el * eff_el_real

                mass_out_temp = self.delta_T + stsv.get_input_value(self.mass_inp_temp)
                mass_out = th_power / (cw * self.delta_T)

                if mass_out > self.mass_flow_max:
                    mass_out = self.mass_flow_max
                    mass_out_temp = stsv.get_input_value(self.mass_inp_temp) + th_power / (mass_out * cw)

            th_power = (mass_out_temp - stsv.get_input_value(self.mass_inp_temp)) * cw * mass_out
            stsv.set_output_value(self.th_power, th_power)  # ThermalPowerOutput
            stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
            stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
            stsv.set_output_value(self.el_power, el_power)  # mass output flow
            # zu ändern, da gas_demand=!gas_power
            stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
            return el_power, th_power, eff_el_real, eff_th_real
            # run in power of control_signal

        ### CHP is Off
        # CHP wants to start and waited long enough since last start--> so it starts
        if control_signal != 0 and (timestep >= self.state.start_timestep + self.min_idle_time):
            self.number_of_cycles = self.number_of_cycles + 1
            number_of_cycles = self.number_of_cycles

            # Calculate Eff_th
            d_eff_th = (self.eff_th_max - self.eff_th_min)

            if control_signal * self.P_th_max < self.P_th_min:
                maximum_power_th = self.P_th_min
                eff_th_real = self.eff_th_min
            else:
                maximum_power_th = control_signal * self.P_th_max
                eff_th_real = self.eff_th_min + d_eff_th * control_signal

            # Calculate Eff_el
            d_eff_el = (self.eff_el_max - self.eff_el_min)

            if control_signal * self.P_el_max < self.P_el_min:
                maximum_power_el = self.P_el_min
                eff_el_real = self.eff_el_min
            else:
                maximum_power_el = control_signal * self.P_el_max
                eff_el_real = self.eff_el_min + d_eff_el * control_signal

            th_power = maximum_power_th * eff_th_real
            el_power = maximum_power_el * eff_el_real

            mass_out_temp = self.delta_T + stsv.get_input_value(self.mass_inp_temp)
            mass_out = th_power / (cw * self.delta_T)

            if mass_out > self.mass_flow_max:
                mass_out = self.mass_flow_max
                mass_out_temp = stsv.get_input_value(self.mass_inp_temp) + th_power / (mass_out * cw)

            self.state = CHPState(start_timestep=timestep,
                                  electricity_output=el_power,
                                  cycle_number=number_of_cycles)



        # CHP wants to starts but didn't wait long enough since last start -> so it won't start
        else:
            # all Outputs should be zero, because CHP can't start
            mass_out_temp = 0
            mass_out = 0
            el_power = 0
            th_power = 0
            eff_el_real = 0
            eff_th_real = 0

        stsv.set_output_value(self.th_power, th_power)  # ThermalPowerOutput
        stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
        stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
        stsv.set_output_value(self.el_power, el_power)  # mass output flow
        # zu ändern, da gas_demand=!gas_power
        stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)

        return el_power, th_power, eff_el_real, eff_th_real

    def calculate_control_signal(self,stsv):
        if (stsv.get_input_value(self.electricity_target)) < self.P_el_min * self.eff_el_min:
            control_signal=0.4
            return control_signal
        elif (stsv.get_input_value(self.electricity_target))> self.P_el_max * self.eff_el_max:
            control_signal = 1
            return control_signal
        else:
            x1 = (-self.P_el_max - math.sqrt((self.P_el_max * self.eff_el_min) ** 2 + 4 * (
                        stsv.get_input_value(self.electricity_target) * self.P_el_max * (
                            self.eff_el_max - self.eff_el_min)))) / (
                             2 * self.P_el_max * (self.eff_el_max - self.eff_el_min))
            x2 = (-self.P_el_max + math.sqrt((self.P_el_max * self.eff_el_min) ** 2 + 4 * (
                        stsv.get_input_value(self.electricity_target) * self.P_el_max * (
                            self.eff_el_max - self.eff_el_min)))) / (
                             2 * self.P_el_max * (self.eff_el_max - self.eff_el_min))
            if 0<x1 and x1<1:
                if 0<x2 and x2<1:
                    if x1<x2:
                        return x2
                    else:
                        return x1
                else:
                    return x1
            else:
                return x2

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        if self.operating_mode=="heat":
            control_signal = stsv.get_input_value(self.control_signal)
        elif self.operating_mode=="electricity":
            control_signal = self.calculate_control_signal(stsv)
            if control_signal > 1 or control_signal<0:
                control_signal=1
        elif self.operating_mode=="both":
            control_signal=self.calculate_control_signal(stsv)
            if control_signal <= stsv.get_input_value(self.control_signal):
                    control_signal=stsv.get_input_value(self.control_signal)
            else:
                control_signal=control_signal
                if control_signal > 1 or control_signal<0:
                    control_signal=1

        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")


        el_power, th_power, eff_el_real, eff_th_real=self.simulate_chp(control_signal=control_signal, stsv=stsv, timestep=timestep)
        #Check if enough hydrogen is in the tank
        if el_power==0 and th_power==0:
            gas_demand_target=0
            gas_demand_real_used=0
        else:
            if self.gas_type == "Hydrogen":
                gas_demand_target= ((el_power/eff_el_real)+(th_power/eff_th_real ))/(PhysicsConfig.hydrogen_specific_fuel_value_per_kg)
                if stsv.get_input_value(self.hydrogen_not_released)==0:
                    #Gas Demand can completly be charged from storage
                    gas_demand_real_used=gas_demand_target

                elif stsv.get_input_value(self.hydrogen_not_released)<0:
                    print("Fault, bc. GasDemandpossible>gasdemandtarget")
                    print(timestep)

                elif stsv.get_input_value(self.hydrogen_not_released)>0:
                    #not enough Gas for running CHP on power demanded/calculated before
                    #to simplify, turn of CHP complelty, also when minimum running time isn't reached, bec. no Hydrogen is there

                    stsv.set_output_value(self.th_power, 0)  # ThermalPowerOutput
                    stsv.set_output_value(self.mass_out_temp, 0)  # mass output temp
                    stsv.set_output_value(self.mass_out, 0)  # mass output flow
                    stsv.set_output_value(self.el_power, 0)  # mass output flow
                    self.number_of_cycles = self.state.cycle_number
                    el_power=0
                    self.state = CHPState(start_timestep=timestep,
                                          cycle_number=self.number_of_cycles,
                                          electricity_output=el_power)

                    stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
                    gas_demand_real_used=0
            elif self.gas_type == "Methan":
                gas_demand_target= ((el_power/eff_el_real)+(th_power/eff_th_real ))/PhysicsConfig.natural_gas_specific_fuel_value_per_kg
                gas_demand_real_used=gas_demand_target
            else:
                raise Exception("No Gas chosen which is integrated in System")


        stsv.set_output_value(self.gas_demand_target, gas_demand_target) #CHP runs with
        stsv.set_output_value(self.gas_demand_real_used, gas_demand_real_used)  # ThermalPowerOutput
