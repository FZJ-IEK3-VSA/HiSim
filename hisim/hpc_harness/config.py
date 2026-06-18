"""Configuration for the HiSim MPI HPC harness.

Values come from a JSON config file (``--config``) and/or command-line overrides.
Command-line values take precedence over the config file, which takes precedence
over the defaults below.
"""

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional


@dataclass
class HarnessConfig:
    """All parameters for a harness ``run``."""

    # --- required (via config file or CLI) ---
    db: Optional[str] = None
    """Path to the SQLite task database (on a shared filesystem)."""
    sim_params: Optional[str] = None
    """Path to the single ``*.simulation.json`` used for every task in this run."""
    result_root: Optional[str] = None
    """Directory (shared filesystem) under which per-task result folders are created."""

    # --- memory-gated admission ---
    per_sim_mem_gb: float = 10.0
    """Estimated peak memory of one HiSim simulation. Used for slot sizing and the gate."""
    min_headroom_gb: float = 12.0
    """Minimum free memory that must remain available. A new simulation is launched only
    when ``psutil.available >= per_sim_mem_gb + min_headroom_gb``."""
    max_slots: Optional[int] = None
    """Hard cap on concurrent simulations per node. Defaults to
    ``floor((node_total_mem - min_headroom_gb) / per_sim_mem_gb)`` computed per node."""

    # --- failure handling ---
    timeout_s: float = 7200.0
    """Wall-clock timeout per simulation. Overruns are killed and retried."""
    max_attempts: int = 3
    """Maximum attempts per task before it is marked ``dead``."""
    lease_timeout_s: Optional[float] = None
    """A leased task with no report after this long is reclaimed. Defaults to ``2 * timeout_s``."""

    # --- behaviour / tuning ---
    head_runs_jobs: bool = True
    """If true, rank 0 also runs a local simulation pool instead of being a pure dispatcher."""
    sample_interval_s: float = 1.0
    """Loop tick: how often to sample memory, reap subprocesses and launch new ones."""
    backoff_s: float = 5.0
    """How long an agent waits after a NONE reply before requesting work again."""

    @classmethod
    def from_file(cls, path: str) -> "HarnessConfig":
        """Load a config from a JSON file, rejecting unknown keys."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown keys in harness config {path}: {sorted(unknown)}")
        return cls(**data)

    def apply_overrides(self, **kwargs: object) -> "HarnessConfig":
        """Override fields with any non-None keyword values (used for CLI flags)."""
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        return self

    def finalize(self) -> "HarnessConfig":
        """Fill in derived defaults and validate required fields."""
        missing = [name for name in ("db", "sim_params", "result_root") if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"Missing required harness settings: {missing}. "
                "Provide them in the config file or via command-line flags."
            )
        if self.lease_timeout_s is None:
            self.lease_timeout_s = 2.0 * self.timeout_s
        # Normalise paths to absolute so every node resolves them identically.
        self.db = str(Path(self.db).expanduser().resolve())
        self.sim_params = str(Path(self.sim_params).expanduser().resolve())
        self.result_root = str(Path(self.result_root).expanduser().resolve())
        return self
