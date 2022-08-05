"""
Contains various utility functions and utility classes
"""
import os
import inspect
import hashlib
import json

from enum import IntEnum
from typing import Any, Dict
from functools import wraps
from timeit import default_timer as timer

from hisim import log

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"

# Retrieves hisim directory absolute path
hisim_abs_path = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
hisim_inputs = os.path.join(hisim_abs_path, "inputs")
hisim_postprocessing_img = os.path.join(hisim_abs_path, "postprocessing", "report")

HISIMPATH: Dict[str, Any] = {"results": os.path.join(hisim_abs_path, "results"),
                             "inputs": os.path.join(hisim_abs_path, "inputs"),
                             "cache_dir": os.path.join(hisim_abs_path, "inputs", "cache"),
                             "cache_indices": os.path.join(hisim_abs_path, "inputs", "cache", "cache_indices.json"),
                             "cfg": os.path.join(hisim_abs_path, "inputs", "cfg.json"),

                             "weather": {"Aachen": os.path.join(hisim_inputs,
                                                                "weather",
                                                                "test-reference-years_1995-2012_1-location",
                                                                "data_processed",
                                                                "aachen_center"),
                                         "01Bremerhaven": os.path.join(hisim_inputs,
                                                                       "weather",
                                                                       "test-reference-years_2015-2045_15-locations",
                                                                       "data_processed",
                                                                       "weather_region_01"),
                                         "02Rostock": os.path.join(hisim_inputs,
                                                                   "weather",
                                                                   "test-reference-years_2015-2045_15-locations",
                                                                   "data_processed",
                                                                   "weather_region_02"),
                                         "03Hamburg": os.path.join(hisim_inputs,
                                                                   "weather",
                                                                   "test-reference-years_2015-2045_15-locations",
                                                                   "data_processed",
                                                                   "weather_region_03"),
                                         "04Potsdam": os.path.join(hisim_inputs,
                                                                   "weather",
                                                                   "test-reference-years_2015-2045_15-locations",
                                                                   "data_processed",
                                                                   "weather_region_04"),
                                         "05Essen": os.path.join(hisim_inputs,
                                                                 "weather",
                                                                 "test-reference-years_2015-2045_15-locations",
                                                                 "data_processed",
                                                                 "weather_region_05"),
                                         "06Bad Marienburg": os.path.join(hisim_inputs,
                                                                          "weather",
                                                                          "test-reference-years_2015-2045_15-locations",
                                                                          "data_processed",
                                                                          "weather_region_06"),
                                         "07Kassel": os.path.join(hisim_inputs,
                                                                  "weather",
                                                                  "test-reference-years_2015-2045_15-locations",
                                                                  "data_processed",
                                                                  "weather_region_07"),
                                         "08Braunlage": os.path.join(hisim_inputs,
                                                                     "weather",
                                                                     "test-reference-years_2015-2045_15-locations",
                                                                     "data_processed",
                                                                     "weather_region_08"),
                                         "09Chemnitz": os.path.join(hisim_inputs,
                                                                    "weather",
                                                                    "test-reference-years_2015-2045_15-locations",
                                                                    "data_processed",
                                                                    "weather_region_09"),
                                         "10Hof": os.path.join(hisim_inputs,
                                                               "weather",
                                                               "test-reference-years_2015-2045_15-locations",
                                                               "data_processed",
                                                               "weather_region_10"),
                                         "11Fichtelberg": os.path.join(hisim_inputs,
                                                                       "weather",
                                                                       "test-reference-years_2015-2045_15-locations",
                                                                       "data_processed",
                                                                       "weather_region_11"),
                                         "12Mannheim": os.path.join(hisim_inputs,
                                                                    "weather",
                                                                    "test-reference-years_2015-2045_15-locations",
                                                                    "data_processed",
                                                                    "weather_region_12"),
                                         "13Muehldorf": os.path.join(hisim_inputs,
                                                                     "weather",
                                                                     "test-reference-years_2015-2045_15-locations",
                                                                     "data_processed",
                                                                     "weather_region_13"),
                                         "14Stoetten": os.path.join(hisim_inputs,
                                                                    "weather",
                                                                    "test-reference-years_2015-2045_15-locations",
                                                                    "data_processed",
                                                                    "weather_region_14"),
                                         "15Garmisch Partenkirchen": os.path.join(hisim_inputs,
                                                                                  "weather",
                                                                                  "test-reference-years_2015-2045_15-locations",
                                                                                  "data_processed",
                                                                                  "weather_region_15"),
                                         },
                             "housing": os.path.join(hisim_inputs,
                                                     "housing",
                                                     "data_processed",
                                                     "episcope-tabula.csv"),
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
                                                                                      "SumProfiles.HH1.Warm Water.csv")}},
                             "smart_devices": {"profile_data": os.path.join(hisim_inputs,
                                                                            "loadprofiles",
                                                                            "electrical-load_2-smart-appliances",
                                                                            "LPG_output",
                                                                            "FlexibilityEvents.HH1.json"),
                                               "device_collection": os.path.join(hisim_inputs,
                                                                                 "loadprofiles",
                                                                                 "electrical-load_2-smart-appliances",
                                                                                 "LPG_output",
                                                                                 "FlexibilityEventsStatistics.HH1.json")},
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
                             "electric_vehicle": [os.path.join("D:", os.sep, "Work", "CHR01", "Results.HH1.sqlite"),
                                                  os.path.join("D:", os.sep, "Work", "CHR01",
                                                               "Results.General.sqlite")],

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
                                                           "Siemens_Junelight.npy")}


