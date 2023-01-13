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

def generate_csv_for_database(all_outputs: List, results: pd.DataFrame) -> None:
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

    csv_frame = pd.Series([0]*len(device_index), index=pd.MultiIndex.from_tuples, names=['Category', 'Fuel'])


    # heating_results = pd.DataFrame({'Oil': [0], 'Gas': [0], 'Distributed Stream': [0], 'Solar': [0], 'Electricity': [0],
    # 'FuelCell': [0]})

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
