"""Electricity meter module should replace the sumbuilder. """

# clean
from dataclasses import dataclass
from typing import List

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
class ElectricityMeterConfig(cp.ConfigBase):
    """Electricity Meter Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ElectricityMeter.get_full_classname()

    building_name: str
    name: str

    @classmethod
    def get_electricity_meter_default_config(
        cls,
        name: str = "ElectricityMeter",
        building_name: str = "BUI1",
    ) -> "ElectricityMeterConfig":
        """Gets a default ElectricityMeter."""
        return ElectricityMeterConfig(
            building_name=building_name,
            name=name,
        )


class ElectricityMeter(DynamicComponent):
    """Electricity meter class.

    It calculates the electricity production and consumption dynamically for all components.
    """

    # Outputs
    ElectricityAvailable = "ElectricityAvailable"
    ElectricityToAndFromGrid = "ElectricityToAndFromGrid"
    ElectricityToGrid = "ElectricityToGrid"
    ElectricityFromGrid = "ElectricityFromGrid"
    ElectricityConsumption = "ElectricityConsumption"
    ElectricityProduction = "ElectricityProduction"
    CumulativeConsumption = "CumulativeConsumption"
    CumulativeProduction = "CumulativeProduction"
    ElectricityToGridInWatt = "ElectricityToGridInWatt"
    ElectricityFromGridInWatt = "ElectricityFromGridInWatt"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricityMeterConfig,
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
            name=config.building_name + "_" + self.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = ElectricityMeterState(cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        # Outputs
        self.electricity_to_grid_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToGridInWatt} will follow.",
            postprocessing_flag=(
                [
                    lt.InandOutputType.ELECTRICITY_PRODUCTION,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ]
                if any(word in self.name.lower() for word in ["quartier", "district"])
                else []
            ),
        )
        self.electricity_from_grid_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityFromGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityFromGridInWatt} will follow.",
            postprocessing_flag=(
                [
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ]
                if any(word in self.name.lower() for word in ["quartier", "district"])
                else []
            ),
        )
        self.electricity_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityAvailable,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityAvailable} will follow.",
        )
        self.electricity_to_and_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToAndFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToAndFromGrid} will follow.",
        )
        self.electricity_to_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToGrid} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.electricity_from_grid_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityFromGrid} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityConsumption} will follow.",
        )

        self.electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
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
        self.add_dynamic_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_dynamic_default_connections(self.get_default_connections_from_pv_system())
        self.add_dynamic_default_connections(self.get_default_connections_from_dhw_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_heat_pump())

    def get_default_connections_from_utsp_occupancy(
        self,
    ):
        """Get utsp occupancy default connections."""

        from hisim.components.loadprofilegenerator_utsp_connector import (  # pylint: disable=import-outside-toplevel
            UtspLpgConnector,
        )

        dynamic_connections = []
        occupancy_class_name = UtspLpgConnector.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=UtspLpgConnector,
                source_class_name=occupancy_class_name,
                source_component_field_name=UtspLpgConnector.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_pv_system(
        self,
    ):
        """Get pv system default connections."""

        from hisim.components.generic_pv_system import PVSystem  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        pv_class_name = PVSystem.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=PVSystem,
                source_class_name=pv_class_name,
                source_component_field_name=PVSystem.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.PV,
                    lt.InandOutputType.ELECTRICITY_PRODUCTION,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_dhw_heat_pump(
        self,
    ):
        """Get dhw heat pump default connections."""

        from hisim.components.generic_heat_pump_modular import (  # pylint: disable=import-outside-toplevel
            ModularHeatPump,
        )

        dynamic_connections = []
        dhw_heat_pump_class_name = ModularHeatPump.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=ModularHeatPump,
                source_class_name=dhw_heat_pump_class_name,
                source_component_field_name=ModularHeatPump.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_advanced_heat_pump(
        self,
    ):
        """Get advanced heat pump default connections."""

        from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        advanced_heat_pump_class_name = HeatPumpHplib.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=HeatPumpHplib,
                source_class_name=advanced_heat_pump_class_name,
                source_component_field_name=HeatPumpHplib.ElectricalInputPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
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
            self.production_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION])
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
            )

        # ELECTRICITY #

        # get sum of production and consumption for all inputs for each iteration
        production_in_watt = sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
        consumption_uncontrolled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
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

        # consumption is bigger than production -> electricity from grid is needed
        # change sign so that value becomes positive
        if difference_between_production_and_consumption_in_watt_hour < 0:
            electricity_from_grid_in_watt_hour = -difference_between_production_and_consumption_in_watt_hour
            electricity_to_grid_in_watt_hour = 0.0
        # production is bigger -> electricity can be fed into grid
        elif difference_between_production_and_consumption_in_watt_hour > 0:
            electricity_to_grid_in_watt_hour = difference_between_production_and_consumption_in_watt_hour
            electricity_from_grid_in_watt_hour = 0.0

        # difference between production and consumption is zero
        else:
            electricity_to_grid_in_watt_hour = 0.0
            electricity_from_grid_in_watt_hour = 0.0

        # set outputs
        stsv.set_output_value(
            self.electricity_to_grid_in_watt_channel,
            (
                difference_between_production_and_consumption_in_watt
                if difference_between_production_and_consumption_in_watt > 0
                else 0
            ),
        )
        stsv.set_output_value(
            self.electricity_from_grid_in_watt_channel,
            (
                -difference_between_production_and_consumption_in_watt
                if difference_between_production_and_consumption_in_watt < 0
                else 0
            ),
        )

        stsv.set_output_value(
            self.electricity_available_channel,
            difference_between_production_and_consumption_in_watt,
        )
        stsv.set_output_value(
            self.electricity_to_and_from_grid_channel,
            difference_between_production_and_consumption_in_watt_hour,
        )
        stsv.set_output_value(self.electricity_to_grid_channel, electricity_to_grid_in_watt_hour)
        stsv.set_output_value(self.electricity_from_grid_channel, electricity_from_grid_in_watt_hour)
        stsv.set_output_value(
            self.electricity_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )

        stsv.set_output_value(
            self.electricity_production_channel,
            production_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_electricity_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_electricity_production_channel,
            cumulative_production_in_watt_hour,
        )

        self.state.cumulative_production_in_watt_hour = cumulative_production_in_watt_hour
        self.state.cumulative_consumption_in_watt_hour = cumulative_consumption_in_watt_hour

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        total_energy_to_grid_in_kwh: float
        total_energy_from_grid_in_kwh: float

        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ElectricityToGrid:
                    # Todo: check component name from system_setups: find another way of using the correct outputs
                    total_energy_to_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 2)

                elif output.field_name == self.ElectricityFromGrid:
                    total_energy_from_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 2)

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        revenue_euro_per_unit = emissions_and_cost_factors.electricity_to_grid_revenue_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = (
            total_energy_from_grid_in_kwh * euro_per_unit - total_energy_to_grid_in_kwh * revenue_euro_per_unit
        )
        co2_per_simulated_period_in_kg = total_energy_from_grid_in_kwh * co2_per_unit
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=opex_cost_per_simulated_period_in_euro,
            co2_footprint=co2_per_simulated_period_in_kg,
            consumption=total_energy_from_grid_in_kwh,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_energy_from_grid_in_kwh: float
        total_energy_to_grid_in_kwh: float
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.ELECTRICITY:
                if output.field_name == self.ElectricityFromGrid:
                    total_energy_from_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3
                elif output.field_name == self.ElectricityToGrid:
                    total_energy_to_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3

        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total energy from grid",
            unit="kWh",
            value=total_energy_from_grid_in_kwh,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_from_grid_in_kwh_entry)

        total_energy_to_grid_in_kwh_entry = KpiEntry(
            name="Total energy to grid",
            unit="kWh",
            value=total_energy_to_grid_in_kwh,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_energy_to_grid_in_kwh_entry)

        # get opex costs
        opex_costs = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        opex_costs_in_euro_entry = KpiEntry(
            name="Opex costs of electricity consumption",
            unit="Euro",
            value=opex_costs.opex_cost,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex_costs_in_euro_entry)
        co2_footprint_in_kg_entry = KpiEntry(
            name="CO2 footprint of electricity consumption",
            unit="kg",
            value=opex_costs.co2_footprint,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_in_kg_entry)

        return list_of_kpi_entries


@dataclass
class ElectricityMeterState:
    """ElectricityMeterState class."""

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the ElectricityMeterState."""
        return ElectricityMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