class PostProcessingOptions(IntEnum):
    """
    Enum class for enabling / disabling parts of the post processing
    """
    PlotLine = 1
    PlotCarpet = 2
    PlotSankey = 3
    PlotDay = 4
    PlotBar = 5
    OpenDirectory = 6
    ExportToCSV = 7
    ComputeKPI = 8


# class Outputs:
#     def __init__(self):
#         self.number_of_outputs = 0
#
#     def add(self):
#         output_index = self.number_of_outputs
#         self.number_of_outputs = self.number_of_outputs + 1
#         return output_index

#
# def open_cache():
#     if os.path.isdir(HISIMPATH["cache_dir"]) is False:
#         os.mkdir(HISIMPATH["cache_dir"])
#     if os.path.isfile(HISIMPATH["cache_indices"]) is False:
#         with open(HISIMPATH["cache_indices"], 'w', encoding="utf-8") as filestream:
#             json.dump({}, filestream)
#
#     with open(HISIMPATH["cache_indices"], "r", encoding="utf-8") as filestream:
#         cache_index = json.load(filestream)
#
#     entries_to_be_deleted = []
#     for classname in cache_index:
#         if os.path.isfile(os.path.join(HISIMPATH["cache_dir"], (cache_index[classname][0]["filepath"]))) is False:
#             entries_to_be_deleted.append(classname)
#
#     if len(entries_to_be_deleted) > 0:
#         for entry in entries_to_be_deleted:
#             del cache_index[entry]
#
#         with open(os.path.join(HISIMPATH["cache_indices"]), "w", encoding="utf-8") as filestream:
#             json.dump(cache_index, filestream, indent=4)
#
#     return cache_index


def load_smart_appliance(name):
    """
    Loads file for a single smart appliance by name

    """
    with open(HISIMPATH["smart_appliances"], encoding="utf-8") as filestream:
        data = json.load(filestream)
    return data[name]


def get_cache_file(component_key: str, parameter_class: Any, my_simulation_parameters):
    """

    This will genererate a file path based on any dataclass_json
    It works by turning the class into a json string, hashing the string and then using that as filename
    The idea is to have a unique file path for every possible configuration
    """
    json_str = parameter_class.to_json()
    if(my_simulation_parameters is None):
        raise ValueError("Simulation parameters was none.")
    simulation_parameter_str = my_simulation_parameters.to_json()
    json_str = json_str + simulation_parameter_str
    if len(json_str) < 5:
        raise Exception("Empty json detected for caching. This is a bug.")
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


