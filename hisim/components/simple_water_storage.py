"""Simple Water Storage Module for dhw storage and hot water storage for heating."""

# clean
# Owned
import importlib
from dataclasses import dataclass
from typing import List, Any, Tuple, Union
from enum import IntEnum
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

import hisim.component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import (
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass,
)
from hisim.components.configuration import PhysicsConfig
from hisim.components import configuration
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry, KpiHelperClass

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""


class HotWaterStorageSizingEnum(IntEnum):
    """Set Simple Hot Water Storage sizing options."""

    SIZE_ACCORDING_TO_HEAT_PUMP = 1
    SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM = 2
    SIZE_ACCORDING_TO_GAS_HEATER = 3


class PositionHotWaterStorageInSystemSetup(IntEnum):
    """Set Simple Hot Water Storage Position options."""

    PARALLEL_TO_HEAT_PUMP = 1
    SERIE_TO_HEAT_PUMP = 2


@dataclass_json
@dataclass
class SimpleHotWaterStorageConfig(cp.ConfigBase):
    """Configuration of the SimpleHotWaterStorage class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleHotWaterStorage.get_full_classname()

    building_name: str
    name: str
    volume_heating_water_storage_in_liter: float
    heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float
    heat_exchanger_is_present: bool
    position_hot_water_storage_in_system: Union[PositionHotWaterStorageInSystemSetup, int]
    # it should be checked how much energy the storage lost during the simulated period (see guidelines below, p.2, accepted loss in kWh/days)
    # https://www.bdh-industrie.de/fileadmin/user_upload/ISH2019/Infoblaetter/Infoblatt_Nr_74_Energetische_Bewertung_Warmwasserspeicher.pdf
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float

    @classmethod
    def get_default_simplehotwaterstorage_config(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHotWaterStorageConfig":
        """Get a default simplehotwaterstorage config."""
        volume_heating_water_storage_in_liter: float = 500
        position_hot_water_storage_in_system: Union[PositionHotWaterStorageInSystemSetup, int] = (
            PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_PUMP
        )
        config = SimpleHotWaterStorageConfig(
            building_name=building_name,
            name="SimpleHotWaterStorage",
            volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=2.0,
            heat_exchanger_is_present=True,  # until now stratified mode is causing problems, so heat exchanger mode is recommended
            position_hot_water_storage_in_system=position_hot_water_storage_in_system,
            co2_footprint=100,  # Todo: check value
            cost=volume_heating_water_storage_in_liter * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=25,  # value from emission_factors_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.0,  # Todo: set correct value
        )
        return config

    @classmethod
    def get_scaled_hot_water_storage(
        cls,
        max_thermal_power_in_watt_of_heating_system: float,
        name: str = "SimpleHotWaterStorage",
        building_name: str = "BUI1",
        temperature_difference_between_flow_and_return_in_celsius: float = 7.0,
        sizing_option: HotWaterStorageSizingEnum = HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM,
    ) -> "SimpleHotWaterStorageConfig":
        """Gets a default storage with scaling according to heating load of the building_name.

        The information for scaling the buffer storage is taken from the heating system guidelines from Buderus:
        https://www.baunetzwissen.de/heizung/fachwissen/speicher/dimensionierung-von-pufferspeichern-161296

        - If the heating system is a heat pump -> use formular:
        buffer storage size [m3] =
        (max. thermal power of heat pump [kW]* bridging time [h])
        /
        (spec. heat capacity water [Wh/(kg*K)]* temperature difference flow-return [K])
        with bridging time = 1h
        You can also check the paper:
        https://www.sciencedirect.com/science/article/pii/S2352152X2201533X?via%3Dihub.

        - If the heating system is something else (e.g. gasheater, ...), use approximation: 60 l per kW thermal power.
        """

        # if the used heating system is a heat pump use formular
        if sizing_option == HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP:
            volume_heating_water_storage_in_liter: float = (
                max_thermal_power_in_watt_of_heating_system
                * 1e-3
                / (
                    PhysicsConfig.water_specific_heat_capacity_in_watthour_per_kilogramm_per_kelvin
                    * temperature_difference_between_flow_and_return_in_celsius
                )
            ) * 1000  # 1m3 = 1000l

        # otherwise use approximation: 60l per kw thermal power
        elif sizing_option == HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM:
            volume_heating_water_storage_in_liter = max_thermal_power_in_watt_of_heating_system / 1e3 * 60

        # or for gas heaters make hws smaller because gas heaters are a bigger inertia than heat pump
        elif sizing_option == HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER:
            volume_heating_water_storage_in_liter = max_thermal_power_in_watt_of_heating_system / 1e3 * 20

        else:
            raise ValueError(f"Sizing option for Simple Hot Water Storage {sizing_option} is unvalid.")

        position_hot_water_storage_in_system: Union[PositionHotWaterStorageInSystemSetup, int] = (
            PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_PUMP
        )

        config = SimpleHotWaterStorageConfig(
            building_name=building_name,
            name=name,
            volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=2.0,
            heat_exchanger_is_present=True,  # until now stratified mode is causing problems, so heat exchanger mode is recommended
            position_hot_water_storage_in_system=position_hot_water_storage_in_system,
            co2_footprint=100,  # Todo: check value
            cost=volume_heating_water_storage_in_liter * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=100,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.0,  # Todo: set correct value
        )
        return config


@dataclass_json
@dataclass
class SimpleHotWaterStorageControllerConfig(cp.ConfigBase):
    """Configuration of the SimpleHotWaterStorageController class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleHotWaterStorageController.get_full_classname()

    building_name: str
    name: str

    @classmethod
    def get_default_simplehotwaterstoragecontroller_config(
        cls,
    ) -> Any:
        """Get a default simplehotwaterstorage controller config."""
        config = SimpleHotWaterStorageControllerConfig(
            building_name="BUI1",
            name="SimpleHotWaterStorageController",
        )
        return config


