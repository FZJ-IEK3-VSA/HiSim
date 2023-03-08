""" Handles all the weather data processing. """

import csv
import datetime
import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, List

import numpy as np
import pandas as pd
import pvlib

from hisim import loadtypes as lt
from hisim import log, utils
from hisim.component import (Component, ComponentOutput, ConfigBase,
                             SingleTimeStepValues)
from hisim.simulationparameters import SimulationParameters

__authors__ = "Vitor Hugo Bellotto Zago, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Noah Pflugradt"

""" The functions cited in this module are at some degree based on the tsib project:

[tsib-kotzur]: Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner. Future grid load of the residential building sector.
No. RWTH-2018-231872. Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following repository:
https://github.com/FZJ-IEK3-VSA/tsib
"""


class WeatherDataSourceEnum(Enum):

    """Describes where the weather data is from. Used to choose the correct reading function."""

    DWD = 1
    NSRDB = 2
    NSRDB_15min = 3


class LocationEnum(Enum):

    """contains all the locations and their corresponding directories."""

    Aachen = (
        "Aachen",
        "test-reference-years_1995-2012_1-location",
        "data_processed",
        "aachen_center",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Bremerhaven = (
        "01_Bremerhaven",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_01",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Rostock = (
        "02_Rostock",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_02",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Hamburg = (
        "03Hamburg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_03",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Potsdam = (
        "04Potsdam",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_04",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Essen = (
        "05Essen",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_05",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Bad_Marienburg = (
        "06Bad Marienburg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_06",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Kassel = (
        "07Kassel",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_07",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Braunlage = (
        "08Braunlage",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_08",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Chemnitz = (
        "09Chemnitz",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_09",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Hof = (
        "10Hof",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_10",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Fichtelberg = (
        "11Fichtelberg",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_11",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Mannheim = (
        "12Mannheim",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_12",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Muehldorf = (
        "13Muehldorf",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_13",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Stoetten = (
        "14Stoetten",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_14",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Garmisch_Partenkirchen = (
        "15Garmisch Partenkirchen",
        "test-reference-years_2015-2045_15-locations",
        "data_processed",
        "weather_region_15",
        WeatherDataSourceEnum.DWD,
    )  # noqa: invalid-name
    Madrid = (
        "Madrid",
        "NSRDB",
        "NSRDB",
        "Madrid",
        WeatherDataSourceEnum.NSRDB,
    )  # noqa: invalid-name
    Seville = (
        "Seville",
        "NSRDB",
        "NSRDB",
        "Seville",
        WeatherDataSourceEnum.NSRDB,
    )  # noqa: invalid-name,
    DE = (
        "Potsdam",
        "NSRDB_15min",
        "Potsdam",
        "742114_52.41_13.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    PL = (
        "Warsaw",
        "NSRDB_15min",
        "Warsaw",
        "1138443_52.25_21.02_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    SE = (
        "Stockholm",
        "NSRDB_15min",
        "Stockholm",
        "984998_59.33_18.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    NO = (
        "Oslo",
        "NSRDB_15min",
        "Oslo",
        "653025_59.93_10.74_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    RS = (
        "Belgrad",
        "NSRDB_15min",
        "Belgrad",
        "1108363_44.77_20.46_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    IT = (
        "Rome",
        "NSRDB_15min",
        "Rome",
        "718838_41.89_12.50_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    GB = (
        "London",
        "NSRDB_15min",
        "London",
        "337089_51.49_-0.10_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    CY = (
        "Nicosia",
        "NSRDB_15min",
        "Nicosia",
        "1809004_35.17_33.38_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    GR = (
        "Athens",
        "NSRDB_15min",
        "Athens",
        "1291832_37.97_23.74_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    IE = (
        "Dublin",
        "NSRDB_15min",
        "Dublin",
        "165308_53.37_-6.26_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    SI = (
        "Ljubljana",
        "NSRDB_15min",
        "Ljubljana",
        "808557_46.05_14.50_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    CZ = (
        "Prague",
        "NSRDB_15min",
        "Prague",
        "804583_50.09_14.42_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    AT = (
        "Viena",
        "NSRDB_15min",
        "Viena",
        "902141_48.21_16.38_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    HU = (
        "Budapest",
        "NSRDB_15min",
        "Budapest",
        "1035927_47.49_19.06_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    BE = (
        "Uccle",
        "NSRDB_15min",
        "Uccle",
        "454992_50.81_4.34_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    ES = (
        "Barcelona",
        "NSRDB_15min",
        "Barcelona",
        "398308_41.41_2.14_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    DK = (
        "Copenhagen",
        "NSRDB_15min",
        "Copenhagen",
        "721796_55.69_12.58_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    NL = (
        "Amsterdam",
        "NSRDB_15min",
        "Amsterdam",
        "469536_52.37_4.90_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name
    BG = (
        "Sofia",
        "NSRDB_15min",
        "Sofia",
        "1267064_42.69_23.30_2019.csv",
        WeatherDataSourceEnum.NSRDB_15min,
    )  # noqa: invalid-name




@dataclass
class WeatherConfig(ConfigBase):

    """Configuration class for Weather."""

    location: str
    source_path: str
    data_source: WeatherDataSourceEnum

    @classmethod
    def get_main_classname(cls):
        """Get the name of the main class."""
        return Weather.get_full_classname()

    @classmethod
    def get_default(cls, location_entry: Any) -> Any:
        """Gets the default configuration for Aachen."""
        path = os.path.join(
            utils.get_input_directory(),
            "weather",
            location_entry.value[1],
            location_entry.value[2],
            location_entry.value[3],
        )
        config = WeatherConfig(
            name="Weather_1",
            location=location_entry.value[0],
            source_path=path,
            data_source=location_entry.value[4],
        )
        return config


class Weather(Component):

    """Provide thermal and solar conditions of local weather."""

    # Inputs
    # None

    # Outputs
    TemperatureOutside = "TemperatureOutside"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    Altitude = "Altitude"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    WindSpeed = "WindSpeed"
    Weather_Temperature_Forecast_24h = "Weather_Temperature_Forecast_24h"
    DailyAverageOutsideTemperatures = "DailyAverageOutsideTemperatures"

    Weather_TemperatureOutside_yearly_forecast = (
        "Weather_TemperatureOutside_yearly_forecast"
    )
    Weather_DirectNormalIrradiance_yearly_forecast = (
        "Weather_DirectNormalIrradiance_yearly_forecast"
    )
    Weather_DiffuseHorizontalIrradiance_yearly_forecast = (
        "Weather_DiffuseHorizontalIrradiance_yearly_forecast"
    )
    Weather_DirectNormalIrradianceExtra_yearly_forecast = (
        "Weather_DirectNormalIrradianceExtra_yearly_forecast"
    )
    Weather_GlobalHorizontalIrradiance_yearly_forecast = (
        "Weather_GlobalHorizontalIrradiance_yearly_forecast"
    )
    Weather_Azimuth_yearly_forecast = "Weather_Azimuth_yearly_forecast"
    Weather_ApparentZenith_yearly_forecast = "Weather_ApparentZenith_yearly_forecast"
    Weather_WindSpeed_yearly_forecast = "Weather_WindSpeed_yearly_forecast"

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: WeatherConfig
    ):
        """Initializes the entire class."""
        super().__init__(
            name="Weather", my_simulation_parameters=my_simulation_parameters
        )
        if my_simulation_parameters is None:
            raise Exception("Simparameters was none")
        self.last_timestep_with_update = -1
        self.weather_config = config
        self.parameter_string = my_simulation_parameters.get_unique_key()

        self.air_temperature_output: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.TemperatureOutside} will follow.",
        )

        self.DNI_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DirectNormalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DirectNormalIrradiance} will follow.",
        )

        self.DNI_extra_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DirectNormalIrradianceExtra,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DirectNormalIrradianceExtra} will follow.",
        )

        self.DHI_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DiffuseHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.DiffuseHorizontalIrradiance} will follow.",
        )

        self.GHI_output: ComponentOutput = self.add_output(
            self.component_name,
            self.GlobalHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            output_description=f"here a description for {self.GlobalHorizontalIrradiance} will follow.",
        )

        self.altitude_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Altitude,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.Altitude} will follow.",
        )

        self.azimuth_output: ComponentOutput = self.add_output(
            self.component_name,
            self.Azimuth,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.Azimuth} will follow.",
        )

        self.apparent_zenith_output: ComponentOutput = self.add_output(
            self.component_name,
            self.ApparentZenith,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            output_description=f"here a description for {self.ApparentZenith} will follow.",
        )

        self.wind_speed_output: ComponentOutput = self.add_output(
            self.component_name,
            self.WindSpeed,
            lt.LoadTypes.SPEED,
            lt.Units.METER_PER_SECOND,
            output_description=f"here a description for {self.WindSpeed} will follow.",
        )

        self.daily_average_outside_temperature_output: ComponentOutput = self.add_output(
            self.component_name,
            self.DailyAverageOutsideTemperatures,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.DailyAverageOutsideTemperatures} will follow.",
        )

        self.temperature_list: List[float]
        self.DNI_list: List[float]
        self.DNIextra_list: List[float]
        self.altitude_list: List[float]
        self.azimuth_list: List[float]
        self.wind_speed_list: List[float]
        self.GHI_list: List[float]
        self.apparent_zenith_list: List[float]
        self.DHI_list: List[float]
        self.dry_bulb_list: List[float]
        self.daily_average_outside_temperature_list_in_celsius: List[float]

    def write_to_report(self):
        """Write configuration to the report."""
        lines = []
        for config_string in self.weather_config.get_string_dict():
            lines.append(config_string)
        return lines

    def i_save_state(self) -> None:
        """Saves the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the previous state. Not needed for weather."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Double chekc."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Performs the simulation."""
        if self.last_timestep_with_update == timestep:
            return
        if force_convergence:
            return
        """ Performs the simulation. """
        stsv.set_output_value(
            self.air_temperature_output, self.temperature_list[timestep]
        )
        stsv.set_output_value(self.DNI_output, self.DNI_list[timestep])
        stsv.set_output_value(self.DNI_extra_output, self.DNIextra_list[timestep])
        stsv.set_output_value(self.DHI_output, self.DHI_list[timestep])
        stsv.set_output_value(self.GHI_output, self.GHI_list[timestep])
        stsv.set_output_value(self.altitude_output, self.altitude_list[timestep])
        stsv.set_output_value(self.azimuth_output, self.azimuth_list[timestep])
        stsv.set_output_value(self.wind_speed_output, self.wind_speed_list[timestep])
        stsv.set_output_value(
            self.apparent_zenith_output, self.apparent_zenith_list[timestep]
        )
        stsv.set_output_value(
            self.daily_average_outside_temperature_output,
            self.daily_average_outside_temperature_list_in_celsius[timestep],
        )

        # set the temperature forecast
        if self.my_simulation_parameters.predictive_control:
            timesteps_24h = (
                24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
            last_forecast_timestep = int(timestep + timesteps_24h)
            if last_forecast_timestep > len(self.temperature_list):
                last_forecast_timestep = len(self.temperature_list)
            # log.information( type(self.temperature))
            temperatureforecast = self.temperature_list[timestep:last_forecast_timestep]
            self.simulation_repository.set_entry(
                self.Weather_Temperature_Forecast_24h, temperatureforecast
            )
        self.last_timestep_with_update = timestep

    def i_prepare_simulation(self) -> None:
        """Generates the lists to be used later."""
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        log.information(self.weather_config.location)
        log.information(self.weather_config.to_json())  # type: ignore
        location_dict = get_coordinates(filepath=self.weather_config.source_path, source_enum=self.weather_config.data_source)
        self.simulation_repository.set_entry("weather_location", location_dict)
        cachefound, cache_filepath = utils.get_cache_file(
            "Weather", self.weather_config, self.my_simulation_parameters
        )
        if cachefound:
            # read cached files
            my_weather = pd.read_csv(
                cache_filepath, sep=",", decimal=".", encoding="cp1252"
            )
            self.temperature_list = my_weather["t_out"].tolist()
            self.dry_bulb_list = self.temperature_list
            self.DHI_list = my_weather["DHI"].tolist()
            self.DNI_list = my_weather[
                "DNI"
            ].tolist()  # self np.float64( maybe not needed? - Noah
            self.DNIextra_list = my_weather["DNIextra"].tolist()
            self.GHI_list = my_weather["GHI"].tolist()
            self.altitude_list = my_weather["altitude"].tolist()
            self.azimuth_list = my_weather["azimuth"].tolist()
            self.apparent_zenith_list = my_weather["apparent_zenith"].tolist()
            self.wind_speed_list = my_weather["Wspd"].tolist()
        else:
            tmy_data = read_test_reference_year_data(
                weatherconfig=self.weather_config,
                year=self.my_simulation_parameters.year,
            )
            if self.weather_config.data_source == WeatherDataSourceEnum.NSRDB_15min:
                DNI = tmy_data["DNI"].resample("1T").asfreq().interpolate(method="linear")
                temperature = tmy_data["T"].resample("1T").asfreq().interpolate(method="linear")
                DHI = tmy_data["DHI"].resample("1T").asfreq().interpolate(method="linear")
                GHI = tmy_data["GHI"].resample("1T").asfreq().interpolate(method="linear")
                wind_speed = tmy_data["Wspd"].resample("1T").asfreq().interpolate(method="linear")
            else:
                DNI = self.interpolate(tmy_data["DNI"], self.my_simulation_parameters.year)
                temperature = self.interpolate(tmy_data["T"], self.my_simulation_parameters.year)
                DHI = self.interpolate(tmy_data["DHI"], self.my_simulation_parameters.year)
                GHI = self.interpolate(tmy_data["GHI"], self.my_simulation_parameters.year)
                wind_speed = self.interpolate(tmy_data["Wspd"], self.my_simulation_parameters.year)
            # calculate extra terrestrial radiation- n eeded for perez array diffuse irradiance models
            dni_extra = pd.Series(pvlib.irradiance.get_extra_radiation(DNI.index), index=DNI.index)  # type: ignore
            
            solpos = pvlib.solarposition.get_solarposition(DNI.index, location_dict["latitude"], location_dict["longitude"])  # type: ignore
            altitude = solpos["elevation"]
            azimuth = solpos["azimuth"]
            apparent_zenith = solpos["apparent_zenith"]

            if seconds_per_timestep != 60:
                self.temperature_list = (
                    temperature.resample(str(seconds_per_timestep) + "S")
                    .mean()
                    .tolist()
                )
                self.dry_bulb_list = (
                    temperature.resample(str(seconds_per_timestep) + "S")
                    .mean()
                    .to_list()
                )
                self.DHI_list = (
                    DHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
                # np.float64( ## not sure what this is fore. python float and npfloat 64 are the same.
                self.DNI_list = (
                    DNI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )  # )  # type: ignore
                self.DNIextra_list = (
                    dni_extra.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
                self.GHI_list = (
                    GHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
                self.altitude_list = (
                    altitude.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
                self.azimuth_list = (
                    azimuth.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
                self.apparent_zenith_list = (
                    apparent_zenith.resample(str(seconds_per_timestep) + "S")
                    .mean()
                    .tolist()
                )
                self.wind_speed_list = (
                    wind_speed.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )
            else:
                self.temperature_list = temperature.tolist()
                self.dry_bulb_list = temperature.to_list()
                self.DHI_list = DHI.tolist()
                self.DNI_list = DNI.tolist()
                self.DNIextra_list = dni_extra.tolist()
                self.GHI_list = GHI.tolist()
                self.altitude_list = altitude.tolist()
                self.azimuth_list = azimuth.tolist()
                self.apparent_zenith_list = apparent_zenith.tolist()
                self.wind_speed_list = (
                    wind_speed.resample(str(seconds_per_timestep) + "S").mean().tolist()
                )

            solardata = [
                self.DNI_list,
                self.DHI_list,
                self.GHI_list,
                self.temperature_list,
                self.altitude_list,
                self.azimuth_list,
                self.apparent_zenith_list,
                self.dry_bulb_list,
                self.wind_speed_list,
                self.DNIextra_list,
            ]

            database = pd.DataFrame(
                np.transpose(solardata),
                columns=[
                    "DNI",
                    "DHI",
                    "GHI",
                    "t_out",
                    "altitude",
                    "azimuth",
                    "apparent_zenith",
                    "DryBulb",
                    "Wspd",
                    "DNIextra",
                ],
            )
            database.to_csv(cache_filepath)

        self.calculate_daily_average_outside_temperature(
            temperaturelist=self.temperature_list,
            seconds_per_timestep=seconds_per_timestep,
        )

        # write one year forecast to simulation repository for PV processing -> if PV forecasts are needed
        if self.my_simulation_parameters.predictive_control:
            self.simulation_repository.set_entry(
                self.Weather_TemperatureOutside_yearly_forecast, self.temperature_list
            )
            self.simulation_repository.set_entry(
                self.Weather_DiffuseHorizontalIrradiance_yearly_forecast, self.DHI_list
            )
            self.simulation_repository.set_entry(
                self.Weather_DirectNormalIrradiance_yearly_forecast, self.DNI_list
            )
            self.simulation_repository.set_entry(
                self.Weather_DirectNormalIrradianceExtra_yearly_forecast,
                self.DNIextra_list,
            )
            self.simulation_repository.set_entry(
                self.Weather_GlobalHorizontalIrradiance_yearly_forecast, self.GHI_list
            )
            self.simulation_repository.set_entry(
                self.Weather_Azimuth_yearly_forecast, self.azimuth_list
            )
            self.simulation_repository.set_entry(
                self.Weather_ApparentZenith_yearly_forecast, self.apparent_zenith_list
            )
            self.simulation_repository.set_entry(
                self.Weather_WindSpeed_yearly_forecast, self.wind_speed_list
            )

    def interpolate(self, pd_database: Any, year: int) -> Any:
        """Interpolates a time series."""
        firstday = pd.Series(
            [0.0],
            index=[
                pd.to_datetime(
                    datetime.datetime(year - 1, 12, 31, 23, 0), utc=True
                ).tz_convert(tz="Europe/Berlin")
            ],
        )
        lastday = pd.Series(
            pd_database[-1],
            index=[
                pd.to_datetime(
                    datetime.datetime(year, 12, 31, 22, 59), utc=True
                ).tz_convert(tz="Europe/Berlin")
            ],
        )
        pd_database = pd.concat([pd_database, firstday, lastday])
        pd_database = pd_database.sort_index()
        return pd_database.resample("1T").asfreq().interpolate(method="linear")

    def calc_sun_position(self, latitude_deg, longitude_deg, year, hoy):
        """Calculates the Sun Position for a specific hour and location.

        :param latitude_deg: Geographical Latitude in Degrees
        :type latitude_deg: float
        :param longitude_deg: Geographical Longitude in Degrees
        :type longitude_deg: float
        :param year: year
        :type year: int
        :param hoy: Hour of the year from the start. The first hour of January is 1
        :type hoy: int
        :return: altitude, azimuth: Sun position in altitude and azimuth degrees [degrees]
        :rtype: tuple
        """
        # Convert to Radians
        latitude_rad = math.radians(latitude_deg)
        # longitude_rad = math.radians(longitude_deg)  # Note: this is never used

        # Set the date in UTC based off the hour of year and the year itself
        start_of_year = datetime.datetime(year, 1, 1, 0, 0, 0, 0)
        utc_datetime = start_of_year + datetime.timedelta(hours=hoy)

        # Angular distance of the sun north or south of the earths equator
        # Determine the day of the year.
        day_of_year = utc_datetime.timetuple().tm_yday

        # Calculate the declination angle: The variation due to the earths tilt
        # http://www.pveducation.org/pvcdrom/properties-of-sunlight/declination-angle
        declination_rad = math.radians(
            23.45 * math.sin((2 * math.pi / 365.0) * (day_of_year - 81))
        )

        # Normalise the day to 2*pi
        # There is some reason as to why it is 364 and not 365.26
        angle_of_day = (day_of_year - 81) * (2 * math.pi / 364)

        # The deviation between local standard time and true solar time
        equation_of_time = (
            (9.87 * math.sin(2 * angle_of_day))
            - (7.53 * math.cos(angle_of_day))
            - (1.5 * math.sin(angle_of_day))
        )

        # True Solar Time
        solar_time = (
            (utc_datetime.hour * 60)
            + utc_datetime.minute
            + (4 * longitude_deg)
            + equation_of_time
        ) / 60.0

        # Angle between the local longitude and longitude where the sun is at
        # higher altitude
        hour_angle_rad = math.radians(15 * (12 - solar_time))

        # Altitude Position of the Sun in Radians
        altitude_rad = math.asin(
            math.cos(latitude_rad)
            * math.cos(declination_rad)
            * math.cos(hour_angle_rad)
            + math.sin(latitude_rad) * math.sin(declination_rad)
        )

        # Azimuth Position fo the sun in radians
        azimuth_rad = math.asin(
            math.cos(declination_rad)
            * math.sin(hour_angle_rad)
            / math.cos(altitude_rad)
        )

        # I don't really know what this code does, it has been imported from
        # PySolar
        if math.cos(hour_angle_rad) >= (
            math.tan(declination_rad) / math.tan(latitude_rad)
        ):
            return math.degrees(altitude_rad), math.degrees(azimuth_rad)
        return math.degrees(altitude_rad), (180 - math.degrees(azimuth_rad))

    def calc_sun_position2(self, hoy: Any) -> Any:
        """Calculates the sun position."""
        return self.altitude_list[hoy], self.azimuth_list[hoy]

    def calculate_daily_average_outside_temperature(
        self, temperaturelist: List[float], seconds_per_timestep: int
    ) -> List[float]:
        """Calculate the daily average outside temperatures."""
        timestep_24h = int(24 * 3600 / seconds_per_timestep)
        total_number_of_timesteps_temperature_list = len(temperaturelist)
        self.daily_average_outside_temperature_list_in_celsius = []
        start_index = 0
        for index in range(0, total_number_of_timesteps_temperature_list):
            daily_average_temperature = float(
                np.mean(temperaturelist[start_index : start_index + timestep_24h])
            )
            if index == start_index + timestep_24h:
                start_index = index
            self.daily_average_outside_temperature_list_in_celsius.append(
                daily_average_temperature
            )
        return self.daily_average_outside_temperature_list_in_celsius


def get_coordinates(filepath: str, source_enum: WeatherDataSourceEnum) -> Any:
    """Reads a test reference year file and gets the GHI, DHI and DNI from it.

    Based on the tsib project @[tsib-kotzur] (Check header)
    """
    # get the correct file path
    # filepath = os.path.join(utils.HISIMPATH["weather"][location])

    if source_enum == WeatherDataSourceEnum.NSRDB_15min:
        with open(filepath, encoding="utf-8") as csvfile:
            spamreader = csv.reader(csvfile)
            for (i, row) in enumerate(spamreader):
                if i==1:
                    location_name=row[1]
                    lat=float(row[5])
                    lon=float(row[6])
                elif i > 1:
                    break
    else:
        # get the geoposition
        with open(filepath + ".dat", encoding="utf-8") as file_stream:
            lines = file_stream.readlines()
            location_name = lines[0].split(maxsplit=2)[2].replace("\n", "")
            lat = float(lines[1][20:37])
            lon = float(lines[2][15:30])
    return {"name": location_name, "latitude": lat, "longitude": lon}
    # self.index = pd.date_range(f"{year}-01-01 00:00:00", periods=60 * 24 * 365, freq="T", tz="Europe/Berlin")


def read_test_reference_year_data(weatherconfig: WeatherConfig, year: int) -> Any:
    """Reads a test reference year file and gets the GHI, DHI and DNI from it.

    Based on the tsib project @[tsib-kotzur] (Check header)
    """
    # get the correct file path
    filepath = os.path.join(weatherconfig.source_path)
    if weatherconfig.data_source == WeatherDataSourceEnum.NSRDB:
        data = read_nsrdb_data(filepath, year)
    elif weatherconfig.data_source == WeatherDataSourceEnum.DWD:
        data = read_dwd_data(filepath, year)
    elif weatherconfig.data_source == WeatherDataSourceEnum.NSRDB_15min:
        data = read_nsrdb_15min_data(filepath, year)

    return data


def read_dwd_data(filepath: str, year: int) -> Any:
    """Reads the DWD data."""
    # get the geoposition
    with open(filepath + ".dat", encoding="utf-8") as file_stream:
        lines = file_stream.readlines()
        lat = float(lines[1][20:37])
        lon = float(lines[2][15:30])
    # check if time series data already exists as .csv with DNI
    if os.path.isfile(filepath + ".csv"):
        data = pd.read_csv(
            filepath + ".csv", index_col=0, parse_dates=True, sep=";", decimal=","
        )
        data.index = pd.to_datetime(data.index, utc=True).tz_convert("Europe/Berlin")
    # else read from .dat and calculate DNI etc.
    else:
        # get data
        data = pd.read_csv(filepath + ".dat", sep=r"\s+", skiprows=list(range(0, 31)))
        data.index = pd.date_range(
            f"{year}-01-01 00:30:00", periods=8760, freq="H", tz="Europe/Berlin"
        )
        data["GHI"] = data["D"] + data["B"]
        data = data.rename(
            columns={
                "D": "DHI",
                "t": "T",
                "WG": "Wspd",
                "MM": "Month",
                "DD": "Day",
                "HH": "Hour",
                "p": "Pressure",
                "WR": "Wdir",
            }
        )

        # calculate direct normal
        data["DNI"] = calculate_direct_normal_radiation(
            data["B"], lon, lat
        )
    return data


def read_nsrdb_data(filepath, year):
    """Reads a set of NSRDB data."""
    # get data
    data = pd.read_csv(filepath + ".dat", sep=",", skiprows=list(range(0, 11)))
    data = data.drop(data.index[8761:8772])
    data.index = pd.date_range(
        f"{year}-01-01 00:30:00", periods=8760, freq="H", tz="Europe/Berlin"
    )
    data = data.rename(
        columns={
            "DHI": "DHI",
            "Temperature": "T",
            "Wind Speed": "Wspd",
            "MM": "Month",
            "DD": "Day",
            "HH": "Hour",
            "Pressure": "Pressure",
            "Wind Direction": "Wdir",
            "GHI": "GHI",
            "DNI": "DNI",
        }
    )
    return data

def read_nsrdb_15min_data(filepath, year):
    """Reads a set of NSRDB data in 15 min resolution."""
    data = pd.read_csv(filepath, encoding="utf-8", skiprows = [0, 1])
    # get data
    data.index = pd.date_range(
        f"{year}-01-01 00:00:00", periods=24 * 4 * 365, freq="900S", tz="Europe/Berlin"
    )
    data = data.rename(
        columns={
            "Temperature": "T",
            "Wind Speed": "Wspd",
        }
    )
    return data


def calculate_direct_normal_radiation(
    direct_horizontal_irradation, lon, lat, zenith_tol=87.0
):
    """Calculates the direct NORMAL irradiance from the direct horizontal irradiance with the help of the PV lib.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    ----------
    direct_horizontal_irradation: pd.Series with time index
        Direct horizontal irradiance
    lon: float
        Longitude of the location
    lat: float
        Latitude of the location
    zenith_tol: float, optional
        Avoid cosines of values above a certain zenith angle of in order to avoid division by zero.

    Returns
    -------
    DNI: pd.Series

    """

    solar_pos = pvlib.solarposition.get_solarposition(
        direct_horizontal_irradation.index, lat, lon
    )
    solar_pos["apparent_zenith"][solar_pos.apparent_zenith > zenith_tol] = zenith_tol
    DNI = direct_horizontal_irradation.div(
        solar_pos["apparent_zenith"].apply(math.radians).apply(math.cos)
    )
    if DNI.isnull().values.any():
        raise ValueError("Something went wrong...")
    return DNI
