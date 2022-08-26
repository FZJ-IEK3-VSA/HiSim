"""Sends a building sizer request to the UTSP and waits until the calculation is finished."""

import json
import random
import string
from utspclient import client
from utspclient.datastructures import TimeSeriesRequest

from building_sizer import BuildingSizerRequest, BuildingSizerResult

# Define URL and API key for the UTSP server
URL = "http://192.168.178.21:443/api/v1/profilerequest"
API_KEY = ""

# Create a simulation configuration
initial_request = BuildingSizerRequest(URL, API_KEY, 4)
building_sizer_config = f"""{{
    "url": {URL},
    "api_key": {API_KEY}
}}"""

building_sizer_config = initial_request.to_json()  # type: ignore


# For testing: create a random id to enforce recalculation for each request
guid = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


previous_iterations = {building_sizer_config}
finished = False
while not finished:
    # Create the request object
    request = TimeSeriesRequest(building_sizer_config, "building_sizer", guid=guid)
    # Wait until the request finishes and the results are delivered
    result = client.request_time_series_and_wait_for_delivery(URL, request, API_KEY)
    # Get the content of the result file created by the Building Sizer
    status_json = result.data["status.json"].decode()
    building_sizer_result = BuildingSizerResult.from_json(status_json)  # type: ignore
    # Check if this was the final iteration and the building sizer is finished
    finished = building_sizer_result.finished
    # Get the building sizer configuration for the next request
    building_sizer_config = building_sizer_result.subsequent_request
    # Loop detection: check if the same building sizer request has been encountered before (that would result in an endless loop)
    if building_sizer_config in previous_iterations:
        raise RuntimeError(
            f"Detected a loop: the following building sizer request has already been sent before.\n{building_sizer_config}"
        )
    previous_iterations.add(building_sizer_config)
    print(f"Interim results: {building_sizer_result.result}")

print("Finished")
