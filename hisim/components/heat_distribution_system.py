"""Heat Distribution Module."""
# clean

from enum import IntEnum
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hisim.component as cp
from hisim.components.building import Building
from hisim.components.simple_hot_water_storage import SimpleHotWaterStorage
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import PhysicsConfig
from hisim import loadtypes as lt
from hisim import utils
from hisim import log

__authors__ = "Katharina Rieck, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


class HeatingSystemType(IntEnum):

    """Set Heating System Types."""

    RADIATOR = 1
    FLOORHEATING = 2


@dataclass_json
@dataclass
class HeatDistributionConfig(cp.ConfigBase):

    """Configuration of the HeatingWaterStorage class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return HeatDistribution.get_full_classname()

    name: str
    water_temperature_in_distribution_system_in_celsius: float
    heating_system: HeatingSystemType

    @classmethod
    def get_default_heatdistributionsystem_config(
        cls,
    ) -> Any:
        """Get a default heat distribution system config."""
        config = HeatDistributionConfig(
            name="HeatDistributionSystem",
            water_temperature_in_distribution_system_in_celsius=50,
            heating_system=HeatingSystemType.FLOORHEATING,
        )
        return config


@dataclass_json
@dataclass
class HeatDistributionControllerConfig(cp.ConfigBase):

    """HeatDistribution Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatDistributionController.get_full_classname()

    name: str
    # set_water_storage_temperature_for_heating_in_celsius: float
    # set_water_storage_temperature_for_cooling_in_celsius: float
    set_heating_threshold_outside_temperature_in_celsius: float
    set_heating_temperature_for_building_in_celsius: float
    set_cooling_temperature_for_building_in_celsius: float

    @classmethod
    def get_default_heat_distribution_controller_config(cls):
        """Gets a default HeatDistribution Controller."""
        return HeatDistributionControllerConfig(
            name="HeatDistributionController",
            # set_water_storage_temperature_for_heating_in_celsius=49,
            # set_water_storage_temperature_for_cooling_in_celsius=55,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            set_heating_temperature_for_building_in_celsius=20,
            set_cooling_temperature_for_building_in_celsius=23,
        )


class HeatDistributionState:

    """HeatDistributionState."""

    def __init__(
        self, water_temperature_in_distribution_system_in_celsius: float
    ) -> None:
        """Construct all the necessary attributes."""
        self.water_temperature_in_distribution_system_in_celsius = (
            water_temperature_in_distribution_system_in_celsius
        )

    def clone(self) -> Any:
        """Save previous state."""
        return HeatDistributionState(
            water_temperature_in_distribution_system_in_celsius=self.water_temperature_in_distribution_system_in_celsius
        )


