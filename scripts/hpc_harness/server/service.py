"""Queue-server logic (spec §4.1, §5.1, §7): everything behind the FastAPI routes.

Owns the core-DB writer thread, the disposable logging DB, and all in-memory state:
worker liveness (heartbeats never write the DB synchronously), pending directives,
orphan strikes, the circuit breaker, the memory budget, and throughput tracking.

The heartbeat handler is the **reconciliation channel** (spec §5.1): it compares the
worker's ``running`` list against the lease table and issues ``kill`` directives for
revoked work, requeues orphaned leases early, and tells resurrected workers to
re-register.
"""

import logging
import os
import re
import threading
import socket
import time
import traceback
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from hpc_harness import db
from hpc_harness.config import ServerConfig
from hpc_harness.logdb import LogDb
from hpc_harness.server.circuit import CircuitBreaker
from hpc_harness.server.eta import ThroughputTracker
from hpc_harness.server.memcheck import MemBudget
from hpc_harness.server.writer import DbWriter

LOGGER = logging.getLogger(__name__)

_LABEL_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_label(label: str, max_len: int = 60) -> str:
    """Make a label safe for use in a directory name."""
    cleaned = _LABEL_SAFE.sub("-", label).strip("-.")
    return cleaned[:max_len] or "job"


class HarnessService:
    """All queue-server behaviour; the FastAPI layer is a thin shell around this."""

    def __init__(self, cfg: ServerConfig) -> None:
        """Open the DBs and initialize in-memory state (no background threads yet)."""
        self.cfg = cfg
        conn = db.connect(cfg.db_path, cfg.journal_mode)
        self.writer = DbWriter(conn)
        self.logdb = LogDb(cfg.logs_db_path, cfg.logs_reopen_check_s)

        self.liveness: Dict[str, float] = {}
        self.pending_kills: Dict[str, List[Dict[str, int]]] = defaultdict(list)
        self.orphan_strikes: Dict[Tuple[str, int], int] = {}
        self.console_follow: Set[str] = set()
        self.console_once: Set[str] = set()
        self.budget_sent: Dict[str, float] = {}
        self.paused: Optional[str] = None

        self.circuit = CircuitBreaker(cfg.circuit_breaker)
        self.eta = ThroughputTracker()
        self.membudget = MemBudget(
            cfg,
            persist_fn=lambda v: self.writer.call(
                lambda c: db.set_meta(c, MemBudget.meta_key(), repr(v))
            ),
        )

        self._counts_cache: Tuple[float, Dict[str, int]] = (0.0, {})
        self._archived = False
        self._host = socket.gethostname()
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        self.autoscaler: Optional[Any] = None  # set by cli when autoscale.enabled

    # ------------------------------------------------------------------ startup

    def startup(self, assume_fleet_dead: bool = False) -> None:
        """Restore state after a (re)start (spec §8, 'Server restart').

        The normal path grants every known non-dead worker a fresh liveness grace
        window and lets heartbeats reconcile their leases — **no blind requeue**.
        ``assume_fleet_dead`` restores the old cold-start behaviour for restarts
        between runs.
        """
        stored = self.writer.call(lambda c: db.get_meta(c, MemBudget.meta_key()))
        if stored:
            try:
                self.membudget.effective = max(self.membudget.effective, float(stored))
            except ValueError:
                pass
        peaks = self.writer.call(
            lambda c: [
                r["peak_mem_mb"]
                for r in c.execute(
                    "SELECT peak_mem_mb FROM attempts WHERE peak_mem_mb IS NOT NULL"
                ).fetchall()
            ]
        )
        self.membudget.peaks_mb.extend(peaks)
        if assume_fleet_dead:
            requeued = self.writer.call(
                lambda c: db.assume_fleet_dead_recovery(c, self.cfg.max_attempts)
            )
            LOGGER.info("Cold start: requeued %d leased jobs, fleet assumed dead", requeued)
        else:
            now = time.time()
            for worker in self.writer.call(db.alive_workers):
                self.liveness[worker["worker_id"]] = now
            LOGGER.info(
                "Warm start: %d workers granted a liveness grace window", len(self.liveness)
            )

    def start_background(self) -> None:
        """Start the reaper / snapshot / autoscaler background loops."""

        def loop(period: float, fn: Any, name: str) -> None:
            def run() -> None:
                while not self._stop.wait(period):
                    try:
                        fn()
                    except Exception as exc:  # pylint: disable=broad-except
                        LOGGER.exception("Background task %s failed", name)
                        self.record_exception("server", f"background:{name}", exc)

            thread = threading.Thread(target=run, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

        loop(self.cfg.reaper_period_s, self.reap, "reaper")
        if self.cfg.db_snapshot_path:
            loop(self.cfg.db_snapshot_interval_s, self.snapshot, "snapshot")
        if self.autoscaler is not None:
            loop(self.cfg.autoscale.period_s, self.autoscaler.tick, "autoscaler")

    def shutdown(self) -> None:
        """Stop background loops, archive telemetry, close DBs."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=10)
        if self.cfg.logs_archive_path:
            self.logdb.archive(self.cfg.logs_archive_path)
        if self.cfg.db_snapshot_path:
            try:
                self.snapshot()
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("Final snapshot failed")
        self.logdb.close()
        self.writer.close()

    # ---------------------------------------------------------------- job paths

    def job_dirs(self, task_id: int, label: Optional[str], runner: str, attempt: int) -> Tuple[str, str]:
        """(canonical result_dir, per-attempt staging_dir) for a job (spec §4.8)."""
        name = f"{task_id:06d}_{_sanitize_label(label or runner)}"
        root = Path(self.cfg.result_root)
        if self.cfg.result_shards:
            canonical = root / f"{task_id // 1000:04d}" / name
        else:
            canonical = root / name
        staging = root / ".staging" / f"{name}.attempt-{attempt}"
        return str(canonical), str(staging)

    # -------------------------------------------------------------------- submit

    def submit_jobs(self, runner: str, jobs: List[Dict[str, Any]], batch: str = "") -> Dict[str, Any]:
        """Enqueue a batch (idempotent per (batch, dedup_key))."""
        result = self.writer.call(lambda c: db.insert_jobs(c, runner, jobs, batch))
        if result["inserted"]:
            self._archived = False  # new work: a later drain re-archives
        return result

    # ------------------------------------------------------------------- workers

    def register_worker(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """Assign a worker_id and hand out the authoritative memory budget (spec §4.6)."""
        worker_id = uuid.uuid4().hex[:12]
        self.writer.call(lambda c: db.register_worker(c, worker_id, info))
        self.liveness[worker_id] = time.time()
        self.budget_sent[worker_id] = self.membudget.effective
        LOGGER.info("Worker %s registered from %s (%s)", worker_id, info.get("host"), info.get("mode"))
        return {"worker_id": worker_id, "per_job_mem_gb": self.membudget.effective}

    def deregister_worker(self, worker_id: str, reason: Optional[str]) -> Dict[str, Any]:
        """Clean worker exit or fatal error: mark dead, reclaim its leases."""
        requeued = self.writer.call(
            lambda c: (
                db.mark_worker_dead(c, worker_id, reason),
                db.requeue_worker_leases(
                    c, worker_id, self.cfg.max_attempts, f"worker deregistered ({reason or 'clean'})"
                ),
            )[1]
        )
        self._forget_worker(worker_id)
        LOGGER.info("Worker %s deregistered (%s); %d leases requeued", worker_id, reason, requeued)
        return {"ok": True, "requeued": requeued}

    def _forget_worker(self, worker_id: str) -> None:
        self.liveness.pop(worker_id, None)
        self.budget_sent.pop(worker_id, None)
        self.pending_kills.pop(worker_id, None)
        self.console_follow.discard(worker_id)
        self.console_once.discard(worker_id)
        for key in [k for k in self.orphan_strikes if k[0] == worker_id]:
            del self.orphan_strikes[key]

    # --------------------------------------------------------------------- lease

    def lease(self, worker_id: str, num_slots: int, lease_id: str) -> Dict[str, Any]:
        """Fenced, replayable lease (spec §7); only jobs of the worker's runner."""
        worker = self.writer.call(lambda c: db.get_worker(c, worker_id))
        if worker is None or worker["status"] == db.W_DEAD:
            return {"jobs": [], "drain": False, "reregister": True}
        self.liveness[worker_id] = time.time()
        if self.paused:
            return {"jobs": [], "drain": False, "paused": self.paused}
        counts = self._counts()
        if counts.get(db.PENDING, 0) == 0:
            return {"jobs": [], "drain": counts.get(db.LEASED, 0) == 0}
        leased = self.writer.call(
            lambda c: db.lease_tasks(c, worker_id, num_slots, lease_id, runner=worker["runner"])
        )
        self._counts_cache = (0.0, {})  # counts changed
        jobs = []
        for task in leased:
            canonical, staging = self.job_dirs(task["id"], task["label"], task["runner"], task["attempt"])
            success_file = task["success_file"] if task["success_file_set"] else self.cfg.success_file
            jobs.append(
                {
                    "id": task["id"],
                    "attempt": task["attempt"],
                    "runner": task["runner"],
                    "payload": task["payload"],
                    "result_dir": canonical,
                    "staging_dir": staging,
                    "success_file": success_file,
                }
            )
        return {"jobs": jobs, "drain": False}

    # -------------------------------------------------------------------- report

    def report(self, worker_id: str, reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply a batch of fenced job reports (spec §5.1)."""
        self.liveness[worker_id] = time.time()
        results = []
        for rep in reports:
            accepted, reason = self.writer.call(
                lambda c, r=rep: db.record_report(c, worker_id, r, self.cfg.max_attempts)
            )
            if accepted and reason != "duplicate":
                ok = rep.get("status") == db.DONE
                final = ok or rep.get("attempt", 0) >= self.cfg.max_attempts
                if final:
                    self.eta.record()
                self.membudget.observe(rep.get("peak_mem_mb"))
                if self.circuit.record(ok, rep.get("error")) and not self.paused:
                    self.paused = f"circuit breaker: {self.circuit.tripped}"
                    LOGGER.error("Leasing auto-paused — %s", self.paused)
                self.orphan_strikes.pop((worker_id, rep["id"]), None)
            entry: Dict[str, Any] = {"id": rep["id"], "accepted": accepted}
            if reason:
                entry["reason"] = reason
            results.append(entry)
        self._counts_cache = (0.0, {})
        return {"results": results}

    # ----------------------------------------------------------------- heartbeat

    def heartbeat(
        self,
        worker_id: str,
        metrics: Optional[Dict[str, Any]],
        running: Optional[List[Dict[str, int]]],
    ) -> Dict[str, Any]:
        """Liveness + telemetry + lease reconciliation; returns the directive set."""
        worker = self.writer.call(lambda c: db.get_worker(c, worker_id))
        if worker is None or worker["status"] == db.W_DEAD:
            return {"reregister": True}
        now = time.time()
        self.liveness[worker_id] = now
        if metrics:
            self.logdb.add_metrics(worker_id, now, metrics)

        directives: Dict[str, Any] = {}
        running = running or []
        leased = self.writer.call(lambda c: db.leased_map_of_worker(c, worker_id))

        # Kills: anything the worker runs that we no longer have leased to it at that
        # attempt (reclaimed / cancelled / reassigned), plus queued cancel kills.
        kills = [
            {"job_id": entry["job_id"], "attempt": entry["attempt"]}
            for entry in running
            if leased.get(entry["job_id"]) != entry["attempt"]
        ]
        for queued in self.pending_kills.pop(worker_id, []):
            if queued not in kills:
                kills.append(queued)
        if kills:
            directives["kill"] = kills

        # Orphans: leased to this worker but absent from its running list.
        running_ids = {entry["job_id"] for entry in running}
        for job_id in leased:
            key = (worker_id, job_id)
            if job_id in running_ids:
                self.orphan_strikes.pop(key, None)
                continue
            strikes = self.orphan_strikes.get(key, 0) + 1
            self.orphan_strikes[key] = strikes
            if strikes >= self.cfg.orphan_strikes:
                if self.writer.call(
                    lambda c, j=job_id: db.requeue_task(
                        c, j, self.cfg.max_attempts, f"orphaned on worker {worker_id}"
                    )
                ):
                    LOGGER.warning("Job %d orphaned on worker %s — requeued", job_id, worker_id)
                self.orphan_strikes.pop(key, None)
                self._counts_cache = (0.0, {})

        counts = self._counts()
        drained = counts.get(db.PENDING, 0) == 0 and counts.get(db.LEASED, 0) == 0
        if drained:
            directives["drain"] = True
            self._maybe_archive()
        elif (
            self.cfg.release_idle_workers
            and counts.get(db.PENDING, 0) == 0
            and not leased
            and not running
        ):
            directives["release"] = True

        if self.budget_sent.get(worker_id) != self.membudget.effective:
            directives["set"] = {"per_job_mem_gb": self.membudget.effective}
            self.budget_sent[worker_id] = self.membudget.effective

        if worker_id in self.console_follow:
            directives["capture_console"] = {"follow": True}
        elif worker_id in self.console_once:
            self.console_once.discard(worker_id)
            directives["capture_console"] = {"follow": False}

        return directives

    # ---------------------------------------------------------- logs and console

    def ship_logs(self, worker_id: str, records: List[Dict[str, Any]]) -> None:
        """Store shipped log records (best-effort)."""
        worker = self.writer.call(lambda c: db.get_worker(c, worker_id))
        self.logdb.add_log_records(worker_id, worker.get("host") if worker else None, records)

    def console_upload(self, worker_id: str, ts: float, text: str, next_offset: int) -> None:
        """Store the latest console snapshot for a worker."""
        self.logdb.store_console(worker_id, ts, text, next_offset)

    def request_console(self, worker_id: str, follow: Optional[bool]) -> None:
        """Queue a capture_console directive (one-shot, or toggle follow mode)."""
        if follow is True:
            self.console_follow.add(worker_id)
        elif follow is False:
            self.console_follow.discard(worker_id)
        else:
            self.console_once.add(worker_id)

    # --------------------------------------------------------------------- admin

    def apply_config(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a live config change (currently: per_job_mem_gb, spec §4.6)."""
        applied = {}
        if "per_job_mem_gb" in changes:
            self.membudget.set_manual(float(changes["per_job_mem_gb"]))
            self.budget_sent.clear()  # every worker gets a `set` directive next heartbeat
            applied["per_job_mem_gb"] = self.membudget.effective
        return {"ok": True, "applied": applied}

    def cancel_job(self, job_id: int) -> Dict[str, Any]:
        """Cancel a pending/leased job; leased holders get a kill directive (spec §5.2)."""
        result = self.writer.call(lambda c: db.cancel_task(c, job_id))
        if result.get("ok") and result.get("leased_by"):
            self.pending_kills[result["leased_by"]].append(
                {"job_id": job_id, "attempt": result["attempt"]}
            )
        self._counts_cache = (0.0, {})
        return {"ok": result.get("ok", False), "reason": result.get("reason")}

    def pause(self) -> None:
        """Stop handing out leases (fleet keeps running in-flight jobs)."""
        self.paused = "paused by admin"

    def resume(self) -> None:
        """Resume leasing; also clears a circuit-breaker trip (spec §8.1)."""
        self.paused = None
        self.circuit.reset()

    def admin_reset(self, leased: bool, failed: bool) -> int:
        """Manual requeue of stuck/failed jobs."""
        requeued = self.writer.call(lambda c: db.reset(c, leased=leased, failed=failed))
        self._counts_cache = (0.0, {})
        return requeued

    def purge_logs(self) -> int:
        """Delete + recreate the logging DB (spec §6.8)."""
        return self.logdb.purge()

    # -------------------------------------------------------------------- errors

    def record_error(self, error: Dict[str, Any]) -> None:
        """Persist one error (fire-and-forget, never raises) — durable core DB (§4.7)."""
        try:
            self.writer.submit(lambda c: db.record_error(c, error))
        except Exception:  # pylint: disable=broad-except
            LOGGER.debug("Could not enqueue error record", exc_info=True)

    def record_exception(
        self,
        source: str,
        location: str,
        exc: BaseException,
        worker_id: Optional[str] = None,
        job_id: Optional[int] = None,
    ) -> None:
        """Format an exception (with full traceback) and persist it (§4.7)."""
        self.record_error({
            "ts": time.time(),
            "source": source,
            "location": location,
            "worker_id": worker_id,
            "job_id": job_id,
            "host": self._host,
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )[:16000],
        })

    def report_errors(self, worker_id: Optional[str], records: List[Dict[str, Any]]) -> None:
        """Ingest a batch of errors reported by a client (worker / submit CLI)."""
        host = None
        if worker_id:
            worker = self.writer.call(lambda c: db.get_worker(c, worker_id))
            host = worker.get("host") if worker else None
        for record in records:
            entry = dict(record)
            entry.setdefault("source", "client")
            if worker_id and not entry.get("worker_id"):
                entry["worker_id"] = worker_id
            if host and not entry.get("host"):
                entry["host"] = host
            self.record_error(entry)

    def errors(self, source: Optional[str], since: Optional[float], limit: int) -> List[Dict[str, Any]]:
        """Error records for the dashboard error page."""
        return self.writer.call(lambda c: db.list_errors(c, source, since, limit))

    def error_summary(self) -> Dict[str, Any]:
        """Aggregate error counts for the error page."""
        return self.writer.call(db.error_summary)

    def clear_errors(self) -> int:
        """Delete all persisted errors (admin)."""
        return self.writer.call(db.clear_errors)

    # ------------------------------------------------------------- config & scaling

    def config_public(self) -> Dict[str, Any]:
        """Server configuration for the settings page (token redacted)."""
        import dataclasses  # pylint: disable=import-outside-toplevel

        data = dataclasses.asdict(self.cfg)
        if data.get("token"):
            data["token"] = "••• (set, redacted)"
        data["effective_per_job_mem_gb"] = self.membudget.effective
        data["max_attempts"] = self.cfg.max_attempts
        return data

    def autoscale_status(self) -> Dict[str, Any]:
        """Autoscaler state for its dashboard page (works whether enabled or not)."""
        if self.autoscaler is not None:
            return self.autoscaler.status()
        cfg = self.cfg.autoscale
        return {
            "enabled": False,
            "worker_mode": cfg.worker_mode,
            "period_s": cfg.period_s,
            "standby_floor": cfg.standby_floor,
            "max_workers": cfg.max_workers,
            "worker_script": cfg.worker_script,
            "worker_config": cfg.worker_config,
            "slurm_log_dir": cfg.slurm_log_dir,
            "partition": cfg.partition,
            "capacity_probe": cfg.capacity_probe or "sinfo -h -o %C (idle field)",
            "squeue_poll_s": cfg.squeue_poll_s,
            "registration_grace_s": cfg.registration_grace_s,
            "trying_to_scale": False,
            "action": "disabled",
            "submission_state_counts": self.writer.call(db.submission_state_counts),
            "submissions": self.writer.call(lambda c: db.recent_submissions(c, 100)),
        }

    # -------------------------------------------------------------------- status

    def _counts(self) -> Dict[str, int]:
        ts, cached = self._counts_cache
        if time.time() - ts < 2.0 and cached:
            return cached
        counts = self.writer.call(db.counts)
        self._counts_cache = (time.time(), counts)
        return counts

    def status(self) -> Dict[str, Any]:
        """Machine-readable summary (GET /status)."""
        counts = self._counts()
        remaining = counts.get(db.PENDING, 0) + counts.get(db.LEASED, 0)
        result: Dict[str, Any] = {
            "counts": counts,
            "workers_alive": len(self.liveness),
            "throughput_per_min": round(self.eta.throughput_per_min(), 3),
            "eta_seconds": self.eta.eta_seconds(remaining),
            "per_job_mem_gb": self.membudget.effective,
            "drained": remaining == 0 and counts.get("total", 0) > 0,
        }
        warning = self.membudget.warning()
        if warning:
            result["mem_warning"] = warning
        if self.paused:
            result["paused"] = self.paused
            if self.circuit.tripped:
                result["circuit_breaker"] = {
                    "tripped": self.circuit.tripped,
                    "top_error": self.circuit.top_error(),
                }
        return result

    def jobs(self, state: Optional[str], batch: Optional[str], limit: int, offset: int) -> List[Dict[str, Any]]:
        """Job rows for the dashboard."""
        newest_first = state in (db.DONE, db.DEAD, db.CANCELLED)
        return self.writer.call(
            lambda c: db.list_jobs(c, state, batch, limit, offset, newest_first=newest_first)
        )

    def workers(self) -> List[Dict[str, Any]]:
        """Worker rows, enriched with in-memory liveness age."""
        rows = self.writer.call(db.list_workers)
        now = time.time()
        for row in rows:
            seen = self.liveness.get(row["worker_id"])
            row["heartbeat_age_s"] = round(now - seen, 1) if seen else None
        return rows

    # -------------------------------------------------------------------- reaper

    def reap(self) -> Dict[str, int]:
        """One reaper pass: stale leases, missing workers, liveness flush (spec §8)."""
        now = time.time()
        stale = self.writer.call(
            lambda c: db.reset_stale_leases(c, self.cfg.lease_timeout_s, self.cfg.max_attempts)
        )
        missing = 0
        for worker_id, seen in list(self.liveness.items()):
            if now - seen > self.cfg.worker_timeout_s:
                requeued = self.writer.call(
                    lambda c, w=worker_id: (
                        db.mark_worker_dead(c, w, "missing (no heartbeat)"),
                        db.requeue_worker_leases(
                            c, w, self.cfg.max_attempts, f"worker {w} missing > {self.cfg.worker_timeout_s:.0f}s"
                        ),
                    )[1]
                )
                LOGGER.warning(
                    "Worker %s missing — marked dead, %d leases requeued", worker_id, requeued
                )
                self._forget_worker(worker_id)
                missing += 1
        self.writer.call(lambda c: db.flush_heartbeats(c, dict(self.liveness)))
        self.writer.call(lambda c: db.trim_errors(c, self.cfg.error_retention))
        if stale or missing:
            self._counts_cache = (0.0, {})
        counts = self._counts()
        if counts.get(db.PENDING, 0) == 0 and counts.get(db.LEASED, 0) == 0:
            self._maybe_archive()
        return {"stale": stale, "missing": missing}

    def _maybe_archive(self) -> None:
        """Archive the logging DB once per drain (spec §6.9)."""
        if self._archived or not self.cfg.logs_archive_path:
            return
        if self.logdb.archive(self.cfg.logs_archive_path):
            self._archived = True
            LOGGER.info("Logging DB archived to %s", self.cfg.logs_archive_path)

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> None:
        """Write a consistent core-DB copy to the shared FS (spec §6.5)."""
        dest = self.cfg.db_snapshot_path
        if not dest:
            return
        tmp = dest + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        self.writer.call(lambda c: c.execute("VACUUM INTO ?", (tmp,)), raw=True, timeout=600.0)
        os.replace(tmp, dest)
