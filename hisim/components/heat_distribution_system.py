"""Heat Distribution Module."""
# clean
# Owned
from typing import List
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils


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
    InitialWaterBoilerTemperature = "InitialWaterBoilerTemperature"
    ResidenceTemperature = "ResidenceTemperature"
    WaterTemperatureDistributionSystemInput = "WaterTemperatureDistributionSystemInput"
    GasPower = "GasPower"
    MaxMassFlow = "MaxMassFlow"
    # Outputs
    WaterTemperatureDistributionSystemOutput = (
        "WaterTemperatureDistributionSystemOutput"
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
        self.gas_power_in_watt: float = 0.0
        self.mean_residence_temperature_in_celsius: float = 0.0
        self.initial_water_boiler_temperature_in_celsius: float = 0.0
        self.water_distribution_temperature_input_in_celsius: float = 0.0
        self.max_mass_flow_in_kg_per_second: float = 0.0
        self.heat_gain_for_building_in_watt: float = 0.0
        self.rest_temperature_return_to_water_boiler_in_celsius: float = 0.0
        self.build()
        # Inputs
        self.initial_water_boiler_temperature_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.InitialWaterBoilerTemperature,
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
        self.water_distribution_temperature_input_channel: cp.ComponentInput = (
            self.add_input(
                self.component_name,
                self.WaterTemperatureDistributionSystemInput,
                lt.LoadTypes.WATER,
                lt.Units.CELSIUS,
                True,
            )
        )
        # Outputs
        self.water_distribution_temperature_output_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.WaterTemperatureDistributionSystemOutput,
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
        self.gas_power_in_watt = stsv.get_input_value(self.gas_power_channel)
        self.initial_water_boiler_temperature_in_celsius = stsv.get_input_value(
            self.initial_water_boiler_temperature_channel
        )
        self.mean_residence_temperature_in_celsius = stsv.get_input_value(
            self.mean_residence_temperature_channel
        )
        self.water_distribution_temperature_input_in_celsius = stsv.get_input_value(
            self.water_distribution_temperature_input_channel
        )
        self.max_mass_flow_in_kg_per_second = stsv.get_input_value(
            self.max_mass_flow_channel
        )
        # calculations --------------------------------------------------------------------------------
        self.heat_gain_for_building_in_watt = (
            self.max_mass_flow_in_kg_per_second
            * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            * (
                self.water_distribution_temperature_input_in_celsius
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
        ) + self.initial_water_boiler_temperature_in_celsius

        stsv.set_output_value(
            self.water_distribution_temperature_output_channel,
            self.rest_temperature_return_to_water_boiler_in_celsius,
        )
        stsv.set_output_value(
            self.thermal_power_delivered_channel, self.heat_gain_for_building_in_watt
        )
