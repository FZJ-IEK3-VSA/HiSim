"""Warm-child loop (spec §4.3): run jobs from a pipe, imports inherited from the spawner.

Runs only on POSIX (children are created by ``fork`` in the spawner). Per job, stdout
and stderr are redirected at fd level into ``<staging_dir>/harness_run.log`` so native
output is captured too.
"""

import os
import socket
import traceback
from pathlib import Path
from typing import Any

from hpc_harness.worker import ipc

CONSOLE_LOG_NAME = "harness_run.log"


def _set_oom_score(value: int) -> None:
    """Bias the kernel OOM killer (children high, infrastructure low) — best effort."""
    try:
        Path("/proc/self/oom_score_adj").write_text(str(value), encoding="utf-8")
    except OSError:
        pass


def child_main(sock: socket.socket, runner: Any) -> None:
    """Loop: receive a job, run it in this warm interpreter, send the result."""
    _set_oom_score(800)  # prefer killing a job child over the worker/spawner
    try:
        runner.on_fork()
    except Exception:  # pylint: disable=broad-except
        pass
    while True:
        msg = ipc.recv_msg(sock)
        if msg is None or msg.get("cmd") == "exit":
            return
        job_id = msg["job_id"]
        staging_dir = msg["staging_dir"]
        result = {"job_id": job_id, "attempt": msg["attempt"], "ok": False, "error": None}
        saved_out, saved_err = os.dup(1), os.dup(2)
        log_fd = None
        try:
            Path(staging_dir).mkdir(parents=True, exist_ok=True)
            log_fd = os.open(
                str(Path(staging_dir) / CONSOLE_LOG_NAME),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
            os.dup2(log_fd, 1)
            os.dup2(log_fd, 2)
            runner.run(msg["payload"], staging_dir)
            result["ok"] = True
        except Exception as exc:  # pylint: disable=broad-except
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["traceback"] = traceback.format_exc()[-8000:]
            traceback.print_exc()  # lands in harness_run.log via the redirect
        finally:
            try:
                os.dup2(saved_out, 1)
                os.dup2(saved_err, 2)
                os.close(saved_out)
                os.close(saved_err)
                if log_fd is not None:
                    os.close(log_fd)
            except OSError:
                pass
        try:
            ipc.send_msg(sock, result)
        except OSError:
            return  # parent gone; nothing sensible left to do
