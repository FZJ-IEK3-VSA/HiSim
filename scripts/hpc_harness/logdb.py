"""Disposable logging/telemetry SQLite DB (spec §6): metrics, shipped logs, consoles.

Every write is **best-effort**: any failure (file deleted mid-run, locked, disk full)
is swallowed and never fails the request that triggered it. The file can be deleted at
any time; the server notices (inode check) and recreates it. Nothing the server
*decides* on lives here — a wipe only costs dashboard history.
"""

import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_metrics (
    worker_id  TEXT NOT NULL,
    ts         REAL NOT NULL,
    cpu_percent      REAL,
    mem_used_gb      REAL,
    mem_available_gb REAL,
    load1            REAL,
    running_jobs     INTEGER,
    free_slots       INTEGER,
    extra            TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_worker_ts ON worker_metrics(worker_id, ts);

CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY,
    ts        REAL,
    worker_id TEXT,
    job_id    INTEGER,
    level     TEXT,
    logger    TEXT,
    message   TEXT,
    traceback TEXT,
    host      TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_worker ON logs(worker_id);
CREATE INDEX IF NOT EXISTS idx_logs_job ON logs(job_id);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);

CREATE TABLE IF NOT EXISTS console_snapshots (
    worker_id   TEXT PRIMARY KEY,
    ts          REAL,
    text        TEXT,
    next_offset INTEGER
);
"""


class LogDb:
    """Owner of the disposable logging DB; all operations are best-effort."""

    def __init__(self, path: str, reopen_check_s: float = 60.0) -> None:
        """Open (creating if needed) the logging DB at ``path``."""
        self.path = path
        self.reopen_check_s = reopen_check_s
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._inode: Optional[int] = None
        self._last_check = 0.0
        self._open()

    def _open(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA synchronous=OFF;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._inode = self._stat_inode()
        except Exception:  # pylint: disable=broad-except
            LOGGER.warning("Could not open logging DB at %s", self.path, exc_info=True)
            self._conn = None

    def _stat_inode(self) -> Optional[int]:
        try:
            return os.stat(self.path).st_ino
        except OSError:
            return None

    def _check_reopen(self) -> None:
        """Recreate the DB if the file was deleted/replaced underneath us (spec §6.8)."""
        now = time.time()
        if now - self._last_check < self.reopen_check_s:
            return
        self._last_check = now
        if self._stat_inode() != self._inode:
            self._close()
            self._open()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pylint: disable=broad-except
                pass
        self._conn = None

    def _execute(self, sql: str, params: tuple) -> None:
        """Best-effort write: swallow every failure (spec §6)."""
        with self._lock:
            self._check_reopen()
            if self._conn is None:
                self._open()
            if self._conn is None:
                return
            try:
                self._conn.execute(sql, params)
                self._conn.commit()
            except Exception:  # pylint: disable=broad-except
                LOGGER.debug("Logging-DB write failed (ignored)", exc_info=True)

    def _query(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        """Best-effort read: an unavailable logging DB just yields no history."""
        with self._lock:
            if self._conn is None:
                self._open()
            if self._conn is None:
                return []
            try:
                return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
            except Exception:  # pylint: disable=broad-except
                return []

    # ---------------------------------------------------------------- writes

    def add_metrics(self, worker_id: str, ts: float, metrics: Dict[str, Any]) -> None:
        """Append one node-metrics sample from a heartbeat."""
        import json

        known = ("cpu_percent", "mem_used_gb", "mem_available_gb", "load1", "running_jobs", "free_slots")
        extra = {k: v for k, v in metrics.items() if k not in known}
        self._execute(
            "INSERT INTO worker_metrics(worker_id, ts, cpu_percent, mem_used_gb,"
            " mem_available_gb, load1, running_jobs, free_slots, extra) VALUES(?,?,?,?,?,?,?,?,?)",
            (worker_id, ts) + tuple(metrics.get(k) for k in known) + (json.dumps(extra) if extra else None,),
        )

    def add_log_records(self, worker_id: str, host: Optional[str], records: List[Dict[str, Any]]) -> None:
        """Store shipped log records (spec §4.7)."""
        for rec in records:
            self._execute(
                "INSERT INTO logs(ts, worker_id, job_id, level, logger, message, traceback, host)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (rec.get("ts"), worker_id, rec.get("job_id"), rec.get("level"),
                 rec.get("logger"), rec.get("message"), rec.get("traceback"), host),
            )

    def store_console(self, worker_id: str, ts: float, text: str, next_offset: int) -> None:
        """Overwrite the latest console snapshot for a worker (never grows, spec §6.6)."""
        self._execute(
            "INSERT INTO console_snapshots(worker_id, ts, text, next_offset) VALUES(?,?,?,?)"
            " ON CONFLICT(worker_id) DO UPDATE SET ts=excluded.ts, text=excluded.text,"
            " next_offset=excluded.next_offset",
            (worker_id, ts, text, next_offset),
        )

    # ----------------------------------------------------------------- reads

    def get_console(self, worker_id: str) -> Optional[Dict[str, Any]]:
        """Latest console snapshot for a worker, or None."""
        rows = self._query("SELECT * FROM console_snapshots WHERE worker_id=?", (worker_id,))
        return rows[0] if rows else None

    def query_logs(
        self,
        worker_id: Optional[str] = None,
        job_id: Optional[int] = None,
        level: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Filterable log records, newest first (dashboard log explorer)."""
        clauses: List[str] = []
        params: tuple = ()
        if worker_id:
            clauses.append("worker_id=?")
            params += (worker_id,)
        if job_id is not None:
            clauses.append("job_id=?")
            params += (job_id,)
        if level:
            clauses.append("level=?")
            params += (level,)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._query(f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ?", params + (limit,))

    def timeseries(self, worker_id: Optional[str] = None, since: float = 0.0, limit: int = 2000) -> List[Dict[str, Any]]:
        """Metric samples for the dashboard charts."""
        if worker_id:
            return self._query(
                "SELECT * FROM worker_metrics WHERE worker_id=? AND ts>=? ORDER BY ts LIMIT ?",
                (worker_id, since, limit),
            )
        return self._query(
            "SELECT * FROM worker_metrics WHERE ts>=? ORDER BY ts LIMIT ?", (since, limit)
        )

    # ------------------------------------------------------------- lifecycle

    def purge(self) -> int:
        """Delete + recreate the logging DB, reclaiming space (spec §6.8)."""
        with self._lock:
            freed = 0
            try:
                freed = os.path.getsize(self.path)
            except OSError:
                pass
            self._close()
            for suffix in ("", "-wal", "-journal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except OSError:
                    pass
            self._open()
            return freed

    def archive(self, dest: str) -> bool:
        """Copy the logging DB to the shared FS at end of run (spec §6.9)."""
        with self._lock:
            if self._conn is None:
                return False
            try:
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                tmp = dest + ".tmp"
                backup = sqlite3.connect(tmp)
                with backup:
                    self._conn.backup(backup)
                backup.close()
                shutil.move(tmp, dest)
                return True
            except Exception:  # pylint: disable=broad-except
                LOGGER.warning("Logging-DB archive to %s failed", dest, exc_info=True)
                return False

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._close()
