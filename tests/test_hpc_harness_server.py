"""End-to-end server API tests (FastAPI TestClient, no network, no cluster).

Exercises the full lease -> report -> heartbeat -> reconcile cycle of spec §7/§5.1,
including auth, fencing, kill directives, reregister, drain/release, the circuit
breaker, memory auto-raise propagation, the logs/console round trip, and warm-restart
recovery without blind requeue.
"""

import pytest
from fastapi.testclient import TestClient

from hpc_harness import db
from hpc_harness.config import ServerConfig
from hpc_harness.server.app import create_app
from hpc_harness.server.autoscaler import Autoscaler
from hpc_harness.server.service import HarnessService

pytestmark = pytest.mark.base

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
API = "/api/v1"


def make_service(tmp_path, **overrides) -> HarnessService:
    """A service over temp storage with test-friendly thresholds."""
    cfg = ServerConfig(
        db_path=str(tmp_path / "tasks.db"),
        result_root=str(tmp_path / "results"),
        db_snapshot_path=str(tmp_path / "snapshot.db"),
        token=TOKEN,
        mem_min_samples=3,
        orphan_strikes=2,
    )
    cfg.circuit_breaker.min_samples = 4
    cfg.circuit_breaker.consecutive = 3
    cfg.circuit_breaker.window = 10
    for key, value in overrides.items():
        setattr(cfg, key, value)
    cfg.finalize()
    cfg.token = TOKEN  # finalize may pull env HARNESS_TOKEN; keep the test token
    service = HarnessService(cfg)
    service.startup()
    return service


@pytest.fixture(name="service")
def service_fixture(tmp_path):
    """A started HarnessService over temp storage, shut down after the test."""
    service = make_service(tmp_path)
    yield service
    service.shutdown()


@pytest.fixture(name="client")
def client_fixture(service):
    """A FastAPI TestClient wrapping the service's app."""
    return TestClient(create_app(service))


def submit(client, n=3, batch="b1"):
    """Submit ``n`` deduped jobs to a batch and return the response JSON."""
    jobs = [{"payload": {"scenario": f"s{i}.json"}, "label": f"s{i}", "dedup_key": f"s{i}"}
            for i in range(n)]
    response = client.post(f"{API}/jobs", json={"runner": "hisim", "batch": batch, "jobs": jobs},
                           headers=AUTH)
    assert response.status_code == 200
    return response.json()


