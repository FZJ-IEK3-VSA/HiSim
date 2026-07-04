"""FastAPI routes for the queue server (spec §7).

Thin shell around :class:`~hpc_harness.server.service.HarnessService`. Auth: the
bearer token is required on **mutating** routes only; GET routes and the dashboard are
open on the cluster-internal interface (spec §11).
"""

from typing import Any, Dict, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from hpc_harness.server.dashboard import render_dashboard
from hpc_harness.server.service import HarnessService

API = "/api/v1"


def create_app(service: HarnessService) -> FastAPI:
    """Build the FastAPI app around a service instance."""
    app = FastAPI(title="HPC harness queue server", docs_url=None, redoc_url=None)
    app.state.service = service

    def require_token(request: Request) -> None:
        token = service.cfg.token
        if not token:
            return  # no token configured: trusted-network mode
        supplied = request.headers.get("Authorization", "")
        if supplied != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    auth = Depends(require_token)

    # ------------------------------------------------------------- mutating API

    @app.post(f"{API}/jobs", dependencies=[auth])
    def submit_jobs(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        runner = body.get("runner")
        jobs = body.get("jobs", [])
        if not runner or not isinstance(jobs, list):
            raise HTTPException(status_code=422, detail="body needs {runner, jobs:[...]}")
        return service.submit_jobs(runner, jobs, body.get("batch") or "")

    @app.post(f"{API}/workers/register", dependencies=[auth])
    def register(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        return service.register_worker(body)

    @app.post(f"{API}/lease", dependencies=[auth])
    def lease(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        for field in ("worker_id", "lease_id"):
            if not body.get(field):
                raise HTTPException(status_code=422, detail=f"missing {field}")
        return service.lease(body["worker_id"], int(body.get("num_slots", 1)), body["lease_id"])

    @app.post(f"{API}/report", dependencies=[auth])
    def report(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        if not body.get("worker_id"):
            raise HTTPException(status_code=422, detail="missing worker_id")
        return service.report(body["worker_id"], body.get("reports", []))

    @app.post(f"{API}/heartbeat", dependencies=[auth])
    def heartbeat(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        if not body.get("worker_id"):
            raise HTTPException(status_code=422, detail="missing worker_id")
        return service.heartbeat(body["worker_id"], body.get("metrics"), body.get("running"))

    @app.post(f"{API}/workers/{{worker_id}}/deregister", dependencies=[auth])
    def deregister(worker_id: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        return service.deregister_worker(worker_id, body.get("reason"))

    @app.post(f"{API}/logs", dependencies=[auth])
    def ship_logs(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        service.ship_logs(body.get("worker_id", "?"), body.get("records", []))
        return {"ok": True}

    @app.post(f"{API}/workers/{{worker_id}}/console", dependencies=[auth])
    def console_upload(worker_id: str, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        service.console_upload(
            worker_id, float(body.get("ts", 0)), body.get("text", ""), int(body.get("next_offset", 0))
        )
        return {"ok": True}

    # ------------------------------------------------------------------- admin

    @app.post(f"{API}/admin/workers/{{worker_id}}/console", dependencies=[auth])
    def admin_console(worker_id: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        service.request_console(worker_id, body.get("follow"))
        return {"ok": True}

    @app.post(f"{API}/admin/config", dependencies=[auth])
    def admin_config(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        return service.apply_config(body)

    @app.post(f"{API}/admin/logs/purge", dependencies=[auth])
    def purge_logs() -> Dict[str, Any]:
        return {"ok": True, "freed_bytes": service.purge_logs()}

    @app.post(f"{API}/admin/reset", dependencies=[auth])
    def admin_reset(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
        return {"requeued": service.admin_reset(bool(body.get("leased")), bool(body.get("failed")))}

    @app.post(f"{API}/admin/jobs/{{job_id}}/cancel", dependencies=[auth])
    def cancel_job(job_id: int) -> Dict[str, Any]:
        return service.cancel_job(job_id)

    @app.post(f"{API}/admin/pause", dependencies=[auth])
    def pause() -> Dict[str, Any]:
        service.pause()
        return {"ok": True}

    @app.post(f"{API}/admin/resume", dependencies=[auth])
    def resume() -> Dict[str, Any]:
        service.resume()
        return {"ok": True}

    # --------------------------------------------------------------- open reads

    @app.get(f"{API}/status")
    def status() -> Dict[str, Any]:
        return service.status()

    @app.get(f"{API}/jobs")
    def jobs(
        state: Optional[str] = Query(default=None),
        batch: Optional[str] = Query(default=None),
        limit: int = Query(default=50, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        return JSONResponse(service.jobs(state, batch, limit, offset))

    @app.get(f"{API}/workers")
    def workers() -> JSONResponse:
        return JSONResponse(service.workers())

    @app.get(f"{API}/workers/{{worker_id}}/console")
    def console_get(worker_id: str) -> Dict[str, Any]:
        snapshot = service.logdb.get_console(worker_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="no console snapshot yet")
        return snapshot

    @app.get(f"{API}/logs")
    def logs(
        worker: Optional[str] = Query(default=None),
        job: Optional[int] = Query(default=None),
        level: Optional[str] = Query(default=None),
        limit: int = Query(default=200, le=2000),
    ) -> JSONResponse:
        return JSONResponse(service.logdb.query_logs(worker, job, level, limit))

    @app.get(f"{API}/metrics/timeseries")
    def timeseries(
        worker: Optional[str] = Query(default=None),
        since: float = Query(default=0.0),
    ) -> JSONResponse:
        return JSONResponse(service.logdb.timeseries(worker, since))

    @app.get("/healthz")
    def healthz() -> Dict[str, bool]:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return render_dashboard()

    return app
