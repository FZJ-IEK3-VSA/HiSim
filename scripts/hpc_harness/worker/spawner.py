"""Fork-server (spec §4.3): safe warm-child creation for a threaded worker parent.

``fork()`` from a multi-threaded process is unsafe, so the worker forks the **spawner**
first — before any thread or HTTP connection exists. The spawner (single-threaded)
runs ``runner.warmup()`` once (the heavy imports), calls ``gc.freeze()`` so refcount
traffic doesn't dirty the shared pages, and then forks every warm child on request,
passing the child's socket back to the parent over SCM_RIGHTS.
"""

import gc
import logging
import os
import signal
import socket
from typing import Tuple

from hpc_harness.runners import get_runner
from hpc_harness.worker import ipc
from hpc_harness.worker.child import child_main, _set_oom_score

LOGGER = logging.getLogger(__name__)


class SpawnerError(RuntimeError):
    """The spawner could not be started or has died."""


class Spawner:
    """Parent-side handle to the fork-server process."""

    def __init__(self, runner_name: str, warmup_timeout_s: float = 600.0) -> None:
        """Fork the spawner and wait for its warmup to finish.

        Must be called before the worker creates any threads or network connections.
        """
        if os.name != "posix":
            raise SpawnerError("the warm-child spawner requires POSIX (fork)")
        parent_sock, spawner_sock = socket.socketpair()
        pid = os.fork()
        if pid == 0:  # spawner process
            parent_sock.close()
            exit_code = 0
            try:
                _spawner_main(spawner_sock, runner_name)
            except BaseException:  # pylint: disable=broad-except
                exit_code = 1
            finally:
                os._exit(exit_code)  # pylint: disable=protected-access
        spawner_sock.close()
        self.pid = pid
        self.sock = parent_sock
        self.sock.settimeout(warmup_timeout_s)
        ready = ipc.recv_msg(self.sock)
        self.sock.settimeout(None)
        if not ready or not ready.get("ready"):
            raise SpawnerError(f"spawner warmup failed: {ready and ready.get('error')}")
        LOGGER.info("Spawner %d ready (runner warmed up)", pid)

    def spawn(self) -> Tuple[int, socket.socket]:
        """Ask the spawner to fork one warm child; returns (pid, message socket)."""
        try:
            ipc.send_msg(self.sock, {"cmd": "spawn"})
            _tag, fds = ipc.recv_fds(self.sock, 1)
            meta = ipc.recv_msg(self.sock)
        except (OSError, socket.timeout) as exc:
            raise SpawnerError(f"spawner IPC failed: {exc}") from exc
        if not fds or meta is None:
            raise SpawnerError("spawner died while forking a child")
        return meta["pid"], socket.socket(fileno=fds[0])

    def shutdown(self) -> None:
        """Stop the spawner (children keep running; the pool kills them separately)."""
        try:
            ipc.send_msg(self.sock, {"cmd": "exit"})
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            pass


def _spawner_main(sock: socket.socket, runner_name: str) -> None:
    """The spawner process: warm up once, then fork children on request."""
    # Children are our children — auto-reap them so no zombies accumulate.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    _set_oom_score(-100)  # keep infrastructure alive when a job OOMs (best effort)
    try:
        runner = get_runner(runner_name)
        runner.warmup()
        gc.collect()
        gc.freeze()  # keep the warmed pages copy-on-write friendly
    except Exception as exc:  # pylint: disable=broad-except
        try:
            ipc.send_msg(sock, {"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        except OSError:
            pass
        return
    ipc.send_msg(sock, {"ready": True})
    while True:
        msg = ipc.recv_msg(sock)
        if msg is None or msg.get("cmd") == "exit":
            return
        if msg.get("cmd") != "spawn":
            continue
        parent_end, child_end = socket.socketpair()
        pid = os.fork()
        if pid == 0:  # warm child
            try:
                sock.close()
                parent_end.close()
                child_main(child_end, runner)
            finally:
                os._exit(0)  # pylint: disable=protected-access
        child_end.close()
        ipc.send_fds(sock, [parent_end.fileno()])
        ipc.send_msg(sock, {"pid": pid})
        parent_end.close()
