""" Defines the simulation parameters class. This defines how the simulation will proceed. """
from __future__ import annotations
import math
import numbers
import os
from typing import Any, ClassVar, List, Optional, Sequence, Tuple
import enum

import datetime
from dataclasses import dataclass

from hisim import log
from hisim.caching.locations import CacheLocations
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulation_day import SimulationDay


class PartLoadThresholdError(ValueError):
    """A ``part_load_above_seconds`` that is not a step length: not a number, a ``bool``, or negative.

    Raised by :class:`SimulationParameters` at construction, so a simulation-parameters file with such a value is
    refused before anything runs.
    """


class WeatherYearError(ValueError):
    """A ``weather_year`` that is not a year HiSim can select weather data for.

    Raised by :class:`SimulationParameters` when the value is not an ``int`` (a ``bool`` included,
    which Python would otherwise accept as one) or lies outside
    :attr:`SimulationParameters.WEATHER_YEAR_RANGE`.
    """


class SimulationWindowError(ValueError):
    """A simulated period that breaks the window contract of :class:`SimulationParameters`.

    The window is the span from ``start_date`` to ``end_date`` that a run simulates. It must start at
    midnight, cover whole days, lie within one calendar year and divide into whole timesteps, with a step
    length that divides a day; see
    :meth:`SimulationParameters.validated_window`. Raised at construction, so a simulation-parameters
    file with such a window is refused before anything runs.
    """


