"""Core SQLite database for the HPC harness (spec §6): the durable source of truth.

Only the server process (via its single writer thread, ``server/writer.py``) ever
mutates this database. All lease/report mutations are **fenced** (spec §5.1):

- ``lease_tasks`` increments ``attempts`` and returns it as the attempt fence token;
  re-posting the same ``lease_id`` replays the identical job set instead of leasing more.
- ``record_report`` accepts a report only when the task is still leased to the reporting
  worker at the reported attempt; duplicates of an accepted report are absorbed via the
  ``UNIQUE(task_id, attempt_no)`` constraint on ``attempts``.

Task lifecycle::

    pending --lease(attempt=n)--> leased --report(done, fenced)--> done
                                    | \\--report(fail, retries left)--> pending (retry)
                                    |  \\-report(fail, no retries)---> dead
                                    |\\--orphaned / lease timeout ---> pending or dead
                                    \\--admin cancel ----------------> cancelled
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sqlite3

# Task status values.
PENDING = "pending"
LEASED = "leased"
DONE = "done"
FAILED = "failed"  # transient report status; tasks themselves go pending/dead
DEAD = "dead"
CANCELLED = "cancelled"

# Worker status values.
W_ALIVE = "alive"
W_MISSING = "missing"
W_DEAD = "dead"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY,
    runner           TEXT NOT NULL,
    payload          TEXT NOT NULL,
    batch_id         TEXT NOT NULL DEFAULT '',
    dedup_key        TEXT,
    label            TEXT,
    priority         INTEGER NOT NULL DEFAULT 0,
    success_file     TEXT,
    success_file_set INTEGER NOT NULL DEFAULT 0,
    lease_id         TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    leased_by        TEXT,
    leased_at        REAL,
    started_at       REAL,
    finished_at      REAL,
    duration_s       REAL,
    peak_mem_mb      REAL,
    cpu_time_s       REAL,
    exit_code        INTEGER,
    result_dir       TEXT,
    error            TEXT,
    updated_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(leased_by, lease_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedup
    ON tasks(batch_id, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL,
    attempt_no  INTEGER NOT NULL,
    worker_id   TEXT,
    host        TEXT,
    started_at  REAL,
    finished_at REAL,
    duration_s  REAL,
    peak_mem_mb REAL,
    cpu_time_s  REAL,
    exit_code   INTEGER,
    status      TEXT,
    error       TEXT,
    staging_dir TEXT,
    UNIQUE(task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS workers (
    worker_id     TEXT PRIMARY KEY,
    host          TEXT,
    mode          TEXT,
    slots         INTEGER,
    cores         INTEGER,
    total_mem_gb  REAL,
    runner        TEXT,
    registered_at REAL,
    last_heartbeat REAL,
    status        TEXT NOT NULL DEFAULT 'alive',
    last_error    TEXT,
    jobs_done     INTEGER NOT NULL DEFAULT 0,
    jobs_failed   INTEGER NOT NULL DEFAULT 0,
    slurm_job_id  TEXT
);

CREATE TABLE IF NOT EXISTS slurm_submissions (
    slurm_job_id         TEXT PRIMARY KEY,
    mode                 TEXT,
    submitted_at         REAL,
    registered_worker_id TEXT,
    state                TEXT NOT NULL DEFAULT 'submitted'
);

CREATE TABLE IF NOT EXISTS errors (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    source     TEXT,
    worker_id  TEXT,
    job_id     INTEGER,
    host       TEXT,
    location   TEXT,
    error_type TEXT,
    message    TEXT,
    traceback  TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts);
CREATE INDEX IF NOT EXISTS idx_errors_source ON errors(source);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: str, journal_mode: str = "WAL") -> sqlite3.Connection:
    """Open (creating if needed) the core database with the schema applied.

    ``journal_mode="WAL"`` (+ ``synchronous=NORMAL``) for the primary local-disk
    profile; ``"DELETE"`` (+ ``synchronous=FULL``) for the shared-FS fallback, where
    WAL's shared-memory file is unreliable over NFS/Lustre/GPFS (spec §6.5).
    """
    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if journal_mode == "WAL":
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    else:
        conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("PRAGMA synchronous=FULL;")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store a key/value pair (config provenance, persisted effective budget, ...)."""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """Read a meta value, or None."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


# --------------------------------------------------------------------------- jobs


def insert_jobs(
    conn: sqlite3.Connection,
    runner: str,
    jobs: List[Dict[str, Any]],
    batch_id: str = "",
) -> Dict[str, Any]:
    """Enqueue a batch of jobs; idempotent on ``(batch_id, dedup_key)`` (spec §7).

    Each job dict: ``{payload, label?, dedup_key?, priority?, success_file?}`` where a
    *present* ``success_file`` key (even with value None) overrides the server default.
    """
    now = time.time()
    inserted, skipped, ids = 0, 0, []
    for job in jobs:
        has_sf = "success_file" in job
        cur = conn.execute(
            "INSERT OR IGNORE INTO tasks(runner, payload, batch_id, dedup_key, label, priority,"
            " success_file, success_file_set, status, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                runner,
                json.dumps(job["payload"]),
                batch_id,
                job.get("dedup_key"),
                job.get("label"),
                int(job.get("priority", 0)),
                job.get("success_file") if has_sf else None,
                1 if has_sf else 0,
                PENDING,
                now,
            ),
        )
        if cur.rowcount:
            inserted += 1
            ids.append(cur.lastrowid)
        else:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped, "ids": ids}


def lease_tasks(
    conn: sqlite3.Connection,
    worker_id: str,
    n: int,
    lease_id: Optional[str] = None,
    runner: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Atomically lease up to ``n`` pending tasks to ``worker_id`` (fenced, replayable).

    Replay: if rows are already leased to this worker under this ``lease_id``, exactly
    those rows are returned again and nothing new is leased — a lost HTTP response
    therefore strands nothing (spec §7). Otherwise leases in priority order and
    increments ``attempts``; the post-increment value is the **attempt fence token**.
    """
    lease_id = lease_id or uuid.uuid4().hex
    replay = conn.execute(
        "SELECT * FROM tasks WHERE leased_by=? AND lease_id=? AND status=?"
        " ORDER BY priority DESC, id",
        (worker_id, lease_id, LEASED),
    ).fetchall()
    if replay:
        return [_task_lease_dict(r) for r in replay]
    if n <= 0:
        return []
    now = time.time()
    if runner:  # a worker serves exactly one runner (spec §4.4)
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status=? AND runner=? ORDER BY priority DESC, id LIMIT ?",
            (PENDING, runner, n),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status=? ORDER BY priority DESC, id LIMIT ?",
            (PENDING, n),
        ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE tasks SET status=?, attempts=attempts+1, leased_by=?, lease_id=?,"
            " leased_at=?, started_at=?, updated_at=? WHERE id=?",
            (LEASED, worker_id, lease_id, now, now, now, row["id"]),
        )
    leased = conn.execute(
        "SELECT * FROM tasks WHERE leased_by=? AND lease_id=? AND status=?"
        " ORDER BY priority DESC, id",
        (worker_id, lease_id, LEASED),
    ).fetchall()
    return [_task_lease_dict(r) for r in leased]


