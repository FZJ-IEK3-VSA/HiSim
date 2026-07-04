"""Data Collection for Scenario Comparison."""

# clean
import time
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from dataclasses_json.core import _decode_dataclass
from hisim.postprocessing.scenario_evaluation import (
    result_data_collection,
    result_data_processing,
    result_data_plotting,
)
from hisim.component import ConfigBase
from hisim import log


#: Mapping of legacy JSON field names to their current names.
#:
#: ``simulation_duration_to_check`` was renamed to
#: ``simulation_duration_to_check_in_days`` so that the duration unit (days) is
#: explicit in the field name (see GitLab issue #816).  Existing serialized
#: config files may still use the old key; :func:`_migrate_legacy_field_names`
#: maps it to the new name during deserialization so the value is not silently
#: dropped.
_LEGACY_FIELD_NAMES: "dict[str, str]" = {
    "simulation_duration_to_check": "simulation_duration_to_check_in_days",
}

_A = TypeVar("_A")


def _migrate_legacy_field_names(kvs: Any) -> Any:
    """Rename legacy JSON keys to their current field names.

    ``ScenarioAnalysisConfig`` was previously serialized with the field name
    ``simulation_duration_to_check``.  This was renamed to
    ``simulation_duration_to_check_in_days`` so that the duration unit (days) is
    explicit in the field name.  Existing JSON config files may still contain
    the old key; this helper maps it to the new name so that the value is not
    silently dropped during deserialization.

    A :class:`DeprecationWarning` is emitted when a legacy key is encountered so
    that users are alerted to update their config files.

    Args:
        kvs: The parsed JSON value (typically a ``dict``).  Non-dict values are
            returned unchanged.

    Returns:
        When ``kvs`` is a ``dict`` a shallow copy with legacy keys renamed is
        returned (the original is not mutated); otherwise ``kvs`` is returned
        unchanged.  If both a legacy key and its replacement are present, the
        replacement value wins and the legacy value is discarded.
    """
    if not isinstance(kvs, dict):
        return kvs
    migrated = dict(kvs)
    found_legacy: "list[str]" = []
    for old_name, new_name in _LEGACY_FIELD_NAMES.items():
        if old_name in migrated:
            found_legacy.append(old_name)
            if new_name not in migrated:
                migrated[new_name] = migrated.pop(old_name)
            else:
                # New name already present -- drop the legacy key, keep the new value.
                migrated.pop(old_name)
    if found_legacy:
        warnings.warn(
            "ScenarioAnalysisConfig JSON config uses deprecated field name(s) "
            + ", ".join(repr(n) for n in found_legacy)
            + ". They have been renamed to "
            + ", ".join(repr(_LEGACY_FIELD_NAMES[n]) for n in found_legacy)
            + "; please update your config file. The values are still loaded "
            + "for backward compatibility.",
            DeprecationWarning,
            stacklevel=2,
        )
    return migrated


@dataclass_json
@dataclass(kw_only=True)
class ScenarioAnalysisConfig(ConfigBase):
    """Configuration for running a scenario analysis."""

    building_name: str
    name: str
    data_format_type: str
    time_resolution_of_data_set: str
    cluster_storage_path: str
    module_results_directory: str
    result_folder_description_one: str
    result_folder_description_two: str
    path_to_default_module_config: str
    data_processing_mode: str
    # Duration (in days) of the simulation results to collect and evaluate; e.g. "365" = one year.
    simulation_duration_to_check_in_days: str
    variables_to_check: List[str]
    dict_with_scenarios_to_check: Optional[Dict]
    dict_with_extra_information_for_specific_plot: Dict[str, Dict]

    @classmethod
    def get_default(cls):
        """Get default ScenarioAnalysisConfig."""

        return ScenarioAnalysisConfig(
            building_name="BUI1",
            name="ScenarioAnalysisConfig_0",
            data_format_type=result_data_processing.DataFormatEnum.CSV.name,
            time_resolution_of_data_set=result_data_processing.ResultDataTypeEnum.YEARLY.name,
            cluster_storage_path="system_setups/",
            module_results_directory="results/household_cluster_advanced_hp_pv_battery_ems/",
            result_folder_description_one="PV-1-hds-2-hpc-mode-2/",
            result_folder_description_two="weather-location-BAD_MARIENBURG",
            path_to_default_module_config="/fast/home/k-rieck/jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simus/default_config_for_builda_data.json",
            data_processing_mode=result_data_collection.ResultDataProcessingModeEnum.PROCESS_ALL_DATA.name,
            simulation_duration_to_check_in_days=str(365),
            variables_to_check=result_data_processing.OutputVariableEnumClass.KPI_DATA.value.descriptions,
            dict_with_scenarios_to_check=None,
            dict_with_extra_information_for_specific_plot={
                "scatter": {
                    "x_data_variable": "Conditioned floor area"
                },  # "Building|Temperature|TemperatureIndoorAir"     "Specific heating demand according to TABULA" "Weather|Temperature|DailyAverageOutsideTemperatures"
                "stacked_bar": {
                    "y1_data_variable": "Mean flow temperature of heat pump",
                    "y2_data_variable": "Mean return temperature of heat pump",
                    "use_y1_as_bottom_for_y2": False,
                    "sort_according_to_y1_or_y2_data": "y2",
                },
            },
        )


