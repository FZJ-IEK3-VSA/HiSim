"""PV system module."""

# clean

# Generic/Built-in
import datetime
import enum
import math
import os
from dataclasses import dataclass
from typing import Any, List, Tuple, Optional

import numpy as np
import pandas as pd
import pvlib
from dataclasses_json import dataclass_json

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from hisim.component import ConfigBase, OpexCostDataClass
from hisim.components.weather import Weather
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.simulationparameters import SimulationParameters

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
Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner.
Future grid load of the residential building sector. No. RWTH-2018-231872. Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following repository:
https://github.com/FZJ-IEK3-VSA/tsib
"""


class PVLibModuleAndInverterEnum(enum.Enum):
    """Class to determine what pvlib database for phtotovoltaic modules and inverters should be used.

    https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.pvsystem.retrieve_sam.html.
    """

    SANDIA_MODULE_DATABASE = 1
    SANDIA_INVERTER_DATABASE = 2
    CEC_MODULE_DATABASE = 3
    CEC_INVERTER_DATABASE = 4
    ANTON_DRIESSE_INVERTER_DATABASE = 5


@dataclass_json
@dataclass
class PVSystemConfig(ConfigBase):
    """PVSystemConfig class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return PVSystem.get_full_classname()

    name: str
    time: int
    location: str
    module_name: str
    integrate_inverter: bool
    inverter_name: str
    module_database: PVLibModuleAndInverterEnum
    inverter_database: PVLibModuleAndInverterEnum
    power_in_watt: float
    azimuth: float
    tilt: float
    load_module_data: bool
    source_weight: int
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: lifetime in years
    lifetime: float
    predictive: bool
    predictive_control: bool
    prediction_horizon: Optional[int]

    @classmethod
    def get_default_pv_system(cls) -> "PVSystemConfig":
        """Gets a default PV system."""
        power_in_watt = 10e3  # W
        return PVSystemConfig(
            time=2019,
            power_in_watt=power_in_watt,
            load_module_data=False,
            integrate_inverter=True,
            module_database=PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
            inverter_database=PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
            module_name="Hanwha HSL60P6-PA-4-250T [2013]",
            inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
            name="PVSystem",
            azimuth=180,
            tilt=30,
            source_weight=0,
            location="Aachen",
            co2_footprint=power_in_watt * 1e-3 * 330.51,  # value from emission_factros_and_costs_devices.csv
            cost=power_in_watt * 1e-3 * 794.41,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.01,  # source: https://solarenergie.de/stromspeicher/preise
            lifetime=25,  # value from emission_factros_and_costs_devices.csv
            predictive=False,
            predictive_control=False,
            prediction_horizon=None,
        )

    @classmethod
    def get_scaled_pv_system(
        cls,
        rooftop_area_in_m2: float,
        share_of_maximum_pv_power: float = 1.0,
        module_name: str = "Hanwha HSL60P6-PA-4-250T [2013]",
        module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
        load_module_data: bool = False,
    ) -> "PVSystemConfig":
        """Gets a default PV system with scaling according to rooftop area."""
        total_pv_power_in_watt = cls.size_pv_system(
            rooftop_area_in_m2=rooftop_area_in_m2,
            share_of_maximum_pv_power=share_of_maximum_pv_power,
            module_name=module_name,
            module_database=module_database,
        )
        return PVSystemConfig(
            time=2019,
            power_in_watt=total_pv_power_in_watt,
            load_module_data=load_module_data,
            module_name=module_name,
            integrate_inverter=True,
            inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
            module_database=module_database,
            inverter_database=PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
            name="PVSystem",
            azimuth=180,
            tilt=30,
            source_weight=0,
            location="Aachen",
            co2_footprint=total_pv_power_in_watt * 1e-3 * 330.51,  # value from emission_factros_and_costs_devices.csv
            cost=total_pv_power_in_watt * 1e-3 * 794.41,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.01,  # source: https://solarenergie.de/stromspeicher/preise
            lifetime=25,  # value from emission_factros_and_costs_devices.csv
            predictive=False,
            predictive_control=False,
            prediction_horizon=None,
        )

    @classmethod
    def size_pv_system(
        cls,
        rooftop_area_in_m2: float,
        share_of_maximum_pv_power: float,
        module_name: str,
        module_database: PVLibModuleAndInverterEnum,
    ) -> float:
        """Size the pv system according to the rooftop type and the share of the maximum pv power that should be used."""

        # get area and power of module
        if (
            module_name == "Hanwha HSL60P6-PA-4-250T [2013]"
            and module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.65
            module_power_in_watt = 250
            # this is equal to an efficiency of 15,15%

        elif (
            module_name == "Trina Solar TSM-410DE09"
            and module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.91
            module_power_in_watt = 410
            # this is equal to an efficiency of 21,47%

        # pv module efficiency calculation see:
        # https://www.ess-kempfle.de/ratgeber/ertrag/pv-ertrag/#:~:text=So%20berechnen%20Sie%20den%20Wirkungsgrad,liegt%20bei%201.000%20W%2Fm%C2%B2.
        else:
            raise ValueError(
                f"Module name or module database {module_name} {module_database} not given in this function. Please check or add your module information."
            )

        # scale rooftop area with limiting factor due to shading and obstacles like chimneys etc.
        # see p.18 in following paper https://www.mdpi.com/1996-1073/15/15/5536 (Stanleys work)
        limiting_factor_for_rooftop = 0.6
        effective_rooftop_area_in_m2 = rooftop_area_in_m2 * limiting_factor_for_rooftop

        total_pv_power_in_watt = (
            effective_rooftop_area_in_m2 / module_area_in_m2 * module_power_in_watt
        ) * share_of_maximum_pv_power

        return total_pv_power_in_watt


class PVSystem(cp.Component):
    """Simulates PV Output based on weather data and peak power.

    Parameters
    ----------
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
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: PVSystemConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initialize the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.pvconfig = config
        self.ac_power_ratios_for_all_timesteps_data: List = []
        self.ac_power_ratios_for_all_timesteps_output: List = []
        self.cache_filepath: str
        self.modules: Any
        self.inverter: Any
        self.inverters: Any
        self.module: Any
        self.coordinates: Any
        self.data_length: int = self.my_simulation_parameters.timesteps
        self.temperature_model_parameters = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
            "open_rack_glass_glass"
        ]
        super().__init__(
            self.pvconfig.name + "_w" + str(self.pvconfig.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.dni_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.dni_extra_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradianceExtra,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.dhi_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.ghi_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.azimuth_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.Azimuth, lt.LoadTypes.ANY, lt.Units.DEGREES, True
        )

        self.apparent_zenith_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )

        self.wind_speed_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WindSpeed,
            lt.LoadTypes.SPEED,
            lt.Units.METER_PER_SECOND,
            True,
        )

        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description=f"here a description for PV {self.ElectricityOutput} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())

    @staticmethod
    def get_default_config(power_in_watt: float = 10e3, source_weight: int = 1) -> Any:
        """Get default config."""
        config = PVSystemConfig(
            name="PVSystem",
            time=2019,
            location="Aachen",
            module_name="Hanwha HSL60P6-PA-4-250T [2013]",
            integrate_inverter=True,
            inverter_name="ABB__MICRO_0_25_I_OUTD_US_208_208V__CEC_2014_",
            module_database=PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE,
            inverter_database=PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
            power_in_watt=power_in_watt,
            azimuth=180,
            tilt=30,
            load_module_data=False,
            source_weight=source_weight,
            co2_footprint=power_in_watt * 1e-3 * 130.7,  # value from emission_factros_and_costs_devices.csv
            cost=power_in_watt * 1e-3 * 535.81,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.01,  # source: https://solarenergie.de/stromspeicher/preise
            lifetime=25,  # value from emission_factros_and_costs_devices.csv
            prediction_horizon=None,
            predictive=False,
            predictive_control=False,
        )
        return config

    @staticmethod
    def get_cost_capex(config: PVSystemConfig) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost, config.co2_footprint, config.lifetime

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for PV."""
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=self.calc_maintenance_cost(),
            co2_footprint=0,
            consumption=0,
        )

        return opex_cost_data_class

    def get_default_connections_from_weather(self):
        """Get default connections from weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                PVSystem.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DirectNormalIrradiance,
                weather_classname,
                Weather.DirectNormalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DirectNormalIrradianceExtra,
                weather_classname,
                Weather.DirectNormalIrradianceExtra,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.DiffuseHorizontalIrradiance,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                PVSystem.GlobalHorizontalIrradiance,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(cp.ComponentConnection(PVSystem.Azimuth, weather_classname, Weather.Azimuth))
        connections.append(cp.ComponentConnection(PVSystem.ApparentZenith, weather_classname, Weather.ApparentZenith))
        connections.append(cp.ComponentConnection(PVSystem.WindSpeed, weather_classname, Weather.WindSpeed))
        return connections

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""

        # check if results could be found in cache and if the lists have the right length
        if (
            hasattr(self, "ac_power_ratios_for_all_timesteps_output")
            and len(self.ac_power_ratios_for_all_timesteps_output) == self.data_length
        ):
            stsv.set_output_value(
                self.electricity_output_channel,
                self.ac_power_ratios_for_all_timesteps_output[timestep] * self.pvconfig.power_in_watt,
            )

        # calculate pv system outputs with pvlib
        else:
            dni = stsv.get_input_value(self.dni_channel)
            dni_extra = stsv.get_input_value(self.dni_extra_channel)
            dhi = stsv.get_input_value(self.dhi_channel)
            ghi = stsv.get_input_value(self.ghi_channel)
            azimuth = stsv.get_input_value(self.azimuth_channel)
            temperature = stsv.get_input_value(self.t_out_channel)
            wind_speed = stsv.get_input_value(self.wind_speed_channel)
            apparent_zenith = stsv.get_input_value(self.apparent_zenith_channel)

            ac_power_ratio = self.simphotovoltaic_two(
                dni_extra=dni_extra,
                dni=dni,
                dhi=dhi,
                ghi=ghi,
                azimuth=azimuth,
                apparent_zenith=apparent_zenith,
                temperature=temperature,
                wind_speed=wind_speed,
                surface_azimuth=self.pvconfig.azimuth,
                surface_tilt=self.pvconfig.tilt,
            )

            ac_power_in_watt = ac_power_ratio * self.pvconfig.power_in_watt

            # if you wanted to access the temperature forecast from the weather component:
            # val = self.simulation_repository.get_entry(Weather.Weather_Temperature_Forecast_24h)

            stsv.set_output_value(self.electricity_output_channel, ac_power_in_watt)

            # cache results at the end of the simulation
            self.ac_power_ratios_for_all_timesteps_data[timestep] = ac_power_ratio

            if timestep + 1 == self.data_length:
                dict_with_results = {
                    "output_power": self.ac_power_ratios_for_all_timesteps_data,
                }

                database = pd.DataFrame(
                    dict_with_results,
                    columns=[
                        "output_power",
                    ],
                )

                database.to_csv(self.cache_filepath, sep=",", decimal=".", index=False)

        if self.pvconfig.predictive_control and self.pvconfig.prediction_horizon is not None:
            last_forecast_timestep = int(
                timestep + self.pvconfig.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
            if last_forecast_timestep > len(self.ac_power_ratios_for_all_timesteps_output):
                last_forecast_timestep = len(self.ac_power_ratios_for_all_timesteps_output)
            pvforecast = [
                self.ac_power_ratios_for_all_timesteps_output[t] * self.pvconfig.power_in_watt
                for t in range(timestep, last_forecast_timestep)
            ]
            self.simulation_repository.set_dynamic_entry(
                component_type=lt.ComponentType.PV,
                source_weight=self.pvconfig.source_weight,
                entry=pvforecast,
            )

            if timestep == 1:
                # delete weather data for PV preprocessing from dictionary -> save memory
                if SingletonSimRepository().exist_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST
                ):
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST
                    )
                    SingletonSimRepository().delete_entry(key=SingletonDictKeyEnum.WEATHERAZIMUTHYEARLYFORECAST)
                    SingletonSimRepository().delete_entry(key=SingletonDictKeyEnum.WEATHERAPPARENTZENITHYEARLYFORECAST)
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST
                    )
                    SingletonSimRepository().delete_entry(key=SingletonDictKeyEnum.WEATHERWINDSPEEDYEARLYFORECAST)

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def write_to_report(self):
        """Write to the report."""
        return self.pvconfig.get_string_dict()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the component for the simulation."""
        log.information(self.pvconfig.to_json())  # type: ignore
        file_exists, self.cache_filepath = utils.get_cache_file(
            "PVSystem", self.pvconfig, self.my_simulation_parameters
        )

        if file_exists:
            log.information("Get PV results from cache.")
            self.ac_power_ratios_for_all_timesteps_output = pd.read_csv(self.cache_filepath, sep=",", decimal=".")[
                "output_power"
            ].tolist()

            if len(self.ac_power_ratios_for_all_timesteps_output) != self.my_simulation_parameters.timesteps:
                raise Exception(
                    "Reading the cached PV values seems to have failed. Expected "
                    + str(self.my_simulation_parameters.timesteps)
                    + " values, but got "
                    + str(len(self.ac_power_ratios_for_all_timesteps_output))
                )
        else:
            if self.simulation_repository.exist_entry("weather_location"):
                self.coordinates = self.simulation_repository.get_entry("weather_location")
            else:
                raise KeyError(
                    "The key weather_location was not found in the repository."
                    "Please check in your system setup if the weather component was added to the simulator before the pv system."
                )

            # read module from pvlib database online or read from csv files in hisim/inputs/photovoltaic/data_processed
            self.module = self.get_modules_from_database(
                module_database=self.pvconfig.module_database,
                load_module_data=self.pvconfig.load_module_data,
                module_name=self.pvconfig.module_name,
            )

            # read inverter from pvlib database online or read from csv files in hisim/inputs/photovoltaic/data_processed
            self.inverter = self.get_inverters_from_database(
                inverter_database=self.pvconfig.inverter_database,
                load_module_data=self.pvconfig.load_module_data,
                inverter_name=self.pvconfig.inverter_name,
            )

            # when predictive control is activated, the PV simulation is run beforhand to make forecasting easier
            if self.pvconfig.predictive_control:
                # get yearly weather data from dictionary
                dni_extra = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST
                )
                dni = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST
                )
                dhi = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST
                )
                ghi = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST
                )
                azimuth = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WEATHERAZIMUTHYEARLYFORECAST)
                apparent_zenith = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERAPPARENTZENITHYEARLYFORECAST
                )
                temperature = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST
                )
                wind_speed = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WEATHERWINDSPEEDYEARLYFORECAST)

                x_simplephotovoltaic = []
                for i in range(self.my_simulation_parameters.timesteps):
                    # calculate outputs
                    ac_power_ratio = self.simphotovoltaic_two(
                        dni_extra=dni_extra[i],
                        dni=dni[i],
                        dhi=dhi[i],
                        ghi=ghi[i],
                        azimuth=azimuth[i],
                        apparent_zenith=apparent_zenith[i],
                        temperature=temperature[i],
                        wind_speed=wind_speed[i],
                        surface_azimuth=self.pvconfig.azimuth,
                        surface_tilt=self.pvconfig.tilt,
                    )

                    # append lists
                    x_simplephotovoltaic.append(ac_power_ratio)

                self.ac_power_ratios_for_all_timesteps_output = x_simplephotovoltaic

                # cache predictive control results
                dict_with_results = {
                    "output_power": self.ac_power_ratios_for_all_timesteps_output,
                }

                database = pd.DataFrame(
                    dict_with_results,
                    columns=[
                        "output_power",
                    ],
                )

                database.to_csv(self.cache_filepath, sep=",", decimal=".", index=False)

            else:
                # create empty result lists as a preparation for caching in i_simulate

                self.ac_power_ratios_for_all_timesteps_data = [0] * self.my_simulation_parameters.timesteps

        if self.pvconfig.predictive:
            pv_forecast_yearly = [
                self.ac_power_ratios_for_all_timesteps_output[t] * self.pvconfig.power_in_watt
                for t in range(self.my_simulation_parameters.timesteps)
            ]
            SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.PVFORECASTYEARLY, entry=pv_forecast_yearly)

    def interpolate(self, pd_database: Any, year: Any) -> Any:
        """Interpolates."""
        lastday = pd.Series(
            pd_database[-1],
            index=[pd.to_datetime(datetime.datetime(year, 12, 31, 22, 59), utc=True).tz_convert("Europe/Berlin")],
        )

        pd_database = pd_database.append(lastday)
        pd_database = pd_database.sort_index()
        return pd_database.resample("1T").asfreq().interpolate(method="linear").tolist()

    def get_modules_from_database(self, module_database: Any, load_module_data: bool, module_name: str) -> Any:
        """Get modules from pvlib module database."""

        # get modules from pvlib database online (TODO: test if this works, it has not been fully tested yet)
        if load_module_data is True:
            if module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                modules = pvlib.pvsystem.retrieve_sam(name="SandiaMod")
            elif module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                modules = pvlib.pvsystem.retrieve_sam(name="CECMod")
            else:
                raise KeyError(f"The module database {module_database} is not integrated in the PV component here.")

            # choose module from modules database
            module = modules[module_name]

        # get modules from input data csv files
        else:
            if module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                modules = pd.read_csv(
                    os.path.join(utils.HISIMPATH["photovoltaic"]["sandia_modules_new"]),
                )

            # note that you can import cec module database but the calculations of the pv system can only be done with sandia!
            elif module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                modules = pd.read_csv(os.path.join(utils.HISIMPATH["photovoltaic"]["cec_modules"]))
                log.warning(
                    "You can import pv cec modules but the pvlib calculations (simphotovoltaic_two) only work with the sandia modules unfortunately."
                )
            else:
                raise KeyError(f"The module database {module_database} is not integrated in the PV component here.")

            # choose module from modules database
            module = modules.loc[modules["Name"] == module_name]

            # transform column object types to numeric types
            for column in module.columns:
                module.loc[:, column] = pd.to_numeric(module.loc[:, column], errors="coerce")

            # transform module dataframe to dict
            module = module.to_dict(orient="records")[0]

        return module

    def get_inverters_from_database(self, inverter_database: Any, load_module_data: bool, inverter_name: str) -> Any:
        """Get inverters from pvlib module database."""

        # get inverters from pvlib database online
        if load_module_data is True:
            if inverter_database in (
                PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
                PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
            ):
                # get inverter data (for both sandia and cec inverters the same database is taken):
                # see docs: https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.pvsystem.retrieve_sam.html
                inverters = pvlib.pvsystem.retrieve_sam("CECInverter")
                inverter = inverters[inverter_name]
            elif inverter_database == PVLibModuleAndInverterEnum.ANTON_DRIESSE_INVERTER_DATABASE:
                inverters = pvlib.pvsystem.retrieve_sam("ADRInverter")
                inverter = inverters[inverter_name]
            else:
                raise KeyError(f"The inverter database {inverter_database} is not integrated in the PV component here.")

        # get inverters from input data csv files
        else:
            # this is the old csv file used in hisim
            if inverter_database == PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE:
                inverters = pd.read_csv(
                    os.path.join(utils.HISIMPATH["photovoltaic"]["sandia_inverters"]),
                    index_col=0,
                )
                # choose inverter from inverters database
                inverter = inverters[inverter_name]
                # transform to numeric types
                inverter = pd.to_numeric(inverter, errors="coerce")

            # this would be the new one, but not tested yet
            elif inverter_database == PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE:
                inverters = pd.read_csv(
                    os.path.join(utils.HISIMPATH["photovoltaic"]["cec_inverters"]),
                )
                # choose inverter from inverters database
                inverter = inverters.loc[inverters["Name"] == inverter_name]

                # transform column object types to numeric types
                for column in inverter.columns:
                    inverter[column] = pd.to_numeric(inverter[column], errors="coerce")

                # transform inverter dataframe to dict
                inverter = inverter.to_dict(orient="records")[0]

            else:
                raise KeyError(f"The inverter database {inverter_database} is not integrated in the PV component here.")

        return inverter

    def simphotovoltaic_two(
        self,
        dni_extra=None,
        dni=None,
        dhi=None,
        ghi=None,
        azimuth=None,
        apparent_zenith=None,
        temperature=None,
        wind_speed=None,
        surface_tilt=30,
        surface_azimuth=180,
        albedo=0.2,
    ):
        r"""Simulates a defined PV array with the Sandia PV Array Performance Model.

        The implementation is done in accordance with following tutorial:
        https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb
        https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.sapm.html#pvlib.pvsystem.sapm

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
        apparent_zenith: Any
            Apparent zenith.
        azimuth: int, float
            Azimuth.
        dni: Any
            direct normal irradiance.
        ghi: Any
            global horizontal irradiance.
        dhi: Any
            direct horizontal irradiance.
        dni_extra: Any
            direct normal irradiance extra.
        temperature: Any
            tempertaure.
        wind_speed: Any
            wind_speed.

        Returns
        -------
        ac_power: Any
            ac power

        """
        # automatic pd time series in future pvlib version
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
        # use perez model to calculate the plane of array diffuse sky radiation
        poa_sky_diffuse = pvlib.irradiance.perez(
            surface_tilt,
            surface_azimuth,
            dhi,
            np.float64(dni),
            dni_extra,
            apparent_zenith,
            azimuth,
            airmass,
        )
        # calculate ground diffuse with specified albedo
        poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(surface_tilt, ghi, albedo=albedo)
        # calculate angle of incidence
        aoi = pvlib.irradiance.aoi(surface_tilt, surface_azimuth, apparent_zenith, azimuth)
        # calculate plane of array irradiance
        poa_irrad = pvlib.irradiance.poa_components(aoi, np.float64(dni), poa_sky_diffuse, poa_ground_diffuse)
        # calculate pv cell and module temperature

        pvtemps = pvlib.temperature.sapm_cell(
            poa_irrad["poa_global"],
            temperature,
            wind_speed,
            **self.temperature_model_parameters,
        )

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
        module_peak_load_in_watt = self.module["Impo"] * self.module["Vmpo"]
        ac_power_ratio: float

        if self.pvconfig.integrate_inverter:
            # calculate load after inverter
            inverter_load_in_watt = pvlib.inverter.sandia(
                inverter=self.inverter, v_dc=sapm_out["v_mp"], p_dc=sapm_out["p_mp"]
            )
            # if inverter load is nan, make it zero otherwise ac_power_ratio will be nan also
            if math.isnan(inverter_load_in_watt):
                inverter_load_in_watt = 0

            ac_power_ratio = inverter_load_in_watt / module_peak_load_in_watt
        else:
            # load in [kW/kWp]
            ac_power_ratio = sapm_out["p_mp"] / module_peak_load_in_watt

        if math.isnan(ac_power_ratio):  # type: ignore
            ac_power_ratio = 0.0

        return ac_power_ratio
