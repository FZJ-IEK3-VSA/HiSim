""" Contains various utility functions and utility classes. """
# clean
import datetime as dt
import gc
import hashlib
import inspect
import json
import os
from functools import wraps
from functools import reduce as freduce
from timeit import default_timer as timer
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import psutil
import pytz

from hisim import log
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


def get_input_directory() -> str:
    """Gets the absolute path to the inputs directory."""
    return os.path.join(hisim_abs_path, "inputs")


# Retrieves hisim directory absolute path
hisim_abs_path = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
hisim_inputs = os.path.join(hisim_abs_path, "inputs")
hisim_results = os.path.join(hisim_abs_path, "results")
hisim_postprocessing_img = os.path.join(hisim_abs_path, "postprocessing", "report")  # noqa

HISIMPATH: Dict[str, Any] = {
    "inputs": hisim_inputs,
    "cache_dir": os.path.join(hisim_abs_path, "inputs", "cache"),
    "cache_indices": os.path.join(hisim_abs_path, "inputs", "cache", "cache_indices.json"),
    "cfg": os.path.join(hisim_abs_path, "inputs", "cfg.json"),
    "utsp_results": hisim_results,
    "utsp_example_results": os.path.join(hisim_inputs, "LPGResults_for_tests", "Results"),
    "utsp_example_reports": os.path.join(hisim_inputs, "LPGResults_for_tests", "Reports"),
    "housing": os.path.join(hisim_inputs, "housing", "data_processed", "episcope-tabula.csv"),
    "housing_reference_temperatures": os.path.join(
        hisim_inputs,
        "housing",
        "data_processed",
        "heating_reference_temperature_per_location.csv",
    ),
    "heater_efficiencies": os.path.join(
        hisim_inputs,
        "housing",
        "data_processed",
        "heater_efficiencies.csv",
    ),
    "fuel_costs": os.path.join(hisim_abs_path, "modular_household", "emission_factors_and_costs_fuels.csv"),
    "component_costs": os.path.join(hisim_abs_path, "modular_household", "emission_factors_and_costs_devices.csv"),
    "occupancy_scaling_factors_per_country": os.path.join(
        hisim_inputs, "loadprofiles", "WHY_reference_data", "scaling_factors_demand.csv"
    ),
    "occupancy": {
        "CHR01 Couple both at Work": {
            "number_of_residents": [
                os.path.join(
                    hisim_inputs,
                    "loadprofiles",
                    "predefined_lpg_household_chr01",
                    "data_processed",
                    "BodilyActivityLevel.High.HH1.json",
                ),
                os.path.join(
                    hisim_inputs,
                    "loadprofiles",
                    "predefined_lpg_household_chr01",
                    "data_processed",
                    "BodilyActivityLevel.Low.HH1.json",
                ),
            ],
            "electricity_consumption": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "predefined_lpg_household_chr01",
                "data_processed",
                "SumProfiles.HH1.Electricity.csv",
            ),
            "electricity_consumption_without_washing_machine_and_dishwasher": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "predefined_lpg_household_chr01",
                "data_processed",
                "SumProfiles.NoFlex.HH1.Electricity.csv",
            ),
            "heating_by_devices": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "predefined_lpg_household_chr01",
                "data_processed",
                "SumProfiles.HH1.Inner Device Heat Gains.csv",
            ),
            "water_consumption": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "predefined_lpg_household_chr01",
                "data_processed",
                "SumProfiles.HH1.Warm Water.csv",
            ),
        },
        "AVG": {
            "number_of_residents": [
                os.path.join(
                    hisim_inputs,
                    "loadprofiles",
                    "WHY_reference_data",
                    "BodilyActivityLevel.High.HH1.json",
                ),
                os.path.join(
                    hisim_inputs,
                    "loadprofiles",
                    "WHY_reference_data",
                    "BodilyActivityLevel.Low.HH1.json",
                ),
            ],
            "electricity_consumption": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "WHY_reference_data",
                "AVG.csv",
            ),
            "water_consumption": os.path.join(
                hisim_inputs,
                "loadprofiles",
                "WHY_reference_data",
                "WarmWater.csv",
            ),
        },
    },
    "photovoltaic": {
        "sandia_modules_new": os.path.join(hisim_inputs, "photovoltaic", "data_processed", "sandia_modules_new.csv"),
        "sandia_modules": os.path.join(hisim_inputs, "photovoltaic", "data_processed", "sandia_modules.csv"),
        "sandia_inverters": os.path.join(hisim_inputs, "photovoltaic", "data_processed", "sandia_inverters.csv"),
        "cec_modules": os.path.join(hisim_inputs, "photovoltaic", "data_processed", "cec_modules.csv"),
        "cec_inverters": os.path.join(hisim_inputs, "photovoltaic", "data_processed", "cec_inverters.csv"),
    },
    "chp_system": os.path.join(hisim_inputs, "chp_system"),
    "smart_appliances": os.path.join(hisim_inputs, "smart_devices", "data_processed", "smart_devices.json"),
    "frank_data": os.path.join(
        hisim_inputs,
        "loadprofiles",
        "electrical-spaceheating-warmwater-photovoltaic_1-household",
        "data_raw",
        "VDI 4655",
    ),
    "report": os.path.join(hisim_abs_path, "results", "report.pdf"),
    "advanced_battery": {
        "parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "parameter", "PerModPAR.xlsx"),
        "reference_case": os.path.join(
            hisim_abs_path,
            "inputs",
            "advanced_battery",
            "reference_case",
            "ref_case_data.npz",
        ),
        "siemens_junelight": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "Siemens_Junelight.npy"),
    },
    "LoadProfileGenerator_export_directory": os.path.join(os.path.join("D:", os.sep, "Work")),
    "bat_parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "Siemens_Junelight.npy"),
    "modular_household": os.path.join(hisim_abs_path, "modular_household"),
    "price_signal": {
        "PricePurchase": os.path.join(hisim_inputs, "price_signal", "PricePurchase.csv"),
        "FeedInTarrif": os.path.join(hisim_inputs, "price_signal", "FeedInTarrif.csv"),
    },
}


