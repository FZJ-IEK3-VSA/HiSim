# Generic/Built-in
import datetime
import math
import os
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import pvlib
from dataclasses_json import dataclass_json
from typing import Optional, Any

from dataclasses import dataclass
from functools import lru_cache

from hisim import sim_repository
from hisim.simulationparameters import SimulationParameters
# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim import log
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
    dni_extra: Any=None,
    DNI: Any=None,
    DHI: Any=None,
    GHI: Any=None,
    azimuth: Any=None,
    apparent_zenith: Any=None,
    temperature: Any=None,
    wind_speed: Any=None,
    surface_azimuth : float = 180,
    surface_tilt : float = 30 )-> Any:
    
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

    poa_irrad = pvlib.irradiance.get_total_irradiance( surface_tilt,
                                                        surface_azimuth,
                                                        apparent_zenith,
                                                        azimuth,
                                                        DNI,
                                                        GHI,
                                                        DHI,
                                                        dni_extra )

    pvtemps = pvlib.temperature.sapm_cell(poa_irrad["poa_global"], temperature, wind_speed, **temp_model)

    pv_dc = pvlib.pvsystem.pvwatts_dc( poa_irrad[ "poa_global" ],
                                      temp_cell = pvtemps,
                                      pdc0 = 1,
                                      gamma_pdc = -0.002,
                                      temp_ref = 25.0 )
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
@dataclass
class PVSystemConfig:
    #parameter_string: str
    #my_simulation_parameters: SimulationParameters
    name: str
    time: int
    location: str
    module_name: str
    integrate_inverter: bool
    inverter_name: str
    power: float
    azimuth : float
    tilt : float
    load_module_data: bool
    source_weight: int