@dataclass_json
@dataclass
class SimpleDHWStorageConfig(cp.ConfigBase):
    """Configuration of the SimpleHotWaterStorage class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleDHWStorage.get_full_classname()

    building_name: str
    name: str
    volume_heating_water_storage_in_liter: float
    heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float

    @classmethod
    def get_default_simpledhwstorage_config(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleDHWStorageConfig":
        """Get a default simplehotwaterstorage config."""
        volume_heating_water_storage_in_liter: float = 250

        config = SimpleDHWStorageConfig(
            building_name=building_name,
            name="DHWStorage",
            volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=0.36,
            co2_footprint=100,  # Todo: check value
            cost=volume_heating_water_storage_in_liter * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=25,  # value from emission_factors_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.0,  # Todo: set correct value
        )
        return config

    @classmethod
    def get_scaled_dhw_storage(
        cls,
        number_of_apartments: int = 1,
        default_volume_in_liter: float = 250.0,
        name: str = "DHWStorage",
        building_name: str = "BUI1",
    ) -> "SimpleDHWStorageConfig":
        """Gets a default storage with scaling according to number of apartments."""

        # if the used heating system is a heat pump use formular

        volume = default_volume_in_liter * max(number_of_apartments, 1)
        config = SimpleDHWStorageConfig(
            building_name=building_name,
            name=name,
            volume_heating_water_storage_in_liter=volume,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=0.36,
            co2_footprint=100,  # Todo: check value
            cost=volume * 14.51,  # value from emission_factros_and_costs_devices.csv
            lifetime=25,  # value from emission_factors_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.0,  # Todo: set correct value
        )
        return config


@dataclass
class SimpleWaterStorageState:
    """SimpleHotWaterStorageState class."""

    mean_water_temperature_in_celsius: float = 25.0
    temperature_loss_in_celsius_per_timestep: float = 0.0
    heat_loss_in_watt: float = 0.0

    def self_copy(self):
        """Copy the Simple Hot Water Storage State."""
        return SimpleWaterStorageState(
            self.mean_water_temperature_in_celsius,
            self.temperature_loss_in_celsius_per_timestep,
            self.heat_loss_in_watt,
        )


class SimpleWaterStorage(cp.Component):
    """SimpleWaterStorage class with generic functions."""

    @utils.measure_execution_time
    def __init__(self, my_simulation_parameters: SimulationParameters, name: str, my_config: cp.ConfigBase,
                 my_display_config: DisplayConfig) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(name, my_simulation_parameters, my_config, my_display_config)
        self.my_simulation_parameters = my_simulation_parameters
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep

    def calculate_masses_of_water_flows(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
        water_mass_flow_rate_of_secondary_side_in_kg_per_second: float,
        seconds_per_timestep: float,
    ) -> Any:
        """ "Calculate masses of the water flows in kg."""

        mass_of_input_water_flows_from_heat_generator_in_kg = (
            water_mass_flow_rate_from_heat_generator_in_kg_per_second * seconds_per_timestep
        )
        mass_of_input_water_flows_from_secondary_side_in_kg = (
            water_mass_flow_rate_of_secondary_side_in_kg_per_second * seconds_per_timestep
        )

        return (
            mass_of_input_water_flows_from_heat_generator_in_kg,
            mass_of_input_water_flows_from_secondary_side_in_kg,
        )

    def calculate_mean_water_temperature_in_water_storage(
        self,
        water_temperature_input_of_secondary_side_in_celsius: float,
        water_temperature_from_heat_generator_in_celsius: float,
        mass_of_input_water_flows_from_heat_generator_in_kg: float,
        mass_of_input_water_flows_of_secondary_side_in_kg: float,
        water_mass_in_storage_in_kg: float,
        previous_mean_water_temperature_in_water_storage_in_celsius: float,
    ) -> float:
        """Calculate the mean temperature of the water in the water boiler."""

        mean_water_temperature_in_water_storage_in_celsius = (
            water_mass_in_storage_in_kg * previous_mean_water_temperature_in_water_storage_in_celsius
            + mass_of_input_water_flows_from_heat_generator_in_kg * water_temperature_from_heat_generator_in_celsius
            + mass_of_input_water_flows_of_secondary_side_in_kg * water_temperature_input_of_secondary_side_in_celsius
        ) / (
            water_mass_in_storage_in_kg
            + mass_of_input_water_flows_from_heat_generator_in_kg
            + mass_of_input_water_flows_of_secondary_side_in_kg
        )

        return mean_water_temperature_in_water_storage_in_celsius

    def calculate_mixing_factor_for_water_temperature_outputs(self) -> Any:
        """Calculate mixing factor for water outputs."""

        # mixing factor depends on seconds per timestep
        # if one timestep = 1h (3600s) or more, the factor for the water storage portion is one

        if 0 <= self.seconds_per_timestep <= 3600:
            factor_for_water_storage_portion = self.seconds_per_timestep / 3600
            factor_for_water_input_portion = 1 - factor_for_water_storage_portion

        elif self.seconds_per_timestep > 3600:
            factor_for_water_storage_portion = 1
            factor_for_water_input_portion = 0

        else:
            raise ValueError("unknown value for seconds per timestep")

        return factor_for_water_storage_portion, factor_for_water_input_portion

    def calculate_water_output_temperature(
        self,
        mean_water_temperature_in_water_storage_in_celsius: float,
        mixing_factor_water_storage_portion: float,
        mixing_factor_water_input_portion: float,
        water_input_temperature_in_celsius: float,
    ) -> float:
        """Calculate the water output temperature of the water storage."""

        water_temperature_output_in_celsius = (
            mixing_factor_water_input_portion * water_input_temperature_in_celsius
            + mixing_factor_water_storage_portion * mean_water_temperature_in_water_storage_in_celsius
        )

        return water_temperature_output_in_celsius

    def calculate_heat_loss_and_temperature_loss(
        self,
        storage_surface_in_m2: float,
        seconds_per_timestep: float,
        mean_water_temperature_in_water_storage_in_celsius: float,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float,
        ambient_temperature_in_celsius: float,
        mass_in_storage_in_kg: float,
    ) -> Tuple[float, float]:
        """Calculate temperature loss in celsius per timestep."""

        heat_loss_in_watt = self.calculate_heat_loss_in_watt(
            mean_temperature_in_storage_in_celsius=mean_water_temperature_in_water_storage_in_celsius,
            storage_surface_in_m2=storage_surface_in_m2,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=heat_transfer_coefficient_in_watt_per_m2_per_kelvin,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
        )

        # basis here: Q = m * cw * delta temperature, temperature loss is another term for delta temperature here
        temperature_loss_of_water_in_celsius_per_hour = heat_loss_in_watt / (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin * mass_in_storage_in_kg
        )

        # transform from °C/h to °C/timestep
        temperature_loss_in_celsius_per_timestep = temperature_loss_of_water_in_celsius_per_hour / (
            3600 / seconds_per_timestep
        )

        return heat_loss_in_watt, temperature_loss_in_celsius_per_timestep

    def calculate_heat_loss_in_watt(
        self,
        storage_surface_in_m2: float,
        mean_temperature_in_storage_in_celsius: float,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin: float,
        ambient_temperature_in_celsius: float,
    ) -> float:
        """Calculate the current heat loss.

        It is dependent on storage surface area and current water temperature as well as heat transfer coefficient and ambient temperature.
        """

        # loss = heat coeff * surface * delta temperature
        heat_loss_in_watt = (
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin
            * storage_surface_in_m2
            * (mean_temperature_in_storage_in_celsius - ambient_temperature_in_celsius)
        )
        return heat_loss_in_watt

    def calculate_surface_area_of_storage(self, storage_volume_in_liter: float) -> float:
        """Calculate the surface area of the storage which is assumed to be a cylinder."""

        storage_volume_in_m3 = storage_volume_in_liter * 1e-3
        # volume = r^2 * pi * h = r^2 * pi * 4r = 4 * r^3 * pi
        radius_of_storage_in_m = (storage_volume_in_m3 / (4 * np.pi)) ** (1 / 3)

        # lateral surface = 2 * pi * r * h (h=4*r here)
        lateral_surface_in_m2 = 2 * radius_of_storage_in_m * np.pi * (4 * radius_of_storage_in_m)
        # circle surface
        circle_surface_in_m2 = np.pi * radius_of_storage_in_m**2

        # total storage surface
        # cylinder surface area = lateral surface +  2 * circle surface
        storage_surface_in_m2 = lateral_surface_in_m2 + 2 * circle_surface_in_m2

        return float(storage_surface_in_m2)

    #########################################################################################################################################################

    def calculate_thermal_energy_in_storage(
        self,
        mean_water_temperature_in_storage_in_celsius: float,
        mass_in_storage_in_kg: float,
    ) -> float:
        """Calculate thermal energy with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)

        thermal_energy_in_storage_in_joule = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
            * mass_in_storage_in_kg
            * (mean_water_temperature_in_storage_in_celsius)
        )  # T_mean - 0°C
        # 1Wh = J / 3600
        thermal_energy_in_storage_in_watt_hour = thermal_energy_in_storage_in_joule / 3600

        return thermal_energy_in_storage_in_watt_hour

    def calculate_thermal_energy_of_water_flow(
        self, water_mass_in_kg: float, water_temperature_difference_in_kelvin: float
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)
        thermal_energy_of_input_water_flow_in_watt_hour = (
            (1 / 3600)
            * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
            * water_mass_in_kg
            * water_temperature_difference_in_kelvin
        )

        return thermal_energy_of_input_water_flow_in_watt_hour

    def calculate_thermal_energy_increase_or_decrease_in_storage(
        self,
        current_thermal_energy_in_storage_in_watt_hour: float,
        previous_thermal_energy_in_storage_in_watt_hour: float,
    ) -> float:
        """Calculate thermal energy difference of current and previous state."""
        thermal_energy_difference_in_watt_hour = (
            current_thermal_energy_in_storage_in_watt_hour - previous_thermal_energy_in_storage_in_watt_hour
        )

        return thermal_energy_difference_in_watt_hour

    def calculate_thermal_power_of_water_flow(
        self,
        water_mass_flow_in_kg_per_s: float,
        water_temperature_cold_in_celsius: float,
        water_temperature_hot_in_celsius: float,
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""

        thermal_power_of_input_water_flow_in_watt = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
            * water_mass_flow_in_kg_per_s
            * (water_temperature_hot_in_celsius - water_temperature_cold_in_celsius)
        )

        return thermal_power_of_input_water_flow_in_watt


class SimpleHotWaterStorage(SimpleWaterStorage):
    """SimpleHotWaterStorage class."""

    # Input
    # A hot water storage can be used also with more than one heat generator. In this case you need to add a new input and output.
    WaterTemperatureFromHeatDistribution = "WaterTemperatureFromHeatDistribution"
    WaterTemperatureFromHeatGenerator = "WaterTemperatureFromHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    WaterMassFlowRateFromHeatDistributionSystem = "WaterMassFlowRateFromHeatDistributionSystem"
    State = "State"

    # Output

    WaterTemperatureToHeatDistribution = "WaterTemperatureToHeatDistribution"
    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"

    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"

    # make some more outputs for testing simple storage

    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyFromHeatGenerator = "ThermalEnergyFromHeatGenerator"
    ThermalEnergyFromHeatDistribution = "ThermalEnergyFromHeatDistribution"
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"

    StandbyHeatLoss = "StandbyHeatLoss"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config

        self.mean_water_temperature_in_water_storage_in_celsius: float = 21

        if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR):
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = (
                SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR)
            )
        else:
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = None

        self.position_hot_water_storage_in_system = self.waterstorageconfig.position_hot_water_storage_in_system
        self.build(heat_exchanger_is_present=self.waterstorageconfig.heat_exchanger_is_present)

        self.state: SimpleWaterStorageState = SimpleWaterStorageState(
            mean_water_temperature_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            temperature_loss_in_celsius_per_timestep=0,
        )
        self.previous_state = self.state.self_copy()

        # =================================================================================================================================
        # Input channels

        self.water_temperature_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatDistribution,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_mass_flow_rate_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatDistributionSystem,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )

        if self.position_hot_water_storage_in_system in [PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_PUMP]:
            self.water_temperature_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureFromHeatGenerator,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )

            self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterMassFlowRateFromHeatGenerator,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                False,
            )

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, False
        )

        # Output channels

        self.water_temperature_heat_distribution_system_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatDistribution,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatDistribution} will follow.",
        )

        self.water_temperature_heat_generator_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatGenerator,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatGenerator} will follow.",
        )

        self.water_temperature_mean_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMeanTemperatureInStorage,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterMeanTemperatureInStorage} will follow.",
        )

        self.thermal_energy_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInStorage} will follow.",
        )
        self.thermal_energy_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromHeatGenerator} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_input_heat_distribution_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatDistribution,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromHeatDistribution} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.thermal_energy_increase_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyIncreaseInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyIncreaseInStorage} will follow.",
        )

        self.stand_by_heat_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyHeatLoss,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.StandbyHeatLoss} will follow.",
        )
        self.add_default_connections(self.get_default_connections_from_heat_distribution_system())
        self.add_default_connections(self.get_default_connections_from_advanced_heat_pump())
        self.add_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_default_connections(self.get_default_connections_from_gasheater())

    def get_default_connections_from_heat_distribution_system(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get heat distribution default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.heat_distribution_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HeatDistribution")
        connections = []
        hds_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution,
                hds_classname,
                component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem,
                hds_classname,
                component_class.WaterMassFlowHDS,
            )
        )
        return connections

    def get_default_connections_from_advanced_heat_pump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.advanced_heat_pump_hplib"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HeatPumpHplib")
        connections = []
        hp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
                hp_classname,
                component_class.TemperatureOutput,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
                hp_classname,
                component_class.MassFlowOutput,
            )
        )
        return connections

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.more_advanced_heat_pump_hplib"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "MoreAdvancedHeatPumpHPLib")
        connections = []
        hp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
                hp_classname,
                component_class.TemperatureOutputSH,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
                hp_classname,
                component_class.MassFlowOutputSH,
            )
        )
        return connections

    def get_default_connections_from_gasheater(self) -> List[cp.ComponentConnection]:
        """Get gasheater default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_gas_heater"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "GasHeater")
        connections = []
        gasheater_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
                gasheater_classname,
                component_class.MassflowOutputTemperature,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
                gasheater_classname,
                component_class.MassflowOutput,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.waterstorageconfig.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heating water storage."""

        # Get inputs --------------------------------------------------------------------------------------------------------

        state_controller = stsv.get_input_value(self.state_channel)

        water_temperature_from_heat_distribution_system_in_celsius = stsv.get_input_value(
            self.water_temperature_heat_distribution_system_input_channel
        )

        water_mass_flow_rate_from_hds_in_kg_per_second = stsv.get_input_value(
            self.water_mass_flow_rate_heat_distribution_system_input_channel
        )

        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_PUMP:
            water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
                self.water_temperature_heat_generator_input_channel
            )

            # get water mass flow rate of heat generator either from singleton sim repo or from input value
            if self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo is not None:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                    self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
                )
            else:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
                    self.water_mass_flow_rate_heat_generator_input_channel
                )
        else:
            water_temperature_from_heat_generator_in_celsius = 0
            water_mass_flow_rate_from_heat_generator_in_kg_per_second = 0

        # Water Temperature Limit Check  --------------------------------------------------------------------------------------------------------

        if (
            self.mean_water_temperature_in_water_storage_in_celsius > 90
            or self.mean_water_temperature_in_water_storage_in_celsius < 0
        ):
            raise ValueError(
                f"The water temperature in the water storage is with {self.mean_water_temperature_in_water_storage_in_celsius}°C way too high or too low."
            )

        # Calculations ------------------------------------------------------------------------------------------------------

        # calc water masses
        # ------------------------------
        (
            water_mass_from_heat_generator_in_kg,
            water_mass_from_heat_distribution_system_in_kg,
        ) = self.calculate_masses_of_water_flows(
            water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_mass_flow_rate_of_secondary_side_in_kg_per_second=water_mass_flow_rate_from_hds_in_kg_per_second,
            seconds_per_timestep=self.seconds_per_timestep,
        )

        # calc thermal energies
        # ------------------------------

        previous_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        current_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour = (
            self.calculate_thermal_energy_increase_or_decrease_in_storage(
                current_thermal_energy_in_storage_in_watt_hour=current_thermal_energy_in_storage_in_watt_hour,
                previous_thermal_energy_in_storage_in_watt_hour=previous_thermal_energy_in_storage_in_watt_hour,
            )
        )

        thermal_energy_input_from_heat_generator_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_generator_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_heat_generator_in_celsius - 0,
        )
        thermal_energy_input_from_heat_distribution_system_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_distribution_system_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_heat_distribution_system_in_celsius - 0,
        )

        # calc water temperatures
        # ------------------------------

        # mean temperature in storage when all water flows are mixed with previous mean water storage temp
        self.mean_water_temperature_in_water_storage_in_celsius = self.calculate_mean_water_temperature_in_water_storage(
            water_temperature_input_of_secondary_side_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            mass_of_input_water_flows_from_heat_generator_in_kg=water_mass_from_heat_generator_in_kg,
            mass_of_input_water_flows_of_secondary_side_in_kg=water_mass_from_heat_distribution_system_in_kg,
            previous_mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
        )

        # with heat exchanger in water storage perfect heat exchange is possible
        if self.heat_exchanger_is_present is True:
            water_temperature_to_heat_distribution_system_in_celsius = self.state.mean_water_temperature_in_celsius
            water_temperature_to_heat_generator_in_celsius = self.state.mean_water_temperature_in_celsius

        # otherwise the water in the water storage is more stratified, which demands some more calculations
        else:
            # state controller is 1 if the heat generator delivers a mass flow rate input
            if state_controller == 1:
                # hds gets water from heat generator (if heat generator is not off, mass flow is not zero)
                water_temperature_to_heat_distribution_system_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_generator_in_celsius,
                )
                # heat generator gets water from hds (if heat generator is not off, mass flow is not zero)
                water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

            # no water coming from heat generator, hds gets mean water and heat generator gets still water from hds
            elif state_controller == 0:
                water_temperature_to_heat_distribution_system_in_celsius = self.state.mean_water_temperature_in_celsius

                water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

            else:
                raise ValueError("unknown storage controller state.")

        # Set outputs -------------------------------------------------------------------------------------------------------
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_PUMP:
            stsv.set_output_value(
                self.water_temperature_heat_distribution_system_output_channel,
                water_temperature_to_heat_distribution_system_in_celsius,
            )

        stsv.set_output_value(
            self.water_temperature_heat_generator_output_channel,
            water_temperature_to_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_mean_channel,
            self.state.mean_water_temperature_in_celsius,
        )

        stsv.set_output_value(
            self.thermal_energy_in_storage_channel,
            current_thermal_energy_in_storage_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_from_heat_generator_channel,
            thermal_energy_input_from_heat_generator_in_watt_hour,
        )
        stsv.set_output_value(
            self.thermal_energy_input_heat_distribution_system_channel,
            thermal_energy_input_from_heat_distribution_system_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_increase_in_storage_channel,
            thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour,
        )

        stsv.set_output_value(
            self.stand_by_heat_loss_channel,
            self.state.heat_loss_in_watt,
        )

        # Set state -------------------------------------------------------------------------------------------------------

        # calc heat loss in W and the temperature loss
        (
            self.state.heat_loss_in_watt,
            self.state.temperature_loss_in_celsius_per_timestep,
        ) = self.calculate_heat_loss_and_temperature_loss(
            storage_surface_in_m2=self.storage_surface_in_m2,
            seconds_per_timestep=self.seconds_per_timestep,
            mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            ambient_temperature_in_celsius=self.ambient_temperature_in_celsius,
        )

        self.state.mean_water_temperature_in_celsius = (
            self.mean_water_temperature_in_water_storage_in_celsius
            - self.state.temperature_loss_in_celsius_per_timestep
        )

    def build(self, heat_exchanger_is_present: bool) -> None:
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        self.specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_watthour_per_kilogramm_per_kelvin
        )
        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
        self.density_water_at_40_degree_celsius_in_kg_per_liter = 0.992

        # physical parameters of storage
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_40_degree_celsius_in_kg_per_liter
            * self.waterstorageconfig.volume_heating_water_storage_in_liter
        )
        self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin = (
            self.config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
        )
        self.storage_surface_in_m2 = self.calculate_surface_area_of_storage(
            storage_volume_in_liter=self.waterstorageconfig.volume_heating_water_storage_in_liter,
        )

        # the ambient temperature is here assumed as the basement temperature which is all year 17°C, this is where the water storage is located
        self.ambient_temperature_in_celsius = 17.0

        self.heat_exchanger_is_present = heat_exchanger_is_present
        # if heat exchanger is present, the heat is perfectly exchanged so the water output temperature corresponds to the mean temperature
        if self.heat_exchanger_is_present is True:
            (
                self.factor_for_water_storage_portion,
                self.factor_for_water_input_portion,
            ) = (1, 0)
        # if heat exchanger is not present, the water temperatures in the storage are more stratified
        # here a mixing factor is calcualted
        else:
            (
                self.factor_for_water_storage_portion,
                self.factor_for_water_input_portion,
            ) = self.calculate_mixing_factor_for_water_temperature_outputs()

    @staticmethod
    def get_cost_capex(
        config: SimpleHotWaterStorageConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
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
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for hot water storage."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


class SimpleHotWaterStorageController(cp.Component):
    """SimpleHotWaterStorageController Class."""

    # Inputs
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"

    # Outputs
    State = "State"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR):
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = (
                SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR)
            )
        else:
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = None

        self.controller_mode: str = "off"
        # Inputs
        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatGenerator,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        # Outputs
        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

    def build(self) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> None:
        """Write important variables to report."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            # get water mass flow rate of heat generator either from singleton sim repo or from input value
            if self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo is not None:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                    self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
                )
            else:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
                    self.water_mass_flow_rate_heat_generator_input_channel
                )

            self.conditions_on_off(
                water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second
            )

            if self.controller_mode == "on":
                state = 1
            elif self.controller_mode == "off":
                state = 0

            else:
                raise ValueError("Controller State unknown.")

            stsv.set_output_value(self.state_channel, state)

    def conditions_on_off(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
    ) -> None:
        """Set conditions for the simple hot water storage controller mode."""

        if self.controller_mode == "on":
            # turn mode off when heat generator delivers no water
            if water_mass_flow_rate_from_heat_generator_in_kg_per_second == 0:
                self.controller_mode = "off"
                return

        elif self.controller_mode == "off":
            # turn mode on if water from heat generator is flowing
            if water_mass_flow_rate_from_heat_generator_in_kg_per_second != 0:
                self.controller_mode = "on"
                return

        else:
            raise ValueError("unknown controller mode")


