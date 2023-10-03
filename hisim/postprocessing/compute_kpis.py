# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from typing import List, Tuple, Union, Any
from pathlib import Path

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters
from hisim.utils import HISIMPATH
from hisim.component_wrapper import ComponentWrapper
from hisim.components import generic_hot_water_storage_modular
from hisim import log
from hisim.postprocessing.investment_cost_co2 import compute_investment_cost
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum


def building_temperature_control(
    results: pd.DataFrame, seconds_per_timestep: int
) -> Tuple[Any, Any, float, float, float, float]:
    """Check the building indoor air temperature.

    Check for all timesteps and count the
    time when the temperature is outside of the building set temperatures
    in order to verify if energy system provides enough heating and cooling.
    """

    temperature_difference_of_building_being_below_heating_set_temperature = 0
    temperature_difference_of_building_being_below_cooling_set_temperature = 0
    for column in results.columns:

        if "TemperatureIndoorAir" in column.split(sep=" "):

            if SingletonSimRepository().exist_entry(
                key=SingletonDictKeyEnum.SETHEATINGTEMPERATUREFORBUILDING
            ) and SingletonSimRepository().exist_entry(
                key=SingletonDictKeyEnum.SETCOOLINGTEMPERATUREFORBUILDING
            ):
                set_heating_temperature_in_celsius = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.SETHEATINGTEMPERATUREFORBUILDING
                )
                set_cooling_temperature_in_celsius = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.SETCOOLINGTEMPERATUREFORBUILDING
                )

            else:
                # take set heating and cooling default temperatures from building component otherwise
                set_heating_temperature_in_celsius = 19.0
                set_cooling_temperature_in_celsius = 24.0

            for temperature in results[column].values:

                if temperature < set_heating_temperature_in_celsius:

                    temperature_difference_heating = (
                        set_heating_temperature_in_celsius - temperature
                    )

                    temperature_difference_of_building_being_below_heating_set_temperature = (
                        temperature_difference_of_building_being_below_heating_set_temperature
                        + temperature_difference_heating
                    )

                elif temperature > set_cooling_temperature_in_celsius:

                    temperature_difference_cooling = (
                        temperature - set_cooling_temperature_in_celsius
                    )
                    temperature_difference_of_building_being_below_cooling_set_temperature = (
                        temperature_difference_of_building_being_below_cooling_set_temperature
                        + temperature_difference_cooling
                    )

            temperature_hours_of_building_being_below_heating_set_temperature = (
                temperature_difference_of_building_being_below_heating_set_temperature
                * seconds_per_timestep
                / 3600
            )

            temperature_hours_of_building_being_above_cooling_set_temperature = (
                temperature_difference_of_building_being_below_cooling_set_temperature
                * seconds_per_timestep
                / 3600
            )

            # get also max and min indoor air temperature
            min_temperature_reached_in_celsius = float(min(results[column].values))
            max_temperature_reached_in_celsius = float(max(results[column].values))

    return (
        set_heating_temperature_in_celsius,
        set_cooling_temperature_in_celsius,
        temperature_hours_of_building_being_below_heating_set_temperature,
        temperature_hours_of_building_being_above_cooling_set_temperature,
        min_temperature_reached_in_celsius,
        max_temperature_reached_in_celsius,
    )


def get_heatpump_cycles(results: pd.DataFrame,) -> float:
    """Get the number of cycles of the heat pump for the simulated period."""
    number_of_cycles = 0
    for column in results.columns:

        if "TimeOff" in column.split(sep=" "):

            for index, off_time in enumerate(results[column].values):

                try:
                    if off_time != 0 and results[column].values[index + 1] == 0:

                        number_of_cycles = number_of_cycles + 1

                except Exception:
                    pass

    return number_of_cycles


def read_in_fuel_costs() -> pd.DataFrame:
    """Reads data for costs and co2 emissions of fuels from csv."""
    price_frame = pd.read_csv(HISIMPATH["fuel_costs"], sep=";", usecols=[0, 2, 4])
    price_frame.index = price_frame["fuel type"]  # type: ignore
    price_frame.drop(columns=["fuel type"], inplace=True)
    return price_frame


def get_euro_and_co2(
    fuel_costs: pd.DataFrame, fuel: Union[LoadTypes, InandOutputType]
) -> Tuple[float, float]:
    """Returns cost (Euro) of kWh of fuel and CO2 consumption (kg) of kWh of fuel."""
    column = fuel_costs.iloc[fuel_costs.index == fuel.value]
    return (float(column["Cost"].iloc[0]), float(column["Footprint"].iloc[0]))


