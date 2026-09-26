"""Allocates and reclaims the working directory that a local LoadProfileGenerator run computes in.

``pylpg`` does not let a caller choose where it works. ``LPGExecutor.__init__`` hard-codes
``self.working_directory = pathlib.Path(__file__).parent.absolute()`` and then derives
``"C" + str(calculation_index)`` beneath it, so the run happens *inside the installed package* and
the calculation index is the only isolation the library offers. HiSim used to default that index to
the constant ``1``, which meant every local-LPG process in one virtual environment computed in the
same ``pylpg/C1`` directory: two at once corrupted each other's sqlite files, and whichever finished
first deleted the directory the other was still using.

Since hisim-epc.23 (the write guard, ``hisim.write_guard``) HiSim no longer lets it: the calculation
directories live below the calculation's cache directory (:meth:`PylpgWorkspace.work_root`) and
:meth:`PylpgWorkspace.start_executor` builds the executor there, so a calculation writes nothing into
the installed package. The binaries the calculations are copied from live in the cache directory as
well (:meth:`PylpgWorkspace.binary_root`, versioned by the LoadProfileGenerator release pylpg
downloads), and :meth:`PylpgWorkspace.install_binaries_if_missing` installs them there. Nothing in
HiSim reads or writes the ``pylpg`` package directory any more; an installation an older HiSim left
there is ignored.

This module owns the index arithmetic and the directory lifecycle instead, so the connector does not
have to. The default index comes from the process, which removes the shared constant; the directory
is claimed before it is created and released once the attempt ends, whether it succeeded or not; and
claiming one that already exists fails immediately with a message naming the index, because the
alternative is two runs silently interleaving in one folder. See ``roadmap/pylpg_flakiness.md`` F3
and F4.

A script that runs several HiSim processes in parallel must give each process a different base index,
otherwise two of them compute in the same ``C<index>`` directory. :class:`LpgBaseIndexPool` hands
those indices out and takes them back. It lives in this module so that the pool and
:meth:`PylpgWorkspace.default_base_index` share one definition of the environment variable's name.
"""

