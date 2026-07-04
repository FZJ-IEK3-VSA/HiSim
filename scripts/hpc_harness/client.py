"""HTTP client for workers and submit tools (spec §7 'Client behaviour').

Retries with exponential backoff on connection errors and 5xx, re-reading the
``server.url`` file each time in case the server moved (spec §4.5). Retrying is safe
by construction: leases replay via ``lease_id``, reports dedup on ``(id, attempt)``.
"""

import logging
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

LOGGER = logging.getLogger(__name__)

API = "/api/v1"


class HarnessClient:
    """Small typed wrapper over the REST API."""

    def __init__(
        self,
        server_url: Optional[str] = None,
        url_file: Optional[str] = None,
        token: Optional[str] = None,
        timeout_s: float = 60.0,
        backoff_s: float = 5.0,
        max_backoff_s: float = 120.0,
        max_tries: Optional[int] = None,
    ) -> None:
        """Provide either a direct ``server_url`` or a ``url_file`` to discover it."""
        if server_url is None and url_file is None:
            raise ValueError("HarnessClient needs server_url or url_file")
        self._explicit_url = server_url
        self.url_file = url_file
        self.token = token
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s
        self.max_backoff_s = max_backoff_s
        self.max_tries = max_tries
        self._client = httpx.Client(timeout=timeout_s)

    def base_url(self) -> str:
        """Current server base URL (re-reads the url file so a moved server is found)."""
        if self.url_file:
            try:
                text = Path(self.url_file).read_text(encoding="utf-8").strip()
                if text:
                    return text if text.startswith("http") else f"http://{text}"
            except OSError:
                if self._explicit_url is None:
                    raise
        if self._explicit_url is None:
            raise RuntimeError(f"server url file {self.url_file} is empty/unreadable")
        return self._explicit_url

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        backoff = self.backoff_s
        tries = 0
        while True:
            tries += 1
            url = self.base_url() + API + path  # re-resolved each try (the server may have moved)
            last_error = ""
            try:
                response = self._client.post(url, json=body, headers=self._headers())
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
                last_error = f"HTTP {response.status_code}"
                LOGGER.warning("Server 5xx on %s (%s): %s", path, url, response.status_code)
            except (httpx.TransportError, OSError) as exc:
                last_error = str(exc)
                LOGGER.warning("Connection error on %s (%s): %s — is the server up and is %s "
                               "reachable from this node?", path, url, exc, self._host_of(url))
            if self.max_tries is not None and tries >= self.max_tries:
                raise RuntimeError(f"POST {path} to {url} failed after {tries} tries: {last_error}")
            time.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff_s)

    @staticmethod
    def _host_of(url: str) -> str:
        """Return the ``host:port`` of ``url`` for diagnostics (best-effort)."""
        try:
            parsed = urllib.parse.urlsplit(url)
            return parsed.netloc or url
        except ValueError:
            return url

    def _get(self, path: str) -> Any:
        response = self._client.get(self.base_url() + API + path)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------ worker calls

    def register(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """POST /workers/register."""
        return self._post("/workers/register", info)

    def lease(self, worker_id: str, num_slots: int, lease_id: Optional[str] = None) -> Dict[str, Any]:
        """POST /lease with a fresh (or supplied, for replay) lease_id."""
        return self._post(
            "/lease",
            {"worker_id": worker_id, "num_slots": num_slots, "lease_id": lease_id or uuid.uuid4().hex},
        )

    def report(self, worker_id: str, reports: List[Dict[str, Any]]) -> Dict[str, Any]:
        """POST /report (batched, fenced)."""
        return self._post("/report", {"worker_id": worker_id, "reports": reports})

    def heartbeat(
        self, worker_id: str, metrics: Dict[str, Any], running: List[Dict[str, int]]
    ) -> Dict[str, Any]:
        """POST /heartbeat; returns the directive set."""
        return self._post(
            "/heartbeat", {"worker_id": worker_id, "metrics": metrics, "running": running}
        )

    def deregister(self, worker_id: str, reason: Optional[str] = None) -> None:
        """POST /workers/{id}/deregister (best effort, few tries)."""
        try:
            saved = self.max_tries
            self.max_tries = 3
            self._post(f"/workers/{worker_id}/deregister", {"reason": reason})
        except Exception:  # pylint: disable=broad-except
            LOGGER.warning("Deregister failed (server will reap)", exc_info=True)
        finally:
            self.max_tries = saved

    def ship_logs(self, worker_id: str, records: List[Dict[str, Any]]) -> None:
        """POST /logs (best effort)."""
        try:
            saved = self.max_tries
            self.max_tries = 2
            self._post("/logs", {"worker_id": worker_id, "records": records})
        except Exception:  # pylint: disable=broad-except
            pass
        finally:
            self.max_tries = saved

    def report_errors(self, worker_id: Optional[str], errors: List[Dict[str, Any]]) -> None:
        """POST /errors (best effort) — persistent error reporting (§4.7)."""
        if not errors:
            return
        try:
            saved = self.max_tries
            self.max_tries = 2
            self._post("/errors", {"worker_id": worker_id, "errors": errors})
        except Exception:  # pylint: disable=broad-except
            pass
        finally:
            self.max_tries = saved

    def upload_console(self, worker_id: str, text: str, next_offset: int) -> None:
        """POST /workers/{id}/console."""
        try:
            saved = self.max_tries
            self.max_tries = 2
            self._post(
                f"/workers/{worker_id}/console",
                {"ts": time.time(), "text": text, "next_offset": next_offset},
            )
        except Exception:  # pylint: disable=broad-except
            pass
        finally:
            self.max_tries = saved

    # ------------------------------------------------------- submit/admin calls

    def submit_jobs(self, runner: str, jobs: List[Dict[str, Any]], batch: str = "") -> Dict[str, Any]:
        """POST /jobs."""
        return self._post("/jobs", {"runner": runner, "batch": batch, "jobs": jobs})

    def status(self) -> Dict[str, Any]:
        """GET /status."""
        return self._get("/status")

    def admin_reset(self, leased: bool = False, failed: bool = False) -> Dict[str, Any]:
        """POST /admin/reset."""
        return self._post("/admin/reset", {"leased": leased, "failed": failed})

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()
