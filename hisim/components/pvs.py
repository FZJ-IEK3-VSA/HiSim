# Generic/Built-in
import datetime
import math
import os
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import pvlib
from dataclasses_json import dataclass_json

from dataclasses import dataclass
from functools import lru_cache
from hisim.simulationparameters import SimulationParameters
# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components.weather import Weather

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

temp_model = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]

@lru_cache(maxsize=16)
def simPhotovoltaicFast(
    dni_extra=None,
    DNI=None,
    DHI=None,
    GHI=None,
    azimuth=None,
    apparent_zenith=None,
    temperature=None,
    wind_speed=None,
    surface_azimuth=180,
    surface_tilt=30):
    """
    Simulates a defined PV array with the Sandia PV Array Performance Model.
    The implementation is done in accordance with following tutorial:
    https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb

    Parameters
    ----------
    surface_tilt: int or float, optional (default:30)
        Tilt angle of of the array in degree.
    surface_azimuth: int or float, optional (default:180)
        Azimuth angle of of the array in degree. 180 degree means south,
        90 degree east and 270 west.
    losses: float, optional (default: 0.1)
        Losses due to soiling, mismatch, diode connections, dc wiring etc.
    Returns
    --------
    """
    poa_irrad = pvlib.irradiance.get_total_irradiance(surface_tilt,
                                                      surface_azimuth,
                                                      apparent_zenith,
                                                      azimuth,
                                                      DNI,
                                                      GHI,
                                                      DHI,
                                                      dni_extra)

    pvtemps = pvlib.temperature.sapm_cell(poa_irrad["poa_global"], temperature, wind_speed, **temp_model)

    pv_dc = pvlib.pvsystem.pvwatts_dc(poa_irrad["poa_global"],
                                      temp_cell=pvtemps,
                                      pdc0=1,
                                      gamma_pdc=-0.002,
                                      temp_ref=25.0)
    if math.isnan(pv_dc):
        pv_dc = 0
    return pv_dc

def simPhotovoltaicSimple(
    dni_extra=None,
    DNI=None,
    DHI=None,
    GHI=None,
    azimuth=None,
    apparent_zenith=None,
    temperature=None,
    wind_speed=None,
    surface_tilt=30,
    surface_azimuth=180,
    albedo=0.2):
    """
    Simulates a defined PV array with the Sandia PV Array Performance Model.
    The implementation is done in accordance with following tutorial:
    https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    ----------
    tmy_data: pandas.DataFrame(), required
        Weatherfile in the format of a tmy file.
    surface_tilt: int or float, optional (default:30)
        Tilt angle of of the array in degree.
    surface_azimuth: int or float, optional (default:180)
        Azimuth angle of of the array in degree. 180 degree means south,
        90 degree east and 270 west.
    albedo: float, optional (default: 0.2)
        Reflection coefficient of the surrounding area.
    losses: float, optional (default: 0.1)
        Losses due to soiling, mismatch, diode connections, dc wiring etc.
    load_module_data: Boolean, optional (default: False)
        If True the module data base is loaded from the Sandia Website.
        Otherwise it is loaded from this relative path
            '\\profiles\\PV-Modules\\sandia_modules.csv'.
    module_name: str, optional (default:'Hanwha_HSL60P6_PA_4_250T__2013_')
        Module name. The string must be existens in Sandia Module database.
    integrateInverter: bool, optional (default: True)
        If an inverter shall be added to the simulation, providing the photovoltaic output after the inverter.
    inverter_name: str, optional (default: 'ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_')
        Type of inverter.

    Returns
    --------
    """
    # automatic pd time series in future pvlib version
    # calculate airmass
    airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
    # use perez model to calculate the plane of array diffuse sky radiation
    poa_sky_diffuse = pvlib.irradiance.perez(
        surface_tilt,
        surface_azimuth,
        DHI,
        np.float64(DNI),
        dni_extra,
        apparent_zenith,
        azimuth,
        airmass,
    )
    # calculate ground diffuse with specified albedo
    poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(
        surface_tilt, GHI, albedo=albedo
    )
    # calculate angle of incidence
    aoi = pvlib.irradiance.aoi(surface_tilt, surface_azimuth, apparent_zenith, azimuth)
    # calculate plane of array irradiance
    poa_irrad = pvlib.irradiance.poa_components(aoi, np.float64(DNI), poa_sky_diffuse, poa_ground_diffuse)
    # calculate pv cell and module temperature
    temp_model = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
    pvtemps = pvlib.temperature.sapm_cell(poa_irrad["poa_global"], temperature, wind_speed, **temp_model)

    pv_dc = pvlib.pvsystem.pvwatts_dc(poa_irrad["poa_global"], temp_cell=pvtemps, pdc0=1, gamma_pdc=-0.002,
                                      temp_ref=25.0)
    if math.isnan(pv_dc):
        pv_dc = 0
    return pv_dc