def load_smart_appliance(name):  # noqa
    """Loads file for a single smart appliance by name."""
    with open(HISIMPATH["smart_appliances"], encoding="utf-8") as filestream:
        data = json.load(filestream)
    return data[name]


def convert_lpg_timestep_to_utc(data: List[int], year: int, seconds_per_timestep: int) -> List[int]:
    """Tranform LPG timesteps (list of integers) from local time to UTC."""
    timeshifts = pytz.timezone("Europe/Berlin")._utc_transition_times  # type: ignore # pylint: disable=W0212
    timeshifts = [elem for elem in timeshifts if elem.year == year]
    steps_per_hour = int(3600 / seconds_per_timestep)
    timeshift1_as_step = (
        int((timeshifts[0] - dt.datetime(year=year, month=1, day=1)).seconds / seconds_per_timestep) - 1
    )
    timeshift2_as_step = (
        int((timeshifts[1] - dt.datetime(year=year, month=1, day=1)).seconds / seconds_per_timestep) - 1
    )

    data_utc = []
    for elem in data:
        if elem < timeshift1_as_step or elem > timeshift2_as_step:
            data_utc.append(elem - steps_per_hour)
        else:
            data_utc.append(elem - 2 * steps_per_hour)
    return data


def convert_lpg_data_to_utc(data: pd.DataFrame, year: int) -> pd.DataFrame:
    """Transform LPG data from local time (not having explicit time shifts) to UTC."""
    # convert Time information to pandas datetime and make it to index
    data.index = pd.DatetimeIndex(pd.to_datetime(data["Time"]))
    lastdate = data.index[-1]

    # find out time shifts of selected year
    timeshifts = pytz.timezone("Europe/Berlin")._utc_transition_times  # type: ignore # pylint: disable=W0212
    timeshifts = [elem for elem in timeshifts if elem.year == year]

    # delete hour in spring if neceary:
    if lastdate > timeshifts[0]:
        indices_of_additional_hour_in_spring = data.loc[
            timeshifts[0]
            + dt.timedelta(seconds=3600) : timeshifts[0]  # noqa: E203
            + dt.timedelta(seconds=60 * (60 + 59))
        ].index

        data.drop(index=indices_of_additional_hour_in_spring, inplace=True)

    # add hour in autumn if necesary
    if lastdate > timeshifts[1]:
        additional_hours_in_autumn = data.loc[
            timeshifts[1]
            + dt.timedelta(seconds=3600) : timeshifts[1]  # noqa: E203
            + dt.timedelta(seconds=60 * (60 + 59))
        ]
        data = pd.concat([data, additional_hours_in_autumn])
        data.sort_index(inplace=True)

    # delete hour at beginning
    data = data[data.index >= dt.datetime(year=year, month=1, day=1, hour=1)]

    # add hour at end
    last_hour = data[
        data.index >= dt.datetime(year=year, month=lastdate.month, day=lastdate.day, hour=23)  # type: ignore
    ]
    data = pd.concat([data, last_hour])  # type: ignore

    # make integer index again, paste new timestamp (UTC) and format
    data.index = pd.Index(list(range(len(data))))
    data["Time"] = pd.date_range(
        start=dt.datetime(year=year, month=1, day=1, hour=0),
        end=dt.datetime(year=year, month=lastdate.month, day=lastdate.day, hour=23, minute=59),  # type: ignore
        freq="min",
        tz="UTC",
    )
    data["Time"] = data["Time"].dt.strftime("%m/%d/%Y %H:%M")
    return data


