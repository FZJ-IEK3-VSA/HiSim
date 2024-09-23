""" Simple hot water storage implementation: HotWaterStorage class together with state and configuration class.

Energy bucket model: extracts energy, adds energy and converts back to temperatere.
The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as
DHW hot water storage or as buffer storage.
"""

import importlib
from dataclasses import dataclass

# clean
# Generic/Built-in
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

# Owned
import hisim.component as cp
import hisim.log
from hisim import loadtypes as lt
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.components import (
    controller_l1_building_heating,
    generic_chp,
    configuration,
)
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry

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
class StorageConfig(cp.ConfigBase):
    """Used in the HotWaterStorageClass defining the basics."""

    building_name: str
    #: name of the hot water storage
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of use, either boiler or buffer
    use: lt.ComponentType
    #: volume of storage in m^3
    volume: float
    #: surface of storage in m^2
    surface: float
    #: u-value of storage in W/(K m^2)
    u_value: float
    #: energy of full cycle in kWh
    energy_full_cycle: Optional[float]
    #: power of heat source in kW
    power: float
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HotWaterStorage.get_full_classname()

    @classmethod
    def get_default_config_for_boiler(
        cls,
        name: str = "DHWBoiler",
        building_name: str = "BUI1",
    ) -> "StorageConfig":
        """Returns default configuration for boiler."""

        volume = 250
        radius = (volume * 1e-3 / (4 * np.pi)) ** (1 / 3)  # l to m^3 so that radius is given in m
        surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)
        config = StorageConfig(
            building_name=building_name,
            name=name,
            use=lt.ComponentType.BOILER,
            source_weight=1,
            volume=volume,
            surface=surface,
            u_value=0.36,
            energy_full_cycle=None,
            power=0,
            co2_footprint=0,  # Todo: check value
            cost=volume * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # SOURCE: VDI2067-1
            maintenance_cost_as_percentage_of_investment=0.02,  # SOURCE: VDI2067-1
        )
        return config

    @classmethod
    def get_scaled_config_for_boiler_to_number_of_apartments(
        cls,
        number_of_apartments: float,
        default_volume_in_liter: float = 250.0,
        name: str = "DHWBoiler",
        building_name: str = "BUI1",
    ) -> "StorageConfig":
        """Returns default configuration for boiler."""

        volume = default_volume_in_liter * max(number_of_apartments, 1)
        radius = (volume * 1e-3 / (4 * np.pi)) ** (1 / 3)  # l to m^3 so that radius is given in m
        surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)
        config = StorageConfig(
            building_name=building_name,
            name=name,
            use=lt.ComponentType.BOILER,
            source_weight=1,
            volume=volume,
            surface=surface,
            u_value=0.36,
            energy_full_cycle=None,
            power=0,
            co2_footprint=0,  # Todo: check value
            cost=volume * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # SOURCE: VDI2067-1
            maintenance_cost_as_percentage_of_investment=0.02,  # SOURCE: VDI2067-1
        )
        return config

    @staticmethod
    def get_default_config_buffer(
        name: str = "Buffer",
        power: float = 2000,
        volume: float = 500,
        building_name: str = "BUI1",
    ) -> Any:
        """Returns default configuration for buffer (radius:height = 1:4)."""
        # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius = (volume * 1e-3 / (4 * np.pi)) ** (1 / 3)  # l to m^3 so that radius is given in m
        # cylinder surface area = floor and ceiling area + lateral surface
        surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)
        config = StorageConfig(
            building_name=building_name,
            name=name,
            use=lt.ComponentType.BUFFER,
            source_weight=1,
            volume=0,
            surface=surface,
            u_value=0.36,
            energy_full_cycle=None,
            power=power,
            co2_footprint=100,  # Todo: check value
            cost=volume * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=100,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.02,  # SOURCE: VDI2067-1
        )
        return config

    def compute_default_volume(
        self,
        time_in_seconds: float,
        temperature_difference_in_kelvin: float,
        multiplier: float,
    ) -> None:
        """Computes default volume and surface from power and min idle time of heating system."""
        if self.use != lt.ComponentType.BUFFER:
            raise Exception("Default volume can only be computed for buffer storage not for boiler.")

        energy_in_kilo_joule = self.power * time_in_seconds * 1e-3
        self.volume = energy_in_kilo_joule * multiplier / (temperature_difference_in_kelvin * 0.977 * 4.182)
        # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius = (self.volume * 1e-3 / (4 * np.pi)) ** (1 / 3)  # l to m^3 so that radius is given in m
        # cylinder surface area = floor and ceiling area + lateral surface
        self.surface = 2 * radius * radius * np.pi + 2 * radius * np.pi * (4 * radius)

    def compute_default_cycle(self, temperature_difference_in_kelvin: float) -> None:
        """Computes energy needed to heat storage from lower threshold of hysteresis to upper threshold."""
        self.energy_full_cycle = self.volume * temperature_difference_in_kelvin * 0.977 * 4.182 / 3600


