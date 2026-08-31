"""Allocates and reclaims the working directory that a local LoadProfileGenerator run computes in.

``pylpg`` does not let a caller choose where it works. ``LPGExecutor.__init__`` hard-codes
``self.working_directory = pathlib.Path(__file__).parent.absolute()`` and then derives
``"C" + str(calculation_index)`` beneath it, so the run happens *inside the installed package* and
the calculation index is the only isolation the library offers. HiSim used to default that index to
the constant ``1``, which meant every local-LPG process in one virtual environment computed in the
same ``pylpg/C1`` directory: two at once corrupted each other's sqlite files, and whichever finished
first deleted the directory the other was still using.

This module owns the index arithmetic and the directory lifecycle instead, so the connector does not
have to. The default index comes from the process, which removes the shared constant; the directory
is claimed before it is created and released once the attempt ends, whether it succeeded or not; and
claiming one that already exists fails immediately with a message naming the index, because the
alternative is two runs silently interleaving in one folder. See ``roadmap/pylpg_flakiness.md`` F3
and F4.
"""

# clean

import os
import pathlib
import shutil
from typing import ClassVar, List

from pylpg import lpg_execution

from hisim import log

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class PylpgWorkingDirectoryInUseError(RuntimeError):
    """Raised when the ``pylpg/C<index>`` directory a run needs is already on disk.

    It is a distinct type rather than a bare ``RuntimeError`` so that a caller which genuinely wants
    to wait, retry or pick another index can tell this apart from a failure of the calculation
    itself. Nothing in HiSim does that today, and deliberately so: the connector lets it propagate
    like any other failure of the profile source.
    """


