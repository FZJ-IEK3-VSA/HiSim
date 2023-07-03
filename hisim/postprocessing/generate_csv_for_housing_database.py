# clean

""" Computes all relevant parameters for data base of Energy System Models, as well as parameters needed for validation of the building types."""

import datetime as dt
import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from hisim import log, utils
from hisim.components.loadprofilegenerator_connector import OccupancyConfig
from hisim.loadtypes import (ComponentType, HeatingSystems, InandOutputType,
                             LoadTypes)
from hisim.simulationparameters import SimulationParameters


def compute_energy_from_power(
    power_timeseries: pd.Series, seconds_per_timestep: int
) -> float:
    """Computes energy from power value."""
    if power_timeseries.empty:
        return 0.0
    return float(power_timeseries.sum() * seconds_per_timestep / 3.6e6)


def get_factor_cooking(
        occupancy_config: OccupancyConfig
) -> float:
    """Reads in portion of electricity consumption which is assigned to cooking. """

    if occupancy_config.profile_name != "AVG":
        return 0
    scaling_factors = pd.read_csv(utils.HISIMPATH["occupancy_scaling_factors_per_country"], encoding="utf-8", sep=";", index_col=1)
    if occupancy_config.country_name in scaling_factors.index:
        scaling_factor_line = scaling_factors.loc[occupancy_config.country_name]
    else:
        scaling_factor_line = scaling_factors.loc["EU"]
        log.warning("Scaling Factor for " + occupancy_config.country_name + "is not available, EU average is used per default.")
    return float(scaling_factor_line["ratio cooking to total"])


def compute_seasonal(
        csv_frame_seasonal: pd.DataFrame, index_in_seasonal_frame: Tuple[str, str], factor: float, output: pd.Series, day: pd.Series, night: pd.Series
) -> pd.DataFrame:
    """Takes annual time series and computes average consumption during day/night in summer, winter and intermediate time.

    Summer from daylight 21.06.2019 to daylight 23.09.2019 (total 94 days)
    Winter from 01.01.2019 - daylight 23.03.2019 and daylight 21.12.2019 to 31.12.2019 (total 92 days)
    Intermediate from daylight 23.03.2029 - daylight 21.06.2019 and daylight 23.09.2019 - daylight 21.12.2019 (total 179)
    """

    output_day = output[day.index]
    output_night = output[night.index]
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Summer-Day"] = output_day[
        ((output_day.index > dt.datetime(year=2019, month=6, day=21, hour=0)) &
        (output_day.index < dt.datetime(year=2019, month=9, day=23, hour=0)))].sum() * factor / 94
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Summer-Night"] = output_night[
        ((output_night.index > dt.datetime(year=2019, month=6, day=21, hour=12)) &
        (output_night.index < dt.datetime(year=2019, month=9, day=23, hour=12)))].sum() * factor / 94
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Winter-Day"] = output_day[
        ((output_day.index > dt.datetime(year=2019, month=12, day=21, hour=0)) |
        (output_day.index < dt.datetime(year=2019, month=3, day=23, hour=0)))].sum() * factor / 92
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Winter-Night"] = output_night[
        ((output_night.index > dt.datetime(year=2019, month=12, day=21, hour=12)) |
        (output_night.index < dt.datetime(year=2019, month=3, day=23, hour=12)))].sum() * factor / 92
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Intermediate-Day"] = output_day[
        ((output_day.index > dt.datetime(year=2019, month=3, day=23, hour=0)) &
        (output_day.index < dt.datetime(year=2019, month=6, day=21, hour=0))) |
        ((output_day.index > dt.datetime(year=2019, month=9, day=23, hour=0)) &
        (output_day.index < dt.datetime(year=2019, month=12, day=21, hour=0)))].sum() * factor / 179
    csv_frame_seasonal.loc[index_in_seasonal_frame, "Intermediate-Night"] = output_night[
        ((output_night.index > dt.datetime(year=2019, month=3, day=23, hour=12)) &
        (output_night.index < dt.datetime(year=2019, month=6, day=21, hour=12))) |
        ((output_night.index > dt.datetime(year=2019, month=9, day=23, hour=12)) &
        (output_night.index < dt.datetime(year=2019, month=12, day=21, hour=12)))].sum() * factor / 179

    return csv_frame_seasonal


