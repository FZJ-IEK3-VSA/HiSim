"""Test if components deliver similar results for different time resolutions.

Here we test the cluster household.
"""

# clean

import os
from typing import Dict, List, Tuple
import pandas as pd
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


def values_are_similar(lst: List, tolerance: float = 1e-1) -> bool:
    """Function to check if values are similar within a certain tolerance."""
    return all(abs(x - lst[0]) <= tolerance for x in lst)


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_cluster.py"


@utils.measure_execution_time
@pytest.mark.base
def test_cluster_houe_for_several_time_resolutions():
    """Test cluster house for several time resolutions."""

    opex_consumption_dict: Dict = {}
    yearly_results_dict: Dict = {}
    for seconds_per_timestep in [60 * 15, 60 * 30, 60 * 60]:
        print("\n")
        print("Seconds per timestep ", seconds_per_timestep)
        # run simulation of cluster house
        try:
            result_dict, opex_consumption_dict = run_cluster_house(
                seconds_per_timestep=seconds_per_timestep,
                yearly_result_dict=yearly_results_dict,
                opex_consumptions_dict=opex_consumption_dict,
            )
        except Exception as exc:
            raise ValueError(f"Cluster house simulation with time resolution {seconds_per_timestep}s is not possible. "
                  "Please make sure that only heat pumps are used for space heating and domestic hot water "
                  "because the gas heaters provoque error at low time resolutions.") from exc

    # go through all results and compare if aggregated results are all the same
    print("\n")
    print("Yearly results including KPIs")
    for key, values in result_dict.items():
        if not values_are_similar(lst=values):
            print(key, values, "not all similar")

    # go through all opex consumptions and compare if results are all the same
    print("\n")
    print("Opex consumtions in kWh")
    for key, values in opex_consumption_dict.items():
        if not values_are_similar(lst=values):
            print(key, values, "not all similar")


def run_cluster_house(
    seconds_per_timestep: int, yearly_result_dict: Dict, opex_consumptions_dict: Dict
) -> Tuple[Dict, Dict]:  # noqa: too-many-statements
    """The test should check if a normal simulation works with the electricity grid implementation."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    # Build Simulation Parameters

    my_simulation_parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)

    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS_AND_WRITE_TO_REPORT)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_ALL_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )
    # my_simulation_parameters.logging_level = 4

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_cluster",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)
    # Build method
    # setup function needs to be imported inside the function, otherwise error occurs
    from system_setups.household_cluster import setup_function  # pylint: disable=import-outside-toplevel
    # make sure that in cluster house setup function HeatPumps are used as energy system (GasHeaters do currently not work for time resolutions > 60 *15)
    setup_function(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Get yearly results from scenario preparation
    yearly_results_path = os.path.join(
        my_simulation_parameters.result_directory, "result_data_for_scenario_evaluation", "yearly_365_days.csv"
    )
    yearly_results = pd.read_csv(yearly_results_path, usecols=["variable", "value"])
    # Get opex consumptions
    opex_results_path = os.path.join(my_simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")

    opex_df = pd.read_csv(opex_results_path, index_col=0)

    # append results to result dictionaries
    if yearly_result_dict == {} and opex_consumptions_dict == {}:
        yearly_result_dict.update(
            {yearly_results["variable"][i]: [yearly_results["value"][i]] for i in range(len(yearly_results))}
        )
        for j in range(len(opex_df)):
            opex_consumptions_dict.update({opex_df.index[j]: [opex_df["Consumption in kWh"].iloc[j]]})
    else:
        for i in range(len(yearly_results)):
            yearly_result_dict[yearly_results["variable"][i]].append(yearly_results["value"][i])

        for j in range(len(opex_df)):
            opex_consumptions_dict[opex_df.index[j]].append(opex_df["Consumption in kWh"].iloc[j])

    return yearly_result_dict, opex_consumptions_dict