import contextlib
import inspect
import os
import pathlib
import queue
import re
import shutil
import socket
import sys
import time
from typing import ClassVar, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

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
    """Raised when the ``C<index>`` working directory a run needs is already on disk.

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


        The lock is held on the pylpg package directory, so every process in one virtual environment
        contends for the same one. The directory is only opened for reading: the binaries themselves
        live below the cache directory (:meth:`binary_root`), and nothing is written into the package.
        On a platform without ``fcntl`` the lock is skipped rather than emulated: the race needs
        concurrent processes in one environment, which is what CI and the parallel regenerator do on
        Linux. Processes of different environments sharing one cache directory are not serialised by
        it; :meth:`install_binaries_if_missing` is atomic on its own for them.

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
        # The lock is taken on the package directory itself, opened read-only: a lock file would
        # have to be created in the installed package, which a calculation must not write to
        # (hisim.write_guard). flock works on a directory descriptor as on any other. Only the
        # directory's identity is used; nothing in it is read or written.
        descriptor = os.open(package_directory, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    #: The subdirectory of a cache directory the LoadProfileGenerator binaries are installed in.
    BINARY_DIRECTORY_NAME: ClassVar[str] = "pylpg"

    #: How the release is spelled in the download URL of ``LPGExecutor.retrieve_lpg_binaries``, e.g.
    #: ``https://www.loadprofilegenerator.de/setup/LPG10.10.0_linux.zip``.
    RELEASE_IN_DOWNLOAD_URL: ClassVar["re.Pattern[str]"] = re.compile(r"/(LPG\d+(?:\.\d+)+)_[A-Za-z]+\.zip")

    _release: ClassVar[Optional[str]] = None

    @classmethod
    def lpg_release(cls) -> str:
        """Returns the LoadProfileGenerator release pylpg downloads, e.g. ``LPG10.10.0``.

        pylpg exposes the release nowhere but in the URLs hard-coded inside
        ``LPGExecutor.retrieve_lpg_binaries``, and the distribution carries no version metadata to
        fall back on. The release is therefore read out of that method's source, once per process.
        Deriving it rather than restating it as a constant means a pylpg upgrade that downloads a
        different release installs into a directory of its own name, next to the old one, instead of
        being filed under a name it does not have -- a restated constant would go stale silently,
        and a stale name in a cache that outlives the upgrade is exactly the mix-up the version in
        the path exists to prevent.

        Returns:
            str: The release, as the URL spells it: every URL in the method must name the same one.

        Raises:
            RuntimeError: When the source is unavailable or names no release, or more than one.
        """
        if cls._release is not None:
            return cls._release
        try:
            source = inspect.getsource(lpg_execution.LPGExecutor.retrieve_lpg_binaries)
        except (OSError, TypeError) as error:
            raise RuntimeError(
                "Cannot read pylpg's LPGExecutor.retrieve_lpg_binaries to learn which LoadProfileGenerator "
                f"release it downloads, which names the directory the binaries are installed in: {error}"
            ) from error
        releases = set(cls.RELEASE_IN_DOWNLOAD_URL.findall(source))
        if len(releases) != 1:
            raise RuntimeError(
                "pylpg's LPGExecutor.retrieve_lpg_binaries was expected to download exactly one "
                f"LoadProfileGenerator release, but its URLs name {sorted(releases) or 'none'}; "
                "PylpgWorkspace.RELEASE_IN_DOWNLOAD_URL no longer matches the pylpg installed."
            )
        cls._release = releases.pop()
        return cls._release

    @classmethod
    def binary_root(cls, cache_directory: str) -> pathlib.Path:
        """Returns the directory the LoadProfileGenerator binaries are installed in, below a cache directory.

        ``<cache_directory>/pylpg/<release>``, with ``LPG_linux`` (or ``LPG_win``) below it, the
        layout ``LPGExecutor.retrieve_lpg_binaries`` extracts into the directory it is given.

        The binaries used to live in the installed pylpg package, where pylpg puts them. A
        calculation may not write there (``hisim.write_guard``), and a fresh environment -- every CI
        runner -- has to install them during its first calculation, so they live where a calculation
        may write and where the next one finds them again: the cache directory. There they also
        travel with the cache (a CI cache restore, a container's cache volume) instead of being
        downloaded once per virtual environment. The release in the path keeps two pylpg versions
        that share one cache apart.

        Args:
            cache_directory: The calculation's cache write directory.

        Returns:
            pathlib.Path: ``<cache_directory>/pylpg/<release>``.
        """
        return pathlib.Path(cache_directory) / cls.BINARY_DIRECTORY_NAME / cls.lpg_release()

    @staticmethod
    def binary_folder_name() -> str:
        """Returns the folder ``retrieve_lpg_binaries`` extracts into on this platform."""
        return "LPG_win" if sys.platform.startswith("win") else "LPG_linux"

    @classmethod
    def binary_path(cls, cache_directory: str) -> pathlib.Path:
        """Returns the LoadProfileGenerator executable's path below a cache directory.

        The executable's name is the one ``LPGExecutor.__init__`` expects, restated because the
        executor exposes it nowhere a caller can reach before constructing one, and constructing one
        is the very thing :meth:`start_executor` avoids.

        Args:
            cache_directory: The calculation's cache write directory.

        Returns:
            pathlib.Path: ``<binary_root>/LPG_linux/simengine2`` (``LPG_win/simengine2.exe`` on Windows).
        """
        executable = "simengine2.exe" if sys.platform.startswith("win") else "simengine2"
        return cls.binary_root(cache_directory) / cls.binary_folder_name() / executable

    @classmethod
    def install_binaries_if_missing(cls, cache_directory: str) -> pathlib.Path:
        """Installs the LoadProfileGenerator binaries below the cache directory, once, before any run needs them.

        The binaries are not shipped with pylpg; pylpg downloads a zip on first use. HiSim installs
        them itself, into :meth:`binary_root`, never into the pylpg package directory, and an
        installation an older HiSim or pylpg itself left in the package directory is neither read
        nor removed.

        The install has to be atomic with respect to every other process, because the check and the
        write are separate steps: several processes starting together in a fresh environment -- four
        regenerator workers on a clean CI container, say -- all see the executable missing, and
        extracting into one directory while the first to finish is already executing the file the
        others are still writing is refused by the kernel with ``ETXTBSY``, "Text file busy" (Fault B,
        roadmap/pylpg_flakiness.md §4a). Two things make it so:

        * the caller holds :meth:`exclusive_generator_access`, which serialises the processes of one
          virtual environment. **The caller must already hold that lock**; this method takes none,
          because it runs inside a calculation that holds it for its whole length;
        * the zip is extracted into a staging directory of this process beside the installation and
          moved into place with one ``rename``, which fails when another process got there first. So
          two environments that share one cache directory -- two containers on one cache volume,
          which the lock does not reach -- cannot write into one installation either, and a download
          that dies halfway leaves no half-installation that would pass the check next time.

        Args:
            cache_directory: The calculation's cache write directory, which the write guard admits.

        Returns:
            pathlib.Path: The executable, installed now or earlier.

        Raises:
            RuntimeError: When the download ran but left no executable.
        """
        executable = cls.binary_path(cache_directory)
        if executable.is_file():
            return executable
        root = cls.binary_root(cache_directory)
        log.information(f"Installing the LoadProfileGenerator binaries into '{root}' under an exclusive lock.")
        staging = root.parent / f".{root.name}.{socket.gethostname()}.{os.getpid()}.partial"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            lpg_execution.LPGExecutor.retrieve_lpg_binaries(staging)
            staged_executable = staging / executable.parent.name / executable.name
            if not staged_executable.is_file():
                raise RuntimeError(
                    f"Downloading the LoadProfileGenerator binaries left no executable at '{staged_executable}'."
                )
            root.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(staging / executable.parent.name, executable.parent)
            except OSError:
                # Another process sharing the cache directory installed them in the meantime: its
                # installation is the same release, so this one is discarded with the staging.
                if not executable.is_file():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return executable

    #: The fragments of a generator log that mark a failure worth trying again. Only sqlite's busy
    #: error qualifies: it is the one failure that is about timing rather than about the request.
    RETRY_SIGNATURES: ClassVar[Tuple[str, ...]] = ("database is locked",)

    #: How long to wait before each retry, in seconds; the length of the tuple is the number of
    #: retries. Two of them, backing off, add at most twenty seconds of waiting and give a merely slow
    #: disk time to catch up. Each retry is also a full run of the generator, which costs what a run
    #: costs, so a genuinely stuck calculation takes three runs to fail instead of one.
    RETRY_DELAYS_IN_SECONDS: ClassVar[Tuple[float, ...]] = (5.0, 15.0)

    @classmethod
    def execute_and_verify(
        cls,
        executor: "lpg_execution.LPGExecutor",
        calculation_index: int,
        result_folder: str,
        required_files: Sequence[str],
    ) -> None:
        """Run the generator and check its results; if it died on a locked database, wait and try again.

        The LoadProfileGenerator writes its results into sqlite files from several threads, and on a slow disk
        (a CI runner, typically) one of them can find the file busy; the run then dies with
        ``SQLiteException: database is locked`` although nothing about the request is wrong. That failure is
        recognised by its text in the generator's log and retried after each delay in
        :attr:`RETRY_DELAYS_IN_SECONDS`. Any other failure is raised immediately, because waiting would not
        help and a retry would only delay the real message.

        Each attempt reruns the binary in the same calculation directory, whose copy of the toolchain the
        executor set up once. The results folder is removed before a retry, so a partial result from a failed
        attempt cannot pass the next attempt's check. The caller holds the generator lock throughout, the
        pauses included: the contention is inside this one run, and letting another calculation start during
        the pause would add to it rather than relieve it.

        Args:
            executor: the pylpg executor whose ``execute_lpg_binaries`` runs the calculation.
            calculation_index: the calculation's index, named in the failure message.
            result_folder: where the generator writes its results.
            required_files: the result files that must exist for the run to count as successful.

        Raises:
            LocalLpgCalculationFailedError: for any failure other than a locked database, or for a database
                that stayed locked through every retry; in that case the message says how many attempts
                were made.
        """
        attempts = len(cls.RETRY_DELAYS_IN_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            executor.execute_lpg_binaries()
            try:
                cls.verify_results_were_produced(calculation_index, result_folder, required_files=required_files)
                return
            except LocalLpgCalculationFailedError as error:
                locked = any(signature in str(error) for signature in cls.RETRY_SIGNATURES)
                if not locked:
                    raise
                if attempt == attempts:
                    raise LocalLpgCalculationFailedError(
                        f"{error}\nThe calculation was attempted {attempts} times and the generator's log "
                        f"mentioned a locked database after every attempt, so this is not the transient "
                        "contention a retry is for."
                    ) from error
                delay = cls.RETRY_DELAYS_IN_SECONDS[attempt - 1]
                log.warning(
                    f"Local LoadProfileGenerator calculation {calculation_index} died on a locked database "
                    f"(attempt {attempt} of {attempts}); trying again in {delay:g} s."
                )
                # Whatever the failed attempt left behind must not satisfy the next attempt's check.
                shutil.rmtree(result_folder, ignore_errors=True)
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

    #: The subdirectory of a cache directory the local calculations compute in.
    WORK_DIRECTORY_NAME: ClassVar[str] = "pylpg_work"

    @classmethod
    def work_root(cls, cache_directory: str) -> pathlib.Path:
        """Returns the directory this machine's local calculations compute in, below a cache directory.

        A calculation must not write into the installed ``pylpg`` package (``hisim.write_guard``), and
        the connector builds its profile while the setup is still constructing components, before the
        run's result directory exists; the cache directory is the one writable location a
        calculation has at that point. The host name keeps two containers that share one cache volume
        apart, because their process ids -- and with them the calculation indices -- can coincide. The
        calculation's own ``C<index>`` directory is deleted by :meth:`release` when it ends.

        Args:
            cache_directory: The calculation's cache write directory.

        Returns:
            pathlib.Path: ``<cache_directory>/pylpg_work/<host name>``.
        """
        return pathlib.Path(cache_directory) / cls.WORK_DIRECTORY_NAME / socket.gethostname()

    @classmethod
    def start_executor(cls, calculation_index: int, cache_directory: str) -> "lpg_execution.LPGExecutor":
        """Builds a pylpg executor that computes in the cache directory instead of inside the package.

        ``LPGExecutor.__init__`` hard-codes both its binaries and its calculation directory below the
        installed package and copies the one into the other. This does what that constructor does --
        minus the binary installation, which :meth:`install_binaries_if_missing` has done under the
        lock -- with the binaries taken from :meth:`binary_root` and the calculation directory
        ``C<index>`` below :meth:`work_root`, and then points the executor at its own copy of the
        binary (:meth:`run_the_copy_not_the_original`).

        ``working_directory`` is set to the binary root rather than to the package, so no attribute
        of the executor names the package directory. None of the executor's methods HiSim calls reads
        it: ``make_default_lpg_settings`` names the database in the calculation directory,
        ``execute_lpg_binaries`` runs the binary with the calculation directory as its working
        directory, and the connector reads the results from there itself.

        Args:
            calculation_index: The claimed index.
            cache_directory: The calculation's cache write directory, holding both the binaries and
                the calculation directories.

        Returns:
            The executor, its toolchain copied into its calculation directory.
        """
        executor = lpg_execution.LPGExecutor.__new__(lpg_execution.LPGExecutor)
        executor.working_directory = cls.binary_root(cache_directory)
        source = cls.binary_path(cache_directory)
        executor.calculation_src_directory = source.parent
        executor.simengine_src_filename = source.name
        executor.calculation_directory = cls.working_directory(calculation_index, cls.work_root(cache_directory))
        executor.calculation_directory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(executor.calculation_src_directory, executor.calculation_directory)
        cls.run_the_copy_not_the_original(executor)
        return executor

    @staticmethod
    def working_directory(calculation_index: int, root: pathlib.Path) -> pathlib.Path:
        """Returns the directory a calculation with the given index computes in.

        ``LPGExecutor.__init__`` would put it below the installed ``pylpg`` package; HiSim puts it
        below *root*, a directory of the cache (:meth:`work_root`), because writing into an installed
        package is the underlying mistake and a calculation may not do it (``hisim.write_guard``).

        Args:
            calculation_index: the index a ``pylpg`` calculation will run under.
            root: where the calculation directories live (:meth:`work_root`).

        Returns:
            pathlib.Path: the absolute path of that calculation's directory.
        """
        return pathlib.Path(root) / f"C{calculation_index}"

    @classmethod
    def claim(cls, calculation_index: int, root: pathlib.Path) -> pathlib.Path:
        """Reserves the working directory for a calculation, failing if something already holds it.

        The check has to happen before ``pylpg`` is invoked, because ``LPGExecutor`` clears whatever
        it finds and starts writing. If the directory belongs to a run that is still going, clearing
        it destroys that run; if it belongs to one that died, its debris makes this run fail later
        and much less clearly, which is how a development box degraded over a session into producing
        the wrong household on every run.

        Args:
            calculation_index: the index the calculation will run under.
            root: where the calculation directories live; see :meth:`working_directory`.

        Returns:
            pathlib.Path: the claimed directory, which does not exist yet and which ``pylpg`` will
                create.

        Raises:
            PylpgWorkingDirectoryInUseError: if the directory is already on disk.
        """
        directory = cls.working_directory(calculation_index, root)
        if directory.exists():
            raise PylpgWorkingDirectoryInUseError(
                f"The local LPG working directory for calculation index {calculation_index} already "
                f"exists: {directory}. Either another run is using it right now, or a previous run was "
                f"killed and left it behind. Delete it if no run is using it, or set "
                f"{cls.INDEX_ENVIRONMENT_VARIABLE} to a free index for this process."
            )
        return directory

    @classmethod
    def release(cls, calculation_indices: List[int], root: pathlib.Path) -> None:
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
            root: where the calculation directories live; see :meth:`working_directory`.
        """
        for calculation_index in calculation_indices:
            directory = cls.working_directory(calculation_index, root)
            try:
                if directory.exists():
                    shutil.rmtree(directory)
                    log.information(f"Local LPG working directory '{directory.name}' deleted.")
            except OSError as error:
                log.warning(f"Could not delete the local LPG working directory '{directory}': {error}")


class LpgBaseIndexPool:
    """A fixed set of local-LPG base indices that parallel workers borrow one at a time.

    Background: each local LoadProfileGenerator request computes inside a directory named
    ``C<index>`` (below :meth:`PylpgWorkspace.work_root`). The *base index* is the number a HiSim
    process derives that name from (see :meth:`PylpgWorkspace.calculation_index`). Two processes with
    the same base index write into the same directory and corrupt each other's results.

    A driver that runs N worker threads, each of which starts one HiSim subprocess after
    another, needs one index per worker. Numbering the subprocesses would not work, because
    there are more of them than workers and the numbers would grow without bound. Instead the script
    creates a pool of N indices and each worker borrows one for the duration of a subprocess::

        pool = LpgBaseIndexPool(slots=4)
        with pool.borrowed() as base_index:
            env = pool.child_environment(base_index)
            subprocess.run([...], env=env)

    Why small numbers instead of the process-id default of :meth:`PylpgWorkspace.default_base_index`:
    they are easier to read in a log. The directories they lead to are easy to tell apart -- base
    index 1 computes in ``C100``, 2 in ``C200`` and so on, because a base index is strided through
    :meth:`PylpgWorkspace.calculation_index` before it names a directory -- where the directories of
    four process ids cannot be told apart at a glance.

    The pool is thread-safe (it is a ``queue.Queue``). It is not shared between processes: a script
    that forks must create one pool per process, and two independent drivers that each own a pool on
    one machine hand out the same indices ``1`` to ``slots`` and collide. Runs that cannot coordinate
    with each other must stay with the process-id default instead of using a pool.
    """

    def __init__(self, slots: int) -> None:
        """Create a pool holding the indices ``1`` to ``slots``.

        Args:
            slots: the number of workers that will borrow at the same time.

        Raises:
            ValueError: if ``slots`` is less than one. An empty pool would make the first
                :meth:`borrowed` call wait forever.
        """
        if slots < 1:
            raise ValueError(f"A pool needs at least one slot to lend from; {slots} were asked for.")
        self._available: "queue.Queue[int]" = queue.Queue()
        for index in range(1, slots + 1):
            self._available.put(index)

    @contextlib.contextmanager
    def borrowed(self) -> Iterator[int]:
        """Take one index for the duration of a ``with`` block and return it afterwards.

        If all indices are out, the call blocks until one is returned. The index is returned in a
        ``finally`` clause, so it comes back even when the block raises.

        Yields:
            int: a base index that no other ``with`` block holds at the same time.
        """
        base_index = self._available.get()
        try:
            yield base_index
        finally:
            self._available.put(base_index)

    @staticmethod
    def child_environment(base_index: int, environment: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        """Return a copy of an environment with the base index set for a child process.

        The variable written is :attr:`PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE`, which
        :meth:`PylpgWorkspace.default_base_index` reads in the child. Both sides use the same
        constant, so they cannot disagree about the variable's name.

        Args:
            base_index: the index to pass on, normally the one from :meth:`borrowed`.
            environment: the environment to copy; ``os.environ`` when omitted. It is not modified.

        Returns:
            Dict[str, str]: the environment for the child, with the variable set.
        """
        child = dict(os.environ if environment is None else environment)
        child[PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE] = str(base_index)
        return child
