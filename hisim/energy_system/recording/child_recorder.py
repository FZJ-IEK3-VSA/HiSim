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

# clean

from __future__ import annotations

import os
import subprocess  # nosec B404 - the only child is this repository's own command line
import sys
from pathlib import Path
from typing import ClassVar, Dict, Optional, Sequence, Tuple


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

    @classmethod
    def environment(cls) -> Dict[str, str]:
        """Builds the child's environment: this process's own, minus the local-profile index.

        The parent's environment is inherited wholesale, because a recording has to see what a
        hand run of the same setup sees — the load profile provider's settings among it — and only
        :attr:`LPG_INDEX_VARIABLE` is removed.

        Returns:
            A copy of this process's environment without the local-profile index variable.
        """
        environment = dict(os.environ)
        environment.pop(cls.LPG_INDEX_VARIABLE, None)
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
