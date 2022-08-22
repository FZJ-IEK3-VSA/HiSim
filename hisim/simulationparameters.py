""" Defines the simulation parameters class. This defines how the simulation will proceed. """
from __future__ import annotations
from typing import List, Optional
import datetime
from dataclasses import dataclass
from dataclasses_json import dataclass_json


from hisim.postprocessingoptions import PostProcessingOptions


@dataclass_json
@dataclass()
class SystemConfig:

    """ Defines the system config for the modular household. """

    predictive: bool = False
    prediction_horizon: int = 24 * 3600
    pv_included: bool = True
    smart_devices_included: bool = True
    water_heating_system_installed: Optional[str] = "HeatPump"
    heating_system_installed: Optional[str] = "HeatPump"
    buffer_volume: float = 500
    battery_included: bool = False
    chp_included: bool = False


class SimulationParameters:

    """ Defines HOW the simulation is going to proceed: Time resolution, time span and all these things. """

    def __init__(self, start_date: datetime.date, end_date: datetime.date, seconds_per_timestep: int,
                 result_directory: str = "",
                 post_processing_options: List[int] = None):
        """ Initializes the class. """
        self.start_date = start_date
        self.end_date = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds / seconds_per_timestep)
        self.year = start_date.year
        if post_processing_options is None:
            post_processing_options = []
        self.post_processing_options: List[int] = post_processing_options
        self.logging_level: int = 3  # Info # noqa
        self.result_directory: str = result_directory
        self.skip_finished_results: bool = True
        self.system_config = SystemConfig()  # noqa

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a full year without any post processing, primarily for unit testing. """
        return cls(datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1), seconds_per_timestep, "")

    def enable_all_options(self) -> None:
        """ Enables all the post processing options . """
        for option in PostProcessingOptions:
            self.post_processing_options.append(option)

    @classmethod
    def full_year_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a full year with all the post processing, primarily for unit testing. """
        pars = cls(datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1), seconds_per_timestep, "")
        pars.enable_all_options()
        return pars

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single january, primarily for unit testing. """
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 31), seconds_per_timestep, "")

    @classmethod
    def one_week_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single week, primarily for unit testing. """
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 8), seconds_per_timestep, "")

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single day, primarily for unit testing. """
        return cls(datetime.date(year, 1, 1), datetime.date(year, 1, 2), seconds_per_timestep, "")

    @classmethod
    def one_day_only_with_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """ Generates a parameter set for a single day, primarily for unit testing. """
        pars = cls(datetime.date(year, 1, 1), datetime.date(year, 1, 2), seconds_per_timestep, "")
        pars.enable_all_options()
        return pars

    def get_unique_key(self) -> str:
        """ Gets a unique key from a simulation parameter class. """
        return str(self.start_date) + "###" + str(self.end_date) + "###" + str(self.seconds_per_timestep) + "###" + str(
            self.year) + "###" + str(self.timesteps)

    def reset_system_config(self, predictive: bool = False, prediction_horizon: int = 0, pv_included: bool = True,
                            smart_devices_included: bool = True, water_heating_system_installed: Optional[str] = 'HeatPump',
                            heating_system_installed: Optional[str] = 'HeatPump', buffer_volume: float = 500,
                            battery_included: bool = False, chp_included: bool = False) -> None:  # noqa
        """ Configures a system config. """
        self.system_config = SystemConfig(predictive=predictive, prediction_horizon=prediction_horizon,
                                          pv_included=pv_included, smart_devices_included=smart_devices_included,
                                          water_heating_system_installed=water_heating_system_installed,
                                          heating_system_installed=heating_system_installed, buffer_volume=buffer_volume,
                                          battery_included=battery_included, chp_included=chp_included)
