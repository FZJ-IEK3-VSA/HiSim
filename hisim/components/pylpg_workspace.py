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

import contextlib
import os
import pathlib
import shutil
import sys
from typing import ClassVar, Iterator, List

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


class LocalLpgCalculationFailedError(RuntimeError):
    """Raised when a local LoadProfileGenerator run left none of the results it was asked for.

    ``pylpg`` runs the LoadProfileGenerator binary with ``subprocess.run`` and neither passes
    ``check=True`` nor looks at the return code (``lpg_execution.LPGExecutor.execute_lpg_binaries``),
    so a calculation that dies -- the sqlite errors this generator is known for, a crash, a killed
    process -- returns to its caller looking exactly like one that worked. HiSim then reads the
    result files it expects and fails on whichever one it happens to open first, which is how a
    failed calculation used to be reported as ``FileNotFoundError`` on an arbitrary json file with
    no mention of the LoadProfileGenerator at all. This error is raised instead, at the point the
    absence is first detectable, and it carries what the binary itself printed.
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
    def run_the_copy_not_the_original(cls, executor: "lpg_execution.LPGExecutor") -> None:
        """Points a constructed executor at the binary in its own directory instead of the shared one.

        ``LPGExecutor.__init__`` copies the whole of ``LPG_linux`` into the calculation's ``C<index>``
        directory -- the executable, the dlls and the 51 MB ``profilegenerator.db3`` -- and then
        ``lpg_simengine_filepath`` returns the path in the *source* directory anyway, so every
        calculation runs the one shared binary with only its working directory to itself. .NET
        resolves the sqlite database beside the executable, which means concurrent calculations all
        open the same ``LPG_linux/profilegenerator.db3`` and the loser dies with
        ``SQLiteException ... database is locked``. It also means the shared executable is running
        whenever any calculation is, which is what makes a concurrent install fail with ``ETXTBSY``.

        The isolation pylpg needs is therefore already on disk and simply not used. Redirecting the
        source directory to the calculation directory makes ``lpg_simengine_filepath`` resolve to the
        copy, so each calculation runs its own binary next to its own database. ``copytree`` preserves
        the executable bit, so the copy is runnable as it stands.

        Args:
            executor: a freshly constructed executor, after its copy has been made.
        """
        copied_binary = pathlib.Path(executor.calculation_directory, executor.simengine_src_filename)
        if not copied_binary.is_file():
            log.warning(
                f"pylpg did not leave a binary at '{copied_binary}', so this calculation has to run the "
                f"shared one and may contend with any other running at the same time."
            )
            return
        executor.calculation_src_directory = pathlib.Path(executor.calculation_directory)

    BINARY_INSTALL_LOCK_NAME: ClassVar[str] = ".hisim-lpg-install.lock"

    @classmethod
    @contextlib.contextmanager
    def _binary_install_lock(cls) -> Iterator[None]:
        """Holds an exclusive lock over the shared LoadProfileGenerator installation.

        The lock file sits in the pylpg package directory, beside the installation it guards, so
        every process in one virtual environment contends for the same one. On a platform without
        ``fcntl`` the lock is skipped rather than emulated: the race needs concurrent processes in
        one environment, which is what CI and the parallel regenerator do on Linux.
        """
        package_directory = pathlib.Path(lpg_execution.__file__).parent.absolute()
        try:
            import fcntl  # pylint: disable=import-outside-toplevel
        except ImportError:
            yield
            return
        with open(package_directory / cls.BINARY_INSTALL_LOCK_NAME, "w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    @classmethod
    def binary_path(cls) -> pathlib.Path:
        """Returns the LoadProfileGenerator executable's path, the way ``LPGExecutor`` derives it.

        Duplicating the derivation is unwelcome and unavoidable: ``LPGExecutor`` computes it in
        ``__init__`` and exposes it nowhere a caller can reach before constructing one, and
        constructing one is the very thing that must not happen before the lock is held.
        """
        package_directory = pathlib.Path(lpg_execution.__file__).parent.absolute()
        if sys.platform.startswith("win"):
            return package_directory / "LPG_win" / "simengine2.exe"
        return package_directory / "LPG_linux" / "simengine2"

    @classmethod
    def ensure_binaries_installed(cls) -> None:
        """Installs the LoadProfileGenerator binaries once, under a lock, before any run needs them.

        The binaries are not shipped with pylpg. ``LPGExecutor.__init__`` checks whether the
        executable is on disk and, if it is not, downloads a zip and extracts it over the shared
        package directory. That check and that write are not atomic with respect to each other, so
        several processes starting together in a fresh environment -- four regenerator workers on a
        clean CI container, say -- all see it missing and all extract into the same directory. The
        first to finish begins executing the file the others are still writing, and the kernel
        refuses that with ``ETXTBSY``: "Text file busy". The loser's calculation dies, produces no
        results, and is then reported as a missing json file, because pylpg does not check the
        return code of the binary it ran.

        Doing the check and the install under an exclusive lock makes them atomic. The first process
        installs while the others wait; by the time they look, the executable is there and pylpg's
        own check inside ``LPGExecutor`` short-circuits without writing anything. Concurrent
        *execution* of the installed file needs no lock and does not get one -- many processes may
        run one binary, and only writing to it while it runs is refused.

        This is the half of Fault B that F3 did not address. F3 gave each process its own ``C<index>``
        working directory; the installation those directories are copied from is still one shared
        thing, and this is the guard for it. See roadmap/pylpg_flakiness.md.
        """
        if cls.binary_path().is_file():
            return
        with cls._binary_install_lock():
            if cls.binary_path().is_file():
                return
            log.information("Installing the LoadProfileGenerator binaries under an exclusive lock.")
            package_directory = pathlib.Path(lpg_execution.__file__).parent.absolute()
            lpg_execution.LPGExecutor.retrieve_lpg_binaries(package_directory)

    LOG_FILE_NAME: ClassVar[str] = "Log.CommandlineCalculation.txt"
    LOG_LINES_TO_QUOTE: ClassVar[int] = 30

    @classmethod
    def verify_results_were_produced(cls, calculation_index: int, result_folder: str) -> None:
        """Checks that a finished calculation actually left results, and explains it if not.

        Called immediately after the binary returns, because that is the last moment at which the
        failure can still be attributed to the calculation. Everything downstream reads individual
        result files, and a missing one there says only that a path does not exist.

        The LoadProfileGenerator writes its own log beside the results directory, and that log is
        where the real cause -- an sqlite error, a bad household reference -- is stated. Its tail is
        quoted into the exception rather than left on a disk the reader may not have: on a CI runner
        the directory is deleted with the workspace, and on a developer box it is deleted by the
        cleanup in :meth:`release` moments later.

        Args:
            calculation_index: the index the calculation ran under, for the message.
            result_folder: the directory the caller is about to read results from.

        Raises:
            LocalLpgCalculationFailedError: if the directory is missing or holds no files.
        """
        results = pathlib.Path(result_folder)
        if results.is_dir() and any(results.iterdir()):
            return

        reason = "did not exist" if not results.is_dir() else "was empty"
        message = [
            f"The local LoadProfileGenerator calculation for index {calculation_index} produced no "
            f"results: '{results}' {reason}.",
            "pylpg does not check the return code of the LoadProfileGenerator binary, so a "
            "calculation that failed returns as though it had worked; this is that case.",
        ]
        log_file = results.parent / cls.LOG_FILE_NAME
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        if lines:
            message.append(f"The last lines of {log_file}:")
            message.extend("    " + line for line in lines[-cls.LOG_LINES_TO_QUOTE:])
        else:
            message.append(f"No log was found at {log_file} either, so the binary failed early.")
        raise LocalLpgCalculationFailedError("\n".join(message))

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
