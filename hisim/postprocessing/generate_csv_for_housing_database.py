# clean

""" Computes all relevant parameters for data base of Energy System Models, as well as parameters needed for validation of the building types."""

import os

import pandas as pd

from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from typing import List
from hisim.simulationparameters import SimulationParameters
from hisim import utils


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
    building_data: pd.DataFrame,
) -> None:
    """Extracts relevant data from the HiSIM simulation and puts it together in a .csv file

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
        "FuelCell [kWh]",
    ]
    fuels_air_cooling = ["Electricity [kWh]"]
    fuels_cooking = ["Gas [kWh]", "Electricity [kWh]"]
    fuels_mobility = ["Diesel [l]", "Electricity [kWh]"]
    fuels_remaining_load = ["Electricity [kWh]"]
    fuels_annual_heating_demand = ["[kWh/(m*m*a)]"]
    fuels_building_size = ["Area [m*m]"]
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
        + fuels_heating_days * 2
        + fuels_average_temperature * 2
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

    # extract infos from used climate data to compare to climate information used for tabula evaluation
    building_code = building_data["Code_BuildingVariant"].to_list()[0]
    print(building_code)
    converting_data = pd.read_csv(
        utils.HISIMPATH["housing_reference_temperatures"]
    )
    converting_data.index = converting_data["Location"]

    # write all necesary data for building validation to csv file
    csv_frame[("Annual Heating Demand Tabula", "[kWh/(m*m*a)]")] = building_data["q_ht"].to_list()[0]
    csv_frame[("HeatingDays Tabula", "Number of Days")] = building_data["HeatingDays"].to_list()[0]
    csv_frame[("AverageTemperatureInHeatingSeason Tabula", "Temperature [C]")] = building_data["Theta_e"].to_list()[0]
    csv_frame[("Annual Heating Demand HiSIM", "[kWh/(m*m*a)]")] = csv_frame[("SpaceHeating", "Distributed Stream [kWh]")] / building_data["A_C_Ref"]
    csv_frame[("HeatingDays HiSIM", "Number of Days")] = int(converting_data.loc[building_code.split(".")[0]]["NumberOfHeatingDays"])
    csv_frame[("AverageTemperatureInHeatingSeason HiSIM", "Temperature [C]")] = float(converting_data.loc[building_code.split(".")[0]]["Average"])
    csv_frame[("Building Size", "Area [m*m]")] = building_data["A_C_Ref"]
    csv_frame[("Annual Heating Demand Tabula with HiSIM climate", "[kWh/(m*m*a)]")] = building_data["q_ht"].to_list()[0] * \
        ((20 - csv_frame[("AverageTemperatureInHeatingSeason HiSIM", "Temperature [C]")]) * csv_frame[("HeatingDays HiSIM", "Number of Days")]) / \
        ((20 - csv_frame[("AverageTemperatureInHeatingSeason Tabula", "Temperature [C]")]) * csv_frame[("HeatingDays Tabula", "Number of Days")])
    
   

    pathname = os.path.join(
        simulation_parameters.result_directory, "csv_for_housing_data_base.csv"
    )
    csv_frame.to_csv(pathname, encoding="utf-8")
