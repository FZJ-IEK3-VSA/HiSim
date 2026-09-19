""" Battery implementation built upon the bslib library. It contains a Battery Class together with its Configuration and State. """

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import ClassVar, List, Tuple, Optional
from dataclasses import dataclass
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

import pandas as pd

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Sizable,
    Size,
    SizingLaw,
    concrete,
    preset,
    sized_field,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


@dataclass_json
@dataclass
class BatteryConfig(ConfigBase):
    """Battery Configuration.

    The named default battery is :meth:`preset_sized_to_pv`, and both of its power numbers are
    sizable: the preset leaves them ``AUTO`` and ``.resolve(ctx)`` derives them from the peak
    power of the PV array the battery is installed beside. An author who knows the device pins
    the two fields instead.
    """

    MAIN_CLASS = "hisim.components.advanced_battery_bslib.Battery"

    #: Sizing law of the battery's capacity: one kilowatt hour of storage per kilowatt peak of
    #: PV, rounded to two decimals. Named as a ClassVar so the field declaration reads as one
    #: line and the rule of thumb is written down in one place.
    #: See https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
    CAPACITY_LAW: ClassVar[SizingLaw] = (Size.PV_PEAK_POWER_IN_WATT * 1e-3).rounded(2)

    #: Sizing law of the charging and discharging power: a C-rate of 0.5 (half the capacity per
    #: hour) on the capacity the law above gives, which is the array's peak power in watt times
    #: 0.5. It reads the fact rather than the sibling capacity field on purpose: the capacity is
    #: rounded to two decimals before it is stored, and inverting that rounding into the inverter
    #: power would move the number by about a watt on a fleet-sized array.
    INVERTER_POWER_LAW: ClassVar[SizingLaw] = (Size.PV_PEAK_POWER_IN_WATT * 0.5).rounded(2)

    #: structured identity (name, building, unit) of the component
    component_id: ComponentID
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int = 1
    #: The battery to look up in the bslib database, by that database's own identifier;
    #: SG1 is its generic lithium-ion system.
    system_id: str = "SG1"
    #: amount of energy used to charge the battery, i.e. the state it starts in
    charge_in_kwh: float = 0
    #: amount of energy discharged from the battery, i.e. the state it starts in
    discharge_in_kwh: float = 0
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the device up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None
    #: lifetime of battery in full cycles; estimated, see
    #: https://pv-held.de/wie-lange-haelt-batteriespeicher-photovoltaik/
    lifetime_in_cycles: float = 5e3
    #: charging and discharging power in Watt. Sizable: left ``AUTO`` it is computed by
    #: :data:`INVERTER_POWER_LAW` from the PV peak power the array contributes.
    custom_pv_inverter_power_generic_in_watt: Sizable[float] = sized_field(rule=INVERTER_POWER_LAW)
    #: battery capacity in kWh. Sizable: left ``AUTO`` it is computed by :data:`CAPACITY_LAW`
    #: from the same fact. Marked as the capacity field for the cost engine.
    custom_battery_capacity_generic_in_kilowatt_hour: Sizable[float] = sized_field(
        rule=CAPACITY_LAW, metadata={"capacity": True}
    )

    @preset
    @classmethod
    def preset_sized_to_pv(cls, name: str) -> "BatteryConfig":
        """The fleet's home battery, scaled to the PV array it is installed beside.

        The field defaults are this battery: a single ``SG1`` lithium-ion system from the bslib
        database, first in the energy management hierarchy, starting empty and rated for five
        thousand full cycles. What the preset does not fix is how big the device is:
        ``custom_battery_capacity_generic_in_kilowatt_hour`` and
        ``custom_pv_inverter_power_generic_in_watt`` stay ``AUTO`` so that :data:`CAPACITY_LAW`
        and :data:`INVERTER_POWER_LAW` derive them from the array's peak power, and an author who
        knows the device pins the two fields instead.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            BatteryConfig: The preset configuration, with both power numbers unsized.
        """
        return cls(component_id=ComponentID(name=name))


