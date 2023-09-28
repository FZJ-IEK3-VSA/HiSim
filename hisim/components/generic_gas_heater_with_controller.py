"""Gas Heater Module with Controller."""
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
class GenericGasHeaterWithControllerConfig(cp.ConfigBase):

    """Configuration of the GasHeater class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return GasHeaterWithController.get_full_classname()

    name: str
    min_operation_time: int
    min_idle_time: int
    maximal_thermal_power_in_watt: float

    @classmethod
    def get_default_gasheater_config(
        cls,
    ) -> Any:
        """Get a default gasheater config."""
        config = GenericGasHeaterWithControllerConfig(
            name="Gasheater",
            min_operation_time=30,
            min_idle_time=10,
            maximal_thermal_power_in_watt=12_000,
        )
        return config


@dataclass_json
@dataclass
class GenericGasHeaterControllerConfig(cp.ConfigBase):

    """Configuration of the GasHeaterController class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return GasHeaterController.get_full_classname()

    name: str
    set_heating_temperature_water_boiler_in_celsius: float
    offset: float
    mode: int

    @classmethod
    def get_default_controller_config(
        cls,
    ) -> Any:
        """Get a default gasheater config."""
        config = GenericGasHeaterControllerConfig(
            name="GasheaterController",
            set_heating_temperature_water_boiler_in_celsius=60.0,
            offset=2.0,
            mode=1,
        )
        return config


