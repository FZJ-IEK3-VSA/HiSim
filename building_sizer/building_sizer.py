"""Requests a load profile that is generated using HiSim"""

import dataclasses
import json
from typing import Any, List, Optional, Tuple

import dataclasses_json
from utspclient import client, result_file_filters  # type: ignore
from utspclient.datastructures import CalculationStatus, TimeSeriesRequest  # type: ignore


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class BuildingSizerRequest:
    url: str
    api_key: str = ""
    remaining_iterations: int = 3
    requisite_requests: List[TimeSeriesRequest] = dataclasses.field(
        default_factory=list
    )


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
            required_result_files={
                result_file_filters.HiSimFilters.ELECTRICITY_SMART_1
            },
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
) -> str:
    """
    Sends the request for the next building_sizer iteration to the UTSP, including the previously sent hisim requests
    """
    subsequent_request_config = BuildingSizerRequest(
        request.url, request.api_key, request.remaining_iterations - 1, hisim_requests
    )
    config_json: str = subsequent_request_config.to_json()  # type: ignore
    next_request = TimeSeriesRequest(config_json, "building_sizer")
    client.send_request(request.url, next_request, request.api_key)
    return config_json


def get_results_from_requisite_requests(
    reqisite_requests: List[TimeSeriesRequest], url: str, api_key: str = ""
):
    """
    Collects the results from the HiSim requests sent in the previous iteration
    """
    return [
        client.request_time_series_and_wait_for_delivery(url, request, api_key)
        for request in reqisite_requests
    ]


def trigger_next_iteration(
    request: BuildingSizerRequest, hisim_configs: List[str]
) -> str:
    """
    Sends the specified HiSim requests to the UTSP, and afterwards sends the request for the next building sizer iteration.

    :param hisim_configs: the requisite HiSim requests started by the last iteration
    :type hisim_configs: List[str]
    :return: the building_sizer config for the next iteration, or None if the calculation is finished
    :rtype: str
    """
    # Send the new requests to the UTSP
    hisim_requests = send_hisim_requests(hisim_configs, request.url, request.api_key)
    # Send a new building_sizer request to trigger the next building sizer iteration. This must be done after sending the
    # requisite hisim requests to guarantee that the UTSP will not be blocked.
    return send_building_sizer_request(request, hisim_requests)


def building_sizer_iteration(
    request: BuildingSizerRequest,
) -> Tuple[Optional[str], Any]:
    results = get_results_from_requisite_requests(
        request.requisite_requests, request.url, request.api_key
    )

    # TODO: termination condition; exit, when the overall calculation is over
    if request.remaining_iterations == 0:
        return None, "my final results"

    # TODO: do something here to determine which hisim simulation configs should be calculated next
    hisim_configs = [
        """{
        "predictive": false,
        "prediction_horizon": 86400,
        "pv_included": false,
        "smart_devices_included": true,
        "boiler_included": "electricity",
        "heatpump_included": false,
        "battery_included": false,
        "chp_included": false
    }"""
    ]

    return (
        trigger_next_iteration(request, hisim_configs),
        f"my interim results ({request.remaining_iterations})",
    )


def main():
    # Read the request file
    input_path = "/input/request.json"
    with open(input_path) as input_file:
        request_json = input_file.read()
    request: BuildingSizerRequest = BuildingSizerRequest.from_json(request_json)  # type: ignore
    # Check if there are hisim requests from previous iterations
    if request.requisite_requests:
        # Execute one building sizer iteration
        next_iteration_str, result = building_sizer_iteration(request)
    else:
        # TODO: first iteration; initialize algorithm and specify initial hisim requests
        initial_hisim_configs = [
            """{
        "predictive": false,
        "prediction_horizon": 86400,
        "pv_included": false,
        "smart_devices_included": true,
        "boiler_included": "electricity",
        "heatpump_included": false,
        "battery_included": false,
        "chp_included": false
    }"""
        ]
        next_iteration_str = trigger_next_iteration(request, initial_hisim_configs)
        result = "My first iteration result"

    # Create result file specifying whether a further iteration was triggered
    building_sizer_status = {
        "finished": next_iteration_str is None,
        "subsequent request": next_iteration_str,
        "results": result,
    }
    with open("/results/status.json", "w+") as result_file:
        json.dump(building_sizer_status, result_file)


if __name__ == "__main__":
    main()
