"""Core-DB tests for the REST HPC harness (scripts/hpc_harness): fencing & lifecycle.

Covers spec §5.1 (attempt fencing, idempotent lease/report), §6.1 (batch-scoped
dedup), and the retry/dead/requeue transitions. Pure SQLite — no server, no network.
"""

import pytest

from hpc_harness import db

pytestmark = pytest.mark.base

MAX_ATTEMPTS = 4  # max_retries=3 -> up to 4 runs


@pytest.fixture(name="conn")
def conn_fixture(tmp_path):
    """A fresh core DB."""
    connection = db.connect(str(tmp_path / "tasks.db"))
    yield connection
    connection.close()


def _submit(conn, n=3, batch="b1", runner="hisim", priority=0):
    jobs = [
        {"payload": {"scenario": f"s{i}.json"}, "dedup_key": f"s{i}.json", "label": f"s{i}",
         "priority": priority}
        for i in range(n)
    ]
    return db.insert_jobs(conn, runner, jobs, batch)


def test_dedup_within_batch_and_fresh_in_new_batch(conn):
    """Dedup is batch-scoped: same keys re-inserted in a new batch are fresh (§6.1)."""
    first = _submit(conn, 3, batch="b1")
    assert first["inserted"] == 3
    again = _submit(conn, 3, batch="b1")
    assert again["inserted"] == 0 and again["skipped"] == 3
    other_batch = _submit(conn, 3, batch="b2")
    assert other_batch["inserted"] == 3
    assert db.counts(conn)["total"] == 6


def test_lease_orders_by_priority_then_id(conn):
    """Leasing hands out higher-priority jobs first, then by insertion id."""
    _submit(conn, 2, priority=0)
    db.insert_jobs(conn, "hisim", [{"payload": {}, "priority": 5, "label": "urgent"}], "b1")
    leased = db.lease_tasks(conn, "w1", 2, "lease-a")
    assert leased[0]["label"] == "urgent"
    assert leased[1]["label"] == "s0"


def test_lease_replay_returns_same_set_without_double_increment(conn):
    """Replaying a lease id returns the same jobs without bumping the attempt (§5.1)."""
    _submit(conn, 5)
    first = db.lease_tasks(conn, "w1", 2, "lease-a")
    replay = db.lease_tasks(conn, "w1", 2, "lease-a")
    assert [j["id"] for j in first] == [j["id"] for j in replay]
    assert all(j["attempt"] == 1 for j in replay)  # not incremented again
    assert db.counts(conn)[db.LEASED] == 2


def test_report_done_requires_matching_worker_and_attempt(conn):
    """A done report is accepted only from the leasing worker and matching attempt (§5.1)."""
    _submit(conn, 1)
    (job,) = db.lease_tasks(conn, "w1", 1, "l1")
    report = {"id": job["id"], "attempt": job["attempt"], "status": db.DONE, "peak_mem_mb": 100}

    accepted, reason = db.record_report(conn, "other-worker", report, MAX_ATTEMPTS)
    assert (accepted, reason) == (False, "stale")

    accepted, reason = db.record_report(conn, "w1", {**report, "attempt": 99}, MAX_ATTEMPTS)
    assert (accepted, reason) == (False, "stale")

    accepted, reason = db.record_report(conn, "w1", report, MAX_ATTEMPTS)
    assert accepted and reason is None
    assert db.counts(conn)[db.DONE] == 1


def test_duplicate_report_replay_is_absorbed(conn):
    """A replayed report is absorbed as a duplicate without a second attempt row."""
    _submit(conn, 1)
    (job,) = db.lease_tasks(conn, "w1", 1, "l1")
    report = {"id": job["id"], "attempt": job["attempt"], "status": db.DONE}
    assert db.record_report(conn, "w1", report, MAX_ATTEMPTS) == (True, None)
    assert db.record_report(conn, "w1", report, MAX_ATTEMPTS) == (True, "duplicate")
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM attempts WHERE task_id=?", (job["id"],)
    ).fetchone()
    assert rows["c"] == 1  # UNIQUE(task_id, attempt_no) held


def test_late_report_after_requeue_is_stale_and_new_attempt_wins(conn):
    """A late report from a revoked lease is rejected; the re-lease's next attempt completes."""
    _submit(conn, 1)
    (job,) = db.lease_tasks(conn, "w1", 1, "l1")
    assert db.requeue_task(conn, job["id"], MAX_ATTEMPTS, "orphaned")
    # Late report from the revoked lease: rejected as duplicate-or-stale, state intact.
    accepted, reason = db.record_report(
        conn, "w1", {"id": job["id"], "attempt": 1, "status": db.DONE}, MAX_ATTEMPTS
    )
    assert reason in ("stale", "duplicate")
    assert db.counts(conn)[db.PENDING] == 1
    # The re-lease gets attempt 2 and can complete normally.
    (retry,) = db.lease_tasks(conn, "w2", 1, "l2")
    assert retry["attempt"] == 2
    accepted, _ = db.record_report(
        conn, "w2", {"id": retry["id"], "attempt": 2, "status": db.DONE}, MAX_ATTEMPTS
    )
    assert accepted
    assert db.counts(conn)[db.DONE] == 1


def test_job_goes_dead_after_max_attempts(conn):
    """A job repeatedly failing reaches DEAD once max attempts are exhausted."""
    _submit(conn, 1)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        (job,) = db.lease_tasks(conn, "w1", 1, f"l{attempt}")
        assert job["attempt"] == attempt
        accepted, _ = db.record_report(
            conn, "w1",
            {"id": job["id"], "attempt": attempt, "status": db.FAILED, "error": "boom"},
            MAX_ATTEMPTS,
        )
        assert accepted
    counts = db.counts(conn)
    assert counts.get(db.DEAD) == 1
    assert counts.get(db.PENDING, 0) == 0