class SimpleDHWStorage(SimpleWaterStorage):
    """SimpleHotWaterStorage class."""

    # Input
    # A hot water storage can be used also with more than one heat generator. In this case you need to add a new input and output.
    WaterTemperatureFromHeatGenerator = "WaterTemperatureFromHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    WaterConsumption = "WaterConsumption"

    # Output
    # WaterTemperatureInputDHW = "WaterTemperatureInputDHW"
    # WaterTemperatureOutputDHW = "WaterTemperatureOutputDHW"
    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"
    WaterTemperatureFromHeatGeneratorOutput = "WaterTemperatureFromHeatGenerator"
    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"
    StandbyTemperatureLoss = "StandbyTemperatureLoss"
    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyFromHeatGenerator = "ThermalEnergyFromHeatGenerator"
    ThermalEnergyConsumptionDHW = "ThermalEnergyConsumptionDHW"
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"
    ThermalPowerConsumptionDHW = "ThermalPowerConsumptionDHW"
    ThermalPowerFromHeatGenerator = "ThermalPowerFromHeatGenerator"
    StandbyHeatLoss = "StandbyHeatLoss"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleDHWStorageConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config

        self.mean_water_temperature_in_water_storage_in_celsius: float = 60

        self.build()

        self.state: SimpleWaterStorageState = SimpleWaterStorageState(
            mean_water_temperature_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            temperature_loss_in_celsius_per_timestep=0,
            heat_loss_in_watt=0,
        )
        self.previous_state = self.state.self_copy()

        # =================================================================================================================================
        # Input channels

        self.water_consumption_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterConsumption,
            lt.LoadTypes.WARM_WATER,
            lt.Units.LITER,
            True,
        )
        self.water_temperature_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatGenerator,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatGenerator,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            True,
        )

        # Output channels
        # self.water_temperature_dhw_input_channel: ComponentOutput = self.add_output(
        #     self.component_name,
        #     self.WaterTemperatureInputDHW,
        #     lt.LoadTypes.WATER,
        #     lt.Units.CELSIUS,
        #     output_description=f"here a description for {self.WaterTemperatureInputDHW} will follow.",
        # )
        #
        # self.water_temperature_dhw_output_channel: ComponentOutput = self.add_output(
        #     self.component_name,
        #     self.WaterTemperatureOutputDHW,
        #     lt.LoadTypes.WATER,
        #     lt.Units.CELSIUS,
        #     output_description=f"here a description for {self.WaterTemperatureOutputDHW} will follow.",
        # )

        self.water_temperature_to_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatGenerator,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatGenerator} will follow.",
        )

        self.water_temperature_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureFromHeatGeneratorOutput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureFromHeatGeneratorOutput} will follow.",
        )

        self.water_temperature_mean_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMeanTemperatureInStorage,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterMeanTemperatureInStorage} will follow.",
        )

        self.temperature_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyTemperatureLoss,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.StandbyTemperatureLoss} will follow.",
        )

        self.thermal_energy_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInStorage} will follow.",
        )
        self.thermal_energy_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyFromHeatGenerator} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_energy_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyConsumptionDHW} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.thermal_energy_increase_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyIncreaseInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyIncreaseInStorage} will follow.",
        )

        self.stand_by_heat_loss_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.StandbyHeatLoss,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.StandbyHeatLoss} will follow.",
        )

        self.thermal_power_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerConsumptionDHW,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerConsumptionDHW} will follow.",
        )

        self.thermal_power_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerFromHeatGenerator} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_default_connections(self.get_default_connections_from_utsp())

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.more_advanced_heat_pump_hplib"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "MoreAdvancedHeatPumpHPLib")
        connections = []
        hp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
                hp_classname,
                component_class.TemperatureOutputDHW,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
                hp_classname,
                component_class.MassFlowOutputDHW,
            )
        )
        return connections

    def get_default_connections_from_utsp(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get advanced het pump default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.loadprofilegenerator_utsp_connector"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "UtspLpgConnector")
        connections = []
        utsp_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleDHWStorage.WaterConsumption,
                utsp_classname,
                component_class.WaterConsumption,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.drain_water_temperature = configuration.HouseholdWarmWaterDemandConfig.freshwater_temperature

        self.warm_water_temperature = (
            configuration.HouseholdWarmWaterDemandConfig.ww_temperature_demand
            - configuration.HouseholdWarmWaterDemandConfig.temperature_difference_hot
        )

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        self.specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_watthour_per_kilogramm_per_kelvin
        )
        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
        self.density_water_at_40_degree_celsius_in_kg_per_liter = 0.992

        # physical parameters of storage
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_40_degree_celsius_in_kg_per_liter
            * self.waterstorageconfig.volume_heating_water_storage_in_liter
        )
        self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin = (
            self.waterstorageconfig.heat_transfer_coefficient_in_watt_per_m2_per_kelvin
        )
        self.storage_surface_in_m2 = self.calculate_surface_area_of_storage(
            storage_volume_in_liter=self.waterstorageconfig.volume_heating_water_storage_in_liter,
        )

        self.ambient_temperature_in_celsius = 20.0

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.waterstorageconfig.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heating water storage."""

        # Get inputs --------------------------------------------------------------------------------------------------------

        water_temperature_input_of_dhw_in_celsius = self.drain_water_temperature
        water_temperature_output_of_dhw_in_celsius = self.warm_water_temperature
        water_mass_flow_rate_of_dhw_in_kg_per_second = (
            stsv.get_input_value(self.water_consumption_channel)
            * self.density_water_at_40_degree_celsius_in_kg_per_liter
            / self.seconds_per_timestep
        )

        water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
            self.water_temperature_heat_generator_input_channel
        )
        water_mass_flow_rate_from_heat_generator_in_kg_per_second = stsv.get_input_value(
            self.water_mass_flow_rate_heat_generator_input_channel
        )

        # Water Temperature Limit Check  --------------------------------------------------------------------------------------------------------

        if (
            self.mean_water_temperature_in_water_storage_in_celsius > 90
            or self.mean_water_temperature_in_water_storage_in_celsius < 0
        ):
            raise ValueError(
                f"The water temperature in the water storage is with {self.mean_water_temperature_in_water_storage_in_celsius}°C way too high or too low."
            )

        # Calculations ------------------------------------------------------------------------------------------------------

        # calc water masses
        # ------------------------------
        (
            water_mass_from_heat_generator_in_kg,
            water_mass_of_dhw_in_kg,
        ) = self.calculate_masses_of_water_flows(
            water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_mass_flow_rate_of_secondary_side_in_kg_per_second=water_mass_flow_rate_of_dhw_in_kg_per_second,
            seconds_per_timestep=self.seconds_per_timestep,
        )

        # calc thermal energies
        # ------------------------------

        previous_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        current_thermal_energy_in_storage_in_watt_hour = self.calculate_thermal_energy_in_storage(
            mean_water_temperature_in_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )
        thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour = (
            self.calculate_thermal_energy_increase_or_decrease_in_storage(
                current_thermal_energy_in_storage_in_watt_hour=current_thermal_energy_in_storage_in_watt_hour,
                previous_thermal_energy_in_storage_in_watt_hour=previous_thermal_energy_in_storage_in_watt_hour,
            )
        )
        thermal_energy_input_from_heat_generator_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_generator_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_from_heat_generator_in_celsius
            - self.mean_water_temperature_in_water_storage_in_celsius,
        )
        thermal_energy_consumption_of_dhw_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_of_dhw_in_kg,
            water_temperature_difference_in_kelvin=water_temperature_output_of_dhw_in_celsius
            - water_temperature_input_of_dhw_in_celsius,
        )

        # calc thermal power
        # ------------------------------
        (heat_loss_in_watt, temperature_loss_in_celsius_per_timestep) = self.calculate_heat_loss_and_temperature_loss(
            storage_surface_in_m2=self.storage_surface_in_m2,
            seconds_per_timestep=self.seconds_per_timestep,
            mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            heat_transfer_coefficient_in_watt_per_m2_per_kelvin=self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin,
            mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            ambient_temperature_in_celsius=self.ambient_temperature_in_celsius,
        )
        thermal_power_from_heat_generator_in_watt = self.calculate_thermal_power_of_water_flow(
            water_mass_flow_in_kg_per_s=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_temperature_cold_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_from_heat_generator_in_celsius,
        )
        thermal_power_consumption_of_dhw_in_watt = self.calculate_thermal_power_of_water_flow(
            water_mass_flow_in_kg_per_s=water_mass_flow_rate_of_dhw_in_kg_per_second,
            water_temperature_cold_in_celsius=water_temperature_input_of_dhw_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_output_of_dhw_in_celsius,
        )

        # calc water temperatures
        # ------------------------------

        # mean temperature in storage when all water flows are mixed with previous mean water storage temp
        self.mean_water_temperature_in_water_storage_in_celsius = self.calculate_mean_water_temperature_in_water_storage(
            water_temperature_input_of_secondary_side_in_celsius=water_temperature_input_of_dhw_in_celsius,
            water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            mass_of_input_water_flows_from_heat_generator_in_kg=water_mass_from_heat_generator_in_kg,
            mass_of_input_water_flows_of_secondary_side_in_kg=water_mass_of_dhw_in_kg,
            previous_mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
        )

        water_temperature_to_heat_generator_in_celsius = self.state.mean_water_temperature_in_celsius

        stsv.set_output_value(
            self.water_temperature_to_heat_generator_channel,
            water_temperature_to_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_from_heat_generator_channel,
            water_temperature_from_heat_generator_in_celsius,
        )

        stsv.set_output_value(
            self.water_temperature_mean_channel,
            self.mean_water_temperature_in_water_storage_in_celsius,
        )

        stsv.set_output_value(
            self.temperature_loss_channel,
            temperature_loss_in_celsius_per_timestep,
        )

        stsv.set_output_value(
            self.thermal_energy_in_storage_channel,
            current_thermal_energy_in_storage_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_from_heat_generator_channel,
            thermal_energy_input_from_heat_generator_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_dhw_channel,
            thermal_energy_consumption_of_dhw_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_increase_in_storage_channel,
            thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour,
        )

        stsv.set_output_value(
            self.stand_by_heat_loss_channel,
            heat_loss_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_dhw_channel,
            thermal_power_consumption_of_dhw_in_watt,
        )

        stsv.set_output_value(
            self.thermal_power_from_heat_generator_channel,
            thermal_power_from_heat_generator_in_watt,
        )
        # Set state -------------------------------------------------------------------------------------------------------

        # calc heat loss in W and the temperature loss

        self.state.heat_loss_in_watt = heat_loss_in_watt
        self.state.temperature_loss_in_celsius_per_timestep = temperature_loss_in_celsius_per_timestep

        self.state.mean_water_temperature_in_celsius = (
            self.mean_water_temperature_in_water_storage_in_celsius - temperature_loss_in_celsius_per_timestep
        )

    @staticmethod
    def get_cost_capex(
        config: SimpleDHWStorageConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
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
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for hot water storage."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.STORAGE_HOT_WATER_SPACE_HEATING,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        thermal_power_dhw_consumption_in_kilowatt_hour: float
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalPowerConsumptionDHW:
                    thermal_power_dhw_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                    thermal_power_dhw_consumption_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=thermal_power_dhw_consumption_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    break

        # make kpi entry
        occupancy_total_electricity_consumption_entry = KpiEntry(
            name="Residents' total thermal dhw consumption",
            unit="kWh",
            value=thermal_power_dhw_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.RESIDENTS,
            description=self.component_name,
        )

        list_of_kpi_entries.append(occupancy_total_electricity_consumption_entry)
        return list_of_kpi_entries
