"""Tests for recording the whole fleet: the parameter files, the defaults, and the driver.

The recorder itself is tested next door in ``test_energy_system_recording.py``, one setup at a
time. What this module tests is what happens when twenty-two of them are recorded in one run: that
a setup whose parameters are already described by a shipped file adds nothing, that two setups
needing the same new parameters share one file rather than getting a twin each, that recording is
reproducible to the byte, and that the driver covers every setup with no way to leave one out.

Almost everything here is fast, because almost everything here is a decision about data rather
than a simulation. The two exceptions are the reproducibility test, which really records the same
setup twice, and it uses the cheapest setup in the repository for that reason.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, List, Sequence, Tuple

import pytest

from hisim.cli import build_parser
from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.recording.parameters import (
    ParameterFileLibrary,
    ParameterFileName,
    ParameterNormalisation,
    normalise_parameters,
)
from hisim.energy_system.recording.session import RecordedFileWriter, RecordingSession
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from scripts.record_all_setups import (
    Paths,
    Recorder,
    Report,
    SetupDiscovery,
    SetupOutcome,
    main,
)


class Fleet:
    """Where the fleet lives, and the parameter sets these tests build their comparisons from.

    The paths are read from the repository rather than reconstructed, so that a test asserting
    "the driver covers every setup" keeps meaning that as setups come and go. The parameter sets
    are built here rather than in each test because most of the tests differ from the baseline in
    exactly one field, and stating the baseline once is what makes that difference visible.
    """

    #: The repository root, from which every other path here is derived.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: Where the Python setups live.
    SETUPS: ClassVar[Path] = ROOT / "system_setups"

    #: Where the committed twins and the shared parameter files live.
    ENERGY_SYSTEMS: ClassVar[Path] = ROOT / "energy_systems"

    #: The shipped one-day file every recording is started from.
    ONE_DAY: ClassVar[Path] = ENERGY_SYSTEMS / "one_day_15min.simulation.yaml"

    #: The cheapest setup in the repository: three toy components, no weather and no load
    #: profile, which is what makes recording it twice affordable in a test.
    CHEAPEST_SETUP: ClassVar[str] = "simple_system_setup_one"

    @classmethod
    def shipped(cls) -> SimulationParameters:
        """The parameters the shipped one-day file describes.

        Returns:
            A fresh parameters object; the caller may change it.
        """
        return SimulationParametersReader.read(cls.ONE_DAY)

    @classmethod
    def unshipped(cls) -> SimulationParameters:
        """A parameter set no committed file describes.

        A July week at a minute's resolution asking for key performance indicators: deliberately
        unlike both shipped files, so that a test about writing a new file is not at the mercy of
        somebody adding a third one.

        Returns:
            A fresh parameters object.
        """
        return SimulationParameters(
            start_date=datetime.datetime(2021, 7, 5),
            end_date=datetime.datetime(2021, 7, 12),
            seconds_per_timestep=60,
            post_processing_options=[
                PostProcessingOptions.COMPUTE_KPIS,
                PostProcessingOptions.WRITE_KPIS_TO_JSON,
            ],
        )

    @classmethod
    def library(cls, tmp_path: Path) -> ParameterFileLibrary:
        """A library that may reference the shipped files and writes new ones into a test's own directory.

        Args:
            tmp_path: The test's temporary directory.

        Returns:
            The library.
        """
        return ParameterFileLibrary(search=(cls.ENERGY_SYSTEMS, tmp_path), write_to=tmp_path)

    @classmethod
    def written(cls, tmp_path: Path) -> List[str]:
        """The parameter files a test's directory holds, by name.

        Args:
            tmp_path: The test's temporary directory.

        Returns:
            The file names, sorted.
        """
        return sorted(path.name for path in tmp_path.glob(ParameterFileLibrary.PATTERN))


class StubRecorder:
    """Stands in for the subprocess recorder so the driver's own rules can be tested in a second.

    The driver's contract is about which setups it covers and what it does with a failure, and
    neither needs a real recording to check: a stub that remembers what it was asked for answers
    the coverage question exactly, and one that refuses a named setup answers the failure question
    without waiting minutes for a setup that genuinely cannot be recorded.
    """

    def __init__(self, failing: Sequence[str] = ()) -> None:
        """Prepares a stub.

        Args:
            failing: Stems the stub refuses, as a real unrecordable setup would.
        """
        self.failing = set(failing)
        self.asked: List[str] = []

    def record(self, setup: Path, parameters: Path, out_dir: Path, python: str) -> SetupOutcome:
        """Pretends to record one setup, remembering that it was asked.

        Args:
            setup: The setup module.
            parameters: Ignored.
            out_dir: Ignored.
            python: Ignored.

        Returns:
            A successful outcome, or a failure for a stem this stub refuses.
        """
        del parameters, out_dir, python  # a stub records nothing, it only remembers being asked
        self.asked.append(setup.stem)
        if setup.stem in self.failing:
            return SetupOutcome(stem=setup.stem, ok=False, message="stub refusal", log="EF-R5")
        return SetupOutcome(stem=setup.stem, ok=True)


@pytest.mark.base
def test_normalisation_ignores_the_machine() -> None:
    """Catches a comparison that would make one run look different on two machines.

    ``cache_dir_path`` is the field eleven setups point at a cluster directory behind an existence
    probe. If it took part in the comparison, the same setup would need a different parameter file
    on the cluster than on a laptop, and no recording would ever be reproducible.
    """
    here = Fleet.shipped()
    elsewhere = Fleet.shipped()
    elsewhere.cache_dir_path = "/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/"
    elsewhere.result_directory = "/somewhere/else"

    assert normalise_parameters(here) == normalise_parameters(elsewhere)
    assert "cache_dir_path" not in normalise_parameters(here)


@pytest.mark.base
def test_options_compare_as_a_sorted_set() -> None:
    """Catches a comparison that would write a second file for the same post-processing.

    Two setups enabling the same options in a different order, or one of them twice, are asking
    for the same thing; a list comparison would call them different and duplicate the file.
    """
    one = Fleet.shipped()
    one.post_processing_options = [
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.EXPORT_TO_CSV,
    ]
    other = Fleet.shipped()
    other.post_processing_options = [
        PostProcessingOptions.EXPORT_TO_CSV,
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.EXPORT_TO_CSV,
    ]

    assert normalise_parameters(one) == normalise_parameters(other)


@pytest.mark.base
def test_shipped_parameters_emit_nothing_and_one_change_emits_one_file(tmp_path: Path) -> None:
    """T-11: catches a recorder that would write a parameter file describing a shipped one.

    The first half is the common case — twenty-two setups all running the shipped one-day pair —
    and a regression there would add twenty-two near-identical files. The second half proves the
    comparison is not simply answering yes: one added option and exactly one file appears.
    """
    library = Fleet.library(tmp_path)

    matched = library.reference(Fleet.shipped())
    assert matched.written is False
    assert matched.path == Fleet.ONE_DAY
    assert not Fleet.written(tmp_path)

    changed = Fleet.shipped()
    changed.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    fresh = library.reference(changed)
    assert fresh.written is True
    assert Fleet.written(tmp_path) == [fresh.path.name]


@pytest.mark.base
def test_two_setups_needing_the_same_new_parameters_share_one_file(tmp_path: Path) -> None:
    """T-12: catches a run that would give each setup its own copy of one parameter set.

    Both halves of R8.3 are here. Two setups asking for the same unshipped parameters must land on
    one file, and two that differ only in the machine-specific cache directory must land on that
    same file as well, because that difference is not a difference in the run.
    """
    library = Fleet.library(tmp_path)

    first = library.reference(Fleet.unshipped())
    second = library.reference(Fleet.unshipped())
    on_the_cluster = Fleet.unshipped()
    on_the_cluster.cache_dir_path = "/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/"
    third = library.reference(on_the_cluster)

    assert first.written is True
    assert second.written is False and second.path == first.path
    assert third.written is False and third.path == first.path
    assert Fleet.written(tmp_path) == [first.path.name]


@pytest.mark.base
def test_a_written_parameter_file_is_named_for_its_content_and_reads_back_equal(tmp_path: Path) -> None:
    """Catches a written file named after a setup, or one that does not describe what it was written for.

    R8.5 forbids the setup's name because the file is shared the moment a second setup matches it,
    and the whole scheme collapses if reading the file back does not normalise to what was asked
    for: the next run would write it again.
    """
    library = Fleet.library(tmp_path)
    wanted = Fleet.unshipped()

    reference = library.reference(wanted)

    assert reference.path.name == f"one_week_minutely_kpis{ParameterFileName.SUFFIX}"
    assert ParameterFileLibrary.read(reference.path) == normalise_parameters(wanted)
    assert ParameterNormalisation.IGNORED_FIELDS[0] not in reference.path.read_text(encoding="utf-8")


@pytest.mark.base
def test_no_two_committed_parameter_files_describe_the_same_run() -> None:
    """R8.6: catches a duplicate parameter file added by hand rather than by the recorder.

    Two files describing the same run would make two recordings of the same thing point at
    different files, and a reader comparing them would hunt for a difference that is not there.
    """
    assert not ParameterFileLibrary.duplicates(Fleet.ENERGY_SYSTEMS)


@pytest.mark.base
def test_the_command_line_writes_into_energy_systems_by_default() -> None:
    """T-13: catches a default output directory that is not the one every file of this format lives in.

    A recorder defaulting to the working directory would scatter twins wherever the contributor
    happened to stand, and the freshness job would never see them.
    """
    setup = Fleet.SETUPS / f"{Fleet.CHEAPEST_SETUP}.py"
    parsed = build_parser().parse_args(
        ["energy-system", "record", str(setup), str(Fleet.ONE_DAY)]
    )

    assert parsed.out is None, "--out must fall through to the recorder's own default"
    assert RecordingSession.default_output_directory(setup) == Fleet.ENERGY_SYSTEMS
    assert Paths.ENERGY_SYSTEMS == Fleet.ENERGY_SYSTEMS


@pytest.mark.base
def test_the_driver_records_every_setup_and_has_no_skip_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-14, first half: catches a driver that quietly leaves a setup out.

    R5.4 is the rule this protects: the set of setups covered is the set of setups that exist, so a
    new one is covered the day it is added and the only way to leave one out is to delete it.
    """
    stub = StubRecorder()
    monkeypatch.setattr(Recorder, "record", stub.record)

    assert main([]) == 0
    assert stub.asked == [path.stem for path in SetupDiscovery.all_setups()]
    assert stub.asked == sorted(
        path.stem for path in Fleet.SETUPS.glob("*.py") if path.name != "__init__.py"
    )


