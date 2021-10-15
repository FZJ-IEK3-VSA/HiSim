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
    ThermalOutputPower="ThermalOutputPower"

    def __init__(self, P_th_min=1000, P_th_max=12000, eff_th_min=0.6, eff_th_max=0.9, temperature_max=80, temperaturedelta=10):
        super().__init__("GasHeater")
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, GasHeater.ControlSignal, lt.LoadTypes.Any, lt.Units.Percent, True)
        self.mass_inp_temp: ComponentInput = self.add_input(self.ComponentName, GasHeater.MassflowInputTemperature, lt.LoadTypes.Water, lt.Units.Celsius, True)


        self.mass_out: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.mass_out_temp: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutputTemperature, lt.LoadTypes.Water, lt.Units.Celsius)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, GasHeater.GasDemand, lt.LoadTypes.Gas, lt.Units.kWh)
        self.p_th: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.ThermalOutputPower,
                                                     load_type=lt.LoadTypes.Heating,
                                                     unit=lt.Units.Watt)

        self.P_th_min = P_th_min
        self.P_th_max = P_th_max
        self.eff_th_min = eff_th_min
        self.eff_th_max = eff_th_max
        self.temperature_max = temperature_max
        self.temperaturedelta = temperaturedelta

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
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
        p_th=cw*mass_out*(mass_out_temp-stsv.get_input_value(self.mass_inp_temp))

        stsv.set_output_value(self.p_th, p_th)  # efficiency
        stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # efficiency
        stsv.set_output_value(self.mass_out, mass_out)  # efficiency
        stsv.set_output_value(self.gas_demand, gas_power)  # gas consumption
