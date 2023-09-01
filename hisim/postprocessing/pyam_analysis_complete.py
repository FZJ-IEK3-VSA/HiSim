"""Data Collection for Scenario Comparison with Pyam."""
# clean
import time
import os
from hisim.postprocessing import pyam_data_collection, pyam_data_processing
from typing import Any, List

class PyamDataAnalysis:

    """PyamDataAnalysis class which executes pyam data collection and processing."""

    def __init__(
        self,
        folder_from_which_data_will_be_collected: str,
        path_to_default_config: str,
        simulation_duration_to_check: str,
        data_processing_mode: Any,
        variables_to_check_for_hourly_data: List[str],
        variables_to_check_for_yearly_data: List[str],
        analyze_yearly_or_hourly_data: Any = None,
    ) -> None:
        """Initialize the class."""

        pyam_data_collection.PyamDataCollector(
            data_processing_mode=data_processing_mode,
            folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
            path_to_default_config=path_to_default_config,
            analyze_yearly_or_hourly_data= analyze_yearly_or_hourly_data,
        )
        pyam_data_processing.PyAmChartGenerator(
            simulation_duration_to_check=simulation_duration_to_check,
            analyze_yearly_or_hourly_data=analyze_yearly_or_hourly_data,
            data_processing_mode=data_processing_mode,
            variables_to_check_for_hourly_data=variables_to_check_for_hourly_data,
            variables_to_check_for_yearly_data=variables_to_check_for_yearly_data,
        )


def main():
    """Main function to execute the pyam data analysis."""

    # Inputs for pyam analysis
    # -------------------------------------------------------------------------------------------------------------------------------------
    analyze_yearly_or_hourly_data=pyam_data_collection.PyamDataTypeEnum.YEARLY
    folder_from_which_data_will_be_collected = "/storage_cluster/internal/home/k-rieck/repositories/HiSim/examples/results/household_hplib_hws_hds_pv_battery_ems_config/german_tabula_buildings_20230831_1608"

    path_to_default_config = "/storage_cluster/internal/home/k-rieck/jobs_hisim/cluster-hisim-paper/job_array_for_hisim_mass_simu_one/default_building_pv_config.json"
    # path_to_default_config = r"C:\Users\k.rieck\Cluster_stuff_copied\job_array_for_hisim_mass_simu_one\default_building_pv_config.json"

    simulation_duration_to_check = str(365)
    data_processing_mode = (
        pyam_data_collection.PyamDataProcessingModeEnum.PROCESS_FOR_DIFFERENT_BUILDING_SIZES
    )
    variables_to_check_for_hourly_data = (
        pyam_data_processing.heating_demand
        + pyam_data_processing.electricity_data
        + pyam_data_processing.occuancy_consumption
    )
    variables_to_check_for_yearly_data = pyam_data_processing.kpi_data

    # -------------------------------------------------------------------------------------------------------------------------------------

    PyamDataAnalysis(
        analyze_yearly_or_hourly_data=pyam_data_collection.PyamDataTypeEnum.YEARLY,
        folder_from_which_data_will_be_collected=folder_from_which_data_will_be_collected,
        path_to_default_config=path_to_default_config,
        simulation_duration_to_check=simulation_duration_to_check,
        data_processing_mode=data_processing_mode,
        variables_to_check_for_hourly_data=variables_to_check_for_hourly_data,
        variables_to_check_for_yearly_data=variables_to_check_for_yearly_data,
    )


if __name__ == "__main__":
    start_time = time.time()
    main()
    print(f"---{time.time() - start_time} seconds ___")
