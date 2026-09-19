"""How a simulation-parameters file is compared, named and written.

Two parts of HiSim write these files and must agree to the byte on what one says. The fleet
recorder writes a *library* file, shared by every recording whose parameters mean the same thing
(:mod:`hisim.energy_system.recording.parameters`), and a run writes the parameter set it actually
used beside its realized record (:func:`hisim.energy_system.executor.write_records`). One
implementation of the comparison, the naming and the rendering serves both, and it lives here --
below both of them -- so that the run path does not have to import the recorder to describe
itself.

Nothing here has state or touches the filesystem: a parameter set reduces to a small mapping of
plain values, that mapping names itself, and it renders as the file that reproduces it.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


class ParameterNormalisation:
    """The semantic identity of one parameter set: what is compared, and what is ignored.

    Normalisation is the whole of requirement R8's comparison rule expressed as data. A parameter
    set reduces to a small mapping of plain values, two such mappings are equal exactly when the
    two runs would do the same thing, and everything the mapping omits is something that describes
    the machine rather than the run.

    Every method is a classmethod over explicit arguments; the class holds no state and is never
    instantiated. It exists so that the recorder, the file writer and the freshness check all
    reduce a parameter set through one implementation instead of three agreeing ones.
    """

    #: Fields of :class:`~hisim.simulationparameters.SimulationParameters` the comparison keeps,
    #: in the order a written file lists them. The post-processing options are handled separately
    #: because they are a set rather than a value, and ``year`` because it is derived.
    COMPARED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "start_date",
        "end_date",
        "seconds_per_timestep",
        "country",
        "logging_level",
    )

    #: Key under which the sorted option names appear in a normalised mapping and in a file.
    OPTIONS_KEY: ClassVar[str] = "post_processing_options"

    #: Key holding the calendar year, which is derived from the start date rather than settable.
    #: It is compared but never written, because the reader rebuilds it and would reject the key.
    YEAR_KEY: ClassVar[str] = "year"

    #: Fields deliberately left out of the comparison, each because it describes the machine or
    #: the invocation rather than the simulation. ``cache_dir_path`` is the one that matters:
    #: eleven setups point it at a cluster directory behind an existence probe, so keeping it
    #: would make two identical runs on two machines look different.
    IGNORED_FIELDS: ClassVar[Tuple[str, ...]] = (
        "cache_dir_path",
        "result_directory",
        "figure_format",
        "timesteps",
        "duration",
    )

    @classmethod
    def normalise(cls, parameters: SimulationParameters) -> Dict[str, Any]:
        """Reduces one parameter set to the mapping two runs are compared through.

        Args:
            parameters: The effective parameters of a run, after any setup has changed them.

        Returns:
            A mapping of plain values: the two dates as ISO strings, the resolution, the country,
            the logging level, the sorted option names and the year.
        """
        reduced: Dict[str, Any] = {}
        for field in cls.COMPARED_FIELDS:
            value = getattr(parameters, field)
            reduced[field] = value.isoformat() if isinstance(value, datetime.datetime) else value
        reduced[cls.OPTIONS_KEY] = cls.option_names(parameters.post_processing_options)
        reduced[cls.YEAR_KEY] = int(parameters.year)
        return reduced

    @classmethod
    def option_names(cls, options: Sequence[Any]) -> Tuple[str, ...]:
        """Turns a run's post-processing options into the sorted set the comparison uses.

        A sorted set rather than the written list: two setups that enable the same options in a
        different order, or one of them twice, are asking for the same post-processing, and a
        comparison that said otherwise would write a second file describing the first one.

        Args:
            options: The options as the parameters hold them, as members or as their integers.

        Returns:
            The distinct option names, sorted.
        """
        names = set()
        for option in options:
            names.add(PostProcessingOptions(option).name)
        return tuple(sorted(names))


class ParameterFileName:
    """The name a newly written parameter file gets, derived from what is inside it.

    R8.5 forbids naming such a file after the setup that first needed it, because it is shared the
    moment a second setup matches it, so the name has to come from the content: how long the run
    is, how finely it is resolved, and what its option set is for. Deriving it also makes it
    reproducible, which the freshness check needs — two runs of the recorder over the same fleet
    have to produce the same file names or every run would look like a change.

    The three vocabularies are class-level tables rather than branches so that adding a horizon or
    a purpose word is a line of data. Anything the tables do not cover falls back to a literal
    spelling, which is uglier but never wrong.
    """

    #: Suffix every simulation-parameters file of this format carries.
    SUFFIX: ClassVar[str] = ".simulation.yaml"

    #: Whole-day durations with a name of their own, longest first so the lookup is a scan.
    HORIZONS: ClassVar[Tuple[Tuple[int, str], ...]] = (
        (1, "one_day"),
        (2, "two_days"),
        (7, "one_week"),
        (14, "two_weeks"),
        (30, "one_month"),
    )

    #: Resolutions with a name of their own, in seconds per timestep.
    RESOLUTIONS: ClassVar[Tuple[Tuple[int, str], ...]] = (
        (60, "minutely"),
        (300, "5min"),
        (600, "10min"),
        (900, "15min"),
        (1800, "30min"),
        (3600, "hourly"),
    )

    #: What an option set is *for*, as families of option names. Every family whose options the
    #: set contains contributes its word, in this order, so a set asking for both key performance
    #: indicators and plots is named for both rather than for whichever was checked first.
    PURPOSES: ClassVar[Tuple[Tuple[str, Tuple[str, ...]], ...]] = (
        (
            "kpis",
            (
                "COMPUTE_KPIS",
                "WRITE_KPIS_TO_JSON",
                "WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER",
            ),
        ),
        ("costs", ("COMPUTE_OPEX", "COMPUTE_CAPEX")),
        ("lifecycle", ("COMPUTE_LIFECYCLE_COSTS", "LIFECYCLE_COST_REPORT")),
        (
            "plots",
            (
                "PLOT_LINE",
                "PLOT_CARPET",
                "PLOT_SANKEY",
                "PLOT_SINGLE_DAYS",
                "PLOT_MONTHLY_BAR_CHARTS",
                "PLOT_SPECIAL_TESTING_SINGLE_DAY",
                "MAKE_NETWORK_CHARTS",
            ),
        ),
        (
            "report",
            (
                "GENERATE_PDF_REPORT",
                "WRITE_COMPONENTS_TO_REPORT",
                "WRITE_ALL_OUTPUTS_TO_REPORT",
                "WRITE_NETWORK_CHARTS_TO_REPORT",
                "INCLUDE_CONFIGS_IN_PDF_REPORT",
                "INCLUDE_IMAGES_IN_PDF_REPORT",
            ),
        ),
        (
            "export",
            (
                "EXPORT_TO_CSV",
                "EXPORT_TO_PKL",
                "EXPORT_MONTHLY_RESULTS",
                "EXPORT_RESULTS_IN_ONE_FILE",
            ),
        ),
        (
            "scenarios",
            (
                "PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION",
            ),
        ),
    )

    #: Word for a run that asks for no post-processing at all.
    NO_PURPOSE: ClassVar[str] = "plain"

    #: Word for an option set none of the families covers, so that a new option still produces a
    #: legible name instead of an empty one.
    OTHER_PURPOSE: ClassVar[str] = "options"

    #: Separator between the three parts of a name.
    SEPARATOR: ClassVar[str] = "_"

    @classmethod
    def stem(cls, normalised: Mapping[str, Any]) -> str:
        """Builds the file stem describing one normalised parameter set.

        Args:
            normalised: The parameter set as :meth:`ParameterNormalisation.normalise` reduced it.

        Returns:
            A stem such as ``one_week_minutely_kpis``, without the format suffix.
        """
        parts = [
            cls.horizon(normalised["start_date"], normalised["end_date"]),
            cls.resolution(int(normalised["seconds_per_timestep"])),
            cls.purpose(tuple(normalised[ParameterNormalisation.OPTIONS_KEY])),
        ]
        return cls.SEPARATOR.join(parts)

    @classmethod
    def horizon(cls, start: str, end: str) -> str:
        """Names the period a run covers.

        A whole calendar year is named by the year, which is how the two shipped files already
        read; shorter whole-day periods get their table word or a plain day count; anything else
        falls back to the two dates, which is unlovely but unambiguous.

        Args:
            start: The start date, as an ISO string.
            end: The end date, likewise.

        Returns:
            The horizon part of the name.
        """
        first = datetime.datetime.fromisoformat(start)
        last = datetime.datetime.fromisoformat(end)
        span = last - first
        if first == datetime.datetime(first.year, 1, 1) and last == datetime.datetime(first.year + 1, 1, 1):
            return str(first.year)
        if span.seconds == 0 and span.microseconds == 0 and span.days > 0:
            for days, word in cls.HORIZONS:
                if span.days == days:
                    return word
            return f"{span.days}_days"
        return f"{first:%Y%m%dT%H%M}to{last:%Y%m%dT%H%M}"

    @classmethod
    def resolution(cls, seconds_per_timestep: int) -> str:
        """Names the timestep length of a run.

        Args:
            seconds_per_timestep: The run's resolution.

        Returns:
            The resolution part of the name.
        """
        for seconds, word in cls.RESOLUTIONS:
            if seconds == seconds_per_timestep:
                return word
        return f"{seconds_per_timestep}s"

    @classmethod
    def purpose(cls, option_names: Tuple[str, ...]) -> str:
        """Names what a run's post-processing option set is for.

        Args:
            option_names: The sorted option names of the parameter set.

        Returns:
            The purpose part of the name.
        """
        if not option_names:
            return cls.NO_PURPOSE
        selected = frozenset(option_names)
        words = [word for word, family in cls.PURPOSES if selected.intersection(family)]
        return cls.SEPARATOR.join(words) if words else cls.OTHER_PURPOSE


class ParameterFileWriter:
    """The text of a newly written simulation-parameters file.

    Written by hand rather than through a YAML dumper because the file is four keys and a list,
    and because every byte of it has to be the same on two machines: every string quoted so that
    the reader gives it back as the string it was rather than as whatever its spelling resolves
    to, the options
    in the sorted order the comparison uses, no trailing whitespace and no key whose value came
    from this machine. A dumper would give the same result today and no guarantee of it tomorrow.

    The header says what the file is for in the same words its name is built from, so that a
    reader of a directory of them can tell the two-day debugging pair from the yearly production
    one without opening either.
    """

    #: Keys written as single scalars, in this order, ahead of the option list.
    SCALAR_KEYS: ClassVar[Tuple[str, ...]] = ("seconds_per_timestep", "country", "logging_level")

    #: The comment lines a library file opens with. It says what the file is for in the same
    #: words its name is built from, and it is the same text whether a person wrote the file into
    #: ``simulation_parameters/`` or the recorder added it there because nothing beside it said
    #: the same thing -- the two are the same kind of file and are used interchangeably.
    HEADER: ClassVar[str] = (
        "# {horizon} at {resolution} resolution, post-processing for {purpose}. One of this\n"
        "# repository's shared simulation-parameters files: any run may be started from it, and\n"
        "# every recording whose parameters normalise to this content references it.\n"
    )

    #: The comment lines the parameter set of a finished run opens with. A run record is not a
    #: library file: it describes one run rather than offering itself to the next, and it is
    #: written next to the realized record so that the pair is the two arguments of the command
    #: that reproduces the run.
    RUN_HEADER: ClassVar[str] = (
        "# {horizon} at {resolution} resolution, post-processing for {purpose}. What this run\n"
        "# was given, written beside the realized record of what it built; the two together are\n"
        "# the arguments that re-run it. Machine-specific settings -- the cache and result\n"
        "# directories above all -- are deliberately absent.\n"
    )

    @classmethod
    def scalar(cls, value: Any) -> str:
        """Spells one scalar value the way YAML reads it back as the type it came in as.

        A string is quoted and every other value is written as it prints. Quoting matters because
        YAML's untyped scalars are resolved by their spelling: a ``country`` of ``"2022"`` written
        bare comes back as the integer 2022 and a ``country`` of ``"none"`` as ``None``, and the
        normalisation comparison that decides whether a recording may share a parameter file would
        then never match the file it just wrote. The quoting is JSON's, which is a subset of YAML's
        double-quoted style, so a value containing a quote or a backslash survives too.

        Args:
            value: The normalised value to write.

        Returns:
            The value's spelling, without the key or the separator.
        """
        return json.dumps(value) if isinstance(value, str) else str(value)

    @classmethod
    def text(cls, normalised: Mapping[str, Any], header: Optional[str] = None) -> str:
        """Renders one normalised parameter set as the file that reproduces it.

        Args:
            normalised: The parameter set as :meth:`ParameterNormalisation.normalise` reduced it.
            header: The comment template to open with, taking ``horizon``, ``resolution`` and
                ``purpose``; :attr:`HEADER`, the library file's, when omitted. A run's own record
                passes :attr:`RUN_HEADER`, because the two files say different things about
                themselves while saying the same thing about the run.

        Returns:
            The complete file text, ending in a newline.
        """
        options = tuple(normalised[ParameterNormalisation.OPTIONS_KEY])
        header = (header or cls.HEADER).format(
            horizon=ParameterFileName.horizon(normalised["start_date"], normalised["end_date"]),
            resolution=ParameterFileName.resolution(int(normalised["seconds_per_timestep"])),
            purpose=ParameterFileName.purpose(options),
        )
        lines = [
            f'start_date: "{normalised["start_date"]}"',
            f'end_date: "{normalised["end_date"]}"',
        ]
        lines += [f"{key}: {cls.scalar(normalised[key])}" for key in cls.SCALAR_KEYS]
        lines.append(f"{ParameterNormalisation.OPTIONS_KEY}:{'' if options else ' []'}")
        lines += [f"  - {name}" for name in options]
        return header + "\n".join(lines) + "\n"
