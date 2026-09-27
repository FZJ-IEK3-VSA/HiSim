"""Keeps every file a calculation writes inside its result directory or the cache directories.

A calculation -- one run of ``hisim_main``, of ``run_energy_system`` or of the RenoVisor
``Calculation`` -- may write in exactly two kinds of place (owner decision of 2026-09-26, bead
hisim-epc.23):

* **its result directory**, the fresh directory :class:`~hisim.result_path_provider.ResultPathProviderSingleton`
  hands out for it (or the directory its caller named: a RenoVisor job directory, a test's
  directory). Nothing the next calculation needs may live there, so the caller can delete it whole
  once it has copied what it wants. A result directory below ``hisim/inputs`` is refused: the
  shipped data is nobody's output;
* **the cache directories**, which are shared between calculations on purpose: the parameters'
  ``cache_locations()`` (``cache_dir_path``, the ordered ``cache_directories`` a container maps in
  through ``HISIM_CACHE_DIRECTORIES``) and the ``HISIM_CACHE_DIR`` / ``HISIM_CACHE_SHARED_DIR``
  overrides of the cache client.

Everything else -- the repository tree, ``hisim/inputs`` (bar the default cache below it), the
working directory, the home directory, the system temporary directory, an installed package -- is a
*stray write*, and this module refuses it at runtime.

**The registry.** Where a calculation may write is one list, :class:`CalculationDirectories`, which
the calculation owns: :class:`hisim.calculation_scope.CalculationScope` creates one per
calculation and hands it to the guard, and the code that learns a directory registers it there --
the result path provider its fresh or adopted result directory
(:meth:`CalculationDirectories.create_result_directory`, :meth:`CalculationDirectories.register_result_directory`),
the simulator and :class:`hisim.caching.locations.CacheLocations` the cache directories
(:meth:`CalculationDirectories.register_cache_directories`). Outside a calculation every
registration does nothing. The guard keeps no list of its own: what it allows is computed from the
registry at each check, and so are the library redirects below.

**How.** :func:`sys.addaudithook` reports every write-shaped operation the interpreter performs:
``open`` with a writing flag (``open()``, ``os.open``, ``Path.write_text``, pandas' and numpy's
writers, matplotlib's ``savefig`` -- they all end in one of those), ``os.mkdir``, ``os.rename`` and
``os.replace``, ``os.remove``/``os.unlink``, ``os.rmdir``, the metadata writes ``os.chmod``,
``os.utime`` and ``os.chown``, links, ``os.truncate``, the ``shutil`` copy, move, remove and
archive functions, and ``sqlite3.connect`` (allowed outside the registry's directories only when
the database is opened read-only, ``file:...?mode=ro``). Writing to a device file (``os.devnull``,
a character or block device below ``/dev``) is allowed; anything else below ``/dev`` -- ``/dev/shm``
is an ordinary directory -- is not. The hook cannot be removed again, so it is installed once,
lazily, the first time a guard is switched on -- a process that never runs a calculation (a plain
unit test) never gets it -- and it consults a context-local *active guard* that
:meth:`WriteGuard.calculation` sets for the length of one calculation. Without an active guard the
hook returns at once. Reading is never restricted.

**What a stray write does.** In the default ``enforce`` mode the offending operation raises
:class:`StrayWriteError` at the point of the write, so the traceback ends in the line that tried it.
Because a ``try``/``except Exception`` somewhere between the writer and the run could swallow that
error, every stray write is also recorded, and a calculation that recorded any fails when it ends
even if its body completed. With ``HISIM_WRITE_GUARD=collect`` nothing is raised at the write: the
calculation runs to its end and then fails with the full list, each ``(event, path)`` once, which
is what a survey wants. There is deliberately no ``off``.

**What the guard does for the libraries.** Some writes are nobody's output: they are made by the
interpreter or a library on its own account. The guard does not allow them; it removes them, and
each redirect is derived from the registry whenever it changes:

* bytecode: ``sys.dont_write_bytecode`` is set for the length of a calculation, so a module first
  imported mid-run is compiled in memory instead of writing a ``.pyc`` into the source tree or the
  installed package;
* matplotlib's configuration and font cache: ``MPLCONFIGDIR`` is pointed at ``matplotlib`` below the
  first *writable* cache directory (a read-only seed listed first is skipped, as
  :meth:`hisim.caching.locations.CacheLocations.write_directory` skips it), or below the result
  directory when the calculation has no cache directory (the economics CLI), so a cold machine never
  builds its font cache in ``~/.config`` or ``~/.cache``. The variable is only read when matplotlib
  is imported, so it is set only when nobody set it and matplotlib is not imported yet: **a
  matplotlib imported before the first calculation of the process is not redirected**, and keeps
  the directory it chose then;
* the temporary directory: :data:`tempfile.tempdir` is pointed at the result directory once the
  calculation's result directory is known, so every ``tempfile`` call of the calculation lands
  where the calculation's files belong and is deleted with them. Before that point -- while a
  Python setup is still building its components -- the system temporary directory is a stray
  write like any other. The interpreter's one-time probe of the system temporary directory (the
  first ``tempfile.gettempdir()`` of a process creates and deletes a file there) is made before the
  guard switches on, because it is not the calculation's and leaves nothing behind.

**What the guard cannot see.** Subprocesses are outside the hook's reach: a child process writes
without an audit event. HiSim starts two during a calculation, and both are pointed at the
calculation's directories: the local LoadProfileGenerator, which computes in its own directory below
the cache directory, next to the binaries it is copied from (see
:mod:`hisim.components.pylpg_workspace`), and Graphviz's ``dot``, which writes the system chart
straight into the result directory (:meth:`hisim.postprocessing.system_chart.SystemChart.render_png`).
Nor are writes through a file descriptor opened *before* the calculation started: the check is made
when a file is opened, so a descriptor opened earlier and written during the calculation passes
unseen.

**Threads.** The active guard is a :class:`contextvars.ContextVar`, so a thread started inside a
calculation starts without it (Python does not copy the context into a new thread unless asked to).
HiSim starts none during a calculation.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import stat
import sys
import sysconfig
import tempfile
import threading
import urllib.parse
from contextvars import ContextVar
from types import FrameType
from typing import Any, ClassVar, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


class StrayWriteError(RuntimeError):

    """A calculation wrote outside its result directory and the cache directories.

    It is a ``RuntimeError`` rather than a ``PermissionError`` on purpose: the many ``except
    OSError`` handlers in HiSim and its libraries treat a ``PermissionError`` as an ordinary
    filesystem hiccup and carry on, which would turn the guard's refusal into a silent skip.

    Args:
        stray_writes: Every stray write the calculation made, in order; at least one.
    """

    def __init__(self, stray_writes: Sequence["StrayWrite"]) -> None:
        """Build the one-line message from the stray writes."""
        self.stray_writes: Tuple[StrayWrite, ...] = tuple(stray_writes)
        if len(self.stray_writes) == 1:
            message = "HiSim wrote outside the calculation's result and cache directories: " + (
                self.stray_writes[0].describe()
            )
        else:
            message = (
                f"HiSim wrote outside the calculation's result and cache directories "
                f"{len(self.stray_writes)} times: "
                + "; ".join(f"({number}) {write.describe()}" for number, write in enumerate(self.stray_writes, 1))
            )
        super().__init__(message)


class CalculationDirectoryError(ValueError):

    """A calculation was given a result directory it may not have.

    Raised when a result directory below ``hisim/inputs`` is registered, and when a second
    simulator of one calculation claims a result directory of its own: one calculation, one result
    directory.
    """


@dataclasses.dataclass(frozen=True)
class StrayWrite:

    """One refused write: what was attempted, where, and which code attempted it.

    Args:
        event: The audit event, e.g. ``open`` or ``os.mkdir``.
        path: The absolute path the operation targeted.
        writer: ``file:line in function`` of the innermost frame outside the standard library,
            which is the code that performed the write (a library's writer when a library did it).
        caller: ``file:line in function`` of the innermost frame outside the standard library and
            the installed packages -- the HiSim line that asked for the write -- or ``None`` when it
            is the writer itself.
        allowed: The directories the guard allowed at the time.
    """

    event: str
    path: str
    writer: str
    caller: Optional[str]
    allowed: Tuple[str, ...]

    def describe(self) -> str:
        """Return the one-line account of this write, as the error message quotes it."""
        text = f"{self.event} of '{self.path}' by {self.writer}"
        if self.caller is not None:
            text += f", called from {self.caller}"
        return text + f" (allowed: {', '.join(self.allowed) if self.allowed else 'nothing'})"


class GuardMode(str, enum.Enum):

    """What a stray write does."""

    #: Raise at the write, and fail the calculation at its end if the error was swallowed.
    ENFORCE = "enforce"
    #: Record every stray write and fail the calculation at its end with the full list.
    COLLECT = "collect"


def _normalize(path: Union[str, "os.PathLike[str]"]) -> str:
    """Return the canonical absolute spelling of a path, symlinks resolved."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


class CalculationDirectories:

    """The registry of the directories one calculation may write in -- the only list the guard reads.

    :class:`hisim.calculation_scope.CalculationScope` creates one per calculation. Its result
    directories come first (the calculation's own, and a directory a caller adopted into it); its
    cache directories follow, in the order they were registered. Code that learns a directory
    registers it through the class methods, which find the running calculation's registry and do
    nothing outside a calculation; the scope and the tests may use the instance methods directly.
    """

    #: Where HiSim keeps the data it ships. A result directory below it is refused; the default
    #: cache below it (``hisim/inputs/cache``) is a cache directory, which is the one exception.
    INPUTS_DIRECTORY: ClassVar[str] = _normalize(os.path.join(os.path.dirname(os.path.abspath(__file__)), "inputs"))

    def __init__(self) -> None:
        """Start with no directory at all."""
        self._result_directories: List[str] = []
        self._cache_directories: List[str] = []
        #: ``(directory, claimant)`` of the fresh result directory the calculation created itself.
        self._claim: Optional[Tuple[str, str]] = None
        #: The library settings before the calculation, while it runs; ``None`` otherwise.
        self._saved: Optional[Dict[str, Any]] = None

    # ----------------------------------------------------------------------------------------------
    # What the calculation may write
    # ----------------------------------------------------------------------------------------------

    @property
    def result_directories(self) -> Tuple[str, ...]:
        """The result directories, canonical spelling, the calculation's own first."""
        return tuple(self._result_directories)

    @property
    def cache_directories(self) -> Tuple[str, ...]:
        """The cache directories, canonical spelling, in registration order."""
        return tuple(self._cache_directories)

    @property
    def allowed_directories(self) -> Tuple[str, ...]:
        """Every directory the calculation may write below: the result directories, then the caches."""
        return tuple(dict.fromkeys(self._result_directories + self._cache_directories))

    def result_directory(self) -> Optional[str]:
        """The calculation's result directory, once it is known."""
        return self._result_directories[0] if self._result_directories else None

    def writable_cache_directory(self) -> Optional[str]:
        """The first cache directory a write can go to, as ``CacheLocations.write_directory`` picks it.

        An existing directory qualifies when the process may write into it; a missing one when its
        nearest existing ancestor is a directory the process may write into. Nothing is created.
        """
        for directory in self._cache_directories:
            if os.path.isdir(directory):
                if os.access(directory, os.W_OK):
                    return directory
                continue
            ancestor = directory
            while not os.path.exists(ancestor) and os.path.dirname(ancestor) != ancestor:
                ancestor = os.path.dirname(ancestor)
            if os.path.isdir(ancestor) and os.access(ancestor, os.W_OK):
                return directory
        return None

    # ----------------------------------------------------------------------------------------------
    # Registering
    # ----------------------------------------------------------------------------------------------

    def add_result_directory(self, directory: Union[str, "os.PathLike[str]"]) -> str:
        """Register a result directory of this calculation; the first one is *the* result directory.

        Raises:
            CalculationDirectoryError: When the directory lies below ``hisim/inputs``.
        """
        root = _normalize(directory)
        if root == self.INPUTS_DIRECTORY or root.startswith(self.INPUTS_DIRECTORY + os.sep):
            raise CalculationDirectoryError(
                f"The result directory '{directory}' lies below '{self.INPUTS_DIRECTORY}', the data HiSim "
                "ships; a calculation's results go to a directory of their own. Point the run's "
                "result_directory elsewhere."
            )
        if root not in self._result_directories:
            self._result_directories.append(root)
            self._changed()
        return root

    def add_cache_directories(self, directories: Iterable[Optional[Union[str, "os.PathLike[str]"]]]) -> None:
        """Register cache directories of this calculation; empty entries are skipped."""
        for directory in directories:
            if not directory:
                continue
            root = _normalize(directory)
            if root not in self._cache_directories:
                self._cache_directories.append(root)
        self._changed()

    def _take_claim(self, claimant: str) -> None:
        """Refuse a claim by a second claimant: one calculation, one result directory."""
        if self._claim is not None and self._claim[1] != claimant:
            raise CalculationDirectoryError(
                f"{claimant} asked for a result directory, but this calculation already created "
                f"'{self._claim[0]}' for {self._claim[1]}: one calculation has one result directory. Run "
                "the second simulation as a calculation of its own, or give it a result_directory."
            )

    def create_directory(self, desired: str, unique: bool, claimant: str) -> str:
        """Create the calculation's fresh result directory and register it; the one place that does.

        With *unique*, the directory is created with ``os.mkdir``, which fails for a directory that
        exists, and on a collision ``_2``, ``_3`` ... is appended until the creation succeeds, so
        two calculations started in the same second get two directories. Each candidate is
        registered while its creation is attempted, which is what lets the way to it be built, and
        taken out again when it turns out to be another run's. Without *unique* the directory is
        created if missing and otherwise used as it stands.

        Args:
            desired: The directory name the result path provider's layout computed.
            unique: Whether the name has to be made unique.
            claimant: Who asks, for the message when a second one does.

        Returns:
            The directory created, as *desired* spelled it (plus the suffix).

        Raises:
            CalculationDirectoryError: When another claimant of this calculation already created
                one, or the directory lies below ``hisim/inputs``.
        """
        self._take_claim(claimant)
        candidate = desired
        suffix = 1
        while True:
            registered_before = _normalize(candidate) in self._result_directories
            root = self.add_result_directory(candidate)
            if not unique:
                os.makedirs(candidate, exist_ok=True)
                break
            os.makedirs(os.path.dirname(candidate) or ".", exist_ok=True)
            try:
                os.mkdir(candidate)
                break
            except FileExistsError:
                # Another run's: it must not stay writable for this one.
                if not registered_before:
                    self._result_directories.remove(root)
                    self._changed()
                suffix += 1
                candidate = f"{desired}_{suffix}"
        self._claim = (candidate, claimant)
        return candidate

    # ----------------------------------------------------------------------------------------------
    # The running calculation's registry; every method is a no-op outside a calculation
    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def running() -> Optional["CalculationDirectories"]:
        """Return the registry of the calculation running in this context, if any."""
        guard = WriteGuard.active()
        return guard.directories if guard is not None else None

    @classmethod
    def register_result_directory(cls, directory: Optional[Union[str, "os.PathLike[str]"]]) -> None:
        """Register a result directory with the running calculation; no-op outside one."""
        registry = cls.running()
        if registry is not None and directory:
            registry.add_result_directory(directory)

    @classmethod
    def register_cache_directories(cls, directories: Iterable[Optional[Union[str, "os.PathLike[str]"]]]) -> None:
        """Register cache directories with the running calculation; no-op outside one."""
        registry = cls.running()
        if registry is not None:
            registry.add_cache_directories(directories)

    @classmethod
    def create_result_directory(cls, desired: str, unique: bool, claimant: str) -> str:
        """Create a fresh result directory, registered with the running calculation if there is one.

        Outside a calculation the directory is created by the same rules and registered nowhere.
        See :meth:`create_directory`.
        """
        registry = cls.running()
        return (registry if registry is not None else cls()).create_directory(desired, unique, claimant)

    @classmethod
    def confirm_claim(cls, claimant: str) -> None:
        """Refuse a second claimant of the running calculation's fresh result directory; no-op outside one.

        The result path provider hands its claimed directory out again on every later request, so
        this is where a second simulator of one calculation is caught when the provider still holds
        the first one's directory.
        """
        registry = cls.running()
        if registry is not None:
            registry._take_claim(claimant)  # pylint: disable=protected-access

    # ----------------------------------------------------------------------------------------------
    # The library redirects, derived from the registry
    # ----------------------------------------------------------------------------------------------

    def activate(self) -> None:
        """Save the library settings and apply the redirects; the guard calls this as it switches on."""
        self._saved = _LibraryRedirects.save()
        sys.dont_write_bytecode = True
        self._changed()

    def deactivate(self) -> None:
        """Restore the library settings saved by :meth:`activate`."""
        if self._saved is not None:
            _LibraryRedirects.restore(self._saved)
            self._saved = None

    def _changed(self) -> None:
        """Derive the redirects again from the directories, while the calculation runs."""
        if self._saved is not None:
            _LibraryRedirects.derive(self, self._saved)


class _Targets:

    """Which argument of each audit event names the path that is written.

    The tuples are ``(path index, dir_fd index)`` pairs; a ``dir_fd`` index of ``None`` means the
    event carries none. ``open`` and ``sqlite3.connect`` are special-cased in
    :meth:`WriteGuard._targets`: whether they write depends on their flags or their URI.
    """

    WRITE_FLAGS: ClassVar[int] = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    BY_EVENT: ClassVar[Dict[str, Tuple[Tuple[int, Optional[int]], ...]]] = {
        "os.mkdir": ((0, 2),),
        "os.rename": ((0, 2), (1, 3)),
        "os.remove": ((0, 1),),
        "os.rmdir": ((0, 1),),
        "os.link": ((1, 3),),
        "os.symlink": ((1, 2),),
        "os.truncate": ((0, None),),
        "os.chmod": ((0, 2),),
        "os.chown": ((0, 3),),
        "os.utime": ((0, 3),),
        "shutil.copyfile": ((1, None),),
        "shutil.copymode": ((1, None),),
        "shutil.copystat": ((1, None),),
        "shutil.copytree": ((1, None),),
        "shutil.move": ((0, None), (1, None)),
        "shutil.rmtree": ((0, 1),),
        "shutil.chown": ((0, None),),
        "shutil.make_archive": ((0, None),),
        "shutil.unpack_archive": ((1, None),),
    }

    EVENTS: ClassVar[frozenset] = frozenset(BY_EVENT) | {"open", "sqlite3.connect"}


class _Frames:

    """Finds the frames a stray write is attributed to."""

    #: Directories whose frames are never "the writer": the standard library.
    STANDARD_LIBRARY: ClassVar[Tuple[str, ...]] = tuple(
        sorted(
            {
                os.path.normcase(os.path.realpath(sysconfig.get_paths()[name])) + os.sep
                for name in ("stdlib", "platstdlib")
            }
        )
    )

    #: Directories whose frames are a library rather than HiSim.
    INSTALLED_PACKAGES: ClassVar[Tuple[str, ...]] = tuple(
        sorted(
            {
                os.path.normcase(os.path.realpath(sysconfig.get_paths()[name])) + os.sep
                for name in ("purelib", "platlib")
            }
        )
    )

    #: This module's own file, whose frames are the guard and never the writer.
    OWN_FILE: ClassVar[str] = os.path.normcase(os.path.realpath(__file__))

    @classmethod
    def _is_standard_library(cls, filename: str) -> bool:
        """Whether a frame's file belongs to the interpreter rather than to any package."""
        if filename.startswith("<"):
            return True
        normalized = os.path.normcase(os.path.realpath(filename))
        if normalized == cls.OWN_FILE:
            return True
        return normalized.startswith(cls.STANDARD_LIBRARY) and not normalized.startswith(cls.INSTALLED_PACKAGES)

    @classmethod
    def _is_installed_package(cls, filename: str) -> bool:
        """Whether a frame's file belongs to an installed library."""
        return os.path.normcase(os.path.realpath(filename)).startswith(cls.INSTALLED_PACKAGES)

    @staticmethod
    def _describe(frame: FrameType) -> str:
        """Return ``file:line in function`` for one frame."""
        return f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"

    @classmethod
    def attribute(cls, frame: Optional[FrameType]) -> Tuple[str, Optional[str]]:
        """Return the writer and the HiSim caller, walking outwards from *frame*.

        Args:
            frame: The innermost frame of the write, the hook's caller.

        Returns:
            ``(writer, caller)``: see :class:`StrayWrite`.
        """
        innermost = frame
        writer: Optional[FrameType] = None
        caller: Optional[FrameType] = None
        while frame is not None:
            filename = frame.f_code.co_filename
            if not cls._is_standard_library(filename):
                if writer is None:
                    writer = frame
                if not cls._is_installed_package(filename):
                    caller = frame
                    break
            frame = frame.f_back
        if writer is None:
            writer = innermost
        writer_text = cls._describe(writer) if writer is not None else "an unknown frame"
        caller_text = cls._describe(caller) if caller is not None and caller is not writer else None
        return writer_text, caller_text


class WriteGuard:

    """Checks one calculation's writes against its :class:`CalculationDirectories`, and records the stray ones.

    Use :meth:`calculation` to run one; :class:`hisim.calculation_scope.CalculationScope` does.

    Args:
        label: What the calculation is, for the messages (a setup module, a request file).
        directories: The calculation's registry, which decides what is allowed.
        mode: What a stray write does.
    """

    #: The environment variable that selects :class:`GuardMode`; ``enforce`` when unset.
    MODE_VARIABLE: ClassVar[str] = "HISIM_WRITE_GUARD"

    #: The cache-client variables that name a directory the cache writes to (``hisim.caching.settings``).
    CACHE_DIRECTORY_VARIABLES: ClassVar[Tuple[str, ...]] = ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR")

    _active: ClassVar[ContextVar[Optional["WriteGuard"]]] = ContextVar("hisim_write_guard", default=None)
    _hook_installed: ClassVar[bool] = False
    _install_lock: ClassVar[threading.Lock] = threading.Lock()
    _inside_hook: ClassVar[threading.local] = threading.local()

    def __init__(
        self, label: str, directories: CalculationDirectories, mode: Union[GuardMode, str] = GuardMode.ENFORCE
    ) -> None:
        """Start with no stray write."""
        self.label = label
        self.directories = directories
        self.mode = GuardMode(mode)
        #: Every stray write, once per ``(event, path)``, in the order they were first made.
        self._recorded: Dict[Tuple[str, str], StrayWrite] = {}

    @property
    def stray_writes(self) -> List[StrayWrite]:
        """The stray writes so far, each ``(event, path)`` once."""
        return list(self._recorded.values())

    @property
    def allowed_directories(self) -> Tuple[str, ...]:
        """Every directory the calculation may write below, from its registry."""
        return self.directories.allowed_directories

    @staticmethod
    def _is_device(path: str) -> bool:
        """Whether *path* is ``os.devnull`` or an existing character or block device."""
        if path == os.devnull:
            return True
        if os.name != "posix" or not path.startswith("/dev/"):
            return False
        try:
            mode = os.stat(path).st_mode
        except OSError:
            return False
        return stat.S_ISCHR(mode) or stat.S_ISBLK(mode)

    def permits(self, path: str, event: str) -> bool:
        """Whether writing *path* with *event* stays inside the registry's directories.

        Creating a directory is also permitted when it is an ancestor of an allowed directory (the
        way to the result directory has to be built) or already exists (``os.makedirs`` and
        ``Path.mkdir(exist_ok=True)`` attempt the ``mkdir`` before they notice).

        Args:
            path: The absolute target of the operation.
            event: The audit event.

        Returns:
            True when the operation is allowed.
        """
        if self._is_device(path):
            return True
        target = _normalize(path)
        roots = self.directories.allowed_directories
        for root in roots:
            if target == root or target.startswith(root + os.sep):
                return True
        if event == "os.mkdir":
            if os.path.isdir(target):
                return True
            for root in roots:
                if root.startswith(target + os.sep):
                    return True
        return False

    # ----------------------------------------------------------------------------------------------
    # The hook
    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _install_hook(cls) -> None:
        """Install the audit hook once per process; audit hooks cannot be removed."""
        with cls._install_lock:
            if cls._hook_installed:
                return
            sys.addaudithook(cls._hook)
            cls._hook_installed = True

    @classmethod
    def _hook(cls, event: str, arguments: Tuple[Any, ...]) -> None:
        """The audit hook: cheap for every event but a write under an active guard."""
        if event not in _Targets.EVENTS:
            return
        guard = cls._active.get()
        if guard is None or getattr(cls._inside_hook, "busy", False):
            return
        cls._inside_hook.busy = True
        try:
            refused = guard._check(event, arguments)  # pylint: disable=protected-access
        finally:
            cls._inside_hook.busy = False
        if refused is not None and guard.mode == GuardMode.ENFORCE:
            raise StrayWriteError([refused])

    @classmethod
    def _targets(cls, event: str, arguments: Tuple[Any, ...]) -> List[str]:
        """Return the absolute paths an event writes, or none when it writes nothing."""
        if event == "open":
            if len(arguments) < 3:
                return []
            flags = arguments[2]
            mode = arguments[1]
            writes = (isinstance(flags, int) and flags & _Targets.WRITE_FLAGS) or (
                isinstance(mode, str) and any(letter in mode for letter in "wax+")
            )
            path = cls._as_path(arguments[0], None)
            return [path] if writes and path is not None else []
        if event == "sqlite3.connect":
            database = cls._sqlite_database(arguments[0]) if arguments else None
            return [database] if database is not None else []
        targets: List[str] = []
        for path_index, directory_index in _Targets.BY_EVENT[event]:
            if path_index >= len(arguments):
                continue
            directory_fd = (
                arguments[directory_index]
                if directory_index is not None and directory_index < len(arguments)
                else None
            )
            path = cls._as_path(arguments[path_index], directory_fd)
            if path is not None:
                targets.append(path)
        return targets

    @staticmethod
    def _sqlite_database(value: Any) -> Optional[str]:
        """Return the file a ``sqlite3.connect`` may write, or ``None`` when it writes no file.

        An in-memory database writes nothing, and neither does a database opened read-only through
        a URI (``file:<path>?mode=ro``). Every other connection may write -- a new database is
        created, an existing one may be changed -- so it is checked like any other write.
        """
        try:
            text = os.fsdecode(os.fspath(value))
        except TypeError:
            return None
        if text in ("", ":memory:"):
            return None
        if not text.startswith("file:"):
            return os.path.abspath(text)
        location, _, query = text[len("file:"):].partition("?")
        parameters = urllib.parse.parse_qs(query)
        if parameters.get("mode", [""])[-1] in ("ro", "memory"):
            return None
        if location.startswith("//"):
            # file://host/path: the authority is empty or localhost, and the path starts at the next slash.
            slash = location.find("/", 2)
            location = location[slash:] if slash >= 0 else ""
        location = urllib.parse.unquote(location)
        return os.path.abspath(location) if location and location != ":memory:" else None

    @staticmethod
    def _as_path(value: Any, directory_fd: Any) -> Optional[str]:
        """Turn an event's path argument into an absolute path, or ``None`` when it names no path.

        An integer is a file descriptor that was checked when it was opened. A relative path with a
        directory descriptor (``shutil.rmtree`` removes a tree that way) is resolved through
        ``/proc/self/fd`` where the platform has it; elsewhere it is left alone, because the
        operation that opened the directory was checked itself.
        """
        if value is None or isinstance(value, int):
            return None
        try:
            text = os.fsdecode(os.fspath(value))
        except TypeError:
            return None
        if text == "":
            return None
        if not os.path.isabs(text) and isinstance(directory_fd, int) and directory_fd >= 0:
            descriptor_link = f"/proc/self/fd/{directory_fd}"
            if not os.path.exists(descriptor_link):
                return None
            return os.path.join(os.readlink(descriptor_link), text)
        return os.path.abspath(text)

    def _check(self, event: str, arguments: Tuple[Any, ...]) -> Optional[StrayWrite]:
        """Check one audited event and record it when it strays.

        Returns:
            The recorded stray write, or ``None`` when the event is allowed.
        """
        for path in self._targets(event, arguments):
            if self.permits(path, event):
                continue
            key = (event, path)
            if key in self._recorded:
                # A log file appended to a thousand times is one finding, not a thousand.
                return self._recorded[key]
            writer, caller = _Frames.attribute(sys._getframe(2))  # pylint: disable=protected-access
            stray = StrayWrite(
                event=event, path=path, writer=writer, caller=caller, allowed=self.allowed_directories
            )
            self._recorded[key] = stray
            return stray
        return None

    # ----------------------------------------------------------------------------------------------
    # The calculation
    # ----------------------------------------------------------------------------------------------

    @classmethod
    def active(cls) -> Optional["WriteGuard"]:
        """Return the guard of the calculation running in this context, if any."""
        return cls._active.get()

    @classmethod
    def mode_from_environment(cls) -> GuardMode:
        """Return the mode ``HISIM_WRITE_GUARD`` selects.

        Raises:
            ValueError: When the variable holds anything but ``enforce`` or ``collect``.
        """
        value = os.environ.get(cls.MODE_VARIABLE, GuardMode.ENFORCE.value).strip().lower()
        try:
            return GuardMode(value)
        except ValueError:
            accepted = ", ".join(mode.value for mode in GuardMode)
            raise ValueError(f"{cls.MODE_VARIABLE}={value!r} is not one of {accepted}") from None

    @classmethod
    @contextlib.contextmanager
    def calculation(
        cls,
        label: str,
        directories: CalculationDirectories,
        mode: Optional[Union[GuardMode, str]] = None,
    ) -> Iterator["WriteGuard"]:
        """Run the body as one guarded calculation over the registry *directories*.

        The cache overrides of the environment (:attr:`CACHE_DIRECTORY_VARIABLES`) are registered
        first; everything else is registered by the code that learns it.

        Args:
            label: What the calculation is, for the messages.
            directories: The calculation's registry.
            mode: What a stray write does; ``HISIM_WRITE_GUARD`` when omitted.

        Yields:
            The guard, whose :attr:`stray_writes` the body may inspect.

        Raises:
            StrayWriteError: When the body completed but made a stray write that was swallowed on
                the way, or made any at all in ``collect`` mode.
        """
        guard = cls(label=label, directories=directories, mode=cls.mode_from_environment() if mode is None else mode)
        cls._install_hook()
        # The first gettempdir() of a process probes the candidate directories by creating and
        # deleting a file in each; a library imported mid-calculation (portalocker evaluates it as a
        # default argument) would otherwise make that probe the calculation's write. It is the
        # interpreter's, it leaves nothing behind, and it happens here, before the guard is on.
        tempfile.gettempdir()
        token = cls._active.set(guard)
        directories.activate()
        try:
            directories.add_cache_directories(os.environ.get(variable) for variable in cls.CACHE_DIRECTORY_VARIABLES)
            try:
                yield guard
            except BaseException as error:
                if guard.stray_writes and not isinstance(error, StrayWriteError):
                    error.add_note(str(StrayWriteError(guard.stray_writes)))
                raise
            if guard.stray_writes:
                raise StrayWriteError(guard.stray_writes)
        finally:
            directories.deactivate()
            cls._active.reset(token)


class _LibraryRedirects:

    """Points the interpreter's and the libraries' own writes into the calculation's directories."""

    MATPLOTLIB_VARIABLE: ClassVar[str] = "MPLCONFIGDIR"

    @classmethod
    def save(cls) -> Dict[str, Any]:
        """Return the settings :meth:`derive` changes, for :meth:`restore`."""
        return {
            "dont_write_bytecode": sys.dont_write_bytecode,
            "tempdir": tempfile.tempdir,
            cls.MATPLOTLIB_VARIABLE: os.environ.get(cls.MATPLOTLIB_VARIABLE),
        }

    @classmethod
    def derive(cls, directories: CalculationDirectories, saved: Dict[str, Any]) -> None:
        """Point the temporary directory and matplotlib's configuration where the registry says.

        The temporary directory is the result directory once it is known, the one saved before
        the calculation until then. ``MPLCONFIGDIR`` is ``matplotlib`` below the first writable
        cache directory, or below the result directory without one, and is only touched when
        nobody set it before the calculation and matplotlib is not imported yet (it is read at the
        import; post-processing imports matplotlib mid-run).
        """
        tempfile.tempdir = directories.result_directory() or saved["tempdir"]
        if saved[cls.MATPLOTLIB_VARIABLE] is not None or "matplotlib" in sys.modules:
            return
        home = directories.writable_cache_directory() or directories.result_directory()
        if home is None:
            os.environ.pop(cls.MATPLOTLIB_VARIABLE, None)
        else:
            os.environ[cls.MATPLOTLIB_VARIABLE] = os.path.join(home, "matplotlib")

    @classmethod
    def restore(cls, saved: Dict[str, Any]) -> None:
        """Undo every redirect."""
        sys.dont_write_bytecode = saved["dont_write_bytecode"]
        tempfile.tempdir = saved["tempdir"]
        previous = saved[cls.MATPLOTLIB_VARIABLE]
        if previous is None:
            os.environ.pop(cls.MATPLOTLIB_VARIABLE, None)
        else:
            os.environ[cls.MATPLOTLIB_VARIABLE] = previous
