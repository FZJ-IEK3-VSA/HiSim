""" Battery implementation built upon the bslib library. It contains a Battery Class together with its Configuration and State. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import List, Tuple
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
    CapexCostDataClass
)
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass

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
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime of battery in years
    lifetime: float
    #: lifetime of battery in full cycles
    lifetime_in_cycles: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float

    @classmethod
    def get_default_config(cls) -> "BatteryConfig":
        """Returns default configuration of battery."""
        custom_battery_capacity_generic_in_kilowatt_hour = (
            10  # size/capacity of battery should be approx. the same as default pv power
        )
        config = BatteryConfig(
            name="Battery",
            # https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
            custom_battery_capacity_generic_in_kilowatt_hour=custom_battery_capacity_generic_in_kilowatt_hour,
            custom_pv_inverter_power_generic_in_watt=10 * 0.5 * 1e3,  # c-rate is 0.5C (0.5/h) here
            source_weight=1,
            system_id="SG1",
            charge_in_kwh=0,
            discharge_in_kwh=0,
            co2_footprint=custom_battery_capacity_generic_in_kilowatt_hour
            * 130.7,  # value from emission_factros_and_costs_devices.csv
            cost=custom_battery_capacity_generic_in_kilowatt_hour
            * 535.81,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # estimated value , source: https://pv-held.de/wie-lange-haelt-batteriespeicher-photovoltaik/
            lifetime_in_cycles=5e3,  # estimated value , source: https://pv-held.de/wie-lange-haelt-batteriespeicher-photovoltaik/
            maintenance_cost_as_percentage_of_investment=0.02,  # SOURCE: https://solarenergie.de/stromspeicher/preise
        )
        return config

    @classmethod
    def get_scaled_battery(cls, total_pv_power_in_watt_peak: float) -> "BatteryConfig":
        """Returns scaled configuration of battery according to pv power."""
        custom_battery_capacity_generic_in_kilowatt_hour = (
            total_pv_power_in_watt_peak * 1e-3
        )  # size/capacity of battery should be approx. the same as default pv power
        c_rate = 0.5  # 0.5C corresponds to 0.5/h for fully charging or discharging
        config = BatteryConfig(
            name="Battery",
            # https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
            custom_battery_capacity_generic_in_kilowatt_hour=custom_battery_capacity_generic_in_kilowatt_hour,
            custom_pv_inverter_power_generic_in_watt=custom_battery_capacity_generic_in_kilowatt_hour * c_rate * 1e3,
            source_weight=1,
            system_id="SG1",
            charge_in_kwh=0,
            discharge_in_kwh=0,
            co2_footprint=custom_battery_capacity_generic_in_kilowatt_hour
            * 130.7,  # value from emission_factros_and_costs_devices.csv
            cost=custom_battery_capacity_generic_in_kilowatt_hour
            * 535.81,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # todo set correct values
            lifetime_in_cycles=5e3,  # todo set correct values
            maintenance_cost_as_percentage_of_investment=0.02,  # SOURCE: https://solarenergie.de/stromspeicher/preise
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

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BatteryConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Loads the parameters of the specified battery storage."""
        self.battery_config = config
        super().__init__(
            name=self.battery_config.name + "_w" + str(self.battery_config.source_weight),
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
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.BATTERY,
            ],
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
            p_load=set_point_for_ac_battery_power_in_watt,
            soc=state_of_charge,
            dt=time_increment_in_seconds,
        )
        # The bslib simulation returns how much of loading power input was actually used for charging and discharging and the resulting state of charge
        ac_battery_power_used_for_charging_or_discharging_in_watt = results[0]
        dc_battery_power_used_for_charging_or_discharging_in_watt = results[1]
        state_of_charge = results[2]

        # write values for output time series
        stsv.set_output_value(self.ac_battery_power_channel, ac_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.dc_battery_power_channel, dc_battery_power_used_for_charging_or_discharging_in_watt)
        stsv.set_output_value(self.state_of_charge_channel, state_of_charge)

        # write values to state
        self.state.state_of_charge = state_of_charge

    def write_to_report(self) -> List[str]:
        """Write to report."""
        return self.battery_config.get_string_dict()

    @staticmethod
    def get_battery_aging_information(
        config: BatteryConfig
    ) -> Tuple[float, float]:
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
    def get_cost_capex(config: BatteryConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        # Todo: think about livetime in cycles not in years
        (virtual_number_of_full_charge_cycles, lifetime_in_cycles) = Battery.get_battery_aging_information(config=config)
        if lifetime_in_cycles > 0:
            capex_per_simulated_period = (config.cost / lifetime_in_cycles) * (virtual_number_of_full_charge_cycles)
            device_co2_footprint_per_simulated_period = (config.co2_footprint / lifetime_in_cycles) * (
                virtual_number_of_full_charge_cycles
            )
        else:
            log.warning(
                "Capex calculation not valid. Check lifetime_in_cycles in Configuration of Battery."
            )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.cost,
            device_co2_footprint_in_kg=config.co2_footprint,
            lifetime_in_years=config.lifetime,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.BATTERY
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs."""
        battery_losses_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.battery_config.name + "_w" + str(self.battery_config.source_weight)
            ):
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


        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=battery_losses_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.BATTERY
        )

        return opex_cost_data_class


@dataclass
class BatteryState:

    """Battery state class."""

    #: state of charge of the battery
    state_of_charge: float = 0

    def clone(self):
        """Creates a copy of the Battery State."""
        return BatteryState(state_of_charge=self.state_of_charge)
