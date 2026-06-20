"""SQLite task database for the HiSim MPI HPC harness.

The database is the durable source of truth for the whole run. Only the head rank
(rank 0) ever opens or writes it, so single-writer SQLite is safe even on a shared
networked filesystem (we deliberately avoid WAL mode, whose shared-memory file is
unreliable over NFS/Lustre/GPFS).

Task lifecycle::

    pending --lease--> leased --report(done)--> done
                          |  \\--report(fail, attempts<max)--> pending (retry)
                          |   \\-report(fail, attempts>=max)--> dead
                          \\--lease timeout / restart--------> pending or dead
"""

import glob
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

# Task status values.
PENDING = "pending"
LEASED = "leased"
DONE = "done"
FAILED = "failed"
DEAD = "dead"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY,
    scenario_path TEXT UNIQUE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    leased_by     TEXT,
    leased_at     REAL,
    started_at    REAL,
    finished_at   REAL,
    duration_s    REAL,
    peak_mem_mb   REAL,
    exit_code     INTEGER,
    result_dir    TEXT,
    error         TEXT,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL,
    attempt_no  INTEGER,
    host        TEXT,
    started_at  REAL,
    finished_at REAL,
    duration_s  REAL,
    peak_mem_mb REAL,
    exit_code   INTEGER,
    status      TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the task database with the schema applied."""
    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE;")   # rollback journal, not WAL (shared-FS safe)
    conn.execute("PRAGMA synchronous=FULL;")        # durability of leases/reports across crashes
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store a provenance key/value pair."""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def import_scenarios(conn: sqlite3.Connection, scenario_dir: str, pattern: str = "*.json") -> Dict[str, int]:
    """Scan ``scenario_dir`` for files matching ``pattern`` and insert them as pending tasks.

    Idempotent: ``scenario_path`` is UNIQUE, so re-scanning only adds new files.
    Returns counts of files seen and newly inserted.
    """
    paths = sorted(str(Path(p).resolve()) for p in glob.glob(os.path.join(scenario_dir, pattern)))
    now = time.time()
    inserted = 0
    for path in paths:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tasks(scenario_path, status, updated_at) VALUES(?, ?, ?)",
            (path, PENDING, now),
        )
        inserted += cur.rowcount
    conn.commit()
    return {"found": len(paths), "inserted": inserted}


def lease_tasks(conn: sqlite3.Connection, n: int, leased_by: str) -> List[Dict[str, Any]]:
    """Atomically lease up to ``n`` pending tasks to ``leased_by``.

    Increments ``attempts`` and marks them ``leased``. The caller is responsible
    for committing (so leasing several batches can share a transaction).
    """
    if n <= 0:
        return []
    now = time.time()
    rows = conn.execute(
        "SELECT id, scenario_path FROM tasks WHERE status = ? ORDER BY id LIMIT ?",
        (PENDING, n),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE tasks SET status=?, attempts=attempts+1, leased_by=?, leased_at=?, "
            "started_at=?, updated_at=? WHERE id=?",
            (LEASED, leased_by, now, now, now, row["id"]),
        )
    return [{"id": r["id"], "scenario_path": r["scenario_path"]} for r in rows]


