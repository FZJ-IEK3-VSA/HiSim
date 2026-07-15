"""Memory-budget validation & auto-raise (spec §4.6).

The configured ``per_job_mem_gb`` is validated against every reported real peak. When
jobs run hotter than budgeted, the effective budget is **raised automatically** to
p99 + margin (OOM protection) and pushed to workers via heartbeat ``set`` directives.
Lowering is manual only (``POST /admin/config``). The effective budget is persisted in
``meta`` so a server restart keeps a raised value.
"""

import math
from typing import Any, Callable, Dict, List, Optional

from hpc_harness.config import ServerConfig

_META_KEY = "effective_per_job_mem_gb"
_MB_PER_GB = 1024.0


class MemBudget:
    """Tracks observed peak memory per job and manages the effective budget."""

    def __init__(
        self,
        cfg: ServerConfig,
        persist_fn: Optional[Callable[[float], None]] = None,
        initial_effective: Optional[float] = None,
        initial_peaks_mb: Optional[List[float]] = None,
    ) -> None:
        """``persist_fn`` stores the effective budget (meta table) whenever it changes."""
        self.cfg = cfg
        self.persist_fn = persist_fn
        self.effective = max(cfg.per_job_mem_gb, initial_effective or 0.0)
        self.peaks_mb: List[float] = list(initial_peaks_mb or [])
        self.last_raise: Optional[Dict[str, Any]] = None

    @staticmethod
    def meta_key() -> str:
        """Meta-table key under which the effective budget is persisted."""
        return _META_KEY

    def observe(self, peak_mem_mb: Optional[float]) -> bool:
        """Feed one reported peak; returns True when this observation auto-raised the budget."""
        if peak_mem_mb is None or peak_mem_mb <= 0:
            return False
        self.peaks_mb.append(float(peak_mem_mb))
        if not self.cfg.mem_autoraise or len(self.peaks_mb) < self.cfg.mem_min_samples:
            return False
        target = self.p99_gb() + self.cfg.mem_autoraise_margin_gb
        if target > self.effective:
            self.last_raise = {"from": self.effective, "to": target, "p99_gb": self.p99_gb()}
            self.effective = target
            if self.persist_fn:
                self.persist_fn(self.effective)
            return True
        return False

    def set_manual(self, value_gb: float) -> None:
        """Apply an operator-set budget (the only way the budget goes down)."""
        self.effective = float(value_gb)
        self.last_raise = None
        if self.persist_fn:
            self.persist_fn(self.effective)

    def p99_gb(self) -> float:
        """99th percentile of observed peaks in GB (0 when no samples)."""
        if not self.peaks_mb:
            return 0.0
        ordered = sorted(self.peaks_mb)
        idx = min(len(ordered) - 1, max(0, math.ceil(0.99 * len(ordered)) - 1))
        return ordered[idx] / _MB_PER_GB

    def max_gb(self) -> float:
        """Maximum observed peak in GB."""
        return max(self.peaks_mb) / _MB_PER_GB if self.peaks_mb else 0.0

    def warning(self) -> Optional[Dict[str, Any]]:
        """Dashboard warning: budget raised, or budget too high (wasted slots)."""
        if self.last_raise is not None:
            return {
                "kind": "auto_raised",
                "message": (
                    f"per-job memory budget auto-raised from {self.last_raise['from']:.1f} GB to "
                    f"{self.effective:.1f} GB (observed p99 {self.last_raise['p99_gb']:.1f} GB)"
                ),
            }
        if len(self.peaks_mb) >= self.cfg.mem_min_samples:
            gap = self.effective - self.max_gb()
            if gap > self.cfg.mem_validation_warn_gb:
                return {
                    "kind": "too_high",
                    "message": (
                        f"budget {self.effective:.1f} GB exceeds observed max peak "
                        f"{self.max_gb():.1f} GB by {gap:.1f} GB — slots are being wasted; "
                        f"consider lowering per_job_mem_gb via POST /admin/config"
                    ),
                }
        return None
