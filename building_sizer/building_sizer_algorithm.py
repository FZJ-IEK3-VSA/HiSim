"""
Building Sizer for use as a provider in the UTSP.
The Buildings Sizer works iteratively. In each iteration, the results of some HiSim calculations are processed. Depending on these the
next HiSim configurations that need to be calculated are determined and sent as requests to the UTSP. Afterwards, a new Building Sizer request
is sent to the UTSP for the next iteration. This Building Sizer request contains all previously sent HiSim requests so it can obtain the results
of these requests and work with them.
To allow the client who sent the initial Building Sizer request to follow the separate Building Sizer iterations, each iteration returns the request
for the next Building Sizer iteration as a result to the UTSP (and therey also to the client).
"""

import dataclasses
from typing import Any, Dict, List, Optional, Tuple

import dataclasses_json
from utspclient import client  # type: ignore
from utspclient.datastructures import (
    CalculationStatus,
    ResultDelivery,
    ResultFileRequirement,
    TimeSeriesRequest,
)

from building_sizer import evolutionary_algorithm as evo_alg
from building_sizer import system_config
from building_sizer import kpi_config


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class BuildingSizerRequest:
    """
    A request object for the building sizer. Contains all necessary data for
    a single building sizer iteration. Can be used to create the request object
    for the subsequent iteration.
    """

    url: str
    api_key: str = ""
    building_sizer_version: str = ""
    hisim_version: str = ""
    remaining_iterations: int = 3
    requisite_requests: List[TimeSeriesRequest] = dataclasses.field(
        default_factory=list
    )

    def create_subsequent_request(
        self, hisim_requests: List[TimeSeriesRequest]
    ) -> "BuildingSizerRequest":
        """
        Creates a request object for the next building sizer iteration.
        Copies all properties except for the requisite hisim requests and remaining_iterations.

        :param hisim_requests: the hisim requests that are required for the next iteration
        :type hisim_requests: List[TimeSeriesRequest]
        :return: the request object for the next iteration
        :rtype: BuildingSizerRequest
        """
        return BuildingSizerRequest(
            self.url,
            self.api_key,
            self.building_sizer_version,
            self.hisim_version,
            self.remaining_iterations - 1,
            hisim_requests,
        )


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class BuildingSizerResult:
    """
    Result object of the building sizer. Contains all results of a single building
    sizer iteration. The finished flag indicates whether it was the final iteration.
    If not, the building sizer request for the subsequent iteration is contained in
    the property subsequent_request.
    """

    finished: bool
    subsequent_request: Optional[TimeSeriesRequest] = None
    result: Any = None


def send_hisim_requests(
    hisim_configs: List[str], url: str, api_key: str = "", hisim_version: str = ""
) -> List[TimeSeriesRequest]:
    """
    Creates and sends one time series request to the utsp for every passed hisim configuration
    """
    # Determine the provider name for the hisim request
    provider_name = "hisim"
    if hisim_version:
        # If a hisim version is specified, use that version
        provider_name += f"-{hisim_version}"
    # Prepare the time series requests
    requests = [
        TimeSeriesRequest(
            sim_config,
            provider_name,
            required_result_files={"kpi_config.json": ResultFileRequirement.REQUIRED},
        )
        for sim_config in hisim_configs
    ]
    # Send the requests
    for request in requests:
        reply = client.send_request(url, request, api_key)
        assert reply.status not in [
            CalculationStatus.CALCULATIONFAILED,
            CalculationStatus.UNKNOWN,
        ], f"Sending the following hisim request returned {reply.status}:\n{request.simulation_config}"
    return requests


def send_building_sizer_request(
    request: BuildingSizerRequest, hisim_requests: List[TimeSeriesRequest]
) -> TimeSeriesRequest:
    """
    Sends the request for the next building_sizer iteration to the UTSP, including the previously sent hisim requests
    """
    subsequent_request_config = request.create_subsequent_request(hisim_requests)
    config_json: str = subsequent_request_config.to_json()  # type: ignore
    # Determine the provider name for the building sizer
    provider_name = "building_sizer"
    if request.building_sizer_version:
        # If a building sizer version is specified, use that version
        provider_name += f"-{request.building_sizer_version}"
    next_request = TimeSeriesRequest(config_json, provider_name)
    client.send_request(request.url, next_request, request.api_key)
    return next_request


def get_results_from_requisite_requests(
    reqisite_requests: List[TimeSeriesRequest], url: str, api_key: str = ""
) -> Dict[str, ResultDelivery]:
    """
    Collects the results from the HiSim requests sent in the previous iteration
    """
    return {
        request.simulation_config: client.request_time_series_and_wait_for_delivery(
            url, request, api_key
        )
        for request in reqisite_requests
    }


def trigger_next_iteration(
    request: BuildingSizerRequest, hisim_configs: List[str]
) -> TimeSeriesRequest:
    """
    Sends the specified HiSim requests to the UTSP, and afterwards sends the request for the next building sizer iteration.

    :param hisim_configs: the requisite HiSim requests started by the last iteration
    :type hisim_configs: List[str]
    :return: the building sizer request for the next iteration
    :rtype: TimeSeriesRequest
    """
    # Send the new requests to the UTSP
    hisim_requests = send_hisim_requests(
        hisim_configs, request.url, request.api_key, request.hisim_version
    )
    # Send a new building_sizer request to trigger the next building sizer iteration. This must be done after sending the
    # requisite hisim requests to guarantee that the UTSP will not be blocked.
    return send_building_sizer_request(request, hisim_requests)


