"""Simple Hot Water Storage Module."""

# clean
# Owned
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hisim.component as cp
from hisim.component import (
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import PhysicsConfig
from hisim import loadtypes as lt
from hisim import utils

__authors__ = "Katharina Rieck, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = "dev"


@dataclass_json
@dataclass
class SimpleHotWaterStorageConfig(cp.ConfigBase):

    """Configuration of the SimpleHotWaterStorage class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleHotWaterStorage.get_full_classname()

    name: str
    volume_heating_water_storage_in_liter: float
    mean_water_temperature_in_storage_in_celsius: float
    cool_water_temperature_in_storage_in_celsius: float
    hot_water_temperature_in_storage_in_celsius: float

    @classmethod
    def get_default_simplehotwaterstorage_config(
        cls,
    ) -> Any:
        """Get a default simplehotwaterstorage config."""
        config = SimpleHotWaterStorageConfig(
            name="SimpleHotWaterStorage",
            mean_water_temperature_in_storage_in_celsius=50,
            cool_water_temperature_in_storage_in_celsius=50,
            hot_water_temperature_in_storage_in_celsius=50,
            volume_heating_water_storage_in_liter=100,
        )
        return config


class SimpleHotWaterStorage(cp.Component):

    """SimpleHotWaterStorage class."""

    # Input

    WaterTemperatureFromHeatDistributionSystem = (
        "WaterTemperatureFromHeatDistributionSystem"
    )
    WaterTemperatureFromHeatGenerator = "WaterTemperaturefromHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    WaterMassFlowRateFromHeatDistributionSystem = (
        "WaterMassFlowRateFromHeatDistributionSystem"
    )

    # Output

    WaterTemperatureToHeatDistributionSystem = (
        "WaterTemperatureToHeatDistributionSystem"
    )
    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"

    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            name=config.name, my_simulation_parameters=my_simulation_parameters
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config

        self.start_water_temperature_in_storage_in_celsius = 50.0
        self.mean_water_temperature_in_water_storage_in_celsius: float = 50.0

        self.water_temperature_to_heat_distribution_system_in_celsius: float = 50.0
        self.water_temperature_to_heat_generator_in_celsius: float = 50.0
        self.build()

        # =================================================================================================================================
        # Input channels

        self.water_temperature_heat_distribution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatDistributionSystem,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_temperature_heat_generator_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.WaterTemperatureFromHeatGenerator,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )
        )

        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.WaterMassFlowRateFromHeatGenerator,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                True,
            )
        )

        self.water_mass_flow_rate_heat_distrution_system_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterMassFlowRateFromHeatDistributionSystem,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            True,
        )
        # Output channels

        self.water_temperature_heat_distribution_system_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureToHeatDistributionSystem,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureToHeatDistributionSystem} will follow.",
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

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.waterstorageconfig.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heating water storage."""
        if force_convergence:
            pass
        else:
            self.start_water_temperature_in_storage_in_celsius = (
                self.mean_water_temperature_in_water_storage_in_celsius
            )
            # Get inputs --------------------------------------------------------------------------------------------------------
            water_temperature_from_heat_distribution_system_in_celsius = (
                stsv.get_input_value(
                    self.water_temperature_heat_distribution_system_input_channel
                )
            )
            water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
                self.water_temperature_heat_generator_input_channel
            )
            water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                stsv.get_input_value(
                    self.water_mass_flow_rate_heat_generator_input_channel
                )
            )
            water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second = (
                stsv.get_input_value(
                    self.water_mass_flow_rate_heat_distrution_system_input_channel
                )
            )

            if water_temperature_from_heat_distribution_system_in_celsius == 0:
                """first iteration --> random numbers"""
                water_temperature_from_heat_distribution_system_in_celsius = 50
            if water_temperature_from_heat_generator_in_celsius == 0:
                """first iteration --> random numbers"""
                water_temperature_from_heat_generator_in_celsius = 50

            self.mean_water_temperature_in_water_storage_in_celsius = self.calculate_mean_water_temperature_in_water_storage(
                water_temperature_from_heat_distribution_system_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
                water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
                water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second=water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second,
                water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
                previous_mean_water_temperature_in_water_storage_in_celsius=self.start_water_temperature_in_storage_in_celsius,
                seconds_per_timestep=self.seconds_per_timestep,
            )

            # Calculations ------------------------------------------------------------------------------------------------------

            (
                factor_for_water_storage_portion,
                factor_for_water_input_portion,
            ) = self.calculate_mixing_factor_for_water_temperature_outputs()

            # hds gets water from hp
            self.water_temperature_to_heat_distribution_system_in_celsius = self.calculate_water_output_temperature(
                mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
                mixing_factor_water_input_portion=factor_for_water_input_portion,
                mixing_factor_water_storage_portion=factor_for_water_storage_portion,
                water_input_temperature_in_celsius=water_temperature_from_heat_generator_in_celsius,
            )
            # hp gets water from hds
            self.water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
                mixing_factor_water_input_portion=factor_for_water_input_portion,
                mixing_factor_water_storage_portion=factor_for_water_storage_portion,
                water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            )

            # Set outputs -------------------------------------------------------------------------------------------------------

            stsv.set_output_value(
                self.water_temperature_heat_distribution_system_output_channel,
                self.water_temperature_to_heat_distribution_system_in_celsius,
            )

            stsv.set_output_value(
                self.water_temperature_heat_generator_output_channel,
                self.water_temperature_to_heat_generator_in_celsius,
            )

            stsv.set_output_value(
                self.water_temperature_mean_channel,
                self.mean_water_temperature_in_water_storage_in_celsius,
            )

    def build(self):
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
        self.density_water_at_50_degree_celsius_in_kg_per_liter = 0.988
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_50_degree_celsius_in_kg_per_liter
            * self.waterstorageconfig.volume_heating_water_storage_in_liter
        )

    def calculate_mean_water_temperature_in_water_storage(
        self,
        water_temperature_from_heat_distribution_system_in_celsius,
        water_temperature_from_heat_generator_in_celsius,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second,
        water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second,
        water_mass_in_storage_in_kg,
        previous_mean_water_temperature_in_water_storage_in_celsius,
        seconds_per_timestep,
    ):
        """Calculate the mean temperature of the water in the water boiler."""

        mass_of_input_water_flows_from_heat_generator_in_kg = (
            water_mass_flow_rate_from_heat_generator_in_kg_per_second
            * seconds_per_timestep
        )
        mass_of_input_water_flows_from_heat_distribution_system_in_kg = (
            water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second
            * seconds_per_timestep
        )
        self.mean_water_temperature_in_water_storage_in_celsius = (
            water_mass_in_storage_in_kg
            * previous_mean_water_temperature_in_water_storage_in_celsius
            + mass_of_input_water_flows_from_heat_generator_in_kg
            * water_temperature_from_heat_generator_in_celsius
            + mass_of_input_water_flows_from_heat_distribution_system_in_kg
            * water_temperature_from_heat_distribution_system_in_celsius
        ) / (
            water_mass_in_storage_in_kg
            + mass_of_input_water_flows_from_heat_generator_in_kg
            + mass_of_input_water_flows_from_heat_distribution_system_in_kg
        )

        return self.mean_water_temperature_in_water_storage_in_celsius

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
    ) -> Any:
        """Calculate the water output temperature of the water storage."""

        water_temperature_output_in_celsius = (
            mixing_factor_water_input_portion * water_input_temperature_in_celsius
            + mixing_factor_water_storage_portion
            * mean_water_temperature_in_water_storage_in_celsius
        )

        return water_temperature_output_in_celsius
