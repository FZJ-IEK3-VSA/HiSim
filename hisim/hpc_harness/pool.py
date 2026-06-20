"""A node-local pool of HiSim simulation subprocesses with memory-gated admission.

One ``LocalPool`` runs on each node (inside the MPI rank for that node). It is the
sole decision-maker for its node's memory, so admission control is a simple local
check with no cross-process race: a new simulation starts only when enough memory is
free. The pool also enforces per-task timeouts and tracks peak memory and runtime.
"""

import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import psutil

from hisim.hpc_harness.db import DONE, FAILED

GB = 1024 ** 3
MB = 1024 ** 2


def compute_max_slots(per_sim_mem_gb: float, min_headroom_gb: float, configured: Optional[int]) -> int:
    """Per-node cap on concurrent simulations.

    Uses the explicit value if given, else sizes from this node's total memory while
    keeping ``min_headroom_gb`` free: ``floor((total - headroom) / per_sim_mem)``.
    """
    if configured is not None:
        return max(1, configured)
    total = psutil.virtual_memory().total
    usable = total - min_headroom_gb * GB
    return max(1, int(usable // (per_sim_mem_gb * GB)))


def _default_command(task: Dict[str, Any], result_dir: str, sim_params: str) -> List[str]:
    """Build the command that runs one HiSim simulation in a fresh subprocess."""
    return [
        sys.executable, "-m", "hisim.hpc_harness.run_one",
        "--scenario", task["scenario_path"],
        "--sim-params", sim_params,
        "--result-dir", result_dir,
    ]


class LocalPool:
    """Manages the simulation subprocesses running on one node."""

    def __init__(
        self,
        host: str,
        sim_params: str,
        result_root: str,
        per_sim_mem_gb: float,
        min_headroom_gb: float,
        timeout_s: float,
        max_slots: int,
        command_builder: Optional[Callable[[Dict[str, Any], str, str], List[str]]] = None,
    ) -> None:
        self.host = host
        self.sim_params = sim_params
        self.result_root = Path(result_root)
        self.per_sim_mem_b = per_sim_mem_gb * GB
        self.min_headroom_b = min_headroom_gb * GB
        self.timeout_s = timeout_s
        self.max_slots = max_slots
        self._build_command = command_builder or _default_command
        self.pending: "deque[Dict[str, Any]]" = deque()
        self.running: Dict[int, Dict[str, Any]] = {}

    # --- queue accounting ------------------------------------------------------
    def add_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        """Queue granted tasks for launching."""
        self.pending.extend(tasks)

    def free_slots(self) -> int:
        """How many more tasks this node could accept (running + queued vs. cap)."""
        return max(0, self.max_slots - len(self.running) - len(self.pending))

    def is_idle(self) -> bool:
        """True when nothing is running or queued."""
        return not self.running and not self.pending

    # --- admission -------------------------------------------------------------
    def _can_launch(self) -> bool:
        if len(self.running) >= self.max_slots:
            return False
        available = psutil.virtual_memory().available
        return bool(available >= (self.per_sim_mem_b + self.min_headroom_b))

    def _launch(self, task: Dict[str, Any]) -> None:
        result_dir = self.result_root / f"{task['id']:06d}_{Path(task['scenario_path']).stem}"
        # Wipe any partial output from a previous attempt for a clean, idempotent retry.
        shutil.rmtree(result_dir, ignore_errors=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        # HiSim sims are single-core; pin BLAS/OpenMP so N sims don't oversubscribe the node.
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            env[var] = "1"

        log_path = result_dir / "harness_run.log"
        log_file = open(log_path, "wb")  # noqa: SIM115 (closed in _finish)
        cmd = self._build_command(task, str(result_dir), self.sim_params)
        popen = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        self.running[task["id"]] = {
            "task": task,
            "popen": popen,
            "proc": psutil.Process(popen.pid),
            "start": time.time(),
            "peak_b": 0,
            "result_dir": str(result_dir),
            "log_file": log_file,
            "log_path": log_path,
        }

    # --- monitoring ------------------------------------------------------------
    def _sample_memory(self) -> None:
        """Sample RSS of every running subprocess (plus children) and track the peak."""
        for info in self.running.values():
            try:
                proc = info["proc"]
                rss = proc.memory_info().rss
                for child in proc.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if rss > info["peak_b"]:
                    info["peak_b"] = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    @staticmethod
    def _kill_tree(info: Dict[str, Any]) -> None:
        """Terminate (then kill) a subprocess and all of its children."""
        try:
            proc = info["proc"]
            targets = proc.children(recursive=True) + [proc]
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

    def _tail(self, log_path: Path, max_chars: int = 2000) -> str:
        """Return the tail of a run log for failure diagnostics."""
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-max_chars:]

    # --- main step -------------------------------------------------------------
    def tick(self) -> List[Dict[str, Any]]:
        """Advance the pool one step: sample memory, reap/timeout, launch one if possible.

        Returns reports for any tasks that finished during this tick. At most one new
        subprocess is launched per tick so memory ramps gradually and the gate always
        sees real free memory.
        """
        now = time.time()
        self._sample_memory()

        reports: List[Dict[str, Any]] = []
        for task_id in list(self.running):
            info = self.running[task_id]
            return_code = info["popen"].poll()
            timed_out = False

            if return_code is None and (now - info["start"]) > self.timeout_s:
                self._kill_tree(info)
                info["popen"].wait()
                return_code = info["popen"].poll()
                timed_out = True

            if return_code is None:
                continue  # still running

            info["log_file"].close()
            duration = now - info["start"]
            success = (return_code == 0 and not timed_out)
            error = None
            if not success:
                error = "timeout" if timed_out else (self._tail(info["log_path"]) or f"exit code {return_code}")
            reports.append({
                "id": task_id,
                "status": DONE if success else FAILED,
                "exit_code": return_code,
                "duration_s": duration,
                "peak_mem_mb": info["peak_b"] / MB,
                "result_dir": info["result_dir"],
                "host": self.host,
                "started_at": info["start"],
                "finished_at": now,
                "error": error,
            })
            del self.running[task_id]

        if self.pending and self._can_launch():
            self._launch(self.pending.popleft())

        return reports

    def kill_all(self) -> None:
        """Best-effort teardown of every running subprocess (used on abort)."""
        for info in self.running.values():
            self._kill_tree(info)
            try:
                info["log_file"].close()
            except OSError:
                pass
        self.running.clear()
        self.pending.clear()
"""A node-local pool of HiSim simulation subprocesses with memory-gated admission.

One ``LocalPool`` runs on each node (inside the MPI rank for that node). It is the
sole decision-maker for its node's memory, so admission control is a simple local
check with no cross-process race: a new simulation starts only when enough memory is
free. The pool also enforces per-task timeouts and tracks peak memory and runtime.
"""

import os
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import psutil

from hisim.hpc_harness.db import DONE, FAILED

GB = 1024 ** 3
MB = 1024 ** 2


def compute_max_slots(per_sim_mem_gb: float, min_headroom_gb: float, configured: Optional[int]) -> int:
    """Per-node cap on concurrent simulations.

    Uses the explicit value if given, else sizes from this node's total memory while
    keeping ``min_headroom_gb`` free: ``floor((total - headroom) / per_sim_mem)``.
    """
    if configured is not None:
        return max(1, configured)
    total = psutil.virtual_memory().total
    usable = total - min_headroom_gb * GB
    return max(1, int(usable // (per_sim_mem_gb * GB)))


def _default_command(task: Dict[str, Any], result_dir: str, sim_params: str) -> List[str]:
    """Build the command that runs one HiSim simulation in a fresh subprocess."""
    return [
        sys.executable, "-m", "hisim.hpc_harness.run_one",
        "--scenario", task["scenario_path"],
        "--sim-params", sim_params,
        "--result-dir", result_dir,
    ]


class LocalPool:
    """Manages the simulation subprocesses running on one node."""

    def __init__(
        self,
        host: str,
        sim_params: str,
        result_root: str,
        per_sim_mem_gb: float,
        min_headroom_gb: float,
        timeout_s: float,
        max_slots: int,
        command_builder: Optional[Callable[[Dict[str, Any], str, str], List[str]]] = None,
    ) -> None:
        self.host = host
        self.sim_params = sim_params
        self.result_root = Path(result_root)
        self.per_sim_mem_b = per_sim_mem_gb * GB
        self.min_headroom_b = min_headroom_gb * GB
        self.timeout_s = timeout_s
        self.max_slots = max_slots
        self._build_command = command_builder or _default_command
        self.pending: "deque[Dict[str, Any]]" = deque()
        self.running: Dict[int, Dict[str, Any]] = {}

    # --- queue accounting ------------------------------------------------------
    def add_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        """Queue granted tasks for launching."""
        self.pending.extend(tasks)

    def free_slots(self) -> int:
        """How many more tasks this node could accept (running + queued vs. cap)."""
        return max(0, self.max_slots - len(self.running) - len(self.pending))

    def is_idle(self) -> bool:
        """True when nothing is running or queued."""
        return not self.running and not self.pending

    # --- admission -------------------------------------------------------------
    def _can_launch(self) -> bool:
        if len(self.running) >= self.max_slots:
            return False
        available = psutil.virtual_memory().available
        return available >= (self.per_sim_mem_b + self.min_headroom_b)

    def _launch(self, task: Dict[str, Any]) -> None:
        result_dir = self.result_root / f"{task['id']:06d}_{Path(task['scenario_path']).stem}"
        # Wipe any partial output from a previous attempt for a clean, idempotent retry.
        shutil.rmtree(result_dir, ignore_errors=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        # HiSim sims are single-core; pin BLAS/OpenMP so N sims don't oversubscribe the node.
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            env[var] = "1"

        log_path = result_dir / "harness_run.log"
        log_file = open(log_path, "wb")  # noqa: SIM115 (closed in _finish)
        cmd = self._build_command(task, str(result_dir), self.sim_params)
        popen = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        self.running[task["id"]] = {
            "task": task,
            "popen": popen,
            "proc": psutil.Process(popen.pid),
            "start": time.time(),
            "peak_b": 0,
            "result_dir": str(result_dir),
            "log_file": log_file,
            "log_path": log_path,
        }

    # --- monitoring ------------------------------------------------------------
    def _sample_memory(self) -> None:
        """Sample RSS of every running subprocess (plus children) and track the peak."""
        for info in self.running.values():
            try:
                proc = info["proc"]
                rss = proc.memory_info().rss
                for child in proc.children(recursive=True):
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                if rss > info["peak_b"]:
                    info["peak_b"] = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    @staticmethod
    def _kill_tree(info: Dict[str, Any]) -> None:
        """Terminate (then kill) a subprocess and all of its children."""
        try:
            proc = info["proc"]
            targets = proc.children(recursive=True) + [proc]
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

    def _tail(self, log_path: Path, max_chars: int = 2000) -> str:
        """Return the tail of a run log for failure diagnostics."""
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-max_chars:]

    # --- main step -------------------------------------------------------------
    def tick(self) -> List[Dict[str, Any]]:
        """Advance the pool one step: sample memory, reap/timeout, launch one if possible.

        Returns reports for any tasks that finished during this tick. At most one new
        subprocess is launched per tick so memory ramps gradually and the gate always
        sees real free memory.
        """
        now = time.time()
        self._sample_memory()

        reports: List[Dict[str, Any]] = []
        for task_id in list(self.running):
            info = self.running[task_id]
            return_code = info["popen"].poll()
            timed_out = False

            if return_code is None and (now - info["start"]) > self.timeout_s:
                self._kill_tree(info)
                info["popen"].wait()
                return_code = info["popen"].poll()
                timed_out = True

            if return_code is None:
                continue  # still running

            info["log_file"].close()
            duration = now - info["start"]
            success = (return_code == 0 and not timed_out)
            error = None
            if not success:
                error = "timeout" if timed_out else (self._tail(info["log_path"]) or f"exit code {return_code}")
            reports.append({
                "id": task_id,
                "status": DONE if success else FAILED,
                "exit_code": return_code,
                "duration_s": duration,
                "peak_mem_mb": info["peak_b"] / MB,
                "result_dir": info["result_dir"],
                "host": self.host,
                "started_at": info["start"],
                "finished_at": now,
                "error": error,
            })
            del self.running[task_id]

        if self.pending and self._can_launch():
            self._launch(self.pending.popleft())

        return reports

    def kill_all(self) -> None:
        """Best-effort teardown of every running subprocess (used on abort)."""
        for info in self.running.values():
            self._kill_tree(info)
            try:
                info["log_file"].close()
            except OSError:
                pass
        self.running.clear()
        self.pending.clear()
