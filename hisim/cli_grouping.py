"""The ``hisim energy-system grouping`` verbs, and the second pass ``record --grouping`` runs.

The grouping pass is the one part of the recorder that has a person in the middle of it, so it is
the one part whose command line is a workflow rather than a single command. ``grouping probe`` runs
the setup once per module configuration and writes the workbook that asks the question; a person
answers it; ``grouping import`` normalises the answer into the file that is committed; and
``record --grouping`` builds the grouped energy system from it and proves it against every probe.

The three live here rather than in :mod:`hisim.cli` for a plain reason: they are a third as much
code again as everything else that module does, and the module is already long. What stays there is
the parser and the dispatch, so the shape of the command line is still readable in one place.

Two defaults are worth knowing. A probe list, a workbook and a grouping decision are all named for
the setup and all live in ``energy_systems/`` beside the recorded twin, so none of the three has to
be spelled out when the conventional name is wanted. And the workbook, alone among them, is not
committed: it is regenerated from the probe runs whenever it is wanted, which is why re-probing
carries the previous decision forward into it rather than asking for it again.
"""

# clean

from __future__ import annotations

import argparse
from pathlib import Path
from typing import ClassVar, Optional, TextIO

from hisim.energy_system.recording.grouping import Grouping
from hisim.energy_system.recording.grouping_io import dump_grouping, read_grouping
from hisim.energy_system.recording.probe_session import GroupingPass, ProbeRunner
from hisim.energy_system.recording.probes import ProbeList
from hisim.energy_system.repository import RepositoryLayout
from hisim.energy_system.recording.session import RecordingSession
from hisim.energy_system.recording.workbook import write_workbook
from hisim.energy_system.recording.workbook_import import read_workbook


class GroupingPaths:
    """Where the four files of one setup's grouping workflow live, and how they are named.

    All four are named for the setup's own stem and all but one sit in ``energy_systems/`` beside
    the recorded twin, which is what lets every command take the setup and work the rest out. The
    exception is deliberate rather than an oversight: the workbook is a scratch artefact and is
    listed in the repository's ignore file, so it lives beside the others but never joins them.
    """

    #: Where the probe list, the decision, the workbook and the grouped file live.
    DIRECTORY: ClassVar[str] = RecordingSession.DEFAULT_OUTPUT_DIRECTORY

    #: The simulation parameters a probe is recorded under unless the caller names another file.
    #: The shortest shipped horizon, because a probe constructs components and never simulates.
    DEFAULT_PARAMETERS: ClassVar[str] = "one_day_15min.simulation.yaml"

    #: The suffix of the workbook, which is the only one of the four that is not committed.
    WORKBOOK_SUFFIX: ClassVar[str] = ".grouping.xlsx"

    @classmethod
    def root(cls, near: Path) -> Path:
        """Finds the repository root by walking up from a path inside it.

        Args:
            near: Any path inside the checkout, normally the setup being probed.

        Returns:
            The root, or the current directory when the path lies outside a checkout.
        """
        return RepositoryLayout.root(near)

    @classmethod
    def directory(cls, near: Path) -> Path:
        """The directory the grouping files of one setup live in.

        Args:
            near: Any path inside the checkout.

        Returns:
            The repository's ``energy_systems/``.
        """
        return cls.root(near) / cls.DIRECTORY

    @classmethod
    def parameters(cls, near: Path, given: Optional[str]) -> Path:
        """The simulation-parameters file the probes are recorded with.

        Args:
            near: Any path inside the checkout.
            given: What the caller asked for, or ``None``.

        Returns:
            The caller's file, or the shortest shipped one.
        """
        return Path(given) if given else cls.directory(near) / cls.DEFAULT_PARAMETERS

    @classmethod
    def workbook(cls, setup: Path, given: Optional[str]) -> Path:
        """Where the workbook of one setup goes.

        Args:
            setup: The setup being probed.
            given: What the caller asked for, or ``None``.

        Returns:
            The caller's path, or ``energy_systems/<stem>.grouping.xlsx``.
        """
        return Path(given) if given else cls.directory(setup) / f"{setup.stem}{cls.WORKBOOK_SUFFIX}"

    @classmethod
    def grouping(cls, setup: Path, given: Optional[str]) -> Path:
        """Where the committed decision of one setup goes.

        Args:
            setup: The setup the decision is about.
            given: What the caller asked for, or ``None``.

        Returns:
            The caller's path, or ``energy_systems/<stem>.grouping.yaml``.
        """
        return Path(given) if given else cls.directory(setup) / f"{setup.stem}{Grouping.SUFFIX}"


