""" Contains various utility functions and utility classes. """

# clean
import datetime as dt
import gc
import inspect
import itertools
import json
import os
import dataclasses
from dataclasses import dataclass
from functools import lru_cache
from functools import reduce as freduce
from functools import wraps
from timeit import default_timer as timer
from typing import Any, Dict, List, Optional, Tuple
import copy

import pandas as pd
import psutil
import pytz

from hisim import log
from hisim.caching import CacheEntryMetadata
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt, Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021-2022, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


def get_input_directory() -> str:
    """Gets the absolute path to the inputs directory.

    Returns:
        str: absolute path to the ``inputs`` subdirectory.
    """
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
    "fuel_costs": os.path.join(hisim_inputs, "costs_and_emissions", "why_project", "emission_factors_and_costs_fuels.csv"),
    "component_costs": os.path.join(hisim_inputs, "costs_and_emissions", "why_project", "emission_factors_and_costs_devices.csv"),
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
    "price_signal": {
        "PricePurchase": os.path.join(hisim_inputs, "price_signal", "PricePurchase.csv"),
        "FeedInTarrif": os.path.join(hisim_inputs, "price_signal", "FeedInTarrif.csv"),
    },
}


@lru_cache(maxsize=1)
def _load_smart_appliances_file() -> Dict[str, Any]:
    """Read and parse the smart-appliances JSON database once per process.

    The smart-devices file (``HISIMPATH["smart_appliances"]``) is static for the
    lifetime of a process, so the parsed dict is memoized to avoid repeated file
    I/O and JSON parsing across the many component initializations in a
    parametric study. The cache is never invalidated: if the underlying file
    changes on disk during a process, callers must explicitly call
    ``cache_clear()`` to pick up the new contents.

    Returns:
        The full JSON-decoded smart-appliances database keyed by appliance name.

    Raises:
        FileNotFoundError: if the smart-appliances file is missing.
        json.JSONDecodeError: if the file is not valid JSON.
        ValueError: if the decoded JSON is not a JSON object (dict) keyed by
            appliance name.
    """
    with open(HISIMPATH["smart_appliances"], encoding="utf-8") as filestream:
        data = json.load(filestream)
    # Validate the structure immediately so a malformed file fails loudly at the
    # source instead of surfacing as a confusing TypeError on the first lookup.
    # Caching an unvalidated structure would also hide the error behind the cache.
    if not isinstance(data, dict):
        raise ValueError(
            "Smart-appliances database at "
            f"{HISIMPATH['smart_appliances']} must be a JSON object keyed by "
            f"appliance name, got {type(data).__name__} instead."
        )
    return data


def load_smart_appliance(name: str) -> Any:  # noqa
    """Load a single smart appliance entry by name.

    The smart-devices database is parsed once per process (see
    :func:`_load_smart_appliances_file`); only the dict lookup runs per call.
    A deep copy of the looked-up entry is returned so every caller receives an
    independent object and cannot mutate the shared cache — preserving
    reproducible results across the repeated component initializations of a
    parametric study. The file read and full-file JSON parse (the expensive
    part) still happen only once; deep-copying a single appliance entry is
    comparatively cheap.

    Args:
        name: appliance entry name to look up in the smart-devices JSON.

    Returns:
        A deep copy of the JSON-decoded data for the requested appliance.

    Raises:
        KeyError: if ``name`` is not a key in the file.
    """
    return copy.deepcopy(_load_smart_appliances_file()[name])


def convert_lpg_timestep_to_utc(data: List[int], year: int, seconds_per_timestep: int) -> List[int]:
    """Transform LPG timesteps (list of integers) from local time to UTC.

    Args:
        data: list of integer timestep indices in Europe/Berlin local time.
        year: simulation year for DST lookup.
        seconds_per_timestep: seconds per index.

    Returns:
        List[int]: indices shifted to UTC.
    """
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
    return data_utc


