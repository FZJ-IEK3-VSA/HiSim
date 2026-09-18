"""Electric Heating Module."""

# Owned
from dataclasses import dataclass
import logging
from typing import ClassVar, List, Optional, Tuple

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
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    SizingLaw,
    concrete,
    law,
    preset,
    sized_field,
)
from hisim.components.building import Building
from hisim.components.weather import Weather
from hisim.components.heat_distribution_system import HeatDistributionControllerConfig
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
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class ElectricHeatingConfig(ConfigBase):
    """Configuration of the Electric Heating class.

    Direct electric heating -- a radiator, an electric boiler, a fan heater -- serving space
    heating and, optionally, domestic hot water. The named default is :meth:`preset_resistive`,
    and the one field that depends on the building is sizable, so the preset leaves it ``AUTO``
    and ``.resolve(ctx)`` copies the building's heating load into it. An author who knows the
    appliance pins the field instead::

        ElectricHeatingConfig.preset_resistive("ElectricHeating").resolve(
            SizingContext(heating_load_in_watt=7780.75)
        )

    ``maximum_electric_power_w`` is both the electric and the thermal cap: the component sets its
    thermal output equal to its electric input, so the heat delivered is limited by this field
    alone.
    """

    MAIN_CLASS = "hisim.components.generic_electric_heating.ElectricHeating"

    component_id: ComponentID
    #: Whether the appliance also heats domestic hot water, in which case it declares the DHW
    #: inputs and outputs and prioritises that demand over space heating.
    with_domestic_hot_water_preparation: bool = False
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the appliance up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: Largest electric power the appliance draws, and therefore also the largest thermal power
    #: it delivers. Sizable: left ``AUTO`` it is the building's heating load exactly, the
    #: appliance covering the design load with no reserve.
    maximum_electric_power_w: Sizable[float] = sized_field(rule=Size.HEATING_LOAD_IN_WATT)

    @staticmethod
    def sizing_facts(config: "ElectricHeatingConfig", ctx: SizingContext) -> dict:
        """Contributes the appliance's resolved thermal power for the components around it.

        Runs after the appliance itself resolved, so the value is the final concrete number
        whether it came from the law, from the preset or from an override. The contributed
        quantity is ``maximum_electric_power_w`` unconverted: resistive heating delivers as much
        heat as it draws current, and the component caps both space heating and domestic hot
        water at that one number.

        Args:
            config: this electric heating configuration, fully resolved.
            ctx: the sizing context; unused, the value is this config's own.

        Returns:
            dict: the one fact named in :attr:`SIZING_CONTRIBUTIONS`.
        """
        del ctx
        return {"maximal_thermal_power_in_watt": concrete(config.maximum_electric_power_w)}

    #: Sizing facts this config contributes: its resolved thermal power, under the name the
    #: heating-generator family shares, so a consumer sizes from electric heating exactly as it
    #: sizes from a boiler. With two generators in one scenario each is addressable as
    #: "<its name>.maximal_thermal_power_in_watt" and a consumer must say which one it means.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(facts=("maximal_thermal_power_in_watt",), compute=sizing_facts),
    )

    @preset
    @classmethod
    def preset_resistive(cls, name: str) -> "ElectricHeatingConfig":
        """Resistive electric heating, scaled to the building it heats.

        The field defaults are that appliance: every watt drawn becomes a watt of heat, space
        heating only, and the investment costs left to the cost database. What the preset does
        not fix is how large the appliance is: ``maximum_electric_power_w`` stays ``AUTO`` so
        that it is copied from the building's heating load.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            ElectricHeatingConfig: The preset configuration, power unsized.
        """
        return cls(component_id=ComponentID(name=name))