#
# def get_cache(classname, parameters):
#     filename = None
#     cache_absolute_filepath = None
#
#     all_caches = open_cache()
#
#     if classname in all_caches:
#         for in_cache in all_caches[classname]:
#             if all(elem in in_cache["parameters"] for elem in parameters):
#                 filename = in_cache["filepath"]
#                 break
#
#     if filename is not None:
#         cache_absolute_filepath = os.path.join(hisim_abs_path, "inputs", "cache", filename)
#
#     return cache_absolute_filepath

# def save_cache(classname, parameters, database=None):
#     """
#     Saves calculated data from component class to cache directory
#     and registers the library indices in cache_indices.json
#
#     :param classname: Name of Component Class
#     :param parameters: Parameters that defined the Component Class
#     :param database: Data from the Component Class
#     :return:
#     """
#
#
#     def get_next_file():
#         list_cache_files = [x.name for x in os.scandir(HISIMPATH["cache_dir"])]
#         counter = 0
#
#         for cache_file in list_cache_files:
#            if name_profile in cache_file:
#                cache_file_number = int(cache_file[3:6])
#                if counter < cache_file_number:
#                     counter = cache_file_number
#
#         return str(counter + 1)
#
#     name_profile = str.lower(classname)
#
#     #with open(os.path.join(HISIMPATH["cache_dir"], "cache_indices.json")) as f:
#     #    cache_index = json.load(f)
#     cache_index = open_cache()
#
#     if classname in cache_index:
#         n_files = len(cache_index[classname])
#         i_next_file = str(n_files + 1)
#     else:
#         cache_index[classname] = {}
#         i_next_file = str(1)
#
#     csv_file = "{}_{}.csv".format(name_profile, i_next_file.zfill(3))
#     next_dict = {"parameters": parameters, "filepath": csv_file }
#
#     if cache_index[classname]:
#         cache_index[classname].append(next_dict)
#     else:
#         cache_index[classname] = [next_dict]
#
#     with open(os.path.join(HISIMPATH["cache_dir"], "cache_indices.json"), "w") as f:
#          json.dump(cache_index, f, indent=4)
#
#     if database is None:
#         return os.path.join(HISIMPATH["cache_dir"], csv_file)
#     else:
#         database.to_csv(os.path.join(HISIMPATH["cache_dir"], csv_file), sep=",", decimal=".", index=False, encoding = "cp1252")

def load_export_load_profile_generator(target):
    """
    Returns the paths for the SQL exported files from
    the Load Profile Generator
    """
    targetpath = os.path.join(HISIMPATH["LoadProfileGenerator_export_directory"], target)
    if os.path.exists(targetpath):
        EXPORTPATH = {"electric_vehicle": [os.path.join(targetpath, "Results.HH1.sqlite"),
                                           os.path.join(targetpath, "Results.General.sqlite")]}
        return EXPORTPATH
    raise ValueError("Target export from Load Profile Generator does not exist")


