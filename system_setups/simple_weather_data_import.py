"""Simple function for weather data request."""

import datetime
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
LATITUDE: float = 50.775
LONGITUDE: float = 6.083

START_DATE = datetime.datetime(year=2023, month=1, day=1, hour=0, minute=0, second=0)
END_DATE = datetime.datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)

input_directory = utils.hisim_inputs

weather_data = WeatherDataImport(
    start_date=START_DATE,
    end_date=END_DATE,
    location=LOCATION,
    latitude=LATITUDE,
    longitude=LONGITUDE,
    path_input_folder=input_directory,
    distance_weather_stations=30,
    weather_data_source=WeatherDataSourceEnum.DWD_10MIN,
)
