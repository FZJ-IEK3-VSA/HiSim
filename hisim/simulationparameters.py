""" Defines the simulation parameters class. This defines how the simulation will proceed. """
# clean
from __future__ import annotations
from typing import List, Optional
import datetime
from dataclasses import dataclass
from dataclass_wizard import JSONWizard

from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions


@dataclass()
class SimulationParameters(JSONWizard):

    """Defines HOW the simulation is going to proceed: Time resolution, time span and all these things."""

    start_date: datetime.datetime
    end_date: datetime.datetime
    seconds_per_timestep: int
    post_processing_options: List[int]
    logging_level: int
    result_directory: str
    skip_finished_results: bool
    surplus_control: bool
    predictive_control: bool
    prediction_horizon: Optional[int]

    def __init__(
        self,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        seconds_per_timestep: int,
        result_directory: str = "",
        post_processing_options: Optional[List[int]] = None,
        logging_level: int = log.LogPrio.INFORMATION,
        skip_finished_results: bool = False,
        surplus_control: bool = True,
        predictive_control: bool = False,
        prediction_horizon: Optional[int] = 0,
    ):
        """Initializes the class."""
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
        self.surplus_control = surplus_control
        self.predictive_control = predictive_control
        self.prediction_horizon = prediction_horizon

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year without any post processing, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
            "",
        )

    def enable_all_options(self) -> None:
        """Enables all the post processing options ."""
        for option in PostProcessingOptions:
            self.post_processing_options.append(option)

    @classmethod
    def full_year_all_options(
        cls, year: int, seconds_per_timestep: int
    ) -> SimulationParameters:
        """Generates a parameter set for a full year with all the post processing, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
            "",
        )
        pars.enable_all_options()
        return pars

    @classmethod
    def january_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 31),
            seconds_per_timestep,
            "",
        )

    @classmethod
    def three_months_only(
        cls, year: int, seconds_per_timestep: int
    ) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 6, 30),
            seconds_per_timestep,
            "",
        )

    @classmethod
    def one_week_only(
        cls, year: int, seconds_per_timestep: int
    ) -> SimulationParameters:
        """Generates a parameter set for a single week, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 8),
            seconds_per_timestep,
            "",
        )

    @classmethod
    def one_day_only(
        cls, year: int, seconds_per_timestep: int = 60
    ) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
            "",
        )

    @classmethod
    def one_day_only_with_all_options(
        cls, year: int, seconds_per_timestep: int
    ) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
            "",
        )
        pars.enable_all_options()
        return pars

    def get_unique_key(self) -> str:
        """Gets a unique key from a simulation parameter class."""
        return (
            str(self.start_date)
            + "###"
            + str(self.end_date)
            + "###"
            + str(self.seconds_per_timestep)
            + "###"
            + str(self.year)
            + "###"
            + str(self.timesteps)
        )

    def get_unique_key_as_list(self) -> List[str]:
        """Gets unique key from a simulation parameter class as list."""
        lines = []
        lines.append(f"Start date: {self.start_date}")
        lines.append(f"End date: {self.end_date}")
        lines.append(f"Simulation year: {self.year}")
        lines.append(f"Seconds per timestep: {self.seconds_per_timestep}")
        lines.append(f"Total number of timesteps: {self.timesteps}")
        return lines
