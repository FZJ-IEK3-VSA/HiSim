# clean

""" Computes all relevant parameters for data base."""

import os

import pandas as pd

from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from typing import List
from hisim.simulationparameters import SimulationParameters


def compute_energy_from_power(
    power_timeseries: pd.Series, seconds_per_timestep: int
) -> float:
    if power_timeseries.empty:
        return 0.0
    return float(power_timeseries.sum() * seconds_per_timestep / 3.6e6)


def generate_csv_for_database(
    all_outputs: List,
    results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
) -> None:
    fuels_heating = [
        "Oil [l]",
        "Gas [kWh]",
        "Distributed Stream [kWh]",
        "Solar [kWh]",
        "Electricity [kWh]",
        "FuelCell [kWh]",
    ]
    fuels_air_cooling = ["Electricity [kWh]"]
    fuels_cooking = ["Gas [kWh]", "Electricity [kWh]"]
    fuels_mobility = ["Diesel [l]", "Electricity [kWh]"]
    fuels_remaining_load = ["Electricity [kWh]"]

    device_index = (
        ["SpaceHeating"] * len(fuels_heating)
        + ["WaterHeating"] * len(fuels_heating)
        + ["AirCooling"] * len(fuels_air_cooling)
        + ["Cooking"] * len(fuels_cooking)
        + ["Transport"] * len(fuels_mobility)
        + ["RemainingLoad"] * len(fuels_remaining_load)
    )
    units = (
        fuels_heating * 2
        + fuels_air_cooling
        + fuels_cooking
        + fuels_mobility
        + fuels_remaining_load
    )
    tuples = list(zip(*[device_index, units]))

    csv_frame = pd.Series(
        [0] * len(device_index),
        index=pd.MultiIndex.from_tuples(tuples, names=["Category", "Fuel"]),
    )

    # heating_results = pd.DataFrame({'Oil': [0], 'Gas': [0], 'Distributed Stream': [0], 'Solar': [0], 'Electricity': [0],
    # 'FuelCell': [0]})

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame[("WaterHeating", "Distributed Stream [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame[("WaterHeating", "Gas [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame[("WaterHeating", "Oil [l]")] = sum(results.iloc[:, index])
                else:
                    csv_frame[
                        ("WaterHeating", "Electricity [kWh]")
                    ] = compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                    )
            elif InandOutputType.HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame[("SpaceHeating", "Distributed Stream [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame[("SpaceHeating", "Gas [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame[("SpaceHeating", "Oil [l]")] = sum(results.iloc[:, index])
                else:
                    csv_frame[
                        ("SpaceHeating", "Electricity [kWh]")
                    ] = compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                    )
            elif ComponentType.CAR in output.postprocessing_flag:
                if LoadTypes.DIESEL in output.postprocessing_flag:
                    csv_frame[("Transport", "Diesel [l]")] = sum(results.iloc[:, index])
                else:
                    csv_frame[
                        ("Transport", "Electricity [kWh]")
                    ] = compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                    )
            elif (
                InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
                in output.postprocessing_flag
            ):
                csv_frame[("RemainingLoad", "Electricity [kWh]")] = csv_frame[
                    ("RemainingLoad", "Electricity [kWh]")
                ] + compute_energy_from_power(
                    power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                )
            elif (
                InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
                in output.postprocessing_flag
            ):
                if ComponentType.SMART_DEVICE in output.postprocessing_flag:
                    csv_frame[("RemainingLoad", "Electricity [kWh]")] = csv_frame[
                        ("RemainingLoad", "Electricity [kWh]")
                    ] + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                    )
        else:
            continue

    pathname = os.path.join(
        simulation_parameters.result_directory, "csv_for_housing_data_base.csv"
    )
    csv_frame.to_csv(pathname, encoding="utf-8")
