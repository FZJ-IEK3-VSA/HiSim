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
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
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
    temperature_loss_in_celsius_per_hour: float
    heat_exchanger_is_present: bool

    @classmethod
    def get_default_simplehotwaterstorage_config(
        cls,
    ) -> Any:
        """Get a default simplehotwaterstorage config."""
        config = SimpleHotWaterStorageConfig(
            name="SimpleHotWaterStorage",
            volume_heating_water_storage_in_liter=500,
            temperature_loss_in_celsius_per_hour=0.21,
            heat_exchanger_is_present=True,  # until now stratified mode is causing problems, so heat exchanger mode is recommended
        )
        return config


@dataclass
class SimpleHotWaterStorageState:

    """SimpleHotWaterStorageState class."""

    mean_water_temperature_in_celsius: float = 25
    temperature_loss_in_celsius_per_timestep: float = 0

    def self_copy(self):
        """Copy the Simple Hot Water Storage State."""
        return SimpleHotWaterStorageState(
            self.mean_water_temperature_in_celsius,
            self.temperature_loss_in_celsius_per_timestep,
        )


class SimpleHotWaterStorage(cp.Component):

    """SimpleHotWaterStorage class."""

    # Input
    # A hot water storage can be used also with more than one heat generator. In this case you need to add a new input and output.
    WaterTemperatureFromHeatDistributionSystem = (
        "WaterTemperatureFromHeatDistributionSystem"
    )
    WaterTemperatureFromHeatGenerator = "WaterTemperaturefromHeatGenerator"
    WaterMassFlowRateFromHeatGenerator = "WaterMassFlowRateFromHeatGenerator"
    State = "State"

    # Output

    WaterTemperatureToHeatDistributionSystem = (
        "WaterTemperatureToHeatDistributionSystem"
    )
    WaterTemperatureToHeatGenerator = "WaterTemperatureToHeatGenerator"

    WaterMeanTemperatureInStorage = "WaterMeanTemperatureInStorage"

    # make some more outputs for testing simple storage

    ThermalEnergyInStorage = "ThermalEnergyInStorage"
    ThermalEnergyInputFromHeatGenerator = "ThermalEnergyInputFromHeatGenerator"
    ThermalEnergyInputFromHeatDistributionSystem = (
        "ThermalEnergyInputFromHeatDistributionSystem"
    )
    ThermalEnergyIncreaseInStorage = "ThermalEnergyIncreaseInStorage"

    StandbyHeatLoss = "StandbyHeatLoss"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHotWaterStorageConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.waterstorageconfig = config
        self.temperature_loss_in_celsius_per_hour = (
            self.waterstorageconfig.temperature_loss_in_celsius_per_hour
        )

        self.mean_water_temperature_in_water_storage_in_celsius: float = 21

        if SingletonSimRepository().exist_entry(
            key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATINGDISTRIBUTIONSYSTEM
        ):
            self.water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATINGDISTRIBUTIONSYSTEM
            )
        else:
            raise KeyError(
                "Keys for water mass flow rate of heating distribution system was not found in the singleton sim repository."
                + "This might be because the heating_distribution_system was not initialized before the simple hot water storage."
                + "Please check the order of the initialization of the components in your example."
            )
        if SingletonSimRepository().exist_entry(
            key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR
        ):
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR
            )
        else:
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = (
                None
            )

        self.build(
            heat_exchanger_is_present=self.waterstorageconfig.heat_exchanger_is_present
        )

        self.state: SimpleHotWaterStorageState = SimpleHotWaterStorageState(
            mean_water_temperature_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            temperature_loss_in_celsius_per_timestep=0,
        )
        self.previous_state = self.state.self_copy()

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
                False,
            )
        )

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, False
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
        #########################
        self.thermal_energy_in_storage_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInStorage,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInStorage} will follow.",
        )
        self.thermal_energy_input_from_heat_generator_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInputFromHeatGenerator,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInputFromHeatGenerator} will follow.",
        )
        self.thermal_energy_input_from_heat_distribution_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyInputFromHeatDistributionSystem,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalEnergyInputFromHeatDistributionSystem} will follow.",
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
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.StandbyHeatLoss} will follow.",
        )

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

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heating water storage."""

        # Get inputs --------------------------------------------------------------------------------------------------------

        state_controller = stsv.get_input_value(self.state_channel)

        water_temperature_from_heat_distribution_system_in_celsius = (
            stsv.get_input_value(
                self.water_temperature_heat_distribution_system_input_channel
            )
        )
        water_temperature_from_heat_generator_in_celsius = stsv.get_input_value(
            self.water_temperature_heat_generator_input_channel
        )

        # get water mass flow rate of heat generator either from singleton sim repo or from input value
        if (
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
            is not None
        ):
            water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
            )
        else:
            water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                stsv.get_input_value(
                    self.water_mass_flow_rate_heat_generator_input_channel
                )
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
            water_mass_from_heat_distribution_system_in_kg,
        ) = self.calculate_masses_of_water_flows(
            water_mass_flow_rate_from_heat_generator_in_kg_per_second=water_mass_flow_rate_from_heat_generator_in_kg_per_second,
            water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second=self.water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second,
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
        thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour = self.calculate_thermal_energy_increase_or_decrease_in_storage(
            current_thermal_energy_in_storage_in_watt_hour=current_thermal_energy_in_storage_in_watt_hour,
            previous_thermal_energy_in_storage_in_watt_hour=previous_thermal_energy_in_storage_in_watt_hour,
        )

        thermal_energy_input_from_heat_generator_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_generator_in_kg,
            water_temperature_in_celsius=water_temperature_from_heat_generator_in_celsius,
        )
        thermal_energy_input_from_heat_distribution_system_in_watt_hour = self.calculate_thermal_energy_of_water_flow(
            water_mass_in_kg=water_mass_from_heat_distribution_system_in_kg,
            water_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
        )

        # calc heat loss in storage
        # ------------------------------
        stand_by_heat_loss_in_watt_hour_per_timestep = self.calculate_stand_by_heat_loss(
            temperature_loss_in_celsius_per_timestep=self.state.temperature_loss_in_celsius_per_timestep,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
        )

        # calc water temperatures
        # ------------------------------

        # mean temperature in storage when all water flows are mixed with previous mean water storage temp
        self.mean_water_temperature_in_water_storage_in_celsius = self.calculate_mean_water_temperature_in_water_storage(
            water_temperature_from_heat_distribution_system_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
            water_temperature_from_heat_generator_in_celsius=water_temperature_from_heat_generator_in_celsius,
            water_mass_in_storage_in_kg=self.water_mass_in_storage_in_kg,
            mass_of_input_water_flows_from_heat_generator_in_kg=water_mass_from_heat_generator_in_kg,
            mass_of_input_water_flows_from_heat_distribution_system_in_kg=water_mass_from_heat_distribution_system_in_kg,
            previous_mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
        )

        # with heat exchanger in water storage perfect heat exchange is possible
        if self.heat_exchanger_is_present is True:
            water_temperature_to_heat_distribution_system_in_celsius = (
                self.state.mean_water_temperature_in_celsius
            )
            water_temperature_to_heat_generator_in_celsius = (
                self.state.mean_water_temperature_in_celsius
            )

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

                water_temperature_to_heat_distribution_system_in_celsius = (
                    self.state.mean_water_temperature_in_celsius
                )

                water_temperature_to_heat_generator_in_celsius = self.calculate_water_output_temperature(
                    mean_water_temperature_in_water_storage_in_celsius=self.state.mean_water_temperature_in_celsius,
                    mixing_factor_water_input_portion=self.factor_for_water_input_portion,
                    mixing_factor_water_storage_portion=self.factor_for_water_storage_portion,
                    water_input_temperature_in_celsius=water_temperature_from_heat_distribution_system_in_celsius,
                )

            else:
                raise ValueError("unknown storage controller state.")

        # Set outputs -------------------------------------------------------------------------------------------------------

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
            self.thermal_energy_input_from_heat_generator_channel,
            thermal_energy_input_from_heat_generator_in_watt_hour,
        )
        stsv.set_output_value(
            self.thermal_energy_input_from_heat_distribution_system_channel,
            thermal_energy_input_from_heat_distribution_system_in_watt_hour,
        )

        stsv.set_output_value(
            self.thermal_energy_increase_in_storage_channel,
            thermal_energy_increase_current_vs_previous_mean_temperature_in_watt_hour,
        )

        stsv.set_output_value(
            self.stand_by_heat_loss_channel,
            stand_by_heat_loss_in_watt_hour_per_timestep,
        )

        # Set state -------------------------------------------------------------------------------------------------------

        self.state.temperature_loss_in_celsius_per_timestep = self.calculate_temperature_loss(
            mean_water_temperature_in_water_storage_in_celsius=self.mean_water_temperature_in_water_storage_in_celsius,
            seconds_per_timestep=self.seconds_per_timestep,
            temperature_loss_in_celsius_per_hour=self.temperature_loss_in_celsius_per_hour,
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
        # https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
        self.density_water_at_40_degree_celsius_in_kg_per_liter = 0.992
        self.water_mass_in_storage_in_kg = (
            self.density_water_at_40_degree_celsius_in_kg_per_liter
            * self.waterstorageconfig.volume_heating_water_storage_in_liter
        )
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

    def calculate_masses_of_water_flows(
        self,
        water_mass_flow_rate_from_heat_generator_in_kg_per_second: float,
        water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second: float,
        seconds_per_timestep: float,
    ) -> Any:
        """ "Calculate masses of the water flows in kg."""

        mass_of_input_water_flows_from_heat_generator_in_kg = (
            water_mass_flow_rate_from_heat_generator_in_kg_per_second
            * seconds_per_timestep
        )
        mass_of_input_water_flows_from_heat_distribution_system_in_kg = (
            water_mass_flow_rate_from_heat_distribution_system_in_kg_per_second
            * seconds_per_timestep
        )

        return (
            mass_of_input_water_flows_from_heat_generator_in_kg,
            mass_of_input_water_flows_from_heat_distribution_system_in_kg,
        )

    def calculate_mean_water_temperature_in_water_storage(
        self,
        water_temperature_from_heat_distribution_system_in_celsius: float,
        water_temperature_from_heat_generator_in_celsius: float,
        mass_of_input_water_flows_from_heat_generator_in_kg: float,
        mass_of_input_water_flows_from_heat_distribution_system_in_kg: float,
        water_mass_in_storage_in_kg: float,
        previous_mean_water_temperature_in_water_storage_in_celsius: float,
    ) -> float:
        """Calculate the mean temperature of the water in the water boiler."""

        mean_water_temperature_in_water_storage_in_celsius = (
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
            + mixing_factor_water_storage_portion
            * mean_water_temperature_in_water_storage_in_celsius
        )

        return water_temperature_output_in_celsius

    def calculate_temperature_loss(
        self,
        mean_water_temperature_in_water_storage_in_celsius: float,
        seconds_per_timestep: float,
        temperature_loss_in_celsius_per_hour: float,
    ) -> float:
        """Calculate temperature loss in celsius per timestep."""

        # make heat loss for mean storage temperature every timestep but only until min temp of 16°C is reached (regular basement temperature)
        # https://www.energieverbraucher.de/de/heizungsspeicher__2102/#:~:text=Ein%20Speicher%20k%C3%BChlt%20t%C3%A4glich%20etwa,heutigen%20Energiepreisen%20t%C3%A4glich%2020%20Cent.

        if mean_water_temperature_in_water_storage_in_celsius >= 16.0:
            temperature_loss_in_celsius_per_timestep = (
                temperature_loss_in_celsius_per_hour / (3600 / seconds_per_timestep)
            )
        else:
            temperature_loss_in_celsius_per_timestep = 0

        return temperature_loss_in_celsius_per_timestep

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
        thermal_energy_in_storage_in_watt_hour = (
            thermal_energy_in_storage_in_joule / 3600
        )

        return thermal_energy_in_storage_in_watt_hour

    def calculate_thermal_energy_of_water_flow(
        self, water_mass_in_kg: float, water_temperature_in_celsius: float
    ) -> float:
        """Calculate thermal energy of the water flow with respect to 0°C temperature."""
        # Q = c * m * (Tout - Tin)
        thermal_energy_of_input_water_flow_in_watt_hour = (
            (1 / 3600)
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * water_mass_in_kg
            * water_temperature_in_celsius
        )

        return thermal_energy_of_input_water_flow_in_watt_hour

    def calculate_thermal_energy_increase_or_decrease_in_storage(
        self,
        current_thermal_energy_in_storage_in_watt_hour: float,
        previous_thermal_energy_in_storage_in_watt_hour: float,
    ) -> float:
        """Calculate thermal energy difference of current and previous state."""
        thermal_energy_difference_in_watt_hour = (
            current_thermal_energy_in_storage_in_watt_hour
            - previous_thermal_energy_in_storage_in_watt_hour
        )

        return thermal_energy_difference_in_watt_hour

    def calculate_stand_by_heat_loss(
        self,
        temperature_loss_in_celsius_per_timestep: float,
        water_mass_in_storage_in_kg: float,
    ) -> float:
        """Calculate stand by heat loss of the storage."""
        heat_loss_in_watt_hour_per_timestep = (
            water_mass_in_storage_in_kg
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            / 3600
            * temperature_loss_in_celsius_per_timestep
        )

        return heat_loss_in_watt_hour_per_timestep


