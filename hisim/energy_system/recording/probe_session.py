"""Running one setup's probes, and the two passes the grouping workflow is made of.

The pass around the human judgement has two halves and this module is both of them. ``probe`` runs
the setup once per module configuration, diffs the recordings against the baseline and writes the
workbook a person fills in. ``regroup`` takes the decision that came back, builds the grouped file
from the same probe runs and holds it against every one of them. Neither half decides anything: the
first observes, the second applies.

Every probe is recorded in its own interpreter, for the reason every recording is: setups mutate
module state, HiSim singletons and the local load-profile-generator index, and two of them in one
process would record each other's leftovers. The child is this repository's own command line rather
than a private worker, so a probe and a hand-run recording cannot drift apart. Probes construct
components and stop there — nothing is ever simulated to fill the table in.

The recordings go into a throwaway directory created *inside the repository*, and that is not
arbitrary. A recorded file's header carries the schema reference as a path relative to the file's
own directory, so a recording made two levels down would differ from the committed twin in its
first line and the byte-for-byte proof would fail for a reason that has nothing to do with the
system. One level under the repository root is where ``energy_systems/`` sits, so that is where the
probes are recorded and afterwards removed.
"""

# clean

from __future__ import annotations

import difflib
import os
import shutil
import subprocess  # nosec B404 - the only child is this repository's own command line
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar, Dict, Iterator, List, Optional, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError
from hisim.energy_system.loader import dump_energy_system, load_energy_system, parse_energy_system
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.recording.grouping import Grouping
from hisim.energy_system.recording.grouping_checks import check_grouping
from hisim.energy_system.recording.grouping_report import (
    ColumnVerdict,
    CombinationSpace,
    GroupingReport,
)
from hisim.energy_system.recording.matrix import ProbeMatrix, ProbeRecording
from hisim.energy_system.recording.probes import ModuleConfigMaterialiser, ProbeConfiguration, ProbeList
from hisim.energy_system.recording.regrouping import ColumnRealizer, GroupedSystemBuilder
from hisim.energy_system.recording.session import RecordedFileWriter, RecordingSession


class ProbeRunner:
    """Records one setup under each of its probe configurations, one interpreter each.

    The runner exists as a class only so that the command it builds, the environment it sets and
    the throwaway directory it uses are stated once. Everything else about it is the same decision
    the fleet-wide recording driver already made: sequential rather than parallel, this
    repository's own command line as the child, and the child's output kept for the message when it
    fails.
    """

    #: The child command that records one probe, completed with the paths.
    COMMAND: ClassVar[Tuple[str, ...]] = ("-m", "hisim.cli", "energy-system", "record")

    #: Environment variable naming the local load-profile-generator working directory. It is
    #: removed from the child's environment: each probe then derives its index from its own process
    #: id and cannot collide with other runs, and an exported value on the parent cannot leak in.
    LPG_INDEX_VARIABLE: ClassVar[str] = "HISIM_LOCAL_LPG_CALC_INDEX"

    #: Prefix of the throwaway directory the probes are recorded into.
    WORK_PREFIX: ClassVar[str] = ".probes-"

    @classmethod
    @contextmanager
    def work_directory(cls, root: Path) -> Iterator[Path]:
        """Creates and afterwards removes the directory the probe recordings live in.

        Args:
            root: The repository root, which the directory is created directly inside so that a
                recording's schema reference is spelled exactly as a committed twin's is.

        Yields:
            The directory.
        """
        directory = Path(tempfile.mkdtemp(prefix=cls.WORK_PREFIX, dir=root))
        try:
            yield directory
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @classmethod
    def run(
        cls,
        probe_list: ProbeList,
        setup: Path,
        parameters: Path,
        work_dir: Path,
        python: Optional[str] = None,
    ) -> Dict[str, ProbeRecording]:
        """Records every probe of one list.

        Args:
            probe_list: The configurations to record under.
            setup: The setup module.
            parameters: The simulation-parameters file each probe is recorded with.
            work_dir: Where the recordings and the materialised module configurations go.
            python: The interpreter to record with; this one when omitted.

        Returns:
            One recording per column, keyed by column name, in probe order.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` naming the column whose recording failed and
                what the child said.
        """
        recordings: Dict[str, ProbeRecording] = {}
        for probe in probe_list.probes:
            recordings[probe.column] = cls.one(probe_list, probe, setup, parameters, work_dir, python)
        return recordings

    @classmethod
    def one(
        cls,
        probe_list: ProbeList,
        probe: ProbeConfiguration,
        setup: Path,
        parameters: Path,
        work_dir: Path,
        python: Optional[str] = None,
    ) -> ProbeRecording:
        """Records one probe in its own interpreter and reads back what it wrote.

        Args:
            probe_list: The list the probe belongs to.
            probe: The probe to record.
            setup: The setup module.
            parameters: The simulation-parameters file.
            work_dir: Where the recording goes.
            python: The interpreter to record with; this one when omitted.

        Returns:
            The recording.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` when the child failed or wrote nothing.
        """
        module_config = ModuleConfigMaterialiser.write(probe_list, probe, work_dir)
        arguments: List[str] = [
            python or sys.executable,
            *cls.COMMAND,
            str(setup),
            str(parameters),
            "--out",
            str(work_dir),
        ]
        if module_config is not None:
            arguments += ["--module-config", str(module_config), "--probe", probe.column]
            arguments += ["--probes", probe_list.origin]
        environment = dict(os.environ)
        environment.pop(cls.LPG_INDEX_VARIABLE, None)
        completed = subprocess.run(  # nosec B603 - fixed argument vector, no shell
            arguments, env=environment, capture_output=True, text=True, check=False
        )
        label = "" if probe.is_baseline else f".{probe.column}"
        path = work_dir / f"{Path(setup).stem}{label}{RecordedFileWriter.SUFFIX}"
        if completed.returncode != 0 or not path.exists():
            raise cls._failed(probe, completed.returncode, completed.stdout + completed.stderr)
        text = path.read_text(encoding="utf-8")
        return ProbeRecording(column=probe.column, path=path, text=text, model=parse_energy_system(path))

    @classmethod
    def _failed(cls, probe: ProbeConfiguration, code: int, output: str) -> EnergySystemRecordingError:
        """Builds the refusal of a probe that did not record; the caller raises it.

        Args:
            probe: The probe.
            code: The child's exit code.
            output: Everything the child wrote, of which the tail is kept.

        Returns:
            The exception.
        """
        tail = "\n".join(line for line in output.splitlines() if line.strip())[-2000:]
        return EnergySystemRecordingError(
            EnergySystemErrorId.RECORDED_FILE_REJECTED,
            f"probe '{probe.column}'",
            f"recording the probe configuration '{probe.label()}' exited with {code}: {tail}",
            remedy="A probe that cannot be recorded is a defect in the setup or in the probe list.",
        )


