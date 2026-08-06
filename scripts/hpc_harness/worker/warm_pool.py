"""Warm-child pool (spec §4.2/§4.3): dispatch, monitoring, recycling — rework of pool.py.

Keeps the proven pieces of the old ``LocalPool`` (peak-RSS sampling incl. descendants,
timeout kill-tree) but children are long-lived warm interpreters created by the
:class:`~hpc_harness.worker.spawner.Spawner` instead of per-job subprocesses.
"""

import logging
import select
import time
from typing import Any, Dict, List, Optional

import psutil

from hpc_harness.worker import ipc
from hpc_harness.worker.spawner import Spawner, SpawnerError

LOGGER = logging.getLogger(__name__)

GB = 1024 ** 3
MB = 1024 ** 2


def compute_max_slots(
    per_job_mem_gb: float,
    min_headroom_gb: float,
    cores: int,
    cores_per_job: int,
    reserved_cores: int,
    configured: Optional[int] = None,
    total_mem_gb: Optional[float] = None,
) -> int:
    """Slot cap = min(memory-based, core-based) (spec §4.2), optionally capped by config."""
    total_b = (total_mem_gb * GB) if total_mem_gb else psutil.virtual_memory().total
    mem_slots = int((total_b - min_headroom_gb * GB) // (per_job_mem_gb * GB))
    core_slots = (cores - reserved_cores) // max(cores_per_job, 1)
    slots = max(1, min(mem_slots, core_slots))
    if configured is not None:
        slots = max(1, min(slots, configured))
    return slots


class _Child:
    """One warm child interpreter."""

    __slots__ = ("pid", "sock", "jobs_run", "job", "start", "peak_b", "proc")

    def __init__(self, pid: int, sock: Any) -> None:
        self.pid = pid
        self.sock = sock
        self.jobs_run = 0
        self.job: Optional[Dict[str, Any]] = None  # the leased job dict while busy
        self.start = 0.0
        self.peak_b = 0
        try:
            self.proc: Optional[psutil.Process] = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self.proc = None

    @property
    def busy(self) -> bool:
        return self.job is not None


class WarmPool:
    """Manages the warm children of one worker."""

    def __init__(
        self,
        spawner: Spawner,
        target_slots: int,
        timeout_s: float,
        max_jobs_per_child: int,
        rss_ceiling_gb: Optional[float] = None,
    ) -> None:
        """Create the pool; call :meth:`ensure` to actually fork the children."""
        self.spawner = spawner
        self.target_slots = target_slots
        self.timeout_s = timeout_s
        self.max_jobs_per_child = max_jobs_per_child
        self.rss_ceiling_b = rss_ceiling_gb * GB if rss_ceiling_gb else None
        self.children: List[_Child] = []

    # ------------------------------------------------------------- pool sizing

    def ensure(self) -> None:
        """Fork children up to ``target_slots``; retire surplus idle ones (spec §4.6)."""
        while len(self.children) < self.target_slots:
            try:
                pid, sock = self.spawner.spawn()
            except SpawnerError:
                LOGGER.exception("Could not spawn a warm child")
                return
            self.children.append(_Child(pid, sock))
        if len(self.children) > self.target_slots:
            for child in [c for c in self.children if not c.busy]:
                if len(self.children) <= self.target_slots:
                    break
                self._retire(child)

    def set_target(self, slots: int) -> None:
        """Apply a new slot cap (budget change): grow now, shrink as children idle."""
        self.target_slots = max(1, slots)
        self.ensure()

    def _retire(self, child: _Child) -> None:
        try:
            ipc.send_msg(child.sock, {"cmd": "exit"})
        except OSError:
            pass
        try:
            child.sock.close()
        except OSError:
            pass
        self.children.remove(child)

    # --------------------------------------------------------------- dispatch

    def idle_count(self) -> int:
        """Children ready to take a job."""
        return sum(1 for c in self.children if not c.busy)

    def running(self) -> List[Dict[str, int]]:
        """(job_id, attempt) pairs for the heartbeat ``running`` list (spec §5.1)."""
        return [
            {"job_id": c.job["id"], "attempt": c.job["attempt"]}
            for c in self.children
            if c.busy
        ]

    def dispatch(self, job: Dict[str, Any]) -> bool:
        """Hand one leased job to an idle child; False when no child is free."""
        for child in self.children:
            if not child.busy:
                try:
                    ipc.send_msg(
                        child.sock,
                        {
                            "job_id": job["id"],
                            "attempt": job["attempt"],
                            "payload": job["payload"],
                            "staging_dir": job["staging_dir"],
                        },
                    )
                except OSError:
                    self._replace_dead(child)
                    continue
                child.job = job
                child.start = time.time()
                child.peak_b = 0
                child.jobs_run += 1
                return True
        return False

    def kill_job(self, job_id: int, attempt: Optional[int] = None) -> bool:
        """Kill directive (spec §5.1): stop the child running this job, fork a fresh one."""
        for child in self.children:
            if child.busy and child.job["id"] == job_id and (
                attempt is None or child.job["attempt"] == attempt
            ):
                LOGGER.info("Killing job %d (revoked lease / cancel)", job_id)
                self._kill_tree(child)
                self.children.remove(child)
                self.ensure()
                return True
        return False

    # -------------------------------------------------------------- monitoring

    def sample(self) -> None:
        """Track peak RSS of busy children incl. their descendants (spec §9)."""
        for child in self.children:
            if not child.busy or child.proc is None:
                continue
            try:
                rss = child.proc.memory_info().rss
                for descendant in child.proc.children(recursive=True):
                    try:
                        rss += descendant.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                child.peak_b = max(child.peak_b, rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def poll(self) -> List[Dict[str, Any]]:
        """Collect finished/timed-out/crashed jobs; returns raw result dicts.

        Each result: ``{job, ok, error?, traceback?, duration_s, peak_mem_mb,
        exit_kind: finished|timeout|died}``. Recycling happens here (spec §4.2).
        """
        now = time.time()
        results: List[Dict[str, Any]] = []

        busy = [c for c in self.children if c.busy]
        readable = []
        if busy:
            try:
                readable, _, _ = select.select([c.sock for c in busy], [], [], 0)
            except (OSError, ValueError):
                readable = []
        ready_socks = set(readable)

        for child in list(busy):
            job = child.job
            if child.sock in ready_socks:
                msg = None
                try:
                    msg = ipc.recv_msg(child.sock)
                except OSError:
                    msg = None
                if msg is None:  # EOF: the child died (crash / OOM)
                    results.append(self._result(child, now, ok=False, error="child died (crash or OOM)", kind="died"))
                    self._replace_dead(child)
                    continue
                results.append(
                    self._result(
                        child, now, ok=bool(msg.get("ok")), error=msg.get("error"),
                        traceback_text=msg.get("traceback"), kind="finished",
                    )
                )
                child.job = None
                self._maybe_recycle(child)
            elif now - child.start > self.timeout_s:
                LOGGER.warning("Job %d timed out after %.0fs — killing child", job["id"], self.timeout_s)
                results.append(self._result(child, now, ok=False, error="timeout", kind="timeout"))
                self._kill_tree(child)
                self.children.remove(child)

        self.ensure()
        return results

    def _result(
        self,
        child: _Child,
        now: float,
        ok: bool,
        error: Optional[str],
        kind: str,
        traceback_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "job": child.job,
            "ok": ok,
            "error": error,
            "traceback": traceback_text,
            "exit_kind": kind,
            "started_at": child.start,
            "finished_at": now,
            "duration_s": now - child.start,
            "peak_mem_mb": child.peak_b / MB,
        }

    def _maybe_recycle(self, child: _Child) -> None:
        """Recycle after max_jobs_per_child jobs or above the RSS ceiling (spec §4.2)."""
        over_rss = False
        if self.rss_ceiling_b and child.proc is not None:
            try:
                over_rss = child.proc.memory_info().rss > self.rss_ceiling_b
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                over_rss = True
        if child.jobs_run >= self.max_jobs_per_child or over_rss:
            LOGGER.info("Recycling warm child %d after %d jobs", child.pid, child.jobs_run)
            self._retire(child)
            self.ensure()

    def _replace_dead(self, child: _Child) -> None:
        try:
            child.sock.close()
        except OSError:
            pass
        if child in self.children:
            self.children.remove(child)
        self.ensure()

    def _kill_tree(self, child: _Child) -> None:
        """Terminate (then kill) a child and all of its descendants (from old pool.py)."""
        try:
            child.sock.close()
        except OSError:
            pass
        if child.proc is None:
            return
        try:
            targets = child.proc.children(recursive=True) + [child.proc]
        except psutil.NoSuchProcess:
            return
        for target in targets:
            try:
                target.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(targets, timeout=10)
        for target in alive:
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass

    def shutdown(self, kill_running: bool = False) -> None:
        """Retire all idle children; optionally kill busy ones (forced shutdown)."""
        for child in list(self.children):
            if child.busy and kill_running:
                self._kill_tree(child)
                self.children.remove(child)
            elif not child.busy:
                self._retire(child)
