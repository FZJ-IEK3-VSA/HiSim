"""Configuration for the HiSim MPI HPC harness.

Values come from a JSON config file (``--config``) and/or command-line overrides.
Command-line values take precedence over the config file, which takes precedence
over the defaults below.
"""

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional, Tuple, Union


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
    """How long an agent waits after a NO_WORK_AVAILABLE reply before requesting work again."""

    @classmethod
    def from_file(cls, path: str) -> "HarnessConfig":
        """Load a config from a JSON file, rejecting unknown keys.

        Args:
            path: Filesystem path to a JSON config file.

        Returns:
            A new :class:`HarnessConfig` populated from the JSON keys.

        Raises:
            ValueError: If the JSON contains keys that are not
                :class:`HarnessConfig` fields.
            json.JSONDecodeError: Propagated from parsing the file when its
                contents are not valid JSON.
            OSError: Propagated from reading the file at ``path``.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown keys in harness config {path}: {sorted(unknown)}")
        return cls(**data)

    def apply_overrides(self, **kwargs: Optional[Union[str, float, int, bool]]) -> "HarnessConfig":
        """Override fields with any non-None keyword values (used for CLI flags).

        Args:
            **kwargs: Field-name/value pairs; any non-None value whose name
                matches a :class:`HarnessConfig` field overwrites the current
                value. Unknown names and ``None`` values are silently ignored.

        Returns:
            ``self``, for chaining.
        """
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        return self

    def finalize(self) -> "HarnessConfig":
        """Fill in derived defaults and validate required fields.

        When ``lease_timeout_s`` is unset it defaults to ``2 * timeout_s``.
        The ``db``, ``sim_params`` and ``result_root`` path strings are then
        rewritten as absolute, user-expanded, resolved paths so every node
        resolves them identically.

        Returns:
            ``self``, with derived defaults filled in and paths normalised.

        Raises:
            ValueError: If ``db``, ``sim_params`` or ``result_root`` is not set
                (raised via :meth:`required_paths`).
        """
        db_path, sim_params_path, result_root_path = self.required_paths()
        if self.lease_timeout_s is None:
            self.lease_timeout_s = 2.0 * self.timeout_s
        # Normalise paths to absolute so every node resolves them identically.
        self.db = str(Path(db_path).expanduser().resolve())
        self.sim_params = str(Path(sim_params_path).expanduser().resolve())
        self.result_root = str(Path(result_root_path).expanduser().resolve())
        return self

    def required_paths(self) -> Tuple[str, str, str]:
        """Return required path settings after validating that all are configured.

        Returns:
            The ``(db, sim_params, result_root)`` path strings.

        Raises:
            ValueError: If any of ``db``, ``sim_params`` or ``result_root`` is
                ``None``.
        """
        missing = [name for name in ("db", "sim_params", "result_root") if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"Missing required harness settings: {missing}. "
                "Provide them in the config file or via command-line flags."
            )
        if self.db is None or self.sim_params is None or self.result_root is None:
            raise ValueError("Harness path settings were not fully configured.")
        return self.db, self.sim_params, self.result_root