class HeatDistribution(cp.Component):

    """Heat Distribution System.

    It simulates the heat exchange between heat generator and building.

    """

    # Inputs
    State = "State"
    WaterTemperatureInput = "WaterTemperatureInput"
    MaxThermalBuildingDemand = "MaxThermalBuildingDemand"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    ResidenceTemperatureIndoorAir = "ResidenceTemperatureIndoorAir"

    # Outputs
    WaterTemperatureOutput = "WaterTemperatureOutput"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    HeatingDistributionSystemWaterMassFlowRate = (
        "HeatingDistributionSystemWaterMassFlowRate"
    )

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        config: HeatDistributionConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            name=config.name, my_simulation_parameters=my_simulation_parameters
        )
        self.heat_distribution_system_config = config
        self.heating_system = self.heat_distribution_system_config.heating_system
        self.state = HeatDistributionState(
            self.heat_distribution_system_config.water_temperature_in_distribution_system_in_celsius
        )

        self.thermal_power_delivered_in_watt: float = 0.0
        self.water_temperature_output_in_celsius: float = 50.0
        self.delta_temperature_in_celsius: float = 1.0
        self.build(heating_system=self.heating_system)

        # Inputs
        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )
        self.theoretical_thermal_building_demand_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.TheoreticalThermalBuildingDemand,
                lt.LoadTypes.HEATING,
                lt.Units.WATT,
                True,
            )
        )
        self.max_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )

        self.residence_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperatureIndoorAir,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        # Outputs
        self.water_temperature_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureOutput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureOutput} will follow.",
        )
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerDelivered} will follow.",
        )
        self.heating_distribution_system_water_mass_flow_rate_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingDistributionSystemWaterMassFlowRate,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.HeatingDistributionSystemWaterMassFlowRate} will follow.",
        )

        self.add_default_connections(
            self.get_default_connections_from_heat_distribution_controller()
        )
        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )

    def get_default_connections_from_heat_distribution_controller(
        self,
    ):
        """Get heat distribution controller default connections."""
        log.information("setting heat distribution controller default connections")
        connections = []
        hdsc_classname = HeatDistributionController.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.State,
                hdsc_classname,
                HeatDistributionController.State,
            )
        )
        return connections

    def get_default_connections_from_building(
        self,
    ):
        """Get building default connections."""
        log.information("setting building default connections")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.TheoreticalThermalBuildingDemand,
                building_classname,
                Building.TheoreticalThermalBuildingDemand,
            )
        )
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.MaxThermalBuildingDemand,
                building_classname,
                Building.ReferenceMaxHeatBuildingDemand,
            )
        )
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.ResidenceTemperatureIndoorAir,
                building_classname,
                Building.TemperatureIndoorAir,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        log.information("setting simple hot water storage default connections")
        connections = []
        hws_classname = SimpleHotWaterStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.WaterTemperatureInput,
                hws_classname,
                SimpleHotWaterStorage.WaterTemperatureToHeatDistributionSystem,
            )
        )
        return connections

    def build(
        self,
        heating_system: HeatingSystemType,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        # choose delta T depending on the chosen heating system
        if heating_system == HeatingSystemType.FLOORHEATING:
            self.delta_temperature_in_celsius = 3
        elif heating_system == HeatingSystemType.RADIATOR:
            self.delta_temperature_in_celsius = 20
        else:
            raise ValueError("unknown heating system.")

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heat_distribution_system_config.get_string_dict()

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution system."""
        if force_convergence:
            pass
        else:
            # Get inputs ------------------------------------------------------------------------------------------------------------
            state_controller = stsv.get_input_value(self.state_channel)
            theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
                self.theoretical_thermal_building_demand_channel
            )

            water_temperature_input_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )

            max_thermal_building_demand_in_watt = stsv.get_input_value(
                self.max_thermal_building_demand_channel
            )
            residence_temperature_input_in_celsius = stsv.get_input_value(
                self.residence_temperature_input_channel
            )
            # Calculations ----------------------------------------------------------------------------------------------------------
            heating_distribution_system_water_mass_flow_rate_in_kg_per_second = (
                self.calc_heating_distribution_system_water_mass_flow_rate(
                    max_thermal_building_demand_in_watt
                )
            )
            self.state.water_temperature_in_distribution_system_in_celsius = (
                self.water_temperature_output_in_celsius
            )
            if state_controller == 1:

                # building gets the heat that it needs
                (
                    self.water_temperature_output_in_celsius,
                    self.thermal_power_delivered_in_watt,
                ) = self.determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power(
                    water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                    water_mass_flow_in_kg_per_second=heating_distribution_system_water_mass_flow_rate_in_kg_per_second,
                    theoretical_thermal_buiding_demand_in_watt=theoretical_thermal_building_demand_in_watt,
                    residence_temperature_in_celsius=residence_temperature_input_in_celsius,
                )

            elif state_controller == 0:

                self.thermal_power_delivered_in_watt = 0.0

                self.water_temperature_output_in_celsius = (
                    water_temperature_input_in_celsius
                )

            else:
                raise ValueError("unknown mode")

            # Set outputs -----------------------------------------------------------------------------------------------------------

            stsv.set_output_value(
                self.water_temperature_output_channel,
                self.water_temperature_output_in_celsius,
            )
            stsv.set_output_value(
                self.thermal_power_delivered_channel,
                self.thermal_power_delivered_in_watt,
            )
            stsv.set_output_value(
                self.heating_distribution_system_water_mass_flow_rate_channel,
                heating_distribution_system_water_mass_flow_rate_in_kg_per_second,
            )

    def calc_heating_distribution_system_water_mass_flow_rate(
        self,
        max_thermal_building_demand_in_watt: float,
    ) -> Any:
        """Calculate water mass flow between heating distribution system and hot water storage."""
        specific_heat_capacity_of_water_in_joule_per_kg_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )

        heating_distribution_system_water_mass_flow_in_kg_per_second = (
            max_thermal_building_demand_in_watt
            / (
                specific_heat_capacity_of_water_in_joule_per_kg_per_celsius
                * self.delta_temperature_in_celsius
            )
        )
        return heating_distribution_system_water_mass_flow_in_kg_per_second

    def determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power(
        self,
        water_mass_flow_in_kg_per_second: float,
        water_temperature_input_in_celsius: float,
        theoretical_thermal_buiding_demand_in_watt: float,
        residence_temperature_in_celsius: float,
    ) -> Any:
        """Calculate cooled water temperature after heat exchange between heat distribution system and building."""
        # Tout = Tin -  Q/(c * m)
        water_temperature_output_in_celsius = (
            water_temperature_input_in_celsius
            - theoretical_thermal_buiding_demand_in_watt
            / (
                water_mass_flow_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            )
        )
        # prevent that water temperature in hds gets colder than residence temperature in building
        water_temperature_output_in_celsius = max(
            water_temperature_output_in_celsius, residence_temperature_in_celsius
        )

        thermal_power_delivered_effective_in_watt = (
            self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * water_mass_flow_in_kg_per_second
            * (water_temperature_input_in_celsius - water_temperature_output_in_celsius)
        )

        return (
            water_temperature_output_in_celsius,
            thermal_power_delivered_effective_in_watt,
        )


class HeatDistributionController(cp.Component):

    """Heat Distribution Controller.

    It takes data from other
    components and sends signal to the heat distribution for
    activation or deactivation.

    """

    # Inputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )

    # Outputs
    State = "State"
    SetHeatingTemperatureForBuilding = "SetHeatingTemperatureForBuilding"
    SetCoolingTemperatureForBuilding = "SetCoolingTemperatureForBuilding"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatDistributionControllerConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heat_distribution_controller_config = config
        super().__init__(
            self.heat_distribution_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
        )
        self.state_controller: int = 0

        self.build(
            set_heating_threshold_temperature=self.heat_distribution_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            # set_water_storage_temperature_for_heating_in_celsius=self.heat_distribution_controller_config.set_water_storage_temperature_for_heating_in_celsius,
            # set_water_storage_temperature_for_cooling_in_celsius=self.heat_distribution_controller_config.set_water_storage_temperature_for_cooling_in_celsius,
            set_heating_temperature_for_building_in_celsius=self.heat_distribution_controller_config.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=self.heat_distribution_controller_config.set_cooling_temperature_for_building_in_celsius,
        )

        # Inputs
        self.theoretical_thermal_building_demand_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.TheoreticalThermalBuildingDemand,
                lt.LoadTypes.HEATING,
                lt.Units.WATT,
                True,
            )
        )
        self.daily_avg_outside_temperature_input_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.DailyAverageOutsideTemperature,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )
        )
        self.water_temperature_input_from_heat_water_storage_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        # Outputs
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

        self.set_heating_temperature_for_building_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.SetHeatingTemperatureForBuilding,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.SetHeatingTemperatureForBuilding} will follow.",
        )

        self.set_cooling_temperature_for_building_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.SetCoolingTemperatureForBuilding,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.SetCoolingTemperatureForBuilding} will follow.",
        )

        self.controller_heat_distribution_mode: str = "off"
        self.previous_controller_heat_distribution_mode: str = "off"

        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )

    def get_default_connections_from_weather(
        self,
    ):
        """Get weather default connections."""
        log.information("setting weather default connections")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_building(
        self,
    ):
        """Get building default connections."""
        log.information("setting building default connections")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.TheoreticalThermalBuildingDemand,
                building_classname,
                Building.TheoreticalThermalBuildingDemand,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_hot_water_storage default connections."""
        log.information("setting simple_hot_water_storage default connections")
        connections = []
        hws_classname = SimpleHotWaterStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.WaterTemperatureInputFromHeatWaterStorage,
                hws_classname,
                SimpleHotWaterStorage.WaterTemperatureToHeatDistributionSystem,
            )
        )
        return connections

    def build(
        self,
        set_heating_threshold_temperature: float,
        # set_water_storage_temperature_for_heating_in_celsius: float,
        # set_water_storage_temperature_for_cooling_in_celsius: float,
        set_heating_temperature_for_building_in_celsius: float,
        set_cooling_temperature_for_building_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heat_distribution_mode = "off"
        self.previous_controller_heat_distribution_mode = (
            self.controller_heat_distribution_mode
        )

        # Configuration
        self.set_heating_threshold_temperature = set_heating_threshold_temperature
        # self.set_water_storage_temperature_for_heating_in_celsius = (
        #     set_water_storage_temperature_for_heating_in_celsius
        # )
        # self.set_water_storage_temperature_for_cooling_in_celsius = (
        #     set_water_storage_temperature_for_cooling_in_celsius
        # )
        self.set_heating_temperature_for_building_in_celsius = (
            set_heating_temperature_for_building_in_celsius
        )
        self.set_cooling_temperature_for_building_in_celsius = (
            set_cooling_temperature_for_building_in_celsius
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_heat_distribution_mode = (
            self.controller_heat_distribution_mode
        )

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heat_distribution_mode = self.controller_heat_distribution_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Heat Distribution Controller")
        # todo: add more useful stuff here
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution controller."""
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
                self.theoretical_thermal_building_demand_channel
            )
            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )
            # water_temperature_input_in_celsius = stsv.get_input_value(
            #     self.water_temperature_input_from_heat_water_storage_channel
            # )
            self.conditions_for_opening_or_shutting_heat_distribution(
                theoretical_thermal_building_demand_in_watt=theoretical_thermal_building_demand_in_watt,
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                # water_temperature_input_in_celsius=self.water_temperature_input_in_celsius,
            )

            if self.controller_heat_distribution_mode == "on":
                self.state_controller = 1
            elif self.controller_heat_distribution_mode == "off":
                self.state_controller = 0
            else:

                raise ValueError("unknown mode")

            stsv.set_output_value(self.state_channel, self.state_controller)
            stsv.set_output_value(
                self.set_heating_temperature_for_building_channel,
                self.set_heating_temperature_for_building_in_celsius,
            )
            stsv.set_output_value(
                self.set_cooling_temperature_for_building_channel,
                self.set_cooling_temperature_for_building_in_celsius,
            )

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        theoretical_thermal_building_demand_in_watt: float,
        daily_average_outside_temperature_in_celsius: float,
        # water_temperature_input_in_celsius: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""

        if self.controller_heat_distribution_mode == "on":
            # no heat exchange with building if theres no demand and if avg temp outside too high
            if (
                theoretical_thermal_building_demand_in_watt == 0
                and daily_average_outside_temperature_in_celsius
                > self.set_heating_threshold_temperature
                # or water_temperature_input_in_celsius < self.set_water_storage_temperature_for_heating_in_celsius
                # or water_temperature_input_in_celsius >= self.set_water_storage_temperature_for_cooling_in_celsius
            ):
                self.controller_heat_distribution_mode = "off"
                return
        elif self.controller_heat_distribution_mode == "off":
            # if heating or cooling is needed for building or if avg temp outside too low
            if (
                theoretical_thermal_building_demand_in_watt != 0
                or daily_average_outside_temperature_in_celsius
                < self.set_heating_threshold_temperature
                # and self.set_water_storage_temperature_for_heating_in_celsius
                # <= water_temperature_input_in_celsius
                # < self.set_water_storage_temperature_for_cooling_in_celsius
            ):
                self.controller_heat_distribution_mode = "on"
                return

        else:
            raise ValueError("unknown mode")
