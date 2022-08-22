"""Sends a building sizer request to the UTSP and waits until the calculation is finished."""

import json
from utspclient import client
from utspclient.datastructures import TimeSeriesRequest

# Define URL and API key for the UTSP server
URL = "http://localhost:443/api/v1/profilerequest"
API_KEY = ""

# Create a simulation configuration
building_sizer_config = """{
}"""

# Call time series request function
finished = False
while not finished:
    request = TimeSeriesRequest(building_sizer_config, "hisim")
    result = client.request_time_series_and_wait_for_delivery(URL, request, API_KEY)
    status_json = result.data["status.json"].decode()
    status = json.loads(status_json)
    finished = status["finished"]
    building_sizer_config = status["subsequent request"]
    print(f"Interim results: {status['results']}")

print("Finished")
