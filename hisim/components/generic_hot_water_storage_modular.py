""" Simple hot water storage implementation.

Energy bucket model: extracts energy, adds energy and converts back to temperatere.
The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as boiler with input:
hot water demand or as  buffer with input ThermalPowerToBuilding. Both options need input signal for heating power and have
one output: the hot water storage temperature.
"""
# clean
# Generic/Built-in
from typing import Optional, List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import numpy as np

# Owned
import hisim.component as cp
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
import hisim.dynamic_component as dycp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.loadprofilegenerator_connector import Occupancy
from hisim.components import generic_heat_pump_modular
from hisim.components import generic_heat_source
from hisim.components import controller_l1_generic_runtime
from hisim.components import generic_CHP
import hisim.log

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

    """Used in the HotWaterStorageClass defining the basics."""

    name: str
    use: lt.ComponentType
    source_weight: int
    volume: float
    surface: float
    u_value: float
    warm_water_temperature: float
    drain_water_temperature: float
    efficiency: float
    power: float

    @staticmethod
    def get_default_config_boiler():
        """ Returns default configuration for boiler. """
        config = StorageConfig(name='DHWBoiler', use=lt.ComponentType.BOILER, source_weight=1, volume=500,
                               surface=2.0, u_value=0.36, warm_water_temperature=50, drain_water_temperature=10,
                               efficiency=1, power=0)
        return config

    @staticmethod
    def get_default_config_buffer(power: float = 2000, volume: float=500) -> Any:
        """ Returns default configuration for buffer (radius:height = 1:4). """
        # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius = (volume * 1e-3 / (4 * np.pi)) ** (
            1 / 3
        )  # l to m^3 so that radius is given in m
        # cylinder surface area = floor and ceiling area + lateral surface
        surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)
        config = StorageConfig(
            name='Buffer', use=lt.ComponentType.BUFFER, source_weight=1, volume=0, surface=surface, u_value=0.36,
            warm_water_temperature=50, drain_water_temperature=10, efficiency=1, power=power)
        return config

    def compute_default_volume(self, time_in_seconds: float, temperature_difference_in_kelvin: float, multiplier: float) -> None:
        """ Computes default volume and surface from power and min idle time of heating system. """
        if self.use != lt.ComponentType.BUFFER:
            raise Exception( "Default volume can only be computed for buffer storage not for boiler.")

        energy_in_kilo_joule = self.power * time_in_seconds * 1e-3
        self.volume = energy_in_kilo_joule * multiplier / (temperature_difference_in_kelvin * 0.977 * 4.182)
         # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius = (self.volume * 1e-3 / (4 * np.pi))**(1 / 3)  # l to m^3 so that radius is given in m
        # cylinder surface area = floor and ceiling area + lateral surface
        self.surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)


