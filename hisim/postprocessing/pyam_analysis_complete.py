"""Data Collection for Scenario Comparison with Pyam."""
# clean
import time
import os
from hisim.postprocessing import pyam_data_collection, pyam_data_processing


class PyamDataAnalysis:

    """PyamDataAnalysis class which executes pyam data collection and processing."""

    def __init__(
        self,
        data_collection_mode: Any,
        folder_from_which_data_will_be_collected: str,
        path_to_default_config: str,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        variables_to_check_for_hourly_data: List[str],
        variables_to_check_for_yearly_data: List[str],
    ) -> None:
        """Initialize the class."""

        pyam_data_collection.PyamDataCollector(
            data_collection_mode=data_collection_mode,
            folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
            path_to_default_config=path_to_default_config,
        )
        pyam_data_processing.PyAmChartGenerator(
            simulation_duration_to_check=simulation_duration_to_check,
            data_processing_mode=data_processing_mode,
            variables_to_check_for_hourly_data=variables_to_check_for_hourly_data,
            variables_to_check_for_yearly_data=variables_to_check_for_yearly_data,
        )


def main():
    """Main function to execute the pyam data analysis."""

    data_collection_mode = (
        pyam_data_collection.PyamDataCollectionModeEnum.COLLECT_AND_SORT_DATA_ACCORDING_TO_PARAMETER_KEYS
    )
    folder_from_which_data_will_be_collected = os.path.join(
        os.pardir, os.pardir, "examples", "results"
    )
    path_to_default_config = "/storage_cluster/internal/home/k-rieck/jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simu_one/default_building_pv_config.json"
    # path_to_default_config = r"C:\Users\k.rieck\Cluster_stuff_copied\job_array_for_hisim_mass_simu_one\default_building_pv_config.json"

    simulation_duration_to_check = str(365)
    data_processing_mode = (
        pyam_data_processing.PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_TYPES
    )
    variables_to_check_for_hourly_data = (
        pyam_data_processing.heating_demand
        + pyam_data_processing.electricity_data
        + pyam_data_processing.occuancy_consumption
    )
    variables_to_check_for_yearly_data = pyam_data_processing.kpi_data

    PyamDataAnalysis(
        data_collection_mode=data_collection_mode,
        folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
        path_to_default_config=path_to_default_config,
        simulation_duration_to_check=simulation_duration_to_check,
        variables_to_check_for_hourly_data=variables_to_check_for_hourly_data,
        variables_to_check_for_yearly_data=variables_to_check_for_yearly_data,
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
