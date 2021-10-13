# Owned
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""

class GasHeaterConfig:
    is_modulating = True
    P_th_min = 1_000                    # [W]
    P_th_max = 12_000                    # [W]
    eff_th_min = 0.60                   # [-]
    eff_th_max = 0.90                   # [-]
    delta_T = 25
    mass_flow_max = P_th_max / (4180 * delta_T)     # kg/s ## -> ~0.07
    temperature_max = 80                # [°C]

class GasHeater(Component):
    #Input
    ControlSignal = "ControlSignal" # at which Procentage is the GasHeater modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"

    #Output
    MassflowOutput= "Hot Water Energy Output"
    MassflowOutputTemperature= "MassflowOutputTemperature"
    GasDemand = "GasDemand"

    def __init__(self, name: str, P_th_min=1000, P_th_max=12000, eff_th_min=0.6, eff_th_max=0.9, temperature_max=80, temperaturedelta=10):
        super().__init__(name)
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, GasHeater.ControlSignal, lt.LoadTypes.Any, lt.Units.Percent, True)
        self.mass_inp_temp: ComponentInput = self.add_input(self.ComponentName, GasHeater.MassflowInputTemperature, lt.LoadTypes.Water, lt.Units.Celcius, True)


        self.mass_out: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.mass_out_temp: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutputTemperature, lt.LoadTypes.Water, lt.Units.Celcius)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, GasHeater.GasDemand, lt.LoadTypes.Gas, lt.Units.kWh)

        self.P_th_min = P_th_min
        self.P_th_max = P_th_max
        self.eff_th_min = eff_th_min
        self.eff_th_max = eff_th_max
        self.temperature_max = temperature_max
        self.temperaturedelta = temperaturedelta


    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool):
        control_signal = stsv.get_input_value(self.control_signal)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")

        #Calculate Eff
        d_eff_th = (self.eff_th_max - self.eff_th_min)

        if control_signal*self.P_th_max<self.P_th_min:
            maximum_power=self.P_th_min
            eff_th_real=self.eff_th_min
        else:
            maximum_power=control_signal*self.P_th_max
            eff_th_real = self.eff_th_min + d_eff_th * control_signal

        gas_power = maximum_power *eff_th_real* control_signal
        cw = 4182
        mass_out_temp=self.temperaturedelta+stsv.get_input_value(self.mass_inp_temp)
        mass_out=gas_power/(cw*mass_out_temp)

        stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # efficiency
        stsv.set_output_value(self.mass_out, mass_out)  # efficiency
        stsv.set_output_value(self.gas_demand, gas_power)  # gas consumption
