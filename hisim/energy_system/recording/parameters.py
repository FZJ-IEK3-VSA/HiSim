"""Which simulation-parameters file a recording points at, and when a new one is written.

A recorded energy-system file says what a household *is*; a simulation-parameters file says what
to do with it. Twenty-two setups would otherwise produce twenty-two near-identical parameter
files, so this module answers the question the recorder asks once per setup: does a file that
already exists say the same thing? If one does, the recording references it and nothing is
written. Only when nothing matches is a file written, and the same comparison covers the files
written earlier in the same run, so two setups needing identical parameters share one file rather
than getting a twin each.

The comparison is semantic, not textual. Two files agree when their period, resolution, sorted
option set, logging level, country and year agree, whatever order their keys happen to be in and
whatever they say about anything else. What "anything else" means matters more than what is kept:
``cache_dir_path`` above all, which eleven setups point at a cluster directory behind an
``os.path.exists`` probe. Keeping it would make the answer depend on which machine the recorder
ran on, which defeats the sharing and breaks the promise that recording is byte-identical
everywhere. It is therefore absent from the comparison and from anything written.

A file this module writes is named for its content — the horizon, the resolution and what its
option set is for — and never for the setup that first needed it, because it is shared from the
moment a second setup matches it. That is also why the name has to be derived rather than
invented: two runs of the recorder on the same fleet must produce the same file names.
"""