def _task_lease_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Shape a leased task row for the lease response (attempt = fence token)."""
    return {
        "id": row["id"],
        "attempt": row["attempts"],
        "runner": row["runner"],
        "payload": json.loads(row["payload"]),
        "label": row["label"],
        "success_file": row["success_file"],
        "success_file_set": bool(row["success_file_set"]),
    }


def record_report(
    conn: sqlite3.Connection,
    worker_id: str,
    report: Dict[str, Any],
    max_attempts: int,
) -> Tuple[bool, Optional[str]]:
    """Apply one fenced finished-job report (spec §5.1).

    Returns ``(accepted, reason)``. Accepted only when the task is still ``leased`` to
    ``worker_id`` at exactly ``report["attempt"]``. A replay of an already-recorded
    attempt returns ``(True, "duplicate")`` without re-applying. Anything else is
    ``(False, "stale")`` / ``(False, "unknown")`` and leaves the task untouched.
    """
    task_id = report["id"]
    attempt = report.get("attempt")
    row = conn.execute(
        "SELECT status, attempts, leased_by FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    if row is None:
        return False, "unknown"
    existing = conn.execute(
        "SELECT 1 FROM attempts WHERE task_id=? AND attempt_no=?", (task_id, attempt)
    ).fetchone()
    if existing is not None:
        return True, "duplicate"
    if row["status"] != LEASED or row["leased_by"] != worker_id or row["attempts"] != attempt:
        return False, "stale"

    now = time.time()
    status = report["status"]
    conn.execute(
        "INSERT INTO attempts(task_id, attempt_no, worker_id, host, started_at, finished_at,"
        " duration_s, peak_mem_mb, cpu_time_s, exit_code, status, error, staging_dir)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id, attempt, worker_id, report.get("host"), report.get("started_at"),
            report.get("finished_at"), report.get("duration_s"), report.get("peak_mem_mb"),
            report.get("cpu_time_s"), report.get("exit_code"), status, report.get("error"),
            report.get("staging_dir"),
        ),
    )
    if status == DONE:
        conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, duration_s=?, peak_mem_mb=?, cpu_time_s=?,"
            " exit_code=?, result_dir=?, error=NULL, leased_by=NULL, lease_id=NULL, updated_at=?"
            " WHERE id=?",
            (DONE, now, report.get("duration_s"), report.get("peak_mem_mb"),
             report.get("cpu_time_s"), report.get("exit_code"), report.get("result_dir"),
             now, task_id),
        )
        counter = "jobs_done"
    elif attempt < max_attempts:
        conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, lease_id=NULL, leased_at=NULL, exit_code=?,"
            " error=?, peak_mem_mb=?, duration_s=?, updated_at=? WHERE id=?",
            (PENDING, report.get("exit_code"), report.get("error"),
             report.get("peak_mem_mb"), report.get("duration_s"), now, task_id),
        )
        counter = "jobs_failed"
    else:
        conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, exit_code=?, error=?, peak_mem_mb=?,"
            " duration_s=?, result_dir=?, leased_by=NULL, lease_id=NULL, updated_at=? WHERE id=?",
            (DEAD, now, report.get("exit_code"), report.get("error"),
             report.get("peak_mem_mb"), report.get("duration_s"),
             report.get("result_dir"), now, task_id),
        )
        counter = "jobs_failed"
    conn.execute(
        f"UPDATE workers SET {counter} = {counter} + 1 WHERE worker_id=?", (worker_id,)
    )
    return True, None


def _requeue_row(conn: sqlite3.Connection, row: sqlite3.Row, max_attempts: int, reason: str, now: float) -> None:
    """Return one leased row to pending (or dead) and record an audit attempts row."""
    conn.execute(
        "INSERT OR IGNORE INTO attempts(task_id, attempt_no, worker_id, status, error, finished_at)"
        " VALUES(?,?,?,?,?,?)",
        (row["id"], row["attempts"], row["leased_by"], FAILED, reason, now),
    )
    if row["attempts"] < max_attempts:
        conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, lease_id=NULL, leased_at=NULL,"
            " error=?, updated_at=? WHERE id=?",
            (PENDING, reason, now, row["id"]),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, lease_id=NULL, error=?, finished_at=?,"
            " updated_at=? WHERE id=?",
            (DEAD, reason + " (attempts exhausted)", now, row["id"]),
        )


def reset_stale_leases(conn: sqlite3.Connection, lease_timeout_s: float, max_attempts: int) -> int:
    """Reclaim tasks leased longer ago than ``lease_timeout_s`` with no report."""
    cutoff = time.time() - lease_timeout_s
    rows = conn.execute(
        "SELECT id, attempts, leased_by FROM tasks WHERE status=? AND leased_at < ?",
        (LEASED, cutoff),
    ).fetchall()
    now = time.time()
    for row in rows:
        _requeue_row(conn, row, max_attempts, "lease timed out, reclaimed", now)
    return len(rows)


def requeue_worker_leases(conn: sqlite3.Connection, worker_id: str, max_attempts: int, reason: str) -> int:
    """Requeue every task currently leased by ``worker_id`` (missing worker / deregister)."""
    rows = conn.execute(
        "SELECT id, attempts, leased_by FROM tasks WHERE status=? AND leased_by=?",
        (LEASED, worker_id),
    ).fetchall()
    now = time.time()
    for row in rows:
        _requeue_row(conn, row, max_attempts, reason, now)
    return len(rows)


def requeue_task(conn: sqlite3.Connection, task_id: int, max_attempts: int, reason: str) -> bool:
    """Requeue a single leased task (orphan reconciliation, spec §5.1)."""
    row = conn.execute(
        "SELECT id, attempts, leased_by FROM tasks WHERE id=? AND status=?", (task_id, LEASED)
    ).fetchone()
    if row is None:
        return False
    _requeue_row(conn, row, max_attempts, reason, time.time())
    return True


def cancel_task(conn: sqlite3.Connection, task_id: int) -> Dict[str, Any]:
    """Cancel a pending or leased task; returns who held the lease (for the kill directive)."""
    row = conn.execute(
        "SELECT status, attempts, leased_by FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "unknown"}
    if row["status"] not in (PENDING, LEASED):
        return {"ok": False, "reason": f"not cancellable (status={row['status']})"}
    now = time.time()
    conn.execute(
        "UPDATE tasks SET status=?, leased_by=NULL, lease_id=NULL, error=?, finished_at=?,"
        " updated_at=? WHERE id=?",
        (CANCELLED, "cancelled by admin", now, now, task_id),
    )
    return {"ok": True, "leased_by": row["leased_by"], "attempt": row["attempts"]}


def assume_fleet_dead_recovery(conn: sqlite3.Connection, max_attempts: int) -> int:
    """Cold-start recovery: requeue *all* leased tasks (old behaviour, ``--assume-fleet-dead``).

    Only correct when no worker is alive; the normal restart path instead reconciles
    live workers' leases via heartbeats (spec §8).
    """
    rows = conn.execute(
        "SELECT id, attempts, leased_by FROM tasks WHERE status=?", (LEASED,)
    ).fetchall()
    now = time.time()
    for row in rows:
        _requeue_row(conn, row, max_attempts, "requeued at cold server start", now)
    conn.execute("UPDATE workers SET status=? WHERE status != ?", (W_DEAD, W_DEAD))
    return len(rows)


def reset(conn: sqlite3.Connection, leased: bool = False, failed: bool = False) -> int:
    """Manually requeue tasks: stuck leases and/or failed/dead/cancelled ones."""
    now = time.time()
    affected = 0
    if leased:
        cur = conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, lease_id=NULL, leased_at=NULL,"
            " updated_at=? WHERE status=?",
            (PENDING, now, LEASED),
        )
        affected += cur.rowcount
    if failed:
        cur = conn.execute(
            "UPDATE tasks SET status=?, attempts=0, leased_by=NULL, lease_id=NULL,"
            " leased_at=NULL, error=NULL, updated_at=? WHERE status IN (?, ?, ?)",
            (PENDING, now, FAILED, DEAD, CANCELLED),
        )
        affected += cur.rowcount
    return affected


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Return a {status: count} dict plus a ``total`` key."""
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status").fetchall()
    result = {row["status"]: row["c"] for row in rows}
    result["total"] = sum(row["c"] for row in rows)
    return result