@dataclass_json
@dataclass()
class PVSystemConfig:

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 time:int,
                 location:str,
                 power:float,
                 module_name:str,
                 integrate_inverter:bool,
                 inverter_name:str):
        self.parameter_string = my_simulation_parameters.get_unique_key()
        self.time = time
        self.location = location
        self.module_name = module_name
        self.integrate_inverter = integrate_inverter
        self.inverter_name = inverter_name
        self.power = power



class PVSystem(cp.Component):
    """
    Parameters:
    -----------------------------------------------------
    time:
        simulation timeline
    location: Location
        object Location with temperature and solar data
    power: float
        Power in kWp to be provided by the PV System


    Returns:
    -----------------------------------------------------
    pass
    """
    # Inputs
    TemperatureOutside = "TemperatureOutside"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    WindSpeed = "WindSpeed"

    # Outputs
    ElectricityOutput = "ElectricityOutput"

    # Similar components to connect to:
    # 1. Weather

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
        time=2019,
                 location="Aachen",
                 power=10E3,
                 load_module_data=False,
                 module_name="Hanwha_HSL60P6_PA_4_250T__2013_",
                 integrateInverter=True,
                 inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_"                 ):
        super().__init__("PVSystem", my_simulation_parameters=my_simulation_parameters)
        self.pvconfig = PVSystemConfig(my_simulation_parameters=my_simulation_parameters,
                                       location=location, power = power, module_name=module_name,
                                       integrate_inverter=integrateInverter, inverter_name=inverter_name,
                                       time=time)
        self.build(load_module_data)

        self.t_outC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                        self.TemperatureOutside,
                                                        lt.LoadTypes.Temperature,
                                                        lt.Units.Celsius,
                                                        True)

        self.DNIC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                    self.DirectNormalIrradiance,
                                                    lt.LoadTypes.Irradiance,
                                                    lt.Units.Wm2,
                                                    True)

        self.DNIextraC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                         self.DirectNormalIrradianceExtra,
                                                         lt.LoadTypes.Irradiance,
                                                         lt.Units.Wm2,
                                                         True)

        self.DHIC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                   self.DiffuseHorizontalIrradiance,
                                                   lt.LoadTypes.Irradiance,
                                                   lt.Units.Wm2,
                                                   True)

        self.GHIC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                   self.GlobalHorizontalIrradiance,
                                                   lt.LoadTypes.Irradiance,
                                                   lt.Units.Wm2,
                                                   True)

        self.azimuthC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                        self.Azimuth,
                                                        lt.LoadTypes.Any,
                                                        lt.Units.Degrees,
                                                        True)

        self.apparent_zenithC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                                self.ApparentZenith,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Degrees,
                                                                True)

        self.wind_speedC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                          self.WindSpeed,
                                                          lt.LoadTypes.Speed,
                                                          lt.Units.MeterPerSecond,
                                                          True)


        self.electricity_outputC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                             PVSystem.ElectricityOutput,
                                                             lt.LoadTypes.Electricity,
                                                             lt.Units.Watt,
                                                             False)

        self.add_default_connections(Weather, self.get_weather_default_connections())

    def get_weather_default_connections(self):
        print("setting weather default connections")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(cp.ComponentConnection(PVSystem.TemperatureOutside,weather_classname, Weather.TemperatureOutside))
        connections.append(cp.ComponentConnection(PVSystem.DirectNormalIrradiance,weather_classname, Weather.DirectNormalIrradiance))
        connections.append(cp.ComponentConnection(PVSystem.DirectNormalIrradianceExtra,weather_classname, Weather.DirectNormalIrradianceExtra))
        connections.append(cp.ComponentConnection(PVSystem.DiffuseHorizontalIrradiance,weather_classname, Weather.DiffuseHorizontalIrradiance))
        connections.append(cp.ComponentConnection(PVSystem.GlobalHorizontalIrradiance,weather_classname, Weather.GlobalHorizontalIrradiance))
        connections.append(cp.ComponentConnection(PVSystem.Azimuth,weather_classname, Weather.Azimuth))
        connections.append(cp.ComponentConnection(PVSystem.ApparentZenith,weather_classname, Weather.ApparentZenith))
        connections.append(cp.ComponentConnection(PVSystem.WindSpeed,weather_classname, Weather.WindSpeed))
        return connections

    def i_restore_state(self):
        pass

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Power: {:3.0f} kWp".format(self.pvconfig.power*1E-3))
        lines.append("Module: {}".format(self.pvconfig.module_name))
        lines.append("Inverter: {}".format(self.pvconfig.inverter_name))
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool):

        if hasattr(self, "output"):
            stsv.set_output_value(self.electricity_outputC, self.output[timestep] * self.pvconfig.power)
        else:
            DNI = stsv.get_input_value(self.DNIC)
            dni_extra = stsv.get_input_value(self.DNIextraC)
            DHI = stsv.get_input_value(self.DHIC)
            GHI = stsv.get_input_value(self.GHIC)
            azimuth = stsv.get_input_value(self.azimuthC)
            temperature = stsv.get_input_value(self.t_outC)
            wind_speed = stsv.get_input_value(self.wind_speedC)
            apparent_zenith = stsv.get_input_value(self.apparent_zenithC)

            #ac_power = self.simPhotovoltaic2(dni_extra=dni_extra,
            #                                 DNI=DNI,
            #                                 DHI=DHI,
            #                                 GHI=GHI,
            #                                 azimuth=azimuth,
            #                                 apparent_zenith=apparent_zenith,
            #                                 temperature=temperature,
            #                                 wind_speed=wind_speed)
            #ac_power = simPhotovoltaicSimple(
            #    dni_extra=dni_extra,
            #                                 DNI=DNI,
            #                                 DHI=DHI,
            #                                 GHI=GHI,
            #                                 azimuth=azimuth,
            #                                 apparent_zenith=apparent_zenith,
            #                                 temperature=temperature,
            #                                 wind_speed=wind_speed)
            ac_power = simPhotovoltaicFast(
                                            dni_extra=dni_extra,
                                            DNI=DNI,
                                            DHI=DHI,
                                            GHI=GHI,
                                            azimuth=azimuth,
                                            apparent_zenith=apparent_zenith,
                                            temperature=temperature,
                                            wind_speed=wind_speed)

            resultingvalue = ac_power * self.pvconfig.power
            # if you wanted to access the temperature forecast from the weather component:
            # val = self.simulation_repository.get_entry(Weather.Weather_Temperature_Forecast_24h)
            stsv.set_output_value(self.electricity_outputC, resultingvalue)
            self.data[timestep] = ac_power
            if timestep + 1 == self.data_length:
                database = pd.DataFrame(self.data, columns=["output"])

                database.to_csv(self.cache_filepath, sep=",", decimal=".", index=False)

    def get_coordinates(self, location="Aachen", year=2019):
        """
        Reads a test reference year file and gets the GHI, DHI and DNI from it.

        Based on the tsib project @[tsib-kotzur] (Check header)

        Parameters
        -------
        try_num: int (default: 4)
            The region number of the test reference year.
        year: int (default: 2010)
            The year. Only data for 2010 and 2030 available
        """
        # get the correct file path
        filepath = os.path.join(utils.HISIMPATH["weather"][location])

        # get the geoposition
        with open(filepath + ".dat", encoding="utf-8") as fp:
            lines = fp.readlines()
            location_name = lines[0].split(maxsplit=2)[2].replace('\n', '')
            lat = float(lines[1][20:37])
            lon = float(lines[2][15:30])
        self.location = {"name": location_name, "latitude": lat, "longitude": lon}
        self.index = pd.date_range(
            "{}-01-01 00:00:00".format(year), periods=60*24*365, freq="T", tz="Europe/Berlin"
        )

    def i_save_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def build(self,  load_module_data):

        file_exists, self.cache_filepath = utils.get_cache_file("PVSystem", self.pvconfig)

        if file_exists:
            self.output = pd.read_csv(self.cache_filepath, sep=',', decimal='.')['output'].tolist()
        else:
            self.get_coordinates(location = self.pvconfig.location, year =  self.pvconfig.time)
            # Factor to guarantee peak power based on module with 250 Wh
            self.ac_power_factor = math.ceil( ( self.pvconfig.power * 1e3 ) / 250 )
            self.data = [0] * self.my_simulation_parameters.timesteps
            self.data_length = self.my_simulation_parameters.timesteps


        self.modules = pd.read_csv(
            os.path.join(utils.HISIMPATH["photovoltaic"]["modules"]),
            index_col=0,
        )

        self.inverters = pd.read_csv(
            os.path.join(utils.HISIMPATH["photovoltaic"]["inverters"]),
            index_col=0,
        )

        self.temp_model = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]

        # load the sandia data
        if load_module_data:
            # load module data online
            modules = pvlib.pvsystem.retrieve_sam(name="SandiaMod")
            self.module = modules[self.pvconfig.module_name]
            # get inverter data
            inverters = pvlib.pvsystem.retrieve_sam("cecinverter")
            self.inverter = inverters[self.pvconfig.inverter_name]
        else:
            # load module and inverter data from csv
            module = self.modules[self.pvconfig.module_name]
            self.module = pd.to_numeric(module, errors="coerce")

            inverter = self.inverters[self.pvconfig.inverter_name]
            self.inverter = pd.to_numeric(inverter, errors="coerce")
        #self.power = self.power
        #self.module_name =  module_name
        #self.inverter_name = inverter_name
        #self.integrateInverter = integrateInverter
        #self.simPhotovoltaicSimpleJit = simPhotovoltaicSimple

    def plot(self):
        # Plots ac_power. One day is represented by 1440 steps.
        #self.ac_power.iloc[0:7200].plot()
        plt.plot(self.data)
        plt.ylabel("Power [W]")
        plt.xlabel("Time")
        plt.show()

    def interpolate(self,pd_database,year):
        firstday = pd.Series([0.0], index=[
            pd.to_datetime(datetime.datetime(year-1, 12, 31, 23, 0), utc=True).tz_convert("Europe/Berlin")])
        lastday = pd.Series(pd_database[-1], index=[
            pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert("Europe/Berlin")])
        #pd_database = pd_database.append(firstday)
        pd_database = pd_database.append(lastday)
        pd_database = pd_database.sort_index()
        return pd_database.resample('1T').asfreq().interpolate(method='linear').tolist()


    def simPhotovoltaic2(
        self,
        dni_extra=None,
        DNI=None,
        DHI=None,
        GHI=None,
        azimuth=None,
        apparent_zenith=None,
        temperature=None,
        wind_speed=None,
        surface_tilt=30,
        surface_azimuth=180,
        albedo=0.2):
        """
        Simulates a defined PV array with the Sandia PV Array Performance Model.
        The implementation is done in accordance with following tutorial:
        https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb

        Based on the tsib project @[tsib-kotzur] (Check header)

        Parameters
        ----------
        tmy_data: pandas.DataFrame(), required
            Weatherfile in the format of a tmy file.
        surface_tilt: int or float, optional (default:30)
            Tilt angle of of the array in degree.
        surface_azimuth: int or float, optional (default:180)
            Azimuth angle of of the array in degree. 180 degree means south,
            90 degree east and 270 west.
        albedo: float, optional (default: 0.2)
            Reflection coefficient of the surrounding area.
        losses: float, optional (default: 0.1)
            Losses due to soiling, mismatch, diode connections, dc wiring etc.
        load_module_data: Boolean, optional (default: False)
            If True the module data base is loaded from the Sandia Website.
            Otherwise it is loaded from this relative path
                '\\profiles\\PV-Modules\\sandia_modules.csv'.
        module_name: str, optional (default:'Hanwha_HSL60P6_PA_4_250T__2013_')
            Module name. The string must be existens in Sandia Module database.
        integrateInverter: bool, optional (default: True)
            If an inverter shall be added to the simulation, providing the photovoltaic output after the inverter.
        inverter_name: str, optional (default: 'ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_')
            Type of inverter.

        Returns
        --------
        """
        # automatic pd time series in future pvlib version
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
        # use perez model to calculate the plane of array diffuse sky radiation
        poa_sky_diffuse = pvlib.irradiance.perez(
            surface_tilt,
            surface_azimuth,
            DHI,
            np.float64(DNI),
            dni_extra,
            apparent_zenith,
            azimuth,
            airmass,
        )
        # calculate ground diffuse with specified albedo
        poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(
            surface_tilt, GHI, albedo=albedo
        )
        # calculate angle of incidence
        aoi = pvlib.irradiance.aoi(surface_tilt, surface_azimuth, apparent_zenith, azimuth)
        # calculate plane of array irradiance
        poa_irrad = pvlib.irradiance.poa_components(aoi, np.float64(DNI), poa_sky_diffuse, poa_ground_diffuse)
        # calculate pv cell and module temperature
        #temp_model = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
        pvtemps = pvlib.temperature.sapm_cell(poa_irrad["poa_global"], temperature, wind_speed, **self.temp_model)


        # calculate effective irradiance on pv module
        sapm_irr = pvlib.pvsystem.sapm_effective_irradiance(
            module=self.module,
            poa_direct=poa_irrad["poa_direct"],
            poa_diffuse=poa_irrad["poa_diffuse"],
            airmass_absolute=airmass,
            aoi=aoi,
        )
        # calculate pv performance
        sapm_out = pvlib.pvsystem.sapm(
            sapm_irr,
            module=self.module,
            temp_cell=pvtemps,
        )
        # calculate peak load of single module [W]
        peak_load = self.module.loc["Impo"] * self.module.loc["Vmpo"]
        ac_power = pd.DataFrame()
        if self.pvconfig.integrate_inverter:
            # calculate load after inverter
            iv_load = pvlib.inverter.sandia(inverter=self.inverter, v_dc=sapm_out["v_mp"], p_dc=sapm_out["p_mp"])
            ac_power = iv_load / peak_load
        else:
            # load in [kW/kWp]
            ac_power = sapm_out["p_mp"] / peak_load

        if math.isnan(ac_power):
            ac_power = 0.0

        #ac_power = ac_power * self.time_correction_factor
        #ac_power = ac_power

        #data = [DHI,
        #        DNI,
        #        GHI,
        #        dni_extra,
        #        aoi,
        #        apparent_zenith,
        #        azimuth,
        #        airmass,
        #        wind_speed]
        #if timestep % 60 == 0 and timestep < 1442:
        #    print(data)
        #    print("Timestep:{} , AcPower: {}".format(timestep, ac_power))

        return ac_power

def readTRY(location="Aachen", year=2010):
    """
    Reads a test reference year file and gets the GHI, DHI and DNI from it.

    Based on the tsib project @[tsib-kotzur] (Check header)

    Parameters
    -------
    try_num: int (default: 4)
        The region number of the test reference year.
    year: int (default: 2010)
        The year. Only data for 2010 and 2030 available
    """
    # get the correct file path
    filepath = os.path.join(utils.HISIMPATH["weather"][location])

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
            "{}-01-01 00:00:00".format(year), periods=8760, freq="H", tz="Europe/Berlin"
        )
        data["GHI"] = data["D"] + data["B"]
        data = data.rename(columns={"D": "DHI", "t": "T", "WG": "WS"})

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
    DNI = DNI.fillna(0)
    if DNI.isnull().values.any():
        raise ValueError("Something went wrong...")
    return DNI

