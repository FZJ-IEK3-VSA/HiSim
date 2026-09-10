#!/usr/bin/env python3
"""Collect what the repository's CI actually costs and report what changed.

Run hourly by ``.github/workflows/ci-usage.yml``, and runnable by hand::

    GH_TOKEN=... python3 scripts/ci_usage_report.py --window-days 30 --out report.md

Produces two things from one sweep. The overview says where the runner-minutes go -- per
workflow, per job, with the share of the total each takes -- and the regression section says
which jobs have got slower or hungrier than they used to be, which is the part worth reading
every morning. A base test that quietly grew from four minutes to eleven is invisible in a
green pipeline and obvious in a table of medians.

Durations come from the jobs API, which reports them for every job of every run at no cost
beyond the request. Memory and real CPU time come from the artifacts the resource-monitor
action uploads, because GitHub reports neither. Note that the billing endpoint is useless
here and is not consulted: this repository is public, Actions minutes are therefore free, and
``/timing`` dutifully answers that every run cost zero. Runner-minutes are computed from the
job timestamps instead, which measures the thing that actually hurts -- wall clock, queueing
and the concurrency other work has to wait behind.

The sweep is incremental because it has to be. At roughly 150 runs a day, each with about ten
jobs, a full re-read would want some 9000 requests where ``GITHUB_TOKEN`` allows 1000 an hour.
So the previous sweep's index is downloaded from its own artifact, only runs newer than its
watermark are fetched, and the result is pruned to the window and uploaded again. That is also
why it runs hourly rather than nightly: the four golden workflows alone upload 88 resource
artifacts per push to main, which one sweep a day cannot collect, while an hour in which
nothing was pushed costs a handful of requests. Memory records are swept for the default
branch and for flagged jobs rather than for everything; a pull request's own numbers are in
its own job summaries the moment the job ends.

A record whose artifact has not been collected within a day is given up on: its memory method
becomes ``expired``, it leaves the wanted list, and the report counts it instead of re-listing
it on every sweep forever. What is still outstanding is counted too, under Collection, so a
backlog that is growing rather than draining is visible from the report alone.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


RESOURCE_COLLECTION_DEADLINE = timedelta(hours=24)
"""How long a record on the memory branch waits for its resource artifact before it is expired.