def record_report(conn: sqlite3.Connection, report: Dict[str, Any], max_attempts: int) -> None:
    """Apply a finished-task report from an agent. Caller commits.

    On success the task becomes ``done``. On failure it is requeued to ``pending``
    if it still has attempts left, otherwise marked ``dead``.
    """
    task_id = report["id"]
    row = conn.execute("SELECT attempts FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return
    attempts = row["attempts"]
    now = time.time()
    status = report["status"]

    conn.execute(
        "INSERT INTO attempts(task_id, attempt_no, host, started_at, finished_at, "
        "duration_s, peak_mem_mb, exit_code, status, error) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            task_id, attempts, report.get("host"), report.get("started_at"),
            report.get("finished_at"), report.get("duration_s"), report.get("peak_mem_mb"),
            report.get("exit_code"), status, report.get("error"),
        ),
    )

    if status == DONE:
        conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, duration_s=?, peak_mem_mb=?, "
            "exit_code=?, result_dir=?, error=NULL, leased_by=NULL, updated_at=? WHERE id=?",
            (DONE, now, report.get("duration_s"), report.get("peak_mem_mb"),
             report.get("exit_code"), report.get("result_dir"), now, task_id),
        )
    elif attempts < max_attempts:
        # Retry: back to the pending pool, keeping the last failure info for debugging.
        conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, leased_at=NULL, exit_code=?, "
            "error=?, peak_mem_mb=?, duration_s=?, updated_at=? WHERE id=?",
            (PENDING, report.get("exit_code"), report.get("error"),
             report.get("peak_mem_mb"), report.get("duration_s"), now, task_id),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status=?, finished_at=?, exit_code=?, error=?, peak_mem_mb=?, "
            "duration_s=?, result_dir=?, leased_by=NULL, updated_at=? WHERE id=?",
            (DEAD, now, report.get("exit_code"), report.get("error"),
             report.get("peak_mem_mb"), report.get("duration_s"), report.get("result_dir"),
             now, task_id),
        )


def reset_stale_leases(conn: sqlite3.Connection, lease_timeout_s: float, max_attempts: int) -> int:
    """Reclaim tasks leased longer ago than ``lease_timeout_s`` with no report.

    Guards against reports lost in flight. Returns the number reclaimed. Caller commits.
    """
    cutoff = time.time() - lease_timeout_s
    rows = conn.execute(
        "SELECT id, attempts FROM tasks WHERE status=? AND leased_at < ?", (LEASED, cutoff)
    ).fetchall()
    now = time.time()
    for row in rows:
        if row["attempts"] < max_attempts:
            conn.execute(
                "UPDATE tasks SET status=?, leased_by=NULL, leased_at=NULL, "
                "error=?, updated_at=? WHERE id=?",
                (PENDING, "lease timed out, reclaimed", now, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
                (DEAD, "lease timed out and attempts exhausted", now, row["id"]),
            )
    return len(rows)


def startup_recovery(conn: sqlite3.Connection, max_attempts: int) -> int:
    """At ``run`` start, requeue any tasks left ``leased`` by a previous (crashed) run.

    No agent is alive to hold those leases, so they are returned to ``pending``
    (or marked ``dead`` if out of attempts). Returns the number affected.
    """
    rows = conn.execute("SELECT id, attempts FROM tasks WHERE status=?", (LEASED,)).fetchall()
    now = time.time()
    for row in rows:
        if row["attempts"] < max_attempts:
            conn.execute(
                "UPDATE tasks SET status=?, leased_by=NULL, leased_at=NULL, updated_at=? WHERE id=?",
                (PENDING, now, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status=?, error=?, updated_at=? WHERE id=?",
                (DEAD, "attempts exhausted before restart", now, row["id"]),
            )
    conn.commit()
    return len(rows)


def reset(conn: sqlite3.Connection, leased: bool = False, failed: bool = False) -> int:
    """Manually requeue tasks. ``leased`` resets stuck leases; ``failed`` revives
    failed/dead tasks (clearing their attempt count). Returns rows affected."""
    now = time.time()
    affected = 0
    if leased:
        cur = conn.execute(
            "UPDATE tasks SET status=?, leased_by=NULL, leased_at=NULL, updated_at=? WHERE status=?",
            (PENDING, now, LEASED),
        )
        affected += cur.rowcount
    if failed:
        cur = conn.execute(
            "UPDATE tasks SET status=?, attempts=0, leased_by=NULL, leased_at=NULL, "
            "error=NULL, updated_at=? WHERE status IN (?, ?)",
            (PENDING, now, FAILED, DEAD),
        )
        affected += cur.rowcount
    conn.commit()
    return affected


def counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Return a {status: count} dict plus a ``total`` key."""
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status").fetchall()
    result = {row["status"]: row["c"] for row in rows}
    result["total"] = sum(row["c"] for row in rows)
    return result


def is_drained(conn: sqlite3.Connection) -> bool:
    """True when no tasks are pending and none are still leased (run is finished)."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE status IN (?, ?)", (PENDING, LEASED)
    ).fetchone()
    return row["c"] == 0
