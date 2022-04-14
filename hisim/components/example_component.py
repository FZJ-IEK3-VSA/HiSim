# Generic/Built-in
import copy
import numpy as np
from typing import List, Optional
# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.utils import HISIMPATH
from hisim import loadtypes as lt
from hisim.utils import load_smart_appliance
from hisim import utils
import pdb
from hisim.simulationparameters import SimulationParameters


__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class Dummy(Component):
    """
    Component component the supports multiple
    dummy values for fictitious scenarios. The
    values passed to the constructor are taken
    as constants to build the load profile for
    the entire simulation duration

    Parameters
    ----------
    electricity : float
        Constant to define electricity output profile
    heat : float
        Constant to define heat output profile
    capacity : float
        Stored energy when starting the simulation
    initial_temperature : float
        Initial temperature when starting the simulation
    sim_params: cp.SimulationParameters
        Simulation parameters used by the setup function:
    """
    ThermalEnergyDelivered = "ThermalEnergyDelivered"

    # Outputs
    ElectricityOutput = "ElectricityOutput"
    TemperatureMean = "Residence Temperature"
    StoredEnergy="StoredEnergy"

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                electricity=None,
                 heat=None,
                 capacity=None,
                 initial_temperature=None,
                 ):
        super().__init__(name="Dummy", my_simulation_parameters=my_simulation_parameters)
        self.capacity:float
        self.initial_temperature:float
        self.build(electricity=electricity,
                   heat=heat,
                   capacity=capacity,
                   initial_temperature=initial_temperature)

        self.thermal_energy_deliveredC : ComponentInput = self.add_input(self.ComponentName,
                                                                         self.ThermalEnergyDelivered,
                                                                         lt.LoadTypes.Heating,
                                                                         lt.Units.Watt,
                                                                         False)

        self.t_mC : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.TemperatureMean,
                                                      lt.LoadTypes.Temperature,
                                                      lt.Units.Celsius)

        self.electricity_outputC: ComponentOutput = self.add_output(self.ComponentName,
                                                               self.ElectricityOutput,
                                                               lt.LoadTypes.Electricity,
                                                               lt.Units.Watt)
        self.stored_energyC: ComponentOutput = self.add_output(self.ComponentName,
                                                               self.StoredEnergy,
                                                               lt.LoadTypes.Heating,
                                                               lt.Units.Watt)
        self.temperature:float = -300


    def build(self, electricity:Optional[float], heat:float, capacity:Optional[float], initial_temperature:Optional[float]):
        self.time_correction_factor:float = 1 / self.my_simulation_parameters.seconds_per_timestep
        self.seconds_per_timestep:float = self.my_simulation_parameters.seconds_per_timestep

        if electricity is None:
            self.electricity_output:float = - 1E3
        else:
            self.electricity_output = - 1E3 * electricity


        if capacity is None:
            self.capacity = 45 * 121.2
        else:
            self.capacity = capacity

        if initial_temperature is None:
            self.temperature = 25
            self.initial_temperature = 25
        else:
            self.temperature = initial_temperature
            self.initial_temperature = initial_temperature
        self.previous_temperature = self.temperature


    def write_to_report(self):
        lines:List =[]
        return lines

    def i_save_state(self):
        self.previous_temperature = self.temperature

    def i_restore_state(self):
        self.temperature = self.previous_temperature


    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool):
        electricity_output:float = 0
        if timestep >= 60*6 and timestep < 60*9:
            electricity_output = self.electricity_output
        elif timestep >= 60*15 and timestep < 60*18:
            electricity_output = - self.electricity_output

        stsv.set_output_value(self.electricity_outputC, electricity_output)

        if timestep <= 60*12:
            thermal_delivered_energy = 0
            temperature:float = self.initial_temperature
            current_stored_energy = ( self.initial_temperature + 273.15) * self.capacity
        else:
            thermal_delivered_energy = stsv.get_input_value(self.thermal_energy_deliveredC)
            previous_stored_energy = (self.previous_temperature + 273.15) * self.capacity
            current_stored_energy = previous_stored_energy + thermal_delivered_energy
            self.temperature = current_stored_energy / self.capacity - 273.15
            temperature = self.temperature

        #thermal_delivered_energy = 0
        #temperature = self.initial_temperature
        #current_stored_energy = ( self.initial_temperature + 273.15) * self.capacity
        #    else:
        #thermal_delivered_energy = stsv.get_input_value(self.thermal_energy_deliveredC)
        #previous_stored_energy = (self.previous_temperature + 273.15) * self.capacity
        #current_stored_energy = previous_stored_energy + thermal_delivered_energy
        #self.temperature = current_stored_energy / self.capacity - 273.15
        #temperature = self.temperature

        stsv.set_output_value(self.stored_energyC, current_stored_energy)
        stsv.set_output_value(self.t_mC, temperature)