class ElectricHeating(Component):
    """Electric Heating class.

    This component refers to direct electric heating like radiators, electric boilers, fan heaters etc.
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    HeatingMode = "HeatingMode"

    # Inputs for space heating
    TheoreticalHeatingDemand = "TheoreticalHeatingDemand"
    TheoreticalHeatingEnergyDemand = "TheoreticalHeatingEnergyDemand"

    # Inputs for DHW
    DeltaTemperatureNeededForDHW = "DeltaTemperatureNeededForDHW"
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"
    WaterInputMassFlowRateFromWarmWaterStorage = "WaterInputMassFlowRateFromWarmWaterStorage"

    # Output
    ThermalOutputShPower = "ThermalOutputShPower"
    ThermalOutputShEnergy = "ThermalOutputShEnergy"

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
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Construct all the neccessary attributes."""
        if my_display_config is None:
            my_display_config = DisplayConfig(display_in_webtool=True)
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
        self.delta_temperature_for_dhw_channel: ComponentInput = self.add_input(
            self.component_name,
            ElectricHeating.DeltaTemperatureNeededForDHW,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.theoretical_thermal_building_power_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalHeatingDemand,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )
        self.theoretical_thermal_building_energy_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalHeatingEnergyDemand,
            LoadTypes.HEATING,
            Units.WATT_HOUR,
            True,
        )

        if self.config.with_domestic_hot_water_preparation:
            self.water_input_temperature_dhw_channel: ComponentInput = self.add_input(
                self.component_name,
                ElectricHeating.WaterInputTemperatureDhw,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
            self.water_input_mass_flow_rate_dhw_channel: ComponentInput = self.add_input(
                self.component_name,
                ElectricHeating.WaterInputMassFlowRateFromWarmWaterStorage,
                LoadTypes.WARM_WATER,
                Units.KG_PER_SEC,
                True,
            )

        # Outputs Space Heating
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
            LoadTypes.WARM_WATER,
            Units.KG_PER_SEC,
            output_description="Water mass flow rate for domestic hot water.",
        )
        self.water_output_temperature_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            ElectricHeating.WaterOutputDhwTemperature,
            LoadTypes.TEMPERATURE,
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
        self.add_default_connections(self.get_default_connections_from_building())
        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_electric_heating_controller(
        self,
    ):
        """Get Controller Electric Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = ElectricHeatingController
        controller_classname = component_class.get_classname()
        return [
            ComponentConnection(
                ElectricHeating.HeatingMode,
                controller_classname,
                component_class.OperatingMode,
            ),
            ComponentConnection(
                ElectricHeating.DeltaTemperatureNeededForDHW,
                controller_classname,
                component_class.DeltaTemperatureNeededForDHW,
            ),
        ]

    def get_default_connections_from_building(
        self,
    ):
        """Get building default connections."""

        component_class = Building
        building_classname = component_class.get_classname()
        return [
            ComponentConnection(
                ElectricHeating.TheoreticalHeatingDemand,
                building_classname,
                component_class.TheoreticalHeatingDemand,
            ),
            ComponentConnection(
                ElectricHeating.TheoreticalHeatingEnergyDemand,
                building_classname,
                component_class.TheoreticalHeatingEnergyDemand,
            ),
        ]

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple dhw storage default connections."""

        component_class = SimpleDHWStorage
        hws_classname = component_class.get_classname()
        return [
            ComponentConnection(
                ElectricHeating.WaterInputTemperatureDhw,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            ),
            ComponentConnection(
                ElectricHeating.WaterInputMassFlowRateFromWarmWaterStorage,
                hws_classname,
                component_class.WaterMassFlowRateOfDHW,
            ),
        ]

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
            theoretical_thermal_building_in_watt = stsv.get_input_value(self.theoretical_thermal_building_power_channel)
            theoretical_thermal_building_energy_in_watthour = stsv.get_input_value(
                self.theoretical_thermal_building_energy_channel
            )

            if theoretical_thermal_building_in_watt >= 0:
                thermal_power_sh_delivered_in_watt = theoretical_thermal_building_in_watt
                thermal_energy_sh_delivered_in_watthour = theoretical_thermal_building_energy_in_watthour
            else:
                thermal_power_sh_delivered_in_watt = 0.0
                thermal_energy_sh_delivered_in_watthour = 0.0
            # dhw outputs
            thermal_power_dhw_delivered_w = 0.0
            thermal_energy_dhw_delivered_in_watt_hour = 0.0
            water_mass_flow_rate_for_dhw_in_kg_per_s = 0.0
            water_output_temperature_for_dhw_deg_c = 0.0

        elif heating_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # Get relevant inputs
            delta_temperature_needed_for_dhw_in_celsius = stsv.get_input_value(self.delta_temperature_for_dhw_channel)
            self._check_delta_temperature(delta_temperature_needed_for_dhw_in_celsius, timestep)

            water_input_temperature_for_dhw_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)

            # Calculate
            (
                thermal_power_dhw_delivered_w,
                thermal_energy_dhw_delivered_in_watt_hour,
                water_output_temperature_for_dhw_deg_c,
                water_mass_flow_rate_for_dhw_in_kg_per_s,
            ) = self._calculate_dhw_outputs(
                water_input_temperature_for_dhw_deg_c,
                delta_temperature_needed_for_dhw_in_celsius,
            )
            # set sh outputs
            thermal_power_sh_delivered_in_watt = 0.0
            thermal_energy_sh_delivered_in_watthour = 0.0

        elif heating_mode == HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL:
            # Get relevant inputs
            delta_temperature_needed_for_dhw_in_celsius = stsv.get_input_value(self.delta_temperature_for_dhw_channel)
            self._check_delta_temperature(delta_temperature_needed_for_dhw_in_celsius, timestep)

            water_input_temperature_for_dhw_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)

            # Calculate first for dhw
            (
                thermal_power_dhw_delivered_w,
                thermal_energy_dhw_delivered_in_watt_hour,
                water_output_temperature_for_dhw_deg_c,
                water_mass_flow_rate_for_dhw_in_kg_per_s,
            ) = self._calculate_dhw_outputs(
                water_input_temperature_for_dhw_deg_c,
                delta_temperature_needed_for_dhw_in_celsius,
            )

            # Now calculate for space heating
            # Calculate
            maximum_electric_power_in_watt = concrete(self.config.maximum_electric_power_w)
            if maximum_electric_power_in_watt - thermal_power_dhw_delivered_w <= 0:
                raise ValueError(
                    f"Electric load for DHW {thermal_power_dhw_delivered_w}W is equal or higher "
                    f"than maximal electric load {maximum_electric_power_in_watt}. "
                )
            theoretical_thermal_building_in_watt = stsv.get_input_value(self.theoretical_thermal_building_power_channel)
            theoretical_thermal_building_energy_in_watthour = stsv.get_input_value(
                self.theoretical_thermal_building_energy_channel
            )
            available_electric_load_in_watt = maximum_electric_power_in_watt - thermal_power_dhw_delivered_w

            if theoretical_thermal_building_in_watt >= 0:
                if theoretical_thermal_building_in_watt > available_electric_load_in_watt:
                    logging.debug(
                        "The needed thermal power for space heating is higher than the maximum connected load."
                    )
                thermal_power_sh_delivered_in_watt = min(
                    theoretical_thermal_building_in_watt, available_electric_load_in_watt
                )
                thermal_energy_sh_delivered_in_watthour = min(
                    theoretical_thermal_building_energy_in_watthour,
                    available_electric_load_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3,
                )
            else:
                thermal_power_sh_delivered_in_watt = 0.0
                thermal_energy_sh_delivered_in_watthour = 0.0

        elif heating_mode == HeatingMode.OFF:
            thermal_power_dhw_delivered_w = 0.0
            thermal_energy_dhw_delivered_in_watt_hour = 0.0
            water_mass_flow_rate_for_dhw_in_kg_per_s = 0.0
            water_output_temperature_for_dhw_deg_c = stsv.get_input_value(self.water_input_temperature_dhw_channel)
            thermal_power_sh_delivered_in_watt = 0.0
            thermal_energy_sh_delivered_in_watthour = 0.0

        else:
            raise ValueError("Unknown heating mode")

        # set outputs
        stsv.set_output_value(self.thermal_output_power_dhw_channel, thermal_power_dhw_delivered_w)
        stsv.set_output_value(self.thermal_output_energy_dhw_channel, thermal_energy_dhw_delivered_in_watt_hour)
        stsv.set_output_value(
            self.water_output_temperature_dhw_channel,
            water_output_temperature_for_dhw_deg_c,
        )
        stsv.set_output_value(self.water_mass_flow_dhw_output_channel, water_mass_flow_rate_for_dhw_in_kg_per_s)
        stsv.set_output_value(self.thermal_output_power_sh_channel, thermal_power_sh_delivered_in_watt)
        stsv.set_output_value(self.thermal_output_energy_sh_channel, thermal_energy_sh_delivered_in_watthour)
        # set electric power and energy
        # stromdirektheizung -> electricpower =thermalpower
        stsv.set_output_value(self.electric_output_power_sh_channel, thermal_power_sh_delivered_in_watt)
        stsv.set_output_value(self.electric_output_energy_sh_channel, thermal_energy_sh_delivered_in_watthour)
        stsv.set_output_value(self.electric_output_power_dhw_channel, thermal_power_dhw_delivered_w)
        stsv.set_output_value(self.electric_output_energy_dhw_channel, thermal_energy_dhw_delivered_in_watt_hour)

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

    def _calculate_dhw_outputs(self, water_input_temperature_deg_c: float, delta_temperature_needed_in_celsius: float):
        water_target_temperature_deg_c = water_input_temperature_deg_c + delta_temperature_needed_in_celsius

        # calculate thermal power delivered Q = m * cw * dT
        if delta_temperature_needed_in_celsius > 0:
            # regulate thermal output power based on deltaT needed
            thermal_power_delivered_w = min(
                concrete(self.config.maximum_electric_power_w) * delta_temperature_needed_in_celsius / 100.0,
                concrete(self.config.maximum_electric_power_w),
            )
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

        if sh_consumption_in_kwh is None:
            raise ValueError(
                f"Could not find {self.ElectricOutputShPower} output for component {self.component_name}"
            )
        if dhw_consumption_in_kwh is None:
            raise ValueError(
                f"Could not find {self.ElectricOutputDhwPower} output for component {self.component_name}"
            )

        total_consumption_in_kwh = sh_consumption_in_kwh + dhw_consumption_in_kwh

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
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
        kpi_tag = KpiTagEnumClass.ELECTRIC_HEATING
        unit = Units.KILOWATT
        size_of_energy_system = concrete(config.maximum_electric_power_w) * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
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
        assert isinstance(self.config, ElectricHeatingConfig)
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
    """Configuration of the direct electric heating appliance's controller.

    The switch in front of the appliance: each time step it decides whether the heat goes to
    space heating, to domestic hot water, to both at once or nowhere, and it stops space
    heating once the daily average outside temperature has risen above the heating threshold.
    The named default is :meth:`preset_standard`; the two fields that describe the building
    rather than the appliance are sizable, so the preset leaves them ``AUTO`` and
    ``.resolve(ctx)`` derives both from the building's facts::

        ElectricHeatingControllerConfig.preset_standard("ElectricHeatingController").resolve(
            SizingContext(heating_load_in_watt=7780.75, conditioned_floor_area_in_m2=121.2)
        )

    Direct electric heating has no water circuit and therefore no heat distribution controller
    whose threshold could be copied, so the threshold is derived here from the building's
    efficiency -- by reusing that controller's law rather than restating its step table, which
    is what keeps an electrically heated house switching off on the same day a water-heated
    one of the same efficiency does.
    """

    MAIN_CLASS = "hisim.components.generic_electric_heating.ElectricHeatingController"

    #: Sizing law of the building's specific heating load: its design heating load divided by
    #: its conditioned floor area. Law terms carry no division, so this is a function law over
    #: the two building facts; the operands and their order are the ones the setups divided by
    #: hand, because the recorded value is the unrounded quotient.
    SPECIFIC_HEATING_LOAD_LAW: ClassVar[SizingLaw] = law(
        lambda ctx: ctx.heating_load_in_watt / ctx.conditioned_floor_area_in_m2,
        reads=(Size.HEATING_LOAD_IN_WATT, Size.CONDITIONED_FLOOR_AREA_IN_M2),
    )

    component_id: ComponentID
    #: Daily average outside temperature above which space heating stops. Sizable: left ``AUTO``
    #: it is the heat distribution controller's step table over the building's specific heating
    #: load -- 16 °C up to 50 W/m², 18 °C up to 80 W/m², 20 °C above that -- because a badly
    #: insulated house cools out in the mornings and evenings of the shoulder season and has to
    #: be heated earlier in the year.
    set_heating_threshold_outside_temperature_in_celsius: Sizable[float] = sized_field(
        rule=HeatDistributionControllerConfig.HEATING_THRESHOLD_LAW,
        note=(
            "the heat distribution controller's step table: 16 °C up to 50 W/m² of specific"
            " heating load, 18 °C up to 80 W/m², 20 °C above that"
        ),
    )
    #: Whether the appliance this controls also prepares domestic hot water, in which case the
    #: controller reads the vessel's temperature and can prioritise it over space heating.
    with_domestic_hot_water_preparation: bool = False
    #: Width of the hysteresis band on the hot water temperature, in kelvin: how far below the
    #: 60 °C aim the vessel may cool before it is heated again, and the margin the controller
    #: adds to the delta temperature it then asks the appliance for.
    hysteresis_water_temperature_offset: float = 15.0
    #: Whether space heating and hot water may be served in the same time step. False makes the
    #: two exclusive, with hot water taking precedence.
    parallel_space_heating_and_dhw_option: bool = False
    #: The building's design heating load per square metre of conditioned floor area. No code
    #: path reads it -- neither controller nor appliance touches the field -- it is recorded
    #: provenance for the threshold above, which the same ratio decides. Sizable so that the
    #: number recorded is the one the threshold was derived from, and not a second hand-typed one.
    specific_heating_load_of_building_in_watt_per_m2: Sizable[float] = sized_field(
        rule=SPECIFIC_HEATING_LOAD_LAW,
        note="the building's heating load per m² of conditioned floor area",
    )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "ElectricHeatingControllerConfig":
        """The one electric heating controller the fleet runs, derived from the building it heats.

        The field defaults are that controller: space heating only, exclusive of hot water
        where the appliance prepares it as well, and a fifteen-kelvin hysteresis band on the
        vessel temperature. What the preset does not fix is the heating threshold and the
        specific heating load behind it, which stay ``AUTO`` so that both come from the
        building instead of being written down twice.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, with its two sizable fields still ``AUTO``.
        """
        return cls(component_id=ComponentID(name=name))


