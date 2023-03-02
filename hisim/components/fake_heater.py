"""Fake Heater Module."""
# clean
# Owned
from typing import List
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils


__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


class FakeHeater(cp.Component):

    """Fake Heater System."""

    # Inputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    SetHeatingTemperatureForBuilding = "SetHeatingTemperatureForBuilding"
    SetCoolingTemperatureForBuilding = "SetCoolingTemperatureForBuilding"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        set_heating_temperature_for_building_in_celsius: float,
        set_cooling_temperature_for_building_in_celsius: float,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "FakeHeaterSystem", my_simulation_parameters=my_simulation_parameters
        )

        self.thermal_power_delivered_in_watt: float = 0
        self.theoretical_thermal_building_in_watt: float = 0
        self.set_heating_temperature_for_building_in_celsius = set_heating_temperature_for_building_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = set_cooling_temperature_for_building_in_celsius

        # Inputs

        self.theoretical_thermal_building_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        # Outputs

        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerDelivered} will follow.",
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

    def build(
        self,
    ) -> None:
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

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Fake Heater System")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the fake heater."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        self.theoretical_thermal_building_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_channel
        )

        # Calculations ----------------------------------------------------------------------------------------------------------

        self.thermal_power_delivered_in_watt = self.theoretical_thermal_building_in_watt

        # Set outputs -----------------------------------------------------------------------------------------------------------

        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.thermal_power_delivered_in_watt,
        )

        stsv.set_output_value(
            self.set_heating_temperature_for_building_channel,
            self.set_heating_temperature_for_building_in_celsius
        )

        stsv.set_output_value(
            self.set_cooling_temperature_for_building_channel,
            self.set_cooling_temperature_for_building_in_celsius
        )