def get_cache_file(
    component_key: str,
    parameter_class: Any,
    my_simulation_parameters: SimulationParameters,
    cache_dir_path: str = os.path.join(hisim_abs_path, "inputs", "cache"),
) -> Tuple[bool, str]:  # noqa
    """Gets a cache path for a given parameter set.

    This will generate a file path based on any dataclass_json.
    It works by turning the class into a json string, hashing the string and then using that as filename.
    The idea is to have a unique file path for every possible configuration.
    """
    json_str = parameter_class.to_json()
    if my_simulation_parameters is None:
        raise ValueError("Simulation parameters was none.")
    simulation_parameter_str = my_simulation_parameters.get_unique_key()
    json_str = json_str + simulation_parameter_str
    if len(json_str) < 5:
        raise ValueError("Empty json detected for caching. This is a bug.")
    json_str_encoded = json_str.encode("utf-8")
    # Johanna Ganglbauer: python told me "TypeError: openssl_sha256() takes at most 1 argument (2 given)",
    # I removed the second input argument "usedforsecurity=False" and it works - maybe I need to update the hashlib package?
    sha_key = hashlib.sha256(json_str_encoded).hexdigest()
    filename = component_key + "_" + sha_key + ".cache"

    cache_absolute_filepath = os.path.join(cache_dir_path, filename)
    if not os.path.isdir(cache_dir_path):
        os.mkdir(cache_dir_path)
    if os.path.isfile(cache_absolute_filepath):
        return True, cache_absolute_filepath
    return False, cache_absolute_filepath


def load_export_load_profile_generator(target):  # noqa
    """Returns the paths for the SQL exported files from the Load Profile Generator."""
    targetpath = os.path.join(HISIMPATH["LoadProfileGenerator_export_directory"], target)
    if os.path.exists(targetpath):
        lpg_export_path = {
            "electric_vehicle": [
                os.path.join(targetpath, "Results.HH1.sqlite"),
                os.path.join(targetpath, "Results.General.sqlite"),
            ]
        }
        return lpg_export_path
    raise ValueError("Target export from Load Profile Generator does not exist")


