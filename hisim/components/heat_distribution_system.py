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
    ResidenceTemperature = "ResidenceTemperature"
    HeatedWaterTemperatureInput = "HeatedWaterTemperatureInput"
    MaxWaterMassFlowRate = "MaxWaterMassFlowRate"

    # Outputs
    CooledWaterTemperatureOutput = "CooledWaterTemperatureOutput"
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
            name=config.name, my_simulation_parameters=my_simulation_parameters
        )
        self.heat_distribution_system_config = config
        self.state = HeatDistributionState(
            self.heat_distribution_system_config.water_temperature_in_distribution_system_in_celsius
        )
        self.state_controller: float = 0.0
        self.residence_temperature_in_celsius: float = 0.0
        self.heated_water_temperature_input_in_celsius: float = 0.0
        self.max_water_mass_flow_rate_in_kg_per_second: float = 0.0
        self.heat_gain_for_building_in_watt: float = 0.0
        self.cooled_water_temperature_output_in_celsius: float = 0.0
        self.build()

        # Inputs

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )
        self.max_water_mass_flow_rate_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxWaterMassFlowRate,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            True,
        )
        self.residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.heated_water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatedWaterTemperatureInput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )
        # Outputs
        self.cooled_water_temperature_output_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.CooledWaterTemperatureOutput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
            )
        )
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin

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
        self.residence_temperature_in_celsius = stsv.get_input_value(
            self.residence_temperature_channel
        )
        self.heated_water_temperature_input_in_celsius = stsv.get_input_value(
            self.heated_water_temperature_input_channel
        )
        self.max_water_mass_flow_rate_in_kg_per_second = stsv.get_input_value(
            self.max_water_mass_flow_rate_channel
        )
        # Calculations ----------------------------------------------------------------------------------------------------------
        self.state.water_temperature_in_distribution_system_in_celsius = (
            self.heated_water_temperature_input_in_celsius
        )
        if self.state_controller == 1:

            self.cooled_water_temperature_output_in_celsius = self.determine_cooled_water_temperature_after_heat_exchange_with_building(
                self.residence_temperature_in_celsius,
            )
            self.calculate_heat_gain_for_building(
                self.max_water_mass_flow_rate_in_kg_per_second,
                self.heated_water_temperature_input_in_celsius,
                self.residence_temperature_in_celsius,
            )


        elif self.state_controller == 0:

            self.cooled_water_temperature_output_in_celsius = (
                self.heated_water_temperature_input_in_celsius
            )

            self.heat_gain_for_building_in_watt = 0.0

        # Set outputs -----------------------------------------------------------------------------------------------------------
        self.state.water_temperature_in_distribution_system_in_celsius = (
            self.cooled_water_temperature_output_in_celsius
        )
        # log.information("hsd timestep " + str(timestep))
        # log.information("hsd cooled output water temperature " + str(self.state.water_temperature_in_distribution_system_in_celsius))
        # log.information("hsd heat gain " + str(self.heat_gain_for_building_in_watt))
        stsv.set_output_value(
            self.cooled_water_temperature_output_channel,
            self.state.water_temperature_in_distribution_system_in_celsius,
        )
        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.heat_gain_for_building_in_watt,
        )



    def calculate_heat_gain_for_building(
        self,
        max_water_mass_flow_in_kg_per_second,
        heated_water_temperature_in_celsius,
        residence_temperature_in_celsius,
    ):
        """Calculate heat gain for the building from heat distribution system."""
        self.heat_gain_for_building_in_watt = (
            max_water_mass_flow_in_kg_per_second
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * (heated_water_temperature_in_celsius - residence_temperature_in_celsius)
        )

    def determine_cooled_water_temperature_after_heat_exchange_with_building(
        self, residence_temperature_in_celsius
    ):
        """Calculate cooled water temperature after heat exchange between heat distribution system and building."""
        cooled_water_temperature_output_in_celsius = (
            residence_temperature_in_celsius
        )
        return cooled_water_temperature_output_in_celsius