class GroupingCommands:
    """The two ``grouping`` verbs and the body of ``record --grouping``.

    Each takes the parsed arguments and the two streams, does one thing and returns an exit code,
    exactly like the verbs next door. None of them decides anything about a system: the first asks
    a question, the second writes an answer down, and the third applies it and reports both what it
    proved and what it could not.
    """

    @classmethod
    def probe(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Records one setup under every probe configuration and writes the workbook to fill in.

        The workbook is prefilled from the recordings — three states per cell, one cell per probe —
        and, when the setup already has a committed decision, from that too, so that re-probing a
        setup that grew a component asks about the new row and no other.
        """
        del error_stream  # every command shares one signature; a refusal propagates to main()
        setup = Path(arguments.setup)
        probe_list = ProbeList.read(Path(arguments.probes))
        parameters = GroupingPaths.parameters(setup, arguments.simulation_parameters)
        decision = cls._previous(setup, arguments.grouping)
        with ProbeRunner.work_directory(GroupingPaths.root(setup)) as work_dir:
            matrix = GroupingPass.matrix(probe_list, setup, parameters, work_dir)
            path = write_workbook(
                GroupingPaths.workbook(setup, arguments.out), matrix, probe_list, decision
            )
        undecided = len(matrix.decided_rows())
        print(
            f"Probed {setup} under {len(matrix.columns)} configurations and wrote {path}: "
            f"{len(matrix.rows)} components, {undecided} of them not the same everywhere.",
            file=out,
        )
        return 0

    @classmethod
    def import_workbook(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Normalises a filled-in workbook into the grouping file that is committed."""
        del error_stream  # every command shares one signature; a refusal propagates to main()
        workbook = Path(arguments.workbook)
        decision = read_workbook(workbook)
        path = (
            Path(arguments.out)
            if arguments.out
            else GroupingPaths.grouping(Path(decision.setup), None)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_grouping(decision), encoding="utf-8")
        print(
            f"Imported {workbook} as {path}: {len(decision.assignments)} assignment(s) over "
            f"{len(decision.configurations)} configuration(s). The workbook is not committed.",
            file=out,
        )
        return 0

    @classmethod
    def record(cls, arguments: argparse.Namespace, out: TextIO, error_stream: TextIO) -> int:
        """Runs the second pass: builds the grouped file and proves it against every probe column.

        Every probe is recorded again rather than cached, because the grouped file's whole claim is
        that it reproduces what the setup builds *today*, and a cached recording would let the claim
        go stale without anybody noticing.
        """
        del error_stream  # every command shares one signature; a refusal propagates to main()
        setup = Path(arguments.setup)
        decision = read_grouping(Path(arguments.grouping))
        root = GroupingPaths.root(setup)
        probe_list = ProbeList.read(root / decision.probes)
        parameters = Path(arguments.simulation_parameters)
        directory = Path(arguments.out) if arguments.out else GroupingPaths.directory(setup)
        with ProbeRunner.work_directory(root) as work_dir:
            matrix = GroupingPass.matrix(probe_list, setup, parameters, work_dir)
            _, _, report = GroupingPass.regroup(
                decision, matrix, probe_list, setup, parameters, directory
            )
        for line in report.describe():
            print(line, file=out)
        return 0

    @classmethod
    def _previous(cls, setup: Path, given: Optional[str]) -> Optional[Grouping]:
        """Reads the decision already committed for one setup, when there is one.

        Args:
            setup: The setup being probed.
            given: An explicitly named decision file, or ``None`` for the conventional one.

        Returns:
            The decision, or ``None`` when the setup has none yet.
        """
        path = GroupingPaths.grouping(setup, given)
        return read_grouping(path) if path.exists() else None