@classmethod
def _scenario_analysis_config_from_dict(
    cls: Type[_A], kvs: Any, *, infer_missing: bool = False
) -> _A:
    """Deserialize a dict into a :class:`ScenarioAnalysisConfig`.

    Supports backward-compatible deserialization of JSON configs that still use
    the deprecated field name ``simulation_duration_to_check`` by mapping it to
    ``simulation_duration_to_check_in_days`` before decoding.  A
    :class:`DeprecationWarning` is emitted when the legacy key is found.

    ``dataclasses_json`` overrides any ``from_dict`` defined in the class body,
    so this method is assigned to :meth:`ScenarioAnalysisConfig.from_dict`
    *after* the ``@dataclass_json`` decorator has run.  ``from_json`` calls
    ``cls.from_dict`` internally, so JSON deserialization is covered as well.
    """
    kvs = _migrate_legacy_field_names(kvs)
    return _decode_dataclass(cls, kvs, infer_missing)


ScenarioAnalysisConfig.from_dict = _scenario_analysis_config_from_dict  # type: ignore[assignment]


class ScenarioAnalysisWithConfig:
    """ScenarioAnalysis class which executes result data collection, processing and plotting."""

    def __init__(self, scenario_analysis_config: ScenarioAnalysisConfig) -> None:
        """Initialize the class."""
        # Get input parameters from config
        try:
            config_name = scenario_analysis_config.name.split("_")[1]
        except (IndexError, AttributeError):
            config_name = ""

        data_processing_mode = scenario_analysis_config.data_processing_mode
        data_format_type = scenario_analysis_config.data_format_type
        folder_from_which_data_will_be_collected = str(
            Path(scenario_analysis_config.cluster_storage_path)
            / scenario_analysis_config.module_results_directory
            / scenario_analysis_config.result_folder_description_one
            / scenario_analysis_config.result_folder_description_two
        )
        path_to_default_config = scenario_analysis_config.path_to_default_module_config
        time_resolution_of_data_set = scenario_analysis_config.time_resolution_of_data_set
        simulation_duration_to_check_in_days = scenario_analysis_config.simulation_duration_to_check_in_days
        variables_to_check = scenario_analysis_config.variables_to_check
        dict_with_scenarios_to_check = scenario_analysis_config.dict_with_scenarios_to_check
        dict_with_extra_information_for_specific_plot = (
            scenario_analysis_config.dict_with_extra_information_for_specific_plot
        )

        result_data_collection_instance = result_data_collection.ResultDataCollection(
            data_processing_mode=data_processing_mode,
            scenario_analysis_config_name=config_name,
            data_format_type=data_format_type,
            folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
            path_to_default_config=path_to_default_config,
            time_resolution_of_data_set=time_resolution_of_data_set,
            simulation_duration_to_check=simulation_duration_to_check_in_days,
        )
        result_data_plotting.ScenarioChartGeneration(
            simulation_duration_to_check=simulation_duration_to_check_in_days,
            filepath_of_aggregated_dataframe=result_data_collection_instance.filepath_of_aggregated_dataframe,
            scenario_config_name=config_name,
            data_format_type=data_format_type,
            time_resolution_of_data_set=time_resolution_of_data_set,
            data_processing_mode=data_processing_mode,
            variables_to_check=variables_to_check,
            dict_of_scenarios_to_check=dict_with_scenarios_to_check,
            dict_with_extra_information_for_specific_plot=dict_with_extra_information_for_specific_plot,
        )


def main():
    """Main function to execute the scenario analysis."""

    # Get inputs for scenario analysis
    python_arguments = sys.argv

    if len(python_arguments) == 1:
        use_default_scenario_analysis_config: bool = True

    elif len(python_arguments) == 2:
        use_default_scenario_analysis_config = False
        scenario_analysis_config_path = sys.argv[1]
    else:
        raise ValueError(
            f"There should be 1 or 2 python arguments (sys.argv). Here {len(python_arguments)} are given. Please check your code."
        )

    my_config: ScenarioAnalysisConfig
    if use_default_scenario_analysis_config is False:
        if isinstance(scenario_analysis_config_path, str) and Path(
            scenario_analysis_config_path.rstrip("\r")
        ).exists():
            with open(
                str(Path(scenario_analysis_config_path.rstrip("\r"))), encoding="unicode_escape"
            ) as scenario_analysis_config_file:

                my_config = ScenarioAnalysisConfig.from_json(scenario_analysis_config_file.read())  # type: ignore

            log.information(f"Read scenario analysis config from {scenario_analysis_config_path}")
            log.information("Config values: " + f"{my_config.to_dict()}" + "\n")
        else:
            # cannot open file for scenario analysis config so default config will be used
            use_default_scenario_analysis_config = True

    if use_default_scenario_analysis_config is True:
        my_config = ScenarioAnalysisConfig.get_default()

        log.information("No scenario analysis config path was given or could be opened. Default config is used.")

    # -------------------------------------------------------------------------------------------------------------------------------------

    ScenarioAnalysisWithConfig(scenario_analysis_config=my_config)


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
