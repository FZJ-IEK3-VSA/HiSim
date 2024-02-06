# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from typing import List, Tuple, Union, Dict, Optional
from pathlib import Path
from dataclasses import dataclass, InitVar
import pandas as pd
from dataclass_wizard import JSONWizard
from hisim.component import ComponentOutput
from hisim.components.heat_distribution_system import HeatDistribution
from hisim.components.building import Building
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib
from hisim.components.electricity_meter import ElectricityMeter
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.utils import HISIMPATH
from hisim import log
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessingoptions import PostProcessingOptions


@dataclass
class KpiEntry(JSONWizard):

    """Class for storing one kpi entry."""

    name: str
    unit: str
    value: Optional[float]
    description: Optional[str] = None
    tag: Optional[str] = None


@dataclass
class KpiGenerator(JSONWizard):

    """Class for generating and calculating key performance indicators."""

    post_processing_data_transfer: InitVar[PostProcessingDataTransfer]

    def __post_init__(self, post_processing_data_transfer):
        """Build the dataclass from input data."""
        self.kpi_collection_dict: Dict = {}
        self.create_kpi_collection(post_processing_data_transfer)
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
        # get consumption and production and store in kpi collection
        (
            total_electricity_consumption_in_kilowatt_hour,
            total_electricity_production_in_kilowatt_hour,
        ) = self.compute_electricity_consumption_and_production(result_dataframe=self.filtered_result_dataframe)
        # get ratio between production and consumption
        self.compute_ratio_between_production_and_consumption(
            total_electricity_production_in_kwh=total_electricity_production_in_kilowatt_hour,
            total_electricity_consumption_in_kwh=total_electricity_consumption_in_kilowatt_hour
        )

        # get self-consumption, autarkie, injection, battery losses
        (
            grid_injection_in_kilowatt_hour,
            self_consumption_in_kilowatt_hour,
            self.filtered_result_dataframe,
        ) = self.compute_self_consumption_injection_autarky_and_battery_losses(
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
        self.compute_relative_electricity_demand_from_grid(
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
        )
        # get self-consumption rate according to mydualsun
        self.compute_self_consumption_rate_according_to_mydualsun(
            total_electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_to_grid_in_kilowatt_hour=total_electricity_to_grid_in_kwh,
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
        building_conditioned_floor_area_in_m2 = self.building_temperature_control_and_heating_load()
        # get heat distriution system kpis
        self.get_heat_distribution_system_kpis()
        # get heat pump performance indicators
        self.get_heat_pump_kpis(building_conditioned_floor_area_in_m2=building_conditioned_floor_area_in_m2)

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
        consumption_ids = []
        production_ids = []
        battery_charge_discharge_ids = []

        index: int
        output: ComponentOutput

        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None:
                if InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag:
                    production_ids.append(index)

                elif (
                    InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED in output.postprocessing_flag
                    or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in output.postprocessing_flag
                ):
                    consumption_ids.append(index)

                elif InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    if ComponentType.BATTERY in output.postprocessing_flag:
                        battery_charge_discharge_ids.append(index)
                    elif ComponentType.CAR_BATTERY in output.postprocessing_flag:
                        consumption_ids.append(index)
            else:
                continue

        result_dataframe = pd.DataFrame()
        result_dataframe["consumption"] = pd.DataFrame(results.iloc[:, consumption_ids]).clip(lower=0).sum(axis=1)
        result_dataframe["production"] = pd.DataFrame(results.iloc[:, production_ids]).clip(lower=0).sum(axis=1)

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

    def compute_electricity_consumption_and_production(self, result_dataframe: pd.DataFrame) -> Tuple[float, float]:
        """Compute electricity consumption and production."""

        # sum consumption and production over time
        electricity_consumption_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
            power_timeseries_in_watt=result_dataframe["consumption"],
            timeresolution=self.simulation_parameters.seconds_per_timestep,
        )

        electricity_production_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
            power_timeseries_in_watt=result_dataframe["production"],
            timeresolution=self.simulation_parameters.seconds_per_timestep,
        )

        # make kpi entry
        consumtion_entry = KpiEntry(name="Consumption", unit="kWh", value=electricity_consumption_in_kilowatt_hour)

        production_entry = KpiEntry(name="Production", unit="kWh", value=electricity_production_in_kilowatt_hour)

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {consumtion_entry.name: consumtion_entry.to_dict(), production_entry.name: production_entry.to_dict()}
        )

        return electricity_consumption_in_kilowatt_hour, electricity_production_in_kilowatt_hour

    def compute_self_consumption_injection_autarky_and_battery_losses(
        self,
        result_dataframe: pd.DataFrame,
        electricity_production_in_kilowatt_hour: float,
        electricity_consumption_in_kilowatt_hour: float,
    ) -> Tuple[float, float, pd.DataFrame]:
        """Computes the self consumption, grid injection, autarky and battery losses if electricty production is bigger than zero."""

        if electricity_production_in_kilowatt_hour > 0:
            # account for battery
            production_with_battery = result_dataframe["production"] + result_dataframe["battery_discharge"]
            consumption_with_battery = result_dataframe["consumption"] + result_dataframe["battery_charge"]

            # evaluate injection and sum over time
            grid_injection_series_in_watt: pd.Series = production_with_battery - consumption_with_battery

            # evaluate self consumption and immidiately sum over time
            # battery is charged (counting to consumption) and discharged (counting to production)
            # -> only one direction can be counted, otherwise the self-consumption can be greater than 100.
            # Here the production side is counted (battery_discharge).
            self_consumption_series_in_watt: pd.Series = (
                pd.concat(
                    (
                        production_with_battery[production_with_battery <= result_dataframe["consumption"]],
                        result_dataframe["consumption"][result_dataframe["consumption"] < production_with_battery],
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

            # compute battery losses
            battery_losses_in_kilowatt_hour = self.compute_battery_losses(result_dataframe=result_dataframe)

        else:
            self_consumption_series_in_watt = pd.Series([])
            grid_injection_series_in_watt = pd.Series([])
            self_consumption_in_kilowatt_hour = 0
            grid_injection_in_kilowatt_hour = 0
            self_consumption_rate_in_percent = 0
            autarky_rate_in_percent = 0
            battery_losses_in_kilowatt_hour = 0

        # add injection and self-consumption timeseries to result dataframe
        result_dataframe["self_consumption_in_watt"] = self_consumption_series_in_watt
        result_dataframe["grid_injection_in_watt"] = grid_injection_series_in_watt

        # make kpi entry
        grid_injection_entry = KpiEntry(name="Injection", unit="kWh", value=grid_injection_in_kilowatt_hour)
        self_consumption_entry = KpiEntry(name="Self-consumption", unit="kWh", value=self_consumption_in_kilowatt_hour)
        self_consumption_rate_entry = KpiEntry(
            name="Self-consumption rate", unit="%", value=self_consumption_rate_in_percent
        )
        autarkie_rate_entry = KpiEntry(name="Autarky rate", unit="%", value=autarky_rate_in_percent)
        battery_losses_entry = KpiEntry(name="Battery losses", unit="kWh", value=battery_losses_in_kilowatt_hour)

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {
                grid_injection_entry.name: grid_injection_entry.to_dict(),
                self_consumption_entry.name: self_consumption_entry.to_dict(),
                self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict(),
                autarkie_rate_entry.name: autarkie_rate_entry.to_dict(),
                battery_losses_entry.name: battery_losses_entry.to_dict(),
            }
        )
        return grid_injection_in_kilowatt_hour, self_consumption_in_kilowatt_hour, result_dataframe

    def compute_battery_losses(self, result_dataframe: pd.DataFrame) -> float:
        """Compute battery losses."""

        if not result_dataframe["battery_charge"].empty:
            battery_losses_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["battery_charge"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            ) - self.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=result_dataframe["battery_discharge"],
                timeresolution=self.simulation_parameters.seconds_per_timestep,
            )
        else:
            battery_losses_in_kilowatt_hour = 0.0

        return battery_losses_in_kilowatt_hour

    def get_electricity_to_and_from_grid_from_electricty_meter(self) -> Tuple[Optional[float], Optional[float]]:
        """Get the electricity injected into the grid or taken from grid measured by the electricity meter."""
        total_energy_from_grid_in_kwh = None
        total_energy_to_grid_in_kwh = None
        # go through all wrapped components and try to find electricity meter
        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, ElectricityMeter):
                total_energy_from_grid_in_kwh = wrapped_component.my_component.config.total_energy_from_grid_in_kwh
                total_energy_to_grid_in_kwh = wrapped_component.my_component.config.total_energy_to_grid_in_kwh

                break
        if total_energy_from_grid_in_kwh is None and total_energy_to_grid_in_kwh is None:
            log.warning(
                "KPI values for total energy to and from grid are None. "
                "Please check if you have correctly initialized and connected the electricity meter in your system setup. "
                f"Also check if you chose no. {str(PostProcessingOptions.COMPUTE_OPEX)} in your postprocessing options because this "
                "option is responsible for writing the energy to/from grid values into the electricity meter config."
            )
        # make kpi entry
        total_energy_from_grid_in_kwh_entry = KpiEntry(
            name="Total energy from grid", unit="kWh", value=total_energy_from_grid_in_kwh
        )
        total_energy_to_grid_in_kwh_entry = KpiEntry(
            name="Total energy to grid", unit="kWh", value=total_energy_to_grid_in_kwh
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {
                total_energy_from_grid_in_kwh_entry.name: total_energy_from_grid_in_kwh_entry.to_dict(),
                total_energy_to_grid_in_kwh_entry.name: total_energy_to_grid_in_kwh_entry.to_dict(),
            }
        )
        return total_energy_from_grid_in_kwh, total_energy_to_grid_in_kwh

    def compute_relative_electricity_demand_from_grid(
        self,
        total_electricity_consumption_in_kilowatt_hour: float,
        electricity_from_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Return the relative electricity demand."""
        if electricity_from_grid_in_kilowatt_hour is None:
            relative_electricity_demand_from_grid_in_percent = None
        else:
            relative_electricity_demand_from_grid_in_percent = (
                electricity_from_grid_in_kilowatt_hour / total_electricity_consumption_in_kilowatt_hour * 100
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
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {relative_electricity_demand_entry.name: relative_electricity_demand_entry.to_dict()}
        )

    def compute_ratio_between_production_and_consumption(self, total_electricity_production_in_kwh: float, total_electricity_consumption_in_kwh: float) -> None:
        """Compute the ration of electricity production and consumption."""
        ratio_in_percent = total_electricity_production_in_kwh / total_electricity_consumption_in_kwh * 100
        # make kpi entry
        ratio_in_percent_entry = KpiEntry(
            name="Ratio between energy production and consumption",
            unit="%",
            value=ratio_in_percent,
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {ratio_in_percent_entry.name: ratio_in_percent_entry.to_dict()}
        )

    def compute_self_consumption_rate_according_to_mydualsun(
        self,
        total_electricity_production_in_kilowatt_hour: float,
        electricity_to_grid_in_kilowatt_hour: Optional[float],
    ) -> None:
        """Return self-consumption according to dual sun.

        https://academy.dualsun.com/hc/en-us/articles/360018456939-How-is-the-self-consumption-rate-calculated-on-MyDualSun.
        """
        if electricity_to_grid_in_kilowatt_hour is None:
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
            name="Self-consumption rate according to mydualsun",
            unit="%",
            value=self_consumption_rate_in_percent,
        )

        # update kpi collection dict
        self.kpi_collection_dict.update({self_consumption_rate_entry.name: self_consumption_rate_entry.to_dict()})

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
        costs_for_energy_use_in_euro = 0.0
        co2_emitted_due_to_energy_use_in_kilogram = 0.0

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
                        power_timeseries_in_watt=result_dataframe["consumption"] - self_consumption,
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
                        power_timeseries_in_watt=result_dataframe["consumption"] * electricity_price_consumption,
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
            name="Cost for energy use", unit="EUR", value=costs_for_energy_use_in_euro
        )
        co2_emission_entry = KpiEntry(
            name="CO2 emission due to energy use", unit="kg", value=co2_emitted_due_to_energy_use_in_kilogram
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
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
        )
        total_device_co2_footprint_per_simulated_period_entry = KpiEntry(
            name="CO2 footprint for equipment per simulated period",
            unit="kg",
            value=total_device_co2_footprint_per_simulated_period,
        )
        total_operational_cost_entry = KpiEntry(
            name="System operational costs for simulated period",
            unit="EUR",
            value=total_operational_cost_per_simulated_period,
        )
        total_operational_emissions_entry = KpiEntry(
            name="System operational emissions for simulated period",
            unit="kg",
            value=total_operational_emissions_per_simulated_period,
        )
        total_cost_entry = KpiEntry(
            name="Total costs for simulated period",
            unit="EUR",
            value=total_operational_cost_per_simulated_period + total_investment_cost_per_simulated_period,
        )
        total_emissions_entry = KpiEntry(
            name="Total CO2 emissions for simulated period",
            unit="kg",
            value=total_operational_emissions_per_simulated_period + total_device_co2_footprint_per_simulated_period,
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {
                total_investment_cost_per_simulated_period_entry.name: total_investment_cost_per_simulated_period_entry.to_dict(),
                total_device_co2_footprint_per_simulated_period_entry.name: total_device_co2_footprint_per_simulated_period_entry.to_dict(),
                total_operational_cost_entry.name: total_operational_cost_entry.to_dict(),
                total_operational_emissions_entry.name: total_operational_emissions_entry.to_dict(),
                total_cost_entry.name: total_cost_entry.to_dict(),
                total_emissions_entry.name: total_emissions_entry.to_dict(),
            }
        )

    def building_temperature_control_and_heating_load(
        self,
    ) -> float:
        """Check the building indoor air temperature.

        Check for all timesteps and count the
        time when the temperature is outside of the building set temperatures
        in order to verify if energy system provides enough heating and cooling.
        """

        temperature_difference_of_building_being_below_heating_set_temperature = 0
        temperature_difference_of_building_being_below_cooling_set_temperature = 0

        # get set temperatures
        wrapped_building_component = None
        for wrapped_component in self.wrapped_components:
            if isinstance(wrapped_component.my_component, Building):
                wrapped_building_component = wrapped_component
                break
        if not wrapped_building_component:
            raise ValueError("Could not find the Building component.")

        set_heating_temperature_in_celsius = getattr(
            wrapped_building_component.my_component, "set_heating_temperature_in_celsius"
        )
        set_cooling_temperature_in_celsius = getattr(
            wrapped_building_component.my_component, "set_cooling_temperature_in_celsius"
        )
        # get heating load and heating ref temperature
        heating_load_in_watt = getattr(
            wrapped_building_component.my_component, "my_building_information"
        ).max_thermal_building_demand_in_watt

        # get specific heating load
        scaled_conditioned_floor_area_in_m2 = float(
            getattr(
                wrapped_building_component.my_component, "my_building_information"
            ).scaled_conditioned_floor_area_in_m2
        )
        specific_heating_load_in_watt_per_m2 = heating_load_in_watt / scaled_conditioned_floor_area_in_m2

        # get tabula reference value for energy need in kWh per m2 / a
        energy_need_for_heating_in_kilowatthour_per_m2_per_year_tabula_ref = getattr(
            wrapped_building_component.my_component, "my_building_information"
        ).energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year

        for column in self.results.columns:
            if all(
                x in column.split(sep=" ")
                for x in [wrapped_building_component.my_component.component_name, Building.TemperatureIndoorAir]
            ):
                for temperature in self.results[column].values:
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
                min_temperature_reached_in_celsius = float(min(self.results[column].values))
                max_temperature_reached_in_celsius = float(max(self.results[column].values))
                break

        # make kpi entry
        temperature_hours_of_building_below_heating_set_temperature_entry = KpiEntry(
            name=f"Temperature deviation of building indoor air temperature being below set temperature {set_heating_temperature_in_celsius} Celsius",
            unit="°C*h",
            value=temperature_hours_of_building_being_below_heating_set_temperature,
        )
        temperature_hours_of_building_above_cooling_set_temperature_entry = KpiEntry(
            name=f"Temperature deviation of building indoor air temperature being above set temperature {set_cooling_temperature_in_celsius} Celsius",
            unit="°C*h",
            value=temperature_hours_of_building_being_above_cooling_set_temperature,
        )
        min_temperature_reached_in_celsius_entry = KpiEntry(
            name="Minimum building indoor air temperature reached",
            unit="°C",
            value=min_temperature_reached_in_celsius,
        )
        max_temperature_reached_in_celsius_entry = KpiEntry(
            name="Maximum building indoor air temperature reached",
            unit="°C",
            value=max_temperature_reached_in_celsius,
        )

        heating_load_in_watt_entry = KpiEntry(
            name="Building heating load",
            unit="W",
            value=heating_load_in_watt,
        )
        specific_heating_load_in_watt_per_m2_entry = KpiEntry(
            name="Specific heating load",
            unit="W/m2",
            value=specific_heating_load_in_watt_per_m2,
        )
        specific_heat_demand_from_tabula_in_kwh_per_m2_per_a_entry = KpiEntry(
            name="Specific heating demand according to TABULA",
            unit="kWh/m2a",
            value=energy_need_for_heating_in_kilowatthour_per_m2_per_year_tabula_ref,
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {
                temperature_hours_of_building_below_heating_set_temperature_entry.name: temperature_hours_of_building_below_heating_set_temperature_entry.to_dict(),
                temperature_hours_of_building_above_cooling_set_temperature_entry.name: temperature_hours_of_building_above_cooling_set_temperature_entry.to_dict(),
                min_temperature_reached_in_celsius_entry.name: min_temperature_reached_in_celsius_entry.to_dict(),
                max_temperature_reached_in_celsius_entry.name: max_temperature_reached_in_celsius_entry.to_dict(),
                heating_load_in_watt_entry.name: heating_load_in_watt_entry.to_dict(),
                specific_heating_load_in_watt_per_m2_entry.name: specific_heating_load_in_watt_per_m2_entry.to_dict(),
                specific_heat_demand_from_tabula_in_kwh_per_m2_per_a_entry.name: specific_heat_demand_from_tabula_in_kwh_per_m2_per_a_entry.to_dict(),
            }
        )
        return scaled_conditioned_floor_area_in_m2

    def get_heat_distribution_system_kpis(self) -> None:
        """Get KPIs from heat distriution system like thermal energy delivered."""

        thermal_output_energy_in_kilowatt_hour = None
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
                break
        if thermal_output_energy_in_kilowatt_hour is None:
            log.warning(
                "KPI values for heat distribution system are None. "
                "Please check if you have correctly initialized and connected the heat distribution system in your system setup or ignore this message."
            )

        thermal_output_energy_hds_entry = KpiEntry(
            name="Thermal output energy of heat distribution system",
            unit="kWh",
            value=thermal_output_energy_in_kilowatt_hour,
        )
        # update kpi collection dict
        self.kpi_collection_dict.update(
            {thermal_output_energy_hds_entry.name: thermal_output_energy_hds_entry.to_dict()}
        )

    def get_heatpump_cycles(self, results: pd.DataFrame, component_name: str) -> float:
        """Get the number of cycles of the heat pump for the simulated period."""
        number_of_cycles = 0
        for column in results.columns:

            if all(x in column.split(sep=" ") for x in [HeatPumpHplib.TimeOff, component_name]):
                for index, off_time in enumerate(results[column].values):
                    try:
                        if off_time != 0 and results[column].values[index + 1] == 0:
                            number_of_cycles = number_of_cycles + 1

                    except Exception:
                        pass

        return number_of_cycles

    def get_heat_pump_energy_performance(
        self,
        results: pd.DataFrame,
        seconds_per_timestep: int,
        component_name: str,
        building_conditioned_floor_area_in_m2: float,
    ) -> Tuple[float, float, float, float]:
        """Get energy performance kpis from heat pump over simulated period.

        Transform thermal and electrical power from heat pump in energies.
        """
        thermal_output_energy_in_kilowatt_hour = 0.0
        electrical_energy_in_kilowatt_hour = 1.0

        for column in results.columns:
            if all(x in column.split(sep=" ") for x in [HeatPumpHplib.ThermalOutputPower, component_name]):
                # take only output values for heating
                thermal_output_power_values_in_watt = results[column].loc[results[column] > 0.0]
                # get energy from power
                thermal_output_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=thermal_output_power_values_in_watt, timeresolution=seconds_per_timestep
                )
            if all(x in column.split(sep=" ") for x in [HeatPumpHplib.ElectricalInputPower, component_name]):
                # get electrical energie values
                electrical_energy_in_kilowatt_hour = self.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=results[column], timeresolution=seconds_per_timestep
                )

        # calculate SPF
        spf = thermal_output_energy_in_kilowatt_hour / electrical_energy_in_kilowatt_hour
        # calculate specific heat pump thermal output energy
        specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2 = (
            thermal_output_energy_in_kilowatt_hour / building_conditioned_floor_area_in_m2
        )

        return (
            spf,
            thermal_output_energy_in_kilowatt_hour,
            electrical_energy_in_kilowatt_hour,
            specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2,
        )

    def get_heat_pump_kpis(self, building_conditioned_floor_area_in_m2: float) -> None:
        """Get some KPIs from Heat Pump."""

        number_of_heat_pump_cycles = None
        seasonal_performance_factor = None
        thermal_output_energy_heatpump_in_kilowatt_hour = None
        electrical_input_energy_heatpump_in_kilowatt_hour = None
        specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2 = None

        # check if Heat Pump was used in components
        for wrapped_component in self.wrapped_components:

            if isinstance(wrapped_component.my_component, HeatPumpHplib):

                # get number of heat pump cycles over simulated period
                number_of_heat_pump_cycles = self.get_heatpump_cycles(
                    results=self.results, component_name=wrapped_component.my_component.component_name
                )

                # get SPF
                (
                    seasonal_performance_factor,
                    thermal_output_energy_heatpump_in_kilowatt_hour,
                    electrical_input_energy_heatpump_in_kilowatt_hour,
                    specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2,
                ) = self.get_heat_pump_energy_performance(
                    results=self.results,
                    seconds_per_timestep=self.simulation_parameters.seconds_per_timestep,
                    component_name=wrapped_component.my_component.component_name,
                    building_conditioned_floor_area_in_m2=building_conditioned_floor_area_in_m2,
                )

                break

        if None in (
            number_of_heat_pump_cycles,
            seasonal_performance_factor,
            thermal_output_energy_heatpump_in_kilowatt_hour,
            electrical_input_energy_heatpump_in_kilowatt_hour,
            specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2,
        ):
            log.warning(
                "KPI values for advanced heat pump HPLib are None. "
                "Please check if you have correctly initialized and connected the heat pump in your system setup or ignore this message."
            )

        # make kpi entry
        number_of_heat_pump_cycles_entry = KpiEntry(
            name="Number of heat pump cycles", unit="-", value=number_of_heat_pump_cycles
        )
        seasonal_performance_factor_entry = KpiEntry(
            name="Seasonal performance factor of heat pump", unit="-", value=seasonal_performance_factor
        )
        thermal_output_energy_heatpump_entry = KpiEntry(
            name="Thermal output energy of heat pump", unit="kWh", value=thermal_output_energy_heatpump_in_kilowatt_hour
        )
        specific_thermal_output_energy_of_heatpump_entry = KpiEntry(
            name="Specific thermal output energy of heat pump",
            unit="kWh/m2",
            value=specific_thermal_output_energy_of_heat_pump_in_kilowatt_hour_per_m2,
        )
        electrical_input_energy_heatpump_entry = KpiEntry(
            name="Electrical input energy of heat pump",
            unit="kWh",
            value=electrical_input_energy_heatpump_in_kilowatt_hour,
        )

        # update kpi collection dict
        self.kpi_collection_dict.update(
            {
                number_of_heat_pump_cycles_entry.name: number_of_heat_pump_cycles_entry.to_dict(),
                seasonal_performance_factor_entry.name: seasonal_performance_factor_entry.to_dict(),
                thermal_output_energy_heatpump_entry.name: thermal_output_energy_heatpump_entry.to_dict(),
                specific_thermal_output_energy_of_heatpump_entry.name: specific_thermal_output_energy_of_heatpump_entry.to_dict(),
                electrical_input_energy_heatpump_entry.name: electrical_input_energy_heatpump_entry.to_dict(),
            }
        )

    def return_table_for_report(self):
        """Return a table with all kpis for the report."""
        table: List = []
        table.append(["KPI", "Value", "Unit"])

        for kpi_key, kpi_entry in self.kpi_collection_dict.items():
            value = kpi_entry["value"]
            if value is not None:
                value = round(value, 2)
            unit = kpi_entry["unit"]
            table.append([f"{kpi_key}: ", f"{value}", f"{unit}"])

        return table
