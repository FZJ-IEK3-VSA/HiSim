"""Sends a building sizer request to the UTSP and waits until the calculation is finished."""

import random
import string
from typing import Dict, List

from building_sizer import building_sizer_algorithm
from building_sizer.building_sizer_algorithm import BuildingSizerRequest, BuildingSizerResult
from utspclient import client  # type: ignore
from utspclient.datastructures import TimeSeriesRequest  # type: ignore

# Define URL and API key for the UTSP server
URL = "http://134.94.131.167:443/api/v1/profilerequest"
API_KEY = ""


def get_ratings_of_generation(
    building_sizer_config: BuildingSizerRequest,
) -> Dict[str, float]:
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
        config: building_sizer_algorithm.get_kpi_from_csv(
            result.data["KPIs.csv"].decode()
        )
        for config, result in hisim_results.items()
    }
    return ratings


def main():
    # For testing: create a random id to enforce recalculation for each request.
    # For production use, this can be left out.
    guid = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))

    # Define the number of iterations as a dummy termination condition. Can be removed if not needed anymore.
    num_iterations = 4

    # Create an initial simulation configuration for the building sizer
    initial_building_sizer_config = BuildingSizerRequest(URL, API_KEY, num_iterations)
    building_sizer_config_json = initial_building_sizer_config.to_json()  # type: ignore
    # Create the initial building sizer request
    building_sizer_request = TimeSeriesRequest(
        building_sizer_config_json, "building_sizer-0.1.9", guid=guid
    )

    # Store the hash of each request in a set for loop detection
    previous_iterations = {building_sizer_request.get_hash()}

    # Store all iterations of building sizer requests in order
    building_sizer_iterations: List[BuildingSizerRequest] = []
    finished = False
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
            building_sizer_config: BuildingSizerRequest = (
                BuildingSizerRequest.from_json(building_sizer_request.simulation_config)
            )
            building_sizer_iterations.append(building_sizer_config)
        print(f"Interim results: {building_sizer_result.result}")
        print(
            f"Generation ratings: {list(get_ratings_of_generation(building_sizer_config).values())}"
        )

    print("Finished")


if __name__ == "__main__":
    main()
