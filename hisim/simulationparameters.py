""" Defines the simulation parameters class. This defines how the simulation will proceed. """
# clean
from __future__ import annotations
import os
import inspect
from typing import Any, List, Optional, Sequence
import enum

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
    cache_dir_path: str
    multiple_buildings: bool
    log_connections: bool

    def __init__(
        self,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        seconds_per_timestep: int,
        country: str = 'DE',
        result_directory: str = "",
        post_processing_options: Optional[Sequence[int]] = None,
        logging_level: int = log.LogPrio.INFORMATION,
        skip_finished_results: bool = False,
        surplus_control: bool = True,
        cache_dir_path: str = os.path.join(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))), "inputs", "cache"),  # type: ignore
        multiple_buildings: bool = False,
        log_connections: bool = False,
    ):
        """Initialize the SimulationParameters.

        Defines how the simulation will proceed, including time resolution, time span,
        logging configuration, and various simulation options.

        Args:
            start_date: The start date and time of the simulation period.
            end_date: The end date and time of the simulation period.
            seconds_per_timestep: The duration of each simulation timestep in seconds.
            country: Country code for location-specific data (e.g., weather, tariffs).
                Defaults to 'DE' (Germany).
            result_directory: Directory path where simulation results will be stored.
                Defaults to empty string (will be set by the simulation runner).
            post_processing_options: List of post-processing option flags to enable.
                Each integer corresponds to a specific post-processing task.
                Defaults to None (empty list).
            logging_level: Logging verbosity level. Use values from log.LogPrio.
                Defaults to log.LogPrio.INFORMATION.
            skip_finished_results: If True, skip computation for timesteps that have
                already been completed. Useful for resuming interrupted simulations.
                Defaults to False.
            surplus_control: If True, enable surplus energy control logic for managing
                excess generation (e.g., from PV systems). Defaults to True.
            cache_dir_path: Directory path for caching intermediate data (e.g., weather
                data, processed inputs). Defaults to the 'inputs/cache' directory
                within the hisim package.
            multiple_buildings: If True, configure the simulation for multiple buildings.
                Defaults to False (single building).
            log_connections: If True, enable logging of component connections for
                debugging and verification. Defaults to False.
        """
        self.start_date: datetime.datetime = start_date
        self.end_date: datetime.datetime = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        total_seconds = self.duration.total_seconds()
        self.timesteps: int = int(total_seconds / seconds_per_timestep)
        self.year: int = int(start_date.year)
        self.country: str = country
        if post_processing_options is None:
            post_processing_options = []
        self.post_processing_options: List[int] = list(post_processing_options)
        self.logging_level: int = logging_level  # Info # noqa
        self.result_directory: str = result_directory
        self.skip_finished_results: bool = skip_finished_results
        self.surplus_control = surplus_control
        self.cache_dir_path = cache_dir_path
        self.multiple_buildings = multiple_buildings
        self.figure_format = FigureFormat.PNG
        self.log_connections = log_connections
        # Parameters of the parallel lifecycle cost engine (cost_spec.md §3.2). Deliberately a
        # plain attribute (not a dataclass field) so *.simulation.json round-trips stay
        # byte-identical during the parallel phase; set via set_economic_parameters() or the
        # RenoVisor request (see cost_module_issues.md #5).
        self.economic_parameters: Optional[Any] = None
        # Optional hisim.economics.bridge.EconomicContext: existing assets, subsidy context,
        # envelope measures, tenancy data and scenario sets a system setup declares for the
        # lifecycle cost engine (see system_setups/economic_example/).
        self.economic_context: Optional[Any] = None

    def set_economic_parameters(self, economic_parameters: Any) -> None:
        """Attaches EconomicParameters for the lifecycle cost engine (cost_spec.md §3.2)."""
        self.economic_parameters = economic_parameters

    def set_economic_context(self, economic_context: Any) -> None:
        """Attaches an EconomicContext for the lifecycle cost engine (cost_spec.md §4-§6)."""
        self.economic_context = economic_context

    @classmethod
    def full_year(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year without any post processing, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
        )

    def enable_all_options(self) -> None:
        """Enables all the post processing options ."""
        for option in PostProcessingOptions:
            self.post_processing_options.append(option)

    def enable_plots_only(self) -> None:
        """Enables line and carpet plots."""
        self.post_processing_options.append(PostProcessingOptions.PLOT_LINE)
        self.post_processing_options.append(PostProcessingOptions.PLOT_CARPET)
        # self.post_processing_options.append(PostProcessingOptions.PLOT_SANKEY)
        self.post_processing_options.append(PostProcessingOptions.PLOT_SINGLE_DAYS)
        self.post_processing_options.append(PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS)
        self.post_processing_options.append(PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER)

    @classmethod
    def full_year_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year with all the post processing, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
        )
        pars.enable_all_options()
        return pars

    @classmethod
    def full_year_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year with all the post processing, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
        )
        pars.enable_plots_only()
        return pars

    @classmethod
    def january_only_with_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 31),
            seconds_per_timestep,
        )
        pars.enable_all_options()
        return pars

    @classmethod
    def january_only_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 31),
            seconds_per_timestep,
        )
        pars.enable_plots_only()
        return pars

    @classmethod
    def three_months_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a three months, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 3, 1),
            datetime.datetime(year, 5, 31),
            seconds_per_timestep,
        )

    @classmethod
    def three_months_with_plots_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a three months, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 6, 1),
            datetime.datetime(year, 8, 31),
            seconds_per_timestep,
        )
        pars.enable_plots_only()
        return pars

    @classmethod
    def one_week_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single week, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 8),
            seconds_per_timestep,
        )

    @classmethod
    def one_week_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single week, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 8),
            seconds_per_timestep,
        )

        pars.enable_plots_only()
        return pars

    @classmethod
    def one_day_only(cls, year: int, seconds_per_timestep: int = 60) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        return cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
        )

    @classmethod
    def one_day_only_with_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
        )
        pars.enable_all_options()
        return pars

    @classmethod
    def one_day_only_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        pars = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
        )
        pars.enable_plots_only()
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
            + "###"
            + str(self.country)
        )

    def get_unique_key_as_list(self) -> List[str]:
        """Gets unique key from a simulation parameter class as list."""
        lines = []
        lines.append(f"Start date: {self.start_date}")
        lines.append(f"End date: {self.end_date}")
        lines.append(f"Simulation year: {self.year}")
        lines.append(f"Seconds per timestep: {self.seconds_per_timestep}")
        lines.append(f"Total number of timesteps: {self.timesteps}")
        lines.append(f"Country: {self.country}")
        return lines


class FigureFormat(str, enum.Enum):

    """Set Figure Formats."""

    PNG = ".png"
    JPG = ".jpg"
