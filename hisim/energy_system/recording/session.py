"""One recording, end to end: run a Python setup, observe it, write it down, prove it works.

The recorder's promise is not that it produces a file but that it produces a file that reproduces
the setup, so the last step of every recording is to load the file back through the executor and
build the system it describes. A recorded file that does not build is a failure of the recording,
not a file somebody has to fix afterwards, and it is reported as one.

Everything before that step is the pipeline the other modules of this package implement — import
the setup, let it construct its components, let the simulator resolve the declared defaults,
observe, build — and this module is the order they run in. The one thing it owns itself is the
comment header: the schema reference an editor binds to, and the line saying which setup and which
parameters produced the file, so that a reader of a twenty-file directory never has to guess.

Each recording is meant to happen in its own process, because a setup mutates module state,
singletons and the local LPG calculation index, and two setups in one interpreter would record each
other's leftovers. The driver that records the whole fleet is what enforces that; a single call
here does not, so that a test can record without paying for a subprocess.
"""

# clean

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Tuple

from hisim.energy_system.errors import EnergySystemError, EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.loader import dump_energy_system
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.recording.builder import build
from hisim.energy_system.recording.observe import RecordedSystem, observe
from hisim.energy_system.schema_export import default_schema_path
from hisim.simulationparameters import SimulationParameters


@dataclass(frozen=True)
class RecordingResult:
    """What one recording produced: the file, its text and the observation behind it.

    The observation is handed back rather than dropped because every check worth making about a
    recording compares the file with the run it came from — component count, wire set, the claim
    that a bare item really matches the declared defaults — and a caller that had to run the setup a
    second time to make those comparisons would be comparing two runs rather than one.

    ``text`` is the exact bytes written, header comments included, so a freshness check compares
    strings instead of re-reading what it just wrote.
    """

    setup: str
    path: Path
    model: EnergySystemFile
    text: str
    observed: RecordedSystem


class RecordedFileWriter:
    """The two comment lines a recorded file carries, and how they are told from its body.

    A recorded file is canonical YAML with a short header above it: the schema reference that makes
    an editor check the file while it is being read, and one line naming what produced it. Both are
    comments, so the reader ignores them and the canonical writer never emits them — which is
    exactly why the split has to be defined in one place. The round-trip rule of the format is that
    re-emitting a file reproduces it, and for a recorded file that rule holds of the body.

    Nothing in the header may change between two recordings of the same setup on two machines. That
    rules out a timestamp, a user name and a commit hash, and it is why the recorder states a
    version of its own rather than the version of the repository it happens to sit in.
    """

    #: Version of the recorder itself, stated in the header. It changes when the *shape* of what a
    #: recording produces changes, never per commit: a freshness check re-records every setup and
    #: fails on any diff, so a header that moved with the repository would fail it constantly.
    RECORDER_VERSION: ClassVar[str] = "1"

    #: The editor binding, written relative to the file so that it survives a checkout anywhere.
    SCHEMA_LINE: ClassVar[str] = "# yaml-language-server: $schema={schema}"

    #: The one line saying what produced the file, naming both inputs and the recorder version.
    ORIGIN_LINE: ClassVar[str] = (
        "# Recorded from {setup} with {parameters} by the HiSim energy-system recorder v{version}."
    )

    #: What every comment line of the header starts with, which is how the body is found again.
    COMMENT_PREFIX: ClassVar[str] = "#"

    #: The suffix a recorded energy-system file carries, appended to the setup's own stem.
    SUFFIX: ClassVar[str] = ".energy_system.yaml"

    @classmethod
    def header(cls, setup: str, parameters: str, out_dir: Path) -> str:
        """Builds the comment header of one recorded file.

        Args:
            setup: The setup module, spelled as the file should name it.
            parameters: The simulation-parameters file, likewise.
            out_dir: Where the file is written, which is what the schema reference is relative to.

        Returns:
            The header, ending in a newline.
        """
        schema = Path(os.path.relpath(default_schema_path(), out_dir)).as_posix()
        lines = [
            cls.SCHEMA_LINE.format(schema=schema),
            cls.ORIGIN_LINE.format(setup=setup, parameters=parameters, version=cls.RECORDER_VERSION),
        ]
        return "\n".join(lines) + "\n"

    @classmethod
    def split(cls, text: str) -> Tuple[str, str]:
        """Separates a recorded file's comment header from its canonical body.

        Args:
            text: The whole file.

        Returns:
            The header and the body; the header is empty for a file that carries none.
        """
        lines = text.splitlines(keepends=True)
        end = 0
        while end < len(lines) and lines[end].startswith(cls.COMMENT_PREFIX):
            end += 1
        return "".join(lines[:end]), "".join(lines[end:])


