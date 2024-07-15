# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate.

KPis for PV-battery systems in houses:
https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
"""

import os
from typing import List, Tuple, Union, Dict, Optional
from pathlib import Path
import pandas as pd
from hisim.component import ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.utils import HISIMPATH
from hisim import log
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry


class KpiPreparation:

    """Class for generating and calculating key performance indicators."""

    def __init__(self, post_processing_data_transfer: PostProcessingDataTransfer):
        """Initialize further variables."""
        self.post_processing_data_transfer = post_processing_data_transfer
        self.kpi_collection_dict_unsorted: Dict = {}
        # get important variables
        self.wrapped_components = self.post_processing_data_transfer.wrapped_components
        self.results = self.post_processing_data_transfer.results
        self.all_outputs = self.post_processing_data_transfer.all_outputs
        self.simulation_parameters = self.post_processing_data_transfer.simulation_parameters
        self.get_all_component_kpis(wrapped_components=self.wrapped_components)

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

    def compute_autarky_according_to_solar_htw_berlin(
        self,
        relative_electricty_demand_in_percent: Optional[float],
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
        self,
        results: pd.DataFrame,
        all_outputs: List,
        timeresolution: int,
        price_frame: pd.DataFrame,
        fuel: LoadTypes,
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
        total_costs_for_energy_use_in_euro: float = 0.0
        total_co2_emitted_due_to_energy_use_in_kilogram: float = 0.0
        total_costs_for_electricity_use_in_euro: float = 0.0
        total_co2_emitted_due_to_electricity_use_in_kilogram: float = 0.0

        price_frame = self.read_in_fuel_costs()

        (
            electricity_price_consumption,
            electricity_price_injection,
        ) = self.search_electricity_prices_in_results(all_outputs=self.all_outputs, results=self.results)
        # Electricity Price
        electricity_price_constant, co2_price_constant = self.get_euro_and_co2(
            fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY
        )
        electricity_inj_price_constant, _ = self.get_euro_and_co2(fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY)

        if electricity_production_in_kilowatt_hour > 0:
            # evaluate electricity price
            if not electricity_price_injection.empty:
                total_costs_for_electricity_use_in_euro = (
                    total_costs_for_electricity_use_in_euro
                    - self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=injection[injection > 0] * electricity_price_injection[injection > 0],
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )
                total_costs_for_electricity_use_in_euro = (
                    total_costs_for_electricity_use_in_euro
                    + self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=result_dataframe["total_consumption"] - self_consumption,
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )  # Todo: is this correct? (maybe not so important, only used if generic_price_signal is used
            else:
                total_costs_for_electricity_use_in_euro = (
                    total_costs_for_electricity_use_in_euro
                    - grid_injection_in_kilowatt_hour * electricity_inj_price_constant
                    + (electricity_consumption_in_kilowatt_hour - self_consumption_in_kilowatt_hour)
                    * electricity_price_constant
                )

        else:
            if not electricity_price_consumption.empty:
                # substract self consumption from consumption for bill calculation
                total_costs_for_electricity_use_in_euro = (
                    total_costs_for_electricity_use_in_euro
                    + self.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=result_dataframe["total_consumption"] * electricity_price_consumption,
                        timeresolution=self.simulation_parameters.seconds_per_timestep,
                    )
                )
            else:
                total_costs_for_electricity_use_in_euro = (
                    total_costs_for_electricity_use_in_euro
                    + electricity_consumption_in_kilowatt_hour * electricity_price_constant
                )

        total_co2_emitted_due_to_electricity_use_in_kilogram = (
            total_co2_emitted_due_to_electricity_use_in_kilogram
            + (electricity_consumption_in_kilowatt_hour - self_consumption_in_kilowatt_hour) * co2_price_constant
        )
        # make kpi entry
        costs_for_electricity_use_entry = KpiEntry(
            name="Cost for use of electricity",
            unit="EUR",
            value=total_costs_for_electricity_use_in_euro,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        co2_emission_for_electricity_use_entry = KpiEntry(
            name="CO2 emission due to use of electricity",
            unit="kg",
            value=total_co2_emitted_due_to_electricity_use_in_kilogram,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )

        # update kpi collection dict
        self.kpi_collection_dict_unsorted.update(
            {
                costs_for_electricity_use_entry.name: costs_for_electricity_use_entry.to_dict(),
                co2_emission_for_electricity_use_entry.name: co2_emission_for_electricity_use_entry.to_dict(),
            }
        )

        # compute cost and co2 for LoadTypes other than electricity
        for fuel in [
            LoadTypes.GAS,
            LoadTypes.OIL,
            LoadTypes.DISTRICTHEATING,
            LoadTypes.DIESEL,
        ]:
            fuel_price_in_euro, fuel_co2_emission_in_kg = self.compute_cost_of_fuel_type(
                results=self.results,
                all_outputs=self.all_outputs,
                timeresolution=self.simulation_parameters.seconds_per_timestep,
                price_frame=price_frame,
                fuel=fuel,
            )
            total_co2_emitted_due_to_energy_use_in_kilogram = (
                total_co2_emitted_due_to_electricity_use_in_kilogram + fuel_co2_emission_in_kg
            )
            total_costs_for_energy_use_in_euro = total_costs_for_electricity_use_in_euro + fuel_price_in_euro

        # make kpi entry
        costs_for_energy_use_entry = KpiEntry(
            name="Total cost for energy use",
            unit="EUR",
            value=total_costs_for_energy_use_in_euro,
            tag=KpiTagEnumClass.COSTS_AND_EMISSIONS,
        )
        co2_emission_entry = KpiEntry(
            name="Total CO2 emission due to energy use",
            unit="kg",
            value=total_co2_emitted_due_to_energy_use_in_kilogram,
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

    def get_all_component_kpis(self, wrapped_components: List[ComponentWrapper]) -> None:
        """Go through all components and get their KPIs if implemented."""
        my_component_kpi_entry_list: List[KpiEntry]
        for wrapped_component in wrapped_components:
            my_component = wrapped_component.my_component
            # get KPIs of respective component

            my_component_kpi_entry_list = my_component.get_component_kpi_entries(
                all_outputs=self.all_outputs, postprocessing_results=self.results
            )

            if my_component_kpi_entry_list != []:
                log.debug("KPI generation for " + my_component.component_name + " was successful.")
                # add all KPI entries to kpi dict
                for kpi_entry in my_component_kpi_entry_list:
                    self.kpi_collection_dict_unsorted[kpi_entry.name] = kpi_entry.to_dict()
            else:
                log.debug(
                    "KPI generation for "
                    + my_component.component_name
                    + " was not successful. KPI method is maybe not implemented yet."
                )
