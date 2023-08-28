"""Electricity meter module should replace the sumbuilder. """
# clean
from dataclasses import dataclass
from typing import List, Tuple

from dataclasses_json import dataclass_json
import pandas as pd

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import ComponentInput, OpexCostDataClass
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class ElectricityMeterConfig(cp.ConfigBase):

    """Electricity Meter Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ElectricityMeter.get_full_classname()

    name: str
    total_energy_to_grid_in_kwh: float
    total_energy_from_grid_in_kwh: float

    @classmethod
    def get_electricity_meter_default_config(cls):
        """Gets a default ElectricityMeter."""
        return ElectricityMeterConfig(
            name="ElectricityMeter",
            total_energy_to_grid_in_kwh=0.0,
            total_energy_from_grid_in_kwh=0.0,
        )


class ElectricityMeter(DynamicComponent):

    """Electricity meter class.

    It calculates the electricity production and consumption dynamically for all components.
    """

    # Outputs
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    ElectricityConsumption = "ElectricityConsumption"
    ElectricityProduction = "ElectricityProduction"
    CumulativeConsumption = "CumulativeConsumption"
    CumulativeProduction = "CumulativeProduction"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricityMeterConfig,
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
        )

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = ElectricityMeterState(
            cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0
        )
        self.previous_state = self.state.self_copy()

        # Outputs
        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToOrFromGrid} will follow.",
        )

        self.electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityConsumption} will follow.",
        )

        self.electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityProduction} will follow.",
        )

        self.cumulative_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
        )

        self.cumulative_electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
        )

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

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the grid energy balancer."""

        if timestep == 0:
            self.production_inputs = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION]
            )
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
            )

        # ELECTRICITY #

        # get sum of production and consumption for all inputs for each iteration
        production_in_watt = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.production_inputs
            ]
        )
        consumption_uncontrolled_in_watt = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.consumption_uncontrolled_inputs
            ]
        )

        # transform watt to watthour
        production_in_watt_hour = production_in_watt * self.seconds_per_timestep / 3600
        consumption_uncontrolled_in_watt_hour = (
            consumption_uncontrolled_in_watt * self.seconds_per_timestep / 3600
        )

        # calculate cumulative production and consumption
        cumulative_production_in_watt_hour = (
            self.state.cumulative_production_in_watt_hour + production_in_watt_hour
        )
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour
            + consumption_uncontrolled_in_watt_hour
        )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        electricity_to_or_from_grid = (
            production_in_watt - consumption_uncontrolled_in_watt
        )

        stsv.set_output_value(
            self.electricity_to_or_from_grid, electricity_to_or_from_grid
        )
        stsv.set_output_value(
            self.electricity_consumption_channel, consumption_uncontrolled_in_watt,
        )

        stsv.set_output_value(
            self.electricity_production_channel, production_in_watt,
        )

        stsv.set_output_value(
            self.cumulative_electricity_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_electricity_production_channel,
            cumulative_production_in_watt_hour,
        )

        self.state.cumulative_production_in_watt_hour = (
            cumulative_production_in_watt_hour
        )
        self.state.cumulative_consumption_in_watt_hour = (
            cumulative_consumption_in_watt_hour
        )

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.config.name
                and output.field_name == self.ElectricityToOrFromGrid
            ):  # Todo: check component name from examples: find another way of using the correct outputs
                self.config.total_energy_to_grid_in_kwh = round(
                    postprocessing_results.iloc[:, index].clip(lower=0).sum()
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
                self.config.total_energy_from_grid_in_kwh = -round(
                    postprocessing_results.iloc[:, index].clip(upper=0).sum()
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )

        co2_per_unit = (
            EmissionFactorsAndCostsForFuelsConfig.electricity_footprint_in_kg_per_kwh
        )
        euro_per_unit = (
            EmissionFactorsAndCostsForFuelsConfig.electricity_costs_in_euro_per_kwh
        )
        revenue_euro_per_unit = (
            EmissionFactorsAndCostsForFuelsConfig.electricity_to_grid_revenue_in_euro_per_kwh
        )

        opex_cost_per_simulated_period_in_euro = (
            self.config.total_energy_from_grid_in_kwh * euro_per_unit
            - self.config.total_energy_to_grid_in_kwh * revenue_euro_per_unit
        )
        co2_per_simulated_period_in_kg = (
            self.config.total_energy_from_grid_in_kwh * co2_per_unit
        )
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=opex_cost_per_simulated_period_in_euro,
            co2_footprint=co2_per_simulated_period_in_kg,
            consumption=0,
        )

        return opex_cost_data_class


@dataclass
class ElectricityMeterState:

    """ElectricityMeterState class."""

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(self,):
        """Copy the ElectricityMeterState."""
        return ElectricityMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
