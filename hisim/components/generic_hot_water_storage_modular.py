""" Simple hot water storage implementation.

Energy bucket model: extracts energy, adds energy and converts back to temperatere.
The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as boiler with input:
hot water demand or as  buffer with input ThermalPowerToBuilding. Both options need input signal for heating power and have
one output: the hot water storage temperature.
"""

# Generic/Built-in
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import matplotlib
import seaborn
import numpy as np

# Owned
import hisim.component as cp
import hisim.dynamic_component as dycp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim.components import generic_heat_pump_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_generic_runtime
import hisim.log
seaborn.set(style='ticks')
font = {'family': 'normal',
        'size': 24}

matplotlib.rc('font', **font)

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class StorageConfig:

    """ Used in the HotWaterStorageClass defining the basics. """

    name: str
    use: lt.ComponentType
    source_weight: int
    volume: float
    surface: float
    u_value: float
    warm_water_temperature: float
    drain_water_temperature: float
    efficiency: float

    def __init__(self, name: str, use: lt.ComponentType, source_weight: int, volume: float, surface: float,
                 u_value: float, warm_water_temperature: float, drain_water_temperature: float, efficiency: float):
        """ Initializes Storage Config. """
        self.name = name
        self.use = use
        self.source_weight = source_weight
        self.volume = volume
        self.surface = surface
        self.u_value = u_value
        self.warm_water_temperature = warm_water_temperature + 273.15
        self.drain_water_temperature = drain_water_temperature + 273.15
        self.efficiency = efficiency


class StorageState:

    """ Data class saves the state of the simulation results. """

    def __init__(self, timestep: int = -1, volume_in_l: float = 200, temperature_in_kelvin: float = 273.15 + 50):
        """Initializes instance of class Storage State.

        Parameters
        ----------
        timestep: int, optional
            Timestep of simulation. The default is 0.
        volume_in_l: float
            Volume of hot water storage in liters.
        temperature_in_kelvin: float
            Temperature of hot water storage in Kelvin.

        """
        self.timestep = timestep
        self.temperature_in_kelvin = temperature_in_kelvin
        self.volume_in_l = volume_in_l

    def clone(self):
        """ Replicates storage state. """
        return StorageState(self.timestep, self.volume_in_l, self.temperature_in_kelvin)

    def energy_from_temperature(self) -> float:
        """ Converts temperature of storage (K) into energy contained in storage (kJ). """
        # 0.977 is the density of water in kg/l
        # 4.182 is the specific heat of water in kJ / (K * kg)
        return self.temperature_in_kelvin * self.volume_in_l * 0.977 * 4.182  # energy given in kJ

    def set_temperature_from_energy(self, energy_in_kilo_joule):
        """ Converts energy contained in storage (kJ) into temperature (K). """
        # 0.977 is the density of water in kg/l
        # 4.182 is the specific heat of water in kJ / (K * kg)
        self.temperature_in_kelvin = energy_in_kilo_joule / (self.volume_in_l * 0.977 * 4.182)  # temperature given in K


