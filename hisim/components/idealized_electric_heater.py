"""Idealized Electric Heater Module."""
from __future__ import annotations

# Owned
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
import hisim.component as cp
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim import utils
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class IdealizedHeaterConfig(ConfigBase):
    """Configuration of the Idealized Heater class.

    A heater with no machine behind it: it delivers exactly the thermal power the building
    asks for, every timestep, with no capacity limit and no losses. It exists to hold a
    building's indoor temperature at a setpoint so that the building's own heating demand can
    be measured, which is why it has no size, no efficiency and no cost.

    The named default is :meth:`preset_standard`; the two setpoints are the whole
    configuration::

        IdealizedHeaterConfig.preset_standard("IdealizedHeater")

    The two setpoints are deliberately not copied from the building's own
    ``set_heating_temperature_in_celsius`` / ``set_cooling_temperature_in_celsius``: this band
    is the narrower one (19.5/23.5 against the building's 20/25), so making the heater follow
    the building would change every result this component appears in.
    """

    MAIN_CLASS = "hisim.components.idealized_electric_heater.IdealizedElectricHeater"

    component_id: ComponentID
    #: Indoor temperature below which the heater delivers heat.
    set_heating_temperature_for_building_in_celsius: float = 19.5
    #: Indoor temperature above which the heater removes heat.
    set_cooling_temperature_for_building_in_celsius: float = 23.5

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "IdealizedHeaterConfig":
        """The heater the building tests hold their indoor temperature with.

        The field defaults are the whole appliance: a heating setpoint of 19.5 °C and a
        cooling setpoint of 23.5 °C. Nothing else describes it, since an idealized heater has
        neither a size nor a fuel.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            IdealizedHeaterConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))


class IdealizedElectricHeater(cp.Component):
    """Idealized Electric Heater System."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    TheoreticalThermalBuildingDemand: str = "TheoreticalThermalBuildingDemand"

    # Outputs
    ThermalPowerDelivered: str = "ThermalPowerDelivered"
    HeatingPowerDelivered: str = "HeatingPowerDelivered"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: IdealizedHeaterConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Construct all the necessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        my_display_config = my_display_config or DisplayConfig()
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
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

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Idealized Electric Heater."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        theoretical_thermal_building_demand_in_watt = stsv.get_input_value(self.theoretical_thermal_building_channel)

        # Calculations ----------------------------------------------------------------------------------------------------------

        thermal_power_delivered_in_watt = theoretical_thermal_building_demand_in_watt

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
        all_outputs: List[cp.ComponentOutput],
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
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