class PylpgWorkspace:
    """Turns a base index into per-household ``pylpg`` working directories and hands them back.

    The class exists because three separate call sites used to invent their own index -- the
    connector defaulted to ``1``, the multi-household request restarted its own counter at ``1``, and
    only the scenario regenerator passed anything distinct -- and none of them checked whether the
    directory they were about to compute in was free. Collecting the rule in one place makes the
    guarantee statable: distinct processes never derive the same directory, and a directory that is
    nevertheless occupied stops the run by name instead of being shared.

    The guarantee comes from the stride. A base index is multiplied by
    :attr:`HOUSEHOLDS_PER_BASE_INDEX` before the household's ordinal is added, so the indices derived
    from two different base indices cannot overlap. With the default base index -- the process id,
    which the kernel already keeps unique among running processes -- that makes concurrent runs
    disjoint without any coordination between them. What remains is a directory left behind by a run
    that was killed, over a process id the kernel has since recycled; :meth:`claim` reports exactly
    that case.
    """

    INDEX_ENVIRONMENT_VARIABLE: ClassVar[str] = "HISIM_LOCAL_LPG_CALC_INDEX"
    HOUSEHOLDS_PER_BASE_INDEX: ClassVar[int] = 100

    @classmethod
    def default_base_index(cls) -> int:
        """Returns the base calculation index for this process, from the environment or the pid.

        The environment variable stays supported because a caller that runs several HiSim processes
        itself -- the parallel scenario regenerator, for one -- would rather hand out small, readable
        indices it controls than trust a derivation. Everyone else gets the process id, which needs
        no coordination and is unique among live processes by construction.

        Returns:
            int: the base index; multiply it through :meth:`calculation_index` before use.

        Raises:
            ValueError: if the environment variable is set to something that is not an integer.
        """
        configured = os.environ.get(cls.INDEX_ENVIRONMENT_VARIABLE)
        if configured:
            return int(configured)
        return os.getpid()

    @classmethod
    def calculation_index(cls, base_index: int, household_ordinal: int) -> int:
        """Derives the calculation index for one household of a request from the run's base index.

        A request for several households needs several directories at once, because the result files
        of all of them are read after the last calculation finishes. Numbering them consecutively
        from the base index would be wrong with a process-derived base: process ids are handed out in
        sequence, so two runs started back to back would overlap immediately. Striding by
        :attr:`HOUSEHOLDS_PER_BASE_INDEX` keeps each base index's block to itself.

        Args:
            base_index: the run's base index, normally from :meth:`default_base_index`.
            household_ordinal: the household's position in this request, counting from zero.

        Returns:
            int: the calculation index to hand to ``pylpg``.

        Raises:
            ValueError: if the ordinal does not fit in the stride, which would let this request's
                indices spill into the next base index's block.
        """
        if not 0 <= household_ordinal < cls.HOUSEHOLDS_PER_BASE_INDEX:
            raise ValueError(
                f"A single local-LPG request cannot cover more than {cls.HOUSEHOLDS_PER_BASE_INDEX} "
                f"households, because the calculation indices of one run would then run into those of "
                f"another; household ordinal {household_ordinal} is out of range."
            )
        return base_index * cls.HOUSEHOLDS_PER_BASE_INDEX + household_ordinal

    @staticmethod
    def working_directory(calculation_index: int) -> pathlib.Path:
        """Returns the directory ``pylpg`` will compute in for the given calculation index.

        The path is reconstructed the way ``LPGExecutor.__init__`` builds it, from the location of
        the installed ``pylpg`` package, because the library exposes it nowhere else. That coupling
        is unpleasant and is the reason the fix is an index scheme rather than a temporary directory:
        writing into an installed package is the underlying mistake, and the index only rations the
        damage until ``pylpg`` allows the working directory to be chosen.

        Args:
            calculation_index: the index a ``pylpg`` calculation will run under.

        Returns:
            pathlib.Path: the absolute path of that calculation's directory.
        """
        pylpg_package_directory = pathlib.Path(lpg_execution.__file__).parent.absolute()
        return pylpg_package_directory / f"C{calculation_index}"

    @classmethod
    def claim(cls, calculation_index: int) -> pathlib.Path:
        """Reserves the working directory for a calculation, failing if something already holds it.

        The check has to happen before ``pylpg`` is invoked, because ``LPGExecutor`` clears whatever
        it finds and starts writing. If the directory belongs to a run that is still going, clearing
        it destroys that run; if it belongs to one that died, its debris makes this run fail later
        and much less clearly, which is how a development box degraded over a session into producing
        the wrong household on every run.

        Args:
            calculation_index: the index the calculation will run under.

        Returns:
            pathlib.Path: the claimed directory, which does not exist yet and which ``pylpg`` will
                create.

        Raises:
            PylpgWorkingDirectoryInUseError: if the directory is already on disk.
        """
        directory = cls.working_directory(calculation_index)
        if directory.exists():
            raise PylpgWorkingDirectoryInUseError(
                f"The local LPG working directory for calculation index {calculation_index} already "
                f"exists: {directory}. Either another run is using it right now, or a previous run was "
                f"killed and left it behind. Delete it if no run is using it, or set "
                f"{cls.INDEX_ENVIRONMENT_VARIABLE} to a free index for this process."
            )
        return directory

    @classmethod
    def release(cls, calculation_indices: List[int]) -> None:
        """Deletes the working directories of the given calculation indices, ignoring what is gone.

        Cleanup used to be driven by the result folder the calculation returned, which only exists
        once the calculation has produced one. A run that failed earlier than that -- an unavailable
        binary, a killed process, a collision -- logged that the result folder was ``None``, skipped
        the cleanup and left its directory on disk, so the next run failed on ``Directory not empty``
        before it started and one failure poisoned the next. The calculation index is known before the
        attempt begins, so resolving the path from it lets the cleanup run either way.

        Failures to delete are logged rather than raised. This runs in a ``finally`` block, where an
        exception would replace whatever the run was already failing with, and a directory that could
        not be removed is reported by the next :meth:`claim` anyway.

        Args:
            calculation_indices: the indices this run claimed; ones whose directory is already gone
                are skipped silently.
        """
        for calculation_index in calculation_indices:
            directory = cls.working_directory(calculation_index)
            try:
                if directory.exists():
                    shutil.rmtree(directory)
                    log.information(f"Local LPG working directory '{directory.name}' deleted.")
            except OSError as error:
                log.warning(f"Could not delete the local LPG working directory '{directory}': {error}")
