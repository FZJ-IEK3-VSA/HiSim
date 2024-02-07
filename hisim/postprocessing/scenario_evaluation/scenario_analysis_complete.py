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
        dict_with_scenarios_to_check: Optional[Dict[str, List[str]]] = None,
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
        )


def main():
    """Main function to execute the scenario analysis."""

    # Inputs for scenario analysis
    # -------------------------------------------------------------------------------------------------------------------------------------
    time_resolution_of_data_set = result_data_collection.ResultDataTypeEnum.YEARLY

    cluster_storage_path = "/fast/home/k-rieck/"
    # cluster_storage_path = "/storage_cluster/projects/2024-k-rieck-hisim-mass-simulations/hisim_results/results/"

    folder_from_which_data_will_be_collected = os.path.join(
        cluster_storage_path,
        "repositories/HiSim/system_setups/results/household_cluster_advanced_hp_pv_battery_ems/monte_carlo_20240108_0935",
    )

    path_to_default_config = os.path.join(
        cluster_storage_path,
        "jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simus/default_building_pv_config.json",
    )
    simulation_duration_to_check = str(365)

    data_processing_mode = result_data_collection.ResultDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_CODES

    filterclass = result_data_processing.FilterClass()
    # list_with_variables_to_check = (
    #     filterclass.variables_for_debugging_purposes + filterclass.heating_demand
    # )
    list_with_variables_to_check = filterclass.kpi_data + filterclass.heating_demand

    # TODO: filter several scenario parameters (eg pv and building code together) not working yet, need to be fixed
    # dict_with_scenarios_to_check = {"share_of_maximum_pv_power": filterclass.pv_share,"building_code": ["DE.N.SFH.05.Gen.ReEx.001.002"]}
    # dict_with_scenarios_to_check = {
    #     "building_code": [
    #         "DE.N.SFH",
    #         "DE.N.TH",
    #         "DE.N.MFH",
    #         "DE.N.AB",
    #     ]
    # }
    dict_with_scenarios_to_check = {
        "building_code": filterclass.building_refurbishment_state
    }
    # dict_with_scenarios_to_check = {"share_of_maximum_pv_power": filterclass.pv_share}
    # dict_with_scenarios_to_check = None

    # -------------------------------------------------------------------------------------------------------------------------------------

    ScenarioAnalysis(
        folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
        time_resolution_of_data_set=time_resolution_of_data_set,
        path_to_default_config=path_to_default_config,
        simulation_duration_to_check=simulation_duration_to_check,
        data_processing_mode=data_processing_mode,
        variables_to_check=list_with_variables_to_check,
        dict_with_scenarios_to_check=dict_with_scenarios_to_check,
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
