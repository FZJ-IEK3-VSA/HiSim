"""Electric Heating Module."""

# clean
# Owned
# import importlib
from dataclasses import dataclass
import logging
from typing import List, Any, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.components.dual_circuit_system import DiverterValve, HeatingMode, SetTemperatureConfig
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
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
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions

__authors__ = "Katharina Rieck, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


@dataclass_json
@dataclass
class ElectricHeatingConfig(ConfigBase):
    """Configuration of the Electric Heating class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return ElectricHeating.get_full_classname()

    building_name: str
    name: str
    # # Maximum electric power that can be delivered
    connected_load_w: float
    # Efficiency for electric to thermal power conversion
    efficiency: float
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
    with_domestic_hot_water_preparation: bool

    @classmethod
    def get_default_electric_heating_config(
        cls,
        building_name: str = "BUI1",
        with_domestic_hot_water_preparation=False,
    ) -> Any:
        """Get a default Electric heating."""
        config = ElectricHeatingConfig(
            building_name=building_name,
            name="ElectricHeating",
            connected_load_w=40000,
            efficiency=1.0,  # 100% efficiency
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )
        return config


class ElectricHeating(Component):
    """Electric Heating class.

    This component refers to direct electric heating like radiators, electric boilers, fan heaters etc.
    """

    # Inputs
    HeatingMode = "HeatingMode"

    # Inputs for space heating
    DeltaTemperatureNeeded = "DeltaTemperatureNeededSh"  # how much water temperature needs to be increased
    WaterInputTemperatureSh = "WaterInputTemperatureSh"
    WaterInputMassFlowRateFromHeatDistributionSystem = "WaterInputMassFlowRateFromHeatDistributionSystem"

    # Inputs for DHW
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"
    WaterInputMassFlowRateFromWarmWaterStorage = "WaterInputMassFlowRateFromWarmWaterStorage"

    # Output
    WaterOutputShTemperature = "WaterOutputShTemperature"
    ThermalOutputShPower = "ThermalOutputShPower"
    ThermalOutputShEnergy = "ThermalOutputShEnergy"
    WaterOutputShMassFlowRate = "WaterOutputShMassFlowRate"
    WaterOutputDhwTemperature = "WaterOutputDhwTemperature"
    ThermalOutputDhwPower = "ThermalOutputDhwPower"
    ThermalOutputDhwEnergy = "ThermalOutputDhwEnergy"
    WaterOutputDhwMassFlowRate = "WaterOutputDhwMassFlowRate"
    ElectricOutputShPower = "ElectricOutputShPower"
    ElectricOutputShEnergy = "ElectricOutputShEnergy"
    ElectricOutputDhwPower = "ElectricOutputDhwPower"
    ElectricOutputDhwEnergy = "ElectricOutputDhwEnergy"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.electric_heating_config = config
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
            ElectricHeating.HeatingMode,
            LoadTypes.ANY,
            Units.ANY,
            True,
        )
        self.delta_temperature_channel: ComponentInput = self.add_input(
            self.component_name,
            ElectricHeating.DeltaTemperatureNeeded,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.water_input_temperature_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            ElectricHeating.WaterInputTemperatureSh,
            LoadTypes.WATER,
            Units.CELSIUS,
            True,
        )
        self.water_input_mass_flow_rate_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            ElectricHeating.WaterInputMassFlowRateFromHeatDistributionSystem,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            True,
        )
        if self.config.with_domestic_hot_water_preparation:
            self.water_input_temperature_dhw_channel: ComponentInput = self.add_input(
                self.component_name,
                ElectricHeating.WaterInputTemperatureDhw,
                LoadTypes.WATER,
                Units.CELSIUS,
                True,
            )
            self.water_input_mass_flow_rate_dhw_channel: ComponentInput = self.add_input(
                self.component_name,
                ElectricHeating.WaterInputMassFlowRateFromWarmWaterStorage,
                LoadTypes.WATER,
                Units.KG_PER_SEC,
                True,
            )

        # Outputs Space Heating
        self.water_mass_flow_sh_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            ElectricHeating.WaterOutputShMassFlowRate,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description="Water mass flow rate for space heating.",
        )
        self.water_output_temperature_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            ElectricHeating.WaterOutputShTemperature,
            LoadTypes.WATER,
            Units.CELSIUS,
            output_description="Water output temperature for space heating.",
        )
        self.thermal_output_power_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputShPower,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal power output for space heating",
        )
        self.thermal_output_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputShEnergy,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description="Thermal energy output for space heating",
        )
        self.electric_output_power_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputShPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            output_description="Electric power output for space heating",
        )
        self.electric_output_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputShEnergy,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description="Electric energy output for space heating",
        )

        # Outputs DHW
        self.water_mass_flow_dhw_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            ElectricHeating.WaterOutputDhwMassFlowRate,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description="Water mass flow rate for domestic hot water.",
        )
        self.water_output_temperature_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            ElectricHeating.WaterOutputDhwTemperature,
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
        self.electric_output_power_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputDhwPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            output_description="Electric power output for domestic hot water",
        )
        self.electric_output_energy_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputDhwEnergy,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            output_description="Electric energy output for domestic hot water",
        )

        self.add_default_connections(self.get_default_connections_from_electric_heating_controller())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_system())
        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_electric_heating_controller(
        self,
    ):
        """Get Controller Electric Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = ElectricHeatingController
        connections = []
        controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                ElectricHeating.HeatingMode,
                controller_classname,
                component_class.OperatingMode,
            )
        )
        connections.append(
            ComponentConnection(
                ElectricHeating.DeltaTemperatureNeeded,
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
                ElectricHeating.WaterInputTemperatureSh,
                hws_classname,
                component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            ComponentConnection(
                ElectricHeating.WaterInputMassFlowRateFromHeatDistributionSystem,
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
                ElectricHeating.WaterInputTemperatureDhw,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        connections.append(
            ComponentConnection(
                ElectricHeating.WaterInputMassFlowRateFromWarmWaterStorage,
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
        return self.electric_heating_config.get_string_dict()

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
        """Simulate the electric heating."""
        if force_convergence:
            return

        # Retrieve inputs
        heating_mode = HeatingMode(stsv.get_input_value(self.heating_mode_channel))

        if heating_mode == HeatingMode.SPACE_HEATING:
            # Get relevant inputs
            delta_temperature_needed_in_celsius = stsv.get_input_value(self.delta_temperature_channel)
            self._check_delta_temperature(delta_temperature_needed_in_celsius, timestep)

            water_input_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_sh_channel)
            water_mass_flow_rate_in_kg_per_s = stsv.get_input_value(self.water_input_mass_flow_rate_sh_channel)

            # Calculate
            (
                thermal_power_delivered_w,
                thermal_energy_delivered_in_watt_hour,
                water_output_temperature_deg_c,
            ) = self._calculate_space_heating_outputs(
                water_mass_flow_rate_in_kg_per_s,
                delta_temperature_needed_in_celsius,
                water_input_temperature_deg_c,
            )
            # Calculate electricity consumption
            electric_power_consumption_w = thermal_power_delivered_w * self.config.efficiency
            electric_energy_consumption_in_watt_hour = thermal_energy_delivered_in_watt_hour * self.config.efficiency

            # Set outputs
            stsv.set_output_value(self.thermal_output_power_sh_channel, thermal_power_delivered_w)
            stsv.set_output_value(
                self.thermal_output_energy_sh_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(self.electric_output_power_sh_channel, electric_power_consumption_w)
            stsv.set_output_value(
                self.electric_output_energy_sh_channel,
                electric_energy_consumption_in_watt_hour,
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
            stsv.set_output_value(self.electric_output_power_dhw_channel, 0)
            stsv.set_output_value(self.electric_output_energy_dhw_channel, 0)
            current_dhw_water_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                current_dhw_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_dhw_output_channel, 0)

        elif heating_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # Get relevant inputs
            delta_temperature_needed_in_celsius = stsv.get_input_value(self.delta_temperature_channel)
            self._check_delta_temperature(delta_temperature_needed_in_celsius, timestep)

            water_input_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)

            # Calculate
            (
                thermal_power_delivered_w,
                thermal_energy_delivered_in_watt_hour,
                water_output_temperature_deg_c,
                water_mass_flow_rate_in_kg_per_s,
            ) = self._calculate_dhw_outputs(
                water_input_temperature_deg_c,
                delta_temperature_needed_in_celsius,
            )
            # Calculate electricity consumption
            electric_power_consumption_w = thermal_power_delivered_w * self.config.efficiency
            electric_energy_consumption_in_watt_hour = thermal_energy_delivered_in_watt_hour * self.config.efficiency

            # Set outputs
            stsv.set_output_value(
                self.thermal_output_power_dhw_channel,
                thermal_power_delivered_w,
            )
            stsv.set_output_value(
                self.thermal_output_energy_dhw_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(self.electric_output_power_dhw_channel, electric_power_consumption_w)
            stsv.set_output_value(
                self.electric_output_energy_dhw_channel,
                electric_energy_consumption_in_watt_hour,
            )
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                water_output_temperature_deg_c,
            )
            stsv.set_output_value(
                self.water_mass_flow_dhw_output_channel,
                water_mass_flow_rate_in_kg_per_s,
            )

            stsv.set_output_value(self.thermal_output_power_sh_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_sh_channel, 0)
            stsv.set_output_value(self.electric_output_power_sh_channel, 0)
            stsv.set_output_value(self.electric_output_energy_sh_channel, 0)
            current_sh_water_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_sh_channel)
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                current_sh_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_sh_output_channel, 0)

        elif heating_mode == HeatingMode.OFF:
            stsv.set_output_value(self.thermal_output_power_dhw_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_dhw_channel, 0)
            stsv.set_output_value(self.electric_output_power_dhw_channel, 0)
            stsv.set_output_value(self.electric_output_energy_dhw_channel, 0)
            current_dhw_water_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                current_dhw_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_dhw_output_channel, 0)

            stsv.set_output_value(self.thermal_output_power_sh_channel, 0)
            stsv.set_output_value(self.thermal_output_energy_sh_channel, 0)
            stsv.set_output_value(self.electric_output_power_sh_channel, 0)
            stsv.set_output_value(self.electric_output_energy_sh_channel, 0)

            current_sh_water_temperature_deg_c = stsv.get_input_value(self.water_input_temperature_sh_channel)
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                current_sh_water_temperature_deg_c,
            )
            stsv.set_output_value(self.water_mass_flow_sh_output_channel, 0)
        else:
            raise ValueError("Unknown heating mode")

    def _check_delta_temperature(self, delta_temperature: float, timestep: int):
        if delta_temperature < 0:
            raise ValueError(
                f"Delta temperature is {delta_temperature} °C"
                "but it should not be negative because electric heating cannot provide cooling. "
                "Please check your electric heating controller."
            )
        if delta_temperature > 100:
            raise ValueError(
                f"Delta temperature is {delta_temperature} °C in timestep {timestep}." "This is way too high. "
            )

    def _calculate_space_heating_outputs(
        self,
        water_mass_flow_rate_in_kg_per_s: float,
        delta_temperature_needed_in_celsius: float,
        water_input_temperature_deg_c: float,
    ) -> Tuple[float, float, float]:
        thermal_power_delivered_w = (
            water_mass_flow_rate_in_kg_per_s
            * PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * delta_temperature_needed_in_celsius
        )

        if thermal_power_delivered_w > self.config.connected_load_w:
            # make sure that not more power is delivered than available
            logging.warning("The needed thermal power for space heating is higher than the maximum connected load.")
            thermal_power_delivered_w = self.config.connected_load_w
            delta_temperature_achieved = thermal_power_delivered_w / (
                water_mass_flow_rate_in_kg_per_s
                * PhysicsConfig.get_properties_for_energy_carrier(
                    energy_carrier=LoadTypes.WATER
                ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            )
            water_output_temperature_deg_c = water_input_temperature_deg_c + delta_temperature_achieved
        else:
            water_output_temperature_deg_c = water_input_temperature_deg_c + delta_temperature_needed_in_celsius

        thermal_energy_delivered_in_watt_hour = (
            thermal_power_delivered_w * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )
        return thermal_power_delivered_w, thermal_energy_delivered_in_watt_hour, water_output_temperature_deg_c

    def _calculate_dhw_outputs(self, water_input_temperature_deg_c: float, delta_temperature_needed_in_celsius: float):
        water_target_temperature_deg_c = water_input_temperature_deg_c + delta_temperature_needed_in_celsius

        # calculate thermal power delivered Q = m * cw * dT
        if delta_temperature_needed_in_celsius > 0:
            thermal_power_delivered_w = self.config.connected_load_w
            water_mass_flow_rate_in_kg_per_s = thermal_power_delivered_w / (
                PhysicsConfig.get_properties_for_energy_carrier(
                    energy_carrier=LoadTypes.WATER
                ).specific_heat_capacity_in_joule_per_kg_per_kelvin
                * delta_temperature_needed_in_celsius
            )
        else:
            thermal_power_delivered_w = 0
            water_mass_flow_rate_in_kg_per_s = 0

        water_target_temperature_deg_c = water_input_temperature_deg_c + delta_temperature_needed_in_celsius
        thermal_energy_delivered_in_watt_hour = (
            thermal_power_delivered_w * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )
        return (
            thermal_power_delivered_w,
            thermal_energy_delivered_in_watt_hour,
            water_target_temperature_deg_c,
            water_mass_flow_rate_in_kg_per_s,
        )

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        total_consumption_in_kwh = None
        sh_consumption_in_kwh = None
        dhw_consumption_in_kwh = None
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricOutputShPower
                and output.unit == Units.WATT
            ):
                sh_consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricOutputDhwPower
                and output.unit == Units.WATT
            ):
                dhw_consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )

        assert sh_consumption_in_kwh is not None
        assert dhw_consumption_in_kwh is not None

        total_consumption_in_kwh = sh_consumption_in_kwh + dhw_consumption_in_kwh

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        co2_per_simulated_period_in_kg = total_consumption_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = total_consumption_in_kwh * euro_per_unit

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=total_consumption_in_kwh,
            consumption_for_space_heating_in_kwh=sh_consumption_in_kwh,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ELECTRIC_HEATING,
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: ElectricHeatingConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = ComponentType.ELECTRIC_HEATER
        kpi_tag = (
            KpiTagEnumClass.ELECTRIC_HEATING
        )
        unit = Units.KILOWATT
        size_of_energy_system = config.connected_load_w * 1e-3

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

        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(self.config, self.my_simulation_parameters)

        # Energy related KPIs
        energy_consumption = KpiEntry(
            name="Total energy consumption",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)
        sh_energy_consumption = KpiEntry(
            name="Energy consumption for space heating",
            unit="kWh",
            value=opex_dataclass.consumption_for_space_heating_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(sh_energy_consumption)
        dhw_energy_consumption = KpiEntry(
            name="Energy consumption for domestic hot water",
            unit="kWh",
            value=opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_energy_consumption)

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
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
        return list_of_kpi_entries


@dataclass_json
@dataclass
class ElectricHeatingControllerConfig(ConfigBase):
    """Electric Heating Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ElectricHeatingController.get_full_classname()

    building_name: str
    name: str
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    with_domestic_hot_water_preparation: bool
    offset: float  # overheating of dhw storage

    @classmethod
    def get_default_electric_heating_controller_config(
        cls,
        building_name: str = "BUI1",
        with_domestic_hot_water_preparation=False,
    ) -> Any:
        """Gets a default electric heating controller."""
        return ElectricHeatingControllerConfig(
            building_name=building_name,
            name="ElectricHeatingController",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
            offset=15,
        )


class ElectricHeatingController(Component):
    """Electric Heating Controller."""

    # Inputs
    WaterTemperatureInputFromHeatDistributionSystem = "WaterTemperatureInputFromHeatDistributionSystem"
    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Relevant when used for dhw as well
    WaterTemperatureInputFromWarmWaterStorage = "WaterTemperatureInputFromWarmWaterStorage"

    # Outputs
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"
    OperatingMode = "HeatingMode"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricHeatingControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.electric_heating_controller_config = config
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
        self.water_temperature_input_channel_sh: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        if self.config.with_domestic_hot_water_preparation:
            self.water_temperature_input_channel_dhw: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureInputFromWarmWaterStorage,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DailyAverageOutsideTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.delta_temperature_to_electric_heating_channel: ComponentOutput = self.add_output(
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
            self.OperatingMode,
            LoadTypes.ANY,
            Units.ANY,
            output_description="Operating mode of electric heating.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_heat_distribution())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())

        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                ElectricHeatingController.WaterTemperatureInputFromWarmWaterStorage,
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
                ElectricHeatingController.WaterTemperatureInputFromHeatDistributionSystem,
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
                ElectricHeatingController.DailyAverageOutsideTemperature,
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
                ElectricHeatingController.HeatingFlowTemperatureFromHeatDistributionSystem,
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
        return self.electric_heating_controller_config.get_string_dict()

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the electric heating comtroller."""

        if force_convergence:
            return

        # Retrieves inputs
        water_temperature_input_from_heat_distibution_in_celsius = stsv.get_input_value(
            self.water_temperature_input_channel_sh
        )

        heating_flow_temperature_from_heat_distribution_in_celsius = stsv.get_input_value(
            self.heating_flow_temperature_from_heat_distribution_system_channel
        )
        water_temperature_input_from_warm_water_storage_in_celsius = None
        if self.config.with_domestic_hot_water_preparation:
            water_temperature_input_from_warm_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel_dhw
            )

        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_avg_outside_temperature_input_channel
        )

        # Determine which operating mode to use in dual-circuit system
        delta_temperature_in_celsius = self.determine_operating_mode(
            daily_avg_outside_temperature_in_celsius,
            water_temperature_input_from_heat_distibution_in_celsius,
            heating_flow_temperature_from_heat_distribution_in_celsius,
            water_temperature_input_from_warm_water_storage_in_celsius,
        )

        stsv.set_output_value(
            self.delta_temperature_to_electric_heating_channel,
            delta_temperature_in_celsius,
        )
        stsv.set_output_value(self.heating_mode_output_channel, self.controller_mode.value)

    def determine_operating_mode(
        self,
        daily_avg_outside_temperature_in_celsius: float,
        sh_current_temperature_deg_c: float,
        sh_set_temperature_deg_c: float,
        dhw_current_temperature_deg_c: Optional[float],
    ) -> float:
        """Determine operating mode."""

        self.controller_mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=self.config.with_domestic_hot_water_preparation,
            current_controller_mode=self.controller_mode,
            daily_average_outside_temperature=daily_avg_outside_temperature_in_celsius,
            water_temperature_input_sh_in_celsius=sh_current_temperature_deg_c,
            water_temperature_input_dhw_in_celsius=(
                dhw_current_temperature_deg_c if self.config.with_domestic_hot_water_preparation else None
            ),
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating=sh_set_temperature_deg_c,
                set_temperature_dhw=self.warm_water_temperature_aim_in_celsius,
                hysteresis_dhw_offset=self.config.offset,
                outside_temperature_threshold=self.electric_heating_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            ),
        )

        if self.controller_mode == HeatingMode.SPACE_HEATING:
            # delta temperature should not be negative because electric heating cannot provide cooling
            delta_temperature_in_celsius = float(
                max(
                    sh_set_temperature_deg_c - sh_current_temperature_deg_c,
                    0.0,
                )
            )
        elif self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # delta temperature should not be negative because electric heating cannot provide cooling
            assert dhw_current_temperature_deg_c is not None
            delta_temperature_in_celsius = float(
                max(
                    self.warm_water_temperature_aim_in_celsius - dhw_current_temperature_deg_c,
                    0.0,
                )
                + self.config.offset
            )
        elif self.controller_mode == HeatingMode.OFF:
            delta_temperature_in_celsius = 0.0
        else:
            raise ValueError("Electric Heating Controller control_signal unknown.")
        return delta_temperature_in_celsius

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: ElectricHeatingControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
