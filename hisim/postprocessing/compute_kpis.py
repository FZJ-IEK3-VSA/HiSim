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

from hisim import log
from hisim.postprocessing.investment_cost_co2 import compute_investment_cost


def building_temperature_control_and_heating_load(
    results: pd.DataFrame, seconds_per_timestep: int, components: List[ComponentWrapper]
) -> Tuple[Any, Any, float, float, float, float, float, float]:
    """Check the building indoor air temperature.

    Check for all timesteps and count the
    time when the temperature is outside of the building set temperatures
    in order to verify if energy system provides enough heating and cooling.
    """

    temperature_difference_of_building_being_below_heating_set_temperature = 0
    temperature_difference_of_building_being_below_cooling_set_temperature = 0

    # get set temperatures
    wrapped_building_component = None
    for wrapped_component in components:
        if "Building" in wrapped_component.my_component.get_classname():
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
    scaled_conditioned_floor_area_in_m2 = getattr(
        wrapped_building_component.my_component, "my_building_information"
    ).scaled_conditioned_floor_area_in_m2
    specific_heating_load_in_watt_per_m2 = heating_load_in_watt / scaled_conditioned_floor_area_in_m2

    for column in results.columns:
        if "TemperatureIndoorAir" in column.split(sep=" "):
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
                temperature_difference_of_building_being_below_heating_set_temperature * seconds_per_timestep / 3600
            )

            temperature_hours_of_building_being_above_cooling_set_temperature = (
                temperature_difference_of_building_being_below_cooling_set_temperature * seconds_per_timestep / 3600
            )

            # get also max and min indoor air temperature
            min_temperature_reached_in_celsius = float(min(results[column].values))
            max_temperature_reached_in_celsius = float(max(results[column].values))
            break

    return (
        set_heating_temperature_in_celsius,
        set_cooling_temperature_in_celsius,
        temperature_hours_of_building_being_below_heating_set_temperature,
        temperature_hours_of_building_being_above_cooling_set_temperature,
        min_temperature_reached_in_celsius,
        max_temperature_reached_in_celsius,
        heating_load_in_watt,
        specific_heating_load_in_watt_per_m2,
    )


def get_heatpump_cycles(
    results: pd.DataFrame,
) -> float:
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


def get_heat_pump_seasonal_performance_factor(results: pd.DataFrame, seconds_per_timestep: int) -> float:
    """Get SPF from heat pump over simulated period.

    Transform thermal and electrical power from heat pump in energies.
    """
    thermal_output_energy_in_watt_hour = 0.0
    electrical_energy_in_watt_hour = 1.0
    for column in results.columns:
        if "ThermalOutputPower" in column.split(sep=" "):
            # take only output values for heating
            thermal_output_power_values_in_watt = [value for value in results[column].values if value > 0.0]
            # get energy from power
            thermal_output_energy_in_watt_hour = sum(thermal_output_power_values_in_watt) * seconds_per_timestep / 3600
        if "ElectricalInputPower" in column.split(sep=" "):
            # get electrical energie values
            electrical_energy_in_watt_hour = sum(results[column].values) * seconds_per_timestep / 3600

    # calculate SPF
    spf = thermal_output_energy_in_watt_hour / electrical_energy_in_watt_hour
    return spf


def get_heat_pump_kpis(
    results: pd.DataFrame, seconds_per_timestep: int, components: List[ComponentWrapper]
) -> Tuple[float, float]:
    """Get some KPIs from Heat Pump."""
    number_of_cycles = 0.0
    spf = 0.0
    # check if Heat Pump was used in components
    for wrapped_component in components:
        if "HeatPump" in wrapped_component.my_component.component_name:
            # get number of heat pump cycles over simulated period
            number_of_cycles = get_heatpump_cycles(results=results)
            # get SPF
            spf = get_heat_pump_seasonal_performance_factor(results=results, seconds_per_timestep=seconds_per_timestep)
        # heat pump was not used
        else:
            pass

    return number_of_cycles, spf


def get_electricity_to_and_from_grid_from_electricity_meter(
    wrapped_components: List[ComponentWrapper],
) -> Tuple[float, float]:
    """Get the electricity injected into the grid or taken from grid measured by the electricity meter."""
    # go through all wrapped components and try to find electricity meter
    for wrapped_component in wrapped_components:
        if "ElectricityMeter" in wrapped_component.my_component.component_name:
            total_energy_from_grid_in_kwh = wrapped_component.my_component.config.total_energy_from_grid_in_kwh
            total_energy_to_grid_in_kwh = wrapped_component.my_component.config.total_energy_to_grid_in_kwh
            return total_energy_to_grid_in_kwh, total_energy_from_grid_in_kwh
    return 0, 0


def read_in_fuel_costs() -> pd.DataFrame:
    """Reads data for costs and co2 emissions of fuels from csv."""
    price_frame = pd.read_csv(HISIMPATH["fuel_costs"], sep=";", usecols=[0, 2, 4])
    price_frame.index = price_frame["fuel type"]  # type: ignore
    price_frame.drop(columns=["fuel type"], inplace=True)
    return price_frame


