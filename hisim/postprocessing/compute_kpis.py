# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate.

KPis for PV-battery systems in houses:
https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
"""

import os
from typing import List, Tuple, Union, Dict, Optional
from pathlib import Path
from dataclasses import dataclass, InitVar
from enum import Enum
import numpy as np
import pandas as pd
from dataclass_wizard import JSONWizard
from hisim.component import ComponentOutput
from hisim.components.heat_distribution_system import HeatDistribution
from hisim.components.building import Building
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib

# from hisim.components.simple_hot_water_storage import SimpleHotWaterStorage
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.generic_heat_pump_modular import ModularHeatPump
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.controller_l2_district_energy_management_system import L2GenericDistrictEnergyManagementSystem
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.utils import HISIMPATH
from hisim import log
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer


class KpiTagEnumClass(Enum):

    """Determine KPI tags as enums."""

    GENERAL = "General"
    COSTS_AND_EMISSIONS = "Costs and Emissions"
    BUILDING = "Building"
    BATTERY = "Battery"
    HEATDISTRIBUTIONSYSTEM = "Heat Distribution System"
    HEATPUMP_SPACE_HEATING = "Heat Pump For Space Heating"
    HEATPUMP_DOMESTIC_HOT_WATER = "Heat Pump For Domestic Hot Water"
    RESIDENTS = "Residents"


@dataclass
class KpiEntry(JSONWizard):

    """Class for storing one kpi entry."""

    name: str
    unit: str
    value: Optional[float]
    description: Optional[str] = None
    tag: Optional[KpiTagEnumClass] = None


@dataclass
class KpiGenerator(JSONWizard):

    """Class for generating and calculating key performance indicators."""

    post_processing_data_transfer: InitVar[PostProcessingDataTransfer]

    def __post_init__(self, post_processing_data_transfer):
        """Build the dataclass from input data."""
        self.kpi_collection_dict_unsorted: Dict = {}
        self.create_kpi_collection(post_processing_data_transfer)
        self.kpi_collection_dict_sorted = self.sort_kpi_collection_according_to_kpi_tags(
            kpi_collection_dict_unsorted=self.kpi_collection_dict_unsorted
        )
        self.return_table_for_report()

    def create_kpi_collection(self, post_processing_data_transfer):
        """Create kpi collection and write back into post processing data transfer."""

        # get important variables
        self.wrapped_components = post_processing_data_transfer.wrapped_components
        self.results = post_processing_data_transfer.results
        self.all_outputs = post_processing_data_transfer.all_outputs
        self.simulation_parameters = post_processing_data_transfer.simulation_parameters

        # get filtered result dataframe
        self.filtered_result_dataframe = self.filter_results_according_to_postprocessing_flags(
            all_outputs=self.all_outputs, results=self.results
        )

        # get consumption and production and battery kpis
        (
            total_electricity_consumption_in_kilowatt_hour,
            total_electricity_production_in_kilowatt_hour,
            pv_production_in_kilowatt_hour,
        ) = self.compute_electricity_consumption_and_production_and_battery_kpis(
            result_dataframe=self.filtered_result_dataframe
        )

        # get ratio between total production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=total_electricity_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between total production and total consumption",
        )
        # get ratio between pv production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=pv_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between PV production and total consumption",
        )

        # get self-consumption, autarkie, injection
        (
            grid_injection_in_kilowatt_hour,
            self_consumption_in_kilowatt_hour,
            self.filtered_result_dataframe,
        ) = self.compute_self_consumption_injection_autarky(
            result_dataframe=self.filtered_result_dataframe,
            electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
        )
        # get electricity to and from grid
        (
            total_electricity_from_grid_in_kwh,
            total_electricity_to_grid_in_kwh,
        ) = self.get_electricity_to_and_from_grid_from_electricty_meter()
        # get relative electricity demand
        relative_electricity_demand_from_grid_in_percent = self.compute_relative_electricity_demand(
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
        )
        # get self-consumption rate according to solar htw berlin
        self.compute_self_consumption_rate_according_to_solar_htw_berlin(
            total_electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_to_grid_in_kilowatt_hour=total_electricity_to_grid_in_kwh,
        )
        # get autarky rate according to solar htw berlin
        self.compute_autarky_according_to_solar_htw_berlin(
            relative_electricty_demand_in_percent=relative_electricity_demand_from_grid_in_percent
        )

        # get energy prices and co2 emissions
        self.compute_energy_prices_and_co2_emission(
            result_dataframe=self.filtered_result_dataframe,
            injection=self.filtered_result_dataframe["grid_injection_in_watt"],
            self_consumption=self.filtered_result_dataframe["self_consumption_in_watt"],
            electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            grid_injection_in_kilowatt_hour=grid_injection_in_kilowatt_hour,
            self_consumption_in_kilowatt_hour=self_consumption_in_kilowatt_hour,
        )
        # get capex and opex costs
        self.read_opex_and_capex_costs_from_results()

        # get building performance indicators
        building_conditioned_floor_area_in_m2 = self.get_building_kpis()

        # get heat distriution system kpis
        self.get_heat_distribution_system_kpis(
            building_conditioned_floor_area_in_m2=building_conditioned_floor_area_in_m2
        )
        # get heat pump grid consumption from energy management system kpis
        (
            sh_heatpump_electricity_from_grid_in_kilowatt_hour,
            dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
            occupancy_electricity_from_grid_in_kilowatt_hour,
        ) = self.get_grid_consumptions_of_heat_pumps_via_energy_management_kpis()

        # get space heating heat pump performance indicators
        self.get_space_heating_heat_pump_kpis(
            building_conditioned_floor_area_in_m2=building_conditioned_floor_area_in_m2,
            sh_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour=sh_heatpump_electricity_from_grid_in_kilowatt_hour,
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            total_electricity_consumption_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
        )
        # get domestic hot water heat pump kpis from modular or more advanced heat pump
        self.get_dhw_heat_pump_kpis(
            dhw_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour=dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            total_electricity_consumption_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
        )

        # get occupancy electrictiy kpis
        self.get_occupancy_electricity_kpis(
            total_electricity_consumption_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            occupancy_electricity_from_grid_in_kilowatt_hour=occupancy_electricity_from_grid_in_kilowatt_hour,
        )

    def filter_results_according_to_postprocessing_flags(
        self, all_outputs: List, results: pd.DataFrame
    ) -> pd.DataFrame:
        """Filter results according to postprocessing flags and get consumption, production, battery charge and battery discharge.

        Also evaluates battery charge and discharge, because it is relevant for self consumption rates.
        To be recognised as production the connected outputs need a postprocessing flag: InandOutputType.ELECTRICITY_PRODUCTION,
        consumption is flagged with either InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED or InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
        storage charge/discharge is flagged with InandOutputType.CHARGE_DISCHARGE. For batteries to be considered as wished, they additionally need the
        Component itself as postprocesessing flag: ComponentType.CAR_BATTERY or ComponentType.BATTERY
        """

        # initialize columns consumption, production, battery_charge, battery_discharge, storage
        total_consumption_ids = []
        total_production_ids = []
        pv_production_ids = []
        battery_charge_discharge_ids = []

        index: int
        output: ComponentOutput

        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None:
                if InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag:
                    total_production_ids.append(index)

                elif (
                    InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag
                    and ComponentType.PV in output.postprocessing_flag
                ):
                    pv_production_ids.append(index)

                elif (
                    InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED in output.postprocessing_flag
                    or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in output.postprocessing_flag
                ):
                    total_consumption_ids.append(index)

                elif InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    if ComponentType.BATTERY in output.postprocessing_flag:
                        battery_charge_discharge_ids.append(index)
                    elif ComponentType.CAR_BATTERY in output.postprocessing_flag:
                        total_consumption_ids.append(index)
            else:
                continue

        result_dataframe = pd.DataFrame()
        result_dataframe["total_consumption"] = (
            pd.DataFrame(results.iloc[:, total_consumption_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["total_production"] = (
            pd.DataFrame(results.iloc[:, total_production_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["pv_production"] = (
            pd.DataFrame(results.iloc[:, total_production_ids]).clip(lower=0).sum(axis=1)
        )

        result_dataframe["battery_charge"] = (
            pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["battery_discharge"] = pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(
            upper=0
        ).sum(axis=1) * (-1)

        return result_dataframe

    def compute_total_energy_from_power_timeseries(
        self, power_timeseries_in_watt: pd.Series, timeresolution: int
    ) -> float:
        """Computes the energy in kWh from a power timeseries in W."""
        if power_timeseries_in_watt.empty:
            return 0.0

        energy_in_kilowatt_hour = float(power_timeseries_in_watt.sum() * timeresolution / 3.6e6)
        return energy_in_kilowatt_hour

    def compute_electricity_consumption_and_production_and_battery_kpis(
        self, result_dataframe: pd.DataFrame
    ) -> Tuple[float, float, float]:
        """Compute electricity consumption and production and battery kpis."""

        # sum consumption and production over time
        total_electricity_consumption_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
            power_timeseries_in_watt=result_dataframe["total_consumption"],
            timeresolution=self.simulation_parameters.seconds_per_timestep,
        )

        total_electricity_production_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
            power_timeseries_in_watt=result_dataframe["total_production"],
            timeresolution=self.simulation_parameters.seconds_per_timestep,
        )
        pv_production_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
            power_timeseries_in_watt=result_dataframe["pv_production"],
            timeresolution=self.simulation_parameters.seconds_per_timestep,
        )

        # compute battery kpis
        (
            battery_charging_energy_in_kilowatt_hour,
            battery_discharging_energy_in_kilowatt_hour,
            battery_losses_in_kilowatt_hour,
        ) = self.compute_battery_kpis(result_dataframe=result_dataframe)

        # if battery losses are not zero, add to total consumption because this is what is consumed by battery indepently from charging and discharging
        total_electricity_consumption_in_kilowatt_hour = (
            total_electricity_consumption_in_kilowatt_hour + battery_losses_in_kilowatt_hour
        )

        # make kpi entry
        total_consumtion_entry = KpiEntry(
            name="Total electricity consumption",
            unit="kWh",
            value=total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        total_production_entry = KpiEntry(
            name="Total electricity production",
            unit="kWh",
            value=total_electricity_production_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        pv_production_entry = KpiEntry(
            name="PV production", unit="kWh", value=pv_production_in_kilowatt_hour, tag=KpiTagEnumClass.GENERAL
        )
        battery_charging_entry = KpiEntry(
            name="Battery charging energy",
            unit="kWh",
            value=battery_charging_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.BATTERY,
        )
        battery_discharging_entry = KpiEntry(
            name="Battery discharging energy",
            unit="kWh",
            value=battery_discharging_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.BATTERY,
        )
        battery_losses_entry = KpiEntry(
            name="Battery losses", unit="kWh", value=battery_losses_in_kilowatt_hour, tag=KpiTagEnumClass.BATTERY
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                total_consumtion_entry.name: total_consumtion_entry.to_dict(),
                total_production_entry.name: total_production_entry.to_dict(),
                pv_production_entry.name: pv_production_entry.to_dict(),
                battery_charging_entry.name: battery_charging_entry.to_dict(),
                battery_discharging_entry.name: battery_discharging_entry.to_dict(),
                battery_losses_entry.name: battery_losses_entry.to_dict(),
            }
        )

        return (
            total_electricity_consumption_in_kilowatt_hour,
            total_electricity_production_in_kilowatt_hour,
            pv_production_in_kilowatt_hour,
        )

    def compute_self_consumption_injection_autarky(
        self,
        result_dataframe: pd.DataFrame,
        electricity_production_in_kilowatt_hour: float,
        electricity_consumption_in_kilowatt_hour: float,
    ) -> Tuple[float, float, pd.DataFrame]:
        """Computes the self consumption, grid injection, autarky and battery losses if electricty production is bigger than zero."""

        if electricity_production_in_kilowatt_hour > 0:
            # account for battery
            production_with_battery = result_dataframe["total_production"] + result_dataframe["battery_discharge"]
            consumption_with_battery = result_dataframe["total_consumption"] + result_dataframe["battery_charge"]

            # evaluate injection and sum over time
            grid_injection_series_in_watt: pd.Series = production_with_battery - consumption_with_battery

            # evaluate self consumption and immidiately sum over time
            # battery is charged (counting to consumption) and discharged (counting to production)
            # -> only one direction can be counted, otherwise the self-consumption can be greater than 100.
            # Here the production side is counted (battery_discharge).
            self_consumption_series_in_watt: pd.Series = (
                pd.concat(
                    (
                        production_with_battery[production_with_battery <= result_dataframe["total_consumption"]],
                        result_dataframe["total_consumption"][
                            result_dataframe["total_consumption"] < production_with_battery
                        ],
                    )
                )
                .groupby(level=0)
                .sum()
            )

            grid_injection_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=grid_injection_series_in_watt[grid_injection_series_in_watt > 0],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            )

            self_consumption_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=self_consumption_series_in_watt,
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            )

            # compute self consumption rate and autarkie rate
            self_consumption_rate_in_percent = 100 * (
                self_consumption_in_kilowatt_hour / electricity_production_in_kilowatt_hour
            )
            autarky_rate_in_percent = 100 * (
                self_consumption_in_kilowatt_hour / electricity_consumption_in_kilowatt_hour
            )
            if autarky_rate_in_percent > 100:
                raise ValueError(
                    "The autarky rate should not be over 100 %. Something is wrong here. Please check your code."
                )

        else:
            self_consumption_series_in_watt = pd.Series([])
            grid_injection_series_in_watt = pd.Series([])
            self_consumption_in_kilowatt_hour = 0
            grid_injection_in_kilowatt_hour = 0
            self_consumption_rate_in_percent = 0
            autarky_rate_in_percent = 0

        # add injection and self-consumption timeseries to result dataframe
        result_dataframe["self_consumption_in_watt"] = self_consumption_series_in_watt
        result_dataframe["grid_injection_in_watt"] = grid_injection_series_in_watt

        # make kpi entry
        grid_injection_entry = KpiEntry(
            name="Grid injection", unit="kWh", value=grid_injection_in_kilowatt_hour, tag=KpiTagEnumClass.GENERAL
        )
        self_consumption_entry = KpiEntry(
            name="Self-consumption", unit="kWh", value=self_consumption_in_kilowatt_hour, tag=KpiTagEnumClass.GENERAL
        )
        self_consumption_rate_entry = KpiEntry(
            name="Self-consumption rate", unit="%", value=self_consumption_rate_in_percent, tag=KpiTagEnumClass.GENERAL
        )
        autarkie_rate_entry = KpiEntry(
            name="Autarky rate", unit="%", value=autarky_rate_in_percent, tag=KpiTagEnumClass.GENERAL
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                grid_injection_entry.name: grid_injection_entry.to_dict(),
                self_consumption_entry.name: self_consumption_entry.to_dict(),
                self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict(),
                autarkie_rate_entry.name: autarkie_rate_entry.to_dict(),
            }
        )
        return grid_injection_in_kilowatt_hour, self_consumption_in_kilowatt_hour, result_dataframe

    def compute_battery_kpis(self, result_dataframe: pd.DataFrame) -> Tuple[float, float, float]:
        """Compute battery kpis."""

        if not result_dataframe["battery_charge"].empty:
            battery_charging_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["battery_charge"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            )
            battery_discharging_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["battery_discharge"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            )
            battery_losses_in_kilowatt_hour = (
                battery_charging_energy_in_kilowatt_hour - battery_discharging_energy_in_kilowatt_hour
            )
        else:
            battery_charging_energy_in_kilowatt_hour = 0.0
            battery_discharging_energy_in_kilowatt_hour = 0.0
            battery_losses_in_kilowatt_hour = 0.0

        return (
            battery_charging_energy_in_kilowatt_hour,
            battery_discharging_energy_in_kilowatt_hour,
            battery_losses_in_kilowatt_hour,
        )

    def get_electricity_to_and_from_grid_from_electricty_meter(self) -> Tuple[Optional[float], Optional[float]]:
        """Get the electricity injected into the grid or taken from grid measured by the electricity meter."""
        total_energy_from_grid_in_kwh = None
        total_energy_to_grid_in_kwh = None
        # go through all wrapped components and try to find electricity meter
        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, ElectricityMeter):
                wrapped_electricity_meter_component = wrapped_component
                break

        if not wrapped_electricity_meter_component:
            log.information("Could not find the Electricity Meter component.")
            return None, None

        for column in self.results.columns:

            if all(x in column.split(sep=" ") for x in [wrapped_electricity_meter_component.my_component.component_name]):
                for string in column.split(sep=" "):

                    if "ElectricityToGrid" in string.split(sep="_") :
                        total_energy_to_grid_in_kwh = (self.results[column].loc[self.results[column] > 0.0]).sum() / 1000

                    if "ElectricityFromGrid" in string.split(sep="_") :
                        total_energy_from_grid_in_kwh = (self.results[column].loc[self.results[column] > 0.0]).sum() / 1000

        if total_energy_from_grid_in_kwh is None and total_energy_to_grid_in_kwh is None:
            log.warning(
                "KPI values for total energy to and from grid are None. "
                "Please check if you have correctly initialized and connected the electricity meter in your system setup. "
            )
        # make kpi entry
        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total energy from grid", unit="kWh", value=total_energy_from_grid_in_kwh, tag=KpiTagEnumClass.GENERAL
        )
        total_energy_to_grid_in_kwh_entry = KpiEntry(
            name="Total energy to grid", unit="kWh", value=total_energy_to_grid_in_kwh, tag=KpiTagEnumClass.GENERAL
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                total_energy_from_grid_in_kwh_entry.name: total_energy_from_grid_in_kwh_entry.to_dict(),
                total_energy_to_grid_in_kwh_entry.name: total_energy_to_grid_in_kwh_entry.to_dict(),
            }
        )
        return total_energy_from_grid_in_kwh, total_energy_to_grid_in_kwh

    def compute_relative_electricity_demand(
        self,
        total_electricity_consumption_in_kilowatt_hour: float,
        electricity_from_grid_in_kilowatt_hour: Optional[float],
    ) -> Optional[float]:
        """Return the relative electricity demand."""
        if electricity_from_grid_in_kilowatt_hour is None:
            relative_electricity_demand_from_grid_in_percent = None
        else:
            relative_electricity_demand_from_grid_in_percent = (
                round(electricity_from_grid_in_kilowatt_hour, 2)
                / round(total_electricity_consumption_in_kilowatt_hour, 2)
                * 100
            )
            if relative_electricity_demand_from_grid_in_percent > 100:
                raise ValueError(
                    "The relative elecricity demand should not be over 100 %. Something is wrong here. Please check your code."
                    f"Electricity from grid {electricity_from_grid_in_kilowatt_hour} kWh, "
                    f"total electricity consumption {total_electricity_consumption_in_kilowatt_hour} kWh."
                )

        # make kpi entry
        relative_electricity_demand_entry = KpiEntry(
            name="Relative electricity demand from grid",
            unit="%",
            value=relative_electricity_demand_from_grid_in_percent,
            tag=KpiTagEnumClass.GENERAL,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {relative_electricity_demand_entry.name: relative_electricity_demand_entry.to_dict()}
        )
        return relative_electricity_demand_from_grid_in_percent

    def compute_autarky_according_to_solar_htw_berlin(
        self, relative_electricty_demand_in_percent: Optional[float],
    ) -> None:
        """Return the autarky rate according to solar htw berlin.

        https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
        """
        if relative_electricty_demand_in_percent is None:
            autraky_rate_in_percent = None
        else:
            autraky_rate_in_percent = 100 - relative_electricty_demand_in_percent
            if autraky_rate_in_percent > 100:
                raise ValueError(
                    "The autarky rate should not be over 100 %. Something is wrong here. Please check your code. "
                    f"The realtive electricity demand is {relative_electricty_demand_in_percent} %. "
                )

        # make kpi entry
        autarky_rate_entry = KpiEntry(
            name="Autarky rate according to solar htw berlin",
            unit="%",
            value=autraky_rate_in_percent,
            tag=KpiTagEnumClass.GENERAL,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update({autarky_rate_entry.name: autarky_rate_entry.to_dict()})

    def compute_ratio_between_two_values_and_set_as_kpi(
        self, denominator_value: float, numerator_value: float, kpi_name: str
    ) -> None:
        """Compute the ratio of two values.

        ratio = denominator / numerator * 100 [%].
        """
        ratio_in_percent = denominator_value / numerator_value * 100
        # make kpi entry
        ratio_in_percent_entry = KpiEntry(name=kpi_name, unit="%", value=ratio_in_percent, tag=KpiTagEnumClass.GENERAL)

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update({ratio_in_percent_entry.name: ratio_in_percent_entry.to_dict()})

    def compute_self_consumption_rate_according_to_solar_htw_berlin(
        self,
        total_electricity_production_in_kilowatt_hour: float,
        electricity_to_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Return self-consumption according to solar htw berlin.

        https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
        https://academy.dualsun.com/hc/en-us/articles/360018456939-How-is-the-self-consumption-rate-calculated-on-MyDualSun.
        """
        if electricity_to_grid_in_kilowatt_hour is None or total_electricity_production_in_kilowatt_hour == 0:
            self_consumption_rate_in_percent = None
        else:
            self_consumption_rate_in_percent = (
                (total_electricity_production_in_kilowatt_hour - electricity_to_grid_in_kilowatt_hour)
                / total_electricity_production_in_kilowatt_hour
                * 100
            )
            if self_consumption_rate_in_percent > 100:
                raise ValueError(
                    "The self-consumption rate should not be over 100 %. Something is wrong here. Please check your code."
                    f"Electricity to grid {electricity_to_grid_in_kilowatt_hour} kWh, "
                    f"total electricity production {total_electricity_production_in_kilowatt_hour} kWh."
                )

        # make kpi entry
        self_consumption_rate_entry = KpiEntry(
            name="Self-consumption rate according to solar htw berlin",
            unit="%",
            value=self_consumption_rate_in_percent,
            tag=KpiTagEnumClass.GENERAL,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict()}
        )

    def read_in_fuel_costs(self) -> pd.DataFrame:
        """Reads data for costs and co2 emissions of fuels from csv."""
        price_frame = pd.read_csv(HISIMPATH["fuel_costs"], sep=";", usecols=[0, 2, 4])
        price_frame.index = price_frame["fuel type"]  # type: ignore
        price_frame.drop(columns=["fuel type"], inplace=True)
        return price_frame

    def search_electricity_prices_in_results(
        self, all_outputs: List, results: pd.DataFrame
    ) -> Tuple["pd.Series[float]", "pd.Series[float]"]:
        """Extracts electricity price consumption and electricity price production from results."""
        electricity_price_consumption = pd.Series(dtype=pd.Float64Dtype())  # type: pd.Series[float]
        electricity_price_injection = pd.Series(dtype=pd.Float64Dtype())  # type: pd.Series[float]
        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None:
                if LoadTypes.PRICE in output.postprocessing_flag:
                    if InandOutputType.ELECTRICITY_CONSUMPTION in output.postprocessing_flag:
                        electricity_price_consumption = results.iloc[:, index]
                    elif InandOutputType.ELECTRICITY_INJECTION in output.postprocessing_flag:
                        electricity_price_injection = results.iloc[:, index]
                    else:
                        continue
        return electricity_price_consumption, electricity_price_injection

    def get_euro_and_co2(
        self, fuel_costs: pd.DataFrame, fuel: Union[LoadTypes, InandOutputType]
    ) -> Tuple[float, float]:
        """Returns cost (Euro) of kWh of fuel and CO2 consumption (kg) of kWh of fuel."""
        column = fuel_costs.iloc[fuel_costs.index == fuel.value]
        return (float(column["Cost"].iloc[0]), float(column["Footprint"].iloc[0]))

    def compute_cost_of_fuel_type(
        self, results: pd.DataFrame, all_outputs: List, timeresolution: int, price_frame: pd.DataFrame, fuel: LoadTypes,
    ) -> Tuple[float, float]:
        """Computes the cost of the fuel type."""
        fuel_consumption = pd.Series(dtype=pd.Float64Dtype())  # type: pd.Series[float]
        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None:
                if InandOutputType.FUEL_CONSUMPTION in output.postprocessing_flag:
                    if fuel in output.postprocessing_flag:
                        fuel_consumption = results.iloc[:, index]
                    else:
                        continue
        if not fuel_consumption.empty:
            if fuel in [LoadTypes.ELECTRICITY, LoadTypes.GAS, LoadTypes.DISTRICTHEATING]:
                consumption_sum = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=fuel_consumption, timeresolution=timeresolution
                )
            # convert from Wh to kWh
            elif fuel in [LoadTypes.GAS, LoadTypes.DISTRICTHEATING]:
                consumption_sum = sum(fuel_consumption) * 1e-3
            # stay with liters
            else:
                consumption_sum = sum(fuel_consumption)
        else:
            consumption_sum = 0

        price, co2 = self.get_euro_and_co2(fuel_costs=price_frame, fuel=fuel)
        return consumption_sum * price, consumption_sum * co2

    def compute_energy_prices_and_co2_emission(
        self,
        result_dataframe: pd.DataFrame,
        injection: pd.Series,
        self_consumption: pd.Series,
        electricity_production_in_kilowatt_hour: float,
        electricity_consumption_in_kilowatt_hour: float,
        grid_injection_in_kilowatt_hour: float,
        self_consumption_in_kilowatt_hour: float,
    ) -> None:
        """Compute energy prices and co2 emissions."""

        # initialize prices
        costs_for_energy_use_in_euro = 0.0
        co2_emitted_due_to_energy_use_in_kilogram = 0.0

        price_frame = self.read_in_fuel_costs()

        (electricity_price_consumption, electricity_price_injection,) = self.search_electricity_prices_in_results(
            all_outputs=self.all_outputs, results=self.results
        )
        # Electricity Price
        electricity_price_constant, co2_price_constant = self.get_euro_and_co2(
            fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY
        )
        electricity_inj_price_constant, _ = self.get_euro_and_co2(fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY)

        if electricity_production_in_kilowatt_hour > 0:
            # evaluate electricity price
            if not electricity_price_injection.empty:
                costs_for_energy_use_in_euro = (
                    costs_for_energy_use_in_euro
                    - self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=injection[injection > 0] * electricity_price_injection[injection > 0],
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )
                costs_for_energy_use_in_euro = (
                    costs_for_energy_use_in_euro
                    + self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=result_dataframe["total_consumption"] - self_consumption,
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )  # Todo: is this correct? (maybe not so important, only used if generic_price_signal is used
            else:
                costs_for_energy_use_in_euro = (
                    costs_for_energy_use_in_euro
                    - grid_injection_in_kilowatt_hour * electricity_inj_price_constant
                    + (electricity_consumption_in_kilowatt_hour - self_consumption_in_kilowatt_hour)
                    * electricity_price_constant
                )

        else:
            if not electricity_price_consumption.empty:
                # substract self consumption from consumption for bill calculation
                costs_for_energy_use_in_euro = (
                    costs_for_energy_use_in_euro
                    + self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=result_dataframe["total_consumption"] * electricity_price_consumption,
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )
            else:
                costs_for_energy_use_in_euro = (
                    costs_for_energy_use_in_euro + electricity_consumption_in_kilowatt_hour * electricity_price_constant
                )

        co2_emitted_due_to_energy_use_in_kilogram = (
            co2_emitted_due_to_energy_use_in_kilogram
            + (electricity_consumption_in_kilowatt_hour - self_consumption_in_kilowatt_hour) * co2_price_constant
        )

        # compute cost and co2 for LoadTypes other than electricity
        for fuel in [
            LoadTypes.GAS,
            LoadTypes.OIL,
            LoadTypes.DISTRICTHEATING,
            LoadTypes.DIESEL,
        ]:
            fuel_price, fuel_co2 = self.compute_cost_of_fuel_type(
                results=self.results,
                all_outputs=self.all_outputs,
                timeresolution=self.simulation_parameters.seconds_per_timestep,
                price_frame=price_frame,
                fuel=fuel,
            )
            co2_emitted_due_to_energy_use_in_kilogram = co2_emitted_due_to_energy_use_in_kilogram + fuel_co2
            costs_for_energy_use_in_euro = costs_for_energy_use_in_euro + fuel_price

        # make kpi entry
        costs_for_energy_use_entry = KpiEntry(
            name="Cost for energy use",
            unit="EUR",
            value=costs_for_energy_use_in_euro,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        co2_emission_entry = KpiEntry(
            name="CO2 emission due to energy use",
            unit="kg",
            value=co2_emitted_due_to_energy_use_in_kilogram,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                costs_for_energy_use_entry.name: costs_for_energy_use_entry.to_dict(),
                co2_emission_entry.name: co2_emission_entry.to_dict(),
            }
        )

    def read_opex_and_capex_costs_from_results(self):
        """Get CAPEX and OPEX costs for simulated period.

        This function will read the opex and capex costs from the results.
        """
        # get CAPEX and OPEX costs for simulated period
        capex_results_path = os.path.join(
            self.simulation_parameters.result_directory, "investment_cost_co2_footprint.csv"
        )
        opex_results_path = os.path.join(
            self.simulation_parameters.result_directory, "operational_costs_co2_footprint.csv"
        )
        if Path(opex_results_path).exists():
            opex_df = pd.read_csv(opex_results_path, index_col=0)
            total_operational_cost_per_simulated_period = opex_df["Operational Costs in EUR"].iloc[-1]
            total_operational_emissions_per_simulated_period = opex_df["Operational C02 footprint in kg"].iloc[-1]
        else:
            log.warning("OPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_OPEX")
            total_operational_cost_per_simulated_period = 0
            total_operational_emissions_per_simulated_period = 0

        if Path(capex_results_path).exists():
            capex_df = pd.read_csv(capex_results_path, index_col=0)
            total_investment_cost_per_simulated_period = capex_df["Investment in EUR"].iloc[-1]
            total_device_co2_footprint_per_simulated_period = capex_df["Device CO2-footprint in kg"].iloc[-1]
        else:
            log.warning("CAPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_CAPEX")
            total_investment_cost_per_simulated_period = 0
            total_device_co2_footprint_per_simulated_period = 0

        # make kpi entry
        total_investment_cost_per_simulated_period_entry = KpiEntry(
            name="Investment costs for equipment per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        total_device_co2_footprint_per_simulated_period_entry = KpiEntry(
            name="CO2 footprint for equipment per simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        total_operational_cost_entry = KpiEntry(
            name="System operational costs for simulated period",
            unit="EUR",
            value=total_operational_cost_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        total_operational_emissions_entry = KpiEntry(
            name="System operational emissions for simulated period",
            unit="kg",
            value=total_operational_emissions_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        total_cost_entry = KpiEntry(
            name="Total costs for simulated period",
            unit="EUR",
            value=total_operational_cost_per_simulated_period + total_investment_cost_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        total_emissions_entry = KpiEntry(
            name="Total CO2 emissions for simulated period",
            unit="kg",
            value=total_operational_emissions_per_simulated_period + total_device_co2_footprint_per_simulated_period,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                total_investment_cost_per_simulated_period_entry.name: total_investment_cost_per_simulated_period_entry.to_dict(),
                total_device_co2_footprint_per_simulated_period_entry.name: total_device_co2_footprint_per_simulated_period_entry.to_dict(),
                total_operational_cost_entry.name: total_operational_cost_entry.to_dict(),
                total_operational_emissions_entry.name: total_operational_emissions_entry.to_dict(),
                total_cost_entry.name: total_cost_entry.to_dict(),
                total_emissions_entry.name: total_emissions_entry.to_dict(),
            }
        )

    def get_building_kpis(self,) -> float:
        """Check building kpi values.

        Check for all timesteps and count the
        time when the temperature is outside of the building set temperatures
        in order to verify if energy system provides enough heating and cooling.
        """

        wrapped_building_component = None
        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, Building):
                wrapped_building_component = wrapped_component
                break
        if not wrapped_building_component:
            raise ValueError("Could not find the Building component.")

        # get heating load and heating ref temperature
        heating_load_in_watt = getattr(
            wrapped_building_component.my_component, "my_building_information"
        ).max_thermal_building_demand_in_watt

        # get building area
        scaled_conditioned_floor_area_in_m2 = float(
            getattr(
                wrapped_building_component.my_component, "my_building_information"
            ).scaled_conditioned_floor_area_in_m2
        )
        # get rooftop area
        scaled_rooftop_area_in_m2 = float(
            getattr(wrapped_building_component.my_component, "my_building_information").scaled_rooftop_area_in_m2
        )

        # get specific heating load
        specific_heating_load_in_watt_per_m2 = heating_load_in_watt / scaled_conditioned_floor_area_in_m2

        # make kpi entry

        heating_load_in_watt_entry = KpiEntry(
            name="Building heating load", unit="W", value=heating_load_in_watt, tag=KpiTagEnumClass.BUILDING
        )
        scaled_conditioned_floor_area_in_m2_entry = KpiEntry(
            name="Conditioned floor area",
            unit="m2",
            value=scaled_conditioned_floor_area_in_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        scaled_rooftop_area_in_m2_entry = KpiEntry(
            name="Rooftop area", unit="m2", value=scaled_rooftop_area_in_m2, tag=KpiTagEnumClass.BUILDING,
        )
        specific_heating_load_in_watt_per_m2_entry = KpiEntry(
            name="Specific heating load",
            unit="W/m2",
            value=specific_heating_load_in_watt_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                heating_load_in_watt_entry.name: heating_load_in_watt_entry.to_dict(),
                scaled_conditioned_floor_area_in_m2_entry.name: scaled_conditioned_floor_area_in_m2_entry.to_dict(),
                specific_heating_load_in_watt_per_m2_entry.name: specific_heating_load_in_watt_per_m2_entry.to_dict(),
                scaled_rooftop_area_in_m2_entry.name: scaled_rooftop_area_in_m2_entry.to_dict(),
            }
        )
        # get building temperatures
        self.get_building_temperature_deviation_from_set_temperatures(
            results=self.results, component_name=wrapped_building_component.my_component
        )

        # get building tabula ref values
        self.get_building_tabula_reference_values(component_name=wrapped_building_component.my_component)

        # get building output kpis
        self.get_building_outputs(
            results=self.results, building_conditioned_floor_area_in_m2=scaled_conditioned_floor_area_in_m2
        )

        return scaled_conditioned_floor_area_in_m2

    def get_building_temperature_deviation_from_set_temperatures(
        self, results: pd.DataFrame, component_name: str
    ) -> None:
        """Check building temperatures.

        Check for all timesteps and count the
        time when the temperature is outside of the building set temperatures
        in order to verify if energy system provides enough heating and cooling.
        """

        temperature_difference_of_building_being_below_heating_set_temperature = 0
        temperature_difference_of_building_being_below_cooling_set_temperature = 0
        temperature_hours_of_building_being_below_heating_set_temperature = None
        temperature_hours_of_building_being_above_cooling_set_temperature = None
        min_temperature_reached_in_celsius = None
        max_temperature_reached_in_celsius = None

        # get set temperatures
        set_heating_temperature_in_celsius = getattr(component_name, "set_heating_temperature_in_celsius")
        set_cooling_temperature_in_celsius = getattr(component_name, "set_cooling_temperature_in_celsius")

        for column in results.columns:
            if all(x in column.split(sep=" ") for x in [Building.TemperatureIndoorAir]):
                for temperature in results[column].values:
                    if temperature < set_heating_temperature_in_celsius:
                        temperature_difference_heating = set_heating_temperature_in_celsius - temperature

                        temperature_difference_of_building_being_below_heating_set_temperature = (
                            temperature_difference_of_building_being_below_heating_set_temperature
                            + temperature_difference_heating
                        )

                    elif temperature > set_cooling_temperature_in_celsius:
                        temperature_difference_cooling = temperature - set_cooling_temperature_in_celsius
                        temperature_difference_of_building_being_below_cooling_set_temperature = (
                            temperature_difference_of_building_being_below_cooling_set_temperature
                            + temperature_difference_cooling
                        )

                temperature_hours_of_building_being_below_heating_set_temperature = (
                    temperature_difference_of_building_being_below_heating_set_temperature
                    * self.simulation_parameters.seconds_per_timestep
                    / 3600
                )

                temperature_hours_of_building_being_above_cooling_set_temperature = (
                    temperature_difference_of_building_being_below_cooling_set_temperature
                    * self.simulation_parameters.seconds_per_timestep
                    / 3600
                )

                # get also max and min indoor air temperature
                min_temperature_reached_in_celsius = float(min(results[column].values))
                max_temperature_reached_in_celsius = float(max(results[column].values))
                break

        # make kpi entry
        temperature_hours_of_building_below_heating_set_temperature_entry = KpiEntry(
            name=f"Temperature deviation of building indoor air temperature being below set temperature {set_heating_temperature_in_celsius} Celsius",
            unit="°C*h",
            value=temperature_hours_of_building_being_below_heating_set_temperature,
            tag=KpiTagEnumClass.BUILDING,
        )
        temperature_hours_of_building_above_cooling_set_temperature_entry = KpiEntry(
            name=f"Temperature deviation of building indoor air temperature being above set temperature {set_cooling_temperature_in_celsius} Celsius",
            unit="°C*h",
            value=temperature_hours_of_building_being_above_cooling_set_temperature,
            tag=KpiTagEnumClass.BUILDING,
        )
        min_temperature_reached_in_celsius_entry = KpiEntry(
            name="Minimum building indoor air temperature reached",
            unit="°C",
            value=min_temperature_reached_in_celsius,
            tag=KpiTagEnumClass.BUILDING,
        )
        max_temperature_reached_in_celsius_entry = KpiEntry(
            name="Maximum building indoor air temperature reached",
            unit="°C",
            value=max_temperature_reached_in_celsius,
            tag=KpiTagEnumClass.BUILDING,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                temperature_hours_of_building_below_heating_set_temperature_entry.name: temperature_hours_of_building_below_heating_set_temperature_entry.to_dict(),
                temperature_hours_of_building_above_cooling_set_temperature_entry.name: temperature_hours_of_building_above_cooling_set_temperature_entry.to_dict(),
                min_temperature_reached_in_celsius_entry.name: min_temperature_reached_in_celsius_entry.to_dict(),
                max_temperature_reached_in_celsius_entry.name: max_temperature_reached_in_celsius_entry.to_dict(),
            }
        )

    def get_building_tabula_reference_values(self, component_name: str) -> None:
        """Check tabula reference values."""

        # get tabula reference value for energy need in kWh per m2 / a
        energy_need_for_heating_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            component_name, "my_building_information"
        ).energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year
        # Q_h_tr
        transmission_heat_loss_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            component_name, "my_building_information"
        ).transmission_heat_losses_ref_in_kilowatthour_per_m2_per_year
        # Q_ht_ve
        ventilation_heat_loss_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            component_name, "my_building_information"
        ).ventilation_heat_losses_ref_in_kilowatthour_per_m2_per_year
        # Q_sol
        solar_gains_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            component_name, "my_building_information"
        ).solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year
        # Q_int
        internal_gains_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            component_name, "my_building_information"
        ).internal_heat_sources_reference_in_kilowatthour_per_m2_per_year
        # eta_h_gn
        gain_utilisation_factor_tabula_ref = getattr(
            component_name, "my_building_information"
        ).gain_utilisation_factor_reference

        # make kpi entry
        specific_heat_demand_from_tabula_in_kwh_per_m2a_entry = KpiEntry(
            name="Specific heating demand according to TABULA",
            unit="kWh/m2a",
            value=energy_need_for_heating_in_kilowatthour_per_m2_per_year_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_heat_loss_transmission_from_tabula_in_kwh_per_m2a_entry = KpiEntry(
            name="Specific transmission heat loss according to TABULA",
            unit="kWh/m2a",
            value=transmission_heat_loss_in_kilowatthour_per_m2_per_year_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_heat_loss_ventilation_from_tabula_in_kwh_per_m2a_entry = KpiEntry(
            name="Specific ventilation heat loss according to TABULA",
            unit="kWh/m2a",
            value=ventilation_heat_loss_in_kilowatthour_per_m2_per_year_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_solar_gains_from_tabula_in_kwh_per_m2a_entry = KpiEntry(
            name="Specific solar gains according to TABULA",
            unit="kWh/m2a",
            value=solar_gains_in_kilowatthour_per_m2_per_year_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_internal_gains_from_tabula_in_kwh_per_m2a_entry = KpiEntry(
            name="Specific internal gains according to TABULA",
            unit="kWh/m2a",
            value=internal_gains_in_kilowatthour_per_m2_per_year_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )
        gain_utilisation_factor_tabula_ref_entry = KpiEntry(
            name="Building gain utilisation factor according to TABULA",
            unit="-",
            value=gain_utilisation_factor_tabula_ref,
            tag=KpiTagEnumClass.BUILDING,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                specific_heat_demand_from_tabula_in_kwh_per_m2a_entry.name: specific_heat_demand_from_tabula_in_kwh_per_m2a_entry.to_dict(),
                specific_heat_loss_transmission_from_tabula_in_kwh_per_m2a_entry.name: specific_heat_loss_transmission_from_tabula_in_kwh_per_m2a_entry.to_dict(),
                specific_heat_loss_ventilation_from_tabula_in_kwh_per_m2a_entry.name: specific_heat_loss_ventilation_from_tabula_in_kwh_per_m2a_entry.to_dict(),
                specific_solar_gains_from_tabula_in_kwh_per_m2a_entry.name: specific_solar_gains_from_tabula_in_kwh_per_m2a_entry.to_dict(),
                specific_internal_gains_from_tabula_in_kwh_per_m2a_entry.name: specific_internal_gains_from_tabula_in_kwh_per_m2a_entry.to_dict(),
                gain_utilisation_factor_tabula_ref_entry.name: gain_utilisation_factor_tabula_ref_entry.to_dict(),
            }
        )

    def get_building_outputs(self, results: pd.DataFrame, building_conditioned_floor_area_in_m2: float) -> None:
        """Get KPIs for building outputs."""

        specific_heat_loss_from_transmission_in_kilowatt_hour_per_m2 = None
        specific_heat_loss_from_ventilation_in_kilowatt_hour_per_m2 = None
        specific_solar_gains_in_kilowatt_hour_per_m2 = None
        specific_internal_heat_gains_in_kilowatt_hour_per_m2 = None
        specific_heat_demand_calculated_with_tabula_method_in_kilowatthour_per_m2 = None

        for column in results.columns:
            # get ouputs in watt and transform to kwh
            if all(x in column.split(sep=" ") for x in [Building.HeatLossFromTransmission]):
                heat_loss_from_transmission_values_in_watt = results[column]
                # get energy from power
                energy_loss_from_transmission_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=heat_loss_from_transmission_values_in_watt,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
                specific_heat_loss_from_transmission_in_kilowatt_hour_per_m2 = (
                    energy_loss_from_transmission_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                )

            if all(x in column.split(sep=" ") for x in [Building.HeatLossFromVentilation]):
                heat_loss_from_ventilation_values_in_watt = results[column]
                # get energy from power
                energy_loss_from_ventilation_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=heat_loss_from_ventilation_values_in_watt,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
                specific_heat_loss_from_ventilation_in_kilowatt_hour_per_m2 = (
                    energy_loss_from_ventilation_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                )

            if all(x in column.split(sep=" ") for x in [Building.SolarGainThroughWindows]):
                solar_gains_values_in_watt = results[column]
                # get energy from power
                energy_gains_from_solar_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=solar_gains_values_in_watt,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
                specific_solar_gains_in_kilowatt_hour_per_m2 = (
                    energy_gains_from_solar_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                )

            if all(x in column.split(sep=" ") for x in [Building.InternalHeatGainsFromOccupancy]):
                internal_gains_values_in_watt = results[column]
                # get energy from power
                energy_gains_from_internal_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=internal_gains_values_in_watt,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
                specific_internal_heat_gains_in_kilowatt_hour_per_m2 = (
                    energy_gains_from_internal_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                )

            if all(x in column.split(sep=" ") for x in [Building.HeatDemandAccordingToTabula]):
                heat_demand_values_in_watt = results[column]
                # get energy from power
                energy_demand_calculated_based_on_tabula_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=heat_demand_values_in_watt,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
                specific_heat_demand_calculated_with_tabula_method_in_kilowatthour_per_m2 = (
                    energy_demand_calculated_based_on_tabula_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                )

        specific_energy_loss_from_transmission_entry = KpiEntry(
            name="Specific energy transfer from transmission",
            unit="kWh/m2",
            value=specific_heat_loss_from_transmission_in_kilowatt_hour_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_energy_loss_from_ventilation_entry = KpiEntry(
            name="Specific energy transfer from ventilation",
            unit="kWh/m2",
            value=specific_heat_loss_from_ventilation_in_kilowatt_hour_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_energy_gains_from_solar_entry = KpiEntry(
            name="Specific solar energy gains",
            unit="kWh/m2",
            value=specific_solar_gains_in_kilowatt_hour_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_energy_gains_from_internal_entry = KpiEntry(
            name="Specific internal energy gains",
            unit="kWh/m2",
            value=specific_internal_heat_gains_in_kilowatt_hour_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        specific_heat_demand_calculated_entry = KpiEntry(
            name="Specific heat demand calculated based on TABULA",
            unit="kWh/m2",
            value=specific_heat_demand_calculated_with_tabula_method_in_kilowatthour_per_m2,
            tag=KpiTagEnumClass.BUILDING,
        )
        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                specific_energy_loss_from_transmission_entry.name: specific_energy_loss_from_transmission_entry.to_dict(),
                specific_energy_loss_from_ventilation_entry.name: specific_energy_loss_from_ventilation_entry.to_dict(),
                specific_energy_gains_from_solar_entry.name: specific_energy_gains_from_solar_entry.to_dict(),
                specific_energy_gains_from_internal_entry.name: specific_energy_gains_from_internal_entry.to_dict(),
                specific_heat_demand_calculated_entry.name: specific_heat_demand_calculated_entry.to_dict(),
            }
        )

    def get_heat_distribution_system_kpis(self, building_conditioned_floor_area_in_m2: float) -> None:
        """Get KPIs from heat distriution system like thermal energy delivered."""

        thermal_output_energy_in_kilowatt_hour = None
        specific_thermal_output_energy_in_kilowatt_hour = None
        mean_flow_temperature_in_celsius = None
        mean_return_temperature_in_celsius = None
        mean_temperature_difference_between_flow_and_return_in_celsius = None
        min_flow_temperature_in_celsius = None
        min_return_temperature_in_celsius = None
        min_temperature_difference_between_flow_and_return_in_celsius = None
        max_flow_temperature_in_celsius = None
        max_return_temperature_in_celsius = None
        max_temperature_difference_between_flow_and_return_in_celsius = None

        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, HeatDistribution):
                wrapped_hds_component = wrapped_component
                for column in self.results.columns:
                    if all(
                        x in column.split(sep=" ")
                        for x in [
                            HeatDistribution.ThermalPowerDelivered,
                            wrapped_hds_component.my_component.component_name,
                        ]
                    ):
                        # take only output values for heating
                        thermal_output_power_values_in_watt = self.results[column].loc[self.results[column] > 0.0]
                        # get energy from power
                        thermal_output_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=thermal_output_power_values_in_watt,
                            timeresolution=self.simulation_parameters.seconds_per_timestep,
                        )
                        specific_thermal_output_energy_in_kilowatt_hour = (
                            thermal_output_energy_in_kilowatt_hour / building_conditioned_floor_area_in_m2
                        )

                break

        # get flow and return temperatures
        (
            mean_flow_temperature_in_celsius,
            mean_return_temperature_in_celsius,
            mean_temperature_difference_between_flow_and_return_in_celsius,
            max_flow_temperature_in_celsius,
            max_return_temperature_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            min_flow_temperature_in_celsius,
            min_return_temperature_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
        ) = self.get_flow_and_return_temperatures(
            results=self.results,
            output_name_flow_temperature=HeatDistribution.WaterTemperatureInlet,
            output_name_return_temperature=HeatDistribution.WaterTemperatureOutput,
        )

        if None in (
            thermal_output_energy_in_kilowatt_hour,
            specific_thermal_output_energy_in_kilowatt_hour,
            mean_temperature_difference_between_flow_and_return_in_celsius,
            mean_flow_temperature_in_celsius,
            mean_return_temperature_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
            min_flow_temperature_in_celsius,
            min_return_temperature_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            max_flow_temperature_in_celsius,
            max_return_temperature_in_celsius,
        ):
            log.warning(
                "KPI values for heat distribution system are None. "
                "Please check if you have correctly initialized and connected the hds in your system setup or ignore this message."
            )

        thermal_output_energy_hds_entry = KpiEntry(
            name="Thermal output energy of heat distribution system",
            unit="kWh",
            value=thermal_output_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        specific_thermal_output_energy_hds_entry = KpiEntry(
            name="Specific thermal output energy of heat distribution system",
            unit="kWh/m2",
            value=specific_thermal_output_energy_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        mean_flow_temperature_hds_entry = KpiEntry(
            name="Mean flow temperature of heat distribution system",
            unit="°C",
            value=mean_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        mean_return_temperature_hds_entry = KpiEntry(
            name="Mean return temperature of heat distribution system",
            unit="°C",
            value=mean_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        mean_temperature_difference_hds_entry = KpiEntry(
            name="Mean temperature difference of heat distribution system",
            unit="°C",
            value=mean_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        max_flow_temperature_hds_entry = KpiEntry(
            name="Max flow temperature of heat distribution system",
            unit="°C",
            value=max_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        max_return_temperature_hds_entry = KpiEntry(
            name="Max return temperature of heat distribution system",
            unit="°C",
            value=max_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        max_temperature_difference_hds_entry = KpiEntry(
            name="Max temperature difference of heat distribution system",
            unit="°C",
            value=max_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        min_flow_temperature_hds_entry = KpiEntry(
            name="Min flow temperature of heat distribution system",
            unit="°C",
            value=min_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        min_return_temperature_hds_entry = KpiEntry(
            name="Min return temperature of heat distribution system",
            unit="°C",
            value=min_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        min_temperature_difference_hds_entry = KpiEntry(
            name="Min temperature difference of heat distribution system",
            unit="°C",
            value=min_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATDISTRIBUTIONSYSTEM,
        )
        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                thermal_output_energy_hds_entry.name: thermal_output_energy_hds_entry.to_dict(),
                specific_thermal_output_energy_hds_entry.name: specific_thermal_output_energy_hds_entry.to_dict(),
                mean_flow_temperature_hds_entry.name: mean_flow_temperature_hds_entry.to_dict(),
                mean_return_temperature_hds_entry.name: mean_return_temperature_hds_entry.to_dict(),
                mean_temperature_difference_hds_entry.name: mean_temperature_difference_hds_entry.to_dict(),
                max_flow_temperature_hds_entry.name: max_flow_temperature_hds_entry.to_dict(),
                max_return_temperature_hds_entry.name: max_return_temperature_hds_entry.to_dict(),
                max_temperature_difference_hds_entry.name: max_temperature_difference_hds_entry.to_dict(),
                min_flow_temperature_hds_entry.name: min_flow_temperature_hds_entry.to_dict(),
                min_return_temperature_hds_entry.name: min_return_temperature_hds_entry.to_dict(),
                min_temperature_difference_hds_entry.name: min_temperature_difference_hds_entry.to_dict(),
            }
        )

    def get_heatpump_cycles(self, results: pd.DataFrame, component_name: str) -> float:
        """Get the number of cycles of the heat pump for the simulated period."""
        number_of_cycles = 0
        for column in results.columns:

            if all(x in column.split(sep=" ") for x in [MoreAdvancedHeatPumpHPLib.TimeOff, component_name]) or all(
                x in column.split(sep=" ") for x in [HeatPumpHplib.TimeOff, component_name]
            ):
                for index, off_time in enumerate(results[column].values):
                    try:
                        if off_time != 0 and results[column].values[index + 1] == 0:
                            number_of_cycles = number_of_cycles + 1

                    except Exception:
                        pass

        return number_of_cycles

    def get_flow_and_return_temperatures(
        self, results: pd.DataFrame, output_name_flow_temperature: str, output_name_return_temperature: str
    ) -> Tuple[float, float, float, float, float, float, float, float, float]:
        """Get the flow and return temperatures of the heat pump for the simulated period."""
        flow_temperature_list_in_celsius = pd.Series([])
        return_temperature_list_in_celsius = pd.Series([])
        for column in results.columns:

            if all(x in column.split(sep=" ") for x in [output_name_flow_temperature]):
                flow_temperature_list_in_celsius = results[column]
            if all(x in column.split(sep=" ") for x in [output_name_return_temperature]):
                return_temperature_list_in_celsius = results[column]

        # get mean, max and min values of flow and return temperatures
        temperature_diff_flow_and_return_in_celsius = (
            flow_temperature_list_in_celsius - return_temperature_list_in_celsius
        )
        (
            mean_temperature_difference_between_flow_and_return_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
        ) = self.calc_mean_max_min_value(list_or_pandas_series=temperature_diff_flow_and_return_in_celsius)

        (
            mean_flow_temperature_in_celsius,
            max_flow_temperature_in_celsius,
            min_flow_temperature_in_celsius,
        ) = self.calc_mean_max_min_value(list_or_pandas_series=flow_temperature_list_in_celsius)

        (
            mean_return_temperature_in_celsius,
            max_return_temperature_in_celsius,
            min_return_temperature_in_celsius,
        ) = self.calc_mean_max_min_value(list_or_pandas_series=return_temperature_list_in_celsius)

        return (
            mean_flow_temperature_in_celsius,
            mean_return_temperature_in_celsius,
            mean_temperature_difference_between_flow_and_return_in_celsius,
            max_flow_temperature_in_celsius,
            max_return_temperature_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            min_flow_temperature_in_celsius,
            min_return_temperature_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
        )

    def get_heat_pump_energy_performance(
        self,
        results: pd.DataFrame,
        seconds_per_timestep: int,
        component_name: str,
        building_conditioned_floor_area_in_m2: float,
    ) -> Tuple[float, float, float, float, float, float, float, float]:
        """Get energy performance kpis from heat pump over simulated period.

        Transform thermal and electrical power from heat pump in energies.
        """
        output_heating_energy_in_kilowatt_hour = 0.0
        output_cooling_energy_in_kilowatt_hour = 0.0
        electrical_energy_for_heating_in_kilowatt_hour = 1.0
        electrical_energy_for_cooling_in_kilowatt_hour = 1.0

        for column in results.columns:
            if all(
                x in column.split(sep=" ") for x in [MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH, component_name]
            ) or all(x in column.split(sep=" ") for x in [HeatPumpHplib.ThermalOutputPower, component_name]):
                # take only output values for heating
                heating_output_power_values_in_watt = results[column].loc[results[column] > 0.0]
                # take only output values for cooling
                cooling_output_power_values_in_watt = results[column].loc[results[column] < 0.0]
                # get energy from power
                output_heating_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=heating_output_power_values_in_watt, timeresolution=seconds_per_timestep
                )
                # for cooling enery use absolute value, not negative value
                output_cooling_energy_in_kilowatt_hour = abs(
                    self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=cooling_output_power_values_in_watt,
                        timeresolution=seconds_per_timestep,
                    )
                )
            if all(
                x in column.split(sep=" ") for x in [MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH, component_name]
            ) or all(
                x in column.split(sep=" ") for x in [HeatPumpHplib.ElectricalInputPowerForHeating, component_name]
            ):
                # get electrical energie values for heating
                electrical_energy_for_heating_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=results[column], timeresolution=seconds_per_timestep
                )
            if all(
                x in column.split(sep=" ")
                for x in [MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling, component_name]
            ) or all(
                x in column.split(sep=" ") for x in [HeatPumpHplib.ElectricalInputPowerForCooling, component_name]
            ):
                # get electrical energie values for cooling
                electrical_energy_for_cooling_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=results[column], timeresolution=seconds_per_timestep
                )

        # calculate SPF
        if electrical_energy_for_heating_in_kilowatt_hour != 0.0:
            spf = output_heating_energy_in_kilowatt_hour / electrical_energy_for_heating_in_kilowatt_hour
        else:
            spf = 0
        # calculate SEER
        if electrical_energy_for_cooling_in_kilowatt_hour != 0.0:
            seer = output_cooling_energy_in_kilowatt_hour / electrical_energy_for_cooling_in_kilowatt_hour
        else:
            seer = 0
        # calculate specific heat pump thermal output energy for heating
        specific_heating_output_energy_of_heat_pump_in_kilowatt_hour_per_m2 = (
            output_heating_energy_in_kilowatt_hour / building_conditioned_floor_area_in_m2
        )
        # calculate total electricty input energy
        total_electrical_energy_input_in_kilowatt_hour = (
            electrical_energy_for_cooling_in_kilowatt_hour + electrical_energy_for_heating_in_kilowatt_hour
        )

        return (
            spf,
            seer,
            output_heating_energy_in_kilowatt_hour,
            output_cooling_energy_in_kilowatt_hour,
            electrical_energy_for_heating_in_kilowatt_hour,
            electrical_energy_for_cooling_in_kilowatt_hour,
            total_electrical_energy_input_in_kilowatt_hour,
            specific_heating_output_energy_of_heat_pump_in_kilowatt_hour_per_m2,
        )

    def get_heat_pump_cooling_and_heating_times(
        self, results: pd.DataFrame, component_name: str,
    ) -> Tuple[float, float]:
        """Get heating and cooling times of heat pump."""
        heating_time_in_hours = 0.0
        cooling_time_in_hours = 0.0

        for column in results.columns:
            if all(
                x in column.split(sep=" ") for x in [MoreAdvancedHeatPumpHPLib.TimeOnHeating, component_name]
            ) or all(x in column.split(sep=" ") for x in [HeatPumpHplib.TimeOnHeating, component_name]):
                heating_time_in_seconds = sum(results[column])
                heating_time_in_hours = heating_time_in_seconds / 3600
            if all(
                x in column.split(sep=" ") for x in [MoreAdvancedHeatPumpHPLib.TimeOnCooling, component_name]
            ) or all(x in column.split(sep=" ") for x in [HeatPumpHplib.TimeOnCooling, component_name]):
                cooling_time_in_seconds = sum(results[column])
                cooling_time_in_hours = cooling_time_in_seconds / 3600

        return heating_time_in_hours, cooling_time_in_hours

    def get_space_heating_heat_pump_kpis(
        self,
        building_conditioned_floor_area_in_m2: float,
        sh_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour: Optional[float],
        total_electricity_consumption_in_kilowatt_hour: Optional[float],
        total_electricity_consumption_from_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Get some KPIs from Space Heating Heat Pump."""

        number_of_heat_pump_cycles = None
        seasonal_performance_factor = None
        seasonal_energy_efficiency_ratio = None
        heating_output_energy_heatpump_in_kilowatt_hour = None
        cooling_output_energy_heatpump_in_kilowatt_hour = None
        electrical_input_energy_for_heating_in_kilowatt_hour = None
        electrical_input_energy_for_cooling_in_kilowatt_hour = None
        total_electrical_energy_input_in_kilowatt_hour = None
        specific_heating_energy_of_heat_pump_in_kilowatt_hour_per_m2 = None
        heating_time_in_hours = None
        cooling_time_in_hours = None
        mean_flow_temperature_in_celsius = None
        mean_return_temperature_in_celsius = None
        mean_temperature_difference_between_flow_and_return_in_celsius = None
        min_flow_temperature_in_celsius = None
        min_return_temperature_in_celsius = None
        min_temperature_difference_between_flow_and_return_in_celsius = None
        max_flow_temperature_in_celsius = None
        max_return_temperature_in_celsius = None
        max_temperature_difference_between_flow_and_return_in_celsius = None
        relative_electricity_demand_in_percent: Optional[float] = None
        share_of_sh_heat_pump_on_total_electricity_consumption_in_percent: Optional[float] = None
        share_of_sh_heat_pump_on_total_consumption_from_grid_in_percent: Optional[float] = None

        # check if Heat Pump was used in components
        for wrapped_component in self.wrapped_components:

            if isinstance(wrapped_component.my_component, (HeatPumpHplib, MoreAdvancedHeatPumpHPLib)):
                # get number of heat pump cycles over simulated period
                number_of_heat_pump_cycles = self.get_heatpump_cycles(
                    results=self.results, component_name=wrapped_component.my_component.component_name
                )

                # get SPF, SEER and other outputs
                (
                    seasonal_performance_factor,
                    seasonal_energy_efficiency_ratio,
                    heating_output_energy_heatpump_in_kilowatt_hour,
                    cooling_output_energy_heatpump_in_kilowatt_hour,
                    electrical_input_energy_for_heating_in_kilowatt_hour,
                    electrical_input_energy_for_cooling_in_kilowatt_hour,
                    total_electrical_energy_input_in_kilowatt_hour,
                    specific_heating_energy_of_heat_pump_in_kilowatt_hour_per_m2,
                ) = self.get_heat_pump_energy_performance(
                    results=self.results,
                    seconds_per_timestep=self.simulation_parameters.seconds_per_timestep,
                    component_name=wrapped_component.my_component.component_name,
                    building_conditioned_floor_area_in_m2=building_conditioned_floor_area_in_m2,
                )
                # get heating and cooling hours
                (heating_time_in_hours, cooling_time_in_hours) = self.get_heat_pump_cooling_and_heating_times(
                    results=self.results, component_name=wrapped_component.my_component.component_name,
                )

                # get flow and return temperatures
                if isinstance(wrapped_component.my_component, MoreAdvancedHeatPumpHPLib):
                    output_name_flow_temperature = MoreAdvancedHeatPumpHPLib.TemperatureOutputSH
                    output_name_return_temperature = MoreAdvancedHeatPumpHPLib.TemperatureInputSH
                elif isinstance(wrapped_component.my_component, HeatPumpHplib):
                    output_name_flow_temperature = HeatPumpHplib.TemperatureOutput
                    output_name_return_temperature = HeatPumpHplib.TemperatureInputWarmWater

                (
                    mean_flow_temperature_in_celsius,
                    mean_return_temperature_in_celsius,
                    mean_temperature_difference_between_flow_and_return_in_celsius,
                    max_flow_temperature_in_celsius,
                    max_return_temperature_in_celsius,
                    max_temperature_difference_between_flow_and_return_in_celsius,
                    min_flow_temperature_in_celsius,
                    min_return_temperature_in_celsius,
                    min_temperature_difference_between_flow_and_return_in_celsius,
                ) = self.get_flow_and_return_temperatures(
                    results=self.results,
                    output_name_flow_temperature=output_name_flow_temperature,
                    output_name_return_temperature=output_name_return_temperature,
                )

                break

        if None in (
            number_of_heat_pump_cycles,
            seasonal_performance_factor,
            seasonal_energy_efficiency_ratio,
            heating_output_energy_heatpump_in_kilowatt_hour,
            cooling_output_energy_heatpump_in_kilowatt_hour,
            electrical_input_energy_for_heating_in_kilowatt_hour,
            electrical_input_energy_for_cooling_in_kilowatt_hour,
            total_electrical_energy_input_in_kilowatt_hour,
            specific_heating_energy_of_heat_pump_in_kilowatt_hour_per_m2,
            heating_time_in_hours,
            cooling_time_in_hours,
            mean_temperature_difference_between_flow_and_return_in_celsius,
            mean_flow_temperature_in_celsius,
            mean_return_temperature_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
            min_flow_temperature_in_celsius,
            min_return_temperature_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            max_flow_temperature_in_celsius,
            max_return_temperature_in_celsius,
        ):
            log.warning(
                "Some KPI values for advanced heat pump HPLib are None. "
                "Please check if you have correctly initialized and connected the heat pump in your system setup or ignore this message."
            )

        # calculate some more sh heat pump kpis if possible
        if (
            sh_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour is not None
            and total_electrical_energy_input_in_kilowatt_hour is not None
            and total_electricity_consumption_from_grid_in_kilowatt_hour is not None
        ):

            # get relative electricity demand of sh heat pump
            relative_electricity_demand_in_percent = float(
                sh_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour
                / total_electrical_energy_input_in_kilowatt_hour
                * 100
            )
            # share of sh heat pump on total electricity consumption
            share_of_sh_heat_pump_on_total_consumption_from_grid_in_percent = float(
                sh_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour
                / total_electricity_consumption_from_grid_in_kilowatt_hour
                * 100
            )
        if (
            total_electrical_energy_input_in_kilowatt_hour is not None
            and total_electricity_consumption_in_kilowatt_hour is not None
        ):
            # share of sh heat pump on total electricity consumption
            share_of_sh_heat_pump_on_total_electricity_consumption_in_percent = float(
                total_electrical_energy_input_in_kilowatt_hour / total_electricity_consumption_in_kilowatt_hour * 100
            )

        # make kpi entry
        number_of_heat_pump_cycles_entry = KpiEntry(
            name="Number of SH heat pump cycles",
            unit="-",
            value=number_of_heat_pump_cycles,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        seasonal_performance_factor_entry = KpiEntry(
            name="Seasonal performance factor of SH heat pump",
            unit="-",
            value=seasonal_performance_factor,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        seasonal_energy_efficiency_entry = KpiEntry(
            name="Seasonal energy efficiency ratio of SH heat pump",
            unit="-",
            value=seasonal_energy_efficiency_ratio,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        heating_output_energy_heatpump_entry = KpiEntry(
            name="Heating output energy of SH heat pump",
            unit="kWh",
            value=heating_output_energy_heatpump_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        cooling_output_energy_heatpump_entry = KpiEntry(
            name="Cooling output energy of SH heat pump",
            unit="kWh",
            value=cooling_output_energy_heatpump_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        specific_heating_energy_of_heatpump_entry = KpiEntry(
            name="Specific heating energy of SH heat pump",
            unit="kWh/m2",
            value=specific_heating_energy_of_heat_pump_in_kilowatt_hour_per_m2,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        electrical_input_energy_for_heating_entry = KpiEntry(
            name="Electrical input energy for heating of SH heat pump",
            unit="kWh",
            value=electrical_input_energy_for_heating_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        electrical_input_energy_for_cooling_entry = KpiEntry(
            name="Electrical input energy for cooling of SH heat pump",
            unit="kWh",
            value=electrical_input_energy_for_cooling_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        electrical_input_energy_total_entry = KpiEntry(
            name="Total electrical input energy of SH heat pump",
            unit="kWh",
            value=total_electrical_energy_input_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        sh_heatpump_relative_electricity_demand_entry = KpiEntry(
            name="Relative electricity demand of SH heat pump",
            unit="%",
            value=relative_electricity_demand_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        sh_heatpump_share_on_total_consumption_entry = KpiEntry(
            name="Share of SH heat pump on total electricity consumption",
            unit="%",
            value=share_of_sh_heat_pump_on_total_electricity_consumption_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        sh_heatpump_share_on_total_consumption_from_grid_entry = KpiEntry(
            name="Share of SH heat pump on total grid consumption",
            unit="%",
            value=share_of_sh_heat_pump_on_total_consumption_from_grid_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        heating_hours_entry = KpiEntry(
            name="Heating hours of SH heat pump",
            unit="h",
            value=heating_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        cooling_hours_entry = KpiEntry(
            name="Cooling hours of SH heat pump",
            unit="h",
            value=cooling_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        electrical_input_energy_for_cooling_entry = KpiEntry(
            name="Electrical input energy for cooling of heat pump",
            unit="kWh",
            value=electrical_input_energy_for_cooling_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        heating_hours_entry = KpiEntry(
            name="Heating hours of heat pump",
            unit="h",
            value=heating_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        cooling_hours_entry = KpiEntry(
            name="Cooling hours of heat pump",
            unit="h",
            value=cooling_time_in_hours,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        mean_flow_temperature_heatpump_entry = KpiEntry(
            name="Mean flow temperature of SH heat pump",
            unit="°C",
            value=mean_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        mean_return_temperature_heatpump_entry = KpiEntry(
            name="Mean return temperature of SH heat pump",
            unit="°C",
            value=mean_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        mean_temperature_difference_heatpump_entry = KpiEntry(
            name="Mean temperature difference of SH heat pump",
            unit="°C",
            value=mean_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        max_flow_temperature_heatpump_entry = KpiEntry(
            name="Max flow temperature of SH heat pump",
            unit="°C",
            value=max_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        max_return_temperature_heatpump_entry = KpiEntry(
            name="Max return temperature of SH heat pump",
            unit="°C",
            value=max_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        max_temperature_difference_heatpump_entry = KpiEntry(
            name="Max temperature difference of SH heat pump",
            unit="°C",
            value=max_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        min_flow_temperature_heatpump_entry = KpiEntry(
            name="Min flow temperature of SH heat pump",
            unit="°C",
            value=min_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        min_return_temperature_heatpump_entry = KpiEntry(
            name="Min return temperature of SH heat pump",
            unit="°C",
            value=min_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        min_temperature_difference_heatpump_entry = KpiEntry(
            name="Min temperature difference of SH heat pump",
            unit="°C",
            value=min_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                number_of_heat_pump_cycles_entry.name: number_of_heat_pump_cycles_entry.to_dict(),
                seasonal_performance_factor_entry.name: seasonal_performance_factor_entry.to_dict(),
                seasonal_energy_efficiency_entry.name: seasonal_energy_efficiency_entry.to_dict(),
                heating_output_energy_heatpump_entry.name: heating_output_energy_heatpump_entry.to_dict(),
                cooling_output_energy_heatpump_entry.name: cooling_output_energy_heatpump_entry.to_dict(),
                specific_heating_energy_of_heatpump_entry.name: specific_heating_energy_of_heatpump_entry.to_dict(),
                electrical_input_energy_for_heating_entry.name: electrical_input_energy_for_heating_entry.to_dict(),
                electrical_input_energy_for_cooling_entry.name: electrical_input_energy_for_cooling_entry.to_dict(),
                electrical_input_energy_total_entry.name: electrical_input_energy_total_entry.to_dict(),
                sh_heatpump_relative_electricity_demand_entry.name: sh_heatpump_relative_electricity_demand_entry.to_dict(),
                sh_heatpump_share_on_total_consumption_entry.name: sh_heatpump_share_on_total_consumption_entry.to_dict(),
                sh_heatpump_share_on_total_consumption_from_grid_entry.name: sh_heatpump_share_on_total_consumption_from_grid_entry.to_dict(),
                heating_hours_entry.name: heating_hours_entry.to_dict(),
                cooling_hours_entry.name: cooling_hours_entry.to_dict(),
                mean_flow_temperature_heatpump_entry.name: mean_flow_temperature_heatpump_entry.to_dict(),
                mean_return_temperature_heatpump_entry.name: mean_return_temperature_heatpump_entry.to_dict(),
                mean_temperature_difference_heatpump_entry.name: mean_temperature_difference_heatpump_entry.to_dict(),
                max_flow_temperature_heatpump_entry.name: max_flow_temperature_heatpump_entry.to_dict(),
                max_return_temperature_heatpump_entry.name: max_return_temperature_heatpump_entry.to_dict(),
                max_temperature_difference_heatpump_entry.name: max_temperature_difference_heatpump_entry.to_dict(),
                min_flow_temperature_heatpump_entry.name: min_flow_temperature_heatpump_entry.to_dict(),
                min_return_temperature_heatpump_entry.name: min_return_temperature_heatpump_entry.to_dict(),
                min_temperature_difference_heatpump_entry.name: min_temperature_difference_heatpump_entry.to_dict(),
            }
        )

    def get_dhw_heat_pump_kpis(
        self,
        dhw_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour: Optional[float],
        total_electricity_consumption_in_kilowatt_hour: Optional[float],
        total_electricity_consumption_from_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Check kpi values of domestic hot water heat pump."""

        wrapped_dhw_heat_pump_component = None
        dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour: Optional[float] = None
        dhw_heat_pump_heating_energy_output_in_kilowatt_hour: Optional[float] = None
        relative_electricity_demand_in_percent: Optional[float] = None
        share_of_dhw_heat_pump_on_total_electricity_consumption_in_percent: Optional[float] = None
        share_of_dhw_heat_pump_on_total_consumption_from_grid_in_percent: Optional[float] = None
        # find DHW Heat Pump among the components
        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, ModularHeatPump):
                if "DHWHeatPump" in wrapped_component.my_component.component_name:
                    wrapped_dhw_heat_pump_component = wrapped_component

            if isinstance(wrapped_component.my_component, MoreAdvancedHeatPumpHPLib):
                if wrapped_component.my_component.with_domestic_hot_water_preparation:
                    wrapped_dhw_heat_pump_component = wrapped_component

        if wrapped_dhw_heat_pump_component is None:
            log.information("No DHWHeatPump could be found. No dhw heat pump KPIs can be calculated.")
            dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour = None
            return

        for column in self.results.columns:
            if all(
                x in column.split(sep=" ")
                for x in [
                    wrapped_dhw_heat_pump_component.my_component.component_name,
                    ModularHeatPump.ElectricityOutput,
                ]
            ) or all(
                x in column.split(sep=" ")
                for x in [
                    wrapped_dhw_heat_pump_component.my_component.component_name,
                    MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
                ]
            ):
                dhw_heat_pump_total_electricity_consumption_in_watt_series = self.results[column]
                dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=dhw_heat_pump_total_electricity_consumption_in_watt_series,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
            if all(
                x in column.split(sep=" ")
                for x in [
                    wrapped_dhw_heat_pump_component.my_component.component_name,
                    ModularHeatPump.ThermalPowerDelivered,
                ]
            ) or all(
                x in column.split(sep=" ")
                for x in [
                    wrapped_dhw_heat_pump_component.my_component.component_name,
                    MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW,
                ]
            ):
                dhw_heat_pump_heating_power_output_in_watt_series = self.results[column]
                dhw_heat_pump_heating_energy_output_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=dhw_heat_pump_heating_power_output_in_watt_series,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )

        # calculate some more dhw heat pump kpis if possible
        if (
            dhw_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour is not None
            and dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour is not None
            and total_electricity_consumption_from_grid_in_kilowatt_hour is not None
        ):
            # get relative electricity demand of dhw heat pump
            relative_electricity_demand_in_percent = float(
                dhw_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour
                / dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour
                * 100
            )

            # share of dhw heat pump on total electricity consumption
            share_of_dhw_heat_pump_on_total_consumption_from_grid_in_percent = float(
                dhw_heat_pump_electricity_consumption_from_grid_in_kilowatt_hour
                / total_electricity_consumption_from_grid_in_kilowatt_hour
                * 100
            )
        if (
            dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour is not None
            and total_electricity_consumption_in_kilowatt_hour is not None
        ):
            # share of dhw heat pump on total electricity consumption
            share_of_dhw_heat_pump_on_total_electricity_consumption_in_percent = float(
                dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour
                / total_electricity_consumption_in_kilowatt_hour
                * 100
            )

        # make kpi entry
        dhw_heatpump_total_electricity_consumption_entry = KpiEntry(
            name="DHW heat pump total electricity consumption",
            unit="kWh",
            value=dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )
        dhw_heatpump_heating_energy_output_entry = KpiEntry(
            name="Heating output energy of DHW heat pump",
            unit="kWh",
            value=dhw_heat_pump_heating_energy_output_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )
        dhw_heatpump_relative_electricity_demand_entry = KpiEntry(
            name="Relative electricity demand of DHW heat pump",
            unit="%",
            value=relative_electricity_demand_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )
        dhw_heatpump_share_on_total_consumption_entry = KpiEntry(
            name="Share of DHW heat pump on total electricity consumption",
            unit="%",
            value=share_of_dhw_heat_pump_on_total_electricity_consumption_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )
        dhw_heatpump_share_on_total_consumption_from_grid_entry = KpiEntry(
            name="Share of DHW heat pump on total grid consumption",
            unit="%",
            value=share_of_dhw_heat_pump_on_total_consumption_from_grid_in_percent,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                dhw_heatpump_total_electricity_consumption_entry.name: dhw_heatpump_total_electricity_consumption_entry.to_dict(),
                dhw_heatpump_heating_energy_output_entry.name: dhw_heatpump_heating_energy_output_entry.to_dict(),
                dhw_heatpump_relative_electricity_demand_entry.name: dhw_heatpump_relative_electricity_demand_entry.to_dict(),
                dhw_heatpump_share_on_total_consumption_entry.name: dhw_heatpump_share_on_total_consumption_entry.to_dict(),
                dhw_heatpump_share_on_total_consumption_from_grid_entry.name: dhw_heatpump_share_on_total_consumption_from_grid_entry.to_dict(),
            }
        )

    def get_occupancy_electricity_kpis(
        self,
        total_electricity_consumption_in_kilowatt_hour: Optional[float],
        total_electricity_consumption_from_grid_in_kilowatt_hour: Optional[float],
        occupancy_electricity_from_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Check electricity kpi values of occupancy."""

        wrapped_occupancy_component = None
        occupancy_total_electricity_consumption_in_kilowatt_hour: Optional[float] = None
        share_of_occupancy_on_total_electricity_consumption_in_percent: Optional[float] = None
        relative_electricity_demand_in_percent: Optional[float] = None
        share_of_occupancy_on_total_consumption_from_grid_in_percent: Optional[float] = None

        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, UtspLpgConnector):
                wrapped_occupancy_component = wrapped_component
                break
        if not wrapped_occupancy_component:
            log.warning(
                "KPI values for occupancy are None. "
                "Please check if you have correctly initialized and connected the occupancy in your system setup or ignore this message."
            )
            return

        for column in self.results.columns:
            if all(
                x in column.split(sep=" ")
                for x in [wrapped_occupancy_component.my_component.component_name, UtspLpgConnector.ElectricalPowerConsumption]
            ):

                occupancy_total_electricity_consumption_in_watt_series = self.results[column]
                occupancy_total_electricity_consumption_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=occupancy_total_electricity_consumption_in_watt_series,
                    timeresolution=self.simulation_parameters.seconds_per_timestep,
                )
        # calculate some more occupancy kpis if possible
        if (
            occupancy_total_electricity_consumption_in_kilowatt_hour is not None
            and total_electricity_consumption_in_kilowatt_hour is not None
        ):

            # share of occupancy on total electricity consumption
            share_of_occupancy_on_total_electricity_consumption_in_percent = float(
                occupancy_total_electricity_consumption_in_kilowatt_hour
                / total_electricity_consumption_in_kilowatt_hour
                * 100
            )

        if (
            occupancy_electricity_from_grid_in_kilowatt_hour is not None
            and occupancy_total_electricity_consumption_in_kilowatt_hour is not None
            and total_electricity_consumption_from_grid_in_kilowatt_hour is not None
        ):

            # get relative electricity demand of occupancy
            relative_electricity_demand_in_percent = float(
                occupancy_electricity_from_grid_in_kilowatt_hour
                / occupancy_total_electricity_consumption_in_kilowatt_hour
                * 100
            )

            # share of occupancy on total electricity consumption
            share_of_occupancy_on_total_consumption_from_grid_in_percent = float(
                occupancy_electricity_from_grid_in_kilowatt_hour
                / total_electricity_consumption_from_grid_in_kilowatt_hour
                * 100
            )

        # make kpi entry
        occupancy_total_electricity_consumption_entry = KpiEntry(
            name="Residents' total electricity consumption",
            unit="kWh",
            value=occupancy_total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.RESIDENTS,
        )

        occupancy_share_on_total_consumption_entry = KpiEntry(
            name="Share of residents on total electricity consumption",
            unit="%",
            value=share_of_occupancy_on_total_electricity_consumption_in_percent,
            tag=KpiTagEnumClass.RESIDENTS,
        )
        relative_electricity_demand_entry = KpiEntry(
            name="Relative electricity demand of residents",
            unit="%",
            value=relative_electricity_demand_in_percent,
            tag=KpiTagEnumClass.RESIDENTS,
        )
        occupancy_share_on_grid_consumption_entry = KpiEntry(
            name="Share of residents on total grid consumption",
            unit="%",
            value=share_of_occupancy_on_total_consumption_from_grid_in_percent,
            tag=KpiTagEnumClass.RESIDENTS,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                occupancy_total_electricity_consumption_entry.name: occupancy_total_electricity_consumption_entry.to_dict(),
                occupancy_share_on_total_consumption_entry.name: occupancy_share_on_total_consumption_entry.to_dict(),
                relative_electricity_demand_entry.name: relative_electricity_demand_entry.to_dict(),
                occupancy_share_on_grid_consumption_entry.name: occupancy_share_on_grid_consumption_entry.to_dict(),
            }
        )

    def get_grid_consumptions_of_heat_pumps_via_energy_management_kpis(
        self,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Check ems kpi values and get from grid consumptions of heat pumps."""

        wrapped_ems_component = None
        sh_heatpump_electricity_from_grid_in_kilowatt_hour = None
        dhw_heatpump_electricity_from_grid_in_kilowatt_hour = None
        occupancy_electricity_from_grid_in_kilowatt_hour = None

        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, (L2GenericEnergyManagementSystem, L2GenericDistrictEnergyManagementSystem)):
                wrapped_ems_component = wrapped_component
                break
        if not wrapped_ems_component:
            log.information("Could not find the Energy Management System component.")
            return None, None, None

        for column in self.results.columns:

            if all(x in column.split(sep=" ") for x in [wrapped_ems_component.my_component.component_name]):
                for string in column.split(sep=" "):

                    if "ElectricityToOrFromGridOfModularHeatPump" in string.split(
                        sep="_"
                    ) or "ElectricityToOrFromGridOfDHWMoreAdvancedHeatPumpHPLib" in string.split(sep="_"):
                        dhw_hp_electricity_from_grid_in_watt_series = self.results[column].loc[
                            self.results[column] < 0.0
                        ]
                        dhw_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                            self.compute_total_energy_from_power_timeseries(
                                power_timeseries_in_watt=dhw_hp_electricity_from_grid_in_watt_series,
                                timeresolution=self.simulation_parameters.seconds_per_timestep,
                            )
                        )

                    if "ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib" in string.split(
                        sep="_"
                    ) or "ElectricityToOrFromGridOfHeatPumpHplib" in string.split(sep="_"):
                        sh_electricity_from_grid_in_watt_series = self.results[column].loc[self.results[column] < 0.0]
                        sh_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                            self.compute_total_energy_from_power_timeseries(
                                power_timeseries_in_watt=sh_electricity_from_grid_in_watt_series,
                                timeresolution=self.simulation_parameters.seconds_per_timestep,
                            )
                        )
                    if "ElectricityToOrFromGridOfUtspLpgConnector" in string.split(sep="_"):

                        occupancy_electricity_from_grid_in_watt_series = self.results[column].loc[
                            self.results[column] < 0.0
                        ]

                        occupancy_electricity_from_grid_in_kilowatt_hour = abs(
                            self.compute_total_energy_from_power_timeseries(
                                power_timeseries_in_watt=occupancy_electricity_from_grid_in_watt_series,
                                timeresolution=self.simulation_parameters.seconds_per_timestep,
                            )
                        )
        if None in (
            sh_heatpump_electricity_from_grid_in_kilowatt_hour,
            dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
            occupancy_electricity_from_grid_in_kilowatt_hour,
        ):
            log.warning(
                "Some KPI values for the energy management system are None. "
                "Please check if you have correctly initialized and connected the EMS in your system setup or ignore this message."
            )

        # make kpi entry
        sh_heatpump_electricity_from_grid_entry = KpiEntry(
            name="Space heating heat pump electricity from grid",
            unit="kWh",
            value=sh_heatpump_electricity_from_grid_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_SPACE_HEATING,
        )
        dhw_heatpump_electricity_from_grid_entry = KpiEntry(
            name="Domestic hot water heat pump electricity from grid",
            unit="kWh",
            value=dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
        )
        occupancy_electricity_from_grid_entry = KpiEntry(
            name="Residents' electricity consumption from grid",
            unit="kWh",
            value=occupancy_electricity_from_grid_in_kilowatt_hour,
            tag=KpiTagEnumClass.RESIDENTS,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                sh_heatpump_electricity_from_grid_entry.name: sh_heatpump_electricity_from_grid_entry.to_dict(),
                dhw_heatpump_electricity_from_grid_entry.name: dhw_heatpump_electricity_from_grid_entry.to_dict(),
                occupancy_electricity_from_grid_entry.name: occupancy_electricity_from_grid_entry.to_dict(),
            }
        )
        return (
            sh_heatpump_electricity_from_grid_in_kilowatt_hour,
            dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
            occupancy_electricity_from_grid_in_kilowatt_hour,
        )

    def calc_mean_max_min_value(self, list_or_pandas_series: Union[List, pd.Series]) -> Tuple[float, float, float]:
        """Calc mean, max and min values from List or pd.Series with numpy."""

        mean_value = float(np.mean(list_or_pandas_series))
        max_value = float(np.max(list_or_pandas_series))
        min_value = float(np.min(list_or_pandas_series))

        return mean_value, max_value, min_value

    def return_table_for_report(self):
        """Return a table with all kpis for the report."""
        table: List = []
        table.append(["KPI", "Value", "Unit"])

        for kpi_tag, kpi_entries in self.kpi_collection_dict_sorted.items():
            table.append([f"{kpi_tag}", "", ""])
            table.append(["-------------------------------------", "", ""])

            for kpi_name, kpi_entry in kpi_entries.items():
                value = kpi_entry["value"]
                if value is not None:
                    value = round(value, 2)
                unit = kpi_entry["unit"]
                table.append([f"{kpi_name}: ", f"{value}", f"{unit}"])
            table.append(["\n", "\n", "\n"])

        return table

    def sort_kpi_collection_according_to_kpi_tags(self, kpi_collection_dict_unsorted: Dict) -> Dict:
        """Sort KPI collection dict according to KPI tags."""

        kpi_collection_dict_sorted: Dict[str, Dict] = {}
        # get all tags and use as keys for sorted kpi dict
        for entry in kpi_collection_dict_unsorted.values():
            kpi_collection_dict_sorted.update({entry["tag"]: {}})

        # now sort kpi dict entries according to tags
        for kpi_name, entry in kpi_collection_dict_unsorted.items():
            for tag, tag_dict in kpi_collection_dict_sorted.items():
                if entry["tag"] in tag:
                    tag_dict.update({kpi_name: entry})

        return kpi_collection_dict_sorted