def is_drained(conn: sqlite3.Connection) -> bool:
    """True when no tasks are pending and none are leased (run is finished)."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE status IN (?, ?)", (PENDING, LEASED)
    ).fetchone()
    return bool(row["c"] == 0)


def leased_map_of_worker(conn: sqlite3.Connection, worker_id: str) -> Dict[int, int]:
    """{task_id: attempt} for every task currently leased by ``worker_id`` (reconciliation)."""
    rows = conn.execute(
        "SELECT id, attempts FROM tasks WHERE status=? AND leased_by=?", (LEASED, worker_id)
    ).fetchall()
    return {row["id"]: row["attempts"] for row in rows}


def list_jobs(
    conn: sqlite3.Connection,
    state: Optional[str] = None,
    batch: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    newest_first: bool = False,
) -> List[Dict[str, Any]]:
    """Job rows for the dashboard tables (next-N pending in lease order / last-N finished)."""
    where = ""
    params: Tuple[Any, ...] = ()
    clauses = []
    if state:
        clauses.append("status=?")
        params += (state,)
    if batch:
        clauses.append("batch_id=?")
        params += (batch,)
    if clauses:
        where = "WHERE " + " AND ".join(clauses)
    order = "ORDER BY updated_at DESC" if newest_first else "ORDER BY priority DESC, id"
    rows = conn.execute(
        f"SELECT id, runner, batch_id, label, priority, status, attempts, leased_by,"
        f" duration_s, peak_mem_mb, exit_code, result_dir, error, updated_at"
        f" FROM tasks {where} {order} LIMIT ? OFFSET ?",
        params + (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


# ------------------------------------------------------------------------ workers


def register_worker(conn: sqlite3.Connection, worker_id: str, info: Dict[str, Any]) -> None:
    """Insert a newly registered worker row."""
    now = time.time()
    conn.execute(
        "INSERT INTO workers(worker_id, host, mode, slots, cores, total_mem_gb, runner,"
        " registered_at, last_heartbeat, status, slurm_job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            worker_id, info.get("host"), info.get("mode"), info.get("slots"),
            info.get("cores"), info.get("total_mem_gb"), info.get("runner"),
            now, now, W_ALIVE, info.get("slurm_job_id"),
        ),
    )
    if info.get("slurm_job_id"):
        conn.execute(
            "UPDATE slurm_submissions SET registered_worker_id=?, state='registered'"
            " WHERE slurm_job_id=?",
            (worker_id, str(info["slurm_job_id"])),
        )


def get_worker(conn: sqlite3.Connection, worker_id: str) -> Optional[Dict[str, Any]]:
    """One worker row as a dict, or None."""
    row = conn.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
    return dict(row) if row else None


def mark_worker_dead(conn: sqlite3.Connection, worker_id: str, error: Optional[str]) -> None:
    """Mark a worker dead (missing timeout / deregister), recording the reason."""
    conn.execute(
        "UPDATE workers SET status=?, last_error=? WHERE worker_id=?",
        (W_DEAD, error, worker_id),
    )


def flush_heartbeats(conn: sqlite3.Connection, liveness: Dict[str, float]) -> None:
    """Persist the in-memory liveness map in one batched write (spec §4.1)."""
    for worker_id, ts in liveness.items():
        conn.execute(
            "UPDATE workers SET last_heartbeat=?, status=? WHERE worker_id=? AND status != ?",
            (ts, W_ALIVE, worker_id, W_DEAD),
        )


def list_workers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """All worker rows for the dashboard."""
    rows = conn.execute("SELECT * FROM workers ORDER BY registered_at DESC").fetchall()
    return [dict(row) for row in rows]


def alive_workers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Workers not marked dead (seed for the restart liveness grace window)."""
    rows = conn.execute("SELECT * FROM workers WHERE status != ?", (W_DEAD,)).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------- slurm submissions


