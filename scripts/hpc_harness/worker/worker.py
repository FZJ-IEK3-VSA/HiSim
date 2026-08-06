"""Worker main loop (spec §4.2): register, lease, dispatch, report, heartbeat, drain.

Single-threaded by design — everything (heartbeats, sampling, log shipping) happens in
one loop, and all forking is delegated to the :class:`Spawner`, which was created
before anything else. POSIX-only (Linux HPC nodes).
"""

import errno
import glob as globmod
import logging
import logging.handlers
import os
import shutil
import signal
import socket as socketmod
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from hpc_harness.client import HarnessClient
from hpc_harness.config import WorkerConfig
from hpc_harness.worker import metrics
from hpc_harness.worker.child import CONSOLE_LOG_NAME
from hpc_harness.worker.logbuffer import ConsoleRing, ErrorReporter, RingHandler, ShipBuffer
from hpc_harness.worker.spawner import Spawner
from hpc_harness.worker.warm_pool import WarmPool, compute_max_slots

LOGGER = logging.getLogger(__name__)

# Errnos that mean "the mount is gone", not merely slow (spec §4.2.1).
_DEFINITIVE_FS_ERRNOS = {errno.ENOTCONN, errno.ESTALE, errno.ENOENT, errno.EROFS}


class Worker:
    """One per Slurm allocation; owns the node's warm pool."""

    def __init__(self, cfg: WorkerConfig) -> None:
        """Set up logging sinks and the HTTP client (no forking yet)."""
        self.cfg = cfg
        self.ring = ConsoleRing()
        self.shipper = ShipBuffer(cfg.log_ship_level)
        self.errors = ErrorReporter("worker")
        self._setup_logging()
        self.client = HarnessClient(
            server_url=cfg.server_url,
            url_file=cfg.server_url_file,
            token=cfg.token,
            backoff_s=cfg.backoff_s,
        )
        self.worker_id: Optional[str] = None
        self.per_job_mem_gb = 10.0
        self.pool: Optional[WarmPool] = None
        self.spawner: Optional[Spawner] = None
        self.drain = False
        self.follow_console = False
        self.console_offset = 0
        self.gate_blocked_since: Optional[float] = None
        self._terminate = False
        self._pending_reports: List[Dict[str, Any]] = []
        self._last_active = 0.0  # last time a job was leased or was running (idle-timeout clock)

    # ------------------------------------------------------------------- setup

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(RingHandler(self.ring))
        root.addHandler(self.shipper)
        root.addHandler(self.errors)
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)

    def _add_file_log(self) -> None:
        """Full-detail rotating file on the shared FS, named by worker_id (spec §4.7)."""
        if not self.cfg.log_root or not self.worker_id:
            return
        try:
            Path(self.cfg.log_root).mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                str(Path(self.cfg.log_root) / f"worker-{self.worker_id}.log"),
                maxBytes=20_000_000, backupCount=2,
            )
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logging.getLogger().addHandler(handler)
        except OSError:
            LOGGER.warning("Could not open shared-FS log file", exc_info=True)

    # --------------------------------------------------------------- preflight

    def preflight(self) -> bool:
        """Bounded-retry shared-FS check (spec §4.2.1); False = give up and quit."""
        probe = Path(self.cfg.result_root) / f".harness_probe.{self.worker_id or os.getpid()}"
        deadline = time.time() + self.cfg.preflight_window_s
        tries = 0
        last_error = "unknown"
        while tries < max(self.cfg.preflight_retries, 1):
            tries += 1
            try:
                Path(self.cfg.result_root).mkdir(parents=True, exist_ok=True)
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return True
            except OSError as exc:
                last_error = f"{exc} (errno={exc.errno})"
                definitive = exc.errno in _DEFINITIVE_FS_ERRNOS
                LOGGER.warning(
                    "Filesystem preflight failed (%s, try %d/%d, %s)",
                    self.cfg.result_root, tries, self.cfg.preflight_retries,
                    "definitive" if definitive else "transient",
                )
            remaining = deadline - time.time()
            if remaining <= 0 or tries >= self.cfg.preflight_retries:
                break
            time.sleep(min(remaining, self.cfg.preflight_window_s / self.cfg.preflight_retries))
        LOGGER.critical(
            "file_access: result_root %s unusable after %d tries: %s",
            self.cfg.result_root, tries, last_error,
        )
        return False

    # --------------------------------------------------------------------- run

    def run(self) -> int:
        """Full worker lifecycle; returns a process exit code."""
        cfg = self.cfg
        if os.name != "posix":
            LOGGER.error("The worker requires POSIX (fork); run it on the cluster.")
            return 2
        if not self.preflight():
            return 3

        LOGGER.info("Python environment: %s", sys.executable)
        exit_reason = "drained"
        try:
            self._register()
            self._add_file_log()

            # Fork-server BEFORE any threads exist (spec §4.3). The HarnessClient above
            # uses blocking httpx without threads, so this ordering is safe.
            # Pin BLAS/OpenMP to cores_per_job (from the old pool.py) so N concurrent
            # sims never oversubscribe the node; applied before warmup so numpy sees it.
            threads = str(max(cfg.cores_per_job, 1))
            pin_env = {
                var: threads
                for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                            "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
            }
            self.spawner = Spawner(cfg.runner, env=pin_env)
            slots = self._compute_slots()
            self.pool = WarmPool(
                self.spawner, slots, cfg.timeout_s, cfg.max_jobs_per_child, cfg.child_rss_ceiling_gb
            )
            self.pool.ensure()
            LOGGER.info("Warm pool ready: %d slots (%s mode)", slots, cfg.mode)

            signal.signal(signal.SIGTERM, self._on_signal)
            signal.signal(signal.SIGINT, self._on_signal)

            last_heartbeat = 0.0
            self._last_active = time.time()  # start the idle-timeout clock at first readiness
            while True:
                if self._terminate:
                    exit_reason = "signal"
                    break
                self.pool.sample()
                for result in self.pool.poll():
                    self._pending_reports.append(self._finalize(result))
                self._flush_reports()
                if self.pool.running():  # work in progress keeps the idle clock reset
                    self._last_active = time.time()

                interval = (
                    self.cfg.console_follow_interval_s if self.follow_console
                    else self.cfg.heartbeat_interval_s
                )
                if time.time() - last_heartbeat >= interval:
                    last_heartbeat = time.time()
                    if not self._heartbeat():
                        exit_reason = "released"
                        break

                if self.drain:
                    if not self.pool.running():
                        break
                elif self.pool.idle_count() > 0 and self._admission_ok():
                    if not self.preflight():
                        exit_reason = "file_access"
                        break
                    if not self._lease_and_dispatch():
                        time.sleep(self.cfg.backoff_s)

                # Release an idle allocation: no job leased or running for idle_timeout_s.
                if self._idle_timed_out(
                    time.time(), self._last_active, self.cfg.idle_timeout_s,
                    bool(self.pool.running()), self.drain,
                ):
                    LOGGER.info(
                        "No job for %.0f s (> idle_timeout %.0f s) — releasing the allocation",
                        time.time() - self._last_active, self.cfg.idle_timeout_s,
                    )
                    exit_reason = "idle_timeout"
                    break
                time.sleep(min(self.cfg.sample_interval_s, 1.0))
        except Exception:  # pylint: disable=broad-except
            # Any startup or loop crash: LOGGER.exception feeds the ErrorReporter with a
            # full traceback, which _shutdown ships to the server's persistent error store.
            LOGGER.exception("Worker crashed")
            exit_reason = "crash"
        finally:
            self._shutdown(exit_reason)
        return 0 if exit_reason in ("drained", "released", "idle_timeout") else 4

    @staticmethod
    def _idle_timed_out(
        now: float, last_active: float, idle_timeout_s: float, running: bool, draining: bool
    ) -> bool:
        """True when the worker should release its allocation for being idle too long.

        Never fires while a job is running, while draining (already exiting), or when the
        timeout is disabled (``idle_timeout_s <= 0``).
        """
        if idle_timeout_s <= 0 or running or draining:
            return False
        return now - last_active > idle_timeout_s

    def _on_signal(self, signum: int, _frame: Any) -> None:
        LOGGER.warning("Signal %d received — shutting down", signum)
        self._terminate = True

    def _register(self) -> None:
        info = {
            "host": socketmod.gethostname(),
            "python": sys.executable,  # for tracing wrong-venv problems in server logs
            "mode": self.cfg.mode,
            "slots": self.cfg.max_slots,
            "cores": psutil.cpu_count() or 1,
            "total_mem_gb": round(psutil.virtual_memory().total / (1024 ** 3), 1),
            "runner": self.cfg.runner,
            "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        }
        response = self.client.register(info)
        self.worker_id = response["worker_id"]
        self.per_job_mem_gb = float(response.get("per_job_mem_gb", self.per_job_mem_gb))
        LOGGER.info("Registered as %s (budget %.1f GB/job)", self.worker_id, self.per_job_mem_gb)

    def _compute_slots(self) -> int:
        if self.cfg.mode == "single_core":
            return 1
        return compute_max_slots(
            self.per_job_mem_gb,
            self.cfg.min_headroom_gb,
            psutil.cpu_count() or 1,
            self.cfg.cores_per_job,
            self.cfg.reserved_cores,
            self.cfg.max_slots,
        )

    # --------------------------------------------------------------- admission

    def _admission_ok(self) -> bool:
        if self.cfg.mode == "whole_node":
            ok = metrics.whole_node_gate(
                self.per_job_mem_gb, self.cfg.min_headroom_gb,
                len(self.pool.running()), psutil.cpu_count() or 1,
                self.cfg.cores_per_job, self.cfg.reserved_cores,
            )
        else:
            ok = metrics.single_core_gate(
                self.cfg.node_gate, self.per_job_mem_gb,
                self.cfg.max_node_cpu_percent, self.cfg.node_safety_buffer_gb,
            )
        now = time.time()
        if ok:
            self.gate_blocked_since = None
            return True
        if self.gate_blocked_since is None:
            self.gate_blocked_since = now
        blocked_for = now - self.gate_blocked_since
        if blocked_for > self.cfg.gate_warn_s:
            LOGGER.warning("Admission gate has blocked leasing for %.0f s", blocked_for)
        if self.cfg.gate_max_wait_s and blocked_for > self.cfg.gate_max_wait_s:
            LOGGER.critical("gate_starved: blocked %.0f s — releasing the allocation", blocked_for)
            self._terminate = True  # deregister happens in shutdown with this reason
            self._gate_starved = True
        return False

    # ----------------------------------------------------------- lease & finish

    def _lease_and_dispatch(self) -> bool:
        response = self.client.lease(self.worker_id, self.pool.idle_count(), uuid.uuid4().hex)
        if response.get("reregister"):
            LOGGER.warning("Server does not know us — re-registering")
            self._register()
            return False
        if response.get("drain"):
            self.drain = True
            LOGGER.info("Queue drained — finishing in-flight jobs and quitting")
        jobs = response.get("jobs", [])
        for job in jobs:
            self._clean_old_attempts(job)
            self.pool.dispatch(job)
        if jobs:
            self._last_active = time.time()  # received work — reset the idle-timeout clock
        return bool(jobs)

    @staticmethod
    def _clean_old_attempts(job: Dict[str, Any]) -> None:
        """Remove older staging dirs of this job before starting the new attempt (§4.8)."""
        staging = Path(job["staging_dir"])
        stem = staging.name.rsplit(".attempt-", 1)[0]
        try:
            for old in staging.parent.glob(f"{stem}.attempt-*"):
                if old != staging:
                    shutil.rmtree(old, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)  # clean re-run of same attempt
        except OSError:
            pass

    def _finalize(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Success check + rename-on-success (spec §4.8), then shape the report."""
        job = result["job"]
        ok = result["ok"]
        error = result["error"]
        staging = job["staging_dir"]

        if ok and job.get("success_file"):
            if not globmod.glob(str(Path(staging) / job["success_file"])):
                ok = False
                error = f"success file missing ({job['success_file']})"
        if ok:
            try:
                canonical = Path(job["result_dir"])
                canonical.parent.mkdir(parents=True, exist_ok=True)
                if canonical.exists():
                    shutil.rmtree(canonical)  # we hold the fenced lease (spec §4.8)
                os.replace(staging, canonical)
            except OSError as exc:
                ok = False
                error = f"result rename failed: {exc}"
        if not ok:
            tail = self._log_tail(Path(staging) / CONSOLE_LOG_NAME)
            self.shipper.add_failure(job["id"], error or "failed", result.get("traceback"), tail)
            # Persist the failure with its traceback in the durable error store (§4.7).
            self.errors.add(
                message=f"job {job['id']} failed: {error or 'failed'}",
                error_type="JobFailure",
                traceback_text=result.get("traceback") or tail,
                job_id=job["id"],
                location=f"runner={self.cfg.runner}",
            )
            if error and error not in ("timeout",) and tail:
                error = f"{error}\n{tail[-1500:]}"

        return {
            "id": job["id"],
            "attempt": job["attempt"],
            "status": "done" if ok else "failed",
            "exit_code": 0 if result["exit_kind"] == "finished" and result["ok"] else 1,
            "duration_s": result["duration_s"],
            "peak_mem_mb": result["peak_mem_mb"],
            "cpu_time_s": None,
            "result_dir": job["result_dir"] if ok else None,
            "staging_dir": staging,
            "error": None if ok else error,
            "host": socketmod.gethostname(),
            "started_at": result["started_at"],
            "finished_at": result["finished_at"],
        }

    @staticmethod
    def _log_tail(path: Path, max_chars: int = 2000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
        except OSError:
            return ""

    def _flush_reports(self) -> None:
        if not self._pending_reports:
            return
        response = self.client.report(self.worker_id, self._pending_reports)
        for entry in response.get("results", []):
            if not entry.get("accepted"):
                LOGGER.warning("Report for job %s rejected (%s)", entry.get("id"), entry.get("reason"))
        self._pending_reports = []

    # ---------------------------------------------------------------- heartbeat

    def _heartbeat(self) -> bool:
        """Send liveness + metrics + running list; apply directives. False = exit now."""
        running = self.pool.running()
        sample = metrics.node_metrics(len(running), self.pool.idle_count())
        directives = self.client.heartbeat(self.worker_id, sample, running)

        records = self.shipper.drain()
        if records:
            self.client.ship_logs(self.worker_id, records)
        errors = self.errors.drain()
        if errors:
            self.client.report_errors(self.worker_id, errors)

        if directives.get("reregister"):
            LOGGER.warning("Server asked us to re-register (we were presumed dead)")
            self._register()
            return True
        for kill in directives.get("kill", []):
            if self.pool.kill_job(kill["job_id"], kill.get("attempt")):
                staging_dirs = globmod.glob(
                    str(Path(self.cfg.result_root) / ".staging" / f"{kill['job_id']:06d}_*")
                )
                for stale in staging_dirs:
                    shutil.rmtree(stale, ignore_errors=True)
        if directives.get("set"):
            new_budget = float(directives["set"].get("per_job_mem_gb", self.per_job_mem_gb))
            if new_budget != self.per_job_mem_gb:
                LOGGER.info("Budget updated by server: %.1f GB/job", new_budget)
                self.per_job_mem_gb = new_budget
                if self.cfg.mode == "whole_node":
                    self.pool.set_target(self._compute_slots())
        if directives.get("capture_console") is not None:
            follow = bool(directives["capture_console"].get("follow"))
            self._upload_console(incremental=follow and self.follow_console)
            self.follow_console = follow
        elif self.follow_console:
            self.follow_console = False
        if directives.get("drain"):
            self.drain = True
        if directives.get("release") and not running:
            LOGGER.info("Released by server (no pending work) — exiting")
            return False
        return True

    def _upload_console(self, incremental: bool) -> None:
        if incremental:
            text, self.console_offset = self.ring.since(self.console_offset)
        else:
            text = self.ring.tail()
            self.console_offset = self.ring.offset
            for entry in self.pool.running():
                job = next((c.job for c in self.pool.children if c.busy and c.job["id"] == entry["job_id"]), None)
                if job:
                    tail = self._log_tail(Path(job["staging_dir"]) / CONSOLE_LOG_NAME)
                    text += f"\n--- job {entry['job_id']} console tail ---\n{tail}"
        self.client.upload_console(self.worker_id, text, self.console_offset)

    # ----------------------------------------------------------------- shutdown

    def _shutdown(self, reason: str) -> None:
        LOGGER.info("Worker shutting down (%s)", reason)
        try:
            if self.pool is not None:
                if reason in ("signal", "file_access"):
                    for entry in self.pool.running():
                        self._pending_reports.append(
                            {
                                "id": entry["job_id"],
                                "attempt": entry["attempt"],
                                "status": "failed",
                                "exit_code": None,
                                "error": f"worker shutdown ({reason})",
                                "host": socketmod.gethostname(),
                            }
                        )
                    self.pool.shutdown(kill_running=True)
                else:
                    self.pool.shutdown(kill_running=False)
            self._flush_reports()
            records = self.shipper.drain()
            if records and self.worker_id:
                self.client.ship_logs(self.worker_id, records)
            errors = self.errors.drain()
            if errors:
                self.client.report_errors(self.worker_id, errors)  # worker_id may be None
            if self.worker_id:
                if getattr(self, "_gate_starved", False):
                    reason = "gate_starved"
                self.client.deregister(self.worker_id, reason)
        except Exception:  # pylint: disable=broad-except
            LOGGER.exception("Best-effort shutdown reporting failed")
        finally:
            if self.spawner is not None:
                self.spawner.shutdown()
            self.client.close()