class ElectricHeatingController(Component):
    """Electric Heating Controller."""

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Relevant when used for dhw as well
    WaterTemperatureInputFromWarmWaterStorage = "WaterTemperatureInputFromWarmWaterStorage"

    # Outputs
    DeltaTemperatureNeededForDHW = "DeltaTemperatureNeededForDHW"
    DeltaTemperatureNeededForSH = "DeltaTemperatureNeededForSH"
    OperatingMode = "HeatingMode"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricHeatingControllerConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Construct all the neccessary attributes."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
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

        self.delta_temperature_for_dhw_to_electric_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.DeltaTemperatureNeededForDHW,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=f"here a description for {self.DeltaTemperatureNeededForDHW} will follow.",
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

        if self.config.with_domestic_hot_water_preparation:
            self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        storage_classname = SimpleDHWStorage.get_classname()
        return [
            ComponentConnection(
                ElectricHeatingController.WaterTemperatureInputFromWarmWaterStorage,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
            ),
        ]

    def get_default_connections_from_weather(
        self,
    ):
        """Get weather default connections."""

        weather_classname = Weather.get_classname()
        return [
            ComponentConnection(
                ElectricHeatingController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            ),
        ]

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

        water_temperature_input_from_warm_water_storage_in_celsius = None
        if self.config.with_domestic_hot_water_preparation:
            water_temperature_input_from_warm_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel_dhw
            )

        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_avg_outside_temperature_input_channel
        )

        # Determine which operating mode to use in dual-circuit system
        delta_temperature_for_dhw_in_celsius = self.determine_operating_mode(
            daily_avg_outside_temperature_in_celsius,
            water_temperature_input_from_warm_water_storage_in_celsius,
        )
        stsv.set_output_value(
            self.delta_temperature_for_dhw_to_electric_heating_channel,
            delta_temperature_for_dhw_in_celsius,
        )

        stsv.set_output_value(self.heating_mode_output_channel, self.controller_mode.value)

    def determine_operating_mode(
        self, daily_avg_outside_temperature_in_celsius: float, dhw_current_temperature_deg_c: Optional[float]
    ) -> float:
        """Determine operating mode."""

        self.controller_mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=self.config.with_domestic_hot_water_preparation,
            current_controller_mode=self.controller_mode,
            daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
            water_temperature_input_sh_in_celsius=0,  # artificial because no sh water used
            water_temperature_input_dhw_in_celsius=(
                dhw_current_temperature_deg_c if self.config.with_domestic_hot_water_preparation else None
            ),
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating_in_celsius=60,  # artificial because no sh water used
                set_temperature_dhw_in_celsius=self.warm_water_temperature_aim_in_celsius,
                hysteresis_water_temperature_offset_in_celsius=self.config.hysteresis_water_temperature_offset,
                outside_temperature_threshold_in_celsius=concrete(
                    self.config.set_heating_threshold_outside_temperature_in_celsius
                ),
            ),
            parallel_space_heating_and_dhw_option=self.config.parallel_space_heating_and_dhw_option,
        )

        if self.controller_mode == HeatingMode.SPACE_HEATING:
            delta_temperature_for_dhw_in_celsius = 0.0

        elif self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER:
            # delta temperature should not be negative because district heating cannot provide cooling
            assert dhw_current_temperature_deg_c is not None
            delta_temperature_for_dhw_in_celsius = float(
                max(
                    self.warm_water_temperature_aim_in_celsius - dhw_current_temperature_deg_c,
                    0.0,
                )
                + self.config.hysteresis_water_temperature_offset
            )

        elif self.controller_mode == HeatingMode.OFF:
            delta_temperature_for_dhw_in_celsius = 0.0

        elif self.controller_mode == HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL:
            assert dhw_current_temperature_deg_c is not None
            delta_temperature_for_dhw_in_celsius = float(
                max(
                    self.warm_water_temperature_aim_in_celsius - dhw_current_temperature_deg_c,
                    0.0,
                )
                + self.config.hysteresis_water_temperature_offset
            )

        else:
            raise ValueError("Electric Heating Controller control_signal unknown.")
        return delta_temperature_for_dhw_in_celsius

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        return OpexCostDataClass.get_default_opex_cost_data_class()

    @staticmethod
    def get_cost_capex(
        config: ElectricHeatingControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
