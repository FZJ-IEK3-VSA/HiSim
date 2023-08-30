"""Data Collection for Scenario Comparison with Pyam."""
# clean
import time
from hisim.postprocessing import pyam_data_collection, pyam_data_processing


class PyamDataAnalysis:

    """PyamDataAnalysis class which executes pyam data collection and processing."""

    def __init__(self) -> None:
        """Initialize the class."""

        pyam_data_collection.PyamDataCollector(
            data_collection_mode=pyam_data_collection.PyamDataCollectionModeEnum.COLLECT_AND_SORT_DATA_ACCORDING_TO_PARAMETER_KEYS,
            path_to_default_config="/storage_cluster/internal/home/k-rieck/jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simu_one/default_building_pv_config.json",  # r"C:\Users\k.rieck\Cluster_stuff_copied\job_array_for_hisim_mass_simu_one\default_building_pv_config.json",
        )
        pyam_data_processing.PyAmChartGenerator(
            simulation_duration_to_check=str(365),
            data_processing_mode=pyam_data_processing.PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_PV_POWERS,
        )


def main():
    """Main function to execute the pyam data analysis."""
    PyamDataAnalysis()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
