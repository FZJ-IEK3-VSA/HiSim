"""PV system module."""

# clean

# Generic/Built-in
import datetime
import enum
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pvlib
from dataclasses_json import dataclass_json

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from hisim.component import ConfigBase, OpexCostDataClass, CapexCostDataClass
from hisim.components.weather import Weather
from hisim.sim_repository_singleton import (
    SingletonSimRepository,
    SingletonDictKeyEnum,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiTagEnumClass,
    KpiEntry,
)
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions


__authors__ = "Vitor Hugo Bellotto Zago, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"

"""
The functions cited in this module are to some degree based on the
tsib project:

[tsib-kotzur]:
Kotzur, Leander, Detlef Stolten, and Hermann-Josef Wagner.
Future grid load of the residential building sector. No. RWTH-2018-231872.
Lehrstuhl für Brennstoffzellen (FZ Jülich), 2019.
ID: http://hdl.handle.net/2128/21115
    http://nbn-resolving.org/resolver?verb=redirect&identifier=urn:nbn:de:0001-2019020614

The implementation of the tsib project can be found under the following
repository: https://github.com/FZJ-IEK3-VSA/tsib

The CEC module and inverter database was downloaded from:
https://github.com/NREL/SAM/tree/patch/deploy/libraries
"""


class PVLibModuleAndInverterEnum(enum.Enum):
    """Module and inverter database options.

    Class to determine what pvlib database for phtotovoltaic modules
    and inverters should be used.

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

    building_name: str
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
    # [0..1], how much pv potential is used
    share_of_maximum_pv_potential: float
    load_module_data: bool
    source_weight: int
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float]
    #: lifetime in years
    lifetime_in_years: Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]
    predictive: bool
    predictive_control: bool
    prediction_horizon: Optional[int]

    @classmethod
    def get_default_pv_system(
        cls,
        name: str = "PVSystem",
        power_in_watt: float = 10e3,
        source_weight: int = 0,
        share_of_maximum_pv_potential: float = 1.0,
        location: str = "Aachen",
        building_name: str = "BUI1",
        module_name: str = "Trina Solar TSM-435NE09RC.05",
        module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,  # noqa: E501
        inverter_name: str = "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]",
        inverter_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,  # noqa: E501
    ) -> "PVSystemConfig":
        """Gets a default PV system."""
        power_in_watt = power_in_watt * share_of_maximum_pv_potential
        return PVSystemConfig(
            building_name=building_name,
            time=2019,
            power_in_watt=power_in_watt,
            load_module_data=False,
            integrate_inverter=True,
            module_database=module_database,
            inverter_database=inverter_database,
            module_name=module_name,
            inverter_name=inverter_name,
            name=name,
            azimuth=180,
            tilt=30,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            source_weight=source_weight,
            location=location,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            predictive=False,
            predictive_control=False,
            prediction_horizon=None,
        )

    @classmethod
    def get_scaled_pv_system(
        cls,
        rooftop_area_in_m2: float,
        name: str = "PVSystem",
        share_of_maximum_pv_potential: float = 1.0,
        module_name: str = "Trina Solar TSM-435NE09RC.05",
        module_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE,  # noqa: E501
        inverter_name: str = "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]",
        inverter_database: PVLibModuleAndInverterEnum = PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
        location: str = "Aachen",
        building_name: str = "BUI1",
        load_module_data: bool = False,
    ) -> "PVSystemConfig":
        """Gets a default PV system with scaling according to rooftop area."""
        total_pv_power_in_watt = cls.size_pv_system(
            rooftop_area_in_m2=rooftop_area_in_m2,
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            module_name=module_name,
            module_database=module_database,
        )
        config = PVSystemConfig.get_default_pv_system(
            name=name,
            location=location,
            power_in_watt=total_pv_power_in_watt,
            building_name=building_name,
            module_name=module_name,
            module_database=module_database,
            inverter_name=inverter_name,
            inverter_database=inverter_database,
        )
        config.load_module_data = load_module_data
        return config

    @classmethod
    def size_pv_system(
        cls,
        rooftop_area_in_m2: float,
        share_of_maximum_pv_potential: float,
        module_name: str,
        module_database: PVLibModuleAndInverterEnum,
    ) -> float:
        """Size PV system.

        Size the pv system according to the rooftop area and the share of
        the maximum pv power that should be used.
        """

        # get area and power of module
        if (
            module_name == "Hanwha HSL60P6-PA-4-250T [2013]"
            and module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.65
            module_power_in_watt = 250.0
            # this is equal to an efficiency of 15,15%

        elif (
            module_name == "Trina Solar TSM-435NE09RC.05"
            and module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
        ):
            module_area_in_m2 = 1.98
            module_power_in_watt = 435.16
            # this is equal to an efficiency of 21,98%

        # pv module efficiency calculation see:
        # https://www.ess-kempfle.de/ratgeber/ertrag/pv-ertrag/#:~:text=So%20berechnen%20Sie%20den%20Wirkungsgrad,liegt%20bei%201.000%20W%2Fm%C2%B2.
        else:
            raise ValueError(
                f"""Module name or module database {module_name}
                {module_database} not given in this function.
                Please check or add your module information."""
            )

        # scale rooftop area with limiting factor due to shading and
        # obstacles like chimneys etc. see p.18 in following paper:
        # https://www.mdpi.com/1996-1073/15/15/5536 (Stanley's work)
        limiting_factor_for_rooftop = 0.6
        effective_rooftop_area_in_m2 = rooftop_area_in_m2 * limiting_factor_for_rooftop

        total_pv_power_in_watt = (
            effective_rooftop_area_in_m2 / module_area_in_m2 * module_power_in_watt
        ) * share_of_maximum_pv_potential

        return round(total_pv_power_in_watt, 2)


class PVSystem(cp.Component):
    """Simulates PV Output based on weather data and peak power.

    Parameters
    ----------
    time : int, optional
        Simulation timeline. The default is 2019.
    location : str, optional
        Object Location with temperature and solar data.
        The default is "Aachen".
    power : float, optional
        Power in kWp to be provided by the PV System.
        The default is 10E3.
    load_module_data : bool
        Access the PV data base (True) or not (False).
        The default is False
    module_name : str, optional
        The default is "Trina Solar TSM-435NE09RC.05"
    integrate_inverter, bool, optional
        Consider inverter efficiency in the calculation (True) or not (False).
        The default is True.
    inverter_name : str, optional
        The default is "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]".
    azimuth : float, optional
        Panel azimuth from north in °. The default is 180°.
    tilt : float, optional
        Panel tilt from horizontal. The default is 90°.
    source_weight : int, optional
        Weight of component, relevant if there is more than one PV System,
        defines hierachy in control. The default is 1.
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
    # Additional output channels must not contain 'ElectricityOutput' or
    # dynamic components will fail.
    ElectricityEnergyOutput = "ElectricityEnergyOutput"

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
        self.temperature_model_parameters = (
            pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["pvsyst"]["freestanding"]
            if self.pvconfig.module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE
            else pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
        )
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
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
            self.component_name,
            self.Azimuth,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
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
                lt.ComponentType.PV,
            ],
            output_description="Electricity output of the PV system.",
        )

        self.electricity_energy_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityEnergyOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description=f"Here a description for PV {self.ElectricityEnergyOutput} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())

    @staticmethod
    def get_default_config(
        power_in_watt: float = 10e3,
        source_weight: int = 1,
        share_of_maximum_pv_potential: float = 1.0,
        building_name: str = "BUI1",
    ) -> Any:
        """Get default config."""
        config = PVSystemConfig(
            building_name=building_name,
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
            share_of_maximum_pv_potential=share_of_maximum_pv_potential,
            load_module_data=False,
            source_weight=source_weight,
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            lifetime_in_years=None,
            prediction_horizon=None,
            predictive=False,
            predictive_control=False,
        )
        return config

    @staticmethod
    def get_cost_capex(config: PVSystemConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.PV
        kpi_tag = KpiTagEnumClass.ROOFTOP_PV
        unit = lt.Units.KILOWATT
        size_of_energy_system = config.power_in_watt * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for PV."""
        production_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.config.name
                and output.load_type == lt.LoadTypes.ELECTRICITY
                and output.field_name == self.ElectricityEnergyOutput
                and output.unit == lt.Units.WATT_HOUR
            ):
                production_in_kwh = sum(postprocessing_results.iloc[:, index]) * 1e-3

        # for production use negative value (co2 and revenue is handled by electricity meter)
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=(-1) * production_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ROOFTOP_PV,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """PV System KPIs.

        Calculates KPIs for the respective component and return
        all KPI entries as list.
        """
        return []

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
        connections.append(
            cp.ComponentConnection(
                PVSystem.ApparentZenith,
                weather_classname,
                Weather.ApparentZenith,
            )
        )
        connections.append(cp.ComponentConnection(PVSystem.WindSpeed, weather_classname, Weather.WindSpeed))
        return connections

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the component."""

        # check if results could be found in cache and if the lists have
        # the right length
        if (
            hasattr(self, "ac_power_ratios_for_all_timesteps_output")
            and len(self.ac_power_ratios_for_all_timesteps_output) == self.data_length
        ):
            stsv.set_output_value(
                self.electricity_output_channel,
                self.ac_power_ratios_for_all_timesteps_output[timestep] * self.pvconfig.power_in_watt,
            )
            stsv.set_output_value(
                self.electricity_energy_output_channel,
                self.ac_power_ratios_for_all_timesteps_output[timestep]
                * self.pvconfig.power_in_watt
                * self.my_simulation_parameters.seconds_per_timestep
                / 3600,
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

            if self.config.module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                simulate_fct = self.simulate_cec
            elif self.config.module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                simulate_fct = self.simulate_sandia
            else:
                raise KeyError(
                    f"""The module database '{self.config.module_database}'
                    is not available."""
                )

            ac_power_ratio = simulate_fct(
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

            # if you wanted to access the temperature forecast from the
            # weather component:
            # val = self.simulation_repository.get_entry(
            #   Weather.Weather_Temperature_Forecast_24h
            # )

            stsv.set_output_value(self.electricity_output_channel, ac_power_in_watt)
            stsv.set_output_value(
                self.electricity_energy_output_channel,
                ac_power_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600,
            )

            # cache results at the end of the simulation
            self.ac_power_ratios_for_all_timesteps_data[timestep] = ac_power_ratio

            if timestep + 1 == self.data_length:
                dict_with_results = {
                    "output_power": self.ac_power_ratios_for_all_timesteps_data,  # noqa: E501
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
                # delete weather data for PV preprocessing from dictionary
                # to save memory
                if SingletonSimRepository().exist_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST  # noqa: E501
                ):
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERAZIMUTHYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERAPPARENTZENITHYEARLYFORECAST  # noqa: E501
                    )
                    SingletonSimRepository().delete_entry(
                        key=SingletonDictKeyEnum.WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST  # noqa: E501
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
        file_exists, self.cache_filepath = utils.get_cache_file(
            self.config.name, self.pvconfig, self.my_simulation_parameters
        )

        if file_exists:
            log.information("Get PV results from cache.")
            self.ac_power_ratios_for_all_timesteps_output = pd.read_csv(self.cache_filepath, sep=",", decimal=".")[
                "output_power"
            ].tolist()

            if len(self.ac_power_ratios_for_all_timesteps_output) != self.my_simulation_parameters.timesteps:
                raise Exception(
                    "Reading the cached PV values seems to have failed. "
                    + "Expected "
                    + str(self.my_simulation_parameters.timesteps)
                    + " values, but got "
                    + str(len(self.ac_power_ratios_for_all_timesteps_output))
                )
        else:
            if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.LOCATION):
                SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.LOCATION)
            else:
                raise KeyError(
                    """The key weather_location was not found in the singleton
                    sim repository. Please check in your system setup if
                    the weather component was added to the simulator before
                    the pv system."""
                )

            # read module from pvlib database online or read from csv files in
            # hisim/inputs/photovoltaic/data_processed
            self.module = self.get_modules_from_database(
                module_database=self.pvconfig.module_database,
                load_module_data=self.pvconfig.load_module_data,
                module_name=self.pvconfig.module_name,
            )

            # read inverter from pvlib database online or read from csv files
            # in hisim/inputs/photovoltaic/data_processed
            self.inverter = self.get_inverters_from_database(
                inverter_database=self.pvconfig.inverter_database,
                load_module_data=self.pvconfig.load_module_data,
                inverter_name=self.pvconfig.inverter_name,
            )

            # when predictive control is activated, the PV simulation is run
            # beforhand to make forecasting easier
            if self.pvconfig.predictive_control:
                # get yearly weather data from dictionary
                dni_extra = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST  # noqa: E501
                )
                dni = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST  # noqa: E501
                )
                dhi = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST  # noqa: E501
                )
                ghi = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST  # noqa: E501
                )
                azimuth = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WEATHERAZIMUTHYEARLYFORECAST)
                apparent_zenith = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERAPPARENTZENITHYEARLYFORECAST  # noqa: E501
                )
                temperature = SingletonSimRepository().get_entry(
                    key=SingletonDictKeyEnum.WEATHERTEMPERATUREOUTSIDEYEARLYFORECAST  # noqa: E501
                )
                wind_speed = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WEATHERWINDSPEEDYEARLYFORECAST)

                x_simplephotovoltaic = []
                for i in range(self.my_simulation_parameters.timesteps):
                    # calculate outputs
                    if self.pvconfig.module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                        simulate_fct = self.simulate_cec
                    elif self.pvconfig.module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                        simulate_fct = self.simulate_sandia
                    ac_power_ratio = simulate_fct(
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
                    "output_power": self.ac_power_ratios_for_all_timesteps_output,  # noqa: E501
                }

                database = pd.DataFrame(
                    dict_with_results,
                    columns=[
                        "output_power",
                    ],
                )

                database.to_csv(self.cache_filepath, sep=",", decimal=".", index=False)

            else:
                # create empty result lists as a preparation for caching
                # in i_simulate

                self.ac_power_ratios_for_all_timesteps_data = [0] * self.my_simulation_parameters.timesteps

        if self.pvconfig.predictive:
            pv_forecast_yearly = [
                self.ac_power_ratios_for_all_timesteps_output[t] * self.pvconfig.power_in_watt
                for t in range(self.my_simulation_parameters.timesteps)
            ]
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.PVFORECASTYEARLY,
                entry=pv_forecast_yearly,
            )

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

        # get modules from pvlib database online
        # (TODO: test if this works, it has not been fully tested yet)
        if load_module_data is True:
            if module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                modules = pvlib.pvsystem.retrieve_sam(name="SandiaMod")
            elif module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                modules = pvlib.pvsystem.retrieve_sam(name="CECMod")
            else:
                raise KeyError(
                    f"""The module database {module_database} is not integrated
                    in the PV component here."""
                )

            # choose module from modules database
            module = modules[module_name]

        # get modules from input data csv files
        else:
            if module_database == PVLibModuleAndInverterEnum.SANDIA_MODULE_DATABASE:
                modules = pd.read_csv(
                    os.path.join(utils.HISIMPATH["photovoltaic"]["sandia_modules_new"]),
                )

            elif module_database == PVLibModuleAndInverterEnum.CEC_MODULE_DATABASE:
                modules = pd.read_csv(os.path.join(utils.HISIMPATH["photovoltaic"]["cec_modules"]))
            else:
                raise KeyError(
                    f"""The module database {module_database} is not integrated
                    in the PV component here."""
                )

            # choose module from modules database
            module = modules.loc[modules["Name"] == module_name]

            # transform column object types to numeric types
            for column in module.columns:
                module.loc[:, column] = pd.to_numeric(module.loc[:, column], errors="coerce")

            # transform module dataframe to dict
            if len(module) != 1:
                raise KeyError(
                    f"""No module {module_name} found in database
                    {module_database}."""
                )

            module = module.to_dict(orient="records")[0]

        return module

    def get_inverters_from_database(
        self,
        inverter_database: Any,
        load_module_data: bool,
        inverter_name: str,
    ) -> Any:
        """Get inverters from pvlib module database."""

        # get inverters from pvlib database online
        if load_module_data is True:
            if inverter_database in (
                PVLibModuleAndInverterEnum.SANDIA_INVERTER_DATABASE,
                PVLibModuleAndInverterEnum.CEC_INVERTER_DATABASE,
            ):
                # get inverter data (for both sandia and cec inverters the same
                # database is taken):
                # see docs: https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.pvsystem.retrieve_sam.html  # noqa: E501
                inverters = pvlib.pvsystem.retrieve_sam("CECInverter")
                inverter = inverters[inverter_name]
            elif inverter_database == PVLibModuleAndInverterEnum.ANTON_DRIESSE_INVERTER_DATABASE:
                inverters = pvlib.pvsystem.retrieve_sam("ADRInverter")
                inverter = inverters[inverter_name]
            else:
                raise KeyError(
                    f"""The inverter database {inverter_database} is not
                    integrated in the PV component here."""
                )

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
                    inverter.loc[:, column] = pd.to_numeric(inverter.loc[:, column], errors="coerce")

                # transform inverter dataframe to dict
                if len(inverter) != 1:
                    raise KeyError(
                        f"""No inverter {inverter_name} found in database
                        {inverter_database}."""
                    )

                inverter = inverter.to_dict(orient="records")[0]

            else:
                raise KeyError(
                    f"""The inverter database {inverter_database} is not
                    integrated in the PV component here."""
                )

        return inverter

    def simulate_sandia(
        self,
        dni_extra=None,
        dni=None,
        dhi=None,
        ghi=None,
        azimuth=None,
        apparent_zenith=None,
        temperature=None,
        wind_speed=None,
        surface_tilt=30.0,
        surface_azimuth=180.0,
        albedo=0.2,
    ):
        """Simulates with the Sandia PV Array Performance Model.

        The implementation is done in accordance with following tutorial:
        https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb
        https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.sapm.html#pvlib.pvsystem.sapm

        Based on the tsib project @[tsib-kotzur] (Check header)

        Parameters
        ----------
        surface_tilt: int or float, optional (default:30)
            Tilt angle of of the array in degree.
        surface_azimuth: int or float, optional (default:180)
            Azimuth angle of of the array in degree. 180 degree means south,
            90 degree east and 270 west.
        albedo: float, optional (default: 0.2)
            Reflection coefficient of the surrounding area.
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
        poa_irrad, airmass, aoi = self._calculate_irradiance(
            dni_extra,
            dni,
            dhi,
            ghi,
            azimuth,
            apparent_zenith,
            surface_tilt,
            surface_azimuth,
            albedo,
        )

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
                inverter=self.inverter,
                v_dc=sapm_out["v_mp"],
                p_dc=sapm_out["p_mp"],
            )
            # if inverter load is nan, make it zero otherwise ac_power_ratio
            # will be nan also
            if math.isnan(inverter_load_in_watt):
                inverter_load_in_watt = 0

            ac_power_ratio = inverter_load_in_watt / module_peak_load_in_watt
        else:
            # load in [kW/kWp]
            ac_power_ratio = sapm_out["p_mp"] / module_peak_load_in_watt

        if math.isnan(ac_power_ratio):  # type: ignore
            ac_power_ratio = 0.0

        return ac_power_ratio

    def simulate_cec(
        self,
        dni_extra=None,
        dni=None,
        dhi=None,
        ghi=None,
        azimuth=None,
        apparent_zenith=None,
        temperature=None,
        wind_speed=None,
        surface_tilt=30.0,
        surface_azimuth=180.0,
        albedo=0.2,
    ):
        """Simulates a defined PV array using the single-diode model.

        This simulation works with data from the CEC database.
        The implementation is done in accordance with following tutorial:
        https://github.com/pvlib/pvlib-python/blob/master/docs/tutorials/tmy_to_power.ipynb
        https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.pvsystem.sapm.html#pvlib.pvsystem.sapm


        Parameters
        ----------
        surface_tilt: int or float, optional (default:30)
            Tilt angle of of the array in degree.
        surface_azimuth: int or float, optional (default:180)
            Azimuth angle of of the array in degree. 180 degree means south,
            90 degree east and 270 west.
        albedo: float, optional (default: 0.2)
            Reflection coefficient of the surrounding area.
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
        # Calculate irradiance
        poa_irrad, _, _ = self._calculate_irradiance(
            dni_extra,
            dni,
            dhi,
            ghi,
            azimuth,
            apparent_zenith,
            surface_tilt,
            surface_azimuth,
            albedo,
        )

        # Calculate cell temperature
        pvtemps = pvlib.temperature.pvsyst_cell(
            poa_irrad["poa_global"],
            temperature,
            wind_speed,
            **self.temperature_model_parameters,
        )

        # Calculate maximum power point
        d = {
            k: self.module[k]
            for k in [
                "alpha_sc",
                "a_ref",
                "I_L_ref",
                "I_o_ref",
                "R_sh_ref",
                "R_s",
                "Adjust",
            ]
        }

        # If global irradiation is undefined (e.g. when dhi was 0), no power
        # output from PV
        if math.isnan(poa_irrad["poa_global"]):  # type: ignore
            return 0.0

        (
            photocurrent,
            saturation_current,
            resistance_series,
            resistance_shunt,
            n_ns_v_th,
        ) = pvlib.pvsystem.calcparams_cec(
            effective_irradiance=poa_irrad["poa_global"],
            temp_cell=pvtemps,
            **d,
        )

        mp = pvlib.pvsystem.max_power_point(
            photocurrent,
            saturation_current,
            resistance_series,
            resistance_shunt,
            n_ns_v_th,
            d2mutau=0,
            NsVbi=np.inf,
            method="brentq",
        )

        # Calculate peak load of single module [W]
        module_peak_load_in_watt = self.module["I_mp_ref"] * self.module["V_mp_ref"]
        ac_power_ratio: float

        if self.pvconfig.integrate_inverter:
            # calculate load after inverter
            inverter_load_in_watt = pvlib.inverter.sandia(inverter=self.inverter, v_dc=mp["v_mp"], p_dc=mp["p_mp"])
            # if inverter load is nan, make it zero otherwise ac_power_ratio
            # will be nan also
            if math.isnan(inverter_load_in_watt):
                inverter_load_in_watt = 0

            ac_power_ratio = inverter_load_in_watt / module_peak_load_in_watt
        else:
            # load in [kW/kWp]
            ac_power_ratio = mp["p_mp"] / module_peak_load_in_watt

        if math.isnan(ac_power_ratio):  # type: ignore
            ac_power_ratio = 0.0

        return ac_power_ratio

    def _calculate_irradiance(
        self,
        dni_extra: Optional[float] = None,
        dni: Optional[float] = None,
        dhi: Optional[float] = None,
        ghi: Optional[float] = None,
        azimuth: Optional[float] = None,
        apparent_zenith: Optional[float] = None,
        surface_tilt: float = 30.0,
        surface_azimuth: float = 180.0,
        albedo: float = 0.2,
    ) -> Tuple[Dict[str, Any], float, float]:
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)

        # calculate diffuse irradiance
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

        return poa_irrad, airmass, aoi
