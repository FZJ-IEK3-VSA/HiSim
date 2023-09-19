"""Data Collection for Scenario Comparison with Pyam."""
# clean
import time
import os
from typing import Any, List, Optional
from hisim.postprocessing import pyam_data_collection, pyam_data_processing


class PyamDataAnalysis:

    """PyamDataAnalysis class which executes pyam data collection and processing."""

    def __init__(
        self,
        folder_from_which_data_will_be_collected: str,
        path_to_default_config: str,
        simulation_duration_to_check: str,
        time_resolution_of_data_set: Any,
        data_processing_mode: Any,
        variables_to_check: List[str],
        list_of_scenarios_to_check: Optional[List[str]] = None,
    ) -> None:
        """Initialize the class."""

        pyam_data_collection.PyamDataCollector(
            data_processing_mode=data_processing_mode,
            folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
            path_to_default_config=path_to_default_config,
            time_resolution_of_data_set=time_resolution_of_data_set,
            simulation_duration_to_check=simulation_duration_to_check,
        )
        pyam_data_processing.PyAmChartGenerator(
            simulation_duration_to_check=simulation_duration_to_check,
            time_resolution_of_data_set=time_resolution_of_data_set,
            data_processing_mode=data_processing_mode,
            variables_to_check=variables_to_check,
            list_of_scenarios_to_check=list_of_scenarios_to_check,
        )


def main():
    """Main function to execute the pyam data analysis."""

    # Inputs for pyam analysis
    # -------------------------------------------------------------------------------------------------------------------------------------
    time_resolution_of_data_set = pyam_data_collection.PyamDataTypeEnum.YEARLY

    cluster_storage_path = "/fast/home/k-rieck/"

    folder_from_which_data_will_be_collected = os.path.join(
        cluster_storage_path,
        # "repositories/HiSim/examples/results/household_cluster_reference_advanced_hp/german_tabula_buildings_20230908_1231/",
        "repositories/HiSim/examples/results/household_cluster_test_advanced_hp/hplib_configs_20230915_1122",
    )
    # folder_from_which_data_will_be_collected = (
    #     r"C:\Users\k.rieck\Cluster_stuff_copied\examples_results"
    # )
    path_to_default_config = os.path.join(
        cluster_storage_path,
        "jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simus/default_config_for_hplib_test.json",
    )
    # path_to_default_config = r"C:\Users\k.rieck\Cluster_stuff_copied\job_array_for_hisim_mass_simu_one\default_building_pv_config.json"

    simulation_duration_to_check = str(365)

    data_processing_mode = (
        pyam_data_collection.PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_DELTA_T_IN_HP_CONTROLLER
    )

    list_with_variables_to_check = (
        pyam_data_processing.heating_demand + pyam_data_processing.electricity_data + pyam_data_processing.kpi_data) # + pyam_data_processing.kpi_data)
    # + pyam_data_processing.heating_demand
    # + pyam_data_processing.electricity_data
    # + pyam_data_processing.occuancy_consumption
    # )

    # list_of_scenarios_to_check = pyam_data_processing.building_type

    list_of_scenarios_to_check = None  # ["001.002"]

    # -------------------------------------------------------------------------------------------------------------------------------------

    PyamDataAnalysis(
        folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
        time_resolution_of_data_set=time_resolution_of_data_set,
        path_to_default_config=path_to_default_config,
        simulation_duration_to_check=simulation_duration_to_check,
        data_processing_mode=data_processing_mode,
        variables_to_check=list_with_variables_to_check,
        list_of_scenarios_to_check=list_of_scenarios_to_check,
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
