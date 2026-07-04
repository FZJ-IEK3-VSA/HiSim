"""Configuration for the HPC harness (spec §12).

Two dataclasses, ``ServerConfig`` and ``WorkerConfig``, each loadable from a JSON file
with CLI overrides on top (CLI > file > defaults). Unknown JSON keys are rejected so a
typo never silently falls back to a default.
"""

import dataclasses
import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional


def _normalize_path(path: str) -> str:
    """Return ``path`` as an absolute, user-expanded, resolved string."""
    return str(Path(path).expanduser().resolve())


def _from_dict(cls: type, data: Dict[str, Any], *, source: str = "<dict>") -> Any:
    """Build a (possibly nested) config dataclass from a parsed JSON dict.

    Rejects unknown keys; recurses into fields whose type is itself a dataclass
    (``autoscale``, ``circuit_breaker``).
    """
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unknown keys in harness config {source}: {sorted(unknown)}")
    kwargs: Dict[str, Any] = {}
    for key, value in data.items():
        f = known[key]
        if dataclasses.is_dataclass(f.type) and isinstance(value, dict):
            kwargs[key] = _from_dict(f.type, value, source=f"{source}.{key}")
        else:
            kwargs[key] = value
    return cls(**kwargs)


@dataclass
class AutoscaleConfig:
    """Autoscaler settings (spec §13.1). Off by default."""

    enabled: bool = False
    worker_mode: str = "single_core"
    period_s: float = 60.0
    standby_floor: int = 10
    """Keep this many workers queued inside Slurm when the cluster is momentarily full."""
    max_workers: int = 2000
    worker_script: Optional[str] = None
    capacity_probe: Optional[str] = None
    """Override command printing the idle-core integer; default parses ``sinfo -h -o %C``."""
    partition: Optional[str] = None
    squeue_poll_s: float = 60.0
    registration_grace_s: float = 900.0
    """Fallback timeout for submitted-but-unregistered workers when squeue is unavailable."""


@dataclass
class CircuitBreakerConfig:
    """Failure-storm circuit breaker (spec §8.1)."""

    enabled: bool = True
    window: int = 100
    min_samples: int = 20
    failure_rate: float = 0.5
    consecutive: int = 25


@dataclass
class ServerConfig:
    """All parameters of the queue server (spec §12)."""

    # --- storage ---
    db_path: Optional[str] = None
    """Core DB (durable source of truth). Server-local persistent disk in the primary profile."""
    db_snapshot_path: Optional[str] = None
    """Shared-FS disaster-recovery copy of the core DB (§6.5). None disables snapshots."""
    db_snapshot_interval_s: float = 300.0
    journal_mode: str = "WAL"
    """"WAL" for local-disk core DB (primary), "DELETE" for the shared-FS fallback profile."""
    logs_db_path: Optional[str] = None
    """Disposable logging DB (§6). Defaults to ``<db_path dir>/logs.db``."""
    logs_archive_path: Optional[str] = None
    """Where the logging DB is copied at end of run (§6.9). None disables archiving."""
    logs_reopen_check_s: float = 60.0

    # --- network ---
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    url_publish_path: Optional[str] = None
    """server.url file on the shared FS (§4.5). None disables publishing."""
    token: Optional[str] = None
    """Bearer token for mutating routes; falls back to the HARNESS_TOKEN env var."""

    # --- results ---
    result_root: Optional[str] = None
    result_shards: bool = False
    """Shard results into subdirs of 1000 jobs each (large-batch FS metadata relief, §4.8)."""
    success_file: Optional[str] = "finished.flag"
    """Default success marker (filename or glob) required for ``done`` (§4.8).

    ``finished.flag`` is what HiSim's ``Simulator`` writes at the end of a run.
    """

    # --- queue behaviour ---
    max_retries: int = 3
    """Re-attempts after the first run; a job runs at most max_retries + 1 times."""
    lease_timeout_s: float = 14400.0
    worker_timeout_s: float = 900.0
    orphan_strikes: int = 2
    reaper_period_s: float = 45.0
    heartbeat_flush_s: float = 30.0
    release_idle_workers: bool = True

    # --- memory budget (§4.6) ---
    per_job_mem_gb: float = 10.0
    mem_autoraise: bool = True
    mem_autoraise_margin_gb: float = 1.0
    mem_min_samples: int = 20
    mem_validation_warn_gb: float = 1.0

    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    autoscale: AutoscaleConfig = field(default_factory=AutoscaleConfig)

    @classmethod
    def from_file(cls, path: str) -> "ServerConfig":
        """Load from a JSON file, rejecting unknown keys."""
        return _from_dict(cls, json.loads(Path(path).read_text(encoding="utf-8")), source=path)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, source: str = "<dict>") -> "ServerConfig":
        """Build from a parsed JSON dict, rejecting unknown keys."""
        return _from_dict(cls, data, source=source)

    def apply_overrides(self, **kwargs: Any) -> "ServerConfig":
        """Overwrite fields with any non-None keyword values (CLI flags)."""
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        return self

    def finalize(self) -> "ServerConfig":
        """Fill derived defaults, resolve paths, validate required settings."""
        if self.db_path is None or self.result_root is None:
            missing = [n for n in ("db_path", "result_root") if getattr(self, n) is None]
            raise ValueError(f"Missing required server settings: {missing}")
        self.db_path = _normalize_path(self.db_path)
        self.result_root = _normalize_path(self.result_root)
        if self.logs_db_path is None:
            self.logs_db_path = str(Path(self.db_path).parent / "logs.db")
        self.logs_db_path = _normalize_path(self.logs_db_path)
        for name in ("db_snapshot_path", "logs_archive_path", "url_publish_path"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _normalize_path(value))
        if self.token is None:
            self.token = os.environ.get("HARNESS_TOKEN")
        if self.journal_mode not in ("WAL", "DELETE"):
            raise ValueError(f"journal_mode must be 'WAL' or 'DELETE', got {self.journal_mode!r}")
        return self

    @property
    def max_attempts(self) -> int:
        """Total runs allowed per job (internal ``db.py`` convention)."""
        return self.max_retries + 1


