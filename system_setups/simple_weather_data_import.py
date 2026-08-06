"""Simple function for weather data request."""

from typing import Optional

import datetime

from hisim.simulator import SimulationParameters, Simulator
from hisim.components.weather_data_import import WeatherDataImport
from hisim.components.weather import WeatherDataSourceEnum
from hisim import utils

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [" Jonas Hoppe "]
__license__ = ""
__version__ = ""
__maintainer__ = "  "
__email__ = ""
__status__ = ""


LOCATION: str = "Aachen"
LATITUDE_IN_DEGREES: float = 50.775  # decimal degrees (geographic latitude)
LONGITUDE_IN_DEGREES: float = 6.083  # decimal degrees (geographic longitude)

# Search radius for nearby DWD weather stations, in kilometers (km).
# WeatherDataImport.filter_by_distance uses unit="km".
DISTANCE_WEATHER_STATIONS_KM: float = 30.0  # kilometers

START_DATE = datetime.datetime(year=2023, month=1, day=1, hour=0, minute=0, second=0)
END_DATE = datetime.datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)

weather_input_directory = utils.hisim_inputs


def setup_function(
    my_sim: Simulator,
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> WeatherDataImport:
    """Construct the :class:`WeatherDataImport` and fetch the weather data.

    Keeps the configuration constants at module scope (see above) while
    performing the actual data request only when this setup function is called,
    instead of as an import-time side effect. The constructed
    :class:`WeatherDataImport` instance (whose ``csv_path`` and
    ``weather_data_source`` attributes describe the fetched data) is returned so
    that callers that previously relied on the module-level
    ``weather_data_import`` global can obtain it from the return value instead.

    ``my_sim`` and ``my_simulation_parameters`` are required by the HiSim
    setup-function calling convention (``hisim_main.initialize_from_python``
    invokes every setup module as ``setup_function(my_sim,
    my_simulation_parameters)``) but are intentionally unused here: this module
    only *pre-fetches* weather data into the input folder. ``WeatherDataImport``
    is a standalone data-fetching utility, not a simulation ``Component``, so it
    is not registered with ``my_sim`` -- mirroring
    ``basic_household_with_weather_data_request.py``, which likewise constructs a
    ``WeatherDataImport`` without calling ``my_sim.add_component``. The
    ``Optional`` default on ``my_simulation_parameters`` matches the sibling
    weather-data setup modules.
    """
    # my_sim and my_simulation_parameters are accepted solely to satisfy the
    # setup-function signature; see the docstring for why they are unused.
    _ = (my_sim, my_simulation_parameters)
    weather_data_import = WeatherDataImport(
        start_date=START_DATE,
        end_date=END_DATE,
        location=LOCATION,
        latitude=LATITUDE_IN_DEGREES,
        longitude=LONGITUDE_IN_DEGREES,
        path_input_folder=weather_input_directory,
        distance_weather_stations=DISTANCE_WEATHER_STATIONS_KM,
        weather_data_source=WeatherDataSourceEnum.DWD_10MIN,
    )
    return weather_data_import
