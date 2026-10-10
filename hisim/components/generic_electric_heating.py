"""Electric Heating Module."""

# Owned
from dataclasses import dataclass, field
import logging
from typing import ClassVar, List, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import hydronics
from hisim.energy_port import EnergyPort
from hisim.components import dual_circuit_system
from hisim.part_load import PartLoadCommand, PartLoadControl
from hisim.components.dual_circuit_system import DiverterValve, DualCircuitHotWater, HeatingMode, SetTemperatureConfig
from hisim.loadtypes import EnergyBalanceCarrier, EnergyRole, LoadTypes, Units, InandOutputType, ComponentType
from hisim.component import (
    ComponentKind,
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
    maximum_electric_power_w: Sizable[float] = sized_field(rule=Size.HEATING_LOAD_IN_WATT, unit=Units.WATT)
    #: The highest supply temperature the hot-water side delivers, °C. A charge whose return plus lift would exceed
    #: it is throttled to it. 80 °C is the usual upper setting of the thermostat of a
    #: domestic electric water heater, whose safety cut-out acts above it (EN 60335-2-21); it lies above the
    #: controller's 75 °C hot-water supply aim (the 60 °C aim plus its 15 K hysteresis).
    maximal_dhw_supply_temperature_in_celsius: float = field(default=80.0, metadata={"unit": Units.CELSIUS})

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


@dataclass(frozen=True)
class SpaceHeatingDelivery:

    """The heat an electric heater delivers to the rooms in one step.

    A value object, so the power and the energy cannot be swapped by position. The energy is the building's stated
    demand energy, or the available power over the step when that is lower.
    """

    #: The heat flow into the rooms, in W.
    power_in_watt: float
    #: The heat of the step, in Wh.
    energy_in_watt_hour: float


class ElectricHeating(Component):
    """Electric Heating class.

    This component refers to direct electric heating like radiators, electric boilers, fan heaters etc.

    A hot-water charge runs the part-load ratio its controller commands (``PartLoadRatioDhw``): 1, the whole step, at
    and below the part-load threshold, and above it the fraction of the step that ends the tank at the controller's
    target. Below 1 the heater publishes the averaged flow at the full-load supply temperature and books the heat that
    flow carries, which is its electricity. It reports the ratio it ran with (``PartLoadRatioRunDhw``): the commanded
    ratio while it charges the tank, 0 otherwise.
    """

    KIND = ComponentKind.PHYSICS

    cost_relevance = CostRelevance.PRICED

    # Inputs
    HeatingMode = "HeatingMode"

    # Inputs for space heating
    TheoreticalHeatingDemand = "TheoreticalHeatingDemand"
    TheoreticalHeatingEnergyDemand = "TheoreticalHeatingEnergyDemand"

    # Inputs for DHW
    DeltaTemperatureNeededForDHW = "DeltaTemperatureNeededForDHW"
    #: The hot-water supply temperature the controller aims at: the heater's hot-water supply never exceeds it.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"
    WaterInputMassFlowRateFromWarmWaterStorage = "WaterInputMassFlowRateFromWarmWaterStorage"
    #: The fraction of the step a hot-water charge runs, commanded by the controller.
    PartLoadRatioDhw = "PartLoadRatioDhw"

    #: The lift at which the hot-water side runs at its maximal power, in K: it regulates its power as
    #: ``P = P_max lift / 100 K``, so a 25 K lift draws a quarter of the maximal power.
    REGULATION_LIFT_AT_FULL_POWER_IN_KELVIN: ClassVar[float] = 100.0

    # Output
    #: The fraction of the step the hot-water charge ran, reported back to the controller.
    PartLoadRatioRunDhw = "PartLoadRatioRunDhw"
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

        # The tank's step mean, the hot-water circuit's return; a heater without a tank has no such input.
        self.water_input_temperature_dhw_channel: Optional[ComponentInput] = None
        if self.config.with_domestic_hot_water_preparation:
            self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentInput = self.add_input(
                self.component_name,
                ElectricHeating.SupplyTemperatureSetForDHWInCelsius,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
            self.water_input_temperature_dhw_channel = self.add_input(
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
            self.dhw_part_load_command = PartLoadCommand(
                self,
                input_name=ElectricHeating.PartLoadRatioDhw,
                ratio_run_output_name=ElectricHeating.PartLoadRatioRunDhw,
                device_description="the heater's hot-water charge",
            )

        # Outputs Space Heating
        self.thermal_output_power_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputShPower,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal power output for space heating",
            energy_port=EnergyPort(EnergyRole.OUT, EnergyBalanceCarrier.SPACE_HEATING_HEAT),
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
            energy_port=EnergyPort(
                EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricOutputShPower
            ),
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
            energy_port=EnergyPort(
                EnergyRole.OUT,
                EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
                peer_output=self.WaterOutputDhwMassFlowRate,
            ),
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
            energy_port=EnergyPort(
                EnergyRole.IN, EnergyBalanceCarrier.ELECTRICITY, peer_output=self.ElectricOutputDhwPower
            ),
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
        """Return the heater's default connections from its controller: mode, hot-water lift, set temperature and ratio.

        The set temperature and the hot-water part-load ratio are connected only when the heater prepares hot water,
        because only then does it declare the inputs that read them.
        """
        component_class = ElectricHeatingController
        controller_classname = component_class.get_classname()
        connections = [
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
        if self.config.with_domestic_hot_water_preparation:
            connections.append(
                ComponentConnection(
                    ElectricHeating.SupplyTemperatureSetForDHWInCelsius,
                    controller_classname,
                    component_class.SupplyTemperatureSetForDHWInCelsius,
                )
            )
            connections.append(
                ComponentConnection(
                    ElectricHeating.PartLoadRatioDhw,
                    controller_classname,
                    component_class.PartLoadRatioDhw,
                )
            )
        return connections

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
                component_class.StepMeanWaterTemperatureToHeatGeneratorInCelsius,
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
        """Heat the rooms and the hot water as the controller's mode asks, and publish the heat and electricity.

        A direct electric heater turns its electricity into heat one to one, so every heat it publishes is also its
        electricity. It computes every pass from its inputs, forced ones included: under ``force_convergence`` its
        controller holds the mode and the part-load ratio, and the heater books the heat its water carries from the
        tank's step mean of that pass, as the tank does.

        Raises:
            ValueError: If the mode is unknown, or the hot water takes the whole maximal power in parallel mode.
        """
        heating_mode = HeatingMode(stsv.get_input_value(self.heating_mode_channel))
        hot_water_ratio_run = 0.0
        if heating_mode in (
            HeatingMode.DOMESTIC_HOT_WATER,
            HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL,
        ):
            hot_water_ratio_run = self.dhw_part_load_command.ratio(stsv)
            hot_water = self.hot_water_circuit_of_step(stsv, timestep).at_part_load(
                part_load_ratio=hot_water_ratio_run, t_return_c=DualCircuitHotWater.return_temperature_in_celsius(stsv, self.water_input_temperature_dhw_channel)
            )
        elif heating_mode in (HeatingMode.SPACE_HEATING, HeatingMode.OFF):
            hot_water = hydronics.CircuitStep.idle(
                t_return_c=DualCircuitHotWater.return_temperature_in_celsius(stsv, self.water_input_temperature_dhw_channel)
            )
        else:
            raise ValueError("Unknown heating mode")
        if self.config.with_domestic_hot_water_preparation:
            self.dhw_part_load_command.publish_ratio_run(stsv, hot_water_ratio_run)
        space_heating = self.space_heating_delivery(stsv, heating_mode, hot_water.power_w)
        self.publish_heat(stsv, space_heating, hot_water)

    def hot_water_circuit_of_step(self, stsv: SingleTimeStepValues, timestep: int) -> hydronics.CircuitStep:
        """Return the hot-water circuit of a step that charges the tank, from the controller's lift and set point.

        Raises:
            ValueError: If the lift is negative or above 100 K.
        """
        lift_in_kelvin = stsv.get_input_value(self.delta_temperature_for_dhw_channel)
        self._check_delta_temperature(lift_in_kelvin, timestep)
        return self.hot_water_circuit(
            return_temperature_in_celsius=DualCircuitHotWater.return_temperature_in_celsius(
                stsv, self.water_input_temperature_dhw_channel
            ),
            lift_in_kelvin=lift_in_kelvin,
            supply_temperature_set_in_celsius=stsv.get_input_value(
                self.supply_temperature_set_for_dhw_in_celsius_channel
            ),
            maximal_supply_temperature_in_celsius=self.config.maximal_dhw_supply_temperature_in_celsius,
            maximal_power_in_watt=concrete(self.config.maximum_electric_power_w),
        )

    def space_heating_delivery(
        self, stsv: SingleTimeStepValues, heating_mode: "dual_circuit_system.HeatingMode", hot_water_power_in_watt: float
    ) -> "SpaceHeatingDelivery":
        """Return the heat the heater delivers to the rooms in this step, as power and energy.

        In space-heating mode the heater covers the building's heating demand; in parallel mode it covers it up to
        the power the hot water leaves; otherwise it delivers nothing. A negative demand (a cooling demand) gets no
        heat.

        Raises:
            ValueError: If the hot water takes the whole maximal power in parallel mode.
        """
        if heating_mode not in (HeatingMode.SPACE_HEATING, HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL):
            return SpaceHeatingDelivery(power_in_watt=0.0, energy_in_watt_hour=0.0)
        demand_in_watt = stsv.get_input_value(self.theoretical_thermal_building_power_channel)
        demand_in_watt_hour = stsv.get_input_value(self.theoretical_thermal_building_energy_channel)
        if demand_in_watt < 0:
            return SpaceHeatingDelivery(power_in_watt=0.0, energy_in_watt_hour=0.0)
        if heating_mode == HeatingMode.SPACE_HEATING:
            return SpaceHeatingDelivery(power_in_watt=demand_in_watt, energy_in_watt_hour=demand_in_watt_hour)
        maximum_electric_power_in_watt = concrete(self.config.maximum_electric_power_w)
        if maximum_electric_power_in_watt - hot_water_power_in_watt <= 0:
            raise ValueError(
                f"Electric load for DHW {hot_water_power_in_watt}W is equal or higher "
                f"than maximal electric load {maximum_electric_power_in_watt}. "
            )
        available_electric_load_in_watt = maximum_electric_power_in_watt - hot_water_power_in_watt
        if demand_in_watt > available_electric_load_in_watt:
            logging.debug("The needed thermal power for space heating is higher than the maximum connected load.")
        return SpaceHeatingDelivery(
            power_in_watt=min(demand_in_watt, available_electric_load_in_watt),
            energy_in_watt_hour=min(
                demand_in_watt_hour,
                available_electric_load_in_watt
                * self.my_simulation_parameters.seconds_per_timestep
                / hydronics.UnitConversion.JOULES_PER_WATT_HOUR,
            ),
        )

    def publish_heat(
        self, stsv: SingleTimeStepValues, space_heating: "SpaceHeatingDelivery", hot_water: hydronics.CircuitStep
    ) -> None:
        """Publish the rooms' heat, the hot-water circuit and the electricity, which equals the heat."""
        hot_water_energy_in_watt_hour = (
            hot_water.power_w
            * self.my_simulation_parameters.seconds_per_timestep
            / hydronics.UnitConversion.JOULES_PER_WATT_HOUR
        )
        stsv.set_output_value(self.thermal_output_power_dhw_channel, hot_water.power_w)
        stsv.set_output_value(self.thermal_output_energy_dhw_channel, hot_water_energy_in_watt_hour)
        stsv.set_output_value(self.water_output_temperature_dhw_channel, hot_water.t_supply_c)
        stsv.set_output_value(self.water_mass_flow_dhw_output_channel, hot_water.mass_flow_kg_per_s)
        stsv.set_output_value(self.thermal_output_power_sh_channel, space_heating.power_in_watt)
        stsv.set_output_value(self.thermal_output_energy_sh_channel, space_heating.energy_in_watt_hour)
        # a direct electric heater: its electricity is its heat
        stsv.set_output_value(self.electric_output_power_sh_channel, space_heating.power_in_watt)
        stsv.set_output_value(self.electric_output_energy_sh_channel, space_heating.energy_in_watt_hour)
        stsv.set_output_value(self.electric_output_power_dhw_channel, hot_water.power_w)
        stsv.set_output_value(self.electric_output_energy_dhw_channel, hot_water_energy_in_watt_hour)

    def _check_delta_temperature(self, delta_temperature: float, timestep: int) -> None:
        """Refuse a hot-water lift that is negative or above 100 K, naming the step.

        Raises:
            ValueError: If the lift is negative, which would ask a heater to cool, or above 100 K.
        """
        if delta_temperature < 0:
            raise ValueError(
                f"Delta temperature is {delta_temperature} °C"
                "but it should not be negative because electric heating cannot provide cooling. "
                "Please check your electric heating controller."
            )
        if delta_temperature > self.REGULATION_LIFT_AT_FULL_POWER_IN_KELVIN:
            raise ValueError(
                f"Delta temperature is {delta_temperature} °C in timestep {timestep}." "This is way too high. "
            )

    @staticmethod
    def hot_water_circuit(
        *,
        return_temperature_in_celsius: float,
        lift_in_kelvin: float,
        supply_temperature_set_in_celsius: float,
        maximal_supply_temperature_in_celsius: float,
        maximal_power_in_watt: float,
    ) -> hydronics.CircuitStep:
        """Return the electric heater's hot-water circuit for one step: its flow, its supply and the heat it carries.

        The heater holds the lift its controller asks for and regulates its power as
        ``P = P_max lift / REGULATION_LIFT_AT_FULL_POWER_IN_KELVIN``, at most ``P_max``; it pumps
        ``m = P / (c lift)`` and supplies the return plus the lift, capped at the lower of its maximal supply
        temperature and the controller's set temperature
        (:func:`hisim.hydronics.capped_hot_water_supply_temperature_c`). The heat is what the water carries,
        ``m c (T_sup - T_ret)``, which is ``P`` while nothing caps the supply. Without a lift the circuit moves no
        water. For example, a 6 kW heater asked for a 25 K lift on a 50 °C return heats with 1.5 kW at
        0.0144 kg/s and supplies 75 °C; with a 70 °C set temperature it supplies 70 °C and carries 1.2 kW.

        Args:
            return_temperature_in_celsius: The circuit's return temperature, the tank's step mean, in °C.
            lift_in_kelvin: The lift the controller asks for, in K; 0 or less asks for no hot water.
            supply_temperature_set_in_celsius: The supply temperature the controller aims at, in °C.
            maximal_supply_temperature_in_celsius: The heater's maximal hot-water supply temperature, in °C.
            maximal_power_in_watt: The heater's maximal electric power, in W.

        Returns:
            The circuit's mass flow, supply temperature and heat.
        """
        if lift_in_kelvin <= 0:
            return hydronics.CircuitStep.idle(t_return_c=return_temperature_in_celsius)
        regulated_power_in_watt = min(
            maximal_power_in_watt * lift_in_kelvin / ElectricHeating.REGULATION_LIFT_AT_FULL_POWER_IN_KELVIN,
            maximal_power_in_watt,
        )
        mass_flow_in_kg_per_second = regulated_power_in_watt / (
            hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * lift_in_kelvin
        )
        supply_temperature_in_celsius = hydronics.capped_hot_water_supply_temperature_c(
            unthrottled_supply_c=return_temperature_in_celsius + lift_in_kelvin,
            return_c=return_temperature_in_celsius,
            maximal_supply_c=maximal_supply_temperature_in_celsius,
            set_supply_c=supply_temperature_set_in_celsius,
        )
        return hydronics.CircuitStep(
            mass_flow_kg_per_s=mass_flow_in_kg_per_second,
            t_supply_c=supply_temperature_in_celsius,
            power_w=hydronics.circuit_power_w(
                mass_flow_kg_per_s=mass_flow_in_kg_per_second,
                t_supply_c=supply_temperature_in_celsius,
                t_return_c=return_temperature_in_celsius,
            ),
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
        description="Size.HEATING_LOAD_IN_WATT / Size.CONDITIONED_FLOOR_AREA_IN_M2",
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
    """Electric Heating Controller.

    It decides the heater's mode on the hot-water tank's start-of-step temperature and the daily outside temperature,
    and owns how much of a step a hot-water charge runs (:class:`hisim.part_load.PartLoadControl`): the part-load ratio
    ``PartLoadRatioDhw`` is 1 whenever the charge runs at and below the part-load threshold; above it the ratio follows
    :class:`hisim.part_load.PartLoadRule` from the ratio the heater reports it ran with (``PartLoadRatioRunDhw``) and
    the tank's start and end temperatures, so that the tank ends the step at the temperature at which the controller
    ends the charge (``TargetTemperatureDhwInCelsius``, :meth:`charge_end_temperature_in_celsius`).
    """

    KIND = ComponentKind.L1_CONTROLLER

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Relevant when used for dhw as well
    WaterTemperatureInputFromWarmWaterStorage = "WaterTemperatureInputFromWarmWaterStorage"
    #: The hot-water tank's temperature at the end of the step, which the part-load ratio is set against.
    WaterTemperatureAtEndOfStepFromWarmWaterStorageInCelsius = "WaterTemperatureAtEndOfStepFromWarmWaterStorageInCelsius"
    #: The fraction of the step the heater's hot-water charge ran, as the heater reports it.
    PartLoadRatioRunDhw = "PartLoadRatioRunDhw"

    # Outputs
    DeltaTemperatureNeededForDHW = "DeltaTemperatureNeededForDHW"
    DeltaTemperatureNeededForSH = "DeltaTemperatureNeededForSH"
    #: The hot-water supply temperature the controller aims at: its 60 °C aim plus its hysteresis, 75 °C by default.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    OperatingMode = "HeatingMode"
    #: The fraction of the step the heater's hot-water charge runs.
    PartLoadRatioDhw = "PartLoadRatioDhw"
    #: The tank temperature at which the controller ends a hot-water charge, its warm-water aim.
    TargetTemperatureDhwInCelsius = "TargetTemperatureDhwInCelsius"

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
        self.dhw_part_load = PartLoadControl(
            self,
            end_temperature_input_name=self.WaterTemperatureAtEndOfStepFromWarmWaterStorageInCelsius,
            ratio_run_input_name=self.PartLoadRatioRunDhw,
            ratio_output_name=self.PartLoadRatioDhw,
            target_output_name=self.TargetTemperatureDhwInCelsius,
            device_description="the heater's hot-water charge",
            controls_a_store=self.config.with_domestic_hot_water_preparation,
        )

        self.delta_temperature_for_dhw_to_electric_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.DeltaTemperatureNeededForDHW,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=f"here a description for {self.DeltaTemperatureNeededForDHW} will follow.",
        )
        self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.SupplyTemperatureSetForDHWInCelsius,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=(
                "The hot-water supply temperature the controller aims at, the warm-water aim plus the hysteresis "
                "(75 °C by default). The heater's hot-water supply stops at it, so a charge ends at its target inside "
                "the step."
            ),
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
            self.add_default_connections(self.get_default_connections_from_electric_heating())

    def get_default_connections_from_electric_heating(self) -> List[ComponentConnection]:
        """Return the default connection from the heater: the part-load ratio its hot-water charge ran with.

        The controller sets the next ratio from the ratio the heater reports, so the heater's report is wired back to
        it; only a controller with hot water reads it.

        Returns:
            The one connection of ``PartLoadRatioRunDhw``.
        """
        return [
            ComponentConnection(
                ElectricHeatingController.PartLoadRatioRunDhw,
                ElectricHeating.get_classname(),
                ElectricHeating.PartLoadRatioRunDhw,
            )
        ]

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        storage_classname = SimpleDHWStorage.get_classname()
        return [
            ComponentConnection(
                ElectricHeatingController.WaterTemperatureInputFromWarmWaterStorage,
                storage_classname,
                # the tank's start-of-step temperature T0: the controller decides on a value the step's
                # iteration does not move
                SimpleDHWStorage.WaterTemperatureAtStartOfStepInCelsius,
            ),
            ComponentConnection(
                ElectricHeatingController.WaterTemperatureAtEndOfStepFromWarmWaterStorageInCelsius,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius,
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
        # the supply temperature a hot-water charge aims at: the warm-water aim plus the hysteresis
        self.hot_water_supply_temperature_set_in_celsius: float = (
            self.warm_water_temperature_aim_in_celsius + self.config.hysteresis_water_temperature_offset
        )
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
        """Decide the heater's mode on the tank's start temperature and the outside temperature, and the hot-water ratio.

        Under ``force_convergence`` every output keeps the value of the last pass, except the part-load ratio: it
        becomes the ratio the heater reports it ran with, so the charge stops changing.
        """

        if force_convergence:
            self.dhw_part_load.publish_held(stsv)
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

        stsv.set_output_value(
            self.supply_temperature_set_for_dhw_in_celsius_channel, self.hot_water_supply_temperature_set_in_celsius
        )
        stsv.set_output_value(self.heating_mode_output_channel, self.controller_mode.value)
        self.dhw_part_load.publish(
            stsv,
            device_runs=self.controller_mode
            in (HeatingMode.DOMESTIC_HOT_WATER, HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL),
            start_temperature_in_celsius=water_temperature_input_from_warm_water_storage_in_celsius or 0.0,
            target_temperature_in_celsius=self.charge_end_temperature_in_celsius(
                self.controller_mode,
                warm_water_temperature_aim_in_celsius=self.warm_water_temperature_aim_in_celsius,
                hysteresis_in_kelvin=self.config.hysteresis_water_temperature_offset,
            ),
        )

    @staticmethod
    def charge_end_temperature_in_celsius(
        mode: HeatingMode, *, warm_water_temperature_aim_in_celsius: float, hysteresis_in_kelvin: float
    ) -> float:
        """Return the tank temperature at which the controller ends a hot-water charge in a mode, in °C.

        The diverter valve keeps a charge that runs alone going until the tank starts a step at the warm-water aim. A
        charge beside space heating, in the parallel mode, is kept going only while the tank is below the switch-on
        point, the aim less the hysteresis. Example: with the 60 °C aim and the 15 K hysteresis, 60 °C alone and 45 °C
        beside space heating.

        Args:
            mode: The controller's mode in this pass.
            warm_water_temperature_aim_in_celsius: The controller's warm-water aim, °C.
            hysteresis_in_kelvin: The controller's hysteresis below the aim, K.

        Returns:
            The temperature the charge ends at, °C.
        """
        if mode == HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL:
            return warm_water_temperature_aim_in_celsius - hysteresis_in_kelvin
        return warm_water_temperature_aim_in_celsius

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
            assert dhw_current_temperature_deg_c is not None
            delta_temperature_for_dhw_in_celsius = DualCircuitHotWater.lift_in_kelvin(
                aim_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius,
                storage_temperature_in_celsius=dhw_current_temperature_deg_c,
                hysteresis_in_kelvin=self.config.hysteresis_water_temperature_offset,
            )

        elif self.controller_mode == HeatingMode.OFF:
            delta_temperature_for_dhw_in_celsius = 0.0

        elif self.controller_mode == HeatingMode.SPACE_HEATING_AND_DOMESTIC_HOT_WATER_IN_PARALLEL:
            assert dhw_current_temperature_deg_c is not None
            delta_temperature_for_dhw_in_celsius = DualCircuitHotWater.lift_in_kelvin(
                aim_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius,
                storage_temperature_in_celsius=dhw_current_temperature_deg_c,
                hysteresis_in_kelvin=self.config.hysteresis_water_temperature_offset,
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
