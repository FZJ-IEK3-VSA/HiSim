""" Battery implementation built upon the bslib library. It contains a Battery Class together with its Configuration and State. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import List, Tuple, Optional
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
    ConfigBase,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions

__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"


@dataclass_json
@dataclass
class BatteryConfig(ConfigBase):
    """Battery Configuration."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return Battery.get_full_classname()

    #: building_name in which component is
    building_name: str
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: name of battery to search in database (bslib)
    system_id: str
    #: charging and discharging power in Watt
    custom_pv_inverter_power_generic_in_watt: float
    #: battery capacity in in kWh
    custom_battery_capacity_generic_in_kilowatt_hour: float
    #: amount of energy used to charge the car battery
    charge_in_kwh: float
    #: amount of energy discharged from the battery
    discharge_in_kwh: float
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
    #: lifetime of battery in full cycles
    lifetime_in_cycles: float

    @classmethod
    def get_default_config(cls, building_name: str = "BUI1", name: str = "Battery") -> "BatteryConfig":
        """Returns default configuration of battery."""
        custom_battery_capacity_generic_in_kilowatt_hour = (
            10  # size/capacity of battery should be approx. the same as default pv power
        )
        config = BatteryConfig(
            building_name=building_name,
            name=name,
            # https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
            custom_battery_capacity_generic_in_kilowatt_hour=round(custom_battery_capacity_generic_in_kilowatt_hour, 2),
            custom_pv_inverter_power_generic_in_watt=round(10 * 0.5 * 1e3, 2),  # c-rate is 0.5C (0.5/h) here
            source_weight=1,
            system_id="SG1",
            charge_in_kwh=0,
            discharge_in_kwh=0,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            lifetime_in_cycles=5e3,  # estimated value , source: https://pv-held.de/wie-lange-haelt-batteriespeicher-photovoltaik/
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None
        )
        return config

    @classmethod
    def get_scaled_battery(
        cls, total_pv_power_in_watt_peak: float, building_name: str = "BUI1", name: str = "Battery"
    ) -> "BatteryConfig":
        """Returns scaled configuration of battery according to pv power."""
        custom_battery_capacity_generic_in_kilowatt_hour = (
            total_pv_power_in_watt_peak * 1e-3
        )  # size/capacity of battery should be approx. the same as default pv power
        c_rate = 0.5  # 0.5C corresponds to 0.5/h for fully charging or discharging
        config = BatteryConfig(
            building_name=building_name,
            name=name,
            # https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
            custom_battery_capacity_generic_in_kilowatt_hour=round(custom_battery_capacity_generic_in_kilowatt_hour, 2),
            custom_pv_inverter_power_generic_in_watt=round(custom_battery_capacity_generic_in_kilowatt_hour * c_rate * 1e3, 2),
            source_weight=1,
            system_id="SG1",
            charge_in_kwh=0,
            discharge_in_kwh=0,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            lifetime_in_cycles=5e3,  # todo set correct values
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None
        )

        return config


class Battery(Component):
    """Battery class.

    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) Energy Management System
    """

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
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Loads the parameters of the specified battery storage."""
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

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.custom_pv_inverter_power_generic_in_watt = self.battery_config.custom_pv_inverter_power_generic_in_watt

        self.custom_battery_capacity_generic_in_kilowatt_hour = (
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
        self.charing_power_channel: ComponentOutput = self.add_output(
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

        # Simulate on timestep
        results = self.ac_coupled_battery_object.simulate(
            p_load=set_point_for_ac_battery_power_in_watt, soc=state_of_charge, dt=time_increment_in_seconds,
        )
        # The bslib simulation returns how much of loading power input was actually used for charging and discharging and the resulting state of charge
        ac_battery_power_used_for_charging_or_discharging_in_watt = results[0]
        dc_battery_power_used_for_charging_or_discharging_in_watt = results[1]
        state_of_charge = results[2]
        if state_of_charge < 0:
            log.warning("SOC of Battery cannot be negative. Check your configuration.")
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
        stsv.set_output_value(self.charing_power_channel, charging_power_in_watt)
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
            config.charge_in_kwh / config.custom_battery_capacity_generic_in_kilowatt_hour
        )
        # virtual_number_of_full_discharge_cycles = self.battery_config.discharge_in_kwh / self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour

        return virtual_number_of_full_charge_cycles, config.lifetime_in_cycles

    @staticmethod
    def get_cost_capex(
        config: BatteryConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""

        component_type = ComponentType.BATTERY
        kpi_tag = (
            KpiTagEnumClass.BATTERY
        )
        unit = Units.KWH
        size_of_energy_system = config.custom_battery_capacity_generic_in_kilowatt_hour * 1e-3

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

        # overwrite capex and emission based on battery cycles
        capex_cost_data_class.capex_investment_cost_for_simulated_period_in_euro = capex_per_simulated_period
        capex_cost_data_class.device_co2_footprint_for_simulated_period_in_kg = device_co2_footprint_per_simulated_period

        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(config=config, capex_cost_data_class=capex_cost_data_class)
        return capex_cost_data_class

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
            self.my_simulation_parameters.year
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
