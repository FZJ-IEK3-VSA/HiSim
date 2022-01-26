# Generic/Built-in
import datetime
import math
import os
import pandas as pd
import pvlib
import numpy as np

# Owned
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt
from globals import HISIMPATH
import globals

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

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

    def __init__(self,
                 location="Aachen",
                 my_simulation_parameters=None):
        super().__init__(name="Weather")

        self.build(location,my_simulation_parameters)

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

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_conversion: bool):
        stsv.set_output_value(self.t_outC, self.temperature[timestep])
        stsv.set_output_value(self.DNIC, self.DNI[timestep])
        stsv.set_output_value(self.DNIextraC, self.DNIextra[timestep])
        stsv.set_output_value(self.DHIC, self.DHI[timestep] )
        stsv.set_output_value(self.GHIC, self.GHI[timestep] )
        stsv.set_output_value(self.altitudeC, self.altitude[timestep])
        stsv.set_output_value(self.azimuthC, self.azimuth[timestep])
        stsv.set_output_value(self.wind_speedC, self.Wspd[timestep])
        stsv.set_output_value(self.apparent_zenithC, self.apparent_zenith[timestep])

    def build(self, location,my_simulation_parameters):
        seconds_per_timestep=my_simulation_parameters.seconds_per_timestep
        parameters = [ location ]
        cache_filepath = globals.get_cache(classname="Weather", parameters=parameters)
        if cache_filepath is not None:
                my_weather = pd.read_csv(cache_filepath, sep=",", decimal=".", encoding = "cp1252")
                my_weather.index=pd.date_range("2015-01-01 00:00","2015-12-31 23:59",freq="1min",tz="Europe/Berlin")
                if seconds_per_timestep!=60:
                    self.temperature = my_weather['t_out'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.DryBulb = self.temperature
                    self.DHI = my_weather['DHI'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.DNI = np.float64(my_weather['DNI'].resample(str(seconds_per_timestep)+"S").mean().tolist())
                    self.DNIextra = my_weather['DNIextra'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.GHI = my_weather['GHI'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.altitude = my_weather['altitude'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.azimuth = my_weather['azimuth'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.apparent_zenith = my_weather['apparent_zenith'].resample(str(seconds_per_timestep)+"S").mean().tolist()
                    self.Wspd = my_weather['Wspd'].resample(str(seconds_per_timestep)+"S").mean().tolist()

                else:
                    self.temperature = my_weather['t_out'].tolist()
                    self.DryBulb = self.temperature
                    self.DHI = my_weather['DHI'].tolist()
                    self.DNI = np.float64(my_weather['DNI'].tolist())
                    self.DNIextra = my_weather['DNIextra'].tolist()
                    self.GHI = my_weather['GHI'].tolist()
                    self.altitude = my_weather['altitude'].tolist()
                    self.azimuth = my_weather['azimuth'].tolist()
                    self.apparent_zenith = my_weather['apparent_zenith'].tolist()
                    self.Wspd = my_weather['Wspd'].tolist()

        else:
             tmy_data, location = readTRY(location=location)
             self.DNI = self.interpolate(tmy_data['DNI'])
             # calculate extra terrestrial radiation- n eeded for perez array diffuse irradiance models
             dni_extra = pd.Series(
                 pvlib.irradiance.get_extra_radiation(self.DNI.index), index=self.DNI.index
             )

             #DNI_data = self.interpolate(tmy_data['DNI'], 2015)
             self.temperature = self.interpolate(tmy_data['T'])
             self.DryBulb = self.temperature
             self.DNIextra = dni_extra
             self.DHI = self.interpolate(tmy_data['DHI'])
             self.GHI = self.interpolate(tmy_data['GHI'])


             solpos = pvlib.solarposition.get_solarposition(self.DNI.index, location['latitude'], location['longitude'])
             self.altitude = solpos['elevation']
             self.azimuth = solpos['azimuth']
             self.apparent_zenith = solpos['apparent_zenith']
             self.Wspd = self.interpolate(tmy_data['Wspd'])

             solardata = [self.DNI.tolist(),
                          self.DHI.tolist(),
                          self.GHI.tolist(),
                          self.temperature,
                          self.altitude.tolist(),
                          self.azimuth.tolist(),
                          self.apparent_zenith.tolist(),
                          self.DryBulb,
                          self.Wspd.tolist(),
                          self.DNIextra.tolist()]
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
                                          'DNIextra'])
             globals.save_cache("Weather", parameters, database)

             if seconds_per_timestep != 60:
                 self.temperature = self.temperature.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.DryBulb = self.temperature
                 self.DHI = self.DHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.DNI = np.float64( self.DNI.resample(str(seconds_per_timestep) + "S").mean().tolist())
                 self.DNIextra = self.DNIextra.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.GHI = self.GHI.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.altitude = self.altitude.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.azimuth = self.azimuth.resample(str(seconds_per_timestep) + "S").mean().tolist()
                 self.apparent_zenith = self.apparent_zenith.resample(
                     str(seconds_per_timestep) + "S").mean().tolist()
                 self.Wspd = self.Wspd.resample(str(seconds_per_timestep) + "S").mean().tolist()



    def interpolate(self, pd_database, year=2015):
        firstday = pd.Series([0.0], index=[
            pd.to_datetime(datetime.datetime(year-1, 12, 31, 23, 0), utc=True).tz_convert("Europe/Berlin")])
        lastday = pd.Series(pd_database[-1], index=[
            pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert("Europe/Berlin")])
        pd_database = pd_database.append(firstday)
        pd_database = pd_database.append(lastday)
        pd_database = pd_database.sort_index()
        return pd_database.resample('1T').asfreq().interpolate(method='linear')

    def test_altitude_azimuth(self, latitude_deg, longitude_deg):
        year = 2015

        index = pd.date_range(
            "{}-01-01 00:00:00".format(year), periods=8760, freq="H", tz="Europe/Berlin"
        )

        altitude = [0] * 8760
        azimuth = [0] * 8760
        for h in range(8760):
            altitude[h], azimuth[h] = self.calc_sun_position(latitude_deg,longitude_deg,year,h)

        altitude_pd = pd.DataFrame(altitude, index=index)
        azimuth_pd = pd.DataFrame(azimuth, index=index)

        return altitude_pd, azimuth_pd

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
        return self.altitude.iloc[hoy], self.azimuth.iloc[hoy]

def readTRY(location="Aachen", year=2015):
    """
    Reads a test reference year file and gets the GHI, DHI and DNI from it.

    Parameters
    -------
    try_num: int (default: 4)
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