def register(client, mode="whole_node"):
    """Register a worker and return the response JSON (incl. its worker_id)."""
    response = client.post(
        f"{API}/workers/register",
        json={"host": "node1", "mode": mode, "slots": 4, "cores": 8, "total_mem_gb": 64,
              "runner": "hisim"},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def lease(client, worker_id, n=1, lease_id="lease-1"):
    """Lease up to ``n`` jobs for a worker under a lease id and return the response JSON."""
    response = client.post(
        f"{API}/lease",
        json={"worker_id": worker_id, "num_slots": n, "lease_id": lease_id},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def report(client, worker_id, job, status="done", **extra):
    """Report a job outcome for a worker and return the single result record."""
    body = {"id": job["id"], "attempt": job["attempt"], "status": status,
            "exit_code": 0 if status == "done" else 1, "duration_s": 5.0,
            "peak_mem_mb": extra.pop("peak_mem_mb", 1000.0),
            "result_dir": job.get("result_dir"), **extra}
    response = client.post(f"{API}/report", json={"worker_id": worker_id, "reports": [body]},
                           headers=AUTH)
    assert response.status_code == 200
    return response.json()["results"][0]


def heartbeat(client, worker_id, running=()):
    """Send a worker heartbeat with the given running job ids and return the response JSON."""
    response = client.post(
        f"{API}/heartbeat",
        json={"worker_id": worker_id, "metrics": {"cpu_percent": 10.0}, "running": list(running)},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------------- auth


def test_mutations_need_token_reads_are_open(client):
    """Mutating routes require the bearer token; reads and the dashboard are open."""
    assert client.post(f"{API}/jobs", json={"runner": "x", "jobs": []}).status_code == 401
    assert client.post(f"{API}/lease", json={}).status_code == 401
    assert client.get(f"{API}/status").status_code == 200
    assert client.get(f"{API}/jobs").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert "<title>HPC Harness</title>" in client.get("/").text


def test_dashboard_pages_render_and_link_each_other(client):
    """Each dashboard page renders with its title and the shared cross-links."""
    for path, title in [("/", "HPC Harness"),
                        ("/errors", "Errors"),
                        ("/autoscaler", "Autoscaler"),
                        ("/settings", "Settings")]:
        page = client.get(path)
        assert page.status_code == 200
        assert title in page.text
        # every page carries the shared nav linking to the others
        assert 'href="/errors"' in page.text
        assert 'href="/autoscaler"' in page.text and 'href="/settings"' in page.text


def test_config_endpoint_exposes_settings_with_redacted_token(client):
    """The config endpoint exposes settings (incl. nested blocks) with the token redacted."""
    cfg = client.get(f"{API}/config").json()
    assert cfg["result_root"] and cfg["max_retries"] == 3
    assert cfg["max_attempts"] == 4
    assert cfg["token"].startswith("•") and TOKEN not in cfg["token"]
    # nested config objects come through for the settings page grouping
    assert cfg["circuit_breaker"]["consecutive"] == 3
    assert cfg["autoscale"]["standby_floor"] == 10
    assert cfg["effective_per_job_mem_gb"] == 10.0


def test_autoscale_endpoint_reports_disabled_by_default(client):
    """With no autoscaler wired, the autoscale endpoint reports disabled but still lists submissions."""
    a = client.get(f"{API}/autoscale").json()
    assert a["enabled"] is False and a["trying_to_scale"] is False
    assert a["worker_mode"] == "single_core"
    assert "submission_state_counts" in a and "submissions" in a


def test_clients_report_errors_and_they_are_queryable(client):
    """Errors reported by a client are aggregated in the summary and listed on the error page."""
    worker_id = register(client)["worker_id"]
    body = {"worker_id": worker_id, "errors": [
        {"source": "worker", "job_id": 42, "error_type": "ValueError",
         "message": "bad payload", "traceback": "Traceback (most recent call last): ..."},
        {"source": "worker", "error_type": "RuntimeError", "message": "child died"},
    ]}
    assert client.post(f"{API}/errors", json=body, headers=AUTH).status_code == 200

    summary = client.get(f"{API}/errors/summary").json()
    assert summary["total"] == 2 and summary["by_source"]["worker"] == 2
    assert summary["by_type"]["ValueError"] == 1

    rows = client.get(f"{API}/errors?source=worker").json()
    assert len(rows) == 2
    top = rows[0]  # newest first
    assert top["worker_id"] == worker_id  # server stamps the worker id
    assert top["host"] == "node1"          # ... and its host
    assert any(r["job_id"] == 42 and r["traceback"] for r in rows)

    # reads are open; reporting/clearing need the token
    assert client.post(f"{API}/errors", json=body).status_code == 401
    assert client.post(f"{API}/admin/errors/clear", headers=AUTH).json()["cleared"] == 2
    assert client.get(f"{API}/errors/summary").json()["total"] == 0


def test_server_catches_and_persists_its_own_exceptions(service):
    """An unhandled route exception is caught, returns 500, and is stored with a traceback."""
    def boom():
        raise RuntimeError("kaboom in status")

    service.status = boom  # force a route to raise
    crash_client = TestClient(create_app(service), raise_server_exceptions=False)
    response = crash_client.get(f"{API}/status")
    assert response.status_code == 500

    errors = crash_client.get(f"{API}/errors?source=server").json()
    hit = [e for e in errors if "kaboom" in (e.get("message") or "")]
    assert hit, "server exception was not persisted"
    assert hit[0]["error_type"] == "RuntimeError"
    assert "Traceback" in (hit[0]["traceback"] or "")
    assert hit[0]["location"] == "GET /api/v1/status"


def test_autoscaler_tick_populates_status_snapshot(service, client):
    """One tick submits for the backlog, then the next reports steady once the fleet covers it."""
    service.autoscaler = Autoscaler(
        service, service.cfg.autoscale,
        probe_fn=lambda: 50,
        sbatch_fn=lambda n: [f"job{i}" for i in range(n)],
        squeue_fn=lambda ids: {i: "queued" for i in ids},
        scancel_fn=lambda ids: None,
    )
    submit(client, 20)  # 20 pending jobs, 50 idle cores -> submit 20
    result = service.autoscaler.tick()
    assert result["submitted"] == 20

    a = client.get(f"{API}/autoscale").json()
    assert a["enabled"] and a["action"] == "scale_up"
    assert a["work"] == 20 and a["available_cores"] == 50 and a["to_submit"] == 20
    assert a["trying_to_scale"] is True
    assert a["submission_state_counts"].get("submitted") == 20  # squeue reclassifies next tick
    assert len(a["submissions"]) == 20

    # Second tick: squeue moves them to queued; fleet now covers the work -> steady.
    assert service.autoscaler.tick()["submitted"] == 0
    a2 = client.get(f"{API}/autoscale").json()
    assert a2["action"] == "steady" and a2["to_submit"] == 0
    assert a2["trying_to_scale"] is False
    assert a2["submission_state_counts"].get("queued") == 20
    assert a2["slurm_queued"] == 20


def test_autoscaler_sbatch_failure_is_surfaced_not_crashed(service, client):
    """A rejected sbatch must not crash the loop; it lands on the dashboard instead."""

    def failing_sbatch(_n):
        raise RuntimeError("sbatch failed: Invalid partition specified")

    service.autoscaler = Autoscaler(
        service, service.cfg.autoscale,
        probe_fn=lambda: 50, sbatch_fn=failing_sbatch,
        squeue_fn=lambda ids: {}, scancel_fn=lambda ids: None,
    )
    submit(client, 5)
    result = service.autoscaler.tick()  # must NOT raise
    assert result["submitted"] == 0

    a = client.get(f"{API}/autoscale").json()
    assert a["action"] == "sbatch_failed"
    assert "Invalid partition" in a["error"]
    assert a["submission_state_counts"] == {}  # nothing recorded


def test_autoscaler_surfaces_worker_that_died_before_registering(service, client, tmp_path):
    """A worker that ends without registering lands on the error page with its Slurm log tail."""
    log_dir = tmp_path / "slurm_logs"
    log_dir.mkdir()
    (log_dir / "worker-job0.out").write_text(
        "[2026-07-04 17:00:00] worker: === harness worker (single_core) starting ===\n"
        "ModuleNotFoundError: No module named 'hisim'\n"
    )
    service.cfg.autoscale.slurm_log_dir = str(log_dir)

    phase = {"squeue": "queued"}
    service.autoscaler = Autoscaler(
        service, service.cfg.autoscale,
        probe_fn=lambda: 50,
        sbatch_fn=lambda n: ["job0"],
        # Mimic default_squeue: ids missing from squeue are reported "ended".
        squeue_fn=lambda ids: {i: phase["squeue"] for i in ids},
        scancel_fn=lambda ids: None,
    )
    submit(client, 5)
    service.autoscaler.tick()  # records submission job0 (state 'submitted')

    phase["squeue"] = "ended"   # job0 vanished from squeue without ever registering
    service.autoscaler.tick()

    errors = [e for e in client.get(f"{API}/errors").json() if e["error_type"] == "WorkerStartupDeath"]
    assert len(errors) == 1
    assert "job0" in errors[0]["message"]
    assert "without ever registering" in errors[0]["message"]
    assert "ModuleNotFoundError" in errors[0]["traceback"]  # the real Slurm log tail

    # Reported exactly once, and the submission is now terminal 'died'.
    service.autoscaler.tick()
    again = [e for e in client.get(f"{API}/errors").json() if e["error_type"] == "WorkerStartupDeath"]
    assert len(again) == 1
    assert client.get(f"{API}/autoscale").json()["submission_state_counts"].get("died") == 1


def test_autoscaler_death_without_log_dir_still_surfaced(service, client):
    """Even with no slurm_log_dir configured, the death is surfaced (just without a log tail)."""
    phase = {"squeue": "queued"}
    service.autoscaler = Autoscaler(
        service, service.cfg.autoscale,
        probe_fn=lambda: 50, sbatch_fn=lambda n: ["jobX"],
        squeue_fn=lambda ids: {i: phase["squeue"] for i in ids},
        scancel_fn=lambda ids: None,
    )
    submit(client, 3)
    service.autoscaler.tick()
    phase["squeue"] = "ended"
    service.autoscaler.tick()

    errors = [e for e in client.get(f"{API}/errors").json() if e["error_type"] == "WorkerStartupDeath"]
    assert len(errors) == 1
    assert "slurm_log_dir is not set" in errors[0]["message"]


# ------------------------------------------------------------------- happy path


def test_full_cycle_submit_lease_report_done(client):
    """The full submit -> lease -> report -> done cycle updates counts and staging paths."""
    assert submit(client, 2)["inserted"] == 2
    worker_id = register(client)["worker_id"]
    leased = lease(client, worker_id, 2)
    assert len(leased["jobs"]) == 2 and not leased["drain"]
    job = leased["jobs"][0]
    assert job["attempt"] == 1
    assert ".staging" in job["staging_dir"] and job["staging_dir"].endswith(".attempt-1")
    assert job["result_dir"] != job["staging_dir"]
    assert job["success_file"] == "finished.flag"  # HiSim's real end-of-run marker

    assert report(client, worker_id, job)["accepted"]
    status = client.get(f"{API}/status").json()
    assert status["counts"]["done"] == 1 and status["counts"]["leased"] == 1


def test_lease_replay_same_lease_id(client):
    """Replaying a lease id returns the same jobs without leasing more."""
    submit(client, 3)
    worker_id = register(client)["worker_id"]
    first = lease(client, worker_id, 2, "L1")["jobs"]
    replay = lease(client, worker_id, 2, "L1")["jobs"]
    assert [j["id"] for j in first] == [j["id"] for j in replay]
    assert client.get(f"{API}/status").json()["counts"]["leased"] == 2


def test_stale_report_rejected_after_cancel(client):
    """A report after cancel is rejected as stale, and the holder gets a kill directive."""
    submit(client, 1)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    assert client.post(f"{API}/admin/jobs/{job['id']}/cancel", headers=AUTH).json()["ok"]
    result = report(client, worker_id, job)
    assert not result["accepted"] and result["reason"] == "stale"
    # ... and the holder gets a kill directive on its next heartbeat.
    directives = heartbeat(client, worker_id, [{"job_id": job["id"], "attempt": job["attempt"]}])
    assert {"job_id": job["id"], "attempt": job["attempt"]} in directives["kill"]


def test_duplicate_report_replay_is_accepted_idempotently(client):
    """A replayed report is accepted idempotently as a duplicate without double-counting."""
    submit(client, 1)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    assert report(client, worker_id, job)["accepted"]
    replay = report(client, worker_id, job)
    assert replay["accepted"] and replay["reason"] == "duplicate"
    assert client.get(f"{API}/status").json()["counts"]["done"] == 1


def test_unknown_worker_gets_reregister(client):
    """An unknown worker is told to re-register on both heartbeat and lease."""
    assert heartbeat(client, "ghost-worker") == {"reregister": True}
    assert lease(client, "ghost-worker").get("reregister")


def test_orphaned_lease_requeued_after_strikes(client, service):
    """A lease whose worker stops reporting it is requeued after the configured strikes."""
    submit(client, 1)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    heartbeat(client, worker_id, running=[])  # strike 1 (just-leased grace)
    assert client.get(f"{API}/status").json()["counts"]["leased"] == 1
    heartbeat(client, worker_id, running=[])  # strike 2 -> requeue
    counts = client.get(f"{API}/status").json()["counts"]
    assert counts["pending"] == 1 and counts.get("leased", 0) == 0
    del job, service


def test_drain_and_release_directives(client, service):
    """An idle worker is released while work remains, and everyone drains once the queue empties."""
    submit(client, 1)
    worker_a = register(client)["worker_id"]
    worker_b = register(client)["worker_id"]
    (job,) = lease(client, worker_a)["jobs"]
    # B is idle, pending==0 while A still works -> release B, don't drain.
    directives_b = heartbeat(client, worker_b)
    assert directives_b.get("release") and not directives_b.get("drain")
    # A finishes -> queue fully drained -> drain everyone.
    report(client, worker_a, job)
    assert heartbeat(client, worker_a).get("drain")
    empty = lease(client, worker_a, lease_id="L2")
    assert empty["jobs"] == [] and empty["drain"]
    del service


def test_missing_worker_reaped_and_resurrection_cleans_up(client, service):
    """A silent worker is reaped and requeued; on resurrection it re-registers and its stale job is killed."""
    submit(client, 1)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    service.liveness[worker_id] = 0.0  # heartbeat "15 min" ago
    swept = service.reap()
    assert swept["missing"] == 1
    assert client.get(f"{API}/status").json()["counts"]["pending"] == 1
    # The resurrected worker is told to re-register; after re-registering, its stale
    # running job draws a kill directive.
    assert heartbeat(client, worker_id) == {"reregister": True}
    new_id = register(client)["worker_id"]
    directives = heartbeat(client, new_id, [{"job_id": job["id"], "attempt": job["attempt"]}])
    assert directives["kill"] == [{"job_id": job["id"], "attempt": job["attempt"]}]


def test_circuit_breaker_pauses_and_resume_clears(client):
    """A failure storm trips the breaker (pausing leasing); admin resume clears it."""
    submit(client, 8)
    worker_id = register(client)["worker_id"]
    jobs = lease(client, worker_id, 4)["jobs"]
    for job in jobs[:3]:
        report(client, worker_id, job, status="failed", error="ModuleNotFoundError: boom")
    status = client.get(f"{API}/status").json()
    assert "circuit breaker" in status["paused"]
    assert "boom" in status["circuit_breaker"]["top_error"]
    paused = lease(client, worker_id, 1, "L-paused")
    assert paused["jobs"] == [] and paused.get("paused")
    assert client.post(f"{API}/admin/resume", headers=AUTH).json()["ok"]
    assert "paused" not in client.get(f"{API}/status").json()
    assert lease(client, worker_id, 1, "L-after")["jobs"]


def test_mem_autoraise_pushes_set_directive(client, service):
    """A memory auto-raise (and later manual lowering) propagates once via a heartbeat set directive."""
    submit(client, 4)
    worker_id = register(client)["worker_id"]
    jobs = lease(client, worker_id, 3)["jobs"]
    for job in jobs:
        report(client, worker_id, job, peak_mem_mb=12 * 1024.0)  # 12 GB > 10 GB budget
    assert service.membudget.effective == pytest.approx(13.0)
    directives = heartbeat(client, worker_id)
    assert directives["set"]["per_job_mem_gb"] == pytest.approx(13.0)
    assert "set" not in heartbeat(client, worker_id)  # sent once until it changes again
    # Manual lowering via admin config propagates the same way.
    response = client.post(f"{API}/admin/config", json={"per_job_mem_gb": 8.0}, headers=AUTH)
    assert response.json()["applied"]["per_job_mem_gb"] == 8.0
    assert heartbeat(client, worker_id)["set"]["per_job_mem_gb"] == 8.0


def test_logs_and_console_round_trip(client):
    """Shipped logs are queryable, and the console capture directive round-trips (one-shot and follow)."""
    worker_id = register(client)["worker_id"]
    response = client.post(
        f"{API}/logs",
        json={"worker_id": worker_id,
              "records": [{"ts": 1.0, "level": "ERROR", "logger": "job", "job_id": 7,
                           "message": "kaputt", "traceback": "Traceback ..."}]},
        headers=AUTH,
    )
    assert response.status_code == 200
    rows = client.get(f"{API}/logs?worker={worker_id}&level=ERROR").json()
    assert rows and rows[0]["message"] == "kaputt" and rows[0]["job_id"] == 7

    # console: admin request -> directive on heartbeat -> upload -> GET
    assert client.get(f"{API}/workers/{worker_id}/console").status_code == 404
    client.post(f"{API}/admin/workers/{worker_id}/console", json={}, headers=AUTH)
    directives = heartbeat(client, worker_id)
    assert directives["capture_console"] == {"follow": False}
    assert "capture_console" not in heartbeat(client, worker_id)  # one-shot consumed
    client.post(f"{API}/workers/{worker_id}/console",
                json={"ts": 2.0, "text": "hello console", "next_offset": 13}, headers=AUTH)
    snap = client.get(f"{API}/workers/{worker_id}/console").json()
    assert snap["text"] == "hello console"

    # follow mode re-issues the directive every heartbeat until switched off
    client.post(f"{API}/admin/workers/{worker_id}/console", json={"follow": True}, headers=AUTH)
    assert heartbeat(client, worker_id)["capture_console"] == {"follow": True}
    assert heartbeat(client, worker_id)["capture_console"] == {"follow": True}
    client.post(f"{API}/admin/workers/{worker_id}/console", json={"follow": False}, headers=AUTH)
    assert "capture_console" not in heartbeat(client, worker_id)


def test_logging_db_purge_leaves_scheduling_intact(client, service):
    """Purging the logging DB frees it while leaving core scheduling state untouched."""
    submit(client, 2)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    freed = client.post(f"{API}/admin/logs/purge", headers=AUTH).json()
    assert freed["ok"]
    # Scheduling state is untouched; a fenced report still lands.
    assert report(client, worker_id, job)["accepted"]
    del service


def test_deregister_requeues_leases(client):
    """Deregistering a worker requeues its leases and marks it dead with the reason."""
    submit(client, 2)
    worker_id = register(client)["worker_id"]
    lease(client, worker_id, 2)
    response = client.post(f"{API}/workers/{worker_id}/deregister",
                           json={"reason": "file_access"}, headers=AUTH)
    assert response.json()["requeued"] == 2
    workers = client.get(f"{API}/workers").json()
    assert workers[0]["status"] == "dead" and workers[0]["last_error"] == "file_access"


def test_snapshot_writes_shared_fs_copy(client, service, tmp_path):
    """snapshot() writes a readable shared-FS copy of the core DB."""
    submit(client, 1)
    service.snapshot()
    snapshot_path = tmp_path / "snapshot.db"
    assert snapshot_path.exists()
    check = db.connect(str(snapshot_path))
    assert db.counts(check)["total"] == 1
    check.close()


def test_warm_restart_keeps_live_leases_cold_restart_requeues(tmp_path):
    """A warm restart preserves live leases (heartbeat re-confirms); a cold restart requeues them."""
    service = make_service(tmp_path)
    app_client = TestClient(create_app(service))
    submit(app_client, 2)
    worker_id = register(app_client)["worker_id"]
    (job,) = lease(app_client, worker_id)["jobs"]
    service.shutdown()

    # Warm restart (spec §8): the lease survives; the worker's heartbeat re-confirms it.
    service2 = make_service(tmp_path)
    client2 = TestClient(create_app(service2))
    assert client2.get(f"{API}/status").json()["counts"]["leased"] == 1
    directives = heartbeat(client2, worker_id, [{"job_id": job["id"], "attempt": job["attempt"]}])
    assert "kill" not in directives and "reregister" not in directives
    assert report(client2, worker_id, job)["accepted"]  # finishes normally: no duplicate run
    service2.shutdown()

    # Cold restart: --assume-fleet-dead requeues what is left.
    service3 = make_service(tmp_path)
    service3.writer.call(lambda c: db.lease_tasks(c, "w-gone", 1, "LX"))
    service3.shutdown()
    service4 = make_service(tmp_path)
    service4.startup(assume_fleet_dead=True)
    assert service4.writer.call(db.counts)[db.PENDING] == 1
    service4.shutdown()