class RecordingSession:
    """Runs one setup and writes the energy-system file that describes what it built.

    A session is a small object over the three paths a recording needs — the setup, the parameters
    file and the output directory — because all three appear in more than one step and threading
    them through as arguments made every step's signature about bookkeeping rather than about what
    it does.

    The setup is spelled relative to the repository wherever it lies inside it, and so is the
    parameters file, because those two strings go into the file's header and a header carrying an
    absolute path would differ between two machines recording the same setup.
    """

    #: Files that mark the repository root, used to spell the two input paths relatively and to
    #: find the directory recorded files belong in.
    ROOT_MARKERS: ClassVar[Tuple[str, ...]] = ("setup.py", "hisim")

    #: Where recorded files go unless a caller says otherwise: beside the hand-written exemplar and
    #: the shared parameter files, which is where every file of this format lives.
    DEFAULT_OUTPUT_DIRECTORY: ClassVar[str] = "energy_systems"

    def __init__(self, module_path: Path, parameters_path: Path, out_dir: Path) -> None:
        """Prepares one recording.

        Args:
            module_path: The ``system_setups/*.py`` module to record.
            parameters_path: The simulation-parameters file the run uses, named in the header.
            out_dir: Directory the recorded file is written to; created when it does not exist.
        """
        self.module_path = Path(module_path).resolve()
        self.parameters_path = Path(parameters_path)
        self.out_dir = Path(out_dir)
        self.stem = self.module_path.stem

    @classmethod
    def default_output_directory(cls, near: Path) -> Path:
        """The directory a recording is written to when the caller names none.

        Recorded files belong beside the hand-written exemplar and the shared parameter files, so
        the default is the repository's own ``energy_systems/``. The repository is found by walking
        up from the setup being recorded rather than from this module, so an editable checkout and
        an installed package answer the same thing; a setup outside any checkout falls back to the
        plain relative name, which is what a caller running from the repository root would type.

        Args:
            near: The setup module being recorded.

        Returns:
            The output directory.
        """
        for parent in Path(near).resolve().parents:
            if all((parent / marker).exists() for marker in cls.ROOT_MARKERS):
                return parent / cls.DEFAULT_OUTPUT_DIRECTORY
        return Path(cls.DEFAULT_OUTPUT_DIRECTORY)

    @classmethod
    def relative(cls, path: Path) -> str:
        """Spells one path relative to the repository root, or by its name when it lies outside.

        Args:
            path: The path to spell.

        Returns:
            A forward-slash path that is the same string on every machine.
        """
        resolved = Path(path).resolve()
        for parent in resolved.parents:
            if all((parent / marker).exists() for marker in cls.ROOT_MARKERS):
                return resolved.relative_to(parent).as_posix()
        return resolved.name

    def record(self, parameters: SimulationParameters) -> RecordingResult:
        """Runs the setup, writes the file and proves that the file builds.

        Args:
            parameters: The parameters handed to the setup; a setup may still replace fields of it,
                and what the simulator ends up with is what the observation records.

        Returns:
            The recording.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` … ``EF-R4`` when the observed system cannot be
                written down, and ``EF-R5`` when the written file does not load, validate or build.
        """
        from hisim.hisim_main import get_description_from_py, initialize_from_python  # noqa: PLC0415

        setup_name = self.relative(self.module_path)
        simulator = initialize_from_python(str(self.module_path), parameters, None)
        simulator.prepare_calculation()
        simulator.connect_all_components()
        observed = observe(simulator, setup=setup_name)
        model = build(observed, self.stem, get_description_from_py(self.module_path) or None)
        text = self.write(model, setup_name)
        self.verify(len(text), simulator.get_simulation_parameters().result_directory)
        return RecordingResult(
            setup=setup_name, path=self.path, model=model, text=text, observed=observed
        )

    @property
    def path(self) -> Path:
        """Where this recording's file goes.

        Returns:
            ``<out dir>/<setup stem>.energy_system.yaml``.
        """
        return self.out_dir / f"{self.stem}{RecordedFileWriter.SUFFIX}"

    def write(self, model: EnergySystemFile, setup_name: str) -> str:
        """Writes the header and the canonical body of one recorded file.

        Args:
            model: The model to emit.
            setup_name: The setup as the header names it.

        Returns:
            The text written.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        header = RecordedFileWriter.header(setup_name, self.relative(self.parameters_path), self.out_dir)
        text = header + dump_energy_system(model)
        self.path.write_text(text, encoding="utf-8")
        return text

    def verify(self, written: int, result_directory: str) -> None:
        """Loads the file back through the executor and builds the system it describes.

        This is the recorder's whole claim, so it is checked on every recording rather than in a
        test: a file that cannot be loaded, validated, configured or wired does not describe the
        setup it was recorded from, whatever it looks like. The build stops short of simulating,
        which is enough — every decision the format makes has been made by then.

        Args:
            written: Length of the file just written, quoted in the message so that a failure says
                whether anything was produced at all.
            result_directory: Where the verification build may put anything it writes, taken from
                the run that was just recorded so that a caller's temporary directory is honoured.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` carrying the executor's own refusal verbatim.
        """
        from hisim.energy_system.executor import build_energy_system  # noqa: PLC0415

        parameters = self.read_parameters()
        parameters.result_directory = result_directory
        try:
            build_energy_system(self.path, parameters)
        except EnergySystemError as refusal:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_FILE_REJECTED,
                f"{self.setup_label}:{self.path}",
                f"the {written}-byte file recorded from '{self.setup_label}' does not build: {refusal}",
                remedy=(
                    "The file is left in place for inspection. A recording that does not build "
                    "means the setup uses something the format cannot yet express."
                ),
            ) from refusal

    @property
    def setup_label(self) -> str:
        """The setup as messages name it.

        Returns:
            The repository-relative spelling of the setup module.
        """
        return self.relative(self.module_path)

    def read_parameters(self) -> SimulationParameters:
        """Reads a fresh parameters object for the verification build.

        A fresh read rather than the object the setup was handed, because a setup routinely mutates
        the parameters it is given — post-processing options, logging level, the year — and the
        verification is meant to prove the *file* builds, not to inherit whatever the Python path
        left behind.

        Returns:
            The parameters as the file on disk states them.
        """
        from hisim.energy_system.executor import SimulationParametersReader  # noqa: PLC0415

        return SimulationParametersReader.read(self.parameters_path)


def record_setup(
    module_path: Path, parameters: SimulationParameters, out_dir: Path, *, parameters_path: Path
) -> RecordingResult:
    """Records one Python setup as an energy-system file and proves the file builds.

    The public entry point of the recorder: one setup, one parameters object, one directory, and a
    file that describes what the setup built. Nothing about the setup is changed and nothing about
    the file is guessed — every value comes from the objects the setup constructed.

    Args:
        module_path: The ``system_setups/*.py`` module to record.
        parameters: The parameters handed to the setup.
        out_dir: Where the recorded file goes.
        parameters_path: The parameters file the object was read from. A recording names both of
            its inputs in the file's header, and the object alone is nameless, so the path is asked
            for rather than reconstructed.

    Returns:
        The recording, carrying the file, its text and the observation it was built from.

    Raises:
        EnergySystemRecordingError: For any of the ``EF-Rx`` conditions.
    """
    session = RecordingSession(Path(module_path), Path(parameters_path), Path(out_dir))
    return session.record(parameters)