class HotWaterStorage(dycp.DynamicComponent):

    """Simple hot water storage implementation.

    Energy bucket model: extracts energy, adds energy and converts back to temperatere.
    The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as boiler with input:
    hot water demand or as  buffer with input ThermalPowerToBuilding. Both options need input signal for heating power and have
    one output: the hot water storage temperature.

    Parameters
    ----------
    name: str, optional
        Name of hot water storage within simulation. The default is 'Boiler'.
    use: lt.ComponentType, optional
        Use of hot water storage either as Boiler or as Buffer
    source_weight: int, optional
        Weight of component, relevant if there is more than one hot water storage, defines hierachy in control. The default is 1.
    volume: float, optional
        Volume of storage in liters. The default is 200 l.
    surface: float, optional
        Surface of storage in square meters. The default is 1.2 m**3
    u_value: float, optional
        u-value of stroage in W m**(-2) K**(-1). The default is 0.36 W / (m*m*K)
    warm_water_temperature: float, optional
        Set temperature of hot water used by residents in °C. The default is 50 °C.
    drain_water_temperature: float, optional
        Temperature of cold water from pipe in °C. The default is 10 °C.
    efficiency: float, optional
        Efficiency of the heating rod / hydrogen oven. The default is 1.

    """

    # Inputs
    ThermalPowerDelivered = "ThermalPowerDelivered"  # either thermal energy delivered
    WaterConsumption = "WaterConsumption"
    HeatConsumption = "HeatConsumption"
    my_component_inputs: List[dycp.DynamicConnectionInput] = []
    my_component_outputs: List[dycp.DynamicConnectionOutput] = []

    # obligatory Outputs
    TemperatureMean = "TemperatureMean"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: StorageConfig):
        """ Initializes instance of HotWaterStorage class. """

        super().__init__(my_component_inputs=self.my_component_inputs, my_component_outputs=self.my_component_outputs,
                         name=config.name + str(config.source_weight), my_simulation_parameters=my_simulation_parameters)

        self.build(config)

        # initialize Boiler State
        self.state = StorageState(volume_in_l=config.volume, temperature_in_kelvin=273 + 60)
        self.previous_state = self.state.clone()
        self.write_to_report()

        # inputs
        if self.use == lt.ComponentType.BOILER:
            self.water_consumption_c: cp.ComponentInput = self.add_input(self.component_name, self.WaterConsumption, lt.LoadTypes.WARM_WATER,
                                                                         lt.Units.LITER, mandatory=True)
            self.add_default_connections(Occupancy, self.get_occupancy_default_connections())
        elif self.use == lt.ComponentType.BUFFER:
            self.heat_consumption_c: cp.ComponentInput = self.add_input(self.component_name, self.HeatConsumption, lt.LoadTypes.HEATING,
                                                                        lt.Units.WATT, mandatory=True)
            self.add_default_connections(controller_l1_generic_runtime.L1_Controller, self.get_l1_power_default_connections())
        else:
            hisim.log.error('Type of hot water storage is not defined')

        self.thermal_power_delivered_c: cp.ComponentInput = self.add_input(self.component_name, self.ThermalPowerDelivered,
                                                                           lt.LoadTypes.HEATING, lt.Units.WATT, mandatory=False)

        # Outputs
        self.temperature_mean_c: cp.ComponentOutput = self.add_output(self.component_name, self.TemperatureMean,
                                                                      lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

        self.add_default_connections(generic_heat_pump_modular.HeatPump, self.get_heatpump_default_connections())
        self.add_default_connections(generic_heat_source.HeatSource, self.get_heatpump_default_connections())

    def get_occupancy_default_connections(self):
        """ Sets occupancy default connections in hot water storage. """
        hisim.log.information("setting occupancy default connections in hot water storage")
        connections = []
        occupancy_classname = Occupancy.get_classname()
        connections.append(cp.ComponentConnection(HotWaterStorage.WaterConsumption, occupancy_classname,
                                                  Occupancy.WaterConsumption))
        return connections
    
    def get_l1_power_default_connections(self):
        """ Sets L1 power default connections in hot water storage. """
        hisim.log.information("setting L1 power default connections in hot water storage")
        connections = []
        l1_classname = controller_l1_generic_runtime.L1_Controller.get_classname()
        connections.append(cp.ComponentConnection(HotWaterStorage.HeatConsumption, l1_classname,
                                                  controller_l1_generic_runtime.L1_Controller.l1_DeviceSignal))
        return connections

    def get_heatpump_default_connections(self):
        """ Sets heat pump default connections in hot water storage. """
        hisim.log.information("setting heat pump default connections in hot water storage")
        connections = []
        heatpump_classname = generic_heat_pump_modular.HeatPump.get_classname()
        connections.append(cp.ComponentConnection(HotWaterStorage.ThermalPowerDelivered, heatpump_classname,
                                                  generic_heat_pump_modular.HeatPump.ThermalPowerDelivered))
        return connections

    def get_heatsource_default_connections(self):
        """ Sets heat source default connections in hot water storage. """
        hisim.log.information("setting heat source default connections in hot water storaage")
        connections = []
        heatsource_classname = generic_heat_source.HeatSource.get_classname()
        connections.append(cp.ComponentConnection(HotWaterStorage.ThermalPowerDelivered, heatsource_classname,
                                                  generic_heat_source.HeatSource.ThermalPowerDelivered))
        return connections

    @staticmethod
    def get_default_config_boiler():
        """ Returns default configuration for boiler. """
        config = StorageConfig(name='Boiler', use=lt.ComponentType.BOILER, source_weight=1, volume=200,
                               surface=2.0, u_value=0.36, warm_water_temperature=50, drain_water_temperature=10, efficiency=1)
        return config

    @staticmethod
    def get_default_config_buffer(volume:float):
        """ Returns default configuration for buffer (radius:height = 1:4)"""
        r = ( volume * 1e-3 / ( 4 * np.pi ) )**( 1 / 3 )
        config = StorageConfig(name='Buffer', use=lt.ComponentType.BUFFER, source_weight=1, volume=800,
                               surface=6*r*r*np.pi, u_value=0.36, warm_water_temperature=50, drain_water_temperature=10, efficiency=1)
        return config

    def build(self, config: StorageConfig):
        """ Initializes hot water storage instance. """

        self.name = config.name
        self.use = config.use
        self.source_weight = config.source_weight
        self.volume = config.volume
        self.surface = config.surface
        self.u_value = config.u_value
        self.efficiency = config.efficiency
        self.drain_water_temperature = config.drain_water_temperature
        self.warm_water_temperature = config.warm_water_temperature

    def write_to_report(self):
        """ Writes to report. """
        lines = []
        lines.append("Name: {}".format(self.name + str(self.source_weight)))
        lines.append("Volume: {:4.0f} l".format(self.volume))
        return lines

    def i_save_state(self):
        """ Abstract. Gets called at the beginning of a timestep to save the state. """
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """ Abstract. Restores the state of the component. Can be called many times while iterating. """
        self.state = self.previous_state.clone()

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):
        """ Simulates iteration of hot water storage. """

        # Retrieves inputs
        if self.use == lt.ComponentType.BOILER:
            # heat loss due to hot water consumption -> base on energy balance in kJ
            # 0.977 density of water in kg/l
            # 4.182 specific heat of water in kJ K^(-1) kg^(-1)
            heatconsumption = stsv.get_input_value(self.water_consumption_c) *\
                (self.warm_water_temperature - self.drain_water_temperature) * 0.977 * 4.182
        elif self.use == lt.ComponentType.BUFFER:
            heatconsumption = stsv.get_input_value(self.heat_consumption_c) *\
                self.my_simulation_parameters.seconds_per_timestep * 1e-3  # 1e-3 conversion J to kJ
                
        if self.thermal_power_delivered_c.source_output is not None:
            thermal_power_delivered = stsv.get_input_value(self.thermal_power_delivered_c)\
                * self.my_simulation_parameters.seconds_per_timestep * 1e-3  # 1e-3 conversion J to kJ
        else:
            thermal_power_delivered = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.HEAT_TO_BUFFER])) \
                * self.my_simulation_parameters.seconds_per_timestep * 1e-3  # 1e-3 conversion J to kJ
        # constant heat loss of heat storage with the assumption that environment has 20°C = 293 K -> based on energy balance in kJ
        # heat gain due to heating of storage -> based on energy balance in kJ
        energy = self.state.energy_from_temperature()
        new_energy = energy - (self.state.temperature_in_kelvin - 293) * self.surface * self.u_value *\
            self.my_simulation_parameters.seconds_per_timestep * 1e-3 - heatconsumption + thermal_power_delivered

        # convert new energy to new temperature
        self.state.set_temperature_from_energy(new_energy)

        # save outputs
        stsv.set_output_value(self.temperature_mean_c, self.state.temperature_in_kelvin - 273.15)
