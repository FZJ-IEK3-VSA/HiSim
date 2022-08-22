"""Requests a load profile that is generated using HiSim"""

import json
from random import random
from typing import Any, Iterable, Optional, Tuple
from utspclient import result_file_filters, client  # type: ignore
from utspclient.datastructures import CalculationStatus, TimeSeriesRequest  # type: ignore

# Define URL and API key
URL = "http://localhost:443/api/v1/profilerequest"
API_KEY = ""


def send_hisim_requests(
    hisim_configs: Iterable[str],
) -> Iterable[TimeSeriesRequest]:
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
        reply = client.send_request(URL, request, API_KEY)
        assert reply.status in [
            CalculationStatus.CALCULATIONSTARTED,
            CalculationStatus.INCALCULATION,
            CalculationStatus.INDATABASE,
        ]
    return requests


def send_building_sizer_request(hisim_requests: Iterable[TimeSeriesRequest]) -> str:
    """
    Sends the request for the next building_sizer iteration to the UTSP, including the previously sent hisim requests
    """
    optimizer_config = {"previous requests": hisim_requests}
    optimizer_config_json = json.dumps(optimizer_config)
    request = TimeSeriesRequest(optimizer_config_json, "building_sizer")
    client.send_request(URL, request, API_KEY)
    return optimizer_config_json


def get_results_from_previous_requests(previous_requests: Iterable[TimeSeriesRequest]):
    """
    Collects the results from the HiSim requests sent in the previous iteration
    """
    return [
        client.request_time_series_and_wait_for_delivery(URL, request, API_KEY)
        for request in previous_requests
    ]


def trigger_next_iteration(hisim_configs: Iterable[str]) -> str:
    """
    Sends the specified HiSim requests to the UTSP, and afterwards sends the request for the next building sizer iteration.

    :param hisim_configs: the requisite HiSim requests started by the last iteration
    :type hisim_configs: Iterable[str]
    :return: the building_sizer config for the next iteration, or None if the calculation is finished
    :rtype: str
    """
    # Send the new requests to the UTSP
    hisim_requests = send_hisim_requests(hisim_configs)
    # Send a new building_sizer request to trigger the next optimization iteration. This must be done after sending the
    # requisite hisim requests to guarantee that the UTSP will not be blocked.
    return send_building_sizer_request(hisim_requests)


def building_sizer_iteration(
    previous_requests: Iterable[TimeSeriesRequest],
) -> Tuple[Optional[str], Any]:
    results = get_results_from_previous_requests(previous_requests)

    # TODO: termination condition; exit, when the overall calculation is over
    if random() > 0.7:
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

    return trigger_next_iteration(hisim_configs), "my interim results"


def main():
    # Read the request file
    input_path = "/input/request.json"
    with open(input_path) as input_file:
        request = json.load(input_file)
    previous_requests = request.get("previous requests")
    # Check if there are hisim requests from previous iterations
    if previous_requests:
        requests = [TimeSeriesRequest.from_json(r) for r in request]
        # Execute one optimization iteration
        next_iteration_str, result = building_sizer_iteration(requests)
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
        next_iteration_str = trigger_next_iteration(initial_hisim_configs)
        result = None

    # Create result file specifying whether a further iteration was triggered
    building_sizer_status = {
        "finished": next_iteration_str is not None,
        "subsequent request": next_iteration_str,
        "results": result,
    }
    with open("/results/status.json", "w+") as result_file:
        json.dump(building_sizer_status, result_file)


if __name__ == "__main__":
    main()
