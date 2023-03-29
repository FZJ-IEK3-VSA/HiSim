# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from typing import Any, List, Tuple, Union

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters
from hisim.utils import HISIMPATH
from hisim.component_wrapper import ComponentWrapper
from hisim.components import generic_hot_water_storage_modular
from hisim import log


def read_in_fuel_costs() -> pd.DataFrame:
    """Reads data for cost from csv."""
    price_frame = pd.read_csv(HISIMPATH["fuel_costs"], sep=";", usecols=[0, 1, 2])
    price_frame.index = price_frame["fuel type"]  # type: ignore
    price_frame.drop(columns=["fuel type"], inplace=True)
    return price_frame


def get_euro_and_co2(
    fuel_costs: pd.DataFrame, fuel: Union[LoadTypes, InandOutputType]
) -> Tuple[float, float]:
    """Returns cost (Euro) of kWh of fuel and CO2 consumption (kg) of kWh of fuel."""
    column = fuel_costs.iloc[fuel_costs.index == fuel.value]
    return (float(column["EUR per kWh"]), float(column["kgC02 per kWh"]))


def compute_consumption_production(
    all_outputs: List, results: pd.DataFrame
) -> pd.DataFrame:
    """Computes electricity consumption and production based on results of hisim simulation.

    Also evaluates battery charge and discharge, because it is relevant for self consumption rates.
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
                    InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
                    in output.postprocessing_flag
                    or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
                    in output.postprocessing_flag
            ):
                consumption_ids.append(index)
            elif InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                if ComponentType.BATTERY in output.postprocessing_flag:
                    battery_charge_discharge_ids.append(index)
                elif ComponentType.CAR_BATTERY in output.postprocessing_flag:
                    consumption_ids.append(index)
        else:
            continue

    postprocessing_results = pd.DataFrame()
    postprocessing_results["consumption"] = pd.DataFrame(results.iloc[:, consumption_ids]).clip(lower=0).sum(axis=1)
    postprocessing_results["production"] = pd.DataFrame(results.iloc[:, production_ids]).clip(lower=0).sum(axis=1)

    postprocessing_results["battery_charge"] = pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(lower=0).sum(axis=1)
    postprocessing_results["battery_discharge"] = pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(upper=0).sum(axis=1) * (-1)

    return postprocessing_results


def compute_hot_water_storage_losses_and_cycles(
    components: List[ComponentWrapper],
    all_outputs: List, results: pd.DataFrame,
    timeresolution: int,
) -> Tuple[float, float, float, float, float, float]:
    """Computes hot water storage losses and cycles. """

    # initialize columns consumption, production, battery_charge, battery_discharge, storage
    charge_sum_dhw = 0.0
    charge_sum_buffer = 0.0
    discharge_sum_dhw = 0.0
    discharge_sum_buffer = 0.0
    cycle_buffer = None
    cycle_dhw = None

    # get cycle of water storages
    for elem in components:
        if isinstance(elem.my_component, generic_hot_water_storage_modular.HotWaterStorage):
            use = elem.my_component.use
            if use == ComponentType.BUFFER:
                cycle_buffer = elem.my_component.config.energy_full_cycle
            elif use == ComponentType.BOILER:
                cycle_dhw = elem.my_component.config.energy_full_cycle

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.CHARGE in output.postprocessing_flag:
                if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                    charge_sum_dhw = charge_sum_dhw + compute_energy_from_power(power_timeseries=results.iloc[:, index], timeresolution=timeresolution)
                elif InandOutputType.HEATING in output.postprocessing_flag:
                    charge_sum_buffer = charge_sum_buffer + compute_energy_from_power(power_timeseries=results.iloc[:, index], timeresolution=timeresolution)
            elif InandOutputType.DISCHARGE in output.postprocessing_flag:
                if ComponentType.BOILER in output.postprocessing_flag:
                    discharge_sum_dhw = discharge_sum_dhw + compute_energy_from_power(power_timeseries=results.iloc[:, index], timeresolution=timeresolution)
                elif ComponentType.BUFFER in output.postprocessing_flag:
                    discharge_sum_buffer = discharge_sum_buffer + compute_energy_from_power(power_timeseries=results.iloc[:, index], timeresolution=timeresolution)
        else:
            continue
        if cycle_dhw is not None:
            cycles_dhw = charge_sum_dhw / cycle_dhw
        else:
            cycles_dhw = 0
            log.error("Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. ")
        storage_loss_dhw = charge_sum_dhw - discharge_sum_dhw
        if cycle_buffer is not None:
            cycles_buffer = charge_sum_buffer / cycle_buffer
        else:
            cycles_buffer = 0
            log.error("Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. ")
        storage_loss_buffer = charge_sum_buffer - discharge_sum_buffer
    if cycle_buffer == 0:
        building_heating = charge_sum_buffer
    else:
        building_heating = discharge_sum_buffer

    return cycles_dhw, storage_loss_dhw, discharge_sum_dhw, cycles_buffer, storage_loss_buffer, building_heating


def compute_self_consumption_and_injection(
    postprocessing_results: pd.DataFrame,
) -> Tuple[pd.Series, pd.Series]:
    """Computes the self consumption and the grid injection."""
    # account for battery
    production_with_battery = postprocessing_results["production"] + postprocessing_results["battery_discharge"]
    consumption_with_battery = postprocessing_results["consumption"] + postprocessing_results["battery_charge"]

    # evaluate injection and sum over time
    injection = production_with_battery - consumption_with_battery

    # evaluate self consumption and immidiately sum over time
    # battery is charged (counting to consumption) and discharged (counting to production)
    # -> only one direction can be counted, otherwise the self-consumption can be greater than 100.
    # Here the production side is counted (battery_discharge).
    self_consumption = (
        pd.concat(
            (
                production_with_battery[
                    production_with_battery <= postprocessing_results["consumption"]
                ],
                postprocessing_results["consumption"][
                    postprocessing_results["consumption"] < production_with_battery
                ],
            )
        )
        .groupby(level=0)
        .sum()
    )

    return injection, self_consumption


def search_electricity_prices_in_results(
    all_outputs: List, results: pd.DataFrame
) -> Tuple[pd.Series, pd.Series]:
    """Extracts electricity price consumption and electricity price production from results."""
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


def compute_energy_from_power(
    power_timeseries: pd.Series, timeresolution: int
) -> float:
    """Computes the energy from a power value."""
    if power_timeseries.empty:
        return 0.0
    return float(power_timeseries.sum() * timeresolution / 3.6e6)


def compute_cost_of_fuel_type(
    results: pd.DataFrame,
    all_outputs: List,
    timeresolution: int,
    price_frame: pd.DataFrame,
    fuel: LoadTypes,
) -> Tuple[float, float]:
    """Computes the cost of the fuel type."""
    fuel_consumption = pd.Series(dtype=pd.Float64Dtype)
    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.FUEL_CONSUMPTION in output.postprocessing_flag:
                if fuel in output.postprocessing_flag:
                    fuel_consumption = results.iloc[:, index]
                else:
                    continue

    # convert liters to Wh
    if not fuel_consumption.empty:
        if fuel == LoadTypes.OIL:
            liters_to_watt_hours = 1e4 / 1.0526315789474
        elif fuel == LoadTypes.DIESEL:
            liters_to_watt_hours = 9.8e3
        else:
            liters_to_watt_hours = 1
        consumption_sum = (
            compute_energy_from_power(
                power_timeseries=fuel_consumption, timeresolution=timeresolution
            )
            * liters_to_watt_hours
        )
    else:
        consumption_sum = 0

    price, co2 = get_euro_and_co2(fuel_costs=price_frame, fuel=fuel)
    return consumption_sum * price, consumption_sum * co2


def compute_kpis(
    components: List[ComponentWrapper],
    results: pd.DataFrame,
    all_outputs: List[ComponentOutput],
    simulation_parameters: SimulationParameters,
) -> Any:  # noqa: MC0001
    """Calculation of several KPIs."""
    # initialize prices
    price = 0.0
    co2 = 0.0

    price_frame = read_in_fuel_costs()

    # compute consumption and production and extract price signals
    postprocessing_results = compute_consumption_production(all_outputs=all_outputs, results=results)
    (
        electricity_price_consumption,
        electricity_price_injection,
    ) = search_electricity_prices_in_results(all_outputs=all_outputs, results=results)

    # sum consumption and production over time make it more clear and better
    consumption_sum = compute_energy_from_power(
        power_timeseries=postprocessing_results["consumption"],
        timeresolution=simulation_parameters.seconds_per_timestep,
    )

    production_sum = compute_energy_from_power(
        power_timeseries=postprocessing_results["production"],
        timeresolution=simulation_parameters.seconds_per_timestep,
    )

    # computes injection and self consumption + autarky and self consumption rates
    if production_sum > 0:
        injection, self_consumption = compute_self_consumption_and_injection(
            postprocessing_results=postprocessing_results
        )
        injection_sum = compute_energy_from_power(
            power_timeseries=injection[injection > 0],
            timeresolution=simulation_parameters.seconds_per_timestep,
        )

        self_consumption_sum = compute_energy_from_power(
            power_timeseries=self_consumption,
            timeresolution=simulation_parameters.seconds_per_timestep,
        )

        self_consumption_rate = 100 * (self_consumption_sum / production_sum)
        autarky_rate = 100 * (self_consumption_sum / consumption_sum)

        if not postprocessing_results["battery_charge"].empty:
            battery_losses = compute_energy_from_power(
                power_timeseries=postprocessing_results["battery_charge"],
                timeresolution=simulation_parameters.seconds_per_timestep,
            ) - compute_energy_from_power(
                power_timeseries=postprocessing_results["battery_discharge"],
                timeresolution=simulation_parameters.seconds_per_timestep,
            )

    else:
        self_consumption_sum = 0
        injection_sum = 0
        self_consumption_rate = 0
        autarky_rate = 0
        battery_losses = 0
        # battery_soc = 0
    h2_system_losses = 0  # explicitly compute that

    # Electricity Price
    electricity_price_constant, co2_price_constant = get_euro_and_co2(
        fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY
    )
    electricity_inj_price_constant, _ = get_euro_and_co2(
        fuel_costs=price_frame, fuel=InandOutputType.ELECTRICITY_INJECTION
    )

    if production_sum > 0:
        # evaluate electricity price
        if not electricity_price_injection.empty:
            price = price - compute_energy_from_power(
                power_timeseries=injection[injection > 0]
                * electricity_price_injection[injection > 0],
                timeresolution=simulation_parameters.seconds_per_timestep,
            )
            price = price + compute_energy_from_power(
                power_timeseries=postprocessing_results["consumption"] - self_consumption,
                timeresolution=simulation_parameters.seconds_per_timestep,
            )
        else:
            price = (
                price
                - injection_sum * electricity_inj_price_constant
                + (consumption_sum - self_consumption_sum) * electricity_price_constant
            )

    else:
        if not electricity_price_consumption.empty:
            # substract self consumption from consumption for bill calculation
            price = price + compute_energy_from_power(
                power_timeseries=postprocessing_results["consumption"] * electricity_price_consumption,
                timeresolution=simulation_parameters.seconds_per_timestep,
            )
        else:
            price = price + consumption_sum * electricity_price_constant

    co2 = co2 + (consumption_sum - self_consumption_sum) * co2_price_constant

    # compute cost and co2 for LoadTypes other than electricity
    for fuel in [
        LoadTypes.GAS,
        LoadTypes.OIL,
        LoadTypes.DISTRICTHEATING,
        LoadTypes.DIESEL,
    ]:
        fuel_price, fuel_co2 = compute_cost_of_fuel_type(
            results=results,
            all_outputs=all_outputs,
            timeresolution=simulation_parameters.seconds_per_timestep,
            price_frame=price_frame,
            fuel=fuel,
        )
        co2 = co2 + fuel_co2
        price = price + fuel_price

    cycles_dhw, loss_dhw, use_dhw, cycles_buffer, loss_buffer, use_heating = compute_hot_water_storage_losses_and_cycles(
        components=components, all_outputs=all_outputs, results=results, timeresolution=simulation_parameters.seconds_per_timestep,
    )

    # initilize lines for report
    lines: List = []
    lines.append(f"Consumption: {consumption_sum:4.0f} kWh")
    lines.append(f"Production: {production_sum:4.0f} kWh")
    lines.append(f"Self-Consumption: {self_consumption_sum:4.0f} kWh")
    lines.append(f"Injection: {injection_sum:4.0f} kWh")
    lines.append(f"Battery losses: {battery_losses:4.0f} kWh")
    lines.append(f"DHW storage heat loss: {loss_dhw:4.0f} kWh")
    lines.append(f"DHW storage heat cycles: {cycles_dhw:4.0f} Cycles")
    lines.append(f"DHW energy provided: {use_dhw:4.0f} kWh")
    lines.append(f"Buffer storage heat loss: {loss_buffer:4.0f} kWh")
    lines.append(f"Buffer storage heat cycles: {cycles_buffer:4.0f} Cycles")
    lines.append(f"Heating energy provided: {use_heating:4.0f} kWh")
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
