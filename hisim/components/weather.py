# Generic/Built-in
import datetime
import math
import os
import pandas as pd
import pvlib
import numpy as np
from dataclasses_json import dataclass_json
from dataclasses import dataclass
from typing import  List, Optional
# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput, SimRepository
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim.utils import HISIMPATH
from hisim import utils
from hisim import log
import json
from timeit import default_timer as timer

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

"""
The functions cited in this module are at some degree based on the tsib project:

[tsib-kotzur]:
Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner. Future grid load of the residential building sector. No. RWTH-2018-231872. Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following repository:
https://github.com/FZJ-IEK3-VSA/tsib
"""

@dataclass_json
@dataclass()
class WeatherConfig:
    location: str="Aachen"

class Weather(Component):
    """
    Provide thermal and solar conditions of local weather

    Parameters
    -----------------------------------------------
    location: string
        place to retrieve weather conditions

    ComponentInputs:
    -----------------------------------------------
       None

    ComponentOutputs:
    -----------------------------------------------
       Outdoor Temperature: Celsius
       Direct Normal Irradiance: kWh/m^2
       Diffuse Normal Irradiance: kWh/m^2
       Sun Altitude: Degrees
       Sun Azimuth: Degrees
       tmy_data["DryBulb"], tmy_data["Wspd"]
    """
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
    
    Weather_TemperatureOutside_yearly_forecast = "Weather_TemperatureOutside_yearly_forecast"
    Weather_DirectNormalIrradiance_yearly_forecast = "Weather_DirectNormalIrradiance_yearly_forecast"
    Weather_DiffuseHorizontalIrradiance_yearly_forecast = "Weather_DiffuseHorizontalIrradiance_yearly_forecast"
    Weather_DirectNormalIrradianceExtra_yearly_forecast = "Weather_DirectNormalIrradianceExtra_yearly_forecast"
    Weather_GlobalHorizontalIrradiance_yearly_forecast = "Weather_GlobalHorizontalIrradiance_yearly_forecast"
    Weather_Azimuth_yearly_forecast = "Weather_Azimuth_yearly_forecast"
    Weather_ApparentZenith_yearly_forecast = "Weather_ApparentZenith_yearly_forecast"
    Weather_WindSpeed_yearly_forecast = "Weather_WindSpeed_yearly_forecast"

    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config : WeatherConfig,
                 my_simulation_repository : Optional[ SimRepository ] = None ):
        super().__init__(name="Weather", my_simulation_parameters=my_simulation_parameters)
        if(my_simulation_parameters is None):
            raise Exception("Simparameters was none")
        self.weatherConfig = config
        self.parameter_string = my_simulation_parameters.get_unique_key()
        self.build( self.weatherConfig.location, my_simulation_parameters, my_simulation_repository )

        self.t_outC : ComponentOutput = self.add_output(self.ComponentName,
                                                        self.TemperatureOutside,
                                                        lt.LoadTypes.Temperature,
                                                        lt.Units.Celsius)

        self.DNIC : ComponentOutput = self.add_output(self.ComponentName,
                                                      self.DirectNormalIrradiance,
                                                      lt.LoadTypes.Irradiance,
                                                      lt.Units.Wm2)

        self.DNIextraC : ComponentOutput = self.add_output(self.ComponentName,
                                                           self.DirectNormalIrradianceExtra,
                                                           lt.LoadTypes.Irradiance,
                                                           lt.Units.Wm2)

        self.DHIC: ComponentOutput = self.add_output(self.ComponentName,
                                                     self.DiffuseHorizontalIrradiance,
                                                     lt.LoadTypes.Irradiance,
                                                     lt.Units.Wm2)

        self.GHIC: ComponentOutput = self.add_output(self.ComponentName,
                                                     self.GlobalHorizontalIrradiance,
                                                     lt.LoadTypes.Irradiance,
                                                     lt.Units.Wm2)

        self.altitudeC : ComponentOutput = self.add_output(self.ComponentName,
                                                           self.Altitude,
                                                           lt.LoadTypes.Any,
                                                           lt.Units.Degrees)

        self.azimuthC : ComponentOutput = self.add_output(self.ComponentName,
                                                          self.Azimuth,
                                                          lt.LoadTypes.Any,
                                                          lt.Units.Degrees)

        self.apparent_zenithC : ComponentOutput = self.add_output(self.ComponentName,
                                                                  self.ApparentZenith,
                                                                  lt.LoadTypes.Any,
                                                                  lt.Units.Degrees)

        self.wind_speedC: ComponentOutput = self.add_output(self.ComponentName,
                                                            self.WindSpeed,
                                                            lt.LoadTypes.Speed,
                                                            lt.Units.MeterPerSecond)
        self.temperature_list : List[float]
        self.DNI_list: List[float]
        self.DNIextra_list: List[float]
        self.altitude_list: List[float]
        self.azimuth_list: List[float]
        self.Wspd_list: List[float]
        self.GHI_list: List[float]
        self.apparent_zenith_list: List[float]
        self.DHI_list: List[float]

    @staticmethod
    def get_default_config():
        config= WeatherConfig(location="Aachen")
        return config
    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_conversion: bool):

        stsv.set_output_value(self.t_outC, self.temperature_list[timestep])
        stsv.set_output_value(self.DNIC, self.DNI_list[timestep])
        stsv.set_output_value(self.DNIextraC, self.DNIextra_list[timestep])
        stsv.set_output_value(self.DHIC, self.DHI_list[timestep] )
        stsv.set_output_value(self.GHIC, self.GHI_list[timestep] )
        stsv.set_output_value(self.altitudeC, self.altitude_list[timestep])
        stsv.set_output_value(self.azimuthC, self.azimuth_list[timestep])
        stsv.set_output_value(self.wind_speedC, self.Wspd_list[timestep])
        stsv.set_output_value(self.apparent_zenithC, self.apparent_zenith_list[timestep])

        # set the temperature forecast
        if self.my_simulation_parameters.system_config.predictive:
            timesteps_24h = 24*3600 / self.my_simulation_parameters.seconds_per_timestep
            last_forecast_timestep = int( timestep + timesteps_24h )
            if(last_forecast_timestep > len(self.temperature_list)):
                last_forecast_timestep = len(self.temperature_list)
            #log.information( type(self.temperature))
            temperatureforecast = self.temperature_list[timestep:last_forecast_timestep]
            self.simulation_repository.set_entry(self.Weather_Temperature_Forecast_24h,temperatureforecast)

    def build( self, 
               location : str, 
               my_simulation_parameters : SimulationParameters, 
               my_simulation_repository : Optional[ SimRepository ] ):
        seconds_per_timestep=my_simulation_parameters.seconds_per_timestep
        parameters = [ location ]
        log.information(self.weatherConfig.location)
        log.information(self.weatherConfig.to_json()) # type: ignore
        #log.information("2:" + json.dumps(self.weatherConfig))

        cachefound, cache_filepath = utils.get_cache_file("Weather", self.weatherConfig, self.my_simulation_parameters)
        if cachefound:
            # read cached files
            my_weather = pd.read_csv(cache_filepath, sep=",", decimal=".", encoding = "cp1252")
            self.temperature_list = my_weather['t_out'].tolist()
            self.DryBulb_list = self.temperature_list
            self.DHI_list = my_weather['DHI'].tolist()
            self.DNI_list = my_weather['DNI'].tolist() #self np.float64( maybe not needed? - Noah
            self.DNIextra_list = my_weather['DNIextra'].tolist()
            self.GHI_list = my_weather['GHI'].tolist()
            self.altitude_list = my_weather['altitude'].tolist()
            self.azimuth_list = my_weather['azimuth'].tolist()
            self.apparent_zenith_list = my_weather['apparent_zenith'].tolist()
            self.Wspd_list = my_weather['Wspd'].tolist()
        else:
             tmy_data, location = readTRY(location=location, year = my_simulation_parameters.year )
             DNI = self.interpolate(tmy_data['DNI'], self.my_simulation_parameters.year)
             # calculate extra terrestrial radiation- n eeded for perez array diffuse irradiance models
             dni_extra = pd.Series(pvlib.irradiance.get_extra_radiation(DNI.index), index=DNI.index) # type: ignore
             #DNI_data = self.interpolate(tmy_data['DNI'], 2015)
             temperature = self.interpolate(tmy_data['T'], self.my_simulation_parameters.year)
             DNIextra = dni_extra
             DHI = self.interpolate(tmy_data['DHI'], self.my_simulation_parameters.year)
             GHI = self.interpolate(tmy_data['GHI'], self.my_simulation_parameters.year)
             solpos = pvlib.solarposition.get_solarposition(DNI.index, location['latitude'], location['longitude'])  # type: ignore
             altitude = solpos['elevation']
             azimuth = solpos['azimuth']
             apparent_zenith = solpos['apparent_zenith']
             Wspd = self.interpolate(tmy_data['Wspd'], self.my_simulation_parameters.year)


             if seconds_per_timestep != 60:
                 self.temperature_list = temperature.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.DryBulb_list = temperature.resample( str( seconds_per_timestep ) + "S" ).mean( ).to_list()
                 self.DHI_list = DHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                  #np.float64( ## not sure what this is fore. python float and npfloat 64 are the same.
                 self.DNI_list = DNI.resample(str(seconds_per_timestep) + "S").mean().tolist()#)  # type: ignore
                 self.DNIextra_list = DNIextra.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.GHI_list = GHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.altitude_list = altitude.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.azimuth_list = azimuth.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.apparent_zenith_list = apparent_zenith.resample(
                     str(seconds_per_timestep) + "S").mean().tolist()
                 self.Wspd_list = Wspd.resample(str(seconds_per_timestep) + "S").mean().tolist()
             else:
                 self.temperature_list = temperature.tolist()
                 self.DryBulb_list = temperature.to_list()
                 self.DHI_list = DHI.tolist()
                 self.DNI_list = DNI.tolist()
                 self.DNIextra_list = DNIextra.tolist()
                 self.GHI_list = GHI.tolist()
                 self.altitude_list = altitude.tolist()
                 self.azimuth_list = azimuth.tolist()
                 self.apparent_zenith_list = apparent_zenith.tolist()
                 self.Wspd_list = Wspd.resample(str(seconds_per_timestep) + "S").mean().tolist()

             solardata = [self.DNI_list,
                          self.DHI_list,
                          self.GHI_list,
                          self.temperature_list,
                          self.altitude_list,
                          self.azimuth_list,
                          self.apparent_zenith_list,
                          self.DryBulb_list,
                          self.Wspd_list,
                          self.DNIextra_list]
    
             database = pd.DataFrame(np.transpose(solardata),
                                     columns=['DNI',
                                              'DHI',
                                              'GHI',
                                              't_out',
                                              'altitude',
                                              'azimuth',
                                              'apparent_zenith',
                                              'DryBulb',
                                              'Wspd',
                                              'DNIextra' ] )
             database.to_csv(cache_filepath)
             
        #write one year forecast to simulation repository for PV processing -> if PV forecasts are needed
        if self.my_simulation_parameters.system_config.predictive and my_simulation_repository is not None:
            print( 'attention, attention:', len( self.temperature_list ))
            my_simulation_repository.set_entry( self.Weather_TemperatureOutside_yearly_forecast, self.temperature_list )
            my_simulation_repository.set_entry( self.Weather_DiffuseHorizontalIrradiance_yearly_forecast, self.DHI_list )
            my_simulation_repository.set_entry( self.Weather_DirectNormalIrradiance_yearly_forecast, self.DNI_list )
            my_simulation_repository.set_entry( self.Weather_DirectNormalIrradianceExtra_yearly_forecast, self.DNIextra_list ) 
            my_simulation_repository.set_entry( self.Weather_GlobalHorizontalIrradiance_yearly_forecast, self.GHI_list )
            my_simulation_repository.set_entry( self.Weather_Azimuth_yearly_forecast, self.azimuth_list )
            my_simulation_repository.set_entry( self.Weather_ApparentZenith_yearly_forecast, self.apparent_zenith_list ) 
            my_simulation_repository.set_entry( self.Weather_WindSpeed_yearly_forecast, self.Wspd_list )

    def interpolate(self, pd_database, year):
        firstday = pd.Series([0.0], index=[
            pd.to_datetime(datetime.datetime(year-1, 12, 31, 23, 0), utc=True).tz_convert("Europe/Berlin")])
        lastday = pd.Series(pd_database[-1], index=[
            pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert("Europe/Berlin")])
        pd_database = pd_database.append(firstday)
        pd_database = pd_database.append(lastday)
        pd_database = pd_database.sort_index()
        return pd_database.resample('1T').asfreq().interpolate(method='linear')

    # def test_altitude_azimuth(self, latitude_deg, longitude_deg):
    #     year = 2015
    #
    #     index = pd.date_range(
    #         "{}-01-01 00:00:00".format(year), periods=8760, freq="H", tz="Europe/Berlin"
    #     )
    #
    #     altitude = [0] * 8760
    #     azimuth = [0] * 8760
    #     for h in range(8760):
    #         altitude[h], azimuth[h] = self.calc_sun_position(latitude_deg,longitude_deg,year,h)
    #
    #     altitude_pd = pd.DataFrame(altitude, index=index)
    #     azimuth_pd = pd.DataFrame(azimuth, index=index)
    #
    #     return altitude_pd, azimuth_pd

    def calc_sun_position(self, latitude_deg, longitude_deg, year, hoy):
        """
        Calculates the Sun Position for a specific hour and location

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
            23.45 * math.sin((2 * math.pi / 365.0) * (day_of_year - 81)))

        # Normalise the day to 2*pi
        # There is some reason as to why it is 364 and not 365.26
        angle_of_day = (day_of_year - 81) * (2 * math.pi / 364)

        # The deviation between local standard time and true solar time
        equation_of_time = (9.87 * math.sin(2 * angle_of_day)) - \
                           (7.53 * math.cos(angle_of_day)) - (1.5 * math.sin(angle_of_day))

        # True Solar Time
        solar_time = ((utc_datetime.hour * 60) + utc_datetime.minute +
                      (4 * longitude_deg) + equation_of_time) / 60.0

        # Angle between the local longitude and longitude where the sun is at
        # higher altitude
        hour_angle_rad = math.radians(15 * (12 - solar_time))

        # Altitude Position of the Sun in Radians
        altitude_rad = math.asin(math.cos(latitude_rad) * math.cos(declination_rad) * math.cos(hour_angle_rad) +
                                 math.sin(latitude_rad) * math.sin(declination_rad))

        # Azimuth Position fo the sun in radians
        azimuth_rad = math.asin(
            math.cos(declination_rad) * math.sin(hour_angle_rad) / math.cos(altitude_rad))

        # I don't really know what this code does, it has been imported from
        # PySolar
        if(math.cos(hour_angle_rad) >= (math.tan(declination_rad) / math.tan(latitude_rad))):
            return math.degrees(altitude_rad), math.degrees(azimuth_rad)
        else:
            return math.degrees(altitude_rad), (180 - math.degrees(azimuth_rad))

    def calc_sun_position2(self, hoy):
        return self.altitude_list[hoy], self.azimuth_list[hoy]

def readTRY(location="Aachen", year=2015):
    """
    Reads a test reference year file and gets the GHI, DHI and DNI from it.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    -------
    location: str
        The region number of the test reference year.
    year: int (default: 2010)
        The year. Only data for 2010 and 2030 available
    """
    # get the correct file path
    filepath = os.path.join(HISIMPATH["weather"][location])

    # get the geoposition
    with open(filepath + ".dat", encoding="utf-8") as fp:
        lines = fp.readlines()
        location_name = lines[0].split(maxsplit=2)[2].replace('\n', '')
        lat = float(lines[1][20:37])
        lon = float(lines[2][15:30])
    location = {"name": location_name, "latitude": lat, "longitude": lon}

    # check if time series data already exists as .csv with DNI
    if os.path.isfile(filepath + ".csv"):
        data = pd.read_csv(filepath + ".csv", index_col=0, parse_dates=True,sep=";",decimal=",")
        data.index = pd.to_datetime(data.index, utc=True).tz_convert("Europe/Berlin")
    # else read from .dat and calculate DNI etc.
    else:
        # get data
        data = pd.read_csv(
            filepath + ".dat", sep=r"\s+", skiprows=([i for i in range(0, 31)])
        )
        data.index = pd.date_range(
            "{}-01-01 00:30:00".format(year), periods=8760, freq="H", tz="Europe/Berlin"
        )
        data["GHI"] = data["D"] + data["B"]
        data = data.rename(columns={"D": "DHI",
                                    "t": "T",
                                    "WG": "Wspd",
                                    "MM": "Month",
                                    "DD": "Day",
                                    "HH": "Hour",
                                    "p": "Pressure",
                                    "WR": "Wdir"})

        # calculate direct normal
        data["DNI"] = calculateDNI(data["B"], lon, lat)
        # data["DNI"] = data["B"]

        # save as .csv
        #data.to_csv(filepath + ".csv",sep=";",decimal=",")

    return data, location

def calculateDNI(directHI, lon, lat, zenith_tol=87.0):
    """
    Calculates the direct NORMAL irradiance from the direct horizontal irradiance with the help of the PV lib.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    ----------
    directHI: pd.Series with time index
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
    solarPos = pvlib.solarposition.get_solarposition(directHI.index, lat, lon)
    solarPos["apparent_zenith"][solarPos.apparent_zenith > zenith_tol] = zenith_tol
    DNI = directHI.div(solarPos["apparent_zenith"].apply(math.radians).apply(math.cos))
    #DNI = DNI.fillna(0)
    if DNI.isnull().values.any():
        raise ValueError("Something went wrong...")
    return DNI
