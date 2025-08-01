"""Electricity meter module should replace the sumbuilder. """

# clean
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim.component import ComponentInput, OpexCostDataClass, CapexCostDataClass
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicConnectionInput,
    DynamicConnectionOutput,
    DynamicComponentConnection,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


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
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float]
    #: lifetime in years
    lifetime_in_years: Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]

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
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
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
    ElectricityProductionInWatt = "ElectricityProductionInWatt"
    ElectricityConsumptionInWatt = "ElectricityConsumptionInWatt"
    ElectricityConsumptionOfBuildingsInWatt = "ElectricityConsumptionOfBuildingsInWatt"
    SurplusUnusedFromBuildingEMSOutput = "SurplusUnusedFromBuildingEMSOutput"

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
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            self.my_component_inputs,
            self.my_component_outputs,
            name=component_name,
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
        )
        self.electricity_from_grid_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityFromGridInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityFromGridInWatt} will follow.",
        )
        self.electricity_production_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProductionInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityProductionInWatt} will follow.",
        )
        if any(word in config.building_name for word in lt.DistrictNames):
            self.surplus_electricity_unused_to_district_ems_from_building_ems_output: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.SurplusUnusedFromBuildingEMSOutput,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                sankey_flow_direction=False,
                output_description=f"here a description for {self.SurplusUnusedFromBuildingEMSOutput} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_PRODUCTION,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if any(word in config.building_name for word in lt.DistrictNames)
                    else []
                ),
            )
            self.electricity_consumption_building_uncontrolled_in_watt_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricityConsumptionOfBuildingsInWatt,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                sankey_flow_direction=False,
                output_description=f"here a description for {self.ElectricityConsumptionOfBuildingsInWatt} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if any(word in config.building_name for word in lt.DistrictNames)
                    else []
                ),
            )
        self.electricity_consumption_uncontrolled_in_watt_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumptionInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityConsumptionInWatt} will follow.",
            postprocessing_flag=(
                [
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ]
                if any(word in config.building_name for word in lt.DistrictNames)
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
        self.add_dynamic_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_electric_heater())
        self.add_dynamic_default_connections(self.get_default_connections_from_solar_thermal_system())

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
                source_component_field_name=UtspLpgConnector.ElectricalPowerConsumption,
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

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ):
        """Get more advanced heat pump default connections."""

        from hisim.components.more_advanced_heat_pump_hplib import (   # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLib,
        )
        dynamic_connections = []
        more_advanced_heat_pump_class_name = MoreAdvancedHeatPumpHPLib.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLib,
                source_class_name=more_advanced_heat_pump_class_name,
                source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )
        )
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLib,
                source_class_name=more_advanced_heat_pump_class_name,
                source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_DHW,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_electric_heater(
        self,
    ):
        """Get electric heater default connections."""

        from hisim.components.generic_electric_heating import ElectricHeating  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        electric_boiler_class_name = ElectricHeating.get_classname()
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=ElectricHeating,
                source_class_name=electric_boiler_class_name,
                source_component_field_name=ElectricHeating.ElectricOutputShPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.ELECTRIC_HEATING_SH,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )
        )
        dynamic_connections.append(
            DynamicComponentConnection(
                source_component_class=ElectricHeating,
                source_class_name=electric_boiler_class_name,
                source_component_field_name=ElectricHeating.ElectricOutputDhwPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.ELECTRIC_HEATING_DHW,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                ],
                source_weight=999,
            )
        )
        return dynamic_connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ):
        """Get solar thermal default connections."""

        from hisim.components.solar_thermal_system import SolarThermalSystem  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        solar_thermal_class_name = SolarThermalSystem.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=SolarThermalSystem,
                source_class_name=solar_thermal_class_name,
                source_component_field_name=SolarThermalSystem.ElectricityConsumptionOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.SOLAR_THERMAL_SYSTEM, lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
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

        if any(word in self.config.building_name for word in lt.DistrictNames):
            production_inputs_building = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION, lt.ComponentType.BUILDINGS])

            building_electricity_surplus_unused = (
                sum([stsv.get_input_value(component_input=elem) for elem in production_inputs_building]))

            stsv.set_output_value(
                self.surplus_electricity_unused_to_district_ems_from_building_ems_output,
                building_electricity_surplus_unused,
            )

            consumption_inputs_building = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED, lt.ComponentType.BUILDINGS])

            consumption_of_buildings = (
                sum([stsv.get_input_value(component_input=elem) for elem in consumption_inputs_building]))

            stsv.set_output_value(
                self.electricity_consumption_building_uncontrolled_in_watt_channel,
                consumption_of_buildings,
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
            self.electricity_production_in_watt_channel,
            production_in_watt,
        )

        stsv.set_output_value(
            self.electricity_consumption_uncontrolled_in_watt_channel,
            consumption_uncontrolled_in_watt,
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
                if output.field_name == self.ElectricityToGrid and output.unit == lt.Units.WATT_HOUR:
                    # Todo: check component name from system_setups: find another way of using the correct outputs
                    total_energy_to_grid_in_kwh = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 2)

                elif output.field_name == self.ElectricityFromGrid and output.unit == lt.Units.WATT_HOUR:
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
            opex_energy_cost_in_euro=opex_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=total_energy_from_grid_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ELECTRICITY_METER
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(config: ElectricityMeterConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.ELECTRICITY_METER
        kpi_tag = (
            KpiTagEnumClass.ELECTRICITY_METER
        )
        unit = lt.Units.ANY
        size_of_energy_system = 1

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)

        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_energy_from_grid_in_kwh: float
        total_energy_to_grid_in_kwh: float
        total_power_from_grid_in_watt: float
        total_power_to_grid_in_watt: float
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.ELECTRICITY:
                if output.field_name == self.ElectricityFromGrid:
                    total_energy_from_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3
                elif output.field_name == self.ElectricityToGrid:
                    total_energy_to_grid_in_kwh = postprocessing_results.iloc[:, index].sum() * 1e-3
                elif output.field_name == self.ElectricityFromGridInWatt:
                    total_power_from_grid_in_watt = postprocessing_results.iloc[:, index] * 1e-3
                elif output.field_name == self.ElectricityToGridInWatt:
                    total_power_to_grid_in_watt = postprocessing_results.iloc[:, index] * 1e-3

        (mean_total_power_from_grid_in_watt,
        max_total_power_from_grid_in_watt,
        min_total_power_from_grid_in_watt,
         ) = KpiHelperClass.calc_mean_max_min_value(list_or_pandas_series=total_power_from_grid_in_watt)

        (mean_total_power_to_grid_in_watt,
        max_total_power_to_grid_in_watt,
        min_total_power_to_grid_in_watt,
         ) = KpiHelperClass.calc_mean_max_min_value(list_or_pandas_series=total_power_to_grid_in_watt)

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

        mean_total_power_from_grid_in_watt_entry = KpiEntry(
            name="Mean power from grid",
            unit="kW",
            value=mean_total_power_from_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_total_power_from_grid_in_watt_entry)

        max_total_power_from_grid_in_watt_entry = KpiEntry(
            name="Max power from grid",
            unit="kW",
            value=max_total_power_from_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_total_power_from_grid_in_watt_entry)

        min_total_power_from_grid_in_watt_entry = KpiEntry(
            name="Min power from grid",
            unit="kW",
            value=min_total_power_from_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_total_power_from_grid_in_watt_entry)

        mean_total_power_to_grid_in_watt_entry = KpiEntry(
            name="Mean power to grid",
            unit="kW",
            value=mean_total_power_to_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_total_power_to_grid_in_watt_entry)

        max_total_power_to_grid_in_watt_entry = KpiEntry(
            name="Max power to grid",
            unit="kW",
            value=max_total_power_to_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_total_power_to_grid_in_watt_entry)

        min_total_power_to_grid_in_watt_entry = KpiEntry(
            name="Min power to grid",
            unit="kW",
            value=min_total_power_to_grid_in_watt,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_total_power_to_grid_in_watt_entry)

        # get opex costs
        opex_costs = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        opex_costs_in_euro_entry = KpiEntry(
            name="Opex costs of electricity consumption from grid",
            unit="Euro",
            value=opex_costs.opex_energy_cost_in_euro,
            tag=KpiTagEnumClass.ELECTRICITY_METER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex_costs_in_euro_entry)
        co2_footprint_in_kg_entry = KpiEntry(
            name="CO2 footprint of electricity consumption from grid",
            unit="kg",
            value=opex_costs.co2_footprint_in_kg,
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