# clean

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

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
                "WRITE_COMPONENT_CONFIGS_TO_JSON",
            ),
        ),
        (
            "scenarios",
            (
                "PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION",
                "WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON",
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
    and because every byte of it has to be the same on two machines: the dates quoted, the options
    in the sorted order the comparison uses, no trailing whitespace and no key whose value came
    from this machine. A dumper would give the same result today and no guarantee of it tomorrow.

    The header says what the file is for in the same words its name is built from, so that a
    reader of a directory of them can tell the two-day debugging pair from the yearly production
    one without opening either.
    """

    #: Keys written as plain scalars, in this order, ahead of the option list.
    SCALAR_KEYS: ClassVar[Tuple[str, ...]] = ("seconds_per_timestep", "country", "logging_level")

    #: The comment line a generated file opens with.
    HEADER: ClassVar[str] = (
        "# {horizon} at {resolution} resolution, post-processing for {purpose}. Written by the\n"
        "# HiSim energy-system recorder because no file beside it said the same thing; it is\n"
        "# shared by every recording whose parameters normalise to this content.\n"
    )

    @classmethod
    def text(cls, normalised: Mapping[str, Any]) -> str:
        """Renders one normalised parameter set as the file that reproduces it.

        Args:
            normalised: The parameter set as :meth:`ParameterNormalisation.normalise` reduced it.

        Returns:
            The complete file text, ending in a newline.
        """
        options = tuple(normalised[ParameterNormalisation.OPTIONS_KEY])
        header = cls.HEADER.format(
            horizon=ParameterFileName.horizon(normalised["start_date"], normalised["end_date"]),
            resolution=ParameterFileName.resolution(int(normalised["seconds_per_timestep"])),
            purpose=ParameterFileName.purpose(options),
        )
        lines = [
            f'start_date: "{normalised["start_date"]}"',
            f'end_date: "{normalised["end_date"]}"',
        ]
        lines += [f"{key}: {normalised[key]}" for key in cls.SCALAR_KEYS]
        lines.append(f"{ParameterNormalisation.OPTIONS_KEY}:{'' if options else ' []'}")
        lines += [f"  - {name}" for name in options]
        return header + "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ParameterReference:
    """What one recording's parameters resolved to: a file, and whether it had to be written.

    Both halves are needed by different callers and neither can be derived from the other. The
    recorder writes the path into its header; the driver reports how many files a fleet-wide run
    added, which is the number a reviewer checks against the diff.
    """

    path: Path
    written: bool


class ParameterFileLibrary:
    """The parameter files that exist, and the one a recording should point at.

    One library serves one run of the recorder, however many setups it records. It reads the files
    that are already committed once, remembers every file it writes, and answers the same question
    against both, which is what makes two setups needing identical parameters share one file
    instead of getting a twin each.

    The search directory and the write directory are separate because they legitimately differ: a
    caller recording into a temporary directory still means to reference the committed parameter
    files, and only a genuinely new parameter set should land in the directory it asked for.
    """

    #: Glob matching every simulation-parameters file of this format.
    PATTERN: ClassVar[str] = "*.simulation.yaml"

    def __init__(self, search: Sequence[Path], write_to: Path) -> None:
        """Reads the existing parameter files a recording may reference.

        Args:
            search: Directories holding files a recording may reference, in priority order; a
                directory that does not exist contributes nothing.
            write_to: Directory a newly written file goes into.
        """
        self.write_to = Path(write_to)
        self.known: List[Tuple[Path, Dict[str, Any]]] = []
        seen = set()
        for directory in search:
            resolved = Path(directory).resolve()
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            for path in sorted(Path(directory).glob(self.PATTERN)):
                self.known.append((path, self.read(path)))

    @classmethod
    def read(cls, path: Path) -> Dict[str, Any]:
        """Normalises one parameter file on disk.

        Args:
            path: The file to read.

        Returns:
            Its normalised content.
        """
        from hisim.energy_system.executor import SimulationParametersReader  # noqa: PLC0415

        return ParameterNormalisation.normalise(SimulationParametersReader.read(path))

    def reference(self, parameters: SimulationParameters) -> ParameterReference:
        """Finds the file a recording of these parameters should point at, writing one if needed.

        Args:
            parameters: The effective parameters of the run being recorded.

        Returns:
            The file to reference and whether this call created it.
        """
        normalised = ParameterNormalisation.normalise(parameters)
        match = self.match(normalised)
        if match is not None:
            return ParameterReference(path=match, written=False)
        return ParameterReference(path=self.write(normalised), written=True)

    def match(self, normalised: Mapping[str, Any]) -> Optional[Path]:
        """Finds an existing file whose content normalises equal to the given parameters.

        Args:
            normalised: The parameter set to look for.

        Returns:
            The first file that says the same thing, or ``None``.
        """
        for path, content in self.known:
            if content == normalised:
                return path
        return None

    def write(self, normalised: Mapping[str, Any]) -> Path:
        """Writes one new parameter file and adds it to what later recordings may match.

        The name comes from the content; a stem already taken by a file saying something else
        gains a numeric discriminator, which happens only when two genuinely different option
        sets share a horizon, a resolution and a purpose word.

        Args:
            normalised: The parameter set to write.

        Returns:
            The path written.
        """
        self.write_to.mkdir(parents=True, exist_ok=True)
        stem = ParameterFileName.stem(normalised)
        path = self.write_to / f"{stem}{ParameterFileName.SUFFIX}"
        attempt = 1
        while path.exists():
            attempt += 1
            path = self.write_to / f"{stem}{ParameterFileName.SEPARATOR}{attempt}{ParameterFileName.SUFFIX}"
        path.write_text(ParameterFileWriter.text(normalised), encoding="utf-8")
        self.known.append((path, dict(normalised)))
        return path

    @classmethod
    def duplicates(cls, directory: Path) -> List[Tuple[Path, Path]]:
        """Finds pairs of committed parameter files that say the same thing.

        R8.6 turns "never two files with the same content" into something a job asserts rather
        than something the recorder merely avoids, because a duplicate can also be added by hand.

        Args:
            directory: The directory to check.

        Returns:
            Every pair of files whose normalised content is equal, each pair once.
        """
        entries = [(path, cls.read(path)) for path in sorted(Path(directory).glob(cls.PATTERN))]
        pairs: List[Tuple[Path, Path]] = []
        for index, (path, content) in enumerate(entries):
            for other, other_content in entries[index + 1:]:
                if content == other_content:
                    pairs.append((path, other))
        return pairs


def normalise_parameters(parameters: SimulationParameters) -> Mapping[str, Any]:
    """Reduces one parameter set to the mapping the recorder compares parameter files through.

    The public spelling of :meth:`ParameterNormalisation.normalise`, given a function of its own
    because it is the part of this module other packages have a reason to call and because the
    class name says how it works rather than what it is for.

    Args:
        parameters: The effective parameters of a run.

    Returns:
        The normalised mapping; two runs share a parameter file exactly when these are equal.
    """
    return ParameterNormalisation.normalise(parameters)
