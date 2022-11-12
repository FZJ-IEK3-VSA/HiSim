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
import random as ra
from typing import Any, List, Optional, Tuple

import dataclasses_json
from utspclient import client  # type: ignore
from utspclient.datastructures import (CalculationStatus,  # type: ignore
                                       ResultDelivery, ResultFileRequirement,
                                       TimeSeriesRequest)

import system_config


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class BuildingSizerRequest:
    url: str
    api_key: str = ""
    remaining_iterations: int = 3
    requisite_requests: List[TimeSeriesRequest] = dataclasses.field(
        default_factory=list
    )


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class BuildingSizerResult:
    finished: bool
    subsequent_request: Optional[TimeSeriesRequest] = None
    result: Any = None


def send_hisim_requests(
    hisim_configs: List[str], url: str, api_key: str = ""
) -> List[TimeSeriesRequest]:
    """
    Creates and sends one time series request to the utsp for every passed hisim configuration
    """
    # Prepare the time series requests
    requests = [
        TimeSeriesRequest(
            sim_config,
            "hisim",
            required_result_files={"KPIs.csv": ResultFileRequirement.REQUIRED},
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
    subsequent_request_config = BuildingSizerRequest(
        request.url, request.api_key, request.remaining_iterations - 1, hisim_requests
    )
    config_json: str = subsequent_request_config.to_json()  # type: ignore
    next_request = TimeSeriesRequest(config_json, "building_sizer")
    client.send_request(request.url, next_request, request.api_key)
    return next_request


def get_results_from_requisite_requests(
    reqisite_requests: List[TimeSeriesRequest], url: str, api_key: str = ""
) -> List[ResultDelivery]:
    """
    Collects the results from the HiSim requests sent in the previous iteration
    """
    return [
        client.request_time_series_and_wait_for_delivery(url, request, api_key)
        for request in reqisite_requests
    ]


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
    hisim_requests = send_hisim_requests(hisim_configs, request.url, request.api_key)
    # Send a new building_sizer request to trigger the next building sizer iteration. This must be done after sending the
    # requisite hisim requests to guarantee that the UTSP will not be blocked.
    return send_building_sizer_request(request, hisim_requests)


def building_sizer_iteration(
    request: BuildingSizerRequest,
) -> Tuple[Optional[TimeSeriesRequest], Any]:
    results = get_results_from_requisite_requests(
        request.requisite_requests, request.url, request.api_key
    )
    # Get the relevant result files from all requisite requests
    for result in results:
        result_file = result.data["KPIs.csv"].decode()

    # TODO: termination condition; exit, when the overall calculation is over
    if request.remaining_iterations == 0:
        return None, "my final results"

    # TODO: do something here to determine which hisim simulation configs should be calculated next
    hisim_config = system_config.SystemConfig()
    hisim_config.utsp_connect = True
    hisim_config.url = request.url
    hisim_config.api_key = request.api_key
    
    hisim_config.battery_included = True
    hisim_config.battery_capacity = ra.randint(1,10)
    
    hisim_configs = [hisim_config.to_json()]  # type: ignore

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
        initial_hisim_configs = [
            system_config.SystemConfig().to_json()  # type: ignore
        ]
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
