"""Idealized Electric Heater Module."""
# clean
# Owned
from typing import List
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum


__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


class IdealizedElectricHeater(cp.Component):

    """Idealized Electric Heater System."""

    # Inputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    HeatingPowerDelivered = "HeatingPowerDelivered"

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
            "IdealizedElectricHeater", my_simulation_parameters=my_simulation_parameters
        )

        self.thermal_power_delivered_in_watt: float = 0
        self.theoretical_thermal_building_in_watt: float = 0
        self.heating_in_watt: float = 0

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.SETHEATINGTEMPERATUREFORBUILDING,
            entry=set_heating_temperature_for_building_in_celsius,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.SETCOOLINGTEMPERATUREFORBUILDING,
            entry=set_cooling_temperature_for_building_in_celsius,
        )

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

        self.heating_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatingPowerDelivered} will follow.",
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
        lines.append("Idealized Electric Heater")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the Idealized Electric Heater."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        theoretical_thermal_building_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_channel
        )

        # Calculations ----------------------------------------------------------------------------------------------------------

        thermal_power_delivered_in_watt = theoretical_thermal_building_in_watt

        if thermal_power_delivered_in_watt >= 0:
            heating_in_watt = thermal_power_delivered_in_watt
        else:
            heating_in_watt = 0

        # Set outputs -----------------------------------------------------------------------------------------------------------

        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            thermal_power_delivered_in_watt,
        )

        stsv.set_output_value(self.heating_power_delivered_channel, heating_in_watt)
