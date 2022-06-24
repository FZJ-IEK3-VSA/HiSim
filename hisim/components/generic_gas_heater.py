# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from dataclasses import dataclass
from dataclasses_json import dataclass_json

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""

@dataclass_json
@dataclass
class GasHeaterConfig:
    """
    Gas Heater Config
    """
    is_modulating : bool
    P_th_min : float           # [W]
    P_th_max : float           # [W]
    eff_th_min : float         # [-]
    eff_th_max : float         # [-]
    delta_T : float            # [°C]
    mass_flow_max :   float  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
    temperature_max :  float   # [°C]
    temperaturedelta : float   # [°C]
    power_max :float           # [W]

class GasHeater(Component):
    """
    Gets Control Signal and calculates on base of it Massflow and Temperature of Massflow
    """
    #Input
    ControlSignal = "ControlSignal" # at which Procentage is the GasHeater modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"

    #Output
    MassflowOutput= "Hot Water Energy Output"
    MassflowOutputTemperature= "MassflowOutputTemperature"
    GasDemand = "GasDemand"
    ThermalOutputPower="ThermalOutputPower"

    def __init__(self,my_simulation_parameters: SimulationParameters , config : GasHeaterConfig):
        super().__init__(name="GasHeater", my_simulation_parameters=my_simulation_parameters)
        self.control_signal: ComponentInput = self.add_input(self.ComponentName, GasHeater.ControlSignal, lt.LoadTypes.Any, lt.Units.Percent, True)
        self.mass_inp_temp: ComponentInput = self.add_input(self.ComponentName, GasHeater.MassflowInputTemperature, lt.LoadTypes.Water, lt.Units.Celsius, True)


        self.mass_out: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutput, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.mass_out_temp: ComponentOutput = self.add_output(self.ComponentName, GasHeater.MassflowOutputTemperature, lt.LoadTypes.Water, lt.Units.Celsius)
        self.gas_demand: ComponentOutput = self.add_output(self.ComponentName, GasHeater.GasDemand, lt.LoadTypes.Gas, lt.Units.kWh)
        self.p_th: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.ThermalOutputPower,
                                                     load_type=lt.LoadTypes.Heating,
                                                     unit=lt.Units.Watt)

        self.P_th_min = config.P_th_min
        self.P_th_max = config.power_max
        self.eff_th_min = config.eff_th_min
        self.eff_th_max = config.eff_th_max
        self.temperature_max = config.temperature_max
        self.temperaturedelta = config.temperaturedelta

    @staticmethod
    def get_default_config():
        config=GasHeaterConfig(
                    temperaturedelta = 10,
                    power_max = 12_000,
                    is_modulating = True,
                    P_th_min = 1_000  ,# [W]
                    P_th_max = 12_000 , # [W]
                    eff_th_min = 0.60  ,# [-]
                    eff_th_max = 0.90  ,# [-]
                    delta_T = 25,
                    mass_flow_max = 12_000 / (4180 * 25),  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
                    temperature_max = 80  # [°C])
                    )
        return config
    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
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
        mass_out=gas_power/(cw*self.temperaturedelta)
        p_th=cw*mass_out*(mass_out_temp-stsv.get_input_value(self.mass_inp_temp))

        stsv.set_output_value(self.p_th, gas_power)  # efficiency
        stsv.set_output_value(self.mass_out_temp, mass_out_temp)  # efficiency
        stsv.set_output_value(self.mass_out, mass_out)  # efficiency
        stsv.set_output_value(self.gas_demand, gas_power)  # gas consumption
