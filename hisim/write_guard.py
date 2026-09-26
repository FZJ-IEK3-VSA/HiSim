"""Keeps every file a calculation writes inside its result directory or the cache directories.

A calculation -- one run of ``hisim_main``, of ``run_energy_system`` or of the RenoVisor
``Calculation`` -- may write in exactly two kinds of place (owner decision of 2026-09-26, bead
hisim-epc.23):

* **its result directory**, the fresh directory :class:`~hisim.result_path_provider.ResultPathProviderSingleton`
  hands out for it (or the directory its caller named: a RenoVisor job directory, a test's
  directory). Nothing the next calculation needs may live there, so the caller can delete it whole
  once it has copied what it wants;
* **the cache directories**, which are shared between calculations on purpose: the parameters'
  ``cache_locations()`` (``cache_dir_path``, the ordered ``cache_directories`` a container maps in
  through ``HISIM_CACHE_DIRECTORIES``) and the ``HISIM_CACHE_DIR`` / ``HISIM_CACHE_SHARED_DIR``
  overrides of the cache client.

Everything else -- the repository tree, ``hisim/inputs``, the working directory, the home
directory, the system temporary directory, an installed package -- is a *stray write*, and this
module refuses it at runtime.

**How.** :func:`sys.addaudithook` reports every write-shaped operation the interpreter performs:
``open`` with a writing flag (``open()``, ``os.open``, ``Path.write_text``, pandas' and numpy's
writers, matplotlib's ``savefig`` -- they all end in one of those), ``os.mkdir``, ``os.rename`` and
``os.replace``, ``os.remove``/``os.unlink``, ``os.rmdir``, the metadata writes ``os.chmod``,
``os.utime`` and ``os.chown``, links, ``os.truncate``, and the ``shutil`` copy, move, remove and
archive functions. The hook cannot be removed again, so it is installed once, lazily, the first time
a guard is switched on -- a process that never runs a calculation (a plain unit test) never gets it
-- and it consults a context-local *active guard* that :meth:`WriteGuard.calculation` sets for the
length of one calculation. Without an active guard the hook returns at once. Reading is never
restricted.

**What a stray write does.** In the default ``enforce`` mode the offending operation raises
:class:`StrayWriteError` at the point of the write, so the traceback ends in the line that tried it.
Because a ``try``/``except Exception`` somewhere between the writer and the run could swallow that
error, every stray write is also recorded, and a calculation that recorded any fails when it ends
even if its body completed. With ``HISIM_WRITE_GUARD=collect`` nothing is raised at the write: the
calculation runs to its end and then fails with the full list, which is what a survey wants. There
is deliberately no ``off``.

**What the guard does for the libraries.** Some writes are nobody's output: they are made by the
interpreter or a library on its own account. The guard does not allow them; it removes them:

* bytecode: ``sys.dont_write_bytecode`` is set for the length of a calculation, so a module first
  imported mid-run is compiled in memory instead of writing a ``.pyc`` into the source tree or the
  installed package;
* matplotlib's configuration and font cache: ``MPLCONFIGDIR`` is pointed at ``matplotlib`` below the
  first cache directory as soon as the calculation knows one, when the variable is unset and
  matplotlib has not been imported yet (post-processing imports it mid-run), so a cold machine
  builds its font cache in the shared cache instead of ``~/.cache``;
* the temporary directory: :data:`tempfile.tempdir` is pointed at the result directory once the
  calculation's result directory is known, so every ``tempfile`` call of the calculation lands
  where the calculation's files belong and is deleted with them. Before that point -- while a
  Python setup is still building its components -- the system temporary directory is a stray
  write like any other. The interpreter's one-time probe of the system temporary directory (the
  first ``tempfile.gettempdir()`` of a process creates and deletes a file there) is made before the
  guard switches on, because it is not the calculation's and leaves nothing behind.

Subprocesses are outside the hook's reach: a child process writes without an audit event. HiSim
starts two during a calculation, and both are pointed at the calculation's directories: the local
LoadProfileGenerator, which computes in its own directory below the cache directory, next to the
binaries it is copied from (see :mod:`hisim.components.pylpg_workspace`), and Graphviz's ``dot``,
which writes the system chart straight into the result directory
(:meth:`hisim.postprocessing.system_chart.SystemChart.render_png`).

**Threads.** The active guard is a :class:`contextvars.ContextVar`, so a thread started inside a
calculation starts without it (Python does not copy the context into a new thread unless asked to).
HiSim starts none during a calculation.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import sys
import sysconfig
import tempfile
import threading
from contextvars import ContextVar
from types import FrameType
from typing import Any, ClassVar, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

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
        stack: The whole stack as ``file:line in function`` lines, outermost first.
        allowed: The directories the guard allowed at the time.
    """

    event: str
    path: str
    writer: str
    caller: Optional[str]
    stack: Tuple[str, ...]
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