The sweep only reads artifacts newer than its watermark, so an artifact that was never
collected -- because a sweep failed, because the download cap cut it off, or because the
artifact was deleted -- would otherwise leave its record in the wanted list of every later
sweep for the whole thirty-day window, costing requests that can never succeed. A day is well
past the point where a further attempt would find anything: every sweep in between has already
had its chance.
"""


class RateLimitExhausted(RuntimeError):
    """Raised when the request budget or the API's own rate limit is used up.

    Caught by the collector, which stops sweeping and marks the report partial rather than
    failing: a report covering four of the last five days is worth having, and a sweep that
    goes red because GitHub was busy trains everyone to ignore it.
    """


class GitHubApi:
    """A minimal, budget-aware GitHub REST client built on the standard library.

    Counts every request it makes and refuses to exceed either the budget it was given or the
    rate limit the API reports back, so a sweep degrades into a partial one instead of
    spending an hour blocked on a 403. Standard library only, to keep the sweep free of an
    install step.

    Attributes:
        requests_made: how many HTTP requests have been issued.
        rate_limit_remaining: what the API said was left, as of the last response.
    """

    API_ROOT = "https://api.github.com"
    ACCEPT_HEADER = "application/vnd.github+json"
    API_VERSION = "2022-11-28"
    USER_AGENT = "hisim-ci-usage-report"
    PAGE_SIZE = 100
    # Requests kept in reserve so a sweep that stops still has room to upload its index.
    RATE_LIMIT_FLOOR = 25
    RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
    MAX_ATTEMPTS = 3

    def __init__(self, repository: str, token: Optional[str], max_requests: int) -> None:
        self.repository = repository
        self.token = token
        self.max_requests = max_requests
        self.requests_made = 0
        self.rate_limit_remaining: Optional[int] = None

    def _headers(self) -> Dict[str, str]:
        """Return the request headers, with authorization only when a token was supplied."""
        headers = {
            "Accept": self.ACCEPT_HEADER,
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": self.USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _check_budget(self) -> None:
        """Raise :class:`RateLimitExhausted` if another request would overrun a limit."""
        if self.requests_made >= self.max_requests:
            raise RateLimitExhausted(f"request budget of {self.max_requests} is used up")
        if self.rate_limit_remaining is not None and self.rate_limit_remaining <= self.RATE_LIMIT_FLOOR:
            raise RateLimitExhausted(f"GitHub rate limit nearly exhausted ({self.rate_limit_remaining} left)")

    def _request(self, url: str, *, binary: bool = False) -> Any:
        """Issue one GET and return the parsed JSON, or the raw bytes when ``binary``.

        Args:
            url: the absolute URL to fetch.
            binary: return the response body as bytes rather than parsing it as JSON.

        Returns:
            The decoded JSON document, or the response bytes.

        Raises:
            RateLimitExhausted: if the budget is used up, or the API answered 403 for rate
                limiting rather than for permissions.
            urllib.error.HTTPError: for any other unsuccessful status.
        """
        self._check_budget()
        request = urllib.request.Request(url, headers=self._headers())
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_ATTEMPTS):
            self.requests_made += 1
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    remaining = response.headers.get("x-ratelimit-remaining")
                    if remaining is not None:
                        self.rate_limit_remaining = int(remaining)
                    payload = response.read()
                return payload if binary else json.loads(payload.decode("utf-8"))
            except urllib.error.HTTPError as error:
                remaining = error.headers.get("x-ratelimit-remaining") if error.headers else None
                if remaining is not None:
                    self.rate_limit_remaining = int(remaining)
                if error.code == 403 and self.rate_limit_remaining == 0:
                    raise RateLimitExhausted("GitHub rate limit reached") from error
                if error.code not in self.RETRYABLE_STATUS or attempt == self.MAX_ATTEMPTS - 1:
                    raise
                last_error = error
            except urllib.error.URLError as error:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise
                last_error = error
        raise RuntimeError(f"unreachable retry loop for {url}: {last_error!r}")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Return the JSON body of ``path``, which is relative to the repository."""
        url = f"{self.API_ROOT}/repos/{self.repository}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._request(url)

    def paginate(self, path: str, item_key: str, params: Optional[Dict[str, Any]] = None,
                 max_pages: int = 100) -> Iterator[Dict[str, Any]]:
        """Yield items from a paginated listing, newest first, up to ``max_pages`` pages.

        Stops early when a page comes back short, which is how the API signals the end, and
        propagates :class:`RateLimitExhausted` so a caller can keep whatever it has already
        consumed.
        """
        page = 1
        while page <= max_pages:
            query = dict(params or {})
            query.update({"per_page": self.PAGE_SIZE, "page": page})
            payload = self.get(path, query)
            items = payload.get(item_key, []) if isinstance(payload, dict) else []
            yield from items
            if len(items) < self.PAGE_SIZE:
                return
            page += 1

    def download_artifact(self, artifact_id: int) -> Optional[bytes]:
        """Return the zip bytes of one artifact, or None if it has expired or vanished.

        The zip lives behind a redirect to blob storage, which rejects a request that still
        carries a GitHub token, so the redirect is followed with the authorization header
        stripped.
        """
        url = f"{self.API_ROOT}/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        self._check_budget()
        self.requests_made += 1
        opener = urllib.request.build_opener(_StripAuthorizationOnRedirect())
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with opener.open(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                return None
            raise


class _StripAuthorizationOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follows redirects without leaking the GitHub token to the storage host.

    Artifact downloads redirect to a signed blob-storage URL that already carries its own
    credentials in the query string and refuses any request presenting a second mechanism.
    Python's default handler would replay every original header, so the header is dropped here.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> Any:
        """Return the follow-up request, minus the Authorization header."""
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None:
            new_request.headers.pop("Authorization", None)
            new_request.headers.pop("authorization", None)
        return new_request


class Timestamps:
    """Parsing and formatting of the ISO-8601 timestamps the API deals in.

    Every timestamp GitHub returns is UTC with a trailing ``Z``, which
    :func:`datetime.fromisoformat` only learned to accept in Python 3.11; the replacement here
    keeps the script runnable on whatever interpreter a runner happens to offer.
    """

    API_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    @classmethod
    def parse(cls, value: Optional[str]) -> Optional[datetime]:
        """Return ``value`` as an aware UTC datetime, or None if it is absent or malformed."""
        if not value:
            return None
        try:
            return datetime.strptime(value, cls.API_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

    @classmethod
    def format(cls, moment: datetime) -> str:
        """Return ``moment`` as the ISO-8601 string the API expects in query filters."""
        return moment.astimezone(timezone.utc).strftime(cls.API_FORMAT)

    @classmethod
    def now(cls) -> datetime:
        """Return the current time as an aware UTC datetime."""
        return datetime.now(timezone.utc)


class JobRecord:
    """One measured job: what it was, how long it held a runner, and what it used.

    The duration half is always present, since it comes from the jobs API. The resource half
    is present only once the resource-monitor artifact for that job has been collected, which
    happens for default-branch and flagged runs, so most pull-request records carry timing
    alone. Stored as plain dicts in the index; this class holds the field names and the
    derivations rather than the state.
    """

    KEY_FIELDS = ("run_id", "job_id")
    # Conclusions whose durations mean something. A cancelled job stopped early for reasons
    # that have nothing to do with its cost, and folding those into a baseline would make
    # every busy afternoon look like an improvement.
    MEANINGFUL_CONCLUSIONS = frozenset({"success", "failure"})
    BASELINE_CONCLUSIONS = frozenset({"success"})

    @classmethod
    def key(cls, record: Dict[str, Any]) -> str:
        """Return the index key of a record, unique per job of per run attempt."""
        return f"{record.get('run_id')}:{record.get('job_id')}"

    @classmethod
    def from_api(cls, run: Dict[str, Any], job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a record from a run and one of its jobs, or None if it never really ran.

        A job still queued, or one that was skipped, has no pair of timestamps to measure and
        is left out entirely rather than recorded as having taken no time.
        """
        started = Timestamps.parse(job.get("started_at"))
        completed = Timestamps.parse(job.get("completed_at"))
        if started is None or completed is None:
            return None
        wall_seconds = (completed - started).total_seconds()
        if wall_seconds < 0:
            return None
        return {
            "run_id": run.get("id"),
            "job_id": job.get("id"),
            "run_attempt": job.get("run_attempt", run.get("run_attempt", 1)),
            "workflow": run.get("name") or run.get("path", ""),
            "workflow_path": run.get("path", ""),
            "job_name": job.get("name", ""),
            "conclusion": job.get("conclusion"),
            "event": run.get("event"),
            "branch": run.get("head_branch"),
            "created_at": run.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "wall_seconds": round(wall_seconds, 1),
            "runner_name": job.get("runner_name"),
            "labels": job.get("labels", []),
        }

    # The one value of ``peak_memory_method`` this script writes itself. Every other value comes
    # from the probe's MeasurementMethod enum, which is the producer side and says how a real
    # reading was obtained; this one says that no reading arrived within the deadline and that
    # none is coming, which is a different thing from a job that was never measured at all.
    MEASUREMENT_EXPIRED = "expired"

    # The fields a resource-monitor artifact contributes, listed once so attaching them from an
    # artifact and carrying them across a re-sweep can never drift apart.
    RESOURCE_FIELDS = (
        "peak_memory_bytes", "peak_memory_fraction", "peak_memory_method", "cpu_seconds",
        "cpu_efficiency", "cpu_count", "memory_total_bytes", "measured_wall_seconds",
        "probe_scope", "probe_job",
    )

    @classmethod
    def is_expired(cls, record: Dict[str, Any]) -> bool:
        """Return whether this record has given up waiting for its resource artifact."""
        return record.get("peak_memory_method") == cls.MEASUREMENT_EXPIRED

    @classmethod
    def mark_expired(cls, record: Dict[str, Any]) -> None:
        """Record that no measurement is coming for this job, so nothing keeps asking for one.

        Only the method is written. The memory fields stay absent, because inventing a zero
        would put the job into every median as a job that used no memory.
        """
        record["peak_memory_method"] = cls.MEASUREMENT_EXPIRED

    @classmethod
    def carry_resources(cls, source: Dict[str, Any], target: Dict[str, Any]) -> None:
        """Copy resource measurements from an indexed record onto its freshly fetched twin."""
        if source.get("peak_memory_bytes") is None:
            # An expired marker is the one thing worth carrying without a measurement behind
            # it: losing it in the overlap would put the record back in the wanted list.
            if cls.is_expired(source):
                cls.mark_expired(target)
            return
        for field in cls.RESOURCE_FIELDS:
            if field in source:
                target[field] = source[field]

    @classmethod
    def attach_resources(cls, record: Dict[str, Any], metrics: Dict[str, Any]) -> None:
        """Fold a resource-monitor record's measurements into an API-derived record.

        The probe's own wall time is kept separately from the API's: the API measures the
        whole job including the container pull, the probe measures from checkout onwards, and
        the difference between them is the fixed overhead a job pays before doing any work.
        """
        record["peak_memory_bytes"] = metrics.get("peak_memory_bytes")
        record["peak_memory_fraction"] = metrics.get("peak_memory_fraction")
        record["peak_memory_method"] = metrics.get("peak_memory_method")
        record["cpu_seconds"] = metrics.get("cpu_seconds")
        record["cpu_efficiency"] = metrics.get("cpu_efficiency")
        record["cpu_count"] = metrics.get("cpu_count")
        record["memory_total_bytes"] = metrics.get("memory_total_bytes")
        record["measured_wall_seconds"] = metrics.get("wall_seconds")
        record["probe_scope"] = metrics.get("scope")
        record["probe_job"] = metrics.get("job")


class UsageIndex:
    """The rolling store of job records, carried between hourly sweeps as an artifact.

    Holds one entry per (run, job) inside the reporting window and the watermark that says how
    far the last sweep got, which together are what make the next sweep cheap. Pruned to the
    window on every save so it cannot grow without bound; at the default thirty days it settles
    at a few megabytes of JSON.
    """

    SCHEMA = "hisim.ci_usage_index/1"
    FILENAME = "ci-usage-index.json"
    # How far back a sweep re-reads past its own watermark, to catch runs that were still in
    # flight when it ran and finished afterwards.
    OVERLAP_HOURS = 8

    def __init__(self, records: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.records: Dict[str, Dict[str, Any]] = records or {}

    @classmethod
    def load(cls, path: Optional[str]) -> "UsageIndex":
        """Return the index stored at ``path``, or an empty one if there is nothing usable.

        A missing, unreadable or wrong-schema file is not an error: it is what the very first
        sweep sees, and what every sweep sees after the index artifact expires.
        """
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return cls()
        if payload.get("schema") != cls.SCHEMA:
            return cls()
        return cls(dict(payload.get("records", {})))

    def save(self, path: str, window_start: datetime) -> None:
        """Prune to the window and write the index to ``path``."""
        self.prune(window_start)
        payload = {
            "schema": self.SCHEMA,
            "written_at": Timestamps.format(Timestamps.now()),
            "watermark": Timestamps.format(self.watermark()) if self.watermark() else None,
            "records": self.records,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)

    def add(self, record: Dict[str, Any]) -> None:
        """Insert or replace a record, keyed so a re-run replaces the attempt it repeats."""
        self.records[JobRecord.key(record)] = record

    def get(self, record_key: str) -> Optional[Dict[str, Any]]:
        """Return the record under ``record_key``, or None."""
        return self.records.get(record_key)

    def prune(self, window_start: datetime) -> int:
        """Drop records older than ``window_start`` and return how many were dropped."""
        keep: Dict[str, Dict[str, Any]] = {}
        for record_key, record in self.records.items():
            created = Timestamps.parse(record.get("created_at"))
            if created is not None and created >= window_start:
                keep[record_key] = record
        dropped = len(self.records) - len(keep)
        self.records = keep
        return dropped

    def watermark(self) -> Optional[datetime]:
        """Return the newest run creation time in the index, or None when it is empty."""
        moments = [Timestamps.parse(record.get("created_at")) for record in self.records.values()]
        present = [moment for moment in moments if moment is not None]
        return max(present) if present else None

    def sweep_start(self, window_start: datetime) -> datetime:
        """Return the time a sweep should start reading from.

        The watermark less an overlap, so runs that were still in flight during the last sweep
        are picked up by this one; or the start of the window when there is no index to build
        on.
        """
        mark = self.watermark()
        if mark is None:
            return window_start
        return max(window_start, mark - timedelta(hours=self.OVERLAP_HOURS))

    def runs_missing_resources(self, branch: Optional[str]) -> Dict[int, List[Dict[str, Any]]]:
        """Group records that still lack resource data by run, optionally filtered to a branch.

        Records already given up on are left out: they are exactly the ones a further sweep
        would spend requests on and find nothing for.

        Args:
            branch: only consider runs of this branch; None considers every branch.

        Returns:
            A mapping of run id to the records of that run that have no memory figure yet.
        """
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for record in self.records.values():
            if record.get("peak_memory_bytes") is not None or JobRecord.is_expired(record):
                continue
            if branch is not None and record.get("branch") != branch:
                continue
            run_id = record.get("run_id")
            if isinstance(run_id, int):
                grouped.setdefault(run_id, []).append(record)
        return grouped

    def expire_stale_resource_records(self, branch: Optional[str], now: datetime) -> int:
        """Give up on records whose artifact never arrived, and return how many were marked.

        Only records of the memory branch are considered. A pull-request job is not waiting for
        anything -- its artifact is deliberately never collected, and its own job summary has
        the numbers -- so calling it expired would say something untrue about it.

        Args:
            branch: the branch whose artifacts are collected; None considers every branch.
            now: the moment the deadline is measured against.

        Returns:
            How many records were marked expired by this call.
        """
        marked = 0
        for record in self.records.values():
            if record.get("peak_memory_bytes") is not None or JobRecord.is_expired(record):
                continue
            if branch is not None and record.get("branch") != branch:
                continue
            created = Timestamps.parse(record.get("created_at"))
            if created is None or now - created < RESOURCE_COLLECTION_DEADLINE:
                continue
            JobRecord.mark_expired(record)
            marked += 1
        return marked

    def expired_count(self) -> int:
        """Return how many records in the index have been given up on altogether."""
        return sum(1 for record in self.records.values() if JobRecord.is_expired(record))

    def values(self) -> Iterable[Dict[str, Any]]:
        """Return every record in the index."""
        return self.records.values()


class ResourceArtifactJoiner:
    """Matches a resource-monitor artifact back to the API job it was produced by.

    The two sides name a job differently and neither can be made to use the other's name: the
    API reports the rendered display name, ``pytest (base)``, while the probe only knows the
    job's YAML id and the scope it was handed, ``pytest`` and ``base``. So the runner is tried
    first, being unique among the jobs of a run that are in flight together, and the names are
    matched on their word content only as a fallback.
    """

    @staticmethod
    def _tokens(text: str) -> frozenset:
        """Return the lowercased alphanumeric words of ``text`` as a set."""
        cleaned = "".join(character if character.isalnum() else " " for character in text.lower())
        return frozenset(word for word in cleaned.split() if word)

    @classmethod
    def match(cls, metrics: Dict[str, Any], candidates: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the record of ``candidates`` that ``metrics`` belongs to, or None.

        Args:
            metrics: one parsed resource-monitor record.
            candidates: the API-derived records of the same run.

        Returns:
            The matching record, or None when neither the runner nor the name identifies one
            unambiguously -- counted as an unjoined artifact rather than guessed at.
        """
        runner = metrics.get("runner_name")
        if runner:
            on_runner = [record for record in candidates if record.get("runner_name") == runner]
            if len(on_runner) == 1:
                return on_runner[0]
        probe_tokens = cls._tokens(f"{metrics.get('job', '')} {metrics.get('scope', '')}")
        if not probe_tokens:
            return None
        scored = [record for record in candidates
                  if probe_tokens <= cls._tokens(str(record.get("job_name", "")))]
        if len(scored) == 1:
            return scored[0]
        return None


class UsageCollector:
    """Sweeps the API for new runs, their jobs and the resource artifacts worth downloading.

    Everything it does is bounded: the run listing stops at the sweep start, the job listing
    happens once per new run, and the artifact downloads are limited both by which runs deserve
    them and by an outright cap. When the budget runs out mid-sweep it stops and says so, and
    the index keeps whatever was already collected.

    Attributes:
        diagnostics: counters describing what the sweep managed and what it left behind,
            rendered into the report so a partial sweep is visible rather than silently
            thinner, and so a backlog that grows instead of draining can be seen.
    """

    ARTIFACT_PREFIX = "ci-usage-"
    METRICS_FILENAME = "metrics.json"
    # Artifact pages are scanned back only this far past the sweep start; the listing is
    # newest-first and every page costs a request.
    MAX_ARTIFACT_PAGES = 40

    def __init__(self, api: GitHubApi, index: UsageIndex) -> None:
        self.api = api
        self.index = index
        self.diagnostics: Dict[str, Any] = {
            "runs_fetched": 0,
            "jobs_recorded": 0,
            "artifacts_downloaded": 0,
            "artifacts_unjoined": 0,
            # Artifacts of runs that still want one and that this sweep did not get to: the
            # backlog the next sweeps have to drain.
            "artifacts_waiting": 0,
            # Artifacts passed over because their run is not on the memory branch. Counted
            # from the listing, never downloaded.
            "artifacts_off_branch": 0,
            "records_expired": 0,
            "partial": False,
            "stopped_because": None,
        }

    def sweep_runs(self, since: datetime) -> None:
        """Fetch every run created since ``since`` and record each of its jobs.

        Runs still in progress are skipped rather than recorded half-finished; the next sweep's
        overlap picks them up once they have concluded.
        """
        try:
            for run in self.api.paginate("actions/runs", "workflow_runs",
                                         {"created": f">={Timestamps.format(since)}"}):
                created = Timestamps.parse(run.get("created_at"))
                if created is not None and created < since:
                    return
                if run.get("status") != "completed":
                    continue
                self.diagnostics["runs_fetched"] += 1
                self._record_jobs(run)
        except RateLimitExhausted as error:
            self._stop(str(error))

    def _record_jobs(self, run: Dict[str, Any]) -> None:
        """Record every job of ``run`` that actually executed."""
        run_id = run.get("id")
        if not isinstance(run_id, int):
            return
        for job in self.api.paginate(f"actions/runs/{run_id}/jobs", "jobs", {"filter": "latest"}):
            record = JobRecord.from_api(run, job)
            if record is None:
                continue
            existing = self.index.get(JobRecord.key(record))
            if existing is not None:
                # The overlap re-reads runs a previous sweep already saw. Their resource data
                # was joined then and the artifact behind it may since have expired, so it is
                # carried over rather than re-fetched -- and re-fetching it would be the most
                # expensive thing this script could possibly do.
                JobRecord.carry_resources(existing, record)
            self.index.add(record)
            self.diagnostics["jobs_recorded"] += 1

    def sweep_resources(self, since: datetime, wanted_runs: Sequence[int], max_downloads: int,
                        memory_branch: Optional[str] = None) -> None:
        """Download and join the resource artifacts of ``wanted_runs``, counting the rest.

        The listing is read to the same depth whether or not the download cap is reached,
        because what it says about the artifacts that were not downloaded -- how many are
        waiting for a later sweep, how many belong to branches whose memory is deliberately
        not collected -- is the only cheap measure of whether the sweep is keeping up. Counting
        them costs the page requests the sweep was already prepared to spend; downloading them
        is what costs.

        Args:
            since: ignore artifacts older than this.
            wanted_runs: the runs whose artifacts are worth spending requests on.
            max_downloads: hard cap on artifact downloads for this sweep.
            memory_branch: the branch whose artifacts are collected, for counting the ones
                skipped because they belong to another; None counts none as skipped.
        """
        if not wanted_runs or max_downloads <= 0:
            return
        wanted = set(wanted_runs)
        by_run: Dict[int, List[Dict[str, Any]]] = {}
        for record in self.index.values():
            run_id = record.get("run_id")
            if run_id in wanted:
                by_run.setdefault(int(run_id), []).append(record)
        downloaded = 0
        try:
            for artifact in self.api.paginate("actions/artifacts", "artifacts",
                                              max_pages=self.MAX_ARTIFACT_PAGES):
                created = Timestamps.parse(artifact.get("created_at"))
                if created is not None and created < since:
                    break
                if not str(artifact.get("name", "")).startswith(self.ARTIFACT_PREFIX):
                    continue
                if artifact.get("expired"):
                    continue
                run = artifact.get("workflow_run") or {}
                run_id = run.get("id")
                branch = run.get("head_branch")
                if memory_branch and branch is not None and branch != memory_branch:
                    self.diagnostics["artifacts_off_branch"] += 1
                    continue
                if run_id not in by_run:
                    continue
                if downloaded >= max_downloads:
                    self.diagnostics["artifacts_waiting"] += 1
                    continue
                metrics = self._read_metrics(artifact)
                downloaded += 1
                if metrics is None:
                    continue
                self.diagnostics["artifacts_downloaded"] += 1
                target = ResourceArtifactJoiner.match(metrics, by_run[int(run_id)])
                if target is None:
                    self.diagnostics["artifacts_unjoined"] += 1
                    continue
                JobRecord.attach_resources(target, metrics)
        except RateLimitExhausted as error:
            self._stop(str(error))
        if downloaded >= max_downloads and not self.diagnostics["partial"]:
            # Said after the listing rather than instead of reading it, and never over a
            # budget message: running out of requests is the more urgent of the two.
            self._stop(f"artifact download cap of {max_downloads} reached")

    def _read_metrics(self, artifact: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Download one artifact and return the metrics record inside it, or None."""
        artifact_id = artifact.get("id")
        if not isinstance(artifact_id, int):
            return None
        payload = self.api.download_artifact(artifact_id)
        if not payload:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [name for name in archive.namelist() if name.endswith(self.METRICS_FILENAME)]
                if not names:
                    return None
                with archive.open(names[0]) as handle:
                    return json.loads(handle.read().decode("utf-8"))
        except (zipfile.BadZipFile, ValueError, KeyError):
            return None

    def _stop(self, reason: str) -> None:
        """Mark the sweep partial and record why it stopped."""
        self.diagnostics["partial"] = True
        self.diagnostics["stopped_because"] = reason


class JobStatistics:
    """Summary numbers for one job's records, and the comparison between two periods.

    Medians rather than means throughout, because a single cancelled-at-the-last-moment run or
    one unlucky cold cache would otherwise move a job's whole history.
    """

    def __init__(self, workflow: str, job_name: str) -> None:
        self.workflow = workflow
        self.job_name = job_name
        self.recent: List[Dict[str, Any]] = []
        self.baseline: List[Dict[str, Any]] = []

    @staticmethod
    def _median(values: Sequence[float]) -> Optional[float]:
        """Return the median of ``values``, or None when there are none."""
        return statistics.median(values) if values else None

    @classmethod
    def _durations(cls, records: Sequence[Dict[str, Any]]) -> List[float]:
        """Return the wall seconds of the records whose duration is comparable."""
        return [float(record["wall_seconds"]) for record in records
                if record.get("conclusion") in JobRecord.BASELINE_CONCLUSIONS
                and record.get("wall_seconds") is not None]

    @classmethod
    def _peaks(cls, records: Sequence[Dict[str, Any]]) -> List[float]:
        """Return the peak memory readings of the records that have one."""
        return [float(record["peak_memory_bytes"]) for record in records
                if record.get("peak_memory_bytes") is not None]

    def recent_duration(self) -> Optional[float]:
        """Return the median duration over the recent period, in seconds."""
        return self._median(self._durations(self.recent))

    def baseline_duration(self) -> Optional[float]:
        """Return the median duration over the baseline period, in seconds."""
        return self._median(self._durations(self.baseline))

    def recent_peak(self) -> Optional[float]:
        """Return the median peak memory over the recent period, in bytes."""
        return self._median(self._peaks(self.recent))

    def baseline_peak(self) -> Optional[float]:
        """Return the median peak memory over the baseline period, in bytes."""
        return self._median(self._peaks(self.baseline))

    def worst_memory_fraction(self) -> Optional[float]:
        """Return the largest share of the runner's RAM any of these jobs reached."""
        fractions = [float(record["peak_memory_fraction"]) for record in self.recent + self.baseline
                     if record.get("peak_memory_fraction") is not None]
        return max(fractions) if fractions else None

    def recent_branches(self, limit: int = 3) -> List[str]:
        """Return the branches the recent runs came from, most frequent first.

        A job's median is taken across every branch, so a regression can equally be a change
        that landed on main or one person pushing a slow work-in-progress branch six times.
        The report cannot tell those apart, but naming the branches lets the reader do it in a
        glance instead of opening the runs.
        """
        counts: Dict[str, int] = {}
        for record in self.recent:
            branch = record.get("branch")
            if branch:
                counts[str(branch)] = counts.get(str(branch), 0) + 1
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        return [branch for branch, _ in ranked[:limit]]

    def expired_count(self) -> int:
        """Return how many of these records gave up waiting for their measurement."""
        return sum(1 for record in self.recent + self.baseline if JobRecord.is_expired(record))

    def median_efficiency(self) -> Optional[float]:
        """Return the median CPU efficiency across every measured record."""
        values = [float(record["cpu_efficiency"]) for record in self.recent + self.baseline
                  if record.get("cpu_efficiency") is not None]
        return self._median(values)

    def total_runner_seconds(self) -> float:
        """Return the runner time these records consumed in total, in seconds."""
        return sum(float(record.get("wall_seconds") or 0.0) for record in self.recent + self.baseline)


class RegressionRules:
    """The thresholds that decide when a change in a job's cost is worth reporting.

    Chosen so the report stays readable rather than exhaustive: CI timings on shared runners
    are noisy, and a table that flags every fifteen-percent wobble is a table people stop
    opening. A regression has to be both proportionally and absolutely large, and has to be
    backed by enough runs on each side that it is unlikely to be one bad afternoon.
    """

    MIN_RECENT_SAMPLES = 3
    MIN_BASELINE_SAMPLES = 5
    DURATION_FACTOR = 1.35
    DURATION_MIN_DELTA_SECONDS = 45.0
    MEMORY_FACTOR = 1.25
    MEMORY_MIN_DELTA_BYTES = 512 * 1024 ** 2
    # A job this close to the runner's ceiling is one dataset away from an unexplained kill.
    MEMORY_PRESSURE_FRACTION = 0.85
    # A job that holds a whole runner to keep a fraction of it busy, and is big enough to care.
    LOW_EFFICIENCY = 0.35
    LOW_EFFICIENCY_MIN_SECONDS = 300.0

    @classmethod
    def duration_regression(cls, stats: JobStatistics) -> Optional[Dict[str, Any]]:
        """Return a finding if this job's duration grew enough to report, else None."""
        if (len(stats.recent) < cls.MIN_RECENT_SAMPLES
                or len(stats.baseline) < cls.MIN_BASELINE_SAMPLES):
            return None
        recent, baseline = stats.recent_duration(), stats.baseline_duration()
        if recent is None or baseline is None or baseline <= 0:
            return None
        if recent < baseline * cls.DURATION_FACTOR:
            return None
        if recent - baseline < cls.DURATION_MIN_DELTA_SECONDS:
            return None
        return {
            "workflow": stats.workflow,
            "job_name": stats.job_name,
            "baseline_seconds": baseline,
            "recent_seconds": recent,
            "factor": recent / baseline,
            "samples": f"{len(stats.baseline)} → {len(stats.recent)}",
            "branches": stats.recent_branches(),
        }

    @classmethod
    def memory_regression(cls, stats: JobStatistics) -> Optional[Dict[str, Any]]:
        """Return a finding if this job's peak memory grew enough to report, else None."""
        recent, baseline = stats.recent_peak(), stats.baseline_peak()
        if recent is None or baseline is None or baseline <= 0:
            return None
        if recent < baseline * cls.MEMORY_FACTOR or recent - baseline < cls.MEMORY_MIN_DELTA_BYTES:
            return None
        return {
            "workflow": stats.workflow,
            "job_name": stats.job_name,
            "baseline_bytes": baseline,
            "recent_bytes": recent,
            "factor": recent / baseline,
        }


class UsageReport:
    """Turns the index into the markdown the hourly job writes to its summary.

    Ordered by what a reader needs first: what changed, then what is close to a limit, then
    where the time goes, and only then the totals and the diagnostics. The regression tables
    are printed even when empty, because "nothing regressed" is the answer the report exists to
    give on most mornings and an absent table reads like a broken job.
    """

    BYTES_PER_GIB = 1024 ** 3
    SECONDS_PER_MINUTE = 60.0
    TOP_WORKFLOWS = 20
    TOP_JOBS = 15

    def __init__(self, index: UsageIndex, window_start: datetime, recent_start: datetime,
                 diagnostics: Dict[str, Any]) -> None:
        self.index = index
        self.window_start = window_start
        self.recent_start = recent_start
        self.diagnostics = diagnostics
        self.statistics = self._group()

    def _group(self) -> Dict[Tuple[str, str], JobStatistics]:
        """Group every record by workflow and job name, split into recent and baseline."""
        grouped: Dict[Tuple[str, str], JobStatistics] = {}
        for record in self.index.values():
            created = Timestamps.parse(record.get("created_at"))
            if created is None or created < self.window_start:
                continue
            if record.get("conclusion") not in JobRecord.MEANINGFUL_CONCLUSIONS:
                continue
            key = (str(record.get("workflow", "")), str(record.get("job_name", "")))
            stats = grouped.setdefault(key, JobStatistics(*key))
            if created >= self.recent_start:
                stats.recent.append(record)
            else:
                stats.baseline.append(record)
        return grouped

    @classmethod
    def _minutes(cls, seconds: Optional[float]) -> str:
        """Format seconds as minutes, or ``n/a``."""
        return "n/a" if seconds is None else f"{seconds / cls.SECONDS_PER_MINUTE:.1f}"

    @classmethod
    def _gib(cls, value: Optional[float]) -> str:
        """Format bytes as a GiB figure with its unit, or ``n/a`` when there is no reading."""
        return "n/a" if value is None else f"{value / cls.BYTES_PER_GIB:.2f} GiB"

    @classmethod
    def _memory_cell(cls, stats: JobStatistics, peak: Optional[float]) -> str:
        """Format a job's memory column: a figure, ``expired``, or ``n/a``.

        A job whose records were given up on is not a job that used no memory, and rendering it
        as ``n/a`` beside the jobs nobody measures would hide the difference between a gap that
        the next sweep closes and one that never closes.
        """
        if peak is not None:
            return cls._gib(peak)
        return "expired" if stats.expired_count() else "n/a"

    def _workflow_totals(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Return per-workflow totals, most expensive first."""
        totals: Dict[str, Dict[str, Any]] = {}
        for (workflow, _), stats in self.statistics.items():
            entry = totals.setdefault(workflow, {"seconds": 0.0, "jobs": 0, "runs": set()})
            entry["seconds"] += stats.total_runner_seconds()
            entry["jobs"] += len(stats.recent) + len(stats.baseline)
            for record in stats.recent + stats.baseline:
                entry["runs"].add(record.get("run_id"))
        return sorted(totals.items(), key=lambda item: item[1]["seconds"], reverse=True)

    def _duration_regressions(self) -> List[Dict[str, Any]]:
        """Return every duration finding, largest factor first."""
        findings = [RegressionRules.duration_regression(stats) for stats in self.statistics.values()]
        present = [finding for finding in findings if finding is not None]
        return sorted(present, key=lambda finding: finding["factor"], reverse=True)

    def _memory_regressions(self) -> List[Dict[str, Any]]:
        """Return every memory finding, largest factor first."""
        findings = [RegressionRules.memory_regression(stats) for stats in self.statistics.values()]
        present = [finding for finding in findings if finding is not None]
        return sorted(present, key=lambda finding: finding["factor"], reverse=True)

    def _memory_pressure(self) -> List[Tuple[JobStatistics, float]]:
        """Return the jobs closest to the runner's memory ceiling, worst first."""
        pressured = []
        for stats in self.statistics.values():
            fraction = stats.worst_memory_fraction()
            if fraction is not None and fraction >= RegressionRules.MEMORY_PRESSURE_FRACTION:
                pressured.append((stats, fraction))
        return sorted(pressured, key=lambda item: item[1], reverse=True)

    def _wasteful(self) -> List[Tuple[JobStatistics, float]]:
        """Return long jobs with the lowest CPU efficiency, by runner time wasted."""
        candidates = []
        for stats in self.statistics.values():
            efficiency = stats.median_efficiency()
            duration = stats.baseline_duration() or stats.recent_duration()
            if efficiency is None or duration is None:
                continue
            if efficiency >= RegressionRules.LOW_EFFICIENCY:
                continue
            if duration < RegressionRules.LOW_EFFICIENCY_MIN_SECONDS:
                continue
            candidates.append((stats, efficiency))
        return sorted(candidates, key=lambda item: item[0].total_runner_seconds(), reverse=True)

    def render(self) -> str:
        """Return the full report as markdown."""
        sections = [
            self._render_header(),
            self._render_duration_regressions(),
            self._render_memory_regressions(),
            self._render_memory_pressure(),
            self._render_workflows(),
            self._render_jobs(),
            self._render_waste(),
            self._render_diagnostics(),
        ]
        return "\n".join(section for section in sections if section)

    def _render_header(self) -> str:
        """Return the headline totals for the window."""
        total_seconds = sum(stats.total_runner_seconds() for stats in self.statistics.values())
        runs = {record.get("run_id") for stats in self.statistics.values()
                for record in stats.recent + stats.baseline}
        days = max(1.0, (Timestamps.now() - self.window_start).total_seconds() / 86400)
        return (
            "# CI resource usage\n\n"
            f"Window {Timestamps.format(self.window_start)} → now "
            f"({days:.0f} days), recent period from {Timestamps.format(self.recent_start)}.\n\n"
            f"- **{len(runs)} runs**, {sum(len(s.recent) + len(s.baseline) for s in self.statistics.values())} jobs\n"
            f"- **{total_seconds / self.SECONDS_PER_MINUTE:,.0f} runner-minutes** "
            f"({total_seconds / self.SECONDS_PER_MINUTE / days:,.0f} per day)\n"
        )

    def _render_duration_regressions(self) -> str:
        """Return the duration regression table, or a line saying there were none."""
        findings = self._duration_regressions()
        lines = ["\n## Jobs that got slower\n"]
        if not findings:
            lines.append("No job's median duration grew by "
                         f"{(RegressionRules.DURATION_FACTOR - 1) * 100:.0f}% or more.\n")
            return "\n".join(lines)
        lines.append("| workflow | job | baseline | recent | change | runs | recent branches |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for finding in findings:
            branches = ", ".join(finding.get("branches") or []) or "unknown"
            lines.append(
                f"| {finding['workflow']} | {finding['job_name']} "
                f"| {self._minutes(finding['baseline_seconds'])} min "
                f"| {self._minutes(finding['recent_seconds'])} min "
                f"| **+{(finding['factor'] - 1) * 100:.0f}%** | {finding['samples']} | {branches} |"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_memory_regressions(self) -> str:
        """Return the memory regression table, or nothing when there is no memory data."""
        findings = self._memory_regressions()
        if not findings:
            return ""
        lines = ["\n## Jobs that got hungrier\n",
                 "| workflow | job | baseline | recent | change |",
                 "| --- | --- | ---: | ---: | ---: |"]
        for finding in findings:
            lines.append(
                f"| {finding['workflow']} | {finding['job_name']} "
                f"| {self._gib(finding['baseline_bytes'])} "
                f"| {self._gib(finding['recent_bytes'])} "
                f"| **+{(finding['factor'] - 1) * 100:.0f}%** |"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_memory_pressure(self) -> str:
        """Return the table of jobs near the runner's memory ceiling, if any."""
        pressured = self._memory_pressure()
        if not pressured:
            return ""
        lines = [f"\n## Close to the runner's memory limit "
                 f"(≥ {RegressionRules.MEMORY_PRESSURE_FRACTION * 100:.0f}%)\n",
                 "| workflow | job | worst peak |",
                 "| --- | --- | ---: |"]
        for stats, fraction in pressured:
            lines.append(f"| {stats.workflow} | {stats.job_name} | **{fraction * 100:.0f}%** of runner RAM |")
        lines.append("")
        return "\n".join(lines)

    def _render_workflows(self) -> str:
        """Return the per-workflow cost table."""
        totals = self._workflow_totals()
        grand_total = sum(entry["seconds"] for _, entry in totals) or 1.0
        lines = ["\n## Where the runner-minutes go\n",
                 "| workflow | runs | jobs | runner-minutes | share |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for workflow, entry in totals[:self.TOP_WORKFLOWS]:
            lines.append(
                f"| {workflow} | {len(entry['runs'])} | {entry['jobs']} "
                f"| {entry['seconds'] / self.SECONDS_PER_MINUTE:,.0f} "
                f"| {entry['seconds'] / grand_total * 100:.0f}% |"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_jobs(self) -> str:
        """Return the most expensive individual jobs, with memory where it is known."""
        ranked = sorted(self.statistics.values(), key=lambda stats: stats.total_runner_seconds(), reverse=True)
        lines = ["\n## Most expensive jobs\n",
                 "| workflow | job | runs | median | total min | peak mem | CPU eff |",
                 "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for stats in ranked[:self.TOP_JOBS]:
            median = stats.recent_duration() or stats.baseline_duration()
            peak = stats.recent_peak() or stats.baseline_peak()
            efficiency = stats.median_efficiency()
            lines.append(
                f"| {stats.workflow} | {stats.job_name} | {len(stats.recent) + len(stats.baseline)} "
                f"| {self._minutes(median)} min | {stats.total_runner_seconds() / 60:,.0f} "
                f"| {self._memory_cell(stats, peak)} "
                f"| {'n/a' if efficiency is None else f'{efficiency * 100:.0f}%'} |"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_waste(self) -> str:
        """Return the table of long jobs that keep a runner mostly idle, if any."""
        wasteful = self._wasteful()
        if not wasteful:
            return ""
        lines = [f"\n## Holding a runner without using it "
                 f"(< {RegressionRules.LOW_EFFICIENCY * 100:.0f}% of cores busy)\n",
                 "| workflow | job | median | CPU efficiency | runner-minutes |",
                 "| --- | --- | ---: | ---: | ---: |"]
        for stats, efficiency in wasteful[:self.TOP_JOBS]:
            median = stats.baseline_duration() or stats.recent_duration()
            lines.append(
                f"| {stats.workflow} | {stats.job_name} | {self._minutes(median)} min "
                f"| {efficiency * 100:.0f}% | {stats.total_runner_seconds() / 60:,.0f} |"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_diagnostics(self) -> str:
        """Return what the sweep managed, so a thin report is never mistaken for a quiet week."""
        measured = sum(1 for stats in self.statistics.values()
                       for record in stats.recent + stats.baseline
                       if record.get("peak_memory_bytes") is not None)
        total = sum(len(stats.recent) + len(stats.baseline) for stats in self.statistics.values())
        deadline_hours = RESOURCE_COLLECTION_DEADLINE.total_seconds() / 3600
        lines = ["\n## Collection\n",
                 f"- runs read this sweep: {self.diagnostics.get('runs_fetched', 0)}",
                 f"- jobs in window: {total}, of which {measured} carry memory data",
                 f"- resource artifacts downloaded: {self.diagnostics.get('artifacts_downloaded', 0)}",
                 f"- resource artifacts still waiting on the memory branch: "
                 f"{self.diagnostics.get('artifacts_waiting', 0)} (the backlog the next sweeps drain)",
                 f"- artifacts skipped, their run is not on the memory branch: "
                 f"{self.diagnostics.get('artifacts_off_branch', 0)}",
                 f"- records given up on after {deadline_hours:.0f} h: "
                 f"{self.diagnostics.get('records_expired', 0)} this sweep, "
                 f"{self.index.expired_count()} in the index",
                 f"- API requests used: {self.diagnostics.get('requests_made', 0)}"]
        if self.diagnostics.get("artifacts_unjoined"):
            lines.append(f"- resource artifacts that matched no job: {self.diagnostics['artifacts_unjoined']}")
        if self.diagnostics.get("partial"):
            lines.append(f"- ⚠️ **partial sweep**: {self.diagnostics.get('stopped_because')}")
        lines.append("")
        return "\n".join(lines)


class ReportCommand:
    """Wires the pieces together for one invocation: load, sweep, report, save.

    Kept separate from :func:`main` so the whole flow can be driven from a test with a fake
    API rather than only from the command line.
    """

    DEFAULT_INDEX_FILENAME = UsageIndex.FILENAME

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def run(self) -> int:
        """Execute the sweep and write the report.

        Returns:
            0 always; a partial sweep is reported in the output rather than as a failure, and
            the only fatal error is one that leaves nothing to report at all.
        """
        now = Timestamps.now()
        window_start = now - timedelta(days=self.args.window_days)
        recent_start = now - timedelta(days=self.args.recent_days)

        index = UsageIndex.load(self.args.index)
        api = GitHubApi(self.args.repository, self._token(), self.args.max_requests)
        collector = UsageCollector(api, index)

        sweep_start = index.sweep_start(window_start)
        print(f"sweeping runs of {self.args.repository} created since {Timestamps.format(sweep_start)}")
        collector.sweep_runs(sweep_start)

        collector.diagnostics["records_expired"] = index.expire_stale_resource_records(
            self.args.memory_branch, now)
        wanted = self._runs_wanting_resources(index, window_start)
        collector.sweep_resources(sweep_start, wanted, self.args.max_artifact_downloads,
                                  self.args.memory_branch)

        collector.diagnostics["requests_made"] = api.requests_made
        report = UsageReport(index, window_start, recent_start, collector.diagnostics)
        markdown = report.render()

        if self.args.out:
            with open(self.args.out, "w", encoding="utf-8") as handle:
                handle.write(markdown)
        if self.args.index:
            index.save(self.args.index, window_start)
        self._append_step_summary(markdown)
        print(markdown)
        return 0

    def _token(self) -> Optional[str]:
        """Return the API token from the environment, or None to go unauthenticated.

        Unauthenticated works for this public repository but allows only 60 requests an hour,
        which is enough to try the script out by hand and nowhere near enough for a sweep.
        """
        for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = os.environ.get(variable)
            if token:
                return token
        print("::warning::no GH_TOKEN or GITHUB_TOKEN set; running unauthenticated at 60 requests/hour")
        return None

    def _runs_wanting_resources(self, index: UsageIndex, window_start: datetime) -> List[int]:
        """Return the runs whose resource artifacts are worth downloading.

        The default branch first, since that is the trend line everything is compared against
        and a pull request's own numbers are already in its own job summaries. Runs of flagged
        jobs are added on top, so a regression that shows up in the timings brings its memory
        figures with it rather than needing a second sweep. Records already given up on are
        left out by the index, so a run whose artifacts never arrived stops being asked for.
        """
        wanted: List[int] = []
        by_run = index.runs_missing_resources(self.args.memory_branch)
        for run_id, records in by_run.items():
            created = Timestamps.parse(records[0].get("created_at"))
            if created is not None and created >= window_start:
                wanted.append(run_id)
        wanted.sort(reverse=True)
        return wanted

    @staticmethod
    def _append_step_summary(markdown: str) -> None:
        """Append the report to the job summary when running inside Actions."""
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not summary_path:
            return
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(markdown)
        except OSError as error:
            print(f"::warning::could not write the job summary: {error!r}")


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "FZJ-IEK3-VSA/HiSim"),
                        help="owner/name of the repository to report on")
    parser.add_argument("--window-days", type=int, default=30,
                        help="how far back the report and the index reach")
    parser.add_argument("--recent-days", type=int, default=3,
                        help="how much of the window counts as 'recent' when looking for regressions")
    parser.add_argument("--index", default=UsageIndex.FILENAME,
                        help="the rolling index file, carried between runs as an artifact")
    parser.add_argument("--out", default="ci-usage-report.md",
                        help="where the markdown report is written")
    parser.add_argument("--max-requests", type=int, default=500,
                        help="hard cap on API requests, kept well under the 1000/hour GITHUB_TOKEN "
                             "allows, which is shared with every other workflow calling the API in "
                             "the same hour")
    parser.add_argument("--max-artifact-downloads", type=int, default=250,
                        help="hard cap on resource artifacts downloaded in one sweep")
    parser.add_argument("--memory-branch", default="main",
                        help="branch whose resource artifacts are collected; empty string for all")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run the report."""
    args = build_parser().parse_args(argv)
    if args.memory_branch == "":
        args.memory_branch = None
    return ReportCommand(args).run()


if __name__ == "__main__":
    sys.exit(main())