def add_submission(conn: sqlite3.Connection, slurm_job_id: str, mode: str) -> None:
    """Record an autoscaler sbatch submission."""
    conn.execute(
        "INSERT OR IGNORE INTO slurm_submissions(slurm_job_id, mode, submitted_at, state)"
        " VALUES(?,?,?,?)",
        (slurm_job_id, mode, time.time(), "submitted"),
    )


def update_submission_states(conn: sqlite3.Connection, states: Dict[str, str]) -> None:
    """Apply squeue-derived states ({slurm_job_id: state}) to tracked submissions."""
    for job_id, state in states.items():
        conn.execute(
            "UPDATE slurm_submissions SET state=? WHERE slurm_job_id=? AND state != 'registered'",
            (state, job_id),
        )


def open_submissions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Submissions that still count toward the fleet (not ended/cancelled)."""
    rows = conn.execute(
        "SELECT * FROM slurm_submissions WHERE state IN ('submitted','queued','running')"
    ).fetchall()
    return [dict(row) for row in rows]


def submission_state_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """{state: count} across all tracked Slurm submissions (autoscaler dashboard)."""
    rows = conn.execute(
        "SELECT state, COUNT(*) AS c FROM slurm_submissions GROUP BY state"
    ).fetchall()
    return {row["state"]: row["c"] for row in rows}


def recent_submissions(conn: sqlite3.Connection, limit: int = 100) -> List[Dict[str, Any]]:
    """Most recent Slurm submissions, newest first (autoscaler dashboard table)."""
    rows = conn.execute(
        "SELECT * FROM slurm_submissions ORDER BY submitted_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def unregistered_deaths(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Submissions that reached 'ended' without ever registering a worker.

    These are workers Slurm launched (or rejected) that died before signing up — a
    bad venv, an import error, an OOM at startup. The autoscaler surfaces each on the
    error page (with its Slurm log tail) and then marks it 'died' so it is not
    re-reported on the next tick.
    """
    rows = conn.execute(
        "SELECT * FROM slurm_submissions WHERE state='ended' AND registered_worker_id IS NULL"
    ).fetchall()
    return [dict(row) for row in rows]