class HeatDistributionController(cp.Component):

    """Heat Distribution Controller.

    It takes data from other
    components and sends signal to the heat distribution for
    activation or deactivation.

    """

    # Inputs
    ResidenceTemperature = "ResidenceTemperature"
    WaterTemperatureFromHeatWaterStorage = "WaterTemperatureFromHeatWaterStorage"
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        min_heating_temperature_building_in_celsius: float = 20.0,
        min_heating_temperature_heat_water_storage_in_celsius: float = 55.0,
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
        self.build(
            set_min_heating_temperature_residence_in_celsius=min_heating_temperature_building_in_celsius,
            set_min_heating_temperature_water_storage_in_celsius=min_heating_temperature_heat_water_storage_in_celsius,
            set_heating_threshold_temperature=set_heating_threshold_temperature_in_celsius,
            mode=mode,
        )

        self.residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureFromHeatWaterStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
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
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY
        )

        self.add_default_connections(self.get_default_connections_from_building())
        self.controller_heat_distribution_mode: str = "close"
        self.previous_controller_gas_valve_mode: str = "close"

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
        set_min_heating_temperature_residence_in_celsius: float,
        set_min_heating_temperature_water_storage_in_celsius: float,
        set_heating_threshold_temperature: float,
        mode: int,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heat_distribution_mode = "off"
        self.previous_controller_gas_valve_mode = self.controller_heat_distribution_mode

        # Configuration
        self.set_residence_temperature_in_celsius = (
            set_min_heating_temperature_residence_in_celsius
        )
        self.set_water_storage_temperature_in_celsius = (
            set_min_heating_temperature_water_storage_in_celsius
        )
        self.set_heating_threshold_temperature = set_heating_threshold_temperature
        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_gas_valve_mode = self.controller_heat_distribution_mode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heat_distribution_mode = self.previous_controller_gas_valve_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Heat Distribution Controller")
        # todo: add more useful stuff here
        lines.append(
            "Set Temperature of Residence [°C]: "
            + str(self.set_residence_temperature_in_celsius)
        )
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution controller."""
        # Retrieves inputs
        residence_temperature_in_celsius = stsv.get_input_value(
            self.residence_temperature_channel
        )
        water_temperature_input = stsv.get_input_value(
            self.water_temperature_input_channel
        )
        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

        if self.mode == 1:
            self.conditions_for_opening_or_shutting_heat_distribution(
                residence_temperature_in_celsius=residence_temperature_in_celsius,
                water_temperature_in_celsius=water_temperature_input,
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
            )

        if self.controller_heat_distribution_mode == "open":
            self.state_controller = 1
        else:
            self.state_controller = 0

        # log.information("hds controller " + str(self.state_controller) + "\n")
        stsv.set_output_value(self.state_channel, self.state_controller)

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        residence_temperature_in_celsius: float,
        water_temperature_in_celsius: float,
        daily_average_outside_temperature_in_celsius: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""
        set_residence_temperature_in_celsius = self.set_residence_temperature_in_celsius
        set_water_storage_temperature_in_celsius = self.set_water_storage_temperature_in_celsius

        if self.controller_heat_distribution_mode == "open": 
            if residence_temperature_in_celsius >= set_residence_temperature_in_celsius or daily_average_outside_temperature_in_celsius > self.set_heating_threshold_temperature: # or water_temperature_in_celsius < set_water_storage_temperature_in_celsius:
                self.controller_heat_distribution_mode = "close"
                return
        if self.controller_heat_distribution_mode == "close":
            if residence_temperature_in_celsius < set_residence_temperature_in_celsius and daily_average_outside_temperature_in_celsius < self.set_heating_threshold_temperature: # or water_temperature_in_celsius <= set_water_storage_temperature_in_celsius:
                self.controller_heat_distribution_mode = "open"
                return