class StorageState:
    """Data class saves the state of the simulation results."""

    def __init__(
        self,
        timestep: int = -1,
        volume_in_l: float = 200,
        temperature_in_kelvin: float = 273.15 + 50,
    ):
        """Initializes instance of class Storage State.

        :param timestep: timestep of simulation
        :type timestep: int, optional
        :param volume_in_l: volume of hot water storage in liters
        :type volume_in_l: float
        :param temperature_in_kelvin: temperature of hot water storage in Kelvin
        :type temperature_in_kelvin: float
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
        return self.temperature_in_kelvin * self.volume_in_l * 0.977 * 4.182  # energy given in kJ

    def set_temperature_from_energy(self, energy_in_kilo_joule: float) -> None:
        """Converts energy contained in storage (kJ) into temperature (K)."""
        # 0.977 is the density of water in kg/l
        # 4.182 is the specific heat of water in kJ / (K * kg)
        self.temperature_in_kelvin = energy_in_kilo_joule / (self.volume_in_l * 0.977 * 4.182)  # temperature given in K
        # filter for boiling water
        # no filtering -> this hides major problems - Noah
        if self.temperature_in_kelvin > 95 + 273.15:
            raise ValueError(StorageConfig.building_name +
                "Water was boiling. This points towards a major problem in your model. Increasing the storage volume may solve the issue"
                             )
        # filter for freezing water
        if self.temperature_in_kelvin < 2 + 273.15:
            raise ValueError(StorageConfig.building_name +
                "Water in your storage tank was freezing. This points towards a major problem in your model."
                             )

    def return_available_energy(self) -> float:
        """Returns available energy in (kJ).

        For heating up the building in winter. Here 30°C is set as the lower limit for the temperature in the buffer storage in winter.
        """
        return (self.temperature_in_kelvin - 273.15 - 25) * self.volume_in_l * 0.977 * 4.182


# class HotWaterStorage(dycp.DynamicComponent):
class HotWaterStorage(cp.Component):
    """Simple hot water storage implementation.

    Energy bucket model: extracts energy, adds energy and converts back to temperatere.
    The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as boiler with input:
    WaterConsumption or as  buffer with input ThermalPowerDelivered from building component. Both options need input signal for heating power and have
    two outputs: the hot water storage temperature, and the power extracted from the hot water storage.

    Components to connect to:
    (1a) Building Controller(controller_l1_building_heating) - if buffer
    (1b) Occupancy Profile (either loadprofilegenerator_connector or loadprofilegenerator_utsp_connector) - if boiler
    (2a) Heat Source (generic_heat_source)
    (2b) Heat Pump (generic_heat_pump_modular)
    (2c) CHP (generic_CHP) - optional - if CHP additionally heats bufffer
    """

    # Inputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ThermalPowerCHP = "ThermalPowerCHP"
    WaterConsumption = "WaterConsumption"
    HeatControllerTargetPercentage = "HeatControllerTargetPercentage"
    # my_component_inputs: List[dycp.DynamicConnectionInput] = []
    # my_component_outputs: List[dycp.DynamicConnectionOutput] = []

    # obligatory Outputs
    TemperatureMean = "TemperatureMean"

    # outputs for buffer storage
    PowerFromHotWaterStorage = "PowerFromHotWaterStorage"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: StorageConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ):
        """Initializes instance of HotWaterStorage class."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build(config)

        # collect all heat inputs
        self.heat_to_buffer_inputs: List[cp.ComponentInput]

        # initialize Boiler State
        self.state = StorageState(volume_in_l=config.volume, temperature_in_kelvin=273 + 60)
        self.previous_state = self.state.clone()
        self.write_to_report()

        # inputs
        if self.use == lt.ComponentType.BOILER:
            self.water_consumption_channel: cp.ComponentInput = self.add_input(
                self.component_name,
                self.WaterConsumption,
                lt.LoadTypes.WARM_WATER,
                lt.Units.LITER,
                mandatory=True,
            )
            self.add_default_connections(self.get_utsp_default_connections())
        elif self.use == lt.ComponentType.BUFFER:
            self.heat_controller_target_percentage_channel: cp.ComponentInput = self.add_input(
                self.component_name,
                self.HeatControllerTargetPercentage,
                lt.LoadTypes.ON_OFF,
                lt.Units.BINARY,
                mandatory=True,
            )
            self.add_default_connections(self.get_heating_controller_default_connections())
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
        self.temperature_mean_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureMean,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            postprocessing_flag=[lt.InandOutputType.STORAGE_CONTENT],
            output_description="Temperature Mean",
        )
        # Outputs
        self.power_from_water_storage_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PowerFromHotWaterStorage,
            lt.LoadTypes.HEATING,
            lt.Units.KILOJOULE,
            postprocessing_flag=[
                lt.InandOutputType.DISCHARGE,
                self.use,
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description="Power transfered to Building or Hot Water Pipe.",
        )

        self.add_default_connections(self.get_default_connections_from_generic_heat_pump_modular())
        self.add_default_connections(self.get_heatsource_default_connections())
        self.add_default_connections(self.get_chp_default_connections())

    def get_utsp_default_connections(self):
        """Sets occupancy default connections in hot water storage."""

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

    def get_default_connections_from_generic_heat_pump_modular(self):
        """Sets heat pump default connections in hot water storage."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_heat_pump_modular"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "ModularHeatPump")

        connections = []
        heatpump_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerDelivered,
                heatpump_classname,
                component_class.ThermalPowerDelivered,
            )
        )
        return connections

    def get_heatsource_default_connections(self):
        """Sets heat source default connections in hot water storage."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_heat_source"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HeatSource")

        connections = []
        heatsource_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerDelivered,
                heatsource_classname,
                component_class.ThermalPowerDelivered,
            )
        )
        return connections

    def get_chp_default_connections(self):
        """Sets chp default connections in hot water storage."""

        connections = []
        chp_classname = generic_chp.SimpleCHP.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.ThermalPowerCHP,
                chp_classname,
                generic_chp.SimpleCHP.ThermalPowerOutputBoiler,
            )
        )
        return connections

    def get_heating_controller_default_connections(self):
        """Sets heating controller default connections in hot water storage."""

        connections = []
        heating_controller_classname = controller_l1_building_heating.L1BuildingHeatController.get_classname()
        connections.append(
            cp.ComponentConnection(
                HotWaterStorage.HeatControllerTargetPercentage,
                heating_controller_classname,
                controller_l1_building_heating.L1BuildingHeatController.HeatControllerTargetPercentage,
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
        self.drain_water_temperature = configuration.HouseholdWarmWaterDemandConfig.freshwater_temperature
        self.warm_water_temperature = (
            configuration.HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - configuration.HouseholdWarmWaterDemandConfig.temperature_difference_hot
        )
        self.power = config.power
        self.config = config

    def write_to_report(self):
        """Writes to report."""
        return self.config.get_string_dict()

    def i_save_state(self):
        """Abstract. Gets called at the beginning of a timestep to save the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """Abstract. Restores the state of the component. Can be called many times while iterating."""
        self.state = self.previous_state.clone()

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates iteration of hot water storage."""

        thermal_energy_delivered = 0.0
        if self.thermal_power_delivered_channel.source_output is not None:
            thermal_energy_delivered = (
                thermal_energy_delivered
                + stsv.get_input_value(self.thermal_power_delivered_channel)
                * self.my_simulation_parameters.seconds_per_timestep
                * 1e-3
            )  # 1e-3 conversion J to kJ
        if self.thermal_power_chp_channel.source_output is not None:
            thermal_energy_delivered = (
                thermal_energy_delivered
                + stsv.get_input_value(self.thermal_power_chp_channel)
                * self.my_simulation_parameters.seconds_per_timestep
                * 1e-3
            )  # 1e-3 conversion J to kJ
        heatconsumption: float = self.calculate_heat_consumption(
            stsv=stsv,
            thermal_energy_delivered=thermal_energy_delivered,
        )
        stsv.set_output_value(self.power_from_water_storage_channel, heatconsumption)

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
        stsv.set_output_value(self.temperature_mean_channel, self.state.temperature_in_kelvin - 273.15)

    def calculate_heat_consumption(
        self,
        stsv: cp.SingleTimeStepValues,
        thermal_energy_delivered: float,
    ) -> float:
        """Calculates the heat consumption."""
        if self.use == lt.ComponentType.BOILER:
            # heat loss due to hot water consumption -> base on energy balance in kJ
            # 0.977 density of water in kg/l
            # 4.182 specific heat of water in kJ K^(-1) kg^(-1)
            return (
                stsv.get_input_value(self.water_consumption_channel)
                * (self.warm_water_temperature - self.drain_water_temperature)
                * 0.977
                * 4.182
            )
        if self.use == lt.ComponentType.BUFFER:
            heatconsumption = (
                stsv.get_input_value(self.heat_controller_target_percentage_channel)
                * self.power
                * self.my_simulation_parameters.seconds_per_timestep
                * 1e-3
            )  # 1e-3 conversion J to kJ
            available_energy = self.state.return_available_energy() + thermal_energy_delivered
            if heatconsumption > available_energy:
                heatconsumption = max(available_energy, 0)
            return heatconsumption
        raise Exception("Modular storage must be defined either as buffer or as boiler.")

    @staticmethod
    def get_cost_capex(config: StorageConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.cost / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.cost,
            device_co2_footprint_in_kg=config.co2_footprint,
            lifetime_in_years=config.lifetime,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for DHW Storage."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.STORAGE_DOMESTIC_HOT_WATER
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
