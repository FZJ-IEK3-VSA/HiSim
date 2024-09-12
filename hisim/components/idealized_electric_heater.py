"""Idealized Electric Heater Module."""

# clean
# Owned
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
import hisim.component as cp
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


@dataclass_json
@dataclass
class IdealizedHeaterConfig(cp.ConfigBase):
    """Configuration of the Idealized Heater."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return IdealizedElectricHeater.get_full_classname()

    building_name: str
    name: str
    set_heating_temperature_for_building_in_celsius: float
    set_cooling_temperature_for_building_in_celsius: float

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default Idealized Heater."""
        return IdealizedHeaterConfig(
            building_name=building_name,
            name="IdealizedHeater",
            set_heating_temperature_for_building_in_celsius=19.5,
            set_cooling_temperature_for_building_in_celsius=23.5,
        )


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
        config: IdealizedHeaterConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.thermal_power_delivered_in_watt: float = 0
        self.theoretical_thermal_building_in_watt: float = 0
        self.heating_in_watt: float = 0
        self.set_heating_temperature_for_building_in_celsius = config.set_heating_temperature_for_building_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = config.set_cooling_temperature_for_building_in_celsius

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

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Idealized Electric Heater."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        theoretical_thermal_building_in_watt = stsv.get_input_value(self.theoretical_thermal_building_channel)

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

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(config: IdealizedHeaterConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
