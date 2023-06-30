"""Heat Distribution Module."""
# clean

from enum import IntEnum
from typing import List, Any, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hisim.component as cp
from hisim.components.building import Building
from hisim.components.simple_hot_water_storage import SimpleHotWaterStorage
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
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

    @classmethod
    def get_default_heatdistributionsystem_config(
        cls,
    ) -> Any:
        """Get a default heat distribution system config."""
        config = HeatDistributionConfig(
            name="HeatDistributionSystem",
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
    heating_system: HeatingSystemType
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    heating_reference_temperature_in_celsius: float
    set_heating_temperature_for_building_in_celsius: float
    set_cooling_temperature_for_building_in_celsius: float
    set_cooling_threshold_water_temperature_in_celsius_for_dew_protection: float

    @classmethod
    def get_default_heat_distribution_controller_config(cls):
        """Gets a default HeatDistribution Controller."""
        return HeatDistributionControllerConfig(
            name="HeatDistributionController",
            heating_system=HeatingSystemType.FLOORHEATING,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            heating_reference_temperature_in_celsius=-14.0,
            set_heating_temperature_for_building_in_celsius=18,
            set_cooling_temperature_for_building_in_celsius=25,
            set_cooling_threshold_water_temperature_in_celsius_for_dew_protection=17.0,
        )


@dataclass
class HeatDistributionSystemState:

    """HeatDistributionSystemState class."""

    water_output_temperature_in_celsius: float = 25
    thermal_power_delivered_in_watt: float = 0

    def self_copy(self):
        """Copy the Heat Distribution State."""
        return HeatDistributionSystemState(
            self.water_output_temperature_in_celsius,
            self.thermal_power_delivered_in_watt,
        )


class HeatDistribution(cp.Component):

    """Heat Distribution System.

    It simulates the heat exchange between heat generator and building.

    """

    # Inputs
    State = "State"
    WaterTemperatureInput = "WaterTemperatureInput"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    ResidenceTemperatureIndoorAir = "ResidenceTemperatureIndoorAir"

    # Outputs
    WaterTemperatureOutput = "WaterTemperatureOutput"
    ThermalPowerDelivered = "ThermalPowerDelivered"

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
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.heat_distribution_system_config = config

        self.thermal_power_delivered_in_watt: float = 0.0
        self.water_temperature_output_in_celsius: float = 21
        self.delta_temperature_in_celsius: float = 1.0

        if SingletonSimRepository().exist_entry(
            key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND
        ):
            self.max_thermal_building_demand_in_watt = (
                SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND
                )
            )

        else:
            raise KeyError(
                "Key for max thermal building demand was not found in the singleton sim repository."
                + "This might be because the building was not initialized before the heat distribution system."
                + "Please check the order of the initialization of the components in your example."
            )

        if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.HEATINGSYSTEM):
            self.heating_system = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATINGSYSTEM
            )

        else:
            raise KeyError(
                "Key for heating system was not found in the singleton sim repository."
                + "This might be because the heat distribution system controller was not initialized before the heat distribution system."
                + "Please check the order of the initialization of the components in your example."
            )

        self.build(heating_system=self.heating_system)

        self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second = (
            self.calc_heating_distribution_system_water_mass_flow_rate(
                self.max_thermal_building_demand_in_watt
            )
        )

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATINGDISTRIBUTIONSYSTEM,
            entry=self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second,
        )

        self.state: HeatDistributionSystemState = HeatDistributionSystemState(
            water_output_temperature_in_celsius=21, thermal_power_delivered_in_watt=0
        )
        self.previous_state = self.state.self_copy()

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
        # DIN/TS 18599-12: 2021-04, p.238
        if heating_system == HeatingSystemType.FLOORHEATING:
            self.delta_temperature_in_celsius = 7
        elif heating_system == HeatingSystemType.RADIATOR:
            self.delta_temperature_in_celsius = 15
        else:
            raise ValueError("unknown heating system.")

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()
        # pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()
        # pass

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

        # Get inputs ------------------------------------------------------------------------------------------------------------
        state_controller = stsv.get_input_value(self.state_channel)
        theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_demand_channel
        )

        water_temperature_input_in_celsius = stsv.get_input_value(
            self.water_temperature_input_channel
        )

        residence_temperature_input_in_celsius = stsv.get_input_value(
            self.residence_temperature_input_channel
        )

        if state_controller == 1:

            (
                self.water_temperature_output_in_celsius,
                self.thermal_power_delivered_in_watt,
            ) = self.determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power(
                water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                water_mass_flow_in_kg_per_second=self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second,
                theoretical_thermal_buiding_demand_in_watt=theoretical_thermal_building_demand_in_watt,
                residence_temperature_in_celsius=residence_temperature_input_in_celsius,
            )

        elif state_controller == 0:

            self.thermal_power_delivered_in_watt = 0.0

            self.water_temperature_output_in_celsius = (
                water_temperature_input_in_celsius
            )

        else:
            raise ValueError("unknown hds controller mode")

        # Set outputs -----------------------------------------------------------------------------------------------------------

        stsv.set_output_value(
            self.water_temperature_output_channel,
            self.state.water_output_temperature_in_celsius
            # self.water_temperature_output_in_celsius,
        )
        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.state.thermal_power_delivered_in_watt
            # self.thermal_power_delivered_in_watt,
        )

        self.state.water_output_temperature_in_celsius = (
            self.water_temperature_output_in_celsius
        )
        self.state.thermal_power_delivered_in_watt = (
            self.thermal_power_delivered_in_watt
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
        """Calculate cooled or heated water temperature after heat exchange between heat distribution system and building."""
        # Tout = Tin -  Q/(c * m)
        water_temperature_output_in_celsius = (
            water_temperature_input_in_celsius
            - theoretical_thermal_buiding_demand_in_watt
            / (
                water_mass_flow_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            )
        )

        if theoretical_thermal_buiding_demand_in_watt > 0:
            # water in hds must be warmer than the building in order to exchange heat
            if water_temperature_input_in_celsius > residence_temperature_in_celsius:
                # prevent that water output temperature in hds gets colder than residence temperature in building when heating
                water_temperature_output_in_celsius = max(
                    water_temperature_output_in_celsius,
                    residence_temperature_in_celsius,
                )
                thermal_power_delivered_effective_in_watt = (
                    self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * water_mass_flow_in_kg_per_second
                    * (
                        water_temperature_input_in_celsius
                        - water_temperature_output_in_celsius
                    )
                )
            else:
                # water in hds is not warmer than the building, therefore heat exchange is not possible
                water_temperature_output_in_celsius = water_temperature_input_in_celsius
                thermal_power_delivered_effective_in_watt = 0

        elif theoretical_thermal_buiding_demand_in_watt < 0:
            # water in hds must be cooler than the building in order to cool building down
            if water_temperature_input_in_celsius < residence_temperature_in_celsius:
                # prevent that water output temperature in hds gets hotter than residence temperature in building when cooling
                water_temperature_output_in_celsius = min(
                    water_temperature_output_in_celsius,
                    residence_temperature_in_celsius,
                )
                thermal_power_delivered_effective_in_watt = (
                    self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * water_mass_flow_in_kg_per_second
                    * (
                        water_temperature_input_in_celsius
                        - water_temperature_output_in_celsius
                    )
                )
            else:
                # water in hds is not colder than building and therefore cooling is not possible
                water_temperature_output_in_celsius = water_temperature_input_in_celsius
                thermal_power_delivered_effective_in_watt = 0

        # in case no heating or cooling needed, water output is equal to water input
        elif theoretical_thermal_buiding_demand_in_watt == 0:
            water_temperature_output_in_celsius = water_temperature_input_in_celsius
            thermal_power_delivered_effective_in_watt = 0
        else:
            raise ValueError(
                f"Theoretical thermal demand has unacceptable value here {theoretical_thermal_buiding_demand_in_watt}."
            )

        return (
            water_temperature_output_in_celsius,
            thermal_power_delivered_effective_in_watt,
        )


class HeatDistributionController(cp.Component):

    """Heat Distribution Controller.

    It takes data from the building, weather and water storage and sends signal to the heat distribution for
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
    HeatingFlowTemperature = "HeatingFlowTemperature"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatDistributionControllerConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.hsd_controller_config = config
        super().__init__(
            self.hsd_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.state_controller: int = 0

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.SETHEATINGTEMPERATUREFORBUILDING,
            entry=self.hsd_controller_config.set_heating_temperature_for_building_in_celsius,
        )

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.SETCOOLINGTEMPERATUREFORBUILDING,
            entry=self.hsd_controller_config.set_cooling_temperature_for_building_in_celsius,
        )

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.HEATINGSYSTEM,
            entry=self.hsd_controller_config.heating_system,
        )

        self.build(
            set_heating_threshold_temperature_in_celsius=self.hsd_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            heating_reference_temperature_in_celsius=self.hsd_controller_config.heating_reference_temperature_in_celsius,
            heating_system_type=self.hsd_controller_config.heating_system,
            set_cooling_threshold_water_temperature_in_celsius=self.hsd_controller_config.set_cooling_threshold_water_temperature_in_celsius_for_dew_protection,
        )
        self.prepare_calc_heating_dist_temperature(
            set_room_temperature_for_building_in_celsius=self.hsd_controller_config.set_heating_temperature_for_building_in_celsius,
            factor_of_oversizing_of_heat_distribution_system=1.0,
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
        self.heating_flow_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingFlowTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.HeatingFlowTemperature} will follow.",
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
        set_heating_threshold_temperature_in_celsius: Optional[float],
        heating_reference_temperature_in_celsius: float,
        heating_system_type: HeatingSystemType,
        set_cooling_threshold_water_temperature_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Configuration
        self.set_heating_threshold_temperature_in_celsius = (
            set_heating_threshold_temperature_in_celsius
        )
        self.heating_reference_temperature_in_celsius = (
            heating_reference_temperature_in_celsius
        )
        self.heating_system_type = heating_system_type

        self.set_cooling_threshold_water_temperature_in_celsius_for_dew_protection = (
            set_cooling_threshold_water_temperature_in_celsius
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
            water_input_temperature_in_celsius = stsv.get_input_value(
                self.water_temperature_input_from_heat_water_storage_channel
            )

            list_of_heating_distribution_system_flow_and_return_temperatures = self.calc_heat_distribution_flow_and_return_temperatures(
                daily_avg_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius
            )

            self.conditions_for_opening_or_shutting_heat_distribution(
                theoretical_thermal_building_demand_in_watt=theoretical_thermal_building_demand_in_watt,
            )

            # no heating threshold for the heat distribution system
            if (
                self.hsd_controller_config.set_heating_threshold_outside_temperature_in_celsius
                is None
            ):
                summer_heating_mode = "on"

            # turning heat distributon system off when the average daily outside temperature is above a certain threshold
            else:
                summer_heating_mode = self.summer_heating_condition(
                    daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                    set_heating_threshold_temperature_in_celsius=self.hsd_controller_config.set_heating_threshold_outside_temperature_in_celsius,
                )

            dew_point_protection_mode = self.dew_point_protection_condition(
                water_input_temperature_in_celsius=water_input_temperature_in_celsius,
                set_cooling_threshold_water_temperature_in_celsius_for_dew_protection=self.set_cooling_threshold_water_temperature_in_celsius_for_dew_protection,
            )

            if (
                self.controller_heat_distribution_mode == "on"
                and summer_heating_mode == "on"
                and dew_point_protection_mode == "off"
            ):
                self.state_controller = 1
            elif self.controller_heat_distribution_mode == "on" and (
                summer_heating_mode == "off" or dew_point_protection_mode == "on"
            ):
                self.state_controller = 0
            elif self.controller_heat_distribution_mode == "off":
                self.state_controller = 0
            else:

                raise ValueError(
                    "unknown hds controller mode or summer mode or dew point protection mode."
                )

            stsv.set_output_value(self.state_channel, self.state_controller)
            stsv.set_output_value(
                self.heating_flow_temperature_channel,
                list_of_heating_distribution_system_flow_and_return_temperatures[0],
            )

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        theoretical_thermal_building_demand_in_watt: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""

        if self.controller_heat_distribution_mode == "on":
            # no heat exchange with building if theres no demand
            if theoretical_thermal_building_demand_in_watt == 0:
                self.controller_heat_distribution_mode = "off"
                return
        elif self.controller_heat_distribution_mode == "off":
            # if heating or cooling is needed for building
            if theoretical_thermal_building_demand_in_watt != 0:
                self.controller_heat_distribution_mode = "on"
                return

        else:
            raise ValueError("unknown hds controller mode.")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: float,
    ) -> str:
        """Set conditions for the valve in heat distribution."""

        if (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "off"

        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )

        return heating_mode

    def dew_point_protection_condition(
        self,
        water_input_temperature_in_celsius: float,
        set_cooling_threshold_water_temperature_in_celsius_for_dew_protection: float,
    ) -> str:
        """Set conditions for the valve in heat distribution."""

        if (
            water_input_temperature_in_celsius
            > set_cooling_threshold_water_temperature_in_celsius_for_dew_protection
        ):
            dew_point_protection_mode = "off"

        elif (
            water_input_temperature_in_celsius
            < set_cooling_threshold_water_temperature_in_celsius_for_dew_protection
        ):
            dew_point_protection_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {water_input_temperature_in_celsius}°C"
                f"or cooling threshold water temperature {set_cooling_threshold_water_temperature_in_celsius_for_dew_protection}°C is not acceptable."
            )

        return dew_point_protection_mode

    def prepare_calc_heating_dist_temperature(
        self,
        set_room_temperature_for_building_in_celsius: float = 20.0,
        factor_of_oversizing_of_heat_distribution_system: float = 1.0,
    ) -> None:
        """Function to set several input parameters for functions regarding the heating system.

        This function is taken from the HeatingSystem class of hplib and slightly adapted here.
        """

        self.set_room_temperature_for_building_in_celsius = (
            set_room_temperature_for_building_in_celsius
        )
        if self.heating_system_type == HeatingSystemType.FLOORHEATING:
            list_of_maximum_flow_and_return_temperatures_in_celsius = [35, 28]
            exponent_factor_of_heating_distribution_system = 1.1

        elif self.heating_system_type == HeatingSystemType.RADIATOR:
            list_of_maximum_flow_and_return_temperatures_in_celsius = [70, 55]
            exponent_factor_of_heating_distribution_system = 1.3
        else:
            raise ValueError(
                "Heating System Type not defined here. Check your heat distribution controller config or your Heating System Type class."
            )

        self.max_flow_temperature_in_celsius = (
            list_of_maximum_flow_and_return_temperatures_in_celsius[0]
        )
        self.min_flow_temperature_in_celsius = (
            set_room_temperature_for_building_in_celsius
        )
        self.max_return_temperature_in_celsius = (
            list_of_maximum_flow_and_return_temperatures_in_celsius[1]
        )
        self.min_return_temperature_in_celsius = (
            set_room_temperature_for_building_in_celsius
        )
        self.factor_of_oversizing_of_heat_distribution_system = (
            factor_of_oversizing_of_heat_distribution_system
        )
        self.exponent_factor_of_heating_distribution_system = (
            exponent_factor_of_heating_distribution_system
        )

    def calc_heat_distribution_flow_and_return_temperatures(
        self, daily_avg_outside_temperature_in_celsius: float
    ) -> List[float]:
        """Calculate the heat distribution flow and return temperature as a function of the moving average daily mean outside temperature.

        Calculations are based on DIN/TS 18599-12: 2021-04, p.170, Eq. 127,128

        Returns
        -------
        list with heating flow and heating return temperature

        """
        # cooling case, daily avg temperature is higher than set indoor temperature.
        # flow and return temperatures can not be lower than set indoor temperature (because number would be complex)
        if (
            self.set_room_temperature_for_building_in_celsius
            < daily_avg_outside_temperature_in_celsius
        ):
            # prevent that flow and return temperatures get colder than 19 °C because this could cause condensation of the indoor air on the heating system
            # https://suissetec.ch/files/PDFs/Merkblaetter/Heizung/Deutsch/2021_11_MB_Kuehlung_mit_Fussbodenheizung_DE_Web.pdf

            flow_temperature_in_celsius = max(
                self.min_flow_temperature_in_celsius, 19.0
            )
            return_temperature_in_celsius = max(
                self.min_return_temperature_in_celsius, 19.0
            )

        else:
            # heating case, daily avg outside temperature is lower than indoor temperature
            flow_temperature_in_celsius = float(
                self.min_flow_temperature_in_celsius
                + (
                    (1 / self.factor_of_oversizing_of_heat_distribution_system)
                    * (
                        (
                            self.set_room_temperature_for_building_in_celsius
                            - daily_avg_outside_temperature_in_celsius
                        )
                        / (
                            self.set_room_temperature_for_building_in_celsius
                            - self.heating_reference_temperature_in_celsius
                        )
                    )
                )
                ** (1 / self.exponent_factor_of_heating_distribution_system)
                * (
                    self.max_flow_temperature_in_celsius
                    - self.min_flow_temperature_in_celsius
                )
            )
            return_temperature_in_celsius = float(
                self.min_return_temperature_in_celsius
                + (
                    (1 / self.factor_of_oversizing_of_heat_distribution_system)
                    * (
                        (
                            self.set_room_temperature_for_building_in_celsius
                            - daily_avg_outside_temperature_in_celsius
                        )
                        / (
                            self.set_room_temperature_for_building_in_celsius
                            - self.heating_reference_temperature_in_celsius
                        )
                    )
                )
                ** (1 / self.exponent_factor_of_heating_distribution_system)
                * (
                    self.max_return_temperature_in_celsius
                    - self.min_return_temperature_in_celsius
                )
            )

        list_of_heating_flow_and_return_temperature_in_celsius = [
            flow_temperature_in_celsius,
            return_temperature_in_celsius,
        ]

        return list_of_heating_flow_and_return_temperature_in_celsius
