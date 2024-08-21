""" Simple hot water storage implementation: HotWaterStorage class together with state and configuration class.

Energy bucket model: extracts energy, adds energy and converts back to temperatere.
The hot water storage simulates only storage and demand and needs to be connnected to a heat source. It can act as
DHW hot water storage or as buffer storage.
"""

import importlib
from dataclasses import dataclass
from typing import List, Any, Tuple
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

import hisim.component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import SingleTimeStepValues, ComponentInput, ComponentOutput, OpexCostDataClass, DisplayConfig
from hisim.components.configuration import PhysicsConfig
from hisim.components import configuration
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass

__authors__ = ""
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""


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
class SimpleDHWStorageState:
    """SimpleHotWaterStorageState class."""

    mean_water_temperature_in_celsius: float = 25.0
    temperature_loss_in_celsius_per_timestep: float = 0.0
    heat_loss_in_watt: float = 0.0

    def self_copy(self):
        """Copy the Simple Hot Water Storage State."""
        return SimpleDHWStorageState(
            self.mean_water_temperature_in_celsius,
            self.temperature_loss_in_celsius_per_timestep,
            self.heat_loss_in_watt,
        )


class SimpleDHWStorage(cp.Component):
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
        super().__init__(
            name=config.building_name + "_" + config.name,
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

        self.state = SimpleDHWStorageState(
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
            water_mass_flow_rate_of_dhw_in_kg_per_second=water_mass_flow_rate_of_dhw_in_kg_per_second,
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
            water_temperature_cold_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_from_heat_generator_in_celsius,
        )
        thermal_energy_consumption_of_dhw_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_of_dhw_in_kg,
            water_temperature_cold_in_celsius=water_temperature_input_of_dhw_in_celsius,
            water_temperature_hot_in_celsius=water_temperature_output_of_dhw_in_celsius,
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
            water_temperature_input_of_dhw_in_celsius=water_temperature_input_of_dhw_in_celsius,
            water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            mass_of_input_water_flows_from_heat_generator_in_kg=water_mass_from_heat_generator_in_kg,
            mass_of_input_water_flows_of_dhw_in_kg=water_mass_of_dhw_in_kg,
            previous_mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
        )

        water_temperature_to_heat_generator_in_celsius = self.state.mean_water_temperature_in_celsius

        # Set outputs -------------------------------------------------------------------------------------------------------
        # stsv.set_output_value(
        #     self.water_temperature_dhw_input_channel,
        #     water_temperature_input_of_dhw_in_celsius,
        # )
        #
        # stsv.set_output_value(
        #     self.water_temperature_dhw_output_channel,
        #     water_temperature_output_of_dhw_in_celsius,
        # )

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

    def calculate_masses_of_water_flows(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
        water_mass_flow_rate_of_dhw_in_kg_per_second: float,
        seconds_per_timestep: float,
    ) -> Any:
        """ "Calculate masses of the water flows in kg."""

        mass_of_input_water_flows_from_heat_generator_in_kg = (
            water_mass_flow_rate_from_heat_generator_in_kg_per_second * seconds_per_timestep
        )
        mass_of_input_water_flows_of_dhw_in_kg = water_mass_flow_rate_of_dhw_in_kg_per_second * seconds_per_timestep

        return (
            mass_of_input_water_flows_from_heat_generator_in_kg,
            mass_of_input_water_flows_of_dhw_in_kg,
        )

    def calculate_mean_water_temperature_in_water_storage(
        self,
        water_temperature_input_of_dhw_in_celsius: float,
        water_temperature_from_heat_generator_in_celsius: float,
        mass_of_input_water_flows_from_heat_generator_in_kg: float,
        mass_of_input_water_flows_of_dhw_in_kg: float,
        water_mass_in_storage_in_kg: float,
        previous_mean_water_temperature_in_water_storage_in_celsius: float,
    ) -> float:
        """Calculate the mean temperature of the water in the water boiler."""

        mean_water_temperature_in_water_storage_in_celsius = (
            water_mass_in_storage_in_kg * previous_mean_water_temperature_in_water_storage_in_celsius
            + mass_of_input_water_flows_from_heat_generator_in_kg * water_temperature_from_heat_generator_in_celsius
            + mass_of_input_water_flows_of_dhw_in_kg * water_temperature_input_of_dhw_in_celsius
        ) / (
            water_mass_in_storage_in_kg
            + mass_of_input_water_flows_from_heat_generator_in_kg
            + mass_of_input_water_flows_of_dhw_in_kg
        )

        return mean_water_temperature_in_water_storage_in_celsius

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
            self.specific_heat_capacity_of_water_in_watthour_per_kilogram_per_celsius * mass_in_storage_in_kg
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
            self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * mass_in_storage_in_kg
            * (mean_water_temperature_in_storage_in_celsius)
        )  # T_mean - 0°C
        # 1Wh = J / 3600
        thermal_energy_in_storage_in_watt_hour = thermal_energy_in_storage_in_joule / 3600

        return thermal_energy_in_storage_in_watt_hour

    def calculate_thermal_energy_of_water_flow(
        self, water_mass_in_kg: float, water_temperature_cold_in_celsius: float, water_temperature_hot_in_celsius: float
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)
        thermal_energy_of_input_water_flow_in_watt_hour = (
            (1 / 3600)
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * water_mass_in_kg
            * (water_temperature_hot_in_celsius - water_temperature_cold_in_celsius)
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
        self, water_mass_flow_in_kg_per_s: float, water_temperature_cold_in_celsius: float, water_temperature_hot_in_celsius: float
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""

        thermal_power_of_input_water_flow_in_watt = (
            self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * water_mass_flow_in_kg_per_s
            * (water_temperature_hot_in_celsius - water_temperature_cold_in_celsius)
        )

        return thermal_power_of_input_water_flow_in_watt

    @staticmethod
    def get_cost_capex(
        config: SimpleDHWStorageConfig,
    ) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost, config.co2_footprint, config.lifetime

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
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
            description=self.component_name
        )

        list_of_kpi_entries.append(occupancy_total_electricity_consumption_entry)
        return list_of_kpi_entries
