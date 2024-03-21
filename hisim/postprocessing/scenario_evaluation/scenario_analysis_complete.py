"""Data Collection for Scenario Comparison."""
# clean
import time
import os
from typing import Any, List, Optional, Dict
from hisim.postprocessing.scenario_evaluation import (
    result_data_collection,
    result_data_processing,
    result_data_plotting,
)


class ScenarioAnalysis:

    """ScenarioAnalysis class which executes result data collection, processing and plotting."""

    def __init__(
        self,
        folder_from_which_data_will_be_collected: str,
        path_to_default_config: str,
        simulation_duration_to_check: str,
        time_resolution_of_data_set: Any,
        data_processing_mode: Any,
        variables_to_check: List[str],
        dict_with_extra_information_for_specific_plot: Dict[str, Dict],
        dict_with_scenarios_to_check: Optional[Dict[str, List[str]]] = None,
        result_folder_description: Optional[str] = None
    ) -> None:
        """Initialize the class."""

        result_data_collection.ResultDataCollection(
            data_processing_mode=data_processing_mode,
            folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
            path_to_default_config=path_to_default_config,
            time_resolution_of_data_set=time_resolution_of_data_set,
            simulation_duration_to_check=simulation_duration_to_check,
        )
        result_data_plotting.ScenarioChartGeneration(
            simulation_duration_to_check=simulation_duration_to_check,
            time_resolution_of_data_set=time_resolution_of_data_set,
            data_processing_mode=data_processing_mode,
            variables_to_check=variables_to_check,
            dict_of_scenarios_to_check=dict_with_scenarios_to_check,
            dict_with_extra_information_for_specific_plot=dict_with_extra_information_for_specific_plot,
            result_folder_description=result_folder_description
        )


def main():
    """Main function to execute the scenario analysis."""

    # Inputs for scenario analysis
    # -------------------------------------------------------------------------------------------------------------------------------------
    time_resolution_of_data_set = result_data_collection.ResultDataTypeEnum.YEARLY
    cluster_storage_path = "/fast/home/k-rieck/"
    module_results_directory = "repositories/HiSim/system_setups/results/household_cluster_advanced_hp_pv_battery_ems/"
    result_folder_description_one = "06-03-2024-floor-with-cooling-pv-share-0"
    result_folder_description_two = "monte_carlo_20240306_1451"
    folder_from_which_data_will_be_collected = os.path.join(
        *[cluster_storage_path, module_results_directory, result_folder_description_one, result_folder_description_two]
    )
    print(folder_from_which_data_will_be_collected)

    path_to_default_config = os.path.join(
        cluster_storage_path,
        "jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simus/default_building_pv_config.json",
    )
    simulation_duration_to_check = str(365)

    data_processing_mode = result_data_collection.ResultDataProcessingModeEnum.PROCESS_ALL_DATA
    filterclass = result_data_processing.FilterClass()
    # list_with_variables_to_check = (
    #     filterclass.electricity_data + filterclass.variables_for_debugging_purposes
    # )  # filterclass.flow_and_return_temperatures  # +filterclass.kpi_data  #
    list_with_variables_to_check = filterclass.variables_for_debugging_purposes

    # TODO: filter several scenario parameters (eg pv and building code together) not working yet, need to be fixed
    # dict_with_scenarios_to_check = {"share_of_maximum_pv_power": filterclass.pv_share}
    # dict_with_scenarios_to_check = {"building_code": filterclass.building_type}

    dict_with_scenarios_to_check = None

    dict_with_extra_information_for_specific_plot: Dict[str, Dict] = {
        "scatter": {
            "x_data_variable": "Weather|Temperature|DailyAverageOutsideTemperatures"
        },  # "Specific heating demand according to TABULA" "Building|Temperature|TemperatureIndoorAir"   "HeatDistributionController|Temperature|HeatingFlowTemperature"
        "stacked_bar": {
            "y1_data_variable": "Mean flow temperature of heat pump",
            "y2_data_variable": "Mean return temperature of heat pump",
            "use_y1_as_bottom_for_y2": False,
            "sort_according_to_y1_or_y2_data": "y2",
        },
    }

    # -------------------------------------------------------------------------------------------------------------------------------------

    ScenarioAnalysis(
        folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
        time_resolution_of_data_set=time_resolution_of_data_set,
        path_to_default_config=path_to_default_config,
        simulation_duration_to_check=simulation_duration_to_check,
        data_processing_mode=data_processing_mode,
        variables_to_check=list_with_variables_to_check,
        dict_with_scenarios_to_check=dict_with_scenarios_to_check,
        dict_with_extra_information_for_specific_plot=dict_with_extra_information_for_specific_plot,
        result_folder_description=result_folder_description_two
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
