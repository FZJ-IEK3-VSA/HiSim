from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt
import copy
from math import pi
from math import floor

import pandas as pd
import os
import globals
'''
class CHPConfig:

    # system_name = "BlueGEN15"
    # system_name = "Dachs 0.8"
    # system_name = "Test_KWK"
    # system_name = "Dachs G2.9"
    # system_name = "HOMER"
    system_name = "BlueGen BG15"

    df = pd.read_excel(os.path.join(globals.HISIMPATH["inputs"], 'mock_up_efficiencies.xlsx'), index_col=0)

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

    # Inputs
    ControlSignal = "ControlSignal" # at which Procentage is the CHP modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"
    #OperatingModelSignal="OperatingModelSignal" #-->Wärme oder Stromgeführt. Nötig?

    #Output
    MassflowOutput= "Hot Water Energy Output"
    MassflowOutputTemperature= "MassflowOutputTemperature"
    ElectricityOutput="ElectricityOutput"
    GasDemand = "GasDemand"
    NumberofCycles = "NumberofCycles"

    def __init__(self,timesteps: int, name="CHP",min_operation_time=60, min_idle_time=15):
        super().__init__(name)
        self.timesteps = timesteps

        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time
        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = CHPState(start_timestep=int(0),cycle_number=0)
        self.previous_state = copy.deepcopy(self.state)

        self.P_th_min = CHPConfig.P_th_min
        self.P_el_min = CHPConfig.P_el_min
        self.P_el_max = CHPConfig.P_el_max
        self.P_th_max = CHPConfig.P_th_max

        self.eff_th_min = CHPConfig.eff_th_min
        self.eff_th_max = CHPConfig.eff_th_max
        self.eff_el_min = CHPConfig.eff_el_min
        self.eff_el_max = CHPConfig.eff_el_max
        self.temperature_max = CHPConfig.temperature_max
        self.mass_flow_max = CHPConfig.mass_flow_max
        self.delta_T=CHPConfig.delta_T

        #Inputs
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, CHP.ControlSignal, lt.LoadTypes.Any, lt.Units.Percent, True)
        #self.operating_mode_signal: ComponentInput = self.add_input(self.ComponentName, CHP.OperatingModelSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.mass_inp_temp: ComponentInput = self.add_input(self.ComponentName, CHP.MassflowInputTemperature, lt.LoadTypes.Water, lt.Units.Celcius, True)

        #Outputs
        self.mass_out: ComponentOutput = self.add_output(self.ComponentName, CHP.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.mass_out_temp: ComponentOutput = self.add_output(self.ComponentName, CHP.MassflowOutputTemperature, lt.LoadTypes.Water, lt.Units.Celcius)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, CHP.GasDemand, lt.LoadTypes.Gas, lt.Units.kWh)
        self.electricity_outputC: ComponentOutput = self.add_output(self.ComponentName, CHP.ElectricityOutput, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.number_of_cyclesC: ComponentOutput = self.add_output(self.ComponentName, CHP.NumberofCycles, lt.LoadTypes.Any, lt.Units.Any)







    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)
        self.number_of_cycles_previous = self.number_of_cycles

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
         pass


    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        control_signal = stsv.get_input_value(self.control_signal)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")
        cw=4182
        ## Calculation.Electric Energy deliverd
        ### CHP is on
        if self.state.activation != 0:
            self.number_of_cycles = self.state.cycle_number
            # Checks if the minimum running time has been reached

            #Minimium running time has been reached and the CHP wants to shut off -->so it shuts off
            if timestep >= self.state.start_timestep + self.min_operation_time and control_signal == 0:
                el_power=0
                self.state = CHPState(start_timestep=timestep,
                                           cycle_number=self.number_of_cycles,
                                           electricity_output=el_power)
                # all Outputs zero
                mass_out_temp=0
                mass_out=0
                el_power=0
                th_power=0
                stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
                stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
                stsv.set_output_value(self.electricity_outputC, el_power)  # mass output flow
                # zu ändern, da gas_demand=!gas_power
                stsv.set_output_value(self.gas_demand, th_power)
                stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
                return



            #Minimium running time has not been reached and the CHP wants to shut off -->so it won't shut off
            elif timestep < self.state.start_timestep + self.min_operation_time and control_signal == 0:
                # CHP doesn't want to run but has to, therefore is going to run in minimum power

                maximum_power_th = self.P_th_min
                eff_th_real = self.eff_th_min
                eff_el_real=self.eff_el_min
                maximum_power_el = self.P_el_min

                th_power = maximum_power_th * eff_th_real
                el_power = maximum_power_el * eff_el_real

                mass_out_temp = self.delta_T + stsv.get_input_value(self.mass_inp_temp)
                mass_out = th_power / (cw * self.delta_T)

                if mass_out > self.mass_flow_max:
                    mass_out = self.mass_flow_max
                    mass_out_temp = self.mass_inp_temp + th_power / (mass_out * cw)

            # CHP doens't want to shut and its activated--> so its stays activated
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
                    mass_out_temp = self.mass_inp_temp + th_power / (mass_out * cw)

            stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
            stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
            stsv.set_output_value(self.electricity_outputC, el_power)  # mass output flow
            # zu ändern, da gas_demand=!gas_power
            stsv.set_output_value(self.gas_demand, th_power)  # gas consumption
            stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
            return
                #run in power of control_signal



        ### CHP is Off
        #CHP wants to start and waited long enough since last start--> so it starts
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
                mass_out_temp = self.mass_inp_temp + th_power / (mass_out * cw)

            self.state = CHPState(start_timestep=timestep,
                                  electricity_output = el_power,
                                  cycle_number=number_of_cycles)



        #CHP wants to starts but didn't wait long enough since last start -> so it won't start
        else:
            #all Outputs should be zero, because CHP can't start
            mass_out_temp = 0
            mass_out = 0
            el_power = 0
            th_power = 0

        stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # mass output temp
        stsv.set_output_value(self.mass_out, mass_out)  # mass output flow
        stsv.set_output_value(self.electricity_outputC, el_power)  # mass output flow
        # zu ändern, da gas_demand=!gas_power
        stsv.set_output_value(self.gas_demand, th_power)
        stsv.set_output_value(self.number_of_cyclesC, self.number_of_cycles)
'''