def test_reset_stale_leases_requeues(conn):
    """A lease older than the timeout is requeued back to PENDING."""
    _submit(conn, 1)
    db.lease_tasks(conn, "w1", 1, "l1")
    assert db.reset_stale_leases(conn, lease_timeout_s=-1.0, max_attempts=MAX_ATTEMPTS) == 1
    assert db.counts(conn)[db.PENDING] == 1


def test_requeue_worker_leases_only_hits_that_worker(conn):
    """Requeuing one worker's leases leaves other workers' leases untouched."""
    _submit(conn, 4)
    db.lease_tasks(conn, "w1", 2, "l1")
    db.lease_tasks(conn, "w2", 2, "l2")
    assert db.requeue_worker_leases(conn, "w1", MAX_ATTEMPTS, "worker missing") == 2
    counts = db.counts(conn)
    assert counts[db.PENDING] == 2 and counts[db.LEASED] == 2


def test_cancel_leased_job_reports_holder(conn):
    """Cancelling a leased job reports its holder; done/dead jobs are not cancellable."""
    _submit(conn, 1)
    (job,) = db.lease_tasks(conn, "w1", 1, "l1")
    result = db.cancel_task(conn, job["id"])
    assert result["ok"] and result["leased_by"] == "w1" and result["attempt"] == 1
    assert db.counts(conn)[db.CANCELLED] == 1
    # done/dead jobs are not cancellable
    assert not db.cancel_task(conn, job["id"])["ok"]


def test_assume_fleet_dead_recovery_requeues_everything(conn):
    """Cold-start recovery requeues every leased job back to PENDING."""
    _submit(conn, 3)
    db.lease_tasks(conn, "w1", 3, "l1")
    assert db.assume_fleet_dead_recovery(conn, MAX_ATTEMPTS) == 3
    assert db.counts(conn)[db.PENDING] == 3


def test_reset_failed_revives_dead_and_cancelled(conn):
    """Resetting failed jobs revives both DEAD and CANCELLED tasks to PENDING."""
    _submit(conn, 2)
    (job,) = db.lease_tasks(conn, "w1", 1, "l1")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        db.record_report(
            conn, "w1", {"id": job["id"], "attempt": attempt, "status": db.FAILED}, MAX_ATTEMPTS
        )
        if attempt < MAX_ATTEMPTS:
            db.lease_tasks(conn, "w1", 1, f"l{attempt + 1}")
    (other,) = list(db.list_jobs(conn, db.PENDING))
    db.cancel_task(conn, other["id"])
    assert db.reset(conn, failed=True) == 2
    assert db.counts(conn)[db.PENDING] == 2


def test_errors_record_list_summary_trim_clear(conn):
    """Error records round-trip through summary, newest-first listing, source filter, trim and clear."""
    for i in range(5):
        db.record_error(conn, {
            "source": "worker" if i % 2 else "server",
            "worker_id": f"w{i}",
            "job_id": i,
            "error_type": "ValueError" if i % 2 else "RuntimeError",
            "message": f"boom {i}",
            "traceback": f"Traceback...\nline {i}",
        })
    summary = db.error_summary(conn)
    assert summary["total"] == 5
    assert summary["by_source"] == {"server": 3, "worker": 2}
    assert summary["by_type"]["RuntimeError"] == 3
    assert summary["recent"] == 5 and summary["last_ts"] is not None

    newest_first = db.list_errors(conn, limit=10)
    assert newest_first[0]["message"] == "boom 4"  # newest first
    assert newest_first[0]["traceback"].startswith("Traceback")
    assert [e["message"] for e in db.list_errors(conn, source="worker")] == ["boom 3", "boom 1"]

    assert db.trim_errors(conn, keep=2) == 3
    assert db.error_summary(conn)["total"] == 2
    assert db.clear_errors(conn) == 2
    assert db.error_summary(conn)["total"] == 0


def test_errors_persist_survives_reopen(tmp_path):
    """Errors live in the durable core DB, so a reopened connection still sees them (§4.7)."""
    path = str(tmp_path / "core.db")
    conn = db.connect(path)
    db.record_error(conn, {"source": "server", "message": "durable", "traceback": "tb"})
    conn.commit()
    conn.close()
    # Errors live in the core DB, so a fresh connection still sees them.
    reopened = db.connect(path)
    assert db.error_summary(reopened)["total"] == 1
    reopened.close()


def test_lease_filters_by_runner(conn):
    """Leasing with a runner filter hands out only jobs for that runner."""
    db.insert_jobs(conn, "hisim", [{"payload": {}, "label": "json-job"}], "b1")
    db.insert_jobs(conn, "hisim_setup", [{"payload": {}, "label": "setup-job"}], "b1")
    leased = db.lease_tasks(conn, "w1", 5, "l1", runner="hisim_setup")
    assert [j["label"] for j in leased] == ["setup-job"]
    assert db.counts(conn)[db.PENDING] == 1  # the hisim job stays for a hisim worker


def test_success_file_override_travels_with_lease(conn):
    """A per-job success_file override (incl. explicit None) is carried on the lease."""
    db.insert_jobs(
        conn, "hisim",
        [{"payload": {}, "success_file": None},  # explicit: exit-code-only success
         {"payload": {}}],  # server default applies
        "b1",
    )
    leased = db.lease_tasks(conn, "w1", 2, "l1")
    overridden = [j for j in leased if j["success_file_set"]]
    defaulted = [j for j in leased if not j["success_file_set"]]
    assert len(overridden) == 1 and overridden[0]["success_file"] is None
    assert len(defaulted) == 1
