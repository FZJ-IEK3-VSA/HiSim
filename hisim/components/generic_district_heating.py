"""District Heating Module."""

# clean
# Owned
# import importlib
from dataclasses import dataclass
import enum
import logging
from typing import List, Any, Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.loadtypes import LoadTypes, Units
from hisim.component import (
    Component,
    ComponentConnection,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass,
)
from hisim.components.heat_distribution_system import (
    HeatDistributionController,
    HeatDistribution,
)
from hisim.components.weather import Weather
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
    PhysicsConfig,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)

__authors__ = "Katharina Rieck, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


class HeatingMode(enum.Enum):
    """Heating mode of the district heating component."""

    OFF = 0
    SPACE_HEATING = 1
    DOMESTIC_HOT_WATER = 2


@dataclass_json
@dataclass
class DistrictHeatingConfig(ConfigBase):
    """Configuration of the District Heating class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return DistrictHeating.get_full_classname()

    building_name: str
    name: str
    # Maximum thermal power that can be delivered
    connected_load_w: float
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    with_domestic_hot_water_preparation: bool

    @classmethod
    def get_default_district_heating_config(
        cls,
        building_name: str = "BUI1",
        with_domestic_hot_water_preparation=False,
    ) -> Any:
        """Get a default district heating."""
        config = DistrictHeatingConfig(
            building_name=building_name,
            name="DistrictHeating",
            connected_load_w=20000,
            # source: https://www.oekobaudat.de/OEKOBAU.DAT/datasetdetail/process.xhtml?
            # lang=de&uuid=dcd5e23a-9bec-40b6-b07c-1642fe696a2e Production and transport
            co2_footprint=4.780735,
            cost=7500,  # approximate value based on https://www.co2online.de/modernisieren-und-bauen/heizung/fernwaerme/
            lifetime=30,  # source: https://www.oekobaudat.de/OEKOBAU.DAT/datasetdetail/process.xhtml?lang=de&uuid=dcd5e23a-9bec-40b6-b07c-1642fe696a2e
            maintenance_cost_as_percentage_of_investment=0,  # source: VDI2067
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )
        return config


class DistrictHeating(Component):
    """District Heating class."""

    # Inputs
    HeatingMode = "HeatingMode"

    # Inputs for space heating
    DeltaTemperatureNeeded = "DeltaTemperatureNeededSh"  # how much water temperature needs to be increased
    WaterInputTemperatureSh = "WaterInputTemperatureSh"
    WaterInputMassFlowRateFromHeatDistributionSystem = (
        "WaterInputMassFlowRateFromHeatDistributionSystem"
    )

    # Inputs for DHW
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"
    WaterInputMassFlowRateFromWarmWaterStorage = (
        "WaterInputMassFlowRateFromWarmWaterStorage"
    )

    # Output
    WaterOutputShTemperature = "WaterOutputShTemperature"
    ThermalOutputShPower = "ThermalOutputShPower"
    ThermalOutputShEnergy = "ThermalOutputShEnergy"
    WaterOutputShMassFlowRate = "WaterOutputShMassFlowRate"
    WaterOutputDhwTemperature = "WaterOutputDhwTemperature"
    ThermalOutputDhwPower = "ThermalOutputDhwPower"
    ThermalOutputDhwEnergy = "ThermalOutputDhwEnergy"
    WaterOutputDhwMassFlowRate = "WaterOutputDhwMassFlowRate"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(
            display_in_webtool=True
        ),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.district_heating_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # Inputs
        self.heating_mode_channel: ComponentInput = self.add_input(
            self.component_name,
            DistrictHeating.HeatingMode,
            LoadTypes.ANY,
            Units.ANY,
            True,
        )
        self.delta_temperature_channel: ComponentInput = self.add_input(
            self.component_name,
            DistrictHeating.DeltaTemperatureNeeded,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.water_input_temperature_sh_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                DistrictHeating.WaterInputTemperatureSh,
                LoadTypes.WATER,
                Units.CELSIUS,
                True,
            )
        )
        self.water_input_mass_flow_rate_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            DistrictHeating.WaterInputMassFlowRateFromHeatDistributionSystem,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            True,
        )
        if self.config.with_domestic_hot_water_preparation:
            self.water_input_temperature_dhw_channel: ComponentInput = (
                self.add_input(
                    self.component_name,
                    DistrictHeating.WaterInputTemperatureDhw,
                    LoadTypes.WATER,
                    Units.CELSIUS,
                    True,
                )
            )
            self.water_input_mass_flow_rate_dhw_channel: ComponentInput = (
                self.add_input(
                    self.component_name,
                    DistrictHeating.WaterInputMassFlowRateFromWarmWaterStorage,
                    LoadTypes.WATER,
                    Units.KG_PER_SEC,
                    True,
                )
            )

        # Outputs Space Heating
        self.water_mass_flow_sh_output_channel: ComponentOutput = (
            self.add_output(
                self.component_name,
                DistrictHeating.WaterOutputShMassFlowRate,
                LoadTypes.WATER,
                Units.KG_PER_SEC,
                output_description="Water mass flow rate for space heating.",
            )
        )
        self.water_output_temperature_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeating.WaterOutputShTemperature,
            LoadTypes.WATER,
            Units.CELSIUS,
            output_description="Water output temperature for space heating.",
        )
        self.thermal_output_power_sh_channel: ComponentOutput = (
            self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalOutputShPower,
                load_type=LoadTypes.HEATING,
                unit=Units.WATT,
                output_description="Thermal power output for space heating",
            )
        )
        self.thermal_output_energy_sh_channel: ComponentOutput = (
            self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalOutputShEnergy,
                load_type=LoadTypes.HEATING,
                unit=Units.WATT_HOUR,
                output_description="Thermal energy output for space heating",
            )
        )

        # Outputs DHW
        self.water_mass_flow_dhw_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeating.WaterOutputDhwMassFlowRate,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description="Water mass flow rate for domestic hot water.",
        )
        self.water_output_temperature_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeating.WaterOutputDhwTemperature,
            LoadTypes.WATER,
            Units.CELSIUS,
            output_description="Water output temperature for domestic hot water.",
        )
        self.thermal_output_power_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputDhwPower,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.WATT,
            output_description="Thermal power output for domestic hot water.",
        )
        self.thermal_output_energy_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputDhwEnergy,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.WATT_HOUR,
            output_description="Thermal energy output for domestic hot water.",
        )
        self.add_default_connections(
            self.get_default_connections_from_district_heating_controller()
        )
        self.add_default_connections(
            self.get_default_connections_from_heat_distribution_system()
        )
        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(
                self.get_default_connections_from_simple_dhw_storage()
            )

    def get_default_connections_from_district_heating_controller(
        self,
    ):
        """Get Controller District Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = DistrictHeatingController
        connections = []
        controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeating.HeatingMode,
                controller_classname,
                component_class.HeatingMode,
            )
        )
        connections.append(
            ComponentConnection(
                DistrictHeating.DeltaTemperatureNeeded,
                controller_classname,
                component_class.DeltaTemperatureNeeded,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_system(
        self,
    ):
        """Get heat distribution system default connections."""

        component_class = HeatDistribution
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeating.WaterInputTemperatureSh,
                hws_classname,
                component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            ComponentConnection(
                DistrictHeating.WaterInputMassFlowRateFromHeatDistributionSystem,
                hws_classname,
                component_class.WaterMassFlowHDS,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple dhw storage default connections."""

        component_class = SimpleDHWStorage
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeating.WaterInputTemperatureDhw,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        connections.append(
            ComponentConnection(
                DistrictHeating.WaterInputMassFlowRateFromWarmWaterStorage,
                hws_classname,
                component_class.WaterMassFlowRateOfDHW,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.district_heating_config.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the district heating."""
        if force_convergence:
            return

        # get inputs
        heating_mode = HeatingMode(
            stsv.get_input_value(self.heating_mode_channel)
        )
        if heating_mode == HeatingMode.SPACE_HEATING:
            # Get relevant inputs
            delta_temperature_needed_in_celsius = stsv.get_input_value(
                self.delta_temperature_channel
            )

            # check values
            if delta_temperature_needed_in_celsius < 0:
                raise ValueError(
                    f"Delta temperature is {delta_temperature_needed_in_celsius} °C"
                    "but it should not be negative because district heating cannot provide cooling. "
                    "Please check your district heating controller."
                )
            if delta_temperature_needed_in_celsius > 100:
                raise ValueError(
                    f"Delta temperature is {delta_temperature_needed_in_celsius} °C in timestep {timestep}."
                    "This is way too high. "
                )

            water_input_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_sh_channel
            )
            water_mass_flow_rate_in_kg_per_s = stsv.get_input_value(
                self.water_input_mass_flow_rate_sh_channel
            )

            thermal_power_delivered_w = (
                water_mass_flow_rate_in_kg_per_s
                * PhysicsConfig.get_properties_for_energy_carrier(
                    energy_carrier=LoadTypes.WATER
                ).specific_heat_capacity_in_joule_per_kg_per_kelvin
                * delta_temperature_needed_in_celsius
            )

            if thermal_power_delivered_w > self.config.connected_load_w:
                # make sure that not more power is delivered than available
                logging.warning(
                    "The needed thermal power for space heating is higher than the maximum connected load."
                )
                thermal_power_delivered_w = self.config.connected_load_w
                delta_temperature_achieved = thermal_power_delivered_w / (
                    water_mass_flow_rate_in_kg_per_s
                    * PhysicsConfig.get_properties_for_energy_carrier(
                        energy_carrier=LoadTypes.WATER
                    ).specific_heat_capacity_in_joule_per_kg_per_kelvin
                )
                water_output_temperature_deg_c = (
                    water_input_temperature_deg_c + delta_temperature_achieved
                )
            else:
                water_output_temperature_deg_c = (
                    water_input_temperature_deg_c
                    + delta_temperature_needed_in_celsius
                )

            thermal_energy_delivered_in_watt_hour = (
                thermal_power_delivered_w
                * self.my_simulation_parameters.seconds_per_timestep
                / 3.6e3
            )
            # Set outputs
            stsv.set_output_value(
                self.thermal_output_power_sh_channel, thermal_power_delivered_w
            )
            stsv.set_output_value(
                self.thermal_output_energy_sh_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                water_output_temperature_deg_c,
            )
            stsv.set_output_value(
                self.water_mass_flow_sh_output_channel,
                water_mass_flow_rate_in_kg_per_s,
            )

            stsv.set_output_value(self.thermal_output_power_dhw_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_dhw_channel, 0)
            current_dhw_water_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_dhw_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                current_dhw_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_dhw_output_channel, 0)

        elif heating_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # Get relevant inputs
            delta_temperature_needed_in_celsius = stsv.get_input_value(
                self.delta_temperature_channel
            )
            water_input_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_dhw_channel
            )

            # check values
            if delta_temperature_needed_in_celsius < 0:
                raise ValueError(
                    f"Delta temperature is {delta_temperature_needed_in_celsius} °C"
                    "but it should not be negative because district heating cannot provide cooling. "
                    "Please check your district heating controller."
                )

            # calculate output temperature
            water_target_temperature_deg_c = (
                water_input_temperature_deg_c
                + delta_temperature_needed_in_celsius
            )

            # calculate thermal power delivered Q = m * cw * dT
            thermal_power_delivered_w = (
                self.config.connected_load_w
                if delta_temperature_needed_in_celsius > 0
                else 0
            )
            water_mass_flow_rate_in_kg_per_s = thermal_power_delivered_w / (
                PhysicsConfig.get_properties_for_energy_carrier(
                    energy_carrier=LoadTypes.WATER
                ).specific_heat_capacity_in_joule_per_kg_per_kelvin
                * delta_temperature_needed_in_celsius
            )
            water_target_temperature_deg_c = (
                water_input_temperature_deg_c
                + delta_temperature_needed_in_celsius
            )
            thermal_energy_delivered_in_watt_hour = (
                thermal_power_delivered_w
                * self.my_simulation_parameters.seconds_per_timestep
                / 3.6e3
            )
            # Set outputs
            stsv.set_output_value(
                self.thermal_output_power_dhw_channel,
                thermal_power_delivered_w,
            )
            stsv.set_output_value(
                self.thermal_output_energy_dhw_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                water_target_temperature_deg_c,
            )
            stsv.set_output_value(
                self.water_mass_flow_dhw_output_channel,
                water_mass_flow_rate_in_kg_per_s,
            )

            stsv.set_output_value(self.thermal_output_power_sh_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_sh_channel, 0)
            current_sh_water_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_sh_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                current_sh_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_sh_output_channel, 0)

        elif heating_mode == HeatingMode.OFF:
            stsv.set_output_value(self.thermal_output_power_dhw_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_dhw_channel, 0)
            current_dhw_water_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_dhw_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                current_dhw_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_dhw_output_channel, 0)

            stsv.set_output_value(self.thermal_output_power_sh_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_sh_channel, 0)
            current_sh_water_temperature_deg_c = stsv.get_input_value(
                self.water_input_temperature_sh_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                current_sh_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_sh_output_channel, 0)
        else:
            raise ValueError("Unknown heating mode")

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.HEATING
                and output.field_name == self.ThermalOutputShPower
                and output.unit == Units.WATT
            ):
                consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
        assert consumption_in_kwh is not None

        emissions_and_cost_factors = (
            EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                self.my_simulation_parameters.year
            )
        )
        co2_per_unit = (
            emissions_and_cost_factors.district_heating_footprint_in_kg_per_kwh
        )
        euro_per_unit = (
            emissions_and_cost_factors.district_heating_costs_in_euro_per_kwh
        )
        co2_per_simulated_period_in_kg = consumption_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = (
            consumption_in_kwh * euro_per_unit
        )

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            consumption_in_kwh=consumption_in_kwh,
            loadtype=LoadTypes.DISTRICTHEATING,
            kpi_tag=KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING,
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: DistrictHeatingConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.cost / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (
            config.co2_footprint / config.lifetime
        ) * (simulation_parameters.duration.total_seconds() / seconds_per_year)

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.cost,
            device_co2_footprint_in_kg=config.co2_footprint,
            lifetime_in_years=config.lifetime,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
        )

        capex_cost_data_class.kpi_tag = (
            KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING
        )

        return capex_cost_data_class

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
        capex_dataclass = self.get_cost_capex(
            self.config, self.my_simulation_parameters
        )

        # Energy related KPIs
        energy_consumption = KpiEntry(
            name="Energy consumption for space heating",
            unit="kWh",
            value=opex_dataclass.consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        # Economic and environmental KPIs
        capex = KpiEntry(
            name="CAPEX - Investment cost",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(capex)

        co2_footprint_capex = KpiEntry(
            name="CAPEX - CO2 Footprint",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_capex)

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

        total_costs = KpiEntry(
            name="Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_for_simulated_period_in_euro
            + opex_dataclass.opex_energy_cost_in_euro
            + opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_costs)

        total_co2_footprint = KpiEntry(
            name="Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg
            + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
        return list_of_kpi_entries


@dataclass_json
@dataclass
class DistrictHeatingControllerConfig(ConfigBase):
    """District Heating Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return DistrictHeatingController.get_full_classname()

    building_name: str
    name: str
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    with_domestic_hot_water_preparation: float
    offset: float  # overheating of dhw storage

    @classmethod
    def get_default_district_heating_controller_config(
        cls,
        building_name: str = "BUI1",
        with_domestic_hot_water_preparation=False,
    ) -> Any:
        """Gets a default district heating controller."""
        return DistrictHeatingControllerConfig(
            building_name=building_name,
            name="DistrictHeatingController",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
            offset=15,
        )


class DistrictHeatingController(Component):
    """District Heating Controller."""

    # Inputs
    WaterTemperatureInputFromHeatDistributionSystem = (
        "WaterTemperatureInputFromHeatDistributionSystem"
    )
    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = (
        "HeatingFlowTemperatureFromHeatDistributionSystem"
    )

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Relevant when used for dhw as well
    WaterTemperatureInputFromWarmWaterStorage = (
        "WaterTemperatureInputFromWarmWaterStorage"
    )

    # Outputs
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"
    HeatingMode = "HeatingMode"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.district_heating_controller_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build()

        # input channel
        self.water_temperature_input_channel_sh: ComponentInput = (
            self.add_input(
                self.component_name,
                self.WaterTemperatureInputFromHeatDistributionSystem,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        )
        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        if self.config.with_domestic_hot_water_preparation:
            self.water_temperature_input_channel_dhw: ComponentInput = (
                self.add_input(
                    self.component_name,
                    self.WaterTemperatureInputFromWarmWaterStorage,
                    LoadTypes.TEMPERATURE,
                    Units.CELSIUS,
                    True,
                )
            )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.DailyAverageOutsideTemperature,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        )

        self.delta_temperature_to_district_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.DeltaTemperatureNeeded,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=f"here a description for {self.DeltaTemperatureNeeded} will follow.",
        )

        self.controller_mode: HeatingMode
        self.previous_controller_mode: HeatingMode

        self.heating_mode_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingMode,
            LoadTypes.ANY,
            Units.ANY,
            output_description="Heating mode of district heating.",
        )

        self.add_default_connections(
            self.get_default_connections_from_weather()
        )
        self.add_default_connections(
            self.get_default_connections_from_heat_distribution()
        )
        self.add_default_connections(
            self.get_default_connections_from_heat_distribution_controller()
        )

        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(
                self.get_default_connections_from_simple_dhw_storage()
            )

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.WaterTemperatureInputFromWarmWaterStorage,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution(
        self,
    ):
        """Get heat ditribution default connections."""

        connections = []
        source_classname = HeatDistribution.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.WaterTemperatureInputFromHeatDistributionSystem,
                source_classname,
                HeatDistribution.WaterTemperatureOutput,
            )
        )
        return connections

    def get_default_connections_from_weather(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_controller(
        self,
    ):
        """Get heat distribution controller default connections."""

        connections = []
        hds_controller_classname = HeatDistributionController.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.HeatingFlowTemperatureFromHeatDistributionSystem,
                hds_controller_classname,
                HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        # warm water should aim for 55°C, should be 60°C when leaving heat generator, see source below
        # https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser#undefined
        self.warm_water_temperature_aim_in_celsius: float = 60.0
        self.controller_mode = HeatingMode.OFF
        self.previous_controller_mode = self.controller_mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_mode = self.controller_mode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_mode = self.previous_controller_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.district_heating_controller_config.get_string_dict()

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the district heating comtroller."""

        if force_convergence:
            return

        # Retrieves inputs
        water_temperature_input_from_heat_distibution_in_celsius = (
            stsv.get_input_value(self.water_temperature_input_channel_sh)
        )

        heating_flow_temperature_from_heat_distribution_in_celsius = stsv.get_input_value(
            self.heating_flow_temperature_from_heat_distribution_system_channel
        )
        if self.config.with_domestic_hot_water_preparation:
            water_temperature_input_from_warm_water_storage_in_celsius = (
                stsv.get_input_value(
                    self.water_temperature_input_channel_dhw
                )
            )

        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_avg_outside_temperature_input_channel
        )

        # turning district heating off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
        summer_heating_mode = self.summer_heating_condition(
            daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
            set_heating_threshold_temperature_in_celsius=self.district_heating_controller_config.set_heating_threshold_outside_temperature_in_celsius,
        )

        # on/off controller
        self.determine_operating_mode(
            water_temperature_input_sh_in_celsius=water_temperature_input_from_heat_distibution_in_celsius,
            set_heating_flow_temperature_sh_in_celsius=heating_flow_temperature_from_heat_distribution_in_celsius,
            water_temperature_input_dhw_in_celsius=water_temperature_input_from_warm_water_storage_in_celsius
            if self.config.with_domestic_hot_water_preparation
            else None,
            set_heating_flow_temperature_dhw_in_celsius=self.warm_water_temperature_aim_in_celsius
            if self.config.with_domestic_hot_water_preparation
            else None,
            summer_heating_mode=summer_heating_mode,
        )

        if self.controller_mode == HeatingMode.SPACE_HEATING:
            # delta temperature should not be negative because district heating cannot provide cooling
            delta_temperature_in_celsius = max(
                heating_flow_temperature_from_heat_distribution_in_celsius
                - water_temperature_input_from_heat_distibution_in_celsius,
                0,
            )
        elif self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # delta temperature should not be negative because district heating cannot provide cooling
            delta_temperature_in_celsius = (
                max(
                    self.warm_water_temperature_aim_in_celsius
                    - water_temperature_input_from_warm_water_storage_in_celsius,
                    0,
                )
                + self.config.offset
            )
        elif self.controller_mode == HeatingMode.OFF:
            delta_temperature_in_celsius = 0
        else:
            raise ValueError(
                "District Heating Controller control_signal unknown."
            )

        stsv.set_output_value(
            self.delta_temperature_to_district_heating_channel,
            delta_temperature_in_celsius,
        )
        stsv.set_output_value(
            self.heating_mode_output_channel, self.controller_mode.value
        )

    def determine_operating_mode(
        self,
        water_temperature_input_sh_in_celsius: float,
        set_heating_flow_temperature_sh_in_celsius: float,
        water_temperature_input_dhw_in_celsius: Optional[float],
        set_heating_flow_temperature_dhw_in_celsius: Optional[float],
        summer_heating_mode: str,
    ) -> None:
        """Set conditions for the district heating controller mode."""

        def dhw_heating_needed(
            controller_mode,
            current_water_temperature,
            target_water_temperature,
        ):
            if not self.config.with_domestic_hot_water_preparation:
                return False

            assert water_temperature_input_dhw_in_celsius is not None
            assert set_heating_flow_temperature_dhw_in_celsius is not None

            if current_water_temperature < target_water_temperature:
                return True

            if (
                controller_mode == HeatingMode.DOMESTIC_HOT_WATER
                and current_water_temperature
                < target_water_temperature + self.config.offset
            ):
                return True

            return False

        def space_heating_needed(
            current_water_temperature, target_water_temperature
        ):
            if summer_heating_mode == "off":
                return False
            if current_water_temperature >= target_water_temperature:
                return False
            return True

        needs_space_heating = space_heating_needed(
            water_temperature_input_sh_in_celsius,
            set_heating_flow_temperature_sh_in_celsius,
        )
        needs_dhw_heating = dhw_heating_needed(
            self.controller_mode,
            water_temperature_input_dhw_in_celsius,
            set_heating_flow_temperature_dhw_in_celsius,
        )

        if needs_dhw_heating:
            # DHW has higher priority
            self.controller_mode = HeatingMode.DOMESTIC_HOT_WATER
        elif needs_space_heating:
            self.controller_mode = HeatingMode.SPACE_HEATING
        else:
            self.controller_mode = HeatingMode.OFF

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the district heating."""

        # if no heating threshold is set, the gas_heater is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "off"

        # it is cold enough for heating
        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
        return heating_mode

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = (
            OpexCostDataClass.get_default_opex_cost_data_class()
        )
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: DistrictHeatingControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = (
            CapexCostDataClass.get_default_capex_cost_data_class()
        )
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