#
# def get_ev_data(target):
#     """
#     Testing function to import EV Data
#     To be soon obsolete
#     """
#     cache_filepath = get_cache("Vehicle", ["CH01"])
#     def open_sql(path, table_name):
#         sql_file = sqlite3.connect(path)
#         return pd.read_sql("SELECT * FROM {};".format(table_name), sql_file)
#     def open_ev_json(filepath):
#         with open(filepath) as f:
#             data = json.load(f)
#         return data["Values"]
#
#     FILEPATH = load_export_load_profile_generator(target=target)
#     if FILEPATH is None:
#         FILEPATH = HISIMPATH
#
#     ev_files = dict()
#     filepaths = open_sql(FILEPATH["electric_vehicle"][1], "ResultFileEntries")
#     list_columns = []
#     list_values = []
#     for index, row in filepaths.iterrows():
#         json_info = json.loads(row["Json"])
#         if "Charging" in json_info["FileName"] and "png" not in json_info["FileName"]:
#             filepath = os.path.normpath(json_info["FullFileName"])
#             filepath_list = filepath.split(os.sep)
#             ev_files[filepath_list[-1].split(".")[0]] = json_info["FullFileName"]
#             list_columns.append(filepath_list[-1].split(".")[0])
#             list_values.append(open_ev_json(json_info["FullFileName"]))
#     list_values = list(map(list, zip(*list_values)))
#     ev_pd = pd.DataFrame(list_values, columns=list_columns)
#
#     # Trying to pass to pandas
#     activations = open_sql(FILEPATH["electric_vehicle"][0], "DeviceActivationEntries")
#     number_of_car_activations = 0
#     device_activation_entries = []
#     for index, column in activations.iterrows():
#         if "Car" in column['AffordanceName']:
#             active = json.loads(column['Json'])
#             device_activation_entries.append(active)
#             number_of_car_activations += 1
#
#
#     transportation_devices = open_sql(FILEPATH["electric_vehicle"][0], "TransportationDevices")
#     for index, vehicle in transportation_devices.iterrows():
#         if "Charging" in vehicle["Name"]:
#             vehicle_info = json.loads(vehicle['Json'])
#             battery_stored_energy_meters = vehicle_info["FullRangeInMeters"]
#             convert_factor = vehicle_info["EnergyToDistanceFactor"]
#             battery_stored_energy_wh = battery_stored_energy_meters * convert_factor
#
#     discharge = []
#     load = []
#     for index, row in ev_pd.iterrows():
#         load.append(row["Soc"] * battery_stored_energy_wh)
#         if index == 0:
#             discharge.append(0)
#         else:
#             diff = load[-1] - load[-2]
#             if diff < 0:
#                 discharge.append(diff)
#             else:
#                 discharge.append(0)
#
#     ev_pd["Load"] = load
#     ev_pd["Discharge"] = discharge
#     data = [ev_pd["CarLocation"].tolist()]
#     data.append(discharge)
#     data = list(map(list, zip(*data)))
#     data_parameters = ["CarLocation", "Discharging"]
#     database = pd.DataFrame(data, columns=data_parameters)
#
#     save_cache("Vehicle", ["CH01"], database)
#
#     return ev_pd, device_activation_entries
#
# def get_last_pickle():
#     stored_results_list = os.listdir(HISIMPATH["results"])
#     execution_dates = []
#     for index, result_dir in enumerate(stored_results_list):
#         temp = result_dir.split("_")
#         execution_dates.append(f"{temp[-2]}_{temp[-1]}")
#
#     dir_index = execution_dates.index(max(execution_dates))
#     latest_dir = stored_results_list[dir_index]
#     latest_dir_path = os.path.join(HISIMPATH["results"], latest_dir)
#     for file in os.listdir(latest_dir_path):
#         if file.endswith(".pkl"):
#             pickle_file = file
#             break
#
#     filepath = os.path.join(latest_dir_path, pickle_file)
#     with open(filepath, 'rb') as input_file:
#         extracted_pickle = pickle.load(input_file)
#     return extracted_pickle, latest_dir_path, latest_dir


# def del_file_type(dirname, filetype):
#     dir_path = os.path.join(HISIMPATH["results"], dirname)
#     for file in os.listdir(dir_path):
#         if file.endswith(filetype):
#             os.remove(os.path.join(dir_path, file))

#
# def open_pickle(dirname):
#     """
#     opens a pickled result file
#
#     """
#     dir_path = os.path.join(HISIMPATH["results"], dirname)
#     for file in os.listdir(dir_path):
#         if file.endswith(".pkl"):
#             pickle_file = file
#             break
#
#     filepath = os.path.join(dir_path, pickle_file)
#     with open(filepath, 'rb') as input:
#         extracted_pickle = pickle.load(input)
#     return extracted_pickle, dir_path


def measure_execution_time(my_function):
    """
    Utility function that works as decorator for measuring execution time
    """
    @wraps(my_function)
    def function_wrapper_for_measuring_execution_time(*args, **kwargs):
        """
        Inner function for the time measuring utility decorator
        """
        start = timer()
        result = my_function(*args, **kwargs)
        end = timer()
        diff = end - start
        log.profile(
            "Executing " + my_function.__module__ + "." + my_function.__name__ + " took " + f"{diff:1.2f}" + " seconds")
        return result

    return function_wrapper_for_measuring_execution_time