@dataclass()
class SimulationParameters:

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
        cache_dir_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inputs", "cache"),
        multiple_buildings: bool = False,
        log_connections: bool = False,
        cache_directories: Optional[Sequence[str]] = None,
        weather_year: Optional[int] = None,
        part_load_above_seconds: Optional[float] = None,
    ):
        """Initialize the SimulationParameters.

        Defines how the simulation will proceed, including time resolution, time span,
        logging configuration, and various simulation options.

        The window from ``start_date`` to ``end_date`` must obey the window contract of
        :meth:`validated_window`: start at midnight, whole days, one calendar year, whole timesteps, a step
        length that divides a day.

        Args:
            start_date: The start of the simulation period, at midnight.
            end_date: The end of the simulation period, excluded, a whole number of days after
                ``start_date``. The start of the last timestep, ``end_date - seconds_per_timestep``,
                lies in the year of ``start_date``; ``end_date`` itself may be 1 January of the next
                year, as it is for a full year.
            seconds_per_timestep: The duration of each simulation timestep in seconds; it divides a day of
                86 400 s, and with it the period, into whole timesteps.
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
            cache_directories: Ordered cache directories, read in order and written to the
                first writable one, for a container that maps a read-only seed directory and a writable
                one in (hisim-epc.22). Empty (the default) means the single
                ``cache_dir_path``, exactly as before.
            weather_year: The year whose weather the run reads, when it is not the calendar year
                (``roadmap/weather_year.md``). ``None`` (the default) means today's behaviour: the
                weather file's rows are laid onto the calendar year by position. ``self.year``
                stays the calendar year either way. An int from 1900 to 2100; anything else raises
                :class:`WeatherYearError`.
            part_load_above_seconds: The step length above which a device its controller has switched on runs
                only the fraction of the step that brings its store to the controller's target (:mod:`hisim.part_load`).
                At and below it every device runs whole steps, as before. Two devices read it today: the boiler's
                hot-water charge (:class:`~hisim.components.generic_boiler.GenericBoilerController`) and the solar
                collector's pump (:class:`~hisim.components.solar_thermal_system.SolarThermalSystemController`);
                every other device, the heat pumps and the electric heaters among them, runs whole steps at any
                step length. ``None`` (the default) means
                :attr:`PART_LOAD_ABOVE_SECONDS_DEFAULT`, 600 s; a number at least 0 otherwise, anything else raises
                :class:`PartLoadThresholdError`.

        Raises:
            SimulationWindowError: If the window breaks the window contract.
            WeatherYearError: If ``weather_year`` is not a year in range.
            PartLoadThresholdError: If ``part_load_above_seconds`` is not a step length.
        """
        self.start_date: datetime.datetime = start_date
        self.end_date: datetime.datetime = end_date
        self.seconds_per_timestep = seconds_per_timestep
        self.duration = end_date - start_date
        # The one place that decides the run's window: everything a component, the simulator or the
        # postprocessing derives from these dates relies on the contract checked here.
        self.timesteps: int = self.validated_window(start_date, end_date, seconds_per_timestep)
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
        self.cache_directories: List[str] = list(cache_directories or [])
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
        # Whether a failed lifecycle cost engine fails the run (see require_lifecycle_costs). A
        # plain attribute for the same round-trip reason as the two above.
        self.lifecycle_costs_required: bool = False
        # The year whose weather the run reads (roadmap/weather_year.md). A plain attribute rather
        # than a dataclass field for the same round-trip reason as the three above: unset, every
        # key, record and file this object produces stays byte-identical.
        self.weather_year: Optional[int] = self.validated_weather_year(weather_year)
        # The part-load threshold (hisim.part_load). A plain attribute for the same round-trip
        # reason as the four above: at its default, every key, record and file this object produces stays
        # byte-identical.
        self.part_load_above_seconds: float = self.validated_part_load_above_seconds(part_load_above_seconds)

    #: The length of the days a window is made of. A window is a whole number of these.
    SECONDS_PER_DAY: ClassVar[int] = SimulationDay.SECONDS_PER_DAY

    @property
    def timesteps_per_day(self) -> int:
        """The exact number of timesteps in each day of the run, which rule 5 of the window contract makes whole.

        Example: 96 at 900 s, 24 at 3600 s. The single-day chart slices a day of the run by it.

        Returns:
            The number of timesteps per day.
        """
        return SimulationDay.timesteps_per_day(self.seconds_per_timestep)

    @classmethod
    def validated_window(
        cls, start_date: datetime.datetime, end_date: datetime.datetime, seconds_per_timestep: int
    ) -> int:
        """Checks the run's window against the window contract and returns its number of timesteps.

        The window is the span from ``start_date`` (included) to ``end_date`` (excluded). It must:

        1. start at midnight, so that every day of the run begins at the start of a row of a carpet
           plot and at the start of a day of every profile;
        2. cover a whole number of days, at least one;
        3. lie within one calendar year: ``start_date`` and the start of the last timestep,
           ``end_date - seconds_per_timestep``, fall in the same year. The profiles a run reads are
           one year long, so a window across 31 December has no single source to read from;
        4. divide into whole timesteps: the duration is a multiple of ``seconds_per_timestep``;
        5. split every day into whole timesteps: ``seconds_per_timestep`` divides the 86 400 s of a day.
           Every day of the run then starts a timestep and has the same number of them
           (:attr:`timesteps_per_day`), which the single-day chart, the carpet and the weather's daily
           average outside temperature rely on. A step longer than a day is refused by this rule too.

        Example: 1 July 2021 to 8 July 2021 at 900 s passes and gives 672 timesteps; 1 July 2021
        06:00 to 8 July 2021 is refused (not midnight), as is 30 December 2021 to 2 January 2022
        (two years), one day at 7000 s (86 400 s is not a multiple of 7000 s), and two days at 16 h
        (three whole timesteps, but a day is one and a half of them). A full year,
        1 January 2021 to 1 January 2022, passes: its last timestep starts on 31 December 2021.

        Whether a profile source can serve the window's dates -- an 8760-row source and a leap year,
        say -- is not decided here, but by the component that owns the source. The contract fixes the
        window's shape, not that its dates are served: until the profiles follow the start date
        (hisim-9g2.4 and the later steps of the mid-year epic), a window that starts after 1 January
        is read from 1 January, as :meth:`one_week_july` explains.

        Args:
            start_date: The first moment of the run.
            end_date: The moment the run ends; the last timestep ends here.
            seconds_per_timestep: The length of one timestep, in seconds.

        Returns:
            The number of timesteps in the window.

        Raises:
            SimulationWindowError: If the window breaks any of the five rules, or if
                ``seconds_per_timestep`` is not a positive, finite real number (``inf``, ``nan`` and
                ``True`` are refused; a numpy integer or float is accepted).
        """
        # Any real number counts, numpy's included, as it did before the contract; a bool does not.
        if isinstance(seconds_per_timestep, bool) or not isinstance(seconds_per_timestep, (int, float, numbers.Real)):
            raise SimulationWindowError(
                f"seconds_per_timestep must be a positive number of seconds, not {seconds_per_timestep!r}."
            )
        if not (math.isfinite(seconds_per_timestep) and seconds_per_timestep > 0):
            raise SimulationWindowError(
                f"seconds_per_timestep must be a positive number of seconds, not {seconds_per_timestep}."
            )
        if start_date.time() != datetime.time(0, 0):
            raise SimulationWindowError(
                f"The simulation must start at midnight, but start_date is {start_date.isoformat()}."
            )
        duration = end_date - start_date
        one_day = datetime.timedelta(seconds=cls.SECONDS_PER_DAY)
        if duration < one_day or duration % one_day:
            raise SimulationWindowError(
                f"The simulation must cover a whole number of days, at least one, but {start_date.isoformat()} "
                f"to {end_date.isoformat()} is {duration}."
            )
        # timedelta takes no numpy scalar; float() is exact for every step length a run can have.
        timestep = datetime.timedelta(seconds=float(seconds_per_timestep))
        if duration % timestep:
            raise SimulationWindowError(
                f"The simulated duration of {duration} is not a whole number of {seconds_per_timestep} s "
                f"timesteps."
            )
        last_timestep_start = end_date - timestep
        if last_timestep_start.year != start_date.year:
            raise SimulationWindowError(
                f"The simulation must lie within one calendar year, but it starts in {start_date.year} and "
                f"its last timestep starts in {last_timestep_start.year} ({last_timestep_start.isoformat()}). "
                f"The profiles a run reads are one year long; split the run at 1 January."
            )
        if not SimulationDay.divides_a_day(seconds_per_timestep):
            raise SimulationWindowError(
                f"A step length of {seconds_per_timestep} s does not divide a day of {cls.SECONDS_PER_DAY} s, so "
                f"the days of the run are not whole numbers of timesteps. Every day must start a timestep: the "
                f"single-day plot, the carpet and the daily average outside temperature are taken per day. Use a "
                f"step length that divides {cls.SECONDS_PER_DAY} s, such as 60, 900 or 3600 s."
            )
        return duration // timestep

    #: The step length above which devices run part load, s, when the parameters name none. 600 s keeps every 60 s
    #: run, the reference, on whole steps, and lets a 900 s run, RenoVisor's resolution, run part load.
    PART_LOAD_ABOVE_SECONDS_DEFAULT: ClassVar[float] = 600.0

    @classmethod
    def validated_part_load_above_seconds(cls, part_load_above_seconds: Any) -> float:
        """Returns the part-load threshold in seconds as a float: the value given, or the default for ``None``.

        The default is :attr:`PART_LOAD_ABOVE_SECONDS_DEFAULT`. Example: ``None`` gives 600.0; ``900`` and ``900.0``
        both give 900.0, which keeps a 900 s run on whole steps. Returning a float for every number keeps one spelling
        of one threshold in keys and records.

        Args:
            part_load_above_seconds: The threshold handed to the constructor, s, or ``None``.

        Returns:
            The threshold in seconds, as a float.

        Raises:
            PartLoadThresholdError: If ``part_load_above_seconds`` is a ``bool``, not a number, not finite, or
                negative.
        """
        if part_load_above_seconds is None:
            return cls.PART_LOAD_ABOVE_SECONDS_DEFAULT
        if isinstance(part_load_above_seconds, bool) or not isinstance(part_load_above_seconds, (int, float)):
            raise PartLoadThresholdError(
                f"part_load_above_seconds must be a number of seconds, not {part_load_above_seconds!r} "
                f"({type(part_load_above_seconds).__name__})."
            )
        if not part_load_above_seconds >= 0 or part_load_above_seconds == float("inf"):
            raise PartLoadThresholdError(
                f"part_load_above_seconds must be a finite number >= 0, not {part_load_above_seconds}."
            )
        return float(part_load_above_seconds)

    def runs_part_load(self) -> bool:
        """Whether this run's steps are longer than the part-load threshold, so generators may run part load.

        Example: a 900 s run with the default 600 s threshold runs part load; a 60 s run never does, nor a 600 s
        run, since the rule applies strictly above the threshold.
        """
        return self.seconds_per_timestep > self.part_load_above_seconds

    def part_load_threshold_is_default(self) -> bool:
        """Whether the part-load threshold is the default, so keys, records and files omit it."""
        return self.part_load_above_seconds == self.PART_LOAD_ABOVE_SECONDS_DEFAULT

    #: The years a ``weather_year`` may name, inclusive.
    WEATHER_YEAR_RANGE: ClassVar[Tuple[int, int]] = (1900, 2100)

    @classmethod
    def validated_weather_year(cls, weather_year: Any) -> Optional[int]:
        """Returns ``weather_year`` unchanged when it is ``None`` or a year in range, else refuses it.

        Args:
            weather_year: The value handed to the constructor.

        Returns:
            The value itself.

        Raises:
            WeatherYearError: When the value is not an int, is a bool, or lies outside
                :attr:`WEATHER_YEAR_RANGE`.
        """
        if weather_year is None:
            return None
        first, last = cls.WEATHER_YEAR_RANGE
        if isinstance(weather_year, bool) or not isinstance(weather_year, int):
            raise WeatherYearError(
                f"weather_year must be an int from {first} to {last}, not {weather_year!r} "
                f"({type(weather_year).__name__})."
            )
        if not first <= weather_year <= last:
            raise WeatherYearError(f"weather_year must lie from {first} to {last}, not {weather_year}.")
        return weather_year

    def require_lifecycle_costs(self) -> None:
        """Makes a failure of the lifecycle cost engine fail the run instead of being logged.

        A plain HiSim simulation treats the cost engine as an addition: when it fails, the error
        is logged and the legacy outputs are written as always. A caller whose answer *is* the
        money -- a RenoVisor calculation, whose frontend cannot show a result without it -- calls
        this before the run, and postprocessing then re-raises the engine's error as a
        `LifecycleCostEngineError` naming it (owner decision 2026-09-27, renovisorissues #66).
        """
        self.lifecycle_costs_required = True

    def set_economic_parameters(self, economic_parameters: Any) -> None:
        """Attaches EconomicParameters for the lifecycle cost engine (cost_spec.md §3.2).

        The economy-wide assumptions of a run — country, price basis year, horizon, discount and
        escalation rates, subsidy catalog path — travel with the simulation parameters because
        that is the one object postprocessing already receives; the setter exists so a system
        setup does not have to know that they are carried as a plain attribute rather than a
        dataclass field (which is what keeps `*.simulation.json` round-trips byte-identical).

        Attaching nothing is valid: the engine then falls back to its documented defaults for the
        simulation's country. Typed as `Any` on purpose, so `hisim.simulationparameters` does not
        import the economics package.

        Args:
            economic_parameters: A `hisim.economics.EconomicParameters` instance.
        """
        self.economic_parameters = economic_parameters

    def set_economic_context(self, economic_context: Any) -> None:
        """Attaches an EconomicContext for the lifecycle cost engine (cost_spec.md §4-§6).

        The counterpart to the parameters above: not the economy, but *this* decision situation —
        what already stands in the building, who applies for which subsidy, which non-simulated
        envelope measures belong to the package, how the building is tenanted, which scenarios to
        sweep. None of it is derivable from a physics simulation, so whoever defines the variant
        declares it once here and `hisim.economics.bridge` merges it into the simulation-derived
        inputs after the run.

        Without a context the run still produces lifecycle costs, but only greenfield ones — every
        device charged as a new purchase into an empty building. See
        `system_setups/economic_example/` for a fully worked attachment.

        Args:
            economic_context: A `hisim.economics.bridge.EconomicContext` instance.
        """
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
        self.post_processing_options.extend(PostProcessingOptions)

    def enable_plots_only(self) -> None:
        """Enables line and carpet plots."""
        self.post_processing_options.extend([
            PostProcessingOptions.PLOT_LINE,
            PostProcessingOptions.PLOT_CARPET,
            # PostProcessingOptions.PLOT_SANKEY,
            PostProcessingOptions.PLOT_SINGLE_DAYS,
            PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS,
            PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER,
        ])

    @classmethod
    def full_year_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year with all the post processing, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
        )
        simulation_parameters.enable_all_options()
        return simulation_parameters

    @classmethod
    def full_year_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a full year with all the post processing, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year + 1, 1, 1),
            seconds_per_timestep,
        )
        simulation_parameters.enable_plots_only()
        return simulation_parameters

    @classmethod
    def january_only_with_all_options(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 31),
            seconds_per_timestep,
        )
        simulation_parameters.enable_all_options()
        return simulation_parameters

    @classmethod
    def january_only_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single january, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 31),
            seconds_per_timestep,
        )
        simulation_parameters.enable_plots_only()
        return simulation_parameters

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
        simulation_parameters = cls(
            datetime.datetime(year, 6, 1),
            datetime.datetime(year, 8, 31),
            seconds_per_timestep,
        )
        simulation_parameters.enable_plots_only()
        return simulation_parameters

    @classmethod
    def _first_week_of(cls, year: int, month: int, seconds_per_timestep: int) -> SimulationParameters:
        """Builds the parameter set of the first seven days of one month.

        The one spelling of "a week starting on the first", shared by the January and the July
        window so an off-by-one fixed in one cannot survive in the other.

        Args:
            year: Calendar year the week is taken from.
            month: The month whose first week is covered.
            seconds_per_timestep: Resolution of the simulation in seconds.

        Returns:
            Parameters covering the 1st to the 8th of that month.
        """
        return cls(
            datetime.datetime(year, month, 1),
            datetime.datetime(year, month, 8),
            seconds_per_timestep,
        )

    @classmethod
    def one_week_only(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single week, primarily for unit testing."""
        return cls._first_week_of(year, 1, seconds_per_timestep)

    @classmethod
    def one_week_july(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for the first week of July; only the weather and its readers deliver July.

        The intent was a summer counterpart to ``one_week_only``: every week- and day-sized
        parameter set of this class starts on the first of January, which measures a cooling
        device, a solar-thermal collector or an air conditioner at its annual minimum, and a
        component that does nothing for the whole run proves nothing about itself. Shifting the
        same seven days into July was meant to exercise the cooling side.

        **It does that only in part today, and a caller must not assume more.** The weather looks
        the run's start up in its series and delivers July (``hisim/window_offset.py``), and with it
        the PV system and the building's solar gains, which compute from the weather's published
        window. Every other profile-driven component -- the LoadProfileGenerator occupancy, the
        cars, the CSV loader, the seasonal gates of the heat pump and the CHP -- still indexes its
        year-long profile by the timestep number, so timestep 0 is read as the 1st of January
        whatever this start date says, and a solar-thermal collector pairs its own sun position with
        the weather's irradiance on a clock of its own. The mid-year-start epic
        (``roadmap/midyear_start_epic.md``) fixes them one pull request at a time.

        The set is kept, and only the parity rig and the mid-year oracle (``scripts/midyear_oracle.py``)
        use it: the rig has fenced its July window (``scripts/p3_parity_matrix.py``) until the epic is
        done. Until then it belongs in nothing that reports physics.

        Args:
            year: Calendar year the week is taken from.
            seconds_per_timestep: Resolution of the simulation in seconds.

        Returns:
            Parameters covering the 1st to the 8th of July of that year.
        """
        return cls._first_week_of(year, 7, seconds_per_timestep)

    @classmethod
    def one_week_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single week, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 8),
            seconds_per_timestep,
        )

        simulation_parameters.enable_plots_only()
        return simulation_parameters

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
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
        )
        simulation_parameters.enable_all_options()
        return simulation_parameters

    @classmethod
    def one_day_only_with_only_plots(cls, year: int, seconds_per_timestep: int) -> SimulationParameters:
        """Generates a parameter set for a single day, primarily for unit testing."""
        simulation_parameters = cls(
            datetime.datetime(year, 1, 1),
            datetime.datetime(year, 1, 2),
            seconds_per_timestep,
        )
        simulation_parameters.enable_plots_only()
        return simulation_parameters

    def get_unique_key(self) -> str:
        """Gets a unique key from a simulation parameter class.

        A part-load threshold other than the default is part of the key, spelled with ``:g`` (``900`` for 900 and
        900.0 alike). Note: the legacy input cache that still keys through ``utils.get_cache_file``, the
        load-profile connector's, appends this key as well, so a run with a non-default threshold misses that cache
        and recomputes it, although it does not depend on the threshold. That costs time, not accuracy.
        """
        return "###".join(
            str(value) for value in (
                self.start_date,
                self.end_date,
                self.seconds_per_timestep,
                self.year,
                self.timesteps,
                self.country,
            )
        ) + ("" if self.weather_year is None else f"###weather={self.weather_year}") + (
            "" if self.part_load_threshold_is_default() else f"###part_load_above={self.part_load_above_seconds:g}"
        )

    def get_unique_key_as_list(self) -> List[str]:
        """Gets unique key from a simulation parameter class as list."""
        return [
            f"Start date: {self.start_date}",
            f"End date: {self.end_date}",
            f"Simulation year: {self.year}",
            f"Seconds per timestep: {self.seconds_per_timestep}",
            f"Total number of timesteps: {self.timesteps}",
            f"Country: {self.country}",
        ] + ([] if self.weather_year is None else [f"Weather year: {self.weather_year}"]) + (
            []
            if self.part_load_threshold_is_default()
            else [f"Part load above: {self.part_load_above_seconds:g} s per timestep"]
        )

    def cache_locations(self) -> "CacheLocations":
        """The ordered cache directories this calculation reads, and the first writable one it writes.

        Every cache reader and writer of a run goes through this, so a container that maps a
        read-only seed directory and a writable one in (hisim-epc.22) redirects the whole cache
        at once. Empty ``cache_directories`` means the single ``cache_dir_path``, exactly the
        behaviour every caller had before the list existed.
        """
        return CacheLocations(self.cache_directories or [self.cache_dir_path])


class FigureFormat(str, enum.Enum):

    """Set Figure Formats."""

    PNG = ".png"
    JPG = ".jpg"
