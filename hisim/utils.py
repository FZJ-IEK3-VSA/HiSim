#   Generic/Built-in
import os
import json
import pickle
import inspect
from enum import Enum, IntEnum
from typing import Any, Dict
import hashlib
import  json
from functools import wraps
from timeit import default_timer as timer
from hisim import log
__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

# Retrieves hisim directory absolute path
hisim_abs_path = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))) # type: ignore
hisim_inputs = os.path.join(hisim_abs_path, "inputs")
hisim_postprocessing_img = os.path.join(hisim_abs_path, "postprocessing", "report")

HISIMPATH : Dict[str,Any]      = {"results": os.path.join(hisim_abs_path, "results"),
                   "inputs" : os.path.join(hisim_abs_path, "inputs"),
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
                   "smart_devices": { "profile_data" : os.path.join( hisim_inputs, 
                                                                      "loadprofiles",
                                                                      "electrical-load_2-smart-appliances",
                                                                      "LPG_output",
                                                                      "FlexibilityEvents.HH1.json" ),
                                      "device_collection" : os.path.join( hisim_inputs, 
                                                                          "loadprofiles",
                                                                          "electrical-load_2-smart-appliances",
                                                                          "LPG_output",
                                                                          "FlexibilityEventsStatistics.HH1.json" ) } ,
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
                   "chp_system": os.path.join(hisim_inputs,"chp_system"),
                   "electric_vehicle": [ os.path.join("D:", os.sep, "Work", "CHR01", "Results.HH1.sqlite"),
                                         os.path.join("D:", os.sep, "Work", "CHR01", "Results.General.sqlite")],

                   "frank_data": os.path.join(hisim_inputs,
                                              "loadprofiles",
                                              "electrical-spaceheating-warmwater-photovoltaic_1-household",
                                              "data_raw",
                                              "VDI 4655"),

                   "report": os.path.join(hisim_abs_path,"results","report.pdf"),
                   "advanced_battery": {"parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "parameter", "PerModPAR.xlsx"),
                                        "reference_case": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "reference_case", "ref_case_data.npz"),
                                        "siemens_junelight": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "Siemens_Junelight.npy")},
                   "LoadProfileGenerator_export_directory": os.path.join(os.path.join("D:", os.sep, "Work")),
                   "bat_parameter": os.path.join(hisim_abs_path, "inputs", "advanced_battery", "Siemens_Junelight.npy")}



class PostProcessingOptions(IntEnum):
    Plot_Line = 1
    Plot_Carpet = 2
    Plot_Sankey = 3
    Plot_Day = 4
    Plot_Bar = 5
    Open_Directory = 6
    Export_To_CSV = 7
    Compute_KPI = 8

class Outputs:

    def __init__(self):
        self.number_of_outputs = 0

    def add(self):
        output_index = self.number_of_outputs
        self.number_of_outputs = self.number_of_outputs + 1
        return output_index

def open_cache():
    if os.path.isdir(HISIMPATH["cache_dir"]) is False:
        os.mkdir(HISIMPATH["cache_dir"])
    if os.path.isfile(HISIMPATH["cache_indices"]) is False:
        with open(HISIMPATH["cache_indices"], 'w') as f:
            json.dump({}, f)

    with open(HISIMPATH["cache_indices"]) as file:
        cache_index = json.load(file)

    entries_to_be_deleted = []
    for classname in cache_index:
        if os.path.isfile(os.path.join(HISIMPATH["cache_dir"], (cache_index[classname][0]["filepath"]))) is False:
            entries_to_be_deleted.append(classname)

    if len(entries_to_be_deleted) > 0:
        for entry in entries_to_be_deleted:
            del cache_index[entry]

        with open(os.path.join(HISIMPATH["cache_indices"]), "w") as file:
            json.dump(cache_index, file, indent=4)

    return cache_index

def load_smart_appliance(name):
    with open(HISIMPATH["smart_appliances"]) as f:
        data = json.load(f)
    return data[name]

def get_cache_file(component_key: str,  parameter_class:Any):
    json_str = parameter_class.to_json()
    if(len(json_str) < 5):
        raise Exception("Empty json detected for caching. This is a bug.")
    json_str_encoded = json_str.encode('utf-8') 
    sha_key = hashlib.sha256( json_str_encoded ).hexdigest()
    filename = component_key + "_" + sha_key + ".cache"
    cache_dir_path =  os.path.join(hisim_abs_path, "inputs", "cache")
    cache_absolute_filepath = os.path.join(hisim_abs_path, "inputs", "cache", filename)
    if not os.path.isdir(cache_dir_path):
        os.mkdir(cache_dir_path)
    if os.path.isfile(cache_absolute_filepath):
        return True, cache_absolute_filepath
    return False, cache_absolute_filepath

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
    else:
        raise Warning("Target export from Load Profile Generator does not exist")

def get_last_pickle():
    stored_results_list = os.listdir(HISIMPATH["results"])
    execution_dates = []
    for index, result_dir in enumerate(stored_results_list):
        temp = result_dir.split("_")
        execution_dates.append("{}_{}".format(temp[-2],temp[-1]))

    dir_index = execution_dates.index(max(execution_dates))
    latest_dir = stored_results_list[dir_index]
    latest_dir_path = os.path.join(HISIMPATH["results"], latest_dir)
    for file in os.listdir(latest_dir_path):
        if file.endswith(".pkl"):
            pickle_file = file
            break

    filepath = os.path.join(latest_dir_path, pickle_file)
    with open(filepath, 'rb') as input:
        extracted_pickle = pickle.load(input)
    return extracted_pickle, latest_dir_path, latest_dir

def del_file_type(dirname, filetype):
    dir_path = os.path.join(HISIMPATH["results"], dirname)
    for file in os.listdir(dir_path):
        if file.endswith(filetype):
            os.remove(os.path.join(dir_path,file))

def open_pickle(dirname):
    dir_path = os.path.join(HISIMPATH["results"],dirname)
    for file in os.listdir(dir_path):
        if file.endswith(".pkl"):
            pickle_file = file
            break

    filepath = os.path.join(dir_path, pickle_file)
    with open(filepath, 'rb') as input:
        extracted_pickle = pickle.load(input)
    return extracted_pickle, dir_path

def measure_execution_time( my_function ):
    @wraps(my_function)
    def wrapTheFunction(*args, **kwargs):
        start = timer()
        result = my_function(*args, **kwargs)
        end = timer()
        diff = end - start
        log.profile("Executing " + my_function.__module__  + "." + my_function.__name__ + " took " + "%1.2f" % diff + " seconds")
        return result
    return wrapTheFunction