def get_euro_and_co2(fuel_costs: pd.DataFrame, fuel: Union[LoadTypes, InandOutputType]) -> Tuple[float, float]:
    """Returns cost (Euro) of kWh of fuel and CO2 consumption (kg) of kWh of fuel."""
    column = fuel_costs.iloc[fuel_costs.index == fuel.value]
    return (float(column["Cost"].iloc[0]), float(column["Footprint"].iloc[0]))


def compute_consumption_production(all_outputs: List, results: pd.DataFrame) -> pd.DataFrame:
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

    postprocessing_results = pd.DataFrame()
    postprocessing_results["consumption"] = pd.DataFrame(results.iloc[:, consumption_ids]).clip(lower=0).sum(axis=1)
    postprocessing_results["production"] = pd.DataFrame(results.iloc[:, production_ids]).clip(lower=0).sum(axis=1)

    postprocessing_results["battery_charge"] = (
        pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(lower=0).sum(axis=1)
    )
    postprocessing_results["battery_discharge"] = pd.DataFrame(results.iloc[:, battery_charge_discharge_ids]).clip(
        upper=0
    ).sum(axis=1) * (-1)

    return postprocessing_results


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
                production_with_battery[production_with_battery <= postprocessing_results["consumption"]],
                postprocessing_results["consumption"][postprocessing_results["consumption"] < production_with_battery],
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


def compute_energy_from_power(power_timeseries: pd.Series, timeresolution: int) -> float:
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

    :param components: List of configured components in the HiSIM system setup
    :type components: List[ComponentWrapper]
    :param results: DataFrame of all results of the HiSIM evaluation
    :type results: pd.DataFrame
    :param all_outputs: List of all configured ComponentOutputs in the HiSIM system setup.
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
    postprocessing_results = compute_consumption_production(all_outputs=all_outputs, results=results)
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
    electricity_inj_price_constant, _ = get_euro_and_co2(fuel_costs=price_frame, fuel=LoadTypes.ELECTRICITY)

    if production_sum > 0:
        # evaluate electricity price
        if not electricity_price_injection.empty:
            price = price - compute_energy_from_power(
                power_timeseries=injection[injection > 0] * electricity_price_injection[injection > 0],
                timeresolution=simulation_parameters.seconds_per_timestep,
            )
            price = price + compute_energy_from_power(
                power_timeseries=postprocessing_results["consumption"] - self_consumption,
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

    # compute cost and co2 for investment/installation:  # Todo: function compute_investment_cost does not include all components, use capex and opex-results instead
    investment_cost, co2_footprint = compute_investment_cost(components=components)

    # get CAPEX and OPEX costs for simulated period
    capex_results_path = os.path.join(simulation_parameters.result_directory, "investment_cost_co2_footprint.csv")
    opex_results_path = os.path.join(simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    if Path(opex_results_path).exists():
        opex_df = pd.read_csv(opex_results_path, index_col=0)
        total_operational_cost = opex_df["Operational Costs in EUR"].iloc[-1]
        total_operational_emisions = opex_df["Operational C02 footprint in kg"].iloc[-1]
    else:
        log.warning("OPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_OPEX")
        total_operational_cost = 0
        total_operational_emisions = 0

    if Path(capex_results_path).exists():
        capex_df = pd.read_csv(capex_results_path, index_col=0)
        total_investment_cost_per_simulated_period = capex_df["Investment in EUR"].iloc[-1]
        total_device_co2_footprint_per_simulated_period = capex_df["Device CO2-footprint in kg"].iloc[-1]
    else:
        log.warning("CAPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_CAPEX")
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
        heating_load_in_watt,
        specific_heating_load_in_watt_per_m2,
    ) = building_temperature_control_and_heating_load(
        results=results,
        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
        components=components,
    )

    # get cycle numbers of heatpump
    number_of_cycles, spf = get_heat_pump_kpis(
        results=results,
        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
        components=components,
    )

    # get electricity from and to grid from electricity meter
    (
        total_energy_to_grid_in_kwh,
        total_energy_from_grid_in_kwh,
    ) = get_electricity_to_and_from_grid_from_electricity_meter(wrapped_components=components)

    # initialize table for report
    table: List = []
    table.append(["KPI", "Value", "Unit"])
    table.append(["Consumption:", f"{consumption_sum:4.0f}", "kWh"])
    table.append(["Production:", f"{production_sum:4.0f}", "kWh"])
    table.append(["Self-consumption:", f"{self_consumption_sum:4.0f}", "kWh"])
    table.append(["Injection:", f"{injection_sum:4.0f}", "kWh"])
    table.append(["Battery losses:", f"{battery_losses:4.0f}", "kWh"])
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
    table.append(["Building heating load:", f"{(heating_load_in_watt):3.0f}", "W"])
    table.append(["Specific heating load:", f"{(specific_heating_load_in_watt_per_m2):3.0f}", "W"])

    table.append(["Number of heat pump cycles:", f"{number_of_cycles:3.0f}", "-"])
    table.append(["Seasonal performance factor of heat pump:", f"{spf:3.0f}", "-"])
    table.append(
        [
            "Total energy from electricity grid:",
            f"{total_energy_from_grid_in_kwh:3.0f}",
            "kWh",
        ]
    )
    table.append(
        [
            "Total energy to electricity grid:",
            f"{total_energy_to_grid_in_kwh:3.0f}",
            "kWh",
        ]
    )

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
