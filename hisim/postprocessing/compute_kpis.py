"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from dataclasses import dataclass
from typing import Any, List, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters
from hisim.utils import HISIMPATH


def read_in_fuel_costs() -> pd.DataFrame:
    """ Reads data for cost from csv. """
    price_frame = pd.read_csv(HISIMPATH["fuel_costs"], sep=";", usecols = [0, 1, 2])
    price_frame.index = price_frame["fuel type"]
    price_frame.drop(columns=["fuel type"], inplace=True)
    return price_frame

def get_euro_and_co2(fuel_costs: pd.DataFrame, fuel: LoadTypes) -> Tuple[float, float]:
    """ Returns cost (Euro) of kWh of fuel and CO2 consumption (kg) of kWh of fuel. """
    column = fuel_costs.iloc[fuel_costs.index == fuel.value]
    return [float(column['EUR per kWh']), float(column['kgC02 per kWh'])]

def compute_consumption_production(all_outputs: List, results: pd.DataFrame) -> pd.DataFrame:
    """ Computes electricity consumption and production based on results of hisim simulation. """

    # replace that loop by searching for flags -> include also battery things and hydrogen things
    # flags for Postprocessing: cp.ComponentOutput.postprocessing_flag -> loadtpyes.InandOutputType :
    # Consumption, Production, StorageContent, ChargeDischarge
    # CHARGE_DISCHARGE from battery has + and - sign and is production and consumption both in one output
    # heat production of heat pump has - sign in summer, either separate or take absolute value
    # flags for ComponentTypes: cp.ComponentOutput.component_type
    # flags for LoadTypes: cp.ComponentOutput.load_type
    # flags for Units: cp.ComponentOutput.unit

    # initialize columns consumption, production, battery_charge, battery_discharge, storage
    results["consumption"] = 0
    results["production"] = 0
    results["battery_charge"] = 0
    results["battery_discharge"] = 0
    results["storage"] = 0
    index: int
    output: ComponentOutput

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag:
                results["production"] = results["production"] + results.iloc[:, index]

            elif (
                (
                    InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
                    in output.postprocessing_flag
                )
                or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
                in output.postprocessing_flag
            ):
                results["consumption"] = results["consumption"] + results.iloc[:, index]

            elif InandOutputType.STORAGE_CONTENT in output.postprocessing_flag:
                results["storage"] = results["storage"] + results.iloc[:, index]

            elif InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                if ComponentType.BATTERY in output.postprocessing_flag:
                    results["battery_charge"] = results[
                        "battery_charge"
                    ] + results.iloc[:, index].clip(lower=0)
                    results["battery_discharge"] = results[
                        "battery_discharge"
                    ] - results.iloc[:, index].clip(upper=0)
                elif ComponentType.CAR_BATTERY in output.postprocessing_flag:
                    results["consumption"] = results["consumption"] + results.iloc[
                        :, index
                    ].clip(lower=0)

        else:
            continue

    return results

