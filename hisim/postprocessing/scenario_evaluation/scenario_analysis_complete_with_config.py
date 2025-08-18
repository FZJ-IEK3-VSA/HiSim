"""Data Collection for Scenario Comparison."""

# clean
import time
import os
import sys
from typing import List, Optional, Dict
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.postprocessing.scenario_evaluation import (
    result_data_collection,
    result_data_processing,
    result_data_plotting,
)
from hisim.component import ConfigBase
from hisim import log


@dataclass_json
@dataclass
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
    simulation_duration_to_check: str
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
            simulation_duration_to_check=str(365),
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


class ScenarioAnalysisWithConfig:
    """ScenarioAnalysis class which executes result data collection, processing and plotting."""

    def __init__(self, scenario_analysis_config: ScenarioAnalysisConfig) -> None:
        """Initialize the class."""
        # Get input parameters from config
        try:
            config_name = scenario_analysis_config.name.split("_")[1]
        except Exception:
            config_name = ""

        data_processing_mode = scenario_analysis_config.data_processing_mode
        data_format_type = scenario_analysis_config.data_format_type
        folder_from_which_data_will_be_collected = os.path.join(
            *[
                scenario_analysis_config.cluster_storage_path,
                scenario_analysis_config.module_results_directory,
                scenario_analysis_config.result_folder_description_one,
                scenario_analysis_config.result_folder_description_two,
            ]
        )
        path_to_default_config = scenario_analysis_config.path_to_default_module_config
        time_resolution_of_data_set = scenario_analysis_config.time_resolution_of_data_set
        simulation_duration_to_check = scenario_analysis_config.simulation_duration_to_check
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
            simulation_duration_to_check=simulation_duration_to_check,
        )
        result_data_plotting.ScenarioChartGeneration(
            simulation_duration_to_check=simulation_duration_to_check,
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
        if isinstance(scenario_analysis_config_path, str) and os.path.exists(
            scenario_analysis_config_path.rstrip("\r")
        ):
            with open(
                scenario_analysis_config_path.rstrip("\r"), encoding="unicode_escape"
            ) as scenario_analysis_config_file:

                my_config = ScenarioAnalysisConfig.from_json(scenario_analysis_config_file.read())  # type: ignore

            log.information(f"Read scenario analysis config from {scenario_analysis_config_path}")
            log.information("Config values: " + f"{my_config.to_dict}" + "\n")
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