def generate_csv_for_database(
    all_outputs: List,
    results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
    building_data: pd.DataFrame,
    occupancy_config: Optional[OccupancyConfig],
) -> None:
    """Extracts relevant data from the HiSIM simulation and puts it together in a .csv file.

    :param all_outputs: List of all outputs
    :type all_outputs: List
    :param results: DataFrame containing all outputs at all time steps
    :type results: pd.DataFrame
    :param simulation_parameters: parameters of the simulation
    :type simulation_parameters: SimulationParameters
    :param building_data: tabula data of building type
    :type building_data: pd.DataFrame
    """

    fuels_heating = [
        "Oil [l]",
        "Gas [kWh]",
        "Distributed Stream [kWh]",
        "Solar [kWh]",
        "Electricity [kWh]",
        "Electricity - HeatPump [kWh]",
        "FuelCell [kWh]",
    ]
    fuels_air_cooling = ["Electricity [kWh]"]
    fuels_cooking = ["Gas [kWh]", "Electricity [kWh]"]
    fuels_mobility = ["Diesel [l]", "Electricity [kWh]"]
    fuels_remaining_load = ["Electricity [kWh]"]
    fuels_annual_heating_demand = ["[kWh/(m*m*a)]"]
    fuels_building_size = ["Area [m*m]"]
    fuels_construction_year = ["Period start", "Period end"]
    fuels_heating_days = ["Number of Days"]
    fuels_average_temperature = ["Temperature [C]"]

    device_index = (
        ["SpaceHeating"] * len(fuels_heating)
        + ["WaterHeating"] * len(fuels_heating)
        + ["AirCooling"] * len(fuels_air_cooling)
        + ["Cooking"] * len(fuels_cooking)
        + ["Transport"] * len(fuels_mobility)
        + ["RemainingLoad"] * len(fuels_remaining_load)
        + ["Annual Heating Demand Tabula"]
        + ["Annual Heating Demand HiSIM"]
        + ["Annual Heating Demand Tabula with HiSIM climate"]
        + ["Building Size"]
        + ["Construction Year"] * 2
        + ["HeatingDays Tabula"]
        + ["HeatingDays HiSIM"]
        + ["AverageTemperatureInHeatingSeason Tabula"]
        + ["AverageTemperatureInHeatingSeason HiSIM"]
    )
    units = (
        fuels_heating * 2
        + fuels_air_cooling
        + fuels_cooking
        + fuels_mobility
        + fuels_remaining_load
        + fuels_annual_heating_demand * 3
        + fuels_building_size
        + fuels_construction_year
        + fuels_heating_days * 2
        + fuels_average_temperature * 2
    )
    tuples = list(zip(*[device_index, units]))

    csv_frame_annual = pd.Series(
        [0] * len(device_index),
        index=pd.MultiIndex.from_tuples(tuples, names=["Category", "Fuel"]),
    )
    csv_frame_seasonal = pd.DataFrame({
        "Summer-Day": [0] * (len(device_index) - 10),
        "Summer-Night": [0] * (len(device_index) - 10),
        "Winter-Day": [0] * (len(device_index) - 10),
        "Winter-Night": [0] * (len(device_index) - 10),
        "Intermediate-Day": [0] * (len(device_index) - 10),
        "Intermediate-Night": [0] * (len(device_index) - 10),
    }, index=pd.MultiIndex.from_tuples(tuples[:-10], names=["Category", "Fuel"]),
    )

    remaining_electricity_annual = 0.0
    remaining_electricity_seasonal = np.array([0.0] * 6)

    # get indices for day and night:
    for index, output in enumerate(all_outputs):
        if output.component_name == "Weather" and output.field_name == "Altitude":
            altitude_data = results.iloc[:, index]
            day = altitude_data[altitude_data > 0]
            night = altitude_data[altitude_data <= 0]

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame_annual[("WaterHeating", "Distributed Stream [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("WaterHeating", "Distributed Stream [kWh]"),
                        factor=1e-3, output=results.iloc[:, index], day=day, night=night,
                    )
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame_annual[("WaterHeating", "Gas [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("WaterHeating", "Gas [kWh]"),
                        output=results.iloc[:, index], factor=1e-3, day=day, night=night,
                    )
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame_annual[("WaterHeating", "Oil [l]")] = sum(results.iloc[:, index])
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("WaterHeating", "Oil [l]"),
                        output=results.iloc[:, index], factor=1, day=day, night=night,
                    )
                else:
                    if HeatingSystems.HEAT_PUMP in output.postprocessing_flag:
                        csv_frame_annual[
                            ("WaterHeating", "Electricity - HeatPump [kWh]")
                        ] = compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                        )
                        csv_frame_seasonal = compute_seasonal(
                            csv_frame_seasonal=csv_frame_seasonal,
                            index_in_seasonal_frame=("WaterHeating", "Electricity - HeatPump [kWh]"),
                            factor=simulation_parameters.seconds_per_timestep / 3.6e6,
                            output=results.iloc[:, index], day=day, night=night,
                        )
                    elif HeatingSystems.ELECTRIC_HEATING in output.postprocessing_flag:
                        csv_frame_annual[
                            ("WaterHeating", "Electricity [kWh]")
                        ] = compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                        )
                        csv_frame_seasonal = compute_seasonal(
                            csv_frame_seasonal=csv_frame_seasonal,
                            index_in_seasonal_frame=("WaterHeating", "Electricity [kWh]"),
                            factor=simulation_parameters.seconds_per_timestep / 3.6e6,
                            output=results.iloc[:, index], day=day, night=night,
                        )
            elif InandOutputType.HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame_annual[("SpaceHeating", "Distributed Stream [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("SpaceHeating", "Distributed Stream [kWh]"),
                        factor=1e-3, output=results.iloc[:, index], day=day, night=night,
                    )
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame_annual[("SpaceHeating", "Gas [kWh]")] = (
                        sum(results.iloc[:, index]) * 1e-3
                    )
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("SpaceHeating", "Gas [kWh]"),
                        factor=1e-3, output=results.iloc[:, index], day=day, night=night,
                    )
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame_annual[("SpaceHeating", "Oil [l]")] = sum(results.iloc[:, index])
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("SpaceHeating", "Oil [l]"),
                        factor=1, output=results.iloc[:, index], day=day, night=night,
                    )
                else:
                    if HeatingSystems.HEAT_PUMP in output.postprocessing_flag:
                        csv_frame_annual[
                            ("SpaceHeating", "Electricity - HeatPump [kWh]")
                        ] = compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                        )
                        csv_frame_seasonal = compute_seasonal(
                            csv_frame_seasonal=csv_frame_seasonal,
                            index_in_seasonal_frame=("SpaceHeating", "Electricity - HeatPump [kWh]"),
                            factor=simulation_parameters.seconds_per_timestep / 3.6e6,
                            output=results.iloc[:, index], day=day, night=night,
                        )
                    elif HeatingSystems.ELECTRIC_HEATING in output.postprocessing_flag:
                        csv_frame_annual[
                            ("SpaceHeating", "Electricity [kWh]")
                        ] = compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                        )
                        csv_frame_seasonal = compute_seasonal(
                            csv_frame_seasonal=csv_frame_seasonal,
                            index_in_seasonal_frame=("SpaceHeating", "Electricity [kWh]"),
                            factor=simulation_parameters.seconds_per_timestep / 3.6e6,
                            output=results.iloc[:, index], day=day, night=night,
                        )
            elif ComponentType.CAR in output.postprocessing_flag:
                if LoadTypes.DIESEL in output.postprocessing_flag:
                    csv_frame_annual[("Transport", "Diesel [l]")] = sum(results.iloc[:, index])
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("Transport", "Diesel [l]"),
                        factor=1, output=results.iloc[:, index], day=day, night=night,
                    )
                else:
                    csv_frame_annual[
                        ("Transport", "Electricity [kWh]")
                    ] = compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                    )
                    csv_frame_seasonal = compute_seasonal(
                        csv_frame_seasonal=csv_frame_seasonal,
                        index_in_seasonal_frame=("Transport", "Electricity [kWh]"),
                        factor=simulation_parameters.seconds_per_timestep / 3.6e6,
                        output=results.iloc[:, index], day=day, night=night,
                    )
            elif (
                InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
                in output.postprocessing_flag
            ):
                remaining_electricity_annual = remaining_electricity_annual + compute_energy_from_power(
                    power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                )
                remaining_electricity_seasonal_item = compute_seasonal(
                    csv_frame_seasonal=csv_frame_seasonal, index_in_seasonal_frame=("RemainingLoad", "Electricity [kWh]"),
                    factor=simulation_parameters.seconds_per_timestep / 3.6e6, output=results.iloc[:, index],
                    day=day, night=night).loc[("RemainingLoad", "Electricity [kWh]")]
                remaining_electricity_seasonal = remaining_electricity_seasonal + np.array(
                    remaining_electricity_seasonal_item
                )
            elif (
                InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
                in output.postprocessing_flag
            ):
                remaining_electricity_annual = remaining_electricity_annual + compute_energy_from_power(
                    power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep,
                )
                remaining_electricity_seasonal_item = compute_seasonal(
                    csv_frame_seasonal=csv_frame_seasonal, index_in_seasonal_frame=("RemainingLoad", "Electricity [kWh]"),
                    factor=simulation_parameters.seconds_per_timestep / 3.6e6, output=results.iloc[:, index],
                    day=day, night=night).loc[("RemainingLoad", "Electricity [kWh]")]
                remaining_electricity_seasonal = remaining_electricity_seasonal + np.array(
                    remaining_electricity_seasonal_item
                )
        else:
            continue

    if occupancy_config is None:
        factor_cooking = 0.0
    else:
        factor_cooking = get_factor_cooking(occupancy_config)

    csv_frame_annual[("RemainingLoad", "Electricity [kWh]")] = remaining_electricity_annual * (1 - factor_cooking)
    csv_frame_annual[("Cooking", "Electricity [kWh]")] = remaining_electricity_annual * factor_cooking
    csv_frame_seasonal.loc[("RemainingLoad", "Electricity [kWh]")] = remaining_electricity_seasonal * (1 - factor_cooking)
    csv_frame_seasonal.loc[("Cooking", "Electricity [kWh]")] = remaining_electricity_seasonal * factor_cooking

    # extract infos from used climate data to compare to climate information used for tabula evaluation
    building_code = building_data["Code_BuildingVariant"].to_list()[0]
    converting_data = pd.read_csv(utils.HISIMPATH["housing_reference_temperatures"])
    converting_data.index = converting_data["Location"]  # type: ignore

    # write all necesary data for building validation to csv file
    csv_frame_annual[("Annual Heating Demand Tabula", "[kWh/(m*m*a)]")] = building_data[
        "q_ht"
    ].to_list()[0]
    csv_frame_annual[("HeatingDays Tabula", "Number of Days")] = building_data[
        "HeatingDays"
    ].to_list()[0]
    csv_frame_annual[
        ("AverageTemperatureInHeatingSeason Tabula", "Temperature [C]")
    ] = building_data["Theta_e"].to_list()[0]
    csv_frame_annual[("Annual Heating Demand HiSIM", "[kWh/(m*m*a)]")] = (
        csv_frame_annual[("SpaceHeating", "Distributed Stream [kWh]")]
        / building_data["A_C_Ref"]
    ).iloc[0]
    csv_frame_annual[("HeatingDays HiSIM", "Number of Days")] = int(
        converting_data.loc[building_code.split(".")[0]]["NumberOfHeatingDays"]
    )
    csv_frame_annual[("AverageTemperatureInHeatingSeason HiSIM", "Temperature [C]")] = float(
        converting_data.loc[building_code.split(".")[0]]["Average"]
    )
    csv_frame_annual[("Building Size", "Area [m*m]")] = building_data["A_C_Ref"].iloc[0]
    csv_frame_annual[("Construction Year", "Period start")] = building_data["Year1_Building"].iloc[0]
    csv_frame_annual[("Construction Year", "Period end")] = building_data["Year2_Building"].iloc[0]
    csv_frame_annual[("Annual Heating Demand Tabula with HiSIM climate", "[kWh/(m*m*a)]")] = (
        building_data["q_ht"].to_list()[0]
        * (
            (
                20
                - csv_frame_annual[
                    ("AverageTemperatureInHeatingSeason HiSIM", "Temperature [C]")
                ]
            )
            * csv_frame_annual[("HeatingDays HiSIM", "Number of Days")]
        )
        / (
            (
                20
                - csv_frame_annual[
                    ("AverageTemperatureInHeatingSeason Tabula", "Temperature [C]")
                ]
            )
            * csv_frame_annual[("HeatingDays Tabula", "Number of Days")]
        )
    )

    csv_frame_annual.to_csv(os.path.join(
        simulation_parameters.result_directory, "csv_for_housing_data_base_annual.csv"
    ), encoding="utf-8")
    csv_frame_seasonal.to_csv(os.path.join(
        simulation_parameters.result_directory, "csv_for_housing_data_base_seasonal.csv"
    ), encoding="utf-8")
