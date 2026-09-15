# -*- coding: utf-8 -*-
"""Night setback controller.

This component provides a temperature offset for the Building component.
It returns a configurable setback value during the configured night window
and zero otherwise. The output is meant to be connected to
``Building.BuildingTemperatureModifier`` so that the heating set temperature
is reduced during the night.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
from dataclasses_json import config as dc_json_config
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import CapexCostDataClass, OpexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.components.building import Building
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.simulationparameters import SimulationParameters
from hisim.economics.facts import CostRelevance

# Conversion factors for time units. The night window is configured in hours of
# the day (0..23), so the comparisons inside ``i_simulate`` are carried out in
# seconds since midnight. Naming these explicitly keeps the h->s and day->s
# conversions distinct from ``seconds_per_timestep`` and prevents silent mix-ups.
SECONDS_PER_HOUR: int = 3600  # s per h
SECONDS_PER_DAY: int = 24 * SECONDS_PER_HOUR  # s per day


@dataclass_json
@dataclass
class NightSetbackConfig(ConfigBase):
    """Configuration of the night setback controller: how much cooler the house is at night.

    Three numbers describe the whole controller: how far the heating set temperature is
    lowered, and the two hours of the day between which that lowering applies. The
    controller emits the offset during the window and zero outside it, which the Building
    adds to its heating set temperature.

    The named default is :meth:`preset_standard`::

        NightSetbackConfig.preset_standard("NightSetbackController")

    Nothing here is derived from the building. A setback window is when the residents sleep
    and how cold they will let the house get, so no field is sizable and the preset takes
    nothing but the instance name.
    """

    MAIN_CLASS = "hisim.components.night_setback_controller.NightSetbackController"

    component_id: ComponentID
    #: Offset added to the building's heating set temperature during the night window, in
    #: kelvin. Negative lowers the set temperature, which is the point of a setback.
    setback_delta_in_kelvin: float = -4.0
    # The Python attributes carry the explicit ``_in_hours`` unit suffix: the
    # value is hours since midnight (0..23). The dataclasses_json ``field_name``
    # aliases preserve the legacy ``night_start_hour`` / ``night_end_hour``
    # serialization keys so existing JSON/HDF5 configs and reports still load
    # unchanged (per KB-6843, KB-5295).
    #: Hour of the day at which the setback starts, 0..23.
    night_start_time_in_hours: int = field(default=22, metadata=dc_json_config(field_name="night_start_hour"))
    #: Hour of the day at which it ends, 0..23. A value below the start hour means the window
    #: wraps across midnight, which is the usual case; equal hours mean no window at all.
    night_end_time_in_hours: int = field(default=6, metadata=dc_json_config(field_name="night_end_hour"))

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "NightSetbackConfig":
        """The one setback the fleet runs: four kelvin cooler from 22:00 to 06:00.

        The three field defaults are the whole controller. The preset is called ``standard``
        because a night window describes no device and no standard — there is nothing else to
        name it after.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete — the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class NightSetbackController(cp.Component):
    """Generates a configurable night setback temperature modifier.

    The component emits a negative temperature offset during the configured
    night window and zero otherwise. Connect the output to the Building input
    ``BuildingTemperatureModifier`` to reduce the heating set temperature
    during the night.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    BuildingTemperatureModifier: str = "BuildingTemperatureModifier"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: NightSetbackConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Construct the controller."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
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
        self.night_start_time_in_hours = config.night_start_time_in_hours
        self.night_end_time_in_hours = config.night_end_time_in_hours

        # Hoist the time-invariant night-window arithmetic out of the per-timestep
        # hot path. ``night_start_time_in_hours``/``night_end_time_in_hours`` are fixed at construction
        # time and never mutated, so the corresponding second-of-day bounds and which
        # of the three evaluation branches applies are constant for the whole
        # simulation. Per KB-5685, ``timestep`` is assumed to align with 00:00, so
        # the modulo against SECONDS_PER_DAY below preserves the existing semantics.
        self._night_start_time_in_seconds: int = self.night_start_time_in_hours * SECONDS_PER_HOUR
        self._night_end_time_in_seconds: int = self.night_end_time_in_hours * SECONDS_PER_HOUR
        if self.night_start_time_in_hours == self.night_end_time_in_hours:
            # No night window configured.
            self._night_mode: str = "none"
        elif self.night_start_time_in_hours < self.night_end_time_in_hours:
            # Window lies entirely within the same day.
            self._night_mode = "within"
        else:
            # Window wraps across midnight.
            self._night_mode = "wrap"

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
        current_time_of_day_in_seconds = (timestep * self.my_simulation_parameters.seconds_per_timestep) % SECONDS_PER_DAY
        if self._night_mode == "none":
            is_night = False
        elif self._night_mode == "within":
            is_night = self._night_start_time_in_seconds <= current_time_of_day_in_seconds < self._night_end_time_in_seconds
        else:  # "wrap": the night window crosses midnight.
            is_night = current_time_of_day_in_seconds >= self._night_start_time_in_seconds or current_time_of_day_in_seconds < self._night_end_time_in_seconds
        modifier_in_kelvin = self.setback_delta_in_kelvin if is_night else 0.0
        stsv.set_output_value(self.building_temperature_modifier_channel, modifier_in_kelvin)

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Return the default opex structure."""
        return OpexCostDataClass.get_default_opex_cost_data_class()

    @staticmethod
    def get_cost_capex(config: NightSetbackConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Return the default capex structure."""
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Return no dedicated KPI entries."""
        return []