def decide_on_mode(
    iteration: int, boolean_iterations: int, discrete_iterations: int
) -> str:
    iteration_in_subiteration = iteration % (boolean_iterations + discrete_iterations)
    if iteration_in_subiteration > discrete_iterations:
        return "bool"
    else:
        return "discrete"


def building_sizer_iteration(
    request: BuildingSizerRequest,
) -> Tuple[Optional[TimeSeriesRequest], Any]:
    """
    Executes one iteration of the building sizer. Collects the results from all
    requisite hisim requests, selects the best individuals, generates new individuals
    using a genetic algorithm and finally sends the next hisim requests and building sizer
    request to the UTSP (if not in the last iteration).

    :param request: the request object for this iteration
    :type request: BuildingSizerRequest
    :return: the request object for the next building sizer iteration (if there is one), and
             the result of this iteration
    :rtype: Tuple[Optional[TimeSeriesRequest], Any]
    """
    pass
    results = get_results_from_requisite_requests(
        request.requisite_requests, request.url, request.api_key
    )

    boolean_iterations: int = 3
    discrete_iterations: int = 9
    r_cross: float = 0.2
    r_mut: float = 0.4
    options = system_config.SizingOptions()

    # Get the relevant result files from all requisite requests and turn them into rated individuals
    rated_individuals = []
    for sim_config_str, result in results.items():

        # Extract the rating for each HiSim config
        # TODO: check if rating works
        rating = kpi_config.get_kpi_from_json(result.data["kpi_config.json"].decode())
        system_config_instance: system_config.SystemConfig = system_config.SystemConfig.from_json(sim_config_str)  # type: ignore
        individual = system_config_instance.get_individual(
            translation=options.translation
        )
        r = system_config.RatedIndividual(individual, rating)
        rated_individuals.append(r)

    # select best individuals
    # TODO: population size as input
    population_size: int = 5
    parents = evo_alg.selection(
        rated_individuals=rated_individuals, population_size=population_size
    )

    # pass rated_individuals to genetic algorithm and receive list of new individual vectors back
    # TODO r_cross and r_mut as inputs
    # TODO boolean iterations and discrete iterations as inputs
    # TODO total iterations as input + transfer to right locations
    parent_individuals = [ri.individual for ri in parents]

    """     parent_individuals = evo_alg.complete_population(
        original_parents=parent_individuals,
        population_size=population_size,
        options=options,
    ) """

    new_individuals = evo_alg.evolution(
        parents=parent_individuals,
        r_cross=r_cross,
        r_mut=r_mut,
        mode=decide_on_mode(
            iteration=request.remaining_iterations,
            boolean_iterations=boolean_iterations,
            discrete_iterations=discrete_iterations,
        ),
        options=options,
    )

    # combine combine parents and children
    new_individuals.extend(parent_individuals)

    # delete duplicates
    new_individuals = evo_alg.unique(new_individuals)

    # TODO: termination condition; exit, when the overall calculation is over
    if request.remaining_iterations == 0:
        return None, "my final results"

    # convert individuals back to HiSim SystemConfigs
    hisim_configs = []
    for individual in new_individuals:
        system_config_instance = system_config.create_from_individual(
            individual=individual, translation=options.translation
        )
        hisim_configs.append(system_config_instance.to_json())  # type: ignore

    # trigger the next iteration with the new hisim configurations
    next_request = trigger_next_iteration(request, hisim_configs)
    # return the building sizer request for the next iteration, and the result of this iteration
    return next_request, f"my interim results ({request.remaining_iterations})"


def main():
    # Read the request file
    input_path = "/input/request.json"
    with open(input_path) as input_file:
        request_json = input_file.read()
    request: BuildingSizerRequest = BuildingSizerRequest.from_json(request_json)  # type: ignore
    # Check if there are hisim requests from previous iterations
    if request.requisite_requests:
        # Execute one building sizer iteration
        next_request, result = building_sizer_iteration(request)
    else:
        # TODO: first iteration; initialize algorithm and specify initial hisim requests
        populations_size: int = 5  # number of individuals to be created
        options = system_config.SizingOptions()
        initial_hisim_configs = []  # initialize system_configs
        for i in range(populations_size):  # create five individuals in population
            individual = system_config.Individual()
            individual.create_random_individual(options=options)
            initial_hisim_configs.append(
                system_config.create_from_individual(
                    individual=individual, translation=options.translation
                ).to_json()  # type: ignore
            )

        next_request = trigger_next_iteration(request, initial_hisim_configs)
        result = "My first iteration result"

    # Create result file specifying whether a further iteration was triggered
    finished = next_request is None
    building_sizer_result = BuildingSizerResult(finished, next_request, result)
    building_sizer_result_json = building_sizer_result.to_json()  # type: ignore

    with open("/results/status.json", "w+") as result_file:
        result_file.write(building_sizer_result_json)


if __name__ == "__main__":
    main()