class Battery(Component):
    """Battery class.

    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) Energy Management System
    """

    # Lifecycle cost engine declaration (cost_spec.md §9.2).
    cost_relevance = CostRelevance.PRICED

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"  # W

    # Outputs
    AcBatteryPowerUsed = "AcBatteryPowerUsed"  # W
    DcBatteryPowerUsed = "DcBatteryPowerUsed"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]
    ChargingPower = "ChargingPower"
    DischargingPower = "DischargingPower"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BatteryConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ):
        """Loads the parameters of the specified battery storage."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.battery_config = config

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.negative_soa_warning: bool = True

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.custom_pv_inverter_power_generic_in_watt = concrete(
            self.battery_config.custom_pv_inverter_power_generic_in_watt
        )

        self.custom_battery_capacity_generic_in_kilowatt_hour = concrete(
            self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour
        )

        # Component has states
        self.state = BatteryState()
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.ac_coupled_battery_object = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.custom_pv_inverter_power_generic_in_watt,
            e_bat_custom=self.custom_battery_capacity_generic_in_kilowatt_hour,
        )

        # Define component inputs
        self.loading_power_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.LoadingPowerInput,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.ac_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryPowerUsed,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[InandOutputType.CHARGE_DISCHARGE, ComponentType.BATTERY],
            output_description=f"here a description for {self.AcBatteryPowerUsed} will follow.",
        )

        self.dc_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryPowerUsed,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.DcBatteryPowerUsed} will follow.",
        )

        self.state_of_charge_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description=f"here a description for {self.StateOfCharge} will follow.",
        )
        self.charging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ChargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.ChargingPower} will follow.",
        )

        self.discharging_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DischargingPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.DischargingPower} will follow.",
        )

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Parameters
        time_increment_in_seconds = self.my_simulation_parameters.seconds_per_timestep

        # Load input values
        set_point_for_ac_battery_power_in_watt = stsv.get_input_value(self.loading_power_input_channel)
        state_of_charge = self.state.state_of_charge

        # Simulate on timestep.
        # The bslib simulation returns how much of loading power input was actually used
        # for charging and discharging and the resulting state of charge.
        (
            ac_battery_power_used_for_charging_or_discharging_in_watt,
            dc_battery_power_used_for_charging_or_discharging_in_watt,
            state_of_charge,
        ) = self.ac_coupled_battery_object.simulate(
            p_load=set_point_for_ac_battery_power_in_watt,
            soc=state_of_charge,
            dt=time_increment_in_seconds,
        )

        if state_of_charge < 0 and self.negative_soa_warning:
            log.warning("SOC of Battery cannot be negative. Check your configuration.")
            self.negative_soa_warning = False  # make sure that the warning comes only once, otherwise too much logging

        # get charging and discharging power
        if ac_battery_power_used_for_charging_or_discharging_in_watt > 0:
            charging_power_in_watt = ac_battery_power_used_for_charging_or_discharging_in_watt
            discharging_power_in_watt = 0
        elif ac_battery_power_used_for_charging_or_discharging_in_watt < 0:
            charging_power_in_watt = 0
            discharging_power_in_watt = ac_battery_power_used_for_charging_or_discharging_in_watt
        else:
            charging_power_in_watt = 0
            discharging_power_in_watt = 0

        # write values for output time series
        stsv.set_output_value(self.ac_battery_power_channel, ac_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.dc_battery_power_channel, dc_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.state_of_charge_channel, state_of_charge)
        stsv.set_output_value(self.charging_power_channel, charging_power_in_watt)
        stsv.set_output_value(self.discharging_power_channel, discharging_power_in_watt)

        # write values to state
        self.state.state_of_charge = state_of_charge

    def write_to_report(self) -> List[str]:
        """Write to report."""
        return self.battery_config.get_string_dict()

    @staticmethod
    def get_battery_aging_information(config: BatteryConfig) -> Tuple[float, float]:
        """Calculate battery aging.

        This is used to calculate investment costs for battery per simulated period.
        Battery aging is ROUGHLY approximated by costs for each virtual charging cycle used in simulated period
        (costs_per_cycle = investment / lifetime_in_cycles).
        """
        # Todo: Think about better approximation for costs of battery aging

        virtual_number_of_full_charge_cycles = (
            config.charge_in_kwh / concrete(config.custom_battery_capacity_generic_in_kilowatt_hour)
        )
        # virtual_number_of_full_discharge_cycles = self.battery_config.discharge_in_kwh / self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour

        return virtual_number_of_full_charge_cycles, config.lifetime_in_cycles

    @staticmethod
    def get_cost_capex(
        config: BatteryConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""

        # Note for the parity harness: the legacy computation below scales the kWh capacity by
        # 1e-3 (latent unit bug, cost_module_issues.md #20a); get_cost_facts() declares the
        # physically correct size, so the parity report shows an explained battery delta.
        component_type = ComponentType.BATTERY
        kpi_tag = KpiTagEnumClass.BATTERY
        unit = Units.KWH
        size_of_energy_system = concrete(config.custom_battery_capacity_generic_in_kilowatt_hour) * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=component_type,
        unit=unit,
        size_of_energy_system=size_of_energy_system,
        config=config,
        kpi_tag=kpi_tag
        )

        # Todo: think about livetime in cycles not in years
        (virtual_number_of_full_charge_cycles, lifetime_in_cycles) = Battery.get_battery_aging_information(
            config=config
        )
        if lifetime_in_cycles > 0:
            capex_per_simulated_period = (capex_cost_data_class.capex_investment_cost_in_euro / lifetime_in_cycles) * (virtual_number_of_full_charge_cycles)
            device_co2_footprint_per_simulated_period = (capex_cost_data_class.device_co2_footprint_in_kg / lifetime_in_cycles) * (
                virtual_number_of_full_charge_cycles
            )

        else:
            log.warning("Capex calculation not valid. Check lifetime_in_cycles in Configuration of Battery.")
            return capex_cost_data_class

        # overwrite capex and emission based on battery cycles
        capex_cost_data_class.capex_investment_cost_for_simulated_period_in_euro = capex_per_simulated_period
        capex_cost_data_class.device_co2_footprint_for_simulated_period_in_kg = device_co2_footprint_per_simulated_period

        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)
        return capex_cost_data_class

    def get_cost_facts(self) -> ComponentCostFacts:
        """Cost facts for the lifecycle cost engine (cost_spec.md §3.3, §9.1).

        Declares the battery as one priced subject: a `BATTERY` entry of the cost database, sized
        by its **capacity in kWh** — the unit batteries are quoted in (EUR/kWh), and the
        unit the database row must therefore declare as its `per_unit`. The config field is
        already in kilowatt-hours, so no conversion happens here; a factor slipped in at this line
        would silently move the whole battery investment by three orders of magnitude.

        Everything monetary comes from the database unless the config carries an explicit figure
        (from the building sizer or a RenoVisor request), in which case it is passed through as a
        per-field override together with the `override_source` the provenance ledger requires.
        The legacy `get_cost_capex` above is deliberately left as it is; the parity report
        compares the two.

        Returns:
            The facts for this battery; never None, since the class declares `PRICED`.
        """
        config = self.battery_config
        return ComponentCostFacts(
            asset_class=ComponentType.BATTERY,
            size=concrete(config.custom_battery_capacity_generic_in_kilowatt_hour),
            size_unit=Units.KWH,
            kpi_tag=KpiTagEnumClass.BATTERY,
            investment_cost_override_in_euro=(
                UncertainValue.exact(config.investment_costs_in_euro)
                if config.investment_costs_in_euro is not None
                else None
            ),
            lifetime_override_in_years=config.lifetime_in_years,
            embodied_co2_override_in_kg=config.device_co2_footprint_in_kg,
            # See generic_pv_system: any of the three overrides needs the provenance, not just
            # the investment.
            override_source=(
                "component config (e.g. building_sizer / RenoVisor request)"
                if (
                    config.investment_costs_in_euro is not None
                    or config.lifetime_in_years is not None
                    or config.device_co2_footprint_in_kg is not None
                )
                else None
            ),
        )

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs."""
        battery_losses_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None and output.component_name == self.component_name:
                if InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    self.battery_config.charge_in_kwh = round(
                        postprocessing_results.iloc[:, index].clip(lower=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6,
                        1,
                    )
                    self.battery_config.discharge_in_kwh = round(
                        postprocessing_results.iloc[:, index].clip(upper=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6,
                        1,
                    ) * (-1)
                    battery_losses_in_kwh = self.battery_config.charge_in_kwh - self.battery_config.discharge_in_kwh

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh
        co2_per_simulated_period_in_kg = battery_losses_in_kwh * co2_per_unit
        opex_energy_cost_per_simulated_period_in_euro = battery_losses_in_kwh * euro_per_unit

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=battery_losses_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.BATTERY,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass
class BatteryState:
    """Battery state class."""

    #: state of charge of the battery
    state_of_charge: float = 0

    def clone(self):
        """Creates a copy of the Battery State."""
        return BatteryState(state_of_charge=self.state_of_charge)