class GroupingPass:
    """The two halves of the grouping workflow, either side of the person who fills the table in.

    Both halves start from the same place — record every probe, diff them against the baseline —
    which is why they are one class. What differs is what happens next: the first writes a workbook
    and stops, the second reads a decision and builds the file it describes, then holds that file
    against every recording the probes produced.

    Neither half is allowed to fill a cell in. That is the whole constraint the pass is designed
    around, and the code shape follows it: the matrix is produced without ever consulting a
    decision, and the decision is consumed without ever being amended.
    """

    #: The suffix of the grouped file, which is deliberately not the twin's own name: the flat twin
    #: stays exactly what the recorder produces and the freshness check keeps comparing it.
    GROUPED_SUFFIX: ClassVar[str] = ".grouped.energy_system.yaml"

    #: The extra header line the grouped file carries, naming the decision that shaped it.
    GROUPING_LINE: ClassVar[str] = "# Grouped by {grouping} from the probe configurations of {probes}."

    @classmethod
    def matrix(
        cls, probe_list: ProbeList, setup: Path, parameters: Path, work_dir: Path
    ) -> ProbeMatrix:
        """Records every probe and builds the three-state table from the recordings.

        Args:
            probe_list: The configurations to record under.
            setup: The setup module.
            parameters: The simulation-parameters file.
            work_dir: Where the recordings go.

        Returns:
            The matrix.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` when a probe could not be recorded.
        """
        return ProbeMatrix.of(probe_list, ProbeRunner.run(probe_list, setup, parameters, work_dir))

    @classmethod
    def regroup(
        cls,
        grouping: Grouping,
        matrix: ProbeMatrix,
        probe_list: ProbeList,
        setup: Path,
        parameters: Path,
        out_dir: Path,
    ) -> Tuple[EnergySystemFile, str, GroupingReport]:
        """Builds the grouped file and proves it against every probe column.

        Args:
            grouping: The decisions a person made.
            matrix: The three-state table and the recordings behind it.
            probe_list: The configurations, for the untested-combination report.
            setup: The setup module, named in the grouped file's header.
            parameters: The simulation-parameters file, likewise.
            out_dir: Where the grouped file goes.

        Returns:
            The grouped model, the text written for it and the report of the whole pass.

        Raises:
            EnergySystemRecordingError: ``EF-R6``/``EF-R7`` when the decision contradicts the
                matrix, and ``EF-R10`` when a column does not reproduce.
        """
        check_grouping(grouping, matrix)
        builder = GroupedSystemBuilder(grouping, matrix)
        grouped = builder.build()
        realizer = ColumnRealizer(grouped, builder)
        verdicts = tuple(cls._verdict(realizer, builder, matrix, column) for column in matrix.columns)
        report = GroupingReport(
            setup=grouping.setup,
            grouped=str(out_dir / f"{Path(setup).stem}{cls.GROUPED_SUFFIX}"),
            verdicts=verdicts,
            knobs=builder.knobs(),
            untested=CombinationSpace.untested(probe_list),
            groups=tuple(grouped.groups),
            variants={name: tuple(variant.options) for name, variant in grouped.variants.items()},
        )
        failed = [verdict for verdict in verdicts if not verdict.reproduced]
        if failed:
            raise cls._not_reproduced(grouping, failed)
        text = cls._write(grouped, grouping, probe_list, setup, parameters, out_dir)
        return grouped, text, report

    @classmethod
    def _verdict(
        cls,
        realizer: ColumnRealizer,
        builder: GroupedSystemBuilder,
        matrix: ProbeMatrix,
        column: str,
    ) -> ColumnVerdict:
        """Realizes the grouped file at one column and compares it with that column's recording.

        Args:
            realizer: The realizer over the grouped file.
            builder: The builder, for the column's knobs.
            matrix: The recordings.
            column: The column to check.

        Returns:
            The verdict, carrying a unified diff when the two differ.
        """
        recording = matrix.recordings[column]
        header, _ = RecordedFileWriter.split(recording.text)
        realized = realizer.text(column, header)
        knobs = sum(len(builder.differences(row.component, column)) for row in matrix.rows)
        if realized == recording.text:
            return ColumnVerdict(column=column, reproduced=True, knobs=knobs)
        diff = "".join(
            difflib.unified_diff(
                recording.text.splitlines(keepends=True),
                realized.splitlines(keepends=True),
                fromfile=f"recorded/{column}",
                tofile=f"realized/{column}",
            )
        )
        return ColumnVerdict(column=column, reproduced=False, knobs=knobs, diff=diff)

    @classmethod
    def _write(
        cls,
        grouped: EnergySystemFile,
        grouping: Grouping,
        probe_list: ProbeList,
        setup: Path,
        parameters: Path,
        out_dir: Path,
    ) -> str:
        """Writes the grouped file with the header saying what produced it.

        Args:
            grouped: The model to write.
            grouping: The decision, named in the header.
            probe_list: The probe list, named beside it.
            setup: The setup module.
            parameters: The simulation-parameters file.
            out_dir: Where the file goes.

        Returns:
            The text written.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` when the file that was just written does not
                load, validate or re-emit as itself.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{Path(setup).stem}{cls.GROUPED_SUFFIX}"
        header = RecordedFileWriter.header(
            RecordingSession.relative(setup), RecordingSession.relative(parameters), out_dir
        )
        header += cls.GROUPING_LINE.format(grouping=grouping.origin, probes=probe_list.origin) + "\n"
        text = header + dump_energy_system(grouped)
        path.write_text(text, encoding="utf-8")
        cls._verify(path, text)
        return text

    @classmethod
    def _verify(cls, path: Path, text: str) -> None:
        """Loads the grouped file back and checks that re-emitting it reproduces it.

        The realization check proves that the file resolves to what each probe recorded, but it
        works on the model rather than on the bytes, so a grouped file could in principle pass it
        and still be a document the reader refuses — a variant selecting an option it does not
        declare, a component name written in two places. Loading it back is what closes that gap,
        and re-emitting it is the format's own round-trip rule applied to a generated file.

        Args:
            path: The file just written.
            text: Its whole text, header included.

        Raises:
            EnergySystemRecordingError: ``EF-R5`` carrying the reader's refusal verbatim, or
                naming the round trip when the file is not in the canonical style.
        """
        _, body = RecordedFileWriter.split(text)
        try:
            reloaded = load_energy_system(path)
        except Exception as refusal:  # pylint: disable=broad-except
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_FILE_REJECTED,
                str(path),
                f"the grouped file does not load: {refusal}",
                remedy="The file is left in place for inspection.",
            ) from refusal
        if dump_energy_system(reloaded) != body:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_FILE_REJECTED,
                str(path),
                "re-emitting the grouped file does not reproduce it, so it is not canonical.",
                remedy="The file is left in place for inspection.",
            )

    @classmethod
    def _not_reproduced(
        cls, grouping: Grouping, failed: List[ColumnVerdict]
    ) -> EnergySystemRecordingError:
        """Builds the refusal of a grouped file that does not reproduce a probe column.

        Args:
            grouping: The decision, for the location.
            failed: The verdicts that did not hold, whose diffs are quoted.

        Returns:
            The exception.
        """
        columns = ", ".join(verdict.column for verdict in failed)
        diffs = "\n".join(verdict.diff for verdict in failed)
        return EnergySystemRecordingError(
            EnergySystemErrorId.GROUPING_NOT_REPRODUCED,
            f"{grouping.origin}:configurations",
            f"the grouped file does not reproduce the flat recording of {columns}:\n{diffs}",
            remedy=(
                "The grouped file is not written. Either the assignments describe a structure the "
                "format cannot resolve back to this configuration, or the probe list and the "
                "decision disagree about which switch the difference follows."
            ),
        )
