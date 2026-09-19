"""Spawning the recorder in a child interpreter: the one place that knows how it is invoked.

Nothing records a setup in the process that asked for it. A setup mutates module state, HiSim
singletons and the local load-profile-generator index, so two setups recorded in one interpreter
would record each other's leftovers; every caller therefore spawns a child, and this module is the
child's whole invocation. The child is this repository's own command line rather than a private
worker module, so that a fleet-wide run, a probe of one setup and a contributor recording a single
setup by hand all go through the same code and cannot drift apart.

Two callers use it — the fleet-wide driver in ``scripts/record_all_setups.py`` and
:class:`~hisim.energy_system.recording.probe_session.ProbeRunner` — and they share exactly three
decisions: which command records, which environment variable must not reach the child, and how the
subprocess is run. What comes back is the plain ``CompletedProcess``, because the two callers are
answerable for different things: the driver turns a nonzero exit into a per-setup outcome the run
summarises, the probe runner turns it into a refusal naming the column, and neither reading belongs
to the spawn.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - the only child is this repository's own command line
import sys
from pathlib import Path
from typing import ClassVar, Dict, Optional, Sequence, Tuple

import hisim


class ChildRecorder:
    """The recorder's child command, its environment and the subprocess call that runs it.

    Held as a class of constants and classmethods rather than as loose functions so that the
    command line, the variable stripped from the environment and the call's settings are stated
    once and read together. Callers supply what genuinely differs between them: the interpreter,
    the argument tail after the verb, and the working directory.
    """

    #: The child command that records one setup or one probe, completed with the paths the caller
    #: appends. Spelled as ``-m hisim.cli`` so that the child is the installed command line of this
    #: checkout and not a copy of it.
    COMMAND: ClassVar[Tuple[str, ...]] = ("-m", "hisim.cli", "energy-system", "record")

    #: Environment variable naming the local load-profile-generator working directory. It is
    #: cleared from every child's environment rather than set, so a machine with a stale setting
    #: records the same thing as a clean one and no recording run is pinned to a fixed directory.
    #: Cleared, ``PylpgWorkspace.default_base_index()`` derives the index from the child's own
    #: process, which is what keeps a recording out of the way of anything else using local
    #: profiles on the same machine -- the collisions #611 exists to prevent. Pinning it to a
    #: constant would reintroduce them for the length of a fleet-wide run, the better part of an
    #: hour.
    LPG_INDEX_VARIABLE: ClassVar[str] = "HISIM_LOCAL_LPG_CALC_INDEX"

    #: Environment variable naming the interpreter's extra import path. The child's copy of it is
    #: prefixed with the parent's own package root so that the two processes run the same code;
    #: see :meth:`environment`.
    PATH_VARIABLE: ClassVar[str] = "PYTHONPATH"

    @classmethod
    def package_root(cls) -> str:
        """The directory the parent's own ``hisim`` package lives in.

        Returns:
            The absolute path of the directory containing the imported ``hisim`` package --
            the worktree root for an editable checkout, the site-packages directory for an
            installed copy.
        """
        return str(Path(hisim.__file__).resolve().parent.parent)

    @classmethod
    def environment(cls) -> Dict[str, str]:
        """Builds the child's environment: this process's own, plus the code the parent is running.

        The parent's environment is inherited wholesale, because a recording has to see what a
        hand run of the same setup sees — the load profile provider's settings among it — and only
        :attr:`LPG_INDEX_VARIABLE` is removed.

        The child must record the *parent's* HiSim, and it would not by default: a worktree with no
        editable install of its own resolves ``hisim`` to whatever checkout the environment happens
        to have installed, so a run inside worktree B can report every twin fresh while describing
        checkout A's code (P3's F-2, seen twice, each time as a false all-clear on a script whose
        whole job is to keep generated files honest). Prepending the parent's own package root to
        ``PYTHONPATH`` makes the child import what the parent imported, whether or not the operator
        remembered to export anything. An existing ``PYTHONPATH`` is kept behind it rather than
        replaced, so the load profile provider's own path settings survive, and a root already on
        it moves to the front instead of appearing twice.

        Returns:
            A copy of this process's environment, without the local-profile index variable and
            with the parent's package root at the front of ``PYTHONPATH``.
        """
        environment = dict(os.environ)
        environment.pop(cls.LPG_INDEX_VARIABLE, None)
        root = cls.package_root()
        inherited = [
            entry for entry in environment.get(cls.PATH_VARIABLE, "").split(os.pathsep)
            if entry and entry != root
        ]
        environment[cls.PATH_VARIABLE] = os.pathsep.join([root, *inherited])
        return environment

    @classmethod
    def run(
        cls,
        arguments: Sequence[str],
        python: Optional[str] = None,
        cwd: Optional[Path] = None,
    ) -> "subprocess.CompletedProcess[str]":
        """Runs the recorder in a child interpreter and hands back what the child did.

        The output is captured as text so that a caller can quote the child in its own error
        message, and a nonzero exit is not raised here: what a failed recording means is the
        caller's decision.

        Args:
            arguments: Everything after the verb — the setup, the parameters file and the options
                the caller wants, already stringified.
            python: The interpreter to record with; this process's own when omitted.
            cwd: The directory the child runs in; the caller's own working directory when omitted.

        Returns:
            The completed process, carrying the exit code and the captured output.
        """
        vector = [python or sys.executable, *cls.COMMAND, *arguments]
        return subprocess.run(  # nosec B603 - fixed argument vector, no shell
            vector,
            cwd=None if cwd is None else str(cwd),
            env=cls.environment(),
            capture_output=True,
            text=True,
            check=False,
        )
