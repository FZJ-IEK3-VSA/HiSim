"""Data Collection for Scenario Comparison with Pyam."""
# clean
import glob
import time
import os
from typing import Dict, Any
import json
import enum
import pyam
import pandas as pd
from hisim import log
from hisim.postprocessing import pyam_data_collection, pyam_data_processing


class PyamDataAnalysis:

    """PyamDataAnalysis class which executes pyam data collection and processing."""

    def __init__(self) -> None:
        """Initialize the class."""

        # pyam_data_collection.PyamDataCollector(
        #     data_collection_mode=pyam_data_collection.PyamDataCollectionModeEnum.COLLECT_AND_SORT_DATA_ACCORDING_TO_PARAMETER_KEYS,
        #     path_to_default_config=r"C:\Users\k.rieck\Cluster_stuff_copied\job_array_for_hisim_mass_simu_one\default_building_pv_config.json",
        # )
        pyam_data_processing.PyAmChartGenerator(
            simulation_duration_to_check=str(1),
            data_processing_mode=pyam_data_processing.PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_TYPES,
        )


def main():
    """Main function to execute the pyam data analysis."""
    PyamDataAnalysis()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
