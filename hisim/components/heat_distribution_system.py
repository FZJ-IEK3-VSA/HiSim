"""Heat Distribution Module."""
# clean
# Owned
from typing import List
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
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


class HeatDistribution(cp.Component):

    """Heat Distribution System.

    It simulates the heat exchange between heat generator and building.

    """

    # Inputs
    State = "State"
    MeanWaterTemperatureDistributionInput = "MeanWaterTemperatureDistributionInput"
    ResidenceTemperature = "ResidenceTemperature"
    HeatedWaterTemperatureDistributionInput = "HeatedWaterTemperatureDistributionInput"
    GasPower = "GasPower"
    MaxMassFlow = "MaxMassFlow"
    # Outputs
    CooledWaterTemperatureDistributionOutput = (
        "CooledWaterTemperatureDistributionOutput"
    )
    ThermalPowerDelivered = "ThermalPowerDelivered"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "HeatDistributionSystem", my_simulation_parameters=my_simulation_parameters
        )
        self.state_controller: float = 0.0
        self.gas_power_in_watt: float = 0.0
        self.mean_residence_temperature_in_celsius: float = 0.0
        self.mean_water_temperature_distribution_input_in_celsius: float = 0.0
        self.heated_water_temperature_distribution_input_in_celsius: float = 0.0
        self.max_mass_flow_in_kg_per_second: float = 0.0
        self.heat_gain_for_building_in_watt: float = 0.0
        self.rest_temperature_return_to_water_boiler_in_celsius: float = 0.0
        self.build()

        # Inputs

        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )

        self.mean_water_temperature_distribution_input_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.MeanWaterTemperatureDistributionInput,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
                True,
            )
        )

        self.max_mass_flow_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxMassFlow,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            True,
        )

        self.gas_power_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GasPower,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )

        self.mean_residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.heated_water_temperature_distribution_input_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.HeatedWaterTemperatureDistributionInput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
                True,
            )
        )
        # Outputs
        self.cooled_water_temperature_distribution_output_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.CooledWaterTemperatureDistributionOutput,
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
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = 4184

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
        # todo: add more useful stuff here
        lines.append("tbd")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution system."""
        # if force_convergence:
        #     pass
        # else:
        # Retrieves inputs
        self.state_controller = stsv.get_input_value(self.state_channel)
        self.gas_power_in_watt = stsv.get_input_value(self.gas_power_channel)
        self.mean_residence_temperature_in_celsius = stsv.get_input_value(
            self.mean_residence_temperature_channel
        )
        self.mean_water_temperature_distribution_input_in_celsius = stsv.get_input_value(
            self.mean_water_temperature_distribution_input_channel
        )
        self.heated_water_temperature_distribution_input_in_celsius = stsv.get_input_value(
            self.heated_water_temperature_distribution_input_channel
        )
        self.max_mass_flow_in_kg_per_second = stsv.get_input_value(
            self.max_mass_flow_channel
        )
        # calculations --------------------------------------------------------------------------------

        if self.state_controller == 1:
            self.heat_gain_for_building_in_watt = (
                self.max_mass_flow_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                * (
                    self.heated_water_temperature_distribution_input_in_celsius
                    - self.mean_residence_temperature_in_celsius
                )
            )

            left_thermal_power_in_water_in_watt = (
                self.gas_power_in_watt - self.heat_gain_for_building_in_watt
            )

            # calculate water temperature after heat exchange with building (use left_power = mass_flow * heat-capacity * (rest_temp_water - initial_temp_water))
            self.rest_temperature_return_to_water_boiler_in_celsius = (
                left_thermal_power_in_water_in_watt
                / (
                    self.max_mass_flow_in_kg_per_second
                    * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                )
            ) + self.mean_water_temperature_distribution_input_in_celsius

            stsv.set_output_value(
                self.cooled_water_temperature_distribution_output_channel,
                self.rest_temperature_return_to_water_boiler_in_celsius,
            )
            stsv.set_output_value(
                self.thermal_power_delivered_channel, self.heat_gain_for_building_in_watt
            )
        else:
            stsv.set_output_value(
                self.cooled_water_temperature_distribution_output_channel,
                self.heated_water_temperature_distribution_input_in_celsius,
            )
            stsv.set_output_value(
                self.thermal_power_delivered_channel, 0.0
            )



class HeatDistributionController(cp.Component):

    """Heat Distribution Controller.

    It takes data from other
    components and sends signal to the heat distribution for
    activation or deactivation.

    """

    # Inputs
    ResidenceTemperature = "ResidenceTemperature"
    HeatedWaterTemperatureDistributionControllerInput = "HeatedWaterTemperatureDistributionControllerInput"
    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        set_heating_temperature_building_in_celsius: float = 0.0,
        set_heating_temperature_water_distribution_in_celsius: float = 0.0,
        mode: int = 1,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "HeatDistributionController", my_simulation_parameters=my_simulation_parameters
        )
        self.state_controller: int = 0
        self.build(
            set_heating_temperature_residence_in_celsius=set_heating_temperature_building_in_celsius,
            set_heating_temperature_water_boiler_in_celsius=set_heating_temperature_water_distribution_in_celsius,
            mode=mode,
        )

        self.mean_residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.water_boiler_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatedWaterTemperatureDistributionControllerInput,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY
        )

        self.add_default_connections(self.get_default_connections_from_building())
        self.controller_heat_distribution_mode: str = "close"
        self.previous_controller_gas_valve_mode: str = "close"

    def get_default_connections_from_building(self) -> List[cp.ComponentConnection]:
        """Get building default connections."""
        log.information("setting building default connections in HeatDistributionController")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.ResidenceTemperature,
                building_classname,
                Building.TemperatureMean,
            )
        )
        return connections

    def build(
        self,
        set_heating_temperature_residence_in_celsius: float,
        set_heating_temperature_water_boiler_in_celsius: float,
        mode: int,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heat_distribution_mode = "off"
        self.previous_controller_gas_valve_mode = self.controller_heat_distribution_mode

        # Configuration
        self.set_heating_temperature_residence_in_celsius = (
            set_heating_temperature_residence_in_celsius
        )
        self.set_temperature_water_boiler_in_celsius = (
            set_heating_temperature_water_boiler_in_celsius
        )
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
        lines.append("tbd")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat distribution controller."""
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            mean_residence_temperature_in_celsius = stsv.get_input_value(
                self.mean_residence_temperature_channel
            )
            water_boiler_temperature_in_celsius = stsv.get_input_value(
                self.water_boiler_temperature_input_channel
            )
            if self.mode == 1:
                self.conditions_for_opening_or_shutting_heat_distribution(
                    mean_residence_temperature_in_celsius,
                    water_boiler_temperature_in_celsius,
                )

        if self.controller_heat_distribution_mode == "open":
            self.state_controller = 1
        if self.controller_heat_distribution_mode == "close":
            self.state_controller = 0
        stsv.set_output_value(self.state_channel, self.state_controller)

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        residence_temperature: float,
        water_boiler_temperature: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""
        maximum_residence_set_temperature = (
            self.set_heating_temperature_residence_in_celsius
        )
        maxium_water_boiler_set_temperature = (
            self.set_temperature_water_boiler_in_celsius
        )

        if self.controller_heat_distribution_mode == "open":
            if (
                residence_temperature >= maximum_residence_set_temperature
                or water_boiler_temperature
                >= maxium_water_boiler_set_temperature
            ):
                self.controller_heat_distribution_mode = "close"
                return

        if self.controller_heat_distribution_mode == "close":
            if (
                residence_temperature < maximum_residence_set_temperature
                and water_boiler_temperature < maxium_water_boiler_set_temperature
            ):
                self.controller_heat_distribution_mode = "open"
            else:
                self.controller_heat_distribution_mode = "close"