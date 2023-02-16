"""Heat Distribution Module."""
# clean
# Owned
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
from hisim.components.configuration import PhysicsConfig
from hisim import loadtypes as lt
from hisim import utils
from hisim import log

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


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

    @classmethod
    def get_default_heatdistributionsystem_config(
        cls,
    ) -> Any:
        """Get a default heat distribution system config."""
        config = HeatDistributionConfig(
            name="HeatDistributionSystem",
            water_temperature_in_distribution_system_in_celsius=60,
        )
        return config


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
    # ResidenceTemperature = "ResidenceTemperature"
    WaterTemperatureInput = "WaterTemperatureInput"
    MaxThermalBuildingDemand = "MaxThermalBuildingDemand"
    # MaxWaterMassFlowRate = "MaxWaterMassFlowRate"
    RealHeatBuildingDemand = "RealHeatBuildingDemand"

    # Outputs
    WaterTemperatureOutput = "WaterTemperatureOutput"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    FloorHeatingWaterMassFlowRate = "FloorHeatingWaterMassFlowRate"

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
        self.state = HeatDistributionState(
            self.heat_distribution_system_config.water_temperature_in_distribution_system_in_celsius
        )
        self.state_controller: float = 0.0
        # self.residence_temperature_in_celsius: float = 0.0
        self.water_temperature_input_in_celsius: float = 0.0
        self.floor_heating_water_mass_flow_rate_in_kg_per_second: float = 0
        # self.max_water_mass_flow_rate_in_kg_per_second: float = 0.0
        self.heat_gain_for_building_in_watt: float = 0.0
        self.water_temperature_output_in_celsius: float = 0.0
        self.max_thermal_building_demand_in_watt: float = 0.0
        self.real_heat_building_demand_in_watt: float = 0.0
        self.build()

        # Inputs

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )
        # self.max_water_mass_flow_rate_channel: cp.ComponentInput = self.add_input(

        #     self.component_name,
        #     self.MaxWaterMassFlowRate,
        #     lt.LoadTypes.WARM_WATER,
        #     lt.Units.KG_PER_SEC,
        #     True,
        # )
        self.real_heat_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.RealHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.max_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        # self.residence_temperature_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.ResidenceTemperature,
        #     lt.LoadTypes.TEMPERATURE,
        #     lt.Units.CELSIUS,
        #     True,
        # )
        self.water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )
        # Outputs
        self.water_temperature_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureOutput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
        )
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )
        self.floor_heating_water_mass_flow_rate_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.FloorHeatingWaterMassFlowRate,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
            )
        )

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )

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
        lines = []
        lines.append("Heat Distribution System")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution system."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        self.state_controller = stsv.get_input_value(self.state_channel)
        self.real_heat_building_demand_in_watt = stsv.get_input_value(
            self.real_heat_building_demand_channel
        )
        # self.residence_temperature_in_celsius = stsv.get_input_value(
        #     self.residence_temperature_channel
        # )
        self.water_temperature_input_in_celsius = stsv.get_input_value(
            self.water_temperature_input_channel
        )
        # self.max_water_mass_flow_rate_in_kg_per_second = stsv.get_input_value(
        #     self.max_water_mass_flow_rate_channel
        # )
        self.max_thermal_building_demand_in_watt = stsv.get_input_value(
            self.max_thermal_building_demand_channel
        )
        # Calculations ----------------------------------------------------------------------------------------------------------
        self.floor_heating_water_mass_flow_rate_in_kg_per_second = (
            self.calc_floor_heating_system_water_mass_flow_rate(
                self.max_thermal_building_demand_in_watt
            )
        )
        self.state.water_temperature_in_distribution_system_in_celsius = (
            self.water_temperature_input_in_celsius
        )
        if self.state_controller == 1:

            # self.calculate_heat_gain_for_building(
            #     self.floor_heating_water_mass_flow_rate_in_kg_per_second,
            #     self.water_temperature_input_in_celsius,
            #     self.residence_temperature_in_celsius,
            # )

            # building gets the heat that it needs
            self.heat_gain_for_building_in_watt = self.real_heat_building_demand_in_watt

            # self.water_temperature_output_in_celsius = self.determine_water_temperature_output_after_heat_exchange_with_building(
            #     self.residence_temperature_in_celsius,
            # )
            self.water_temperature_output_in_celsius = self.determine_water_temperature_output_after_heat_exchange_with_building(
                water_temperature_input_in_celsius=self.water_temperature_input_in_celsius,
                water_mass_flow_in_kg_per_second=self.floor_heating_water_mass_flow_rate_in_kg_per_second,
                real_heat_buiding_demand_in_watt=self.real_heat_building_demand_in_watt,
            )

        elif self.state_controller == 0:

            self.heat_gain_for_building_in_watt = 0.0

            self.water_temperature_output_in_celsius = (
                self.water_temperature_input_in_celsius
            )

        else:
            raise ValueError("unknown mode")

        # Set outputs -----------------------------------------------------------------------------------------------------------
        self.state.water_temperature_in_distribution_system_in_celsius = (
            self.water_temperature_output_in_celsius
        )
        # log.information("hsd timestep " + str(timestep))
        # log.information("hsd water temperature output " + str(self.state.water_temperature_in_distribution_system_in_celsius))
        # log.information("hsd water mass flow rate "  + str(self.floor_heating_water_mass_flow_rate_in_kg_per_second))
        # log.information("hsd heat gain " + str(self.heat_gain_for_building_in_watt))

        stsv.set_output_value(
            self.water_temperature_output_channel,
            self.water_temperature_output_in_celsius,
        )
        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.heat_gain_for_building_in_watt,
        )
        stsv.set_output_value(
            self.floor_heating_water_mass_flow_rate_channel,
            self.floor_heating_water_mass_flow_rate_in_kg_per_second,
        )

    def calc_floor_heating_system_water_mass_flow_rate(
        self,
        max_thermal_building_demand_in_watt: float,
    ) -> Any:
        """Calculate water mass flow between floor heating system and hot water storage."""
        specific_heat_capacity_of_water_in_joule_per_kg_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        # information from Noah: deltaT for floor heating = 3 K or °C
        delta_temperature_for_floor_heating_in_celsius = 3
        floor_heating_water_mass_flow_in_kg_per_second = (
            max_thermal_building_demand_in_watt
            / (
                specific_heat_capacity_of_water_in_joule_per_kg_per_celsius
                * delta_temperature_for_floor_heating_in_celsius
            )
        )
        return floor_heating_water_mass_flow_in_kg_per_second

    # def calculate_heat_gain_for_building(
    #     self,
    #     water_mass_flow_in_kg_per_second,
    #     heated_water_temperature_in_celsius,
    #     residence_temperature_in_celsius,
    # ):
    #     """Calculate heat gain for the building from heat distribution system."""
    #     self.heat_gain_for_building_in_watt = (
    #         water_mass_flow_in_kg_per_second
    #         * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
    #         * (heated_water_temperature_in_celsius - residence_temperature_in_celsius)
    #     )

    # def determine_water_temperature_output_after_heat_exchange_with_building(
    #     self, residence_temperature_in_celsius
    # ):
    #     """Calculate cooled water temperature after heat exchange between heat distribution system and building."""
    #     cooled_water_temperature_output_in_celsius = residence_temperature_in_celsius
    #     return cooled_water_temperature_output_in_celsius

    def determine_water_temperature_output_after_heat_exchange_with_building(
        self,
        water_mass_flow_in_kg_per_second,
        water_temperature_input_in_celsius,
        real_heat_buiding_demand_in_watt,
    ):
        """Calculate cooled water temperature after heat exchange between heat distribution system and building."""
        # Tnew = Told -  Q/(c * m)
        water_temperature_output_in_celsius = (
            water_temperature_input_in_celsius
            - real_heat_buiding_demand_in_watt
            / (
                water_mass_flow_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            )
        )
        return water_temperature_output_in_celsius