class PVSystem( cp.Component ):
    """
    Simulates PV Output based on weather data and peak power.

    Parameters:
    -----------------------------------------------------
    time : int, optional
        Simulation timeline. The default is 2019.
    location : str, optional
        Object Location with temperature and solar data. The default is "Aachen".
    power : float, optional
        Power in kWp to be provided by the PV System. The default is 10E3.
    load_module_data : bool
        Access the PV data base (True) or not (False). The default is False
    module_name : str, optional
        The default is "Hanwha_HSL60P6_PA_4_250T__2013_"
    integrate_inverter, bool, optional
        Consider inverter efficiency in the calculation (True) or not (False). The default is True.
    inverter_name : str, optional
        The default is "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_".
    azimuth : float, optional
        Panel azimuth from north in °. The default is 180°.
    tilt : float, optional
        Panel tilt from horizontal. The default is 90°.
    source_weight : int, optional
        Weight of component, relevant if there is more than one PV System, defines hierachy in control. The default is 1.
    name : str, optional
        Name of pv panel within simulation. The default is 'PVSystem'

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
    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config: PVSystemConfig,
                 my_simulation_repository : Optional[sim_repository.SimRepository] = None
                 )-> None:
        self.my_simulation_parameters = my_simulation_parameters
        self.pvconfig=config
        self.data: Any
        super().__init__( self.pvconfig.name + str( self.pvconfig.source_weight ), my_simulation_parameters = my_simulation_parameters )

        self.build( self.pvconfig.load_module_data, my_simulation_repository, self.pvconfig.source_weight )

        self.t_outC : cp.ComponentInput = self.add_input(self.component_name,
                                                         self.TemperatureOutside,
                                                         lt.LoadTypes.TEMPERATURE,
                                                         lt.Units.CELSIUS,
                                                         True)

        self.DNIC : cp.ComponentInput = self.add_input(self.component_name,
                                                       self.DirectNormalIrradiance,
                                                       lt.LoadTypes.IRRADIANCE,
                                                       lt.Units.WATT_PER_SQUARE_METER,
                                                       True)

        self.DNIextraC : cp.ComponentInput = self.add_input(self.component_name,
                                                            self.DirectNormalIrradianceExtra,
                                                            lt.LoadTypes.IRRADIANCE,
                                                            lt.Units.WATT_PER_SQUARE_METER,
                                                            True)

        self.DHIC: cp.ComponentInput = self.add_input(self.component_name,
                                                      self.DiffuseHorizontalIrradiance,
                                                      lt.LoadTypes.IRRADIANCE,
                                                      lt.Units.WATT_PER_SQUARE_METER,
                                                      True)

        self.GHIC: cp.ComponentInput = self.add_input(self.component_name,
                                                      self.GlobalHorizontalIrradiance,
                                                      lt.LoadTypes.IRRADIANCE,
                                                      lt.Units.WATT_PER_SQUARE_METER,
                                                      True)

        self.azimuthC : cp.ComponentInput = self.add_input(self.component_name,
                                                           self.Azimuth,
                                                           lt.LoadTypes.ANY,
                                                           lt.Units.DEGREES,
                                                           True)

        self.apparent_zenithC : cp.ComponentInput = self.add_input(self.component_name,
                                                                   self.ApparentZenith,
                                                                   lt.LoadTypes.ANY,
                                                                   lt.Units.DEGREES,
                                                                   True)

        self.wind_speedC: cp.ComponentInput = self.add_input(self.component_name,
                                                             self.WindSpeed,
                                                             lt.LoadTypes.SPEED,
                                                             lt.Units.METER_PER_SECOND,
                                                             True)


        self.electricity_outputC : cp.ComponentOutput = self.add_output(
            object_name=self.component_name, field_name=PVSystem.ElectricityOutput, load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT, postprocessing_flag=lt.InandOutputType.PRODUCTION)

        self.add_default_connections(Weather, self.get_weather_default_connections())

    @staticmethod
    def get_default_config():
        config= PVSystemConfig(
                        name= 'PVSystem',
                        time= 2019,
                        location= "Aachen",
                        module_name= "Hanwha_HSL60P6_PA_4_250T__2013_",
                        integrate_inverter= True,
                        inverter_name= "ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
                        power= 10E3,
                        azimuth= 180,
                        tilt= 30,
                        load_module_data= False,
                        source_weight= 1)
        return config
    def get_weather_default_connections(self):
        log.information("setting weather default connections")
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
        lines.append("Name: {}".format(self.component_name))
        lines.append("Power: {:3.0f} kWp".format(self.pvconfig.power*1E-3))
        lines.append("Module: {}".format(self.pvconfig.module_name))
        lines.append("Inverter: {}".format(self.pvconfig.inverter_name))
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool) -> None:

        if hasattr(self, "output"):
            #if(len(self.output) < timestep)
             #   raise Exception("Somehow the precalculated list of values for the PV system seems to be incorrect. Please delete the cache.")
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
                                            wind_speed=wind_speed,
                                            surface_azimuth = self.pvconfig.azimuth,
                                            surface_tilt = self.pvconfig.tilt )

            resultingvalue = ac_power * self.pvconfig.power
            # if you wanted to access the temperature forecast from the weather component:
            # val = self.simulation_repository.get_entry(Weather.Weather_Temperature_Forecast_24h)
            stsv.set_output_value(self.electricity_outputC, resultingvalue)
            self.data[timestep] = ac_power
            if timestep + 1 == self.data_length:
                database = pd.DataFrame(self.data, columns=["output"])

                database.to_csv(self.cache_filepath, sep=",", decimal=".", index=False)
                
        if self.my_simulation_parameters.system_config.predictive == True:
            last_forecast_timestep = int( timestep + self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep )
            if ( last_forecast_timestep > len( self.output ) ):
                last_forecast_timestep = len( self.output )
            pvforecast = [ self.output[ t ] * self.pvconfig.power for t in range( timestep, last_forecast_timestep ) ]
            self.simulation_repository.set_dynamic_entry( component_type = lt.ComponentType.PV, source_weight = self.source_weight, entry = pvforecast )
            
            if timestep == 1: 
                #delete weather data for PV preprocessing from dictionary -> save memory
                if self.simulation_repository.exist_entry( Weather.Weather_DirectNormalIrradianceExtra_yearly_forecast ):
                    self.simulation_repository.delete_entry( Weather.Weather_DirectNormalIrradianceExtra_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_DirectNormalIrradiance_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_DiffuseHorizontalIrradiance_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_GlobalHorizontalIrradiance_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_Azimuth_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_ApparentZenith_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_TemperatureOutside_yearly_forecast )
                    self.simulation_repository.delete_entry( Weather.Weather_WindSpeed_yearly_forecast )

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

    def i_save_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def build(self, load_module_data : bool, my_simulation_repository : Optional[sim_repository.SimRepository], source_weight : int) -> None:
        
        self.source_weight = source_weight
        
        log.information(self.pvconfig.to_json())  # type: ignore
        file_exists, self.cache_filepath = utils.get_cache_file("PVSystem", self.pvconfig, self.my_simulation_parameters)

        if file_exists:
            self.output = pd.read_csv(self.cache_filepath, sep=',', decimal='.')['output'].tolist()
            if len(self.output) !=        self.my_simulation_parameters.timesteps:
                raise Exception("Reading the cached PV values seems to have failed. Expected "
                                + str(self.my_simulation_parameters.timesteps) + " values, but got " + str(len(self.output )))
        else:
            self.get_coordinates(location = self.pvconfig.location, year =  self.pvconfig.time )
            # Factor to guarantee peak power based on module with 250 Wh
            self.ac_power_factor = math.ceil( ( self.pvconfig.power * 1e3 ) / 250 )
            
            #when predictive control is activated, the PV simulation is run beforhand to make forecasting easier
            if self.my_simulation_parameters.system_config.predictive and my_simulation_repository is not None:
                #get yearly weather data from dictionary
                dni_extra = my_simulation_repository.get_entry( Weather.Weather_DirectNormalIrradianceExtra_yearly_forecast )
                DNI = my_simulation_repository.get_entry( Weather.Weather_DirectNormalIrradiance_yearly_forecast )
                DHI = my_simulation_repository.get_entry( Weather.Weather_DiffuseHorizontalIrradiance_yearly_forecast )
                GHI = my_simulation_repository.get_entry( Weather.Weather_GlobalHorizontalIrradiance_yearly_forecast )
                azimuth = my_simulation_repository.get_entry( Weather.Weather_Azimuth_yearly_forecast )
                apparent_zenith = my_simulation_repository.get_entry( Weather.Weather_ApparentZenith_yearly_forecast )
                temperature = my_simulation_repository.get_entry( Weather.Weather_TemperatureOutside_yearly_forecast )
                wind_speed = my_simulation_repository.get_entry( Weather.Weather_WindSpeed_yearly_forecast )
                
                x= [ ]
                for i in range( self.my_simulation_parameters.timesteps ):
                    x.append( simPhotovoltaicFast( dni_extra[ i ], DNI[ i ], DHI[ i ], GHI[ i ], azimuth[ i ], apparent_zenith[ i ], temperature[ i ], wind_speed[ i ], self.pvconfig.azimuth, self.pvconfig.tilt ) )

                self.output = x
                
                database = pd.DataFrame( self.output, columns = [ "output" ] )

                database.to_csv( self.cache_filepath, sep=",", decimal=".", index=False )
                
            else:
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

    def plot(self) -> None:
        # Plots ac_power. One day is represented by 1440 steps.
        #self.ac_power.iloc[0:7200].plot()
        plt.plot(self.data)
        plt.ylabel("Power [W]")
        plt.xlabel("Time")
        plt.show()

    def interpolate(self,pd_database: Any,year: Any) -> Any:
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
        #    log.information(data)
        #    log.information("Timestep:{} , AcPower: {}".format(timestep, ac_power))

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

