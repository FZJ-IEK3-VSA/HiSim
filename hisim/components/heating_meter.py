"""Heating meter module to measure energy consumption for all fuel types except gas (natural and hydrogen) and electricity."""

# clean
from dataclasses import dataclass
from typing import List, Optional, Any

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
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
    fuel_loadtype: lt.LoadTypes
    heating_value_of_fuel_in_kwh_per_liter: Optional[float]
    fuel_density_in_kg_per_m3: Optional[float]

    @classmethod
    def get_heating_meter_default_config(
        cls,
        building_name: str = "BUI1",
        fuel_loadtype: lt.LoadTypes = lt.LoadTypes.OIL,
        heating_value_of_fuel_in_kwh_per_liter: Optional[float] = 9.82,  # configuration.py
        fuel_density_in_kg_per_m3: Optional[float] = 0.83 * 1e3,  # configuration.py
    ) -> Any:
        """Gets a default HeatingMeter."""
        return HeatingMeterConfig(
            building_name=building_name,
            name="HeatingMeter",
            fuel_loadtype=fuel_loadtype,
            heating_value_of_fuel_in_kwh_per_liter=heating_value_of_fuel_in_kwh_per_liter,
            fuel_density_in_kg_per_m3=fuel_density_in_kg_per_m3
        )