class HeatDistributionController(cp.Component):

    """Heat Distribution Controller.

    It takes data from other
    components and sends signal to the heat distribution for
    activation or deactivation.

    """

    # Inputs
    # ResidenceTemperature = "ResidenceTemperature"
    RealHeatBuildingDemand = "RealHeatBuildingDemand"
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    # Outputs
    State = "State"
    RealHeatBuildingDemandPassedToHeatDistributionSystem = (
        "RealHeatBuildingDemandPassedToHeatDistributionSystem"
    )

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        # min_heating_temperature_building_in_celsius: float = 20.0,
        set_heating_threshold_temperature_in_celsius: float = 16.0,
        mode: int = 1,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "HeatDistributionController",
            my_simulation_parameters=my_simulation_parameters,
        )
        self.state_controller: int = 0
        self.start_timestep: int = 0
        self.real_heat_building_demand_in_watt: float = 0.0
        self.build(
            # set_min_heating_temperature_residence_in_celsius=min_heating_temperature_building_in_celsius,
            set_heating_threshold_temperature=set_heating_threshold_temperature_in_celsius,
            mode=mode,
        )

        # self.residence_temperature_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.ResidenceTemperature,
        #     lt.LoadTypes.TEMPERATURE,
        #     lt.Units.CELSIUS,
        #     False,
        # )
        self.real_heat_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.RealHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
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
        self.real_heat_building_demand_passed_to_hds_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.RealHeatBuildingDemandPassedToHeatDistributionSystem,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            )
        )
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY
        )

        self.add_default_connections(self.get_default_connections_from_building())
        self.controller_heat_distribution_mode: str = "off"
        self.previous_controller_heat_distribution_mode: str = "off"

    def get_default_connections_from_building(self) -> List[cp.ComponentConnection]:
        """Get building default connections."""
        log.information(
            "setting building default connections in HeatDistributionController"
        )
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.ResidenceTemperature,
                building_classname,
                Building.TemperatureMeanThermalMass,
            )
        )
        return connections

    def build(
        self,
        # set_min_heating_temperature_residence_in_celsius: float,
        set_heating_threshold_temperature: float,
        mode: int,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heat_distribution_mode = "off"
        self.previous_controller_heat_distribution_mode = self.controller_heat_distribution_mode

        # Configuration
        # self.set_residence_temperature_in_celsius = (
        #     set_min_heating_temperature_residence_in_celsius
        # )

        self.set_heating_threshold_temperature = set_heating_threshold_temperature
        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_heat_distribution_mode = self.controller_heat_distribution_mode

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
        # lines.append(
        #     "Set Temperature of Residence [°C]: "
        #     + str(self.set_residence_temperature_in_celsius)
        # )
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution controller."""
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            # residence_temperature_in_celsius = stsv.get_input_value(
            #     self.residence_temperature_channel
            # )
            self.real_heat_building_demand_in_watt = stsv.get_input_value(
                self.real_heat_building_demand_channel
            )
            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            if self.mode == 1:
                # self.conditions_for_opening_or_shutting_heat_distribution(
                #     residence_temperature_in_celsius=residence_temperature_in_celsius,
                #     daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                # )
                self.conditions_for_opening_or_shutting_heat_distribution(
                    real_heat_building_demand_in_watt=self.real_heat_building_demand_in_watt,
                    daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                )

            if self.controller_heat_distribution_mode == "on":
                self.state_controller = 1
            elif self.controller_heat_distribution_mode == "off":
                self.state_controller = 0
            else:
                raise ValueError("unknown mode")

            # log.information("hds controller " + str(self.state_controller) + "\n")
            stsv.set_output_value(self.state_channel, self.state_controller)
            stsv.set_output_value(
                self.real_heat_building_demand_passed_to_hds_channel,
                self.real_heat_building_demand_in_watt,
            )

    # def conditions_for_opening_or_shutting_heat_distribution(
    #     self,
    #     residence_temperature_in_celsius: float,
    #     daily_average_outside_temperature_in_celsius: float,
    # ) -> None:
    #     """Set conditions for the valve in heat distribution."""
    #     set_residence_temperature_in_celsius = self.set_residence_temperature_in_celsius

    #     if self.controller_heat_distribution_mode == "open":
    #         if (
    #             residence_temperature_in_celsius >= set_residence_temperature_in_celsius
    #             or daily_average_outside_temperature_in_celsius
    #             > self.set_heating_threshold_temperature
    #         ):
    #             self.controller_heat_distribution_mode = "close"
    #             return
    #     elif self.controller_heat_distribution_mode == "close":
    #         if (
    #             residence_temperature_in_celsius < set_residence_temperature_in_celsius
    #             and daily_average_outside_temperature_in_celsius
    #             < self.set_heating_threshold_temperature
    #         ):
    #             self.controller_heat_distribution_mode = "open"
    #             return
    #     else:
    #         raise ValueError("unknown mode")

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        real_heat_building_demand_in_watt: float,
        daily_average_outside_temperature_in_celsius: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""
        # set_residence_temperature_in_celsius = self.set_residence_temperature_in_celsius

        if self.controller_heat_distribution_mode == "on":
            if (
                real_heat_building_demand_in_watt == 0
                or daily_average_outside_temperature_in_celsius
                > self.set_heating_threshold_temperature
            ):
                self.controller_heat_distribution_mode = "off"
                return
        elif self.controller_heat_distribution_mode == "off":
            if (
                real_heat_building_demand_in_watt != 0
                and daily_average_outside_temperature_in_celsius
                < self.set_heating_threshold_temperature
            ):
                self.controller_heat_distribution_mode = "on"
                return
        else:
            raise ValueError("unknown mode")