class _Targets:

    """Which argument of each audit event names the path that is written.

    The tuples are ``(path index, dir_fd index)`` pairs; a ``dir_fd`` index of ``None`` means the
    event carries none. ``open`` is special-cased in :meth:`WriteGuard._targets` because whether it
    writes depends on its flags.
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
        "sqlite3.connect": ((0, None),),
    }

    EVENTS: ClassVar[frozenset] = frozenset(BY_EVENT) | {"open"}


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
    def attribute(cls, frame: Optional[FrameType]) -> Tuple[str, Optional[str], Tuple[str, ...]]:
        """Return the writer, the HiSim caller and the whole stack, starting from *frame*.

        Args:
            frame: The innermost frame of the write, the hook's caller.

        Returns:
            ``(writer, caller, stack)``: see :class:`StrayWrite`.
        """
        frames: List[FrameType] = []
        while frame is not None:
            frames.append(frame)
            frame = frame.f_back
        writer: Optional[FrameType] = None
        caller: Optional[FrameType] = None
        for candidate in frames:
            filename = candidate.f_code.co_filename
            if cls._is_standard_library(filename):
                continue
            if writer is None:
                writer = candidate
            if not cls._is_installed_package(filename):
                caller = candidate
                break
        stack = tuple(cls._describe(candidate) for candidate in reversed(frames))
        if writer is None:
            writer = frames[0] if frames else None
        writer_text = cls._describe(writer) if writer is not None else "an unknown frame"
        caller_text = cls._describe(caller) if caller is not None and caller is not writer else None
        return writer_text, caller_text, stack


class WriteGuard:

    """The allowed directories of one calculation, and the stray writes it made.

    Use :meth:`calculation` to run one; the lifecycle code of a run tells the active guard about its
    result directory and its cache directories through :meth:`admit_result_directory` and
    :meth:`admit_cache_directories`, which do nothing when no calculation is running.

    Args:
        label: What the calculation is, for the messages (a setup module, a request file).
        mode: What a stray write does.
    """

    #: The environment variable that selects :class:`GuardMode`; ``enforce`` when unset.
    MODE_VARIABLE: ClassVar[str] = "HISIM_WRITE_GUARD"

    #: The cache-client variables that name a directory the cache writes to (``hisim.caching.settings``).
    CACHE_DIRECTORY_VARIABLES: ClassVar[Tuple[str, ...]] = ("HISIM_CACHE_DIR", "HISIM_CACHE_SHARED_DIR")

    #: Files that are not files: writing to them leaves nothing on disk.
    DEVICE_FILES: ClassVar[Tuple[str, ...]] = (os.devnull,)

    _active: ClassVar[ContextVar[Optional["WriteGuard"]]] = ContextVar("hisim_write_guard", default=None)
    _hook_installed: ClassVar[bool] = False
    _install_lock: ClassVar[threading.Lock] = threading.Lock()
    _inside_hook: ClassVar[threading.local] = threading.local()

    def __init__(self, label: str, mode: GuardMode = GuardMode.ENFORCE) -> None:
        """Start with no allowed directory at all."""
        self.label = label
        self.mode = mode
        self._roots: List[str] = []
        self.stray_writes: List[StrayWrite] = []
        self.result_directories: List[str] = []
        self.cache_directories: List[str] = []
        #: The system temporary directory, where :data:`tempfile.tempdir` points until the
        #: calculation's result directory is known.
        self.system_tempdir: Optional[str] = None

    # ----------------------------------------------------------------------------------------------
    # The allowed directories
    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def _normalize(path: str) -> str:
        """Return the canonical absolute spelling of a path, symlinks resolved."""
        return os.path.normcase(os.path.realpath(os.path.abspath(path)))

    @property
    def allowed_directories(self) -> Tuple[str, ...]:
        """Every directory the calculation may write below, canonical spelling."""
        return tuple(self._roots)

    def _admit(self, directory: str) -> None:
        """Add one directory to the allowed ones."""
        root = self._normalize(directory)
        if root not in self._roots:
            self._roots.append(root)

    def permits(self, path: str, event: str) -> bool:
        """Whether writing *path* with *event* stays inside the allowed directories.

        Creating a directory is also permitted when it is an ancestor of an allowed directory (the
        way to the result directory has to be built) or already exists (``os.makedirs`` and
        ``Path.mkdir(exist_ok=True)`` attempt the ``mkdir`` before they notice).

        Args:
            path: The absolute target of the operation.
            event: The audit event.

        Returns:
            True when the operation is allowed.
        """
        if path in self.DEVICE_FILES or (os.name == "posix" and path.startswith("/dev/")):
            return True
        target = self._normalize(path)
        for root in self._roots:
            if target == root or target.startswith(root + os.sep):
                return True
        if event == "os.mkdir":
            if os.path.isdir(target):
                return True
            for root in self._roots:
                if root.startswith(target + os.sep):
                    return True
        if event == "sqlite3.connect" and os.path.exists(target):
            # Opening an existing database is how a reader starts; only creating one is a write.
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
        if refused is not None and guard.mode is GuardMode.ENFORCE:
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
        if text == ":memory:" or text.startswith("file:") or text == "":
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
            for earlier in self.stray_writes:
                if earlier.event == event and earlier.path == path:
                    # A log file appended to a thousand times is one finding, not a thousand.
                    return earlier
            writer, caller, stack = _Frames.attribute(sys._getframe(2))  # pylint: disable=protected-access
            stray = StrayWrite(
                event=event,
                path=path,
                writer=writer,
                caller=caller,
                stack=stack,
                allowed=self.allowed_directories,
            )
            self.stray_writes.append(stray)
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
    def admit_result_directory(cls, directory: Optional[str]) -> None:
        """Tell the running calculation where its result directory is; no-op outside one.

        The first result directory also becomes the calculation's temporary directory
        (:data:`tempfile.tempdir`), so a library's temporary file lands with the calculation's
        other files instead of in the system temporary directory.
        """
        guard = cls._active.get()
        if guard is None or not directory:
            return
        guard._admit(directory)  # pylint: disable=protected-access
        if guard._normalize(directory) not in guard.result_directories:  # pylint: disable=protected-access
            guard.result_directories.append(guard._normalize(directory))  # pylint: disable=protected-access
        if len(guard.result_directories) == 1:
            # The temporary files of the calculation go where its other files go, and are deleted
            # with them; nothing is created for them, the result directory exists already.
            tempfile.tempdir = guard.result_directories[0]

    @classmethod
    def withdraw_result_directory(cls, directory: str) -> None:
        """Take back a result directory admitted a moment ago that turned out to be another run's.

        :meth:`~hisim.result_path_provider.ResultPathProviderSingleton.claim_fresh_directory` admits
        a candidate before it creates it exclusively; when the creation finds the directory taken,
        the candidate belongs to a different calculation and must not stay writable for this one.
        """
        guard = cls._active.get()
        if guard is None:
            return
        root = guard._normalize(directory)  # pylint: disable=protected-access
        if root in guard._roots:  # pylint: disable=protected-access
            guard._roots.remove(root)  # pylint: disable=protected-access
        if root in guard.result_directories:
            guard.result_directories.remove(root)
            if tempfile.tempdir == root:
                tempfile.tempdir = guard.result_directories[0] if guard.result_directories else guard.system_tempdir

    @classmethod
    def admit_cache_directories(cls, directories: Iterable[Optional[str]]) -> None:
        """Tell the running calculation which cache directories it uses; no-op outside one."""
        guard = cls._active.get()
        if guard is None:
            return
        for directory in directories:
            if not directory:
                continue
            guard._admit(directory)  # pylint: disable=protected-access
            if guard._normalize(directory) not in guard.cache_directories:  # pylint: disable=protected-access
                guard.cache_directories.append(guard._normalize(directory))  # pylint: disable=protected-access
        _LibraryRedirects.point_matplotlib(guard)

    @classmethod
    @contextlib.contextmanager
    def calculation(
        cls,
        label: str,
        result_directories: Sequence[Optional[str]] = (),
        cache_directories: Sequence[Optional[str]] = (),
        mode: Optional[GuardMode] = None,
    ) -> Iterator["WriteGuard"]:
        """Run the body as one guarded calculation.

        Args:
            label: What the calculation is, for the messages.
            result_directories: Directories already known to belong to the calculation (a job
                directory its caller named). The simulator adds the result directory it resolves.
            cache_directories: Cache directories already known; the environment's cache overrides
                and the simulation parameters' cache locations are added as they become known.
            mode: What a stray write does; ``HISIM_WRITE_GUARD`` when omitted.

        Yields:
            The guard, whose :attr:`stray_writes` the body may inspect.

        Raises:
            StrayWriteError: When the body completed but made a stray write that was swallowed on
                the way, or made any at all in ``collect`` mode.
        """
        guard = cls(label=label, mode=mode or cls.mode_from_environment())
        cls._install_hook()
        # The first gettempdir() of a process probes the candidate directories by creating and
        # deleting a file in each; a library imported mid-calculation (portalocker evaluates it as a
        # default argument) would otherwise make that probe the calculation's write. It is the
        # interpreter's, it leaves nothing behind, and it happens here, before the guard is on.
        guard.system_tempdir = tempfile.gettempdir()
        saved = _LibraryRedirects.save()
        token = cls._active.set(guard)
        try:
            cls.admit_cache_directories(
                [os.environ.get(variable) for variable in cls.CACHE_DIRECTORY_VARIABLES]
            )
            cls.admit_cache_directories(cache_directories)
            for directory in result_directories:
                cls.admit_result_directory(directory)
            _LibraryRedirects.apply(guard)
            try:
                yield guard
            except BaseException as error:
                if guard.stray_writes and not isinstance(error, StrayWriteError):
                    error.add_note(str(StrayWriteError(guard.stray_writes)))
                raise
            if guard.stray_writes:
                raise StrayWriteError(guard.stray_writes)
        finally:
            _LibraryRedirects.restore(saved)
            cls._active.reset(token)


class _LibraryRedirects:

    """Points the interpreter's and the libraries' own writes into the calculation's directories."""

    MATPLOTLIB_VARIABLE: ClassVar[str] = "MPLCONFIGDIR"

    @classmethod
    def save(cls) -> Dict[str, Any]:
        """Return the settings :meth:`apply` changes, for :meth:`restore`."""
        return {
            "dont_write_bytecode": sys.dont_write_bytecode,
            "tempdir": tempfile.tempdir,
            cls.MATPLOTLIB_VARIABLE: os.environ.get(cls.MATPLOTLIB_VARIABLE),
        }

    @classmethod
    def apply(cls, guard: WriteGuard) -> None:
        """Switch off bytecode writing and point matplotlib at the cache, as the module docstring says."""
        sys.dont_write_bytecode = True
        cls.point_matplotlib(guard)

    @classmethod
    def point_matplotlib(cls, guard: WriteGuard) -> None:
        """Point ``MPLCONFIGDIR`` below the first cache directory, once one is known.

        Post-processing imports matplotlib in the middle of a calculation, and matplotlib creates
        its configuration directory and builds its font cache in ``~/.config`` and ``~/.cache`` on
        first import. The variable is read at that import, so it is set as soon as the calculation
        knows a cache directory and only when nobody set it and matplotlib is not imported yet.
        """
        if (
            os.environ.get(cls.MATPLOTLIB_VARIABLE) is None
            and "matplotlib" not in sys.modules
            and guard.cache_directories
        ):
            os.environ[cls.MATPLOTLIB_VARIABLE] = os.path.join(guard.cache_directories[0], "matplotlib")

    @classmethod
    def restore(cls, saved: Dict[str, Any]) -> None:
        """Undo :meth:`apply` and the temporary-directory redirect."""
        sys.dont_write_bytecode = saved["dont_write_bytecode"]
        tempfile.tempdir = saved["tempdir"]
        previous = saved[cls.MATPLOTLIB_VARIABLE]
        if previous is None:
            os.environ.pop(cls.MATPLOTLIB_VARIABLE, None)
        else:
            os.environ[cls.MATPLOTLIB_VARIABLE] = previous
