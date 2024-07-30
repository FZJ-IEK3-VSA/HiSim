"""Test if components deliver similar results for different time resolutions.

Here we test the cluster household.
"""

# clean

import os
import json
from typing import Dict
import pandas as pd
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim import utils
from hisim.postprocessingoptions import PostProcessingOptions


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_cluster.py"


@utils.measure_execution_time
@pytest.mark.base
def test_cluster_houe_for_several_time_resolutions():
    """Test cluster house for several time resolutions."""
    result_dict: Dict = {"Consumptions [kWh]": {}, "Electricity Meter KPIs [kWh]": {}}
    for seconds_per_timestep in [60 * 15, 60 * 30, 60 * 60]:
        print("seconds per timestep", seconds_per_timestep)
        # run simulation of cluster house
        result_dict = run_cluster_house(seconds_per_timestep=seconds_per_timestep, result_dict=result_dict)

    # show results
    for key, values in result_dict.items():
        print("\n")
        print(key)
        for key_2, values_2 in values.items():
            print(key_2, values_2)
        print("\n")


def run_cluster_house(seconds_per_timestep: int, result_dict: Dict) -> Dict:  # noqa: too-many-statements
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
    my_simulation_parameters.logging_level = 4

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

    setup_function(my_sim=my_sim, my_simulation_parameters=my_simulation_parameters)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Compare with kpi computation results

    # read kpi data
    with open(
        os.path.join(my_sim._simulation_parameters.result_directory, "all_kpis.json"), "r", encoding="utf-8"  # pylint: disable=W0212
    ) as file:
        jsondata = json.load(file)

    cumulative_consumption_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity consumption"].get("value")

    cumulative_production_kpi_in_kilowatt_hour = jsondata["General"]["Total electricity production"].get("value")

    electricity_from_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy from grid"].get("value")

    electricity_to_grid_kpi_in_kilowatt_hour = jsondata["Electricity Meter"]["Total energy to grid"].get("value")

    # get opex values
    opex_results_path = os.path.join(my_simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    if os.path.exists(opex_results_path):
        opex_df = pd.read_csv(opex_results_path, index_col=0)
        opex_consumptions = opex_df["Consumption in kWh"]
    else:
        print("OPEX-costs for components are not calculated yet. Set PostProcessingOptions.COMPUTE_OPEX")

    if result_dict["Consumptions [kWh]"] == {}:
        for key in opex_consumptions.keys():
            result_dict["Consumptions [kWh]"][key] = [opex_consumptions[key]]
        result_dict["Electricity Meter KPIs [kWh]"]["Total electricity consumption"] = [
            cumulative_consumption_kpi_in_kilowatt_hour
        ]
        result_dict["Electricity Meter KPIs [kWh]"]["Total electricity production"] = [
            cumulative_production_kpi_in_kilowatt_hour
        ]
        result_dict["Electricity Meter KPIs [kWh]"]["Total energy from grid"] = [electricity_from_grid_kpi_in_kilowatt_hour]
        result_dict["Electricity Meter KPIs [kWh]"]["Total energy to grid"] = [electricity_to_grid_kpi_in_kilowatt_hour]

    else:
        for key in opex_consumptions.keys():
            result_dict["Consumptions [kWh]"][key].append(opex_consumptions[key])
        result_dict["Electricity Meter KPIs [kWh]"]["Total electricity consumption"].append(
            cumulative_consumption_kpi_in_kilowatt_hour
        )
        result_dict["Electricity Meter KPIs [kWh]"]["Total electricity production"].append(
            cumulative_production_kpi_in_kilowatt_hour
        )
        result_dict["Electricity Meter KPIs [kWh]"]["Total energy from grid"].append(
            electricity_from_grid_kpi_in_kilowatt_hour
        )
        result_dict["Electricity Meter KPIs [kWh]"]["Total energy to grid"].append(electricity_to_grid_kpi_in_kilowatt_hour)

    return result_dict
