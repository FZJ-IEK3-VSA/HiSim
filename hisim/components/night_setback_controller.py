# -*- coding: utf-8 -*-
# clean
"""Night setback controller.

This component provides a temperature offset for the Building component.
It returns a configurable setback value during the configured night window
and zero otherwise. The output is meant to be connected to
``Building.BuildingTemperatureModifier`` so that the heating set temperature
is reduced during the night.
"""

from dataclasses import dataclass
from typing import List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import CapexCostDataClass, OpexCostDataClass
from hisim.components.building import Building
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.simulationparameters import SimulationParameters

__authors__ = "Jonas Pfeiffer"
__copyright__ = "Copyright 2026, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Vitor Hugo Bellotto Zago"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Jonas Pfeiffer"
__status__ = "development"


@dataclass_json
@dataclass
class NightSetbackConfig(cp.ConfigBase):
    """Configuration of the night setback controller."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the controller."""
        return NightSetbackController.get_full_classname()

    building_name: str
    name: str
    setback_delta_in_kelvin: float
    night_start_hour: int
    night_end_hour: int

    @staticmethod
    def get_default_config(
        building_name: str = "BUI1",
    ) -> "NightSetbackConfig":
        """Return a default configuration with a 22:00 to 06:00 setback window."""
        return NightSetbackConfig(
            building_name=building_name,
            name="NightSetbackController",
            setback_delta_in_kelvin=-4.0,
            night_start_hour=22,
            night_end_hour=6,
        )


class NightSetbackController(cp.Component):
    """Generates a configurable night setback temperature modifier.

    The component emits a negative temperature offset during the configured
    night window and zero otherwise. Connect the output to the Building input
    ``BuildingTemperatureModifier`` to reduce the heating set temperature
    during the night.
    """

    BuildingTemperatureModifier = "BuildingTemperatureModifier"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: NightSetbackConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Construct the controller."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.setback_delta_in_kelvin = config.setback_delta_in_kelvin
        self.night_start_hour = config.night_start_hour
        self.night_end_hour = config.night_end_hour

        # Units.CELSIUS is required here because Building.BuildingTemperatureModifier
        # declares its input as CELSIUS. The value is a delta-T, so 1 K == 1 degC numerically.
        self.building_temperature_modifier_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.BuildingTemperatureModifier,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="Temperature modifier for the building during the night setback window.",
        )

        self.add_default_connections(self.get_default_connections_to_building())

    def get_default_connections_to_building(self) -> List[cp.ComponentConnection]:
        """Connect the output to the building temperature modifier input."""
        return [
            cp.ComponentConnection(
                NightSetbackController.BuildingTemperatureModifier,
                Building.get_classname(),
                Building.BuildingTemperatureModifier,
            )
        ]

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck the outputs."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Set the night setback value for the current timestep."""
        seconds_per_day = 24 * 3600
        current_second_of_day = (timestep * self.my_simulation_parameters.seconds_per_timestep) % seconds_per_day
        night_start_second = self.night_start_hour * 3600
        night_end_second = self.night_end_hour * 3600
        if self.night_start_hour == self.night_end_hour:
            is_night = False
        elif self.night_start_hour < self.night_end_hour:
            is_night = night_start_second <= current_second_of_day < night_end_second
        else:
            is_night = current_second_of_day >= night_start_second or current_second_of_day < night_end_second
        modifier = self.setback_delta_in_kelvin if is_night else 0.0
        stsv.set_output_value(self.building_temperature_modifier_channel, modifier)

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results,
    ) -> OpexCostDataClass:
        """Return the default opex structure."""
        return OpexCostDataClass.get_default_opex_cost_data_class()

    @staticmethod
    def get_cost_capex(config: NightSetbackConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Return the default capex structure."""
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results,
    ) -> List[KpiEntry]:
        """Return no dedicated KPI entries."""
        return []
