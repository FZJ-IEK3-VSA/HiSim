"""End-to-end server API tests (FastAPI TestClient, no network, no cluster).

Exercises the full lease -> report -> heartbeat -> reconcile cycle of spec §7/§5.1,
including auth, fencing, kill directives, reregister, drain/release, the circuit
breaker, memory auto-raise propagation, the logs/console round trip, and warm-restart
recovery without blind requeue.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fastapi.testclient import TestClient  # noqa: E402

from hpc_harness import db  # noqa: E402
from hpc_harness.config import ServerConfig  # noqa: E402
from hpc_harness.server.app import create_app  # noqa: E402
from hpc_harness.server.service import HarnessService  # noqa: E402

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
    service = make_service(tmp_path)
    yield service
    service.shutdown()


@pytest.fixture(name="client")
def client_fixture(service):
    return TestClient(create_app(service))


def submit(client, n=3, batch="b1"):
    jobs = [{"payload": {"scenario": f"s{i}.json"}, "label": f"s{i}", "dedup_key": f"s{i}"}
            for i in range(n)]
    response = client.post(f"{API}/jobs", json={"runner": "hisim", "batch": batch, "jobs": jobs},
                           headers=AUTH)
    assert response.status_code == 200
    return response.json()


def register(client, mode="whole_node"):
    response = client.post(
        f"{API}/workers/register",
        json={"host": "node1", "mode": mode, "slots": 4, "cores": 8, "total_mem_gb": 64,
              "runner": "hisim"},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def lease(client, worker_id, n=1, lease_id="lease-1"):
    response = client.post(
        f"{API}/lease",
        json={"worker_id": worker_id, "num_slots": n, "lease_id": lease_id},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def report(client, worker_id, job, status="done", **extra):
    body = {"id": job["id"], "attempt": job["attempt"], "status": status,
            "exit_code": 0 if status == "done" else 1, "duration_s": 5.0,
            "peak_mem_mb": extra.pop("peak_mem_mb", 1000.0),
            "result_dir": job.get("result_dir"), **extra}
    response = client.post(f"{API}/report", json={"worker_id": worker_id, "reports": [body]},
                           headers=AUTH)
    assert response.status_code == 200
    return response.json()["results"][0]


def heartbeat(client, worker_id, running=()):
    response = client.post(
        f"{API}/heartbeat",
        json={"worker_id": worker_id, "metrics": {"cpu_percent": 10.0}, "running": list(running)},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------------- auth


def test_mutations_need_token_reads_are_open(client):
    assert client.post(f"{API}/jobs", json={"runner": "x", "jobs": []}).status_code == 401
    assert client.post(f"{API}/lease", json={}).status_code == 401
    assert client.get(f"{API}/status").status_code == 200
    assert client.get(f"{API}/jobs").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert "<title>HPC Harness</title>" in client.get("/").text


# ------------------------------------------------------------------- happy path


def test_full_cycle_submit_lease_report_done(client):
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
    submit(client, 3)
    worker_id = register(client)["worker_id"]
    first = lease(client, worker_id, 2, "L1")["jobs"]
    replay = lease(client, worker_id, 2, "L1")["jobs"]
    assert [j["id"] for j in first] == [j["id"] for j in replay]
    assert client.get(f"{API}/status").json()["counts"]["leased"] == 2


def test_stale_report_rejected_after_cancel(client):
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
    submit(client, 1)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    assert report(client, worker_id, job)["accepted"]
    replay = report(client, worker_id, job)
    assert replay["accepted"] and replay["reason"] == "duplicate"
    assert client.get(f"{API}/status").json()["counts"]["done"] == 1


def test_unknown_worker_gets_reregister(client):
    assert heartbeat(client, "ghost-worker") == {"reregister": True}
    assert lease(client, "ghost-worker").get("reregister")


def test_orphaned_lease_requeued_after_strikes(client, service):
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
    submit(client, 2)
    worker_id = register(client)["worker_id"]
    (job,) = lease(client, worker_id)["jobs"]
    freed = client.post(f"{API}/admin/logs/purge", headers=AUTH).json()
    assert freed["ok"]
    # Scheduling state is untouched; a fenced report still lands.
    assert report(client, worker_id, job)["accepted"]
    del service


def test_deregister_requeues_leases(client):
    submit(client, 2)
    worker_id = register(client)["worker_id"]
    lease(client, worker_id, 2)
    response = client.post(f"{API}/workers/{worker_id}/deregister",
                           json={"reason": "file_access"}, headers=AUTH)
    assert response.json()["requeued"] == 2
    workers = client.get(f"{API}/workers").json()
    assert workers[0]["status"] == "dead" and workers[0]["last_error"] == "file_access"


def test_snapshot_writes_shared_fs_copy(client, service, tmp_path):
    submit(client, 1)
    service.snapshot()
    snapshot_path = tmp_path / "snapshot.db"
    assert snapshot_path.exists()
    check = db.connect(str(snapshot_path))
    assert db.counts(check)["total"] == 1
    check.close()


def test_warm_restart_keeps_live_leases_cold_restart_requeues(tmp_path):
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
