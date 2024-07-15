"""Gas meter module to measure gas consumption, costs and co2 emission. """

# clean
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim.component import ComponentInput, OpexCostDataClass
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
    DynamicComponentConnection,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass


@dataclass_json
@dataclass
class GasMeterConfig(cp.ConfigBase):
    """Gas Meter Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GasMeter.get_full_classname()

    name: str
    total_energy_from_grid_in_kwh: None

    @classmethod
    def get_gas_meter_default_config(cls):
        """Gets a default GasMeter."""
        return GasMeterConfig(
            name="GasMeter",
            total_energy_from_grid_in_kwh=None,
        )


class GasMeter(DynamicComponent):
    """Gas meter class.

    It calculates the gas production and consumption dynamically for all components.
    So far only gas consumers are represented here but gas producers can be added here too.
    """

    # Outputs
    GasAvailable = "GasAvailable"
    GasFromGrid = "GasFromGrid"
    GasConsumption = "GasConsumption"
    GasProduction = "GasProduction"
    CumulativeConsumption = "CumulativeConsumption"
    CumulativeProduction = "CumulativeProduction"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GasMeterConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ):
        """Initialize the component."""
        self.grid_energy_balancer_config = config
        self.name = self.grid_energy_balancer_config.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        super().__init__(
            self.my_component_inputs,
            self.my_component_outputs,
            self.name,
            my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = GasMeterState(cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        # Outputs
        self.gas_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasAvailable,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.GasAvailable} will follow.",
        )

        self.gas_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasFromGrid,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.GasFromGrid} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.gas_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasConsumption,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.GasConsumption} will follow.",
        )

        self.gas_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.GasProduction,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.GasProduction} will follow.",
        )

        self.cumulative_gas_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
        )

        self.cumulative_gas_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=lt.LoadTypes.GAS,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
        )

        self.add_dynamic_default_connections(self.get_default_connections_from_generic_gas_heater())
        self.add_dynamic_default_connections(self.get_default_connections_from_generic_heat_source())

    def get_default_connections_from_generic_gas_heater(
        self,
    ):
        """Get gas heater default connections."""

        from hisim.components.generic_gas_heater import (  # pylint: disable=import-outside-toplevel
            GasHeater,
        )

        dynamic_connections = []
        gas_heater_class_name = GasHeater.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=GasHeater,
                source_class_name=gas_heater_class_name,
                source_component_field_name=GasHeater.GasDemand,
                source_load_type=lt.LoadTypes.GAS,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_generic_heat_source(
        self,
    ):
        """Get generic heat source default connections."""

        from hisim.components.generic_heat_source import HeatSource  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        heat_source_class_name = HeatSource.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=HeatSource,
                source_class_name=heat_source_class_name,
                source_component_field_name=HeatSource.FuelDelivered,
                source_load_type=lt.LoadTypes.GAS,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[
                    lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def write_to_report(self):
        """Writes relevant information to report."""
        return self.grid_energy_balancer_config.get_string_dict()

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.self_copy()

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks values."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the grid energy balancer."""

        if timestep == 0:
            self.production_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.GAS_PRODUCTION])
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED]
            )

        # GAS #

        # get sum of production and consumption for all inputs for each iteration
        production_in_watt = sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
        consumption_uncontrolled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        # Production of Gas positve sign
        # Consumption of Gas negative sign
        difference_between_production_and_consumption_in_watt = production_in_watt - consumption_uncontrolled_in_watt

        # transform watt to watthour
        production_in_watt_hour = production_in_watt * self.seconds_per_timestep / 3600
        consumption_uncontrolled_in_watt_hour = consumption_uncontrolled_in_watt * self.seconds_per_timestep / 3600
        difference_between_production_and_consumption_in_watt_hour = (
            production_in_watt_hour - consumption_uncontrolled_in_watt_hour
        )

        # calculate cumulative production and consumption
        cumulative_production_in_watt_hour = self.state.cumulative_production_in_watt_hour + production_in_watt_hour
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour + consumption_uncontrolled_in_watt_hour
        )

        # consumption is bigger than production -> gas from grid is needed
        # change sign so that value becomes positive
        if difference_between_production_and_consumption_in_watt_hour < 0:
            gas_from_grid_in_watt_hour = -difference_between_production_and_consumption_in_watt_hour
        # production is bigger -> gas can be fed into grid
        elif difference_between_production_and_consumption_in_watt_hour > 0:
            gas_from_grid_in_watt_hour = 0.0

        # difference between production and consumption is zero
        else:
            gas_from_grid_in_watt_hour = 0.0

        # set outputs
        stsv.set_output_value(
            self.gas_available_channel,
            difference_between_production_and_consumption_in_watt,
        )

        stsv.set_output_value(self.gas_from_grid_channel, gas_from_grid_in_watt_hour)
        stsv.set_output_value(
            self.gas_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )

        stsv.set_output_value(
            self.gas_production_channel,
            production_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_gas_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_gas_production_channel,
            cumulative_production_in_watt_hour,
        )

        self.state.cumulative_production_in_watt_hour = cumulative_production_in_watt_hour
        self.state.cumulative_consumption_in_watt_hour = cumulative_consumption_in_watt_hour

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of gas costs and revenues."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.config.name:
                if output.field_name == self.GasFromGrid:
                    self.config.total_energy_from_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = self.config.total_energy_from_grid_in_kwh * euro_per_unit
        co2_per_simulated_period_in_kg = self.config.total_energy_from_grid_in_kwh * co2_per_unit
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=opex_cost_per_simulated_period_in_euro,
            co2_footprint=co2_per_simulated_period_in_kg,
            consumption=0,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_energy_from_grid_in_kwh: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.config.name and output.load_type == lt.LoadTypes.GAS:
                if output.field_name == self.GasFromGrid:
                    total_energy_from_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 1)
                    break

        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total gas demand from grid",
            unit="kWh",
            value=total_energy_from_grid_in_kwh,
            tag=KpiTagEnumClass.GENERAL,
            description=self.component_name,
        )

        list_of_kpi_entries = [total_energy_from_grid_in_kwh_entry]
        return list_of_kpi_entries


@dataclass
class GasMeterState:
    """GasMeterState class."""

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the GasMeterState."""
        return GasMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