def convert_lpg_data_to_utc(data: pd.DataFrame, year: int) -> pd.DataFrame:
    """Transform LPG data from local time (not having explicit time shifts) to UTC.

    Args:
        data: DataFrame with a ``Time`` column of local-time strings.
        year: simulation year for DST lookup.

    Returns:
        pd.DataFrame: data with reformatted UTC ``Time`` column.

    Note:
        The input DataFrame is modified in place; the returned object is the
        same instance.
    """
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
    cache_dir_path: Optional[str] = None
) -> Tuple[bool, str]:  # noqa
    """Gets a cache path for a given parameter set.

    This will generate a file path based on any dataclass_json.
    It works by turning the class into a json string, hashing the string and then using that as filename.
    The idea is to have a unique file path for every possible configuration.

    The ``exists`` flag is advisory and nothing more. It is a plain ``os.path.isfile`` check, so two
    processes that miss the same key concurrently will both compute the entry and both write it; that
    wastes work but is harmless, because the contents are a pure function of the key. What is *not*
    harmless is writing the entry straight to the returned path, which lets a concurrent reader see it
    half written -- so every writer must land its entry through
    :func:`hisim.caching.atomic_cache_write`. This function becomes a thin delegating wrapper over
    ``hisim/caching/`` in a later phase of ``roadmap/cache_service_spec.md`` §4.

    An existing entry only counts as a hit if its companion metadata is present and hashes to the
    name the entry is filed under. One that fails that check is deleted here and reported as a miss:
    either it was written before the metadata scheme existed, or its contents came from something
    other than what its key describes, and there is no way to tell those apart from the file alone.
    That is also what removes the need for a one-off purge whenever the key scheme changes.

    Args:
        component_key: filename prefix for the component type.
        parameter_class: dataclass with a ``to_json`` method; the building of its
            ``component_id`` is nulled before hashing.
        my_simulation_parameters: provides the cache directory and a unique key appended before hashing.
        cache_dir_path: optional override; defaults to ``my_simulation_parameters.cache_dir_path``.

    Returns:
        Tuple[bool, str]: ``(exists, absolute_path)``. ``exists`` is advisory, see above.

    Raises:
        ValueError: if ``my_simulation_parameters`` is None or the JSON string is too short.
    """
    json_str = build_cache_key_string(parameter_class, my_simulation_parameters)
    if cache_dir_path is None:
        cache_dir_path = my_simulation_parameters.cache_dir_path
    sha_key = CacheEntryMetadata.hash_of(json_str)
    filename = f"{component_key}_{sha_key}.cache"

    cache_absolute_filepath = os.path.join(cache_dir_path, filename)
    if not os.path.isdir(cache_dir_path):
        os.mkdir(cache_dir_path)
    if os.path.isfile(cache_absolute_filepath):
        if CacheEntryMetadata.describes(cache_absolute_filepath):
            return True, cache_absolute_filepath
        # An entry whose metadata is missing or disagrees with its own filename cannot be shown to
        # belong to this key: either it predates the metadata scheme, or something other than what
        # the key describes produced it. Deleting it turns both cases into an ordinary miss, which
        # is what makes the migration self-executing and a poisoning self-repairing.
        log.warning(
            f"Discarding the cache entry {cache_absolute_filepath}: its metadata is missing or does "
            f"not hash to the name it is filed under, so its contents cannot be shown to belong to "
            f"this key. It will be recomputed."
        )
        CacheEntryMetadata.discard(cache_absolute_filepath)
    return False, cache_absolute_filepath


def build_cache_key_string(parameter_class: Any, my_simulation_parameters: SimulationParameters) -> str:
    """Builds the raw string a cache entry's name is hashed from, and its metadata records verbatim.

    The string is shared by three callers that must agree exactly: the lookup that turns it into a
    filename, the writer that stores it beside the entry, and the validation that recomputes the hash
    from the stored copy. Splitting it out of ``get_cache_file`` is what lets a writer record the
    inputs that actually produced its bytes rather than re-deriving what was asked for.

    The component's building is removed from its identity first. The cached data depends on what the
    component is and how it is parameterized, not on which building it happens to sit in, and leaving
    the building in would file the same computation under a different name for every house.

    Args:
        parameter_class: the configuration dataclass, which must provide ``to_json``.
        my_simulation_parameters: contributes start, end, resolution, year, timesteps and country.

    Returns:
        str: the configuration JSON followed by the simulation parameters' unique key.

    Raises:
        ValueError: if ``my_simulation_parameters`` is None, or the resulting string is too short to
            be a real configuration, which would mean the caller passed something empty.
    """
    if my_simulation_parameters is None:
        raise ValueError("Simulation parameters was none.")
    parameter_class_copy = copy.deepcopy(parameter_class)
    component_id = getattr(parameter_class_copy, "component_id", None)
    if component_id is not None:
        # The identity is frozen, hence the replacement rather than an assignment.
        setattr(parameter_class_copy, "component_id", dataclasses.replace(component_id, building=None))
    json_str = parameter_class_copy.to_json() + my_simulation_parameters.get_unique_key()
    if len(json_str) < 5:
        raise ValueError("Empty json detected for caching. This is a bug.")
    return str(json_str)


