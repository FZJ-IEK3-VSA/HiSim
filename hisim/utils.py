""" Contains various utility functions and utility classes. """
# clean
import gc
import os
import inspect
import hashlib
import json

from timeit import default_timer as timer
from typing import Any, Dict, Tuple
from functools import wraps

import psutil

from hisim.simulationparameters import SimulationParameters
from hisim import log

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


def get_input_directory() -> str:
    """ Gets the absolute path to the inputs directory. """
    return os.path.join(hisim_abs_path, "inputs")


# Retrieves hisim directory absolute path
hisim_abs_path = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
hisim_inputs = os.path.join(hisim_abs_path, "inputs")
hisim_results = os.path.join(hisim_abs_path, "results")
hisim_postprocessing_img = os.path.join(hisim_abs_path, "postprocessing", "report")  # noqa

HISIMPATH: Dict[str, Any] = {"results": hisim_results,
                             "inputs": hisim_inputs,
                             "cache_dir": os.path.join(hisim_abs_path, "inputs", "cache"),
                             "cache_indices": os.path.join(hisim_abs_path, "inputs", "cache", "cache_indices.json"),
                             "cfg": os.path.join(hisim_abs_path, "inputs", "cfg.json"),
                             "utsp_results": os.path.join(hisim_results, "Results"),
                             "utsp_example_results": os.path.join(hisim_inputs, "LPGResults_for_tests", "Results"),
                             "utsp_reports": os.path.join(hisim_results, "Reports"),
                             "utsp_example_reports": os.path.join(hisim_inputs, "LPGResults_for_tests", "Reports"),
                             "housing": os.path.join(hisim_inputs,
                                                     "housing",
                                                     "data_processed",
                                                     "episcope-tabula.csv"),
                             "housing_reference_temperatures": os.path.join(hisim_inputs, "housing", "data_processed",
                             "heating_reference_temperature_per_location.csv"),
                             "fuel_costs": os.path.join(hisim_inputs,
                                                     "fuelcosts",
                                                     "fuel_costs.csv"),
                             "occupancy": {"CH01": {"number_of_residents": [os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.High.HH1.json"),
                                                                            os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.Low.HH1.json")],
                                                    "electricity_consumption": os.path.join(hisim_inputs,
                                                                                            "loadprofiles",
                                                                                            "electrical-warmwater-presence-load_1-family",
                                                                                            "data_processed",
                                                                                            "SumProfiles.HH1.Electricity.csv"),
                                                    "water_consumption": os.path.join(hisim_inputs,
                                                                                      "loadprofiles",
                                                                                      "electrical-warmwater-presence-load_1-family",
                                                                                      "data_processed",
                                                                                      "SumProfiles.HH1.Warm Water.csv")},
                                           "CHR01 Couple both at Work": {"number_of_residents": [os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.High.HH1.json"),
                                                                            os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.Low.HH1.json")],
                                                    "electricity_consumption": os.path.join(hisim_inputs,
                                                                                            "loadprofiles",
                                                                                            "electrical-warmwater-presence-load_1-family",
                                                                                            "data_processed",
                                                                                            "SumProfiles.HH1.Electricity.csv"),
                                                    "water_consumption": os.path.join(hisim_inputs,
                                                                                      "loadprofiles",
                                                                                      "electrical-warmwater-presence-load_1-family",
                                                                                      "data_processed",
                                                                                      "SumProfiles.HH1.Warm Water.csv")},
                                            "AVG": {"number_of_residents": [os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.High.HH1.json"),
                                                                            os.path.join(hisim_inputs,
                                                                                         "loadprofiles",
                                                                                         "electrical-warmwater-presence-load_1-family",
                                                                                         "data_processed",
                                                                                         "BodilyActivityLevel.Low.HH1.json")],
                                                    "electricity_consumption": os.path.join(hisim_inputs,
                                                                                            "loadprofiles",
                                                                                            "electrical-warmwater-presence-load_1-family",
                                                                                            "data_processed",
                                                                                            "why_reference_data.csv"),
                                                    "water_consumption": os.path.join(hisim_inputs,
                                                                                      "loadprofiles",
                                                                                      "electrical-warmwater-presence-load_1-family",
                                                                                      "data_processed",
                                                                                      "SumProfiles.HH1.Warm Water.csv")}},
                             "photovoltaic": {"modules": os.path.join(hisim_inputs,
                                                                      "photovoltaic",
                                                                      "data_processed",
                                                                      "sandia_modules.csv"),
                                              "inverters": os.path.join(hisim_inputs,
                                                                        "photovoltaic",
                                                                        "data_processed",
                                                                        "sandia_inverters.csv")},
                             "chp_system": os.path.join(hisim_inputs,
                                                        "chp_system"),

                             "smart_appliances": os.path.join(hisim_inputs,
                                                              "smart_devices",
                                                              "data_processed",
                                                              "smart_devices.json"),
                             "frank_data": os.path.join(hisim_inputs,
                                                        "loadprofiles",
                                                        "electrical-spaceheating-warmwater-photovoltaic_1-household",
                                                        "data_raw",
                                                        "VDI 4655"),

                             "report": os.path.join(hisim_abs_path, "results", "report.pdf"),
                             "advanced_battery": {
                                 "parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "parameter",
                                                           "PerModPAR.xlsx"),
                                 "reference_case": os.path.join(hisim_abs_path, "inputs", "advanced_battery",
                                                                "reference_case", "ref_case_data.npz"),
                                 "siemens_junelight": os.path.join(hisim_abs_path, "inputs", "advanced_battery",
                                                                   "Siemens_Junelight.npy")},
                             "LoadProfileGenerator_export_directory": os.path.join(os.path.join("D:", os.sep, "Work")),
                             "bat_parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery",
                                                           "Siemens_Junelight.npy"),
                             "modular_household": os.path.join(hisim_abs_path, "modular_household")}