@dataclass_json
@dataclass
class SimpleHotWaterStorageControllerConfig(cp.ConfigBase):

    """Configuration of the SimpleHotWaterStorageController class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleHotWaterStorageController.get_full_classname()

    name: str

    @classmethod
    def get_default_simplehotwaterstoragecontroller_config(
        cls,
    ) -> Any:
        """Get a default simplehotwaterstorage controller config."""
        config = SimpleHotWaterStorageControllerConfig(
            name="SimpleHotWaterStorageController",
        )
        return config


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
    ) -> None:
        """Construct all the neccessary attributes."""

        super().__init__(
            "SimpleHotWaterStorageController",
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        if SingletonSimRepository().exist_entry(
            key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR
        ):
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATGENERATOR
            )
        else:
            self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo = (
                None
            )

        self.controller_mode: str = "off"
        # Inputs
        self.water_mass_flow_rate_heat_generator_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.WaterMassFlowRateFromHeatGenerator,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                False,
            )
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

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            # get water mass flow rate of heat generator either from singleton sim repo or from input value
            if (
                self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
                is not None
            ):
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                    self.water_mass_flow_rate_from_heat_generator_in_kg_per_second_from_singleton_sim_repo
                )
            else:
                water_mass_flow_rate_from_heat_generator_in_kg_per_second = (
                    stsv.get_input_value(
                        self.water_mass_flow_rate_heat_generator_input_channel
                    )
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