def compute_self_consumption_and_injection(results: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
# account for battery
    production_with_battery = results["production"] + results["battery_discharge"]
    consumption_with_battery = results["consumption"] + results["battery_charge"]

    # evaluate injection and sum over time
    injection = production_with_battery - consumption_with_battery

    # evaluate self consumption and immidiately sum over time
    self_consumption = (
        pd.concat(
            (
                results["production"][
                    results["production"] <= consumption_with_battery
                ],
                consumption_with_battery[
                    consumption_with_battery < results["production"]
                ],
            )
        )
        .groupby(level=0)
        .sum()
    )

    return injection, self_consumption

def search_electricity_prices_in_results(all_outputs: List, results: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """ Extracts electricity price consumption and electricity price production from results."""
    electricity_price_consumption = pd.Series(dtype=pd.Float64Dtype)
    electricity_price_injection = pd.Series(dtype=pd.Float64Dtype)
    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if LoadTypes.PRICE in output.postprocessing_flag:
                if (
                    InandOutputType.ELECTRICITY_CONSUMPTION
                    in output.postprocessing_flag
                ):
                    electricity_price_consumption = results.iloc[:, index]
                elif (
                    InandOutputType.ELECTRICITY_INJECTION in output.postprocessing_flag
                ):
                    electricity_price_injection = results.iloc[:, index]
                else:
                    continue
    return electricity_price_consumption, electricity_price_injection

def compute_energy_from_power(power_timeseries: pd.Series, timeresolution: int) -> float:
    return power_timeseries.sum() * timeresolution / 3.6e6

def compute_cost_of_fuel_type(results: pd.DataFrame, all_outputs: List, timeresolution: int,
price_frame: pd.DataFrame, fuel: LoadTypes) -> Tuple[float, float]:
    fuel_consumption = pd.Series(dtype=pd.Float64Dtype)
    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.FUEL_CONSUMPTION in output.postprocessing_flag:
                if (
                    fuel
                    in output.postprocessing_flag
                ):
                    fuel_consumption = results.iloc[:, index]
                else:
                    continue
    
    # convert liters to Wh
    if not fuel_consumption.empty:
        if fuel == LoadTypes.OIL:
            liters_to_Wh = 1e4 / 1.0526315789474
        elif fuel == LoadTypes.DIESEL:
            liters_to_Wh = 9.8e3
        else: 
            liters_to_Wh = 1
        consumption_sum = compute_energy_from_power(power_timeseries=fuel_consumption,
        timeresolution=timeresolution) * liters_to_Wh
    else:
        consumption_sum = 0

    price, co2 = get_euro_and_co2(fuel_costs=price_frame, fuel=fuel)
    return consumption_sum * price, consumption_sum * co2


def compute_kpis(
    results: pd.DataFrame,
    all_outputs: List[ComponentOutput],
    simulation_parameters: SimulationParameters,
) -> Any:  # noqa: MC0001
    """Calculation of several KPIs."""
    # initialize prices
    price = 0
    co2 = 0

    price_frame = read_in_fuel_costs()

    # compute consumption and production and extract price signals
    results = compute_consumption_production(all_outputs=all_outputs, results=results)
    electricity_price_consumption, electricity_price_injection = search_electricity_prices_in_results(all_outputs=all_outputs, results=results)

    # sum consumption and production over time make it more clear and better
    consumption_sum = compute_energy_from_power(power_timeseries = results["consumption"],
    timeresolution=simulation_parameters.seconds_per_timestep)

    production_sum = compute_energy_from_power(power_timeseries = results["production"],
    timeresolution=simulation_parameters.seconds_per_timestep)

    # computes injection and self consumption + autarky and self consumption rates
    if production_sum > 0:
        injection, self_consumption = compute_self_consumption_and_injection(results=results)
        injection_sum = compute_energy_from_power(power_timeseries = injection[ injection > 0],
        timeresolution=simulation_parameters.seconds_per_timestep)
        
        self_consumption_sum = compute_energy_from_power(power_timeseries = self_consumption,
        timeresolution=simulation_parameters.seconds_per_timestep)

        self_consumption_rate = 100 * (self_consumption_sum / production_sum)
        autarky_rate = 100 * (self_consumption_sum / consumption_sum)
    else:
        self_consumption_sum = 0
        injection_sum = 0
        self_consumption_rate = 0
        autarky_rate = 0
    
    battery_losses = 0  # explicity compute that
    h2_system_losses = 0  # explicitly compute that

    # Electricity Price
    electricity_price_constant, co2_price_constant = get_euro_and_co2(fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY)

    if production_sum > 0:
        # evaluate electricity price
        if not electricity_price_injection.empty:
            price = price - compute_energy_from_power(
                power_timeseries = injection[injection > 0] * electricity_price_injection[injection > 0],
                timeresolution=simulation_parameters.seconds_per_timestep
            )
        else:
            price = price - injection_sum * electricity_price_constant

    if not electricity_price_consumption.empty:
        # substract self consumption from consumption for bill calculation
        price = price + compute_energy_from_power(
            power_timeseries=(results["consumption"] - self_consumption) * electricity_price_consumption,
            timeresolution=simulation_parameters.seconds_per_timestep
        )
    else:
        price = price + (consumption_sum - self_consumption_sum) * electricity_price_constant

    co2 = co2 + (consumption_sum - self_consumption_sum) * co2_price_constant

    for fuel in [LoadTypes.GAS, LoadTypes.OIL,
    LoadTypes.DISTRICTHEATING, LoadTypes.DIESEL]:
        fuel_price, fuel_co2 = compute_cost_of_fuel_type(
            results=results,
            all_outputs=all_outputs, 
            timeresolution=simulation_parameters.seconds_per_timestep,
            price_frame=price_frame,
            fuel=fuel
            )
        co2 = co2 + fuel_co2
        price = price + fuel_price

    # initilize lines for report
    lines: List = []
    lines.append(f"Consumption: {consumption_sum:4.0f} kWh")
    lines.append(f"Production: {production_sum:4.0f} kWh")
    lines.append(f"Self-Consumption: {self_consumption_sum:4.0f} kWh")
    lines.append(f"Injection: {injection_sum:4.0f} kWh")
    lines.append(f"Battery losses: {battery_losses:4.0f} kWh")
    lines.append(f"Battery content: {0:4.0f} kWh")
    lines.append(f"Hydrogen system losses: {h2_system_losses:4.0f} kWh")
    lines.append(f"Hydrogen storage content: {0:4.0f} kWh")
    lines.append(f"Autarky Rate: {autarky_rate:3.1f} %")
    lines.append(f"Self Consumption Rate: {self_consumption_rate:3.1f} %")
    lines.append(f"Price paid for electricity: {price:3.0f} EUR")
    lines.append(f"CO2 emitted due to electricity use: {co2:3.0f} kg")

    # initialize json interface to pass kpi's to building_sizer
    kpi_config = KPIConfig(
        self_consumption_rate=self_consumption_rate,
        autarky_rate=autarky_rate,
        injection=injection_sum,
        economic_cost=price,
        co2_cost=co2,
    )

    pathname = os.path.join(simulation_parameters.result_directory, "kpi_config.json")
    config_file_written = kpi_config.to_json()  # type: ignore
    with open(pathname, "w", encoding="utf-8") as outfile:
        outfile.write(config_file_written)

    return lines