def load_smart_appliance(name):  # noqa
    """ Loads file for a single smart appliance by name. """
    with open(HISIMPATH["smart_appliances"], encoding="utf-8") as filestream:
        data = json.load(filestream)
    return data[name]


def get_cache_file(component_key: str, parameter_class: Any, my_simulation_parameters: SimulationParameters) -> Tuple[bool, str]:  # noqa
    """ Gets a cache path for a given parameter set.

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
    json_str_encoded = json_str.encode('utf-8')
    # Johanna Ganglbauer: python told me "TypeError: openssl_sha256() takes at most 1 argument (2 given)",
    # I removed the second input argument "usedforsecurity=False" and it works - maybe I need to update the hashlib package?
    sha_key = hashlib.sha256(json_str_encoded).hexdigest()
    filename = component_key + "_" + sha_key + ".cache"
    cache_dir_path = os.path.join(hisim_abs_path, "inputs", "cache")
    cache_absolute_filepath = os.path.join(hisim_abs_path, "inputs", "cache", filename)
    if not os.path.isdir(cache_dir_path):
        os.mkdir(cache_dir_path)
    if os.path.isfile(cache_absolute_filepath):
        return True, cache_absolute_filepath
    return False, cache_absolute_filepath


def load_export_load_profile_generator(target):  # noqa
    """ Returns the paths for the SQL exported files from the Load Profile Generator. """
    targetpath = os.path.join(HISIMPATH["LoadProfileGenerator_export_directory"], target)
    if os.path.exists(targetpath):
        lpg_export_path = {"electric_vehicle": [os.path.join(targetpath, "Results.HH1.sqlite"), os.path.join(targetpath, "Results.General.sqlite")]}
        return lpg_export_path
    raise ValueError("Target export from Load Profile Generator does not exist")


def measure_execution_time(my_function):  # noqa
    """ Utility function that works as decorator for measuring execution time. """
    @wraps(my_function)
    def function_wrapper_for_measuring_execution_time(*args, **kwargs):
        """ Inner function for the time measuring utility decorator. """
        start = timer()
        result = my_function(*args, **kwargs)
        end = timer()
        diff = end - start
        log.profile("Executing " + my_function.__module__ + "." + my_function.__name__ + " took " + f"{diff:1.2f}" + " seconds")
        return result

    return function_wrapper_for_measuring_execution_time


def measure_memory_leak(my_function):  # noqa
    """ Utility function that works as decorator for measuring execution time. """
    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """ Inner function for the time measuring utility decorator. """
        process = psutil.Process(os.getpid())
        rss_by_psutil_start = process.memory_info().rss / (1024 * 1024)
        result = my_function(*args, **kwargs)
        rss_by_psutil_end = process.memory_info().rss / (1024 * 1024)
        gc.collect()
        diff = rss_by_psutil_end - rss_by_psutil_start
        log.trace("Executing " + my_function.__module__ + "." + my_function.__name__ + " leaked " + f"{diff:1.2f}" + " MB")
        return result

    return function_wrapper_for_measuring_memory_leak


def measure_memory_leak_with_error(my_function):  # noqa
    """ Utility function that works as decorator for measuring execution time. """
    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """ Inner function for the time measuring utility decorator. """
        process = psutil.Process(os.getpid())
        rss_by_psutil_start = process.memory_info().rss / (1024 * 1024)
        result = my_function(*args, **kwargs)
        rss_by_psutil_end = process.memory_info().rss / (1024 * 1024)
        gc.collect()
        diff = rss_by_psutil_end - rss_by_psutil_start
        log.information(
            "Executing " + my_function.__module__ + "." + my_function.__name__ + " leaked " + f"{diff:1.2f}" + " MB")
        if diff > 100:
            raise ValueError("Lost over 100MB of memory during the function call")
        return result

    return function_wrapper_for_measuring_memory_leak


def deprecated(message):
    """ Decorator for marking a function as deprecated. """
    def deprecated_decorator(func):
        """ Decorator. """
        def deprecated_func(*args, **kwargs):
            """ Core function. """
            log.warning(f"{func.__name__} is a deprecated function. {message}")
            return func(*args, **kwargs)
        return deprecated_func
    return deprecated_decorator
