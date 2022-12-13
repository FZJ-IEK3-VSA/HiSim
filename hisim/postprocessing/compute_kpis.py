"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from dataclasses import dataclass
from typing import Any, List

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass()
class FuelCost:

    """Defines the fuel costs in terms of euros and co2."""

    electricity_consumption_in_euro_per_kwh: float = 0.35
    electricity_injection_in_euro_per_kwh: float = 0.2
    electricity_consumption_in_kg_co2_per_kwh: float = 0.2
    oil_consumption_in_euro_per_kwh: float = 0.17
    oil_consumption_in_kg_co2_per_kwh: float = 0.3


def compute_kpis(
    results: pd.DataFrame,
    all_outputs: List[ComponentOutput],
    simulation_parameters: SimulationParameters,
) -> Any:  # noqa: MC0001
    """Calculation of several KPIs."""
    results["consumption"] = 0
    results["production"] = 0
    results["battery_charge"] = 0
    results["battery_discharge"] = 0
    results["storage"] = 0
    index: int
    output: ComponentOutput

    electricity_price_consumption = pd.Series(dtype=pd.Float64Dtype)
    electricity_price_injection = pd.Series(dtype=pd.Float64Dtype)
    self_consumption = pd.Series(dtype=pd.Float64Dtype)

    price_config = FuelCost()

    # replace that loop by searching for flags -> include also battery things and hydrogen things
    # flags for Postprocessing: cp.ComponentOutput.postprocessing_flag -> loadtpyes.InandOutputType :
    # Consumption, Production, StorageContent, ChargeDischarge
    # CHARGE_DISCHARGE from battery has + and - sign and is production and consumption both in one output
    # heat production of heat pump has - sign in summer, either separate or take absolute value
    # flags for ComponentTypes: cp.ComponentOutput.component_type
    # flags for LoadTypes: cp.ComponentOutput.load_type
    # flags for Units: cp.ComponentOutput.unit

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

            elif LoadTypes.PRICE in output.postprocessing_flag:
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

        else:
            continue

    # sum over time make it more clear and better
    consumption_sum = (
        results["consumption"].sum()
        * simulation_parameters.seconds_per_timestep
        / 3.6e6
    )
    production_sum = (
        results["production"].sum() * simulation_parameters.seconds_per_timestep / 3.6e6
    )

    if production_sum > 0:
        # account for battery
        production_with_battery = results["production"] + results["battery_discharge"]
        consumption_with_battery = results["consumption"] + results["battery_charge"]

        # evaluate injection and sum over time
        injection = production_with_battery - consumption_with_battery
        injection_sum = (
            injection[injection > 0].sum()
            * simulation_parameters.seconds_per_timestep
            / 3.6e6
        )

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
        self_consumption_sum = (
            self_consumption.sum() * simulation_parameters.seconds_per_timestep / 3.6e6
        )

        battery_losses = 0
    else:
        self_consumption_sum = 0
        injection_sum = 0
        battery_losses = 0
    h2_system_losses = 0  # explicitly compute that

    # Electricity Price
    price = 0
    co2 = 0
    if production_sum > 0:
        # evaluate electricity price
        if not electricity_price_injection.empty:
            price = (
                price
                - (
                    injection[injection > 0]
                    * electricity_price_injection[injection > 0]
                ).sum()
                * simulation_parameters.seconds_per_timestep
                / 3.6e6
            )
        else:
            price = (
                price
                - injection_sum * price_config.electricity_injection_in_euro_per_kwh
            )
        self_consumption_rate = 100 * (self_consumption_sum / production_sum)
        autarky_rate = 100 * (self_consumption_sum / consumption_sum)
    else:
        self_consumption_rate = 0
        autarky_rate = 0

    if not electricity_price_consumption.empty:
        # substract self consumption from consumption for bill calculation
        if not self_consumption.empty:
            results["consumption"] = results["consumption"] - self_consumption
        price = (
            price
            + (results["consumption"] * electricity_price_consumption).sum()
            * simulation_parameters.seconds_per_timestep
            / 3.6e6
        )
    else:
        price = (
            price
            + (consumption_sum - self_consumption_sum)
            * price_config.electricity_consumption_in_euro_per_kwh
        )
    co2 = (
        co2
        + (consumption_sum - self_consumption_sum)
        * price_config.electricity_consumption_in_kg_co2_per_kwh
    )

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