@dataclass
class WorkerConfig:
    """All parameters of one worker process (spec §12)."""

    server_url_file: Optional[str] = None
    server_url: Optional[str] = None
    """Direct URL override (mainly for tests); normally discovered via server_url_file."""
    runner: str = "hisim"
    result_root: Optional[str] = None
    log_root: Optional[str] = None
    mode: str = "whole_node"

    # --- whole_node self-accounting gate ---
    min_headroom_gb: float = 12.0
    cores_per_job: int = 1
    reserved_cores: int = 0
    max_slots: Optional[int] = None
    max_jobs_per_child: int = 50
    child_rss_ceiling_gb: Optional[float] = None
    """Recycle a warm child whose RSS exceeds this between jobs (None = only job-count based)."""

    # --- single_core gate (§4.2) ---
    node_gate: str = "auto"
    """"auto" | "cgroup" | "observed" | "off" — auto uses cgroup limits when detected."""
    max_node_cpu_percent: float = 95.0
    node_safety_buffer_gb: float = 16.0
    gate_warn_s: float = 600.0
    gate_max_wait_s: Optional[float] = 3600.0

    # --- filesystem preflight (§4.2.1) ---
    preflight_retries: int = 3
    preflight_window_s: float = 60.0

    # --- timing ---
    timeout_s: float = 7200.0
    heartbeat_interval_s: float = 30.0
    console_follow_interval_s: float = 2.0
    sample_interval_s: float = 10.0
    log_ship_level: str = "WARNING"
    lease_batch: Optional[int] = None
    backoff_s: float = 5.0
    token: Optional[str] = None

    @classmethod
    def from_file(cls, path: str) -> "WorkerConfig":
        """Load from a JSON file, rejecting unknown keys."""
        return _from_dict(cls, json.loads(Path(path).read_text(encoding="utf-8")), source=path)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, source: str = "<dict>") -> "WorkerConfig":
        """Build from a parsed JSON dict, rejecting unknown keys."""
        return _from_dict(cls, data, source=source)

    def apply_overrides(self, **kwargs: Any) -> "WorkerConfig":
        """Overwrite fields with any non-None keyword values (CLI flags)."""
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        return self

    def finalize(self) -> "WorkerConfig":
        """Resolve paths and validate required settings."""
        if self.server_url is None and self.server_url_file is None:
            raise ValueError("Set server_url_file (or server_url) so the worker can find the server.")
        if self.result_root is None:
            raise ValueError("Missing required worker setting: result_root")
        self.result_root = _normalize_path(self.result_root)
        if self.log_root is not None:
            self.log_root = _normalize_path(self.log_root)
        if self.server_url_file is not None:
            self.server_url_file = _normalize_path(self.server_url_file)
        if self.mode not in ("whole_node", "single_core"):
            raise ValueError(f"mode must be 'whole_node' or 'single_core', got {self.mode!r}")
        if self.node_gate not in ("auto", "cgroup", "observed", "off"):
            raise ValueError(f"node_gate must be auto|cgroup|observed|off, got {self.node_gate!r}")
        if self.mode == "single_core":
            self.max_slots = 1
        if self.token is None:
            self.token = os.environ.get("HARNESS_TOKEN")
        return self