def measure_execution_time(my_function):  # noqa
    """Utility function that works as decorator for measuring execution time."""

    @wraps(my_function)
    def function_wrapper_for_measuring_execution_time(*args, **kwargs):
        """Inner function for the time measuring utility decorator."""
        start = timer()
        result = my_function(*args, **kwargs)
        end = timer()
        diff = end - start
        log.profile(
            "Executing " + my_function.__module__ + "." + my_function.__name__ + " took " + f"{diff:1.2f}" + " seconds"
        )
        return result

    return function_wrapper_for_measuring_execution_time


def measure_memory_leak(my_function):  # noqa
    """Utility function that works as decorator for measuring execution time."""

    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """Inner function for the time measuring utility decorator."""
        process = psutil.Process(os.getpid())
        rss_by_psutil_start = process.memory_info().rss / (1024 * 1024)
        result = my_function(*args, **kwargs)
        rss_by_psutil_end = process.memory_info().rss / (1024 * 1024)
        gc.collect()
        diff = rss_by_psutil_end - rss_by_psutil_start
        log.trace(
            "Executing " + my_function.__module__ + "." + my_function.__name__ + " leaked " + f"{diff:1.2f}" + " MB"
        )
        return result

    return function_wrapper_for_measuring_memory_leak


def measure_memory_leak_with_error(my_function):  # noqa
    """Utility function that works as decorator for measuring execution time."""

    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """Inner function for the time measuring utility decorator."""
        process = psutil.Process(os.getpid())
        rss_by_psutil_start = process.memory_info().rss / (1024 * 1024)
        result = my_function(*args, **kwargs)
        rss_by_psutil_end = process.memory_info().rss / (1024 * 1024)
        gc.collect()
        diff = rss_by_psutil_end - rss_by_psutil_start
        log.information(
            "Executing " + my_function.__module__ + "." + my_function.__name__ + " leaked " + f"{diff:1.2f}" + " MB"
        )
        if diff > 100:
            raise ValueError("Lost over 100MB of memory during the function call")
        return result

    return function_wrapper_for_measuring_memory_leak


def deprecated(message):
    """Decorator for marking a function as deprecated."""

    def deprecated_decorator(func):
        """Decorator."""

        def deprecated_func(*args, **kwargs):
            """Core function."""
            log.warning(f"{func.__name__} is a deprecated function. {message}")
            return func(*args, **kwargs)

        return deprecated_func

    return deprecated_decorator


def rsetattr(obj, attr, val):
    """Recursive setattr for multi level attributes like `obj.attribute.subattribute`."""
    pre, _, post = attr.rpartition(".")
    return setattr(rgetattr(obj, pre) if pre else obj, post, val)


def rgetattr(obj, attr, *args):
    """Recursive getattr for multi level attributes like `obj.attribute.subattribute`."""

    def _getattr(obj, attr):
        return getattr(obj, attr, *args)

    return freduce(_getattr, [obj] + attr.split("."))


def rhasattr(obj, attr):
    """Recursive hasattr for multi level attributes like `obj.attribute.subattribute`."""
    pre, _, post = attr.rpartition(".")
    return hasattr(rgetattr(obj, pre) if pre else obj, post)


def set_attributes_of_dataclass_from_dict(dataclass_, dict_, nested=None):
    """Set values in a Dataclass from a dictionary."""
    for key, value in dict_.items():
        if nested:
            path_list = nested + [key]
        else:
            path_list = [key]
        if isinstance(value, dict):
            set_attributes_of_dataclass_from_dict(dataclass_, value, path_list)
        else:
            attribute = ".".join(path_list)
            if rhasattr(dataclass_, attribute):
                rsetattr(dataclass_, attribute, value)
            else:
                raise AttributeError(
                    f"""Attribute `{attribute}` from JSON cannot be found
                    in `{dataclass_.__class__.__name__}`."""
                )


def get_environment_variable(key: str, default: Optional[str] = None) -> str:
    """Get environment variable. Raise error if variable not found."""
    value = os.getenv(key, default)
    if not value:
        raise ValueError(
            f"""Could not determine value of environment variable: {key}.
                         Make sure to set it in an `.env` file inside the HiSim root folder
                         or somewhere within your system environment."""
        )
    return value
