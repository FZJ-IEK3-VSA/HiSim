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


location: str = "Aachen"
latitude: float = 50.775
longitude: float = 6.083

start_date = datetime.datetime(year=2023, month=1, day=1, hour=0, minute=0, second=0)
end_date = datetime.datetime(year=2024, month=1, day=1, hour=0, minute=0, second=0)

input_directory = utils.hisim_inputs

weather_data = WeatherDataImport(
    start_date=start_date,
    end_date=end_date,
    location=location,
    latitude=latitude,
    longitude=longitude,
    path_input_folder=input_directory,
    distance_weather_stations=30,
    weather_data_source=WeatherDataSourceEnum.DWD_10MIN,
)