def mark_submission_died(conn: sqlite3.Connection, slurm_job_id: str) -> None:
    """Terminal 'died' state for a submission already surfaced as a startup death."""
    conn.execute(
        "UPDATE slurm_submissions SET state='died' WHERE slurm_job_id=?", (slurm_job_id,)
    )


# ------------------------------------------------------------------------- errors


def record_error(conn: sqlite3.Connection, error: Dict[str, Any]) -> None:
    """Persist one error with its full traceback in the durable core DB (§4.7 extension).

    Errors live here (not the disposable logging DB) so they survive a telemetry purge
    and a server restart — they are the debugging record of record.
    """
    conn.execute(
        "INSERT INTO errors(ts, source, worker_id, job_id, host, location, error_type,"
        " message, traceback) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            error.get("ts") or time.time(), error.get("source"), error.get("worker_id"),
            error.get("job_id"), error.get("host"), error.get("location"),
            error.get("error_type"), error.get("message"), error.get("traceback"),
        ),
    )


def list_errors(
    conn: sqlite3.Connection,
    source: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Error records, newest first, filterable by source and time (dashboard)."""
    clauses: List[str] = []
    params: Tuple[Any, ...] = ()
    if source:
        clauses.append("source=?")
        params += (source,)
    if since is not None:
        clauses.append("ts>=?")
        params += (since,)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM errors {where} ORDER BY id DESC LIMIT ?", params + (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def error_summary(conn: sqlite3.Connection, recent_s: float = 3600.0) -> Dict[str, Any]:
    """Totals, per-source and per-type counts, recent rate for the error page."""
    total = conn.execute("SELECT COUNT(*) AS c FROM errors").fetchone()["c"]
    by_source = {
        row["source"] or "?": row["c"]
        for row in conn.execute(
            "SELECT source, COUNT(*) AS c FROM errors GROUP BY source"
        ).fetchall()
    }
    by_type = {
        row["error_type"] or "?": row["c"]
        for row in conn.execute(
            "SELECT error_type, COUNT(*) AS c FROM errors GROUP BY error_type ORDER BY c DESC LIMIT 10"
        ).fetchall()
    }
    recent = conn.execute(
        "SELECT COUNT(*) AS c FROM errors WHERE ts>=?", (time.time() - recent_s,)
    ).fetchone()["c"]
    last = conn.execute("SELECT ts FROM errors ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "total": total, "by_source": by_source, "by_type": by_type,
        "recent": recent, "recent_window_s": recent_s,
        "last_ts": last["ts"] if last else None,
    }


def clear_errors(conn: sqlite3.Connection) -> int:
    """Delete every recorded error (admin). Returns rows removed."""
    return conn.execute("DELETE FROM errors").rowcount


def trim_errors(conn: sqlite3.Connection, keep: int) -> int:
    """Bound growth: keep only the newest ``keep`` errors. Returns rows removed."""
    cur = conn.execute(
        "DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT ?)",
        (keep,),
    )
    return cur.rowcount