@pytest.mark.base
def test_the_driver_keeps_going_and_fails_naming_every_setup_it_could_not_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-14, second half: catches a driver that stops at the first failure or hides it.

    One broken setup must not mask the next, and the run must not pass: an unrecordable setup is a
    defect, so every one of them is named and the exit code says so.
    """
    refused = ["basic_household", "dynamic_components"]
    stub = StubRecorder(failing=refused)
    monkeypatch.setattr(Recorder, "record", stub.record)

    exit_code = main([])
    printed = capsys.readouterr().out

    assert exit_code == 1
    assert stub.asked == [path.stem for path in SetupDiscovery.all_setups()]
    for stem in refused:
        assert f"{stem}: stub refusal" in printed


@pytest.mark.base
def test_a_failure_summary_names_the_setup_and_quotes_the_recorder(capsys: pytest.CaptureFixture[str]) -> None:
    """Catches a summary that reports a count without saying which setup or why.

    The driver's failure output is the only thing a contributor sees when a fleet-wide run goes
    wrong, so it has to carry the setup, the reason and the recorder's own last words.
    """
    Report.outcomes(
        [
            SetupOutcome(stem="ok_one", ok=True),
            SetupOutcome(stem="broken_one", ok=False, message="the recorder exited with 1", log="EF-R5 at ..."),
        ]
    )
    printed = capsys.readouterr().out

    assert "broken_one: the recorder exited with 1" in printed
    assert "EF-R5 at ..." in printed
    assert "ok_one" not in printed


@pytest.mark.base
def test_recording_the_same_setup_twice_is_byte_identical(tmp_path: Path) -> None:
    """T-10: catches any non-determinism that would make the freshness job fail at random.

    R4 turns reproducibility into a requirement rather than an aesthetic: a timestamp, an absolute
    path or an iteration over a set anywhere in the recorder would make every run of the freshness
    job report every setup as changed. The two recordings go into directories at the same depth
    because the schema comment a recorded file opens with is written relative to the file.
    """
    setup = Fleet.SETUPS / f"{Fleet.CHEAPEST_SETUP}.py"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    texts: List[str] = []
    for directory in (first_dir, second_dir):
        directory.mkdir()
        parameters = SimulationParametersReader.read(Fleet.ONE_DAY)
        parameters.result_directory = str(directory / "results")
        session = RecordingSession(
            setup,
            Fleet.ONE_DAY,
            directory,
            ParameterFileLibrary(search=(Fleet.ENERGY_SYSTEMS, directory), write_to=directory),
        )
        texts.append(session.record(parameters).text)

    assert texts[0] == texts[1]
    assert texts[0].startswith(RecordedFileWriter.COMMENT_PREFIX)
    assert not Fleet.written(first_dir) and not Fleet.written(second_dir)


@pytest.mark.base
@pytest.mark.parametrize(
    "start, end, seconds, options, expected",
    [
        ("2021-01-01T00:00:00", "2021-01-02T00:00:00", 900, ("EXPORT_TO_CSV",), "one_day_15min_export"),
        ("2021-01-01T00:00:00", "2022-01-01T00:00:00", 60, ("PLOT_LINE",), "2021_minutely_plots"),
        ("2021-01-01T00:00:00", "2021-01-08T00:00:00", 3600, (), "one_week_hourly_plain"),
        ("2021-03-01T00:00:00", "2021-03-04T12:00:00", 120, ("COMPUTE_OPEX",), None),
    ],
)
def test_a_generated_name_describes_horizon_resolution_and_purpose(
    start: str, end: str, seconds: int, options: Tuple[str, ...], expected: str
) -> None:
    """Catches a naming scheme that would rename files between two runs of the recorder.

    The name has to be a function of the content and of nothing else, or the freshness job would
    see a rename every time somebody recorded the fleet. The last case has no expected name on
    purpose: what it checks is that an awkward period still produces something, and the same
    something twice.
    """
    normalised = {
        "start_date": start,
        "end_date": end,
        "seconds_per_timestep": seconds,
        ParameterNormalisation.OPTIONS_KEY: options,
    }

    stem = ParameterFileName.stem(normalised)

    assert stem == ParameterFileName.stem(normalised)
    if expected is not None:
        assert stem == expected


class RecordedChildEnvironment:
    """A stand-in for :func:`subprocess.run` that keeps the environment each child was handed.

    The recording driver's only channel to its children is the environment it builds for them, so
    capturing it is the whole test; nothing has to be simulated, which is what keeps this test in
    the fast set alongside the rest of the module.
    """

    def __init__(self) -> None:
        """Start out having seen nothing."""
        self.environments: List[dict] = []

    def __call__(self, *args, **kwargs) -> subprocess.CompletedProcess:
        """Record the environment and report a successful, silent child.

        Args:
            *args: The argument vector the driver built; ignored.
            **kwargs: The keyword arguments, of which ``env`` is what this test reads.

        Returns:
            A finished process with return code zero and no output.
        """
        self.environments.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0, stdout="", stderr="")


@pytest.mark.base
def test_a_recording_child_picks_its_own_profile_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No child is pinned to a fixed local-profile directory, and none inherits one either.

    A recording run takes the better part of an hour, and pinning it to one calculation index would
    put it in the same ``pylpg/C<index>`` directory as anything else on the machine using local
    profiles for all of that time -- the collisions the index scheme was introduced to end. Cleared
    instead, each child derives its index from its own process, and an operator with the variable
    exported cannot make one machine record something another would not.

    Catches: a driver that pins the index, and a driver that lets a stale exported setting through.
    """
    recorded = RecordedChildEnvironment()
    monkeypatch.setattr("scripts.record_all_setups.subprocess.run", recorded)
    monkeypatch.setenv(Recorder.LPG_INDEX_VARIABLE, "1")

    Recorder.record(
        setup=Fleet.SETUPS / f"{Fleet.CHEAPEST_SETUP}.py",
        parameters=Fleet.ONE_DAY,
        out_dir=tmp_path,
        python=sys.executable,
    )

    assert len(recorded.environments) == 1
    assert Recorder.LPG_INDEX_VARIABLE not in recorded.environments[0], (
        "the recorder pinned or passed on a local-LPG calculation index; cleared, the child derives "
        "its own from its process and cannot collide with another run"
    )
