"""Sends a building sizer request to the UTSP and waits until the calculation is finished."""

import json
from typing import Dict, Iterable, List

import matplotlib.pyplot as plt
import pandas as pd
from utspclient import client  # type: ignore
from utspclient.datastructures import TimeSeriesRequest  # type: ignore

from building_sizer import building_sizer_algorithm, kpi_config, system_config
from building_sizer.building_sizer_algorithm import (
    BuildingSizerRequest,
    BuildingSizerResult,
)

# Define URL and API key for the UTSP server
URL = "http://134.94.131.167:443/api/v1/profilerequest"
API_KEY = "OrjpZY93BcNWw8lKaMp0BEchbCc"


def plot_ratings(ratings: List[List[float]]) -> None:
    """
    Generate a boxplot for each generation showing the range of ratings

    :param ratings: nested list, creating a list of ratings for each generation
    :type ratings: List[List[float]]
    """
    fig = plt.figure(figsize=(10, 7))
    ax = fig.add_subplot(111)
    ax.set_xlabel("Iterations")
    ax.set_ylabel("self consumption rate + autarky rate [%]")
    # Creating plot
    bp = ax.boxplot(ratings)
    # show plot
    plt.show()


def get_ratings_of_generation(
    building_sizer_config: BuildingSizerRequest,
) -> Dict[str, str]:
    """
    Returns the performance ratings for one generation of HiSim configurations

    :param building_sizer_config: the building sizer request for the generation
    :type building_sizer_config: BuildingSizerRequest
    :return: a dict mapping each HiSim configuration to its ratings
    :rtype: Dict[float]
    """
    hisim_results = building_sizer_algorithm.get_results_from_requisite_requests(
        building_sizer_config.requisite_requests, URL, API_KEY
    )
    # Extract the rating for each HiSim config
    ratings = {
        config: result.data["kpi_config.json"].decode()
        for config, result in hisim_results.items()
    }
    return ratings


def get_rating(kpi: str) -> float:
    return kpi_config.KPIConfig.from_json(kpi).get_kpi()  # type: ignore


def get_ratings(kpis: Iterable[str]) -> List[float]:
    return [get_rating(s) for s in kpis]


def minimize_config(hisim_config: str) -> str:
    """
    Helper method for testing, that extracts only the relevant fields of a system config
    to print them in a clearer way.
    """
    import json

    d = json.loads(hisim_config)
    keys = ["pv_included", "pv_peak_power", "battery_included", "battery_capacity"]
    d = {k: d[k] for k in keys}
    return json.dumps(d)


def main():
    # For testing: create a random id to enforce recalculation for each request.
    # For production use, this can be left out.
    guid = ""  # .join(random.choices(string.ascii_uppercase + string.digits, k=10))

    # Set the parameters for the building sizer
    hisim_version = "0.1.0.test2"
    building_sizer_version = "0.1.1"
    options = system_config.SizingOptions()

    # Create an initial simulation configuration for the building sizer
    initial_building_sizer_config = BuildingSizerRequest(
        URL,
        API_KEY,
        building_sizer_version,
        hisim_version,
        remaining_iterations=12,
        boolean_iterations=3,
        discrete_iterations=5,
        population_size=5,
        crossover_probability=0.2,
        mutation_probability=0.4,
        options=options,
    )
    building_sizer_config_json = initial_building_sizer_config.to_json()  # type: ignore
    # Create the initial building sizer request
    building_sizer_request = TimeSeriesRequest(
        building_sizer_config_json,
        f"building_sizer-{building_sizer_version}",
        guid=guid,
    )

    # Store the hash of each request in a set for loop detection
    previous_iterations = {building_sizer_request.get_hash()}

    # Store all iterations of building sizer requests in order
    building_sizer_iterations: List[BuildingSizerRequest] = []
    finished = False
    all_ratings = ""
    generations = []
    all_ratings_list = []
    while not finished:
        # Wait until the request finishes and the results are delivered
        result = client.request_time_series_and_wait_for_delivery(
            URL, building_sizer_request, API_KEY
        )
        # Get the content of the result file created by the Building Sizer
        status_json = result.data["status.json"].decode()
        building_sizer_result: BuildingSizerResult = BuildingSizerResult.from_json(status_json)  # type: ignore
        # Check if this was the final iteration and the building sizer is finished
        finished = building_sizer_result.finished
        # Get the building sizer configuration for the next request
        if building_sizer_result.subsequent_request is not None:
            building_sizer_request = building_sizer_result.subsequent_request
            # Loop detection: check if the same building sizer request has been encountered before (that would result in an endless loop)
            request_hash = building_sizer_request.get_hash()
            if request_hash in previous_iterations:
                raise RuntimeError(
                    f"Detected a loop: the following building sizer request has already been sent before.\n{building_sizer_request}"
                )
            previous_iterations.add(request_hash)

            # Store the building sizer config
            building_sizer_config = BuildingSizerRequest.from_json(building_sizer_request.simulation_config)  # type: ignore
            building_sizer_iterations.append(building_sizer_config)
        print(f"Interim results: {building_sizer_result.result}")
        all_ratings += (
            f"{list(get_ratings_of_generation(building_sizer_config).values())}\n"
        )
        # store the ratings of this generation
        generation = get_ratings_of_generation(building_sizer_config)
        generations.append(generation)
        all_ratings_list.append(get_ratings(generation.values()))
        for bs_config, kpis in generation.items():
            print(minimize_config(bs_config), " - ", get_rating(kpis))
            print("---")

    print("Finished")
    print(all_ratings)
    plot_ratings(all_ratings_list)

    create_table(generations)


def create_table(generations):
    data: dict = {}
    for iteration, generation in enumerate(generations):
        for config, kpi in generation.items():
            config = minimize_config(config)
            d_config = json.loads(config)
            d_kpi = json.loads(kpi)
            d_total = dict(d_config, **d_kpi)
            d_total["iteration"] = iteration
            for name, value in d_total.items():
                if name not in data:
                    data[name] = []
                data[name].append(value)

    df = pd.DataFrame.from_dict(data)
    print(df)
    df.to_csv("./building_sizer_results.csv")


if __name__ == "__main__":
    main()
