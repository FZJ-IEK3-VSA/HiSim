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
import time
from typing import ClassVar, Iterator, List, Optional, Sequence, Tuple

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
    def exclusive_generator_access(cls) -> Iterator[None]:
        """Holds an exclusive lock over the whole shared LoadProfileGenerator installation.

        The generator does not support two calculations running at once inside one installation.
        Giving each its own working directory was not enough, and neither was giving each its own
        copy of the executable and the database beside it: a cold parallel regeneration still lost a
        calculation to ``SQLiteException ... database is locked``, so the generator reaches a shared
        database by some route other than its own directory. Rather than keep hunting for that route
        through a closed-source binary, the lock is held for the length of a calculation and the
        calculations serialise.

        The cost is bounded and known: of the twenty-two system setups, four run local profiles, so
        eighteen keep their parallelism and four take turns -- and those four were contending with
        each other in any case. The install shares this lock rather than having one of its own,
        because installing while another calculation runs is precisely the write-a-running-executable
        case that fails with ``ETXTBSY``.

        This is a workaround for a defect in the generator, and it is expected to be deleted rather
        than maintained: the cache service (PR #584) removes the need to generate the same profile
        repeatedly at all, which is the real answer.


        The lock file sits in the pylpg package directory, beside the installation it guards, so
        every process in one virtual environment contends for the same one. On a platform without
        ``fcntl`` the lock is skipped rather than emulated: the race needs concurrent processes in
        one environment, which is what CI and the parallel regenerator do on Linux.

        The lock is not reentrant. ``flock`` associates a lock with an open file description, so a
        second acquisition from the same process on a new descriptor blocks against the first and
        deadlocks. Nothing may take this lock while already holding it, which is why
        :meth:`install_binaries_if_missing` does no locking of its own.
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
    def install_binaries_if_missing(cls) -> None:
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

        Doing the check and the install inside :meth:`exclusive_generator_access` makes them atomic
        with respect to every other process. The first installs while the others wait; by the time
        they look, the executable is there and pylpg's own check inside ``LPGExecutor``
        short-circuits without writing anything. **The caller must already hold that lock**; this
        method takes none, because it runs inside a calculation that holds it for its whole length.

        This is the half of Fault B that F3 did not address. F3 gave each process its own ``C<index>``
        working directory; the installation those directories are copied from is still one shared
        thing, and this is the guard for it. See roadmap/pylpg_flakiness.md.
        """
        if cls.binary_path().is_file():
            return
        log.information("Installing the LoadProfileGenerator binaries under an exclusive lock.")
        package_directory = pathlib.Path(lpg_execution.__file__).parent.absolute()
        lpg_execution.LPGExecutor.retrieve_lpg_binaries(package_directory)

    #: The fragments of a generator log that mark a failure worth trying again. Only sqlite's busy
    #: error qualifies: it is the one failure that is about timing rather than about the request.
    RETRY_SIGNATURES: ClassVar[Tuple[str, ...]] = ("database is locked",)

    #: How long to wait before each retry, in seconds; the length of the tuple is the number of
    #: retries. Two of them, backing off, bound the extra cost of a genuinely stuck calculation to
    #: under half a minute while giving a merely slow disk time to catch up.
    RETRY_DELAYS_IN_SECONDS: ClassVar[Tuple[float, ...]] = (5.0, 15.0)

    @classmethod
    def execute_and_verify(
        cls,
        executor: "lpg_execution.LPGExecutor",
        calculation_index: int,
        result_folder: str,
        required_files: Sequence[str],
    ) -> None:
        """Runs the generator and checks its results, trying again when it died on a locked database.

        The generator writes its results into sqlite files in WAL mode from several of its own
        threads, and on a slow disk -- a two-core CI runner, typically -- one of them can find the
        file busy and the whole run dies with ``SQLiteException: database is locked``. Nothing about
        the request is wrong when that happens; the same calculation a moment later succeeds. So that
        one failure, recognised by its text in the generator's log, is retried after a pause, while
        every other failure is raised at once: a missing household or a bad calcspec will not get
        better by waiting, and re-running it would only hide the real message under a delay.

        Each attempt reruns the binary in the same calculation directory; the generator deletes and
        recreates its results folder itself, so a partial result from the failed attempt cannot
        survive into the verification of the next one.

        Args:
            executor: the executor whose ``execute_lpg_binaries`` runs the calculation.
            calculation_index: the calculation's index, named in the failure.
            result_folder: where the generator writes its results.
            required_files: the result files the caller cannot do without.

        Raises:
            LocalLpgCalculationFailedError: when the calculation failed for a reason that is not a
                locked database, or kept failing on a locked database after every retry; in the
                second case the message says how many attempts were made.
        """
        attempts = len(cls.RETRY_DELAYS_IN_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            executor.execute_lpg_binaries()
            try:
                cls.verify_results_were_produced(calculation_index, result_folder, required_files=required_files)
                return
            except LocalLpgCalculationFailedError as error:
                locked = any(signature in str(error) for signature in cls.RETRY_SIGNATURES)
                if not locked or attempt == attempts:
                    if locked:
                        raise LocalLpgCalculationFailedError(
                            f"{error}\nThe calculation was attempted {attempts} times and died on a locked "
                            f"database every time, so this is not the transient contention a retry is for."
                        ) from error
                    raise
                delay = cls.RETRY_DELAYS_IN_SECONDS[attempt - 1]
                log.warning(
                    f"Local LoadProfileGenerator calculation {calculation_index} died on a locked database "
                    f"(attempt {attempt} of {attempts}); trying again in {delay:g} s."
                )
                time.sleep(delay)

    LOG_FILE_NAME: ClassVar[str] = "Log.CommandlineCalculation.txt"
    LOG_LINES_TO_QUOTE: ClassVar[int] = 30

    @classmethod
    def verify_results_were_produced(
        cls, calculation_index: int, result_folder: str, required_files: Optional[Sequence[str]] = None
    ) -> None:
        """Checks that a finished calculation actually left results, and explains it if not.

        Called immediately after the binary returns, because that is the last moment at which the
        failure can still be attributed to the calculation. Everything downstream reads individual
        result files, and a missing one there says only that a path does not exist.

        The LoadProfileGenerator writes its own log beside the results directory, and that log is
        where the real cause -- an sqlite error, a bad household reference -- is stated. Its tail is
        quoted into the exception rather than left on a disk the reader may not have: on a CI runner
        the directory is deleted with the workspace, and on a developer box it is deleted by the
        cleanup in :meth:`release` moments later.

        Checking that the directory merely holds *something* is not enough, and the first version of
        this check made that mistake. A calculation can die partway and leave some of its outputs
        behind: a run that produced eleven of the fourteen files it was asked for satisfied the
        emptiness test, and the absence resurfaced later as ``FileNotFoundError`` on whichever file
        the reader happened to open first -- ``SumProfiles.HH1.Warm Water.csv``, in the case that
        prompted this -- naming neither the calculation nor the generator, three layers from the
        cause. The caller knows which files it is about to read, so it says so and they are checked
        by name.

        Args:
            calculation_index: the index the calculation ran under, for the message.
            result_folder: the directory the caller is about to read results from.
            required_files: the result files this run must have produced. When omitted, only the
                presence of any output at all is checked, which is the weaker test described above.

        Raises:
            LocalLpgCalculationFailedError: if the directory is missing, holds no files, or is
                missing any of ``required_files``.
        """
        results = pathlib.Path(result_folder)
        missing = [name for name in (required_files or []) if not (results / name).is_file()]
        if results.is_dir() and any(results.iterdir()) and not missing:
            return

        if not results.is_dir():
            reason = "did not exist"
        elif not any(results.iterdir()):
            reason = "was empty"
        else:
            reason = (
                f"is missing {len(missing)} of the {len(required_files or [])} files the run needs: "
                + ", ".join(f"'{name}'" for name in missing)
            )
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
