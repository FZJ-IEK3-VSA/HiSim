""" Defines the simulation parameters class. This defines how the simulation will proceed. """
# clean
from __future__ import annotations
from typing import List, Optional
import datetime
from dataclasses import dataclass
from dataclass_wizard import JSONWizard

from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.loadtypes import HeatingSystems, Locations, OccupancyProfiles, BuildingCodes, Cars, MobilityDistance


@dataclass()
class SystemConfig:

    """ Defines the system config for the modular household. """

    location: Locations = Locations.AACHEN
    occupancy_profile: OccupancyProfiles = OccupancyProfiles.CH01
    building_code: BuildingCodes = BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002
    predictive: bool = False
    prediction_horizon: int = 24 * 3600
    pv_included: bool = True
    pv_peak_power: Optional[float] = 10e3  # in Watt
    smart_devices_included: bool = True
    water_heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP
    heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP
    buffer_included: bool = True
    buffer_volume: Optional[float] = 500  # in liter
    battery_included: bool = False
    battery_capacity: Optional[float] = 10e3  # in Wh
    chp_included: bool = False
    chp_power: Optional[float] = 10e3
    h2_storage_size: Optional[float] = 100
    electrolyzer_power: Optional[float] = 10e3
    current_mobility: Cars = Cars.NO_CAR
    mobility_distance: MobilityDistance = MobilityDistance.RURAL


@dataclass()
class SimulationParameters(JSONWizard):

    """ Defines HOW the simulation is going to proceed: Time resolution, time span and all these things. """

    start_date: datetime.datetime
    end_date: datetime.datetime
    seconds_per_timestep: int
    post_processing_options: List[int]
    logging_level: int
    result_directory: str
    skip_finished_results: bool
    system_config: SystemConfig

    def __init__(self, start_date: datetime.datetime, end_date: datetime.datetime, seconds_per_timestep: int,
                 result_directory: str = "",
                 post_processing_options: List[int] = None, logging_level: int = log.LogPrio.INFORMATION,
                 skip_finished_results: bool = False, system_config: SystemConfig = SystemConfig()):
        """ Initializes the class. """
        self.start_date: datetime.datetime = start_date
        self.end_date: datetime.datetime = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds / seconds_per_timestep)
        self.year: int = int(start_date.year)
        if post_processing_options is None:
            post_processing_options = []
        self.post_processing_options: List[int] = post_processing_options
        self.logging_level: int = logging_level  # Info # noqa
        self.result_directory: str = result_directory
        self.skip_finished_results: bool = skip_finished_results
        self.system_config = system_config  # noqa

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a full year without any post processing, primarily for unit testing. """
        return cls(datetime.datetime(year, 1, 1), datetime.datetime(year + 1, 1, 1), seconds_per_timestep, "")

    def enable_all_options(self) -> None:
        """ Enables all the post processing options . """
        for option in PostProcessingOptions:
            self.post_processing_options.append(option)

    @classmethod
    def full_year_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a full year with all the post processing, primarily for unit testing. """
        pars = cls(datetime.datetime(year, 1, 1), datetime.datetime(year + 1, 1, 1), seconds_per_timestep, "")
        pars.enable_all_options()
        return pars

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single january, primarily for unit testing. """
        return cls(datetime.datetime(year, 1, 1), datetime.datetime(year, 1, 31), seconds_per_timestep, "")

    @classmethod
    def three_months_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single january, primarily for unit testing. """
        return cls(datetime.datetime(year, 1, 1), datetime.datetime(year, 6, 30), seconds_per_timestep, "")

    @classmethod
    def one_week_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single week, primarily for unit testing. """
        return cls(datetime.datetime(year, 1, 1), datetime.datetime(year, 1, 8), seconds_per_timestep, "")

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int=60) -> SimulationParameters:
        """ Generates a parameter set for a single day, primarily for unit testing. """
        return cls(datetime.datetime(year, 1, 1), datetime.datetime(year, 1, 2), seconds_per_timestep, "")

    @classmethod
    def one_day_only_with_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single day, primarily for unit testing. """
        pars = cls(datetime.datetime(year, 1, 1), datetime.datetime(year, 1, 2), seconds_per_timestep, "")
        pars.enable_all_options()
        return pars

    def get_unique_key(self) -> str:
        """ Gets a unique key from a simulation parameter class. """
        return str(self.start_date) + "###" + str(self.end_date) + "###" + str(self.seconds_per_timestep) + "###" + str(
            self.year) + "###" + str(self.timesteps)

    def reset_system_config(
            self, location: Locations = Locations.AACHEN, occupancy_profile: OccupancyProfiles = OccupancyProfiles.CH01,
            building_code: BuildingCodes = BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002, predictive: bool = True, prediction_horizon: int = 0,
            pv_included: bool = True, pv_peak_power: Optional[float] = 9, smart_devices_included: bool = True,
            water_heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            buffer_included: bool = True, buffer_volume: Optional[float] = 500, battery_included: bool = False, battery_capacity: Optional[float] = 5,
            chp_included: bool = False, chp_power: Optional[float] = 12, h2_storage_size: Optional[float] = 100,
            electrolyzer_power: Optional[float] = 12, current_mobility: Cars = Cars.NO_CAR,
            mobility_distance: MobilityDistance = MobilityDistance.RURAL) -> None:  # noqa
        """ Configures a system config. """
        self.system_config = SystemConfig(
            location=location, occupancy_profile=occupancy_profile, building_code=building_code, predictive=predictive,
            prediction_horizon=prediction_horizon, pv_included=pv_included, pv_peak_power=pv_peak_power,
            smart_devices_included=smart_devices_included, water_heating_system_installed=water_heating_system_installed,
            heating_system_installed=heating_system_installed, buffer_included=buffer_included, buffer_volume=buffer_volume,
            battery_included=battery_included, battery_capacity=battery_capacity, chp_included=chp_included,
            chp_power=chp_power, h2_storage_size=h2_storage_size, electrolyzer_power=electrolyzer_power,
            current_mobility=current_mobility, mobility_distance=mobility_distance)
