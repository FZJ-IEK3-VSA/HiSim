"""Import weather data from dwd and era5."""

import os
import urllib.request as url
import datetime
import pandas as pd
from wetterdienst import Settings
from wetterdienst.provider.dwd.observation import DwdObservationRequest
import numpy as np
import cdsapi
import xarray as xr


__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [" Jonas Hoppe "]
__license__ = ""
__version__ = ""
__maintainer__ = "  "
__email__ = ""
__status__ = ""


class WeatherDataImport:
    """Import class for weather data from dwd and era5."""

    def __init__(
        self,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        location: str,
        latitude: float,
        longitude: float,
        distance_weather_stations: float,
        path_input_folder: str,
        weather_data_source: str,
    ):
        """Initializes Data Request."""
        self.location = location
        self.latitude = latitude
        self.longitude = longitude
        self.distance_weather_stations = distance_weather_stations
        self.path_input_folder = path_input_folder
        self.weather_data_source = weather_data_source

        self.long_round = round(self.longitude, 2)
        self.lat_round = round(self.latitude, 2)

        location_data = [
            {
                "location": self.location,
                "longitude": self.longitude,
                "latitude": self.latitude,
            }
        ]
        self.location_df = pd.DataFrame(location_data)

        start_year = start_date.year

        if end_date.year == start_date.year:
            end_year = end_date.year + 1
        elif end_date.year > start_date.year:
            end_year = end_date.year
        else:
            raise KeyError("Check start and end date of weather data request!")

        self.start_date_for_weather_data = datetime.datetime(
            year=start_year, month=1, day=1, hour=0, minute=0, second=0
        )
        self.end_date_for_weather_data = datetime.datetime(year=end_year, month=1, day=1, hour=0, minute=0, second=0)

        print(
            f"Weatherdata request from {self.start_date_for_weather_data} to {self.end_date_for_weather_data} in {weather_data_source} database."
        )

        self.csv_path: str

        if self.weather_data_source == "DWD_10MIN":
            self.dwd_10min_request()
        elif self.weather_data_source == "ERA5":
            self.era5_request()
        else:
            raise KeyError("Unknown data source. Only DWD_10MIN and ERA5.")

    def safe_as_csv(self, weather_df, csv_path):
        """Safe weather data as .csv in given path."""
        self.location_df.to_csv(csv_path, index=False)

        weather_df.to_csv(csv_path, mode="a", index=False)

        print(f"Weather data saved in:  {csv_path}")

    def dwd_10min_request(
        self,
    ):
        """Data request from "Deutschen Wetterdienst".

        Based on:
        https://wetterdienst.readthedocs.io/en/latest/data/coverage/dwd/observation/minute_10.html

        Weighted average based on distance --> http://www.hydrology.uni-freiburg.de/abschluss/Niederberger_J_2000_DA.pdf .
        """

        csv_name = f"dwd_10min_{self.start_date_for_weather_data.year}-{self.end_date_for_weather_data.year}_{self.location}_{self.long_round}_{self.lat_round}.csv"
        speicherpfad = os.path.join(self.path_input_folder, "weather", "dwd_10min_data")

        # Abfrage, ob csv schon vorhanden

        if not os.path.exists(speicherpfad):
            os.makedirs(speicherpfad)
            print(f"Directory {speicherpfad} created.")

        csv_path = os.path.join(speicherpfad, csv_name)

        if os.path.isfile(csv_path):
            print(f"CSV file  {csv_name} under  {speicherpfad} already exists.")
        else:
            print(f"Weather data is loaded from DWD and saved under {csv_path}.")

            settings = Settings(  # default
                ts_shape="long",  # tidy data
                ts_humanize=True,  # humanized parameters
                ts_si_units=False,  # convert values to SI units
                ts_interpolation_use_nearby_station_distance=40,
            )

            request = DwdObservationRequest(
                parameter=["tt_10", "pp_10", "dd_10", "ff_10", "ds_10", "gs_10"],
                resolution="10_minutes",
                start_date=str(self.start_date_for_weather_data),
                end_date=str(self.end_date_for_weather_data),
                settings=settings,
            ).filter_by_distance(
                latlon=(self.latitude, self.longitude),
                distance=self.distance_weather_stations,
                unit="km",
            )

            stations = request.df

            if len(stations) == 0:
                raise KeyError("No weather station found in the area. Increase radius for station search.")
            if len(stations) < 3:
                raise KeyError(
                    "Only 2 weather stations found in the area. Minimum number 3! Increase radius for station search."
                )

            stations = stations.to_pandas()
            distance_location_to_stations_df = pd.DataFrame(
                {
                    "longitude": stations["longitude"],
                    "latitude": stations["latitude"],
                    "distance": stations["distance"],
                }
            )

            # Calculating weight based on inverse distance
            radial_distance = np.sqrt(
                np.square(self.longitude - distance_location_to_stations_df["longitude"])
                + np.square(self.latitude - distance_location_to_stations_df["latitude"])
            )
            weights = 1 / (np.sqrt(radial_distance))
            weights = pd.DataFrame({"weights": weights})

            values = request.values.all().df
            values = values.to_pandas()

            date_df = pd.DataFrame(columns=["date"])
            date_df = pd.to_datetime(values["date"], utc=True)
            date_df = pd.DataFrame({"date": date_df})
            date_df = date_df["date"].drop_duplicates()

            time_df = pd.DataFrame(columns=["year", "month", "day", "hour", "minute"])

            time_df["year"] = date_df.dt.year
            time_df["month"] = date_df.dt.month
            time_df["day"] = date_df.dt.day
            time_df["hour"] = date_df.dt.hour
            time_df["minute"] = date_df.dt.minute

            missing_time_df = pd.DataFrame()
            if len(time_df) == 60 * 24 * 365 / 10 + 1:
                print("All weather data for a year is available.")
            else:
                if 60 * 24 * 365 / 10 - len(time_df) + 1 < 50:
                    print(f"Note: {60 * 24 * 365 /10 - len(time_df)+1} entries for an entire year are missing.")

                    time_index_full = pd.to_datetime(time_df[["year", "month", "day", "hour", "minute"]])

                    missing_times = pd.date_range(
                        start=time_index_full.min(),
                        end=time_index_full.max(),
                        freq="1H",
                    ).difference(time_index_full)

                    missing_times = pd.to_datetime(missing_times)

                    missing_times_df_year = pd.DataFrame({"year": missing_times.year})
                    missing_times_df_month = pd.DataFrame({"month": missing_times.month})
                    missing_times_df_day = pd.DataFrame({"day": missing_times.day})
                    missing_times_df_hour = pd.DataFrame({"hour": missing_times.hour})
                    missing_times_df_minute = pd.DataFrame({"minute": missing_times.minute})

                    missing_time_df = pd.concat(
                        [
                            missing_times_df_year["year"],
                            missing_times_df_month["month"],
                            missing_times_df_day["day"],
                            missing_times_df_hour["hour"],
                            missing_times_df_minute["minute"],
                        ],
                        axis=1,
                    )

                    print("Missing timeseries:")
                    print(missing_times)

                else:
                    raise KeyError(
                        f"Note: {60 * 24 * 365 /10 - len(time_df)+1} entries for an entire year are missing. Too much!"
                    )

            temperature_dwd_df = (
                values[values["parameter"] == "temperature_air_mean_200"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            temperature_dwd_df = temperature_dwd_df.rename(columns={"value": "temperature"})
            temperature_dwd_df["temperature"] = temperature_dwd_df["temperature"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            temperatur_air_list = []
            temperature_air_valid_data = pd.DataFrame()
            for i in range(len(temperature_dwd_df)):
                temperature_air_valid_data = pd.DataFrame(
                    {"temperature": temperature_dwd_df["temperature"][i]}
                ).dropna()

                if temperature_air_valid_data.empty:
                    temperature_location = np.nan
                else:
                    temperature_location = sum(
                        temperature_air_valid_data["temperature"] * weights["weights"][temperature_air_valid_data.index]
                    ) / sum(weights["weights"][temperature_air_valid_data.index])

                temperatur_air_list.append(temperature_location)

            temperature_air_df = pd.DataFrame(temperatur_air_list, columns=["temperature"])
            temperature_air_df = temperature_air_df.interpolate(method="linear", limit_direction="backward")

            pressure_dwd_df = (
                values[values["parameter"] == "pressure_air_site"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            pressure_dwd_df = pressure_dwd_df.rename(columns={"value": "pressure"})
            pressure_dwd_df["pressure"] = pressure_dwd_df["pressure"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            pressure_list = []
            pressure_valid_data = pd.DataFrame()
            for i in range(len(temperature_dwd_df)):
                pressure_valid_data = pd.DataFrame({"pressure": pressure_dwd_df["pressure"][i]}).dropna()

                if pressure_valid_data.empty:
                    pressure_location = np.nan
                else:
                    pressure_location = sum(
                        pressure_valid_data["pressure"] * weights["weights"][pressure_valid_data.index]
                    ) / sum(weights["weights"][pressure_valid_data.index])

                pressure_list.append(pressure_location)

            pressure_df = pd.DataFrame(pressure_list, columns=["pressure"])
            pressure_df = pressure_df.interpolate(method="linear", limit_direction="backward")

            wind_direction_dwd_df = (
                values[values["parameter"] == "wind_direction"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            wind_direction_dwd_df = wind_direction_dwd_df.rename(columns={"value": "wind_direction"})
            wind_direction_dwd_df["wind_direction"] = wind_direction_dwd_df["wind_direction"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            wind_direction_list = []
            wind_direction_valid_data = pd.DataFrame()
            for i in range(len(wind_direction_dwd_df)):
                wind_direction_valid_data = pd.DataFrame(
                    {"wind_direction": wind_direction_dwd_df["wind_direction"][i]}
                ).dropna()

                if wind_direction_valid_data.empty:
                    wind_direction_location = np.nan
                else:
                    wind_direction_location = sum(
                        wind_direction_valid_data["wind_direction"]
                        * weights["weights"][wind_direction_valid_data.index]
                    ) / sum(weights["weights"][wind_direction_valid_data.index])

                wind_direction_list.append(wind_direction_location)

            wind_direction_df = pd.DataFrame(wind_direction_list, columns=["wind_direction"])
            wind_direction_df = wind_direction_df.interpolate(method="linear", limit_direction="backward")

            wind_speed_dwd_df = (
                values[values["parameter"] == "wind_speed"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            wind_speed_dwd_df = wind_speed_dwd_df.rename(columns={"value": "wind_speed"})
            wind_speed_dwd_df["wind_speed"] = wind_speed_dwd_df["wind_speed"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            wind_speed_list = []
            wind_speed_valid_data = pd.DataFrame()
            for i in range(len(wind_speed_dwd_df)):
                wind_speed_valid_data = pd.DataFrame({"wind_speed": wind_speed_dwd_df["wind_speed"][i]}).dropna()

                if wind_speed_valid_data.empty:
                    wind_speed_location = np.nan
                else:
                    wind_speed_location = sum(
                        wind_speed_valid_data["wind_speed"] * weights["weights"][wind_speed_valid_data.index]
                    ) / sum(weights["weights"][wind_speed_valid_data.index])

                wind_speed_list.append(wind_speed_location)

            wind_speed_df = pd.DataFrame(wind_speed_list, columns=["wind_speed"])
            wind_speed_df = wind_speed_df.interpolate(method="linear", limit_direction="backward")

            diffuse_irradiance_dwd_df = (
                values[values["parameter"] == "radiation_sky_short_wave_diffuse"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            diffuse_irradiance_dwd_df = diffuse_irradiance_dwd_df.rename(columns={"value": "diffuse_irradiance"})
            diffuse_irradiance_dwd_df["diffuse_irradiance"] = diffuse_irradiance_dwd_df["diffuse_irradiance"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            diffuse_irradiance_list = []
            diffuse_irradiance_valid_data = pd.DataFrame()
            for i in range(len(diffuse_irradiance_dwd_df)):
                diffuse_irradiance_valid_data = pd.DataFrame(
                    {"diffuse_irradiance": diffuse_irradiance_dwd_df["diffuse_irradiance"][i]}
                ).dropna()

                if diffuse_irradiance_valid_data.empty:
                    diffuse_irradiance_location_watt_per_m2 = np.nan
                else:
                    diffuse_irradiance_location_j_per_cm2 = sum(
                        diffuse_irradiance_valid_data["diffuse_irradiance"]
                        * weights["weights"][diffuse_irradiance_valid_data.index]
                    ) / sum(weights["weights"][diffuse_irradiance_valid_data.index])

                    diffuse_irradiance_location_watt_per_m2 = diffuse_irradiance_location_j_per_cm2 * 10000 / (10 * 60)

                diffuse_irradiance_list.append(diffuse_irradiance_location_watt_per_m2)

            diffuse_irradiance_df = pd.DataFrame(diffuse_irradiance_list, columns=["diffuse_irradiance"])
            diffuse_irradiance_df = diffuse_irradiance_df.interpolate(method="linear", limit_direction="backward")

            global_irradiance_dwd_df = (
                values[values["parameter"] == "radiation_global"]
                .groupby("date")["value"]
                .apply(lambda x: ", ".join(map(str, x)))
                .reset_index()
            )
            global_irradiance_dwd_df = global_irradiance_dwd_df.rename(columns={"value": "global_irradiance"})
            global_irradiance_dwd_df["global_irradiance"] = global_irradiance_dwd_df["global_irradiance"].apply(
                lambda x: [float(val) if val != "nan" else np.nan for val in x.split(", ")]
            )

            global_irradiance_list = []
            global_irradiance_valid_data = pd.DataFrame()
            for i in range(len(global_irradiance_dwd_df)):
                global_irradiance_valid_data = pd.DataFrame(
                    {"global_irradiance": global_irradiance_dwd_df["global_irradiance"][i]}
                ).dropna()

                if global_irradiance_valid_data.empty:
                    global_irradiance_location_watt_per_m2 = np.nan
                else:
                    global_irradiance_location_j_per_cm2 = sum(
                        global_irradiance_valid_data["global_irradiance"]
                        * weights["weights"][global_irradiance_valid_data.index]
                    ) / sum(weights["weights"][global_irradiance_valid_data.index])

                    global_irradiance_location_watt_per_m2 = global_irradiance_location_j_per_cm2 * 10000 / (10 * 60)

                global_irradiance_list.append(global_irradiance_location_watt_per_m2)

            global_irradiance_df = pd.DataFrame(global_irradiance_list, columns=["global_irradiance"])
            global_irradiance_df = global_irradiance_df.interpolate(method="linear", limit_direction="backward")

            weather_df = pd.DataFrame()
            weather_df = pd.concat(
                [
                    time_df,
                    temperature_air_df,  # ["temperature"],
                    pressure_df,  # ["pressure"],
                    wind_direction_df,  # ["wind_direction"],
                    wind_speed_df,  # ["wind_speed"],
                    diffuse_irradiance_df,  # ["diffuse_irradiance"],
                    global_irradiance_df,  # ["global_irradiance"],
                ],
                axis=1,
                join="outer",
            )

            if len(missing_time_df) != 0:
                # Setze den Zeitindex für df2
                weather_df_time_index = pd.to_datetime(weather_df[["year", "month", "day", "hour", "minute"]])
                time_index_full = pd.to_datetime(missing_time_df[["year", "month", "day", "hour", "minute"]])

                merged_weather_df = pd.concat(
                    [
                        missing_time_df.set_index(time_index_full),
                        weather_df.set_index(weather_df_time_index),
                    ]
                ).sort_index()

                interpolated_weather_df = merged_weather_df.interpolate()

                weather_df = interpolated_weather_df
                print("Missing times successfully added and weather data interpolated!")

            weather_df = weather_df.reset_index(drop=True)
            weather_df = weather_df.drop(weather_df.index[-1])

            if weather_df.isna().any(axis=None):
                raise KeyError(
                    "The DataFrame weather_df contains NaN values. Check data or increase radius for station search."
                )

            if weather_df.eq("nan").any().any():
                raise KeyError(
                    "The DataFrame weather_df contains NaN values. Check data or increase radius for station search."
                )

            self.safe_as_csv(weather_df, csv_path)

        self.csv_path = csv_path

    def era5_request(
        self,
    ):
        """Data request from era5.

        Based on:
        https://confluence.ecmwf.int/display/CKB/ERA5%3A+data+documentation
        https://cds.climate.copernicus.eu/cdsapp#!/dataset/reanalysis-era5-single-levels?tab=overview
        https://cds.climate.copernicus.eu/cdsapp#!/dataset/reanalysis-era5-single-levels?tab=form

        warning: Necessary to set up an account and match URL and user key to storage for retrieval.
        """

        # Check if .cdsapirc key for data request is available
        username = os.getlogin()
        user_path = os.path.join("C:\\Users", username)

        file_name = ".cdsapirc"
        file_path = os.path.join(user_path, file_name)

        if not os.path.isfile(file_path):
            raise KeyError(
                f"The config-file {file_name} for era5 data request is missing in {user_path}. "
                f"Please register: https://cds.climate.copernicus.eu/user/login "
                f"and follow the instructions for using era5."
            )

        csv_name = f"era5_1h_{self.start_date_for_weather_data.year}-{self.end_date_for_weather_data.year}_{self.location}_{self.long_round}_{self.lat_round}.csv"
        speicherpfad = os.path.join(self.path_input_folder, "weather", "era5_data")

        if not os.path.exists(speicherpfad):
            os.makedirs(speicherpfad)
            print(f"Directory {speicherpfad} created.")

        csv_path = os.path.join(speicherpfad, csv_name)

        if os.path.isfile(csv_path):
            print(f"CSV file  {csv_name} under  {speicherpfad} already exists.")
        else:
            print(f"Weather data is loaded from DWD and saved under {csv_path}.")

            cds = cdsapi.Client()

            data = cds.retrieve(
                "reanalysis-era5-single-levels",
                {
                    "product_type": "reanalysis",
                    "variable": [
                        "2m_temperature",
                        "10m_u_component_of_wind",
                        "10m_v_component_of_wind",
                        "surface_pressure",
                        "surface_solar_radiation_downwards",
                        "total_sky_direct_solar_radiation_at_surface",
                    ],
                    "year": [
                        str(self.start_date_for_weather_data.year),
                    ],
                    "month": [
                        "01",
                        "02",
                        "03",
                        "04",
                        "05",
                        "06",
                        "07",
                        "08",
                        "09",
                        "10",
                        "11",
                        "12",
                    ],
                    "day": [
                        "01",
                        "02",
                        "03",
                        "04",
                        "05",
                        "06",
                        "07",
                        "08",
                        "09",
                        "10",
                        "11",
                        "12",
                        "13",
                        "14",
                        "15",
                        "16",
                        "17",
                        "18",
                        "19",
                        "20",
                        "21",
                        "22",
                        "23",
                        "24",
                        "25",
                        "26",
                        "27",
                        "28",
                        "29",
                        "30",
                        "31",
                    ],
                    "time": [
                        "00:00",
                        "01:00",
                        "02:00",
                        "03:00",
                        "04:00",
                        "05:00",
                        "06:00",
                        "07:00",
                        "08:00",
                        "09:00",
                        "10:00",
                        "11:00",
                        "12:00",
                        "13:00",
                        "14:00",
                        "15:00",
                        "16:00",
                        "17:00",
                        "18:00",
                        "19:00",
                        "20:00",
                        "21:00",
                        "22:00",
                        "23:00",
                    ],
                    "area": [
                        self.latitude,
                        self.longitude,
                        self.latitude,
                        self.longitude,
                    ],
                    "format": "netcdf",
                },
                # 'download.nc'
            )

            with url.urlopen(data.location) as data_set:
                weather_data_set = xr.open_dataset(data_set.read())
            values = weather_data_set.to_dataframe()

            values = values.dropna()

            time_value = values.index.get_level_values("time")
            df_time = pd.DataFrame(index=time_value, columns=["data_column"])
            df_year = pd.DataFrame({"year": df_time.index.year})
            df_month = pd.DataFrame({"month": df_time.index.month})
            df_day = pd.DataFrame({"day": df_time.index.day})
            df_hour = pd.DataFrame({"hour": df_time.index.hour})
            df_minute = pd.DataFrame({"minute": df_time.index.minute})

            time_df = pd.concat(
                [
                    df_year["year"],
                    df_month["month"],
                    df_day["day"],
                    df_hour["hour"],
                    df_minute["minute"],
                ],
                axis=1,
            )

            missing_time_df = pd.DataFrame()

            if len(time_df) == 24 * 365:
                print("All weather data for a year is available.")
            else:
                if 24 * 365 - len(time_df) < 50:
                    print(f"Note: {24 * 365 - len(time_df)} entries for an entire year are missing.")

                    time_index_full = pd.to_datetime(time_df[["year", "month", "day", "hour", "minute"]])

                    missing_times = pd.date_range(
                        start=time_index_full.min(),
                        end=time_index_full.max(),
                        freq="1H",
                    ).difference(time_index_full)

                    missing_times = pd.to_datetime(missing_times)

                    missing_times_df_year = pd.DataFrame({"year": missing_times.year})
                    missing_times_df_month = pd.DataFrame({"month": missing_times.month})
                    missing_times_df_day = pd.DataFrame({"day": missing_times.day})
                    missing_times_df_hour = pd.DataFrame({"hour": missing_times.hour})
                    missing_times_df_minute = pd.DataFrame({"minute": missing_times.minute})

                    missing_time_df = pd.concat(
                        [
                            missing_times_df_year["year"],
                            missing_times_df_month["month"],
                            missing_times_df_day["day"],
                            missing_times_df_hour["hour"],
                            missing_times_df_minute["minute"],
                        ],
                        axis=1,
                    )

                    print("Missing timeseries:")
                    print(missing_times)

                else:
                    raise KeyError(f"Note: {24 * 365 - len(time_df)} entries for an entire year are missing. Too much!")

            temperature_air_list_k = values["t2m"].tolist()
            pressure_list_pa = values["sp"].tolist()
            wind_speed_u_list_m_s = values["u10"].tolist()
            wind_speed_v_list_m_s = values["v10"].tolist()
            global_horizontal_irradiation_list_j_per_m2 = values["ssrd"].tolist()
            direct_horizontal_irradiation_list_j_per_m2 = values["fdir"].tolist()

            temperature_air_list_celsius = []
            for temperature_in_k in temperature_air_list_k:
                temperature_in_celsius = temperature_in_k - 273.15
                temperature_air_list_celsius.append(temperature_in_celsius)
            temperature_air_df = pd.DataFrame(temperature_air_list_celsius, columns=["temperature"])
            temperature_air_df = temperature_air_df.interpolate(method="linear", limit_direction="backward")

            pressure_list_hpa = []
            for pressure_in_pa in pressure_list_pa:
                pressure_in_hpa = pressure_in_pa / 100
                pressure_list_hpa.append(pressure_in_hpa)
            pressure_df = pd.DataFrame(pressure_list_hpa, columns=["pressure"])
            pressure_df = pressure_df.interpolate(method="linear", limit_direction="backward")

            wind_speed_list_m_s = []
            wind_direction_list = []
            for u_i, v_i in zip(wind_speed_u_list_m_s, wind_speed_v_list_m_s):
                wind_speed_res = np.sqrt(np.square(u_i) + np.square(v_i))
                wind_speed_list_m_s.append(wind_speed_res)

                wind_direction = 180 + (180 / np.pi) * np.arctan2(v_i, u_i)
                wind_direction_list.append(wind_direction)
            wind_speed_df = pd.DataFrame(wind_speed_list_m_s, columns=["wind_speed"])
            wind_speed_df = wind_speed_df.interpolate(method="linear", limit_direction="backward")
            wind_direction_df = pd.DataFrame(wind_direction_list, columns=["wind_direction"])
            wind_direction_df = wind_direction_df.interpolate(method="linear", limit_direction="backward")

            global_horizontal_irradiation_list_watt_per_m2 = []
            for global_horizontal_irradiation_in_j_per_m2 in global_horizontal_irradiation_list_j_per_m2:
                global_horizontal_irradiation_in_watt_per_m2 = global_horizontal_irradiation_in_j_per_m2 / (3600)
                global_horizontal_irradiation_list_watt_per_m2.append(global_horizontal_irradiation_in_watt_per_m2)
            global_irradiance_df = pd.DataFrame(
                global_horizontal_irradiation_list_watt_per_m2,
                columns=["global_irradiance"],
            )
            global_irradiance_df = global_irradiance_df.interpolate(method="linear", limit_direction="backward")

            direct_horizontal_irradiation_list_watt_per_m2 = []
            for direct_horizontal_irradiation_in_j_per_m2 in direct_horizontal_irradiation_list_j_per_m2:
                direct_horizontal_irradiation_in_watt_per_m2 = direct_horizontal_irradiation_in_j_per_m2 / (3600)
                direct_horizontal_irradiation_list_watt_per_m2.append(direct_horizontal_irradiation_in_watt_per_m2)
            direct_irradiance_df = pd.DataFrame(
                direct_horizontal_irradiation_list_watt_per_m2,
                columns=["direct_irradiance"],
            )
            direct_irradiance_df = direct_irradiance_df.interpolate(method="linear", limit_direction="backward")

            weather_df = pd.DataFrame()
            weather_df = pd.concat(
                [
                    time_df,
                    temperature_air_df,  # ["temperature"],
                    pressure_df,  # ["pressure"],
                    wind_direction_df,  # ["wind_direction"],
                    wind_speed_df,  # ["wind_speed"],
                    direct_irradiance_df,  # ["direct_irradiance"],
                    global_irradiance_df,  # ["global_irradiance"],
                ],
                axis=1,
                join="outer",
            )

            if len(missing_time_df) != 0:
                weather_df_time_index = pd.to_datetime(weather_df[["year", "month", "day", "hour", "minute"]])
                time_index_full = pd.to_datetime(missing_time_df[["year", "month", "day", "hour", "minute"]])

                merged_weather_df = pd.concat(
                    [
                        missing_time_df.set_index(time_index_full),
                        weather_df.set_index(weather_df_time_index),
                    ]
                ).sort_index()

                interpolated_weather_df = merged_weather_df.interpolate()

                weather_df = interpolated_weather_df
                print("Missing times successfully added and weather data interpolated!")

            weather_df = weather_df.reset_index(drop=True)

            if weather_df.isna().any(axis=None):
                raise KeyError("The DataFrame weather_df contains NaN values. Check data")
            if weather_df.eq("nan").any().any():
                raise KeyError("The DataFrame weather_df contains NaN values. Check data")

            self.safe_as_csv(weather_df, csv_path)

        self.csv_path = csv_path