def load_export_load_profile_generator(target: str) -> Dict[str, List[str]]:  # noqa
    """Returns the paths for the SQL exported files from the Load Profile Generator.

    Args:
        target: subdirectory name within the LPG export directory.

    Returns:
        Dict[str, List[str]]: mapping of output types to SQL file paths.

    Raises:
        ValueError: if the target directory does not exist.
    """
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
    """Utility function that works as decorator for measuring execution time.

    Args:
        my_function: function to wrap.

    Returns:
        wrapped callable.
    """

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
    """Decorator that measures RSS memory delta before/after a call.

    Logs the difference in MB via ``log.trace``. Intended for profiling
    memory growth during simulation steps.

    Args:
        my_function: function to wrap.

    Returns:
        wrapped callable.
    """

    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """Inner function for the memory leak measuring utility decorator."""
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
    """Decorator that measures RSS memory delta and raises if it exceeds 100 MB.

    Logs the difference in MB via ``log.information`` and raises
    ``ValueError`` when the leaked memory surpasses 100 MB.

    Args:
        my_function: function to wrap.

    Returns:
        wrapped callable.

    Raises:
        ValueError: if the measured RSS memory delta exceeds 100 MB.
    """

    @wraps(my_function)
    def function_wrapper_for_measuring_memory_leak(*args, **kwargs):
        """Inner function for the memory leak measuring utility decorator."""
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
    """Decorator for marking a function as deprecated.

    Args:
        message: deprecation notice shown in the warning.

    Returns:
        A decorator factory: calling it with a function returns the wrapped
        function that emits the deprecation warning before delegating.
    """

    def deprecated_decorator(func):
        """Decorator."""

        def deprecated_func(*args, **kwargs):
            """Core function."""
            log.warning(f"{func.__name__} is a deprecated function. {message}")
            return func(*args, **kwargs)

        return deprecated_func

    return deprecated_decorator


def rsetattr(obj, attr, val):
    """Recursive setattr for multi level attributes like `obj.attribute.subattribute`.

    Args:
        obj: object whose nested attribute should be set.
        attr: dotted attribute path such as ``attribute.subattribute``.
        val: value to assign to the final attribute.

    Returns:
        None (the return value of ``setattr``).
    """
    pre, _, post = attr.rpartition(".")
    return setattr(rgetattr(obj, pre) if pre else obj, post, val)


def rgetattr(obj, attr, *args):
    """Recursive getattr for multi level attributes like `obj.attribute.subattribute`.

    Args:
        obj: object whose nested attribute should be retrieved.
        attr: dotted attribute path such as ``attribute.subattribute``.
        *args: optional default value forwarded to ``getattr`` when the
            attribute is missing.

    Returns:
        The value of the nested attribute, or the default from ``*args``.
    """

    def _getattr(obj, attr):
        return getattr(obj, attr, *args)

    return freduce(_getattr, [obj] + attr.split("."))


def rhasattr(obj, attr):
    """Recursive hasattr for multi level attributes like `obj.attribute.subattribute`.

    Args:
        obj: object whose nested attribute should be checked.
        attr: dotted attribute path such as ``attribute.subattribute``.

    Returns:
        bool: ``True`` if the nested attribute exists, ``False`` otherwise.
    """
    pre, _, post = attr.rpartition(".")
    return hasattr(rgetattr(obj, pre) if pre else obj, post)


def set_attributes_of_dataclass_from_dict(dataclass_, dict_, nested=None):
    """Set values in a Dataclass from a dictionary.

    Args:
        dataclass_: dataclass instance to update.
        dict_: dictionary whose keys map to attributes; nested dicts traverse nested attributes.
        nested: internal recursion accumulator; callers omit.

    Raises:
        AttributeError: if a key has no matching attribute.
    """
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
    """Get environment variable. Raise error if variable not found.

    Args:
        key: environment variable name.
        default: optional fallback.

    Returns:
        str: the value.

    Raises:
        ValueError: if the variable is unset (or set to an empty string) and
            no truthy default is provided.
    """
    value = os.getenv(key, default)
    if not value:
        raise ValueError(
            f"""Could not determine value of environment variable: {key}.
                         Make sure to set it in an `.env` file inside the HiSim root folder
                         or somewhere within your system environment."""
        )
    return value


class InstanceCounterMeta(type):
    """Metaclass to make instance counter not share count with descendants."""

    ids: itertools.count = itertools.count(1)


@dataclass
class InstanceCounter(metaclass=InstanceCounterMeta):
    """Mixin to add automatic ID generation."""

    def __post_init__(self, reset=False):
        """Runs after initialization of the dataclass."""
        if reset:
            self.instance_id = 0
        else:
            self.instance_id = next(self.__class__.ids)
        if self.instance_id > 1e3:
            raise RuntimeError(
                f"""Too many instances of {self.__class__}.
                Consider using a more performant or simpler type (e.g. int, float)."""
            )