class StorageState:

    """Data class saves the state of the simulation results."""

    def __init__(
        self,
        timestep: int = -1,
        volume_in_l: float = 200,
        temperature_in_kelvin: float = 273.15 + 50,
    ):
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
        """Replicates storage state."""
        return StorageState(self.timestep, self.volume_in_l, self.temperature_in_kelvin)

    def energy_from_temperature(self) -> float:
        """Converts temperature of storage (K) into energy contained in storage (kJ)."""
        # 0.977 is the density of water in kg/l
        # 4.182 is the specific heat of water in kJ / (K * kg)
        return (
            self.temperature_in_kelvin * self.volume_in_l * 0.977 * 4.182
        )  # energy given in kJ

    def set_temperature_from_energy(self, energy_in_kilo_joule):
        """Converts energy contained in storage (kJ) into temperature (K)."""
        # 0.977 is the density of water in kg/l
        # 4.182 is the specific heat of water in kJ / (K * kg)
        self.temperature_in_kelvin = energy_in_kilo_joule / (
            self.volume_in_l * 0.977 * 4.182
        )  # temperature given in K
        # filter for boiling water
        # no filtering -> this hides major problems - Noah
        if self.temperature_in_kelvin > 95 + 273.15:
            raise ValueError(
                "Water was boiling. This points towards a major problem in your model."
            )
        # filter for freezing water
        if self.temperature_in_kelvin < 2 + 273.15:
            raise ValueError(
                "Water in your storage tank was freezing. This points towards a major problem in your model."
            )

    def return_available_energy(self, heating: bool) -> float:
        """Returns available energy in (J).

        For heating up the building in winter. Here 30°C is set as the lower limit for the temperature in the buffer storage in winter.
        """
        return (self.temperature_in_kelvin - 273.15 - 25) * self.volume_in_l * 0.977 * 4.182 * 1e3


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
    ThermalPowerCHP = "ThermalPowerCHP"
    WaterConsumption = "WaterConsumption"
    L1DeviceSignal = "L1DeviceSignal"
    my_component_inputs: List[dycp.DynamicConnectionInput] = []
    my_component_outputs: List[dycp.DynamicConnectionOutput] = []

    # obligatory Outputs
    TemperatureMean = "TemperatureMean"

    # outputs for buffer storage
    PowerToBuilding = "PowerToBuilding"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: StorageConfig
    ):
        """Initializes instance of HotWaterStorage class."""

        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )

        self.build(config)

        # collect all heat inputs
        self.heat_to_buffer_inputs: List[cp.ComponentInput]

        # initialize Boiler State
        self.state = StorageState(
            volume_in_l=config.volume, temperature_in_kelvin=273 + 60
        )
        self.previous_state = self.state.clone()
        self.write_to_report()

        # inputs
        if self.use == lt.ComponentType.BOILER:
            self.water_consumption_c: cp.ComponentInput = self.add_input(
                self.component_name,
                self.WaterConsumption,
                lt.LoadTypes.WARM_WATER,
                lt.Units.LITER,
                mandatory=True,
            )
            self.add_default_connections(self.get_occupancy_default_connections())
            self.add_default_connections(self.get_utsp_default_connections())
        elif self.use == lt.ComponentType.BUFFER:
            self.l1_device_signal_c: cp.ComponentInput = self.add_input(
                self.component_name,
                self.L1DeviceSignal,
                lt.LoadTypes.ON_OFF,
                lt.Units.BINARY,
                mandatory=True,
            )
            self.add_default_connections(self.get_l1_default_connections())
        else:
            hisim.log.error("Type of hot water storage is not defined")

        self.thermal_power_delivered_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            mandatory=False,
        )
        self.thermal_power_chp_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerCHP,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            mandatory=False,
        )

        # Outputs
        self.temperature_mean_c: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureMean,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            postprocessing_flag=[lt.InandOutputType.STORAGE_CONTENT],
        )
        # Outputs
        self.power_to_building_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PowerToBuilding,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )

        self.add_default_connections(
            self.get_default_connections_from_generic_heat_pump_modular()
        )
        self.add_default_connections(self.get_heatsource_default_connections())
        self.add_default_connections(self.get_chp_default_connections())

    def get_occupancy_default_connections(self):
        """Sets occupancy default connections in hot water storage."""
        hisim.log.information(
            "setting occupancy default connections in hot water storage"
        )
        connections = []
        occupancy_classname = Occupancy.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.WaterConsumption,
                occupancy_classname,
                Occupancy.WaterConsumption,
            )
        )
        return connections

    def get_utsp_default_connections(self):
        """Sets occupancy default connections in hot water storage."""
        hisim.log.information("setting utsp default connections in hot water storage")
        connections = []
        utsp_classname = UtspLpgConnector.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.WaterConsumption,
                utsp_classname,
                UtspLpgConnector.WaterConsumption,
            )
        )
        return connections

    def get_l1_default_connections(self):
        """Sets L1 power default connections in hot water storage."""
        hisim.log.information(
            "setting L1 power default connections in hot water storage"
        )
        connections = []
        l1_classname = (
            controller_l1_generic_runtime.L1GenericRuntimeController.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.L1DeviceSignal,
                l1_classname,
                controller_l1_generic_runtime.L1GenericRuntimeController.L1DeviceSignal,
            )
        )
        return connections

    def get_default_connections_from_generic_heat_pump_modular(self):
        """Sets heat pump default connections in hot water storage."""
        hisim.log.information(
            "setting heat pump default connections in hot water storage"
        )
        connections = []
        heatpump_classname = generic_heat_pump_modular.ModularHeatPump.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerDelivered,
                heatpump_classname,
                generic_heat_pump_modular.ModularHeatPump.ThermalPowerDelivered,
            )
        )
        return connections

    def get_heatsource_default_connections(self):
        """Sets heat source default connections in hot water storage."""
        hisim.log.information(
            "setting heat source default connections in hot water storaage"
        )
        connections = []
        heatsource_classname = generic_heat_source.HeatSource.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerDelivered,
                heatsource_classname,
                generic_heat_source.HeatSource.ThermalPowerDelivered,
            )
        )
        return connections

    def get_chp_default_connections(self):
        """Sets chp default connections in hot water storage."""
        hisim.log.information("setting chp default connections in hot water storaage")
        connections = []
        chp_classname = generic_CHP.GCHP.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerCHP,
                chp_classname,
                generic_heat_source.HeatSource.ThermalPowerDelivered,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self, config: StorageConfig) -> None:
        """Initializes hot water storage instance."""

        self.name = config.name
        self.use = config.use
        self.source_weight = config.source_weight
        self.volume = config.volume
        self.surface = config.surface
        self.u_value = config.u_value
        self.efficiency = config.efficiency
        self.drain_water_temperature = config.drain_water_temperature
        self.warm_water_temperature = config.warm_water_temperature
        self.power = config.power


    def write_to_report(self):
        """Writes to report."""
        lines = []
        lines.append(f"Name: {self.name + str(self.source_weight)}")
        lines.append(f"Volume: {self.volume:4.0f} l")
        return lines

    def i_save_state(self):
        """Abstract. Gets called at the beginning of a timestep to save the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """Abstract. Restores the state of the component. Can be called many times while iterating."""
        self.state = self.previous_state.clone()

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates iteration of hot water storage."""

        thermal_energy_delivered = 0.0
        if self.thermal_power_delivered_channel.source_output is not None:
            thermal_energy_delivered = (
                thermal_energy_delivered
                + stsv.get_input_value(self.thermal_power_delivered_channel)
                * self.my_simulation_parameters.seconds_per_timestep
                * 1e-3
            )  # 1e-3 conversion J to kJ
        elif self.thermal_power_chp_channel.source_output is not None:
            thermal_energy_delivered = (
                thermal_energy_delivered
                + stsv.get_input_value(self.thermal_power_chp_channel)
                * self.my_simulation_parameters.seconds_per_timestep
                * 1e-3
            )  # 1e-3 conversion J to kJ
        heatconsumption: float = self.calculate_heat_consumption(
            stsv=stsv,
            thermal_energy_delivered=thermal_energy_delivered,
            timestep=timestep,
        )
        stsv.set_output_value(self.power_to_building_channel, heatconsumption)

        # constant heat loss of heat storage with the assumption that environment has 20°C = 293 K -> based on energy balance in kJ
        # heat gain due to heating of storage -> based on energy balance in kJ
        energy = self.state.energy_from_temperature()
        new_energy = (
            energy
            - (self.state.temperature_in_kelvin - 293)
            * self.surface
            * self.u_value
            * self.my_simulation_parameters.seconds_per_timestep
            * 1e-3
            - heatconsumption
            + thermal_energy_delivered
        )

        # convert new energy to new temperature
        self.state.set_temperature_from_energy(new_energy)

        # save outputs
        stsv.set_output_value(
            self.temperature_mean_c, self.state.temperature_in_kelvin - 273.15
        )

    def calculate_heat_consumption(
        self,
        stsv: cp.SingleTimeStepValues,
        thermal_energy_delivered: float,
        timestep: int,
    ) -> float:
        """Calculates the heat consumption."""
        if self.use == lt.ComponentType.BOILER:
            # heat loss due to hot water consumption -> base on energy balance in kJ
            # 0.977 density of water in kg/l
            # 4.182 specific heat of water in kJ K^(-1) kg^(-1)
            return stsv.get_input_value(self.water_consumption_c) \
                * (self.warm_water_temperature - self.drain_water_temperature) * 0.977 * 4.182
        elif self.use == lt.ComponentType.BUFFER:
            heatconsumption = stsv.get_input_value(self.l1_device_signal_c) \
                * self.power * self.my_simulation_parameters.seconds_per_timestep * 1e-3  # 1e-3 conversion J to kJ
            available_energy = self.state.return_available_energy(heating=True)\
                + thermal_energy_delivered
            if heatconsumption > available_energy:
                heatconsumption = max(available_energy, 0)
            return heatconsumption
        else:
            raise Exception("Modular storage must be defined either as buffer or as boiler.")
