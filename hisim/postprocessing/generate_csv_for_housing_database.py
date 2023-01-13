# clean

""" Computes all relevant parameters for data base."""

import os
from typing import Any, List, Tuple, Union

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters
from hisim.utils import HISIMPATH

def compute_energy_from_power(power_timeseries: pd.Series, seconds_per_timestep: int) -> float:
    if power_timeseries.empty:
        return 0.0
    return float(power_timeseries.sum() * seconds_per_timestep / 3.6e6)

def generate_csv_for_database(all_outputs: List, results: pd.DataFrame, simulation_parameters: SimulationParameters) -> None:
    units_heating = ['Oil', 'Gas', 'Distributed Stream', 'Solar', 'Electricity', 'FuelCell']
    units_air_cooling = ['Electricity']
    units_cooking = ['Gas', 'Electricity']
    units_mobility = ['Diesel', 'Electricity']
    units_remaining_load = ['Electricity']

    device_index = ['SpaceHeating'] * len(units_heating) + ['WaterHeating'] * len(units_heating) + \
        ['AirCooling'] * len(units_air_cooling) + ['Cooking'] * len(units_cooking) + ['Transport'] * len(units_mobility) + \
        ['RemainingLoad'] * len(units_remaining_load)
    units = units_heating * 2 + units_air_cooling + units_cooking + units_mobility + units_remaining_load
    tuples = list(zip(*[device_index, units]))

    csv_frame = pd.Series([0]*len(device_index), index=pd.MultiIndex.from_tuples(tuples, names=['Category', 'Fuel']))


    # heating_results = pd.DataFrame({'Oil': [0], 'Gas': [0], 'Distributed Stream': [0], 'Solar': [0], 'Electricity': [0],
    # 'FuelCell': [0]})

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame[('WaterHeating', 'Distributed Stream')] = sum(results.iloc[:, index]) * 1e-3
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame[('WaterHeating', 'Gas')] = sum(results.iloc[:, index]) * 1e-3
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame[('WaterHeating', 'Oil')] = sum(results.iloc[:, index])
                else:
                    csv_frame[('WaterHeating', 'Electricity')] = compute_energy_from_power(power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep)
            elif InandOutputType.HEATING in output.postprocessing_flag:
                if LoadTypes.DISTRICTHEATING in output.postprocessing_flag:
                    csv_frame[('SpaceHeating', 'Distributed Stream')] = sum(results.iloc[:, index]) * 1e-3
                elif LoadTypes.GAS in output.postprocessing_flag:
                    csv_frame[('SpaceHeating', 'Gas')] = sum(results.iloc[:, index]) * 1e-3
                elif LoadTypes.OIL in output.postprocessing_flag:
                    csv_frame[('SpaceHeating', 'Oil')] = sum(results.iloc[:, index])
                else:
                    csv_frame[('SpaceHeating', 'Electricity')] = compute_energy_from_power(power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep)
            elif ComponentType.CAR in output.postprocessing_flag:
                if LoadTypes.DIESEL in output.postprocessing_flag:
                    csv_frame[('Transport', 'Diesel')] = sum(results.iloc[:, index])
                else:
                    csv_frame[('Transport', 'Electricity')] = compute_energy_from_power(power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep)
            elif InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in output.postprocessing_flag:
                csv_frame[('RemainingLoad', 'Electricity')] = csv_frame[('RemainingLoad', 'Electricity')] + \
                    compute_energy_from_power(power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep)
            elif InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED in output.postprocessing_flag:
                if ComponentType.SMART_DEVICE in output.postprocessing_results:
                    csv_frame[('RemainingLoad', 'Electricity')] = csv_frame[('RemainingLoad', 'Electricity')] + \
                    compute_energy_from_power(power_timeseries=results.iloc[:, index],
                    seconds_per_timestep=simulation_parameters.seconds_per_timestep)
        else:
            continue

    pathname = os.path.join(simulation_parameters.result_directory, "csv_for_housing_data_base.csv")
    csv_frame.to_csv(pathname, encoding='utf-8')