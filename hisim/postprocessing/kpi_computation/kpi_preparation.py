# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and self-sufficiency rate.

KPis for PV-battery systems in houses:
https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
"""

import os
from typing import List, Tuple, Dict, Optional
from pathlib import Path
import pandas as pd
from hisim.component import ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.loadtypes import ComponentType, InandOutputType, DistrictNames
from hisim import log
from hisim.components.electricity_meter import ElectricityMeter
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry


class KpiPreparation:
    """Class for generating and calculating key performance indicators."""

    def __init__(
        self, post_processing_data_transfer: PostProcessingDataTransfer, building_objects_in_district_list: list
    ):
        """Initialize further variables."""
        self.post_processing_data_transfer = post_processing_data_transfer
        self.building_objects_in_district_list = building_objects_in_district_list
        self.kpi_collection_dict_unsorted: Dict = {}
        # get important variables
        self.wrapped_components = self.post_processing_data_transfer.wrapped_components
        self.results = self.post_processing_data_transfer.results
        self.all_outputs = self.post_processing_data_transfer.all_outputs
        self.simulation_parameters = self.post_processing_data_transfer.simulation_parameters
        self.get_all_component_kpis(wrapped_components=self.wrapped_components)

    def filter_results_according_to_postprocessing_flags(
        self,
        all_outputs: List,
        results: pd.DataFrame,
        building_objects_in_district: str,
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
        buildings_production_ids = []
        pv_production_ids = []
        windturbine_production_ids = []
        battery_charge_discharge_ids = []
        buildings_consumption_ids = []

        index: int
        output: ComponentOutput

        for index, output in enumerate(all_outputs):
            if (building_objects_in_district == output.component_name.split("_")[0] or
                    not self.simulation_parameters.multiple_buildings):
                if output.postprocessing_flag is not None:
                    if InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag:
                        total_production_ids.append(index)
                        if ComponentType.PV in output.postprocessing_flag:
                            pv_production_ids.append(index)
                        elif ComponentType.WINDTURBINE in output.postprocessing_flag:
                            windturbine_production_ids.append(index)
                        elif ComponentType.BUILDINGS in output.postprocessing_flag:
                            buildings_production_ids.append(index)
                    if (
                        InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED in output.postprocessing_flag
                        or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in output.postprocessing_flag
                    ):
                        if ComponentType.BUILDINGS not in output.postprocessing_flag:
                            total_consumption_ids.append(index)
                            log.debug(
                                "Output considered in total electricity consumption "
                                + output.full_name
                                + " "
                                + str(output.unit)
                            )
                        if ComponentType.BUILDINGS in output.postprocessing_flag:
                            buildings_consumption_ids.append(index)
                            log.debug(
                                "Output considered in BUILDINGS electricity consumption "
                                + output.full_name
                                + " "
                                + str(output.unit)
                            )

                    if InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                        if ComponentType.BATTERY in output.postprocessing_flag:
                            battery_charge_discharge_ids.append(index)
                        elif ComponentType.CAR_BATTERY in output.postprocessing_flag:
                            total_consumption_ids.append(index)
                            log.debug(
                                "Output considered in total electricity consumption "
                                + output.full_name
                                + " "
                                + str(output.unit)
                            )
                else:
                    continue

        result_dataframe = pd.DataFrame()
        result_dataframe["total_consumption"] = (
            pd.DataFrame(results.iloc[:, total_consumption_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["building_consumption"] = (
            pd.DataFrame(results.iloc[:, buildings_consumption_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["total_production"] = (
            pd.DataFrame(results.iloc[:, total_production_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["building_production"] = (
            pd.DataFrame(results.iloc[:, buildings_production_ids]).clip(lower=0).sum(axis=1)
        )
        result_dataframe["pv_production"] = pd.DataFrame(results.iloc[:, pv_production_ids]).clip(lower=0).sum(axis=1)
        result_dataframe["windturbine_production"] = (
            pd.DataFrame(results.iloc[:, windturbine_production_ids]).clip(lower=0).sum(axis=1)
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
        self,
        result_dataframe: pd.DataFrame,
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
    ) -> Tuple[float, float, float, float, float]:
        """Compute electricity consumption and production and battery kpis."""

        # sum consumption and production over time
        total_electricity_consumption_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["total_consumption"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
        )
        building_electricity_consumption_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["building_consumption"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
        )

        total_electricity_production_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["total_production"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
        )
        pv_production_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["pv_production"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
        )
        windturbine_production_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["windturbine_production"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
        )
        building_production_in_kilowatt_hour = round(
            self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["building_production"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ),
            1,
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
        log.debug("Battery losses " + str(battery_losses_in_kilowatt_hour))
        log.debug(
            "Total electricity consumption (battery losses included) "
            + str(total_electricity_consumption_in_kilowatt_hour)
        )

        # make kpi entry
        total_consumtion_entry = KpiEntry(
            name="Total electricity consumption",
            unit="kWh",
            value=total_electricity_consumption_in_kilowatt_hour,
            tag=kpi_tag,
        )

        total_production_entry = KpiEntry(
            name="Total electricity production",
            unit="kWh",
            value=total_electricity_production_in_kilowatt_hour,
            tag=kpi_tag,
        )
        pv_production_entry = KpiEntry(
            name="PV production", unit="kWh", value=pv_production_in_kilowatt_hour, tag=kpi_tag
        )
        windturbine_production_entry = KpiEntry(
            name="Windturbine production", unit="kWh", value=windturbine_production_in_kilowatt_hour, tag=kpi_tag
        )
        if any(word in building_objects_in_district for word in DistrictNames):
            building_consumption_entry = KpiEntry(
                name="Total building electricity consumption",
                unit="kWh",
                value=building_electricity_consumption_in_kilowatt_hour,
                tag=kpi_tag,
            )

            building_production_entry = KpiEntry(
                name="Building production", unit="kWh", value=building_production_in_kilowatt_hour, tag=kpi_tag
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
            name="Battery losses",
            unit="kWh",
            value=battery_losses_in_kilowatt_hour,
            tag=KpiTagEnumClass.BATTERY,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {
                total_consumtion_entry.name: total_consumtion_entry.to_dict(),
                total_production_entry.name: total_production_entry.to_dict(),
                pv_production_entry.name: pv_production_entry.to_dict(),
                windturbine_production_entry.name: windturbine_production_entry.to_dict(),
                battery_charging_entry.name: battery_charging_entry.to_dict(),
                battery_discharging_entry.name: battery_discharging_entry.to_dict(),
                battery_losses_entry.name: battery_losses_entry.to_dict(),
            }
        )
        if any(word in building_objects_in_district for word in DistrictNames):
            self.kpi_collection_dict_unsorted[building_objects_in_district].update(
                {
                    building_production_entry.name: building_production_entry.to_dict(),
                    building_consumption_entry.name: building_consumption_entry.to_dict(),
                }
            )

        return (
            total_electricity_consumption_in_kilowatt_hour,
            total_electricity_production_in_kilowatt_hour,
            pv_production_in_kilowatt_hour,
            windturbine_production_in_kilowatt_hour,
            building_production_in_kilowatt_hour,
        )

    def compute_self_consumption_injection_self_sufficiency(
        self,
        result_dataframe: pd.DataFrame,
        electricity_production_in_kilowatt_hour: float,
        electricity_consumption_in_kilowatt_hour: float,
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
    ) -> pd.DataFrame:
        """Computes the self consumption, grid injection, self-sufficiency and battery losses if electricty production is bigger than zero."""

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
            self_sufficiency_rate_in_percent = 100 * (
                self_consumption_in_kilowatt_hour / electricity_consumption_in_kilowatt_hour
            )
            if self_sufficiency_rate_in_percent > 100:
                raise ValueError(
                    "The self-sufficiency rate should not be over 100 %. Something is wrong here. Please check your code."
                )

        else:
            self_consumption_series_in_watt = pd.Series([])
            grid_injection_series_in_watt = pd.Series([])
            self_consumption_in_kilowatt_hour = 0
            grid_injection_in_kilowatt_hour = 0
            self_consumption_rate_in_percent = 0
            self_sufficiency_rate_in_percent = 0

        # add injection and self-consumption timeseries to result dataframe
        result_dataframe["self_consumption_in_watt"] = self_consumption_series_in_watt
        result_dataframe["grid_injection_in_watt"] = grid_injection_series_in_watt

        # make kpi entry
        grid_injection_entry = KpiEntry(
            name="Grid injection of electricity", unit="kWh", value=grid_injection_in_kilowatt_hour, tag=kpi_tag
        )
        self_consumption_entry = KpiEntry(
            name="Self-consumption of electricity", unit="kWh", value=self_consumption_in_kilowatt_hour, tag=kpi_tag
        )
        self_consumption_rate_entry = KpiEntry(
            name="Self-consumption rate of electricity", unit="%", value=self_consumption_rate_in_percent, tag=kpi_tag
        )
        self_sufficiency_rate_entry = KpiEntry(
            name="Self-sufficiency rate of electricity", unit="%", value=self_sufficiency_rate_in_percent, tag=kpi_tag
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {
                grid_injection_entry.name: grid_injection_entry.to_dict(),
                self_consumption_entry.name: self_consumption_entry.to_dict(),
                self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict(),
                self_sufficiency_rate_entry.name: self_sufficiency_rate_entry.to_dict(),
            }
        )
        return result_dataframe

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

    def get_electricity_to_and_from_grid_from_electricty_meter(
        self,
        building_objects_in_district: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Get electricity to and from grid from electricity meter."""

        total_energy_from_grid_in_kwh: Optional[float] = None
        total_energy_to_grid_in_kwh: Optional[float] = None
        for kpi_entry in self.kpi_collection_dict_unsorted[building_objects_in_district].values():
            if (
                isinstance(kpi_entry["description"], str)
                and ElectricityMeter.get_classname() in kpi_entry["description"]
            ):
                if kpi_entry["name"] == "Total energy from grid" and kpi_entry["unit"] == "kWh":
                    total_energy_from_grid_in_kwh = kpi_entry["value"]
                elif kpi_entry["name"] == "Total energy to grid" and kpi_entry["unit"] == "kWh":
                    total_energy_to_grid_in_kwh = kpi_entry["value"]
            else:
                continue

        return total_energy_from_grid_in_kwh, total_energy_to_grid_in_kwh

    def compute_relative_electricity_demand(
        self,
        total_electricity_consumption_in_kilowatt_hour: float,
        electricity_from_grid_in_kilowatt_hour: Optional[float],
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
        name: str = "Relative electricity demand from grid",
    ) -> Optional[float]:
        """Return the relative electricity demand."""

        if electricity_from_grid_in_kilowatt_hour is None:
            relative_electricity_demand_from_grid_in_percent = None
        else:
            relative_electricity_demand_from_grid_in_percent = (
                round(electricity_from_grid_in_kilowatt_hour, 1)
                / round(total_electricity_consumption_in_kilowatt_hour, 1)
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
            name=name, unit="%", value=relative_electricity_demand_from_grid_in_percent, tag=kpi_tag
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {relative_electricity_demand_entry.name: relative_electricity_demand_entry.to_dict()}
        )

        return relative_electricity_demand_from_grid_in_percent

    def compute_self_sufficiency_according_to_solar_htw_berlin(
        self,
        relative_electricty_demand_in_percent: Optional[float],
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
        name: str = "Self-sufficiency rate according to solar htw berlin",
    ) -> None:
        """Return the self-sufficiency rate according to solar htw berlin.

        https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
        """
        if relative_electricty_demand_in_percent is None:
            self_sufficiency_rate_in_percent = None
        else:
            self_sufficiency_rate_in_percent = 100 - relative_electricty_demand_in_percent
            if self_sufficiency_rate_in_percent > 100:
                raise ValueError(
                    "The self-sufficiency rate should not be over 100 %. Something is wrong here. Please check your code. "
                    f"The realtive electricity demand is {relative_electricty_demand_in_percent} %. "
                )

        # make kpi entry
        self_sufficiency_rate_entry = KpiEntry(name=name, unit="%", value=self_sufficiency_rate_in_percent, tag=kpi_tag)

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {self_sufficiency_rate_entry.name: self_sufficiency_rate_entry.to_dict()}
        )

    def compute_self_consumption_rate_according_to_solar_htw_berlin(
        self,
        total_electricity_production_in_kilowatt_hour: float,
        electricity_to_grid_in_kilowatt_hour: Optional[float],
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
        name: str = "Self-consumption rate according to solar htw berlin",
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
            name=name,
            unit="%",
            value=self_consumption_rate_in_percent,
            tag=kpi_tag,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict()}
        )

    def compute_ratio_between_two_values_and_set_as_kpi(
        self,
        denominator_value: float,
        numerator_value: float,
        kpi_name: str,
        building_objects_in_district: str,
        kpi_tag: KpiTagEnumClass,
    ) -> None:
        """Compute the ratio of two values.

        ratio = denominator / numerator * 100 [%].
        """
        ratio_in_percent = denominator_value / numerator_value * 100
        # make kpi entry
        ratio_in_percent_entry = KpiEntry(
            name=kpi_name,
            unit="%",
            value=ratio_in_percent,
            tag=kpi_tag,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_objects_in_district].update(
            {ratio_in_percent_entry.name: ratio_in_percent_entry.to_dict()}
        )

    def read_opex_and_capex_costs_from_results(self, building_object: str) -> None:
        """Get CAPEX and OPEX costs for simulated period.

        This function will read the opex and capex costs from the results.
        """
        # get costs and emissions from electricity meter and gas meter
        electricity_costs_in_euro: float = 0
        electricity_co2_in_kg: float = 0
        gas_costs_in_euro: float = 0
        gas_co2_in_kg: float = 0
        heating_costs_in_euro: float = 0
        heating_co2_in_kg: float = 0

        for kpi_name, kpi_entry in self.kpi_collection_dict_unsorted[building_object].items():
            if kpi_entry["tag"] == KpiTagEnumClass.ELECTRICITY_METER.value:
                if kpi_name == "Opex costs of electricity consumption from grid":
                    electricity_costs_in_euro = kpi_entry["value"]
                if kpi_name == "CO2 footprint of electricity consumption from grid":
                    electricity_co2_in_kg = kpi_entry["value"]

            elif kpi_entry["tag"] == KpiTagEnumClass.GAS_METER.value:
                if kpi_name == "Opex costs of gas consumption from grid":
                    gas_costs_in_euro = kpi_entry["value"]
                if kpi_name == "CO2 footprint of gas consumption from grid":
                    gas_co2_in_kg = kpi_entry["value"]

            elif kpi_entry["tag"] == KpiTagEnumClass.HEATING_METER.value:
                if kpi_name == "OPEX - Energy costs":
                    heating_costs_in_euro = kpi_entry["value"]
                if kpi_name == "OPEX - CO2 Footprint":
                    heating_co2_in_kg = kpi_entry["value"]

        # get CAPEX and OPEX costs for simulated period
        capex_results_path = os.path.join(
            self.simulation_parameters.result_directory, "investment_cost_co2_footprint.csv"
        )
        opex_results_path = os.path.join(
            self.simulation_parameters.result_directory, "operational_costs_co2_footprint.csv"
        )

        if Path(opex_results_path).exists():
            opex_df = pd.read_csv(opex_results_path, index_col=0, sep=";")
            log.debug("Opex df " + str(opex_df) + "\n")
            if self.simulation_parameters.multiple_buildings:
                total_maintenance_cost_per_simulated_period = opex_df["Maintenance costs per year [EUR]"].loc[
                    building_object + "_Total"
                ]
                total_maintenance_cost_per_simulated_period_without_hp = opex_df["Maintenance costs per year [EUR]"].loc[
                    building_object + "_Total_without_heatpump"
                ]
                total_maintenance_cost_per_simulated_period_only_hp = opex_df["Maintenance costs per year [EUR]"].loc[
                    building_object + "_Total_only_heatpump"
                ]
            if not self.simulation_parameters.multiple_buildings:
                total_maintenance_cost_per_simulated_period = opex_df["Maintenance costs per year [EUR]"].loc[
                    "Total"
                ]
                total_maintenance_cost_per_simulated_period_without_hp = opex_df["Maintenance costs per year [EUR]"].loc[
                    "Total_without_heatpump"
                ]
                total_maintenance_cost_per_simulated_period_only_hp = opex_df["Maintenance costs per year [EUR]"].loc[
                    "Total_only_heatpump"
                ]
        else:
            log.warning("OPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_OPEX")
            total_maintenance_cost_per_simulated_period = 0
            total_maintenance_cost_per_simulated_period_without_hp = 0
            total_maintenance_cost_per_simulated_period_only_hp = 0

        if Path(capex_results_path).exists():
            capex_df = pd.read_csv(capex_results_path, index_col=0, sep=";")
            log.debug("Capex df " + str(capex_df) + "\n")
            if self.simulation_parameters.multiple_buildings:
                total_investment_cost_per_simulated_period = capex_df["Investment for simulated period [EUR]"].loc[
                    building_object + "_Total"
                ]
                # investment minus subsidies
                total_rest_investment_cost_per_simulated_period = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    building_object + "_Total"
                ]
                total_device_co2_footprint_per_simulated_period = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    building_object + "_Total"
                ]
                total_investment_cost_per_simulated_period_without_hp = capex_df["Investment for simulated period [EUR]"].loc[
                    building_object + "_Total_without_heatpump"
                ]
                total_rest_investment_cost_per_simulated_period_without_hp = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    building_object + "_Total"
                ]
                total_device_co2_footprint_per_simulated_period_without_hp = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    building_object + "_Total_without_heatpump"
                ]
                total_investment_cost_per_simulated_period_only_hp = capex_df["Investment for simulated period [EUR]"].loc[
                    building_object + "_Total_only_heatpump"
                ]
                total_rest_investment_cost_per_simulated_period_only_hp = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    building_object + "_Total"
                ]
                total_device_co2_footprint_per_simulated_period_only_hp = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    building_object + "_Total_only_heatpump"
                ]
            if not self.simulation_parameters.multiple_buildings:
                total_investment_cost_per_simulated_period = capex_df["Investment for simulated period [EUR]"].loc[
                    "Total"
                ]
                total_rest_investment_cost_per_simulated_period = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    "Total"
                ]
                total_device_co2_footprint_per_simulated_period = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    "Total"
                ]
                total_investment_cost_per_simulated_period_without_hp = capex_df["Investment for simulated period [EUR]"].loc[
                    "Total_without_heatpump"
                ]
                total_rest_investment_cost_per_simulated_period_without_hp = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    "Total_without_heatpump"
                ]
                total_device_co2_footprint_per_simulated_period_without_hp = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    "Total_without_heatpump"
                ]
                total_investment_cost_per_simulated_period_only_hp = capex_df["Investment for simulated period [EUR]"].loc[
                    "Total_only_heatpump"
                ]
                total_rest_investment_cost_per_simulated_period_only_hp = capex_df["Rest-Investment for simulated period [EUR]"].loc[
                    "Total_only_heatpump"
                ]
                total_device_co2_footprint_per_simulated_period_only_hp = capex_df["Device CO2-footprint for simulated period [kg]"].loc[
                    "Total_only_heatpump"
                ]
        else:
            log.warning("CAPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_CAPEX")
            total_investment_cost_per_simulated_period = 0
            total_rest_investment_cost_per_simulated_period = 0
            total_device_co2_footprint_per_simulated_period = 0
            total_investment_cost_per_simulated_period_without_hp = 0
            total_rest_investment_cost_per_simulated_period_without_hp = 0
            total_device_co2_footprint_per_simulated_period_without_hp = 0
            total_investment_cost_per_simulated_period_only_hp = 0
            total_rest_investment_cost_per_simulated_period_only_hp = 0
            total_device_co2_footprint_per_simulated_period_only_hp = 0

        # make kpi entry
        total_electricity_costs_entry = KpiEntry(
            name="Costs of grid electricity for simulated period",
            unit="EUR",
            value=electricity_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_electricity_co2_footprint_entry = KpiEntry(
            name="CO2 footprint of grid electricity for simulated period",
            unit="kg",
            value=electricity_co2_in_kg,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_gas_costs_entry = KpiEntry(
            name="Costs of grid gas for simulated period",
            unit="EUR",
            value=gas_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_gas_co2_emissions_entry = KpiEntry(
            name="CO2 footprint of grid gas for simulated period",
            unit="kg",
            value=gas_co2_in_kg,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_heat_costs_entry = KpiEntry(
            name="Costs of grid heat for simulated period",
            unit="EUR",
            value=heating_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_heat_co2_emissions_entry = KpiEntry(
            name="CO2 footprint of grid heat consumption for simulated period",
            unit="kg",
            value=heating_co2_in_kg,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_investment_cost_per_simulated_period_entry = KpiEntry(
            name="Investment costs for equipment per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_rest_investment_cost_per_simulated_period_entry = KpiEntry(
            name="Investment costs for equipment per simulated period minus subsidies",
            unit="EUR",
            value=total_rest_investment_cost_per_simulated_period,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_device_co2_footprint_per_simulated_period_entry = KpiEntry(
            name="CO2 footprint for equipment per simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_maintenance_cost_entry = KpiEntry(
            name="Maintenance costs for simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )

        total_energy_cost_entry = KpiEntry(
            name="Energy grid costs for simulated period",
            unit="EUR",
            value=gas_costs_in_euro
            + electricity_costs_in_euro
            + heating_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )

        total_cost_entry = KpiEntry(
            name="Total costs for simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period
            + total_rest_investment_cost_per_simulated_period
            + gas_costs_in_euro
            + electricity_costs_in_euro
            + heating_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_emissions_entry = KpiEntry(
            name="Total CO2 emissions for simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period
            + gas_co2_in_kg
            + electricity_co2_in_kg
            + heating_co2_in_kg,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )

        total_investment_cost_per_simulated_period_without_hp_entry = KpiEntry(
            name="Investment costs for equipment without heatpump per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_without_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_rest_investment_cost_per_simulated_period_without_hp_entry = KpiEntry(
            name="Investment costs for equipment without heatpump per simulated period minus subsidies",
            unit="EUR",
            value=total_rest_investment_cost_per_simulated_period_without_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_device_co2_footprint_per_simulated_period_without_hp_entry = KpiEntry(
            name="CO2 footprint for equipment without heatpump per simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period_without_hp,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_maintenance_cost_without_hp_entry = KpiEntry(
            name="Maintenance costs without heatpump for simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_without_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_cost_without_hp_entry = KpiEntry(
            name="Total costs without heatpump for simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_without_hp
            + total_rest_investment_cost_per_simulated_period_without_hp
            + gas_costs_in_euro
            + electricity_costs_in_euro
            + heating_costs_in_euro,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_emissions_without_hp_entry = KpiEntry(
            name="Total CO2 emissions without heatpump for simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period_without_hp
            + gas_co2_in_kg
            + electricity_co2_in_kg
            + heating_co2_in_kg,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )

        total_investment_cost_per_simulated_period_only_hp_entry = KpiEntry(
            name="Investment costs for equipment only heatpump per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_only_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_rest_investment_cost_per_simulated_period_only_hp_entry = KpiEntry(
            name="Investment costs for equipment only heatpump per simulated period minus subsidies",
            unit="EUR",
            value=total_rest_investment_cost_per_simulated_period_only_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_device_co2_footprint_per_simulated_period_only_hp_entry = KpiEntry(
            name="CO2 footprint for equipment only heatpump per simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period_only_hp,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )
        total_maintenance_cost_only_hp_entry = KpiEntry(
            name="Maintenance costs only heatpump for simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_only_hp,
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_cost_only_hp_entry = KpiEntry(
            name="Total costs only heatpump for simulated period",
            unit="EUR",
            value=(
                total_maintenance_cost_per_simulated_period_only_hp + total_rest_investment_cost_per_simulated_period_only_hp
            ),
            tag=(
                KpiTagEnumClass.COSTS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.COSTS_DISTRICT_GRID
            ),
        )
        total_emissions_only_hp_entry = KpiEntry(
            name="Total CO2 emissions only heatpump for simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period_only_hp,
            tag=(
                KpiTagEnumClass.EMISSIONS
                if not any(word in building_object for word in DistrictNames)
                else KpiTagEnumClass.EMISSIONS_DISTRICT_GRID
            ),
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[building_object].update(
            {
                total_electricity_costs_entry.name: total_electricity_costs_entry.to_dict(),
                total_electricity_co2_footprint_entry.name: total_electricity_co2_footprint_entry.to_dict(),
                total_gas_costs_entry.name: total_gas_costs_entry.to_dict(),
                total_gas_co2_emissions_entry.name: total_gas_co2_emissions_entry.to_dict(),
                total_heat_costs_entry.name: total_heat_costs_entry.to_dict(),
                total_heat_co2_emissions_entry.name: total_heat_co2_emissions_entry.to_dict(),
                total_investment_cost_per_simulated_period_entry.name: total_investment_cost_per_simulated_period_entry.to_dict(),
                total_rest_investment_cost_per_simulated_period_entry.name: total_rest_investment_cost_per_simulated_period_entry.to_dict(),
                total_device_co2_footprint_per_simulated_period_entry.name: total_device_co2_footprint_per_simulated_period_entry.to_dict(),
                total_energy_cost_entry.name: total_energy_cost_entry.to_dict(),
                total_maintenance_cost_entry.name: total_maintenance_cost_entry.to_dict(),
                total_cost_entry.name: total_cost_entry.to_dict(),
                total_emissions_entry.name: total_emissions_entry.to_dict(),
                total_investment_cost_per_simulated_period_without_hp_entry.name: total_investment_cost_per_simulated_period_without_hp_entry.to_dict(),
                total_rest_investment_cost_per_simulated_period_without_hp_entry.name: total_rest_investment_cost_per_simulated_period_without_hp_entry.to_dict(),
                total_device_co2_footprint_per_simulated_period_without_hp_entry.name: total_device_co2_footprint_per_simulated_period_without_hp_entry.to_dict(),
                total_maintenance_cost_without_hp_entry.name: total_maintenance_cost_without_hp_entry.to_dict(),
                total_cost_without_hp_entry.name: total_cost_without_hp_entry.to_dict(),
                total_emissions_without_hp_entry.name: total_emissions_without_hp_entry.to_dict(),
                total_investment_cost_per_simulated_period_only_hp_entry.name: total_investment_cost_per_simulated_period_only_hp_entry.to_dict(),
                total_rest_investment_cost_per_simulated_period_only_hp_entry.name: total_rest_investment_cost_per_simulated_period_only_hp_entry.to_dict(),
                total_device_co2_footprint_per_simulated_period_only_hp_entry.name: total_device_co2_footprint_per_simulated_period_only_hp_entry.to_dict(),
                total_maintenance_cost_only_hp_entry.name: total_maintenance_cost_only_hp_entry.to_dict(),
                total_cost_only_hp_entry.name: total_cost_only_hp_entry.to_dict(),
                total_emissions_only_hp_entry.name: total_emissions_only_hp_entry.to_dict(),
            }
        )

    def create_overall_district_kpi_collection(self, district_name: str) -> Tuple[
        float,
        float,
        float,
    ]:
        """Overall kpis for districts."""
        electricity_consumption_all_single_buildings_in_kilowatt_hour = 0.0
        electricity_produktion_all_single_buildings_in_kilowatt_hour = 0.0
        self_consumption_all_single_buildings_in_kilowatt_hour = 0.0
        electricity_production_district_in_kilowatt_hour = 0.0
        electricity_consumption_district_in_kilowatt_hour = 0.0
        self_consumption_district_in_kilowatt_hour = 0.0

        for building_objects_in_district in self.building_objects_in_district_list:
            if not any(word in building_objects_in_district for word in DistrictNames):
                electricity_consumption_all_single_buildings_in_kilowatt_hour += self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Total electricity consumption"]["value"]
                electricity_produktion_all_single_buildings_in_kilowatt_hour += self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Total electricity production"]["value"]
                self_consumption_all_single_buildings_in_kilowatt_hour += self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Self-consumption of electricity"]["value"]

            if any(word in building_objects_in_district for word in DistrictNames):
                electricity_production_district_in_kilowatt_hour += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district]["Total electricity production"][
                        "value"
                    ]
                    - self.kpi_collection_dict_unsorted[building_objects_in_district]["Building production"]["value"]
                )
                electricity_consumption_district_in_kilowatt_hour += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district]["Total electricity consumption"][
                        "value"
                    ]
                    - self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Total building electricity consumption"
                    ]["value"]
                )
                self_consumption_district_in_kilowatt_hour += self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Self-consumption of electricity"]["value"]

        overall_production_district_in_kilowatt_hour = (
            electricity_produktion_all_single_buildings_in_kilowatt_hour
            + electricity_production_district_in_kilowatt_hour
        )

        overall_consumption_district_in_kilowatt_hour = (
            electricity_consumption_all_single_buildings_in_kilowatt_hour
            + electricity_consumption_district_in_kilowatt_hour
        )

        overall_self_consumption_district_in_kilowatt_hour = (
            self_consumption_all_single_buildings_in_kilowatt_hour + self_consumption_district_in_kilowatt_hour
        )

        electricity_consumption_all_single_buildings_entry = KpiEntry(
            name="Electricity consumption of all single buildings",
            unit="kWh",
            value=electricity_consumption_all_single_buildings_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        electricity_production_all_single_buildings_entry = KpiEntry(
            name="Electricity production of all single buildings",
            unit="kWh",
            value=electricity_produktion_all_single_buildings_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        self_consumption_all_single_buildings_entry = KpiEntry(
            name="Self-consumption of all single buildings",
            unit="kWh",
            value=self_consumption_all_single_buildings_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        electricity_production_district_entry = KpiEntry(
            name="Electricity production of district without buildings",
            unit="kWh",
            value=electricity_production_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        electricity_consumption_district_entry = KpiEntry(
            name="Electricity consumption of district without buildings",
            unit="kWh",
            value=electricity_consumption_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        self_consumption_district_entry = KpiEntry(
            name="Self-consumption of district without buildings",
            unit="kWh",
            value=self_consumption_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        overall_production_district_entry = KpiEntry(
            name="Overall electricity production in district",
            unit="kWh",
            value=overall_production_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        overall_consumption_district_entry = KpiEntry(
            name="Overall electricity consumption in district",
            unit="kWh",
            value=overall_consumption_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )
        overall_self_consumption_district_entry = KpiEntry(
            name="Overall self-consumption in district",
            unit="kWh",
            value=overall_self_consumption_district_in_kilowatt_hour,
            tag=KpiTagEnumClass.GENERAL,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted[district_name].update(
            {
                electricity_consumption_all_single_buildings_entry.name: electricity_consumption_all_single_buildings_entry.to_dict(),
                electricity_production_all_single_buildings_entry.name: electricity_production_all_single_buildings_entry.to_dict(),
                self_consumption_all_single_buildings_entry.name: self_consumption_all_single_buildings_entry.to_dict(),
                electricity_production_district_entry.name: electricity_production_district_entry.to_dict(),
                electricity_consumption_district_entry.name: electricity_consumption_district_entry.to_dict(),
                self_consumption_district_entry.name: self_consumption_district_entry.to_dict(),
                overall_production_district_entry.name: overall_production_district_entry.to_dict(),
                overall_consumption_district_entry.name: overall_consumption_district_entry.to_dict(),
                overall_self_consumption_district_entry.name: overall_self_consumption_district_entry.to_dict(),
            }
        )

        return (
            overall_production_district_in_kilowatt_hour,
            overall_consumption_district_in_kilowatt_hour,
            overall_self_consumption_district_in_kilowatt_hour,
        )

    def create_overall_district_costs_collection(self, district_name):
        """Overall kpis for districts."""

        total_investment_cost_for_equipment_per_simulated_period_all_single_buildings = 0.0
        total_investment_cost_per_simulated_period_without_hp_all_single_buildings = 0.0
        total_investment_cost_per_simulated_period_only_hp_all_single_buildings = 0.0
        total_maintenance_cost_per_simulated_period_all_single_buildings = 0.0
        total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings = 0.0
        total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings = 0.0

        total_investment_cost_for_equipment_per_simulated_period_district = 0.0
        total_investment_cost_per_simulated_period_without_hp_district = 0.0
        total_investment_cost_per_simulated_period_only_hp_district = 0.0
        total_maintenance_cost_per_simulated_period_district = 0.0
        total_maintenance_cost_per_simulated_period_without_hp_district = 0.0
        total_maintenance_cost_per_simulated_period_only_hp_district = 0.0

        for building_objects_in_district in self.building_objects_in_district_list:
            if not any(word in building_objects_in_district for word in DistrictNames):
                total_investment_cost_for_equipment_per_simulated_period_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Investment costs for equipment per simulated period"
                    ]["value"]
                )

                total_investment_cost_per_simulated_period_without_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Investment costs for equipment without heatpump per simulated period"
                    ]["value"]
                )

                total_investment_cost_per_simulated_period_only_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Investment costs for equipment only heatpump per simulated period"
                    ]["value"]
                )

                total_maintenance_cost_per_simulated_period_all_single_buildings += self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Maintenance costs for simulated period"]["value"]

                total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Maintenance costs without heatpump for simulated period"
                    ]["value"]
                )

                total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "Maintenance costs only heatpump for simulated period"
                    ]["value"]
                )

            if any(word in building_objects_in_district for word in DistrictNames):
                total_investment_cost_for_equipment_per_simulated_period_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Investment costs for equipment per simulated period"]["value"]

                total_investment_cost_per_simulated_period_without_hp_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Investment costs for equipment without heatpump per simulated period"]["value"]

                total_investment_cost_per_simulated_period_only_hp_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Investment costs for equipment only heatpump per simulated period"]["value"]

                total_maintenance_cost_per_simulated_period_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Maintenance costs for simulated period"]["value"]

                total_maintenance_cost_per_simulated_period_without_hp_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Maintenance costs without heatpump for simulated period"]["value"]

                total_maintenance_cost_per_simulated_period_only_hp_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["Maintenance costs only heatpump for simulated period"]["value"]

        overall_investment_cost_for_equipment_per_simulated_period_district = (
            total_investment_cost_for_equipment_per_simulated_period_all_single_buildings
            + total_investment_cost_for_equipment_per_simulated_period_district
        )

        overall_investment_cost_per_simulated_period_without_hp_district = (
            total_investment_cost_per_simulated_period_without_hp_all_single_buildings
            + total_investment_cost_per_simulated_period_without_hp_district
        )

        overall_investment_cost_per_simulated_period_only_hp_district = (
            total_investment_cost_per_simulated_period_only_hp_all_single_buildings
            + total_investment_cost_per_simulated_period_only_hp_district
        )

        overall_maintenance_cost_per_simulated_period_district = (
            total_maintenance_cost_per_simulated_period_all_single_buildings
            + total_maintenance_cost_per_simulated_period_district
        )

        overall_maintenance_cost_per_simulated_period_without_hp_district = (
            total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings
            + total_maintenance_cost_per_simulated_period_without_hp_district
        )

        overall_maintenance_cost_per_simulated_period_only_hp_district = (
            total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings
            + total_maintenance_cost_per_simulated_period_only_hp_district
        )

        total_investment_cost_for_equipment_per_simulated_period_all_single_buildings_entry = KpiEntry(
            name="Total investment costs for equipment for all single buildings per simulated period",
            unit="EUR",
            value=total_investment_cost_for_equipment_per_simulated_period_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_investment_cost_per_simulated_period_without_hp_all_single_buildings_entry = KpiEntry(
            name="Total investment costs without heatpump for all single buildings per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_without_hp_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_investment_cost_per_simulated_period_only_hp_all_single_buildings_entry = KpiEntry(
            name="Total investment costs only heatpump for all single buildings per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_only_hp_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_all_single_buildings_entry = KpiEntry(
            name="Total maintenance costs for all single buildings per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings_entry = KpiEntry(
            name="Total maintenance costs without heatpump for all single buildings per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings_entry = KpiEntry(
            name="Total maintenance costs only heatpump for all single buildings per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings,
            tag=KpiTagEnumClass.COSTS,
        )
        total_investment_cost_for_equipment_per_simulated_period_district_entry = KpiEntry(
            name="Total investment costs for equipment for district without buildings per simulated period",
            unit="EUR",
            value=total_investment_cost_for_equipment_per_simulated_period_district,
            tag=KpiTagEnumClass.COSTS,
        )
        total_investment_cost_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="Total investment costs without heatpump for district per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        total_investment_cost_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="Total investment costs only heatpump for district per simulated period",
            unit="EUR",
            value=total_investment_cost_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_district_entry = KpiEntry(
            name="Total maintenance costs for district without buildings per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_district,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="Total maintenance costs without heatpump for district per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        total_maintenance_cost_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="Total maintenance costs only heatpump for district per simulated period",
            unit="EUR",
            value=total_maintenance_cost_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_investment_cost_for_equipment_per_simulated_period_district_entry = KpiEntry(
            name="Overall investment for equipment costs in district per simulated period",
            unit="EUR",
            value=overall_investment_cost_for_equipment_per_simulated_period_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_investment_cost_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="Overall investment costs without heatpump in district per simulated period",
            unit="EUR",
            value=overall_investment_cost_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_investment_cost_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="Overall investment costs only heatpump in district per simulated period",
            unit="EUR",
            value=overall_investment_cost_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_maintenance_cost_per_simulated_period_district_entry = KpiEntry(
            name="Overall maintenance costs in district per simulated period",
            unit="EUR",
            value=overall_maintenance_cost_per_simulated_period_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_maintenance_cost_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="Overall maintenance costs without heatpump in district per simulated period",
            unit="EUR",
            value=overall_maintenance_cost_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )
        overall_maintenance_cost_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="Overall maintenance costs only heatpump in district per simulated period",
            unit="EUR",
            value=overall_maintenance_cost_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.COSTS,
        )

        self.kpi_collection_dict_unsorted[district_name].update(
            {
                total_investment_cost_for_equipment_per_simulated_period_all_single_buildings_entry.name:
                    total_investment_cost_for_equipment_per_simulated_period_all_single_buildings_entry.to_dict(),
                total_investment_cost_per_simulated_period_without_hp_all_single_buildings_entry.name:
                    total_investment_cost_per_simulated_period_without_hp_all_single_buildings_entry.to_dict(),
                total_investment_cost_per_simulated_period_only_hp_all_single_buildings_entry.name:
                    total_investment_cost_per_simulated_period_only_hp_all_single_buildings_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_all_single_buildings_entry.name:
                    total_maintenance_cost_per_simulated_period_all_single_buildings_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings_entry.name:
                    total_maintenance_cost_per_simulated_period_without_hp_all_single_buildings_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings_entry.name:
                    total_maintenance_cost_per_simulated_period_only_hp_all_single_buildings_entry.to_dict(),
                total_investment_cost_for_equipment_per_simulated_period_district_entry.name:
                    total_investment_cost_for_equipment_per_simulated_period_district_entry.to_dict(),
                total_investment_cost_per_simulated_period_without_hp_district_entry.name:
                    total_investment_cost_per_simulated_period_without_hp_district_entry.to_dict(),
                total_investment_cost_per_simulated_period_only_hp_district_entry.name:
                    total_investment_cost_per_simulated_period_only_hp_district_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_district_entry.name:
                    total_maintenance_cost_per_simulated_period_district_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_without_hp_district_entry.name:
                    total_maintenance_cost_per_simulated_period_without_hp_district_entry.to_dict(),
                total_maintenance_cost_per_simulated_period_only_hp_district_entry.name:
                    total_maintenance_cost_per_simulated_period_only_hp_district_entry.to_dict(),
                overall_investment_cost_for_equipment_per_simulated_period_district_entry.name:
                    overall_investment_cost_for_equipment_per_simulated_period_district_entry.to_dict(),
                overall_investment_cost_per_simulated_period_without_hp_district_entry.name:
                    overall_investment_cost_per_simulated_period_without_hp_district_entry.to_dict(),
                overall_investment_cost_per_simulated_period_only_hp_district_entry.name:
                    overall_investment_cost_per_simulated_period_only_hp_district_entry.to_dict(),
                overall_maintenance_cost_per_simulated_period_district_entry.name:
                    overall_maintenance_cost_per_simulated_period_district_entry.to_dict(),
                overall_maintenance_cost_per_simulated_period_without_hp_district_entry.name:
                    overall_maintenance_cost_per_simulated_period_without_hp_district_entry.to_dict(),
                overall_maintenance_cost_per_simulated_period_only_hp_district_entry.name:
                    overall_maintenance_cost_per_simulated_period_only_hp_district_entry.to_dict(),
            }
        )

    def create_overall_district_emissions_collection(self, district_name):
        """Overall kpis for districts."""

        total_co2_emissions_for_equipment_per_simulated_period_all_single_buildings = 0.0
        total_co2_emissions_for_equipment_per_simulated_period_without_hp_all_single_buildings = 0.0
        total_co2_emissions_for_equipment_per_simulated_period_only_hp_all_single_buildings = 0.0

        total_co2_emissions_for_equipment_per_simulated_period_district = 0.0
        total_co2_emissions_for_equipment_per_simulated_period_without_hp_district = 0.0
        total_co2_emissions_for_equipment_per_simulated_period_only_hp_district = 0.0

        for building_objects_in_district in self.building_objects_in_district_list:
            if not any(word in building_objects_in_district for word in DistrictNames):
                total_co2_emissions_for_equipment_per_simulated_period_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "CO2 footprint for equipment per simulated period"
                    ]["value"]
                )

                total_co2_emissions_for_equipment_per_simulated_period_without_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "CO2 footprint for equipment without heatpump per simulated period"
                    ]["value"]
                )

                total_co2_emissions_for_equipment_per_simulated_period_only_hp_all_single_buildings += (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "CO2 footprint for equipment only heatpump per simulated period"
                    ]["value"]
                )

            if any(word in building_objects_in_district for word in DistrictNames):
                total_co2_emissions_for_equipment_per_simulated_period_district = self.kpi_collection_dict_unsorted[
                    building_objects_in_district
                ]["CO2 footprint for equipment per simulated period"]["value"]

                total_co2_emissions_for_equipment_per_simulated_period_without_hp_district = (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "CO2 footprint for equipment without heatpump per simulated period"
                    ]["value"]
                )

                total_co2_emissions_for_equipment_per_simulated_period_only_hp_district = (
                    self.kpi_collection_dict_unsorted[building_objects_in_district][
                        "CO2 footprint for equipment only heatpump per simulated period"
                    ]["value"]
                )

        overall_co2_emission_for_equipment_per_simulated_period_district = (
            total_co2_emissions_for_equipment_per_simulated_period_all_single_buildings
            + total_co2_emissions_for_equipment_per_simulated_period_district
        )

        overall_co2_emission_per_simulated_period_without_hp_district = (
            total_co2_emissions_for_equipment_per_simulated_period_without_hp_all_single_buildings
            + total_co2_emissions_for_equipment_per_simulated_period_without_hp_district
        )

        overall_co2_emission_per_simulated_period_only_hp_district = (
            total_co2_emissions_for_equipment_per_simulated_period_only_hp_all_single_buildings
            + total_co2_emissions_for_equipment_per_simulated_period_only_hp_district
        )

        total_co2_emissions_per_simulated_period_all_single_buildings_entry = KpiEntry(
            name="CO2 footprint for equipment for all single buildings per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_all_single_buildings,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        total_co2_emissions_per_simulated_period_without_hp_all_single_buildings_entry = KpiEntry(
            name="CO2 footprint for equipment without heatpump for all single buildings per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_without_hp_all_single_buildings,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        total_co2_emissions_per_simulated_period_only_hp_all_single_buildings_entry = KpiEntry(
            name="CO2 footprint for equipment only heatpump for all single buildings per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_only_hp_all_single_buildings,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        total_co2_emissions_for_equipment_per_simulated_period_district_entry = KpiEntry(
            name="CO2 footprint for equipment for district without buildings per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        total_co2_emissions_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="CO2 footprint for equipment without heatpump for district per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        total_co2_emissions_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="CO2 footprint for equipment only heatpump for district per simulated period",
            unit="kg",
            value=total_co2_emissions_for_equipment_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        overall_co2_emissions_for_equipment_per_simulated_period_district_entry = KpiEntry(
            name="Overall CO2 footprint for equipment in district per simulated period",
            unit="kg",
            value=overall_co2_emission_for_equipment_per_simulated_period_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        overall_co2_emissions_per_simulated_period_without_hp_district_entry = KpiEntry(
            name="Overall CO2 footprint for equipment without heatpump in district per simulated period",
            unit="kg",
            value=overall_co2_emission_per_simulated_period_without_hp_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )
        overall_co2_emissions_per_simulated_period_only_hp_district_entry = KpiEntry(
            name="Overall CO2 footprint for equipment only heatpump in district per simulated period",
            unit="kg",
            value=overall_co2_emission_per_simulated_period_only_hp_district,
            tag=KpiTagEnumClass.EMISSIONS,
        )

        self.kpi_collection_dict_unsorted[district_name].update(
            {
                total_co2_emissions_per_simulated_period_all_single_buildings_entry.name:
                    total_co2_emissions_per_simulated_period_all_single_buildings_entry.to_dict(),
                total_co2_emissions_per_simulated_period_without_hp_all_single_buildings_entry.name:
                    total_co2_emissions_per_simulated_period_without_hp_all_single_buildings_entry.to_dict(),
                total_co2_emissions_per_simulated_period_only_hp_all_single_buildings_entry.name:
                    total_co2_emissions_per_simulated_period_only_hp_all_single_buildings_entry.to_dict(),
                total_co2_emissions_for_equipment_per_simulated_period_district_entry.name:
                    total_co2_emissions_for_equipment_per_simulated_period_district_entry.to_dict(),
                total_co2_emissions_per_simulated_period_without_hp_district_entry.name:
                    total_co2_emissions_per_simulated_period_without_hp_district_entry.to_dict(),
                total_co2_emissions_per_simulated_period_only_hp_district_entry.name:
                    total_co2_emissions_per_simulated_period_only_hp_district_entry.to_dict(),
                overall_co2_emissions_for_equipment_per_simulated_period_district_entry.name:
                    overall_co2_emissions_for_equipment_per_simulated_period_district_entry.to_dict(),
                overall_co2_emissions_per_simulated_period_without_hp_district_entry.name:
                    overall_co2_emissions_per_simulated_period_without_hp_district_entry.to_dict(),
                overall_co2_emissions_per_simulated_period_only_hp_district_entry.name:
                    overall_co2_emissions_per_simulated_period_only_hp_district_entry.to_dict(),
            }
        )

    def create_overall_district_contracting_collection(self, district_name, all_outputs):
        """If buildings are in contracting with district, heatpump costs and emissions are on district side."""

        set_of_buildings_in_contracting = set()
        for building_objekt in self.building_objects_in_district_list:
            for output in all_outputs:
                if building_objekt == output.component_name.split("_")[0] and district_name in output.component_name:
                    set_of_buildings_in_contracting.add(building_objekt)

        if district_name in set_of_buildings_in_contracting:
            set_of_buildings_in_contracting.remove(district_name)
        list_of_buildings_in_contracting = list(set_of_buildings_in_contracting)

        if len(list_of_buildings_in_contracting) == 0:
            pass
        else:
            number_of_building_in_contracting = len(list_of_buildings_in_contracting)

            total_co2_emissions_for_hp_of_building_per_simulated_period_contracting = 0.0
            total_investment_cost_for_hp_of_building_per_simulated_period_contracting = 0.0
            total_maintenance_cost_for_hp_of_building_per_simulated_period_contracting = 0.0

            for building in list_of_buildings_in_contracting:
                total_co2_emissions_for_hp_of_building_per_simulated_period_contracting += (
                    self.kpi_collection_dict_unsorted[building][
                        "CO2 footprint for equipment only heatpump per simulated period"
                    ]["value"]
                )
                total_investment_cost_for_hp_of_building_per_simulated_period_contracting += (
                    self.kpi_collection_dict_unsorted[building][
                        "Investment costs for equipment only heatpump per simulated period"
                    ]["value"]
                )
                total_maintenance_cost_for_hp_of_building_per_simulated_period_contracting += (
                    self.kpi_collection_dict_unsorted[building][
                        "Maintenance costs only heatpump for simulated period"
                    ]["value"]
                )

            total_co2_emissions_of_district_grid_per_simulated_period_contracting = (
                self.kpi_collection_dict_unsorted[district_name][
                    "Total CO2 emissions for simulated period"
                ]["value"]
            )

            total_costs_of_district_grid_per_simulated_period_contracting = (
                self.kpi_collection_dict_unsorted[district_name][
                    "Total costs for simulated period"
                ]["value"]
            )

            overall_district_cost_contracting = \
                (total_costs_of_district_grid_per_simulated_period_contracting +
                 total_investment_cost_for_hp_of_building_per_simulated_period_contracting +
                 total_maintenance_cost_for_hp_of_building_per_simulated_period_contracting)

            overall_district_co2_emissions_contracting = \
                (total_co2_emissions_of_district_grid_per_simulated_period_contracting +
                 total_co2_emissions_for_hp_of_building_per_simulated_period_contracting)

            number_of_building_in_contracting_entry = KpiEntry(
                name="Number of buildings in district contracting",
                unit="-",
                value=number_of_building_in_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )

            total_co2_emissions_for_hp_of_building_in_contracting_entry = KpiEntry(
                name="CO2 footprint of hp only of buildings in contracting",
                unit="kg",
                value=total_co2_emissions_for_hp_of_building_per_simulated_period_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )

            total_investment_cost_for_hp_of_building_in_contracting_entry = KpiEntry(
                name="Investment cost of hp only of buildings in contracting",
                unit="EUR",
                value=total_investment_cost_for_hp_of_building_per_simulated_period_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )

            total_maintenance_cost_for_hp_of_building_contracting_entry = KpiEntry(
                name="Maintenance cost of hp only of buildings in contracting",
                unit="EUR",
                value=total_maintenance_cost_for_hp_of_building_per_simulated_period_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )
            overall_district_cost_contracting_entry = KpiEntry(
                name="Overall costs for grid and hp in contracting per simulated period",
                unit="EUR",
                value=overall_district_cost_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )
            overall_district_co2_emissions_contracting_entry = KpiEntry(
                name="Overall CO2 footprint for grid and hp in contracting per simulated period",
                unit="kg",
                value=overall_district_co2_emissions_contracting,
                tag=KpiTagEnumClass.CONTRACTING,
            )

            self.kpi_collection_dict_unsorted[district_name].update(
                {
                    number_of_building_in_contracting_entry.name:
                        number_of_building_in_contracting_entry.to_dict(),
                    total_co2_emissions_for_hp_of_building_in_contracting_entry.name:
                        total_co2_emissions_for_hp_of_building_in_contracting_entry.to_dict(),
                    total_investment_cost_for_hp_of_building_in_contracting_entry.name:
                        total_investment_cost_for_hp_of_building_in_contracting_entry.to_dict(),
                    total_maintenance_cost_for_hp_of_building_contracting_entry.name:
                        total_maintenance_cost_for_hp_of_building_contracting_entry.to_dict(),
                    overall_district_cost_contracting_entry.name:
                        overall_district_cost_contracting_entry.to_dict(),
                    overall_district_co2_emissions_contracting_entry.name:
                        overall_district_co2_emissions_contracting_entry.to_dict(),
                }
            )

    def get_all_component_kpis(self, wrapped_components: List[ComponentWrapper]) -> None:
        """Go through all components and get their KPIs if implemented."""
        my_component_kpi_entry_list: List[KpiEntry]

        self.kpi_collection_dict_unsorted = {
            building_objects: {} for building_objects in self.building_objects_in_district_list
        }

        for wrapped_component in wrapped_components:
            my_component = wrapped_component.my_component
            # get KPIs of respective component
            my_component_kpi_entry_list = my_component.get_component_kpi_entries(
                all_outputs=self.all_outputs, postprocessing_results=self.results
            )

            if my_component_kpi_entry_list != []:
                # add all KPI entries to kpi dict
                for kpi_entry in my_component_kpi_entry_list:

                    for object_name in self.kpi_collection_dict_unsorted.keys():
                        if (object_name == my_component.component_name.split("_")[0] or
                                not self.simulation_parameters.multiple_buildings):
                            self.kpi_collection_dict_unsorted[object_name][kpi_entry.name] = kpi_entry.to_dict()
                            break
            else:
                log.debug(
                    "KPI generation for "
                    + my_component.component_name
                    + " was not successful. KPI method is maybe not implemented yet."
                )