class HeatingMeter(DynamicComponent):
    """Heating meter class."""

    # Outputs
    HeatConsumption = "HeatConsumption"
    CumulativeConsumption = "CumulativeConsumption"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatingMeterConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ):
        """Initialize the component."""
        self.config = config
        self.name = self.config.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # check if component has valid gas loadtype
        if self.config.fuel_loadtype not in [
            lt.LoadTypes.OIL,
            lt.LoadTypes.PELLETS,
            lt.LoadTypes.WOOD_CHIPS,
            lt.LoadTypes.DISTRICTHEATING,
        ]:
            raise ValueError(
                f"HeatingMeter {self.component_name} has invalid fuel loadtype: {self.config.fuel_loadtype}. "
                f"Either use {lt.LoadTypes.OIL}, {lt.LoadTypes.PELLETS}, {lt.LoadTypes.WOOD_CHIPS} or {lt.LoadTypes.DISTRICTHEATING} "
                "or add new fuel_type (except gas or electricity, for those there are already meters available)"
            )

        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # Component has states
        self.state = HeatingMeterState(cumulative_consumption_in_watt_hour=0)
        self.previous_state = self.state.self_copy()

        self.heat_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatConsumption,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.HeatConsumption} will follow.",
        )

        self.cumulative_heat_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeConsumption,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeConsumption} will follow.",
        )

        self.add_dynamic_default_connections(self.get_default_connections_from_generic_district_heating())
        self.add_dynamic_default_connections(self.get_default_connections_from_generic_boiler())

    def get_default_connections_from_generic_district_heating(
        self,
    ):
        """Get generic district heating default connections."""

        from hisim.components.generic_district_heating import DistrictHeating  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        heat_source_class_name = DistrictHeating.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=DistrictHeating,
                source_class_name=heat_source_class_name,
                source_component_field_name=DistrictHeating.ThermalOutputShEnergy,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[
                    lt.InandOutputType.HEAT_CONSUMPTION,
                ],
                source_weight=999,
            )
        )
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=DistrictHeating,
                source_class_name=heat_source_class_name,
                source_component_field_name=DistrictHeating.ThermalOutputDhwEnergy,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[
                    lt.InandOutputType.HEAT_CONSUMPTION,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_generic_boiler(
        self,
    ):
        """Get generic district boiler default connections."""

        from hisim.components.generic_boiler import GenericBoiler  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        heat_source_class_name = GenericBoiler.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=GenericBoiler,
                source_class_name=heat_source_class_name,
                source_component_field_name=GenericBoiler.EnergyDemandSh,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[
                    lt.InandOutputType.HEAT_CONSUMPTION,
                ],
                source_weight=999,
            )
        )
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=GenericBoiler,
                source_class_name=heat_source_class_name,
                source_component_field_name=GenericBoiler.EnergyDemandDhw,
                source_load_type=lt.LoadTypes.HEATING,
                source_unit=lt.Units.WATT_HOUR,
                source_tags=[
                    lt.InandOutputType.HEAT_CONSUMPTION,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def write_to_report(self):
        """Writes relevant information to report."""
        return self.config.get_string_dict()

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
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.HEAT_CONSUMPTION])

        # get sum of consumptions of all inputs
        consumption_uncontrolled_in_watt_hour = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )

        # calculate cumulative consumption
        cumulative_consumption_in_watt_hour = (
            self.state.cumulative_consumption_in_watt_hour + consumption_uncontrolled_in_watt_hour
        )

        # set outputs

        stsv.set_output_value(
            self.heat_consumption_channel,
            consumption_uncontrolled_in_watt_hour,
        )
        stsv.set_output_value(
            self.cumulative_heat_consumption_channel,
            cumulative_consumption_in_watt_hour,
        )
        self.state.cumulative_consumption_in_watt_hour = cumulative_consumption_in_watt_hour

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of gas costs and revenues."""
        total_heat_consumed_in_kwh: float
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.HeatConsumption and output.unit == lt.Units.WATT_HOUR:
                    total_heat_consumed_in_kwh = sum(postprocessing_results.iloc[:, index]) * 1e-3

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        if (
            self.config.heating_value_of_fuel_in_kwh_per_liter is not None
            and self.config.fuel_density_in_kg_per_m3 is not None
        ):
            fuel_consumption_in_liter = round(
                total_heat_consumed_in_kwh / self.config.heating_value_of_fuel_in_kwh_per_liter, 1
            )
            fuel_consumption_in_kg = round(fuel_consumption_in_liter * 1e-3 * self.config.fuel_density_in_kg_per_m3, 1)

        if self.config.fuel_loadtype == lt.LoadTypes.OIL:

            co2_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
            euro_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
            opex_cost_per_simulated_period_in_euro = fuel_consumption_in_liter * euro_per_unit
            co2_per_simulated_period_in_kg = fuel_consumption_in_liter * co2_per_unit

        elif self.config.fuel_loadtype == lt.LoadTypes.PELLETS:

            co2_per_unit = emissions_and_cost_factors.pellet_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.pellet_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = total_heat_consumed_in_kwh * co2_per_unit
            opex_cost_per_simulated_period_in_euro = fuel_consumption_in_kg / 1000 * euro_per_unit

        elif self.config.fuel_loadtype == lt.LoadTypes.WOOD_CHIPS:

            co2_per_unit = emissions_and_cost_factors.wood_chip_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.wood_chip_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = total_heat_consumed_in_kwh * co2_per_unit
            opex_cost_per_simulated_period_in_euro = fuel_consumption_in_kg / 1000 * euro_per_unit

        elif self.config.fuel_loadtype == lt.LoadTypes.DISTRICTHEATING:
            co2_per_unit = emissions_and_cost_factors.district_heating_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.district_heating_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = total_heat_consumed_in_kwh * co2_per_unit
            opex_cost_per_simulated_period_in_euro = total_heat_consumed_in_kwh * euro_per_unit
        else:
            raise ValueError(f"The loadtype {self.config.fuel_loadtype} is not implemented for the heating meter.")

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=round(opex_cost_per_simulated_period_in_euro, 2),
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=round(co2_per_simulated_period_in_kg, 2),
            total_consumption_in_kwh=round(total_heat_consumed_in_kwh, 2),
            loadtype=self.config.fuel_loadtype,
            kpi_tag=KpiTagEnumClass.HEATING_METER,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )

        # Energy related KPIs
        energy_consumption = KpiEntry(
            name="Total energy consumption",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        # Economic and environmental KPIs

        opex = KpiEntry(
            name="OPEX - Energy costs",
            unit="EUR",
            value=opex_dataclass.opex_energy_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex)

        maintenance_costs = KpiEntry(
            name="OPEX - Maintenance costs",
            unit="EUR",
            value=opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(maintenance_costs)

        co2_footprint = KpiEntry(
            name="OPEX - CO2 Footprint",
            unit="kg",
            value=opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint)

        return list_of_kpi_entries

    @staticmethod
    def get_cost_capex(
        config: HeatingMeterConfig, simulation_parameters: SimulationParameters
    ) -> cp.CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = cp.CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class


@dataclass
class HeatingMeterState:
    """HeatingMeterState class."""

    cumulative_consumption_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the GasMeterState."""
        return HeatingMeterState(
            self.cumulative_consumption_in_watt_hour,
        )
