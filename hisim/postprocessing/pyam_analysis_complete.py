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

        pyam_data_collection.PyamDataCollector(
            analysis_mode=pyam_data_collection.PyamDataAnalysisEnum.SENSITIVITY_ANALYSIS,
            path_to_default_config="/storage_cluster/internal/home/k-rieck/jobs_hisim/job_array_for_hisim_mass_simu_one/default_building_pv_config.json",
        )
        # pyam_data_processing.PyAmChartGenerator(simulation_duration_to_check=str(365))


def main():
    """Main function to execute the pyam data analysis."""
    PyamDataAnalysis()


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