def compute_consumption_production(
    all_outputs: List, results: pd.DataFrame
) -> pd.DataFrame:
    """Computes electricity consumption and production based on results of hisim simulation.

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
    postprocessing_results["consumption"] = (
        pd.DataFrame(results.iloc[:, consumption_ids]).clip(lower=0).sum(axis=1)
    )
    postprocessing_results["production"] = (
        pd.DataFrame(results.iloc[:, production_ids]).clip(lower=0).sum(axis=1)
    )

    postprocessing_results["battery_charge"] = (
        pd.DataFrame(results.iloc[:, battery_charge_discharge_ids])
        .clip(lower=0)
        .sum(axis=1)
    )
    postprocessing_results["battery_discharge"] = pd.DataFrame(
        results.iloc[:, battery_charge_discharge_ids]
    ).clip(upper=0).sum(axis=1) * (-1)

    return postprocessing_results


def compute_hot_water_storage_losses_and_cycles(
    components: List[ComponentWrapper],
    all_outputs: List,
    results: pd.DataFrame,
    timeresolution: int,
) -> Tuple[float, float, float, float, float, float]:
    """Computes hot water storage losses and cycles."""

    # initialize columns consumption, production, battery_charge, battery_discharge, storage
    charge_sum_dhw = 0.0
    charge_sum_buffer = 0.0
    discharge_sum_dhw = 0.0
    discharge_sum_buffer = 0.0
    cycle_buffer = None
    cycle_dhw = None

    # get cycle of water storages
    for elem in components:
        if isinstance(
            elem.my_component, generic_hot_water_storage_modular.HotWaterStorage
        ):
            use = elem.my_component.use
            if use == ComponentType.BUFFER:
                cycle_buffer = elem.my_component.config.energy_full_cycle
            elif use == ComponentType.BOILER:
                cycle_dhw = elem.my_component.config.energy_full_cycle

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.CHARGE in output.postprocessing_flag:
                if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                    charge_sum_dhw = charge_sum_dhw + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
                elif InandOutputType.HEATING in output.postprocessing_flag:
                    charge_sum_buffer = charge_sum_buffer + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
            elif InandOutputType.DISCHARGE in output.postprocessing_flag:
                if ComponentType.BOILER in output.postprocessing_flag:
                    discharge_sum_dhw = discharge_sum_dhw + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
                elif ComponentType.BUFFER in output.postprocessing_flag:
                    discharge_sum_buffer = (
                        discharge_sum_buffer
                        + compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            timeresolution=timeresolution,
                        )
                    )
        else:
            continue
        if cycle_dhw is not None:
            cycles_dhw = charge_sum_dhw / cycle_dhw
        else:
            cycles_dhw = 0
            log.error(
                "Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. "
            )
        storage_loss_dhw = charge_sum_dhw - discharge_sum_dhw
        if cycle_buffer is not None:
            cycles_buffer = charge_sum_buffer / cycle_buffer
        else:
            cycles_buffer = 0
            log.error(
                "Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. "
            )
        storage_loss_buffer = charge_sum_buffer - discharge_sum_buffer
    if cycle_buffer == 0:
        building_heating = charge_sum_buffer
    else:
        building_heating = discharge_sum_buffer

    return (
        cycles_dhw,
        storage_loss_dhw,
        discharge_sum_dhw,
        cycles_buffer,
        storage_loss_buffer,
        building_heating,
    )


def compute_self_consumption_and_injection(
    postprocessing_results: pd.DataFrame,
) -> Tuple[pd.Series, pd.Series]:
    """Computes the self consumption and the grid injection."""
    # account for battery
    production_with_battery = (
        postprocessing_results["production"]
        + postprocessing_results["battery_discharge"]
    )
    consumption_with_battery = (
        postprocessing_results["consumption"] + postprocessing_results["battery_charge"]
    )

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
) -> Tuple["pd.Series[float]", "pd.Series[float]"]:
    """Extracts electricity price consumption and electricity price production from results."""
    electricity_price_consumption = pd.Series(
        dtype=pd.Float64Dtype
    )  # type: pd.Series[float]
    electricity_price_injection = pd.Series(
        dtype=pd.Float64Dtype
    )  # type: pd.Series[float]
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
    fuel_consumption = pd.Series(dtype=pd.Float64Dtype)  # type: pd.Series[float]
    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.FUEL_CONSUMPTION in output.postprocessing_flag:
                if fuel in output.postprocessing_flag:
                    fuel_consumption = results.iloc[:, index]
                else:
                    continue
    if not fuel_consumption.empty:
        if fuel in [LoadTypes.ELECTRICITY, LoadTypes.GAS, LoadTypes.DISTRICTHEATING]:
            consumption_sum = compute_energy_from_power(
                power_timeseries=fuel_consumption, timeresolution=timeresolution
            )
        # convert from Wh to kWh
        elif fuel in [LoadTypes.GAS, LoadTypes.DISTRICTHEATING]:
            consumption_sum = sum(fuel_consumption) * 1e-3
        # stay with liters
        else:
            consumption_sum = sum(fuel_consumption)
    else:
        consumption_sum = 0

    price, co2 = get_euro_and_co2(fuel_costs=price_frame, fuel=fuel)
    return consumption_sum * price, consumption_sum * co2


def compute_kpis(
    components: List[ComponentWrapper],
    results: pd.DataFrame,
    all_outputs: List[ComponentOutput],
    simulation_parameters: SimulationParameters,
) -> List[str]:  # noqa: MC0001
    """Calculation of Kpi's: self consumption rate, autarky rate, injection, annual CO2 emissions and annual cost.

    :param components: List of configured components in the HiSIM example
    :type components: List[ComponentWrapper]
    :param results: DataFrame of all results of the HiSIM evaluation
    :type results: pd.DataFrame
    :param all_outputs: List of all configured ComponentOutputs in the HiSIM example.
    :type all_outputs: List[ComponentOutput]
    :param simulation_parameters: Simulation parameters for HiSIM calculation
    :type simulation_parameters: SimulationParameters
    :return: Description for the report.
    :rtype: List[str]
    """
    # initialize prices
    price = 0.0
    co2 = 0.0

    price_frame = read_in_fuel_costs()

    # compute consumption and production and extract price signals
    postprocessing_results = compute_consumption_production(
        all_outputs=all_outputs, results=results
    )
    (
        electricity_price_consumption,
        electricity_price_injection,
    ) = search_electricity_prices_in_results(all_outputs=all_outputs, results=results)

    # sum consumption and production over time
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
    # h2_system_losses = 0  # explicitly compute that

    # Electricity Price
    electricity_price_constant, co2_price_constant = get_euro_and_co2(
        fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY
    )
    electricity_inj_price_constant, _ = get_euro_and_co2(
        fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY
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
                power_timeseries=postprocessing_results["consumption"]
                - self_consumption,
                timeresolution=simulation_parameters.seconds_per_timestep,
            )  # Todo: is this correct? (maybe not so important, only used if generic_price_signal is used
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
                power_timeseries=postprocessing_results["consumption"]
                * electricity_price_consumption,
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

    # (
    #     cycles_dhw,
    #     loss_dhw,
    #     use_dhw,
    #     cycles_buffer,
    #     loss_buffer,
    #     use_heating,
    # ) = compute_hot_water_storage_losses_and_cycles(
    #     components=components,
    #     all_outputs=all_outputs,
    #     results=results,
    #     timeresolution=simulation_parameters.seconds_per_timestep,
    # )

    # compute cost and co2 for investment/installation:  # Todo: function compute_investment_cost does not include all components, use capex and opex-results instead
    investment_cost, co2_footprint = compute_investment_cost(components=components)

    # get CAPEX and OPEX costs for simulated period
    capex_results_path = os.path.join(
        simulation_parameters.result_directory, "investment_cost_co2_footprint.csv"
    )
    opex_results_path = os.path.join(
        simulation_parameters.result_directory, "operational_costs_co2_footprint.csv"
    )
    if Path(opex_results_path).exists():
        opex_df = pd.read_csv(opex_results_path, index_col=0)
        total_operational_cost = opex_df["Operational Costs in EUR"].iloc[-1]
        total_operational_emisions = opex_df["Operational C02 footprint in kg"].iloc[-1]
    else:
        log.warning(
            "OPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_OPEX"
        )
        total_operational_cost = 0
        total_operational_emisions = 0

    if Path(capex_results_path).exists():
        capex_df = pd.read_csv(capex_results_path, index_col=0)
        total_investment_cost_per_simulated_period = capex_df["Investment in EUR"].iloc[
            -1
        ]
        total_device_co2_footprint_per_simulated_period = capex_df[
            "Device CO2-footprint in kg"
        ].iloc[-1]
    else:
        log.warning(
            "CAPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_CAPEX"
        )
        total_investment_cost_per_simulated_period = 0
        total_device_co2_footprint_per_simulated_period = 0

    # building temp control
    (
        set_heating_temperature_in_celsius,
        set_cooling_temperature_in_celsius,
        temperature_hours_of_building_being_below_heating_set_temperature,
        temperature_in_hours_of_building_being_above_cooling_set_temperature,
        min_temperature_reached_in_celsius,
        max_temperature_reached_in_celsius,
    ) = building_temperature_control(
        results=results, seconds_per_timestep=simulation_parameters.seconds_per_timestep
    )

    # get cycle numbers of heatpump

    number_of_cycles = get_heatpump_cycles(results=results)

    # initialize table for report
    table: List = []
    table.append(["KPI", "Value", "Unit"])
    table.append(["Consumption:", f"{consumption_sum:4.0f}", "kWh"])
    table.append(["Production:", f"{production_sum:4.0f}", "kWh"])
    table.append(["Self-consumption:", f"{self_consumption_sum:4.0f}", "kWh"])
    table.append(["Injection:", f"{injection_sum:4.0f}", "kWh"])
    table.append(["Battery losses:", f"{battery_losses:4.0f}", "kWh"])
    # table.append(["DHW storage heat loss:", f"{loss_dhw:4.0f}", "kWh"])
    # table.append(["DHW storage heat cycles:", f"{cycles_dhw:4.0f}", "Cycles"])
    # table.append(["DHW energy provided:", f"{use_dhw:4.0f}", "kWh"])
    # table.append(["Buffer storage heat loss:", f"{loss_buffer:4.0f}", "kWh"])
    # table.append(["Buffer storage heat cycles:", f"{cycles_buffer:4.0f}", "Cycles"])
    # table.append(["Heating energy provided:", f"{use_heating:4.0f}", "kWh"])
    # table.append(["Hydrogen system losses:", f"{h2_system_losses:4.0f}", "kWh"])
    # table.append(["Hydrogen storage content:", f"{0:4.0f}", "kWh"])
    table.append(["Autarky rate:", f"{autarky_rate:3.1f}", "%"])
    table.append(["Self-consumption rate:", f"{self_consumption_rate:3.1f}", "%"])
    table.append(["Cost for energy use:", f"{price:3.0f}", "EUR"])
    table.append(["CO2 emitted due energy use:", f"{co2:3.0f}", "kg"])
    table.append(
        [
            "Annual investment costs for equipment (old version):",
            f"{investment_cost:3.0f}",
            "EUR",
        ]
    )
    table.append(
        [
            "Annual CO2 footprint for equipment (old version):",
            f"{co2_footprint:3.0f}",
            "kg",
        ]
    )
    table.append(["------", "---", "---"])
    table.append(
        [
            "Investment costs for equipment per simulated period:",
            f"{total_investment_cost_per_simulated_period:3.0f}",
            "EUR",
        ]
    )
    table.append(
        [
            "CO2 footprint for equipment per simulated period:",
            f"{total_device_co2_footprint_per_simulated_period:3.0f}",
            "kg",
        ]
    )
    table.append(
        [
            "System operational costs for simulated period:",
            f"{total_operational_cost:3.0f}",
            "EUR",
        ]
    )
    table.append(
        [
            "System operational emissions for simulated period:",
            f"{total_operational_emisions:3.0f}",
            "kg",
        ]
    )
    table.append(
        [
            "Total costs for simulated period:",
            f"{(total_investment_cost_per_simulated_period + total_operational_cost):3.0f}",
            "EUR",
        ]
    )
    table.append(
        [
            "Total emissions for simulated period:",
            f"{(total_device_co2_footprint_per_simulated_period + total_operational_emisions):3.0f}",
            "kg",
        ]
    )
    table.append(
        [
            f"Temperature deviation of building indoor air temperature being below set temperature {set_heating_temperature_in_celsius} °C:",
            f"{(temperature_hours_of_building_being_below_heating_set_temperature):3.0f}",
            "°C*h",
        ]
    )
    table.append(
        [
            "Minimum building indoor air temperature reached:",
            f"{(min_temperature_reached_in_celsius):3.0f}",
            "°C",
        ]
    )
    table.append(
        [
            f"Temperature deviation of building indoor air temperature being above set temperature {set_cooling_temperature_in_celsius} °C:",
            f"{(temperature_in_hours_of_building_being_above_cooling_set_temperature):3.0f}",
            "°C*h",
        ]
    )
    table.append(
        [
            "Maximum building indoor air temperature reached:",
            f"{(max_temperature_reached_in_celsius):3.0f}",
            "°C",
        ]
    )

    table.append(["Number of heat pump cycles:", f"{number_of_cycles:3.0f}", "-"])

    # initialize json interface to pass kpi's to building_sizer
    kpi_config = KPIConfig(
        self_consumption_rate=self_consumption_rate,
        autarky_rate=autarky_rate,
        injection=injection_sum,
        economic_cost=price + investment_cost,
        co2_cost=co2 + co2_footprint,
    )

    pathname = os.path.join(simulation_parameters.result_directory, "kpi_config.json")
    config_file_written = kpi_config.to_json()  # type: ignore
    with open(pathname, "w", encoding="utf-8") as outfile:
        outfile.write(config_file_written)

    return table
