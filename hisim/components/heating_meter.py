"""Heating meter module to measure gas consumption, costs and co2 emission. """

# clean
from dataclasses import dataclass
from typing import List, Optional, Any

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
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = "-"
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass_json
@dataclass
class HeatingMeterConfig(cp.ConfigBase):
    """Heating Meter Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatingMeter.get_full_classname()

    building_name: str
    name: str

    @classmethod
    def get_heating_meter_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default GasMeter."""
        return HeatingMeterConfig(
            building_name=building_name,
            name="HeatingMeter",
        )


class HeatingMeter(DynamicComponent):
    """Heating meter class."""

    # Outputs
    HeatAvailable = "HeatAvailable"
    HeatConsumption = "HeatConsumption"
    HeatProduction = "HeatProduction"
    CumulativeConsumption = "CumulativeConsumption"
    CumulativeProduction = "CumulativeProduction"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatingMeterConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ):
        """Initialize the component."""
        self.grid_energy_balancer_config = config
        self.name = self.grid_energy_balancer_config.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = HeatingMeterState(cumulative_production_in_watt_hour=0, cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        # Outputs
        self.heat_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatAvailable,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.HeatAvailable} will follow.",
        )

        self.heat_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatConsumption,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.HeatConsumption} will follow.",
        )

        self.heat_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatProduction,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.HeatProduction} will follow.",
        )

        self.cumulative_heat_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
        )

        self.cumulative_heat_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
        )

        self.add_dynamic_default_connections(self.get_default_connections_from_heat_distribution_system())
        # self.add_dynamic_default_connections(self.get_default_connections_from_simple_dhw_storage())
        self.add_dynamic_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_generic_heat_source())

    def get_default_connections_from_heat_distribution_system(
        self,
    ):
        """Get gas heater default connections."""

        from hisim.components.heat_distribution_system import (  # pylint: disable=import-outside-toplevel
            HeatDistribution,
        )

        dynamic_connections = []
        heat_distribution_class_name = HeatDistribution.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=HeatDistribution,
                source_class_name=heat_distribution_class_name,
                source_component_field_name=HeatDistribution.ThermalPowerDelivered,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT,
                source_tags=[lt.InandOutputType.HEAT_CONSUMPTION],
                source_weight=999,
            )
        )
        return dynamic_connections

    # def get_default_connections_from_simple_dhw_storage(
    #     self,
    # ):
    #     """Get gas heater default connections."""
    #
    #     from hisim.components.simple_dhw_storage import (  # pylint: disable=import-outside-toplevel
    #         SimpleDHWStorage,
    #     )
    #
    #     dynamic_connections = []
    #     dhw_storage_class_name = SimpleDHWStorage.get_classname()
    #     dynamic_connections.append(
    #         dynamic_component.DynamicComponentConnection(
    #             source_component_class=SimpleDHWStorage,
    #             source_class_name=dhw_storage_class_name,
    #             source_component_field_name=SimpleDHWStorage.ThermalPowerConsumptionDHW,
    #             source_load_type=lt.LoadTypes.HEATING,
    #             source_unit=lt.Units.WATT,
    #             source_tags=[lt.InandOutputType.HEAT_CONSUMPTION],
    #             source_weight=999,
    #         )
    #     )
    #     return dynamic_connections

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ):
        """Get gas heater default connections."""

        from hisim.components.more_advanced_heat_pump_hplib import (  # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLib,
        )

        dynamic_connections = []
        hp_class_name = MoreAdvancedHeatPumpHPLib.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLib,
                source_class_name=hp_class_name,
                source_component_field_name=MoreAdvancedHeatPumpHPLib.ThermalOutputPowerTotal,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT,
                source_tags=[lt.InandOutputType.HEAT_DELIVERED],
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
                source_component_field_name=HeatSource.ThermalPowerDelivered,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.InandOutputType.HEAT_DELIVERED,
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
            self.production_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.HEAT_DELIVERED])
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.HEAT_CONSUMPTION])

        # get sum of production and consumption for all inputs for each iteration
        production_in_watt_hour = (
            sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
            * self.seconds_per_timestep
            / 3600
        )

        consumption_uncontrolled_in_watt_hour = (
            sum([stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs])
            * self.seconds_per_timestep
            / 3600
        )

        # Production of Heat positve sign
        # Consumption of Heat negative sign
        difference_between_production_and_consumption_in_watt_hour = (
            production_in_watt_hour - consumption_uncontrolled_in_watt_hour
        )

        # calculate cumulative production and consumption
        cumulative_production_in_watt_hour = self.state.cumulative_production_in_watt_hour + production_in_watt_hour
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour + consumption_uncontrolled_in_watt_hour
        )

        # set outputs
        stsv.set_output_value(
            self.heat_available_channel,
            difference_between_production_and_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.heat_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )

        stsv.set_output_value(
            self.heat_production_channel,
            production_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_heat_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )

        stsv.set_output_value(
            self.cumulative_heat_production_channel,
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
        total_used_energy_in_kwh: float
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.HeatConsumption:
                    total_used_energy_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_used_energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=total_used_energy_in_watt,
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    )

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.contracting_heating_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.contracting_heating_costs_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = total_used_energy_in_kwh * euro_per_unit
        co2_per_simulated_period_in_kg = total_used_energy_in_kwh * co2_per_unit
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            consumption_in_kwh=total_used_energy_in_kwh,
            loadtype=lt.LoadTypes.GAS,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_used_energy_in_kwh: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.HEATING:
                if output.field_name == self.HeatConsumption:
                    total_used_energy_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_used_energy_in_kwh = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=total_used_energy_in_watt,
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    )

                    break

        total_heating_energy_consumption_in_building_in_kwh_entry = KpiEntry(
            name="Total heating energy used in building",
            unit="kWh",
            value=total_used_energy_in_kwh,
            tag=KpiTagEnumClass.HEATING_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_heating_energy_consumption_in_building_in_kwh_entry)
        # try to get opex costs
        opex_costs = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        opex_costs_in_euro_entry = KpiEntry(
            name="Opex costs of heat consumption in building",
            unit="Euro",
            value=opex_costs.opex_energy_cost_in_euro,
            tag=KpiTagEnumClass.HEATING_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex_costs_in_euro_entry)
        co2_footprint_in_kg_entry = KpiEntry(
            name="CO2 footprint of heat consumption in building",
            unit="kg",
            value=opex_costs.co2_footprint_in_kg,
            tag=KpiTagEnumClass.HEATING_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_in_kg_entry)

        return list_of_kpi_entries


@dataclass
class HeatingMeterState:
    """HeatingMeterState class."""

    cumulative_production_in_watt_hour: float
    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the GasMeterState."""
        return HeatingMeterState(
            self.cumulative_production_in_watt_hour,
            self.cumulative_consumption_in_watt_hour,
        )