class GasHeaterWithController(cp.Component):

    """GasHeater class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    State = "State"
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"
    InitialResidenceTemperature = "InitialResidenceTemperature"
    ResidenceTemperature = "Residence Temperature"
    CooledWaterTemperatureBoilerInput = "CooledWaterTemperatureBoilerInput"

    # Output
    ControlSignalfromHeaterToDistribution = "ControlSignalfromHeaterToDistribution"
    MeanWaterTemperatureBoilerOutput = "MeanWaterTemperatureBoilerOutput"
    HeatedWaterTemperatureBoilerOutput = "HeatedWaterTemperatureBoilerOutput"
    GasPower = "GasPower"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    MaxMassFlow = "MaxMassFlow"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericGasHeaterWithControllerConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        # =================================================================================================================================
        # Initialization of variables
        self.start_timestep_gas_heater: int = 0
        self.max_mass_flow_in_kg_per_second: float = 0
        self.initial_temperature_water_boiler_in_celsius: float = 35.0
        self.initial_temperature_building_in_celsius: float = 0.0
        self.control_signal_from_heater_to_heat_distribution: int = 0
        self.temperature_gain_in_celsius: float = 0.0
        self.cooled_water_temperature_return_to_water_boiler_in_celsius = (
            self.initial_temperature_water_boiler_in_celsius
        )
        self.heated_water_temperature_in_boiler_in_celsius: float = 0.0
        self.mean_water_temperature_in_boiler_in_celsius: float = (
            self.initial_temperature_water_boiler_in_celsius
        )
        self.state_gas_controller: float = 0

        self.mean_temperature_building_in_celsius: float = 0.0
        self.ref_max_thermal_building_demand_in_watt: float = 0.0

        # Config Values
        self.maximal_thermal_power_in_watt = config.maximal_thermal_power_in_watt

        self.build(config.min_operation_time, config.min_idle_time)
        # =================================================================================================================================
        # Input channels
        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )
        self.cooled_water_temperature_boiler_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.CooledWaterTemperatureBoilerInput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
                True,
            )
        )
        self.initial_temperature_building_channel: ComponentInput = self.add_input(
            self.component_name,
            self.InitialResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.temperature_mean_building_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.ref_max_thermal_building_demand_channel: ComponentInput = self.add_input(
            self.component_name,
            self.ReferenceMaxHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )

        # Output channels

        self.control_signal_from_heater_to_heat_distribution_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalfromHeaterToDistribution,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
        )
        self.heated_water_temperature_boiler_output_channel: ComponentOutput = (
            self.add_output(
                self.component_name,
                self.HeatedWaterTemperatureBoilerOutput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
            )
        )

        self.mean_water_temperature_boiler_output_channel: ComponentOutput = (
            self.add_output(
                self.component_name,
                self.MeanWaterTemperatureBoilerOutput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
            )
        )

        self.gas_power_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.GasPower,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )

        self.max_mass_flow_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.MaxMassFlow,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
        )

        self.add_default_connections(
            self.get_default_connections_gasheater_controller()
        )

    def get_default_connections_gasheater_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get gas heater controller default connections."""
        log.information("setting controller default connections in GasHeater")
        connections = []
        controller_classname = GasHeaterController.get_classname()
        connections.append(
            cp.ComponentConnection(
                self.State, controller_classname, GasHeaterController.State
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        lines: List = []
        lines.append("Gas Heater")
        lines.append(
            "Max Thermal Power [W]: " + str(self.maximal_thermal_power_in_watt)
        )
        lines.append("Operation Time [min]: " + str(self.min_operation_time))
        lines.append("Idle Time [min]: " + str(self.min_idle_time))
        return lines

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
        """Simulate the gas heater."""

        # Get inputs --------------------------------------------------------------------------------------------------------
        self.cooled_water_temperature_return_to_water_boiler_in_celsius = (
            stsv.get_input_value(self.cooled_water_temperature_boiler_input_channel)
        )
        self.state_gas_controller = stsv.get_input_value(self.state_channel)
        self.initial_temperature_building_in_celsius = stsv.get_input_value(
            self.initial_temperature_building_channel
        )
        self.mean_temperature_building_in_celsius = stsv.get_input_value(
            self.temperature_mean_building_channel
        )
        self.ref_max_thermal_building_demand_in_watt = stsv.get_input_value(
            self.ref_max_thermal_building_demand_channel
        )

        # Calculations ------------------------------------------------------------------------------------------------------
        self.calculate_max_mass_flow()

        # Open Gas valve
        if self.state_gas_controller == 1:
            gas_power_in_watt = self.maximal_thermal_power_in_watt

            # when operation time is reached and gas heater has heated long enough so heat distribution can open valve
            if timestep >= self.start_timestep_gas_heater + self.min_operation_time:
                self.control_signal_from_heater_to_heat_distribution = 1
                self.start_timestep_gas_heater = timestep

            # control signal to heat distribution is turned off after some idle time
            if timestep >= self.start_timestep_gas_heater + self.min_idle_time:
                self.control_signal_from_heater_to_heat_distribution = 0

        if self.state_gas_controller == 0:
            gas_power_in_watt = 0

        self.calculate_temperature_gain_from_heating(gas_power_in_watt)

        self.calculate_temperature_of_heated_water()

        self.calculate_mean_water_temperature_in_water_boiler(
            self.cooled_water_temperature_return_to_water_boiler_in_celsius
        )

        # Set outputs -------------------------------------------------------------------------------------------------------

        stsv.set_output_value(
            self.control_signal_from_heater_to_heat_distribution_channel,
            self.control_signal_from_heater_to_heat_distribution,
        )
        stsv.set_output_value(
            self.max_mass_flow_channel, self.max_mass_flow_in_kg_per_second
        )
        stsv.set_output_value(
            self.heated_water_temperature_boiler_output_channel,
            self.heated_water_temperature_in_boiler_in_celsius,
        )
        stsv.set_output_value(
            self.mean_water_temperature_boiler_output_channel,
            self.mean_water_temperature_in_boiler_in_celsius,
        )
        stsv.set_output_value(self.gas_power_channel, gas_power_in_watt)

    def build(self, min_operation_time, min_idle_time):
        """Build function.

        The function sets important constants an parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = 4184
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time

    def calculate_max_mass_flow(self):
        """Calculate maximal water mass flow of the water boiler of the gas heater."""

        self.max_mass_flow_in_kg_per_second = (
            self.ref_max_thermal_building_demand_in_watt
            / (
                self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                * (
                    self.initial_temperature_water_boiler_in_celsius
                    - self.initial_temperature_building_in_celsius
                )
            )
        )

    def calculate_temperature_gain_from_heating(self, gas_power_in_watt):
        """Calculate temperature delta after heating the water in the boiler with a certain gas power."""
        self.temperature_gain_in_celsius = gas_power_in_watt / (
            self.max_mass_flow_in_kg_per_second
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        )

    def calculate_temperature_of_heated_water(self):
        """Calculate the temperature of the heated water in the boiler after heating with certain gas power."""
        self.heated_water_temperature_in_boiler_in_celsius = (
            self.mean_water_temperature_in_boiler_in_celsius
            + self.temperature_gain_in_celsius
        )

    def calculate_mean_water_temperature_in_water_boiler(
        self, cooled_water_temperature_in_celsius
    ):
        """Calculate the mean temperature of the water in the water boiler."""
        self.mean_water_temperature_in_boiler_in_celsius = (
            cooled_water_temperature_in_celsius
            + self.heated_water_temperature_in_boiler_in_celsius
        ) / 2


class GasHeaterController(cp.Component):

    """Gas Heater Controller.

    It takes data from other
    components and sends signal to the gas heater for
    activation or deactivation.

    """

    # Inputs
    MeanWaterTemperatureGasHeaterControllerInput = (
        "MeanWaterTemperatureGasHeaterControllerInput"
    )
    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericGasHeaterControllerConfig,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "GasHeaterController",
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.state_controller: int = 0
        self.build(
            set_heating_temperature_water_boiler_in_celsius=config.set_heating_temperature_water_boiler_in_celsius,
            offset=config.offset,
            mode=config.mode,
        )

        self.mean_water_temperature_gas_heater_controller_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MeanWaterTemperatureGasHeaterControllerInput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY
        )
        self.controller_gas_valve_mode: str = "close"
        self.previous_controller_gas_valve_mode: str = "close"

    def build(
        self,
        set_heating_temperature_water_boiler_in_celsius: float,
        offset: float,
        mode: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        self.controller_gas_valve_mode = "off"
        self.previous_controller_gas_valve_mode = self.controller_gas_valve_mode

        # Configuration
        self.set_temperature_water_boiler_in_celsius = (
            set_heating_temperature_water_boiler_in_celsius
        )
        self.offset = offset
        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_gas_valve_mode = self.controller_gas_valve_mode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_gas_valve_mode = self.previous_controller_gas_valve_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Gas Heater Controller")
        lines.append(
            "Set Temperature of Water [°C]: "
            + str(self.set_temperature_water_boiler_in_celsius)
        )
        # todo: add more useful stuff here
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the gas heater controller."""

        # Retrieves inputs
        water_boiler_temperature_in_celsius = stsv.get_input_value(
            self.mean_water_temperature_gas_heater_controller_input_channel
        )
        # mode = [1,2] for different controller modes, here mode only 1
        if self.mode == 1:
            self.conditions_for_opening_or_shutting_gas_valve(
                water_boiler_temperature_in_celsius,
            )

        if self.controller_gas_valve_mode == "open":
            self.state_controller = 1
        if self.controller_gas_valve_mode == "close":
            self.state_controller = 0
        stsv.set_output_value(self.state_channel, self.state_controller)

    def conditions_for_opening_or_shutting_gas_valve(
        self,
        water_boiler_temperature: float,
    ) -> None:
        """Set conditions for the gas valve in gas heater."""
        maxium_water_boiler_set_temperature = (
            self.set_temperature_water_boiler_in_celsius
        )
        # gas is turned off a little before maximum water temp is reached
        if (
            water_boiler_temperature
            >= maxium_water_boiler_set_temperature - self.offset
        ):
            self.controller_gas_valve_mode = "close"
            return

        if water_boiler_temperature < maxium_water_boiler_set_temperature:
            self.controller_gas_valve_mode = "open"
            return
